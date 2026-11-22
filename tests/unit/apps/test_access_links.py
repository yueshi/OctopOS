"""One-time MiniApp access links."""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends import FilesystemBackend
from octop_harness.backends import BackendWorkspace

from octop.infra.apps.service import AppService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.app_access_codes import AppAccessCodeRepo
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid


class _Workspaces:
    def __init__(self, agent_id: str, workspace: BackendWorkspace) -> None:
        self._agent_id = agent_id
        self._workspace = workspace

    def workspace_for_agent(self, agent_id: str) -> BackendWorkspace | None:
        return self._workspace if agent_id == self._agent_id else None


@pytest.fixture
def world(tmp_path: Path):
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    now = [1_000]
    user_repo = UserRepo(db)
    alice = user_repo.create(username="alice", password_hash="h", role="user")
    bob = user_repo.create(username="bob", password_hash="h", role="user")
    carol = user_repo.create(username="carol", password_hash="h", role="user")
    agent_repo = AgentRepo(db)
    agent_id = new_ulid()
    agent_repo.create(agent_id=agent_id, user_id=alice, name="bot")
    root = tmp_path / "ws"
    root.mkdir()
    workspace = BackendWorkspace(FilesystemBackend(root_dir=str(root), virtual_mode=True), root)
    service = AppService(
        app_repo=LightAppRepo(db),
        agent_repo=agent_repo,
        user_repo=user_repo,
        workspaces=_Workspaces(agent_id, workspace),
        secret_repo=SecretRepo(db),
        audit_repo=AuditRepo(db),
        access_code_repo=AppAccessCodeRepo(db),
        clock=lambda: now[0],
    )
    return service, agent_id, alice, bob, carol, now


@pytest.mark.asyncio
async def test_access_link_is_one_shot_and_preview_does_not_consume(world) -> None:
    service, agent_id, alice, _bob, _carol, now = world
    app = await service.create(
        agent_id=agent_id,
        owner_user_id=alice,
        name="Tracker",
        columns=[{"name": "title", "type": "string"}],
    )
    code, expires_at = service.create_access_link(
        app,
        user_id=alice,
        channel_type="weixin",
        channel_subject_id="wx-alice",
        account_key="app_1",
        sender_id="ou_alice",
        origin="test",
    )
    assert expires_at == 1_600

    preview = service.preview_access_link(code)
    assert preview is not None
    assert preview.app_id == app.app_id
    assert preview.permission == "edit_structure"
    assert preview.account_key == "app_1"
    assert preview.sender_id == "ou_alice"
    assert service.preview_access_link(code) is not None  # still not consumed

    consumed = service.consume_access_link(code)
    assert consumed is not None
    assert consumed.app_id == app.app_id
    assert service.consume_access_link(code) is None
    assert service.preview_access_link(code) is None

    now[0] = 1_601
    expired, _ = service.create_access_link(app, user_id=alice, origin="test")
    now[0] = 2_202
    assert service.consume_access_link(expired) is None


@pytest.mark.asyncio
async def test_viewer_can_get_access_link_but_outsider_cannot(world) -> None:
    service, agent_id, alice, bob, carol, _now = world
    app = await service.create(
        agent_id=agent_id,
        owner_user_id=alice,
        name="Shared",
        columns=[{"name": "title", "type": "string"}],
    )
    service.set_shares(app, [("bob", "view")])

    viewer_code, _ = service.create_access_link(app, user_id=bob, origin="test")
    assert viewer_code
    with pytest.raises(OctopError) as excinfo:
        service.create_access_link(app, user_id=carol, origin="test")
    assert excinfo.value.code is ErrorCode.APP_FORBIDDEN
