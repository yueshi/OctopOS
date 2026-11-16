"""Per-user durable notification inbox for the dashboard notification center."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/notifications")


class NotificationOut(BaseModel):
    notification_id: str
    kind: str = Field(description="approval / success / info / error / task / security / cron")
    title: str
    body: str = ""
    path: str | None = Field(
        default=None,
        description="Dashboard route to open when the notification is clicked.",
    )
    dedupe_key: str | None = Field(
        default=None,
        description="Stable key used to suppress duplicate rows for the same event.",
    )
    read: bool
    created_at: int = Field(description="Unix epoch seconds.")


class NotificationsResponse(BaseModel):
    notifications: list[NotificationOut]
    unread: int


class MarkReadResult(BaseModel):
    ok: bool


class MarkAllReadResult(BaseModel):
    updated: int


class ClearResult(BaseModel):
    deleted: int


def _repo(server: OctopServer) -> Any:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return server.services.notification_repo


def _out(row: Any) -> NotificationOut:
    return NotificationOut(
        notification_id=row.notification_id,
        kind=row.kind,
        title=row.title,
        body=row.body,
        path=row.path,
        dedupe_key=row.dedupe_key,
        read=row.read,
        created_at=row.created_at,
    )


async def _push(user: User, server: OctopServer, frame: dict[str, Any]) -> None:
    """Best-effort cross-tab sync; read state is already durable in the DB."""
    runtime = server.app_runtime
    hub = getattr(getattr(runtime, "gateway", None), "ws_hub", None)
    if hub is None:
        return
    try:
        await hub.push_to_user(user.id, frame)
    except Exception:  # noqa: BLE001 - a push failure must not fail the request
        return


@router.get(
    "",
    response_model=NotificationsResponse,
    summary="List dashboard notifications",
    description=(
        "Returns the signed-in user's durable notification inbox, newest first. "
        "Read and unread rows are both returned so the notification center can "
        "show history; ``unread`` is the badge count."
    ),
)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    unread_only: bool = False,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> NotificationsResponse:
    repo = _repo(server)
    rows = repo.list_for_user(user.id, limit=limit, unread_only=unread_only)
    return NotificationsResponse(
        notifications=[_out(row) for row in rows],
        unread=repo.unread_count(user.id),
    )


@router.post(
    "/{notification_id}/read",
    response_model=MarkReadResult,
    summary="Mark a notification read",
)
async def mark_notification_read(
    notification_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> MarkReadResult:
    row = _repo(server).mark_read(user.id, notification_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "notification not found")
    await _push(
        user,
        server,
        {"type": "notification.read", "notification_id": notification_id},
    )
    return MarkReadResult(ok=True)


@router.post(
    "/read-all",
    response_model=MarkAllReadResult,
    summary="Mark all notifications read",
)
async def mark_all_notifications_read(
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> MarkAllReadResult:
    updated = _repo(server).mark_all_read(user.id)
    await _push(user, server, {"type": "notification.read_all"})
    return MarkAllReadResult(updated=updated)


@router.delete(
    "",
    response_model=ClearResult,
    summary="Clear notifications",
    description="Deletes every notification in the signed-in user's inbox.",
)
async def clear_notifications(
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> ClearResult:
    deleted = _repo(server).clear(user.id)
    await _push(user, server, {"type": "notification.cleared"})
    return ClearResult(deleted=deleted)
