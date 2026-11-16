-- Schema v34: declarative table rules (state machine v1).
-- rules_json is an empty object for tables without rules.

ALTER TABLE light_app_tables ADD COLUMN rules_json TEXT NOT NULL DEFAULT '{}';

UPDATE _schema_version SET version = 34;
