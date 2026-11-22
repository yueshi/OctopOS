"""MiniApp members, external invites and join codes."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from octop.api.common.client_ip import resolve_client_ip
from octop.api.deps import current_user, get_server, optional_principal
from octop.api.routers.apps import _audit, _authorize, _service
from octop.infra.apps import access as app_access
from octop.infra.apps.host_policy import HostPolicyService
from octop.infra.apps.limits import public_slot
from octop.infra.apps.members import (
    HOST_APPROVED,
    STATUS_ACTIVE,
    STATUS_PENDING,
    AppMemberService,
    MemberView,
    Redemption,
)
from octop.infra.apps.service import AppRecord, AppService
from octop.infra.auth.guest_sessions import AppSessionService
from octop.infra.db.repos.app_join_codes import AppJoinCodeRow
from octop.infra.db.repos.app_member_invites import AppMemberInviteRow
from octop.infra.db.repos.users import UserRow
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.users.principals import Principal

router = APIRouter()

MemberPermission = Literal["view", "participate", "edit_data", "edit_structure"]
InviteAudience = Literal["guest", "member"]


def _services(server: OctopServer) -> SharedServices:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return server.services


def _member_service(server: OctopServer) -> AppMemberService:
    services = _services(server)
    return AppMemberService(
        app_repo=services.light_app_repo,
        user_repo=services.user_repo,
        invite_repo=services.app_member_invite_repo,
        join_code_repo=services.app_join_code_repo,
        host_policy=HostPolicyService(services.agent_repo),
    )


def _principal_user_row(server: OctopServer, principal: Principal | None) -> UserRow | None:
    if principal is None or principal.user_id is None:
        return None
    return _services(server).user_repo.get(principal.user_id)


def _read_access(
    server: OctopServer,
    user: User,
    app_id: str,
) -> tuple[AppService, AppRecord]:
    service, row, decision = _authorize(server, user, app_id, app_access.Action.VIEW_APP)
    if decision.role != app_access.ROLE_OWNER and not service.is_agent_owner(row.agent_id, user.id):
        raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
    return service, row


def _join_link(server: OctopServer, token: str) -> str:
    base = (getattr(server.config, "public_base_url", "") or "").strip().rstrip("/")
    path = f"/join?invite={token}"
    return f"{base}{path}" if base else path


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class MemberOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    kind: str = Field(description="member or guest.")
    permission: str = Field(description="Effective app role.")
    status: str = Field(description="active, pending or removed.")
    joined_via: str = Field(description="share, invite, join_code or im.")
    invited_by: int | None = None
    created_at: int
    last_active_at: int = 0


class MembersResponse(BaseModel):
    members: list[MemberOut]


class AddMemberBody(BaseModel):
    username: str | None = Field(default=None, description="Platform member username.")
    user_id: int | None = Field(default=None, description="Platform member user id.")
    permission: MemberPermission = Field(default="view", description="App role to grant.")


class AddGuestBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    permission: Literal["participate"] = "participate"


class UpdateMemberBody(BaseModel):
    permission: MemberPermission | None = None
    status: Literal["active", "pending", "removed"] | None = None


class InviteOut(BaseModel):
    invite_id: str
    invite_url: str = Field(
        default="",
        description="One-time invite URL; populated only by the create call.",
    )
    audience: str
    permission: str
    expires_at: int
    max_uses: int
    used_count: int
    host_status: str
    revoked_at: int | None = None


class InvitesResponse(BaseModel):
    invites: list[InviteOut]


class CreateInviteBody(BaseModel):
    audience: InviteAudience = "guest"
    permission: str = Field(default="participate")
    invitee_hint: str = Field(default="", max_length=200)
    ttl_seconds: int = Field(default=86400, ge=60, le=30 * 86400)
    max_uses: int = Field(default=1, ge=1, le=100)


class InviteTokenBody(BaseModel):
    token: str = Field(min_length=1)


class RedeemInviteBody(BaseModel):
    token: str = Field(min_length=1)
    display_name: str = Field(default="", max_length=100)


class UpdateInviteHostBody(BaseModel):
    host_status: Literal["approved", "rejected"]


class InvitePreviewOut(BaseModel):
    app_id: str
    app_name: str
    audience: str
    permission: str
    host_status: str


class RedemptionOut(BaseModel):
    app_id: str
    user_id: int
    role: str
    status: str
    display_name: str
    guest_token: str | None = Field(
        default=None,
        description="Guest-session JWT; present only for active guest memberships.",
    )
    refresh_token: str | None = Field(
        default=None,
        description="Opaque guest refresh token; rotate it at /api/guest/session/refresh.",
    )
    app_token: str | None = Field(
        default=None,
        description="Two-hour JWT scoped to this MiniApp.",
    )
    expires_in: int = 7200
    guest_expires_in: int | None = None
    refresh_expires_in: int | None = None


class JoinCodeOut(BaseModel):
    join_code_id: str
    code: str | None = Field(
        default=None,
        description="Plaintext code, returned only by the create call.",
    )
    audience: str
    permission: str
    expires_at: int
    max_uses: int
    used_count: int
    requires_approval: bool
    revoked_at: int | None = None


class JoinCodesResponse(BaseModel):
    join_codes: list[JoinCodeOut]


class CreateJoinCodeBody(BaseModel):
    audience: InviteAudience = "guest"
    permission: str = Field(default="participate")
    ttl_seconds: int = Field(default=7 * 86400, ge=60, le=90 * 86400)
    max_uses: int = Field(default=10, ge=1, le=1000)
    requires_approval: bool = False


class JoinCodeValueBody(BaseModel):
    code: str = Field(min_length=1)


class RedeemJoinCodeBody(BaseModel):
    code: str = Field(min_length=1)
    display_name: str = Field(default="", max_length=100)


class JoinCodePreviewOut(BaseModel):
    app_id: str
    app_name: str
    audience: str
    role: str
    requires_approval: bool
    host_approval_required: bool = False


def _member_out(view: MemberView) -> MemberOut:
    return MemberOut(**asdict(view))


def _invite_out(row: AppMemberInviteRow, *, invite_url: str = "") -> InviteOut:
    return InviteOut(
        invite_id=row.invite_id,
        invite_url=invite_url,
        audience=row.audience,
        permission=row.permission,
        expires_at=row.expires_at,
        max_uses=row.max_uses,
        used_count=row.used_count,
        host_status=row.host_status,
        revoked_at=row.revoked_at,
    )


def _redemption_out(server: OctopServer, result: Redemption) -> RedemptionOut:
    out = RedemptionOut(**asdict(result))
    user_row = _services(server).user_repo.get(result.user_id)
    if user_row is None:
        return out
    sessions = AppSessionService(_services(server).repos)
    if user_row.kind == "guest":
        # Pending guests still get a session so they can refresh/poll after the
        # owner approves; the app token is minted only for active memberships.
        guest = sessions.create_guest_session(result.user_id)
        out.guest_token = guest.guest_token
        out.refresh_token = guest.refresh_token
        out.guest_expires_in = guest.guest_expires_in
        out.refresh_expires_in = guest.refresh_expires_in
        if result.status != STATUS_ACTIVE:
            return out
        token = sessions.mint_app_token(
            user_row,
            result.app_id,
            role=result.role,
            session_id=guest.session_id,
        )
        out.app_token = token.access_token
        out.expires_in = token.expires_in
        return out
    if result.status != STATUS_ACTIVE:
        return out
    token = sessions.mint_app_token(user_row, result.app_id, role=result.role)
    out.app_token = token.access_token
    out.expires_in = token.expires_in
    return out


def _join_code_out(row: AppJoinCodeRow, *, code: str | None = None) -> JoinCodeOut:
    return JoinCodeOut(
        join_code_id=row.join_code_id,
        code=code,
        audience=row.audience,
        permission=row.permission,
        expires_at=row.expires_at,
        max_uses=row.max_uses,
        used_count=row.used_count,
        requires_approval=row.requires_approval,
        revoked_at=row.revoked_at,
    )


@router.get(
    "/apps/{app_id}/members",
    response_model=MembersResponse,
    summary="List MiniApp members",
    description="App owners, admins and the agent owner can read the member list.",
)
async def list_app_members(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> MembersResponse:
    _app_service, app = _read_access(server, user, app_id)
    members = _member_service(server).list_members(app)
    return MembersResponse(members=[_member_out(item) for item in members])


@router.post(
    "/apps/{app_id}/members",
    response_model=MemberOut,
    summary="Add a platform member",
    description="Adds an existing platform member (not a guest). Owner or admin only.",
)
async def add_app_member(
    app_id: str,
    body: AddMemberBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> MemberOut:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    view = _member_service(server).add_member(
        app,
        username=body.username,
        user_id=body.user_id,
        permission=body.permission,
        actor=user.id,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.member.join",
        target=app_id,
        payload={
            "user_id": view.user_id,
            "permission": view.permission,
            "kind": view.kind,
        },
    )
    return _member_out(view)


@router.post(
    "/apps/{app_id}/guests",
    response_model=MemberOut,
    summary="Add a guest member",
    description="Creates a guest account and adds it to the app with participate access.",
)
async def add_app_guest(
    app_id: str,
    body: AddGuestBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> MemberOut:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    view = _member_service(server).add_guest(
        app,
        display_name=body.display_name,
        permission=body.permission,
        actor=user.id,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.member.join",
        target=app_id,
        payload={"user_id": view.user_id, "permission": view.permission, "kind": "guest"},
    )
    return _member_out(view)


@router.patch(
    "/apps/{app_id}/members/{user_id}",
    response_model=MemberOut,
    summary="Update a member role or status",
    description="Owner or admin only. Use status=removed instead of hard deletion.",
)
async def update_app_member(
    app_id: str,
    user_id: int,
    body: UpdateMemberBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> MemberOut:
    service = _service(server)
    is_host = service.is_agent_owner(service.get(app_id).agent_id, user.id)
    host_approval = (
        is_host and body.permission is None and body.status in {STATUS_ACTIVE, "removed"}
    )
    if host_approval:
        app = _authorize(server, user, app_id, app_access.Action.VIEW_APP)[1]
    else:
        app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    before = _services(server).light_app_repo.get_collaborator(app_id, user_id)
    view = _member_service(server).update_member(
        app,
        user_id=user_id,
        permission=body.permission,
        status=body.status,
        actor=user.id,
    )
    if host_approval:
        action = (
            "light_app.host.approve" if view.status == STATUS_ACTIVE else "light_app.host.reject"
        )
    else:
        action = (
            "light_app.member.approve"
            if before is not None
            and before.status == STATUS_PENDING
            and view.status == STATUS_ACTIVE
            else "light_app.member.role"
        )
    _audit(
        server,
        actor=f"user:{user.id}",
        action=action,
        target=app_id,
        payload={
            "user_id": view.user_id,
            "permission": view.permission,
            "status": view.status,
        },
    )
    return _member_out(view)


@router.delete(
    "/apps/{app_id}/members/{user_id}",
    status_code=204,
    summary="Remove a member",
    description="Marks the collaborator as removed; the row is retained for audit.",
)
async def remove_app_member(
    app_id: str,
    user_id: int,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    _member_service(server).remove_member(app, user_id=user_id, actor=user.id)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.member.remove",
        target=app_id,
        payload={"user_id": user_id},
    )
    return Response(status_code=204)


@router.get(
    "/apps/{app_id}/invites",
    response_model=InvitesResponse,
    summary="List external invites",
)
async def list_app_invites(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> InvitesResponse:
    app = _read_access(server, user, app_id)[1]
    rows = _member_service(server).list_invites(app)
    return InvitesResponse(invites=[_invite_out(item) for item in rows])


@router.post(
    "/apps/{app_id}/invites",
    response_model=InviteOut,
    summary="Create an external invite",
    description=(
        "Creates a one-time (by default) invite link. The plaintext token is "
        "returned once inside invite_url; only its hash is stored."
    ),
)
async def create_app_invite(
    app_id: str,
    body: CreateInviteBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> InviteOut:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    row, token = _member_service(server).create_invite(
        app,
        audience=body.audience,
        permission=body.permission,
        invitee_hint=body.invitee_hint,
        ttl_seconds=body.ttl_seconds,
        max_uses=body.max_uses,
        actor=user.id,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.invite.create",
        target=app_id,
        payload={
            "invite_id": row.invite_id,
            "audience": row.audience,
            "permission": row.permission,
            "host_status": row.host_status,
        },
    )
    return _invite_out(row, invite_url=_join_link(server, token))


@router.delete(
    "/apps/{app_id}/invites/{invite_id}",
    status_code=204,
    summary="Revoke an external invite",
)
async def revoke_app_invite(
    app_id: str,
    invite_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    _member_service(server).revoke_invite(app, invite_id=invite_id)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.invite.revoke",
        target=app_id,
        payload={"invite_id": invite_id},
    )
    return Response(status_code=204)


@router.patch(
    "/apps/{app_id}/invites/{invite_id}",
    response_model=InviteOut,
    summary="Approve or reject an invite at the host",
    description="The app owner or the agent owner can resolve host approval.",
)
async def update_app_invite_host(
    app_id: str,
    invite_id: str,
    body: UpdateInviteHostBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> InviteOut:
    app = _read_access(server, user, app_id)[1]
    row = _member_service(server).update_invite_host_status(
        app,
        invite_id=invite_id,
        host_status=body.host_status,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action=(
            "light_app.host.approve"
            if body.host_status == HOST_APPROVED
            else "light_app.host.reject"
        ),
        target=app_id,
        payload={"invite_id": invite_id},
    )
    return _invite_out(row)


@router.post(
    "/apps/invites/preview",
    response_model=InvitePreviewOut,
    summary="Preview an external invite",
    description="Read-only; does not consume the invite. Rate limited by IP and token.",
)
async def preview_app_invite(
    body: InviteTokenBody,
    request: Request,
    server: OctopServer = Depends(get_server),
) -> InvitePreviewOut:
    async with (
        public_slot(f"invite:{resolve_client_ip(request)}"),
        public_slot(f"invite-code:{_fingerprint(body.token)}"),
    ):
        preview = _member_service(server).preview_invite(body.token)
    return InvitePreviewOut(**asdict(preview))


@router.post(
    "/apps/invites/redeem",
    response_model=RedemptionOut,
    summary="Redeem an external invite",
    description=(
        "Consumes one use atomically. Guest audiences create a guest account; "
        "member audiences require a platform login."
    ),
)
async def redeem_app_invite(
    body: RedeemInviteBody,
    request: Request,
    response: Response,
    server: OctopServer = Depends(get_server),
    principal: Principal | None = Depends(optional_principal),
) -> RedemptionOut:
    async with (
        public_slot(f"invite:{resolve_client_ip(request)}"),
        public_slot(f"invite-code:{_fingerprint(body.token)}"),
    ):
        result = _member_service(server).redeem_invite(
            body.token,
            display_name=body.display_name,
            user_row=_principal_user_row(server, principal),
        )
    _audit(
        server,
        actor=f"user:{result.user_id}",
        action="light_app.member.join",
        target=result.app_id,
        payload={
            "role": result.role,
            "status": result.status,
            "via": "invite",
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return _redemption_out(server, result)


@router.get(
    "/apps/{app_id}/join-codes",
    response_model=JoinCodesResponse,
    summary="List join codes",
)
async def list_app_join_codes(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> JoinCodesResponse:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    rows = _member_service(server).list_join_codes(app)
    return JoinCodesResponse(join_codes=[_join_code_out(item) for item in rows])


@router.post(
    "/apps/{app_id}/join-codes",
    response_model=JoinCodeOut,
    summary="Create a join code",
    description="Returns the plaintext code once; only its hash is stored.",
)
async def create_app_join_code(
    app_id: str,
    body: CreateJoinCodeBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> JoinCodeOut:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    row, code = _member_service(server).create_join_code(
        app,
        audience=body.audience,
        permission=body.permission,
        ttl_seconds=body.ttl_seconds,
        max_uses=body.max_uses,
        requires_approval=body.requires_approval,
        actor=user.id,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.join_code.create",
        target=app_id,
        payload={
            "join_code_id": row.join_code_id,
            "audience": row.audience,
            "permission": row.permission,
        },
    )
    return _join_code_out(row, code=code)


@router.delete(
    "/apps/{app_id}/join-codes/{join_code_id}",
    status_code=204,
    summary="Revoke a join code",
)
async def revoke_app_join_code(
    app_id: str,
    join_code_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    app = _authorize(server, user, app_id, app_access.Action.MANAGE_MEMBERS)[1]
    _member_service(server).revoke_join_code(app, join_code_id=join_code_id)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.join_code.revoke",
        target=app_id,
        payload={"join_code_id": join_code_id},
    )
    return Response(status_code=204)


@router.post(
    "/apps/join/preview",
    response_model=JoinCodePreviewOut,
    summary="Preview a join code",
    description="Read-only; does not consume the code. Rate limited by IP and code.",
)
async def preview_app_join_code(
    body: JoinCodeValueBody,
    request: Request,
    server: OctopServer = Depends(get_server),
) -> JoinCodePreviewOut:
    async with (
        public_slot(f"join:{resolve_client_ip(request)}"),
        public_slot(f"join-code:{_fingerprint(body.code.strip().upper())}"),
    ):
        preview = _member_service(server).preview_join_code(body.code)
    return JoinCodePreviewOut(**asdict(preview))


@router.post(
    "/apps/join/redeem",
    response_model=RedemptionOut,
    summary="Redeem a join code",
    description=(
        "Consumes one use atomically. Guest audiences create a guest account; "
        "member audiences require a platform login."
    ),
)
async def redeem_app_join_code(
    body: RedeemJoinCodeBody,
    request: Request,
    response: Response,
    server: OctopServer = Depends(get_server),
    principal: Principal | None = Depends(optional_principal),
) -> RedemptionOut:
    async with (
        public_slot(f"join:{resolve_client_ip(request)}"),
        public_slot(f"join-code:{_fingerprint(body.code.strip().upper())}"),
    ):
        result = _member_service(server).redeem_join_code(
            body.code,
            display_name=body.display_name,
            user_row=_principal_user_row(server, principal),
        )
    _audit(
        server,
        actor=f"user:{result.user_id}",
        action="light_app.join_code.redeem",
        target=result.app_id,
        payload={
            "role": result.role,
            "status": result.status,
            "via": "join_code",
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return _redemption_out(server, result)
