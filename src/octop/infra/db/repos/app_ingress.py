"""light_app_ingress — app-owned inbound endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_short_id


@dataclass(frozen=True)
class LightAppIngressRow:
    pk: int
    ingress_id: str
    app_id: str
    name: str
    mode: str
    source_id: str
    subscription_id: str
    enabled: bool
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> LightAppIngressRow:
        return cls(
            pk=int(r["id"]),
            ingress_id=str(r["ingress_id"]),
            app_id=str(r["app_id"]),
            name=str(r["name"]),
            mode=str(r["mode"]),
            source_id=str(r["source_id"]),
            subscription_id=str(r["subscription_id"]),
            enabled=bool(int(r["enabled"] or 0)),
            created_by=int(r["created_by"]),
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class LightAppIngressRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, ingress_id: str) -> LightAppIngressRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM light_app_ingress WHERE ingress_id = ?",
                (ingress_id,),
            ).fetchone()
        return LightAppIngressRow.from_row(row) if row else None

    def list_for_app(self, app_id: str) -> list[LightAppIngressRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM light_app_ingress WHERE app_id = ? ORDER BY id",
                (app_id,),
            ).fetchall()
        return map_rows(rows, LightAppIngressRow)

    def create(
        self,
        *,
        app_id: str,
        name: str,
        mode: str,
        source_id: str,
        subscription_id: str,
        created_by: int,
        enabled: bool = True,
        ingress_id: str | None = None,
    ) -> LightAppIngressRow:
        ingress_id = ingress_id or f"ing_{new_short_id(14)}"
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO light_app_ingress("
                "ingress_id, app_id, name, mode, source_id, subscription_id, "
                "enabled, created_by, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ingress_id,
                    app_id,
                    name,
                    mode,
                    source_id,
                    subscription_id,
                    int(enabled),
                    created_by,
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM light_app_ingress WHERE ingress_id = ?",
                (ingress_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - insert + select in one transaction
            raise RuntimeError("ingress insert returned no row")
        return LightAppIngressRow.from_row(row)

    def update(
        self,
        ingress_id: str,
        *,
        name: str | None = None,
        mode: str | None = None,
        enabled: bool | None = None,
    ) -> LightAppIngressRow | None:
        fields: list[str] = []
        params: list[object] = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if mode is not None:
            fields.append("mode = ?")
            params.append(mode)
        if enabled is not None:
            fields.append("enabled = ?")
            params.append(int(enabled))
        if not fields:
            return self.get(ingress_id)
        fields.append("updated_at = ?")
        params.append(now_ts())
        params.append(ingress_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE light_app_ingress SET {', '.join(fields)} WHERE ingress_id = ?",
                tuple(params),
            )
            row = conn.execute(
                "SELECT * FROM light_app_ingress WHERE ingress_id = ?",
                (ingress_id,),
            ).fetchone()
        return LightAppIngressRow.from_row(row) if row else None

    def delete(self, ingress_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM light_app_ingress WHERE ingress_id = ?",
                (ingress_id,),
            )
        return int(cursor.rowcount or 0) > 0


__all__ = ["LightAppIngressRepo", "LightAppIngressRow"]
