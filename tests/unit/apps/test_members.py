"""Member lifecycle, external invites and join codes."""

from __future__ import annotations

import pytest

from octop.infra.apps.host_policy import HostPolicyService
from octop.infra.apps.members import (
    HOST_PENDING,
    HOST_REJECTED,
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REMOVED,
    AppMemberService,
)
from octop.infra.db.repos.app_join_codes import AppJoinCodeRepo
from octop.infra.db.repos.app_member_invites import AppMemberInviteRepo
from octop.infra.errors import ErrorCode, OctopError
from tests.unit.apps.test_service import World, _create, world  # noqa: F401


class Clock:
    def __init__(self, value: int = 1_700_000_000) -> None:
        self.value = value

    def __call__(self) -> float:
        return float(self.value)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def members(world: World, clock: Clock) -> AppMemberService:  # noqa: F811
    return AppMemberService(
        app_repo=world.repo,
        user_repo=world.user_repo,
        invite_repo=AppMemberInviteRepo(world.db),
        join_code_repo=AppJoinCodeRepo(world.db),
        clock=clock,
    )


async def test_host_policy_gates_external_participants(world: World) -> None:  # noqa: F811
    app = world.service.get(await _create(world))
    hosted = AppMemberService(
        app_repo=world.repo,
        user_repo=world.user_repo,
        invite_repo=AppMemberInviteRepo(world.db),
        join_code_repo=AppJoinCodeRepo(world.db),
        host_policy=HostPolicyService(world.agent_repo),
    )

    with pytest.raises(OctopError) as exc:
        hosted.add_guest(app, display_name="Eva", actor=world.alice_id)
    assert exc.value.code == ErrorCode.APP_FORBIDDEN
    with pytest.raises(OctopError) as exc:
        hosted.create_invite(
            app,
            audience="guest",
            permission="participate",
            invitee_hint="Eva",
            ttl_seconds=600,
            max_uses=1,
            actor=world.alice_id,
        )
    assert exc.value.code == ErrorCode.APP_FORBIDDEN

    HostPolicyService(world.agent_repo).update(
        world.agent_id,
        allow_external_participants=True,
        require_host_approval=True,
    )
    guest = hosted.add_guest(app, display_name="Eva", actor=world.alice_id)
    assert guest.status == STATUS_PENDING

    invite, _token = hosted.create_invite(
        app,
        audience="guest",
        permission="participate",
        invitee_hint="Eva",
        ttl_seconds=600,
        max_uses=1,
        actor=world.alice_id,
    )
    assert invite.host_status == HOST_PENDING

    _row, code = hosted.create_join_code(
        app,
        audience="guest",
        permission="participate",
        ttl_seconds=600,
        max_uses=1,
        requires_approval=False,
        actor=world.alice_id,
    )
    redeemed = hosted.redeem_join_code(code)
    assert redeemed.status == STATUS_PENDING


async def test_member_lifecycle(world: World, members: AppMemberService) -> None:  # noqa: F811
    app = world.service.get(await _create(world))

    view = members.add_member(
        app,
        username="bob",
        permission="view",
        actor=world.alice_id,
    )
    assert (view.user_id, view.permission, view.status, view.kind) == (
        world.bob_id,
        "view",
        STATUS_ACTIVE,
        "member",
    )
    assert view.joined_via == "share"
    assert view.invited_by == world.alice_id
    assert world.repo.permission_for(app.app_id, world.bob_id) == "view"

    pending = members.update_member(
        app,
        user_id=world.bob_id,
        status=STATUS_PENDING,
        actor=world.alice_id,
    )
    assert pending.status == STATUS_PENDING
    assert world.repo.permission_for(app.app_id, world.bob_id) is None

    active = members.update_member(
        app,
        user_id=world.bob_id,
        permission="edit_data",
        status=STATUS_ACTIVE,
        actor=world.alice_id,
    )
    assert active.permission == "edit_data"
    assert world.repo.permission_for(app.app_id, world.bob_id) == "edit_data"

    assert members.remove_member(app, user_id=world.bob_id, actor=world.alice_id) is True
    assert world.repo.permission_for(app.app_id, world.bob_id) is None
    assert world.repo.get_collaborator(app.app_id, world.bob_id).status == STATUS_REMOVED
    assert [item.user_id for item in members.list_members(app)] == []


