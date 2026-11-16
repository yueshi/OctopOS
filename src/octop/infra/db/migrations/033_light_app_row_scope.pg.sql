-- Schema v33: table-level row scope for MiniApps.
-- See 033_light_app_row_scope.sql for the contract.

ALTER TABLE light_app_tables ADD COLUMN IF NOT EXISTS row_scope TEXT NOT NULL DEFAULT 'app';

CREATE INDEX IF NOT EXISTS idx_light_app_rows_owner
  ON light_app_rows(app_id, table_key, created_by);

UPDATE _schema_version SET version = 33;
