"""Read-only public surface for light apps.

A publication token is the credential: no JWT, no cookies. The API returns the
app shell and its rows; the host page embeds the shell in the same sandboxed
iframe the dashboard uses, with a bridge that only supports reads.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, Field

from octop.api.common.client_ip import resolve_client_ip
from octop.api.deps import get_server
from octop.api.routers.apps import _columns, _service
from octop.infra.apps.limits import public_slot
from octop.infra.apps.models import MAIN_TABLE_KEY, PII_MASK, validate_html
from octop.infra.apps.sandbox import APP_CSP, build_app_doc, build_wrapper_doc
from octop.infra.errors import ErrorCode, OctopError

router = APIRouter(prefix="/public/apps")
page_router = APIRouter()

_NO_STORE = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

_SHARE_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "public, max-age=300",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
_SHARE_IMAGE_SIZE = 300
# Bump when the rendered share image changes so WeChat re-fetches the URL.
_SHARE_IMAGE_REVISION = "4"
_SHARE_ASSET_NAMES = (
    "share.png",
    "share.jpg",
    "share.jpeg",
    "cover.png",
    "cover.jpg",
    "cover.jpeg",
    "preview.png",
    "preview.jpg",
    "preview.jpeg",
)
_SHARE_ASSET_PREFIXES = ("share", "cover", "preview")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# The 40px header uses a dedicated small-size icon when the app provides one.
_PREVIEW_ASSET_NAMES = ("icon.png", "icon.jpg", "icon.jpeg", "icon.webp")


class PublicAppOut(BaseModel):
    app_id: str
    name: str
    description: str
    html: str
    tables: list[dict[str, Any]]


class PublicServiceCallBody(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class PublicServiceCallOut(BaseModel):
    value: Any
    cached: bool


class PublicRowsOut(BaseModel):
    columns: list[dict[str, Any]]
    rows: list[dict[str, str]]
    total: int
    limit: int
    offset: int
    table: str
    pii_columns: list[str]


def _audit_public(server: Any, app_id: str, action: str) -> None:
    if getattr(server, "services", None) is None:
        return
    server.services.audit_repo.write(actor="_public", action=action, target=app_id)


def _public_key(token: str, request: Request) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{digest}:{resolve_client_ip(request)}"


async def _public_limit(token: str, request: Request) -> AsyncIterator[None]:
    async with public_slot(_public_key(token, request)):
        yield


def _public_app(server: Any, token: str) -> tuple[Any, Any]:
    service = _service(server)
    row = service.resolve_public_app(token)
    if row is None:
        raise OctopError(ErrorCode.APP_NOT_FOUND, "public app not found")
    return service, row


async def _public_payload(service: Any, row: Any) -> tuple[str, list[dict[str, Any]]]:
    """The frozen published version, or the live app for a legacy publication."""
    snapshot = service.public_snapshot(row)
    if snapshot is not None:
        frozen_html, frozen_tables = snapshot
        html = str(frozen_html)
        if html:
            # Snapshots created before serve-time validation may predate the
            # scan; never serve them unchecked.
            validate_html(html)
        return html, list(frozen_tables)
    tables: list[dict[str, Any]] = []
    for table in service.list_tables(row):
        if not table.public_visible or table.row_scope == "user":
            continue
        schema = await service.read_schema(row, table.table_key)
        tables.append(
            {
                "table_key": table.table_key,
                "label": table.label,
                "row_count": table.row_count,
                "is_main": table.is_main,
                "columns": [column.model_dump() for column in _columns(schema)],
            }
        )
    return await service.read_html(row, validate=True) or "", tables


@router.get("/{token}", response_model=PublicAppOut, summary="Get a public app")
async def get_public_app(
    token: str,
    _limit: None = Depends(_public_limit),
    server: Any = Depends(get_server),
) -> JSONResponse:
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_public_view_total")
    service, row = _public_app(server, token)
    _audit_public(server, row.app_id, "light_app.public.view")
    html, tables = await _public_payload(service, row)
    payload = PublicAppOut(
        app_id=row.app_id,
        name=row.name,
        description=row.description,
        html=html,
        tables=tables,
    )
    return JSONResponse(payload.model_dump(), headers=_NO_STORE)


@router.get(
    "/{token}/rows",
    response_model=PublicRowsOut,
    summary="Read a public app table",
)
async def get_public_rows(
    token: str,
    table: str = Query(default=MAIN_TABLE_KEY),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _limit: None = Depends(_public_limit),
    server: Any = Depends(get_server),
) -> JSONResponse:
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_public_view_total")
    service, row = _public_app(server, token)
    snapshot = service.public_snapshot(row)
    snapshot_table: dict[str, Any] | None = None
    if snapshot is not None:
        _html, tables = snapshot
        snapshot_table = next(
            (item for item in tables if item.get("table_key") == table),
            None,
        )
        # The published surface is the snapshot: a table added after publish
        # is not visible until the owner republishes.
        if snapshot_table is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "public app table not found")
    elif not service.is_table_public(row, table):
        raise OctopError(ErrorCode.APP_NOT_FOUND, "public app table not found")

    schema, rows, _meta, total = await service.read_rows_detailed(
        row,
        limit=limit,
        offset=offset,
        table=table,
        redact_pii=True,
    )
    if snapshot_table is not None:
        snapshot_columns = snapshot_table.get("columns")
        if not isinstance(snapshot_columns, list):
            snapshot_columns = []
        live_pii = {column.name: bool(column.pii) for column in schema.columns}
        columns = [
            {
                **column,
                # Tightening PII takes effect immediately; declassifying a
                # column requires republishing the snapshot.
                "pii": bool(column.get("pii")) or live_pii.get(column["name"], False),
            }
            for column in snapshot_columns
            if isinstance(column, dict) and isinstance(column.get("name"), str)
        ]
        allowed = {column["name"] for column in columns}
        pii_columns = {column["name"] for column in columns if column.get("pii")}
        rows = [
            {
                key: PII_MASK if key in pii_columns else value
                for key, value in row.items()
                if key in allowed
            }
            for row in rows
        ]
    else:
        columns = [column.model_dump() for column in _columns(schema)]
    payload = PublicRowsOut(
        columns=columns,
        rows=rows,
        total=total,
        limit=limit,
        offset=offset,
        table=table,
        pii_columns=[column["name"] for column in columns if column.get("pii")],
    )
    return JSONResponse(payload.model_dump(), headers=_NO_STORE)


@router.post(
    "/{token}/services/{name}/call",
    response_model=PublicServiceCallOut,
    summary="Read a cached external service response",
    description=(
        "Public links never call a connector live. This endpoint only returns a "
        "cached response for a service whose declaration has public_live enabled."
    ),
)
async def public_service_call(
    token: str,
    name: str,
    body: PublicServiceCallBody,
    _limit: None = Depends(_public_limit),
    server: Any = Depends(get_server),
) -> PublicServiceCallOut:
    service, row = _public_app(server, token)
    result = service.read_cached_service(
        row,
        name=name,
        tool=body.tool,
        args=body.args,
    )
    return PublicServiceCallOut(value=result.value, cached=result.cached)


@router.get(
    "/{token}/assets/{name}",
    summary="Read a public app asset",
)
async def get_public_asset(
    token: str,
    name: str,
    _limit: None = Depends(_public_limit),
    server: Any = Depends(get_server),
) -> Response:
    service, row = _public_app(server, token)
    try:
        media_type, data = await service.read_asset(row, name)
    except OctopError as exc:
        if exc.code == ErrorCode.APP_SOURCE_INVALID:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND) from exc
        raise
    return Response(content=data, media_type=media_type, headers=_NO_STORE)


@router.get(
    "/{token}/share.png",
    include_in_schema=False,
)
async def get_public_share_image(
    token: str,
    _limit: None = Depends(_public_limit),
    server: Any = Depends(get_server),
) -> Response:
    """Public share thumbnail for WeChat/OG cards (PNG, no JWT)."""
    service, row = _public_app(server, token)
    data = await _share_image_bytes(service, row)
    return Response(content=data, media_type="image/png", headers=_SHARE_HEADERS)


_SDK = """
(function () {
  var pending = new Map();
  var seq = 0;
  function call(method, params) {
    return new Promise(function (resolve, reject) {
      var id = ++seq;
      pending.set(id, { resolve: resolve, reject: reject });
      parent.postMessage(
        { source: "octop-app", type: "request", id: id, method: method, params: params || {} },
        "*"
      );
    });
  }
  window.addEventListener("message", function (event) {
    var data = event.data;
    if (!data || data.source !== "octop-app-host") return;
    var entry = pending.get(data.id);
    if (!entry) return;
    pending.delete(data.id);
    if (data.ok) entry.resolve(data.data);
    else entry.reject(new Error(data.error || "Request failed"));
  });
  window.octopApp = {
    schema: function (table) { return call("schema", { table: table || null }); },
    tables: function () { return call("tables"); },
    listRows: function (options) { return call("rows", options || {}); },
    me: function () { return Promise.resolve({ permission: "view" }); },
    asset: function (name) { return call("asset", { name: name || "" }); },
    service: function (name) {
      return {
        call: function (tool, args) {
          return call("service", { name: name || "", tool: tool || "", args: args || {} });
        }
      };
    },
    appendRow: function () {
      return Promise.reject(new Error("This is a read-only public link"));
    },
    updateRow: function () {
      return Promise.reject(new Error("This is a read-only public link"));
    },
    updateRows: function () {
      return Promise.reject(new Error("This is a read-only public link"));
    },
    deleteRow: function () {
      return Promise.reject(new Error("This is a read-only public link"));
    },
    generate: function () {
      return Promise.reject(new Error("This is a read-only public link"));
    },
    launchGame: function () {
      return Promise.reject(new Error("This is a read-only public link"));
    }
  };
})();
"""

_CSP = APP_CSP


def _with_head(html: str) -> str:
    """Inject the app CSP, SDK and pre-script freeze into the app document."""
    head = f'<meta http-equiv="Content-Security-Policy" content="{_CSP}"><script>{_SDK}</script>'
    return build_app_doc(html, head)


def _public_origin(server: Any, request: Request) -> str:
    base = (getattr(getattr(server, "config", None), "public_base_url", "") or "").strip()
    base = base.rstrip("/")
    if base.startswith(("http://", "https://")):
        return base
    return str(request.base_url).rstrip("/")


def _meta_text(value: object, fallback: str = "") -> str:
    text = " ".join(str(value or fallback).split())
    return _escape(text)


def _pick_share_asset(names: list[str]) -> str:
    by_lower = {name.lower(): name for name in names}
    for preferred in _SHARE_ASSET_NAMES:
        if preferred in by_lower:
            return by_lower[preferred]
    for name in names:
        lower = name.lower()
        if lower.startswith(_SHARE_ASSET_PREFIXES) and lower.endswith(_IMAGE_SUFFIXES):
            return name
    return ""


def _pick_preview_asset(names: list[str]) -> str:
    by_lower = {name.lower(): name for name in names}
    for preferred in _PREVIEW_ASSET_NAMES:
        if preferred in by_lower:
            return by_lower[preferred]
    return ""


def _render_share_image(data: bytes) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            frame = source.convert("RGB")
            # Preserve the whole cover. Cropping a 1200x630 cover to a square
            # can cut away the title and leave a blank center thumbnail.
            corner = frame.getpixel((0, 0))
            background = corner if isinstance(corner, tuple) else (255, 255, 255)
            frame = ImageOps.pad(
                frame,
                (_SHARE_IMAGE_SIZE, _SHARE_IMAGE_SIZE),
                method=Image.Resampling.LANCZOS,
                color=background,
            )
            output = io.BytesIO()
            frame.save(output, format="PNG")
            return output.getvalue()
    except Exception:
        return None


@lru_cache(maxsize=1)
def _default_share_image() -> bytes:
    size = _SHARE_IMAGE_SIZE
    canvas = Image.new("RGB", (size, size), (15, 23, 42))
    draw = ImageDraw.Draw(canvas)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line(
            (0, y, size, y),
            fill=(int(15 + 18 * t), int(23 + 28 * t), int(42 + 36 * t)),
        )
    draw.rounded_rectangle(
        (70, 70, size - 70, size - 70),
        radius=48,
        outline=(45, 212, 191),
        width=6,
    )
    draw.polygon([(270, 210), (270, 430), (450, 320)], fill=(45, 212, 191))
    draw.ellipse((440, 420, 520, 500), fill=(56, 189, 248))
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


async def _share_image_bytes(service: Any, row: Any) -> bytes:
    try:
        names = await service.list_assets(row)
    except Exception:
        names = []
    asset = _pick_share_asset(names)
    if asset:
        try:
            _mime, data = await service.read_asset(row, asset)
            rendered = _render_share_image(data)
            if rendered is not None:
                return rendered
        except Exception:
            pass
    return _default_share_image()


def _host_page(
    token: str,
    app: PublicAppOut,
    *,
    base_url: str,
    version: str,
    preview_asset: str = "",
) -> str:
    # The app runs inside the wrapper's own frame. The wrapper declares
    # `frame-src 'none'`, which is what stops the app from navigating itself
    # away and taking the bridge with it.
    wrapper_doc = build_wrapper_doc(_with_head(app.html))
    # Escape "</" so a script tag inside the app cannot close the host script.
    wrapper_json = json.dumps(wrapper_doc).replace("</", "<\\/")
    token_json = json.dumps(token)
    title = _meta_text(app.name, "MiniApp")
    description = _meta_text(app.description, "Open this MiniApp")
    share_version = f"{quote(version, safe='')}-{_SHARE_IMAGE_REVISION}"
    page_url = f"{base_url}/p/{quote(token, safe='')}?v={share_version}"
    image_url = f"{base_url}/api/public/apps/{quote(token, safe='')}/share.png?v={share_version}"
    preview_url = (
        f"{base_url}/api/public/apps/{quote(token, safe='')}/assets/"
        f"{quote(preview_asset, safe='')}?v={share_version}"
        if preview_asset
        else ""
    )
    cover_url = preview_url or image_url
    cover_html = (
        f'<img src="{_escape(cover_url)}" width="300" height="300" alt="{title}" itemprop="image">'
    )
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Octop">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{_escape(page_url)}">
<meta property="og:image" content="{_escape(image_url)}">
<meta property="og:image:secure_url" content="{_escape(image_url)}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="{_SHARE_IMAGE_SIZE}">
<meta property="og:image:height" content="{_SHARE_IMAGE_SIZE}">
<meta property="og:image:alt" content="{title}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{_escape(image_url)}">
<link rel="image_src" href="{_escape(image_url)}">
<style>
html,body{{margin:0;height:100%;background:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}}
body{{display:flex;flex-direction:column}}
.app-head{{display:flex;align-items:center;gap:10px;flex:0 0 auto;padding:8px 12px;border-bottom:1px solid #e5e7eb;background:#fff}}
.app-cover{{position:relative;display:block;flex:0 0 auto;width:40px;height:40px;overflow:hidden;border-radius:9px;background:#0f172a}}
.app-cover img{{display:block;width:300px;height:300px;object-fit:cover;transform:scale(0.133333);transform-origin:top left}}
.app-meta{{display:flex;flex-direction:column;min-width:0;line-height:1.25}}
.app-meta strong,.app-meta small{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.app-meta strong{{font-size:14px;color:#0f172a}}
.app-meta small{{font-size:12px;color:#64748b}}
iframe{{flex:1 1 auto;width:100%;min-height:0;border:0;display:block}}
</style>
</head><body>
<noscript><img src="{_escape(image_url)}" width="300" height="300" alt="{title}" style="display:block;width:300px;height:300px"></noscript>
<header class="app-head">
  <span class="app-cover">{cover_html}</span>
  <span class="app-meta"><strong>{title}</strong><small>{description}</small></span>
</header>
<iframe id="wrapper" sandbox="allow-scripts"></iframe>
<script>
const token = {token_json};
const wrapper = document.getElementById("wrapper");
wrapper.srcdoc = {wrapper_json};
window.addEventListener("message", async function (event) {{
  // Only the wrapper may drive this bridge. Without the source check any
  // window could post a request here.
  if (event.source !== wrapper.contentWindow) return;
  const data = event.data;
  if (!data || data.source !== "octop-app" || data.type !== "request" || typeof data.id !== "number") return;
  const reply = (payload) => wrapper.contentWindow.postMessage(Object.assign({{source: "octop-app-host", id: data.id}}, payload), "*");
  try {{
    if (data.method === "tables") {{
      const res = await fetch("/api/public/apps/" + encodeURIComponent(token), {{cache: "no-store"}});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const app = await res.json();
      reply({{ok: true, data: {{tables: app.tables.map((t) => ({{table_key: t.table_key, label: t.label, row_count: t.row_count, is_main: t.is_main}}))}}}});
    }} else if (data.method === "schema" || data.method === "rows") {{
      const table = data.params?.table || "main";
      const qs = new URLSearchParams({{table: table, limit: String(data.params?.limit || 200), offset: String(data.params?.offset || 0)}});
      const res = await fetch("/api/public/apps/" + encodeURIComponent(token) + "/rows?" + qs.toString(), {{cache: "no-store"}});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const body = await res.json();
      if (data.method === "schema") reply({{ok: true, data: {{table: table, columns: body.columns}}}});
      else reply({{ok: true, data: body}});
    }} else if (data.method === "service") {{
      const name = String(data.params?.name || "");
      const res = await fetch("/api/public/apps/" + encodeURIComponent(token) + "/services/" + encodeURIComponent(name) + "/call", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{tool: data.params?.tool || "", args: data.params?.args || {{}}}}),
        cache: "no-store"
      }});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const body = await res.json();
      reply({{ok: true, data: body.value}});
    }} else if (data.method === "asset") {{
      const name = String(data.params?.name || "");
      const res = await fetch("/api/public/apps/" + encodeURIComponent(token) + "/assets/" + encodeURIComponent(name), {{cache: "no-store"}});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const blob = await res.blob();
      const dataUrl = await new Promise((resolve, reject) => {{
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
      }});
      reply({{ok: true, data: dataUrl}});
    }} else {{
      reply({{ok: false, error: "unsupported method"}});
    }}
  }} catch (err) {{
    reply({{ok: false, error: err instanceof Error ? err.message : String(err)}});
  }}
}});
</script></body></html>"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


@page_router.get("/p/{token}", response_class=HTMLResponse, include_in_schema=False)
async def public_app_page(
    token: str,
    request: Request,
    _limit: None = Depends(_public_limit),
    server: Any = Depends(get_server),
) -> HTMLResponse:
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_public_view_total")
    service, row = _public_app(server, token)
    html, tables = await _public_payload(service, row)
    try:
        preview_asset = _pick_preview_asset(await service.list_assets(row))
    except Exception:
        preview_asset = ""
    app = PublicAppOut(
        app_id=row.app_id,
        name=row.name,
        description=row.description,
        html=html,
        tables=tables,
    )
    return HTMLResponse(
        _host_page(
            token,
            app,
            base_url=_public_origin(server, request),
            version=str(getattr(row, "updated_at", "") or "1"),
            preview_asset=preview_asset,
        ),
        headers=_NO_STORE,
    )
