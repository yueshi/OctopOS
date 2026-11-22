"""Single authorization kernel for MiniApp data and management actions.

The HTTP layer, agent tools, and IM tools must all call this module. The caller
resolves the principal and the effective app role; this function combines the
role with table visibility, row ownership, and PII policy so no endpoint has
to re-implement those rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from octop.infra.users.principals import Principal, PrincipalKind

if TYPE_CHECKING:
    from octop.infra.apps.service import AppRecord, AppTableView
    from octop.infra.db.repos.apps import LightAppDataRow


class Action(StrEnum):
    VIEW_APP = "view_app"
    VIEW_SCHEMA = "view_schema"
    VIEW_ROW = "view_row"
    CREATE_ROW = "create_row"
    UPDATE_ROW = "update_row"
    DELETE_ROW = "delete_row"
    EDIT_DATA = "edit_data"
    EXPORT = "export"
    AGGREGATE = "aggregate"
    AI = "ai"
    SERVICE_CALL = "service_call"
    EMIT_EVENT = "emit_event"
    MANAGE_STRUCTURE = "manage_structure"
    MANAGE_APP_SETTINGS = "manage_app_settings"
    MANAGE_ASSETS = "manage_assets"
    UPLOAD_IMPORT = "upload_import"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_SHARES = "manage_shares"
    MANAGE_BINDINGS = "manage_bindings"
    MANAGE_INGRESS = "manage_ingress"
    MANAGE_SCHEDULE = "manage_schedule"
    EXPORT_DATASET = "export_dataset"
    RUN_SOURCE = "run_source"
    PUBLISH = "publish"
    DELETE_APP = "delete_app"
    VIEW_HISTORY = "view_history"
    RUN_GAME = "run_game"


PiiPolicy = Literal["visible", "masked", "mask_unless_own"]
RowScope = Literal["all", "own", "none"]

ROLE_OWNER = "owner"
ROLE_EDIT_STRUCTURE = "edit_structure"
ROLE_EDIT_DATA = "edit_data"
ROLE_VIEW = "view"
ROLE_PARTICIPATE = "participate"

_VIEW_ROLES = frozenset(
    {ROLE_OWNER, ROLE_EDIT_STRUCTURE, ROLE_EDIT_DATA, ROLE_VIEW, ROLE_PARTICIPATE}
)
_EDIT_ROLES = frozenset({ROLE_OWNER, ROLE_EDIT_STRUCTURE, ROLE_EDIT_DATA, ROLE_PARTICIPATE})
_STRUCTURE_ROLES = frozenset({ROLE_OWNER, ROLE_EDIT_STRUCTURE})
_OWNER_ONLY = frozenset(
    {
        Action.MANAGE_APP_SETTINGS,
        Action.MANAGE_MEMBERS,
        Action.MANAGE_SHARES,
        Action.MANAGE_BINDINGS,
        Action.MANAGE_INGRESS,
        Action.MANAGE_SCHEDULE,
        Action.EXPORT_DATASET,
        Action.PUBLISH,
        Action.DELETE_APP,
    }
)
_READ_ACTIONS = frozenset(
    {
        Action.VIEW_APP,
        Action.VIEW_SCHEMA,
        Action.VIEW_ROW,
        Action.VIEW_HISTORY,
    }
)
_ROW_WRITE_ACTIONS = frozenset({Action.UPDATE_ROW, Action.DELETE_ROW})
_ROW_ACTIONS = frozenset(
    {
        Action.VIEW_ROW,
        Action.CREATE_ROW,
        Action.UPDATE_ROW,
        Action.DELETE_ROW,
    }
)
_TABLE_ACTIONS = _ROW_ACTIONS | {Action.VIEW_SCHEMA}
_HIDDEN_TABLE_ACTIONS = _TABLE_ACTIONS | {
    Action.VIEW_HISTORY,
    Action.EXPORT,
    Action.AGGREGATE,
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    role: str | None
    row_scope: RowScope
    pii_policy: PiiPolicy
    reason: str


def deny(reason: str, *, role: str | None = None) -> Decision:
    return Decision(
        allowed=False,
        role=role,
        row_scope="none",
        pii_policy="masked",
        reason=reason,
    )


def _pii_policy(role: str) -> PiiPolicy:
    if role in {ROLE_OWNER, ROLE_EDIT_STRUCTURE, ROLE_EDIT_DATA}:
        return "visible"
    if role == ROLE_PARTICIPATE:
        return "mask_unless_own"
    return "masked"


def row_owner_id(row: LightAppDataRow) -> int | None:
    """Effective owner: explicit row owner, else the legacy creator."""
    return row.row_owner_user_id if row.row_owner_user_id is not None else row.created_by


def owns_row(principal: Principal, row: LightAppDataRow) -> bool:
    return principal.user_id is not None and row_owner_id(row) == principal.user_id


def _row_scope(role: str, table: AppTableView | None) -> RowScope:
    if table is not None and table.row_scope == "user" and role != ROLE_OWNER:
        return "own"
    return "all"


def can_view_pii(
    principal: Principal,
    decision: Decision,
    row: LightAppDataRow | None = None,
) -> bool:
    """Whether PII cells in *row* may be returned unmasked."""
    if not decision.allowed:
        return False
    if decision.pii_policy == "visible":
        return True
    if decision.pii_policy == "masked":
        return False
    return row is not None and owns_row(principal, row)


def authorize(
    principal: Principal,
    app: AppRecord,
    action: Action,
    *,
    role: str | None,
    table: AppTableView | None = None,
    row: LightAppDataRow | None = None,
    is_admin: bool = False,
) -> Decision:
    """Combined role × table × row × PII decision.

    ``role`` is the effective app role resolved by the caller:
    ``owner | edit_structure | edit_data | participate | view``. ``None`` is
    denied. Agent-owner read-only access is passed as ``view``.
    """
    if principal.user_id is None:
        return deny("no principal user")
    if principal.kind is PrincipalKind.GUEST and role != ROLE_PARTICIPATE:
        # Guests may only ever hold `participate`, even if a row was
        # misconfigured or a caller passed a stronger role.
        return deny("guest role invalid", role=role)
    if role not in _VIEW_ROLES:
        return deny("no app access")
    if role == ROLE_OWNER and not is_admin and principal.user_id != app.owner_user_id:
        # Defensive: a caller must not smuggle an "owner" role for a different
        # user. Admins are the only exception.
        return deny("owner mismatch", role=role)

    if action in _TABLE_ACTIONS and table is None:
        return deny("table required", role=role)
    if (
        role == ROLE_PARTICIPATE
        and action in _HIDDEN_TABLE_ACTIONS
        and table is not None
        and not table.member_visible
    ):
        return deny("table hidden", role=role)

    row_scope = _row_scope(role, table)
    pii = _pii_policy(role)

    def decide() -> bool:
        if action in _READ_ACTIONS:
            return True
        if action in {Action.CREATE_ROW, Action.EDIT_DATA}:
            return role in _EDIT_ROLES
        if action in _ROW_WRITE_ACTIONS:
            if role == ROLE_VIEW:
                return False
            if role == ROLE_PARTICIPATE:
                return row is not None and owns_row(principal, row)
            if table is not None and table.row_scope == "user" and role != ROLE_OWNER:
                return row is not None and owns_row(principal, row)
            return True
        if action in {Action.EXPORT, Action.AGGREGATE}:
            return role != ROLE_PARTICIPATE
        if action in {Action.AI, Action.SERVICE_CALL, Action.EMIT_EVENT}:
            return role != ROLE_PARTICIPATE
        if action in {Action.MANAGE_ASSETS, Action.RUN_SOURCE, Action.UPLOAD_IMPORT}:
            return role in _EDIT_ROLES
        if action is Action.RUN_GAME:
            return role in {ROLE_OWNER, ROLE_EDIT_STRUCTURE, ROLE_EDIT_DATA}
        if action is Action.MANAGE_STRUCTURE:
            return role in _STRUCTURE_ROLES
        if action in _OWNER_ONLY:
            return role == ROLE_OWNER
        return False

    if not decide():
        return deny("action denied", role=role)
    return Decision(
        allowed=True,
        role=role,
        row_scope=row_scope,
        pii_policy=pii,
        reason="ok",
    )


__all__ = [
    "Action",
    "Decision",
    "PiiPolicy",
    "ROLE_EDIT_DATA",
    "ROLE_EDIT_STRUCTURE",
    "ROLE_OWNER",
    "ROLE_PARTICIPATE",
    "ROLE_VIEW",
    "RowScope",
    "authorize",
    "can_view_pii",
    "deny",
    "owns_row",
    "row_owner_id",
]
