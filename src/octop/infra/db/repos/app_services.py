"""light_app_services / light_app_service_cache — SQL only."""

from __future__ import annotations

import json
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    DbRow,
    insert_returning_id,
    map_rows,
    now_ts,
)
from octop.infra.utils.ulid import new_short_id


def _json_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def _json_dict(raw: object) -> dict[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


@dataclass(frozen=True)
class LightAppServiceRow:
    service_id: str
    pk: int
    app_id: str
    name: str
    provider: str
    connector_instance_id: str
    allowed_tools: tuple[str, ...]
    allowed_url_prefixes: tuple[str, ...]
    request_headers: dict[str, str]
    cache_ttl_seconds: int
    max_calls_per_minute: int
    public_live: bool
    enabled: bool
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> LightAppServiceRow:
        return cls(
            service_id=str(r["service_id"]),
            pk=int(r["id"]),
            app_id=str(r["app_id"]),
            name=str(r["name"]),
            provider=str(r["provider"]),
            connector_instance_id=str(r["connector_instance_id"]),
            allowed_tools=_json_list(r["allowed_tools_json"]),
            allowed_url_prefixes=_json_list(r["allowed_url_prefixes_json"]),
            request_headers=_json_dict(r["request_headers_json"]),
            cache_ttl_seconds=int(r["cache_ttl_seconds"] or 0),
            max_calls_per_minute=int(r["max_calls_per_minute"] or 0),
            public_live=bool(int(r["public_live"] or 0)),
            enabled=bool(int(r["enabled"] or 0)),
            created_by=int(r["created_by"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class LightAppServiceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def allocate_id(self) -> str:
        for _ in range(16):
            service_id = f"svc_{new_short_id(12)}"
            if self.get(service_id) is None:
                return service_id
        raise RuntimeError("failed to allocate unique service id")

    def get(self, service_id: str) -> LightAppServiceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_services WHERE service_id = ?",
                (service_id,),
            ).fetchone()
        return LightAppServiceRow.from_row(row) if row else None

    def get_by_name(self, app_id: str, name: str) -> LightAppServiceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_services WHERE app_id = ? AND name = ?",
                (app_id, name),
            ).fetchone()
        return LightAppServiceRow.from_row(row) if row else None

    def list_for_app(self, app_id: str) -> list[LightAppServiceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_services WHERE app_id = ? ORDER BY name",
                (app_id,),
            ).fetchall()
        return map_rows(rows, LightAppServiceRow)

    def create(
        self,
        *,
        app_id: str,
        name: str,
        provider: str,
        connector_instance_id: str,
        allowed_tools: list[str],
        allowed_url_prefixes: list[str],
        request_headers: dict[str, str],
        cache_ttl_seconds: int,
        max_calls_per_minute: int,
        public_live: bool,
        enabled: bool,
        created_by: int,
    ) -> LightAppServiceRow:
        service_id = self.allocate_id()
        ts = now_ts()
        with self._db.transaction() as conn:
            insert_returning_id(
                conn,
                "INSERT INTO light_app_services("
                "service_id, app_id, name, provider, connector_instance_id, "
                "allowed_tools_json, allowed_url_prefixes_json, request_headers_json, "
                "cache_ttl_seconds, max_calls_per_minute, public_live, enabled, "
                "created_by, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    service_id,
                    app_id,
                    name,
                    provider,
                    connector_instance_id,
                    json.dumps(allowed_tools, ensure_ascii=False),
                    json.dumps(allowed_url_prefixes, ensure_ascii=False),
                    json.dumps(request_headers, ensure_ascii=False),
                    cache_ttl_seconds,
                    max_calls_per_minute,
                    int(public_live),
                    int(enabled),
                    created_by,
                    ts,
                    ts,
                ),
            )
        row = self.get(service_id)
        if row is None:
            raise RuntimeError("service insert failed")
        return row

    def update(
        self,
        service_id: str,
        *,
        provider: str,
        connector_instance_id: str,
        allowed_tools: list[str],
        allowed_url_prefixes: list[str],
        request_headers: dict[str, str],
        cache_ttl_seconds: int,
        max_calls_per_minute: int,
        public_live: bool,
        enabled: bool,
    ) -> LightAppServiceRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE light_app_services SET "
                "provider = ?, connector_instance_id = ?, allowed_tools_json = ?, "
                "allowed_url_prefixes_json = ?, request_headers_json = ?, "
                "cache_ttl_seconds = ?, "
                "max_calls_per_minute = ?, public_live = ?, enabled = ?, updated_at = ? "
                "WHERE service_id = ?",
                (
                    provider,
                    connector_instance_id,
                    json.dumps(allowed_tools, ensure_ascii=False),
                    json.dumps(allowed_url_prefixes, ensure_ascii=False),
                    json.dumps(request_headers, ensure_ascii=False),
                    cache_ttl_seconds,
                    max_calls_per_minute,
                    int(public_live),
                    int(enabled),
                    now_ts(),
                    service_id,
                ),
            )
        return self.get(service_id)

    def delete(self, app_id: str, name: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_services WHERE app_id = ? AND name = ?",
                (app_id, name),
            )
        return int(cursor.rowcount) > 0

    # ----- cache -----

    def get_cached(
        self,
        service_id: str,
        tool: str,
        args_hash: str,
        *,
        now: int,
    ) -> str | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT response_json FROM light_app_service_cache "
                "WHERE service_id = ? AND tool = ? AND args_hash = ? AND expires_at > ?",
                (service_id, tool, args_hash, now),
            ).fetchone()
        if row is None:
            return None
        return str(row["response_json"])

    def put_cached(
        self,
        *,
        service_id: str,
        tool: str,
        args_hash: str,
        response_json: str,
        expires_at: int,
    ) -> None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO light_app_service_cache("
                "service_id, tool, args_hash, response_json, expires_at, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(service_id, tool, args_hash) DO UPDATE SET "
                "response_json = excluded.response_json, "
                "expires_at = excluded.expires_at, "
                "created_at = excluded.created_at",
                (service_id, tool, args_hash, response_json, expires_at, ts),
            )

    def clear_cache(self, service_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM light_app_service_cache WHERE service_id = ?",
                (service_id,),
            )

    def delete_expired(self, *, now: int) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_service_cache WHERE expires_at <= ?",
                (now,),
            )
        return int(cursor.rowcount)


__all__ = ["LightAppServiceRepo", "LightAppServiceRow"]
