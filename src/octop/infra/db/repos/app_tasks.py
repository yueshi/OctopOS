"""light_app_tasks — MiniApp AI task state machine. SQL only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    insert_returning_id,
    is_unique_violation,
    map_rows,
    now_ts,
)
from octop.infra.utils.ulid import new_short_id

TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_TERMINAL = frozenset({TASK_SUCCEEDED, TASK_FAILED, TASK_CANCELLED})


def _json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class AppTaskRow:
    pk: int
    task_id: str
    app_id: str
    table_key: str
    row_id: str
    task_type: str
    status: str
    input: dict[str, Any]
    result: dict[str, Any] | None
    error: str
    actor_user_id: int
    agent_id: str
    model_ref: str
    thread_id: str
    idempotency_key: str
    attempts: int
    cancel_requested: bool
    lease_until: int | None
    created_at: int
    started_at: int | None
    finished_at: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> AppTaskRow:
        raw_result = r["result_json"]
        result = _json_object(raw_result) if raw_result else None
        return cls(
            pk=int(r["id"]),
            task_id=str(r["task_id"]),
            app_id=str(r["app_id"]),
            table_key=str(r["table_key"]),
            row_id=str(r["row_id"]),
            task_type=str(r["task_type"]),
            status=str(r["status"]),
            input=_json_object(r["input_json"]),
            result=result,
            error=str(r["error"] or ""),
            actor_user_id=int(r["actor_user_id"]),
            agent_id=str(r["agent_id"]),
            model_ref=str(r["model_ref"] or ""),
            thread_id=str(r["thread_id"] or ""),
            idempotency_key=str(r["idempotency_key"] or ""),
            attempts=int(r["attempts"] or 0),
            cancel_requested=bool(r["cancel_requested"]),
            lease_until=r["lease_until"],
            created_at=int(r["created_at"]),
            started_at=r["started_at"],
            finished_at=r["finished_at"],
        )


class AppTaskRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, task_id: str) -> AppTaskRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return AppTaskRow.from_row(row) if row else None

    def find_by_idempotency(self, app_id: str, key: str) -> AppTaskRow | None:
        if not key:
            return None
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_tasks WHERE app_id = ? AND idempotency_key = ?",
                (app_id, key),
            ).fetchone()
        return AppTaskRow.from_row(row) if row else None

    def list_for_app(
        self,
        app_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AppTaskRow]:
        sql = "SELECT * FROM light_app_tasks WHERE app_id = ?"
        params: list[Any] = [app_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 200)), max(0, int(offset))])
        with self._db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return map_rows(rows, AppTaskRow)

    def list_queued(self, *, limit: int = 10) -> list[AppTaskRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_tasks WHERE status = 'queued' "
                "AND cancel_requested = 0 ORDER BY created_at ASC, id ASC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return map_rows(rows, AppTaskRow)

    def create(
        self,
        *,
        app_id: str,
        table_key: str,
        row_id: str,
        task_type: str,
        actor_user_id: int,
        agent_id: str,
        input: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> AppTaskRow:
        task_id = f"tsk_{new_short_id(16)}"
        ts = now_ts()
        try:
            with self._db.transaction() as conn:
                insert_returning_id(
                    conn,
                    "INSERT INTO light_app_tasks("
                    "task_id, app_id, table_key, row_id, task_type, status, "
                    "input_json, actor_user_id, agent_id, idempotency_key, created_at"
                    ") VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                    (
                        task_id,
                        app_id,
                        table_key,
                        row_id,
                        task_type,
                        json.dumps(input or {}, ensure_ascii=False),
                        actor_user_id,
                        agent_id,
                        idempotency_key,
                        ts,
                    ),
                )
        except Exception as exc:
            if idempotency_key and is_unique_violation(exc):
                existing = self.find_by_idempotency(app_id, idempotency_key)
                if existing is not None:
                    return existing
            raise
        row = self.get(task_id)
        if row is None:
            raise RuntimeError(f"task insert failed: {app_id}/{task_id}")
        return row

    def recover_expired(self, *, now: int) -> int:
        """Requeue running tasks whose lease expired (runner crash/restart)."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_tasks SET status = 'queued', lease_until = NULL "
                "WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until <= ?",
                (int(now),),
            )
        return int(cursor.rowcount)

    def claim(self, task_id: str, *, now: int, lease_seconds: int) -> int | None:
        """Move one queued task to running; returns the lease expiry or None."""
        lease_until = int(now) + max(1, int(lease_seconds))
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_tasks SET status = 'running', "
                "started_at = COALESCE(started_at, ?), lease_until = ?, "
                "attempts = attempts + 1 "
                "WHERE task_id = ? AND status = 'queued' AND cancel_requested = 0",
                (int(now), lease_until, task_id),
            )
        return lease_until if int(cursor.rowcount) > 0 else None

    def heartbeat(
        self,
        task_id: str,
        *,
        lease_until: int,
        now: int,
        lease_seconds: int,
    ) -> int | None:
        renewed = int(now) + max(1, int(lease_seconds))
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_tasks SET lease_until = ? "
                "WHERE task_id = ? AND status = 'running' AND lease_until = ?",
                (renewed, task_id, lease_until),
            )
        return renewed if int(cursor.rowcount) > 0 else None

    def finish(
        self,
        task_id: str,
        *,
        lease_until: int,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
        finished_at: int | None = None,
    ) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_tasks SET status = ?, result_json = ?, error = ?, "
                "finished_at = ?, lease_until = NULL "
                "WHERE task_id = ? AND status = 'running' AND lease_until = ?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    int(finished_at if finished_at is not None else now_ts()),
                    task_id,
                    lease_until,
                ),
            )
        return int(cursor.rowcount) > 0

    def cancel(self, task_id: str) -> bool:
        """Cancel a queued task, or flag a running one for the runner to stop."""
        row = self.get(task_id)
        if row is None or row.status in TASK_TERMINAL:
            return False
        with self._db.transaction() as conn:
            if row.status == TASK_QUEUED:
                cursor = conn.execute(
                    "UPDATE light_app_tasks SET status = 'cancelled', "
                    "cancel_requested = 1, finished_at = ? "
                    "WHERE task_id = ? AND status = 'queued'",
                    (now_ts(), task_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE light_app_tasks SET cancel_requested = 1 "
                    "WHERE task_id = ? AND status = 'running'",
                    (task_id,),
                )
        return int(cursor.rowcount) > 0

    def count_active_for_app(self, app_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM light_app_tasks "
                "WHERE app_id = ? AND status IN ('queued', 'running')",
                (app_id,),
            ).fetchone()
        return int(row["n"] if row else 0)
