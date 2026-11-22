"""Pure authorization matrix for the MiniApp access kernel."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.infra.apps.access import (
    ROLE_EDIT_DATA,
    ROLE_EDIT_STRUCTURE,
    ROLE_OWNER,
    ROLE_PARTICIPATE,
    ROLE_VIEW,
    Action,
    authorize,
    can_view_pii,
)
from octop.infra.users.identity import User
from octop.infra.users.principals import principal_of

APP = SimpleNamespace(owner_user_id=1)


def _principal(user_id: int, kind: str = "member"):
    return principal_of(
        User(
            id=user_id,
            username=f"u{user_id}",
            role="user",
            display_name=None,
            kind=kind,
        )
    )


def _table(*, member_visible: bool = True, row_scope: str = "app"):
    return SimpleNamespace(member_visible=member_visible, row_scope=row_scope)


def _row(owner: int | None):
    return SimpleNamespace(row_owner_user_id=owner, created_by=owner)


def _decide(role: str, action: Action, *, table=None, row=None, user_id=2, is_admin=False):
    return authorize(
        _principal(user_id),
        APP,
        action,
        role=role,
        table=table,
        row=row,
        is_admin=is_admin,
    )


def test_owner_can_do_everything() -> None:
    owner = _decide(ROLE_OWNER, Action.UPDATE_ROW, table=_table(), row=_row(99), user_id=1)
    assert owner.allowed
    assert owner.row_scope == "all"
    assert owner.pii_policy == "visible"
    assert _decide(ROLE_OWNER, Action.MANAGE_MEMBERS, user_id=1).allowed
    assert _decide(ROLE_OWNER, Action.DELETE_APP, user_id=1).allowed


def test_admin_role_owner_allows_other_owner_id() -> None:
    decision = authorize(
        _principal(2),
        APP,
        Action.MANAGE_MEMBERS,
        role=ROLE_OWNER,
        is_admin=True,
    )
    assert decision.allowed


def test_owner_role_for_wrong_user_is_rejected_unless_admin() -> None:
    decision = _decide(ROLE_OWNER, Action.VIEW_APP, user_id=2)
    assert not decision.allowed
    assert decision.reason == "owner mismatch"


def test_edit_structure_can_change_structure_but_not_manage_members() -> None:
    assert _decide(ROLE_EDIT_STRUCTURE, Action.MANAGE_STRUCTURE).allowed
    assert not _decide(ROLE_EDIT_STRUCTURE, Action.MANAGE_MEMBERS).allowed
    assert not _decide(ROLE_EDIT_STRUCTURE, Action.PUBLISH).allowed


def test_edit_data_writes_any_app_scoped_row_but_only_own_user_rows() -> None:
    app_table = _table(row_scope="app")
    user_table = _table(row_scope="user")
    assert _decide(ROLE_EDIT_DATA, Action.UPDATE_ROW, table=app_table, row=_row(99)).allowed
    assert not _decide(ROLE_EDIT_DATA, Action.UPDATE_ROW, table=user_table, row=_row(99)).allowed
    assert _decide(ROLE_EDIT_DATA, Action.UPDATE_ROW, table=user_table, row=_row(2)).allowed
    assert _decide(ROLE_EDIT_DATA, Action.EXPORT, table=app_table).allowed


def test_view_reads_but_cannot_write_and_export_is_masked() -> None:
    table = _table()
    assert _decide(ROLE_VIEW, Action.VIEW_ROW, table=table).allowed
    assert not _decide(ROLE_VIEW, Action.CREATE_ROW, table=table).allowed
    assert not _decide(ROLE_VIEW, Action.UPDATE_ROW, table=table, row=_row(2)).allowed
    decision = _decide(ROLE_VIEW, Action.EXPORT, table=table)
    assert decision.allowed
    assert decision.pii_policy == "masked"


def test_participate_is_limited_to_member_visible_tables_and_own_rows() -> None:
    hidden = _table(member_visible=False)
    visible = _table(member_visible=True, row_scope="app")
    assert not _decide(ROLE_PARTICIPATE, Action.VIEW_ROW, table=hidden).allowed
    hidden_export = _decide(ROLE_PARTICIPATE, Action.EXPORT, table=hidden)
    assert not hidden_export.allowed
    assert hidden_export.reason == "table hidden"
    assert _decide(ROLE_PARTICIPATE, Action.VIEW_ROW, table=visible).allowed
    assert not _decide(ROLE_PARTICIPATE, Action.EXPORT, table=visible).allowed
    assert _decide(ROLE_PARTICIPATE, Action.UPDATE_ROW, table=visible, row=_row(2)).allowed
    assert not _decide(ROLE_PARTICIPATE, Action.UPDATE_ROW, table=visible, row=_row(99)).allowed
    decision = _decide(ROLE_PARTICIPATE, Action.VIEW_ROW, table=visible)
    assert decision.pii_policy == "mask_unless_own"
    assert decision.row_scope == "all"


def test_participate_on_user_scope_gets_own_row_scope() -> None:
    table = _table(row_scope="user")
    decision = _decide(ROLE_PARTICIPATE, Action.VIEW_ROW, table=table)
    assert decision.allowed
    assert decision.row_scope == "own"


def test_no_role_is_denied() -> None:
    decision = authorize(_principal(2), APP, Action.VIEW_APP, role=None)
    assert not decision.allowed
    assert decision.reason == "no app access"


def test_guest_cannot_hold_a_stronger_role() -> None:
    decision = authorize(
        _principal(2, kind="guest"),
        APP,
        Action.VIEW_APP,
        role=ROLE_EDIT_DATA,
    )
    assert not decision.allowed
    assert decision.reason == "guest role invalid"


def test_can_view_pii_follows_policy_and_ownership() -> None:
    table = _table()
    owner_decision = _decide(ROLE_OWNER, Action.VIEW_ROW, table=table, user_id=1)
    assert can_view_pii(_principal(1), owner_decision, _row(1))
    view_decision = _decide(ROLE_VIEW, Action.VIEW_ROW, table=table)
    assert not can_view_pii(_principal(2), view_decision, _row(2))
    participate_decision = _decide(ROLE_PARTICIPATE, Action.VIEW_ROW, table=table)
    assert can_view_pii(_principal(2), participate_decision, _row(2))
    assert not can_view_pii(_principal(2), participate_decision, _row(99))
    assert not can_view_pii(_principal(2), participate_decision, None)


@pytest.mark.parametrize("action", [Action.VIEW_APP, Action.VIEW_SCHEMA, Action.VIEW_ROW])
def test_unknown_role_denied(action: Action) -> None:
    assert not authorize(_principal(2), APP, action, role="custom-role").allowed
