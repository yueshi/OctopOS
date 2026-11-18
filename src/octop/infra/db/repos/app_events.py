"""light_app_events — MiniApp event inbox. SQL only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    insert_returning_id,
    map_rows,
    now_ts,
)
from octop.infra.utils.ulid import new_short_id


@dataclass(frozen=True)
class AppEventRow:
    pk: int
    event_id: str
    app_id: str
    event_type: str
    source: str
    summary: str
    payload: dict[str, Any]
    created_at: int
    acknowledged_at: int | None

    @classmethod
    def from_row(cls, r: DbRow) -> AppEventRow:
        raw = str(r["payload_json"] or "{}")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        return cls(
            pk=int(r["id"]),
            event_id=str(r["event_id"]),
            app_id=str(r["app_id"]),
            event_type=str(r["event_type"]),
            source=str(r["source"] or ""),
            summary=str(r["summary"] or ""),
            payload=payload if isinstance(payload, dict) else {},
            created_at=int(r["created_at"]),
            acknowledged_at=r["acknowledged_at"],
        )


class AppEventRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        app_id: str,
        event_type: str,
        source: str = "",
        summary: str = "",
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> AppEventRow:
        event_id = event_id or f"evt_{new_short_id(14)}"
        ts = now_ts()
        with self._db.transaction() as conn:
            insert_returning_id(
                conn,
                "INSERT INTO light_app_events("
                "event_id, app_id, event_type, source, summary, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    app_id,
                    event_type,
                    source,
                    summary,
                    json.dumps(payload or {}, ensure_ascii=False),
                    ts,
                ),
            )
        row = self.get_for_app(app_id, event_id)
        if row is None:
            raise RuntimeError("event insert failed")
        return row

    def get_for_app(self, app_id: str, event_id: str) -> AppEventRow | None:
        """One app's inbox row; event ids are only unique within an app."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_events WHERE app_id = ? AND event_id = ?",
                (app_id, event_id),
            ).fetchone()
        return AppEventRow.from_row(row) if row else None

    def get(self, event_id: str, *, app_id: str | None = None) -> AppEventRow | None:
        if app_id is not None:
            return self.get_for_app(app_id, event_id)
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_events WHERE event_id = ? ORDER BY id LIMIT 1",
                (event_id,),
            ).fetchone()
        return AppEventRow.from_row(row) if row else None

    def list_after(
        self,
        app_id: str,
        *,
        since: int = 0,
        limit: int = 100,
    ) -> tuple[list[AppEventRow], int]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_events WHERE app_id = ? AND id > ? "
                "ORDER BY id ASC LIMIT ?",
                (app_id, since, max(1, min(limit, 500))),
            ).fetchall()
            latest = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS latest FROM light_app_events WHERE app_id = ?",
                (app_id,),
            ).fetchone()
        return map_rows(rows, AppEventRow), int(latest["latest"] if latest else 0)

    def ack(self, app_id: str, event_ids: list[str]) -> int:
        clean = list(dict.fromkeys(item for item in event_ids if item))[:200]
        if not clean:
            return 0
        placeholders = ", ".join("?" for _ in clean)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE light_app_events SET acknowledged_at = ? "
                f"WHERE app_id = ? AND event_id IN ({placeholders})",
                (now_ts(), app_id, *clean),
            )
        return int(cursor.rowcount)

    def prune(self, app_id: str, *, before: int, keep: int = 500) -> int:
        with self._db.transaction() as conn:
            old = conn.execute(
                "DELETE FROM light_app_events WHERE app_id = ? AND created_at < ?",
                (app_id, before),
            )
            extra = conn.execute(
                "DELETE FROM light_app_events WHERE app_id = ? AND id NOT IN ("
                "SELECT id FROM light_app_events WHERE app_id = ? "
                "ORDER BY id DESC LIMIT ?)",
                (app_id, app_id, max(1, keep)),
            )
        return int(old.rowcount) + int(extra.rowcount)


__all__ = ["AppEventRepo", "AppEventRow"]
