"""Reference columns: schema, write validation and label expansion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from deepagents.backends import FilesystemBackend
from octop_harness.backends import BackendWorkspace

from octop.infra.apps.models import parse_schema_payload, validate_row
from octop.infra.apps.service import AppService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.app_access import AppAccessRequestRepo
from octop.infra.db.repos.app_bindings import LightAppBindingRepo
from octop.infra.db.repos.app_events import AppEventRepo
from octop.infra.db.repos.app_schedules import LightAppScheduleRepo
from octop.infra.db.repos.app_services import LightAppServiceRepo
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid

DEMO_PII_MASK = "••••"


@dataclass
class World:
    service: AppService
    workspace: BackendWorkspace
    agent_id: str
    alice_id: int


class _Workspaces:
    def __init__(self, workspace: BackendWorkspace) -> None:
        self._workspace = workspace

    def workspace_for_agent(self, agent_id: str) -> BackendWorkspace | None:
        return self._workspace


@pytest.fixture
def world(tmp_path: Path) -> World:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    users = UserRepo(db)
    alice_id = users.create(username="alice", password_hash="h", role="user")
    agents = AgentRepo(db)
    agent_id = new_ulid()
    agents.create(agent_id=agent_id, user_id=alice_id, name="bot")
    root = tmp_path / "ws"
    root.mkdir()
    workspace = BackendWorkspace(FilesystemBackend(root_dir=str(root), virtual_mode=True), root)
    service = AppService(
        app_repo=LightAppRepo(db),
        agent_repo=agents,
        user_repo=users,
        workspaces=_Workspaces(workspace),
        secret_repo=SecretRepo(db),
        access_repo=AppAccessRequestRepo(db),
        audit_repo=AuditRepo(db),
        binding_repo=LightAppBindingRepo(db),
        service_repo=LightAppServiceRepo(db),
        event_repo=AppEventRepo(db),
        schedule_repo=LightAppScheduleRepo(db),
        connector_repo=ConnectorRepo(db),
    )
    return World(
        service=service,
        workspace=workspace,
        agent_id=agent_id,
        alice_id=alice_id,
    )


async def _app(
    world: World,
    *,
    order_columns: list[dict[str, object]] | None = None,
) -> str:
    row = await world.service.create(
        agent_id=world.agent_id,
        owner_user_id=world.alice_id,
        name="Ops",
        columns=order_columns
        or [
            {"name": "order_no", "type": "string", "required": True},
            {
                "name": "customer",
                "type": "reference",
                "ref_table": "customers",
                "ref_label": "name",
            },
        ],
    )
    await world.service.create_table(
        row,
        table_key="customers",
        label="Customers",
        columns=[
            {"name": "name", "type": "string", "required": True},
            {"name": "phone", "type": "string", "pii": "phone"},
        ],
    )
    return row.app_id


def _assert_code(exc: pytest.ExceptionInfo[OctopError], code: ErrorCode) -> None:
    assert exc.value.code == code


async def test_reference_schema_roundtrip() -> None:
    schema = parse_schema_payload(
        [
            {
                "name": "customer",
                "type": "reference",
                "ref_table": "customers",
                "ref_label": "name",
            }
        ]
    )
    column = schema.columns[0]
    assert column.type == "reference"
    assert column.ref_table == "customers"
    assert column.ref_label == "name"
    assert column.to_dict()["ref_table"] == "customers"

    with pytest.raises(OctopError) as exc:
        parse_schema_payload([{"name": "x", "type": "reference"}])
    _assert_code(exc, ErrorCode.APP_SCHEMA_INVALID)
    with pytest.raises(OctopError) as exc:
        parse_schema_payload([{"name": "x", "type": "string", "ref_table": "customers"}])
    _assert_code(exc, ErrorCode.APP_SCHEMA_INVALID)


async def test_reference_is_a_scalar_row_id(world: World) -> None:
    schema = parse_schema_payload([{"name": "t", "type": "reference", "ref_table": "customers"}])
    assert validate_row(schema, {"t": "row_abc"}) == {"t": "row_abc"}
    with pytest.raises(OctopError):
        validate_row(schema, {"t": {"row_id": "row_abc"}})


async def test_write_requires_an_existing_target_row(world: World) -> None:
    app_id = await _app(world)
    app = world.service.get(app_id)
    _index, customer = await world.service.append_row(
        app, {"name": "Acme", "phone": "13800000000"}, table="customers"
    )
    customer_id = world.service._app_repo.list_all_data_rows(app_id, "customers")[0].row_id

    _index, stored = await world.service.append_row(
        app, {"order_no": "A-1", "customer": customer_id}
    )
    assert stored["customer"] == customer_id

    with pytest.raises(OctopError) as exc:
        await world.service.append_row(app, {"order_no": "A-2", "customer": "row_missing"})
    _assert_code(exc, ErrorCode.APP_ROW_INVALID)
    assert "missing row" in exc.value.message

    with pytest.raises(OctopError) as exc:
        await world.service.append_row(
            app, {"order_no": "A-3", "customer": customer_id}, table="customers"
        )
    _assert_code(exc, ErrorCode.APP_ROW_INVALID)

    assert customer["name"] == "Acme"


async def test_delete_target_is_blocked_while_referenced(world: World) -> None:
    app_id = await _app(world)
    app = world.service.get(app_id)
    await world.service.append_row(app, {"name": "Acme", "phone": "13800000000"}, table="customers")
    customer_id = world.service._app_repo.list_all_data_rows(app_id, "customers")[0].row_id
    await world.service.append_row(app, {"order_no": "A-1", "customer": customer_id})

    with pytest.raises(OctopError) as exc:
        await world.service.delete_row(app, customer_id)
    _assert_code(exc, ErrorCode.APP_ROW_IN_USE)
    assert exc.value.details["table"] == "main"
    assert exc.value.details["column"] == "customer"

    order_id = world.service._app_repo.list_all_data_rows(app_id, "main")[0].row_id
    await world.service.delete_row(app, order_id)
    assert await world.service.delete_row(app, customer_id) == customer_id


async def test_replace_is_blocked_while_referenced(world: World) -> None:
    app_id = await _app(world)
    app = world.service.get(app_id)
    await world.service.append_row(app, {"name": "Acme", "phone": "13800000000"}, table="customers")
    customer_id = world.service._app_repo.list_all_data_rows(app_id, "customers")[0].row_id
    await world.service.append_row(app, {"order_no": "A-1", "customer": customer_id})

    with pytest.raises(OctopError) as exc:
        await world.service.import_rows(
            app,
            [{"name": "Acme 2", "phone": ""}],
            table="customers",
            mode="replace",
        )
    _assert_code(exc, ErrorCode.APP_ROW_IN_USE)

    result = await world.service.import_rows(
        app,
        [{"name": "Acme 2", "phone": ""}],
        table="customers",
        mode="append",
    )
    assert result.imported == 1


async def test_reference_labels_and_pii(world: World) -> None:
    app_id = await _app(world)
    app = world.service.get(app_id)
    await world.service.append_row(app, {"name": "Acme", "phone": "13800000000"}, table="customers")
    customer_id = world.service._app_repo.list_all_data_rows(app_id, "customers")[0].row_id
    await world.service.append_row(app, {"order_no": "A-1", "customer": customer_id})
    await world.service.add_columns(
        app,
        [
            {
                "name": "customer_phone",
                "type": "reference",
                "ref_table": "customers",
                "ref_label": "phone",
            }
        ],
    )
    order_id = world.service._app_repo.list_all_data_rows(app_id, "main")[0].row_id
    await world.service.update_row(
        app,
        order_id,
        {"order_no": "A-1", "customer": customer_id, "customer_phone": customer_id},
    )

    schema, rows, _total = await world.service.read_rows(app)
    labels = await world.service.reference_labels(app, schema, rows)
    assert labels["customer"] == {customer_id: "Acme"}
    assert labels["customer_phone"] == {customer_id: "13800000000"}

    masked = await world.service.reference_labels(
        app,
        schema,
        rows,
        redact_pii=True,
    )
    assert masked["customer"] == {customer_id: "Acme"}
    assert masked["customer_phone"] == {customer_id: DEMO_PII_MASK}


async def test_backup_restore_preserves_reference_links(world: World) -> None:
    app_id = await _app(world)
    app = world.service.get(app_id)
    await world.service.append_row(app, {"name": "Acme", "phone": "13800000000"}, table="customers")
    customer_id = world.service._app_repo.list_all_data_rows(app_id, "customers")[0].row_id
    await world.service.append_row(app, {"order_no": "A-1", "customer": customer_id})

    _filename, archive = await world.service.backup_archive(world.service.get(app_id))
    restored = await world.service.restore_archive(
        agent_id=world.agent_id,
        owner_user_id=world.alice_id,
        data=archive,
    )
    restored_customer = world.service._app_repo.list_all_data_rows(restored.app_id, "customers")[0]

    schema, rows, _total = await world.service.read_rows(restored)
    labels = await world.service.reference_labels(restored, schema, rows)
    assert rows[0]["customer"] == restored_customer.row_id
    assert labels == {"customer": {restored_customer.row_id: "Acme"}}
