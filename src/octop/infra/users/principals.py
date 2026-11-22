"""Resolve verified credentials and IM bindings into a :class:`Principal`.

The HTTP layer keeps returning ``User`` for compatibility, but authorization
must not depend on the ``users`` table shape. This module is the single place
that turns a token, a channel identity, or a user id into the four principal
kinds the MiniApp access model understands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from octop.infra.auth.tokens import InvalidToken, TokenExpired, decode_token
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.utils.locale import normalize_locale


class PrincipalKind(StrEnum):
    MEMBER = "member"
    GUEST = "guest"
    SUBMISSION = "submission"
    ANONYMOUS = "anonymous"


class GuestSessionLookup(Protocol):
    """Narrow seam for guest-session revocation (implemented in P1)."""

    def require_active(self, session_id: str, user_id: int) -> None: ...


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    user: User | None = None
    app_id: str | None = None
    session_id: str | None = None
    submitter_ref: str | None = None

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user is not None else None

    @property
    def display_name(self) -> str:
        return self.user.label if self.user is not None else ""


def principal_of(user: User) -> Principal:
    """Adapt a platform ``User`` to a principal (guest callers use the directory)."""
    kind = PrincipalKind.GUEST if user.kind == "guest" else PrincipalKind.MEMBER
    return Principal(kind=kind, user=user)


def principal_from_user_row(row: Any) -> Principal:
    """Build a principal from a raw ``UserRow`` (tools only hold a user id)."""
    user = User(
        id=int(row.id),
        username=str(row.username),
        role=str(row.role),
        display_name=row.display_name,
        locale=normalize_locale(row.locale),
        permissions=list(row.permissions),
        kind=str(row.kind or "member"),
    )
    return principal_of(user)


class PrincipalDirectory:
    """Resolve tokens and channel identities; never manages app memberships."""

    def __init__(
        self,
        repos: RepoBundle,
        *,
        user_manager: Any = None,
        guest_sessions: GuestSessionLookup | None = None,
    ) -> None:
        self._repos = repos
        self._user_manager = user_manager
        self._guest_sessions = guest_sessions

    # ----- token / claims -------------------------------------------------

    def resolve_token(
        self,
        token: str,
        *,
        method: str = "GET",
        path: str | None = None,
    ) -> Principal:
        """Decode *token* and resolve its principal.

        Path allowlists stay in ``api.deps`` (the transport adapter); this
        method only proves identity.
        """
        del method, path
        secret = self._repos.secret_repo.get("jwt")
        if secret is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
        try:
            payload = decode_token(secret, token)
        except TokenExpired as exc:
            raise OctopError(ErrorCode.TOKEN_EXPIRED, "token expired") from exc
        except InvalidToken as exc:
            raise OctopError(ErrorCode.AUTH_FAILED, "invalid token") from exc
        return self.resolve_claims(payload)

    def resolve_claims(self, payload: dict[str, Any]) -> Principal:
        subject = payload.get("sub")
        if subject is None:
            raise OctopError(ErrorCode.AUTH_FAILED, "not an access token")
        user_id = int(subject)
        scope = str(payload.get("scope") or "platform")
        kind = str(payload.get("kind") or "")
        if scope == "guest":
            if kind and kind != "guest":
                raise OctopError(ErrorCode.AUTH_FAILED, "invalid guest token")
            return self._guest(
                user_id,
                app_id=None,
                session_id=str(payload.get("sid") or "") or None,
                check_membership=False,
            )
        if scope == "app":
            app_id = str(payload.get("app_id") or "")
            if not app_id:
                raise OctopError(ErrorCode.AUTH_FAILED, "app-scoped token missing app_id")
            if kind == "guest":
                return self._guest(
                    user_id,
                    app_id=app_id,
                    session_id=str(payload.get("sid") or "") or None,
                    check_membership=True,
                )
            return self._member(user_id)
        if kind == "guest":
            raise OctopError(ErrorCode.AUTH_FAILED, "guest token cannot be a platform token")
        return self._member(user_id)

    # ----- principals -----------------------------------------------------

    def _member(self, user_id: int) -> Principal:
        if self._user_manager is not None:
            cached = self._user_manager.get_by_id(user_id)
            if cached is not None:
                return Principal(kind=PrincipalKind.MEMBER, user=cached)
        row = self._repos.user_repo.get(user_id)
        if row is None or row.disabled or row.kind != "member":
            raise OctopError(ErrorCode.USER_DISABLED, "user not active")
        return Principal(kind=PrincipalKind.MEMBER, user=self._user_from_row(row))

    def _guest(
        self,
        user_id: int,
        *,
        app_id: str | None,
        session_id: str | None,
        check_membership: bool,
    ) -> Principal:
        row = self._repos.user_repo.get(user_id)
        if row is None or row.kind != "guest" or row.disabled:
            raise OctopError.localized(ErrorCode.APP_GUEST_DISABLED)
        if check_membership and app_id is not None:
            collab = self._repos.light_app_repo.get_collaborator(app_id, user_id)
            if collab is None or collab.status == "removed":
                raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
            if collab.status == "pending":
                raise OctopError.localized(ErrorCode.APP_MEMBER_PENDING)
        if self._guest_sessions is not None and session_id is not None:
            self._guest_sessions.require_active(session_id, user_id)
        return Principal(
            kind=PrincipalKind.GUEST,
            user=self._user_from_row(row),
            app_id=app_id,
            session_id=session_id,
        )

    def resolve_channel(
        self,
        channel_type: str,
        account_key: str,
        sender_id: str,
    ) -> Principal | None:
        """Resolve a bound IM sender (exact account key, then legacy key)."""
        row = self._repos.channel_identity_repo.get(
            channel_type, sender_id, account_key=account_key
        )
        if row is None:
            return None
        return self.get_user(row.user_id)

    def get_user(self, user_id: int) -> Principal | None:
        row = self._repos.user_repo.get(user_id)
        if row is None or row.disabled:
            return None
        if row.kind == "guest":
            return Principal(kind=PrincipalKind.GUEST, user=self._user_from_row(row))
        if row.kind == "member":
            return Principal(kind=PrincipalKind.MEMBER, user=self._user_from_row(row))
        return None

    @staticmethod
    def require_member(principal: Principal) -> int:
        if principal.kind is not PrincipalKind.MEMBER or principal.user_id is None:
            raise OctopError.localized(ErrorCode.FORBIDDEN)
        return principal.user_id

    @staticmethod
    def _user_from_row(row: Any) -> User:
        return User(
            id=int(row.id),
            username=str(row.username),
            role=str(row.role),
            display_name=row.display_name,
            locale=normalize_locale(row.locale),
            permissions=list(row.permissions),
            kind=str(row.kind or "member"),
        )


__all__ = [
    "GuestSessionLookup",
    "Principal",
    "PrincipalDirectory",
    "PrincipalKind",
    "principal_from_user_row",
    "principal_of",
]
