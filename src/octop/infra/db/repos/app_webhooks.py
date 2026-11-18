"""light_app_webhooks — owner-declared outbound webhook destinations."""

from __future__ import annotations

import json
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_short_id


@dataclass(frozen=True)
class LightAppWebhookRow:
    pk: int
    webhook_id: str
    app_id: str
    name: str
    url: str
    event_types: tuple[str, ...]
    source_id: str
    subscription_id: str
    secret_blob: bytes | None
    enabled: bool
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> LightAppWebhookRow:
        try:
            raw_types = json.loads(str(r["event_types_json"] or "[]"))
        except ValueError:
            raw_types = []
        event_types = tuple(str(item) for item in raw_types) if isinstance(raw_types, list) else ()
        return cls(
            pk=int(r["id"]),
            webhook_id=str(r["webhook_id"]),
            app_id=str(r["app_id"]),
            name=str(r["name"]),
            url=str(r["url"]),
            event_types=event_types,
            source_id=str(r["source_id"]),
            subscription_id=str(r["subscription_id"]),
            secret_blob=r["secret_blob"],
            enabled=bool(int(r["enabled"] or 0)),
            created_by=int(r["created_by"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class LightAppWebhookRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, webhook_id: str) -> LightAppWebhookRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_webhooks WHERE webhook_id = ?",
                (webhook_id,),
            ).fetchone()
        return LightAppWebhookRow.from_row(row) if row else None

    def list_for_app(self, app_id: str) -> list[LightAppWebhookRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_webhooks WHERE app_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        return map_rows(rows, LightAppWebhookRow)

    def create(
        self,
        *,
        app_id: str,
        name: str,
        url: str,
        event_types: list[str],
        source_id: str,
        subscription_id: str,
        secret_blob: bytes | None,
        created_by: int,
        enabled: bool = True,
        webhook_id: str | None = None,
    ) -> LightAppWebhookRow:
        webhook_id = webhook_id or f"wh_{new_short_id(14)}"
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO light_app_webhooks("
                "webhook_id, app_id, name, url, event_types_json, source_id, "
                "subscription_id, secret_blob, enabled, created_by, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    webhook_id,
                    app_id,
                    name,
                    url,
                    json.dumps(event_types, ensure_ascii=False),
                    source_id,
                    subscription_id,
                    secret_blob,
                    int(enabled),
                    created_by,
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM light_app_webhooks WHERE webhook_id = ?",
                (webhook_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - insert + select in one transaction
            raise RuntimeError("webhook insert returned no row")
        return LightAppWebhookRow.from_row(row)

    def update(
        self,
        webhook_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        event_types: list[str] | None = None,
        enabled: bool | None = None,
        secret_blob: bytes | None = None,
    ) -> LightAppWebhookRow | None:
        fields: list[str] = []
        params: list[object] = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if url is not None:
            fields.append("url = ?")
            params.append(url)
        if event_types is not None:
            fields.append("event_types_json = ?")
            params.append(json.dumps(event_types, ensure_ascii=False))
        if enabled is not None:
            fields.append("enabled = ?")
            params.append(int(enabled))
        if secret_blob is not None:
            fields.append("secret_blob = ?")
            params.append(secret_blob)
        if not fields:
            return self.get(webhook_id)
        fields.append("updated_at = ?")
        params.append(now_ts())
        params.append(webhook_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE light_app_webhooks SET {', '.join(fields)} WHERE webhook_id = ?",
                tuple(params),
            )
            row = conn.execute(
                "SELECT * FROM light_app_webhooks WHERE webhook_id = ?",
                (webhook_id,),
            ).fetchone()
        return LightAppWebhookRow.from_row(row) if row else None

    def delete(self, webhook_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_webhooks WHERE webhook_id = ?",
                (webhook_id,),
            )
        return int(cursor.rowcount or 0) > 0


__all__ = ["LightAppWebhookRepo", "LightAppWebhookRow"]
