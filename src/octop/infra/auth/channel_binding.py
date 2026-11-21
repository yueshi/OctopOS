"""IM channel identity binding (D4-lite).

A user generates a short-lived code in the dashboard and sends ``/bind <code>``
from a private chat; the gateway then records the platform subject as that
user. Only the MiniApp owner's own binding is trusted for personal sign-in
links, so the acting user for IM turns stays the agent owner.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from octop.infra.db.repos.channel_identities import (
    ChannelBindCodeRepo,
    ChannelIdentityRepo,
    ChannelIdentityRow,
)
from octop.infra.errors import ErrorCode, OctopError

BIND_CODE_TTL_SECONDS = 300
_BIND_CODE_CLEANUP_SECONDS = 86400
_LOCAL_USER_MESSAGE = (
    "This bind code is invalid, expired, or has already been used. "
    "Generate a new one in the dashboard and send it again."
)
_TAKEN_MESSAGE = "This IM account is already bound to another user. Unbind it there first."


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_bind_code(*, code_repo: ChannelBindCodeRepo, user_id: int) -> tuple[str, int]:
    """Create a one-time bind code for *user_id*; returns ``(code, expires_at)``."""
    now = int(time.time())
    code_repo.delete_expired(before=now - _BIND_CODE_CLEANUP_SECONDS)
    code = secrets.token_urlsafe(9)
    expires_at = now + BIND_CODE_TTL_SECONDS
    code_repo.create(code_hash=_hash(code), user_id=user_id, expires_at=expires_at)
    return code, expires_at


def bind_identity(
    *,
    identity_repo: ChannelIdentityRepo,
    code_repo: ChannelBindCodeRepo,
    code: str,
    channel_type: str,
    subject_id: str,
) -> ChannelIdentityRow:
    """Consume *code* and bind ``(channel_type, subject_id)`` to its owner."""
    clean = code.strip()
    kind = channel_type.strip().lower()
    subject = subject_id.strip()
    if not clean or not kind or not subject:
        raise OctopError.localized(ErrorCode.CHANNEL_BIND_CODE_INVALID, message=_LOCAL_USER_MESSAGE)
    row = code_repo.consume(_hash(clean), now=int(time.time()))
    if row is None:
        raise OctopError.localized(ErrorCode.CHANNEL_BIND_CODE_INVALID, message=_LOCAL_USER_MESSAGE)
    bound = identity_repo.bind(
        channel_type=kind,
        external_subject_id=subject,
        user_id=row.user_id,
    )
    if bound is None:
        raise OctopError.localized(ErrorCode.CHANNEL_IDENTITY_TAKEN, message=_TAKEN_MESSAGE)
    return bound


def is_owner_verified(
    *,
    identity_repo: ChannelIdentityRepo | None,
    channel_type: str,
    subject_id: str,
    chat_type: str,
    owner_user_id: int | None,
) -> bool:
    """True when a DM sender is bound to *owner_user_id* itself."""
    if identity_repo is None or owner_user_id is None or owner_user_id <= 0:
        return False
    if chat_type.strip().lower() != "dm":
        return False
    kind = channel_type.strip().lower()
    subject = subject_id.strip()
    if not kind or not subject:
        return False
    row = identity_repo.get(kind, subject)
    return row is not None and row.user_id == owner_user_id


__all__ = [
    "BIND_CODE_TTL_SECONDS",
    "bind_identity",
    "is_owner_verified",
    "issue_bind_code",
]
