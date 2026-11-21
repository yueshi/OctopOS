"""`/bind <code>` links a private IM subject to the signed-in dashboard user."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from octop.config import OctopConfig
from octop.infra.agents.manager import AgentManager
from octop.infra.auth.channel_binding import issue_bind_code
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.channel_identities import (
    ChannelBindCodeRepo,
    ChannelIdentityRepo,
)
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
        UserRepo(self.db).create(username="u", password_hash="h", role="user")
        AgentRepo(self.db).create(agent_id="a1", user_id=1, name="bot")
        services = build_shared_services(
            db=self.db, paths=PathLayout(tmp_path), config=OctopConfig()
        )
        manager = AgentManager(repos=services.repos, paths=services.paths)
        manager._harness_manager = MagicMock()
        self.identities = ChannelIdentityRepo(self.db)
        self.codes = ChannelBindCodeRepo(self.db)
        self.registry = ThreadRegistry(
            session_repo=SessionRepo(self.db),
            thread_repo=ThreadRepo(self.db),
        )
        self.manager = manager

    def ctx(self, *, chat_type: str = "dm", channel_type: str = "weixin") -> SlashCtx:
        session_key = ThreadRegistry.make_key(
            agent_id="a1",
            channel_type=channel_type,
            channel_subject_id="wx-u",
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
            channel_bind_code_repo=self.codes,
            locale="en",
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


@pytest.fixture
def dispatcher():
    return build_default_dispatcher()


@pytest.mark.asyncio
async def test_bind_in_dm_records_identity(world, dispatcher) -> None:
    code, _ = issue_bind_code(code_repo=world.codes, user_id=1)
    sink = BufferSink()
    await dispatcher.handle(SlashCommand("bind", code), world.ctx(), sink)
    assert "bound" in "\n".join(sink.lines).lower()
    row = world.identities.get("weixin", "wx-u")
    assert row is not None and row.user_id == 1


@pytest.mark.asyncio
async def test_bind_is_rejected_in_groups_and_dashboard(world, dispatcher) -> None:
    code, _ = issue_bind_code(code_repo=world.codes, user_id=1)
    group = BufferSink()
    await dispatcher.handle(SlashCommand("bind", code), world.ctx(chat_type="group"), group)
    assert "private chat" in "\n".join(group.lines).lower()
    assert world.identities.get("weixin", "wx-u") is None

    dashboard = BufferSink()
    await dispatcher.handle(
        SlashCommand("bind", code),
        world.ctx(channel_type="dashboard"),
        dashboard,
    )
    assert "private chat" in "\n".join(dashboard.lines).lower()
    assert world.identities.get("weixin", "wx-u") is None


@pytest.mark.asyncio
async def test_bind_rejects_invalid_code(world, dispatcher) -> None:
    sink = BufferSink()
    await dispatcher.handle(SlashCommand("bind", "nope"), world.ctx(), sink)
    assert "invalid" in "\n".join(sink.lines).lower()
    assert world.identities.get("weixin", "wx-u") is None


@pytest.mark.asyncio
async def test_bind_without_args_shows_usage(world, dispatcher) -> None:
    sink = BufferSink()
    await dispatcher.handle(SlashCommand("bind", ""), world.ctx(), sink)
    assert "usage" in "\n".join(sink.lines).lower()
