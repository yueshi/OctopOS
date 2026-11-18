-- Schema v41: app-scoped event idempotency and delivery result payloads.
--
-- ``event_id`` used to be globally unique, so the same external event fanned
-- out to two MiniApps made the second app read the first app's inbox row.
-- The idempotency key is now ``(app_id, event_id)``.
--
-- SQLite cannot drop a UNIQUE constraint, so the table is rebuilt.

CREATE TABLE IF NOT EXISTS light_app_events_new (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id        TEXT NOT NULL,
  app_id          TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  event_type      TEXT NOT NULL,
  source          TEXT NOT NULL DEFAULT '',
  summary         TEXT NOT NULL DEFAULT '',
  payload_json    TEXT NOT NULL DEFAULT '{}',
  created_at      INTEGER NOT NULL,
  acknowledged_at INTEGER,
  UNIQUE(app_id, event_id)
);
INSERT OR IGNORE INTO light_app_events_new(
  id, event_id, app_id, event_type, source, summary, payload_json, created_at, acknowledged_at
)
SELECT id, event_id, app_id, event_type, source, summary, payload_json, created_at, acknowledged_at
FROM light_app_events;
DROP TABLE light_app_events;
ALTER TABLE light_app_events_new RENAME TO light_app_events;
CREATE INDEX IF NOT EXISTS idx_light_app_events_app
  ON light_app_events(app_id, id);
CREATE INDEX IF NOT EXISTS idx_light_app_events_created
  ON light_app_events(created_at);

ALTER TABLE external_deliveries ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}';

UPDATE _schema_version SET version = 41;
