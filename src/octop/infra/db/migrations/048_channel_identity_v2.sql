-- Schema v48: IM identity v2 and the /link handshake.
--
-- `channel_identities` gains the bot/account dimension (`account_key`) so the
-- same platform sender on two bots is not silently merged. Existing bindings
-- keep their old single-key behaviour via `account_key = ''` and still match
-- as a legacy fallback. `app_access_codes` records the issuing IM identity;
-- `channel_link_codes` carries the short WebView-to-IM handshake code.
--
-- The runner applies `_ensure_miniapp_im_identity_v2_schema` idempotently
-- because SQLite cannot express this table rebuild as an ALTER. Keep this file
-- and the helper equivalent.

CREATE TABLE channel_identities_new (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_type  TEXT NOT NULL,
  account_key   TEXT NOT NULL DEFAULT '',
  sender_id     TEXT NOT NULL,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  verified_at   INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  UNIQUE(channel_type, account_key, sender_id)
);

INSERT INTO channel_identities_new(
  id, channel_type, account_key, sender_id, user_id, verified_at, created_at
)
SELECT id, channel_type, '', external_subject_id, user_id, verified_at, created_at
FROM channel_identities;

DROP TABLE channel_identities;
ALTER TABLE channel_identities_new RENAME TO channel_identities;

CREATE INDEX IF NOT EXISTS idx_channel_identities_user
  ON channel_identities(user_id, channel_type);

ALTER TABLE app_access_codes ADD COLUMN account_key TEXT NOT NULL DEFAULT '';
ALTER TABLE app_access_codes ADD COLUMN sender_id TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS channel_link_codes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash   TEXT NOT NULL UNIQUE,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  app_id      TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  expires_at  INTEGER NOT NULL,
  consumed_at INTEGER,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channel_link_codes_expires
  ON channel_link_codes(expires_at);

UPDATE _schema_version SET version = 48;
