"""users.kind gates: guests never enter the platform user paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.users.manager import UserManager
from octop.infra.users.permissions import effective_permissions, user_has_permission
from octop.infra.utils.paths import PathLayout


def _services(tmp_path: Path):
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    return build_shared_services(db=db, paths=PathLayout(tmp_path), config=OctopConfig())


@pytest.mark.asyncio
async def test_boot_and_listing_exclude_guests(tmp_path: Path) -> None:
    services = _services(tmp_path)
    member_id = services.user_repo.create(username="alice", password_hash="h", role="user")
    guest_id = services.user_repo.create(
        username="guest_1",
        password_hash="h",
        role="user",
        kind="guest",
    )
    manager = UserManager(services)
    await manager.boot()

    assert manager.get_by_id(member_id) is not None
    assert manager.get_by_id(guest_id) is None
    assert [user.username for user in manager.list()] == ["alice"]
    assert manager.count() == 1
    assert manager.count_by_kind("guest") == 1
    assert [row.username for row in manager.list_all(include_disabled=True)] == ["alice"]
    assert await manager.authenticate("guest_1", "h") is None


@pytest.mark.asyncio
async def test_guest_cannot_be_a_user_management_target(tmp_path: Path) -> None:
    services = _services(tmp_path)
    services.user_repo.create(
        username="guest_1",
        password_hash="h",
        role="user",
        kind="guest",
    )
    manager = UserManager(services)
    await manager.boot()

    with pytest.raises(OctopError) as reset:
        await manager.reset_password("guest_1", "AnotherPass12")
    assert reset.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(OctopError) as change:
        await manager.change_password("guest_1", "h", "AnotherPass12")
    assert change.value.code is ErrorCode.AUTH_FAILED


def test_guest_never_holds_platform_permissions() -> None:
    guest = User(
        id=7,
        username="guest_1",
        role="admin",
        display_name=None,
        permissions=["users"],
        kind="guest",
    )
    assert guest.is_admin is False
    assert user_has_permission(guest, "users") is False
    assert effective_permissions(guest) == []


def test_register_cached_user_ignores_guests(tmp_path: Path) -> None:
    manager = UserManager(_services(tmp_path))
    manager.register_cached_user(
        User(id=8, username="guest_2", role="user", display_name=None, kind="guest")
    )
    assert manager.get_by_id(8) is None
