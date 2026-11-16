-- Schema v35: split the access-request owner into app and expert owners, and
-- enforce one pending request per requester/target.
--
-- A MiniApp can be owned by a collaborator while its expert belongs to someone
-- else. The single owner column could only notify/authorize one side, so an
-- approval could be marked done while the requester still lacked expert access.
-- Both owners are now recorded and each can resolve their half.

ALTER TABLE app_access_requests ADD COLUMN app_owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE app_access_requests ADD COLUMN agent_owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

-- Backfill from the current app/agent rows; fall back to the legacy owner.
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

-- Collapse pre-existing duplicates (keep the newest) before adding the unique
-- indexes. Older duplicates are rejected rather than deleted.
UPDATE app_access_requests
SET status = 'rejected', resolved_at = CAST(strftime('%s', 'now') AS INTEGER)
WHERE status = 'pending'
  AND app_id IS NOT NULL
  AND id NOT IN (
    SELECT MAX(id) FROM app_access_requests
    WHERE status = 'pending' AND app_id IS NOT NULL
    GROUP BY requester_user_id, app_id
  );

UPDATE app_access_requests
SET status = 'rejected', resolved_at = CAST(strftime('%s', 'now') AS INTEGER)
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

ALTER TABLE app_access_requests DROP COLUMN owner_user_id;

UPDATE _schema_version SET version = 35;
