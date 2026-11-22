"""IM identity bindings for the signed-in user.

A binding proves that a private-chat subject belongs to an Octop account. It is
what lets ``app_share_link`` issue a personal MiniApp sign-in link instead of
the read-only public link; the code is typed once into the bot chat.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.auth.channel_binding import BIND_CODE_TTL_SECONDS, issue_bind_code
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/channel-bindings")


class ChannelBindCodeOut(BaseModel):
    code: str = Field(description="One-time code to send to the bot as /bind <code>.")
    expires_at: int = Field(description="Unix timestamp when the code stops working.")
    ttl_seconds: int = Field(description="Lifetime of the code in seconds.")


class ChannelIdentityOut(BaseModel):
    channel_type: str = Field(description="Channel type, e.g. weixin or feishu.")
    subject_id: str = Field(description="Platform sender id bound to this account.")
    account_key: str = Field(
        default="",
        description="Bot/account key; empty for bindings created before identity v2.",
    )
    verified_at: int = Field(description="Unix timestamp when the binding was verified.")


class ChannelIdentityListOut(BaseModel):
    identities: list[ChannelIdentityOut]


def _repos(server: OctopServer) -> RepoBundle:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return server.services.repos


@router.post(
    "/code",
    response_model=ChannelBindCodeOut,
    summary="Create a one-time IM bind code",
    description=(
        "The signed-in user sends the returned code to an agent's IM bot in a "
        "private chat as `/bind <code>`. The binding is single-use, expires "
        f"after {BIND_CODE_TTL_SECONDS // 60} minutes, and only proves the IM "
        "account belongs to this user; it does not change who owns existing "
        "agent sessions."
    ),
)
async def create_channel_bind_code(
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ChannelBindCodeOut:
    repos = _repos(server)
    code, expires_at = issue_bind_code(code_repo=repos.channel_bind_code_repo, user_id=user.id)
    repos.audit_repo.write(
        actor=f"user:{user.id}",
        action="auth.channel_bind_code.issue",
        target=str(user.id),
        payload="",
    )
    return ChannelBindCodeOut(
        code=code,
        expires_at=expires_at,
        ttl_seconds=BIND_CODE_TTL_SECONDS,
    )


@router.get(
    "",
    response_model=ChannelIdentityListOut,
    summary="List my bound IM identities",
)
async def list_channel_bindings(
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ChannelIdentityListOut:
    rows = _repos(server).channel_identity_repo.list_for_user(user.id)
    return ChannelIdentityListOut(
        identities=[
            ChannelIdentityOut(
                channel_type=row.channel_type,
                subject_id=row.sender_id,
                account_key=row.account_key,
                verified_at=row.verified_at,
            )
            for row in rows
        ]
    )


@router.delete(
    "",
    status_code=204,
    summary="Unbind one of my IM identities",
)
async def delete_channel_binding(
    channel_type: str = Query(min_length=1, max_length=64),
    subject_id: str = Query(min_length=1, max_length=256),
    account_key: str = Query(default="", max_length=256),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> None:
    repos = _repos(server)
    removed = repos.channel_identity_repo.delete(
        channel_type=channel_type.strip().lower(),
        sender_id=subject_id.strip(),
        account_key=account_key.strip(),
        user_id=user.id,
    )
    if not removed:
        raise OctopError(ErrorCode.NOT_FOUND, "binding not found")
    repos.audit_repo.write(
        actor=f"user:{user.id}",
        action="auth.channel_identity.unbind",
        target=f"{channel_type}:{subject_id}",
        payload="",
    )
