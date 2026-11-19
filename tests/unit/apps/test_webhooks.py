"""Outbound webhooks: owner declarations, the emit path and its limits."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from octop.infra.apps import capabilities as app_capabilities
from octop.infra.apps import limits as app_limits
from octop.infra.apps import webhooks as webhooks_module
from octop.infra.apps.webhooks import AppWebhookService
from octop.infra.connectors.crypto import decrypt_credentials
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.events.service import ExternalEventService


class FakeAppService:
    def __init__(self, capabilities: set[str] | None = None) -> None:
        self.capabilities = capabilities or set()

    def require_capability(self, row: object, capability: str) -> None:
        if capability not in self.capabilities:
            raise OctopError(
                ErrorCode.APP_CAPABILITY_DENIED,
                f"app does not allow {capability}",
            )


@dataclass
class World:
    repos: RepoBundle
    service: AppWebhookService
    events: ExternalEventService
    app: object
    other_app: object
    alice_id: int


@pytest.fixture(autouse=True)
def _reset_limits() -> None:
    app_limits.reset_for_tests()


@pytest.fixture(autouse=True)
def _skip_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests have no DNS; the guard itself is tested separately."""
    monkeypatch.setattr(webhooks_module, "validate_agent_fetch_url", lambda url: url)


@pytest.fixture
def world(tmp_path) -> World:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    users = UserRepo(db)
    alice_id = users.create(username="alice", password_hash="h", role="user")
    agents = AgentRepo(db)
    agents.create(agent_id="agent-1", user_id=alice_id, name="bot")
    apps = LightAppRepo(db)
    app = apps.create(agent_id="agent-1", owner_user_id=alice_id, name="Tracker")
    other_app = apps.create(agent_id="agent-1", owner_user_id=alice_id, name="Other")
    repos = RepoBundle.from_pool(db)
    fake = FakeAppService({app_capabilities.CAP_EVENTS_EMIT})
    service = AppWebhookService(
        app_service=fake,  # type: ignore[arg-type]
        webhook_repo=repos.app_webhook_repo,
        source_repo=repos.external_source_repo,
        subscription_repo=repos.external_subscription_repo,
        secret_repo=repos.secret_repo,
    )
    events = ExternalEventService(repos=repos, secret_repo=repos.secret_repo)
    return World(
        repos=repos,
        service=service,
        events=events,
        app=app,
        other_app=other_app,
        alice_id=alice_id,
    )


async def _create(world: World, **overrides: object):
    values: dict[str, object] = {
        "name": "ERP",
        "url": "https://93.184.216.34/hook",
        "event_types": ["order.*"],
        "enabled": True,
        "actor_user_id": world.alice_id,
    }
    values.update(overrides)
    return await world.service.create(
        world.app,  # type: ignore[arg-type]
        **values,  # type: ignore[arg-type]
    )


def _assert_code(exc: pytest.ExceptionInfo[OctopError], code: ErrorCode) -> None:
    assert exc.value.code == code


