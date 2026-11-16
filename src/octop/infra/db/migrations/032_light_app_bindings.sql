-- Schema v32: MiniApp service bindings.
-- A binding declares where a MiniApp gets data or actions. The built-in
-- dataset is implicit (binding_id='dataset'); connector/external bindings are
-- stored here and resolved by the R2 service layer.

CREATE TABLE IF NOT EXISTS light_app_bindings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  binding_id  TEXT NOT NULL,
  app_id      TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  provider    TEXT NOT NULL,
  service_id  TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  enabled     INTEGER NOT NULL DEFAULT 1,
  version     INTEGER NOT NULL DEFAULT 1,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  UNIQUE(app_id, binding_id)
);

CREATE INDEX IF NOT EXISTS idx_light_app_bindings_app
  ON light_app_bindings(app_id);

UPDATE _schema_version SET version = 32;
