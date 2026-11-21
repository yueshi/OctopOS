"""app_setup_plans — durable MiniApp setup plans (SQL only).

The plan row is the single source of truth for a file-based MiniApp creation:
the dashboard card, the dry-run result, the created app and the import result
all reference one ``plan_id``. Status transitions use compare-and-set so a
double click or two tabs cannot create two apps or import twice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, now_ts

PLAN_DRAFT = "draft"
PLAN_CONFIRMED = "confirmed"
PLAN_DRY_RUN_READY = "dry_run_ready"
PLAN_DRY_RUN_FAILED = "dry_run_failed"
PLAN_IMPORTING = "importing"
PLAN_IMPORTED = "imported"
PLAN_IMPORT_PARTIAL = "import_partial"
PLAN_CANCELLED = "cancelled"
PLAN_EXPIRED = "expired"
PLAN_SUPERSEDED = "superseded"

ACTIVE_PLAN_STATUSES = (PLAN_DRAFT, PLAN_CONFIRMED, PLAN_DRY_RUN_READY, PLAN_IMPORTING)
CONFIRMABLE_PLAN_STATUSES = (PLAN_DRAFT, PLAN_DRY_RUN_FAILED)
EXECUTABLE_PLAN_STATUSES = (PLAN_DRY_RUN_READY, PLAN_IMPORT_PARTIAL)
CANCELLABLE_PLAN_STATUSES = (
    PLAN_DRAFT,
    PLAN_CONFIRMED,
    PLAN_DRY_RUN_READY,
    PLAN_DRY_RUN_FAILED,
)


@dataclass(frozen=True)
class AppSetupPlanRow:
    plan_id: str
    pk: int
    thread_id: str | None
    agent_id: str
    user_id: int
    source_path: str
    source_hash: str
    report: dict[str, Any]
    table: dict[str, Any]
    name: str
    status: str
    revision: int
    app_id: str | None
    dry_run: dict[str, Any]
    result: dict[str, Any]
    error: str
    expires_at: int | None
    created_at: int
    updated_at: int
    confirmed_at: int | None
    executed_at: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> AppSetupPlanRow:
        def load(key: str) -> dict[str, Any]:
            try:
                value = json.loads(str(r[key] or "{}"))
            except (TypeError, ValueError):
                return {}
            return value if isinstance(value, dict) else {}

        return cls(
            plan_id=str(r["plan_id"]),
            pk=int(r["id"]),
            thread_id=str(r["thread_id"]) if r["thread_id"] else None,
            agent_id=str(r["agent_id"]),
            user_id=int(r["user_id"]),
            source_path=str(r["source_path"]),
            source_hash=str(r["source_hash"] or ""),
            report=load("report_json"),
            table=load("table_json"),
            name=str(r["name"] or ""),
            status=str(r["status"] or PLAN_DRAFT),
            revision=int(r["revision"] or 1),
            app_id=str(r["app_id"]) if r["app_id"] else None,
            dry_run=load("dry_run_json"),
            result=load("result_json"),
            error=str(r["error"] or ""),
            expires_at=int(r["expires_at"]) if r["expires_at"] is not None else None,
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
            confirmed_at=int(r["confirmed_at"]) if r["confirmed_at"] is not None else None,
            executed_at=int(r["executed_at"]) if r["executed_at"] is not None else None,
        )


class AppSetupPlanRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        plan_id: str,
        thread_id: str | None,
        agent_id: str,
        user_id: int,
        source_path: str,
        source_hash: str,
        report: dict[str, Any],
        table: dict[str, Any],
        name: str,
        expires_at: int | None,
    ) -> AppSetupPlanRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_setup_plans("
                "plan_id, thread_id, agent_id, user_id, source_path, source_hash, "
                "report_json, table_json, name, status, revision, expires_at, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    plan_id,
                    thread_id,
                    agent_id,
                    user_id,
                    source_path,
                    source_hash,
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(table, ensure_ascii=False),
                    name,
                    PLAN_DRAFT,
                    expires_at,
                    ts,
                    ts,
                ),
            )
        row = self.get(plan_id)
        if row is None:
            raise RuntimeError(f"app_setup_plans insert failed: {plan_id}")
        return row

    def get(self, plan_id: str) -> AppSetupPlanRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM app_setup_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return AppSetupPlanRow.from_row(r) if r else None

    def get_active_for_thread(self, thread_id: str) -> AppSetupPlanRow | None:
        placeholders = ", ".join("?" * len(ACTIVE_PLAN_STATUSES))
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM app_setup_plans WHERE thread_id = ? "
                f"AND status IN ({placeholders}) "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (thread_id, *ACTIVE_PLAN_STATUSES),
            ).fetchone()
        return AppSetupPlanRow.from_row(r) if r else None

    @staticmethod
    def _filters(
        *,
        status: str | None,
        agent_id: str | None,
        user_id: int | None,
        app_id: str | None,
        thread_id: str | None,
        has_error: bool | None,
    ) -> tuple[str, list[object]]:
        conditions: list[str] = []
        params: list[object] = []
        for column, value in (
            ("status", status),
            ("agent_id", agent_id),
            ("user_id", user_id),
            ("app_id", app_id),
            ("thread_id", thread_id),
        ):
            if value is not None and value != "":
                conditions.append(f"{column} = ?")
                params.append(value)
        if has_error is True:
            conditions.append("error != ''")
        elif has_error is False:
            conditions.append("error = ''")
        return (" WHERE " + " AND ".join(conditions) if conditions else "", params)

    def list_plans(
        self,
        *,
        status: str | None = None,
        agent_id: str | None = None,
        user_id: int | None = None,
        app_id: str | None = None,
        thread_id: str | None = None,
        has_error: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AppSetupPlanRow]:
        where, params = self._filters(
            status=status,
            agent_id=agent_id,
            user_id=user_id,
            app_id=app_id,
            thread_id=thread_id,
            has_error=has_error,
        )
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM app_setup_plans{where} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, max(1, min(200, limit)), max(0, offset)),
            ).fetchall()
        return [AppSetupPlanRow.from_row(row) for row in rows]

    def count_plans(
        self,
        *,
        status: str | None = None,
        agent_id: str | None = None,
        user_id: int | None = None,
        app_id: str | None = None,
        thread_id: str | None = None,
        has_error: bool | None = None,
    ) -> int:
        where, params = self._filters(
            status=status,
            agent_id=agent_id,
            user_id=user_id,
            app_id=app_id,
            thread_id=thread_id,
            has_error=has_error,
        )
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM app_setup_plans{where}",
                params,
            ).fetchone()
        return int(row["n"] if row else 0)

    def update_draft(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        source_path: str,
        source_hash: str,
        report: dict[str, Any],
        table: dict[str, Any],
        name: str,
        expires_at: int | None,
    ) -> AppSetupPlanRow | None:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE app_setup_plans SET "
                "source_path = ?, source_hash = ?, report_json = ?, table_json = ?, "
                "name = ?, expires_at = ?, updated_at = ?, error = '', "
                "revision = revision + 1 "
                "WHERE plan_id = ? AND status = ? AND revision = ?",
                (
                    source_path,
                    source_hash,
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(table, ensure_ascii=False),
                    name,
                    expires_at,
                    now_ts(),
                    plan_id,
                    PLAN_DRAFT,
                    expected_revision,
                ),
            )
            if cur.rowcount != 1:
                return None
        return self.get(plan_id)

    def transition(
        self,
        plan_id: str,
        *,
        expected_status: str | tuple[str, ...],
        status: str,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> AppSetupPlanRow | None:
        """Compare-and-set a status transition; ``None`` means the caller lost."""
        statuses = (
            (expected_status,) if isinstance(expected_status, str) else tuple(expected_status)
        )
        clauses = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now_ts()]
        for column, value in fields.items():
            if column.endswith("_json") and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            clauses.append(f"{column} = ?")
            params.append(value)
        where = ["plan_id = ?", f"status IN ({', '.join('?' * len(statuses))})"]
        params.extend([plan_id, *statuses])
        if expected_revision is not None:
            where.append("revision = ?")
            params.append(expected_revision)
        with self._db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE app_setup_plans SET {', '.join(clauses)} WHERE {' AND '.join(where)}",
                tuple(params),
            )
            if cur.rowcount != 1:
                return None
        return self.get(plan_id)


__all__ = [
    "ACTIVE_PLAN_STATUSES",
    "CANCELLABLE_PLAN_STATUSES",
    "CONFIRMABLE_PLAN_STATUSES",
    "EXECUTABLE_PLAN_STATUSES",
    "PLAN_CANCELLED",
    "PLAN_CONFIRMED",
    "PLAN_DRAFT",
    "PLAN_DRY_RUN_FAILED",
    "PLAN_DRY_RUN_READY",
    "PLAN_EXPIRED",
    "PLAN_IMPORT_PARTIAL",
    "PLAN_IMPORTED",
    "PLAN_IMPORTING",
    "PLAN_SUPERSEDED",
    "AppSetupPlanRepo",
    "AppSetupPlanRow",
]
