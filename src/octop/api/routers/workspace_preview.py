"""Token-authenticated workspace HTML preview mount.

The dashboard renders agent-generated HTML inside a sandboxed iframe. Iframes
cannot send the dashboard JWT, so the preview URL carries a short-lived,
read-only capability token scoped to one agent and one workspace directory
(:mod:`octop.infra.agents.workspace.preview_tokens`).

The mount is JWT-exempt on purpose: the preview token is the credential. The
route re-checks agent access on every request, so revoking a share or changing
ownership invalidates outstanding tokens immediately.
"""

from __future__ import annotations

import mimetypes
import re
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from octop.api.common.agent import require_agent_row
from octop.api.common.content_disposition import content_disposition
from octop.api.common.workspace import normalize_preview_rel
from octop.api.deps import get_server
from octop.infra.agents.workspace.preview_tokens import decode_workspace_preview_token
from octop.infra.errors import ErrorCode, OctopError

router = APIRouter()

# Host header is injected into a CSP host-source. Keep the grammar to literal
# host / IPv6 + optional port; anything else falls back to the stricter policy.
_HOST_RE = re.compile(r"^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")
_INLINE_MIME_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/pdf",
        "application/wasm",
    }
)
_INLINE_MIME_PREFIXES = ("text/", "image/", "font/", "audio/", "video/")


def _csp_host(request: Request) -> str:
    host = (request.headers.get("host") or "").strip()
    return host if _HOST_RE.fullmatch(host) else ""


def _html_csp(host: str) -> str:
    """CSP for the preview document (opaque origin via the sandbox directive)."""
    allowed = f" {host}" if host else ""
    return (
        "sandbox allow-scripts allow-forms; "
        "default-src 'none'; "
        f"script-src 'unsafe-inline'{allowed}; "
        f"style-src 'unsafe-inline'{allowed}; "
        f"img-src{allowed} data: blob:; "
        f"font-src{allowed} data:; "
        f"media-src{allowed} data: blob:; "
        "connect-src 'none'; worker-src 'none'; frame-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'"
    )


def _media_type(mime: str) -> str:
    if mime.startswith("text/") or mime in {
        "application/javascript",
        "application/json",
        "image/svg+xml",
    }:
        return f"{mime}; charset=utf-8"
    return mime


def _is_inline(mime: str) -> bool:
    return mime.startswith(_INLINE_MIME_PREFIXES) or mime in _INLINE_MIME_TYPES


def _preview_headers(*, rel: str, mime: str, request: Request) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }
    if _is_inline(mime):
        headers["Content-Disposition"] = "inline"
    else:
        headers["Content-Disposition"] = content_disposition(rel.rsplit("/", 1)[-1])
    if mime == "text/html":
        headers["Content-Security-Policy"] = _html_csp(_csp_host(request))
    elif mime == "image/svg+xml":
        # A direct navigation to a generated SVG must not run scripts on the
        # API origin; image-embedded SVGs remain unaffected.
        headers["Content-Security-Policy"] = "sandbox"
    return headers


def _decode_token(server: Any, token: str) -> dict[str, Any]:
    assert server.services is not None
    secret = server.services.secret_repo.get("jwt")
    if secret is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
    try:
        return decode_workspace_preview_token(secret, token)
    except jwt.ExpiredSignatureError as exc:
        raise OctopError(ErrorCode.TOKEN_EXPIRED, "preview token expired") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise OctopError(ErrorCode.AUTH_FAILED, "invalid preview token") from exc


@router.get(
    "/workspace-preview/{token}/{path:path}",
    summary="Serve a workspace HTML preview",
    description=(
        "Token-authenticated, read-only file mount used by the desktop HTML viewer. "
        "The token scopes access to one agent and one workspace directory; HTML is "
        "served with a sandboxing CSP and relative assets resolve under the same "
        "token path. Never proxy this route through a JWT-protected caller."
    ),
)
async def serve_workspace_preview(
    token: str,
    path: str,
    request: Request,
    server: Any = Depends(get_server),
) -> Response:
    """Serve one file from the token's workspace directory."""
    payload = _decode_token(server, token)
    agent_id = str(payload["agent_id"])
    assert server.user_manager is not None
    user = server.user_manager.get_by_id(int(payload["user_id"]))
    if user is None:
        raise OctopError(ErrorCode.USER_DISABLED, "user not active")
    require_agent_row(
        agent_id,
        user=user,
        as_user=payload.get("as_user"),
        server=server,
    )

    assert server.app_runtime is not None
    workspace = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
    if workspace is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")

    rel_parts = normalize_preview_rel(path).split("/")
    root_raw = str(payload["root"])
    root_parts = [] if root_raw == "." else normalize_preview_rel(root_raw).split("/")
    rel = "/".join([*root_parts, *rel_parts])

    try:
        data = await workspace.adownload_bytes(rel)
    except PermissionError as exc:
        raise OctopError(ErrorCode.FORBIDDEN, f"cannot read {path!r}") from exc
    if data is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot read {path!r}")

    mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    return Response(
        content=data,
        media_type=_media_type(mime),
        headers=_preview_headers(rel=rel, mime=mime, request=request),
    )
