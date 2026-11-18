"""v41 rebuilds the app event inbox to scope event ids per app."""

from __future__ import annotations

from pathlib import Path

from octop.infra.db.migrate import (
    _ensure_external_deliveries_result_schema,
    _ensure_light_app_events_schema,
    _light_app_events_has_global_unique,
)
from octop.infra.db.pool import SqlitePool


def test_v41_rebuilds_global_event_unique_and_adds_delivery_result(
    tmp_path: Path,
) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    with db.connect() as conn:
        conn.execute("CREATE TABLE light_apps (app_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO light_apps(app_id) VALUES ('app1'), ('app2')")
        conn.execute(
            """
            CREATE TABLE light_app_events (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id        TEXT NOT NULL UNIQUE,
              app_id          TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
              event_type      TEXT NOT NULL,
              source          TEXT NOT NULL DEFAULT '',
              summary         TEXT NOT NULL DEFAULT '',
              payload_json    TEXT NOT NULL DEFAULT '{}',
              created_at      INTEGER NOT NULL,
              acknowledged_at INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO light_app_events(event_id, app_id, event_type, created_at) "
            "VALUES ('ext_1', 'app1', 'order.created', 1)"
        )
        conn.execute("CREATE TABLE external_deliveries (id INTEGER PRIMARY KEY)")

    assert _light_app_events_has_global_unique(db) is True

    _ensure_light_app_events_schema(db)
    _ensure_external_deliveries_result_schema(db)
    # Re-running the repair must be a no-op.
    _ensure_light_app_events_schema(db)
    _ensure_external_deliveries_result_schema(db)

    assert _light_app_events_has_global_unique(db) is False
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO light_app_events(event_id, app_id, event_type, created_at) "
            "VALUES ('ext_1', 'app2', 'order.created', 2)"
        )
        rows = conn.execute(
            "SELECT app_id FROM light_app_events WHERE event_id = 'ext_1' ORDER BY app_id"
        ).fetchall()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(external_deliveries)")}
    assert [row["app_id"] for row in rows] == ["app1", "app2"]
    assert "result_json" in columns
