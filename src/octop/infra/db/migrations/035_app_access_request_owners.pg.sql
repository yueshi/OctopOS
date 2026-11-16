-- Schema v35: split the access-request owner into app and expert owners, and
-- enforce one pending request per requester/target. Mirrors the SQLite file.

ALTER TABLE app_access_requests
  ADD COLUMN IF NOT EXISTS app_owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE app_access_requests
  ADD COLUMN IF NOT EXISTS agent_owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

UPDATE app_access_requests
SET agent_owner_user_id = COALESCE(
  (SELECT user_id FROM agents WHERE agents.agent_id = app_access_requests.agent_id),
  owner_user_id
)
WHERE agent_owner_user_id IS NULL;

UPDATE app_access_requests
SET app_owner_user_id = COALESCE(
  (SELECT owner_user_id FROM light_apps WHERE light_apps.app_id = app_access_requests.app_id),
  owner_user_id
)
WHERE app_id IS NOT NULL AND app_owner_user_id IS NULL;

UPDATE app_access_requests
SET status = 'rejected', resolved_at = EXTRACT(EPOCH FROM NOW())::BIGINT
WHERE status = 'pending'
  AND app_id IS NOT NULL
  AND id NOT IN (
    SELECT MAX(id) FROM app_access_requests
    WHERE status = 'pending' AND app_id IS NOT NULL
    GROUP BY requester_user_id, app_id
  );

UPDATE app_access_requests
SET status = 'rejected', resolved_at = EXTRACT(EPOCH FROM NOW())::BIGINT
WHERE status = 'pending'
  AND app_id IS NULL
  AND id NOT IN (
    SELECT MAX(id) FROM app_access_requests
    WHERE status = 'pending' AND app_id IS NULL
    GROUP BY requester_user_id, agent_id
  );

DROP INDEX IF EXISTS idx_app_access_requests_owner;
CREATE INDEX IF NOT EXISTS idx_app_access_requests_app_owner
  ON app_access_requests(app_owner_user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_app_access_requests_agent_owner
  ON app_access_requests(agent_owner_user_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_access_requests_pending_app
  ON app_access_requests(requester_user_id, app_id)
  WHERE status = 'pending' AND app_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_access_requests_pending_agent
  ON app_access_requests(requester_user_id, agent_id)
  WHERE status = 'pending' AND app_id IS NULL;

ALTER TABLE app_access_requests DROP COLUMN IF EXISTS owner_user_id;

UPDATE _schema_version SET version = 35;
