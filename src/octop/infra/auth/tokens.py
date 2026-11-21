"""Access-token JWT helpers shared by HTTP auth and infra (Bridge)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import jwt

_IDENTITY_CLAIMS = frozenset({"sub", "uname", "role", "iat", "exp"})


class InvalidToken(Exception): ...


class TokenExpired(InvalidToken): ...


def sign_token(
    secret: bytes,
    *,
    sub: int,
    uname: str,
    role: str,
    ttl_seconds: int = 86400,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Sign an access token.

    ``extra`` adds narrower-scope claims (``scope``/``app_id``). Identity
    claims cannot be overridden, so a scoped token can never change who it
    belongs to or outlive its requested TTL.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        key: value for key, value in (extra or {}).items() if key not in _IDENTITY_CLAIMS
    }
    payload.update(
        {
            "sub": str(sub),
            "uname": uname,
            "role": role,
            "iat": now,
            "exp": now + ttl_seconds,
        }
    )
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: bytes, token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if "sub" in payload:
            payload["sub"] = int(payload["sub"])
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken(str(exc)) from exc
