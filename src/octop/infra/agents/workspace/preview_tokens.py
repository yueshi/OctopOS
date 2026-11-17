"""Short-lived capability tokens for workspace HTML previews.

An agent-generated HTML report is rendered inside a sandboxed iframe. The
iframe cannot send the dashboard JWT, so the dashboard mints a narrow token
instead: read-only, scoped to one agent + workspace directory, and valid for a
few minutes. The token is stateless (no server-side session table) and uses its
own ``typ`` so it can never be accepted as an access token.
"""

from __future__ import annotations

import time
from typing import Any

import jwt

WORKSPACE_PREVIEW_TOKEN_TYPE = "workspace_preview"
DEFAULT_WORKSPACE_PREVIEW_TTL = 10 * 60


def create_workspace_preview_token(
    secret: bytes,
    *,
    agent_id: str,
    user_id: int,
    root: str,
    as_user: int | None = None,
    is_admin: bool = False,
    ttl_seconds: int = DEFAULT_WORKSPACE_PREVIEW_TTL,
) -> tuple[str, int]:
    """Sign a preview token and return ``(token, expires_at)``.

    ``root`` is a workspace-relative directory. Deliberately no ``sub`` claim:
    the access-token resolver only accepts tokens carrying ``sub``, so this
    token cannot be replayed against the normal API.
    """
    now = int(time.time())
    expires_at = now + max(60, int(ttl_seconds))
    payload: dict[str, Any] = {
        "typ": WORKSPACE_PREVIEW_TOKEN_TYPE,
        "agent_id": str(agent_id),
        "user_id": int(user_id),
        "root": str(root),
        "admin": bool(is_admin),
        "iat": now,
        "exp": expires_at,
    }
    if as_user is not None:
        payload["as_user"] = int(as_user)
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token, expires_at


def decode_workspace_preview_token(secret: bytes, token: str) -> dict[str, Any]:
    """Validate a preview token and normalize its claims."""
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    if payload.get("typ") != WORKSPACE_PREVIEW_TOKEN_TYPE:
        raise ValueError("not a workspace preview token")
    for key in ("agent_id", "user_id", "root"):
        if payload.get(key) in (None, ""):
            raise ValueError(f"workspace preview token is missing {key}")
    payload["agent_id"] = str(payload["agent_id"])
    payload["user_id"] = int(payload["user_id"])
    payload["root"] = str(payload["root"])
    as_user = payload.get("as_user")
    payload["as_user"] = int(as_user) if as_user is not None else None
    payload["admin"] = bool(payload.get("admin"))
    return payload


__all__ = [
    "DEFAULT_WORKSPACE_PREVIEW_TTL",
    "WORKSPACE_PREVIEW_TOKEN_TYPE",
    "create_workspace_preview_token",
    "decode_workspace_preview_token",
]