async def test_set_collaborators_preserves_lifecycle(
    world: World,  # noqa: F811
    members: AppMemberService,
) -> None:
    app = world.service.get(await _create(world))
    members.add_member(app, username="bob", permission="view", actor=world.alice_id)
    members.update_member(
        app,
        user_id=world.bob_id,
        permission="participate",
        status=STATUS_PENDING,
        actor=world.alice_id,
    )

    world.repo.set_collaborators(app.app_id, [(world.bob_id, "edit_data")])

    row = world.repo.get_collaborator(app.app_id, world.bob_id)
    assert row is not None
    assert row.permission == "edit_data"
    assert row.status == STATUS_PENDING
    assert row.joined_via == "share"
    assert row.invited_by == world.alice_id


async def test_member_owner_and_guest_are_rejected(world: World, members: AppMemberService) -> None:  # noqa: F811
    app = world.service.get(await _create(world))
    with pytest.raises(OctopError) as exc:
        members.add_member(
            app,
            user_id=world.alice_id,
            permission="view",
            actor=world.alice_id,
        )
    assert exc.value.code == ErrorCode.APP_SHARE_INVALID

    guest = members.add_guest(app, display_name="Eva", actor=world.alice_id)
    with pytest.raises(OctopError) as exc:
        members.add_member(
            app,
            user_id=guest.user_id,
            permission="view",
            actor=world.alice_id,
        )
    assert exc.value.code == ErrorCode.APP_SHARE_INVALID


async def test_guest_display_name_is_cleaned(world: World, members: AppMemberService) -> None:  # noqa: F811
    app = world.service.get(await _create(world))
    view = members.add_guest(
        app,
        display_name="  E\u0000va\n  ",
        actor=world.alice_id,
    )
    assert view.kind == "guest"
    assert view.display_name == "Eva"
    assert view.username.startswith("guest_")
    assert view.permission == "participate"
    with pytest.raises(OctopError) as exc:
        members.add_guest(app, display_name="Eva", permission="view", actor=world.alice_id)
    assert exc.value.code == ErrorCode.APP_SHARE_INVALID


async def test_invite_redeem_creates_guest_and_consumes_once(
    world: World,  # noqa: F811
    members: AppMemberService,
    clock: Clock,
) -> None:
    app = world.service.get(await _create(world))
    invite, token = members.create_invite(
        app,
        audience="guest",
        permission="participate",
        invitee_hint="Eva",
        ttl_seconds=600,
        max_uses=1,
        actor=world.alice_id,
    )
    assert invite.host_status == "not_required"
    preview = members.preview_invite(token)
    assert (preview.app_name, preview.permission, preview.audience) == (
        "Tracker",
        "participate",
        "guest",
    )

    result = members.redeem_invite(token)
    assert result.status == STATUS_ACTIVE
    assert result.role == "participate"
    assert result.display_name == "Eva"
    assert world.repo.permission_for(app.app_id, result.user_id) == "participate"
    user = world.user_repo.get(result.user_id)
    assert user is not None and user.kind == "guest"

    with pytest.raises(OctopError) as exc:
        members.redeem_invite(token)
    assert exc.value.code == ErrorCode.APP_INVITE_INVALID


async def test_invite_host_approval_and_rejection(
    world: World,  # noqa: F811
    members: AppMemberService,
) -> None:
    app = world.service.get(await _create(world))
    invite, token = members.create_invite(
        app,
        audience="guest",
        permission="participate",
        invitee_hint="Eva",
        ttl_seconds=600,
        max_uses=1,
        actor=world.alice_id,
    )
    repo = AppMemberInviteRepo(world.db)
    repo.set_host_status(app.app_id, invite.invite_id, host_status=HOST_PENDING)

    with pytest.raises(OctopError) as exc:
        members.preview_invite(token)
    assert exc.value.code == ErrorCode.APP_HOST_APPROVAL_REQUIRED

    approved = members.update_invite_host_status(
        app,
        invite_id=invite.invite_id,
        host_status="approved",
    )
    assert approved.host_status == "approved"
    assert members.preview_invite(token).app_id == app.app_id

    repo.set_host_status(app.app_id, invite.invite_id, host_status=HOST_REJECTED)
    with pytest.raises(OctopError) as exc:
        members.preview_invite(token)
    assert exc.value.code == ErrorCode.APP_INVITE_INVALID


