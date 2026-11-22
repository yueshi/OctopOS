"""v38 migration: source intervals become application-level schedules."""

from __future__ import annotations

from pathlib import Path

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.users import UserRepo


def _version(db: SqlitePool) -> int:
    with db.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])


def _seed_source(db: SqlitePool) -> None:
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    AgentRepo(db).create(agent_id="ag1", user_id=alice, name="bot")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO light_apps("
            "app_id, agent_id, owner_user_id, name, created_at, updated_at"
            ") VALUES ('app1', 'ag1', ?, 'Tracker', 1, 1)",
            (alice,),
        )
        conn.execute(
            "INSERT INTO light_app_sources("
            "source_id, app_id, table_key, kind, mode, schedule_seconds, "
            "params_json, mapping_json, next_run_at, fail_count, last_run_at, "
            "last_status, last_error, created_by, created_at, updated_at"
            ") VALUES ('src1', 'app1', 'main', 'workspace_file', 'append', 120, "
            "'{}', '{}', 555, 2, 444, 'error', 'boom', ?, 100, 100)",
            (alice,),
        )


def test_v38_migrates_source_intervals(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    real_discover = migrate._discover
    migrate._discover = lambda dialect="sqlite": [
        (version, path) for version, path in real_discover(dialect) if version <= 37
    ]
    try:
        migrate.run_migrations(db)
        assert _version(db) == 37
        _seed_source(db)
    finally:
        migrate._discover = real_discover

    migrate.run_migrations(db)
    assert _version(db) == 51
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM light_app_schedules").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["schedule_id"] == "sch_src_src1"
    assert row["app_id"] == "app1"
    assert row["action"] == "sync_source"
    assert row["target_json"] == '{"source_id":"src1"}'
    assert row["interval_seconds"] == 120
    assert row["next_run_at"] == 555
    assert row["last_run_at"] == 444
    assert row["last_status"] == "error"
    assert row["failure_count"] == 2

    # Re-running migrations keeps one schedule per source.
    migrate.run_migrations(db)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM light_app_schedules").fetchone()[0] == 1


def test_v38_repairs_a_recorded_version_without_the_table(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    migrate.run_migrations(db)
    _seed_source(db)
    with db.transaction() as conn:
        conn.execute("DROP TABLE light_app_schedules")
        conn.execute("UPDATE _schema_version SET version = 38")

    migrate.run_migrations(db)
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM light_app_schedules").fetchall()
    assert [row["schedule_id"] for row in rows] == ["sch_src_src1"]
