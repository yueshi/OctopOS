"""MiniApp access requests: blocked users ask the owner for access."""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.i18n import tr
from octop.infra.apps.service import AppService
from octop.infra.apps.wiring import build_app_service
from octop.infra.db.repos.app_access import APP_ACCESS_PENDING, AppAccessRequestRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.notifications import create_notification
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_user_locale

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps/access-requests")


class CreateAccessRequestBody(BaseModel):
    agent_id: str = Field(description="Expert hosting the MiniApp.")
    app_id: str | None = Field(
        default=None,
        description="MiniApp the caller wants to use; omit for expert-only access.",
    )
    needed: Literal["participate", "view", "edit_data", "edit_structure"] = Field(
        default="edit_data",
        description="Permission the caller needs.",
    )
    note: str = Field(
        default="",
        max_length=500,
        description="Optional message shown to the owner.",
    )


class AccessRequestOut(BaseModel):
    request_id: str
    app_id: str | None
    agent_id: str
    requester_username: str
    app_owner_username: str | None = Field(
        default=None,
        description="Owner of the MiniApp; null for expert-only requests.",
    )
    agent_owner_username: str = Field(
        description="Owner of the expert that hosts the MiniApp.",
    )
    needed: str
    note: str
    status: str
    created_at: int
    resolved_at: int | None


class AccessRequestsResponse(BaseModel):
    requests: list[AccessRequestOut]


def _service(server: OctopServer) -> AppService:
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return build_app_service(
        repos=server.services.repos,
        workspaces=server.app_runtime.agent_registry,
    )


async def _persist_notification(
    server: OctopServer,
    *,
    user_id: int,
    kind: str,
    title: str,
    body: str,
    path: str | None,
    dedupe_key: str,
) -> dict[str, Any] | None:
    """Persist one inbox row and return its socket frame.

    A write failure must not fail the access request; the legacy toast still
    goes out and the next inbox sync retries the read model.
    """
    if server.services is None:
        return None
    try:
        return await create_notification(
            server.services.notification_repo,
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            path=path,
            dedupe_key=dedupe_key,
        )
    except Exception:  # noqa: BLE001 - notification persistence is best effort
        logger.warning("failed to persist access notification", exc_info=True)
        return None


async def _push_owner(
    server: OctopServer,
    request: AppAccessRequestRow,
    *,
    exclude_user_id: int | None = None,
) -> None:
    """Best-effort live notification; the durable inbox is the fallback."""
    if server.services is None or server.app_runtime is None:
        return
    gateway = getattr(server.app_runtime, "gateway", None)
    hub = getattr(gateway, "ws_hub", None)
    requester = server.services.user_repo.get(request.requester_user_id)
    requester_name = requester.username if requester is not None else ""
    app_name = ""
    if request.app_id:
        app = server.services.light_app_repo.get(request.app_id)
        app_name = app.name if app is not None else ""
    target = app_name or request.app_id or request.agent_id
    frame = {
        "type": "app_access_request",
        "request_id": request.request_id,
        "agent_id": request.agent_id,
        "app_id": request.app_id,
        "app_name": app_name,
        "requester": requester_name,
        "deep_link": (f"/apps/{request.app_id}" if request.app_id else None),
    }
    owner_ids = {
        user_id
        for user_id in (request.app_owner_user_id, request.agent_owner_user_id)
        if user_id is not None and user_id != exclude_user_id
    }
    for owner_id in owner_ids:
        payload = dict(frame)
        if owner_id != request.app_owner_user_id:
            # The expert owner may have no app ACL, so the app page would 403;
            # keep the toast (with resolve buttons) but drop the dead link.
            payload["deep_link"] = None
        if hub is not None:
            with contextlib.suppress(Exception):
                await hub.push_to_user(owner_id, payload)
        locale = resolve_user_locale(
            user_repo=server.services.user_repo,
            user_id=owner_id,
        )
        notification = await _persist_notification(
            server,
            user_id=owner_id,
            kind="approval",
            title=tr("notifications.access_request.title", locale),
            body=tr(
                "notifications.access_request.body",
                locale,
                requester=requester_name,
                target=target,
            ),
            path=payload["deep_link"],
            dedupe_key=f"access:{request.request_id}",
        )
        if notification is not None and hub is not None:
            with contextlib.suppress(Exception):
                await hub.push_to_user(owner_id, notification)


