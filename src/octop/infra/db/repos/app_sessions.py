"""MiniApp guest sessions. SQL only."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts


@dataclass(frozen=True)
class AppSessionRow:
    session_id: str
    user_id: int
    scope: str
    refresh_token_hash: str
    created_at: int
    expires_at: int
    revoked_at: int | None
    last_seen_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> AppSessionRow:
        revoked = row["revoked_at"]
        return cls(
            session_id=str(row["session_id"]),
            user_id=int(row["user_id"]),
            scope=str(row["scope"]),
            refresh_token_hash=str(row["refresh_token_hash"]),
            created_at=int(row["created_at"]),
            expires_at=int(row["expires_at"]),
            revoked_at=int(revoked) if revoked is not None else None,
            last_seen_at=int(row["last_seen_at"] or 0),
        )


class AppSessionRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        session_id: str,
        user_id: int,
        refresh_token_hash: str,
        expires_at: int,
        scope: str = "guest",
    ) -> AppSessionRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_sessions("
                "session_id, user_id, scope, refresh_token_hash, created_at, "
                "expires_at, last_seen_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, user_id, scope, refresh_token_hash, ts, expires_at, ts),
            )
        row = self.get(session_id)
        if row is None:
            raise RuntimeError(f"app session insert failed: {session_id}")
        return row

    def get(self, session_id: str) -> AppSessionRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return AppSessionRow.from_row(row) if row else None

    def get_by_refresh_hash(self, refresh_token_hash: str) -> AppSessionRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_sessions WHERE refresh_token_hash = ?",
                (refresh_token_hash,),
            ).fetchone()
        return AppSessionRow.from_row(row) if row else None

    def list_for_user(self, user_id: int) -> list[AppSessionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM app_sessions WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                (user_id,),
            ).fetchall()
        return map_rows(rows, AppSessionRow)

    def rotate(
        self,
        old_hash: str,
        *,
        new_hash: str,
        expires_at: int,
        now: int,
    ) -> AppSessionRow | None:
        """Consume one refresh token and replace it; None when invalid/expired."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_sessions SET refresh_token_hash = ?, expires_at = ?, "
                "last_seen_at = ? WHERE refresh_token_hash = ? AND revoked_at IS NULL "
                "AND expires_at > ?",
                (new_hash, expires_at, now, old_hash, now),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM app_sessions WHERE refresh_token_hash = ?",
                (new_hash,),
            ).fetchone()
        return AppSessionRow.from_row(row) if row else None

    def touch(self, session_id: str, *, now: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE app_sessions SET last_seen_at = ? WHERE session_id = ?",
                (now, session_id),
            )

    def revoke(self, session_id: str, *, user_id: int, now: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_sessions SET revoked_at = ? "
                "WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL",
                (now, session_id, user_id),
            )
        return int(cursor.rowcount) > 0

    def revoke_all(self, user_id: int, *, now: int) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
        return int(cursor.rowcount)
