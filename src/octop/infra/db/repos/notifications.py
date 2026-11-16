"""notifications — durable per-user dashboard notification inbox. SQL only."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

try:
    from psycopg import errors as pg_errors
except ImportError:  # pragma: no cover - optional PostgreSQL driver
    pg_errors = None  # type: ignore[assignment]

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, insert_returning_id, map_rows, now_ts
from octop.infra.utils.ulid import new_short_id

DEFAULT_NOTIFICATION_LIMIT = 50
MAX_NOTIFICATIONS_PER_USER = 200


def _is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "unique" in str(exc).lower()
    return pg_errors is not None and isinstance(exc, pg_errors.UniqueViolation)


@dataclass(frozen=True)
class NotificationRow:
    notification_id: str
    pk: int
    user_id: int
    kind: str
    title: str
    body: str
    path: str | None
    dedupe_key: str | None
    read_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> NotificationRow:
        return cls(
            notification_id=str(r["notification_id"]),
            pk=int(r["id"]),
            user_id=int(r["user_id"]),
            kind=str(r["kind"]),
            title=str(r["title"]),
            body=str(r["body"] or ""),
            path=r["path"],
            dedupe_key=r["dedupe_key"],
            read_at=r["read_at"],
            created_at=int(r["created_at"]),
        )

    @property
    def read(self) -> bool:
        return self.read_at is not None


class NotificationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def allocate_id(self) -> str:
        for _ in range(16):
            notification_id = f"ntf_{new_short_id(10)}"
            with self._db.connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM notifications WHERE notification_id = ?",
                    (notification_id,),
                ).fetchone()
            if row is None:
                return notification_id
        raise RuntimeError("failed to allocate unique notification id")

    def get(self, user_id: int, notification_id: str) -> NotificationRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? AND notification_id = ?",
                (user_id, notification_id),
            ).fetchone()
        return NotificationRow.from_row(row) if row else None

    def get_by_dedupe(self, user_id: int, dedupe_key: str) -> NotificationRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? AND dedupe_key = ?",
                (user_id, dedupe_key),
            ).fetchone()
        return NotificationRow.from_row(row) if row else None

    def create(
        self,
        *,
        user_id: int,
        kind: str,
        title: str,
        body: str = "",
        path: str | None = None,
        dedupe_key: str | None = None,
        created_at: int | None = None,
    ) -> NotificationRow:
        """Insert a notification, returning the existing row for a duplicate key.

        ``(user_id, dedupe_key)`` is unique. The pre-check keeps the common path
        cheap; the unique violation fallback covers two processes racing.
        """
        if dedupe_key:
            existing = self.get_by_dedupe(user_id, dedupe_key)
            if existing is not None:
                return existing
        notification_id = self.allocate_id()
        try:
            with self._db.transaction() as conn:
                insert_returning_id(
                    conn,
                    "INSERT INTO notifications("
                    "notification_id, user_id, kind, title, body, path, dedupe_key, "
                    "read_at, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    (
                        notification_id,
                        user_id,
                        kind,
                        title,
                        body,
                        path,
                        dedupe_key,
                        created_at if created_at is not None else now_ts(),
                    ),
                )
        except Exception as exc:
            if dedupe_key and _is_unique_violation(exc):
                existing = self.get_by_dedupe(user_id, dedupe_key)
                if existing is not None:
                    return existing
            raise
        self.prune(user_id)
        row = self.get(user_id, notification_id)
        if row is None:
            raise RuntimeError("notification insert failed")
        return row

    def list_for_user(
        self,
        user_id: int,
        *,
        limit: int = DEFAULT_NOTIFICATION_LIMIT,
        unread_only: bool = False,
    ) -> list[NotificationRow]:
        sql = "SELECT * FROM notifications WHERE user_id = ?"
        params: list[object] = [user_id]
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, NotificationRow)

    def unread_count(self, user_id: int) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL",
                (user_id,),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def mark_read(self, user_id: int, notification_id: str) -> NotificationRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE notifications SET read_at = ? "
                "WHERE user_id = ? AND notification_id = ? AND read_at IS NULL",
                (now_ts(), user_id, notification_id),
            )
        return self.get(user_id, notification_id)

    def mark_all_read(self, user_id: int) -> int:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                (now_ts(), user_id),
            )
        return int(cur.rowcount or 0)

    def clear(self, user_id: int) -> int:
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
        return int(cur.rowcount or 0)

    def prune(self, user_id: int, keep: int = MAX_NOTIFICATIONS_PER_USER) -> int:
        """Drop the oldest rows beyond *keep* for one user."""
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM notifications WHERE user_id = ? AND id NOT IN ("
                "SELECT id FROM notifications WHERE user_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?"
                ")",
                (user_id, user_id, max(1, keep)),
            )
        return int(cur.rowcount or 0)