async def _push_requester(server: OctopServer, request: AppAccessRequestRow) -> None:
    """Tell the requester the outcome; the durable inbox is the fallback."""
    if server.services is None or server.app_runtime is None:
        return
    gateway = getattr(server.app_runtime, "gateway", None)
    hub = getattr(gateway, "ws_hub", None)
    app_name = ""
    if request.app_id:
        app = server.services.light_app_repo.get(request.app_id)
        app_name = app.name if app is not None else ""
    target = app_name or request.app_id or request.agent_id
    deep_link = f"/apps/{request.app_id}" if request.app_id else None
    if hub is not None:
        with contextlib.suppress(Exception):
            await hub.push_to_user(
                request.requester_user_id,
                {
                    "type": "app_access_resolved",
                    "request_id": request.request_id,
                    "agent_id": request.agent_id,
                    "app_id": request.app_id,
                    "app_name": app_name,
                    "status": request.status,
                    "deep_link": deep_link,
                },
            )
    locale = resolve_user_locale(
        user_repo=server.services.user_repo,
        user_id=request.requester_user_id,
    )
    status_key = (
        "notifications.access_status.approved"
        if request.status == "approved"
        else "notifications.access_status.rejected"
    )
    notification = await _persist_notification(
        server,
        user_id=request.requester_user_id,
        kind="success" if request.status == "approved" else "info",
        title=tr("notifications.access_resolved.title", locale),
        body=tr(
            "notifications.access_resolved.body",
            locale,
            target=target,
            status=tr(status_key, locale),
        ),
        path=deep_link,
        dedupe_key=f"access_resolved:{request.request_id}",
    )
    if notification is not None and hub is not None:
        with contextlib.suppress(Exception):
            await hub.push_to_user(request.requester_user_id, notification)


def _out(server: OctopServer, row: AppAccessRequestRow) -> AccessRequestOut:
    assert server.services is not None
    requester = server.services.user_repo.get(row.requester_user_id)
    app_owner = (
        server.services.user_repo.get(row.app_owner_user_id)
        if row.app_owner_user_id is not None
        else None
    )
    agent_owner = server.services.user_repo.get(row.agent_owner_user_id)
    return AccessRequestOut(
        request_id=row.request_id,
        app_id=row.app_id,
        agent_id=row.agent_id,
        requester_username=requester.username if requester is not None else "",
        app_owner_username=app_owner.username if app_owner is not None else None,
        agent_owner_username=agent_owner.username if agent_owner is not None else "",
        needed=row.needed,
        note=row.note,
        status=row.status,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


@router.post(
    "",
    response_model=AccessRequestOut,
    summary="Request MiniApp access",
    description=(
        "Creates a pending request for the app owner and the expert owner. "
        "Repeating the call returns the existing pending request instead of "
        "creating a duplicate."
    ),
)
async def create_access_request(
    body: CreateAccessRequestBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> AccessRequestOut:
    request, created = _service(server).request_access_with_status(
        app_id=body.app_id,
        agent_id=body.agent_id,
        user_id=user.id,
        needed=body.needed,
        note=body.note,
    )
    if created:
        await _push_owner(server, request)
    return _out(server, request)


@router.get(
    "",
    response_model=AccessRequestsResponse,
    summary="List MiniApp access requests",
    description=(
        "``scope=owner`` lists pending requests where the caller owns the app "
        "or the hosting expert (admins see every pending request); "
        "``scope=mine`` lists the caller's own requests."
    ),
)
async def list_access_requests(
    scope: Literal["owner", "mine"] = "owner",
    app_id: str | None = None,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> AccessRequestsResponse:
    rows = _service(server).list_access_requests(
        user_id=user.id,
        is_admin=user.is_admin,
        scope=scope,
        app_id=app_id,
    )
    return AccessRequestsResponse(requests=[_out(server, row) for row in rows])


@router.post(
    "/{request_id}/approve",
    response_model=AccessRequestOut,
    summary="Approve an access request",
    description=(
        "App owner, expert owner, or admin. Each owner applies their half: the "
        "expert owner shares the expert, the app owner grants the MiniApp "
        "permission. The request becomes ``approved`` only once the requester "
        "can actually reach both; otherwise it stays ``pending`` for the other "
        "owner."
    ),
)
async def approve_access_request(
    request_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> AccessRequestOut:
    request = _service(server).resolve_access_request(
        request_id=request_id,
        actor_user_id=user.id,
        is_admin=user.is_admin,
        approve=True,
    )
    if request.status == APP_ACCESS_PENDING:
        # This owner granted their half; ask the other side to finish.
        await _push_owner(server, request, exclude_user_id=user.id)
    else:
        await _push_requester(server, request)
    return _out(server, request)


@router.post(
    "/{request_id}/reject",
    response_model=AccessRequestOut,
    summary="Reject an access request",
    description="Owner/admin only.",
)
async def reject_access_request(
    request_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> AccessRequestOut:
    request = _service(server).resolve_access_request(
        request_id=request_id,
        actor_user_id=user.id,
        is_admin=user.is_admin,
        approve=False,
    )
    await _push_requester(server, request)
    return _out(server, request)
