-- Schema v41: app-scoped event idempotency and delivery result payloads.
-- Mirrors the SQLite file. PostgreSQL can drop the old global unique constraint.

ALTER TABLE light_app_events
  DROP CONSTRAINT IF EXISTS light_app_events_event_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_light_app_events_app_event
  ON light_app_events(app_id, event_id);

ALTER TABLE external_deliveries
  ADD COLUMN IF NOT EXISTS result_json TEXT NOT NULL DEFAULT '{}';

UPDATE _schema_version SET version = 41;
