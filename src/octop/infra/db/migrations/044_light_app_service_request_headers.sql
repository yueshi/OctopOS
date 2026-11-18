-- Schema v44: allowlisted request headers for public HTTP services.

ALTER TABLE light_app_services
  ADD COLUMN request_headers_json TEXT NOT NULL DEFAULT '{}';

UPDATE _schema_version SET version = 44;
