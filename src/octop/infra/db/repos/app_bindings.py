"""light_app_bindings — MiniApp service bindings. SQL only."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, insert_returning_id, map_rows, now_ts

BUILTIN_BINDING_ID = "dataset"
BUILTIN_SERVICE_ID = "builtin.dataset"
BINDING_PROVIDERS = ("builtin", "connector", "external")


@dataclass(frozen=True)
class LightAppBindingRow:
    binding_id: str
    pk: int
    app_id: str
    provider: str
    service_id: str
    config_json: str
    enabled: bool
    version: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> LightAppBindingRow:
        return cls(
            binding_id=str(r["binding_id"]),
            pk=int(r["id"]),
            app_id=str(r["app_id"]),
            provider=str(r["provider"]),
            service_id=str(r["service_id"]),
            config_json=str(r["config_json"] or "{}"),
            enabled=bool(int(r["enabled"] or 0)),
            version=int(r["version"] or 1),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class LightAppBindingRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_for_app(self, app_id: str) -> list[LightAppBindingRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_bindings WHERE app_id = ? ORDER BY created_at ASC, id ASC",
                (app_id,),
            ).fetchall()
        return map_rows(rows, LightAppBindingRow)

    def get(self, app_id: str, binding_id: str) -> LightAppBindingRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_bindings WHERE app_id = ? AND binding_id = ?",
                (app_id, binding_id),
            ).fetchone()
        return LightAppBindingRow.from_row(row) if row else None

    def upsert(
        self,
        *,
        app_id: str,
        binding_id: str,
        provider: str,
        service_id: str,
        config_json: str = "{}",
        enabled: bool = True,
    ) -> LightAppBindingRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM light_app_bindings WHERE app_id = ? AND binding_id = ?",
                (app_id, binding_id),
            ).fetchone()
            if existing is None:
                insert_returning_id(
                    conn,
                    "INSERT INTO light_app_bindings("
                    "binding_id, app_id, provider, service_id, config_json, "
                    "enabled, version, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        binding_id,
                        app_id,
                        provider,
                        service_id,
                        config_json,
                        int(enabled),
                        ts,
                        ts,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE light_app_bindings SET provider = ?, service_id = ?, "
                    "config_json = ?, enabled = ?, version = version + 1, updated_at = ? "
                    "WHERE app_id = ? AND binding_id = ?",
                    (
                        provider,
                        service_id,
                        config_json,
                        int(enabled),
                        ts,
                        app_id,
                        binding_id,
                    ),
                )
        row = self.get(app_id, binding_id)
        if row is None:
            raise RuntimeError("binding upsert failed")
        return row

    def delete(self, app_id: str, binding_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_bindings WHERE app_id = ? AND binding_id = ?",
                (app_id, binding_id),
            )
        return int(cursor.rowcount) > 0


__all__ = [
    "BINDING_PROVIDERS",
    "BUILTIN_BINDING_ID",
    "BUILTIN_SERVICE_ID",
    "LightAppBindingRepo",
    "LightAppBindingRow",
]
