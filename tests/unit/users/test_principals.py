"""PrincipalDirectory: member / guest resolution and token scopes."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.auth.tokens import sign_token
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.users.principals import PrincipalDirectory, PrincipalKind, principal_of
from octop.infra.utils.paths import PathLayout

_SECRET = b"k" * 32


def _setup(tmp_path: Path):
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
    return services, member_id, guest_id


def _token(user_id: int, username: str, **extra: object) -> str:
    return sign_token(
        _SECRET,
        sub=user_id,
        uname=username,
        role="user",
        ttl_seconds=600,
        extra=extra,
    )


def test_member_token_resolves_member(tmp_path: Path) -> None:
    services, member_id, _guest_id = _setup(tmp_path)
    directory = PrincipalDirectory(services.repos)
    principal = directory.resolve_token(_token(member_id, "alice"))
    assert principal.kind is PrincipalKind.MEMBER
    assert principal.user_id == member_id


def test_guest_scope_resolves_guest(tmp_path: Path) -> None:
    services, _member_id, guest_id = _setup(tmp_path)
    directory = PrincipalDirectory(services.repos)
    principal = directory.resolve_token(
        _token(guest_id, "guest_1", scope="guest", kind="guest", sid="s1")
    )
    assert principal.kind is PrincipalKind.GUEST
    assert principal.user_id == guest_id
    assert principal.app_id is None


def test_guest_app_token_requires_membership(tmp_path: Path) -> None:
    services, _member_id, guest_id = _setup(tmp_path)
    directory = PrincipalDirectory(services.repos)
    principal = directory.resolve_token(
        _token(guest_id, "guest_1", scope="app", app_id="app1", kind="guest")
    )
    assert principal.kind is PrincipalKind.GUEST
    assert principal.app_id == "app1"

    with pytest.raises(OctopError) as exc:
        directory.resolve_token(
            _token(guest_id, "guest_1", scope="app", app_id="missing", kind="guest")
        )
    assert exc.value.code is ErrorCode.APP_FORBIDDEN


def test_guest_platform_token_is_rejected(tmp_path: Path) -> None:
    services, _member_id, guest_id = _setup(tmp_path)
    directory = PrincipalDirectory(services.repos)
    with pytest.raises(OctopError) as exc:
        directory.resolve_token(_token(guest_id, "guest_1", kind="guest"))
    assert exc.value.code is ErrorCode.AUTH_FAILED


def test_disabled_guest_is_rejected(tmp_path: Path) -> None:
    services, _member_id, guest_id = _setup(tmp_path)
    services.user_repo.set_disabled(guest_id, True)
    directory = PrincipalDirectory(services.repos)
    with pytest.raises(OctopError) as exc:
        directory.resolve_token(_token(guest_id, "guest_1", scope="guest", kind="guest"))
    assert exc.value.code is ErrorCode.APP_GUEST_DISABLED


def test_pending_and_removed_guest_memberships_are_rejected(tmp_path: Path) -> None:
    services, _member_id, guest_id = _setup(tmp_path)
    directory = PrincipalDirectory(services.repos)
    token = _token(guest_id, "guest_1", scope="app", app_id="app1", kind="guest")

    services.light_app_repo.update_collaborator("app1", guest_id, status="pending")
    with pytest.raises(OctopError) as exc:
        directory.resolve_token(token)
    assert exc.value.code is ErrorCode.APP_MEMBER_PENDING

    services.light_app_repo.update_collaborator("app1", guest_id, status="removed")
    with pytest.raises(OctopError) as exc:
        directory.resolve_token(token)
    assert exc.value.code is ErrorCode.APP_FORBIDDEN


def test_resolve_channel_and_get_user(tmp_path: Path) -> None:
    services, member_id, guest_id = _setup(tmp_path)
    services.channel_identity_repo.bind(
        channel_type="telegram",
        external_subject_id="sender-1",
        user_id=guest_id,
    )
    directory = PrincipalDirectory(services.repos)
    principal = directory.resolve_channel("telegram", "", "sender-1")
    assert principal is not None
    assert principal.kind is PrincipalKind.GUEST
    assert principal.user_id == guest_id
    assert directory.resolve_channel("telegram", "", "missing") is None

    member = directory.get_user(member_id)
    assert member is not None and member.kind is PrincipalKind.MEMBER
    assert directory.require_member(member) == member_id


def test_principal_of_preserves_guest_kind() -> None:
    guest = User(id=1, username="g", role="user", display_name=None, kind="guest")
    principal = principal_of(guest)
    assert principal.kind is PrincipalKind.GUEST
    assert principal.user_id == 1
    with pytest.raises(OctopError):
        PrincipalDirectory.require_member(principal)
