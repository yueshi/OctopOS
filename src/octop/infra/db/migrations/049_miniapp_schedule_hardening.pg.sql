-- Schema v49: MiniApp schedule hardening.
-- A public link has at most one snapshot-refresh job. Drop duplicates created
-- by the old read-then-create race before making the invariant explicit.

DELETE FROM light_app_schedules
WHERE action = 'snapshot_publication'
  AND id NOT IN (
    SELECT keep_id FROM (
      SELECT MIN(id) AS keep_id
      FROM light_app_schedules
      WHERE action = 'snapshot_publication'
      GROUP BY app_id
    ) AS keep
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_light_app_schedules_snapshot
  ON light_app_schedules(app_id)
  WHERE action = 'snapshot_publication';

UPDATE _schema_version SET version = 49;
