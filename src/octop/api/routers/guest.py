"""MiniApp guest portal and scoped session tokens."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from octop.api.common.client_ip import resolve_client_ip
from octop.api.deps import current_principal, current_user, get_server
from octop.api.routers.apps import _audit, _authorize
from octop.infra.apps import access as app_access
from octop.infra.apps.limits import public_slot
from octop.infra.auth.guest_sessions import AppSessionService
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.users.principals import Principal, PrincipalKind

router = APIRouter()

STATUS_PENDING = "pending"


def _sessions(server: OctopServer) -> AppSessionService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return AppSessionService(server.services.repos)


def _guest(principal: Principal) -> int:
    if principal.kind is not PrincipalKind.GUEST or principal.user_id is None:
        raise OctopError.localized(ErrorCode.GUEST_SCOPE_FORBIDDEN)
    return principal.user_id


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str = "app"
    app_id: str
    role: str


class GuestTokenOut(BaseModel):
    guest_token: str
    refresh_token: str
    guest_expires_in: int
    refresh_expires_in: int


class RefreshGuestBody(BaseModel):
    refresh_token: str = Field(min_length=1)


class GuestAppOut(BaseModel):
    app_id: str
    name: str
    role: str
    last_opened_at: int = 0


class GuestAppsOut(BaseModel):
    apps: list[GuestAppOut]


@router.post(
    "/apps/{app_id}/session",
    response_model=SessionOut,
    summary="Mint an app-scoped session for a platform member",
    description=(
        "Re-checks the caller's app role and returns a two-hour JWT scoped to "
        "this MiniApp. Pending members receive APP_MEMBER_PENDING."
    ),
)
async def create_app_session(
    app_id: str,
    response: Response,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SessionOut:
    assert server.services is not None
    collab = server.services.light_app_repo.get_collaborator(app_id, user.id)
    if collab is not None and collab.status == STATUS_PENDING:
        raise OctopError.localized(ErrorCode.APP_MEMBER_PENDING)
    _app_service, row, decision = _authorize(server, user, app_id, app_access.Action.VIEW_APP)
    user_row = server.services.user_repo.get(user.id)
    if user_row is None:
        raise OctopError.localized(ErrorCode.USER_DISABLED)
    token = _sessions(server).mint_app_token(
        user_row,
        row.app_id,
        role=decision.role or "view",
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.session.mint",
        target=app_id,
        payload={"scope": "app", "role": token.role},
    )
    _no_store(response)
    return SessionOut(
        access_token=token.access_token,
        expires_in=token.expires_in,
        app_id=token.app_id,
        role=token.role,
    )


@router.post(
    "/guest/apps/{app_id}/session",
    response_model=SessionOut,
    summary="Mint an app-scoped session for a guest",
    description=(
        "Requires a guest token and an active membership in the app. The app "
        "token carries the guest session id, so revoking the session ends it."
    ),
)
async def create_guest_app_session(
    app_id: str,
    response: Response,
    principal: Principal = Depends(current_principal),
    server: OctopServer = Depends(get_server),
) -> SessionOut:
    user_id = _guest(principal)
    sessions = _sessions(server)
    role = sessions.app_role(user_id, app_id)
    assert server.services is not None
    user_row = server.services.user_repo.get(user_id)
    if user_row is None:
        raise OctopError.localized(ErrorCode.APP_GUEST_DISABLED)
    token = sessions.mint_app_token(
        user_row,
        app_id,
        role=role,
        session_id=principal.session_id,
    )
    _audit(
        server,
        actor=f"user:{user_id}",
        action="light_app.session.mint",
        target=app_id,
        payload={"scope": "app", "role": role, "kind": "guest"},
    )
    _no_store(response)
    return SessionOut(
        access_token=token.access_token,
        expires_in=token.expires_in,
        app_id=token.app_id,
        role=token.role,
    )


@router.post(
    "/guest/session/refresh",
    response_model=GuestTokenOut,
    summary="Rotate a guest refresh token",
    description=(
        "Consumes the refresh token and returns a new guest token plus a new "
        "refresh token. Reusing an old refresh token returns APP_SESSION_INVALID."
    ),
)
async def refresh_guest_session(
    body: RefreshGuestBody,
    request: Request,
    response: Response,
    server: OctopServer = Depends(get_server),
) -> GuestTokenOut:
    async with public_slot(f"guest-refresh:{resolve_client_ip(request)}"):
        tokens = _sessions(server).rotate(body.refresh_token)
    _no_store(response)
    return GuestTokenOut(
        guest_token=tokens.guest_token,
        refresh_token=tokens.refresh_token,
        guest_expires_in=tokens.guest_expires_in,
        refresh_expires_in=tokens.refresh_expires_in,
    )


@router.delete(
    "/guest/sessions/current",
    status_code=204,
    summary="Revoke the current guest session",
)
async def revoke_guest_session(
    principal: Principal = Depends(current_principal),
    server: OctopServer = Depends(get_server),
) -> Response:
    user_id = _guest(principal)
    if not principal.session_id:
        raise OctopError.localized(ErrorCode.APP_SESSION_INVALID)
    _sessions(server).revoke(principal.session_id, user_id=user_id)
    return Response(status_code=204)


@router.get(
    "/guest/apps",
    response_model=GuestAppsOut,
    summary="List apps the guest has joined",
)
async def list_guest_apps(
    principal: Principal = Depends(current_principal),
    server: OctopServer = Depends(get_server),
) -> GuestAppsOut:
    user_id = _guest(principal)
    views = _sessions(server).list_guest_apps(user_id)
    return GuestAppsOut(apps=[GuestAppOut(**asdict(item)) for item in views])


@router.delete(
    "/guest/memberships/{app_id}",
    status_code=204,
    summary="Leave a MiniApp",
    description="Marks the guest's membership as removed; the account stays usable for other apps.",
)
async def leave_guest_app(
    app_id: str,
    principal: Principal = Depends(current_principal),
    server: OctopServer = Depends(get_server),
) -> Response:
    user_id = _guest(principal)
    if _sessions(server).leave_app(user_id, app_id):
        _audit(
            server,
            actor=f"user:{user_id}",
            action="light_app.member.leave",
            target=app_id,
            payload={"kind": "guest"},
        )
    return Response(status_code=204)


@router.delete(
    "/guest/account",
    status_code=204,
    summary="Delete the guest account",
    description=(
        "Disables the guest account, anonymizes its display name, marks every "
        "membership removed and revokes all sessions."
    ),
)
async def delete_guest_account(
    principal: Principal = Depends(current_principal),
    server: OctopServer = Depends(get_server),
) -> Response:
    user_id = _guest(principal)
    _sessions(server).delete_account(user_id)
    _audit(
        server,
        actor=f"user:{user_id}",
        action="light_app.guest.delete",
        target=f"guest:{user_id}",
        payload={"user_id": user_id},
    )
    return Response(status_code=204)
