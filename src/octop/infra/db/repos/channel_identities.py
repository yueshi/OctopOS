"""Verified IM identities and one-time bind codes. SQL only.

``channel_identities`` maps a platform subject (a DM peer) to an Octop user.
``channel_bind_codes`` stores the short-lived code the user sends to the bot
with ``/bind``; only its hash is persisted.
"""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, insert_returning_id, now_ts


@dataclass(frozen=True)
class ChannelIdentityRow:
    pk: int
    channel_type: str
    external_subject_id: str
    user_id: int
    verified_at: int
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> ChannelIdentityRow:
        return cls(
            pk=int(row["id"]),
            channel_type=str(row["channel_type"]),
            external_subject_id=str(row["external_subject_id"]),
            user_id=int(row["user_id"]),
            verified_at=int(row["verified_at"]),
            created_at=int(row["created_at"]),
        )


@dataclass(frozen=True)
class ChannelBindCodeRow:
    pk: int
    code_hash: str
    user_id: int
    expires_at: int
    consumed_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> ChannelBindCodeRow:
        consumed = row["consumed_at"]
        return cls(
            pk=int(row["id"]),
            code_hash=str(row["code_hash"]),
            user_id=int(row["user_id"]),
            expires_at=int(row["expires_at"]),
            consumed_at=int(consumed) if consumed is not None else None,
            created_at=int(row["created_at"]),
        )


class ChannelIdentityRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, channel_type: str, external_subject_id: str) -> ChannelIdentityRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_identities "
                "WHERE channel_type = ? AND external_subject_id = ?",
                (channel_type, external_subject_id),
            ).fetchone()
        return ChannelIdentityRow.from_row(row) if row is not None else None

    def list_for_user(self, user_id: int) -> list[ChannelIdentityRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM channel_identities WHERE user_id = ? ORDER BY verified_at DESC",
                (user_id,),
            ).fetchall()
        return [ChannelIdentityRow.from_row(row) for row in rows]

    def bind(
        self,
        *,
        channel_type: str,
        external_subject_id: str,
        user_id: int,
    ) -> ChannelIdentityRow | None:
        """Create or refresh this subject's binding to *user_id*.

        A subject already bound to a different user is left untouched and
        ``None`` is returned, so an attacker cannot steal an existing binding.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO channel_identities("
                "channel_type, external_subject_id, user_id, verified_at, created_at"
                ") VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(channel_type, external_subject_id) DO UPDATE SET "
                "user_id = excluded.user_id, verified_at = excluded.verified_at "
                "WHERE channel_identities.user_id = excluded.user_id",
                (channel_type, external_subject_id, user_id, ts, ts),
            )
            row = conn.execute(
                "SELECT * FROM channel_identities "
                "WHERE channel_type = ? AND external_subject_id = ?",
                (channel_type, external_subject_id),
            ).fetchone()
        if row is None or int(row["user_id"]) != user_id:
            return None
        return ChannelIdentityRow.from_row(row)

    def delete(self, *, channel_type: str, external_subject_id: str, user_id: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM channel_identities "
                "WHERE channel_type = ? AND external_subject_id = ? AND user_id = ?",
                (channel_type, external_subject_id, user_id),
            )
        return bool(cursor.rowcount)


class ChannelBindCodeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(self, *, code_hash: str, user_id: int, expires_at: int) -> int:
        with self._db.transaction() as conn:
            return insert_returning_id(
                conn,
                "INSERT INTO channel_bind_codes(code_hash, user_id, expires_at, created_at) "
                "VALUES (?, ?, ?, ?)",
                (code_hash, user_id, expires_at, now_ts()),
            )

    def consume(self, code_hash: str, *, now: int) -> ChannelBindCodeRow | None:
        """Consume one bind code atomically and return the row."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "UPDATE channel_bind_codes SET consumed_at = ? "
                "WHERE code_hash = ? AND consumed_at IS NULL AND expires_at > ? "
                "RETURNING *",
                (now, code_hash, now),
            ).fetchone()
        return ChannelBindCodeRow.from_row(row) if row is not None else None

    def delete_expired(self, *, before: int) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM channel_bind_codes WHERE expires_at <= ?",
                (before,),
            )
        return int(cursor.rowcount or 0)
