"""MiniApp members, external invites and self-service join codes.

This module owns the membership lifecycle. Authorization stays in the HTTP /
tool layer (`authorize()`), and the repository layer stays SQL-only.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from octop.infra.apps.host_policy import (
    HOST_PENDING as HOST_GATE_PENDING,
)
from octop.infra.apps.host_policy import (
    HostGate,
    HostPolicyService,
)
from octop.infra.apps.models import (
    MAX_COLLABORATORS,
    MAX_GUESTS,
    validate_collaborator_permission,
)
from octop.infra.db.repos.app_join_codes import AppJoinCodeRepo, AppJoinCodeRow
from octop.infra.db.repos.app_member_invites import AppMemberInviteRepo, AppMemberInviteRow
from octop.infra.db.repos.apps import LightAppCollaboratorRow, LightAppRepo
from octop.infra.db.repos.users import UserRepo, UserRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_short_id, new_ulid

AUDIENCE_GUEST = "guest"
AUDIENCE_MEMBER = "member"
AUDIENCES = frozenset({AUDIENCE_GUEST, AUDIENCE_MEMBER})

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_REMOVED = "removed"
STATUSES = frozenset({STATUS_ACTIVE, STATUS_PENDING, STATUS_REMOVED})

JOINED_VIA_SHARE = "share"
JOINED_VIA_INVITE = "invite"
JOINED_VIA_JOIN_CODE = "join_code"

HOST_NOT_REQUIRED = "not_required"
HOST_PENDING = "pending"
HOST_APPROVED = "approved"
HOST_REJECTED = "rejected"

MAX_INVITE_TTL_SECONDS = 30 * 24 * 3600
MAX_JOIN_CODE_TTL_SECONDS = 90 * 24 * 3600
MAX_INVITE_USES = 100
MAX_JOIN_CODE_USES = 1000
MAX_DISPLAY_NAME_LEN = 100
GUEST_FALLBACK_NAME = "Guest"

_JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class MemberApp(Protocol):
    @property
    def app_id(self) -> str: ...

    @property
    def agent_id(self) -> str: ...

    @property
    def owner_user_id(self) -> int: ...

    @property
    def name(self) -> str: ...


@dataclass(frozen=True)
class MemberView:
    user_id: int
    username: str
    display_name: str
    kind: str
    permission: str
    status: str
    joined_via: str
    invited_by: int | None
    created_at: int
    last_active_at: int = 0


@dataclass(frozen=True)
class InvitePreview:
    app_id: str
    app_name: str
    audience: str
    permission: str
    host_status: str


@dataclass(frozen=True)
class JoinCodePreview:
    app_id: str
    app_name: str
    audience: str
    role: str
    requires_approval: bool
    host_approval_required: bool = False


@dataclass(frozen=True)
class Redemption:
    app_id: str
    user_id: int
    role: str
    status: str
    display_name: str


def clean_display_name(raw: str | None) -> str:
    """Strip control/format characters and cap the stored display name."""
    text = "".join(
        char for char in str(raw or "") if not unicodedata.category(char).startswith("C")
    )
    text = " ".join(text.split())[:MAX_DISPLAY_NAME_LEN]
    return text or GUEST_FALLBACK_NAME


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _invalid_audience(code: ErrorCode, message: str) -> OctopError:
    return OctopError(code, message)


def _validate_audience_permission(
    audience: str,
    permission: str,
    *,
    code: ErrorCode,
    allow_participate: bool,
) -> tuple[str, str]:
    """Return the normalized (audience, permission) pair or raise."""
    if audience not in AUDIENCES:
        raise _invalid_audience(code, "audience must be 'guest' or 'member'")
    if audience == AUDIENCE_GUEST:
        if permission and permission != "participate":
            raise _invalid_audience(code, "guest invitations are limited to participate")
        return audience, "participate"
    allowed = {"view", "participate"} if allow_participate else {"view"}
    if permission not in allowed:
        choices = " or ".join(sorted(allowed))
        raise _invalid_audience(code, f"member permission must be {choices}")
    return audience, permission


def _clamp_ttl(ttl_seconds: int, *, maximum: int) -> int:
    return max(60, min(int(ttl_seconds), maximum))


def _clamp_uses(max_uses: int, *, maximum: int) -> int:
    return max(1, min(int(max_uses), maximum))


def _new_join_code() -> str:
    return "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(8))


class AppMemberService:
    def __init__(
        self,
        *,
        app_repo: LightAppRepo,
        user_repo: UserRepo,
        invite_repo: AppMemberInviteRepo | None = None,
        join_code_repo: AppJoinCodeRepo | None = None,
        host_policy: HostPolicyService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app_repo = app_repo
        self._user_repo = user_repo
        self._invite_repo = invite_repo
        self._join_code_repo = join_code_repo
        self._host_policy = host_policy
        self._clock = clock

    # ----- members ---------------------------------------------------------

    def list_members(self, app: MemberApp, *, include_removed: bool = False) -> list[MemberView]:
        views: list[MemberView] = []
        for collab in self._app_repo.list_collaborators(app.app_id):
            if not include_removed and collab.status == STATUS_REMOVED:
                continue
            account = self._user_repo.get(collab.user_id)
            if account is None:
                continue
            views.append(self._member_view(account, collab))
        return views

    def add_member(
        self,
        app: MemberApp,
        *,
        username: str | None = None,
        user_id: int | None = None,
        permission: str,
        actor: int,
    ) -> MemberView:
        account = self._resolve_member(username=username, user_id=user_id)
        if account.id == app.owner_user_id:
            raise OctopError(
                ErrorCode.APP_SHARE_INVALID,
                "the owner always has full access",
            )
        self._ensure_member_capacity(app, account.id)
        gate = self._host_gate(app, user_id=account.id)
        status = STATUS_ACTIVE
        if gate is not None and not gate.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        existing = self._app_repo.get_collaborator(app.app_id, account.id)
        if (
            gate is not None
            and gate.host_status == HOST_GATE_PENDING
            and (existing is None or existing.status != STATUS_ACTIVE)
        ):
            status = STATUS_PENDING
        collab = self._app_repo.upsert_collaborator(
            app.app_id,
            account.id,
            validate_collaborator_permission(permission),
            status=status,
            joined_via=JOINED_VIA_SHARE,
            invited_by=actor,
        )
        return self._member_view(account, collab)

    def add_guest(
        self,
        app: MemberApp,
        *,
        display_name: str,
        permission: str = "participate",
        actor: int,
        joined_via: str = JOINED_VIA_INVITE,
    ) -> MemberView:
        if permission != "participate":
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "guests can only hold participate")
        self._ensure_guest_capacity(app)
        gate = self._host_gate(app, guest=True)
        if gate is not None and not gate.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        status = (
            STATUS_PENDING
            if gate is not None and gate.host_status == HOST_GATE_PENDING
            else STATUS_ACTIVE
        )
        return self._create_guest_for_app(
            app,
            display_name,
            joined_via=joined_via,
            invited_by=actor,
            status=status,
        )

    def update_member(
        self,
        app: MemberApp,
        *,
        user_id: int,
        permission: str | None = None,
        status: str | None = None,
        actor: int,
    ) -> MemberView:
        if user_id == app.owner_user_id:
            raise OctopError(
                ErrorCode.APP_SHARE_INVALID,
                "the owner always has full access",
            )
        collab = self._app_repo.get_collaborator(app.app_id, user_id)
        if collab is None:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "member not found")
        account = self._user_repo.get(user_id)
        if account is None:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "member not found")
        if permission is not None:
            if account.kind == "guest":
                if permission != "participate":
                    raise OctopError(
                        ErrorCode.APP_SHARE_INVALID,
                        "guests can only hold participate",
                    )
            else:
                permission = validate_collaborator_permission(permission)
        if status is not None and status not in STATUSES:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "invalid member status")
        updated = self._app_repo.update_collaborator(
            app.app_id,
            user_id,
            permission=permission,
            status=status,
        )
        if updated is None:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "member not found")
        return self._member_view(account, updated)

    def remove_member(self, app: MemberApp, *, user_id: int, actor: int) -> bool:
        if user_id == app.owner_user_id:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "the owner cannot be removed")
        updated = self._app_repo.update_collaborator(
            app.app_id,
            user_id,
            status=STATUS_REMOVED,
        )
        return updated is not None

    # ----- invites ---------------------------------------------------------

    def create_invite(
        self,
        app: MemberApp,
        *,
        audience: str,
        permission: str,
        invitee_hint: str,
        ttl_seconds: int,
        max_uses: int,
        actor: int,
    ) -> tuple[AppMemberInviteRow, str]:
        repo = self._require_invite_repo()
        clean_audience, clean_permission = _validate_audience_permission(
            audience,
            permission,
            code=ErrorCode.APP_SHARE_INVALID,
            allow_participate=True,
        )
        host_status = HOST_NOT_REQUIRED
        if clean_audience == AUDIENCE_GUEST:
            gate = self._host_gate(app, guest=True)
            if gate is not None and not gate.allowed:
                raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
            if gate is not None and gate.host_status == HOST_GATE_PENDING:
                host_status = HOST_GATE_PENDING
        token = secrets.token_urlsafe(32)
        now = int(self._clock())
        row = repo.create(
            invite_id=f"inv_{new_short_id(16)}",
            app_id=app.app_id,
            token_hash=_hash(token),
            audience=clean_audience,
            permission=clean_permission,
            invitee_hint=clean_display_name(invitee_hint) if invitee_hint else "",
            expires_at=now + _clamp_ttl(ttl_seconds, maximum=MAX_INVITE_TTL_SECONDS),
            max_uses=_clamp_uses(max_uses, maximum=MAX_INVITE_USES),
            host_status=host_status,
            created_by=actor,
        )
        return row, token

    def list_invites(self, app: MemberApp) -> list[AppMemberInviteRow]:
        return self._require_invite_repo().list_for_app(app.app_id)

    def preview_invite(self, token: str) -> InvitePreview:
        row = self._usable_invite(token, require_host_approval=True)
        app = self._require_app(row.app_id)
        if row.audience == AUDIENCE_GUEST:
            gate = self._host_gate(app, guest=True)
            if gate is not None and not gate.allowed:
                raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
            if (
                gate is not None
                and gate.host_status == HOST_GATE_PENDING
                and row.host_status != HOST_APPROVED
            ):
                raise OctopError.localized(ErrorCode.APP_HOST_APPROVAL_REQUIRED)
        return InvitePreview(
            app_id=row.app_id,
            app_name=app.name,
            audience=row.audience,
            permission=row.permission,
            host_status=row.host_status,
        )

    def redeem_invite(
        self,
        token: str,
        *,
        display_name: str = "",
        user_row: UserRow | None = None,
    ) -> Redemption:
        repo = self._require_invite_repo()
        row = self._usable_invite(token, require_host_approval=True)
        app = self._require_app(row.app_id)
        if row.audience == AUDIENCE_MEMBER:
            account = self._require_member_identity(user_row)
            self._ensure_member_capacity(app, account.id)
            gate = self._host_gate(app, user_id=account.id)
        else:
            account = None
            self._ensure_guest_capacity(app)
            gate = self._host_gate(app, guest=True)
        if gate is not None and not gate.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        if (
            gate is not None
            and gate.host_status == HOST_GATE_PENDING
            and row.host_status != HOST_APPROVED
        ):
            raise OctopError.localized(ErrorCode.APP_HOST_APPROVAL_REQUIRED)
        consumed = repo.consume(row.token_hash, now=int(self._clock()))
        if consumed is None:
            raise OctopError.localized(ErrorCode.APP_INVITE_INVALID)
        if consumed.audience == AUDIENCE_MEMBER:
            assert account is not None
            collab = self._app_repo.upsert_collaborator(
                app.app_id,
                account.id,
                consumed.permission,
                status=STATUS_ACTIVE,
                joined_via=JOINED_VIA_INVITE,
                invited_by=consumed.created_by,
            )
            name = account.display_name or account.username
            return Redemption(
                app_id=app.app_id,
                user_id=account.id,
                role=collab.permission,
                status=collab.status,
                display_name=name,
            )
        guest = self._create_guest_for_app(
            app,
            display_name or consumed.invitee_hint,
            joined_via=JOINED_VIA_INVITE,
            invited_by=consumed.created_by,
            guest_user=self._reusable_guest(user_row),
        )
        return Redemption(
            app_id=app.app_id,
            user_id=guest.user_id,
            role=guest.permission,
            status=guest.status,
            display_name=guest.display_name,
        )

    def update_invite_host_status(
        self,
        app: MemberApp,
        *,
        invite_id: str,
        host_status: str,
    ) -> AppMemberInviteRow:
        if host_status not in {HOST_APPROVED, HOST_REJECTED}:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "invalid host status")
        updated = self._require_invite_repo().set_host_status(
            app.app_id,
            invite_id,
            host_status=host_status,
        )
        if updated is None:
            raise OctopError.localized(ErrorCode.APP_INVITE_INVALID)
        return updated

    def revoke_invite(self, app: MemberApp, *, invite_id: str) -> bool:
        return self._require_invite_repo().revoke(
            app.app_id,
            invite_id,
            revoked_at=int(self._clock()),
        )

    # ----- join codes ------------------------------------------------------

    def create_join_code(
        self,
        app: MemberApp,
        *,
        audience: str,
        permission: str,
        ttl_seconds: int,
        max_uses: int,
        requires_approval: bool,
        actor: int,
    ) -> tuple[AppJoinCodeRow, str]:
        repo = self._require_join_code_repo()
        clean_audience, clean_permission = _validate_audience_permission(
            audience,
            permission,
            code=ErrorCode.APP_SHARE_INVALID,
            allow_participate=True,
        )
        if clean_audience == AUDIENCE_GUEST:
            gate = self._host_gate(app, guest=True)
            if gate is not None and not gate.allowed:
                raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        code = _new_join_code()
        now = int(self._clock())
        row = repo.create(
            join_code_id=f"jc_{new_short_id(16)}",
            code_hash=_hash(code),
            app_id=app.app_id,
            audience=clean_audience,
            permission=clean_permission,
            expires_at=now + _clamp_ttl(ttl_seconds, maximum=MAX_JOIN_CODE_TTL_SECONDS),
            max_uses=_clamp_uses(max_uses, maximum=MAX_JOIN_CODE_USES),
            requires_approval=requires_approval,
            created_by=actor,
        )
        return row, code

    def list_join_codes(self, app: MemberApp) -> list[AppJoinCodeRow]:
        return self._require_join_code_repo().list_for_app(app.app_id)

    def preview_join_code(self, code: str) -> JoinCodePreview:
        row = self._usable_join_code(code)
        app = self._require_app(row.app_id)
        host_pending = False
        if row.audience == AUDIENCE_GUEST:
            gate = self._host_gate(app, guest=True)
            if gate is not None and not gate.allowed:
                raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
            host_pending = gate is not None and gate.host_status == HOST_GATE_PENDING
        return JoinCodePreview(
            app_id=row.app_id,
            app_name=app.name,
            audience=row.audience,
            role=row.permission,
            requires_approval=row.requires_approval,
            host_approval_required=host_pending,
        )

    def redeem_join_code(
        self,
        code: str,
        *,
        display_name: str = "",
        user_row: UserRow | None = None,
    ) -> Redemption:
        repo = self._require_join_code_repo()
        row = self._usable_join_code(code)
        app = self._require_app(row.app_id)
        if row.audience == AUDIENCE_MEMBER:
            account = self._require_member_identity(user_row)
            self._ensure_member_capacity(app, account.id)
            gate = self._host_gate(app, user_id=account.id)
        else:
            account = None
            self._ensure_guest_capacity(app)
            gate = self._host_gate(app, guest=True)
        if gate is not None and not gate.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        consumed = repo.consume(row.code_hash, now=int(self._clock()))
        if consumed is None:
            raise OctopError.localized(ErrorCode.APP_JOIN_CODE_INVALID)
        host_pending = gate is not None and gate.host_status == HOST_GATE_PENDING
        status = STATUS_PENDING if consumed.requires_approval or host_pending else STATUS_ACTIVE
        if consumed.audience == AUDIENCE_MEMBER:
            assert account is not None
            collab = self._app_repo.upsert_collaborator(
                app.app_id,
                account.id,
                consumed.permission,
                status=status,
                joined_via=JOINED_VIA_JOIN_CODE,
                invited_by=consumed.created_by,
            )
            name = account.display_name or account.username
            return Redemption(
                app_id=app.app_id,
                user_id=account.id,
                role=collab.permission,
                status=collab.status,
                display_name=name,
            )
        guest = self._create_guest_for_app(
            app,
            display_name,
            joined_via=JOINED_VIA_JOIN_CODE,
            invited_by=consumed.created_by,
            status=status,
            guest_user=self._reusable_guest(user_row),
        )
        return Redemption(
            app_id=app.app_id,
            user_id=guest.user_id,
            role=guest.permission,
            status=guest.status,
            display_name=guest.display_name,
        )

    def revoke_join_code(self, app: MemberApp, *, join_code_id: str) -> bool:
        return self._require_join_code_repo().revoke(
            app.app_id,
            join_code_id,
            revoked_at=int(self._clock()),
        )

    # ----- internals -------------------------------------------------------

    def _host_gate(
        self,
        app: MemberApp,
        *,
        user_id: int | None = None,
        guest: bool = False,
    ) -> HostGate | None:
        if self._host_policy is None:
            return None
        return self._host_policy.gate(app.agent_id, user_id=user_id, guest=guest)

    def _member_view(
        self,
        account: UserRow,
        collab: LightAppCollaboratorRow,
    ) -> MemberView:
        return MemberView(
            user_id=account.id,
            username=account.username,
            display_name=str(account.display_name or ""),
            kind=account.kind,
            permission=collab.permission,
            status=collab.status,
            joined_via=collab.joined_via,
            invited_by=collab.invited_by,
            created_at=collab.created_at,
        )

    def _resolve_member(self, *, username: str | None, user_id: int | None) -> UserRow:
        if (username is None) == (user_id is None):
            raise OctopError(
                ErrorCode.APP_SHARE_INVALID,
                "provide exactly one of username or user_id",
            )
        account = (
            self._user_repo.get_member_by_username(username.strip())
            if username is not None
            else self._user_repo.get(int(user_id))
            if user_id is not None
            else None
        )
        if account is None or account.kind != "member" or account.disabled:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "unknown or disabled member")
        return account

    def _require_member_identity(self, user_row: UserRow | None) -> UserRow:
        if user_row is None or user_row.kind != "member" or user_row.disabled:
            raise OctopError(
                ErrorCode.AUTH_FAILED,
                "sign in as a platform member to redeem this invite",
            )
        return user_row

    def _member_counts(self, app: MemberApp) -> tuple[int, int, set[int]]:
        members = 0
        guests = 0
        ids: set[int] = set()
        for collab in self._app_repo.list_collaborators(app.app_id):
            if collab.status == STATUS_REMOVED:
                continue
            account = self._user_repo.get(collab.user_id)
            ids.add(collab.user_id)
            if account is not None and account.kind == "guest":
                guests += 1
            else:
                members += 1
        return members, guests, ids

    def _ensure_member_capacity(self, app: MemberApp, user_id: int) -> None:
        members, _guests, ids = self._member_counts(app)
        if user_id not in ids and members >= MAX_COLLABORATORS:
            raise OctopError(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"at most {MAX_COLLABORATORS} members",
            )

    def _ensure_guest_capacity(self, app: MemberApp) -> None:
        _members, guests, _ids = self._member_counts(app)
        limit = (
            self._host_policy.guest_limit(app.agent_id)
            if self._host_policy is not None
            else MAX_GUESTS
        )
        if guests >= limit:
            raise OctopError(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"at most {limit} guest members",
            )

    def _create_guest_for_app(
        self,
        app: MemberApp,
        display_name: str,
        *,
        joined_via: str,
        invited_by: int,
        status: str = STATUS_ACTIVE,
        guest_user: UserRow | None = None,
    ) -> MemberView:
        account: UserRow | None
        if guest_user is not None:
            account = guest_user
            user_id = guest_user.id
        else:
            name = clean_display_name(display_name)
            user_id = self._user_repo.create(
                username=f"guest_{new_ulid().lower()}",
                password_hash=None,
                role="user",
                display_name=name,
                kind="guest",
            )
            account = self._user_repo.get(user_id)
            if account is None:
                raise RuntimeError(f"guest user missing after create: {user_id}")
        collab = self._app_repo.upsert_collaborator(
            app.app_id,
            user_id,
            "participate",
            status=status,
            joined_via=joined_via,
            invited_by=invited_by,
        )
        return self._member_view(account, collab)

    @staticmethod
    def _reusable_guest(user_row: UserRow | None) -> UserRow | None:
        if user_row is None or user_row.kind != "guest" or user_row.disabled:
            return None
        return user_row

    def _require_app(self, app_id: str) -> MemberApp:
        app = self._app_repo.get(app_id)
        if app is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return app

    def _usable_invite(self, token: str, *, require_host_approval: bool) -> AppMemberInviteRow:
        row = self._require_invite_repo().get_by_token_hash(_hash(token))
        if row is None or row.revoked_at is not None:
            raise OctopError.localized(ErrorCode.APP_INVITE_INVALID)
        if row.host_status == HOST_REJECTED:
            raise OctopError.localized(ErrorCode.APP_INVITE_INVALID)
        if row.host_status == HOST_PENDING:
            if require_host_approval:
                raise OctopError.localized(ErrorCode.APP_HOST_APPROVAL_REQUIRED)
            raise OctopError.localized(ErrorCode.APP_INVITE_INVALID)
        if row.expires_at <= int(self._clock()) or row.used_count >= row.max_uses:
            raise OctopError.localized(ErrorCode.APP_INVITE_INVALID)
        return row

    def _usable_join_code(self, code: str) -> AppJoinCodeRow:
        row = self._require_join_code_repo().get_by_code_hash(_hash(code.strip().upper()))
        if row is None or row.revoked_at is not None:
            raise OctopError.localized(ErrorCode.APP_JOIN_CODE_INVALID)
        if row.expires_at <= int(self._clock()) or row.used_count >= row.max_uses:
            raise OctopError.localized(ErrorCode.APP_JOIN_CODE_INVALID)
        return row

    def _require_invite_repo(self) -> AppMemberInviteRepo:
        if self._invite_repo is None:
            raise RuntimeError("invite repository is not configured")
        return self._invite_repo

    def _require_join_code_repo(self) -> AppJoinCodeRepo:
        if self._join_code_repo is None:
            raise RuntimeError("join code repository is not configured")
        return self._join_code_repo


__all__ = [
    "AUDIENCES",
    "HOST_APPROVED",
    "HOST_NOT_REQUIRED",
    "HOST_PENDING",
    "HOST_REJECTED",
    "STATUS_ACTIVE",
    "STATUS_PENDING",
    "STATUS_REMOVED",
    "AppMemberService",
    "InvitePreview",
    "JoinCodePreview",
    "MemberView",
    "Redemption",
    "clean_display_name",
]
