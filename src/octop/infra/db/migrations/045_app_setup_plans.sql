-- Schema v45: durable MiniApp setup plans.
--
-- A plan is the single authorization/state record for a file-based MiniApp
-- creation: inspect report, proposed schema, dry-run summary, created app and
-- import result all point at one plan_id. The dashboard card is backed by this
-- row, so history can render the final state without replaying a prompt.

CREATE TABLE IF NOT EXISTS app_setup_plans (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id       TEXT NOT NULL UNIQUE,
  thread_id     TEXT REFERENCES threads(thread_id) ON DELETE CASCADE,
  agent_id      TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_path   TEXT NOT NULL,
  source_hash   TEXT NOT NULL DEFAULT '',
  report_json   TEXT NOT NULL DEFAULT '{}',
  table_json    TEXT NOT NULL DEFAULT '{}',
  name          TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'draft',
  revision      INTEGER NOT NULL DEFAULT 1,
  app_id        TEXT REFERENCES light_apps(app_id) ON DELETE SET NULL,
  dry_run_json  TEXT NOT NULL DEFAULT '{}',
  result_json   TEXT NOT NULL DEFAULT '{}',
  error         TEXT NOT NULL DEFAULT '',
  expires_at    INTEGER,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  confirmed_at  INTEGER,
  executed_at   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_app_setup_plans_thread
  ON app_setup_plans(thread_id, status);
CREATE INDEX IF NOT EXISTS idx_app_setup_plans_user
  ON app_setup_plans(user_id, created_at);

-- One app per setup plan; the partial unique index is the final guard against
-- two concurrent confirms creating duplicate apps.
ALTER TABLE light_apps ADD COLUMN setup_plan_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_light_apps_setup_plan
  ON light_apps(setup_plan_id) WHERE setup_plan_id IS NOT NULL;

-- IM channel identity binding (D4) and one-time MiniApp access links (D3).
-- The bind code and access code are stored hashed; the plaintext is returned
-- once and never persisted.

CREATE TABLE IF NOT EXISTS channel_identities (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_type         TEXT NOT NULL,
  external_subject_id  TEXT NOT NULL,
  user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  verified_at          INTEGER NOT NULL,
  created_at           INTEGER NOT NULL,
  UNIQUE(channel_type, external_subject_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_identities_user
  ON channel_identities(user_id, channel_type);

CREATE TABLE IF NOT EXISTS channel_bind_codes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash   TEXT NOT NULL UNIQUE,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at  INTEGER NOT NULL,
  consumed_at INTEGER,
  created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channel_bind_codes_expires
  ON channel_bind_codes(expires_at);

CREATE TABLE IF NOT EXISTS app_access_codes (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash          TEXT NOT NULL UNIQUE,
  app_id             TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  permission         TEXT NOT NULL,
  channel_type       TEXT NOT NULL DEFAULT '',
  channel_subject_id TEXT NOT NULL DEFAULT '',
  expires_at         INTEGER NOT NULL,
  consumed_at        INTEGER,
  created_at         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_access_codes_user
  ON app_access_codes(user_id, app_id);
CREATE INDEX IF NOT EXISTS idx_app_access_codes_expires
  ON app_access_codes(expires_at);

UPDATE _schema_version SET version = 45;
