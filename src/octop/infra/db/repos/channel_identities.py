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
    account_key: str
    sender_id: str
    user_id: int
    verified_at: int
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> ChannelIdentityRow:
        return cls(
            pk=int(row["id"]),
            channel_type=str(row["channel_type"]),
            account_key=str(row["account_key"] or ""),
            sender_id=str(row["sender_id"]),
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

    def get(
        self,
        channel_type: str,
        sender_id: str,
        *,
        account_key: str = "",
    ) -> ChannelIdentityRow | None:
        """Exact ``account_key`` binding first, then the legacy empty-key row."""
        clean_key = account_key.strip()
        with self._db.connect() as conn:
            if clean_key:
                row = conn.execute(
                    "SELECT * FROM channel_identities "
                    "WHERE channel_type = ? AND account_key = ? AND sender_id = ?",
                    (channel_type, clean_key, sender_id),
                ).fetchone()
                if row is not None:
                    return ChannelIdentityRow.from_row(row)
            row = conn.execute(
                "SELECT * FROM channel_identities "
                "WHERE channel_type = ? AND account_key = '' AND sender_id = ?",
                (channel_type, sender_id),
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
        sender_id: str,
        user_id: int,
        account_key: str = "",
    ) -> ChannelIdentityRow | None:
        """Create or refresh this sender's binding to *user_id*.

        A sender already bound to a different user is left untouched and
        ``None`` is returned, so an attacker cannot steal an existing binding.
        The legacy ``account_key=''`` row is kept for older bots; the exact
        key is inserted alongside it. A legacy row owned by another user
        blocks the exact binding (the sender is already claimed).
        """
        key = account_key.strip()
        ts = now_ts()
        with self._db.transaction() as conn:
            exact = conn.execute(
                "SELECT * FROM channel_identities "
                "WHERE channel_type = ? AND account_key = ? AND sender_id = ?",
                (channel_type, key, sender_id),
            ).fetchone()
            legacy = (
                conn.execute(
                    "SELECT * FROM channel_identities "
                    "WHERE channel_type = ? AND account_key = '' AND sender_id = ?",
                    (channel_type, sender_id),
                ).fetchone()
                if key
                else None
            )
            for row in (exact, legacy):
                if row is not None and int(row["user_id"]) != user_id:
                    return None
            if exact is not None:
                conn.execute(
                    "UPDATE channel_identities SET verified_at = ? WHERE id = ?",
                    (ts, int(exact["id"])),
                )
                target_id = int(exact["id"])
            else:
                # A concurrent /bind may win the unique key between the probe
                # above and this insert; DO NOTHING keeps the loser a clean
                # "taken" result instead of a 500.
                conn.execute(
                    "INSERT INTO channel_identities("
                    "channel_type, account_key, sender_id, user_id, verified_at, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(channel_type, account_key, sender_id) DO NOTHING",
                    (channel_type, key, sender_id, user_id, ts, ts),
                )
                row = conn.execute(
                    "SELECT * FROM channel_identities "
                    "WHERE channel_type = ? AND account_key = ? AND sender_id = ?",
                    (channel_type, key, sender_id),
                ).fetchone()
                if row is None or int(row["user_id"]) != user_id:
                    return None
                target_id = int(row["id"])
            row = conn.execute(
                "SELECT * FROM channel_identities WHERE id = ?", (target_id,)
            ).fetchone()
        return ChannelIdentityRow.from_row(row) if row is not None else None

    def delete(
        self,
        *,
        channel_type: str,
        sender_id: str,
        user_id: int,
        account_key: str = "",
    ) -> bool:
        key = account_key.strip()
        with self._db.transaction() as conn:
            if key:
                cursor = conn.execute(
                    "DELETE FROM channel_identities "
                    "WHERE channel_type = ? AND sender_id = ? AND user_id = ? "
                    "AND (account_key = ? OR account_key = '')",
                    (channel_type, sender_id, user_id, key),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM channel_identities "
                    "WHERE channel_type = ? AND sender_id = ? AND user_id = ? "
                    "AND account_key = ''",
                    (channel_type, sender_id, user_id),
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
