"""Guest sessions and scoped MiniApp tokens.

Guest tokens stay in the ``guest`` scope and may only reach the guest portal;
app tokens are short-lived and bound to one MiniApp. Refresh tokens are opaque
and rotate on every use. Revocation is checked on every request through
``PrincipalDirectory``.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from octop.infra.auth.tokens import sign_token
from octop.infra.db.repos.app_sessions import AppSessionRepo, AppSessionRow
from octop.infra.db.repos.users import UserRow
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_short_id

GUEST_TOKEN_TTL_SECONDS = 7 * 24 * 3600
GUEST_REFRESH_TTL_SECONDS = 30 * 24 * 3600
APP_TOKEN_TTL_SECONDS = 7200

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_REMOVED = "removed"


@dataclass(frozen=True)
class GuestSessionTokens:
    session_id: str
    guest_token: str
    refresh_token: str
    guest_expires_in: int
    refresh_expires_in: int


@dataclass(frozen=True)
class AppSessionToken:
    app_id: str
    access_token: str
    role: str
    expires_in: int
    session_id: str | None = None


@dataclass(frozen=True)
class GuestAppView:
    app_id: str
    name: str
    role: str
    last_opened_at: int = 0


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AppSessionService:
    def __init__(self, repos: RepoBundle, *, clock: Callable[[], float] = time.time) -> None:
        self._repos = repos
        self._clock = clock

    @property
    def repo(self) -> AppSessionRepo:
        return self._repos.app_session_repo

    # ----- guest sessions --------------------------------------------------

    def create_guest_session(self, user_id: int) -> GuestSessionTokens:
        user = self._require_guest(user_id)
        now = int(self._clock())
        session_id = f"sess_{new_short_id(20)}"
        refresh_token = secrets.token_urlsafe(48)
        self.repo.create(
            session_id=session_id,
            user_id=user.id,
            refresh_token_hash=_hash(refresh_token),
            expires_at=now + GUEST_REFRESH_TTL_SECONDS,
        )
        return GuestSessionTokens(
            session_id=session_id,
            guest_token=self._guest_token(user, session_id),
            refresh_token=refresh_token,
            guest_expires_in=GUEST_TOKEN_TTL_SECONDS,
            refresh_expires_in=GUEST_REFRESH_TTL_SECONDS,
        )

    def rotate(self, refresh_token: str) -> GuestSessionTokens:
        new_refresh = secrets.token_urlsafe(48)
        now = int(self._clock())
        row = self.repo.rotate(
            _hash(refresh_token),
            new_hash=_hash(new_refresh),
            expires_at=now + GUEST_REFRESH_TTL_SECONDS,
            now=now,
        )
        if row is None:
            raise OctopError.localized(ErrorCode.APP_SESSION_INVALID)
        user = self._require_guest(row.user_id)
        return GuestSessionTokens(
            session_id=row.session_id,
            guest_token=self._guest_token(user, row.session_id),
            refresh_token=new_refresh,
            guest_expires_in=GUEST_TOKEN_TTL_SECONDS,
            refresh_expires_in=GUEST_REFRESH_TTL_SECONDS,
        )

    def require_active(self, session_id: str, user_id: int) -> None:
        row = self.repo.get(session_id)
        if (
            row is None
            or row.user_id != user_id
            or row.revoked_at is not None
            or row.expires_at <= int(self._clock())
        ):
            raise OctopError.localized(ErrorCode.APP_SESSION_INVALID)

    def revoke(self, session_id: str, *, user_id: int) -> bool:
        return self.repo.revoke(session_id, user_id=user_id, now=int(self._clock()))

    def revoke_all(self, user_id: int) -> int:
        return self.repo.revoke_all(user_id, now=int(self._clock()))

    def active_session(self, session_id: str, user_id: int) -> AppSessionRow | None:
        try:
            self.require_active(session_id, user_id)
        except OctopError:
            return None
        return self.repo.get(session_id)

    # ----- scoped app tokens ----------------------------------------------

    def mint_app_token(
        self,
        user: UserRow,
        app_id: str,
        *,
        role: str,
        session_id: str | None = None,
    ) -> AppSessionToken:
        if user.disabled:
            raise OctopError.localized(ErrorCode.USER_DISABLED)
        secret = self._repos.secret_repo.get("jwt")
        if secret is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
        extra: dict[str, object] = {
            "scope": "app",
            "app_id": app_id,
            "perm": role,
            "kind": user.kind,
        }
        if user.kind == "guest":
            if session_id is None:
                raise OctopError.localized(ErrorCode.APP_SESSION_INVALID)
            self.require_active(session_id, user.id)
            extra["sid"] = session_id
            self.repo.touch(session_id, now=int(self._clock()))
        token = sign_token(
            secret,
            sub=user.id,
            uname=user.username,
            role=user.role,
            ttl_seconds=APP_TOKEN_TTL_SECONDS,
            extra=extra,
        )
        return AppSessionToken(
            app_id=app_id,
            access_token=token,
            role=role,
            expires_in=APP_TOKEN_TTL_SECONDS,
            session_id=session_id,
        )

    def app_role(self, user_id: int, app_id: str) -> str:
        """Return the active collaborator role or raise a precise error."""
        collab = self._repos.light_app_repo.get_collaborator(app_id, user_id)
        if collab is None or collab.status == STATUS_REMOVED:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        if collab.status == STATUS_PENDING:
            raise OctopError.localized(ErrorCode.APP_MEMBER_PENDING)
        return collab.permission

    # ----- guest portal ----------------------------------------------------

    def list_guest_apps(self, user_id: int) -> list[GuestAppView]:
        self._require_guest(user_id)
        views: list[GuestAppView] = []
        for app in self._repos.light_app_repo.list_shared(user_id):
            collab = self._repos.light_app_repo.get_collaborator(app.app_id, user_id)
            if collab is None or collab.status != STATUS_ACTIVE:
                continue
            views.append(
                GuestAppView(
                    app_id=app.app_id,
                    name=app.name,
                    role=collab.permission,
                )
            )
        return views

    def leave_app(self, user_id: int, app_id: str) -> bool:
        self._require_guest(user_id)
        collab = self._repos.light_app_repo.get_collaborator(app_id, user_id)
        if collab is None or collab.status == STATUS_REMOVED:
            return False
        self._repos.light_app_repo.update_collaborator(app_id, user_id, status=STATUS_REMOVED)
        return True

    def delete_account(self, user_id: int) -> None:
        self._require_guest(user_id)
        for app in self._repos.light_app_repo.list_shared(user_id):
            self._repos.light_app_repo.update_collaborator(
                app.app_id,
                user_id,
                status=STATUS_REMOVED,
            )
        self._repos.user_repo.set_display_name(user_id, "")
        self._repos.user_repo.set_disabled(user_id, True)
        self.repo.revoke_all(user_id, now=int(self._clock()))

    # ----- internals -------------------------------------------------------

    def _require_guest(self, user_id: int) -> UserRow:
        row = self._repos.user_repo.get(user_id)
        if row is None or row.kind != "guest":
            raise OctopError.localized(ErrorCode.APP_GUEST_DISABLED)
        if row.disabled:
            raise OctopError.localized(ErrorCode.APP_GUEST_DISABLED)
        return row

    def _guest_token(self, user: UserRow, session_id: str) -> str:
        secret = self._repos.secret_repo.get("jwt")
        if secret is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
        return sign_token(
            secret,
            sub=user.id,
            uname=user.username,
            role=user.role,
            ttl_seconds=GUEST_TOKEN_TTL_SECONDS,
            extra={"scope": "guest", "kind": "guest", "sid": session_id},
        )


__all__ = [
    "APP_TOKEN_TTL_SECONDS",
    "GUEST_REFRESH_TTL_SECONDS",
    "GUEST_TOKEN_TTL_SECONDS",
    "AppSessionService",
    "AppSessionToken",
    "GuestAppView",
    "GuestSessionTokens",
]
