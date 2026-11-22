"""IM channel identity binding: codes, binding, owner verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.auth.channel_binding import (
    bind_identity,
    is_owner_verified,
    issue_bind_code,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.channel_identities import (
    ChannelBindCodeRepo,
    ChannelIdentityRepo,
)
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError


@pytest.fixture
def world(tmp_path: Path):
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    bob = users.create(username="bob", password_hash="h", role="user")
    identities = ChannelIdentityRepo(db)
    codes = ChannelBindCodeRepo(db)
    return identities, codes, alice, bob


def test_issue_and_bind_identity(world) -> None:
    identities, codes, alice, _bob = world
    code, expires_at = issue_bind_code(code_repo=codes, user_id=alice)
    assert expires_at > 0
    row = bind_identity(
        identity_repo=identities,
        code_repo=codes,
        code=code,
        channel_type="weixin",
        sender_id="wx-alice",
    )
    assert row.user_id == alice
    assert identities.get("weixin", "wx-alice").user_id == alice


def test_bind_isolated_by_account_key(world) -> None:
    identities, codes, alice, bob = world
    exact, _ = issue_bind_code(code_repo=codes, user_id=bob)
    bind_identity(
        identity_repo=identities,
        code_repo=codes,
        code=exact,
        channel_type="feishu",
        sender_id="ou-1",
        account_key="app-b",
    )
    legacy, _ = issue_bind_code(code_repo=codes, user_id=alice)
    bind_identity(
        identity_repo=identities,
        code_repo=codes,
        code=legacy,
        channel_type="feishu",
        sender_id="ou-1",
    )
    assert identities.get("feishu", "ou-1", account_key="app-b").user_id == bob
    assert identities.get("feishu", "ou-1").user_id == alice
    # Another bot key falls back to the legacy owner.
    assert identities.get("feishu", "ou-1", account_key="app-c").user_id == alice
    assert is_owner_verified(
        identity_repo=identities,
        channel_type="feishu",
        sender_id="ou-1",
        chat_type="dm",
        owner_user_id=bob,
        account_key="app-b",
    )


def test_bind_code_is_single_use(world) -> None:
    identities, codes, alice, _bob = world
    code, _ = issue_bind_code(code_repo=codes, user_id=alice)
    bind_identity(
        identity_repo=identities,
        code_repo=codes,
        code=code,
        channel_type="weixin",
        sender_id="wx-alice",
    )
    with pytest.raises(OctopError) as exc:
        bind_identity(
            identity_repo=identities,
            code_repo=codes,
            code=code,
            channel_type="weixin",
            sender_id="wx-other",
        )
    assert exc.value.code is ErrorCode.CHANNEL_BIND_CODE_INVALID


def test_existing_binding_cannot_be_stolen(world) -> None:
    identities, codes, alice, bob = world
    first, _ = issue_bind_code(code_repo=codes, user_id=alice)
    bind_identity(
        identity_repo=identities,
        code_repo=codes,
        code=first,
        channel_type="weixin",
        sender_id="wx-shared",
    )
    second, _ = issue_bind_code(code_repo=codes, user_id=bob)
    with pytest.raises(OctopError) as exc:
        bind_identity(
            identity_repo=identities,
            code_repo=codes,
            code=second,
            channel_type="weixin",
            sender_id="wx-shared",
        )
    assert exc.value.code is ErrorCode.CHANNEL_IDENTITY_TAKEN
    assert identities.get("weixin", "wx-shared").user_id == alice


def test_is_owner_verified_requires_dm_and_owner_binding(world) -> None:
    identities, codes, alice, bob = world
    code, _ = issue_bind_code(code_repo=codes, user_id=alice)
    bind_identity(
        identity_repo=identities,
        code_repo=codes,
        code=code,
        channel_type="weixin",
        sender_id="wx-alice",
    )

    assert is_owner_verified(
        identity_repo=identities,
        channel_type="weixin",
        sender_id="wx-alice",
        chat_type="dm",
        owner_user_id=alice,
    )
    # Group chats never qualify, even for a bound owner.
    assert not is_owner_verified(
        identity_repo=identities,
        channel_type="weixin",
        sender_id="wx-alice",
        chat_type="group",
        owner_user_id=alice,
    )
    # Someone else's binding is not the owner.
    assert not is_owner_verified(
        identity_repo=identities,
        channel_type="weixin",
        sender_id="wx-alice",
        chat_type="dm",
        owner_user_id=bob,
    )
    # Unbound subject.
    assert not is_owner_verified(
        identity_repo=identities,
        channel_type="weixin",
        sender_id="wx-other",
        chat_type="dm",
        owner_user_id=alice,
    )
    # No repo / no owner.
    assert not is_owner_verified(
        identity_repo=None,
        channel_type="weixin",
        sender_id="wx-alice",
        chat_type="dm",
        owner_user_id=alice,
    )
    assert not is_owner_verified(
        identity_repo=identities,
        channel_type="weixin",
        sender_id="wx-alice",
        chat_type="dm",
        owner_user_id=None,
    )
