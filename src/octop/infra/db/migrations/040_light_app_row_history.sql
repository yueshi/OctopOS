-- Schema v40: append-only row change history for MiniApps.
-- Every update, delete and replace-import records the row's before/after
-- values so an owner can answer "who changed what, when". Appends are not
-- recorded here: the row itself carries created_by/created_at/source.

CREATE TABLE IF NOT EXISTS light_app_row_history (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id       TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  table_key    TEXT NOT NULL,
  row_id       TEXT NOT NULL,
  version      INTEGER NOT NULL,
  action       TEXT NOT NULL,
  actor        TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT '',
  before_json  TEXT,
  after_json   TEXT,
  changed_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_light_app_row_history_row
  ON light_app_row_history(app_id, row_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_light_app_row_history_app
  ON light_app_row_history(app_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_light_app_row_history_changed
  ON light_app_row_history(changed_at);

UPDATE _schema_version SET version = 40;
