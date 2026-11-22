"""Agent host policy parsing and external-participant gating."""

from __future__ import annotations

import pytest

from octop.infra.apps.host_policy import (
    HOST_DENIED,
    HOST_NOT_REQUIRED,
    HOST_PENDING,
    HostPolicyService,
    parse_host_policy,
)
from octop.infra.errors import ErrorCode, OctopError
from tests.unit.apps.test_service import World, world  # noqa: F401


def test_parse_defaults_and_partial_json() -> None:
    default = parse_host_policy("{}")
    assert default.allow_external_participants is False
    assert default.max_guests_per_app == 50
    assert default.require_host_approval is True

    partial = parse_host_policy('{"allow_external_participants": true}')
    assert partial.allow_external_participants is True
    assert partial.require_host_approval is True

    invalid = parse_host_policy("not json")
    assert invalid == default


def test_update_clamps_and_persists(world: World) -> None:  # noqa: F811
    service = HostPolicyService(world.agent_repo)
    updated = service.update(
        world.agent_id,
        allow_external_participants=True,
        max_guests_per_app=5000,
        require_host_approval=False,
    )
    assert updated.allow_external_participants is True
    assert updated.max_guests_per_app == 1000
    assert updated.require_host_approval is False
    assert service.get(world.agent_id) == updated

    negative = service.update(world.agent_id, max_guests_per_app=-3)
    assert negative.max_guests_per_app == 0

    with pytest.raises(OctopError) as exc:
        service.get("missing")
    assert exc.value.code is ErrorCode.APP_NOT_FOUND


def test_gate_internal_external_and_approval(world: World) -> None:  # noqa: F811
    service = HostPolicyService(world.agent_repo)

    owner = service.gate(world.agent_id, user_id=world.alice_id)
    assert (owner.allowed, owner.host_status) == (True, HOST_NOT_REQUIRED)

    denied = service.gate(world.agent_id, guest=True)
    assert (denied.allowed, denied.host_status) == (False, HOST_DENIED)

    service.update(
        world.agent_id,
        allow_external_participants=True,
        require_host_approval=True,
    )
    pending_guest = service.gate(world.agent_id, guest=True)
    assert (pending_guest.allowed, pending_guest.host_status) == (True, HOST_PENDING)
    pending_member = service.gate(world.agent_id, user_id=world.bob_id)
    assert (pending_member.allowed, pending_member.host_status) == (True, HOST_PENDING)

    service.update(world.agent_id, require_host_approval=False)
    assert service.gate(world.agent_id, guest=True).host_status == HOST_NOT_REQUIRED

    # Sharing the agent makes another member internal.
    world.agent_repo.set_shared(world.agent_id, True)
    internal = service.gate(world.agent_id, user_id=world.bob_id)
    assert (internal.allowed, internal.host_status) == (True, HOST_NOT_REQUIRED)
