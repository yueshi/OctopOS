-- Schema v34: declarative table rules (state machine v1).
-- See 034_light_app_table_rules.sql for the contract.

ALTER TABLE light_app_tables ADD COLUMN IF NOT EXISTS rules_json TEXT NOT NULL DEFAULT '{}';

UPDATE _schema_version SET version = 34;