async def test_create_persists_source_subscription_and_secret(world: World) -> None:
    view, secret = await _create(world)

    assert view.webhook_id.startswith("wh_")
    assert view.event_types == ("order.*",)
    assert view.enabled is True
    assert secret

    row = world.repos.app_webhook_repo.get(view.webhook_id)
    assert row is not None
    assert row.secret_blob is not None
    assert decrypt_credentials(world.repos.secret_repo, row.secret_blob)["secret"] == secret

    source = world.repos.external_source_repo.get(row.source_id)
    assert source is not None
    assert source.kind == "app_outbound"
    assert json.loads(source.config_json) == {"app_id": world.app.app_id}

    subscription = world.repos.external_subscription_repo.get(row.subscription_id)
    assert subscription is not None
    assert subscription.target_type == "webhook"
    assert subscription.target_id == view.webhook_id
    assert subscription.mode == "webhook.post"

    assert world.service.list_for_app(world.app) == [view]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": "   "}, "name is required"),
        ({"url": "ftp://example.com/hook"}, "http(s)"),
        ({"url": "https://user:pass@example.com/hook"}, "credentials"),
        ({"url": "https:///hook"}, "http(s)"),
        ({"event_types": []}, "at least one event type"),
        ({"event_types": ["bad type"]}, "invalid event type"),
    ],
)
async def test_create_rejects_bad_input(
    world: World, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(OctopError) as exc:
        await _create(world, **overrides)
    _assert_code(exc, ErrorCode.EXTERNAL_EVENT_INVALID)
    assert message in exc.value.message


async def test_create_rejects_ssrf_target(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def _reject(url: str) -> str:
        raise RuntimeError("private address")

    monkeypatch.setattr(webhooks_module, "validate_agent_fetch_url", _reject)
    with pytest.raises(OctopError) as exc:
        await _create(world)
    _assert_code(exc, ErrorCode.EXTERNAL_EVENT_INVALID)
    assert "not allowed" in exc.value.message


async def test_create_cleans_up_source_when_subscription_fails(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**kwargs: object):
        raise RuntimeError("db down")

    monkeypatch.setattr(world.repos.external_subscription_repo, "create", _boom)
    with pytest.raises(RuntimeError):
        await _create(world)
    assert world.repos.external_source_repo.list_for_owner(world.alice_id) == []


async def test_update_and_rotate_secret(world: World) -> None:
    view, secret = await _create(world)

    updated, rotated = await world.service.update(
        world.app,  # type: ignore[arg-type]
        view.webhook_id,
        name="ERP v2",
        url="https://93.184.216.35/hook",
        event_types=["order.*", "*"],
        enabled=False,
    )
    assert rotated is None
    assert updated.name == "ERP v2"
    assert updated.url == "https://93.184.216.35/hook"
    assert updated.event_types == ("order.*", "*")
    assert updated.enabled is False

    rotated_view, new_secret = await world.service.update(
        world.app,  # type: ignore[arg-type]
        view.webhook_id,
        rotate_secret=True,
    )
    assert rotated_view.webhook_id == view.webhook_id
    assert new_secret and new_secret != secret
    row = world.repos.app_webhook_repo.get(view.webhook_id)
    assert row is not None and row.secret_blob is not None
    assert decrypt_credentials(world.repos.secret_repo, row.secret_blob)["secret"] == new_secret


async def test_update_and_delete_reject_foreign_webhook(world: World) -> None:
    view, _secret = await _create(world)

    with pytest.raises(OctopError) as exc:
        await world.service.update(
            world.other_app,  # type: ignore[arg-type]
            view.webhook_id,
            name="stolen",
        )
    _assert_code(exc, ErrorCode.APP_NOT_FOUND)

    assert world.service.delete(world.other_app, view.webhook_id) is False  # type: ignore[arg-type]
    assert world.repos.app_webhook_repo.get(view.webhook_id) is not None


async def test_delete_removes_source_and_subscription(world: World) -> None:
    view, _secret = await _create(world)
    row = world.repos.app_webhook_repo.get(view.webhook_id)
    assert row is not None

    assert world.service.delete(world.app, view.webhook_id) is True  # type: ignore[arg-type]

    assert world.repos.app_webhook_repo.get(view.webhook_id) is None
    assert world.repos.external_source_repo.get(row.source_id) is None
    assert world.repos.external_subscription_repo.get(row.subscription_id) is None


async def test_emit_matches_patterns_and_dedupes(world: World) -> None:
    wildcard, _ = await _create(world, name="All", event_types=["*"])
    orders, _ = await _create(world, name="Orders", event_types=["order.*"])
    await _create(world, name="Disabled", event_types=["*"], enabled=False)

    first = await world.service.emit(
        world.app,  # type: ignore[arg-type]
        world.events,
        event_type="order.approved",
        payload={"order_id": "A-1", "summary": "approved"},
        client_event_id="req-1",
    )

    assert first["external_id"] == "req-1"
    assert first["emitted"] == 2
    assert first["duplicates"] == 0
    assert first["deliveries"] == 2
    assert {item["webhook_id"] for item in first["webhooks"]} == {
        wildcard.webhook_id,
        orders.webhook_id,
    }

    second = await world.service.emit(
        world.app,  # type: ignore[arg-type]
        world.events,
        event_type="order.approved",
        payload={"order_id": "A-1"},
        client_event_id="req-1",
    )
    assert second["duplicates"] == 2
    assert second["deliveries"] == 0
    assert {item["event_id"] for item in second["webhooks"]} == {
        item["event_id"] for item in first["webhooks"]
    }

    other = await world.service.emit(
        world.app,  # type: ignore[arg-type]
        world.events,
        event_type="invoice.paid",
    )
    assert [item["webhook_id"] for item in other["webhooks"]] == [wildcard.webhook_id]
    assert other["external_id"].startswith("app-")


async def test_emit_requires_capability_and_matching_hook(world: World) -> None:
    with pytest.raises(OctopError) as exc:
        await world.service.emit(
            world.app,  # type: ignore[arg-type]
            world.events,
            event_type="order.approved",
        )
    _assert_code(exc, ErrorCode.EXTERNAL_EVENT_INVALID)
    assert "no enabled outbound webhook" in exc.value.message

    denied = AppWebhookService(
        app_service=FakeAppService(),  # type: ignore[arg-type]
        webhook_repo=world.repos.app_webhook_repo,
        source_repo=world.repos.external_source_repo,
        subscription_repo=world.repos.external_subscription_repo,
        secret_repo=world.repos.secret_repo,
    )
    with pytest.raises(OctopError) as exc:
        await denied.emit(
            world.app,  # type: ignore[arg-type]
            world.events,
            event_type="order.approved",
        )
    _assert_code(exc, ErrorCode.APP_CAPABILITY_DENIED)


async def test_emit_rejects_bad_type_and_oversized_payload(world: World) -> None:
    await _create(world, event_types=["*"])

    with pytest.raises(OctopError) as exc:
        await world.service.emit(
            world.app,  # type: ignore[arg-type]
            world.events,
            event_type="bad type",
        )
    _assert_code(exc, ErrorCode.EXTERNAL_EVENT_INVALID)

    with pytest.raises(OctopError) as exc:
        await world.service.emit(
            world.app,  # type: ignore[arg-type]
            world.events,
            event_type="order.approved",
            payload={"blob": "x" * (64 * 1024)},
        )
    _assert_code(exc, ErrorCode.EXTERNAL_EVENT_INVALID)
    assert "exceeds" in exc.value.message


async def test_app_delete_removes_webhook_artifacts(world: World) -> None:
    view, _secret = await _create(world)
    row = world.repos.app_webhook_repo.get(view.webhook_id)
    assert row is not None

    world.repos.light_app_repo.delete(world.app.app_id)

    assert world.repos.app_webhook_repo.get(view.webhook_id) is None
    assert world.repos.external_source_repo.get(row.source_id) is None
    assert world.repos.external_subscription_repo.get(row.subscription_id) is None


async def test_webhook_test_event_uses_pattern(world: World) -> None:
    view, _secret = await _create(world, event_types=["order.*"])

    result = await world.service.test(
        world.app,  # type: ignore[arg-type]
        view.webhook_id,
        world.events,
    )
    assert result.event.event_type == "order.test"
    assert result.delivery_count == 1

    await world.service.update(
        world.app,  # type: ignore[arg-type]
        view.webhook_id,
        enabled=False,
    )
    with pytest.raises(OctopError) as exc:
        await world.service.test(
            world.app,  # type: ignore[arg-type]
            view.webhook_id,
            world.events,
        )
    _assert_code(exc, ErrorCode.EXTERNAL_EVENT_INVALID)
    assert "disabled" in exc.value.message
