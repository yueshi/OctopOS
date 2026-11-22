"""v39: dashboard notification inbox table and skipped-version repair."""

from __future__ import annotations

from pathlib import Path

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo


def _version(db: SqlitePool) -> int:
    with db.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])


def _columns(db: SqlitePool) -> set[str]:
    with db.connect() as conn:
        return {row["name"] for row in conn.execute("PRAGMA table_info(notifications)")}


def test_v39_creates_notifications_table(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    migrate._discover = lambda dialect="sqlite": [
        (version, path) for version, path in real_discover(dialect) if version <= 38
    ]
    try:
        migrate.run_migrations(db)
        assert _version(db) == 38
        UserRepo(db).create(username="alice", password_hash="h", role="user")
    finally:
        migrate._discover = real_discover

    migrate.run_migrations(db)

    assert _version(db) == 51
    assert {
        "notification_id",
        "user_id",
        "kind",
        "title",
        "body",
        "path",
        "dedupe_key",
        "read_at",
        "created_at",
    } <= _columns(db)


def test_v39_repairs_a_recorded_version_without_the_table(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    with db.transaction() as conn:
        conn.execute("DROP TABLE notifications")
        conn.execute("UPDATE _schema_version SET version = 39")

    migrate.run_migrations(db)

    assert {"notification_id", "user_id", "dedupe_key"} <= _columns(db)
