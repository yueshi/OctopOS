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
