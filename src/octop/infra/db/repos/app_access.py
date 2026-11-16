"""app_access_requests — MiniApp access requests. SQL only."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

try:
    from psycopg import errors as pg_errors
except ImportError:  # pragma: no cover - optional PostgreSQL driver
    pg_errors = None  # type: ignore[assignment]

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    insert_returning_id,
    map_rows,
    now_ts,
)
from octop.infra.utils.ulid import new_short_id

APP_ACCESS_PENDING = "pending"
APP_ACCESS_APPROVED = "approved"
APP_ACCESS_REJECTED = "rejected"
APP_ACCESS_TERMINAL = (APP_ACCESS_APPROVED, APP_ACCESS_REJECTED)


def is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "unique" in str(exc).lower()
    return pg_errors is not None and isinstance(exc, pg_errors.UniqueViolation)


@dataclass(frozen=True)
class AppAccessRequestRow:
    request_id: str
    pk: int
    app_id: str | None
    agent_id: str
    requester_user_id: int
    app_owner_user_id: int | None
    agent_owner_user_id: int
    needed: str
    note: str
    status: str
    created_at: int
    resolved_at: int | None
    resolved_by: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> AppAccessRequestRow:
        return cls(
            request_id=str(r["request_id"]),
            pk=int(r["id"]),
            app_id=r["app_id"],
            agent_id=str(r["agent_id"]),
            requester_user_id=int(r["requester_user_id"]),
            app_owner_user_id=r["app_owner_user_id"],
            agent_owner_user_id=int(r["agent_owner_user_id"]),
            needed=str(r["needed"]),
            note=str(r["note"] or ""),
            status=str(r["status"] or APP_ACCESS_PENDING),
            created_at=int(r["created_at"]),
            resolved_at=r["resolved_at"],
            resolved_by=r["resolved_by"],
        )


class AppAccessRequestRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def allocate_id(self) -> str:
        for _ in range(16):
            request_id = f"req_{new_short_id(10)}"
            if self.get(request_id) is None:
                return request_id
        raise RuntimeError("failed to allocate unique access request id")

    def create(
        self,
        *,
        app_id: str | None,
        agent_id: str,
        requester_user_id: int,
        app_owner_user_id: int | None,
        agent_owner_user_id: int,
        needed: str,
        note: str = "",
    ) -> AppAccessRequestRow:
        request_id = self.allocate_id()
        with self._db.transaction() as conn:
            insert_returning_id(
                conn,
                "INSERT INTO app_access_requests("
                "request_id, app_id, agent_id, requester_user_id, "
                "app_owner_user_id, agent_owner_user_id, "
                "needed, note, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    app_id,
                    agent_id,
                    requester_user_id,
                    app_owner_user_id,
                    agent_owner_user_id,
                    needed,
                    note,
                    APP_ACCESS_PENDING,
                    now_ts(),
                ),
            )
        row = self.get(request_id)
        if row is None:
            raise RuntimeError("access request insert failed")
        return row

    def get(self, request_id: str) -> AppAccessRequestRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM app_access_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return AppAccessRequestRow.from_row(r) if r else None

    def find_pending(
        self,
        *,
        app_id: str | None,
        agent_id: str,
        requester_user_id: int,
    ) -> AppAccessRequestRow | None:
        with self._db.connect() as conn:
            if app_id is None:
                r = conn.execute(
                    "SELECT * FROM app_access_requests "
                    "WHERE app_id IS NULL AND agent_id = ? AND requester_user_id = ? "
                    "AND status = ? ORDER BY created_at DESC LIMIT 1",
                    (agent_id, requester_user_id, APP_ACCESS_PENDING),
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT * FROM app_access_requests "
                    "WHERE app_id = ? AND agent_id = ? AND requester_user_id = ? "
                    "AND status = ? ORDER BY created_at DESC LIMIT 1",
                    (app_id, agent_id, requester_user_id, APP_ACCESS_PENDING),
                ).fetchone()
        return AppAccessRequestRow.from_row(r) if r else None

    def list_for_owner(
        self,
        owner_user_id: int,
        *,
        app_id: str | None = None,
        status: str = APP_ACCESS_PENDING,
        limit: int = 100,
    ) -> list[AppAccessRequestRow]:
        sql = (
            "SELECT * FROM app_access_requests WHERE status = ? "
            "AND (app_owner_user_id = ? OR agent_owner_user_id = ?)"
        )
        params: list[object] = [status, owner_user_id, owner_user_id]
        if app_id is not None:
            sql += " AND app_id = ?"
            params.append(app_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return map_rows(rows, AppAccessRequestRow)

    def list_for_requester(
        self,
        requester_user_id: int,
        *,
        limit: int = 100,
    ) -> list[AppAccessRequestRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM app_access_requests "
                "WHERE requester_user_id = ? ORDER BY created_at DESC LIMIT ?",
                (requester_user_id, limit),
            ).fetchall()
        return map_rows(rows, AppAccessRequestRow)

    def list_pending_admin(self, *, limit: int = 100) -> list[AppAccessRequestRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM app_access_requests "
                "WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (APP_ACCESS_PENDING, limit),
            ).fetchall()
        return map_rows(rows, AppAccessRequestRow)

    def resolve(
        self,
        request_id: str,
        *,
        status: str,
        resolved_by: int,
    ) -> AppAccessRequestRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE app_access_requests "
                "SET status = ?, resolved_at = ?, resolved_by = ? "
                "WHERE request_id = ? AND status = ?",
                (status, now_ts(), resolved_by, request_id, APP_ACCESS_PENDING),
            )
        return self.get(request_id)

    def resolve_with_collaborators(
        self,
        request_id: str,
        *,
        status: str,
        resolved_by: int,
        app_id: str,
        collaborators: list[tuple[int, str]],
    ) -> AppAccessRequestRow | None:
        """Resolve the request and replace the app ACL in one transaction.

        The status update is conditional on ``pending``; if another caller won
        the race the ACL is left untouched and the existing row is returned.
        """
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_access_requests "
                "SET status = ?, resolved_at = ?, resolved_by = ? "
                "WHERE request_id = ? AND status = ?",
                (status, now_ts(), resolved_by, request_id, APP_ACCESS_PENDING),
            )
            if int(cursor.rowcount) > 0:
                ts = now_ts()
                conn.execute(
                    "DELETE FROM light_app_collaborators WHERE app_id = ?",
                    (app_id,),
                )
                for user_id, permission in collaborators:
                    conn.execute(
                        "INSERT INTO light_app_collaborators("
                        "app_id, user_id, permission, created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?)",
                        (app_id, user_id, permission, ts, ts),
                    )
                conn.execute(
                    "UPDATE light_apps SET updated_at = ? WHERE app_id = ?",
                    (ts, app_id),
                )
        return self.get(request_id)
