-- Schema v38: R2.3 application-level schedules.
-- Interval jobs with a DB lease; the first actions are sync_source and
-- snapshot_publication. Existing source intervals are migrated once.

CREATE TABLE IF NOT EXISTS light_app_schedules (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  schedule_id      TEXT NOT NULL UNIQUE,
  app_id           TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  action           TEXT NOT NULL,
  target_json      TEXT NOT NULL DEFAULT '{}',
  interval_seconds INTEGER NOT NULL,
  next_run_at      INTEGER NOT NULL,
  lease_until      INTEGER,
  last_run_at      INTEGER,
  last_status      TEXT NOT NULL DEFAULT '',
  last_error       TEXT NOT NULL DEFAULT '',
  failure_count    INTEGER NOT NULL DEFAULT 0,
  enabled          INTEGER NOT NULL DEFAULT 1,
  created_by       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_light_app_schedules_due
  ON light_app_schedules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_light_app_schedules_app
  ON light_app_schedules(app_id);

-- Migrate the old per-source interval once. The deterministic id makes this
-- idempotent; the old source scheduler skips sources that have this schedule.
INSERT INTO light_app_schedules(
  schedule_id, app_id, action, target_json, interval_seconds, next_run_at,
  lease_until, last_run_at, last_status, last_error, failure_count, enabled,
  created_by, created_at, updated_at
)
SELECT
  'sch_src_' || source_id,
  app_id,
  'sync_source',
  json_object('source_id', source_id),
  schedule_seconds,
  COALESCE(next_run_at, created_at),
  NULL, last_run_at, COALESCE(last_status, ''), COALESCE(last_error, ''),
  COALESCE(fail_count, 0), 1, created_by, created_at, updated_at
FROM light_app_sources
WHERE schedule_seconds > 0
  AND NOT EXISTS (
    SELECT 1 FROM light_app_schedules
    WHERE schedule_id = 'sch_src_' || light_app_sources.source_id
  );

UPDATE _schema_version SET version = 38;
