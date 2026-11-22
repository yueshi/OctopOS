"""`/link <code>` binds a private IM sender to a MiniApp guest/member session."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from octop.config import OctopConfig
from octop.infra.agents.manager import AgentManager
from octop.infra.auth.channel_binding import issue_link_code
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.channel_identities import ChannelIdentityRepo
from octop.infra.db.repos.channel_link_codes import ChannelLinkCodeRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.db.services import build_shared_services
from octop.infra.gateway.slash import BufferSink, SlashCommand, build_default_dispatcher
from octop.infra.gateway.slash.ctx import SlashCtx
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.utils.paths import PathLayout


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.db = SqlitePool(tmp_path / "x.db")
        run_migrations(self.db)
        users = UserRepo(self.db)
        users.create(username="u", password_hash="h", role="user")
        self.guest = users.create(
            username="guest-link", password_hash=None, role="user", kind="guest"
        )
        AgentRepo(self.db).create(agent_id="a1", user_id=1, name="bot")
        LightAppRepo(self.db).create(agent_id="a1", owner_user_id=1, name="app", app_id="app_1")
        services = build_shared_services(
            db=self.db, paths=PathLayout(tmp_path), config=OctopConfig()
        )
        manager = AgentManager(repos=services.repos, paths=services.paths)
        manager._harness_manager = MagicMock()
        self.identities = ChannelIdentityRepo(self.db)
        self.links = ChannelLinkCodeRepo(self.db)
        self.registry = ThreadRegistry(
            session_repo=SessionRepo(self.db),
            thread_repo=ThreadRepo(self.db),
        )
        self.manager = manager

    def ctx(
        self,
        *,
        chat_type: str = "dm",
        channel_type: str = "feishu",
        account_key: str = "app_1",
    ) -> SlashCtx:
        session_key = ThreadRegistry.make_key(
            agent_id="a1",
            channel_type=channel_type,
            channel_subject_id="ou_1",
            channel_chat_type=chat_type,
        )
        return SlashCtx(
            agent_id="a1",
            user_id=1,
            channel_type=channel_type,
            session_key=session_key,
            thread_registry=self.registry,
            agent_manager=self.manager,
            channel_identity_repo=self.identities,
            channel_link_code_repo=self.links,
            account_key=account_key,
            sender_id="ou_1",
            locale="en",
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


@pytest.fixture
def dispatcher():
    return build_default_dispatcher()


@pytest.mark.asyncio
async def test_link_in_dm_binds_the_guest(world, dispatcher) -> None:
    code, _ = issue_link_code(link_code_repo=world.links, user_id=world.guest, app_id="app_1")
    sink = BufferSink()
    await dispatcher.handle(SlashCommand("link", code), world.ctx(), sink)
    assert "linked" in "\n".join(sink.lines).lower()
    row = world.identities.get("feishu", "ou_1", account_key="app_1")
    assert row is not None and row.user_id == world.guest


@pytest.mark.asyncio
async def test_link_is_rejected_in_groups_and_dashboard(world, dispatcher) -> None:
    code, _ = issue_link_code(link_code_repo=world.links, user_id=world.guest, app_id="app_1")
    group = BufferSink()
    await dispatcher.handle(SlashCommand("link", code), world.ctx(chat_type="group"), group)
    assert "private chat" in "\n".join(group.lines).lower()
    assert world.identities.get("feishu", "ou_1") is None

    dashboard = BufferSink()
    await dispatcher.handle(
        SlashCommand("link", code),
        world.ctx(channel_type="dashboard"),
        dashboard,
    )
    assert "private chat" in "\n".join(dashboard.lines).lower()
    assert world.identities.get("feishu", "ou_1") is None


@pytest.mark.asyncio
async def test_link_rejects_invalid_code_and_shows_usage(world, dispatcher) -> None:
    sink = BufferSink()
    await dispatcher.handle(SlashCommand("link", "NOPE"), world.ctx(), sink)
    assert "invalid" in "\n".join(sink.lines).lower()
    usage = BufferSink()
    await dispatcher.handle(SlashCommand("link", ""), world.ctx(), usage)
    assert "usage" in "\n".join(usage.lines).lower()
