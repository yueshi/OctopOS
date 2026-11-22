"""Agent-level policy for MiniApps that host external participants.

The agent owner is the host. An app owner who is not the agent owner (or is
not otherwise able to use the agent) must not silently pull external people
into the host's agent. This module is the single place that parses the policy
and answers "is this participant external, and does the host allow them?".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from octop.infra.db.repos.agents import AgentRepo
from octop.infra.errors import ErrorCode, OctopError

ALLOW_EXTERNAL_PARTICIPANTS = "allow_external_participants"
MAX_GUESTS_PER_APP = "max_guests_per_app"
REQUIRE_HOST_APPROVAL = "require_host_approval"

HOST_NOT_REQUIRED = "not_required"
HOST_PENDING = "pending"
HOST_DENIED = "denied"

DEFAULT_MAX_GUESTS = 50
MAX_GUEST_LIMIT = 1000


@dataclass(frozen=True)
class HostPolicy:
    allow_external_participants: bool = False
    max_guests_per_app: int = DEFAULT_MAX_GUESTS
    require_host_approval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                ALLOW_EXTERNAL_PARTICIPANTS: self.allow_external_participants,
                MAX_GUESTS_PER_APP: self.max_guests_per_app,
                REQUIRE_HOST_APPROVAL: self.require_host_approval,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class HostGate:
    allowed: bool
    host_status: str
    policy: HostPolicy


DEFAULT_HOST_POLICY = HostPolicy()


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_guest_limit(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, min(value, MAX_GUEST_LIMIT))


def parse_host_policy(raw: str | None) -> HostPolicy:
    if not raw:
        return DEFAULT_HOST_POLICY
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return DEFAULT_HOST_POLICY
    if not isinstance(data, dict):
        return DEFAULT_HOST_POLICY
    return HostPolicy(
        allow_external_participants=_as_bool(
            data.get(ALLOW_EXTERNAL_PARTICIPANTS),
            DEFAULT_HOST_POLICY.allow_external_participants,
        ),
        max_guests_per_app=_as_guest_limit(
            data.get(MAX_GUESTS_PER_APP),
            DEFAULT_HOST_POLICY.max_guests_per_app,
        ),
        require_host_approval=_as_bool(
            data.get(REQUIRE_HOST_APPROVAL),
            DEFAULT_HOST_POLICY.require_host_approval,
        ),
    )


class HostPolicyService:
    def __init__(self, agent_repo: AgentRepo) -> None:
        self._agent_repo = agent_repo

    def get(self, agent_id: str) -> HostPolicy:
        agent = self._agent_repo.get(agent_id)
        if agent is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return parse_host_policy(agent.external_sharing_policy)

    def update(
        self,
        agent_id: str,
        *,
        allow_external_participants: bool | None = None,
        max_guests_per_app: int | None = None,
        require_host_approval: bool | None = None,
    ) -> HostPolicy:
        current = self.get(agent_id)
        policy = current
        if allow_external_participants is not None:
            policy = replace(policy, allow_external_participants=allow_external_participants)
        if require_host_approval is not None:
            policy = replace(policy, require_host_approval=require_host_approval)
        if max_guests_per_app is not None:
            policy = replace(
                policy,
                max_guests_per_app=max(0, min(int(max_guests_per_app), MAX_GUEST_LIMIT)),
            )
        self._agent_repo.set_external_sharing_policy(agent_id, policy.to_json())
        return policy

    def gate(
        self,
        agent_id: str,
        *,
        user_id: int | None = None,
        guest: bool = False,
    ) -> HostGate:
        """Decide whether a participant needs host approval or is blocked."""
        agent = self._agent_repo.get(agent_id)
        if agent is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        external = (
            guest or user_id is None or not (agent.user_id == user_id or bool(agent.is_shared))
        )
        policy = parse_host_policy(agent.external_sharing_policy)
        if not external:
            return HostGate(True, HOST_NOT_REQUIRED, policy)
        if not policy.allow_external_participants:
            return HostGate(False, HOST_DENIED, policy)
        if policy.require_host_approval:
            return HostGate(True, HOST_PENDING, policy)
        return HostGate(True, HOST_NOT_REQUIRED, policy)

    def guest_limit(self, agent_id: str) -> int:
        return self.get(agent_id).max_guests_per_app


__all__ = [
    "DEFAULT_HOST_POLICY",
    "HOST_DENIED",
    "HOST_NOT_REQUIRED",
    "HOST_PENDING",
    "HostGate",
    "HostPolicy",
    "HostPolicyService",
    "parse_host_policy",
]
