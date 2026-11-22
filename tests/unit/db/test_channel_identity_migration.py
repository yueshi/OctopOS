"""Schema v48: channel identity v2, access-code columns and /link codes."""

from __future__ import annotations

from pathlib import Path

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool


def _columns(db: SqlitePool, table: str) -> set[str]:
    with db.connect() as conn:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _version(db: SqlitePool) -> int:
    with db.connect() as conn:
        return int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])


def test_fresh_schema_has_v2_identity_and_link_codes(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    assert _version(db) == 51
    assert _columns(db, "channel_identities") == {
        "id",
        "channel_type",
        "account_key",
        "sender_id",
        "user_id",
        "verified_at",
        "created_at",
    }
    assert {"account_key", "sender_id"}.issubset(_columns(db, "app_access_codes"))
    with db.connect() as conn:
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "channel_link_codes" in tables
    # Second run is a no-op.
    run_migrations(db)
    assert _version(db) == 51


def test_legacy_binding_is_preserved_and_still_resolves(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    with db.connect() as conn:
        conn.execute("DROP TABLE channel_identities")
        conn.execute(
            """
            CREATE TABLE channel_identities (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              channel_type TEXT NOT NULL,
              external_subject_id TEXT NOT NULL,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              verified_at INTEGER NOT NULL,
              created_at INTEGER NOT NULL,
              UNIQUE(channel_type, external_subject_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) "
            "VALUES ('legacy', 'h', 'user', 1)"
        )
        user_id = int(conn.execute("SELECT id FROM users WHERE username = 'legacy'").fetchone()[0])
        conn.execute(
            "INSERT INTO channel_identities("
            "channel_type, external_subject_id, user_id, verified_at, created_at"
            ") VALUES ('feishu', 'ou-1', ?, 1, 1)",
            (user_id,),
        )
        conn.execute("UPDATE _schema_version SET version = 47")

    run_migrations(db)
    assert _version(db) == 51
    with db.connect() as conn:
        row = conn.execute(
            "SELECT account_key, sender_id, user_id FROM channel_identities "
            "WHERE channel_type = 'feishu'"
        ).fetchone()
    assert dict(row) == {"account_key": "", "sender_id": "ou-1", "user_id": user_id}
