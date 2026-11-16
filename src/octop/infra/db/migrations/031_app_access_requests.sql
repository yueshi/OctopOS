-- Schema v31: MiniApp access requests.
-- A blocked user asks the app/agent owner for access; the owner (or an admin)
-- approves by sharing the expert and granting the requested app permission.

CREATE TABLE IF NOT EXISTS app_access_requests (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id        TEXT NOT NULL UNIQUE,
  app_id            TEXT REFERENCES light_apps(app_id) ON DELETE CASCADE,
  agent_id          TEXT NOT NULL,
  requester_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  owner_user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  needed            TEXT NOT NULL,
  note              TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'pending',
  created_at        INTEGER NOT NULL,
  resolved_at       INTEGER,
  resolved_by       INTEGER REFERENCES users(id)
);
-- The owner index is superseded by v35's app/agent owner indexes.
CREATE INDEX IF NOT EXISTS idx_app_access_requests_requester
  ON app_access_requests(requester_user_id, created_at);

UPDATE _schema_version SET version = 31;
