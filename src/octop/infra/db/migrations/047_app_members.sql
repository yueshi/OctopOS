-- Schema v47: MiniApp members, external invites and join codes.
--
-- Collaborator rows gain a lifecycle (status), provenance (joined_via) and the
-- inviter (invited_by). External people are described by app_member_invites
-- before they have a users row; app_join_codes are app-wide bearer codes.
--
-- The runner also applies `_ensure_app_member_schema` idempotently so a
-- recorded version without these columns/tables (or a replayed migration)
-- repairs itself. Keep this file and the helper equivalent.

ALTER TABLE light_app_collaborators ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE light_app_collaborators ADD COLUMN joined_via TEXT NOT NULL DEFAULT 'share';
ALTER TABLE light_app_collaborators ADD COLUMN invited_by INTEGER;
ALTER TABLE agents ADD COLUMN external_sharing_policy TEXT NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_light_app_collaborators_status
  ON light_app_collaborators(app_id, status);

CREATE TABLE IF NOT EXISTS app_member_invites (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  invite_id     TEXT NOT NULL UNIQUE,
  app_id        TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  token_hash    TEXT NOT NULL UNIQUE,
  audience      TEXT NOT NULL,
  permission    TEXT NOT NULL,
  invitee_hint  TEXT NOT NULL DEFAULT '',
  expires_at    INTEGER NOT NULL,
  max_uses      INTEGER NOT NULL DEFAULT 1,
  used_count    INTEGER NOT NULL DEFAULT 0,
  host_status   TEXT NOT NULL DEFAULT 'not_required',
  created_by    INTEGER NOT NULL,
  revoked_at    INTEGER,
  created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_member_invites_app
  ON app_member_invites(app_id, created_at);
CREATE INDEX IF NOT EXISTS idx_app_member_invites_host
  ON app_member_invites(host_status);

CREATE TABLE IF NOT EXISTS app_join_codes (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  join_code_id       TEXT NOT NULL UNIQUE,
  code_hash          TEXT NOT NULL UNIQUE,
  app_id             TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  audience           TEXT NOT NULL,
  permission         TEXT NOT NULL,
  expires_at         INTEGER NOT NULL,
  max_uses           INTEGER NOT NULL DEFAULT 1,
  used_count         INTEGER NOT NULL DEFAULT 0,
  requires_approval  INTEGER NOT NULL DEFAULT 0,
  revoked_at         INTEGER,
  created_by         INTEGER NOT NULL,
  created_at         INTEGER NOT NULL,
  bound_channel_type TEXT NOT NULL DEFAULT '',
  bound_account_key  TEXT NOT NULL DEFAULT '',
  bound_sender_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_app_join_codes_app
  ON app_join_codes(app_id, created_at);

CREATE TABLE IF NOT EXISTS app_sessions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id         TEXT NOT NULL UNIQUE,
  user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope              TEXT NOT NULL DEFAULT 'guest',
  refresh_token_hash TEXT NOT NULL UNIQUE,
  created_at         INTEGER NOT NULL,
  expires_at         INTEGER NOT NULL,
  revoked_at         INTEGER,
  last_seen_at       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_app_sessions_user
  ON app_sessions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_app_sessions_expires
  ON app_sessions(expires_at);

UPDATE _schema_version SET version = 47;
