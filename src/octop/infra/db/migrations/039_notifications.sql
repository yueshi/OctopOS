-- Schema v39: durable dashboard notification inbox.
-- One row per notification recipient. ``dedupe_key`` is scoped to the user so
-- repeated approvals or access requests do not create duplicate rows.
-- ``read_at`` is the read cursor; there is no hard delete except the explicit
-- user-facing "clear" action.

CREATE TABLE IF NOT EXISTS notifications (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  notification_id TEXT NOT NULL UNIQUE,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,
  title           TEXT NOT NULL,
  body            TEXT NOT NULL DEFAULT '',
  path            TEXT,
  dedupe_key      TEXT,
  read_at         INTEGER,
  created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_created
  ON notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
  ON notifications(user_id, read_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_user_dedupe
  ON notifications(user_id, dedupe_key);

UPDATE _schema_version SET version = 39;
