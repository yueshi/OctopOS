"""App-owned inbound ingress: ownership, secret, mapping and teardown."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends import FilesystemBackend
from octop_harness.backends import BackendWorkspace

from octop.infra.apps.ingress import AppIngressService
from octop.infra.apps.wiring import build_app_service, build_ingress_service
from octop.infra.connectors.crypto import decrypt_credentials
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.events.security import sign
from octop.infra.events.service import ExternalEventService
from octop.infra.utils.ulid import new_ulid


class _Workspaces:
    def __init__(self, workspace: BackendWorkspace) -> None:
        self._workspace = workspace

    def workspace_for_agent(self, agent_id: str) -> BackendWorkspace | None:
        return self._workspace


@dataclass
class World:
    repos: RepoBundle
    service: Any
    ingress: AppIngressService
    events: ExternalEventService
    agent_id: str
    alice_id: int
    app_id: str


@pytest.fixture
async def world(tmp_path: Path) -> World:
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
    repos = RepoBundle.from_pool(db)
    service = build_app_service(repos=repos, workspaces=_Workspaces(workspace))
    app = await service.create(
        agent_id=agent_id,
        owner_user_id=alice_id,
        name="Ops",
        columns=[{"name": "title", "type": "string", "required": True}],
    )
    events = ExternalEventService(repos=repos, secret_repo=repos.secret_repo)
    return World(
        repos=repos,
        service=service,
        ingress=build_ingress_service(repos=repos, app_service=service),
        events=events,
        agent_id=agent_id,
        alice_id=alice_id,
        app_id=app.app_id,
    )


def _assert_code(exc: pytest.ExceptionInfo[OctopError], code: ErrorCode) -> None:
    assert exc.value.code == code


async def test_create_ingress_owns_source_and_subscription(world: World) -> None:
    view, secret = await world.ingress.create(
        world.service.get(world.app_id),
        name="Orders",
        mode="app.inbox",
        event_type="order.created",
        mapping={},
        actor_user_id=world.alice_id,
    )

    assert view.ingress_id.startswith("ing_")
    assert view.event_type == "order.created"
    assert view.enabled is True
    assert secret

    row = world.repos.app_ingress_repo.get(view.ingress_id)
    assert row is not None
    source = world.repos.external_source_repo.get(row.source_id)
    assert source is not None and source.kind == "app_ingress"
    assert json.loads(source.config_json)["app_id"] == world.app_id
    assert source.secret_blob is not None
    assert decrypt_credentials(world.repos.secret_repo, source.secret_blob)["secret"] == secret

    subscription = world.repos.external_subscription_repo.get(row.subscription_id)
    assert subscription is not None
    assert subscription.target_type == "app"
    assert subscription.target_id == world.app_id
    assert subscription.mode == "app.inbox"
    assert json.loads(subscription.filter_json) == {"event_type": "order.created"}

    assert world.ingress.list(world.service.get(world.app_id))[0].ingress_id == view.ingress_id

    body = b'{"summary":"signed"}'
    timestamp = str(int(time.time()))
    verified = world.events.verify_webhook(
        source_id=row.source_id,
        timestamp=timestamp,
        signature=sign(secret, timestamp, body),
        body=body,
    )
    assert verified.source_id == row.source_id


async def test_update_rotate_and_delete_ingress(world: World) -> None:
    view, secret = await world.ingress.create(
        world.service.get(world.app_id),
        name="Orders",
        mode="app.inbox",
        actor_user_id=world.alice_id,
    )
    updated = await world.ingress.update(
        world.service.get(world.app_id),
        view.ingress_id,
        name="Orders v2",
        mode="app.import",
        event_type="order.updated",
        mapping={"table": "main"},
        max_per_minute=10,
        enabled=False,
    )
    assert updated.name == "Orders v2"
    assert updated.mode == "app.import"
    assert updated.event_type == "order.updated"
    assert updated.mapping == {"table": "main"}
    assert updated.enabled is False
    row = world.repos.app_ingress_repo.get(view.ingress_id)
    source = world.repos.external_source_repo.get(row.source_id)
    assert source is not None and source.enabled is False
    subscription = world.repos.external_subscription_repo.get(row.subscription_id)
    assert subscription is not None and subscription.enabled is False

    new_secret = await world.ingress.rotate_secret(world.service.get(world.app_id), view.ingress_id)
    assert new_secret and new_secret != secret
    source = world.repos.external_source_repo.get(row.source_id)
    assert source is not None and source.secret_blob is not None
    assert decrypt_credentials(world.repos.secret_repo, source.secret_blob)["secret"] == new_secret

    assert world.ingress.delete(world.service.get(world.app_id), view.ingress_id) is True
    assert world.repos.app_ingress_repo.get(view.ingress_id) is None
    assert world.repos.external_source_repo.get(row.source_id) is None
    assert world.repos.external_subscription_repo.get(row.subscription_id) is None


async def test_ingress_validation(world: World) -> None:
    app = world.service.get(world.app_id)
    with pytest.raises(OctopError) as exc:
        await world.ingress.create(
            app,
            name="Bad",
            mode="app.import",
            mapping={"table": "main", "columns": {"x": "missing"}},
            actor_user_id=world.alice_id,
        )
    _assert_code(exc, ErrorCode.APP_SOURCE_INVALID)
    assert "missing" in exc.value.message

    with pytest.raises(OctopError) as exc:
        await world.ingress.create(
            app,
            name="Bad",
            mode="app.refresh",
            mapping={},
            actor_user_id=world.alice_id,
        )
    _assert_code(exc, ErrorCode.APP_SOURCE_INVALID)

    with pytest.raises(OctopError) as exc:
        await world.ingress.create(
            app,
            name="Bad",
            mode="app.inbox",
            event_type="order.*",
            actor_user_id=world.alice_id,
        )
    _assert_code(exc, ErrorCode.APP_SOURCE_INVALID)
    assert world.repos.external_source_repo.list_for_owner(world.alice_id) == []


async def test_test_event_and_app_delete_cleanup(world: World) -> None:
    view, _secret = await world.ingress.create(
        world.service.get(world.app_id),
        name="Orders",
        mode="app.inbox",
        actor_user_id=world.alice_id,
    )
    result = await world.ingress.test_event(
        world.service.get(world.app_id),
        view.ingress_id,
        world.events,
    )
    assert result.event.event_type == "app.ingress.test"
    assert result.delivery_count == 1

    row = world.repos.app_ingress_repo.get(view.ingress_id)
    assert row is not None
    world.repos.light_app_repo.delete(world.app_id)
    assert world.repos.app_ingress_repo.get(view.ingress_id) is None
    assert world.repos.external_source_repo.get(row.source_id) is None
    assert world.repos.external_subscription_repo.get(row.subscription_id) is None


async def test_backup_restores_integration_declarations_without_secrets(
    world: World,
) -> None:
    import io
    import zipfile

    from octop.infra.apps.wiring import build_webhook_service

    app = world.service.get(world.app_id)
    ingress, ingress_secret = await world.ingress.create(
        app,
        name="Orders",
        mode="app.inbox",
        event_type="order.created",
        actor_user_id=world.alice_id,
    )
    webhook_service = build_webhook_service(repos=world.repos, app_service=world.service)
    webhook, webhook_secret = await webhook_service.create(
        app,
        name="ERP",
        url="https://93.184.216.34/hook",
        event_types=["order.*"],
        enabled=True,
        actor_user_id=world.alice_id,
    )
    world.service.save_service(
        app,
        name="books",
        provider="external",
        connector_instance_id="",
        allowed_tools=["fetch_json"],
        allowed_url_prefixes=["https://api.example.com"],
        cache_ttl_seconds=300,
        max_calls_per_minute=5,
        public_live=False,
        enabled=True,
        actor_user_id=world.alice_id,
        is_admin=False,
    )
    await world.service.set_table_row_scope(app, table="main", row_scope="user")
    source = world.service.save_source(
        app,
        source_id="src_old",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=120,
        params={"path": "inbound/data.csv"},
        mapping={},
        created_by=world.alice_id,
    )
    refresh_ingress, _refresh_secret = await world.ingress.create(
        app,
        name="Refresh",
        mode="app.refresh",
        mapping={"source_id": source.source_id},
        actor_user_id=world.alice_id,
    )

    _filename, archive = await world.service.backup_archive(app)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        raw = archive
    assert manifest["version"] == 3
    assert manifest["integrations"]["ingress"][0]["name"] == "Orders"
    assert manifest["integrations"]["webhooks"][0]["name"] == "ERP"
    assert manifest["integrations"]["services"][0]["name"] == "books"
    assert manifest["integrations"]["sources"][0]["source_id"] == "src_old"
    assert manifest["integrations"]["schedules"][0]["action"] == "sync_source"
    assert manifest["tables"][0]["row_scope"] == "user"
    assert ingress_secret.encode() not in raw
    assert webhook_secret.encode() not in raw

    restored = await world.service.restore_archive(
        agent_id=world.agent_id,
        owner_user_id=world.alice_id,
        data=archive,
    )
    restored_view = world.service.get(restored.app_id)
    ingress_views = world.ingress.list(restored_view)
    assert {item.name for item in ingress_views} == {"Orders", "Refresh"}
    orders = next(item for item in ingress_views if item.name == "Orders")
    refresh = next(item for item in ingress_views if item.name == "Refresh")
    assert orders.enabled is False
    assert orders.source_id != ingress.source_id
    assert refresh.enabled is False
    restored_sources = world.service.list_sources(restored_view)
    assert [item.table_key for item in restored_sources] == ["main"]
    assert refresh.mapping["source_id"] == restored_sources[0].source_id
    schedules = world.service.list_schedules(restored_view)
    assert [(item.action, item.enabled) for item in schedules] == [("sync_source", False)]
    assert schedules[0].target["source_id"] == restored_sources[0].source_id
    assert schedules[0].interval_seconds == 120

    webhooks = world.repos.app_webhook_repo.list_for_app(restored.app_id)
    assert [item.name for item in webhooks] == ["ERP"]
    assert webhooks[0].enabled is False
    services = world.service.list_services(restored_view)
    assert [item.name for item in services] == ["books"]
    assert services[0].enabled is False
    table = world.repos.light_app_repo.get_table(restored.app_id, "main")
    assert table is not None and table.row_scope == "user"
