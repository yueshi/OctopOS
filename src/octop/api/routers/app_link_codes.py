"""Mint the one-time code for the WebView-to-IM ``/link`` handshake."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.apps.service import AppService
from octop.infra.apps.wiring import build_app_service
from octop.infra.auth.channel_binding import LINK_CODE_TTL_SECONDS, issue_link_code
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/apps")


class LinkCodeOut(BaseModel):
    code: str = Field(description="Send this code to the bot as /link <code> in a private chat.")
    expires_at: int = Field(description="Unix timestamp when the code stops working.")
    ttl_seconds: int = Field(description="Lifetime of the code in seconds.")


def _service(server: OctopServer) -> AppService:
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return build_app_service(
        repos=server.services.repos,
        workspaces=server.app_runtime.agent_registry,
    )


@router.post(
    "/{app_id}/link-code",
    response_model=LinkCodeOut,
    summary="Create an IM link code",
    description=(
        "Mints a short one-time code so a signed-in MiniApp participant can "
        "bind their private IM chat to this guest/member account with `/link`. "
        "The code is single-use and expires after a few minutes."
    ),
)
async def create_app_link_code(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> LinkCodeOut:
    service = _service(server)
    row = service.get(app_id)
    permission = service.permission_for(
        row,
        user.id,
        is_admin=service.is_admin(user.id),
    )
    if permission is None:
        raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
    assert server.services is not None
    code, expires_at = issue_link_code(
        link_code_repo=server.services.channel_link_code_repo,
        user_id=user.id,
        app_id=row.app_id,
    )
    server.services.audit_repo.write(
        actor=f"user:{user.id}",
        action="light_app.link_code.create",
        target=row.app_id,
        payload="",
    )
    return LinkCodeOut(
        code=code,
        expires_at=expires_at,
        ttl_seconds=LINK_CODE_TTL_SECONDS,
    )
