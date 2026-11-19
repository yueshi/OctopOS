"""Read-only integration operations tools for MiniApp agents."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends import FilesystemBackend
from langgraph.config import var_child_runnable_config
from octop_harness.backends import BackendWorkspace

from octop.infra.apps.tools import build_app_tools
from octop.infra.apps.wiring import build_app_service
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.events.service import ExternalEventService
from octop.infra.utils.ulid import new_ulid


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


class _Workspaces:
    def __init__(self, workspace: BackendWorkspace) -> None:
        self._workspace = workspace

    def workspace_for_agent(self, agent_id: str) -> BackendWorkspace | None:
        return self._workspace


@dataclass
class World:
    repos: RepoBundle
    service: Any
    events: ExternalEventService
    tools: list[Any]
    agent_id: str
    alice_id: int
    bob_id: int
    app_id: str


def _tool(tools: list[Any], name: str) -> Any:
    return next(tool for tool in tools if tool.name == name)


@pytest.fixture
async def world(tmp_path: Path) -> World:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    users = UserRepo(db)
    alice_id = users.create(username="alice", password_hash="h", role="user")
    bob_id = users.create(username="bob", password_hash="h", role="user")
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
    tools = build_app_tools(service, repos=repos)
    return World(
        repos=repos,
        service=service,
        events=events,
        tools=tools,
        agent_id=agent_id,
        alice_id=alice_id,
        bob_id=bob_id,
        app_id=app.app_id,
    )


async def _source_subscription(
    world: World,
    *,
    mode: str = "app.inbox",
    mapping: dict[str, Any] | None = None,
):
    source, _secret = world.events.create_source(owner_user_id=world.alice_id, name="Orders")
    subscription = world.events.create_subscription(
        user_id=world.alice_id,
        is_admin=False,
        source_id=source.source_id,
        target_type="app",
        target_id=world.app_id,
        mode=mode,
        mapping=mapping,
    )
    return source, subscription


async def test_integration_list_and_health(world: World) -> None:
    source, subscription = await _source_subscription(
        world,
        mode="app.import",
        mapping={"table": "main", "columns": {"name": "missing_column"}},
    )
    ingested = world.events.ingest(
        source=source,
        event_type="order.created",
        external_id="evt-1",
        occurred_at=None,
        payload={"summary": "created"},
    )
    delivery = world.repos.external_delivery_repo.list_by_event(ingested.event.event_id)[0]
    world.repos.external_delivery_repo.finish(
        delivery.delivery_id,
        status="dead",
        attempt=6,
        last_error="app target is no longer writable",
    )

    list_tool = _tool(world.tools, "app_integration_list")
    health_tool = _tool(world.tools, "app_integration_health")
    with _configurable(agent_id=world.agent_id, user=str(world.alice_id)):
        inventory = json.loads(await list_tool.ainvoke({"app_id": world.app_id}))
        health = json.loads(await health_tool.ainvoke({"app_id": world.app_id}))

    assert inventory["app_id"] == world.app_id
    assert [item["source_id"] for item in inventory["sources"]] == [source.source_id]
    assert [item["subscription_id"] for item in inventory["subscriptions"]] == [
        subscription.subscription_id
    ]
    assert inventory["deliveries"]["counts"]["dead"] == 1

    codes = {item["code"] for item in health["issues"]}
    assert health["healthy"] is False
    assert "mapping_column_missing" in codes
    assert "delivery_dead" in codes
    assert health["recent_failures"][0]["delivery_id"] == delivery.delivery_id


async def test_integration_trace_resolves_external_id(world: World) -> None:
    source, _subscription = await _source_subscription(world)
    ingested = world.events.ingest(
        source=source,
        event_type="order.created",
        external_id="external-42",
        occurred_at=None,
        payload={"summary": "created"},
    )
    trace_tool = _tool(world.tools, "app_integration_trace")
    with _configurable(agent_id=world.agent_id, user=str(world.alice_id)):
        trace = json.loads(
            await trace_tool.ainvoke({"app_id": world.app_id, "event_id": "external-42"})
        )

    assert trace["event"]["event_id"] == ingested.event.event_id
    assert trace["event"]["external_id"] == "external-42"
    assert [item["delivery_id"] for item in trace["deliveries"]] == [
        world.repos.external_delivery_repo.list_by_event(ingested.event.event_id)[0].delivery_id
    ]


async def test_integration_tools_are_owner_only(world: World) -> None:
    await _source_subscription(world)
    world.repos.agent_repo.set_shared(world.agent_id, True)
    health_tool = _tool(world.tools, "app_integration_health")
    with _configurable(agent_id=world.agent_id, user=str(world.bob_id)):
        payload = json.loads(await health_tool.ainvoke({"app_id": world.app_id}))
    assert payload["data"]["code"] == "APP_PERMISSION_DENIED"


async def test_ingress_mutation_tools_and_hitl(world: World) -> None:
    from octop.infra.agents.security.hitl_session import MANDATORY_HITL_TOOLS

    assert {
        "app_integration_create_ingress",
        "app_integration_update_ingress",
        "app_integration_delete_ingress",
        "app_integration_test_ingress",
        "app_integration_create_webhook",
        "app_integration_update_webhook",
        "app_integration_delete_webhook",
        "app_integration_test_webhook",
    } <= MANDATORY_HITL_TOOLS

    create_tool = _tool(world.tools, "app_integration_create_ingress")
    list_tool = _tool(world.tools, "app_integration_list")
    test_tool = _tool(world.tools, "app_integration_test_ingress")
    delete_tool = _tool(world.tools, "app_integration_delete_ingress")
    with _configurable(agent_id=world.agent_id, user=str(world.alice_id)):
        created = json.loads(
            await create_tool.ainvoke(
                {
                    "app_id": world.app_id,
                    "name": "Orders",
                    "mode": "app.inbox",
                    "event_type": "order.created",
                }
            )
        )
        ingress_id = created["ingress_id"]
        assert created["secret"]
        assert created["hook_url"].endswith(f"/api/hooks/{created['source_id']}")

        inventory = json.loads(await list_tool.ainvoke({"app_id": world.app_id}))
        assert inventory["ingress"][0]["ingress_id"] == ingress_id

        tested = json.loads(
            await test_tool.ainvoke({"app_id": world.app_id, "ingress_id": ingress_id})
        )
        assert tested["event_type"] == "order.created"
        assert tested["deliveries"] == 1

        deleted = json.loads(
            await delete_tool.ainvoke({"app_id": world.app_id, "ingress_id": ingress_id})
        )
        assert deleted["deleted"] is True


async def test_outbound_webhook_mutation_tools(world: World) -> None:
    create_tool = _tool(world.tools, "app_integration_create_webhook")
    update_tool = _tool(world.tools, "app_integration_update_webhook")
    test_tool = _tool(world.tools, "app_integration_test_webhook")
    delete_tool = _tool(world.tools, "app_integration_delete_webhook")
    list_tool = _tool(world.tools, "app_integration_list")

    with _configurable(agent_id=world.agent_id, user=str(world.alice_id)):
        created = json.loads(
            await create_tool.ainvoke(
                {
                    "app_id": world.app_id,
                    "name": "ERP",
                    "url": "https://93.184.216.34/hook",
                    "event_types": ["order.*"],
                }
            )
        )
        webhook_id = created["webhook_id"]
        assert created["enabled"] is True
        assert "secret" not in created
        assert "secret_note" in created

        inventory = json.loads(await list_tool.ainvoke({"app_id": world.app_id}))
        assert [item["webhook_id"] for item in inventory["outbound_webhooks"]] == [webhook_id]

        updated = json.loads(
            await update_tool.ainvoke(
                {
                    "app_id": world.app_id,
                    "webhook_id": webhook_id,
                    "enabled": False,
                }
            )
        )
        assert updated["enabled"] is False
        tested = json.loads(
            await test_tool.ainvoke(
                {
                    "app_id": world.app_id,
                    "webhook_id": webhook_id,
                }
            )
        )
        assert "error" in tested

        updated = json.loads(
            await update_tool.ainvoke(
                {
                    "app_id": world.app_id,
                    "webhook_id": webhook_id,
                    "enabled": True,
                }
            )
        )
        assert updated["enabled"] is True

        tested = json.loads(
            await test_tool.ainvoke(
                {
                    "app_id": world.app_id,
                    "webhook_id": webhook_id,
                }
            )
        )
        assert tested["event_type"] == "order.test"
        assert tested["deliveries"] == 1

        deleted = json.loads(
            await delete_tool.ainvoke({"app_id": world.app_id, "webhook_id": webhook_id})
        )
        assert deleted["deleted"] is True
