"""Background runner for MiniApp AI tasks.

One process-wide loop leases queued ``light_app_tasks`` rows and executes them
through :class:`AppTaskService`. P1 is intentionally single-lane: tasks run one
at a time, with a hard action timeout shorter than the lease. Tier 2 can add
heartbeats and per-app concurrency later.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from octop.i18n import tr
from octop.infra.apps.task_types import get_task_type
from octop.infra.apps.tasks import AppTaskCancelled, AppTaskService
from octop.infra.apps.wiring import build_app_service
from octop.infra.db.repos.app_tasks import (
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_SUCCEEDED,
    AppTaskRepo,
    AppTaskRow,
)
from octop.infra.notifications import create_notification
from octop.infra.utils.locale import Locale, resolve_user_locale

if TYPE_CHECKING:
    from octop.config import OctopConfig
    from octop.infra.db.services import SharedServices

logger = logging.getLogger(__name__)

SCAN_SECONDS = 10
LEASE_SECONDS = 300
"""Lease is longer than every P1 task timeout; Tier 2 adds a heartbeat."""

MAX_PER_TICK = 5


class AppTaskRunner:
    def __init__(
        self,
        *,
        task_repo: AppTaskRepo,
        service: AppTaskService,
        scan_seconds: int = SCAN_SECONDS,
        clock: Callable[[], float] | None = None,
        notification_repo: Any | None = None,
        user_repo: Any | None = None,
        push: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._service = service
        self._scan_seconds = scan_seconds
        self._clock = clock or time.time
        self._notification_repo = notification_repo
        self._user_repo = user_repo
        self._push = push
        self._task: asyncio.Task[None] | None = None

    def replace_persistence(
        self,
        *,
        task_repo: AppTaskRepo,
        service: AppTaskService,
        notification_repo: Any | None = None,
        user_repo: Any | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._service = service
        if notification_repo is not None:
            self._notification_repo = notification_repo
        if user_repo is not None:
            self._user_repo = user_repo

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="light-app-task-runner")

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("light app task runner tick failed")
            await asyncio.sleep(self._scan_seconds)

    async def tick(self) -> int:
        now = int(self._clock())
        recovered = self._task_repo.recover_expired(now=now)
        if recovered:
            logger.warning("requeued %d expired MiniApp AI tasks", recovered)
        ran = 0
        for row in self._task_repo.list_queued(limit=MAX_PER_TICK):
            lease = self._task_repo.claim(
                row.task_id,
                now=int(self._clock()),
                lease_seconds=LEASE_SECONDS,
            )
            if lease is None:
                continue
            ran += 1
            await self._run(row, lease=lease)
        return ran

    async def _run(self, row: AppTaskRow, *, lease: int) -> None:
        task = self._task_repo.get(row.task_id)
        if task is None:
            return
        if task.cancel_requested:
            self._finish(task, status=TASK_CANCELLED, lease=lease)
            return
        spec = get_task_type(task.task_type)
        timeout = spec.timeout_seconds if spec is not None else 60.0
        try:
            result = await asyncio.wait_for(self._service.execute(task), timeout=timeout)
        except AppTaskCancelled:
            self._finish(task, status=TASK_CANCELLED, lease=lease)
        except TimeoutError:
            if self._finish(
                task,
                status=TASK_FAILED,
                lease=lease,
                error=f"task timed out after {timeout:g}s",
            ):
                await self._notify(task, TASK_FAILED)
        except Exception as exc:
            logger.warning(
                "MiniApp AI task %s (%s/%s) failed",
                task.task_id,
                task.app_id,
                task.task_type,
                exc_info=True,
            )
            if self._finish(task, status=TASK_FAILED, lease=lease, error=str(exc)[:1_000]):
                await self._notify(task, TASK_FAILED)
        else:
            status = TASK_CANCELLED if task.cancel_requested else TASK_SUCCEEDED
            if self._finish(
                task,
                status=status,
                lease=lease,
                result={
                    **result.result,
                    "model_ref": result.model_ref,
                    "written": result.written,
                },
            ):
                await self._notify(task, status)

    def _finish(
        self,
        task: AppTaskRow,
        *,
        status: str,
        lease: int,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> bool:
        finished = self._service.finish(
            task,
            status=status,
            result=result,
            error=error,
            lease_until=lease,
        )
        if not finished:
            logger.warning("MiniApp AI task %s finished after losing its lease", task.task_id)
        return finished

    async def _notify(self, task: AppTaskRow, status: str) -> None:
        if status not in {TASK_SUCCEEDED, TASK_FAILED}:
            return
        if self._notification_repo is None or self._user_repo is None:
            return
        locale: Locale = resolve_user_locale(
            user_repo=self._user_repo,
            user_id=task.actor_user_id,
            channel_type="dashboard",
        )
        key = (
            "notifications.app_task.succeeded"
            if status == TASK_SUCCEEDED
            else "notifications.app_task.failed"
        )
        try:
            frame = await create_notification(
                self._notification_repo,
                user_id=task.actor_user_id,
                kind="app_task",
                title=tr(f"{key}.title", locale),
                body=tr(f"{key}.body", locale),
                path=f"/apps/{task.app_id}?tab=tasks",
                dedupe_key=f"app_task:{task.task_id}",
            )
        except Exception:  # noqa: BLE001 - notification failure must not affect the task
            logger.warning("failed to persist MiniApp AI task notification", exc_info=True)
            return
        if self._push is not None:
            try:
                await self._push(task.actor_user_id, frame)
            except Exception:  # noqa: BLE001 - socket push is best-effort
                logger.warning("failed to push MiniApp AI task notification", exc_info=True)


def build_task_service(
    *,
    services: SharedServices,
    workspaces: object,
    agents: Any,
) -> AppTaskService:
    service = build_app_service(repos=services.repos, workspaces=workspaces)
    return AppTaskService(
        app_service=service,
        app_repo=services.repos.light_app_repo,
        task_repo=services.repos.app_task_repo,
        agents=agents,
        usage_repo=services.repos.usage_repo,
        policy_repo=services.repos.user_policy_repo,
        audit_repo=services.repos.audit_repo,
    )


def build_task_runner(
    *,
    services: SharedServices,
    workspaces: object,
    agents: Any,
    config: OctopConfig,
    notification_repo: Any | None = None,
    user_repo: Any | None = None,
    push: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
) -> AppTaskRunner:
    """Build (not start) the task runner against the server's repos."""
    del config  # reserved for Tier 2 executor configuration
    return AppTaskRunner(
        task_repo=services.repos.app_task_repo,
        service=build_task_service(services=services, workspaces=workspaces, agents=agents),
        notification_repo=notification_repo,
        user_repo=user_repo,
        push=push,
    )


__all__ = ["AppTaskRunner", "build_task_runner", "build_task_service"]
