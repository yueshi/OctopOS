"""Unit tests for the IM identity and bind-code repos."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.channel_identities import (
    ChannelBindCodeRepo,
    ChannelIdentityRepo,
)
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def test_bind_reuses_same_user_and_lists(db: SqlitePool) -> None:
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    repo = ChannelIdentityRepo(db)

    first = repo.bind(channel_type="weixin", external_subject_id="wx-1", user_id=alice)
    again = repo.bind(channel_type="weixin", external_subject_id="wx-1", user_id=alice)
    assert first is not None and again is not None
    assert again.user_id == alice
    assert [row.external_subject_id for row in repo.list_for_user(alice)] == ["wx-1"]


def test_bind_refuses_to_replace_another_user(db: SqlitePool) -> None:
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    bob = users.create(username="bob", password_hash="h", role="user")
    repo = ChannelIdentityRepo(db)
    assert repo.bind(channel_type="weixin", external_subject_id="wx-1", user_id=alice)
    assert repo.bind(channel_type="weixin", external_subject_id="wx-1", user_id=bob) is None
    assert repo.get("weixin", "wx-1").user_id == alice


def test_delete_only_removes_own_binding(db: SqlitePool) -> None:
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    bob = users.create(username="bob", password_hash="h", role="user")
    repo = ChannelIdentityRepo(db)
    assert repo.bind(channel_type="feishu", external_subject_id="fs-1", user_id=alice)
    assert not repo.delete(channel_type="feishu", external_subject_id="fs-1", user_id=bob)
    assert repo.delete(channel_type="feishu", external_subject_id="fs-1", user_id=alice)
    assert repo.get("feishu", "fs-1") is None


def test_bind_code_consume_is_single_use_and_expires(db: SqlitePool) -> None:
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    repo = ChannelBindCodeRepo(db)
    repo.create(code_hash="hash-1", user_id=alice, expires_at=2_000)
    consumed = repo.consume("hash-1", now=1_000)
    assert consumed is not None and consumed.user_id == alice
    assert repo.consume("hash-1", now=1_000) is None

    repo.create(code_hash="hash-2", user_id=alice, expires_at=900)
    assert repo.consume("hash-2", now=1_000) is None
