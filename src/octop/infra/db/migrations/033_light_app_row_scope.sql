-- Schema v33: table-level row scope for MiniApps.
-- 'app' keeps the existing shared-table behavior; 'user' means each normal
-- user sees only rows they created. The owner/admin always sees every row.

ALTER TABLE light_app_tables ADD COLUMN row_scope TEXT NOT NULL DEFAULT 'app';

CREATE INDEX IF NOT EXISTS idx_light_app_rows_owner
  ON light_app_rows(app_id, table_key, created_by);

UPDATE _schema_version SET version = 33;
