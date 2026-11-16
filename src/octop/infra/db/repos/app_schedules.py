"""light_app_schedules — application-level interval jobs. SQL only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    insert_returning_id,
    map_rows,
    now_ts,
)
from octop.infra.utils.ulid import new_short_id

JITTER_MAX_SECONDS = 300
JITTER_MIN_SECONDS = 30
"""Deterministic re-arm offset bounds (spread a due herd across scans)."""


def schedule_jitter(schedule_id: str, interval_seconds: int) -> int:
    """Stable offset so schedules do not fire in the same scheduler tick."""
    interval = max(1, int(interval_seconds))
    window = min(JITTER_MAX_SECONDS, max(JITTER_MIN_SECONDS, interval // 10))
    digest = hashlib.sha256(schedule_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % (window + 1)


@dataclass(frozen=True)
class LightAppScheduleRow:
    pk: int
    schedule_id: str
    app_id: str
    action: str
    target: dict[str, Any]
    interval_seconds: int
    next_run_at: int
    lease_until: int | None
    last_run_at: int | None
    last_status: str
    last_error: str
    failure_count: int
    enabled: bool
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> LightAppScheduleRow:
        raw = str(r["target_json"] or "{}")
        try:
            target = json.loads(raw)
        except ValueError:
            target = {}
        return cls(
            pk=int(r["id"]),
            schedule_id=str(r["schedule_id"]),
            app_id=str(r["app_id"]),
            action=str(r["action"]),
            target=target if isinstance(target, dict) else {},
            interval_seconds=int(r["interval_seconds"]),
            next_run_at=int(r["next_run_at"]),
            lease_until=r["lease_until"],
            last_run_at=r["last_run_at"],
            last_status=str(r["last_status"] or ""),
            last_error=str(r["last_error"] or ""),
            failure_count=int(r["failure_count"]),
            enabled=bool(r["enabled"]),
            created_by=int(r["created_by"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class LightAppScheduleRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, schedule_id: str) -> LightAppScheduleRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_schedules WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
        return LightAppScheduleRow.from_row(row) if row else None

    def list_for_app(self, app_id: str) -> list[LightAppScheduleRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_schedules WHERE app_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        return map_rows(rows, LightAppScheduleRow)

    def count_for_app(self, app_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM light_app_schedules WHERE app_id = ?",
                (app_id,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def list_due(self, now: int, *, limit: int = 100) -> list[LightAppScheduleRow]:
        """Enabled schedules whose next run is due and lease is free/expired."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT s.* FROM light_app_schedules s "
                "JOIN light_apps a ON a.app_id = s.app_id "
                "WHERE s.enabled = 1 AND s.next_run_at <= ? "
                "AND (s.lease_until IS NULL OR s.lease_until <= ?) "
                "ORDER BY s.next_run_at ASC, s.id ASC LIMIT ?",
                (now, now, max(1, limit)),
            ).fetchall()
        return map_rows(rows, LightAppScheduleRow)

    def create(
        self,
        *,
        app_id: str,
        action: str,
        interval_seconds: int,
        created_by: int,
        target: dict[str, Any] | None = None,
        enabled: bool = True,
        next_run_at: int | None = None,
        schedule_id: str | None = None,
    ) -> LightAppScheduleRow:
        schedule_id = schedule_id or f"sch_{new_short_id(16)}"
        ts = now_ts()
        first_run = (
            ts + schedule_jitter(schedule_id, interval_seconds)
            if next_run_at is None
            else int(next_run_at)
        )
        with self._db.transaction() as conn:
            insert_returning_id(
                conn,
                "INSERT INTO light_app_schedules("
                "schedule_id, app_id, action, target_json, interval_seconds, "
                "next_run_at, enabled, created_by, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    schedule_id,
                    app_id,
                    action,
                    json.dumps(target or {}, ensure_ascii=False),
                    int(interval_seconds),
                    first_run,
                    1 if enabled else 0,
                    int(created_by),
                    ts,
                    ts,
                ),
            )
        row = self.get(schedule_id)
        if row is None:
            raise RuntimeError(f"schedule insert failed: {app_id}/{schedule_id}")
        return row

    def upsert_source(
        self,
        *,
        app_id: str,
        source_id: str,
        interval_seconds: int,
        created_by: int,
        enabled: bool = True,
    ) -> LightAppScheduleRow:
        """Mirror one source's interval as its deterministic ``sync_source`` job."""
        schedule_id = f"sch_src_{source_id}"
        target = {"source_id": source_id}
        existing = self.get(schedule_id)
        if existing is None:
            return self.create(
                app_id=app_id,
                action="sync_source",
                interval_seconds=interval_seconds,
                created_by=created_by,
                target=target,
                enabled=enabled,
                schedule_id=schedule_id,
            )
        now = now_ts()
        interval_changed = existing.interval_seconds != int(interval_seconds)
        reenabled = not existing.enabled and enabled
        reset_state = interval_changed or reenabled
        next_run_at = existing.next_run_at
        if reset_state:
            next_run_at = (
                now + int(interval_seconds) + schedule_jitter(schedule_id, interval_seconds)
            )
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE light_app_schedules SET action = ?, target_json = ?, "
                "interval_seconds = ?, next_run_at = ?, enabled = ?, "
                "failure_count = ?, last_status = ?, last_error = ?, updated_at = ? "
                "WHERE schedule_id = ?",
                (
                    "sync_source",
                    json.dumps(target, ensure_ascii=False),
                    int(interval_seconds),
                    next_run_at,
                    1 if enabled else 0,
                    0 if reset_state else existing.failure_count,
                    "" if reset_state else existing.last_status,
                    "" if reset_state else existing.last_error,
                    now,
                    schedule_id,
                ),
            )
        row = self.get(schedule_id)
        if row is None:
            raise RuntimeError(f"schedule update failed: {app_id}/{schedule_id}")
        return row

    def update(
        self,
        schedule_id: str,
        *,
        interval_seconds: int,
        enabled: bool,
        next_run_at: int | None = None,
    ) -> LightAppScheduleRow | None:
        existing = self.get(schedule_id)
        if existing is None:
            return None
        now = now_ts()
        interval_changed = existing.interval_seconds != int(interval_seconds)
        reenabled = not existing.enabled and enabled
        reset_state = interval_changed or reenabled
        if next_run_at is None:
            next_run_at = existing.next_run_at
            if reset_state:
                next_run_at = (
                    now + int(interval_seconds) + schedule_jitter(schedule_id, interval_seconds)
                )
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE light_app_schedules SET interval_seconds = ?, "
                "next_run_at = ?, enabled = ?, failure_count = ?, "
                "last_status = ?, last_error = ?, updated_at = ? WHERE schedule_id = ?",
                (
                    int(interval_seconds),
                    int(next_run_at),
                    1 if enabled else 0,
                    0 if reset_state else existing.failure_count,
                    "" if reset_state else existing.last_status,
                    "" if reset_state else existing.last_error,
                    now,
                    schedule_id,
                ),
            )
        return self.get(schedule_id)

    def claim(
        self,
        schedule_id: str,
        *,
        now: int,
        lease_seconds: int,
        due_only: bool = True,
    ) -> int | None:
        """Take the lease for one run; returns its expiry or None if not claimable."""
        lease_until = now + max(1, lease_seconds)
        sql = (
            "UPDATE light_app_schedules SET lease_until = ?, updated_at = ? "
            "WHERE schedule_id = ? AND enabled = 1 "
            "AND (lease_until IS NULL OR lease_until <= ?)"
        )
        params: list[Any] = [lease_until, now, schedule_id, now]
        if due_only:
            sql += " AND next_run_at <= ?"
            params.append(now)
        with self._db.transaction() as conn:
            cursor = conn.execute(sql, tuple(params))
        return lease_until if int(cursor.rowcount) > 0 else None

    def heartbeat(
        self,
        schedule_id: str,
        *,
        lease_until: int,
        now: int,
        lease_seconds: int,
    ) -> int | None:
        """Extend a held lease; None means the lease was lost."""
        renewed = now + max(1, lease_seconds)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_schedules SET lease_until = ?, updated_at = ? "
                "WHERE schedule_id = ? AND lease_until = ?",
                (renewed, now, schedule_id, lease_until),
            )
        return renewed if int(cursor.rowcount) > 0 else None

    def release(self, schedule_id: str, *, lease_until: int) -> bool:
        """Give the lease back without changing run state."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_schedules SET lease_until = NULL, updated_at = ? "
                "WHERE schedule_id = ? AND lease_until = ?",
                (now_ts(), schedule_id, lease_until),
            )
        return int(cursor.rowcount) > 0

    def finish(
        self,
        schedule_id: str,
        *,
        lease_until: int,
        run_at: int,
        status: str,
        next_run_at: int,
        failure_count: int,
        error: str = "",
    ) -> bool:
        """Record a finished run and release the matching lease."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_schedules SET last_run_at = ?, last_status = ?, "
                "last_error = ?, next_run_at = ?, failure_count = ?, "
                "lease_until = NULL, updated_at = ? "
                "WHERE schedule_id = ? AND lease_until = ?",
                (
                    run_at,
                    status,
                    error,
                    int(next_run_at),
                    int(failure_count),
                    now_ts(),
                    schedule_id,
                    lease_until,
                ),
            )
        return int(cursor.rowcount) > 0

    def record_run(
        self,
        schedule_id: str,
        *,
        run_at: int,
        status: str,
        next_run_at: int,
        failure_count: int,
        error: str = "",
    ) -> bool:
        """Record a manual run without touching the lease."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_schedules SET last_run_at = ?, last_status = ?, "
                "last_error = ?, next_run_at = ?, failure_count = ?, updated_at = ? "
                "WHERE schedule_id = ?",
                (
                    run_at,
                    status,
                    error,
                    int(next_run_at),
                    int(failure_count),
                    now_ts(),
                    schedule_id,
                ),
            )
        return int(cursor.rowcount) > 0

    def delete(self, app_id: str, schedule_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_schedules WHERE app_id = ? AND schedule_id = ?",
                (app_id, schedule_id),
            )
        return int(cursor.rowcount) > 0

    def delete_by_source(self, source_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_schedules WHERE schedule_id = ?",
                (f"sch_src_{source_id}",),
            )
        return int(cursor.rowcount) > 0


__all__ = ["LightAppScheduleRepo", "LightAppScheduleRow"]
