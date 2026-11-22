"""One-time MiniApp access codes. SQL only."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, insert_returning_id, now_ts


@dataclass(frozen=True)
class AppAccessCodeRow:
    pk: int
    code_hash: str
    app_id: str
    user_id: int
    permission: str
    channel_type: str
    channel_subject_id: str
    account_key: str
    sender_id: str
    expires_at: int
    consumed_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> AppAccessCodeRow:
        consumed = row["consumed_at"]
        return cls(
            pk=int(row["id"]),
            code_hash=str(row["code_hash"]),
            app_id=str(row["app_id"]),
            user_id=int(row["user_id"]),
            permission=str(row["permission"]),
            channel_type=str(row["channel_type"] or ""),
            channel_subject_id=str(row["channel_subject_id"] or ""),
            account_key=str(row["account_key"] or ""),
            sender_id=str(row["sender_id"] or ""),
            expires_at=int(row["expires_at"]),
            consumed_at=int(consumed) if consumed is not None else None,
            created_at=int(row["created_at"]),
        )


class AppAccessCodeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        code_hash: str,
        app_id: str,
        user_id: int,
        permission: str,
        channel_type: str,
        channel_subject_id: str,
        expires_at: int,
        account_key: str = "",
        sender_id: str = "",
    ) -> int:
        ts = now_ts()
        with self._db.transaction() as conn:
            return insert_returning_id(
                conn,
                "INSERT INTO app_access_codes("
                "code_hash, app_id, user_id, permission, channel_type, "
                "channel_subject_id, account_key, sender_id, expires_at, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    code_hash,
                    app_id,
                    user_id,
                    permission,
                    channel_type,
                    channel_subject_id,
                    account_key,
                    sender_id,
                    expires_at,
                    ts,
                ),
            )

    def get_by_hash(self, code_hash: str) -> AppAccessCodeRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_access_codes WHERE code_hash = ?",
                (code_hash,),
            ).fetchone()
        return AppAccessCodeRow.from_row(row) if row is not None else None

    def consume(self, code_hash: str, *, now: int) -> AppAccessCodeRow | None:
        """Consume one access code atomically and return the row."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "UPDATE app_access_codes SET consumed_at = ? "
                "WHERE code_hash = ? AND consumed_at IS NULL AND expires_at > ? "
                "RETURNING *",
                (now, code_hash, now),
            ).fetchone()
        return AppAccessCodeRow.from_row(row) if row is not None else None

    def delete_expired(self, *, before: int) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM app_access_codes WHERE expires_at <= ?",
                (before,),
            )
        return int(cursor.rowcount or 0)
