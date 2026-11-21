"""Built-in LangChain tools for agent-managed MiniApps."""

from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import Field

from octop.infra.agents.tool_scope import current_tool_context, current_tool_scope
from octop.infra.apps import capabilities as app_capabilities
from octop.infra.apps.integration_ops import (
    integration_health,
    integration_inventory,
    integration_trace,
)
from octop.infra.apps.models import MAIN_TABLE_KEY
from octop.infra.apps.pii import detect_text_pii, find_unmarked_pii
from octop.infra.apps.service import (
    FILE_PREVIEW,
    MAX_ASSET_BYTES,
    AppService,
    AppTableView,
    app_path,
)
from octop.infra.apps.wiring import build_ingress_service, build_webhook_service
from octop.infra.db.repos.apps import PERMISSION_RANK, LightAppRow
from octop.infra.db.services import RepoBundle

APP_DELETE_TOOL_NAME = "app_delete"

APP_TOOL_NAMES: tuple[str, ...] = (
    "app_inspect_file",
    "app_propose_plan",
    "app_get_plan",
    "app_cancel_plan",
    "app_list",
    "app_list_bindings",
    "app_add_service",
    "app_list_services",
    "app_remove_service",
    "app_open",
    "app_share_link",
    "app_create",
    "app_create_from_template",
    "app_add_table",
    "app_update_schema",
    "app_alter_schema",
    "app_set_table_visibility",
    "app_set_capabilities",
    "app_write_html",
    "app_read_data",
    "app_append_row",
    "app_update_row",
    "app_delete_row",
    "app_import_rows",
    "app_save_asset",
    "app_set_preview",
    "app_integration_list",
    "app_integration_health",
    "app_integration_trace",
    "app_integration_create_ingress",
    "app_integration_update_ingress",
    "app_integration_delete_ingress",
    "app_integration_test_ingress",
    "app_integration_create_webhook",
    "app_integration_update_webhook",
    "app_integration_delete_webhook",
    "app_integration_test_webhook",
    APP_DELETE_TOOL_NAME,
)
"""Names of the built-in light-app tools, in the order they are registered."""

_COLUMNS_HELP = (
    "Column definitions. Each item: "
    '{"name": "snake_case identifier", "label": "display label", '
    '"type": "string|text|integer|number|boolean|date|datetime|enum|reference", '
    '"required": false, "values": ["only for enum"], '
    '"ref_table": "target table, only for reference", '
    '"ref_label": "target display column, only for reference", '
    '"pii": "required for sensitive data: id_card|bank_card|phone|address|salary|email|secret"}. '
    "A reference column stores the target row_id; write/import values must be "
    "row ids that already exist in ref_table. "
    "Up to 50 columns; names must start with a letter or underscore. "
    "Never put a phone, ID, bank card, email or address in a free-text/note "
    "column: mark the column pii instead."
)

_TABLE_HELP = (
    "Which table to use. Every app has a main table named 'main'; "
    "tables created with app_add_table use their own key. "
    "Omit it to read or write the main table."
)

_HTML_HELP = (
    "Complete single-file HTML with inline CSS/JS. It renders inside a sandboxed "
    "iframe (scripts allowed, no same-origin access and no network fetch). "
    "Inside the page use the injected `window.octopApp` SDK: "
    "`await octopApp.schema(table)` returns the column definitions; "
    "`await octopApp.listRows({table, limit, offset})` returns {rows, total}; "
    "`await octopApp.tables()` returns the tables visible to the current viewer; "
    "render tabs from that response instead of hardcoding table keys, because "
    "public links omit private tables. "
    "`await octopApp.appendRow({column: value}, {table})` adds a row when the "
    "viewer has edit permission; `await octopApp.updateRow(rowId, values, "
    "{table, expectedVersion})` and `updateRows(rows, {table})` edit existing "
    "rows, and `deleteRow(rowId, {table, expectedVersion})` deletes one. "
    "The row_id comes from the `row_meta` array returned by listRows(). "
    "Remote scripts/styles/images/fonts are blocked, "
    "so keep CSS/JS inline and use data: or blob: URLs for images; relative URLs "
    "and fetch() to the platform API are not allowed. Call octopApp.listRows() on "
    "load to render current data."
)


def _tool_ctx() -> tuple[str, int]:
    scope = current_tool_scope()
    if scope is not None:
        return scope
    cfg = _tool_config()
    agent_id = cfg.get("agent_id")
    user_raw = cfg.get("user")
    if not agent_id:
        raise ValueError("missing configurable.agent_id")
    if user_raw is None:
        raise ValueError("missing configurable.user")
    return str(agent_id), int(user_raw)


def _tool_config() -> dict[str, Any]:
    """Harness ``configurable`` plus the HITL-resume channel scope.

    A resume invocation carries only ``thread_id``; the manager restores the
    original IM context through :func:`current_tool_context`.
    """
    try:
        config = dict(get_config().get("configurable") or {})
    except RuntimeError:
        config = {}
    scope = current_tool_context()
    if scope is not None:
        if not str(config.get("octop_source") or "").strip() and scope.source:
            config["octop_source"] = scope.source
        if not str(config.get("session_key") or "").strip() and scope.session_key:
            config["session_key"] = scope.session_key
        if scope.identity_verified and not config.get("octop_identity_verified"):
            config["octop_identity_verified"] = True
    return config


def _tool_source() -> str:
    """Channel source for the current turn, e.g. ``weixin/01M5...``."""
    return str(_tool_config().get("octop_source") or "").strip()


def _is_dashboard_source(source: str) -> bool:
    return not source or source.split("/", 1)[0] in {"dashboard", "web"}


def _tool_identity_verified() -> bool:
    return bool(_tool_config().get("octop_identity_verified"))


def _session_chat_type() -> str:
    """Chat type from the session key, with the HITL-scope value as fallback."""
    session_key = str(_tool_config().get("session_key") or "")
    if session_key:
        _subject, _, chat_type = session_key.rpartition(":")
        if chat_type:
            return chat_type.strip().lower()
    scope = current_tool_context()
    return scope.chat_type.strip().lower() if scope is not None else ""


def _source_channel_type(source: str) -> str:
    return source.split("/", 1)[0].strip().lower() if source else ""


def _current_thread_id() -> str | None:
    thread_id = _tool_config().get("thread_id")
    return str(thread_id).strip() or None if thread_id else None


def _reject_if_plan_app(service: AppService, app_id: str) -> None:
    """Keep the plan's app untouched by direct writes until the plan executes."""
    plan = service.active_setup_plan(_current_thread_id())
    if plan is not None and plan.app_id == app_id:
        raise ValueError(
            "this app is owned by an active MiniApp setup plan; use the plan card to import data"
        )


def _setup_plan_payload(plan: Any, *, include_report: bool = False) -> dict[str, Any]:
    raw_table = plan.table if isinstance(plan.table, dict) else {}
    tables = raw_table.get("tables")
    if not isinstance(tables, list) or not tables:
        tables = [raw_table] if raw_table.get("columns") else []
    data: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "status": plan.status,
        "revision": plan.revision,
        "name": plan.name,
        "app_id": plan.app_id,
        "file": plan.source_path,
        "table": plan.table,
        "tables": tables,
        "services": raw_table.get("services") or [],
        "capabilities": raw_table.get("capabilities"),
        "dry_run": plan.dry_run,
        "result": plan.result,
        "error": plan.error,
        "expires_at": plan.expires_at,
    }
    if include_report:
        data["report"] = plan.report
    return data


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


