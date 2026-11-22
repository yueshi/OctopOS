-- Schema v46: MiniApp identity primitives.
--
-- PostgreSQL mirror of 046_miniapp_identity.sql. The runner uses the
-- idempotent `_ensure_miniapp_identity_schema` helper; this file is the
-- canonical v45 -> v46 DDL for fresh databases and reviewers.

ALTER TABLE users ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'member';
CREATE INDEX IF NOT EXISTS idx_users_kind ON users(kind);

ALTER TABLE light_app_tables
  ADD COLUMN IF NOT EXISTS member_visible INTEGER NOT NULL DEFAULT 0;

ALTER TABLE light_app_rows
  ADD COLUMN IF NOT EXISTS row_owner_user_id INTEGER;
UPDATE light_app_rows
SET row_owner_user_id = created_by
WHERE row_owner_user_id IS NULL AND created_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_light_app_rows_row_owner
  ON light_app_rows(app_id, table_key, row_owner_user_id);

UPDATE _schema_version SET version = 46;