async def test_member_invite_requires_sign_in(
    world: World,  # noqa: F811
    members: AppMemberService,
) -> None:
    app = world.service.get(await _create(world))
    _invite, token = members.create_invite(
        app,
        audience="member",
        permission="view",
        invitee_hint="bob",
        ttl_seconds=600,
        max_uses=1,
        actor=world.alice_id,
    )
    with pytest.raises(OctopError) as exc:
        members.redeem_invite(token)
    assert exc.value.code == ErrorCode.AUTH_FAILED

    result = members.redeem_invite(token, user_row=world.user_repo.get(world.bob_id))
    assert result.role == "view"
    assert world.repo.permission_for(app.app_id, world.bob_id) == "view"


async def test_join_code_is_atomic_and_can_require_approval(
    world: World,  # noqa: F811
    members: AppMemberService,
    clock: Clock,
) -> None:
    app = world.service.get(await _create(world))
    _code_row, code = members.create_join_code(
        app,
        audience="guest",
        permission="participate",
        ttl_seconds=600,
        max_uses=1,
        requires_approval=False,
        actor=world.alice_id,
    )
    preview = members.preview_join_code(code.lower())
    assert preview.requires_approval is False
    result = members.redeem_join_code(code.lower())
    assert result.status == STATUS_ACTIVE
    assert world.repo.permission_for(app.app_id, result.user_id) == "participate"
    with pytest.raises(OctopError) as exc:
        members.redeem_join_code(code)
    assert exc.value.code == ErrorCode.APP_JOIN_CODE_INVALID

    _code_row, pending_code = members.create_join_code(
        app,
        audience="guest",
        permission="participate",
        ttl_seconds=600,
        max_uses=1,
        requires_approval=True,
        actor=world.alice_id,
    )
    pending = members.redeem_join_code(pending_code)
    assert pending.status == STATUS_PENDING
    assert world.repo.permission_for(app.app_id, pending.user_id) is None
    assert world.repo.get_collaborator(app.app_id, pending.user_id).status == STATUS_PENDING
    clock.value += 1
    approved = members.update_member(
        app,
        user_id=pending.user_id,
        status=STATUS_ACTIVE,
        actor=world.alice_id,
    )
    assert approved.status == STATUS_ACTIVE
    assert world.repo.permission_for(app.app_id, pending.user_id) == "participate"


async def test_join_code_ttl_and_permissions(
    world: World,  # noqa: F811
    members: AppMemberService,
    clock: Clock,
) -> None:
    app = world.service.get(await _create(world))
    with pytest.raises(OctopError) as exc:
        members.create_join_code(
            app,
            audience="guest",
            permission="view",
            ttl_seconds=600,
            max_uses=1,
            requires_approval=False,
            actor=world.alice_id,
        )
    assert exc.value.code == ErrorCode.APP_SHARE_INVALID

    with pytest.raises(OctopError) as exc:
        members.create_join_code(
            app,
            audience="member",
            permission="edit_data",
            ttl_seconds=600,
            max_uses=1,
            requires_approval=False,
            actor=world.alice_id,
        )
    assert exc.value.code == ErrorCode.APP_SHARE_INVALID

    _row, code = members.create_join_code(
        app,
        audience="guest",
        permission="participate",
        ttl_seconds=60,
        max_uses=1,
        requires_approval=False,
        actor=world.alice_id,
    )
    clock.value += 61
    with pytest.raises(OctopError) as exc:
        members.preview_join_code(code)
    assert exc.value.code == ErrorCode.APP_JOIN_CODE_INVALID
