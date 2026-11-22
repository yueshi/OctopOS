-- Schema v46: MiniApp identity primitives.
--
-- Adds the first pieces of the v3 identity model:
--   * users.kind distinguishes platform members from guests.
--   * light_app_tables.member_visible gates tables for `participate`.
--   * light_app_rows.row_owner_user_id separates row ownership from the
--     executing user (`created_by`).
--
-- The runner also applies `_ensure_miniapp_identity_schema` idempotently so a
-- recorded version without these columns (or a replayed migration) repairs
-- itself. Keep this file and the helper equivalent.

ALTER TABLE users ADD COLUMN kind TEXT NOT NULL DEFAULT 'member';
CREATE INDEX IF NOT EXISTS idx_users_kind ON users(kind);

ALTER TABLE light_app_tables ADD COLUMN member_visible INTEGER NOT NULL DEFAULT 0;

ALTER TABLE light_app_rows ADD COLUMN row_owner_user_id INTEGER;
UPDATE light_app_rows
SET row_owner_user_id = created_by
WHERE row_owner_user_id IS NULL AND created_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_light_app_rows_row_owner
  ON light_app_rows(app_id, table_key, row_owner_user_id);

UPDATE _schema_version SET version = 46;
