"""MiniApp self-service join codes. SQL only."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts


@dataclass(frozen=True)
class AppJoinCodeRow:
    join_code_id: str
    code_hash: str
    app_id: str
    audience: str
    permission: str
    expires_at: int
    max_uses: int
    used_count: int
    requires_approval: bool
    revoked_at: int | None
    created_by: int
    created_at: int
    bound_channel_type: str
    bound_account_key: str
    bound_sender_id: str

    @classmethod
    def from_row(cls, row: DbRow) -> AppJoinCodeRow:
        revoked = row["revoked_at"]
        return cls(
            join_code_id=str(row["join_code_id"]),
            code_hash=str(row["code_hash"]),
            app_id=str(row["app_id"]),
            audience=str(row["audience"]),
            permission=str(row["permission"]),
            expires_at=int(row["expires_at"]),
            max_uses=int(row["max_uses"]),
            used_count=int(row["used_count"]),
            requires_approval=bool(int(row["requires_approval"] or 0)),
            revoked_at=int(revoked) if revoked is not None else None,
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            bound_channel_type=str(row["bound_channel_type"] or ""),
            bound_account_key=str(row["bound_account_key"] or ""),
            bound_sender_id=str(row["bound_sender_id"] or ""),
        )


class AppJoinCodeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        join_code_id: str,
        code_hash: str,
        app_id: str,
        audience: str,
        permission: str,
        expires_at: int,
        max_uses: int,
        requires_approval: bool,
        created_by: int,
        bound_channel_type: str = "",
        bound_account_key: str = "",
        bound_sender_id: str = "",
    ) -> AppJoinCodeRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_join_codes("
                "join_code_id, code_hash, app_id, audience, permission, expires_at, "
                "max_uses, used_count, requires_approval, created_by, created_at, "
                "bound_channel_type, bound_account_key, bound_sender_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
                (
                    join_code_id,
                    code_hash,
                    app_id,
                    audience,
                    permission,
                    expires_at,
                    max_uses,
                    int(requires_approval),
                    created_by,
                    ts,
                    bound_channel_type,
                    bound_account_key,
                    bound_sender_id,
                ),
            )
        row = self.get_by_code_hash(code_hash)
        if row is None:
            raise RuntimeError(f"app join code insert failed: {join_code_id}")
        return row

    def get(self, app_id: str, join_code_id: str) -> AppJoinCodeRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_join_codes WHERE app_id = ? AND join_code_id = ?",
                (app_id, join_code_id),
            ).fetchone()
        return AppJoinCodeRow.from_row(row) if row else None

    def get_by_code_hash(self, code_hash: str) -> AppJoinCodeRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_join_codes WHERE code_hash = ?",
                (code_hash,),
            ).fetchone()
        return AppJoinCodeRow.from_row(row) if row else None

    def list_for_app(self, app_id: str) -> list[AppJoinCodeRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM app_join_codes WHERE app_id = ? ORDER BY created_at DESC, id DESC",
                (app_id,),
            ).fetchall()
        return map_rows(rows, AppJoinCodeRow)

    def consume(self, code_hash: str, *, now: int) -> AppJoinCodeRow | None:
        """Atomically take one use; None when invalid, expired, revoked or used up."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_join_codes SET used_count = used_count + 1 "
                "WHERE code_hash = ? AND revoked_at IS NULL AND expires_at > ? "
                "AND used_count < max_uses",
                (code_hash, now),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM app_join_codes WHERE code_hash = ?",
                (code_hash,),
            ).fetchone()
        return AppJoinCodeRow.from_row(row) if row else None

    def revoke(self, app_id: str, join_code_id: str, *, revoked_at: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_join_codes SET revoked_at = ? "
                "WHERE app_id = ? AND join_code_id = ? AND revoked_at IS NULL",
                (revoked_at, app_id, join_code_id),
            )
        return int(cursor.rowcount) > 0
