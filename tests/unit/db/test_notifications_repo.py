"""Durable notification inbox: dedupe, read state, isolation, prune."""

from __future__ import annotations

from pathlib import Path

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.notifications import NotificationRepo
from octop.infra.db.repos.users import UserRepo


def _db(tmp_path: Path) -> tuple[SqlitePool, int, int]:
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    bob = users.create(username="bob", password_hash="h", role="user")
    return db, alice, bob


def test_create_dedupe_list_and_read(tmp_path: Path) -> None:
    db, alice, _bob = _db(tmp_path)
    repo = NotificationRepo(db)

    first = repo.create(
        user_id=alice,
        kind="approval",
        title="Access request",
        body="alice → Tracker",
        path="/apps/app1",
        dedupe_key="access:req1",
    )
    duplicate = repo.create(
        user_id=alice,
        kind="approval",
        title="Access request",
        body="alice → Tracker",
        path="/apps/app1",
        dedupe_key="access:req1",
    )

    assert duplicate.notification_id == first.notification_id
    assert repo.unread_count(alice) == 1
    rows = repo.list_for_user(alice)
    assert [row.notification_id for row in rows] == [first.notification_id]
    assert rows[0].read is False
    assert rows[0].path == "/apps/app1"

    marked = repo.mark_read(alice, first.notification_id)
    assert marked is not None and marked.read is True
    assert repo.unread_count(alice) == 0
    assert repo.mark_all_read(alice) == 0
    assert repo.list_for_user(alice, unread_only=True) == []


def test_user_isolation_and_same_dedupe_key(tmp_path: Path) -> None:
    db, alice, bob = _db(tmp_path)
    repo = NotificationRepo(db)

    alice_row = repo.create(
        user_id=alice,
        kind="info",
        title="Alice",
        dedupe_key="shared-key",
    )
    bob_row = repo.create(
        user_id=bob,
        kind="info",
        title="Bob",
        dedupe_key="shared-key",
    )

    assert alice_row.notification_id != bob_row.notification_id
    assert [row.title for row in repo.list_for_user(alice)] == ["Alice"]
    assert [row.title for row in repo.list_for_user(bob)] == ["Bob"]


def test_prune_keeps_newest_and_clear_removes_all(tmp_path: Path) -> None:
    db, alice, _bob = _db(tmp_path)
    repo = NotificationRepo(db)

    for index in range(5):
        repo.create(
            user_id=alice,
            kind="info",
            title=f"n{index}",
            created_at=100 + index,
        )
    repo.prune(alice, keep=2)

    assert [row.title for row in repo.list_for_user(alice)] == ["n4", "n3"]
    assert repo.clear(alice) == 2
    assert repo.list_for_user(alice) == []
