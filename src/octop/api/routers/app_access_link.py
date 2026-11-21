"""One-time MiniApp authenticated links: preview and JWT exchange."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from octop.api.common.client_ip import resolve_client_ip
from octop.api.deps import get_server
from octop.api.routers.auth import _user_json
from octop.infra.apps.limits import public_slot
from octop.infra.apps.service import AppService
from octop.infra.apps.wiring import build_app_service
from octop.infra.auth.tokens import sign_token
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/app-access")

# The exchanged session is app-scoped, so it does not inherit the global
# dashboard TTL. Two hours is enough to work in the app and keeps a forwarded
# link's blast radius small.
APP_ACCESS_SESSION_TTL_SECONDS = 7200


class AppAccessCodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=512, description="One-time access code.")


class AppAccessPreviewOut(BaseModel):
    app_id: str = Field(description="MiniApp public id.")
    app_name: str = Field(description="MiniApp display name.")
    username: str = Field(description="Dashboard user this link signs in as.")
    display_name: str = Field(description="Display name of that user, if set.")
    expires_at: int = Field(description="Unix timestamp when the code expires.")


class AppAccessExchangeOut(BaseModel):
    access_token: str = Field(
        description="App-scoped JWT: only this MiniApp's routes and a few read-only bootstrap calls."
    )
    token_type: str = Field(default="Bearer")
    expires_in: int = Field(description="Access token lifetime in seconds.")
    scope: str = Field(default="app", description="Token scope; always ``app`` for this link.")
    app_id: str = Field(description="MiniApp this session is limited to.")
    user: dict[str, Any] = Field(description="User profile for the signed-in user.")
    redirect: str = Field(description="Dashboard path to open after login.")


def _service(server: OctopServer) -> AppService:
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return build_app_service(
        repos=server.services.repos,
        workspaces=server.app_runtime.agent_registry,
    )


def _invalid() -> OctopError:
    return OctopError.localized(ErrorCode.APP_ACCESS_LINK_INVALID)


def _linked_user(server: OctopServer, user_id: int) -> User:
    if server.user_manager is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    user = server.user_manager.get_by_id(user_id)
    if user is None:
        raise _invalid()
    return user


async def _limit(request: Request) -> AsyncIterator[None]:
    async with public_slot(f"app-access:{resolve_client_ip(request)}"):
        yield


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.post(
    "/preview",
    response_model=AppAccessPreviewOut,
    dependencies=[Depends(_limit)],
    summary="Preview a MiniApp access link",
    description=(
        "Read-only: returns the app name and the Dashboard user the link will "
        "sign in as. Does not consume the code, so link previews are harmless."
    ),
)
async def preview_app_access(
    body: AppAccessCodeBody,
    response: Response,
    server: OctopServer = Depends(get_server),
) -> AppAccessPreviewOut:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    service = _service(server)
    row = service.preview_access_link(body.code)
    if row is None:
        raise _invalid()
    try:
        app = service.get(row.app_id)
    except OctopError as exc:
        raise _invalid() from exc
    user = _linked_user(server, row.user_id)
    _no_store(response)
    return AppAccessPreviewOut(
        app_id=app.app_id,
        app_name=app.name,
        username=user.username,
        display_name=user.display_name or "",
        expires_at=row.expires_at,
    )


@router.post(
    "/exchange",
    response_model=AppAccessExchangeOut,
    dependencies=[Depends(_limit)],
    summary="Exchange a MiniApp access link for an app-scoped session",
    description=(
        "Consumes the one-time code and returns a JWT scoped to this MiniApp: "
        "it can call this app's routes and read-only bootstrap endpoints, but "
        "the rest of the dashboard returns APP_SCOPE_FORBIDDEN. The code is "
        "single-use; a second exchange returns APP_ACCESS_LINK_INVALID."
    ),
)
async def exchange_app_access(
    body: AppAccessCodeBody,
    response: Response,
    server: OctopServer = Depends(get_server),
) -> AppAccessExchangeOut:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    service = _service(server)
    row = service.consume_access_link(body.code)
    if row is None:
        raise _invalid()
    user = _linked_user(server, row.user_id)
    secret = server.services.secret_repo.get("jwt")
    if secret is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
    ttl = APP_ACCESS_SESSION_TTL_SECONDS
    token = sign_token(
        secret,
        sub=user.id,
        uname=user.username,
        role=user.role,
        ttl_seconds=ttl,
        extra={"scope": "app", "app_id": row.app_id, "perm": row.permission},
    )
    server.services.audit_repo.write(
        actor=f"user:{user.id}",
        action="light_app.access_link.exchange",
        target=row.app_id,
        payload=json.dumps(
            {"permission": row.permission, "scope": "app", "ttl_seconds": ttl},
            ensure_ascii=False,
        ),
    )
    _no_store(response)
    return AppAccessExchangeOut(
        access_token=token,
        expires_in=ttl,
        scope="app",
        app_id=row.app_id,
        user=_user_json(user, locale=user.locale),
        redirect=f"/apps/{row.app_id}",
    )
