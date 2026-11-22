"""MiniApp external member invites. SQL only."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts


@dataclass(frozen=True)
class AppMemberInviteRow:
    invite_id: str
    app_id: str
    token_hash: str
    audience: str
    permission: str
    invitee_hint: str
    expires_at: int
    max_uses: int
    used_count: int
    host_status: str
    created_by: int
    revoked_at: int | None
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> AppMemberInviteRow:
        revoked = row["revoked_at"]
        return cls(
            invite_id=str(row["invite_id"]),
            app_id=str(row["app_id"]),
            token_hash=str(row["token_hash"]),
            audience=str(row["audience"]),
            permission=str(row["permission"]),
            invitee_hint=str(row["invitee_hint"] or ""),
            expires_at=int(row["expires_at"]),
            max_uses=int(row["max_uses"]),
            used_count=int(row["used_count"]),
            host_status=str(row["host_status"]),
            created_by=int(row["created_by"]),
            revoked_at=int(revoked) if revoked is not None else None,
            created_at=int(row["created_at"]),
        )


class AppMemberInviteRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        invite_id: str,
        app_id: str,
        token_hash: str,
        audience: str,
        permission: str,
        invitee_hint: str,
        expires_at: int,
        max_uses: int,
        host_status: str,
        created_by: int,
    ) -> AppMemberInviteRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_member_invites("
                "invite_id, app_id, token_hash, audience, permission, invitee_hint, "
                "expires_at, max_uses, used_count, host_status, created_by, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    invite_id,
                    app_id,
                    token_hash,
                    audience,
                    permission,
                    invitee_hint,
                    expires_at,
                    max_uses,
                    host_status,
                    created_by,
                    ts,
                ),
            )
        row = self.get_by_token_hash(token_hash)
        if row is None:
            raise RuntimeError(f"app member invite insert failed: {invite_id}")
        return row

    def get(self, app_id: str, invite_id: str) -> AppMemberInviteRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_member_invites WHERE app_id = ? AND invite_id = ?",
                (app_id, invite_id),
            ).fetchone()
        return AppMemberInviteRow.from_row(row) if row else None

    def get_by_token_hash(self, token_hash: str) -> AppMemberInviteRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_member_invites WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return AppMemberInviteRow.from_row(row) if row else None

    def list_for_app(self, app_id: str) -> list[AppMemberInviteRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM app_member_invites WHERE app_id = ? ORDER BY created_at DESC, id DESC",
                (app_id,),
            ).fetchall()
        return map_rows(rows, AppMemberInviteRow)

    def consume(self, token_hash: str, *, now: int) -> AppMemberInviteRow | None:
        """Atomically take one use; None when invalid, expired, revoked or used up."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_member_invites SET used_count = used_count + 1 "
                "WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ? "
                "AND used_count < max_uses",
                (token_hash, now),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM app_member_invites WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return AppMemberInviteRow.from_row(row) if row else None

    def set_host_status(
        self,
        app_id: str,
        invite_id: str,
        *,
        host_status: str,
    ) -> AppMemberInviteRow | None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE app_member_invites SET host_status = ? WHERE app_id = ? AND invite_id = ?",
                (host_status, app_id, invite_id),
            )
        return self.get(app_id, invite_id)

    def revoke(self, app_id: str, invite_id: str, *, revoked_at: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE app_member_invites SET revoked_at = ? "
                "WHERE app_id = ? AND invite_id = ? AND revoked_at IS NULL",
                (revoked_at, app_id, invite_id),
            )
        return int(cursor.rowcount) > 0
