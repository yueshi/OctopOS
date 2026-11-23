"""light_app_service_operations — thin idempotency ledger for invoke.

This table records one proxied write call. It is deliberately not a task
runtime: no leases, no execution loop, no second task UI. Provider jobs and
user-visible progress live in the extension service / ``runs`` projection.
"""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    insert_returning_id,
    is_unique_violation,
    map_rows,
    now_ts,
)
from octop.infra.utils.ulid import new_short_id

OP_PENDING_CONFIRMATION = "pending_confirmation"
OP_RUNNING = "running"
OP_SUCCEEDED = "succeeded"
OP_FAILED = "failed"
OP_CANCELLED = "cancelled"
OP_EXPIRED = "expired"
OP_UNKNOWN = "submission_unknown"

OP_STATUSES: tuple[str, ...] = (
    OP_PENDING_CONFIRMATION,
    OP_RUNNING,
    OP_SUCCEEDED,
    OP_FAILED,
    OP_CANCELLED,
    OP_EXPIRED,
    OP_UNKNOWN,
)


@dataclass(frozen=True)
class LightAppServiceOperationRow:
    op_id: str
    pk: int
    service_id: str
    app_id: str
    tool: str
    args_hash: str
    idempotency_key: str
    status: str
    confirmation_token_hash: str
    confirmation_expires_at: int | None
    result_json: str
    error_code: str
    error_message: str
    actor_user_id: int
    created_at: int
    updated_at: int
    expires_at: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> LightAppServiceOperationRow:
        expires = r["confirmation_expires_at"]
        ttl = r["expires_at"]
        return cls(
            op_id=str(r["op_id"]),
            pk=int(r["id"]),
            service_id=str(r["service_id"]),
            app_id=str(r["app_id"]),
            tool=str(r["tool"]),
            args_hash=str(r["args_hash"]),
            idempotency_key=str(r["idempotency_key"]),
            status=str(r["status"]),
            confirmation_token_hash=str(r["confirmation_token_hash"] or ""),
            confirmation_expires_at=int(expires) if expires is not None else None,
            result_json=str(r["result_json"] or ""),
            error_code=str(r["error_code"] or ""),
            error_message=str(r["error_message"] or ""),
            actor_user_id=int(r["actor_user_id"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
            expires_at=int(ttl) if ttl is not None else None,
        )


class LightAppServiceOperationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def allocate_op_id(self) -> str:
        for _ in range(16):
            op_id = f"op_{new_short_id(16)}"
            if self.get(op_id) is None:
                return op_id
        raise RuntimeError("failed to allocate unique operation id")

    def get(self, op_id: str) -> LightAppServiceOperationRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_service_operations WHERE op_id = ?",
                (op_id,),
            ).fetchone()
        return LightAppServiceOperationRow.from_row(row) if row else None

    def get_by_key(
        self,
        *,
        service_id: str,
        tool: str,
        actor_user_id: int,
        idempotency_key: str,
    ) -> LightAppServiceOperationRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_service_operations "
                "WHERE service_id = ? AND tool = ? AND actor_user_id = ? "
                "AND idempotency_key = ?",
                (service_id, tool, actor_user_id, idempotency_key),
            ).fetchone()
        return LightAppServiceOperationRow.from_row(row) if row else None

    def create(
        self,
        *,
        service_id: str,
        app_id: str,
        tool: str,
        args_hash: str,
        idempotency_key: str,
        status: str,
        actor_user_id: int,
        confirmation_token_hash: str = "",
        confirmation_expires_at: int | None = None,
        expires_at: int | None = None,
    ) -> LightAppServiceOperationRow:
        ts = now_ts()
        for _ in range(16):
            op_id = self.allocate_op_id()
            try:
                with self._db.transaction() as conn:
                    insert_returning_id(
                        conn,
                        "INSERT INTO light_app_service_operations("
                        "op_id, service_id, app_id, tool, args_hash, idempotency_key, "
                        "status, confirmation_token_hash, confirmation_expires_at, "
                        "actor_user_id, created_at, updated_at, expires_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            op_id,
                            service_id,
                            app_id,
                            tool,
                            args_hash,
                            idempotency_key,
                            status,
                            confirmation_token_hash,
                            confirmation_expires_at,
                            actor_user_id,
                            ts,
                            ts,
                            expires_at,
                        ),
                    )
            except Exception as exc:
                if idempotency_key and is_unique_violation(exc):
                    existing = self.get_by_key(
                        service_id=service_id,
                        tool=tool,
                        actor_user_id=actor_user_id,
                        idempotency_key=idempotency_key,
                    )
                    if existing is not None:
                        return existing
                raise
            row = self.get(op_id)
            if row is None:
                raise RuntimeError(f"operation insert failed: {op_id}")
            return row
        raise RuntimeError("failed to allocate unique operation id")

    def refresh_confirmation(
        self,
        op_id: str,
        *,
        token_hash: str,
        expires_at: int,
    ) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_service_operations SET "
                "confirmation_token_hash = ?, confirmation_expires_at = ?, updated_at = ? "
                "WHERE op_id = ? AND status = ?",
                (
                    token_hash,
                    expires_at,
                    now_ts(),
                    op_id,
                    OP_PENDING_CONFIRMATION,
                ),
            )
        return int(cursor.rowcount) > 0

    def confirm(self, op_id: str, *, token_hash: str, now: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_service_operations SET "
                "status = ?, confirmation_token_hash = '', updated_at = ? "
                "WHERE op_id = ? AND status = ? AND confirmation_token_hash = ? "
                "AND confirmation_expires_at IS NOT NULL "
                "AND confirmation_expires_at >= ?",
                (
                    OP_RUNNING,
                    now,
                    op_id,
                    OP_PENDING_CONFIRMATION,
                    token_hash,
                    now,
                ),
            )
        return int(cursor.rowcount) > 0

    def finish(
        self,
        op_id: str,
        *,
        status: str,
        result_json: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> bool:
        if status not in (OP_SUCCEEDED, OP_FAILED, OP_CANCELLED, OP_UNKNOWN):
            raise ValueError(f"invalid terminal operation status: {status}")
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_service_operations SET "
                "status = ?, result_json = ?, error_code = ?, error_message = ?, "
                "updated_at = ? WHERE op_id = ? AND status = ?",
                (
                    status,
                    result_json,
                    error_code,
                    error_message,
                    now_ts(),
                    op_id,
                    OP_RUNNING,
                ),
            )
        return int(cursor.rowcount) > 0

    def resolve_unknown(
        self,
        op_id: str,
        *,
        status: str,
        result_json: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> bool:
        if status not in (OP_SUCCEEDED, OP_FAILED, OP_CANCELLED):
            raise ValueError(f"invalid resolved status: {status}")
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_service_operations SET "
                "status = ?, result_json = ?, error_code = ?, error_message = ?, "
                "updated_at = ? WHERE op_id = ? AND status = ?",
                (
                    status,
                    result_json,
                    error_code,
                    error_message,
                    now_ts(),
                    op_id,
                    OP_UNKNOWN,
                ),
            )
        return int(cursor.rowcount) > 0

    def expire(self, op_id: str, *, now: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_service_operations SET status = ?, updated_at = ? "
                "WHERE op_id = ? AND status = ? AND confirmation_expires_at IS NOT NULL "
                "AND confirmation_expires_at < ?",
                (OP_EXPIRED, now, op_id, OP_PENDING_CONFIRMATION, now),
            )
        return int(cursor.rowcount) > 0

    def delete_expired(self, *, now: int) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_service_operations "
                "WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
        return int(cursor.rowcount)

    def list_for_app(self, app_id: str, *, limit: int = 50) -> list[LightAppServiceOperationRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_service_operations WHERE app_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (app_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return map_rows(rows, LightAppServiceOperationRow)


__all__ = [
    "LightAppServiceOperationRepo",
    "LightAppServiceOperationRow",
    "OP_CANCELLED",
    "OP_EXPIRED",
    "OP_FAILED",
    "OP_PENDING_CONFIRMATION",
    "OP_RUNNING",
    "OP_STATUSES",
    "OP_SUCCEEDED",
    "OP_UNKNOWN",
]
