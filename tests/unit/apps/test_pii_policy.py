"""Tests for row-level PII policy and effective row ownership."""

from __future__ import annotations

import pytest

from octop.infra.apps.models import PII_MASK
from octop.infra.errors import ErrorCode, OctopError
from tests.unit.apps.test_service import World, world  # noqa: F401


async def _create_contacts(state: World) -> str:
    row = await state.service.create(
        agent_id=state.agent_id,
        owner_user_id=state.alice_id,
        name="Contacts",
        columns=[
            {"name": "title", "type": "string"},
            {"name": "phone", "type": "string", "pii": "phone"},
        ],
    )
    return row.app_id


def _phone_by_title(rows: list[dict[str, str]], title: str) -> str:
    return next(item["phone"] for item in rows if item["title"] == title)


async def test_pii_policy_masks_only_other_rows(world: World) -> None:  # noqa: F811
    app_id = await _create_contacts(world)
    app = world.service.get(app_id)
    await world.service.append_row(
        app,
        {"title": "mine", "phone": "13800000001"},
        created_by=world.bob_id,
    )
    await world.service.append_row(
        app,
        {"title": "other", "phone": "13900000002"},
        created_by=world.alice_id,
    )

    _schema, rows, meta, _total = await world.service.read_rows_detailed(
        app,
        pii_policy="mask_unless_own",
        viewer_user_id=world.bob_id,
        include_all_rows=True,
    )

    assert _phone_by_title(rows, "mine") == "13800000001"
    assert _phone_by_title(rows, "other") == PII_MASK
    assert [item["pii_masked"] for item in meta] == [False, True]


async def test_pii_policy_masked_and_visible_apply_to_every_row(world: World) -> None:  # noqa: F811
    app_id = await _create_contacts(world)
    app = world.service.get(app_id)
    await world.service.append_row(
        app,
        {"title": "mine", "phone": "13800000001"},
        created_by=world.bob_id,
    )

    _schema, masked, meta, _total = await world.service.read_rows_detailed(
        app,
        pii_policy="masked",
        viewer_user_id=world.bob_id,
        include_all_rows=True,
    )
    assert masked[0]["phone"] == PII_MASK
    assert meta[0]["pii_masked"] is True

    _schema, visible, meta, _total = await world.service.read_rows_detailed(
        app,
        pii_policy="visible",
        viewer_user_id=world.bob_id,
        include_all_rows=True,
    )
    assert visible[0]["phone"] == "13800000001"
    assert meta[0]["pii_masked"] is False


async def test_query_rows_applies_pii_policy_per_row(world: World) -> None:  # noqa: F811
    app_id = await _create_contacts(world)
    app = world.service.get(app_id)
    await world.service.append_row(
        app,
        {"title": "mine", "phone": "13800000001"},
        created_by=world.bob_id,
    )
    await world.service.append_row(
        app,
        {"title": "other", "phone": "13900000002"},
        created_by=world.alice_id,
    )

    _schema, rows, meta, _total = await world.service.query_rows_detailed(
        app,
        sort="title",
        pii_policy="mask_unless_own",
        viewer_user_id=world.bob_id,
        include_all_rows=True,
    )

    assert [item["title"] for item in rows] == ["mine", "other"]
    assert [item["phone"] for item in rows] == ["13800000001", PII_MASK]
    assert [item["pii_masked"] for item in meta] == [False, True]


async def test_query_rejects_pii_filters_when_rows_may_be_masked(world: World) -> None:  # noqa: F811
    app_id = await _create_contacts(world)
    app = world.service.get(app_id)

    with pytest.raises(OctopError) as exc:
        await world.service.query_rows_detailed(
            app,
            filters={"phone": "13800000001"},
            pii_policy="mask_unless_own",
            viewer_user_id=world.bob_id,
            include_all_rows=True,
        )
    assert exc.value.code == ErrorCode.APP_FORBIDDEN


async def test_row_owner_defaults_to_creator_and_survives_update(world: World) -> None:  # noqa: F811
    app_id = await _create_contacts(world)
    app = world.service.get(app_id)
    _index, stored = await world.service.append_row_record(
        app,
        {"title": "mine", "phone": "13800000001"},
        created_by=world.bob_id,
    )
    assert stored.row_owner_user_id == world.bob_id

    updated = await world.service.update_row(
        app,
        stored.row_id,
        {"title": "renamed", "phone": "13800000001"},
        viewer_user_id=world.bob_id,
    )
    assert updated["values"]["title"] == "renamed"
    row = world.repo.get_data_row(app_id, stored.row_id)
    assert row is not None
    assert row.row_owner_user_id == world.bob_id


async def test_user_scoped_read_uses_row_owner_then_creator(world: World) -> None:  # noqa: F811
    app_id = await _create_contacts(world)
    app = world.service.get(app_id)
    await world.service.set_table_row_scope(app, table="main", row_scope="user")
    # Legacy row: no explicit owner, visible through its creator.
    _index, legacy = await world.service.append_row_record(
        app,
        {"title": "legacy", "phone": "13800000001"},
        created_by=world.bob_id,
    )
    with world.db.transaction() as conn:
        conn.execute(
            "UPDATE light_app_rows SET row_owner_user_id = NULL WHERE app_id = ? AND row_id = ?",
            (app_id, legacy.row_id),
        )
    # New row: created by one user on behalf of another.
    _index, on_behalf = await world.service.append_row_record(
        app,
        {"title": "on-behalf", "phone": "13900000002"},
        created_by=world.alice_id,
        row_owner_user_id=world.bob_id,
    )

    _schema, bob_rows, bob_total = await world.service.read_rows(
        app,
        table="main",
        viewer_user_id=world.bob_id,
    )
    assert bob_total == 2
    assert {item["title"] for item in bob_rows} == {"legacy", "on-behalf"}

    _schema, alice_rows, alice_total = await world.service.read_rows(
        app,
        table="main",
        viewer_user_id=world.alice_id,
    )
    assert alice_total == 0
    assert alice_rows == []

    # Ownership follows row_owner_user_id, not created_by.
    with pytest.raises(OctopError) as exc:
        await world.service.update_row(
            app,
            on_behalf.row_id,
            {"title": "hijacked", "phone": "13900000002"},
            viewer_user_id=world.alice_id,
        )
    assert exc.value.code == ErrorCode.APP_ROW_NOT_FOUND
    updated = await world.service.update_row(
        app,
        on_behalf.row_id,
        {"title": "renamed", "phone": "13900000002"},
        viewer_user_id=world.bob_id,
    )
    assert updated["values"]["title"] == "renamed"
