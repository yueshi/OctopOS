"""v46 migration: MiniApp identity primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.users import UserRepo


def _version(db: SqlitePool) -> int:
    with db.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])


def _columns(db: SqlitePool, table: str) -> set[str]:
    with db.connect() as conn:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _seed_app_with_rows(db: SqlitePool) -> int:
    user_id = UserRepo(db).create(username="alice", password_hash="h", role="user")
    AgentRepo(db).create(agent_id="ag1", user_id=user_id, name="bot")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO light_apps("
            "app_id, agent_id, owner_user_id, name, created_at, updated_at"
            ") VALUES ('app1', 'ag1', ?, 'Tracker', 1, 1)",
            (user_id,),
        )
    repo = LightAppRepo(db)
    repo.create_table("app1", "main")
    repo.append_data_rows("app1", "main", [{"name": "owned"}], created_by=user_id)
    repo.append_data_rows("app1", "main", [{"name": "orphan"}], created_by=None)
    return user_id


def test_v46_adds_columns_and_backfills_row_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [
            (version, path) for version, path in real_discover(dialect) if version <= 45
        ],
    )
    migrate.run_migrations(db)
    assert _version(db) == 45
    owner_id = _seed_app_with_rows(db)

    monkeypatch.setattr(migrate, "_discover", real_discover)
    migrate.run_migrations(db)

    assert _version(db) == 52
    assert "kind" in _columns(db, "users")
    assert "member_visible" in _columns(db, "light_app_tables")
    assert "row_owner_user_id" in _columns(db, "light_app_rows")
    with db.connect() as conn:
        kind = conn.execute("SELECT kind FROM users WHERE id = ?", (owner_id,)).fetchone()[0]
        member_visible = conn.execute(
            "SELECT member_visible FROM light_app_tables "
            "WHERE app_id = 'app1' AND table_key = 'main'"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT created_by, row_owner_user_id FROM light_app_rows ORDER BY id"
        ).fetchall()
        user_indexes = {
            str(row["name"]) for row in conn.execute("PRAGMA index_list(users)").fetchall()
        }
        row_indexes = {
            str(row["name"]) for row in conn.execute("PRAGMA index_list(light_app_rows)").fetchall()
        }
    assert kind == "member"
    assert int(member_visible) == 0
    assert int(rows[0]["row_owner_user_id"]) == owner_id
    assert rows[1]["row_owner_user_id"] is None
    assert "idx_users_kind" in user_indexes
    assert "idx_light_app_rows_row_owner" in row_indexes


def test_v46_replay_is_idempotent(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    with db.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 45")

    migrate.run_migrations(db)
    migrate.run_migrations(db)

    assert _version(db) == 52
    assert "kind" in _columns(db, "users")
    assert "member_visible" in _columns(db, "light_app_tables")
    assert "row_owner_user_id" in _columns(db, "light_app_rows")


def test_v46_repairs_columns_when_version_was_recorded(tmp_path: Path) -> None:
    """The end-of-run ensure repairs a DB that recorded v46 without the DDL."""
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    with db.connect() as conn:
        conn.execute("DROP INDEX idx_users_kind")
        conn.execute("ALTER TABLE users DROP COLUMN kind")
        conn.execute("ALTER TABLE light_app_tables DROP COLUMN member_visible")
        conn.execute("DROP INDEX idx_light_app_rows_row_owner")
        conn.execute("ALTER TABLE light_app_rows DROP COLUMN row_owner_user_id")

    migrate.run_migrations(db)

    assert _version(db) == 52
    assert "kind" in _columns(db, "users")
    assert "member_visible" in _columns(db, "light_app_tables")
    assert "row_owner_user_id" in _columns(db, "light_app_rows")