class AppAccessError(ValueError):
    """Permission failure carrying a machine-readable card payload."""

    def __init__(self, message: str, *, code: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = {**data, "code": code, "message": message}


def _err(exc: Exception) -> str:
    if isinstance(exc, AppAccessError):
        return json.dumps(
            {
                "octop_ui": {"renderer": "app_permission_card", "version": 1},
                "data": exc.data,
                "error": str(exc),
            },
            ensure_ascii=False,
            indent=2,
        )
    return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _access_error(
    service: AppService,
    *,
    agent_id: str,
    app_id: str | None,
    user_id: int,
    needed: str,
    code: str,
    message: str,
) -> AppAccessError:
    data = service.describe_access(
        agent_id=agent_id,
        app_id=app_id,
        user_id=user_id,
        needed=needed,
    )
    return AppAccessError(message, code=code, data=data)


def _get_for_actor(
    service: AppService,
    app_id: str,
    agent_id: str,
    user_id: int,
    needed: str,
) -> LightAppRow:
    """Resolve an app for the acting user, using the app ACL.

    The agent is only the executor: what the user may do with an app comes
    from the app's own collaborator list, so a shared agent's members can use
    the apps they were given and nobody inherits access from owning an agent.
    Administrators may act on any app (matching the HTTP routes); cross-owner
    writes are recorded in the audit log.
    """
    is_admin = service.is_admin(user_id)
    if not (is_admin or service.is_agent_accessible(agent_id, user_id)):
        raise _access_error(
            service,
            agent_id=agent_id,
            app_id=app_id,
            user_id=user_id,
            needed=needed,
            code="APP_AGENT_NOT_ACCESSIBLE",
            message="agent is not accessible to this user",
        )
    row = service.get(app_id)
    if row is None:
        raise ValueError(f"MiniApp not found: {app_id}")
    granted = service.permission_for(row, user_id, is_admin=is_admin)
    if granted is None or PERMISSION_RANK.get(granted, 0) < PERMISSION_RANK[needed]:
        raise _access_error(
            service,
            agent_id=agent_id,
            app_id=app_id,
            user_id=user_id,
            needed=needed,
            code="APP_PERMISSION_DENIED",
            message=(
                f"this user has no '{needed}' permission on MiniApp {app_id} "
                f"(granted: {granted or 'none'})"
            ),
        )
    if (
        is_admin
        and needed in {"edit_data", "edit_structure"}
        and (row.owner_user_id != user_id or not service.is_agent_owner(agent_id, user_id))
    ):
        service.audit_admin_override(
            user_id=user_id,
            agent_id=agent_id,
            app_id=row.app_id,
            needed=needed,
            app_owner_id=row.owner_user_id,
        )
    return row


def _require_manager(service: AppService, row: LightAppRow, user_id: int) -> None:
    """Owner/admin only: publishing, sharing, capability and deletion actions."""
    if not (service.is_admin(user_id) or row.owner_user_id == user_id):
        raise ValueError("only the app owner or an admin can manage this MiniApp")


def _may_see_pii(service: AppService, row: LightAppRow, user_id: int) -> bool:
    """Editors and owners see sensitive columns; viewers do not."""
    permission = service.permission_for(row, user_id, is_admin=service.is_admin(user_id))
    return PERMISSION_RANK.get(permission or "", 0) >= PERMISSION_RANK["edit_data"]


def _summary(row: LightAppRow) -> dict[str, Any]:
    return {
        "app_id": row.app_id,
        "name": row.name,
        "description": row.description,
        "row_count": row.row_count,
        "capabilities": app_capabilities.describe(row.capabilities_json),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def _app_card(service: AppService, row: LightAppRow) -> dict[str, Any]:
    """Structured chat card for an app (open/created app)."""
    return {
        "octop_ui": {"renderer": "app_card", "version": 1},
        "data": {
            **_summary(row),
            "agent_id": row.agent_id,
            "has_html": await service.has_html(row),
            "has_preview": await service.has_preview(row),
        },
        "text": f"MiniApp {row.name} ({row.app_id})",
    }


def _table_summary(table: AppTableView) -> dict[str, Any]:
    return {
        "table_key": table.table_key,
        "label": table.label,
        "row_count": table.row_count,
        "is_main": table.is_main,
    }


async def _read_asset_source(service: AppService, agent_id: str, source: str) -> bytes:
    """Read the bytes of an image from a URL or from the agent workspace."""
    text = source.strip()
    if text.startswith(("http://", "https://")):
        return await _download_asset(text)
    return await service.read_workspace_file(agent_id, text)


async def _download_asset(url: str) -> bytes:
    """Download an image, refusing private and loopback targets on every hop.

    The body is capped while it streams: a server that lies about its size
    must not be able to fill memory.
    """
    from octop_harness.security.ssrf import validate_agent_fetch_url

    current = validate_agent_fetch_url(url)
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        for _ in range(5):
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = (response.headers.get("location") or "").strip()
                    if not location:
                        break
                    current = validate_agent_fetch_url(str(response.url.join(location)))
                    continue
                if response.is_error:
                    raise ValueError(f"download failed: HTTP {response.status_code}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_ASSET_BYTES:
                        raise ValueError("asset exceeds the 5 MB limit")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError("too many redirects while downloading the asset")


async def _share_link_warning(service: AppService, row: LightAppRow) -> str | None:
    """Describe what a public link actually exposes, for the model to relay."""
    tables = service.list_tables(row)
    public = [table for table in tables if table.public_visible]
    private = [table for table in tables if not table.public_visible]
    pii_columns: list[str] = []
    unmarked: list[str] = []
    for table in public:
        schema = await service.read_schema(row, table.table_key)
        pii_columns.extend(
            f"{table.table_key}.{column.name}" for column in schema.columns if column.pii
        )
        _schema, rows, _total = await service.read_rows(
            row,
            limit=50,
            table=table.table_key,
            redact_pii=False,
        )
        seen: set[str] = set()
        for values in rows:
            for column, _hit in find_unmarked_pii(
                ((item.name, item.pii, item.type) for item in schema.columns),
                values,
            ):
                key = f"{table.table_key}.{column}"
                if key not in seen:
                    seen.add(key)
                    unmarked.append(key)
    if not private and not pii_columns and not unmarked:
        return None
    lines = ["Sharing notice for the public link:"]
    lines.append("Public tables: " + (", ".join(table.table_key for table in public) or "(none)"))
    if private:
        lines.append(
            "Private tables (not in the public link): "
            + ", ".join(table.table_key for table in private)
        )
    if pii_columns:
        lines.append("Sensitive columns masked on public reads: " + ", ".join(pii_columns))
    if unmarked:
        lines.append(
            "Unmarked PII detected in public tables: "
            + ", ".join(unmarked)
            + ". Mark these columns as pii or keep the table private before sharing."
        )
    html = await service.read_html(row)
    if html and detect_text_pii(html):
        lines.append("PII detected in the app HTML itself; remove it from the page before sharing.")
    return "\n".join(lines)


async def _share_link_payload(
    service: AppService,
    row: LightAppRow,
    *,
    user_id: int,
    rotate: bool,
    ttl_days: int,
    public_base_url: str,
) -> list[dict[str, Any]]:
    """Channel-ready blocks: one plain-text link plus an optional cover."""
    token, _expires_at = await service.publish(
        row,
        user_id=user_id,
        rotate=rotate,
        ttl_days=ttl_days,
    )
    path = f"/p/{token}"
    url = f"{public_base_url.rstrip('/')}{path}" if public_base_url else path
    lines = [row.name]
    if row.description:
        lines.append(row.description)
    lines.append(url)
    blocks: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(lines)}]
    if await service.has_preview(row):
        preview = app_path(row.app_id, FILE_PREVIEW)
        blocks.append(
            {
                "type": "image",
                "path": preview,
                "source": {"type": "url", "url": preview, "media_type": "image/svg+xml"},
                "filename": FILE_PREVIEW,
                # iLink image messages need a raster format; dashboard can
                # still render the SVG through its preview endpoint.
                "im_safe": False,
            }
        )
    return blocks


def _session_subject() -> str:
    """Subject segment of ``agent:channel:subject:chat_type`` (may contain colons)."""
    session_key = str(_tool_config().get("session_key") or "")
    parts = session_key.split(":", 2)
    if len(parts) != 3:
        return ""
    subject, _, _chat_type = parts[2].rpartition(":")
    return subject


def _resolve_app_by_name(
    service: AppService,
    agent_id: str,
    user_id: int,
    name: str,
) -> LightAppRow:
    """Find an app by name among apps the user can actually view."""
    is_admin = service.is_admin(user_id)
    if not (is_admin or service.is_agent_accessible(agent_id, user_id)):
        raise _access_error(
            service,
            agent_id=agent_id,
            app_id=None,
            user_id=user_id,
            needed="view",
            code="APP_AGENT_NOT_ACCESSIBLE",
            message="agent is not accessible to this user",
        )
    if is_admin:
        rows = service.apps_for_agent(agent_id)
    else:
        rows = [
            row
            for row, _permission in service.list_for_user(user_id, is_admin=False)
            if row.agent_id == agent_id
        ]
    needle = name.strip().lower()
    exact = [candidate for candidate in rows if candidate.name.strip().lower() == needle]
    matches = exact or [candidate for candidate in rows if needle in candidate.name.lower()]
    if not matches:
        raise ValueError(f"MiniApp not found: {name}")
    if len(matches) > 1:
        options = ", ".join(f"{item.name} ({item.app_id})" for item in matches[:5])
        raise ValueError(f"more than one MiniApp matches {name!r}: {options}")
    row = matches[0]
    service.require(row, user_id, "view", is_admin=is_admin)
    return row


def _auth_link_missing(source: str, public_base_url: str) -> list[str]:
    missing: list[str] = []
    if not public_base_url.strip():
        missing.append("public_base_url")
    if _is_dashboard_source(source):
        missing.append("im_source")
    if _session_chat_type() != "dm":
        missing.append("dm_chat")
    if not _tool_identity_verified():
        missing.append("identity_verified")
    return missing


def _can_issue_auth_link(source: str, public_base_url: str) -> bool:
    return not _auth_link_missing(source, public_base_url)


async def _channel_link_payload(
    service: AppService,
    row: LightAppRow,
    *,
    user_id: int,
    source: str,
    public_base_url: str,
    mode: str,
    rotate: bool,
    ttl_days: int,
    origin: str = "app_share_link",
) -> list[dict[str, Any]]:
    """Return either a personal sign-in link or the public read-only link."""
    if mode not in {"auto", "public", "auth"}:
        raise ValueError("mode must be auto, public or auth")
    if mode == "auth" and rotate:
        raise ValueError("rotate only applies to public links")
    permission = service.permission_for(
        row,
        user_id,
        is_admin=service.is_admin(user_id),
    )
    if permission is None:
        raise ValueError("this user has no permission on this MiniApp")
    auth_ready = _can_issue_auth_link(source, public_base_url)
    is_manager = service.is_admin(user_id) or row.owner_user_id == user_id

    if mode == "auth":
        missing = _auth_link_missing(source, public_base_url)
        if missing:
            guidance = (
                " This turn is from the dashboard; personal links require a verified "
                "channel identity in a private IM chat."
                if _is_dashboard_source(source)
                else " Personal links require the sender to be /bind-verified as the "
                "app owner in a private IM chat; use a public link otherwise."
            )
            raise ValueError(
                "auth links require a verified channel identity in a private IM chat and a configured "
                f"public_base_url (missing: {', '.join(missing)}; "
                f"source={source or 'unknown'}; chat_type={_session_chat_type() or 'unknown'})."
                f"{guidance} Do not create the link with a script, shell command, database "
                "query or platform source; retry app_share_link from the IM chat."
            )
        use_auth = True
    elif mode == "public":
        use_auth = False
    elif rotate:
        # Rotating a public link must not silently return a personal auth link.
        use_auth = False
    else:
        use_auth = auth_ready

    if use_auth:
        token, _expires_at = service.create_access_link(
            row,
            user_id=user_id,
            channel_type=_source_channel_type(source),
            channel_subject_id=_session_subject(),
            origin=origin,
        )
        # Query, not fragment: IM clients (notably the WeChat in-app browser)
        # mangle `#code=...` into a broken path, so the confirm page never
        # renders. The SPA reads both forms (see AppAccessComplete).
        url = f"{public_base_url.rstrip('/')}/login/app-access?code={token}"
        return [
            {"type": "text", "text": f"{row.name}\n{url}"},
            {
                "type": "text",
                "text": (f"single_use=true\nvalid_for_seconds={service.ACCESS_LINK_TTL_SECONDS}"),
            },
        ]

    if not is_manager:
        raise ValueError("only the app owner or an admin can manage public links")
    service.require(row, user_id, "edit_structure", is_admin=service.is_admin(user_id))
    payload = await _share_link_payload(
        service,
        row,
        user_id=user_id,
        rotate=rotate,
        ttl_days=ttl_days,
        public_base_url=public_base_url,
    )
    warning = await _share_link_warning(service, row)
    if warning:
        payload.append({"type": "text", "text": warning})
    return payload


def build_app_tools(
    service: AppService,
    *,
    public_base_url: str = "",
    repos: RepoBundle | None = None,
) -> list[StructuredTool]:
    """Return built-in light-app tools (wired via ``HarnessAgentConfig.tools``)."""

    async def app_inspect_file(
        path: Annotated[
            str,
            Field(
                description=(
                    "Workspace-relative path of the file to inspect "
                    "(CSV/TSV/JSON/.xlsx/.xls), e.g. inbound/imports/sales.xlsx. "
                    "Hidden files (starting with a dot) are rejected."
                )
            ),
        ],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            if not (service.is_admin(user_id) or service.is_agent_accessible(agent_id, user_id)):
                raise _access_error(
                    service,
                    agent_id=agent_id,
                    app_id=None,
                    user_id=user_id,
                    needed="view",
                    code="APP_AGENT_NOT_ACCESSIBLE",
                    message="agent is not accessible to this user",
                )
            report = await service.inspect_workspace_file(agent_id, path)
            return _ok(
                {
                    "octop_ui": {"renderer": "app_inspect_card", "version": 1},
                    "data": report,
                    "text": (
                        f"Inspected {report['file']}: "
                        f"{len(report['sheets'])} sheet(s), "
                        f"{len(report['pii_columns'])} sensitive column(s)."
                    ),
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_propose_plan(
        path: Annotated[
            str,
            Field(description="Workspace-relative path of the inspected spreadsheet."),
        ],
        name: Annotated[
            str,
            Field(description="Short display name shown in the app list (max 100 characters)."),
        ],
        columns: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(_COLUMNS_HELP + " Omit when passing `tables` for a multi-sheet plan.")
            ),
        ] = None,
        sheet: Annotated[
            str | None,
            Field(description="Sheet name from the inspection report."),
        ] = None,
        header_row: Annotated[
            int | None,
            Field(description="0-based header row from the inspection report."),
        ] = None,
        mapping: Annotated[
            dict[str, str] | None,
            Field(description="Optional source header -> schema column map."),
        ] = None,
        upsert_key: Annotated[
            list[str] | None,
            Field(description="Columns whose combined value identifies an existing row."),
        ] = None,
        include_pii: Annotated[
            bool,
            Field(description="Import values of columns detected as sensitive."),
        ] = False,
        tables: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    "Multi-sheet plan: a list of {key?, label?, sheet, header_row?, "
                    "columns, mapping?, upsert_key?, include_pii?}. The first table "
                    "becomes main; keys must be unique. Omit for a single table."
                )
            ),
        ] = None,
        services: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    "Optional service declarations applied after the import: "
                    "{name, provider, connector_instance_id?, connector_kind?, "
                    "allowed_tools, allowed_url_prefixes?, request_headers?, "
                    "cache_ttl_seconds?, max_calls_per_minute?, enabled?}."
                )
            ),
        ] = None,
        capabilities: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional page capability list applied after the import. "
                    "Owner-only capabilities are rejected during proposal."
                )
            ),
        ] = None,
    ) -> str:
        """Create a durable setup plan and show the confirmation card.

        The tool has no MiniApp side effects: it only writes the draft plan. The
        user confirms it in the dashboard card; the backend then creates the
        app and runs a dry-run before the explicit import action.
        """
        try:
            agent_id, user_id = _tool_ctx()
            if not _is_dashboard_source(_tool_source()):
                raise ValueError(
                    "app_propose_plan is available in the dashboard only; "
                    "use app_create/app_import_rows directly in this channel"
                )
            plan = await service.propose_setup_plan(
                agent_id=agent_id,
                user_id=user_id,
                thread_id=_current_thread_id(),
                path=path,
                name=name,
                columns=columns,
                sheet=sheet,
                header_row=header_row,
                mapping=mapping,
                upsert_key=upsert_key,
                include_pii=include_pii,
                tables=tables,
                services=services,
                capabilities=capabilities,
            )
            return _ok(
                {
                    "octop_ui": {"renderer": "app_inspect_card", "version": 2},
                    "data": _setup_plan_payload(plan),
                    "text": (
                        f"Setup plan {plan.plan_id} proposed for {plan.name}; "
                        "wait for the user to confirm it in the plan card."
                    ),
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_get_plan(
        plan_id: Annotated[
            str,
            Field(description="Plan id returned by app_propose_plan."),
        ],
    ) -> str:
        """Read a setup plan's compact state and confirmed schema."""
        try:
            _, user_id = _tool_ctx()
            plan = await service.get_setup_plan(plan_id, user_id)
            return _ok(_setup_plan_payload(plan))
        except Exception as exc:
            return _err(exc)

    async def app_cancel_plan(
        plan_id: Annotated[
            str,
            Field(description="Plan id returned by app_propose_plan."),
        ],
    ) -> str:
        """Cancel a setup plan and remove its empty app, before import."""
        try:
            _, user_id = _tool_ctx()
            plan = await service.cancel_setup_plan(plan_id, user_id=user_id)
            return _ok(_setup_plan_payload(plan))
        except Exception as exc:
            return _err(exc)

    async def app_list() -> str:
        try:
            agent_id, user_id = _tool_ctx()
            is_admin = service.is_admin(user_id)
            if not (is_admin or service.is_agent_accessible(agent_id, user_id)):
                return _ok({"apps": []})
            rows = (
                service.apps_for_agent(agent_id)
                if is_admin
                else [
                    row
                    for row, _permission in service.list_for_user(user_id, is_admin=is_admin)
                    if row.agent_id == agent_id
                ]
            )
            return _ok(
                {
                    "apps": [
                        {
                            **_summary(row),
                            "tables": [_table_summary(table) for table in service.list_tables(row)],
                        }
                        for row in rows
                    ]
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_list_bindings(
        app_id: Annotated[str, Field(description="App id returned by app_list/app_create.")],
    ) -> str:
        """List service bindings; binding writes remain an owner/admin action."""
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            return _ok(
                {
                    "app_id": row.app_id,
                    "bindings": [
                        {
                            "binding_id": item.binding_id,
                            "provider": item.provider,
                            "service_id": item.service_id,
                            "config": item.config,
                            "enabled": item.enabled,
                            "version": item.version,
                            "builtin": item.builtin,
                        }
                        for item in service.list_bindings(row)
                    ],
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_add_service(
        app_id: Annotated[str, Field(description="App id returned by app_list/app_create.")],
        name: Annotated[
            str,
            Field(description="Page-facing service name, lowercase letters/digits/-/_."),
        ],
        provider: Annotated[
            str,
            Field(
                description=(
                    "connector (authenticated; requires connector_instance_id) or "
                    "external (public source; the server auto-provisions a connector)."
                )
            ),
        ],
        allowed_tools: Annotated[
            list[str],
            Field(description="Read-only tool names the page may call."),
        ],
        allowed_url_prefixes: Annotated[
            list[str] | None,
            Field(description="http(s) prefixes accepted for URL-fetching tools."),
        ] = None,
        request_headers: Annotated[
            dict[str, str] | None,
            Field(
                description=(
                    "Optional headers for http-extract: User-Agent, Referer, "
                    "Accept, Accept-Language."
                )
            ),
        ] = None,
        connector_instance_id: Annotated[
            str,
            Field(
                description=(
                    "Existing connector instance owned by the app owner/admin. "
                    "Required when provider=connector. Omit for provider=external; "
                    "the server auto-provisions the public connector."
                )
            ),
        ] = "",
        connector_kind: Annotated[
            str | None,
            Field(
                description=(
                    "Connector kind to auto-provision for provider=external: "
                    "http-extract (default) or douban. Ignored when "
                    "connector_instance_id is set."
                )
            ),
        ] = None,
        cache_ttl_seconds: Annotated[
            int,
            Field(description="Response cache TTL; 0 disables caching."),
        ] = 300,
        max_calls_per_minute: Annotated[
            int,
            Field(description="Live call budget per minute (1-600)."),
        ] = 30,
        enabled: Annotated[bool, Field(description="Whether the page may call it.")] = True,
    ) -> str:
        """Declare a read-only external service; the owner must still grant external.fetch."""
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            _require_manager(service, row, user_id)
            view = service.save_service(
                row,
                name=name,
                provider=provider,
                connector_instance_id=connector_instance_id,
                connector_kind=connector_kind,
                allowed_tools=allowed_tools,
                allowed_url_prefixes=allowed_url_prefixes or [],
                request_headers=request_headers,
                cache_ttl_seconds=cache_ttl_seconds,
                max_calls_per_minute=max_calls_per_minute,
                public_live=False,
                enabled=enabled,
                actor_user_id=user_id,
                is_admin=service.is_admin(user_id),
            )
            return _ok(
                {
                    "app_id": row.app_id,
                    "service": {
                        "name": view.name,
                        "provider": view.provider,
                        "connector_instance_id": view.connector_instance_id,
                        "allowed_tools": list(view.allowed_tools),
                        "allowed_url_prefixes": list(view.allowed_url_prefixes),
                        "request_headers": dict(view.request_headers),
                        "enabled": view.enabled,
                    },
                    "note": "The app owner must enable external.fetch before the page can call it.",
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_list_services(
        app_id: Annotated[str, Field(description="App id returned by app_list/app_create.")],
    ) -> str:
        """List declared external services for an app."""
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            return _ok(
                {
                    "app_id": row.app_id,
                    "services": [
                        {
                            "name": item.name,
                            "provider": item.provider,
                            "connector_instance_id": item.connector_instance_id,
                            "allowed_tools": list(item.allowed_tools),
                            "allowed_url_prefixes": list(item.allowed_url_prefixes),
                            "request_headers": dict(item.request_headers),
                            "enabled": item.enabled,
                        }
                        for item in service.list_services(row)
                    ],
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_remove_service(
        app_id: Annotated[str, Field(description="App id returned by app_list/app_create.")],
        name: Annotated[str, Field(description="Service name to remove.")],
    ) -> str:
        """Remove a declared external service and its cache."""
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            _require_manager(service, row, user_id)
            if not service.delete_service(row, name):
                raise ValueError(f"unknown service: {name!r}")
            return _ok({"app_id": row.app_id, "deleted": name.strip().lower()})
        except Exception as exc:
            return _err(exc)

    async def app_open(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            source = _tool_source()
            if _is_dashboard_source(source):
                return _ok(await _app_card(service, row))
            # IM channels cannot render the dashboard app_card. Prefer a
            # personal sign-in link for bound private chats; otherwise fall
            # back to the public link or a plain-text description.
            try:
                payload = await _channel_link_payload(
                    service,
                    row,
                    user_id=user_id,
                    source=source,
                    public_base_url=public_base_url,
                    mode="auto",
                    rotate=False,
                    ttl_days=0,
                    origin="app_open",
                )
                return _ok(payload)
            except ValueError:
                lines = [row.name]
                if row.description:
                    lines.append(row.description)
                return _ok([{"type": "text", "text": "\n".join(lines)}])
        except Exception as exc:
            return _err(exc)

    async def app_share_link(
        app_id: Annotated[
            str | None,
            Field(description="App id; omit when passing name."),
        ] = None,
        name: Annotated[
            str | None,
            Field(description="App name (exact or substring match); omit when passing app_id."),
        ] = None,
        rotate: Annotated[
            bool,
            Field(
                description=(
                    "Rotate the public link. Leave false to reuse the current link; "
                    "rotating invalidates links already sent to users."
                )
            ),
        ] = False,
        ttl_days: Annotated[
            int,
            Field(ge=0, le=3650, description="Link lifetime in days; 0 means no expiry."),
        ] = 0,
        mode: Annotated[
            str,
            Field(
                description=(
                    "auto (default): a personal one-time sign-in link in a bound private "
                    "IM chat, otherwise the public link; public: force the public link; "
                    "auth: require a personal one-time sign-in link."
                )
            ),
        ] = "auto",
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            if app_id:
                row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            elif name and name.strip():
                row = _resolve_app_by_name(service, agent_id, user_id, name)
            else:
                raise ValueError("app_id or name is required")
            payload = await _channel_link_payload(
                service,
                row,
                user_id=user_id,
                source=_tool_source(),
                public_base_url=public_base_url,
                mode=mode,
                rotate=rotate,
                ttl_days=ttl_days,
            )
            return _ok(payload)
        except Exception as exc:
            return _err(exc)

    async def app_create(
        name: Annotated[
            str,
            Field(description="Short display name shown in the app list (max 100 characters)."),
        ],
        columns: Annotated[
            list[dict[str, Any]],
            Field(description=_COLUMNS_HELP),
        ],
        description: Annotated[
            str,
            Field(description="Optional one-line description shown in the app list."),
        ] = "",
        capabilities: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Page capabilities to declare. Omit for the safe default: data.read, "
                    "data.write, asset.read, external.open, chat.compose, events.read. "
                    "High-risk actions (ai.generate, game.launch, external.fetch, "
                    "events.emit, schedules.manage) must be enabled by the owner in the "
                    "app detail page; this tool rejects them."
                )
            ),
        ] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            active_plan = service.active_setup_plan(_current_thread_id())
            if active_plan is not None:
                raise ValueError(
                    "a MiniApp setup plan is active in this conversation; "
                    "confirm or cancel it before calling app_create"
                )
            is_admin = service.is_admin(user_id)
            if not (is_admin or service.is_agent_accessible(agent_id, user_id)):
                raise _access_error(
                    service,
                    agent_id=agent_id,
                    app_id=None,
                    user_id=user_id,
                    needed="edit_structure",
                    code="APP_AGENT_NOT_ACCESSIBLE",
                    message="this user cannot use this agent",
                )
            selected = (
                app_capabilities.validate_agent_settable(capabilities)
                if capabilities is not None
                else None
            )
            row = await service.create(
                agent_id=agent_id,
                owner_user_id=user_id,
                name=name,
                description=description,
                columns=columns,
                capabilities=selected,
            )
            if is_admin and not service.is_agent_owner(agent_id, user_id):
                service.audit_admin_override(
                    user_id=user_id,
                    agent_id=agent_id,
                    app_id=row.app_id,
                    needed="edit_structure",
                    app_owner_id=row.owner_user_id,
                )
            return _ok({**_summary(row), "next_step": "call app_write_html to build the UI"})
        except Exception as exc:
            return _err(exc)

    async def app_update_schema(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        columns: Annotated[
            list[dict[str, Any]],
            Field(
                description=(
                    "Columns to append. Existing columns cannot be renamed or removed. "
                    "New columns cannot be required when the app already has rows. " + _COLUMNS_HELP
                )
            ),
        ],
        table: Annotated[
            str,
            Field(description=_TABLE_HELP),
        ] = MAIN_TABLE_KEY,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            _reject_if_plan_app(service, app_id)
            schema = await service.add_columns(row, columns, table=table)
            return _ok(
                {
                    "app_id": row.app_id,
                    "table": table,
                    "version": schema.version,
                    "columns": schema.to_dict()["columns"],
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_alter_schema(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        renames: Annotated[
            dict[str, str] | None,
            Field(description="Old column name -> new column name."),
        ] = None,
        drops: Annotated[
            list[str] | None,
            Field(description="Columns to remove; their data is deleted."),
        ] = None,
        types: Annotated[
            dict[str, dict[str, Any]] | None,
            Field(
                description=(
                    "Column name -> {type, label?, required?, values?}. Types: "
                    "string, text, integer, number, boolean, date, datetime, enum."
                )
            ),
        ] = None,
        pii: Annotated[
            dict[str, str | None] | None,
            Field(
                description=(
                    "Column name -> sensitive-data kind (id_card, bank_card, phone, "
                    "address, salary, email, secret); pass null to clear. Use this "
                    "for existing sensitive columns before writing free-text PII."
                )
            ),
        ] = None,
        table: Annotated[str, Field(description=_TABLE_HELP)] = MAIN_TABLE_KEY,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            app = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            schema = await service.alter_schema(
                app,
                table=table,
                renames=renames,
                drops=drops,
                types=types,
                pii=pii,
            )
            return _ok(
                {
                    "app_id": app.app_id,
                    "table": table,
                    "version": schema.version,
                    "columns": schema.to_dict()["columns"],
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_set_table_visibility(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        public: Annotated[
            bool,
            Field(
                description=(
                    "True to expose this table on the public read-only link; false "
                    "to keep it visible only to signed-in users with access."
                )
            ),
        ],
        table: Annotated[str, Field(description=_TABLE_HELP)] = MAIN_TABLE_KEY,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            _require_manager(service, row, user_id)
            view = await service.set_table_visibility(
                row,
                table=table,
                public_visible=public,
            )
            return _ok(
                {
                    "app_id": row.app_id,
                    "table": view.table_key,
                    "public_visible": view.public_visible,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_set_capabilities(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        capabilities: Annotated[
            list[str],
            Field(
                description=(
                    "Complete page capability list. Pass [] to deny every page action; "
                    "high-risk actions (ai.generate, game.launch, external.fetch, "
                    "events.emit, schedules.manage) must be enabled by the owner in the "
                    "app detail page and are rejected here. Do not retry a rejected "
                    "call; ask the owner to enable the capability instead."
                )
            ),
        ],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            _require_manager(service, row, user_id)
            row = service.set_capabilities(
                row, app_capabilities.validate_agent_settable(capabilities)
            )
            return _ok(_summary(row))
        except Exception as exc:
            return _err(exc)

    async def app_write_html(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        html: Annotated[str, Field(description=_HTML_HELP)],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            size = await service.write_html(row, html)
            # Writing the shell is an internal edit, not a "show the app" action.
            # Only app_open/app_create_from_template return a chat card; emitting
            # one here would duplicate the card whenever the agent opens the app.
            return _ok(
                {
                    **_summary(row),
                    "bytes": size,
                    "has_html": True,
                    "next_step": (
                        "After the first complete usable version, call app_open once "
                        "to show the entry; do not call app_open after later edits."
                    ),
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_create_from_template(
        template: Annotated[
            str,
            Field(description="Template id from the agent workspace or the system catalog."),
        ],
        source: Annotated[
            str,
            Field(description="'agent' (workspace template, default) or 'system' (built-in)."),
        ] = "agent",
        name: Annotated[
            str | None,
            Field(description="Optional app name; defaults to the template name."),
        ] = None,
        description: Annotated[str, Field(description="Optional app description.")] = "",
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            is_admin = service.is_admin(user_id)
            if not (is_admin or service.is_agent_accessible(agent_id, user_id)):
                raise _access_error(
                    service,
                    agent_id=agent_id,
                    app_id=None,
                    user_id=user_id,
                    needed="edit_structure",
                    code="APP_AGENT_NOT_ACCESSIBLE",
                    message="agent is not accessible to this user",
                )
            active_plan = service.active_setup_plan(_current_thread_id())
            row = await service.create_from_template(
                agent_id=agent_id,
                owner_user_id=user_id,
                template=template,
                source=source,
                name=name,
                description=description,
            )
            if active_plan is not None:
                await service.supersede_setup_plan(
                    active_plan.plan_id,
                    reason="app_create_from_template",
                    app_id=row.app_id,
                    actor_user_id=user_id,
                )
            if is_admin and not service.is_agent_owner(agent_id, user_id):
                service.audit_admin_override(
                    user_id=user_id,
                    agent_id=agent_id,
                    app_id=row.app_id,
                    needed="edit_structure",
                    app_owner_id=row.owner_user_id,
                )
            card = await _app_card(service, row)
            card["data"]["next_step"] = "call app_write_html only if you want to change the shell"
            return _ok({**card["data"], **card})
        except Exception as exc:
            return _err(exc)

    async def app_add_table(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        key: Annotated[
            str,
            Field(
                description=(
                    "Machine key for the new table, e.g. 'orders'. Use letters, digits "
                    "and underscores; 'main' is taken."
                )
            ),
        ],
        columns: Annotated[
            list[dict[str, Any]],
            Field(description="Column definitions for the new table. " + _COLUMNS_HELP),
        ],
        label: Annotated[
            str,
            Field(description="Display name for the table (optional)."),
        ] = "",
        public: Annotated[
            bool,
            Field(
                description=(
                    "Expose this table on the public read-only link. Defaults to "
                    "false; only the main table is public by default."
                )
            ),
        ] = False,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            table = await service.create_table(
                row,
                table_key=key,
                label=label,
                columns=columns,
                public_visible=public,
            )
            return _ok(
                {
                    "app_id": row.app_id,
                    "table": table.table_key,
                    "label": table.label,
                    "public_visible": table.public_visible,
                    "columns": columns,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_read_data(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        limit: Annotated[int, Field(description="Maximum rows to return (1-500).")] = 100,
        offset: Annotated[int, Field(description="Rows to skip, for paging.")] = 0,
        table: Annotated[
            str,
            Field(description=_TABLE_HELP),
        ] = MAIN_TABLE_KEY,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            redact_pii = not _may_see_pii(service, row, user_id)
            schema, rows, row_meta, total = await service.read_rows_detailed(
                row,
                limit=limit,
                offset=offset,
                table=table,
                redact_pii=redact_pii,
                viewer_user_id=user_id,
                include_all_rows=service.is_admin(user_id) or row.owner_user_id == user_id,
            )
            pii_columns = [column.name for column in schema.columns if column.pii]
            return _ok(
                {
                    "app_id": row.app_id,
                    "table": table,
                    "columns": schema.to_dict()["columns"],
                    "rows": rows,
                    "row_meta": row_meta,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "pii_columns": pii_columns,
                    "pii_masked": redact_pii,
                    "pii_note": (
                        "Tool output may be masked by the platform for safety; "
                        "masking does not mean the stored value is masked or erased."
                    ),
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_append_row(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        row: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "One row keyed by column name. Missing optional columns become empty; "
                    "unknown columns and type mismatches are rejected."
                )
            ),
        ],
        table: Annotated[
            str,
            Field(description=_TABLE_HELP),
        ] = MAIN_TABLE_KEY,
        client_request_id: Annotated[
            str | None,
            Field(
                description=(
                    "Optional idempotency key. Retrying with the same value does "
                    "not create a duplicate row."
                )
            ),
        ] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            app = _get_for_actor(service, app_id, agent_id, user_id, "edit_data")
            _reject_if_plan_app(service, app_id)
            index, stored = await service.append_row_record(
                app,
                row,
                table=table,
                client_request_id=client_request_id,
                created_by=user_id,
                source="agent",
            )
            schema = await service.read_schema(app, table)
            return _ok(
                {
                    "app_id": app.app_id,
                    "table": table,
                    "row_index": index,
                    "row_id": stored.row_id,
                    "version": stored.version,
                    "pii_columns": [column.name for column in schema.columns if column.pii],
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_update_row(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        row_id: Annotated[
            str, Field(description="Row id returned by app_read_data/app_append_row.")
        ],
        row: Annotated[
            dict[str, Any],
            Field(description="Complete row keyed by column name; missing columns become empty."),
        ],
        expected_version: Annotated[
            int | None,
            Field(description="Reject the update when the stored row version differs."),
        ] = None,
        table: Annotated[str, Field(description=_TABLE_HELP)] = MAIN_TABLE_KEY,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            app = _get_for_actor(service, app_id, agent_id, user_id, "edit_data")
            _reject_if_plan_app(service, app_id)
            updated = await service.update_row(
                app,
                row_id,
                row,
                table=table,
                expected_version=expected_version,
                viewer_user_id=user_id,
                include_all_rows=service.is_admin(user_id) or app.owner_user_id == user_id,
                actor_user_id=user_id,
                source="agent",
            )
            schema = await service.read_schema(app, table)
            return _ok(
                {
                    "app_id": app.app_id,
                    "row_id": updated["row_id"],
                    "version": updated["version"],
                    "pii_columns": [column.name for column in schema.columns if column.pii],
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_delete_row(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        row_id: Annotated[str, Field(description="Row id returned by app_read_data.")],
        expected_version: Annotated[
            int | None,
            Field(description="Reject the delete when the stored row version differs."),
        ] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            app = _get_for_actor(service, app_id, agent_id, user_id, "edit_data")
            _reject_if_plan_app(service, app_id)
            deleted = await service.delete_row(
                app,
                row_id,
                expected_version=expected_version,
                viewer_user_id=user_id,
                include_all_rows=service.is_admin(user_id) or app.owner_user_id == user_id,
                actor_user_id=user_id,
                source="agent",
            )
            return _ok({"app_id": app.app_id, "deleted": deleted})
        except Exception as exc:
            return _err(exc)

    async def app_import_rows(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        path: Annotated[
            str,
            Field(
                description=(
                    "Workspace-relative path of the file to import "
                    "(CSV/TSV/JSON/.xlsx/.xls), e.g. inbound/exports/report.csv. "
                    "Hidden files (starting with a dot) are rejected."
                )
            ),
        ] = "",
        paths: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Import several workspace files into the same table, in order. "
                    "Use either path or paths."
                )
            ),
        ] = None,
        table: Annotated[
            str,
            Field(description=_TABLE_HELP),
        ] = MAIN_TABLE_KEY,
        mode: Annotated[
            str,
            Field(
                description="'append' adds rows after the existing ones; 'replace' overwrites the table."
            ),
        ] = "append",
        mapping: Annotated[
            dict[str, str] | None,
            Field(
                description=(
                    "Optional source-column -> table-column map. Default: columns "
                    "with the same name map automatically; others are ignored."
                )
            ),
        ] = None,
        sheet: Annotated[
            str | None,
            Field(
                description=(
                    "Worksheet to import from: a sheet name or a 0-based index. "
                    "Defaults to the first sheet."
                )
            ),
        ] = None,
        header_row: Annotated[
            int | None,
            Field(
                description=(
                    "0-based index of the header line, for files that start with a "
                    "title. Defaults to the first line."
                )
            ),
        ] = None,
        dry_run: Annotated[
            bool,
            Field(
                description=(
                    "Validate and count the rows without writing them. Use it to "
                    "show the user what would land before importing for real."
                )
            ),
        ] = False,
        upsert_key: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Columns whose combined value identifies an existing row; "
                    "matching rows are updated instead of duplicated (e.g. "
                    "['employee_no', 'month'] for a monthly ledger)."
                )
            ),
        ] = None,
        include_pii: Annotated[
            bool,
            Field(
                description=(
                    "Import values of columns detected as sensitive. Default "
                    "false drops them; ask the user before setting true."
                )
            ),
        ] = False,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            active_plan = service.active_setup_plan(_current_thread_id())
            if active_plan is not None:
                source_paths = set(paths or [])
                if path:
                    source_paths.add(path)
                belongs_to_plan = (
                    active_plan.app_id == app_id
                    if active_plan.app_id
                    else active_plan.source_path in source_paths
                )
                if belongs_to_plan:
                    raise ValueError(
                        "this import belongs to an active MiniApp setup plan; "
                        "use the plan card to dry-run and import"
                    )
            app = _get_for_actor(service, app_id, agent_id, user_id, "edit_data")
            if paths and path:
                raise ValueError("use either path or paths, not both")
            if not paths and not path:
                raise ValueError("an import path is required")
            if paths:
                result = await service.import_from_files(
                    app,
                    paths=paths,
                    table=table,
                    mode=mode,
                    mapping=mapping,
                    sheet=sheet,
                    header_row=header_row,
                    dry_run=dry_run,
                    upsert_key=upsert_key,
                    include_pii=include_pii,
                    created_by=user_id,
                )
            else:
                result = await service.import_from_file(
                    app,
                    path=path,
                    table=table,
                    mode=mode,
                    mapping=mapping,
                    sheet=sheet,
                    header_row=header_row,
                    dry_run=dry_run,
                    upsert_key=upsert_key,
                    include_pii=include_pii,
                    created_by=user_id,
                )
            return _ok(
                {
                    "app_id": app.app_id,
                    "table": table,
                    "imported": result.imported,
                    "updated": result.updated,
                    "warnings": result.warnings,
                    "would_import": result.would_import,
                    "dry_run": result.dry_run,
                    "skipped": result.skipped,
                    "errors": result.errors,
                    "pii_columns": result.pii_columns,
                    "blocked_pii": result.blocked_pii,
                    "failed_rows": result.failed_rows,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_save_asset(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        source: Annotated[
            str,
            Field(
                description=(
                    "Where to take the image from: an http(s) URL to download, "
                    "or a workspace-relative path (e.g. inbound/cover.jpg)."
                )
            ),
        ],
        name: Annotated[
            str,
            Field(
                description=(
                    "File name to store it under, e.g. cover.jpg. Letters, "
                    "digits, dot, dash and underscore only."
                )
            ),
        ],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            app = _get_for_actor(service, app_id, agent_id, user_id, "edit_data")
            data = await _read_asset_source(service, app.agent_id, source)
            stored = await service.save_asset(app, name, data)
            return _ok(
                {
                    "app_id": app.app_id,
                    "name": stored,
                    "size": len(data),
                    # Not a fetchable URL: the sandbox has no network. The page
                    # reads the asset through the SDK.
                    "usage": f'octopApp.asset("{stored}")',
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_set_preview(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
        svg: Annotated[
            str,
            Field(
                description=(
                    "The preview image of the app as one self-contained SVG "
                    "document. Use a 1200x630 viewBox, inline styles only, and "
                    "no scripts, no event handlers and no remote references."
                )
            ),
        ],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            app = _get_for_actor(service, app_id, agent_id, user_id, "edit_data")
            size = await service.write_preview(app, svg)
            return _ok(
                {
                    "app_id": app.app_id,
                    "preview": "preview.svg",
                    "size": size,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_delete(
        app_id: Annotated[str, Field(description="App id returned by app_create/app_list.")],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "edit_structure")
            _require_manager(service, row, user_id)
            await service.delete(row)
            return _ok({"deleted": row.app_id})
        except Exception as exc:
            return _err(exc)

    def _integration_repos() -> RepoBundle:
        if repos is None:
            raise ValueError("integration data is unavailable for this agent")
        return repos

    async def app_integration_list(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            return _ok(await integration_inventory(service, _integration_repos(), row))
        except Exception as exc:
            return _err(exc)

    async def app_integration_health(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            return _ok(await integration_health(service, _integration_repos(), row))
        except Exception as exc:
            return _err(exc)

    async def app_integration_trace(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        event_id: Annotated[
            str,
            Field(
                description=(
                    "Internal event id (evt_…) or the external id supplied by the "
                    "sender. Use the value shown by the webhook response or the "
                    "External Events page."
                )
            ),
        ],
        source_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional source id; disambiguates an external id shared by multiple sources."
                ),
            ),
        ] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            return _ok(
                await integration_trace(
                    service,
                    _integration_repos(),
                    row,
                    event_id,
                    source_id=source_id,
                )
            )
        except Exception as exc:
            return _err(exc)

    def _ingress_service() -> Any:
        return build_ingress_service(repos=_integration_repos(), app_service=service)

    def _webhook_service() -> Any:
        return build_webhook_service(repos=_integration_repos(), app_service=service)

    def _hook_url(source_id: str) -> str:
        base = public_base_url.strip().rstrip("/")
        path = f"/api/hooks/{source_id}"
        return f"{base}{path}" if base else path

    async def app_integration_create_ingress(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        name: Annotated[str, Field(description="Human-readable endpoint name.")],
        mode: Annotated[
            str,
            Field(description="app.inbox, app.import or app.refresh."),
        ],
        event_type: Annotated[
            str,
            Field(
                default="",
                description="Optional exact event type; empty accepts every type.",
            ),
        ] = "",
        mapping: Annotated[
            dict[str, Any] | None,
            Field(
                default=None,
                description=(
                    "app.import: {table, columns}; app.refresh: {source_id}; app.inbox: omit or {}."
                ),
            ),
        ] = None,
        max_per_minute: Annotated[
            int,
            Field(default=0, description="Subscription limit; 0 means no extra limit."),
        ] = 0,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            view, secret = await _ingress_service().create(
                row,
                name=name,
                mode=mode,
                event_type=event_type,
                mapping=mapping or {},
                max_per_minute=max_per_minute,
                actor_user_id=user_id,
            )
            return _ok(
                {
                    "ingress_id": view.ingress_id,
                    "name": view.name,
                    "mode": view.mode,
                    "event_type": view.event_type,
                    "mapping": view.mapping,
                    "source_id": view.source_id,
                    "hook_url": _hook_url(view.source_id),
                    "secret": secret,
                    "secret_shown_once": True,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_integration_update_ingress(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        ingress_id: Annotated[str, Field(description="Ingress id from list/health.")],
        name: Annotated[str | None, Field(default=None)] = None,
        mode: Annotated[str | None, Field(default=None)] = None,
        event_type: Annotated[str | None, Field(default=None)] = None,
        mapping: Annotated[dict[str, Any] | None, Field(default=None)] = None,
        max_per_minute: Annotated[int | None, Field(default=None)] = None,
        enabled: Annotated[bool | None, Field(default=None)] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            view = await _ingress_service().update(
                row,
                ingress_id,
                name=name,
                mode=mode,
                event_type=event_type,
                mapping=mapping,
                max_per_minute=max_per_minute,
                enabled=enabled,
            )
            return _ok(
                {
                    "ingress_id": view.ingress_id,
                    "name": view.name,
                    "mode": view.mode,
                    "event_type": view.event_type,
                    "mapping": view.mapping,
                    "enabled": view.enabled,
                    "hook_url": _hook_url(view.source_id),
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_integration_delete_ingress(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        ingress_id: Annotated[str, Field(description="Ingress id from list/health.")],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            deleted = _ingress_service().delete(row, ingress_id)
            return _ok({"deleted": deleted, "ingress_id": ingress_id})
        except Exception as exc:
            return _err(exc)

    async def app_integration_test_ingress(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        ingress_id: Annotated[str, Field(description="Ingress id from list/health.")],
        event_type: Annotated[
            str,
            Field(default="", description="Optional type; defaults to the ingress filter."),
        ] = "",
        payload: Annotated[
            dict[str, Any] | None,
            Field(default=None, description="Test event payload."),
        ] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            repos_value = _integration_repos()
            from octop.infra.events.service import ExternalEventService  # noqa: PLC0415

            events = ExternalEventService(repos=repos_value, secret_repo=repos_value.secret_repo)
            result = await _ingress_service().test_event(
                row,
                ingress_id,
                events,
                event_type=event_type,
                payload=payload or {},
            )
            return _ok(
                {
                    "event_id": result.event.event_id,
                    "event_type": result.event.event_type,
                    "deliveries": result.delivery_count,
                    "rate_limited": result.rate_limited,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_integration_create_webhook(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        name: Annotated[str, Field(description="Human-readable webhook name.")],
        url: Annotated[str, Field(description="http(s) destination URL.")],
        event_types: Annotated[
            list[str],
            Field(description="Exact types, prefix patterns like order.*, or *."),
        ],
        enabled: Annotated[bool, Field(description="Enable immediately.")] = True,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            view, _secret = await _webhook_service().create(
                row,
                name=name,
                url=url,
                event_types=event_types,
                enabled=enabled,
                actor_user_id=user_id,
            )
            return _ok(
                {
                    "webhook_id": view.webhook_id,
                    "name": view.name,
                    "url": view.url,
                    "event_types": list(view.event_types),
                    "enabled": view.enabled,
                    "secret_note": (
                        "The signing secret is intentionally not returned to the agent. "
                        "Ask the owner to open the app's Outbound Webhooks section and "
                        "rotate/copy the secret."
                    ),
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_integration_update_webhook(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        webhook_id: Annotated[str, Field(description="Webhook id from list/health.")],
        name: Annotated[str | None, Field(default=None)] = None,
        url: Annotated[str | None, Field(default=None)] = None,
        event_types: Annotated[list[str] | None, Field(default=None)] = None,
        enabled: Annotated[bool | None, Field(default=None)] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            view, _secret = await _webhook_service().update(
                row,
                webhook_id,
                name=name,
                url=url,
                event_types=event_types,
                enabled=enabled,
            )
            return _ok(
                {
                    "webhook_id": view.webhook_id,
                    "name": view.name,
                    "url": view.url,
                    "event_types": list(view.event_types),
                    "enabled": view.enabled,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def app_integration_delete_webhook(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        webhook_id: Annotated[str, Field(description="Webhook id from list/health.")],
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            deleted = _webhook_service().delete(row, webhook_id)
            return _ok({"deleted": deleted, "webhook_id": webhook_id})
        except Exception as exc:
            return _err(exc)

    async def app_integration_test_webhook(
        app_id: Annotated[
            str,
            Field(description="App id returned by app_list/app_create."),
        ],
        webhook_id: Annotated[str, Field(description="Webhook id from list/health.")],
        event_type: Annotated[
            str,
            Field(
                default="",
                description="Optional type; defaults to the webhook's first pattern.",
            ),
        ] = "",
        payload: Annotated[
            dict[str, Any] | None,
            Field(default=None, description="Test event payload."),
        ] = None,
    ) -> str:
        try:
            agent_id, user_id = _tool_ctx()
            row = _get_for_actor(service, app_id, agent_id, user_id, "view")
            _require_manager(service, row, user_id)
            repos_value = _integration_repos()
            from octop.infra.events.service import ExternalEventService  # noqa: PLC0415

            events = ExternalEventService(repos=repos_value, secret_repo=repos_value.secret_repo)
            result = await _webhook_service().test(
                row,
                webhook_id,
                events,
                event_type=event_type,
                payload=payload or {},
            )
            return _ok(
                {
                    "event_id": result.event.event_id,
                    "event_type": result.event.event_type,
                    "deliveries": result.delivery_count,
                    "rate_limited": result.rate_limited,
                    "note": (
                        "The delivery worker will POST this event to the webhook URL. "
                        "Use app_integration_health to check the delivery result."
                    ),
                }
            )
        except Exception as exc:
            return _err(exc)

    return [
        StructuredTool.from_function(
            coroutine=app_inspect_file,
            name="app_inspect_file",
            description=(
                "Inspect a spreadsheet file in the workspace and return a compact report: "
                "sheets, header row, per-column type/emptiness/distinct counts, sample "
                "values and PII warnings. Read-only; it does not create an app. To turn "
                "a ledger into an app, call app_propose_plan and wait for the user to "
                "confirm the plan card."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_propose_plan,
            name="app_propose_plan",
            description=(
                "Create a durable MiniApp setup plan from an inspected spreadsheet and "
                "show the user a plan card. Supports one table or a multi-sheet tables "
                "list, plus optional service/capability declarations that run after the "
                "import under the same approval. This does NOT create the app. The user "
                "confirms the card, then the backend creates the app and runs a dry-run; "
                "the user explicitly approves the real import. Do not call app_create, "
                "app_import_rows, app_add_service or app_set_capabilities for this plan. "
                "Dashboard only."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_get_plan,
            name="app_get_plan",
            description=(
                "Read a setup plan's status, confirmed schema and dry-run/import result. "
                "Use it after the user confirms or executes a plan instead of re-inspecting."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_cancel_plan,
            name="app_cancel_plan",
            description=(
                "Cancel a MiniApp setup plan and remove its empty app. Only valid before "
                "the real import; after import use app_delete with its normal approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_list,
            name="app_list",
            description=(
                "List the lightweight apps owned by the current user and created by this agent. "
                "Returns app_id, name, description and row_count."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_list_bindings,
            name="app_list_bindings",
            description=(
                "List service bindings for a MiniApp: the implicit builtin.dataset "
                "binding plus any connector/external bindings. Binding changes are "
                "owner/admin actions in the dashboard."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_add_service,
            name="app_add_service",
            description=(
                "Declare a read-only external service for a MiniApp page: one "
                "connector instance, an allowlist of read-only tools, URL prefixes, "
                "cache TTL and per-minute limit. Owner/admin only; the owner must "
                "also enable external.fetch before the page can call it."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_list_services,
            name="app_list_services",
            description="List the external services declared for a MiniApp.",
        ),
        StructuredTool.from_function(
            coroutine=app_remove_service,
            name="app_remove_service",
            description="Remove a declared external service and its cached responses. "
            "Owner/admin only.",
        ),
        StructuredTool.from_function(
            coroutine=app_open,
            name="app_open",
            description=(
                "Open an existing lightweight app in the chat. Call this when the user "
                "asks to open, show or use an app; the dashboard renders an app card "
                "with an inline preview and an open button. On IM channels this returns "
                "a personal one-time sign-in link in a private chat when the sender is "
                "bound to the app owner, otherwise a public link + plain text."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_share_link,
            name="app_share_link",
            description=(
                "Get a channel-ready entry for a lightweight app in ONE call. In a "
                "private IM chat whose sender is /bind-verified as the app owner, this "
                "returns a personal one-time sign-in link (valid 10 minutes, never "
                "forward it); otherwise it reuses or creates the read-only public link "
                "and, when available, a cover. Use this for chat/IM requests such as "
                "'give me the entry/link/card for X'. Public results may include a "
                "sharing notice (public/private tables, masked or unmarked PII); relay "
                "that notice to the user in their language. Treat the returned text as "
                "the final answer: do not browse, screenshot, curl, query the database, "
                "read source code, download or self-host the app afterwards."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_create,
            name="app_create",
            description=(
                "Create a lightweight app (single-file HTML UI backed by a CSV table) in the "
                "agent workspace. Define 1-50 columns up front, then call app_write_html to "
                "build the interface and app_append_row to add data. Use this when the user "
                "asks for a small tracker, dashboard, list or calculator-like tool."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_create_from_template,
            name="app_create_from_template",
            description=(
                "Create an empty app from an expert-provided app template "
                "(schema + HTML shell). The template carries no user data; use it "
                "when the user asks for a standard tool such as a ledger or tracker."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_add_table,
            name="app_add_table",
            description=(
                "Add another table (its own CSV and columns) to an existing lightweight "
                "app. Use it when one app needs several datasets, e.g. a todo list and a "
                "list of projects."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_update_schema,
            name="app_update_schema",
            description=(
                "Append columns to an existing lightweight app. Existing data is preserved; "
                "new cells start empty. Use this instead of deleting and recreating the app."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_alter_schema,
            name="app_alter_schema",
            description=(
                "Rename, drop or retype columns of a lightweight app. Every "
                "existing row is migrated; the change is rejected when a row "
                "does not fit the new schema."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_set_table_visibility,
            name="app_set_table_visibility",
            description=(
                "Expose or hide one table on the app's public read-only link. The main "
                "table is public by default; add-on tables are private. Call this before "
                "sharing when the public link should include (or must exclude) a table."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_set_capabilities,
            name="app_set_capabilities",
            description=(
                "Replace the capability manifest that gates what the sandboxed app page "
                "may request from the host. Use it to remove high-risk actions the app "
                "does not need, or to grant ai.generate / game.launch / external.fetch / "
                "events.emit / schedules.manage when the app really uses them."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_write_html,
            name="app_write_html",
            description=(
                "Write the complete HTML UI for a lightweight app, replacing the previous "
                "version. The page can read, append, update and delete rows through "
                "window.octopApp."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_read_data,
            name="app_read_data",
            description=(
                "Read a page of rows from a lightweight app together with its column "
                "definitions. `row_meta` is parallel to `rows` and carries the row_id "
                "and version needed by app_update_row / app_delete_row."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_append_row,
            name="app_append_row",
            description=(
                "Append one validated row to a lightweight app. Values are coerced to the "
                "column type; enums must match one of their declared values."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_update_row,
            name="app_update_row",
            description=(
                "Replace one row of a lightweight app. Pass expected_version to "
                "reject a stale edit instead of overwriting another user's change."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_delete_row,
            name="app_delete_row",
            description=(
                "Delete one row of a lightweight app. Pass expected_version to "
                "reject a stale delete."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_import_rows,
            name="app_import_rows",
            description=(
                "Import rows from a workspace file (CSV/TSV/JSON/.xlsx/.xls) into a "
                "lightweight app table. Rows that fail validation are skipped and "
                "counted. Use it to bulk-load data the user provided or exported."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_save_asset,
            name="app_save_asset",
            description=(
                "Store an image next to the app (a book cover, an icon). The app "
                "page cannot load remote images, so covers have to be saved "
                "here and shown through octopApp.asset(name). PNG, JPEG, WebP "
                "or GIF, at most 5 MB."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_set_preview,
            name="app_set_preview",
            description=(
                "Set the preview image (cover) of a lightweight app. The preview "
                "is one self-contained SVG document that the app list shows as "
                "the app's thumbnail. Draw it yourself with a 1200x630 viewBox, "
                "inline styles only, no scripts, no event handlers and no "
                "remote references. Call it once the app's look is settled."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_list,
            name="app_integration_list",
            description=(
                "List this MiniApp's inbound sources and subscriptions, outbound "
                "webhooks, schedules and external services. Read-only; owner/admin only."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_health,
            name="app_integration_health",
            description=(
                "Diagnose this MiniApp's integrations: source errors/staleness, "
                "disabled subscriptions, mapping drift against the current schema, "
                "target write permission, dead/failed deliveries and failing "
                "schedules. Returns issues with suggested actions. Read-only; "
                "owner/admin only."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_trace,
            name="app_integration_trace",
            description=(
                "Trace one event into this MiniApp: source, delivery attempts and "
                "results, inbox row and imported app rows. Accepts an internal "
                "event id or the sender's external id. Read-only; owner/admin only."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_create_ingress,
            name="app_integration_create_ingress",
            description=(
                "Create an app-owned inbound endpoint (source + subscription + "
                "HMAC secret). Returns hook_url and the secret ONCE; show it to "
                "the user and never repeat it later. Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_update_ingress,
            name="app_integration_update_ingress",
            description=(
                "Update an app-owned inbound endpoint: name, mode, event type, "
                "mapping, rate limit or enabled. Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_delete_ingress,
            name="app_integration_delete_ingress",
            description=(
                "Delete an app-owned inbound endpoint, its subscription and its "
                "source. Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_test_ingress,
            name="app_integration_test_ingress",
            description=(
                "Send one test event through an app-owned inbound endpoint and "
                "let the worker deliver it. Use it after creating/updating an "
                "ingress. Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_create_webhook,
            name="app_integration_create_webhook",
            description=(
                "Create an app-owned outbound webhook. The signing secret is not "
                "returned to the agent; the owner copies it from the app's "
                "Outbound Webhooks section. Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_update_webhook,
            name="app_integration_update_webhook",
            description=(
                "Update an outbound webhook's name, URL, event types or enabled "
                "state. Secret rotation stays in the Dashboard. Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_delete_webhook,
            name="app_integration_delete_webhook",
            description=(
                "Delete an outbound webhook and its source/subscription. Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_integration_test_webhook,
            name="app_integration_test_webhook",
            description=(
                "Ingest one test event for an outbound webhook and let the worker "
                "POST it. Check app_integration_health for the delivery result. "
                "Requires user approval."
            ),
        ),
        StructuredTool.from_function(
            coroutine=app_delete,
            name=APP_DELETE_TOOL_NAME,
            description=(
                "Permanently delete a lightweight app and all of its data. The user must "
                "approve this call before it runs."
            ),
        ),
    ]
