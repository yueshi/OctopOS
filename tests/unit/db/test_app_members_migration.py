"""v47 migration: collaborator lifecycle and member invite/join tables."""

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


def _table_names(db: SqlitePool) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _seed_collaborator(db: SqlitePool) -> tuple[int, int]:
    owner_id = UserRepo(db).create(username="alice", password_hash="h", role="user")
    bob_id = UserRepo(db).create(username="bob", password_hash="h", role="user")
    AgentRepo(db).create(agent_id="ag1", user_id=owner_id, name="bot")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO light_apps("
            "app_id, agent_id, owner_user_id, name, created_at, updated_at"
            ") VALUES ('app1', 'ag1', ?, 'Tracker', 1, 1)",
            (owner_id,),
        )
    LightAppRepo(db).set_collaborators("app1", [(bob_id, "view")])
    return owner_id, bob_id


def test_v47_adds_collaborator_fields_and_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [
            (version, path) for version, path in real_discover(dialect) if version <= 46
        ],
    )
    migrate.run_migrations(db)
    assert _version(db) == 46
    _owner_id, bob_id = _seed_collaborator(db)

    monkeypatch.setattr(migrate, "_discover", real_discover)
    migrate.run_migrations(db)

    assert _version(db) == 47
    assert {"status", "joined_via", "invited_by"} <= _columns(db, "light_app_collaborators")
    assert "external_sharing_policy" in _columns(db, "agents")
    assert {"app_member_invites", "app_join_codes", "app_sessions"} <= _table_names(db)
    with db.connect() as conn:
        policy = conn.execute(
            "SELECT external_sharing_policy FROM agents WHERE agent_id = 'ag1'"
        ).fetchone()
        row = conn.execute(
            "SELECT status, joined_via, invited_by FROM light_app_collaborators "
            "WHERE app_id = 'app1' AND user_id = ?",
            (bob_id,),
        ).fetchone()
        indexes = {
            str(item["name"])
            for item in conn.execute("PRAGMA index_list(light_app_collaborators)").fetchall()
        }
    assert row["status"] == "active"
    assert row["joined_via"] == "share"
    assert row["invited_by"] is None
    assert policy["external_sharing_policy"] == "{}"
    assert "idx_light_app_collaborators_status" in indexes


def test_permission_for_requires_active_status(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    _owner_id, bob_id = _seed_collaborator(db)
    repo = LightAppRepo(db)

    assert repo.permission_for("app1", bob_id) == "view"
    repo.update_collaborator("app1", bob_id, status="pending")
    assert repo.permission_for("app1", bob_id) is None
    repo.update_collaborator("app1", bob_id, status="active", permission="edit_data")
    assert repo.permission_for("app1", bob_id) == "edit_data"
    repo.update_collaborator("app1", bob_id, status="removed")
    assert repo.permission_for("app1", bob_id) is None
    assert repo.list_shared(bob_id) == []


def test_v47_replay_is_idempotent(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    with db.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 46")

    migrate.run_migrations(db)
    migrate.run_migrations(db)

    assert _version(db) == 47
    assert {"status", "joined_via", "invited_by"} <= _columns(db, "light_app_collaborators")
    assert "external_sharing_policy" in _columns(db, "agents")
    assert {"app_member_invites", "app_join_codes", "app_sessions"} <= _table_names(db)


def test_v47_repairs_schema_when_version_was_recorded(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    with db.connect() as conn:
        conn.execute("DROP INDEX idx_light_app_collaborators_status")
        conn.execute("ALTER TABLE light_app_collaborators DROP COLUMN status")
        conn.execute("ALTER TABLE light_app_collaborators DROP COLUMN joined_via")
        conn.execute("ALTER TABLE light_app_collaborators DROP COLUMN invited_by")
        conn.execute("DROP TABLE app_member_invites")
        conn.execute("DROP TABLE app_join_codes")
        conn.execute("DROP TABLE app_sessions")
        conn.execute("ALTER TABLE agents DROP COLUMN external_sharing_policy")

    migrate.run_migrations(db)

    assert _version(db) == 47
    assert {"status", "joined_via", "invited_by"} <= _columns(db, "light_app_collaborators")
    assert "external_sharing_policy" in _columns(db, "agents")
    assert {"app_member_invites", "app_join_codes", "app_sessions"} <= _table_names(db)
