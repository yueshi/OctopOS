-- Schema v51: usage attribution for MiniApp AI tasks.
-- ``user_id`` remains the billing subject; app_id/task_id make per-app and
-- per-task cost reporting possible without joining thread/source heuristics.

ALTER TABLE usage_log ADD COLUMN app_id TEXT NOT NULL DEFAULT '';
ALTER TABLE usage_log ADD COLUMN task_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_usage_log_app ON usage_log(app_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_log_task ON usage_log(task_id);

UPDATE _schema_version SET version = 51;
