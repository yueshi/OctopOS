"""One-time WebView-to-IM link codes. SQL only.

The MiniApp page mints a short code with its app token; the user sends
``/link <code>`` in a private chat. Only the hash is persisted.
"""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, insert_returning_id, now_ts


@dataclass(frozen=True)
class ChannelLinkCodeRow:
    pk: int
    code_hash: str
    user_id: int
    app_id: str
    expires_at: int
    consumed_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> ChannelLinkCodeRow:
        consumed = row["consumed_at"]
        return cls(
            pk=int(row["id"]),
            code_hash=str(row["code_hash"]),
            user_id=int(row["user_id"]),
            app_id=str(row["app_id"]),
            expires_at=int(row["expires_at"]),
            consumed_at=int(consumed) if consumed is not None else None,
            created_at=int(row["created_at"]),
        )


class ChannelLinkCodeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        code_hash: str,
        user_id: int,
        app_id: str,
        expires_at: int,
    ) -> int:
        with self._db.transaction() as conn:
            return insert_returning_id(
                conn,
                "INSERT INTO channel_link_codes("
                "code_hash, user_id, app_id, expires_at, created_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (code_hash, user_id, app_id, expires_at, now_ts()),
            )

    def consume(self, code_hash: str, *, now: int) -> ChannelLinkCodeRow | None:
        """Consume one link code atomically and return the row."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "UPDATE channel_link_codes SET consumed_at = ? "
                "WHERE code_hash = ? AND consumed_at IS NULL AND expires_at > ? "
                "RETURNING *",
                (now, code_hash, now),
            ).fetchone()
        return ChannelLinkCodeRow.from_row(row) if row is not None else None

    def delete_expired(self, *, before: int) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM channel_link_codes WHERE expires_at <= ?",
                (before,),
            )
        return int(cursor.rowcount or 0)
