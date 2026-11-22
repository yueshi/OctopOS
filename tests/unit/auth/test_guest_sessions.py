"""Guest sessions, refresh rotation and scoped app tokens."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.auth.guest_sessions import (
    APP_TOKEN_TTL_SECONDS,
    GUEST_TOKEN_TTL_SECONDS,
    AppSessionService,
)
from octop.infra.auth.tokens import decode_token
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout

_SECRET = b"k" * 32


class Clock:
    def __init__(self, value: int = 1_700_000_000) -> None:
        self.value = value

    def __call__(self) -> float:
        return float(self.value)


@pytest.fixture
def guest_env(tmp_path: Path):
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    services = build_shared_services(db=db, paths=PathLayout(tmp_path), config=OctopConfig())
    services.secret_repo.set("jwt", _SECRET)
    member_id = services.user_repo.create(username="alice", password_hash="h", role="user")
    guest_id = services.user_repo.create(
        username="guest_1",
        password_hash=None,
        role="user",
        kind="guest",
    )
    AgentRepo(db).create(agent_id="ag1", user_id=member_id, name="bot")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO light_apps("
            "app_id, agent_id, owner_user_id, name, created_at, updated_at"
            ") VALUES ('app1', 'ag1', ?, 'Tracker', 1, 1)",
            (member_id,),
        )
    services.light_app_repo.set_collaborators("app1", [(guest_id, "participate")])
    clock = Clock()
    service = AppSessionService(services.repos, clock=clock)
    return services, service, guest_id, clock


def test_guest_token_claims_and_refresh_rotation(guest_env) -> None:
    services, service, guest_id, _clock = guest_env
    tokens = service.create_guest_session(guest_id)
    payload = decode_token(_SECRET, tokens.guest_token)
    assert payload["scope"] == "guest"
    assert payload["kind"] == "guest"
    assert payload["sid"] == tokens.session_id
    assert payload["exp"] - payload["iat"] == GUEST_TOKEN_TTL_SECONDS

    rotated = service.rotate(tokens.refresh_token)
    assert rotated.session_id == tokens.session_id
    assert rotated.refresh_token != tokens.refresh_token
    new_payload = decode_token(_SECRET, rotated.guest_token)
    assert new_payload["sid"] == tokens.session_id

    with pytest.raises(OctopError) as exc:
        service.rotate(tokens.refresh_token)
    assert exc.value.code is ErrorCode.APP_SESSION_INVALID


def test_revoke_session_blocks_guest_token(guest_env) -> None:
    services, service, guest_id, _clock = guest_env
    tokens = service.create_guest_session(guest_id)
    assert service.revoke(tokens.session_id, user_id=guest_id) is True
    with pytest.raises(OctopError) as exc:
        service.require_active(tokens.session_id, guest_id)
    assert exc.value.code is ErrorCode.APP_SESSION_INVALID


def test_app_token_claims_and_membership_checks(guest_env) -> None:
    services, service, guest_id, _clock = guest_env
    tokens = service.create_guest_session(guest_id)
    user = services.user_repo.get(guest_id)
    assert user is not None
    app_token = service.mint_app_token(
        user,
        "app1",
        role="participate",
        session_id=tokens.session_id,
    )
    payload = decode_token(_SECRET, app_token.access_token)
    assert payload["scope"] == "app"
    assert payload["app_id"] == "app1"
    assert payload["kind"] == "guest"
    assert payload["sid"] == tokens.session_id
    assert payload["exp"] - payload["iat"] == APP_TOKEN_TTL_SECONDS
    assert service.app_role(guest_id, "app1") == "participate"

    services.light_app_repo.update_collaborator("app1", guest_id, status="pending")
    with pytest.raises(OctopError) as exc:
        service.app_role(guest_id, "app1")
    assert exc.value.code is ErrorCode.APP_MEMBER_PENDING

    services.light_app_repo.update_collaborator("app1", guest_id, status="removed")
    with pytest.raises(OctopError) as exc:
        service.app_role(guest_id, "app1")
    assert exc.value.code is ErrorCode.APP_FORBIDDEN


def test_portal_list_leave_and_delete_account(guest_env) -> None:
    services, service, guest_id, _clock = guest_env
    tokens = service.create_guest_session(guest_id)
    apps = service.list_guest_apps(guest_id)
    assert [(item.app_id, item.role) for item in apps] == [("app1", "participate")]

    assert service.leave_app(guest_id, "app1") is True
    assert service.list_guest_apps(guest_id) == []
    assert service.leave_app(guest_id, "app1") is False

    services.light_app_repo.update_collaborator("app1", guest_id, status="active")
    service.delete_account(guest_id)
    row = services.user_repo.get(guest_id)
    assert row is not None and row.disabled == 1 and row.display_name == ""
    with pytest.raises(OctopError) as exc:
        service.list_guest_apps(guest_id)
    assert exc.value.code is ErrorCode.APP_GUEST_DISABLED
    with pytest.raises(OctopError) as exc:
        service.require_active(tokens.session_id, guest_id)
    assert exc.value.code is ErrorCode.APP_SESSION_INVALID
    with pytest.raises(OctopError) as exc:
        service.create_guest_session(guest_id)
    assert exc.value.code is ErrorCode.APP_GUEST_DISABLED
