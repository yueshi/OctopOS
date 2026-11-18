"""Application-level scheduler for MiniApp interval jobs.

One periodic asyncio task scans ``light_app_schedules`` for due rows, takes a
DB lease (``lease_until``) so multiple server instances run a job only once,
and executes the row's action through :class:`AppService`. A heartbeat extends
the lease while a long import or snapshot is still running; a crashed process
simply lets the lease expire. Failures back off exponentially from the row's
interval, capped at ``MAX_BACKOFF_SECONDS``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from octop.infra.apps.service import (
    MAX_BACKOFF_SECONDS,
    SCHEDULE_ACTION_SNAPSHOT_PUBLICATION,
    SCHEDULE_ACTION_SYNC_SOURCE,
    SOURCE_STATUS_ERROR,
    SOURCE_STATUS_OK,
    AppService,
)
from octop.infra.apps.wiring import build_app_service
from octop.infra.db.repos.app_schedules import (
    LightAppScheduleRepo,
    LightAppScheduleRow,
    schedule_jitter,
)
from octop.infra.db.repos.apps import LightAppRepo

if TYPE_CHECKING:
    from octop.config import OctopConfig
    from octop.infra.db.services import SharedServices

logger = logging.getLogger(__name__)

SCAN_SECONDS = 30
"""How often the loop looks for due schedules."""

LEASE_SECONDS = 300
"""A run holds its row for this long; the heartbeat renews it while active."""

HEARTBEAT_SECONDS = 60

HOUSEKEEPING_SECONDS = 24 * 3600
"""How often to trim old row-history entries."""

DEFAULT_ACTION_TIMEOUT_SECONDS = 15 * 60
"""Hard ceiling for one run; a hung action must not hold its lease forever."""

ACTION_TIMEOUTS: dict[str, float] = {
    SCHEDULE_ACTION_SYNC_SOURCE: 15 * 60,
    SCHEDULE_ACTION_SNAPSHOT_PUBLICATION: 2 * 60,
}


class AppSourceScheduler:
    def __init__(
        self,
        *,
        repo: LightAppRepo,
        service: AppService,
        schedule_repo: LightAppScheduleRepo,
        scan_seconds: int = SCAN_SECONDS,
        clock: Callable[[], float] | None = None,
        action_timeouts: Mapping[str, float] | None = None,
    ) -> None:
        self._repo = repo
        self._service = service
        self._schedule_repo = schedule_repo
        self._scan_seconds = scan_seconds
        self._clock = clock or time.time
        self._action_timeouts = dict(action_timeouts or ACTION_TIMEOUTS)
        self._last_housekeeping = 0.0
        self._task: asyncio.Task[None] | None = None

    def replace_persistence(
        self,
        *,
        repo: LightAppRepo,
        schedule_repo: LightAppScheduleRepo,
        service: AppService,
    ) -> None:
        """Retarget onto new repos/services (setup wizard DB hot-swap)."""
        self._repo = repo
        self._schedule_repo = schedule_repo
        self._service = service

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="light-app-scheduler")

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
                logger.exception("light app scheduler tick failed")
            await asyncio.sleep(self._scan_seconds)

    async def tick(self, *, now: int | None = None) -> int:
        """Run every due schedule once; returns how many jobs ran."""
        now = int(self._clock()) if now is None else now
        if now - self._last_housekeeping >= HOUSEKEEPING_SECONDS:
            self._last_housekeeping = now
            try:
                self._service.trim_row_history()
            except Exception:
                logger.warning("light app row-history trim failed", exc_info=True)
        due = self._schedule_repo.list_due(now)
        from octop.infra.metrics import METRICS  # noqa: PLC0415

        METRICS.set("light_app_schedule_backlog", len(due))
        ran = 0
        for item in due:
            lease = self._schedule_repo.claim(
                item.schedule_id,
                now=now,
                lease_seconds=LEASE_SECONDS,
            )
            if lease is None:
                continue  # claimed elsewhere, disabled, or already leased
            ran += 1
            await self._run(item, lease=lease, run_at=now)
        return ran

    async def _run(self, item: LightAppScheduleRow, *, lease: int, run_at: int) -> None:
        current = {"lease": lease}
        stop = asyncio.Event()

        async def heartbeat() -> None:
            while True:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
                    return
                except TimeoutError:
                    try:
                        renewed = self._schedule_repo.heartbeat(
                            item.schedule_id,
                            lease_until=current["lease"],
                            now=int(time.time()),
                            lease_seconds=LEASE_SECONDS,
                        )
                    except Exception:
                        logger.warning(
                            "light app schedule %s heartbeat failed",
                            item.schedule_id,
                            exc_info=True,
                        )
                        return
                    if renewed is None:
                        logger.warning(
                            "light app schedule %s lost its lease while running",
                            item.schedule_id,
                        )
                        return
                    current["lease"] = renewed

        beat = asyncio.create_task(heartbeat(), name=f"schedule-lease-{item.schedule_id}")
        error: Exception | None = None
        timeout = self._action_timeouts.get(item.action, DEFAULT_ACTION_TIMEOUT_SECONDS)
        try:
            row = self._service.get(item.app_id)
            await asyncio.wait_for(
                self._service.execute_schedule(row, item.action, item.target),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            stop.set()
            self._schedule_repo.release(item.schedule_id, lease_until=current["lease"])
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
            raise
        except TimeoutError:
            error = TimeoutError(f"schedule action timed out after {timeout:g}s")
        except Exception as exc:
            error = exc
        finally:
            stop.set()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        finished_at = max(run_at, int(self._clock()))
        from octop.infra.metrics import METRICS  # noqa: PLC0415

        METRICS.inc("light_app_schedule_runs_total")
        if error is None:
            self._finish_success(
                item, lease=current["lease"], run_at=run_at, finished_at=finished_at
            )
        else:
            METRICS.inc("light_app_schedule_errors_total")
            if isinstance(error, TimeoutError):
                METRICS.inc("light_app_schedule_timeouts_total")
            self._finish_failure(
                item,
                lease=current["lease"],
                run_at=run_at,
                finished_at=finished_at,
                error=str(error),
            )
            logger.warning(
                "light app schedule %s (%s/%s) failed",
                item.schedule_id,
                item.app_id,
                item.action,
                exc_info=(type(error), error, error.__traceback__),
            )

    def _finish_success(
        self,
        item: LightAppScheduleRow,
        *,
        lease: int,
        run_at: int,
        finished_at: int,
    ) -> None:
        if item.action == SCHEDULE_ACTION_SYNC_SOURCE:
            self._finish_from_source(item, lease=lease, run_at=run_at)
            return
        self._schedule_repo.finish(
            item.schedule_id,
            lease_until=lease,
            run_at=run_at,
            status=SOURCE_STATUS_OK,
            next_run_at=(
                finished_at
                + item.interval_seconds
                + schedule_jitter(item.schedule_id, item.interval_seconds)
            ),
            failure_count=0,
        )

    def _finish_failure(
        self,
        item: LightAppScheduleRow,
        *,
        lease: int,
        run_at: int,
        finished_at: int,
        error: str,
    ) -> None:
        if item.action == SCHEDULE_ACTION_SYNC_SOURCE:
            self._finish_from_source(item, lease=lease, run_at=run_at, fallback_error=error)
            return
        failure_count = item.failure_count + 1
        delay = min(item.interval_seconds * 2 ** (failure_count - 1), MAX_BACKOFF_SECONDS)
        self._schedule_repo.finish(
            item.schedule_id,
            lease_until=lease,
            run_at=run_at,
            status=SOURCE_STATUS_ERROR,
            next_run_at=(
                finished_at + delay + schedule_jitter(item.schedule_id, item.interval_seconds)
            ),
            failure_count=failure_count,
            error=error,
        )

    def _finish_from_source(
        self,
        item: LightAppScheduleRow,
        *,
        lease: int,
        run_at: int,
        fallback_error: str = "",
    ) -> None:
        """Use the source row's bookkeeping (run_source records it itself)."""
        source_id = str(item.target.get("source_id") or "")
        source = self._repo.get_source(item.app_id, source_id) if source_id else None
        if source is None:
            self._schedule_repo.delete(item.app_id, item.schedule_id)
            return
        if source.last_run_at is not None and source.last_run_at >= run_at:
            self._schedule_repo.finish(
                item.schedule_id,
                lease_until=lease,
                run_at=run_at,
                status=source.last_status or SOURCE_STATUS_OK,
                next_run_at=source.next_run_at or run_at + item.interval_seconds,
                failure_count=source.fail_count,
                error=source.last_error or "",
            )
            return
        failure_count = item.failure_count + 1
        delay = min(item.interval_seconds * 2 ** (failure_count - 1), MAX_BACKOFF_SECONDS)
        finished_at = max(run_at, int(self._clock()))
        self._schedule_repo.finish(
            item.schedule_id,
            lease_until=lease,
            run_at=run_at,
            status=SOURCE_STATUS_ERROR,
            next_run_at=(
                finished_at + delay + schedule_jitter(item.schedule_id, item.interval_seconds)
            ),
            failure_count=failure_count,
            error=fallback_error or "source run did not record an outcome",
        )


def build_source_service(
    *,
    services: SharedServices,
    workspaces: object,
    config: OctopConfig,
) -> AppService:
    """AppService wired for background work (external runner included)."""
    from octop.infra.apps.sources import GatewayToolRunner  # noqa: PLC0415
    from octop.infra.connectors.service import ConnectorService  # noqa: PLC0415

    connector_repo = services.repos.connector_repo
    return build_app_service(
        repos=services.repos,
        workspaces=workspaces,
        external_tool_runner=GatewayToolRunner(
            repo=connector_repo,
            service=ConnectorService(
                repo=connector_repo,
                secret_repo=services.secret_repo,
                settings_repo=services.settings_repo,
                config=config,
            ),
        ),
    )


def build_scheduler(
    *,
    services: SharedServices,
    workspaces: object,
    config: OctopConfig,
) -> AppSourceScheduler:
    """Build (not start) the scheduler against the server's repos."""
    return AppSourceScheduler(
        repo=services.light_app_repo,
        service=build_source_service(services=services, workspaces=workspaces, config=config),
        schedule_repo=services.repos.app_schedule_repo,
    )
