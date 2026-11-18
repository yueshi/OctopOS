-- Schema v42: owner-declared outbound webhooks for MiniApps.
-- Each webhook owns an internal external source and one webhook.post
-- subscription, so delivery reuses the existing external-event outbox.

CREATE TABLE IF NOT EXISTS light_app_webhooks (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  webhook_id      TEXT NOT NULL UNIQUE,
  app_id          TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  url             TEXT NOT NULL,
  event_types_json TEXT NOT NULL DEFAULT '[]',
  source_id       TEXT NOT NULL REFERENCES external_sources(source_id) ON DELETE CASCADE,
  subscription_id TEXT NOT NULL REFERENCES external_subscriptions(subscription_id)
                  ON DELETE CASCADE,
  secret_blob     BLOB,
  enabled         INTEGER NOT NULL DEFAULT 1,
  created_by      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_light_app_webhooks_app
  ON light_app_webhooks(app_id);

UPDATE _schema_version SET version = 42;
