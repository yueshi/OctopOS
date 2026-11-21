"""FastAPI Depends, JWT helpers, and auth routing."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, Header, Query, Request

from octop.infra.auth.tokens import InvalidToken as InvalidToken
from octop.infra.auth.tokens import TokenExpired as TokenExpired
from octop.infra.auth.tokens import decode_token as decode_token
from octop.infra.auth.tokens import sign_token as sign_token
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.permissions import PERMISSIONS, user_has_permission

if TYPE_CHECKING:
    from octop.infra.server import OctopServer
    from octop.infra.users.identity import User
else:
    from octop.infra.users.identity import User


# Sliding renew: when remaining life is below this fraction of configured TTL,
# middleware issues a fresh access token via ACCESS_TOKEN_RESPONSE_HEADER.
ACCESS_TOKEN_RESPONSE_HEADER = "X-Octop-Access-Token"
_SLIDING_RENEW_REMAINING_FRACTION = 1 / 3


# Paths that bypass JWT middleware (setup wizard, health, login).
_JWT_EXEMPT_PREFIXES = (
    "/api/setup/",
    "/api/health/",
    "/api/i18n/",
    "/api/connectors/oauth/callback",
    "/api/internal/mcp/",
    # Short-lived download links: the unguessable token is the credential.
    "/api/downloads/link/",
    # Public read-only light-app links: the unguessable token is the credential.
    "/api/public/apps/",
    # Workspace HTML previews: the scoped, short-lived preview token is the credential.
    "/api/workspace-preview/",
    # External game result writes: the scoped, short-lived session token is the credential.
    "/api/game-sessions/",
    # Public webhook ingress: HMAC signature and source id are the credentials.
    "/api/hooks/",
)
_JWT_EXEMPT_EXACT = (
    "/api/health",
    "/api/auth/login",
    "/api/auth/captcha",
    "/api/auth/oidc/status",
    "/api/auth/oidc/start",
    "/api/auth/oidc/callback",
    "/api/auth/oidc/exchange",
    "/api/auth/app-access/preview",
    "/api/auth/app-access/exchange",
    "/api/auth/oauth/status",
    "/api/auth/oauth/start",
    "/api/auth/oauth/callback",
    "/api/auth/oauth/exchange",
    "/api/auth/ldap/status",
    "/api/auth/invite/validate",
    "/api/auth/invite/redeem",
    "/api/docs",
    "/api/openapi.json",
)


def is_jwt_exempt_path(path: str) -> bool:
    return path in _JWT_EXEMPT_EXACT or any(path.startswith(p) for p in _JWT_EXEMPT_PREFIXES)


def is_jwt_exempt_request(request: Request) -> bool:
    return is_jwt_exempt_path(request.url.path)


def get_server(request: Request) -> OctopServer:
    server: OctopServer = request.app.state.octop_server
    if not getattr(server, "_started", False):
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server not started")
    return server


def require_database(server: OctopServer) -> None:
    """Raise when the control-plane DB has not been bound yet (deferred setup)."""
    if not server.database_bound:
        raise OctopError(
            ErrorCode.SETUP_REQUIRED,
            "control-plane database not configured yet",
            status=503,
        )


def extract_raw_token(
    *,
    authorization: str | None = None,
    access_token: str | None = None,
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if access_token:
        return access_token
    return None


def _decode(server: OctopServer, token: str) -> dict[str, Any]:
    assert server.services is not None
    secret = server.services.secret_repo.get("jwt")
    if secret is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
    try:
        return decode_token(secret, token)
    except TokenExpired as exc:
        raise OctopError(ErrorCode.TOKEN_EXPIRED, "token expired") from exc
    except InvalidToken as exc:
        raise OctopError(ErrorCode.AUTH_FAILED, "invalid token") from exc


_APP_SCOPE_READ_PATHS = frozenset(
    {
        "/api/auth/me",
        "/api/settings/timezone",
        "/api/preferences",
        "/api/notifications",
    }
)
_APP_SCOPE_READ_PREFIXES = ("/api/i18n/",)


def _app_scope_forbidden() -> OctopError:
    return OctopError.localized(ErrorCode.APP_SCOPE_FORBIDDEN)


def _app_scope_allows(*, method: str, path: str, app_id: str, user_id: int) -> bool:
    """Paths a MiniApp-scoped session may use.

    The app's own subtree is fully allowed (its routes still apply their own
    ACL). Everything else is limited to read-only bootstrap calls the SPA
    makes before rendering; the rest of the dashboard stays out of reach.
    """
    prefix = f"/api/apps/{app_id}"
    if path == prefix or path.startswith(f"{prefix}/"):
        return True
    if method.upper() != "GET":
        return False
    if path in _APP_SCOPE_READ_PATHS:
        return True
    if any(path.startswith(item) for item in _APP_SCOPE_READ_PREFIXES):
        return True
    avatar = f"/api/users/{user_id}/avatar"
    return path == avatar or path.startswith(f"{avatar}/")


def _check_app_scope(payload: dict[str, Any], *, method: str, path: str | None) -> None:
    if payload.get("scope") != "app":
        return
    app_id = str(payload.get("app_id") or "")
    subject = payload.get("sub")
    if not app_id or subject is None or path is None:
        raise _app_scope_forbidden()
    if not _app_scope_allows(
        method=method,
        path=path,
        app_id=app_id,
        user_id=int(subject),
    ):
        raise _app_scope_forbidden()


def resolve_user_from_token(
    server: OctopServer,
    token: str,
    *,
    method: str = "GET",
    path: str | None = None,
) -> User:
    payload = _decode(server, token)
    _check_app_scope(payload, method=method, path=path)
    assert server.user_manager is not None
    # The same secret signs other, narrower tokens (a MiniApp game session).
    # Only a token carrying ``sub`` is an access token; the rest must fail as
    # an auth error, not as a missing-claim crash.
    subject = payload.get("sub")
    if subject is None:
        raise OctopError(ErrorCode.AUTH_FAILED, "not an access token")
    user = server.user_manager.get_by_id(int(subject))
    if user is None:
        raise OctopError(ErrorCode.USER_DISABLED, "user not active")
    return user


def maybe_sliding_renew_token(server: OctopServer, token: str, user: User) -> str | None:
    """Return a fresh access token when ``token`` is past the sliding renew threshold.

    Threshold is one-third of ``access_token_ttl_seconds`` remaining. Active clients
    that pick up ``ACCESS_TOKEN_RESPONSE_HEADER`` stay signed in without re-login.
    """
    assert server.services is not None
    payload = _decode(server, token)
    if payload.get("scope") == "app":
        # A scoped MiniApp session must not be upgraded into a full dashboard
        # token by sliding renewal.
        return None
    ttl = int(server.services.config.access_token_ttl_seconds)
    remaining = int(payload["exp"]) - int(time.time())
    threshold = max(1, int(ttl * _SLIDING_RENEW_REMAINING_FRACTION))
    if remaining >= threshold:
        return None
    secret = server.services.secret_repo.get("jwt")
    if secret is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
    return sign_token(
        secret,
        sub=user.id,
        uname=user.username,
        role=user.role,
        ttl_seconds=ttl,
    )


def authenticate_request(request: Request, server: OctopServer) -> User:
    raw = extract_raw_token(
        authorization=request.headers.get("authorization"),
        access_token=request.query_params.get("access_token"),
    )
    if not raw:
        raise OctopError(ErrorCode.AUTH_FAILED, "missing credentials")
    return resolve_user_from_token(
        server,
        raw,
        method=request.method,
        path=request.url.path,
    )


async def current_user(
    request: Request,
    server: OctopServer = Depends(get_server),
    authorization: str | None = Header(None),
    access_token: str | None = Query(None),
) -> User:
    cached = getattr(request.state, "octop_user", None)
    if cached is not None:
        return cast("User", cached)
    raw = extract_raw_token(authorization=authorization, access_token=access_token)
    if not raw:
        raise OctopError(ErrorCode.AUTH_FAILED, "missing credentials")
    return resolve_user_from_token(
        server,
        raw,
        method=request.method,
        path=request.url.path,
    )


def require_permission(key: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory: require the module permission ``key``.

    ``admin`` is bypassed inside ``user_has_permission``. Unknown keys fail
    fast at construction so typos cannot silently grant access.
    """
    if key not in PERMISSIONS:
        raise RuntimeError(f"unknown permission key: {key}")

    async def _dep(user: User = Depends(current_user)) -> User:
        if not user_has_permission(user, key):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "permission required",
                details={"permission": key},
            )
        return user

    return _dep


def require_admin() -> Callable[..., Awaitable[User]]:
    """Dependency factory: require the admin role (no module key)."""

    async def _dep(user: User = Depends(current_user)) -> User:
        if not user.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "admin required")
        return user

    return _dep
