"""v35 migration: split access-request owners and dedupe pending rows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.users import UserRepo


def _version(db: SqlitePool) -> int:
    with db.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])


def test_v35_splits_owners_and_dedupes_pending(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    migrate._discover = lambda dialect="sqlite": [
        (version, path) for version, path in real_discover(dialect) if version <= 34
    ]
    try:
        migrate.run_migrations(db)
        assert _version(db) == 34
        users = UserRepo(db)
        alice = users.create(username="alice", password_hash="h", role="user")
        bob = users.create(username="bob", password_hash="h", role="user")
        carol = users.create(username="carol", password_hash="h", role="user")
        AgentRepo(db).create(agent_id="ag1", user_id=alice, name="bot")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO light_apps("
                "app_id, agent_id, owner_user_id, name, created_at, updated_at"
                ") VALUES ('app1', 'ag1', ?, 'Tracker', 1, 1)",
                (bob,),
            )
            for created_at, status in ((1, "pending"), (2, "pending")):
                conn.execute(
                    "INSERT INTO app_access_requests("
                    "request_id, app_id, agent_id, requester_user_id, owner_user_id, "
                    "needed, status, created_at"
                    ") VALUES (?, 'app1', 'ag1', ?, ?, 'view', ?, ?)",
                    (f"req_app_{created_at}", carol, bob, status, created_at),
                )
            conn.execute(
                "INSERT INTO app_access_requests("
                "request_id, app_id, agent_id, requester_user_id, owner_user_id, "
                "needed, status, created_at"
                ") VALUES ('req_agent', NULL, 'ag1', ?, ?, 'view', 'pending', 3)",
                (carol, alice),
            )
    finally:
        migrate._discover = real_discover

    migrate.run_migrations(db)
    assert _version(db) == 45

    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(app_access_requests)")}
        rows = conn.execute("SELECT * FROM app_access_requests ORDER BY id").fetchall()
    assert "owner_user_id" not in columns
    assert {"app_owner_user_id", "agent_owner_user_id"} <= columns

    app_rows = [row for row in rows if row["app_id"] == "app1"]
    assert [(row["request_id"], row["status"]) for row in app_rows] == [
        ("req_app_1", "rejected"),
        ("req_app_2", "pending"),
    ]
    assert all(row["app_owner_user_id"] == bob for row in app_rows)
    assert all(row["agent_owner_user_id"] == alice for row in app_rows)

    agent_row = next(row for row in rows if row["request_id"] == "req_agent")
    assert agent_row["app_owner_user_id"] is None
    assert agent_row["agent_owner_user_id"] == alice

    # The partial unique index keeps the surviving pending row authoritative.
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO app_access_requests("
            "request_id, app_id, agent_id, requester_user_id, "
            "app_owner_user_id, agent_owner_user_id, needed, status, created_at"
            ") VALUES ('req_dup', 'app1', 'ag1', ?, ?, ?, 'view', 'pending', 4)",
            (carol, bob, alice),
        )


def test_v35_recreates_a_missing_table(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    migrate._discover = lambda dialect="sqlite": [
        (version, path) for version, path in real_discover(dialect) if version <= 34
    ]
    try:
        migrate.run_migrations(db)
        with db.connect() as conn:
            conn.execute("DROP TABLE app_access_requests")
    finally:
        migrate._discover = real_discover

    migrate.run_migrations(db)
    assert _version(db) == 45
    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(app_access_requests)")}
    assert {"app_owner_user_id", "agent_owner_user_id"} <= columns
    assert "owner_user_id" not in columns
