"""WebView-to-IM /link codes: one-shot consumption and identity binding."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.auth.channel_binding import issue_link_code, redeem_link_code
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.channel_identities import ChannelIdentityRepo
from octop.infra.db.repos.channel_link_codes import ChannelLinkCodeRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError


@pytest.fixture
def world(tmp_path: Path):
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    users = UserRepo(db)
    alice = users.create(username="alice", password_hash="h", role="user")
    guest = users.create(username="guest-link", password_hash=None, role="user", kind="guest")
    AgentRepo(db).create(agent_id="agent-1", user_id=alice, name="bot")
    LightAppRepo(db).create(agent_id="agent-1", owner_user_id=alice, name="app", app_id="app_1")
    return (
        ChannelIdentityRepo(db),
        ChannelLinkCodeRepo(db),
        alice,
        guest,
    )


def test_link_code_is_one_shot_and_binds_guest(world) -> None:
    identities, links, _alice, guest = world
    code, expires_at = issue_link_code(link_code_repo=links, user_id=guest, app_id="app_1")
    assert expires_at > 0
    row = redeem_link_code(
        identity_repo=identities,
        link_code_repo=links,
        code=code,
        channel_type="feishu",
        sender_id="ou_guest",
        account_key="app_1",
    )
    assert row is not None
    assert row.user_id == guest
    assert row.account_key == "app_1"
    assert row.sender_id == "ou_guest"
    # A second redemption fails: the code was consumed.
    assert (
        redeem_link_code(
            identity_repo=identities,
            link_code_repo=links,
            code=code,
            channel_type="feishu",
            sender_id="ou_guest",
            account_key="app_1",
        )
        is None
    )


def test_link_code_rejects_an_existing_other_user(world) -> None:
    identities, links, alice, guest = world
    identities.bind(channel_type="feishu", sender_id="ou_1", user_id=alice)
    code, _ = issue_link_code(link_code_repo=links, user_id=guest, app_id="app_1")
    with pytest.raises(OctopError) as excinfo:
        redeem_link_code(
            identity_repo=identities,
            link_code_repo=links,
            code=code,
            channel_type="feishu",
            sender_id="ou_1",
        )
    assert excinfo.value.code is ErrorCode.CHANNEL_IDENTITY_TAKEN


def test_invalid_link_code_returns_none(world) -> None:
    identities, links, _alice, guest = world
    assert (
        redeem_link_code(
            identity_repo=identities,
            link_code_repo=links,
            code="NOPE",
            channel_type="feishu",
            sender_id="ou_1",
        )
        is None
    )
    assert links.consume("nope", now=1) is None
