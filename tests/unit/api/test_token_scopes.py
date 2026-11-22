"""Token scope allowlists: platform / app / guest."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.api.deps import (
    _check_app_scope,
    _check_guest_scope,
    is_jwt_exempt_path,
    resolve_user_from_token,
)
from octop.config import OctopConfig
from octop.infra.auth.tokens import sign_token
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.manager import UserManager
from octop.infra.utils.paths import PathLayout

_SECRET = b"k" * 32


def _server(tmp_path: Path):
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    services = build_shared_services(db=db, paths=PathLayout(tmp_path), config=OctopConfig())
    services.secret_repo.set("jwt", _SECRET)
    member_id = services.user_repo.create(username="alice", password_hash="h", role="user")
    guest_id = services.user_repo.create(
        username="guest_1",
        password_hash=None,
        role="user",
        kind="guest",
    )
    manager = UserManager(services)
    return SimpleNamespace(services=services, user_manager=manager), member_id, guest_id


def _token(user_id: int, username: str, **extra: object) -> str:
    return sign_token(
        _SECRET,
        sub=user_id,
        uname=username,
        role="user",
        ttl_seconds=600,
        extra=extra,
    )


def test_app_scope_guest_bootstrap_is_narrower() -> None:
    payload = {"scope": "app", "sub": 2, "app_id": "app1", "kind": "guest"}
    _check_app_scope(payload, method="GET", path="/api/auth/me")
    _check_app_scope(payload, method="GET", path="/api/apps/app1/rows")

    with pytest.raises(OctopError) as prefs:
        _check_app_scope(payload, method="GET", path="/api/preferences")
    assert prefs.value.code is ErrorCode.APP_SCOPE_FORBIDDEN

    with pytest.raises(OctopError) as notifications:
        _check_app_scope(payload, method="GET", path="/api/notifications")
    assert notifications.value.code is ErrorCode.APP_SCOPE_FORBIDDEN


def test_guest_scope_only_allows_portal_and_bootstrap() -> None:
    payload = {"scope": "guest", "sub": 2, "kind": "guest"}
    _check_guest_scope(payload, method="GET", path="/api/guest/apps")
    _check_guest_scope(payload, method="POST", path="/api/guest/session/refresh")
    _check_guest_scope(payload, method="GET", path="/api/auth/me")

    with pytest.raises(OctopError) as app:
        _check_guest_scope(payload, method="GET", path="/api/apps/app1")
    assert app.value.code is ErrorCode.GUEST_SCOPE_FORBIDDEN

    with pytest.raises(OctopError) as platform:
        _check_guest_scope(payload, method="GET", path="/api/agents")
    assert platform.value.code is ErrorCode.GUEST_SCOPE_FORBIDDEN


def test_new_join_routes_are_jwt_exempt() -> None:
    assert is_jwt_exempt_path("/api/apps/join/preview")
    assert is_jwt_exempt_path("/api/apps/join/redeem")
    assert is_jwt_exempt_path("/api/apps/invites/preview")
    assert is_jwt_exempt_path("/api/apps/invites/redeem")
    assert is_jwt_exempt_path("/api/guest/session/refresh")
    assert not is_jwt_exempt_path("/api/guest/apps")


async def test_resolve_user_from_token_returns_guest_and_member(tmp_path: Path) -> None:
    server, member_id, guest_id = _server(tmp_path)
    await server.user_manager.boot()

    member = resolve_user_from_token(server, _token(member_id, "alice"), path="/api/agents")
    assert member.id == member_id
    assert member.kind == "member"

    guest = resolve_user_from_token(
        server,
        _token(guest_id, "guest_1", scope="guest", kind="guest"),
        path="/api/guest/apps",
    )
    assert guest.id == guest_id
    assert guest.kind == "guest"

    with pytest.raises(OctopError) as forbidden:
        resolve_user_from_token(
            server,
            _token(guest_id, "guest_1", scope="guest", kind="guest"),
            path="/api/agents",
        )
    assert forbidden.value.code is ErrorCode.GUEST_SCOPE_FORBIDDEN
