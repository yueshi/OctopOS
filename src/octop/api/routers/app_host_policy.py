"""Agent-level external sharing policy for hosted MiniApps."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.api.routers.apps import _audit
from octop.infra.apps.host_policy import (
    MAX_GUEST_LIMIT,
    HostPolicy,
    HostPolicyService,
    parse_host_policy,
)
from octop.infra.db.repos.agents import AgentRow
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/agents")


class ExternalSharingOut(BaseModel):
    allow_external_participants: bool
    max_guests_per_app: int
    require_host_approval: bool
    can_manage: bool = Field(
        default=False,
        description="True when the caller is the agent owner or an admin.",
    )


class ExternalSharingBody(BaseModel):
    allow_external_participants: bool | None = Field(
        default=None,
        description="Whether app owners may add guests or members outside the agent.",
    )
    max_guests_per_app: int | None = Field(
        default=None,
        ge=0,
        le=MAX_GUEST_LIMIT,
        description="Guest member cap per MiniApp.",
    )
    require_host_approval: bool | None = Field(
        default=None,
        description="Whether external participants need the agent owner's approval.",
    )


def _services(server: OctopServer) -> SharedServices:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return server.services


def _agent(server: OctopServer, agent_id: str) -> AgentRow:
    agent = _services(server).agent_repo.get(agent_id)
    if agent is None:
        raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
    return agent


def _can_read(server: OctopServer, user: User, agent_id: str, agent_owner_id: int | None) -> bool:
    if user.is_admin or agent_owner_id == user.id:
        return True
    # App owners need to understand why their invites are blocked.
    return any(
        app.owner_user_id == user.id
        for app in _services(server).light_app_repo.list_by_agent(agent_id)
    )


def _out(policy: HostPolicy, *, can_manage: bool = False) -> ExternalSharingOut:
    return ExternalSharingOut(
        allow_external_participants=policy.allow_external_participants,
        max_guests_per_app=policy.max_guests_per_app,
        require_host_approval=policy.require_host_approval,
        can_manage=can_manage,
    )


@router.get(
    "/{agent_id}/external-sharing",
    response_model=ExternalSharingOut,
    summary="Read external sharing policy",
    description=(
        "Returns the host policy for MiniApps on this agent. Visible to the "
        "agent owner, admins and app owners hosted on the agent."
    ),
)
async def get_external_sharing(
    agent_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ExternalSharingOut:
    agent = _agent(server, agent_id)
    if not _can_read(server, user, agent_id, agent.user_id):
        raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
    return _out(
        parse_host_policy(agent.external_sharing_policy),
        can_manage=user.is_admin or agent.user_id == user.id,
    )


@router.patch(
    "/{agent_id}/external-sharing",
    response_model=ExternalSharingOut,
    summary="Update external sharing policy",
    description="Only the agent owner or an admin may change this policy.",
)
async def update_external_sharing(
    agent_id: str,
    body: ExternalSharingBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ExternalSharingOut:
    agent = _agent(server, agent_id)
    if not user.is_admin and agent.user_id != user.id:
        raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
    policy = HostPolicyService(_services(server).agent_repo).update(
        agent_id,
        allow_external_participants=body.allow_external_participants,
        max_guests_per_app=body.max_guests_per_app,
        require_host_approval=body.require_host_approval,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.host_policy.update",
        target=agent_id,
        payload=_out(policy).model_dump(),
    )
    return _out(policy, can_manage=True)
