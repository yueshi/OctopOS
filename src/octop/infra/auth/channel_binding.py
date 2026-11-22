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
from octop.infra.db.repos.channel_link_codes import ChannelLinkCodeRepo
from octop.infra.errors import ErrorCode, OctopError

BIND_CODE_TTL_SECONDS = 300
LINK_CODE_TTL_SECONDS = 300
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
    sender_id: str,
    account_key: str = "",
) -> ChannelIdentityRow:
    """Consume *code* and bind ``(channel_type, account_key, sender_id)``."""
    clean = code.strip()
    kind = channel_type.strip().lower()
    sender = sender_id.strip()
    if not clean or not kind or not sender:
        raise OctopError.localized(ErrorCode.CHANNEL_BIND_CODE_INVALID, message=_LOCAL_USER_MESSAGE)
    row = code_repo.consume(_hash(clean), now=int(time.time()))
    if row is None:
        raise OctopError.localized(ErrorCode.CHANNEL_BIND_CODE_INVALID, message=_LOCAL_USER_MESSAGE)
    bound = identity_repo.bind(
        channel_type=kind,
        sender_id=sender,
        account_key=account_key,
        user_id=row.user_id,
    )
    if bound is None:
        raise OctopError.localized(ErrorCode.CHANNEL_IDENTITY_TAKEN, message=_TAKEN_MESSAGE)
    return bound


def resolve_identity(
    *,
    identity_repo: ChannelIdentityRepo | None,
    channel_type: str,
    sender_id: str,
    account_key: str = "",
) -> ChannelIdentityRow | None:
    """Return the verified binding for this sender, if any.

    The exact bot/account key wins; a legacy ``account_key=''`` row is the
    fallback so bindings created before PR-11 keep working.
    """
    if identity_repo is None:
        return None
    kind = channel_type.strip().lower()
    sender = sender_id.strip()
    if not kind or not sender:
        return None
    return identity_repo.get(kind, sender, account_key=account_key)


def issue_link_code(
    *,
    link_code_repo: ChannelLinkCodeRepo,
    user_id: int,
    app_id: str,
) -> tuple[str, int]:
    """Mint a short-lived code the user sends back as ``/link <code>``."""
    now = int(time.time())
    link_code_repo.delete_expired(before=now - _BIND_CODE_CLEANUP_SECONDS)
    code = secrets.token_hex(4).upper()
    expires_at = now + LINK_CODE_TTL_SECONDS
    link_code_repo.create(
        code_hash=_hash(code),
        user_id=user_id,
        app_id=app_id,
        expires_at=expires_at,
    )
    return code, expires_at


def redeem_link_code(
    *,
    identity_repo: ChannelIdentityRepo,
    link_code_repo: ChannelLinkCodeRepo,
    code: str,
    channel_type: str,
    sender_id: str,
    account_key: str = "",
) -> ChannelIdentityRow | None:
    """Consume a link code and bind this IM sender to its Web user.

    Returns ``None`` when the code is invalid/expired. A sender already bound
    to another user raises ``CHANNEL_IDENTITY_TAKEN``.
    """
    clean = code.strip().upper()
    kind = channel_type.strip().lower()
    sender = sender_id.strip()
    if not clean or not kind or not sender:
        return None
    row = link_code_repo.consume(_hash(clean), now=int(time.time()))
    if row is None:
        return None
    bound = identity_repo.bind(
        channel_type=kind,
        sender_id=sender,
        account_key=account_key,
        user_id=row.user_id,
    )
    if bound is None:
        raise OctopError.localized(ErrorCode.CHANNEL_IDENTITY_TAKEN, message=_TAKEN_MESSAGE)
    return bound


def is_owner_verified(
    *,
    identity_repo: ChannelIdentityRepo | None,
    channel_type: str,
    sender_id: str,
    chat_type: str,
    owner_user_id: int | None,
    account_key: str = "",
) -> bool:
    """True when a DM sender is bound to *owner_user_id* itself."""
    if owner_user_id is None or owner_user_id <= 0:
        return False
    if chat_type.strip().lower() != "dm":
        return False
    row = resolve_identity(
        identity_repo=identity_repo,
        channel_type=channel_type,
        sender_id=sender_id,
        account_key=account_key,
    )
    return row is not None and row.user_id == owner_user_id


__all__ = [
    "BIND_CODE_TTL_SECONDS",
    "LINK_CODE_TTL_SECONDS",
    "bind_identity",
    "is_owner_verified",
    "issue_bind_code",
    "issue_link_code",
    "redeem_link_code",
    "resolve_identity",
]
