"""Light app lifecycle: workspace files + control-plane metadata.

Workspace layout (workspace-relative, resolved through ``BackendWorkspace``)::

    apps/<app_id>/app.html      rendered UI (written by the agent)
    apps/<app_id>/data.csv      rows; header == schema column names
    apps/<app_id>/schema.json   column definitions
    apps/<app_id>/manifest.json human-readable snapshot
    apps/<app_id>/preview.svg   cover image drawn by the agent

Schema and data updates are staged to ``*.new`` files and committed through
``.pending.json`` so an interrupted write is finished by the next read instead
of leaving the pair truncated or inconsistent.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import secrets
import threading
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from octop_harness.backends import BackendWorkspace
from octop_harness.security.ssrf import validate_agent_fetch_url

from octop.infra.apps import access as app_access
from octop.infra.apps import aggregate as app_aggregate
from octop.infra.apps import capabilities as app_capabilities
from octop.infra.apps import limits as app_limits
from octop.infra.apps.importer import parse_import_file
from octop.infra.apps.inspect import detect_pii, inspect_file
from octop.infra.apps.models import (
    MAIN_TABLE_KEY,
    MAX_APPS_PER_OWNER,
    MAX_COLLABORATORS,
    MAX_CSV_BYTES,
    MAX_GUESTS,
    MAX_LABEL_LEN,
    MAX_ROW_READ,
    MAX_ROWS,
    MAX_TABLES_PER_APP,
    PII_MASK,
    TABLE_KEY_RE,
    Column,
    Schema,
    extend_schema,
    parse_csv,
    parse_schema_payload,
    redact_rows,
    render_csv,
    validate_app_id,
    validate_collaborator_permission,
    validate_description,
    validate_html,
    validate_name,
    validate_row,
    validate_table_key,
)
from octop.infra.apps.pii import find_unmarked_pii
from octop.infra.apps.pii import redact_text as redact_pii_text
from octop.infra.apps.preview import PREVIEW_FILENAME, validate_preview_svg
from octop.infra.apps.templates import (
    AGENT_SOURCE,
    SYSTEM_SOURCE,
    SYSTEM_TEMPLATE_ROOT,
    TemplateInfo,
    agent_template_info,
    clean_template_id,
    list_system_templates,
    read_system_template,
    read_system_template_services,
)
from octop.infra.apps.viewgen import render_html as render_view_html
from octop.infra.apps.viewgen import render_multi_html, suggest_view
from octop.infra.connectors.builder import mcp_server_name
from octop.infra.connectors.catalog import get_catalog_entry
from octop.infra.connectors.gateway.adapters.douban import COVER_URL_PREFIXES
from octop.infra.connectors.gateway.adapters.http_extract import normalize_request_headers
from octop.infra.connectors.gateway.registry import mcp_tools_for_kind
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.app_access import (
    APP_ACCESS_APPROVED,
    APP_ACCESS_PENDING,
    APP_ACCESS_REJECTED,
    AppAccessRequestRepo,
    AppAccessRequestRow,
    is_unique_violation,
)
from octop.infra.db.repos.app_access_codes import AppAccessCodeRepo, AppAccessCodeRow
from octop.infra.db.repos.app_bindings import (
    BINDING_PROVIDERS,
    BUILTIN_BINDING_ID,
    BUILTIN_SERVICE_ID,
    LightAppBindingRepo,
    LightAppBindingRow,
)
from octop.infra.db.repos.app_events import AppEventRepo, AppEventRow
from octop.infra.db.repos.app_ingress import LightAppIngressRepo
from octop.infra.db.repos.app_schedules import (
    LightAppScheduleRepo,
    LightAppScheduleRow,
    schedule_jitter,
)
from octop.infra.db.repos.app_services import (
    LightAppServiceRepo,
    LightAppServiceRow,
)
from octop.infra.db.repos.app_setup_plans import (
    CANCELLABLE_PLAN_STATUSES,
    CONFIRMABLE_PLAN_STATUSES,
    EXECUTABLE_PLAN_STATUSES,
    PLAN_CANCELLED,
    PLAN_CONFIRMED,
    PLAN_DRY_RUN_FAILED,
    PLAN_DRY_RUN_READY,
    PLAN_EXPIRED,
    PLAN_IMPORT_PARTIAL,
    PLAN_IMPORTED,
    PLAN_IMPORTING,
    PLAN_SUPERSEDED,
    AppSetupPlanRepo,
    AppSetupPlanRow,
)
from octop.infra.db.repos.app_webhooks import LightAppWebhookRepo
from octop.infra.db.repos.apps import (
    PERMISSION_RANK,
    LightAppCollaboratorRow,
    LightAppDataRow,
    LightAppPublicationRow,
    LightAppRepo,
    LightAppRow,
    LightAppSourceRow,
)
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.external_sources import ExternalSourceRepo
from octop.infra.db.repos.external_subscriptions import ExternalSubscriptionRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.users import UserRepo, UserRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.principals import Principal, principal_from_user_row
from octop.infra.utils.ulid import new_short_id, new_ulid

logger = logging.getLogger(__name__)

# Access requests can ask for any collaborator role, including participate.
_ACCESS_ROLE_RANK = {
    "participate": 0,
    "view": 1,
    "edit_data": 2,
    "edit_structure": 3,
}
_AGENT_ACCESS_ROLES = frozenset({"edit_data", "edit_structure"})


def _access_rank(role: str | None) -> int:
    return _ACCESS_ROLE_RANK.get(role or "", -1)


APPS_DIR = "apps"
FILE_HTML = "app.html"
FILE_DATA = "data.csv"
FILE_SCHEMA = "schema.json"
FILE_ROW_IDS = "row_ids.json"
FILE_MANIFEST = "manifest.json"
FILE_PENDING = ".pending.json"
FILE_PREVIEW = PREVIEW_FILENAME
"""Cover image of an app, drawn by the agent as SVG."""
TABLES_DIR = "tables"
STAGED_SUFFIX = ".new"
ASSETS_DIR = "assets"

MAX_ASSET_BYTES = 5 * 1024 * 1024

MAX_FAILED_ROWS = 200
"""Largest per-row failure list returned by one import."""
"""Largest asset an app may store (covers book covers and small icons)."""

BACKUP_FORMAT = "octop.light-app.backup"
BACKUP_VERSION = 3
MAX_BACKUP_BYTES = 64 * 1024 * 1024
"""Largest backup archive accepted for restore."""

MAX_BACKUP_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
"""Cap on the expanded archive; guards against zip bombs."""

ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
"""Asset file names: no path separators, no leading dot, no traversal."""

# The app page may not load remote images, so a cover or an icon has to be
# stored next to the app. SVG is deliberately absent: it can carry script.
_ASSET_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/gif", b"GIF8"),
)

_APP_LOCKS: dict[str, asyncio.Lock] = {}
_APP_LOCKS_GUARD = threading.Lock()


def _lock_for(app_id: str) -> asyncio.Lock:
    with _APP_LOCKS_GUARD:
        lock = _APP_LOCKS.get(app_id)
        if lock is None:
            lock = asyncio.Lock()
            _APP_LOCKS[app_id] = lock
        return lock


def _forget_lock(app_id: str) -> None:
    with _APP_LOCKS_GUARD:
        _APP_LOCKS.pop(app_id, None)


def app_dir(app_id: str) -> str:
    return f"{APPS_DIR}/{validate_app_id(app_id)}"


def app_path(app_id: str, filename: str) -> str:
    return f"{app_dir(app_id)}/{filename}"


def table_file_names(table_key: str) -> tuple[str, str]:
    """Workspace-relative ``(schema, data)`` file names for a table.

    The main table keeps the historical ``schema.json`` / ``data.csv`` names so
    apps created before multi-table support keep working untouched.
    """
    if table_key == MAIN_TABLE_KEY:
        return FILE_SCHEMA, FILE_DATA
    return f"{TABLES_DIR}/{table_key}.schema.json", f"{TABLES_DIR}/{table_key}.csv"


def _is_atomic_app_file(name: str) -> bool:
    """Whether *name* may be written through the staged-write journal."""
    if name in (FILE_DATA, FILE_SCHEMA):
        return True
    if not name.startswith(f"{TABLES_DIR}/"):
        return False
    stem, _, filename = name[len(TABLES_DIR) + 1 :].partition(".")
    if not stem or not TABLE_KEY_RE.fullmatch(stem):
        return False
    return filename in ("csv", "schema.json")


class WorkspaceSource(Protocol):
    def workspace_for_agent(self, agent_id: str) -> BackendWorkspace | None: ...


class ExternalToolRunner(Protocol):
    """Runs one connector tool with the app owner's credentials.

    Implemented by the server wiring (``ConnectorService`` + gateway registry);
    kept as a protocol so the app service stays free of connector imports.
    """

    def run(self, *, instance_id: str, tool: str, args: dict[str, Any]) -> str: ...


class AppRecord(Protocol):
    """Read-only view of an app row used by the service (avoids repo imports above)."""

    @property
    def app_id(self) -> str: ...

    @property
    def agent_id(self) -> str: ...

    @property
    def owner_user_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def row_count(self) -> int: ...

    @property
    def html_bytes(self) -> int: ...

    @property
    def data_bytes(self) -> int: ...

    @property
    def storage_version(self) -> int: ...

    @property
    def capabilities_json(self) -> str: ...

    @property
    def previous_html(self) -> str: ...

    @property
    def previous_html_at(self) -> int | None: ...

    @property
    def created_at(self) -> int: ...

    @property
    def updated_at(self) -> int: ...


@dataclass(frozen=True)
class AppView:
    app_id: str
    name: str
    description: str
    agent_id: str
    agent_name: str
    owner_user_id: int
    owner_username: str
    permission: str
    row_count: int
    html_bytes: int
    data_bytes: int
    created_at: int
    updated_at: int
    has_preview: bool = False
    capabilities: tuple[str, ...] = ()
    previous_html_at: int | None = None
    agent_owner_user_id: int | None = None

    @property
    def has_html(self) -> bool:
        """Whether the agent has written the app UI yet."""
        return self.html_bytes > 0


@dataclass(frozen=True)
class ShareView:
    user_id: int
    username: str
    display_name: str
    permission: str


@dataclass(frozen=True)
class AppFile:
    """One file of an app's workspace directory."""

    name: str
    path: str
    size: int | None = None


@dataclass(frozen=True)
class AppTableView:
    """One table of an app as the dashboard and tools see it."""

    table_key: str
    label: str
    row_count: int
    data_version: int
    is_main: bool
    public_visible: bool
    row_scope: str = "app"
    rules: dict[str, Any] = field(default_factory=dict)
    member_visible: bool = False


@dataclass(frozen=True)
class ServiceView:
    """One R2.1 service declaration exposed to the dashboard and page SDK."""

    service_id: str
    name: str
    provider: str
    connector_instance_id: str
    allowed_tools: tuple[str, ...]
    allowed_url_prefixes: tuple[str, ...]
    request_headers: dict[str, str]
    cache_ttl_seconds: int
    max_calls_per_minute: int
    public_live: bool
    enabled: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class ServiceCallResult:
    value: Any
    cached: bool
    expires_at: int | None


@dataclass(frozen=True)
class AppEventView:
    event_id: str
    event_type: str
    source: str
    summary: str
    payload: dict[str, Any]
    created_at: int
    acknowledged_at: int | None


@dataclass(frozen=True)
class ScheduleView:
    """One application-level interval job."""

    schedule_id: str
    app_id: str
    action: str
    target: dict[str, Any]
    interval_seconds: int
    next_run_at: int
    last_run_at: int | None
    last_status: str
    last_error: str
    failure_count: int
    enabled: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class PublicationStatus:
    """Whether the public link still shows the current app version."""

    published_at: int | None
    stale: bool
    reasons: tuple[str, ...] = ()


IMPORT_MODE_APPEND = "append"
IMPORT_MODE_REPLACE = "replace"
IMPORT_MODES = (IMPORT_MODE_APPEND, IMPORT_MODE_REPLACE)

SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SERVICE_PROVIDERS = ("connector", "external")
SERVICE_MAX_RESPONSE_BYTES = 5 * 1024 * 1024

APP_EVENT_RETENTION_SECONDS = 30 * 24 * 3600
APP_EVENT_KEEP = 500
APP_EVENT_MAX_PAYLOAD_BYTES = 64 * 1024

ROW_HISTORY_RETENTION_DAYS = 90
"""Row-history entries older than this are trimmed by the app scheduler."""

IMPORT_DIR = "inbound/imports"
MAX_IMPORT_ERRORS = 5
"""Row-level validation failures reported per import (the rest are counted)."""

SOURCE_KIND_WORKSPACE_FILE = "workspace_file"
SOURCE_KIND_EXTERNAL = "external"
SOURCE_KINDS = (SOURCE_KIND_WORKSPACE_FILE, SOURCE_KIND_EXTERNAL)
SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_ERROR = "error"
MAX_SCHEDULE_SECONDS = 30 * 24 * 3600
MAX_BACKOFF_SECONDS = 24 * 3600
"""Auto-sync failure backoff cap (a day between retries, however often it fails)."""
MAX_SCHEDULES_PER_APP = 20

SCHEDULE_ACTION_SYNC_SOURCE = "sync_source"
SCHEDULE_ACTION_SNAPSHOT_PUBLICATION = "snapshot_publication"
SCHEDULE_ACTIONS = (
    SCHEDULE_ACTION_SYNC_SOURCE,
    SCHEDULE_ACTION_SNAPSHOT_PUBLICATION,
)


@dataclass(frozen=True)
class ImportResult:
    """Outcome of one manual import into a single table."""

    imported: int
    skipped: int
    errors: list[str]
    updated: int = 0
    """Rows replaced in place by an upsert key."""
    warnings: list[str] = field(default_factory=list)
    """Non-fatal notes, e.g. columns that look like sensitive data."""
    would_import: int = 0
    """Rows a dry run would have written."""
    dry_run: bool = False
    """True when nothing was written."""
    pii_columns: list[str] = field(default_factory=list)
    """Columns detected or marked as sensitive during this import."""
    blocked_pii: list[str] = field(default_factory=list)
    """Sensitive columns whose values were not written (``include_pii=false``)."""
    failed_rows: list[dict[str, Any]] = field(default_factory=list)
    """Up to ``MAX_FAILED_ROWS`` rejected rows with their errors, for retry."""


def import_result_payload(result: ImportResult) -> dict[str, Any]:
    """Serializable summary stored on a setup plan and returned to the card."""
    return {
        "imported": result.imported,
        "updated": result.updated,
        "skipped": result.skipped,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
        "would_import": result.would_import,
        "dry_run": result.dry_run,
        "pii_columns": list(result.pii_columns),
        "blocked_pii": list(result.blocked_pii),
        "failed_rows": list(result.failed_rows),
    }


@dataclass(frozen=True)
class BindingView:
    """A service binding as the dashboard and agent tools see it."""

    binding_id: str
    provider: str
    service_id: str
    config: dict[str, Any]
    enabled: bool
    version: int
    builtin: bool


@dataclass(frozen=True)
class SourceView:
    """A data-source binding as the dashboard and tools see it."""

    source_id: str
    app_id: str
    table_key: str
    kind: str
    mode: str
    schedule_seconds: int
    params: dict[str, Any]
    mapping: dict[str, str]
    last_run_at: int | None
    last_status: str | None
    last_error: str | None
    last_row_count: int | None
    created_by: int
    created_at: int
    updated_at: int


def _safe_import_path(path: str) -> str:
    """Normalize a workspace-relative import source path.

    Rejects empty paths, traversal, and any hidden segment (``.octop`` system
    files, ``.pending.json`` journal, …) so imports cannot read credentials or
    internal state into app data.
    """
    clean = "/".join(part for part in path.strip().split("/") if part and part != ".")
    if (
        not clean
        or clean.startswith("/")
        or any(part.startswith(".") or part == ".." for part in clean.split("/"))
    ):
        raise _invalid(ErrorCode.APP_ROW_INVALID, f"invalid import path: {path!r}")
    return clean


def _safe_import_filename(filename: str) -> str:
    """Basename of an uploaded file for storage under ``inbound/imports/``."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = " ".join(name.split())
    if not name or name.startswith(".") or "/" in name:
        name = "data.csv"
    return name[:100]


def _file_stem(filename: str) -> str:
    name = _safe_import_filename(filename)
    return name.rsplit(".", 1)[0].strip()[:100] or "工作表"


def _plan_tables_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read the plan's table list, accepting the legacy single-table shape."""
    tables = payload.get("tables")
    if isinstance(tables, list) and tables:
        return [dict(item) for item in tables if isinstance(item, Mapping)]
    if payload.get("columns"):
        return [dict(payload)]
    return []


def _plan_services_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    services = payload.get("services")
    if not isinstance(services, list):
        return []
    return [dict(item) for item in services if isinstance(item, Mapping)]


def _plan_capabilities_from_payload(payload: Mapping[str, Any]) -> list[str] | None:
    capabilities = payload.get("capabilities")
    if capabilities is None:
        return None
    if not isinstance(capabilities, list):
        return None
    return [str(item) for item in capabilities]


def _aggregate_import_payloads(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Combine per-table dry-run/import payloads for the plan card summary."""

    def collect(key: str) -> list[Any]:
        out: list[Any] = []
        for item in items:
            value = item.get(key)
            if isinstance(value, list):
                out.extend(value)
        return out

    return {
        "imported": sum(int(item.get("imported") or 0) for item in items),
        "updated": sum(int(item.get("updated") or 0) for item in items),
        "skipped": sum(int(item.get("skipped") or 0) for item in items),
        "would_import": sum(int(item.get("would_import") or 0) for item in items),
        "dry_run": all(bool(item.get("dry_run")) for item in items) if items else False,
        "errors": collect("errors")[: MAX_IMPORT_ERRORS * max(1, len(items))],
        "warnings": collect("warnings"),
        "pii_columns": list(dict.fromkeys(str(value) for value in collect("pii_columns"))),
        "blocked_pii": list(dict.fromkeys(str(value) for value in collect("blocked_pii"))),
        "failed_rows": collect("failed_rows")[:MAX_FAILED_ROWS],
    }


def _select_sheet_report(report: Mapping[str, Any], sheet: str | None) -> dict[str, Any]:
    sheets = list(report.get("sheets") or [])
    if not sheets:
        raise _invalid(ErrorCode.APP_ROW_INVALID, "file has no sheets")
    if sheet is None or not str(sheet).strip():
        return dict(sheets[0])
    wanted = str(sheet).strip()
    for item in sheets:
        if str(item.get("name") or "") == wanted:
            return dict(item)
    try:
        index = int(wanted)
    except ValueError:
        index = -1
    if 0 <= index < len(sheets):
        return dict(sheets[index])
    raise _invalid(ErrorCode.APP_ROW_INVALID, f"sheet not found: {sheet!r}")


_QUICK_COLUMN_TYPES = {"string", "text", "integer", "number", "boolean", "date", "datetime"}


def _quick_columns(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Turn inspection suggestions into a valid, editable schema."""
    columns: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(report.get("columns") or []):
        if not isinstance(item, Mapping):
            continue
        base = str(item.get("name_suggestion") or f"column_{index + 1}").strip()
        name = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_") or f"column_{index + 1}"
        candidate = name
        suffix = 2
        while candidate in used:
            candidate = f"{name}_{suffix}"
            suffix += 1
        used.add(candidate)
        column_type = str(item.get("type") or "string")
        if column_type not in _QUICK_COLUMN_TYPES:
            column_type = "string"
        label = (str(item.get("source") or candidate).strip() or candidate)[:100]
        column: dict[str, Any] = {
            "name": candidate,
            "label": label,
            "type": column_type,
            "required": False,
        }
        pii = item.get("pii")
        if isinstance(pii, str) and pii:
            column["pii"] = pii
        columns.append(column)
    if not columns:
        raise _invalid(ErrorCode.APP_SCHEMA_INVALID, "file has no usable columns")
    return columns


def _json_path(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _extract_records(output: str, result_path: str) -> list[dict[str, str]]:
    """Parse a connector tool's text output into rows.

    ``result_path`` is a dot path (e.g. ``data.items``) pointing at the array
    of objects inside the returned JSON; empty means the output itself is the
    array. Every element is stringified — column validation happens later.
    """
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise _invalid(
            ErrorCode.APP_SOURCE_INVALID, f"tool output is not JSON: {exc.msg}"
        ) from None
    if result_path:
        node: Any = payload
        for part in result_path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    f"result_path {result_path!r} not found in tool output",
                )
    else:
        node = payload
    if not isinstance(node, list) or any(not isinstance(item, dict) for item in node):
        raise _invalid(
            ErrorCode.APP_SOURCE_INVALID,
            "tool result must be an array of objects (adjust result_path)",
        )
    records: list[dict[str, str]] = []
    for item in node:
        records.append(
            {
                str(key).strip(): "" if value is None else str(value)
                for key, value in item.items()
                if str(key).strip()
            }
        )
    return records


def _history_actor(user_id: int | None) -> str:
    """Stable actor label for the row-history ledger."""
    return f"user:{user_id}" if user_id is not None else "_system"


def _invalid(code: ErrorCode, reason: str, **details: object) -> OctopError:
    return OctopError(code, reason, details={**details})


def _entry_field(entry: Any, key: str) -> Any:
    """Read a field from an ``ls`` entry (mapping or object, per backend)."""
    if isinstance(entry, Mapping):
        return entry.get(key)
    return getattr(entry, key, None)


def _zip_entries(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _read_backup_zip(
    data: bytes,
) -> tuple[
    dict[str, Any],
    dict[str, tuple[str, bytes, bytes, bytes]],
    str | None,
    str | None,
    list[tuple[str, bytes]],
]:
    """Read and validate a backup archive without touching the workspace."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > MAX_BACKUP_UNCOMPRESSED_BYTES:
            raise ValueError("archive expands beyond the allowed size")
        names = {info.filename for info in infos}
        if FILE_MANIFEST not in names:
            raise ValueError("manifest.json is missing")
        # Read through a running budget: the per-entry header size can be
        # forged, so trust the bytes actually decompressed, not the manifest.
        remaining = MAX_BACKUP_UNCOMPRESSED_BYTES

        def read_entry(name: str) -> bytes:
            nonlocal remaining
            with archive.open(name) as handle:
                content = handle.read(remaining + 1)
            if len(content) > remaining:
                raise ValueError("archive expands beyond the allowed size")
            remaining -= len(content)
            return content

        try:
            manifest = json.loads(read_entry(FILE_MANIFEST))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("manifest.json is not valid JSON") from None
        if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
            raise ValueError("not an Octop MiniApp backup")
        version = manifest.get("version")
        if not isinstance(version, int) or version > BACKUP_VERSION:
            raise ValueError("backup was made by a newer version")
        if not isinstance(manifest.get("app"), dict):
            raise ValueError("manifest is missing app metadata")
        raw_tables = manifest.get("tables")
        if not isinstance(raw_tables, list) or not raw_tables:
            raise ValueError("manifest is missing tables")
        tables: dict[str, tuple[str, bytes, bytes, bytes]] = {}
        for item in raw_tables:
            if not isinstance(item, dict):
                raise ValueError("manifest has an invalid table entry")
            key = validate_table_key(str(item.get("table_key") or ""))
            if key in tables:
                raise ValueError(f"duplicate table: {key}")
            schema_name = f"{TABLES_DIR}/{key}/{FILE_SCHEMA}"
            csv_name = f"{TABLES_DIR}/{key}/{FILE_DATA}"
            row_ids_name = f"{TABLES_DIR}/{key}/{FILE_ROW_IDS}"
            if schema_name not in names:
                raise ValueError(f"table {key} has no schema")
            label = str(item.get("label") or "")[:MAX_LABEL_LEN]
            tables[key] = (
                label,
                read_entry(schema_name),
                read_entry(csv_name) if csv_name in names else b"",
                read_entry(row_ids_name) if row_ids_name in names else b"",
            )
        html: str | None = None
        if FILE_HTML in names:
            try:
                html = read_entry(FILE_HTML).decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("app.html is not valid UTF-8") from None
        assets: list[tuple[str, bytes]] = []
        for name in names:
            if not name.startswith(f"{ASSETS_DIR}/"):
                continue
            asset_name = name[len(ASSETS_DIR) + 1 :]
            if not asset_name or "/" in asset_name or "\\" in asset_name:
                continue
            assets.append((asset_name, read_entry(name)))
        preview: str | None = None
        if FILE_PREVIEW in names:
            try:
                preview = read_entry(FILE_PREVIEW).decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("preview.svg is not valid UTF-8") from None
    return manifest, tables, html, preview, assets


def _rules_from_json(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _service_value(raw: str) -> Any:
    """Return JSON values as objects; plain text stays a string."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _service_url_allowed(prefixes: Sequence[str], url: str) -> bool:
    return any(url == prefix or url.startswith(f"{prefix}/") for prefix in prefixes)


def _validate_webhook_url(raw: str) -> str:
    """Reject destinations the outbound webhook delivery would refuse."""
    url = raw.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("webhook URL must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("webhook URL must not contain credentials")
    validate_agent_fetch_url(url)
    return url


def _preflight_result(
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    summary: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "ok": not errors and not warnings,
        "can_restore": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "issues": [*errors, *warnings],
        "summary": dict(summary),
    }


def _fetch_service_image(url: str, *, timeout: float = 20.0) -> bytes:
    """Fetch a public image with the same SSRF/size guards as http-extract."""
    current = validate_agent_fetch_url(url)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(6):
            validate_agent_fetch_url(current)
            # Some public image CDNs (e.g. Douban) reject hotlinks without a
            # Referer; the image's own origin is what a browser would send.
            parts = urlsplit(current)
            response = client.get(
                current,
                headers={"Referer": f"{parts.scheme}://{parts.netloc}/"},
            )
            if 300 <= response.status_code < 400:
                location = (response.headers.get("location") or "").strip()
                if not location:
                    break
                current = str(response.url.join(location))
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("image/"):
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, "service image is not an image")
            data = response.content
            if len(data) > MAX_ASSET_BYTES:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    f"service image exceeds the {MAX_ASSET_BYTES // (1024 * 1024)} MB limit",
                )
            return data
    raise _invalid(ErrorCode.APP_SOURCE_INVALID, "too many redirects")


def _batch_prefix(
    source_id: str,
    records: Sequence[Mapping[str, Any]],
    *,
    table: str,
    mapping: Mapping[str, str] | None = None,
    upsert_key: Sequence[str] | None = None,
    include_pii: bool = False,
) -> str:
    """Stable idempotency prefix for one source batch.

    The record payload plus the target table/mapping is hashed, so retrying
    the same file/tool output after a crash (or after rows were committed but
    the run state was not) skips rows that were already written. Config changes
    produce a new prefix and are imported normally. ``client_request_id`` is
    unique per app, hence the source id is part of the prefix.
    """
    payload = json.dumps(
        {
            "source": source_id,
            "table": table,
            "mapping": dict(mapping or {}),
            "upsert_key": list(upsert_key or []),
            "include_pii": include_pii,
            "records": records,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"src:{source_id}:{digest}"


def _normalize_rows(schema: Schema, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Fill columns added after a row was written with empty cells."""
    names = schema.names
    return [{name: item.get(name, "") for name in names} for item in rows]


def _row_sort_key(value: object, numeric: bool) -> tuple[int, float, str]:
    """Sort key for one cell.

    Numeric columns compare as numbers so ``9`` sorts before ``10``; anything
    that does not parse falls back to text and sorts after the numbers.
    """
    text = str(value if value is not None else "").strip()
    if numeric:
        try:
            return (0, float(text.replace(",", "")), "")
        except ValueError:
            return (1, 0.0, text.casefold())
    return (0, 0.0, text.casefold())


def _parse_sort(schema: Schema, sort: str | None) -> tuple[str, bool] | None:
    """Resolve a ``col`` / ``-col`` sort spec against the schema."""
    text = str(sort or "").strip()
    if not text:
        return None
    descending = text.startswith("-")
    name = (text[1:] if descending else text).strip()
    if not name:
        return None
    if schema.column(name) is None:
        raise _invalid(
            ErrorCode.APP_SCHEMA_INVALID,
            f"unknown sort column: {name!r}",
            column=name,
        )
    return name, descending


def _with_detected_pii(schema: Schema, rows: Sequence[Mapping[str, str]]) -> Schema:
    """Mark columns whose name or sample values look sensitive."""
    columns: list[Column] = []
    changed = False
    for column in schema.columns:
        if column.pii:
            columns.append(column)
            continue
        samples = [str(row.get(column.name, "")) for row in rows[:50]]
        kind = detect_pii(column.name, samples)
        if kind is None and column.label != column.name:
            kind = detect_pii(column.label, samples)
        if kind is None:
            columns.append(column)
        else:
            columns.append(replace(column, pii=kind))
            changed = True
    return replace(schema, columns=tuple(columns)) if changed else schema


def _pii_names(schema: Schema) -> list[str]:
    return [column.name for column in schema.columns if column.pii]


def _apply_pii(
    schema: Schema,
    rows: list[dict[str, str]],
    *,
    redact_pii: bool,
) -> tuple[Schema, list[dict[str, str]]]:
    schema = _with_detected_pii(schema, rows)
    return schema, (redact_rows(schema, rows, redact_text=True) if redact_pii else rows)


def _apply_pii_policy(
    schema: Schema,
    rows: list[dict[str, str]],
    owners: list[int | None],
    *,
    pii_policy: str,
    viewer_user_id: int | None,
) -> tuple[Schema, list[dict[str, str]], list[bool]]:
    """Apply a per-row PII policy and return a mask flag for every row."""
    schema = _with_detected_pii(schema, rows)
    if pii_policy == "visible":
        return schema, rows, [False] * len(rows)
    if pii_policy == "masked":
        return schema, redact_rows(schema, rows, redact_text=True), [True] * len(rows)
    out: list[dict[str, str]] = []
    flags: list[bool] = []
    for row, owner in zip(rows, owners, strict=True):
        masked = viewer_user_id is None or owner != viewer_user_id
        out.append(redact_rows(schema, [row], redact_text=True)[0] if masked else row)
        flags.append(masked)
    return schema, out, flags


def _reject_unmarked_pii(
    schema: Schema,
    values: Mapping[str, object],
    *,
    table: str,
) -> None:
    """Refuse a write that would put PII into an unclassified free-text column."""
    hits = find_unmarked_pii(
        ((column.name, column.pii, column.type) for column in schema.columns),
        values,
    )
    if not hits:
        return
    column, hit = hits[0]
    raise _invalid(
        ErrorCode.APP_PII_UNMARKED,
        (
            f"column {column!r} contains {hit.kind} data; mark it with pii "
            "or move the value to a column marked as sensitive"
        ),
        table=table,
        column=column,
        kind=hit.kind,
    )


def _sniff_asset_mime(data: bytes) -> str | None:
    """Return the media type of an image by its magic bytes.

    The content type header of an upload is not trusted: an SVG or an HTML
    document renamed to ``.png`` would end up in the app directory.
    """
    for mime, signature in _ASSET_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class AppService:
    def __init__(
        self,
        *,
        app_repo: LightAppRepo,
        agent_repo: AgentRepo,
        user_repo: UserRepo,
        workspaces: WorkspaceSource,
        external_tool_runner: ExternalToolRunner | None = None,
        secret_repo: SecretRepo | None = None,
        audit_repo: AuditRepo | None = None,
        access_repo: AppAccessRequestRepo | None = None,
        access_code_repo: AppAccessCodeRepo | None = None,
        binding_repo: LightAppBindingRepo | None = None,
        service_repo: LightAppServiceRepo | None = None,
        connector_repo: ConnectorRepo | None = None,
        event_repo: AppEventRepo | None = None,
        schedule_repo: LightAppScheduleRepo | None = None,
        webhook_repo: LightAppWebhookRepo | None = None,
        ingress_repo: LightAppIngressRepo | None = None,
        external_source_repo: ExternalSourceRepo | None = None,
        external_subscription_repo: ExternalSubscriptionRepo | None = None,
        setup_plan_repo: AppSetupPlanRepo | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._app_repo = app_repo
        self._agent_repo = agent_repo
        self._user_repo = user_repo
        self._workspaces = workspaces
        self._external_tool_runner = external_tool_runner
        self._secret_repo = secret_repo
        self._audit_repo = audit_repo
        self._access_repo = access_repo
        self._access_code_repo = access_code_repo
        self._binding_repo = binding_repo
        self._service_repo = service_repo
        self._connector_repo = connector_repo
        self._event_repo = event_repo
        self._schedule_repo = schedule_repo
        self._webhook_repo = webhook_repo
        self._ingress_repo = ingress_repo
        self._external_source_repo = external_source_repo
        self._external_subscription_repo = external_subscription_repo
        self._setup_plan_repo = setup_plan_repo
        self._clock = clock or time.time

    # ----- lookup / ACL -----

    def get(self, app_id: str) -> LightAppRow:
        row = self._app_repo.get(validate_app_id(app_id))
        if row is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return row

    def permission_for(self, row: AppRecord, user_id: int, *, is_admin: bool) -> str | None:
        if is_admin or row.owner_user_id == user_id:
            return "edit_structure"
        granted = self._app_repo.permission_for(row.app_id, user_id)
        if granted is not None:
            return granted
        # An agent owner can read every app hosted in their agent, including
        # apps another user created there. Explicit collaborator grants above
        # keep edit access intact; this fallback is read-only.
        if self.is_agent_owner(row.agent_id, user_id):
            return "view"
        return None

    def require(
        self,
        row: AppRecord,
        user_id: int,
        needed: str,
        *,
        is_admin: bool,
    ) -> str:
        granted = self.permission_for(row, user_id, is_admin=is_admin)
        if granted is None or PERMISSION_RANK.get(granted, 0) < PERMISSION_RANK[needed]:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        return granted

    def get_data_row(self, app_id: str, row_id: str) -> LightAppDataRow | None:
        """Resolve one stored row for access checks before a mutating route."""
        return self._app_repo.get_data_row(app_id, row_id)

    def authorize_actor(
        self,
        user_id: int,
        row: AppRecord,
        action: app_access.Action,
        *,
        table_key: str | None = None,
        row_record: LightAppDataRow | None = None,
    ) -> app_access.Decision:
        """Authorize a caller known only by user id (agent tools, webhooks)."""
        user_row = self._user_repo.get(user_id)
        if user_row is None or user_row.disabled:
            return app_access.deny("user not active")
        return self.authorize(
            principal_from_user_row(user_row),
            row,
            action,
            table_key=table_key,
            row_record=row_record,
        )

    def access_role(
        self,
        row: AppRecord,
        user_id: int,
        *,
        is_admin: bool,
    ) -> str | None:
        """Effective app role for the access kernel.

        ``permission_for`` collapses owner/admin into ``edit_structure``; the
        kernel needs to distinguish full management, so owner/admin become
        ``owner`` and the agent-owner read fallback stays ``view``.
        """
        if is_admin or row.owner_user_id == user_id:
            return app_access.ROLE_OWNER
        granted = self._app_repo.permission_for(row.app_id, user_id)
        if granted is not None:
            return granted
        if self.is_agent_owner(row.agent_id, user_id):
            return app_access.ROLE_VIEW
        return None

    def authorize(
        self,
        principal: Principal,
        row: AppRecord,
        action: app_access.Action,
        *,
        table_key: str | None = None,
        row_record: LightAppDataRow | None = None,
    ) -> app_access.Decision:
        """Resolve the caller's role and delegate to the single access kernel."""
        user_id = principal.user_id
        if user_id is None:
            return app_access.deny("no principal user")
        is_admin = (
            principal.user is not None
            and principal.user.kind == "member"
            and self.is_admin(user_id)
        )
        role = self.access_role(row, user_id, is_admin=is_admin)
        table = None
        if table_key is not None:
            table = next(
                (item for item in self.list_tables(row) if item.table_key == table_key),
                None,
            )
            if table is None:
                return app_access.deny("table not found", role=role)
        return app_access.authorize(
            principal,
            row,
            action,
            role=role,
            table=table,
            row=row_record,
            is_admin=is_admin,
        )

    def list_for_user(self, user_id: int, *, is_admin: bool) -> list[tuple[LightAppRow, str]]:
        # Admins list the same apps as everyone else: owned + explicitly
        # shared. Direct access to a known app id still bypasses ACL through
        # ``permission_for``; only the catalogue is scoped.
        del is_admin
        entries: dict[str, tuple[LightAppRow, str]] = {
            row.app_id: (row, "edit_structure") for row in self._app_repo.list_owned(user_id)
        }
        for row in self._app_repo.list_shared(user_id):
            permission = self._app_repo.permission_for(row.app_id, user_id)
            if permission is not None:
                entries[row.app_id] = (row, permission)
        # The agent owner can read every app hosted in their agents, even when
        # another user created it; explicit owned/shared grants win above.
        for agent in self._agent_repo.list_by_user(user_id, include_disabled=False):
            for row in self._app_repo.list_by_agent(agent.agent_id):
                entries.setdefault(row.app_id, (row, "view"))
        return sorted(
            entries.values(),
            key=lambda item: item[0].updated_at,
            reverse=True,
        )

    def view(
        self,
        row: AppRecord,
        permission: str,
        *,
        owners: dict[int, str] | None = None,
    ) -> AppView:
        owner_names = owners if owners is not None else {}
        if row.owner_user_id not in owner_names:
            owner = self._user_repo.get(row.owner_user_id)
            owner_names[row.owner_user_id] = owner.username if owner is not None else ""
        agent = self._agent_repo.get(row.agent_id)
        return AppView(
            app_id=row.app_id,
            name=row.name,
            description=row.description,
            agent_id=row.agent_id,
            agent_name=agent.name if agent is not None else "",
            owner_user_id=row.owner_user_id,
            owner_username=owner_names[row.owner_user_id],
            permission=permission,
            row_count=row.row_count,
            html_bytes=row.html_bytes,
            data_bytes=row.data_bytes,
            created_at=row.created_at,
            updated_at=row.updated_at,
            capabilities=tuple(app_capabilities.describe(row.capabilities_json)),
            previous_html_at=row.previous_html_at,
            agent_owner_user_id=agent.user_id if agent is not None else None,
        )

    def capabilities(self, row: AppRecord) -> frozenset[str]:
        """The page actions this app currently allows."""
        return app_capabilities.parse(row.capabilities_json)

    def capability_list(self, row: AppRecord) -> list[str]:
        return sorted(self.capabilities(row))

    def set_capabilities(self, row: AppRecord, values: Iterable[str]) -> LightAppRow:
        """Replace the page capability manifest; requires edit_structure upstream."""
        clean = app_capabilities.validate(values)
        self._app_repo.set_capabilities(row.app_id, app_capabilities.dump(clean))
        return self.get(row.app_id)

    def require_capability(self, row: AppRecord, capability: str) -> None:
        if capability not in self.capabilities(row):
            raise OctopError(
                ErrorCode.APP_CAPABILITY_DENIED,
                f"app does not allow {capability}",
            )

    async def views_for_app_ids(
        self, app_ids: Iterable[str], user_id: int, *, is_admin: bool
    ) -> list[AppView]:
        """Views for *app_ids* the user may open, preserving their order.

        Ids that no longer resolve (deleted app) or that the user cannot see are
        skipped. Used by the chat dock, whose ids come from the transcript.
        """
        owners: dict[int, str] = {}
        candidates: list[tuple[AppRecord, str]] = []
        for app_id in app_ids:
            try:
                row = self.get(app_id)
            except OctopError:
                continue
            permission = self.permission_for(row, user_id, is_admin=is_admin)
            if permission is None:
                continue
            candidates.append((row, permission))
        # Preview reads hit the workspace; do them concurrently so the chat
        # dock does not wait for one remote round trip per app.
        previews = await asyncio.gather(*(self.has_preview(row) for row, _permission in candidates))
        return [
            replace(
                self.view(row, permission, owners=owners),
                has_preview=preview,
            )
            for (row, permission), preview in zip(candidates, previews, strict=True)
        ]

    async def list_views_with_stats(self, user_id: int, *, is_admin: bool) -> list[AppView]:
        """List views, backfilling the cached file sizes for apps that predate them.

        Apps written before the file-size columns existed carry cached sizes of
        zero even though their files are on disk; listing the app directory
        once repairs the cache instead of showing them as "no interface yet".
        The preview flag is read from the workspace as well: the cover is an
        optional file, not a column.
        """
        owners: dict[int, str] = {}
        rows = self.list_for_user(user_id, is_admin=is_admin)
        views = [self.view(row, permission, owners=owners) for row, permission in rows]
        previews = await asyncio.gather(*(self.has_preview(row) for row, _permission in rows))
        needs_stats = [
            index for index, view in enumerate(views) if not (view.html_bytes and view.data_bytes)
        ]
        stats = await asyncio.gather(*(self._file_stats(rows[index][0]) for index in needs_stats))
        for index, (html_bytes, data_bytes) in zip(needs_stats, stats, strict=True):
            view = views[index]
            if (html_bytes, data_bytes) != (view.html_bytes, view.data_bytes):
                views[index] = replace(view, html_bytes=html_bytes, data_bytes=data_bytes)
        return [
            replace(view, has_preview=preview)
            for view, preview in zip(views, previews, strict=True)
        ]

    async def _file_stats(self, row: AppRecord) -> tuple[int, int]:
        """Cached (html_bytes, data_bytes), refreshed from the workspace when unknown.

        ``data_bytes`` covers every table's CSV, matching the aggregate the
        registry maintains on writes.
        """
        ws = await self._workspace(row.agent_id)
        sizes = await self._dir_sizes(ws, row.app_id)
        html_bytes = sizes.get(FILE_HTML, 0)
        if row.storage_version >= 2:
            # Rows live in the DB; the CSV is a create-time snapshot and must
            # not overwrite the live data_bytes cache.
            if html_bytes != row.html_bytes:
                self._app_repo.set_file_stats(
                    row.app_id,
                    html_bytes=html_bytes,
                    data_bytes=row.data_bytes,
                )
            return html_bytes, row.data_bytes
        data_bytes = 0
        for table in self.list_tables(row):
            _, data_file = table_file_names(table.table_key)
            size = sizes.get(data_file, 0)
            data_bytes += size
            registered = self._app_repo.get_table(row.app_id, table.table_key)
            if registered is not None and registered.data_bytes != size:
                self._app_repo.set_table_stats(
                    row.app_id,
                    table.table_key,
                    row_count=registered.row_count,
                    data_bytes=size,
                )
        if html_bytes != row.html_bytes or data_bytes != row.data_bytes:
            self._app_repo.set_file_stats(
                row.app_id,
                html_bytes=html_bytes,
                data_bytes=data_bytes,
            )
        return html_bytes, data_bytes

    async def _dir_sizes(self, ws: BackendWorkspace, app_id: str) -> dict[str, int]:
        """File sizes of the app directory, keyed by file name.

        Table files live in a ``tables/`` subdirectory and are keyed by their
        path relative to the app directory so they do not collide with the
        app's own files.
        """
        sizes: dict[str, int] = {}
        for prefix in ("", f"{TABLES_DIR}/"):
            result = await ws.als(
                app_path(app_id, prefix.rstrip("/")) if prefix else app_dir(app_id)
            )
            entries = list(getattr(result, "entries", None) or []) if result is not None else []
            for entry in entries:
                if _entry_field(entry, "is_dir"):
                    continue
                path = str(_entry_field(entry, "path") or "").replace("\\", "/")
                name = path.rsplit("/", 1)[-1]
                if prefix:
                    name = f"{prefix}{name}"
                size = _entry_field(entry, "size")
                if name and size is not None:
                    sizes[name] = int(size)
        return sizes

    def list_for_agent(self, agent_id: str, owner_user_id: int) -> list[LightAppRow]:
        return self._app_repo.list_for_agent(agent_id, owner_user_id)

    def is_agent_owner(self, agent_id: str, user_id: int) -> bool:
        agent = self._agent_repo.get(agent_id)
        return agent is not None and agent.user_id == user_id

    def is_admin(self, user_id: int) -> bool:
        """True when the acting user is a system administrator."""
        user = self._user_repo.get(user_id)
        return user is not None and user.role == "admin"

    def describe_access(
        self,
        *,
        agent_id: str,
        app_id: str | None,
        user_id: int,
        needed: str,
    ) -> dict[str, Any]:
        """Identity and ACL snapshot used by permission-error cards."""
        agent = self._agent_repo.get(agent_id)
        user = self._user_repo.get(user_id)
        row = self._app_repo.get(app_id) if app_id else None
        agent_owner = (
            self._user_repo.get(agent.user_id)
            if agent is not None and agent.user_id is not None
            else None
        )
        app_owner = self._user_repo.get(row.owner_user_id) if row is not None else None
        return {
            "agent_id": agent_id,
            "agent_owner": agent_owner.username if agent_owner is not None else "",
            "agent_shared": bool(getattr(agent, "is_shared", False)) if agent else False,
            "app_id": app_id or "",
            "app_owner": app_owner.username if app_owner is not None else "",
            "acting_user": user.username if user is not None else "",
            "needed": needed,
            "granted": (
                self.permission_for(row, user_id, is_admin=self.is_admin(user_id))
                if row is not None
                else None
            ),
        }

    def audit_admin_override(
        self,
        *,
        user_id: int,
        agent_id: str,
        app_id: str,
        needed: str,
        app_owner_id: int | None = None,
        via: str = "agent_tool",
    ) -> None:
        """Record an admin acting on an app/agent they do not own."""
        if self._audit_repo is None:
            return
        self._audit_repo.write(
            actor=f"user:{user_id}",
            action="miniapp.admin_override",
            target=app_id,
            payload=json.dumps(
                {
                    "agent_id": agent_id,
                    "needed": needed,
                    "app_owner_id": app_owner_id,
                    "via": via,
                },
                ensure_ascii=False,
            ),
        )

    def is_agent_accessible(self, agent_id: str, user_id: int) -> bool:
        """True when the user may use this agent at all (owner or shared)."""
        agent = self._agent_repo.get(agent_id)
        if agent is None:
            return False
        if agent.user_id == user_id:
            return True
        return bool(getattr(agent, "is_shared", False))

    # ----- access requests -----

    def _audit_access(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        payload: dict[str, Any],
    ) -> None:
        if self._audit_repo is None:
            return
        self._audit_repo.write(
            actor=actor,
            action=action,
            target=target,
            payload=json.dumps(payload, ensure_ascii=False),
        )

    def request_access(
        self,
        *,
        app_id: str | None,
        agent_id: str,
        user_id: int,
        needed: str,
        note: str = "",
    ) -> AppAccessRequestRow:
        """Create (or return the pending) access request for a blocked user."""
        request, _created = self.request_access_with_status(
            app_id=app_id,
            agent_id=agent_id,
            user_id=user_id,
            needed=needed,
            note=note,
        )
        return request

    def request_access_with_status(
        self,
        *,
        app_id: str | None,
        agent_id: str,
        user_id: int,
        needed: str,
        note: str = "",
    ) -> tuple[AppAccessRequestRow, bool]:
        """Return the request plus whether this call created a new row."""
        if self._access_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "access requests are not configured")
        permission = validate_collaborator_permission(needed)
        row = self._app_repo.get(app_id) if app_id else None
        if app_id and row is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        if permission == "participate" and row is None:
            raise OctopError(
                ErrorCode.APP_SHARE_INVALID,
                "participate requires an app",
            )
        # The app is the source of truth for its agent; never trust a
        # caller-supplied pair that could share an unrelated expert on approval.
        if row is not None:
            agent_id = row.agent_id
        agent = self._agent_repo.get(agent_id)
        if agent is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, f"agent not found: {agent_id}")
        app_owner_id = row.owner_user_id if row is not None else None
        agent_owner_id = agent.user_id
        if agent_owner_id is None:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "agent has no owner")
        if user_id in {app_owner_id, agent_owner_id}:
            raise OctopError(ErrorCode.APP_SHARE_INVALID, "the owner already has access")
        if self.is_admin(user_id) or self.is_agent_accessible(agent_id, user_id):
            granted = (
                self.permission_for(row, user_id, is_admin=False) if row is not None else "view"
            )
            if _access_rank(granted) >= _access_rank(permission):
                raise OctopError(ErrorCode.APP_SHARE_INVALID, "access is already granted")
        account = self._user_repo.get(user_id)
        if account is None or account.disabled:
            raise OctopError(ErrorCode.APP_FORBIDDEN, "user is not active")
        existing = self._access_repo.find_pending(
            app_id=app_id,
            agent_id=agent_id,
            requester_user_id=user_id,
        )
        if existing is not None:
            return existing, False
        try:
            request = self._access_repo.create(
                app_id=app_id,
                agent_id=agent_id,
                requester_user_id=user_id,
                app_owner_user_id=app_owner_id,
                agent_owner_user_id=agent_owner_id,
                needed=permission,
                note=note.strip()[:500],
            )
        except Exception as exc:
            # Two processes can pass find_pending together; the partial unique
            # index decides the winner and the loser returns the stored row.
            if not is_unique_violation(exc):
                raise
            existing = self._access_repo.find_pending(
                app_id=app_id,
                agent_id=agent_id,
                requester_user_id=user_id,
            )
            if existing is None:
                raise
            return existing, False
        self._audit_access(
            actor=f"user:{user_id}",
            action="miniapp.access_request",
            target=app_id or agent_id,
            payload={
                "request_id": request.request_id,
                "agent_id": agent_id,
                "needed": permission,
                "app_owner_user_id": app_owner_id,
                "agent_owner_user_id": agent_owner_id,
            },
        )
        return request, True

    def list_access_requests(
        self,
        *,
        user_id: int,
        is_admin: bool,
        scope: str = "owner",
        app_id: str | None = None,
    ) -> list[AppAccessRequestRow]:
        if self._access_repo is None:
            return []
        if scope == "mine":
            return self._access_repo.list_for_requester(user_id)
        if is_admin:
            rows = self._access_repo.list_pending_admin(limit=500)
            if app_id is not None:
                rows = [row for row in rows if row.app_id == app_id]
            return rows
        return self._access_repo.list_for_owner(user_id, app_id=app_id)

    def resolve_access_request(
        self,
        *,
        request_id: str,
        actor_user_id: int,
        is_admin: bool,
        approve: bool,
    ) -> AppAccessRequestRow:
        """Approve or reject a request.

        A split-owner approval applies the acting owner's half immediately and
        keeps the request pending until the requester can reach both the expert
        and the app; the other owner (or an admin) completes it.
        """
        if self._access_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "access requests are not configured")
        request = self._access_repo.get(request_id)
        if request is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "access request not found")
        owners = {
            user_id
            for user_id in (request.app_owner_user_id, request.agent_owner_user_id)
            if user_id is not None
        }
        if not is_admin and actor_user_id not in owners:
            raise OctopError(ErrorCode.APP_FORBIDDEN, "only an owner can resolve this request")
        if request.status != APP_ACCESS_PENDING:
            return request

        if not approve:
            rejected = self._access_repo.resolve(
                request.request_id,
                status=APP_ACCESS_REJECTED,
                resolved_by=actor_user_id,
            )
            if rejected is None:
                raise OctopError(ErrorCode.APP_NOT_FOUND, "access request not found")
            self._audit_access(
                actor=f"user:{actor_user_id}",
                action="miniapp.access_rejected",
                target=request.app_id or request.agent_id,
                payload={
                    "request_id": request.request_id,
                    "requester_user_id": request.requester_user_id,
                    "needed": request.needed,
                },
            )
            return rejected

        agent = self._agent_repo.get(request.agent_id)
        if agent is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, f"agent not found: {request.agent_id}")
        row = self._app_repo.get(request.app_id) if request.app_id else None
        if request.app_id and row is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)

        # view / participate only need the app owner; edit roles and anything
        # that uses the agent keep the split-owner (app + agent) approval.
        needs_agent = request.needed in _AGENT_ACCESS_ROLES
        can_share_agent = needs_agent and (is_admin or agent.user_id == actor_user_id)
        can_grant_app = row is not None and (is_admin or row.owner_user_id == actor_user_id)

        if can_share_agent and not agent.is_shared:
            self._agent_repo.set_shared(request.agent_id, True)
            self._audit_access(
                actor=f"user:{actor_user_id}",
                action="miniapp.agent_shared",
                target=request.agent_id,
                payload={"request_id": request.request_id},
            )

        updated: AppAccessRequestRow | None = None
        if row is not None:
            collaborators = self._app_repo.list_collaborators(row.app_id)
            current = next(
                (
                    item.permission
                    for item in collaborators
                    if item.user_id == request.requester_user_id
                ),
                None,
            )
            needed_rank = _access_rank(request.needed)
            if _access_rank(current) < needed_rank and can_grant_app:
                granted = request.needed
                if current and _access_rank(current) > _access_rank(granted):
                    granted = current
                entries = [
                    (item.user_id, item.permission)
                    for item in collaborators
                    if item.user_id != request.requester_user_id
                ]
                entries.append((request.requester_user_id, granted))
                self.set_collaborators(row, entries)
                # An approved access request activates a pending membership.
                self._app_repo.update_collaborator(
                    row.app_id,
                    request.requester_user_id,
                    status="active",
                )
                collaborators = self._app_repo.list_collaborators(row.app_id)
                current = next(
                    (
                        item.permission
                        for item in collaborators
                        if item.user_id == request.requester_user_id
                    ),
                    granted,
                )
                self._audit_access(
                    actor=f"user:{actor_user_id}",
                    action="miniapp.access_app_granted",
                    target=row.app_id,
                    payload={
                        "request_id": request.request_id,
                        "requester_user_id": request.requester_user_id,
                        "permission": current,
                    },
                )
            agent_accessible = self.is_agent_accessible(request.agent_id, request.requester_user_id)
            app_allowed = _access_rank(current) >= needed_rank
            if app_allowed and (not needs_agent or agent_accessible):
                cleaned = self._clean_collaborators(
                    row,
                    [(item.user_id, item.permission) for item in collaborators],
                )
                updated = self._access_repo.resolve_with_collaborators(
                    request.request_id,
                    status=APP_ACCESS_APPROVED,
                    resolved_by=actor_user_id,
                    app_id=row.app_id,
                    collaborators=cleaned,
                )
                if updated is None:
                    raise OctopError(ErrorCode.APP_NOT_FOUND, "access request not found")
                if updated.status != APP_ACCESS_APPROVED:
                    # Another caller resolved the request first.
                    return updated
            else:
                # One side approved; the request stays pending until the other
                # owner's grant (or an admin) makes access real.
                return self._access_repo.get(request_id) or request
        elif self.is_agent_accessible(request.agent_id, request.requester_user_id):
            updated = self._access_repo.resolve(
                request.request_id,
                status=APP_ACCESS_APPROVED,
                resolved_by=actor_user_id,
            )
            if updated is None:
                raise OctopError(ErrorCode.APP_NOT_FOUND, "access request not found")
        else:
            return self._access_repo.get(request_id) or request

        self._audit_access(
            actor=f"user:{actor_user_id}",
            action="miniapp.access_approved",
            target=request.app_id or request.agent_id,
            payload={
                "request_id": request.request_id,
                "requester_user_id": request.requester_user_id,
                "needed": request.needed,
            },
        )
        assert updated is not None
        return updated

    def apps_for_agent(self, agent_id: str) -> list[LightAppRow]:
        """Every app hosted in an agent's workspace, whoever created it."""
        return self._app_repo.list_by_agent(agent_id)

    async def transfer(
        self,
        row: AppRecord,
        *,
        target_agent_id: str,
        actor_user_id: int,
    ) -> LightAppRow:
        """Move an app to another agent's workspace.

        The app files are copied to the target workspace and the metadata
        follows. The source copy is left in place: the backend protocol has no
        portable delete, and it disappears with the source agent anyway.
        """
        if target_agent_id == row.agent_id:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "the app already belongs to that agent",
            )
        if not self.is_agent_accessible(target_agent_id, actor_user_id):
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"target agent is not accessible: {target_agent_id}",
            )
        source_ws = await self._workspace(row.agent_id)
        target_ws = await self._workspace(target_agent_id)
        async with _lock_for(row.app_id):
            # Copy and metadata move together so an append cannot land in the
            # source after the app has already moved.
            await self._copy_app_files(source_ws, target_ws, row.app_id)
            self._app_repo.set_agent_id(row.app_id, target_agent_id)
            self._app_repo.touch(row.app_id)
        moved = self._app_repo.get(row.app_id)
        if moved is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return moved

    async def _copy_app_files(
        self,
        source_ws: BackendWorkspace,
        target_ws: BackendWorkspace,
        app_id: str,
    ) -> None:
        """Copy the app directory (and its ``tables/`` subdirectory) between workspaces."""
        for prefix in ("", f"{TABLES_DIR}/", f"{ASSETS_DIR}/"):
            source_dir = app_path(app_id, prefix.rstrip("/")) if prefix else app_dir(app_id)
            target_dir = app_path(app_id, prefix.rstrip("/")) if prefix else app_dir(app_id)
            result = await source_ws.als(source_dir)
            entries = list(getattr(result, "entries", None) or []) if result is not None else []
            for entry in entries:
                if _entry_field(entry, "is_dir"):
                    continue
                path = str(_entry_field(entry, "path") or "").replace("\\", "/")
                name = path.rsplit("/", 1)[-1]
                if not name or name.startswith("."):
                    continue
                # Read through the known directory rather than the entry's own
                # path: backends report different shapes (relative vs root).
                data = await source_ws.adownload_bytes(f"{source_dir}/{name}")
                if data is None:
                    continue
                await target_ws.aupload_bytes(f"{target_dir}/{name}", data)

    # ----- workspace helpers -----

    async def _workspace(self, agent_id: str) -> BackendWorkspace:
        ws = await asyncio.to_thread(self._workspaces.workspace_for_agent, agent_id)
        if ws is None:
            raise OctopError.localized(ErrorCode.AGENT_NOT_FOUND)
        return ws

    async def _read_schema(
        self,
        ws: BackendWorkspace,
        app_id: str,
        table_key: str = MAIN_TABLE_KEY,
    ) -> Schema:
        app = self._app_repo.get(app_id)
        if app is not None and app.storage_version >= 2:
            stored = self._app_repo.get_table_schema(app_id, table_key)
            if stored is not None:
                return parse_schema_payload(stored[0])
        return await self._read_schema_file(ws, app_id, table_key)

    async def _read_schema_file(
        self,
        ws: BackendWorkspace,
        app_id: str,
        table_key: str = MAIN_TABLE_KEY,
    ) -> Schema:
        await self._recover(ws, app_id)
        schema_file, _ = table_file_names(table_key)
        text = await ws.aread_text(app_path(app_id, schema_file))
        if text is None:
            raise _invalid(
                ErrorCode.APP_SCHEMA_INVALID,
                f"schema file for table {table_key!r} is missing",
            )
        return parse_schema_payload(text)

    async def _read_rows_file(
        self,
        ws: BackendWorkspace,
        app_id: str,
        schema: Schema,
        table_key: str = MAIN_TABLE_KEY,
    ) -> list[dict[str, str]]:
        _, data_file = table_file_names(table_key)
        text = await ws.aread_text(app_path(app_id, data_file))
        if text is None:
            # aread_text is fail-soft: None also covers transient backend
            # errors. Treating that as an empty file would overwrite every
            # existing row on the next append.
            raise OctopError(
                ErrorCode.INTERNAL_ERROR,
                f"failed to read app data file for table {table_key!r}",
            )
        return parse_csv(text, schema)

    async def _ensure_db_rows(self, row: AppRecord) -> bool:
        """Migrate a legacy CSV app into the DB on first access.

        Returns True when the app is on DB storage. A migration failure keeps
        the app on CSV storage (the caller falls back) rather than losing rows.
        """
        if row.storage_version >= 2:
            return True
        async with _lock_for(row.app_id):
            current = self._app_repo.get(row.app_id)
            if current is None:
                raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
            if current.storage_version >= 2:
                return True
            try:
                ws = await self._workspace(current.agent_id)
                self._ensure_main_table(current)
                for table in self.list_tables(current):
                    schema = await self._read_schema_file(ws, current.app_id, table.table_key)
                    rows = await self._read_rows_file(
                        ws,
                        current.app_id,
                        schema,
                        table.table_key,
                    )
                    self._app_repo.set_table_schema(
                        current.app_id,
                        table.table_key,
                        schema.to_json(),
                        schema.version,
                    )
                    self._app_repo.replace_data_rows(
                        current.app_id,
                        table.table_key,
                        rows,
                        source="migration",
                    )
                self._app_repo.set_storage_version(current.app_id, 2)
                await self._after_write(ws, current.app_id)
                return True
            except Exception:
                logger.warning(
                    "light app %s migration to DB storage failed; keeping CSV",
                    current.app_id,
                    exc_info=True,
                )
                return False

    def _require_live(self, app_id: str) -> None:
        if self._app_repo.get(app_id) is None:
            # A stale request re-creates the lock in ``_lock_for``; drop it so
            # deleted app ids do not accumulate entries.
            _forget_lock(app_id)
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)

    async def _write_atomic(
        self,
        ws: BackendWorkspace,
        app_id: str,
        files: dict[str, str],
    ) -> None:
        """Stage app files, then promote them as one recoverable group.

        A crash while staging leaves the previous files untouched; a crash
        during promotion leaves the journal so the next read finishes it.
        """
        if not files or not all(_is_atomic_app_file(name) for name in files):
            raise ValueError("unsupported atomic app files")
        for filename, content in files.items():
            await ws.awrite_text(app_path(app_id, filename + STAGED_SUFFIX), content)
        await ws.awrite_text(
            app_path(app_id, FILE_PENDING),
            json.dumps({"files": list(files)}, ensure_ascii=False),
        )
        await self._promote(ws, app_id, list(files))
        await self._clear_journal(ws, app_id)

    async def _recover(self, ws: BackendWorkspace, app_id: str) -> None:
        files = await self._pending_files(ws, app_id)
        if files is None:
            return
        await self._promote(ws, app_id, files)
        await self._clear_journal(ws, app_id)

    async def _pending_files(self, ws: BackendWorkspace, app_id: str) -> list[str] | None:
        marker = app_path(app_id, FILE_PENDING)
        if not await ws.aexists(marker):
            return None
        text = await ws.aread_text(marker)
        if text is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "failed to read app update journal")
        names: list[str] = []
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        files = payload.get("files") if isinstance(payload, dict) else None
        if isinstance(files, list):
            for item in files:
                if not isinstance(item, str):
                    names = []
                    break
                names.append(item)
        if not names or not all(_is_atomic_app_file(name) for name in names):
            # The journal is written after staging and before promotion, so an
            # unreadable journal means no target was touched.
            await self._clear_journal(ws, app_id)
            return None
        return names

    async def _promote(self, ws: BackendWorkspace, app_id: str, files: list[str]) -> None:
        for filename in files:
            staged = app_path(app_id, filename + STAGED_SUFFIX)
            if not await ws.aexists(staged):
                continue  # promoted by an earlier attempt
            content = await ws.aread_text(staged)
            if content is None:
                raise OctopError(ErrorCode.INTERNAL_ERROR, "failed to read staged app file")
            await ws.awrite_text(app_path(app_id, filename), content)
            try:
                await ws.adelete(staged)
            except Exception:
                # The target is already committed; a leftover staged file is
                # harmless and gets overwritten by the next write.
                logger.warning("failed to remove staged app file: %s", staged, exc_info=True)

    async def _clear_journal(self, ws: BackendWorkspace, app_id: str) -> None:
        try:
            await ws.adelete(app_path(app_id, FILE_PENDING))
        except Exception:
            logger.warning("failed to clear app update journal: %s", app_id, exc_info=True)

    @staticmethod
    def _manifest_text(row: AppRecord, tables: list[dict[str, Any]]) -> str:
        main_version = next(
            (table["schema_version"] for table in tables if table.get("is_main")),
            1,
        )
        manifest = {
            "app_id": row.app_id,
            "name": row.name,
            "description": row.description,
            "agent_id": row.agent_id,
            "owner_user_id": row.owner_user_id,
            "schema_version": main_version,
            "data_storage": ("control-plane-db" if row.storage_version >= 2 else "workspace-csv"),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "tables": tables,
        }
        return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"

    async def _after_write(self, ws: BackendWorkspace, app_id: str) -> None:
        """Single post-write hook for MiniApp persistence.

        Counts, ``data_version`` and app totals are updated atomically inside
        the row-store repo methods. This hook owns the remaining derived
        artifact — the workspace ``manifest.json`` — so a new write path does
        not have to remember it separately.
        """
        await self._refresh_manifest(ws, app_id)

    async def _refresh_manifest(self, ws: BackendWorkspace, app_id: str) -> None:
        row = self._app_repo.get(app_id)
        if row is None:
            return
        tables: list[dict[str, Any]] = []
        for table in self.list_tables(row):
            try:
                version: int | None = (await self._read_schema(ws, app_id, table.table_key)).version
            except OctopError:
                version = None
            tables.append(
                {
                    "key": table.table_key,
                    "label": table.label,
                    "schema_version": version,
                    "is_main": table.is_main,
                    "public_visible": table.public_visible,
                }
            )
        try:
            await ws.awrite_text(
                app_path(app_id, FILE_MANIFEST),
                self._manifest_text(row, tables),
            )
        except Exception:
            logger.warning("failed to refresh app manifest: %s", app_id, exc_info=True)

    # ----- app templates (system and expert-provided shells) -----

    async def list_templates(
        self, agent_id: str, *, source: str = "all", locale: str = "zh"
    ) -> list[dict[str, Any]]:
        """List system and workspace templates for the creation gallery."""
        if source not in ("all", SYSTEM_SOURCE, AGENT_SOURCE):
            raise _invalid(ErrorCode.APP_NOT_FOUND, f"unknown template source: {source!r}")
        templates: list[dict[str, Any]] = []
        if source in ("all", SYSTEM_SOURCE):
            templates.extend(info.to_dict() for info in list_system_templates(locale))
        if source in ("all", AGENT_SOURCE):
            templates.extend(
                info.to_dict() for info in await self._agent_templates(agent_id, locale)
            )
        return templates

    async def _agent_templates(self, agent_id: str, locale: str) -> list[TemplateInfo]:
        ws = await self._workspace(agent_id)
        result = await ws.aglob("app_templates/*/schema.json")
        matches = list(getattr(result, "matches", None) or []) if result is not None else []
        names: set[str] = set()
        for entry in matches:
            path = str(_entry_field(entry, "path") or "").replace("\\", "/")
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2 and parts[-1] == "schema.json":
                names.add(parts[-2])
        templates = []
        for name in sorted(names):
            if not name or name.startswith("."):
                continue
            manifest: dict[str, Any] = {}
            manifest_text = await ws.aread_text(f"app_templates/{name}/manifest.json")
            if manifest_text:
                try:
                    payload = json.loads(manifest_text)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    manifest = payload
            templates.append(
                agent_template_info(
                    agent_id=agent_id,
                    template_id=name,
                    manifest=manifest,
                    has_html=await ws.aexists(f"app_templates/{name}/app.html"),
                    locale=locale,
                    has_preview=await ws.aexists(f"app_templates/{name}/{FILE_PREVIEW}"),
                )
            )
        return templates

    async def list_app_templates(self, agent_id: str) -> list[dict[str, Any]]:
        """Legacy shape: the agent's workspace templates only."""
        return [
            {"template": info.template_id, "label": info.name, "has_html": info.has_html}
            for info in await self._agent_templates(agent_id, "zh")
        ]

    async def create_from_template(
        self,
        *,
        agent_id: str,
        owner_user_id: int,
        template: str,
        source: str = AGENT_SOURCE,
        name: str | None = None,
        description: str = "",
        capabilities: Iterable[str] | None = None,
    ) -> LightAppRow:
        """Create an empty app from a system or workspace shell (schema + HTML)."""
        clean = clean_template_id(template)
        schema_text: str
        html: str | None
        services_text: str | None = None
        if source == SYSTEM_SOURCE:
            schema_text, html = read_system_template(clean)
            services_text = read_system_template_services(clean)
        elif source == AGENT_SOURCE:
            ws = await self._workspace(agent_id)
            stored_schema = await ws.aread_text(f"app_templates/{clean}/schema.json")
            if stored_schema is None:
                raise _invalid(ErrorCode.APP_NOT_FOUND, f"unknown app template: {clean}")
            schema_text = stored_schema
            html = await ws.aread_text(f"app_templates/{clean}/app.html")
            services_text = await ws.aread_text(f"app_templates/{clean}/services.json")
        else:
            raise _invalid(ErrorCode.APP_NOT_FOUND, f"unknown template source: {source!r}")
        row = await self.create(
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            name=(name or clean).strip() or clean,
            description=description,
            columns=schema_text,
            capabilities=capabilities,
        )
        if html:
            try:
                await self.write_html(row, html)
            except Exception:
                # Do not leave an empty app behind when the template shell is
                # rejected by the HTML sandbox rules.
                await self.delete(row)
                raise
        preview = await self.template_preview(agent_id, clean, source)
        if preview:
            try:
                await self.write_preview(row, preview)
            except Exception:
                # A template without a usable cover still yields a usable app.
                logger.warning(
                    "failed to copy template preview for %s: %s",
                    clean,
                    exc_info=True,
                )
        if services_text:
            await self._apply_template_services(
                row,
                services_text,
                owner_user_id=owner_user_id,
                template=clean,
            )
        return row

    async def _apply_template_services(
        self,
        row: AppRecord,
        services_text: str,
        *,
        owner_user_id: int,
        template: str,
    ) -> None:
        """Create the service declarations a template ships with.

        Template files must not contain credentials or connector instance ids;
        zero-credential gateway connectors are auto-provisioned per owner.
        A service failure is logged and does not delete an otherwise usable app.
        """
        try:
            payload = json.loads(services_text)
        except ValueError:
            logger.warning("template %s: invalid services.json", template)
            return
        raw_items = payload.get("services") if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            logger.warning("template %s: services.json must be a list", template)
            return
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            try:
                self.save_service(
                    row,
                    name=name,
                    provider=str(raw.get("provider") or "external"),
                    connector_instance_id=str(raw.get("connector_instance_id") or ""),
                    connector_kind=(
                        str(raw["connector_kind"]).strip() if raw.get("connector_kind") else None
                    ),
                    allowed_tools=[
                        str(item).strip()
                        for item in raw.get("allowed_tools") or []
                        if str(item).strip()
                    ],
                    allowed_url_prefixes=[
                        str(item).strip()
                        for item in raw.get("allowed_url_prefixes") or []
                        if str(item).strip()
                    ],
                    request_headers=(
                        raw.get("request_headers")
                        if isinstance(raw.get("request_headers"), dict)
                        else None
                    ),
                    cache_ttl_seconds=int(raw.get("cache_ttl_seconds", 300)),
                    max_calls_per_minute=int(raw.get("max_calls_per_minute", 30)),
                    public_live=bool(raw.get("public_live", False)),
                    enabled=bool(raw.get("enabled", True)),
                    actor_user_id=owner_user_id,
                    is_admin=False,
                )
            except Exception:
                logger.warning(
                    "template %s: failed to create service %s",
                    template,
                    name,
                    exc_info=True,
                )

    # ----- create / metadata -----

    async def create(
        self,
        *,
        agent_id: str,
        owner_user_id: int,
        name: str,
        description: str = "",
        columns: object,
        capabilities: Iterable[str] | None = None,
        setup_plan_id: str | None = None,
    ) -> LightAppRow:
        if setup_plan_id:
            existing = self._app_repo.get_by_setup_plan_id(setup_plan_id)
            if existing is not None:
                return existing
        clean_name = validate_name(name)
        clean_description = validate_description(description)
        schema = parse_schema_payload(columns)
        selected_capabilities = (
            app_capabilities.NEW_APP_CAPABILITIES
            if capabilities is None
            else app_capabilities.validate(capabilities)
        )
        if self._app_repo.count_for_owner(owner_user_id) >= MAX_APPS_PER_OWNER:
            raise _invalid(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"at most {MAX_APPS_PER_OWNER} apps per user",
                limit=MAX_APPS_PER_OWNER,
            )
        ws = await self._workspace(agent_id)
        app_id = self._app_repo.allocate_id()
        row = self._app_repo.create(
            app_id=app_id,
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            name=clean_name,
            description=clean_description,
            storage_version=2,
            capabilities_json=app_capabilities.dump(selected_capabilities),
            setup_plan_id=setup_plan_id,
        )
        try:
            # v2 rows live in the control-plane DB. The workspace keeps a
            # schema mirror for the agent, but no empty data.csv shell: a
            # file that looks authoritative but is always empty is exactly
            # the drift P0-3 removes.
            await ws.awrite_text(app_path(app_id, FILE_SCHEMA), schema.to_json())
            self._app_repo.create_table(
                app_id,
                MAIN_TABLE_KEY,
                public_visible=True,
            )
            self._app_repo.set_table_schema(
                app_id,
                MAIN_TABLE_KEY,
                schema.to_json(),
                schema.version,
            )
            self._app_repo.set_table_stats(
                app_id,
                MAIN_TABLE_KEY,
                row_count=0,
                data_bytes=0,
            )
            await self._after_write(ws, app_id)
            self._app_repo.set_file_stats(app_id, html_bytes=0, data_bytes=0)
        except Exception:
            self._app_repo.delete(app_id)
            try:
                await ws.adelete(app_dir(app_id))
            except Exception:
                logger.warning("failed to clean up app workspace files: %s", app_id, exc_info=True)
            raise
        return row

    # ----- tables -----

    def list_tables(self, row: AppRecord) -> list[AppTableView]:
        """Tables of an app, always including the main table.

        Apps created before multi-table support have no registry row for the
        main table, so it is synthesized from the app row.
        """
        registered = self._app_repo.list_tables(row.app_id)
        main = next((item for item in registered if item.table_key == MAIN_TABLE_KEY), None)
        views = [
            AppTableView(
                table_key=MAIN_TABLE_KEY,
                label=main.label if main is not None else "",
                row_count=main.row_count if main is not None else row.row_count,
                data_version=main.data_version if main is not None else 0,
                is_main=True,
                public_visible=main.public_visible if main is not None else True,
                row_scope=main.row_scope if main is not None else "app",
                rules=_rules_from_json(main.rules_json if main is not None else "{}"),
                member_visible=main.member_visible if main is not None else False,
            )
        ]
        for item in registered:
            if item.table_key == MAIN_TABLE_KEY:
                continue
            views.append(
                AppTableView(
                    table_key=item.table_key,
                    label=item.label,
                    row_count=item.row_count,
                    data_version=item.data_version,
                    is_main=False,
                    public_visible=item.public_visible,
                    row_scope=item.row_scope,
                    rules=_rules_from_json(item.rules_json),
                    member_visible=item.member_visible,
                )
            )
        return views

    def _ensure_main_table(self, row: AppRecord) -> None:
        """Register the main table for apps created before multi-table support.

        Their ``light_apps.row_count`` is already the main table's count, so the
        backfilled registry row starts from it and the aggregate stays correct.
        """
        if self._app_repo.get_table(row.app_id, MAIN_TABLE_KEY) is not None:
            return
        self._app_repo.create_table(row.app_id, MAIN_TABLE_KEY, public_visible=True)
        self._app_repo.set_table_stats(
            row.app_id,
            MAIN_TABLE_KEY,
            row_count=row.row_count,
            data_bytes=row.data_bytes,
        )

    def _require_table(self, row: AppRecord, table_key: str) -> str:
        """Validate *table_key* exists in the app's registry."""
        key = validate_table_key(table_key)
        if key == MAIN_TABLE_KEY:
            return key
        if self._app_repo.get_table(row.app_id, key) is None:
            raise _invalid(
                ErrorCode.APP_SCHEMA_INVALID,
                f"unknown table: {key!r}",
                table=key,
            )
        return key

    async def create_table(
        self,
        row: AppRecord,
        *,
        table_key: str,
        label: str = "",
        columns: object,
        public_visible: bool = False,
        row_scope: str = "app",
    ) -> AppTableView:
        """Add a table with its own columns and an empty CSV."""
        key = validate_table_key(table_key)
        clean_scope = row_scope.strip().lower()
        if clean_scope not in {"app", "user"}:
            raise _invalid(
                ErrorCode.APP_SCHEMA_INVALID,
                "row_scope must be 'app' or 'user'",
            )
        if key == MAIN_TABLE_KEY:
            raise _invalid(
                ErrorCode.APP_SCHEMA_INVALID,
                "the main table already exists",
                table=key,
            )
        schema = parse_schema_payload(columns)
        clean_label = label.strip()[:MAX_LABEL_LEN]
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            self._ensure_main_table(row)
            if self._app_repo.get_table(row.app_id, key) is not None:
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    f"table already exists: {key!r}",
                    table=key,
                )
            if self._app_repo.count_tables(row.app_id) >= MAX_TABLES_PER_APP:
                raise _invalid(
                    ErrorCode.APP_LIMIT_EXCEEDED,
                    f"at most {MAX_TABLES_PER_APP} tables per app",
                    limit=MAX_TABLES_PER_APP,
                )
            ws = await self._workspace(row.agent_id)
            schema_file, _data_file = table_file_names(key)
            await self._write_atomic(
                ws,
                row.app_id,
                {schema_file: schema.to_json()},
            )
            self._app_repo.create_table(
                row.app_id,
                key,
                clean_label,
                public_visible=public_visible,
                row_scope=clean_scope,
            )
            self._app_repo.set_table_schema(row.app_id, key, schema.to_json(), schema.version)
            self._app_repo.set_table_stats(
                row.app_id,
                key,
                row_count=0,
                data_bytes=0,
            )
            self._app_repo.refresh_app_totals(row.app_id)
            await self._after_write(ws, row.app_id)
        return AppTableView(
            table_key=key,
            label=clean_label,
            row_count=0,
            data_version=0,
            is_main=False,
            public_visible=public_visible,
            row_scope=clean_scope,
        )

    async def set_table_row_scope(
        self,
        row: AppRecord,
        *,
        table: str,
        row_scope: str,
    ) -> AppTableView:
        """Switch a table between shared (``app``) and per-user (``user``)."""
        clean_scope = row_scope.strip().lower()
        if clean_scope not in {"app", "user"}:
            raise _invalid(
                ErrorCode.APP_SCHEMA_INVALID,
                "row_scope must be 'app' or 'user'",
            )
        key = self._require_table(row, table)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            self._ensure_main_table(current)
            self._app_repo.set_table_row_scope(current.app_id, key, row_scope=clean_scope)
            ws = await self._workspace(current.agent_id)
            await self._after_write(ws, current.app_id)
        return next(item for item in self.list_tables(current) if item.table_key == key)

    def table_row_scope(self, row: AppRecord, table: str) -> str:
        try:
            key = validate_table_key(table)
        except OctopError:
            return "app"
        registered = self._app_repo.get_table(row.app_id, key)
        return registered.row_scope if registered is not None else "app"

    def table_rules(self, row: AppRecord, table: str) -> dict[str, Any]:
        registered = self._app_repo.get_table(row.app_id, validate_table_key(table))
        return _rules_from_json(registered.rules_json) if registered is not None else {}

    async def set_table_rules(
        self,
        row: AppRecord,
        *,
        table: str,
        rules: Mapping[str, Any],
    ) -> AppTableView:
        key = self._require_table(row, table)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, key)
            clean = self._validate_rules(schema, rules)
            payload = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
            if len(payload.encode("utf-8")) > 16 * 1024:
                raise _invalid(ErrorCode.APP_SHARE_INVALID, "table rules exceed 16 KB")
            self._app_repo.set_table_rules(current.app_id, key, rules_json=payload)
            await self._after_write(ws, current.app_id)
        return next(item for item in self.list_tables(current) if item.table_key == key)

    @staticmethod
    def _validate_rules(schema: Schema, rules: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(rules, Mapping):
            raise _invalid(ErrorCode.APP_SHARE_INVALID, "rules must be an object")
        unknown = set(rules) - {"state_field", "transitions", "triggers"}
        if unknown:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                f"unknown rule keys: {', '.join(sorted(map(str, unknown)))}",
            )
        triggers = AppService._validate_triggers(schema, rules.get("triggers"))
        state_field = rules.get("state_field")
        transitions = rules.get("transitions") or {}
        if not state_field:
            if transitions:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    "transitions require state_field",
                )
            return {"triggers": triggers} if triggers else {}
        if not isinstance(state_field, str):
            raise _invalid(ErrorCode.APP_SHARE_INVALID, "state_field must be a string")
        column = schema.column(state_field)
        if column is None:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                f"unknown state field: {state_field!r}",
            )
        if column.type not in {"string", "enum"}:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                "state_field must be a string or enum column",
            )
        if not isinstance(transitions, dict):
            raise _invalid(ErrorCode.APP_SHARE_INVALID, "transitions must be an object")
        allowed_values = set(column.values) if column.type == "enum" else None
        clean: dict[str, list[str]] = {}
        for old_state, targets in transitions.items():
            if not isinstance(old_state, str) or not isinstance(targets, list):
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    "transitions must map a state to a list of states",
                )
            if not all(isinstance(item, str) for item in targets):
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    "transition targets must be strings",
                )
            if allowed_values is not None and (
                old_state not in allowed_values
                or any(item not in allowed_values for item in targets)
            ):
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"transition uses a value outside {state_field!r}: {old_state!r}",
                )
            clean[old_state] = list(dict.fromkeys(targets))
        result: dict[str, Any] = {"state_field": state_field, "transitions": clean}
        if triggers:
            result["triggers"] = triggers
        return result

    @staticmethod
    def _validate_triggers(schema: Schema, raw: object) -> dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise _invalid(ErrorCode.APP_SHARE_INVALID, "triggers must be an object")
        clean: dict[str, Any] = {}
        for name, config in raw.items():
            if name not in {"on_create", "on_update"}:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"unknown trigger: {name!r}",
                )
            if not isinstance(config, Mapping):
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"trigger {name!r} must be an object",
                )
            unknown = set(config) - {"event_type", "when"}
            if unknown:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"unknown trigger keys: {', '.join(sorted(map(str, unknown)))}",
                )
            default_type = "row.created" if name == "on_create" else "row.updated"
            event_type = str(config.get("event_type") or default_type).strip()[:100]
            if not event_type:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"trigger {name!r} needs an event_type",
                )
            clean_config: dict[str, Any] = {"event_type": event_type}
            when = config.get("when")
            if when is not None:
                if not isinstance(when, Mapping):
                    raise _invalid(
                        ErrorCode.APP_SHARE_INVALID,
                        f"trigger {name!r}.when must be an object",
                    )
                if set(when) - {"field", "equals"}:
                    raise _invalid(
                        ErrorCode.APP_SHARE_INVALID,
                        f"trigger {name!r}.when supports field and equals",
                    )
                field = str(when.get("field") or "")
                if schema.column(field) is None:
                    raise _invalid(
                        ErrorCode.APP_SHARE_INVALID,
                        f"trigger {name!r} uses an unknown field: {field!r}",
                    )
                clean_config["when"] = {
                    "field": field,
                    "equals": str(when.get("equals") or ""),
                }
            clean[str(name)] = clean_config
        return clean

    def _emit_row_event(
        self,
        row: AppRecord,
        key: str,
        trigger: str,
        row_id: str,
        values: Mapping[str, str],
    ) -> None:
        """Append an inbox event for a configured on_create/on_update trigger."""
        if self._event_repo is None:
            return
        rules = self.table_rules(row, key)
        triggers = rules.get("triggers")
        if not isinstance(triggers, Mapping):
            return
        config = triggers.get(trigger)
        if not isinstance(config, Mapping):
            return
        when = config.get("when")
        if isinstance(when, Mapping):
            field = str(when.get("field") or "")
            if field and str(values.get(field, "")) != str(when.get("equals") or ""):
                return
        event_type = str(config.get("event_type") or f"row.{trigger.removeprefix('on_')}")
        self.append_event(
            row,
            event_type=event_type,
            payload={"table": key, "row_id": row_id},
            source="trigger",
            summary=f"{trigger} {key}",
        )

    def _check_transition(
        self,
        row: AppRecord,
        key: str,
        old_values: Mapping[str, str],
        new_values: Mapping[str, str],
    ) -> None:
        rules = self.table_rules(row, key)
        field = rules.get("state_field")
        if not isinstance(field, str) or not field:
            return
        old_state = str(old_values.get(field, ""))
        new_state = str(new_values.get(field, ""))
        if old_state == new_state:
            return
        transitions = rules.get("transitions")
        allowed = transitions.get(old_state) if isinstance(transitions, dict) else None
        if not isinstance(allowed, list) or new_state not in allowed:
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                f"invalid transition for {field!r}: {old_state!r} -> {new_state!r}",
                field=field,
                from_state=old_state,
                to_state=new_state,
            )

    def _row_owner_filter(
        self,
        row: AppRecord,
        key: str,
        viewer_user_id: int | None,
        include_all_rows: bool,
    ) -> int | None:
        if self.table_row_scope(row, key) != "user" or include_all_rows:
            return None
        if viewer_user_id is None:
            raise OctopError(
                ErrorCode.APP_FORBIDDEN,
                "row-scoped table requires a viewer",
            )
        return viewer_user_id

    # ----- reference columns -------------------------------------------------

    @staticmethod
    def _reference_columns(schema: Schema) -> list[Column]:
        return [column for column in schema.columns if column.type == "reference"]

    @staticmethod
    def _reference_label_column(target: Schema, column: Column) -> Column | None:
        if column.ref_label:
            found = target.column(column.ref_label)
            if found is not None:
                return found
        candidates = [item for item in target.columns if item.type in {"string", "text"}]
        for item in candidates:
            if not item.pii:
                return item
        return candidates[0] if candidates else None

    async def _reference_ids(
        self,
        current: AppRecord,
        table_key: str,
        *,
        cache: dict[str, set[str]] | None = None,
    ) -> set[str]:
        if cache is not None and table_key in cache:
            return cache[table_key]
        ids = {item.row_id for item in self._app_repo.list_all_data_rows(current.app_id, table_key)}
        if cache is not None:
            cache[table_key] = ids
        return ids

    async def _validate_references(
        self,
        current: AppRecord,
        schema: Schema,
        values: Mapping[str, str],
        *,
        cache: dict[str, set[str]] | None = None,
    ) -> None:
        """Reject a write whose reference column points at a missing row."""
        refs = [
            (column, str(values.get(column.name) or "").strip())
            for column in self._reference_columns(schema)
        ]
        refs = [(column, value) for column, value in refs if value]
        if not refs:
            return
        ws = await self._workspace(current.agent_id)
        for column, value in refs:
            target_key = column.ref_table or ""
            # Raises APP_SCHEMA_INVALID when the target table no longer exists.
            await self._read_schema(ws, current.app_id, target_key)
            ids = await self._reference_ids(current, target_key, cache=cache)
            if value not in ids:
                raise _invalid(
                    ErrorCode.APP_ROW_INVALID,
                    f"column {column.name!r} references a missing row in table {target_key!r}",
                    column=column.name,
                    ref_table=target_key,
                    row_id=value,
                )

    async def _inbound_references(
        self,
        current: AppRecord,
        target_table: str,
        target_row_ids: set[str],
    ) -> list[tuple[str, str, str]]:
        """Rows in other tables that point at one of *target_row_ids*."""
        ws = await self._workspace(current.agent_id)
        hits: list[tuple[str, str, str]] = []
        for table_view in self.list_tables(current):
            schema = await self._read_schema(ws, current.app_id, table_view.table_key)
            ref_cols = [
                column
                for column in self._reference_columns(schema)
                if column.ref_table == target_table
            ]
            if not ref_cols:
                continue
            for item in self._app_repo.list_all_data_rows(current.app_id, table_view.table_key):
                for column in ref_cols:
                    value = str(item.values.get(column.name) or "").strip()
                    if value and value in target_row_ids:
                        hits.append((table_view.table_key, column.name, value))
        return hits

    async def reference_labels(
        self,
        row: AppRecord,
        schema: Schema,
        rows: Sequence[Mapping[str, str]],
        *,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
        redact_pii: bool = False,
    ) -> dict[str, dict[str, str]]:
        """Resolve page-visible labels for the reference columns in *rows*."""
        ref_columns = self._reference_columns(schema)
        if not ref_columns:
            return {}
        ids_by_table: dict[str, set[str]] = {}
        for column in ref_columns:
            target_key = column.ref_table or ""
            for item in rows:
                value = str(item.get(column.name) or "").strip()
                if value:
                    ids_by_table.setdefault(target_key, set()).add(value)
        if not ids_by_table:
            return {}
        ws = await self._workspace(row.agent_id)
        schemas: dict[str, Schema | None] = {}
        target_rows: dict[str, dict[str, Any]] = {}
        for table_key, ids in ids_by_table.items():
            try:
                schemas[table_key] = await self._read_schema(ws, row.app_id, table_key)
            except OctopError:
                schemas[table_key] = None
                continue
            target_rows[table_key] = {
                item.row_id: item
                for item in self._app_repo.list_data_rows_by_ids(row.app_id, table_key, sorted(ids))
            }
        output: dict[str, dict[str, str]] = {}
        for column in ref_columns:
            table_key = column.ref_table or ""
            target_schema = schemas.get(table_key)
            if target_schema is None:
                output[column.name] = {}
                continue
            label_column = self._reference_label_column(target_schema, column)
            scope = self.table_row_scope(row, table_key)
            available: dict[str, str] = {}
            for target_row in target_rows.get(table_key, {}).values():
                if (
                    scope == "user"
                    and not include_all_rows
                    and viewer_user_id is not None
                    and app_access.row_owner_id(target_row) != viewer_user_id
                ):
                    continue
                label = (
                    str(target_row.values.get(label_column.name) or "")
                    if label_column is not None
                    else ""
                )
                if redact_pii and label_column is not None and label_column.pii:
                    label = PII_MASK
                available[target_row.row_id] = label or target_row.row_id
            resolved: dict[str, str] = {}
            for item in rows:
                value = str(item.get(column.name) or "").strip()
                if value and value in available:
                    resolved[value] = available[value]
            output[column.name] = resolved
        return output

    async def set_table_visibility(
        self,
        row: AppRecord,
        *,
        table: str,
        public_visible: bool,
    ) -> AppTableView:
        """Expose or hide one table on the app's public read-only link."""
        key = self._require_table(row, table)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            self._ensure_main_table(current)
            self._app_repo.set_table_visibility(
                current.app_id,
                key,
                public_visible=public_visible,
            )
            ws = await self._workspace(current.agent_id)
            await self._after_write(ws, current.app_id)
        view = next(item for item in self.list_tables(current) if item.table_key == key)
        return view

    async def set_table_member_visibility(
        self,
        row: AppRecord,
        *,
        table: str,
        member_visible: bool,
    ) -> AppTableView:
        """Expose or hide one table from app members with participate permission."""
        key = self._require_table(row, table)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            self._ensure_main_table(current)
            self._app_repo.set_table_member_visible(
                current.app_id,
                key,
                member_visible=member_visible,
            )
            ws = await self._workspace(current.agent_id)
            await self._after_write(ws, current.app_id)
        return next(item for item in self.list_tables(current) if item.table_key == key)

    def is_table_public(self, row: AppRecord, table: str) -> bool:
        """Return whether *table* is exposed on the public link."""
        try:
            key = validate_table_key(table)
        except OctopError:
            return False
        for view in self.list_tables(row):
            if view.table_key == key:
                return view.public_visible and view.row_scope != "user"
        return False

    async def update_metadata(
        self,
        row: AppRecord,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> LightAppRow:
        clean_name = validate_name(name) if name is not None else None
        clean_description = validate_description(description) if description is not None else None
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            self._app_repo.update_metadata(
                row.app_id,
                name=clean_name,
                description=clean_description,
            )
            ws = await self._workspace(row.agent_id)
            await self._after_write(ws, row.app_id)
            return self.get(row.app_id)

    async def delete(self, row: AppRecord) -> None:
        try:
            async with _lock_for(row.app_id):
                ws = await asyncio.to_thread(self._workspaces.workspace_for_agent, row.agent_id)
                self._app_repo.delete(row.app_id)
                # ON DELETE CASCADE covers this, but the explicit delete keeps
                # history cleanup working on databases without FK enforcement.
                self._app_repo.delete_assistant_messages(row.app_id)
                if self._secret_repo is not None:
                    self._secret_repo.delete(self._publication_secret_key(row.app_id))
                if ws is None:
                    return
                try:
                    await ws.adelete(app_dir(row.app_id))
                except Exception:
                    logger.warning(
                        "failed to delete app workspace files: %s",
                        row.app_id,
                        exc_info=True,
                    )
        finally:
            _forget_lock(row.app_id)

    # ----- UI / data -----

    async def read_html(self, row: AppRecord, *, validate: bool = False) -> str | None:
        """Read the app HTML.

        ``validate=True`` re-runs the write-time external-reference scan so a
        page edited directly in the workspace (generic ``write_file``/shell, a
        user's file browser) cannot bypass ``app_write_html`` and reach the
        dashboard or a published snapshot unchecked. Backup/share-inspection
        callers keep the raw read so legacy files remain exportable.
        """
        ws = await self._workspace(row.agent_id)
        html = await ws.aread_text(app_path(row.app_id, FILE_HTML))
        if validate and html:
            validate_html(html)
        return html

    async def has_html(self, row: AppRecord) -> bool:
        ws = await self._workspace(row.agent_id)
        return await ws.aexists(app_path(row.app_id, FILE_HTML))

    async def list_files(self, row: AppRecord) -> list[AppFile]:
        """List the app's workspace files with their sizes.

        The known app files are listed even on backends that report no size;
        tables contribute one CSV and one schema file each.
        """
        ws = await self._workspace(row.agent_id)
        current = self._app_repo.get(row.app_id) or row
        sizes = await self._dir_sizes(ws, row.app_id)
        # v2 rows are authoritative in the DB; its data.csv (if any) is a
        # migration leftover, not a file the user should act on.
        names = [FILE_HTML, FILE_SCHEMA, FILE_MANIFEST]
        if current.storage_version < 2:
            names.insert(1, FILE_DATA)
        for table in self.list_tables(row):
            if table.is_main:
                continue
            schema_file, data_file = table_file_names(table.table_key)
            names.append(schema_file)
            if current.storage_version < 2:
                names.append(data_file)
        names.extend(f"{ASSETS_DIR}/{asset}" for asset in await self.list_assets(row))
        if await ws.aexists(app_path(row.app_id, FILE_PREVIEW)):
            names.append(FILE_PREVIEW)
        return [
            AppFile(
                name=name,
                path=app_path(row.app_id, name),
                size=sizes.get(name),
            )
            for name in names
        ]

    # ----- preview -----

    async def write_preview(self, row: AppRecord, svg: str) -> int:
        """Store the app's cover image and return its size in bytes.

        The agent draws the preview as SVG, so the markup is validated before
        it reaches the workspace: no scripts, no inline handlers, no remote
        references.
        """
        try:
            content = validate_preview_svg(svg)
        except ValueError as exc:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, str(exc)) from exc
        ws = await self._workspace(row.agent_id)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            await ws.awrite_text(app_path(row.app_id, FILE_PREVIEW), content)
            self._app_repo.touch(row.app_id)
        return len(content.encode("utf-8"))

    async def read_preview(self, row: AppRecord) -> str | None:
        """Return the app's cover markup, or ``None`` when it has none."""
        try:
            ws = await self._workspace(row.agent_id)
        except OctopError:
            # The owning agent is gone: the app has no cover any more.
            return None
        return await ws.aread_text(app_path(row.app_id, FILE_PREVIEW))

    async def has_preview(self, row: AppRecord) -> bool:
        """Whether the app has a cover image."""
        try:
            ws = await self._workspace(row.agent_id)
        except OctopError:
            return False
        return await ws.aexists(app_path(row.app_id, FILE_PREVIEW))

    async def template_preview(self, agent_id: str, template_id: str, source: str) -> str | None:
        """Return a template's cover markup, or ``None`` when it has none."""
        clean = clean_template_id(template_id)
        if source == SYSTEM_SOURCE:
            path = SYSTEM_TEMPLATE_ROOT / clean / FILE_PREVIEW
            try:
                return path.read_text(encoding="utf-8") if path.is_file() else None
            except OSError:
                return None
        if source == AGENT_SOURCE:
            ws = await self._workspace(agent_id)
            return await ws.aread_text(f"app_templates/{clean}/{FILE_PREVIEW}")
        raise _invalid(ErrorCode.APP_NOT_FOUND, f"unknown template source: {source!r}")

    # ----- assets -----

    async def save_asset(self, row: AppRecord, name: str, data: bytes) -> str:
        """Store an image next to the app and return its name.

        The app page may not load remote images, so covers and icons live in
        the app's own directory and are served through the app ACL.
        """
        clean = name.strip()
        if not ASSET_NAME_RE.match(clean) or ".." in clean:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"invalid asset name: {name!r}",
            )
        if not data:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "asset is empty")
        if len(data) > MAX_ASSET_BYTES:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"asset exceeds the {MAX_ASSET_BYTES // (1024 * 1024)} MB limit",
            )
        if _sniff_asset_mime(data) is None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "asset must be a PNG, JPEG, WebP or GIF image",
            )
        ws = await self._workspace(row.agent_id)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            await ws.aupload_bytes(app_path(row.app_id, f"{ASSETS_DIR}/{clean}"), data)
            self._app_repo.touch(row.app_id)
        return clean

    async def read_asset(self, row: AppRecord, name: str) -> tuple[str, bytes]:
        """Read one asset and its media type."""
        clean = name.strip()
        if not ASSET_NAME_RE.match(clean) or ".." in clean:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"invalid asset name: {name!r}")
        ws = await self._workspace(row.agent_id)
        data = await ws.adownload_bytes(app_path(row.app_id, f"{ASSETS_DIR}/{clean}"))
        if data is None:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"asset not found: {clean}")
        mime = _sniff_asset_mime(data) or "application/octet-stream"
        return mime, data

    async def list_assets(self, row: AppRecord) -> list[str]:
        """Names of the assets stored next to the app."""
        ws = await self._workspace(row.agent_id)
        result = await ws.als(app_path(row.app_id, ASSETS_DIR))
        entries = list(getattr(result, "entries", None) or []) if result is not None else []
        names: list[str] = []
        for entry in entries:
            if _entry_field(entry, "is_dir"):
                continue
            path = str(_entry_field(entry, "path") or "").replace("\\", "/")
            name = path.rsplit("/", 1)[-1]
            if name and ASSET_NAME_RE.match(name):
                names.append(name)
        return sorted(names)

    async def write_html(self, row: AppRecord, html: str) -> int:
        content = validate_html(html)
        size = len(content.encode("utf-8"))
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            ws = await self._workspace(row.agent_id)
            previous = await ws.aread_text(app_path(row.app_id, FILE_HTML))
            if previous and previous != content:
                self._app_repo.set_previous_html(
                    row.app_id,
                    previous,
                    int(time.time()),
                )
            await ws.awrite_text(app_path(row.app_id, FILE_HTML), content)
            self._app_repo.touch(row.app_id)
            self._app_repo.set_file_stats(row.app_id, html_bytes=size)
            await self._after_write(ws, row.app_id)
        return size

    async def restore_html(self, row: AppRecord) -> LightAppRow:
        """Put the last replaced HTML back; calling it again swaps versions."""
        current = self.get(row.app_id)
        if not current.previous_html:
            raise OctopError(
                ErrorCode.APP_NOT_FOUND,
                "no previous HTML to restore",
            )
        await self.write_html(current, current.previous_html)
        return self.get(row.app_id)

    async def read_schema(self, row: AppRecord, table_key: str = MAIN_TABLE_KEY) -> Schema:
        key = self._require_table(row, table_key)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            ws = await self._workspace(row.agent_id)
            return await self._read_schema(ws, row.app_id, key)

    async def read_rows(
        self,
        row: AppRecord,
        *,
        limit: int = MAX_ROW_READ,
        offset: int = 0,
        table: str = MAIN_TABLE_KEY,
        redact_pii: bool = False,
        pii_policy: str | None = None,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> tuple[Schema, list[dict[str, str]], int]:
        schema, rows, _meta, total = await self.read_rows_detailed(
            row,
            limit=limit,
            offset=offset,
            table=table,
            redact_pii=redact_pii,
            pii_policy=pii_policy,
            viewer_user_id=viewer_user_id,
            include_all_rows=include_all_rows,
        )
        return schema, rows, total

    async def read_rows_detailed(
        self,
        row: AppRecord,
        *,
        limit: int = MAX_ROW_READ,
        offset: int = 0,
        table: str = MAIN_TABLE_KEY,
        redact_pii: bool = False,
        pii_policy: str | None = None,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> tuple[Schema, list[dict[str, str]], list[dict[str, Any]], int]:
        """Read a page plus per-row ``row_id``/``version``/``pii_masked`` metadata."""
        key = self._require_table(row, table)
        safe_limit = max(1, min(int(limit), MAX_ROW_READ))
        safe_offset = max(0, int(offset))
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, key)
            owner_filter = self._row_owner_filter(current, key, viewer_user_id, include_all_rows)
            if current.storage_version >= 2:
                data_page, total = self._app_repo.list_data_rows(
                    current.app_id,
                    key,
                    limit=safe_limit,
                    offset=safe_offset,
                    created_by=owner_filter,
                )
                rows_page = _normalize_rows(schema, [item.values for item in data_page])
                if pii_policy is not None:
                    schema, rows_page, masked = _apply_pii_policy(
                        schema,
                        rows_page,
                        [app_access.row_owner_id(item) for item in data_page],
                        pii_policy=pii_policy,
                        viewer_user_id=viewer_user_id,
                    )
                else:
                    schema, rows_page = _apply_pii(schema, rows_page, redact_pii=redact_pii)
                    masked = [redact_pii] * len(rows_page)
                return (
                    schema,
                    rows_page,
                    [
                        {
                            "row_id": item.row_id,
                            "version": item.version,
                            "source": item.source,
                            "pii_masked": flag,
                        }
                        for item, flag in zip(data_page, masked, strict=True)
                    ],
                    total,
                )
            if owner_filter is not None:
                raise OctopError(
                    ErrorCode.APP_FORBIDDEN,
                    "row-scoped table requires DB storage; migrate the app first",
                )
            rows = await self._read_rows_file(ws, current.app_id, schema, key)
            total = len(rows)
            # ``row_count`` is a list-view cache; reconcile it when a file-level
            # edit (or an interrupted append) made it drift from the CSV.
            # Reads must not bump updated_at or the list re-sorts on every GET.
            cached = self._app_repo.get_table(current.app_id, key)
            if cached is not None:
                if total != cached.row_count:
                    self._app_repo.set_table_row_count(current.app_id, key, total)
                    self._app_repo.refresh_app_totals(current.app_id)
            elif key == MAIN_TABLE_KEY and total != current.row_count:
                # Legacy app: only ``light_apps.row_count`` tracks the main CSV.
                self._app_repo.set_row_count(current.app_id, total, touch=False)
            rows_page = rows[safe_offset : safe_offset + safe_limit]
            if pii_policy is not None:
                schema, rows_page, masked = _apply_pii_policy(
                    schema,
                    rows_page,
                    [None] * len(rows_page),
                    pii_policy=pii_policy,
                    viewer_user_id=viewer_user_id,
                )
            else:
                schema, rows_page = _apply_pii(schema, rows_page, redact_pii=redact_pii)
                masked = [redact_pii] * len(rows_page)
            return (
                schema,
                rows_page,
                [
                    {"row_id": "", "version": 1, "source": "manual", "pii_masked": flag}
                    for flag in masked
                ],
                total,
            )

    async def query_rows(
        self,
        row: AppRecord,
        *,
        table: str = MAIN_TABLE_KEY,
        filters: Mapping[str, object] | None = None,
        limit: int = MAX_ROW_READ,
        offset: int = 0,
        redact_pii: bool = False,
        pii_policy: str | None = None,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> tuple[Schema, list[dict[str, str]], int]:
        schema, rows, _meta, total = await self.query_rows_detailed(
            row,
            table=table,
            filters=filters,
            limit=limit,
            offset=offset,
            redact_pii=redact_pii,
            pii_policy=pii_policy,
            viewer_user_id=viewer_user_id,
            include_all_rows=include_all_rows,
        )
        return schema, rows, total

    async def query_rows_detailed(
        self,
        row: AppRecord,
        *,
        table: str = MAIN_TABLE_KEY,
        filters: Mapping[str, object] | None = None,
        sort: str | None = None,
        limit: int = MAX_ROW_READ,
        offset: int = 0,
        redact_pii: bool = False,
        pii_policy: str | None = None,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> tuple[Schema, list[dict[str, str]], list[dict[str, Any]], int]:
        """Read filtered rows plus per-row ``row_id``/``version`` metadata.

        Filter values compare case-insensitively against the stored cell text;
        unknown filter columns raise instead of silently matching nothing.
        ``sort`` is a column name, optionally prefixed with ``-`` for
        descending order; numeric columns compare as numbers.
        """
        key = self._require_table(row, table)
        clean_filters = {str(col).strip(): str(val) for col, val in (filters or {}).items()}
        safe_limit = max(1, min(int(limit), MAX_ROW_READ))
        safe_offset = max(0, int(offset))
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, key)
            restrict_pii = redact_pii or (pii_policy is not None and pii_policy != "visible")
            for column in clean_filters:
                found = schema.column(column)
                if found is None:
                    raise _invalid(
                        ErrorCode.APP_SCHEMA_INVALID,
                        f"unknown filter column: {column!r}",
                        table=key,
                    )
                if restrict_pii and found.pii:
                    raise _invalid(
                        ErrorCode.APP_FORBIDDEN,
                        f"cannot filter on a sensitive column: {column!r}",
                        table=key,
                    )
            sort_spec = _parse_sort(schema, sort)
            if sort_spec is not None:
                sort_column = schema.column(sort_spec[0])
                if restrict_pii and sort_column is not None and sort_column.pii:
                    raise _invalid(
                        ErrorCode.APP_FORBIDDEN,
                        f"cannot sort by a sensitive column: {sort_spec[0]!r}",
                        table=key,
                    )
            owner_filter = self._row_owner_filter(current, key, viewer_user_id, include_all_rows)
            if current.storage_version >= 2:
                data_rows = self._app_repo.list_all_data_rows(current.app_id, key)
                if owner_filter is not None:
                    data_rows = [
                        item for item in data_rows if app_access.row_owner_id(item) == owner_filter
                    ]
                rows = _normalize_rows(schema, [item.values for item in data_rows])
                meta = [
                    {
                        "row_id": item.row_id,
                        "version": item.version,
                        "source": item.source,
                    }
                    for item in data_rows
                ]
                owners: list[int | None] = [app_access.row_owner_id(item) for item in data_rows]
            else:
                if owner_filter is not None:
                    raise OctopError(
                        ErrorCode.APP_FORBIDDEN,
                        "row-scoped table requires DB storage; migrate the app first",
                    )
                rows = await self._read_rows_file(ws, current.app_id, schema, key)
                meta = [{"row_id": "", "version": 1, "source": "manual"} for _ in rows]
                owners = [None] * len(rows)
        if clean_filters:
            wanted = {col: val.strip().lower() for col, val in clean_filters.items()}
            keep = [
                index
                for index, item in enumerate(rows)
                if all(item.get(col, "").strip().lower() == val for col, val in wanted.items())
            ]
            rows = [rows[index] for index in keep]
            meta = [meta[index] for index in keep]
            owners = [owners[index] for index in keep]
        if sort_spec is not None:
            sort_name, descending = sort_spec
            sort_column = schema.column(sort_name)
            numeric = sort_column is not None and sort_column.type in {"number", "integer"}
            order = sorted(
                range(len(rows)),
                key=lambda index: _row_sort_key(rows[index].get(sort_name, ""), numeric),
                reverse=descending,
            )
            rows = [rows[index] for index in order]
            meta = [meta[index] for index in order]
            owners = [owners[index] for index in order]
        end = safe_offset + safe_limit
        if pii_policy is not None:
            schema, page, masked = _apply_pii_policy(
                schema,
                rows[safe_offset:end],
                owners[safe_offset:end],
                pii_policy=pii_policy,
                viewer_user_id=viewer_user_id,
            )
        else:
            schema, page = _apply_pii(
                schema,
                rows[safe_offset:end],
                redact_pii=redact_pii,
            )
            masked = [redact_pii] * len(page)
        page_meta = [
            {**item, "pii_masked": flag}
            for item, flag in zip(meta[safe_offset:end], masked, strict=True)
        ]
        return schema, page, page_meta, len(rows)

    async def append_row(
        self,
        row: AppRecord,
        values: Mapping[str, object],
        *,
        table: str = MAIN_TABLE_KEY,
        client_request_id: str | None = None,
        source: str = "manual",
        source_id: str | None = None,
        source_key: str | None = None,
        created_by: int | None = None,
        row_owner_user_id: int | None = None,
    ) -> tuple[int, dict[str, str]]:
        index, stored = await self.append_row_record(
            row,
            values,
            table=table,
            client_request_id=client_request_id,
            source=source,
            source_id=source_id,
            source_key=source_key,
            created_by=created_by,
            row_owner_user_id=row_owner_user_id,
        )
        return index, stored.values

    async def append_row_record(
        self,
        row: AppRecord,
        values: Mapping[str, object],
        *,
        table: str = MAIN_TABLE_KEY,
        client_request_id: str | None = None,
        source: str = "manual",
        source_id: str | None = None,
        source_key: str | None = None,
        created_by: int | None = None,
        row_owner_user_id: int | None = None,
    ) -> tuple[int, LightAppDataRow]:
        key = self._require_table(row, table)
        scope = self.table_row_scope(row, key)
        if scope == "user" and created_by is None:
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                "row-scoped table requires a creator",
            )
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            self._ensure_main_table(row)
            current = self._app_repo.get(row.app_id) or row
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, key)
            if current.storage_version >= 2:
                if self._app_repo.count_data_rows(current.app_id, key) >= MAX_ROWS:
                    raise _invalid(
                        ErrorCode.APP_LIMIT_EXCEEDED,
                        f"table has reached the {MAX_ROWS}-row limit",
                        limit=MAX_ROWS,
                    )
                normalized = validate_row(schema, values)
                await self._validate_references(current, schema, normalized)
                _reject_unmarked_pii(schema, normalized, table=key)
                table_view = self._app_repo.get_table(current.app_id, key)
                current_bytes = table_view.data_bytes if table_view is not None else 0
                payload_bytes = len(json.dumps(normalized, ensure_ascii=False).encode("utf-8"))
                if current_bytes + payload_bytes > MAX_CSV_BYTES:
                    raise _invalid(
                        ErrorCode.APP_LIMIT_EXCEEDED,
                        f"table data would exceed {MAX_CSV_BYTES // (1024 * 1024)} MB",
                        limit=MAX_CSV_BYTES,
                    )
                index, stored = self._app_repo.append_data_row(
                    current.app_id,
                    key,
                    normalized,
                    source=source,
                    source_id=source_id,
                    source_key=source_key,
                    client_request_id=client_request_id,
                    created_by=created_by,
                    row_owner_user_id=row_owner_user_id,
                )
                self._emit_row_event(current, key, "on_create", stored.row_id, stored.values)
                await self._after_write(ws, current.app_id)
                return index, stored
            if scope == "user":
                raise OctopError(
                    ErrorCode.APP_FORBIDDEN,
                    "row-scoped table requires DB storage; migrate the app first",
                )
            rows = await self._read_rows_file(ws, current.app_id, schema, key)
            if len(rows) >= MAX_ROWS:
                raise _invalid(
                    ErrorCode.APP_LIMIT_EXCEEDED,
                    f"table has reached the {MAX_ROWS}-row limit",
                    limit=MAX_ROWS,
                )
            normalized = validate_row(schema, values)
            await self._validate_references(current, schema, normalized)
            _reject_unmarked_pii(schema, normalized, table=key)
            rows.append(normalized)
            await self._persist_rows(ws, current, key, schema, rows)
            ts = int(time.time())
            return len(rows) - 1, LightAppDataRow(
                row_id="",
                app_id=current.app_id,
                table_key=key,
                values=normalized,
                version=1,
                source=source,
                created_by=created_by,
                row_owner_user_id=(
                    row_owner_user_id if row_owner_user_id is not None else created_by
                ),
                created_at=ts,
                updated_at=ts,
            )

    async def _persist_rows(
        self,
        ws: BackendWorkspace,
        row: AppRecord,
        key: str,
        schema: Schema,
        rows: list[dict[str, str]],
    ) -> None:
        """Write one table's rows back to its CSV and refresh cached stats."""
        csv_text = render_csv(schema, rows)
        if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
            raise _invalid(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"data file would exceed {MAX_CSV_BYTES // (1024 * 1024)} MB",
                limit=MAX_CSV_BYTES,
            )
        _, data_file = table_file_names(key)
        await self._write_atomic(ws, row.app_id, {data_file: csv_text})
        self._app_repo.set_table_stats(
            row.app_id,
            key,
            row_count=len(rows),
            data_bytes=len(csv_text.encode("utf-8")),
        )
        self._app_repo.refresh_app_totals(row.app_id)
        self._app_repo.touch(row.app_id)
        await self._after_write(ws, row.app_id)

    async def _persist_schema(
        self,
        ws: BackendWorkspace,
        app_id: str,
        key: str,
        schema: Schema,
    ) -> None:
        """Persist schema metadata (PII flags) without bumping its version."""
        schema_file, _data_file = table_file_names(key)
        schema_text = schema.to_json()
        await self._write_atomic(ws, app_id, {schema_file: schema_text})
        self._app_repo.set_table_schema(app_id, key, schema_text, schema.version)

    async def save_import_file(self, row: AppRecord, *, filename: str, data: bytes) -> str:
        """Store an uploaded import file under ``inbound/imports/`` in the workspace."""
        return await self.save_quick_import_file(row.agent_id, filename=filename, data=data)

    async def save_quick_import_file(self, agent_id: str, *, filename: str, data: bytes) -> str:
        """Store a file before an app exists (the quick-create upload step)."""
        ws = await self._workspace(agent_id)
        path = f"{IMPORT_DIR}/{new_short_id(8)}-{_safe_import_filename(filename)}"
        await ws.aupload_bytes(path, data)
        return path

    async def quick_inspect(
        self,
        agent_id: str,
        *,
        filename: str,
        data: bytes,
        sheet: str | None = None,
    ) -> dict[str, Any]:
        """Inspect an uploaded ledger and suggest a dashboard view."""
        report = inspect_file(filename, data)
        selected = _select_sheet_report(report, sheet)
        columns = _quick_columns(selected)
        schema = parse_schema_payload({"columns": columns})
        title = _file_stem(filename)
        upload_path = await self.save_quick_import_file(agent_id, filename=filename, data=data)
        return {
            "upload_path": upload_path,
            "sheet": selected.get("name", ""),
            "report": report,
            "suggested_view": suggest_view(selected, schema, title=title),
        }

    async def quick_create(
        self,
        *,
        agent_id: str,
        owner_user_id: int,
        path: str,
        columns: Sequence[Mapping[str, Any]],
        view: str,
        name: str,
        description: str = "",
        sheet: str | None = None,
        header_row: int | None = None,
        mapping: Mapping[str, str] | None = None,
        metric_column: str | None = None,
        group_column: str | None = None,
        include_pii: bool = False,
        upsert_key: list[str] | None = None,
    ) -> tuple[LightAppRow, ImportResult]:
        """Create, import and render a dashboard in one transaction-like step."""
        clean_path = _safe_import_path(path)
        if not clean_path.startswith(f"{IMPORT_DIR}/"):
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                f"quick import path must live under {IMPORT_DIR}/",
            )
        schema = parse_schema_payload({"columns": list(columns)})
        clean_name = validate_name(name)
        clean_description = validate_description(description)
        data = await self.read_workspace_file(agent_id, clean_path)
        report = inspect_file(clean_path, data)
        selected = _select_sheet_report(report, sheet)
        if header_row is None:
            detected = selected.get("header_row")
            if isinstance(detected, int) and detected >= 0:
                # The client may omit header_row; use the same detection the
                # inspection report showed instead of assuming row 0.
                header_row = detected
        row = await self.create(
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            name=clean_name,
            description=clean_description,
            columns={"columns": list(columns)},
        )
        try:
            result = await self.import_from_file(
                row,
                path=clean_path,
                sheet=sheet,
                header_row=header_row,
                mapping=mapping,
                include_pii=include_pii,
                upsert_key=upsert_key,
            )
            if result.imported == 0 and result.updated == 0 and result.errors:
                raise _invalid(ErrorCode.APP_ROW_INVALID, result.errors[0])
            spec = suggest_view(
                selected,
                schema,
                view=view,
                title=clean_name,
                metric=metric_column,
                group=group_column,
            )
            html = render_view_html(schema, spec, title=clean_name, description=clean_description)
            await self.write_html(row, html)
        except Exception:
            try:
                await self.delete(row)
            except Exception:
                logger.warning("failed to roll back quick-create app %s", row.app_id, exc_info=True)
            raise
        return row, result

    async def quick_create_multi(
        self,
        *,
        agent_id: str,
        owner_user_id: int,
        path: str,
        sheets: Sequence[Mapping[str, Any]],
        name: str,
        description: str = "",
        include_pii: bool = False,
    ) -> tuple[LightAppRow, ImportResult]:
        """Create one app from several sheets, each landing in its own table."""
        clean_path = _safe_import_path(path)
        if not clean_path.startswith(f"{IMPORT_DIR}/"):
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                f"quick import path must live under {IMPORT_DIR}/",
            )
        if not sheets:
            raise _invalid(ErrorCode.APP_ROW_INVALID, "at least one sheet is required")
        if len(sheets) > MAX_TABLES_PER_APP:
            raise _invalid(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"at most {MAX_TABLES_PER_APP} sheets can be imported at once",
            )
        clean_name = validate_name(name)
        clean_description = validate_description(description)
        parsed: list[dict[str, Any]] = []
        seen_sheets: set[str] = set()
        for index, item in enumerate(sheets):
            sheet_name = str(item.get("sheet") or "").strip()
            if not sheet_name:
                raise _invalid(ErrorCode.APP_ROW_INVALID, "sheet name is required")
            if sheet_name in seen_sheets:
                raise _invalid(
                    ErrorCode.APP_ROW_INVALID,
                    f"sheet selected more than once: {sheet_name!r}",
                )
            seen_sheets.add(sheet_name)
            columns = item.get("columns")
            schema = parse_schema_payload({"columns": columns})
            key = MAIN_TABLE_KEY if index == 0 else f"table_{index + 1}"
            parsed.append(
                {
                    "sheet": sheet_name,
                    "key": key,
                    "label": sheet_name[:MAX_LABEL_LEN],
                    "schema": schema,
                    "columns": list(columns or []),
                    "header_row": item.get("header_row"),
                    "mapping": item.get("mapping"),
                    "metric": item.get("metric_column"),
                    "group": item.get("group_column"),
                    "view": str(item.get("view") or "dashboard"),
                }
            )
        data = await self.read_workspace_file(agent_id, clean_path)
        report = inspect_file(clean_path, data)
        for item in parsed:
            if item["header_row"] is not None:
                continue
            selected_sheet = _select_sheet_report(report, item["sheet"])
            detected = selected_sheet.get("header_row")
            if isinstance(detected, int) and detected >= 0:
                item["header_row"] = detected
        row = await self.create(
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            name=clean_name,
            description=clean_description,
            columns={"columns": parsed[0]["columns"]},
        )
        imported = updated = skipped = 0
        errors: list[str] = []
        warnings: list[str] = []
        pii_columns: list[str] = []
        blocked_pii: list[str] = []
        failed_rows: list[dict[str, Any]] = []
        try:
            for item in parsed[1:]:
                await self.create_table(
                    row,
                    table_key=item["key"],
                    label=item["label"],
                    columns={"columns": item["columns"]},
                )
            for item in parsed:
                result = await self.import_from_file(
                    row,
                    path=clean_path,
                    table=item["key"],
                    sheet=item["sheet"],
                    header_row=item["header_row"],
                    mapping=item["mapping"],
                    include_pii=include_pii,
                )
                imported += result.imported
                updated += result.updated
                skipped += result.skipped
                errors.extend(result.errors)
                warnings.extend(result.warnings)
                pii_columns.extend(result.pii_columns)
                blocked_pii.extend(result.blocked_pii)
                failed_rows.extend(result.failed_rows)
            if imported == 0 and updated == 0 and errors:
                raise _invalid(ErrorCode.APP_ROW_INVALID, errors[0])
            tables: list[dict[str, Any]] = []
            for item in parsed:
                selected = _select_sheet_report(report, item["sheet"])
                schema = await self.read_schema(row, item["key"])
                spec = suggest_view(
                    selected,
                    schema,
                    view=item["view"],
                    title=clean_name,
                    metric=item["metric"],
                    group=item["group"],
                )
                tables.append(
                    {
                        "key": item["key"],
                        "label": item["label"],
                        "schema": schema,
                        "spec": spec,
                    }
                )
            html = render_multi_html(tables, title=clean_name, description=clean_description)
            await self.write_html(row, html)
        except Exception:
            try:
                await self.delete(row)
            except Exception:
                logger.warning("failed to roll back quick-create app %s", row.app_id, exc_info=True)
            raise
        return row, ImportResult(
            imported=imported,
            skipped=skipped,
            errors=errors[:MAX_IMPORT_ERRORS],
            updated=updated,
            warnings=warnings,
            pii_columns=list(dict.fromkeys(pii_columns)),
            blocked_pii=list(dict.fromkeys(blocked_pii)),
            failed_rows=failed_rows[:MAX_FAILED_ROWS],
        )

    # ----- setup plans -------------------------------------------------

    def _plan_repo(self) -> AppSetupPlanRepo:
        if self._setup_plan_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "setup plan repo is not configured")
        return self._setup_plan_repo

    def _require_setup_plan(
        self,
        plan_id: str,
        user_id: int,
    ) -> tuple[AppSetupPlanRow, bool]:
        plan = self._plan_repo().get(plan_id)
        if plan is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        is_admin = self.is_admin(user_id)
        if not is_admin and plan.user_id != user_id:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        return plan, is_admin

    def _plan_is_expired(self, plan: AppSetupPlanRow) -> bool:
        return plan.expires_at is not None and plan.expires_at < int(self._clock())

    def _audit_plan(
        self,
        action: str,
        plan: AppSetupPlanRow,
        *,
        actor: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if self._audit_repo is None:
            return
        self._audit_repo.write(
            actor=actor,
            action=action,
            target=plan.plan_id,
            payload=json.dumps(dict(payload or {}), ensure_ascii=False),
        )

    async def _expire_plan_if_needed(self, plan: AppSetupPlanRow) -> AppSetupPlanRow:
        """Persist and audit expiry; remove an empty app left by a stale plan."""
        if not self._plan_is_expired(plan):
            return plan
        if plan.status not in (
            "draft",
            PLAN_CONFIRMED,
            PLAN_DRY_RUN_READY,
            PLAN_DRY_RUN_FAILED,
        ):
            return plan
        expired = self._plan_repo().transition(
            plan.plan_id,
            expected_status=plan.status,
            status=PLAN_EXPIRED,
            error="",
        )
        if expired is None:
            return self._plan_repo().get(plan.plan_id) or plan
        await self._rollback_plan_app(plan.plan_id)
        self._audit_plan(
            "miniapp.setup_plan.expire",
            expired,
            actor="_system",
            payload={
                "previous_status": plan.status,
                "expires_at": plan.expires_at,
            },
        )
        return expired

    def _normalize_plan_tables(
        self,
        report: Mapping[str, Any],
        tables: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Validate a multi-sheet plan and fill each table's detected header."""
        if not tables:
            raise _invalid(ErrorCode.APP_SCHEMA_INVALID, "a plan needs at least one table")
        if len(tables) > MAX_TABLES_PER_APP:
            raise _invalid(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"at most {MAX_TABLES_PER_APP} tables can be planned at once",
            )
        normalized: list[dict[str, Any]] = []
        used: set[str] = set()
        for index, raw in enumerate(tables):
            if not isinstance(raw, Mapping):
                raise _invalid(ErrorCode.APP_SCHEMA_INVALID, "each table must be an object")
            key = (
                MAIN_TABLE_KEY
                if index == 0
                else validate_table_key(str(raw.get("key") or f"table_{index + 1}"))
            )
            if key in used:
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    f"duplicate table key: {key}",
                )
            used.add(key)
            sheet_name = str(raw.get("sheet") or "").strip()
            selected = _select_sheet_report(report, sheet_name or None)
            header_row = raw.get("header_row")
            if not isinstance(header_row, int) or header_row < 0:
                detected = selected.get("header_row")
                header_row = detected if isinstance(detected, int) and detected >= 0 else 0
            schema = parse_schema_payload({"columns": list(raw.get("columns") or [])})
            label = str(raw.get("label") or selected.get("name") or key)[:MAX_LABEL_LEN]
            mapping = raw.get("mapping")
            upsert_key = raw.get("upsert_key")
            normalized.append(
                {
                    "key": key,
                    "label": label,
                    "sheet": str(selected.get("name") or ""),
                    "header_row": int(header_row),
                    "columns": schema.to_dict()["columns"],
                    "mapping": dict(mapping) if isinstance(mapping, Mapping) else {},
                    "upsert_key": [str(item) for item in (upsert_key or [])]
                    if isinstance(upsert_key, (list, tuple))
                    else [],
                    "include_pii": bool(raw.get("include_pii")),
                }
            )
        return normalized

    def _normalize_plan_services(
        self,
        services: Sequence[Mapping[str, Any]],
        *,
        owner_user_id: int,
        is_admin: bool,
    ) -> list[dict[str, Any]]:
        """Preflight the setup manifest's services without creating anything.

        Mirrors ``save_service`` validation: connector existence/ownership,
        auto-provisionable public kinds, tool allowlists, URL prefixes, headers,
        cache TTL and rate limits.
        """
        normalized: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for raw in services:
            if not isinstance(raw, Mapping):
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, "each service must be an object")
            name = str(raw.get("name") or "").strip().lower()
            if not SERVICE_NAME_RE.fullmatch(name) or name == BUILTIN_BINDING_ID:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "service name must be lowercase letters, digits, '-' or '_'",
                )
            if name in used_names:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    f"duplicate service name: {name!r}",
                )
            used_names.add(name)
            provider = str(raw.get("provider") or "").strip().lower()
            if provider not in SERVICE_PROVIDERS:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "service provider must be connector or external",
                )
            if provider == "connector" and not self._connector_repo:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "connectors are not configured on this server",
                )
            instance_id = str(raw.get("connector_instance_id") or "").strip()
            instance_kind: str | None = None
            if provider == "external" and not instance_id:
                auto_kind = str(raw.get("connector_kind") or "http-extract").strip().lower()
                entry = get_catalog_entry(auto_kind)
                if entry is None or entry.auth_kind != "none" or entry.mcp_mode != "gateway":
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        f"connector {auto_kind!r} cannot be auto-provisioned; it needs credentials",
                    )
                instance_kind = auto_kind
                if self._connector_repo is not None:
                    existing = self._connector_repo.get_by_user_kind(owner_user_id, auto_kind)
                    if existing is not None:
                        instance_id = existing.instance_id
            elif not instance_id:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "connector_instance_id is required for provider=connector",
                )
            else:
                if self._connector_repo is None:
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        "connectors are not configured on this server",
                    )
                instance = self._connector_repo.get(instance_id)
                if instance is None:
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        f"unknown connector: {instance_id!r}",
                    )
                instance_kind = instance.kind
                if not (is_admin or instance.user_id == owner_user_id or instance.shared):
                    raise _invalid(
                        ErrorCode.APP_FORBIDDEN,
                        "only the connector owner can bind it to this app",
                    )
            tools = [
                str(item).strip() for item in (raw.get("allowed_tools") or []) if str(item).strip()
            ]
            if not tools:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "at least one read-only tool is required",
                )
            tools = list(dict.fromkeys(tools))
            prefixes = list(
                dict.fromkeys(
                    str(item).strip().rstrip("/")
                    for item in (raw.get("allowed_url_prefixes") or [])
                    if str(item).strip()
                )
            )
            if instance_kind == "douban" and not prefixes:
                prefixes = list(COVER_URL_PREFIXES)
            for prefix in prefixes:
                if not prefix.startswith(("https://", "http://")):
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        f"invalid URL prefix: {prefix!r}",
                    )
            gateway_tools = self._gateway_tool_names(instance_kind) if instance_kind else set()
            if gateway_tools:
                unknown = set(tools) - gateway_tools
                if unknown:
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        f"connector {instance_kind!r} does not provide tools: "
                        f"{', '.join(sorted(unknown))}",
                    )
            if instance_kind == "http-extract" and not prefixes:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "http-extract services require at least one URL prefix",
                )
            try:
                headers = normalize_request_headers(
                    raw.get("request_headers")
                    if isinstance(raw.get("request_headers"), Mapping)
                    else None
                )
                if headers and instance_kind != "http-extract":
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        "request_headers are only supported by http-extract services",
                    )
                ttl_raw = raw.get("cache_ttl_seconds", 300)
                calls_raw = raw.get("max_calls_per_minute", 30)
                ttl = int(str(ttl_raw)) if ttl_raw is not None else 300
                max_calls = int(str(calls_raw)) if calls_raw is not None else 30
            except (TypeError, ValueError) as exc:
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, str(exc)) from None
            if not 0 <= ttl <= 86_400:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "cache_ttl_seconds must be 0..86400",
                )
            if not 1 <= max_calls <= 600:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "max_calls_per_minute must be 1..600",
                )
            normalized.append(
                {
                    "name": name,
                    "provider": provider,
                    "connector_instance_id": instance_id,
                    "connector_kind": instance_kind
                    if provider == "external" and instance_kind
                    else (str(raw.get("connector_kind") or "").strip() or None),
                    "allowed_tools": tools,
                    "allowed_url_prefixes": prefixes,
                    "request_headers": headers,
                    "cache_ttl_seconds": ttl,
                    "max_calls_per_minute": max_calls,
                    "enabled": bool(raw.get("enabled", True)),
                }
            )
        return normalized

    def _apply_plan_extras(
        self,
        app: LightAppRow,
        payload: Mapping[str, Any],
        *,
        user_id: int,
    ) -> tuple[LightAppRow, dict[str, str], list[str]]:
        """Apply the confirmed service/capability manifest after the import."""
        steps: dict[str, str] = {}
        errors: list[str] = []
        capabilities = _plan_capabilities_from_payload(payload)
        if capabilities is not None:
            try:
                app = self.set_capabilities(
                    app,
                    app_capabilities.validate_agent_settable(capabilities),
                )
                steps["capabilities"] = "ok"
            except Exception as exc:
                steps["capabilities"] = str(exc)
                errors.append(str(exc))
        for service in _plan_services_from_payload(payload):
            name = str(service.get("name") or "")
            try:
                self.save_service(
                    app,
                    name=name,
                    provider=str(service.get("provider") or ""),
                    connector_instance_id=str(service.get("connector_instance_id") or ""),
                    connector_kind=service.get("connector_kind"),
                    allowed_tools=list(service.get("allowed_tools") or []),
                    allowed_url_prefixes=list(service.get("allowed_url_prefixes") or []),
                    request_headers=service.get("request_headers") or {},
                    cache_ttl_seconds=int(service.get("cache_ttl_seconds") or 300),
                    max_calls_per_minute=int(service.get("max_calls_per_minute") or 30),
                    public_live=False,
                    enabled=bool(service.get("enabled", True)),
                    actor_user_id=user_id,
                    is_admin=self.is_admin(user_id),
                )
                steps[f"service:{name}"] = "ok"
            except Exception as exc:
                steps[f"service:{name}"] = str(exc)
                errors.append(f"{name}: {exc}")
        return app, steps, errors

    async def _plan_source_data(self, plan: AppSetupPlanRow) -> bytes:
        """Read the plan's source and reject a file that changed after inspection."""
        data = await self.read_workspace_file(plan.agent_id, plan.source_path)
        if plan.source_hash and hashlib.sha256(data).hexdigest() != plan.source_hash:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the source file changed after inspection; re-propose the plan",
            )
        return data

    async def propose_setup_plan(
        self,
        *,
        agent_id: str,
        user_id: int,
        thread_id: str | None,
        path: str,
        name: str,
        columns: Sequence[Mapping[str, Any]] | None = None,
        sheet: str | None = None,
        header_row: int | None = None,
        mapping: Mapping[str, str] | None = None,
        upsert_key: Sequence[str] | None = None,
        include_pii: bool = False,
        tables: Sequence[Mapping[str, Any]] | None = None,
        services: Sequence[Mapping[str, Any]] | None = None,
        capabilities: Sequence[str] | None = None,
        expires_in: int = 24 * 3600,
    ) -> AppSetupPlanRow:
        """Create (or refresh) a draft setup plan; no side effects beyond the row."""
        if not (self.is_admin(user_id) or self.is_agent_accessible(agent_id, user_id)):
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        clean_name = validate_name(name)
        clean_path = _safe_import_path(path)
        data = await self.read_workspace_file(agent_id, clean_path)
        report = inspect_file(clean_path, data)
        if tables is not None:
            requested = list(tables)
        else:
            if not columns:
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    "provide columns for one table, or a tables list",
                )
            requested = [
                {
                    "key": MAIN_TABLE_KEY,
                    "label": sheet or "",
                    "sheet": sheet,
                    "header_row": header_row,
                    "columns": list(columns),
                    "mapping": mapping,
                    "upsert_key": upsert_key,
                    "include_pii": include_pii,
                }
            ]
        normalized = self._normalize_plan_tables(report, requested)
        clean_services = self._normalize_plan_services(
            list(services or []),
            owner_user_id=user_id,
            is_admin=self.is_admin(user_id),
        )
        clean_capabilities = (
            list(app_capabilities.validate_agent_settable(capabilities))
            if capabilities is not None
            else None
        )
        table = {
            "tables": normalized,
            "services": clean_services,
            "capabilities": clean_capabilities,
        }
        source_hash = hashlib.sha256(data).hexdigest()
        expires_at = int(self._clock()) + max(60, int(expires_in))
        repo = self._plan_repo()
        if thread_id:
            existing = repo.get_active_for_thread(thread_id)
            if existing is not None:
                if existing.user_id != user_id or existing.agent_id != agent_id:
                    raise OctopError.localized(ErrorCode.APP_CONFLICT)
                if existing.status != "draft":
                    raise OctopError(
                        ErrorCode.APP_CONFLICT,
                        "a setup plan is already in progress; confirm or cancel it first",
                    )
                refreshed = repo.update_draft(
                    existing.plan_id,
                    expected_revision=existing.revision,
                    source_path=clean_path,
                    source_hash=source_hash,
                    report=report,
                    table=table,
                    name=clean_name,
                    expires_at=expires_at,
                )
                if refreshed is None:
                    raise OctopError(
                        ErrorCode.APP_CONFLICT,
                        "the setup plan changed; refresh and try again",
                    )
                self._audit_plan(
                    "miniapp.setup_plan.propose",
                    refreshed,
                    actor=f"user:{user_id}",
                    payload={
                        "agent_id": agent_id,
                        "thread_id": thread_id,
                        "source": clean_path,
                        "revision": refreshed.revision,
                        "tables": [item["key"] for item in normalized],
                        "services": [item["name"] for item in clean_services],
                        "capabilities": clean_capabilities,
                        "refreshed": True,
                    },
                )
                return refreshed
        created = repo.create(
            plan_id=f"plan_{new_ulid()}",
            thread_id=thread_id,
            agent_id=agent_id,
            user_id=user_id,
            source_path=clean_path,
            source_hash=source_hash,
            report=report,
            table=table,
            name=clean_name,
            expires_at=expires_at,
        )
        self._audit_plan(
            "miniapp.setup_plan.propose",
            created,
            actor=f"user:{user_id}",
            payload={
                "agent_id": agent_id,
                "thread_id": thread_id,
                "source": clean_path,
                "revision": created.revision,
                "tables": [item["key"] for item in normalized],
                "services": [item["name"] for item in clean_services],
                "capabilities": clean_capabilities,
                "refreshed": False,
            },
        )
        return created

    async def get_setup_plan(self, plan_id: str, user_id: int) -> AppSetupPlanRow:
        plan, _ = self._require_setup_plan(plan_id, user_id)
        return await self._expire_plan_if_needed(plan)

    def active_setup_plan(self, thread_id: str | None) -> AppSetupPlanRow | None:
        if not thread_id:
            return None
        return self._plan_repo().get_active_for_thread(thread_id)

    async def confirm_setup_plan(
        self,
        plan_id: str,
        *,
        user_id: int,
        revision: int,
        name: str,
        columns: Sequence[Mapping[str, Any]] | None = None,
        include_pii: bool | None = None,
        tables: Sequence[Mapping[str, Any]] | None = None,
        services: Sequence[Mapping[str, Any]] | None = None,
        capabilities: Sequence[str] | None = None,
    ) -> AppSetupPlanRow:
        """Freeze the confirmed schema, create the empty app and dry-run it."""
        plan, _ = self._require_setup_plan(plan_id, user_id)
        plan = await self._expire_plan_if_needed(plan)
        # A crash between the CAS and the dry-run leaves the plan in
        # ``confirmed``. After a short lease, allow the user to retry the same
        # revision; the app is reused and no data has been written yet.
        stale_confirmed = (
            plan.status == PLAN_CONFIRMED and int(self._clock()) - plan.updated_at > 300
        )
        if plan.status not in CONFIRMABLE_PLAN_STATUSES and not stale_confirmed:
            raise OctopError.localized(ErrorCode.APP_CONFLICT)
        if plan.revision != revision:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the setup plan changed; refresh and confirm the latest revision",
            )
        if self._plan_is_expired(plan):
            raise OctopError(ErrorCode.APP_CONFLICT, "the setup plan has expired")
        source_data = await self._plan_source_data(plan)
        if stale_confirmed:
            # The app/schema from the interrupted attempt is authoritative.
            clean_name = validate_name(plan.name)
            normalized = _plan_tables_from_payload(plan.table)
            if not normalized:
                raise _invalid(ErrorCode.APP_SCHEMA_INVALID, "the plan has no tables")
        else:
            clean_name = validate_name(name)
            if tables is not None:
                requested = [dict(item) for item in tables]
            else:
                if not columns:
                    raise _invalid(
                        ErrorCode.APP_SCHEMA_INVALID,
                        "provide columns for one table, or a tables list",
                    )
                base_tables = _plan_tables_from_payload(plan.table)
                base = base_tables[0] if base_tables else {"key": MAIN_TABLE_KEY}
                requested = [{**base, "columns": list(columns)}]
                if include_pii is not None:
                    requested[0]["include_pii"] = bool(include_pii)
            normalized = self._normalize_plan_tables(plan.report, requested)
        if stale_confirmed:
            extras = {
                key: plan.table[key] for key in ("services", "capabilities") if key in plan.table
            }
        else:
            extras = {}
            if services is not None:
                extras["services"] = self._normalize_plan_services(
                    list(services),
                    owner_user_id=plan.user_id,
                    is_admin=self.is_admin(user_id),
                )
            elif "services" in plan.table:
                extras["services"] = plan.table["services"]
            if capabilities is not None:
                extras["capabilities"] = list(
                    app_capabilities.validate_agent_settable(capabilities)
                )
            elif "capabilities" in plan.table:
                extras["capabilities"] = plan.table["capabilities"]
        table = {"tables": normalized, **extras}
        main_table = normalized[0]
        next_revision = plan.revision + 1 if plan.status == PLAN_DRY_RUN_FAILED else plan.revision
        confirmed = self._plan_repo().transition(
            plan_id,
            expected_status=plan.status,
            status=PLAN_CONFIRMED,
            expected_revision=plan.revision,
            name=clean_name,
            table_json=table,
            revision=next_revision,
            dry_run_json={},
            result_json={},
            error="",
            confirmed_at=int(self._clock()),
        )
        if confirmed is None:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the setup plan changed; refresh and confirm again",
            )
        try:
            app = self._app_repo.get_by_setup_plan_id(plan_id)
            new_app = app is None
            if app is None:
                app = await self.create(
                    agent_id=confirmed.agent_id,
                    owner_user_id=confirmed.user_id,
                    name=clean_name,
                    description="",
                    columns={"columns": main_table["columns"]},
                    setup_plan_id=plan_id,
                )
            if confirmed.app_id != app.app_id:
                linked = self._plan_repo().transition(
                    plan_id,
                    expected_status=PLAN_CONFIRMED,
                    status=PLAN_CONFIRMED,
                    app_id=app.app_id,
                )
                if linked is None:
                    await self._rollback_plan_app(plan_id)
                    raise OctopError(
                        ErrorCode.APP_CONFLICT,
                        "the setup plan changed while creating the app; refresh",
                    )
            if new_app:
                for item in normalized[1:]:
                    await self.create_table(
                        app,
                        table_key=item["key"],
                        label=item["label"],
                        columns={"columns": item["columns"]},
                    )
            dry_results: dict[str, dict[str, Any]] = {}
            for item in normalized:
                result = await self.import_from_file(
                    app,
                    path=confirmed.source_path,
                    data=source_data,
                    table=item["key"],
                    sheet=str(item.get("sheet") or "") or None,
                    header_row=item.get("header_row"),
                    mapping=item.get("mapping") or None,
                    dry_run=True,
                    upsert_key=item.get("upsert_key") or None,
                    include_pii=bool(item.get("include_pii")),
                    created_by=confirmed.user_id,
                    record_history=False,
                )
                dry_results[item["key"]] = import_result_payload(result)
        except Exception as exc:
            await self._rollback_plan_app(plan_id)
            failed = self._plan_repo().transition(
                plan_id,
                expected_status=PLAN_CONFIRMED,
                status=PLAN_DRY_RUN_FAILED,
                app_id=None,
                error=str(exc),
            )
            if failed is not None:
                self._audit_plan(
                    "miniapp.setup_plan.confirm",
                    failed,
                    actor=f"user:{user_id}",
                    payload={"status": failed.status, "error": failed.error},
                )
                return failed
            raise
        dry_summary = _aggregate_import_payloads(list(dry_results.values()))
        failed_tables = [
            key
            for key, payload in dry_results.items()
            if payload.get("errors") and int(payload.get("would_import") or 0) == 0
        ]
        if failed_tables:
            await self._rollback_plan_app(plan_id)
            failed = self._plan_repo().transition(
                plan_id,
                expected_status=PLAN_CONFIRMED,
                status=PLAN_DRY_RUN_FAILED,
                app_id=None,
                error="; ".join(dry_summary["errors"][:MAX_IMPORT_ERRORS]),
            )
            if failed is not None:
                self._audit_plan(
                    "miniapp.setup_plan.confirm",
                    failed,
                    actor=f"user:{user_id}",
                    payload={"status": failed.status, "error": failed.error},
                )
                return failed
            raise OctopError(ErrorCode.APP_ROW_INVALID, "dry run found no importable rows")
        ready = self._plan_repo().transition(
            plan_id,
            expected_status=PLAN_CONFIRMED,
            status=PLAN_DRY_RUN_READY,
            dry_run_json={**dry_summary, "tables": dry_results},
        )
        if ready is None:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the setup plan changed while running the dry run; refresh",
            )
        self._audit_plan(
            "miniapp.setup_plan.confirm",
            ready,
            actor=f"user:{user_id}",
            payload={
                "status": ready.status,
                "revision": ready.revision,
                "app_id": ready.app_id,
                "tables": list(dry_results),
                "services": [item.get("name") for item in _plan_services_from_payload(ready.table)],
                "capabilities": _plan_capabilities_from_payload(ready.table),
                "would_import": dry_summary["would_import"],
                "errors": dry_summary["errors"][:5],
            },
        )
        return ready

    async def execute_setup_plan(
        self,
        plan_id: str,
        *,
        user_id: int,
        revision: int,
    ) -> AppSetupPlanRow:
        """Run the real import once for an approved, dry-run-ready plan."""
        plan, _ = self._require_setup_plan(plan_id, user_id)
        plan = await self._expire_plan_if_needed(plan)
        if plan.status not in EXECUTABLE_PLAN_STATUSES:
            raise OctopError.localized(ErrorCode.APP_CONFLICT)
        if plan.revision != revision:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the setup plan changed; refresh before importing",
            )
        if self._plan_is_expired(plan):
            raise OctopError(ErrorCode.APP_CONFLICT, "the setup plan has expired")
        if not plan.app_id:
            raise OctopError(ErrorCode.APP_CONFLICT, "the setup plan has no app")
        app = self._app_repo.get(plan.app_id)
        if app is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        source_data = await self._plan_source_data(plan)
        importing = self._plan_repo().transition(
            plan_id,
            expected_status=plan.status,
            status=PLAN_IMPORTING,
            expected_revision=plan.revision,
            error="",
        )
        if importing is None:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the setup plan is already being imported; refresh",
            )
        tables = _plan_tables_from_payload(importing.table)
        if not tables:
            failed = self._plan_repo().transition(
                plan_id,
                expected_status=PLAN_IMPORTING,
                status=PLAN_DRY_RUN_FAILED,
                error="the plan has no tables",
            )
            if failed is not None:
                self._audit_plan(
                    "miniapp.setup_plan.execute",
                    failed,
                    actor=f"user:{user_id}",
                    payload={"status": failed.status, "error": failed.error},
                )
            raise OctopError(ErrorCode.APP_SCHEMA_INVALID, "the plan has no tables")
        results: dict[str, dict[str, Any]] = {}
        try:
            for item in tables:
                result = await self.import_from_file(
                    app,
                    path=importing.source_path,
                    data=source_data,
                    table=item["key"],
                    sheet=str(item.get("sheet") or "") or None,
                    header_row=item.get("header_row"),
                    mapping=item.get("mapping") or None,
                    dry_run=False,
                    upsert_key=item.get("upsert_key") or None,
                    include_pii=bool(item.get("include_pii")),
                    created_by=importing.user_id,
                    idempotency_source=(f"plan:{plan_id}:r{importing.revision}:{item['key']}"),
                )
                results[item["key"]] = import_result_payload(result)
        except Exception as exc:
            failed = self._plan_repo().transition(
                plan_id,
                expected_status=PLAN_IMPORTING,
                status=PLAN_DRY_RUN_READY,
                error=str(exc),
            )
            if failed is not None:
                self._audit_plan(
                    "miniapp.setup_plan.execute",
                    failed,
                    actor=f"user:{user_id}",
                    payload={"status": failed.status, "error": failed.error},
                )
            raise
        app, steps, extra_errors = self._apply_plan_extras(
            app,
            importing.table,
            user_id=user_id,
        )
        summary = _aggregate_import_payloads(list(results.values()))
        summary["errors"].extend(extra_errors)
        status = PLAN_IMPORT_PARTIAL if summary["errors"] else PLAN_IMPORTED
        finished = self._plan_repo().transition(
            plan_id,
            expected_status=PLAN_IMPORTING,
            status=status,
            result_json={**summary, "tables": results, "steps": steps},
            executed_at=int(self._clock()),
        )
        if finished is None:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the setup plan changed while importing; refresh",
            )
        self._audit_plan(
            "miniapp.setup_plan.execute",
            finished,
            actor=f"user:{user_id}",
            payload={
                "status": finished.status,
                "revision": finished.revision,
                "app_id": finished.app_id,
                "tables": list(results),
                "services": [
                    item.get("name") for item in _plan_services_from_payload(finished.table)
                ],
                "capabilities": _plan_capabilities_from_payload(finished.table),
                "imported": summary["imported"],
                "errors": summary["errors"][:5],
                "steps": steps,
            },
        )
        try:
            latest = self._app_repo.get(app.app_id) or app
            if latest.html_bytes == 0:
                views: list[dict[str, Any]] = []
                for item in tables:
                    selected = _select_sheet_report(importing.report, item.get("sheet") or None)
                    schema = await self.read_schema(app, item["key"])
                    spec = suggest_view(
                        selected,
                        schema,
                        view="dashboard",
                        title=importing.name,
                    )
                    views.append(
                        {
                            "key": item["key"],
                            "label": item["label"],
                            "schema": schema,
                            "spec": spec,
                        }
                    )
                if len(views) == 1:
                    html = render_view_html(
                        views[0]["schema"],
                        views[0]["spec"],
                        title=importing.name,
                        description="",
                    )
                else:
                    html = render_multi_html(
                        views,
                        title=importing.name,
                        description="",
                    )
                await self.write_html(app, html)
        except Exception:
            # The rows are already imported; a deterministic view is a bonus,
            # and the agent can still write a custom UI with app_write_html.
            logger.warning(
                "failed to render setup plan view for %s",
                plan_id,
                exc_info=True,
            )
        return finished

    async def supersede_setup_plan(
        self,
        plan_id: str,
        *,
        reason: str,
        app_id: str | None = None,
        actor_user_id: int | None = None,
    ) -> AppSetupPlanRow | None:
        """Close a still-active plan when another app creation wins the thread."""
        plan = self._plan_repo().get(plan_id)
        if plan is None:
            return None
        if plan.status not in (
            "draft",
            PLAN_CONFIRMED,
            PLAN_DRY_RUN_READY,
            PLAN_DRY_RUN_FAILED,
        ):
            return plan
        superseded = self._plan_repo().transition(
            plan_id,
            expected_status=plan.status,
            status=PLAN_SUPERSEDED,
            app_id=None,
            error="",
        )
        if superseded is None:
            return self._plan_repo().get(plan_id)
        await self._rollback_plan_app(plan_id)
        self._audit_plan(
            "miniapp.setup_plan.supersede",
            superseded,
            actor=f"user:{actor_user_id}" if actor_user_id is not None else "_system",
            payload={
                "previous_status": plan.status,
                "reason": reason,
                "new_app_id": app_id,
            },
        )
        return superseded

    async def cancel_setup_plan(self, plan_id: str, *, user_id: int) -> AppSetupPlanRow:
        plan, _ = self._require_setup_plan(plan_id, user_id)
        if plan.status not in CANCELLABLE_PLAN_STATUSES:
            raise OctopError.localized(ErrorCode.APP_CONFLICT)
        app = self._app_repo.get_by_setup_plan_id(plan_id)
        if app is not None and app.row_count > 0:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                "the plan has already imported data; delete the app explicitly instead",
            )
        cancelled = self._plan_repo().transition(
            plan_id,
            expected_status=plan.status,
            status=PLAN_CANCELLED,
            app_id=None,
        )
        if cancelled is None:
            raise OctopError(ErrorCode.APP_CONFLICT, "the setup plan changed; refresh")
        await self._rollback_plan_app(plan_id)
        self._audit_plan(
            "miniapp.setup_plan.cancel",
            cancelled,
            actor=f"user:{user_id}",
            payload={"previous_status": plan.status},
        )
        return cancelled

    async def _rollback_plan_app(self, plan_id: str) -> None:
        """Delete the empty app created by a failed/cancelled plan."""
        app = self._app_repo.get_by_setup_plan_id(plan_id)
        if app is None:
            return
        if app.row_count > 0:
            return
        try:
            await self.delete(app)
        except Exception:
            logger.warning("failed to roll back setup plan app %s", app.app_id, exc_info=True)

    async def read_workspace_file(self, agent_id: str, path: str) -> bytes:
        """Read a workspace file (a path outside the app's own directory)."""
        source_path = _safe_import_path(path)
        ws = await self._workspace(agent_id)
        data = await ws.adownload_bytes(source_path)
        if data is None:
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                f"file not found: {source_path}",
            )
        return data

    async def inspect_workspace_file(self, agent_id: str, path: str) -> dict[str, Any]:
        """Inspect a spreadsheet in the agent workspace and return a report.

        The report is a compact description of the file — sheets, headers,
        column statistics and a few sample rows — so an agent can propose a
        schema without reading the whole file into its context.
        """
        source_path = _safe_import_path(path)
        data = await self.read_workspace_file(agent_id, source_path)
        return inspect_file(source_path, data)

    async def import_from_file(
        self,
        row: AppRecord,
        *,
        path: str,
        table: str = MAIN_TABLE_KEY,
        mode: str = IMPORT_MODE_APPEND,
        mapping: Mapping[str, str] | None = None,
        data: bytes | None = None,
        sheet: str | None = None,
        header_row: int | None = None,
        dry_run: bool = False,
        upsert_key: list[str] | None = None,
        include_pii: bool = False,
        created_by: int | None = None,
        idempotency_source: str | None = None,
        record_history: bool = True,
    ) -> ImportResult:
        """Parse a workspace file (or raw bytes) and import it into one table.

        ``sheet`` and ``header_row`` point at the part of the file to import
        (an Excel worksheet and its header line). ``dry_run`` validates and
        counts the rows without writing anything. ``idempotency_source`` turns
        the parsed batch into a content-addressed import for a data source.
        """
        source_path = _safe_import_path(path)
        if data is None:
            ws = await self._workspace(row.agent_id)
            data = await ws.adownload_bytes(source_path)
            if data is None:
                raise _invalid(
                    ErrorCode.APP_ROW_INVALID,
                    f"import file not found: {source_path}",
                )
        records = parse_import_file(
            source_path,
            data,
            sheet=sheet,
            header_row=header_row,
        )
        if not records:
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                f"import file has no data rows: {source_path}",
            )
        return await self.import_rows(
            row,
            records,
            table=table,
            mode=mode,
            mapping=mapping,
            dry_run=dry_run,
            upsert_key=upsert_key,
            include_pii=include_pii,
            created_by=created_by,
            record_history=record_history,
            client_request_id_prefix=(
                _batch_prefix(
                    idempotency_source,
                    records,
                    table=table,
                    mapping=mapping,
                    upsert_key=upsert_key,
                    include_pii=include_pii,
                )
                if idempotency_source
                else None
            ),
        )

    async def import_from_files(
        self,
        row: AppRecord,
        *,
        paths: list[str],
        table: str = MAIN_TABLE_KEY,
        mode: str = IMPORT_MODE_APPEND,
        mapping: Mapping[str, str] | None = None,
        sheet: str | None = None,
        header_row: int | None = None,
        dry_run: bool = False,
        upsert_key: list[str] | None = None,
        include_pii: bool = False,
        created_by: int | None = None,
        record_history: bool = True,
    ) -> ImportResult:
        """Import a batch of workspace files into one table, in order."""
        if not paths:
            raise _invalid(ErrorCode.APP_ROW_INVALID, "no import files were given")
        ws = await self._workspace(row.agent_id)
        records: list[dict[str, str]] = []
        for path in paths:
            source_path = _safe_import_path(path)
            data = await ws.adownload_bytes(source_path)
            if data is None:
                raise _invalid(
                    ErrorCode.APP_ROW_INVALID,
                    f"import file not found: {source_path}",
                )
            records.extend(
                parse_import_file(
                    source_path,
                    data,
                    sheet=sheet,
                    header_row=header_row,
                )
            )
        if not records:
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                "import files have no data rows",
            )
        return await self.import_rows(
            row,
            records,
            table=table,
            mode=mode,
            mapping=mapping,
            dry_run=dry_run,
            upsert_key=upsert_key,
            include_pii=include_pii,
            created_by=created_by,
            record_history=record_history,
        )

    async def import_rows(
        self,
        row: AppRecord,
        records: list[dict[str, str]],
        *,
        table: str = MAIN_TABLE_KEY,
        mode: str = IMPORT_MODE_APPEND,
        mapping: Mapping[str, str] | None = None,
        dry_run: bool = False,
        upsert_key: list[str] | None = None,
        include_pii: bool = False,
        source: str = "import",
        client_request_id_prefix: str | None = None,
        created_by: int | None = None,
        row_owner_user_id: int | None = None,
        actor_user_id: int | None = None,
        record_history: bool = True,
    ) -> ImportResult:
        """Validate and write parsed records into one table (append or replace).

        Rows that fail validation are skipped and counted, not fatal; a
        replace that would wipe the table because *every* record failed is
        rejected instead of silently emptying it. With ``dry_run`` the rows
        are validated and counted but nothing is written.
        """
        if mode not in IMPORT_MODES:
            raise _invalid(
                ErrorCode.APP_ROW_INVALID,
                f"invalid import mode: {mode!r}",
            )
        key = self._require_table(row, table)
        actor = actor_user_id if actor_user_id is not None else created_by
        # Scheduled source syncs replace/upsert the whole table every few
        # minutes; recording each old row would turn the history ledger into
        # a write-amplified copy of the source. Manual/agent imports keep it.
        history_actor = _history_actor(actor) if record_history else None
        owner_id = created_by
        if self.table_row_scope(row, key) == "user" and owner_id is None:
            # Imports do not have a per-row actor here; attribute them to the
            # owner so user-scoped rows never become invisible orphans.
            owner_id = row.owner_user_id
        owner_override = row_owner_user_id if row_owner_user_id is not None else owner_id
        clean = [dict(record) for record in records]
        if len(clean) > MAX_ROWS:
            raise _invalid(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"import file exceeds the {MAX_ROWS}-row limit",
                limit=MAX_ROWS,
            )
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            self._ensure_main_table(row)
            current = self._app_repo.get(row.app_id) or row
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, key)
            source_map = (
                {str(src).strip(): str(dst).strip() for src, dst in mapping.items()}
                if mapping
                else {name: name for name in schema.names}
            )
            unknown = set(source_map.values()) - set(schema.names)
            if unknown:
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    f"unknown target columns: {', '.join(sorted(unknown))}",
                )
            if upsert_key:
                unknown_keys = set(upsert_key) - set(schema.names)
                if unknown_keys:
                    raise _invalid(
                        ErrorCode.APP_SCHEMA_INVALID,
                        f"unknown upsert columns: {', '.join(sorted(unknown_keys))}",
                    )
            targets = [
                {dst: str(record.get(src, "")) for src, dst in source_map.items()}
                for record in clean
            ]
            schema_with_pii = _with_detected_pii(schema, targets)
            pii_changed = schema_with_pii is not schema
            schema = schema_with_pii
            pii_columns = _pii_names(schema)
            blocked_pii = [] if include_pii else pii_columns
            required_blocked = [
                name
                for name in blocked_pii
                if (column := schema.column(name)) is not None and column.required
            ]
            if required_blocked:
                raise _invalid(
                    ErrorCode.APP_ROW_INVALID,
                    (
                        "sensitive required columns cannot be skipped: "
                        f"{', '.join(required_blocked)}; pass include_pii=true "
                        "to import them, or make them optional"
                    ),
                    blocked_pii=required_blocked,
                )
            imported_rows: list[dict[str, str]] = []
            errors: list[str] = []
            failed_rows: list[dict[str, Any]] = []
            reference_cache: dict[str, set[str]] = {}
            for index, target in enumerate(targets):
                if blocked_pii:
                    target = {
                        name: ("" if name in blocked_pii else value)
                        for name, value in target.items()
                    }
                try:
                    normalized = validate_row(schema, target)
                    await self._validate_references(
                        current, schema, normalized, cache=reference_cache
                    )
                    imported_rows.append(normalized)
                except OctopError as exc:
                    if len(errors) < MAX_IMPORT_ERRORS:
                        errors.append(f"row {index + 1}: {exc.message}")
                    if len(failed_rows) < MAX_FAILED_ROWS:
                        failed_rows.append(
                            {"row": index + 1, "values": target, "error": exc.message}
                        )
                    continue
            warnings: list[str] = []
            for name in pii_columns:
                column = schema.column(name)
                kind = column.pii if column is not None else "sensitive"
                if name in blocked_pii:
                    warnings.append(
                        f"column {name!r} looks like {kind}; values were not imported "
                        "(pass include_pii=true to keep them)"
                    )
                else:
                    warnings.append(
                        f"column {name!r} looks like {kind}; imported as sensitive data"
                    )
            # Replace always writes the whole table, so an empty result wipes
            # it. That is never what "import zero records" means: either every
            # row failed validation, or the source returned nothing (a
            # connector can transiently do that). Refuse both instead of
            # destroying the user's data.
            if mode == IMPORT_MODE_REPLACE and not imported_rows:
                raise _invalid(
                    ErrorCode.APP_ROW_INVALID,
                    (
                        "every row failed validation; table left unchanged"
                        if clean
                        else "no records to import; table left unchanged"
                    ),
                    errors=errors,
                )
            if mode == IMPORT_MODE_REPLACE:
                target_ids = {
                    item.row_id for item in self._app_repo.list_all_data_rows(current.app_id, key)
                }
                inbound = (
                    await self._inbound_references(current, key, target_ids) if target_ids else []
                )
                if inbound:
                    table_key, column_name, _value = inbound[0]
                    raise OctopError(
                        ErrorCode.APP_ROW_IN_USE,
                        (
                            f"table {key!r} is referenced by "
                            f"{table_key}.{column_name}; replace would break those references"
                        ),
                        details={"table": table_key, "column": column_name},
                    )
            if current.storage_version >= 2:
                duplicate_indices: set[int] = set()
                if client_request_id_prefix and mode != IMPORT_MODE_REPLACE:
                    existing_ids = self._app_repo.existing_client_request_ids(
                        current.app_id, client_request_id_prefix
                    )
                    duplicate_indices = {
                        index
                        for index in range(len(imported_rows))
                        if f"{client_request_id_prefix}:{index}" in existing_ids
                    }
                new_rows = [
                    item
                    for index, item in enumerate(imported_rows)
                    if index not in duplicate_indices
                ]
                existing_count = (
                    0
                    if mode == IMPORT_MODE_REPLACE
                    else self._app_repo.count_data_rows(current.app_id, key)
                )
                if existing_count + len(new_rows) > MAX_ROWS:
                    raise _invalid(
                        ErrorCode.APP_LIMIT_EXCEEDED,
                        f"table has reached the {MAX_ROWS}-row limit",
                        limit=MAX_ROWS,
                    )
                table_view = self._app_repo.get_table(current.app_id, key)
                current_bytes = (
                    0
                    if mode == IMPORT_MODE_REPLACE
                    else (table_view.data_bytes if table_view is not None else 0)
                )
                added_bytes = sum(
                    len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in new_rows
                )
                if current_bytes + added_bytes > MAX_CSV_BYTES:
                    raise _invalid(
                        ErrorCode.APP_LIMIT_EXCEEDED,
                        f"table data would exceed {MAX_CSV_BYTES // (1024 * 1024)} MB",
                        limit=MAX_CSV_BYTES,
                    )
                if dry_run:
                    return ImportResult(
                        imported=0,
                        skipped=len(clean) - len(imported_rows),
                        errors=errors,
                        warnings=warnings,
                        would_import=len(imported_rows),
                        dry_run=True,
                        pii_columns=pii_columns,
                        blocked_pii=blocked_pii,
                        failed_rows=failed_rows,
                    )
                if pii_changed:
                    await self._persist_schema(ws, current.app_id, key, schema)
                updated_count = 0
                duplicate_count = 0
                if mode == IMPORT_MODE_REPLACE:
                    self._app_repo.replace_data_rows(
                        current.app_id,
                        key,
                        imported_rows,
                        source=source,
                        created_by=owner_id,
                        row_owner_user_id=owner_override,
                        history_actor=history_actor,
                    )
                elif upsert_key:
                    for index, item in enumerate(imported_rows):
                        key_values = [item.get(name, "") for name in upsert_key]
                        if any(value == "" for value in key_values):
                            client_request_id = (
                                f"{client_request_id_prefix}:{index}"
                                if client_request_id_prefix
                                else None
                            )
                            if (
                                client_request_id is not None
                                and self._app_repo.find_data_row_by_client_request_id(
                                    current.app_id, client_request_id
                                )
                                is not None
                            ):
                                duplicate_count += 1
                                continue
                            self._app_repo.append_data_row(
                                current.app_id,
                                key,
                                item,
                                source=source,
                                client_request_id=client_request_id,
                                created_by=owner_id,
                                row_owner_user_id=owner_override,
                            )
                            continue
                        source_key = "|".join(key_values)
                        existing_row = self._app_repo.find_data_row_by_source(
                            current.app_id,
                            key,
                            source,
                            source_key,
                        )
                        if existing_row is None:
                            self._app_repo.append_data_row(
                                current.app_id,
                                key,
                                item,
                                source=source,
                                source_id=source,
                                source_key=source_key,
                                created_by=owner_id,
                                row_owner_user_id=owner_override,
                            )
                        else:
                            self._check_transition(current, key, existing_row.values, item)
                            self._app_repo.update_data_row(
                                current.app_id,
                                existing_row.row_id,
                                item,
                                history_actor=history_actor,
                                history_source=source,
                            )
                            updated_count += 1
                elif client_request_id_prefix:
                    # Event imports carry a stable prefix so a retried delivery
                    # does not append the same records twice.
                    for index, item in enumerate(imported_rows):
                        client_request_id = f"{client_request_id_prefix}:{index}"
                        if (
                            self._app_repo.find_data_row_by_client_request_id(
                                current.app_id, client_request_id
                            )
                            is not None
                        ):
                            duplicate_count += 1
                            continue
                        self._app_repo.append_data_row(
                            current.app_id,
                            key,
                            item,
                            source=source,
                            client_request_id=client_request_id,
                            created_by=owner_id,
                            row_owner_user_id=owner_override,
                        )
                else:
                    self._app_repo.append_data_rows(
                        current.app_id,
                        key,
                        imported_rows,
                        created_by=owner_id,
                        row_owner_user_id=owner_override,
                    )
                await self._after_write(ws, current.app_id)
                return ImportResult(
                    imported=len(imported_rows) - updated_count - duplicate_count,
                    skipped=len(clean) - len(imported_rows),
                    errors=errors,
                    warnings=warnings,
                    updated=updated_count,
                    pii_columns=pii_columns,
                    blocked_pii=blocked_pii,
                    failed_rows=failed_rows,
                )
            updated_count = 0
            if mode == IMPORT_MODE_REPLACE:
                rows = list(imported_rows)
            else:
                rows = await self._read_rows_file(ws, current.app_id, schema, key)
                if upsert_key:
                    index_by_key = {
                        tuple(row.get(name, "") for name in upsert_key): position
                        for position, row in enumerate(rows)
                    }
                    for item in imported_rows:
                        item_key = tuple(item.get(name, "") for name in upsert_key)
                        if item_key in index_by_key:
                            rows[index_by_key[item_key]] = item
                            updated_count += 1
                        else:
                            index_by_key[item_key] = len(rows)
                            rows.append(item)
                else:
                    rows = rows + imported_rows
            if len(rows) > MAX_ROWS:
                raise _invalid(
                    ErrorCode.APP_LIMIT_EXCEEDED,
                    f"table has reached the {MAX_ROWS}-row limit",
                    limit=MAX_ROWS,
                )
            if dry_run:
                # Everything above (limits, validation, the replace guard) is
                # the real work; only the write is skipped.
                return ImportResult(
                    imported=0,
                    skipped=len(clean) - len(imported_rows),
                    errors=errors,
                    warnings=warnings,
                    would_import=len(imported_rows),
                    dry_run=True,
                    pii_columns=pii_columns,
                    blocked_pii=blocked_pii,
                    failed_rows=failed_rows,
                )
            if pii_changed:
                await self._persist_schema(ws, current.app_id, key, schema)
            await self._persist_rows(ws, current, key, schema, rows)
            return ImportResult(
                imported=len(imported_rows) - updated_count,
                skipped=len(clean) - len(imported_rows),
                errors=errors,
                warnings=warnings,
                updated=updated_count,
                pii_columns=pii_columns,
                blocked_pii=blocked_pii,
                failed_rows=failed_rows,
            )

    async def find_row_by_values(
        self,
        row: AppRecord,
        values: Mapping[str, object],
        *,
        table: str = MAIN_TABLE_KEY,
    ) -> LightAppDataRow | None:
        """First row whose stored values match every supplied column.

        Used by external writers (games, callbacks) that identify a row by a
        business key rather than a row id. Legacy CSV storage is migrated on
        first access, so this always reads the database when migration works.
        """
        key = self._require_table(row, table)
        if not values:
            return None
        if not await self._ensure_db_rows(row):
            return None
        current = self._app_repo.get(row.app_id) or row
        wanted = {str(name): str(value) for name, value in values.items()}
        for item in self._app_repo.list_all_data_rows(current.app_id, key):
            if all(str(item.values.get(name, "")) == value for name, value in wanted.items()):
                return item
        return None

    async def update_row(
        self,
        row: AppRecord,
        row_id: str,
        values: Mapping[str, object],
        *,
        table: str = MAIN_TABLE_KEY,
        expected_version: int | None = None,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
        actor_user_id: int | None = None,
        source: str = "manual",
    ) -> dict[str, Any]:
        """Update one row with an optional version check (CAS)."""
        key = self._require_table(row, table)
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            if current.storage_version < 2:
                raise _invalid(
                    ErrorCode.APP_ROW_NOT_FOUND,
                    f"row not found: {row_id}",
                )
            ws = await self._workspace(current.agent_id)
            existing_row = self._app_repo.get_data_row(current.app_id, row_id)
            if existing_row is None:
                raise _invalid(ErrorCode.APP_ROW_NOT_FOUND, f"row not found: {row_id}")
            # A row id identifies the row inside the whole app, so validate it
            # against the table it actually lives in — otherwise a multi-table
            # app would check the values against another table's schema.
            key = existing_row.table_key or key
            schema = await self._read_schema(ws, current.app_id, key)
            owner_filter = self._row_owner_filter(current, key, viewer_user_id, include_all_rows)
            if owner_filter is not None and app_access.row_owner_id(existing_row) != owner_filter:
                raise _invalid(ErrorCode.APP_ROW_NOT_FOUND, f"row not found: {row_id}")
            normalized = validate_row(schema, values)
            await self._validate_references(current, schema, normalized)
            _reject_unmarked_pii(schema, normalized, table=key)
            self._check_transition(current, key, existing_row.values, normalized)
            table_view = self._app_repo.get_table(current.app_id, key)
            current_bytes = table_view.data_bytes if table_view is not None else 0
            old_bytes = len(json.dumps(existing_row.values, ensure_ascii=False).encode("utf-8"))
            new_bytes = len(json.dumps(normalized, ensure_ascii=False).encode("utf-8"))
            if current_bytes - old_bytes + new_bytes > MAX_CSV_BYTES:
                raise _invalid(
                    ErrorCode.APP_LIMIT_EXCEEDED,
                    f"table data would exceed {MAX_CSV_BYTES // (1024 * 1024)} MB",
                    limit=MAX_CSV_BYTES,
                )
            updated = self._app_repo.update_data_row(
                current.app_id,
                row_id,
                normalized,
                expected_version=expected_version,
                history_actor=_history_actor(actor_user_id),
                history_source=source,
            )
            if updated is None:
                raise OctopError(
                    ErrorCode.APP_CONFLICT,
                    "row changed since it was read; refresh and retry",
                )
            self._emit_row_event(current, key, "on_update", updated.row_id, updated.values)
            await self._after_write(ws, current.app_id)
            return {
                "row_id": updated.row_id,
                "values": updated.values,
                "version": updated.version,
            }

    async def delete_row(
        self,
        row: AppRecord,
        row_id: str,
        *,
        expected_version: int | None = None,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
        actor_user_id: int | None = None,
        source: str = "manual",
    ) -> str:
        """Delete one row with an optional version check (CAS)."""
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            if current.storage_version < 2:
                raise _invalid(ErrorCode.APP_ROW_NOT_FOUND, f"row not found: {row_id}")
            existing_row = self._app_repo.get_data_row(current.app_id, row_id)
            if existing_row is None:
                raise _invalid(ErrorCode.APP_ROW_NOT_FOUND, f"row not found: {row_id}")
            owner_filter = self._row_owner_filter(
                current, existing_row.table_key, viewer_user_id, include_all_rows
            )
            if owner_filter is not None and app_access.row_owner_id(existing_row) != owner_filter:
                raise _invalid(ErrorCode.APP_ROW_NOT_FOUND, f"row not found: {row_id}")
            inbound = await self._inbound_references(current, existing_row.table_key, {row_id})
            if inbound:
                table_key, column_name, _value = inbound[0]
                raise OctopError(
                    ErrorCode.APP_ROW_IN_USE,
                    f"row is referenced by {table_key}.{column_name}; remove those references first",
                    details={"table": table_key, "column": column_name},
                )
            if not self._app_repo.delete_data_row(
                current.app_id,
                row_id,
                expected_version=expected_version,
                history_actor=_history_actor(actor_user_id),
                history_source=source,
            ):
                raise OctopError(
                    ErrorCode.APP_CONFLICT,
                    "row changed since it was read; refresh and retry",
                )
            ws = await self._workspace(current.agent_id)
            await self._after_write(ws, current.app_id)
            return row_id

    async def export_rows(
        self,
        row: AppRecord,
        *,
        table: str = MAIN_TABLE_KEY,
        redact_pii: bool = False,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> tuple[str, str]:
        """Render one table as CSV (a snapshot, not the live storage)."""
        key = self._require_table(row, table)
        await self._ensure_db_rows(row)
        current = self._app_repo.get(row.app_id) or row
        ws = await self._workspace(current.agent_id)
        schema = await self._read_schema(ws, current.app_id, key)
        owner_filter = self._row_owner_filter(current, key, viewer_user_id, include_all_rows)
        if current.storage_version >= 2:
            data_rows = self._app_repo.list_all_data_rows(current.app_id, key)
            if owner_filter is not None:
                data_rows = [
                    item for item in data_rows if app_access.row_owner_id(item) == owner_filter
                ]
            rows = _normalize_rows(schema, [item.values for item in data_rows])
        else:
            if owner_filter is not None:
                raise OctopError(
                    ErrorCode.APP_FORBIDDEN,
                    "row-scoped table requires DB storage; migrate the app first",
                )
            rows = await self._read_rows_file(ws, current.app_id, schema, key)
        schema, rows = _apply_pii(schema, rows, redact_pii=redact_pii)
        return f"{key}.csv", render_csv(schema, rows)

    async def aggregate_rows(
        self,
        row: AppRecord,
        *,
        table: str = MAIN_TABLE_KEY,
        group_by: str | None = None,
        metric: str | None = None,
        agg: str = "count",
        filters: Mapping[str, object] | None = None,
        limit: int = app_aggregate.DEFAULT_AGGREGATE_LIMIT,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> dict[str, Any]:
        """Aggregate one table server-side without returning every row."""
        key = self._require_table(row, table)
        await self._ensure_db_rows(row)
        current = self._app_repo.get(row.app_id) or row
        ws = await self._workspace(current.agent_id)
        schema = await self._read_schema(ws, current.app_id, key)
        clean_filters = {
            str(column).strip(): str(value) for column, value in (filters or {}).items()
        }
        for column in clean_filters:
            found = schema.column(column)
            if found is None:
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    f"unknown filter column: {column!r}",
                    table=key,
                )
            if found.pii:
                raise OctopError(
                    ErrorCode.APP_FORBIDDEN,
                    f"cannot filter on a sensitive column: {column!r}",
                )
        owner_filter = self._row_owner_filter(current, key, viewer_user_id, include_all_rows)
        if current.storage_version >= 2:
            data_rows = self._app_repo.list_all_data_rows(current.app_id, key)
            if owner_filter is not None:
                data_rows = [
                    item for item in data_rows if app_access.row_owner_id(item) == owner_filter
                ]
            rows = _normalize_rows(schema, [item.values for item in data_rows])
        else:
            if owner_filter is not None:
                raise OctopError(
                    ErrorCode.APP_FORBIDDEN,
                    "row-scoped table requires DB storage; migrate the app first",
                )
            rows = await self._read_rows_file(ws, current.app_id, schema, key)
        if clean_filters:
            wanted = {column: value.strip().lower() for column, value in clean_filters.items()}
            rows = [
                item
                for item in rows
                if all(
                    item.get(column, "").strip().lower() == value
                    for column, value in wanted.items()
                )
            ]
        result = app_aggregate.aggregate_rows(
            schema,
            rows,
            group_by=group_by,
            metric=metric,
            agg=agg,
            limit=limit,
        )
        result["table"] = key
        return result

    async def row_history(
        self,
        row: AppRecord,
        row_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        redact_pii: bool = False,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> list[dict[str, Any]]:
        """Append-only change history for one row, newest first.

        A viewer on a ``user``-scoped table only sees history while the row
        still exists and belongs to them. After a delete, the owner/admin (who
        can see every row) and the user who made a change can read the trail;
        another collaborator cannot.
        """
        current = self._app_repo.get(row.app_id) or row
        if current.storage_version < 2:
            return []
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        entries = self._app_repo.list_row_history(
            current.app_id,
            row_id,
            limit=safe_limit,
            offset=safe_offset,
        )
        if not entries:
            return []
        table_key = entries[0].table_key
        table_view = self._app_repo.get_table(current.app_id, table_key)
        if table_view is None:
            # The table was deleted; only an owner/admin may still read its
            # history, because row-scope ownership can no longer be checked.
            if not include_all_rows:
                return []
        elif not include_all_rows and table_view.row_scope == "user":
            live = self._app_repo.get_data_row(current.app_id, row_id)
            if live is not None:
                if app_access.row_owner_id(live) != viewer_user_id:
                    return []
            elif viewer_user_id is None or not any(
                entry.actor == f"user:{viewer_user_id}" for entry in entries
            ):
                # The row is gone, so ownership cannot be checked; let the
                # user who changed it read their own trail.
                return []
        schema: Schema | None = None
        try:
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, table_key)
        except OctopError:
            # The table or workspace is gone; history is still readable but
            # needs the value-level PII fallback below.
            schema = None
        result: list[dict[str, Any]] = []
        for entry in entries:
            before = entry.before
            after = entry.after
            if redact_pii:
                if schema is not None:
                    if before is not None:
                        before = redact_rows(schema, [before], redact_text=True)[0]
                    if after is not None:
                        after = redact_rows(schema, [after], redact_text=True)[0]
                else:
                    if before is not None:
                        before = {key: redact_pii_text(value) for key, value in before.items()}
                    if after is not None:
                        after = {key: redact_pii_text(value) for key, value in after.items()}
            result.append(
                {
                    "history_id": entry.history_id,
                    "table": entry.table_key,
                    "row_id": entry.row_id,
                    "version": entry.version,
                    "action": entry.action,
                    "actor": entry.actor,
                    "source": entry.source,
                    "changed_at": entry.changed_at,
                    "before": before,
                    "after": after,
                }
            )
        return result

    def trim_row_history(self, keep_days: int = ROW_HISTORY_RETENTION_DAYS) -> int:
        """Drop row-history entries older than *keep_days*."""
        if keep_days <= 0:
            return 0
        cutoff = int(time.time()) - keep_days * 86400
        return self._app_repo.delete_row_history_before(cutoff)

    # ----- backup / restore -----

    @staticmethod
    def _backup_params(value: object) -> object:
        """Best-effort redaction of credentials inside data-source params."""
        hints = ("secret", "password", "token", "api_key", "apikey", "authorization")
        if isinstance(value, Mapping):
            return {
                str(key): AppService._backup_params(item)
                for key, item in value.items()
                if not any(hint in str(key).lower() for hint in hints)
            }
        if isinstance(value, list):
            return [AppService._backup_params(item) for item in value]
        return value

    def _backup_integrations(self, row: AppRecord) -> dict[str, Any]:
        """Integration declarations that are safe to archive (never secrets)."""
        integrations: dict[str, Any] = {}
        if self._webhook_repo is not None:
            integrations["webhooks"] = [
                {
                    "name": item.name,
                    "url": item.url,
                    "event_types": list(item.event_types),
                    "enabled": item.enabled,
                }
                for item in self._webhook_repo.list_for_app(row.app_id)
            ]
        if self._ingress_repo is not None and self._external_subscription_repo is not None:
            ingress: list[dict[str, Any]] = []
            for item in self._ingress_repo.list_for_app(row.app_id):
                subscription = self._external_subscription_repo.get(item.subscription_id)
                if subscription is None:
                    continue
                try:
                    mapping = json.loads(subscription.mapping_json or "{}")
                except ValueError:
                    mapping = {}
                try:
                    filter_rules = json.loads(subscription.filter_json or "{}")
                except ValueError:
                    filter_rules = {}
                ingress.append(
                    {
                        "name": item.name,
                        "mode": item.mode,
                        "event_type": str(filter_rules.get("event_type") or ""),
                        "mapping": mapping if isinstance(mapping, dict) else {},
                        "max_per_minute": subscription.max_per_minute,
                        "enabled": item.enabled,
                    }
                )
            integrations["ingress"] = ingress
        integrations["services"] = [
            {
                "name": item.name,
                "provider": item.provider,
                "connector_instance_id": item.connector_instance_id,
                "allowed_tools": list(item.allowed_tools),
                "allowed_url_prefixes": list(item.allowed_url_prefixes),
                "request_headers": dict(item.request_headers),
                "cache_ttl_seconds": item.cache_ttl_seconds,
                "max_calls_per_minute": item.max_calls_per_minute,
                "public_live": item.public_live,
                "enabled": item.enabled,
            }
            for item in self.list_services(row)
        ]
        integrations["sources"] = [
            {
                "source_id": item.source_id,
                "table_key": item.table_key,
                "kind": item.kind,
                "mode": item.mode,
                "schedule_seconds": item.schedule_seconds,
                "params": self._backup_params(item.params),
                "mapping": item.mapping,
            }
            for item in self.list_sources(row)
        ]
        if self._schedule_repo is not None:
            integrations["schedules"] = [
                {
                    "action": item.action,
                    "target": item.target,
                    "interval_seconds": item.interval_seconds,
                    "enabled": item.enabled,
                }
                for item in self._schedule_repo.list_for_app(row.app_id)
            ]
        return integrations

    async def backup_archive(self, row: AppRecord) -> tuple[str, bytes]:
        """Bundle the app shell, tables and assets into one zip.

        The archive is a portable snapshot: the control-plane ids are not part
        of it, so restoring it creates a new app.
        """
        current = self._app_repo.get(row.app_id) or row
        entries: list[tuple[str, bytes]] = []
        tables: list[dict[str, Any]] = []
        for table_view in self.list_tables(current):
            schema = await self.read_schema(current, table_view.table_key)
            tables.append(
                {
                    "table_key": table_view.table_key,
                    "label": table_view.label,
                    "is_main": table_view.is_main,
                    "public_visible": table_view.public_visible,
                    "row_scope": table_view.row_scope,
                    "rules": table_view.rules,
                }
            )
            entries.append(
                (
                    f"{TABLES_DIR}/{table_view.table_key}/{FILE_SCHEMA}",
                    schema.to_json().encode("utf-8"),
                )
            )
            _filename, csv_text = await self.export_rows(
                current,
                table=table_view.table_key,
                include_all_rows=True,
            )
            entries.append(
                (
                    f"{TABLES_DIR}/{table_view.table_key}/{FILE_DATA}",
                    csv_text.encode("utf-8"),
                )
            )
            if current.storage_version >= 2:
                row_ids = [
                    item.row_id
                    for item in self._app_repo.list_all_data_rows(
                        current.app_id, table_view.table_key
                    )
                ]
                entries.append(
                    (
                        f"{TABLES_DIR}/{table_view.table_key}/{FILE_ROW_IDS}",
                        json.dumps(row_ids, ensure_ascii=False).encode("utf-8"),
                    )
                )
        html = await self.read_html(current)
        if html:
            entries.append((FILE_HTML, html.encode("utf-8")))
        preview = await self.read_preview(current)
        if preview:
            entries.append((FILE_PREVIEW, preview.encode("utf-8")))
        for asset_name in await self.list_assets(current):
            _mime, asset_data = await self.read_asset(current, asset_name)
            entries.append((f"{ASSETS_DIR}/{asset_name}", asset_data))
        manifest = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "created_at": int(time.time()),
            "app": {"name": current.name, "description": current.description},
            "capabilities": self.capability_list(current),
            "tables": tables,
            "integrations": self._backup_integrations(current),
        }
        entries.insert(
            0,
            (
                FILE_MANIFEST,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            ),
        )
        archive = await asyncio.to_thread(_zip_entries, entries)
        return f"miniapp-{current.app_id}.zip", archive

    async def _restore_table_rows(
        self,
        row: AppRecord,
        key: str,
        records: list[dict[str, Any]],
        *,
        row_ids: list[str] | None,
    ) -> dict[str, str]:
        """Insert backup rows and return old-row-id -> new-row-id.

        Row ids are globally unique, so restoring into the same database
        cannot keep them. The mapping is used to rewrite reference columns
        afterwards, which keeps the restored graph intact.
        """
        if len(records) > MAX_ROWS:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"backup table {key!r} exceeds the {MAX_ROWS}-row limit",
            )
        if row_ids is not None and len(set(row_ids)) != len(row_ids):
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"backup has duplicate row ids for table {key!r}",
            )
        ws = await self._workspace(row.agent_id)
        schema = await self._read_schema(ws, row.app_id, key)
        normalized: list[dict[str, str]] = []
        for index, record in enumerate(records):
            try:
                normalized.append(validate_row(schema, record))
            except OctopError as exc:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    f"backup row {index + 1} of table {key!r} does not fit the schema: {exc.message}",
                ) from exc
        total_bytes = sum(
            len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in normalized
        )
        if total_bytes > MAX_CSV_BYTES:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"backup table {key!r} exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MB limit",
            )
        new_ids = [f"row_{new_short_id(12)}" for _ in normalized]
        self._app_repo.append_data_rows(
            row.app_id,
            key,
            normalized,
            source="restore",
            created_by=row.owner_user_id,
            row_ids=new_ids,
        )
        await self._after_write(ws, row.app_id)
        if row_ids is None:
            return {}
        return dict(zip(row_ids, new_ids, strict=True))

    async def _remap_restored_references(
        self, row: AppRecord, id_maps: Mapping[str, Mapping[str, str]]
    ) -> None:
        ws = await self._workspace(row.agent_id)
        for table_view in self.list_tables(row):
            schema = await self._read_schema(ws, row.app_id, table_view.table_key)
            ref_columns = self._reference_columns(schema)
            if not ref_columns:
                continue
            rewritten: list[tuple[str, dict[str, str]]] = []
            for item in self._app_repo.list_all_data_rows(row.app_id, table_view.table_key):
                values = dict(item.values)
                changed = False
                for column in ref_columns:
                    old = str(values.get(column.name) or "").strip()
                    new = id_maps.get(column.ref_table or "", {}).get(old)
                    if new and new != old:
                        values[column.name] = new
                        changed = True
                if changed:
                    rewritten.append((item.row_id, values))
            if rewritten:
                self._app_repo.rewrite_data_rows(row.app_id, table_view.table_key, rewritten)

    async def _validate_restored_references(self, row: AppRecord) -> None:
        ws = await self._workspace(row.agent_id)
        cache: dict[str, set[str]] = {}
        for table_view in self.list_tables(row):
            schema = await self._read_schema(ws, row.app_id, table_view.table_key)
            for item in self._app_repo.list_all_data_rows(row.app_id, table_view.table_key):
                try:
                    await self._validate_references(row, schema, item.values, cache=cache)
                except OctopError as exc:
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        f"backup has a broken reference: {exc.message}",
                    ) from exc

    async def _apply_restored_table_config(
        self, row: AppRecord, key: str, meta: Mapping[str, Any]
    ) -> None:
        """Restore table visibility, row scope and declarative rules."""
        scope = str(meta.get("row_scope") or "app").strip().lower()
        if scope not in {"app", "user"}:
            scope = "app"
        await self.set_table_row_scope(row, table=key, row_scope=scope)
        await self.set_table_visibility(
            row,
            table=key,
            public_visible=bool(meta.get("public_visible", key == MAIN_TABLE_KEY)),
        )
        rules = meta.get("rules")
        if isinstance(rules, Mapping) and rules:
            await self.set_table_rules(row, table=key, rules=rules)

    def _restore_sources(self, row: AppRecord, integrations: Mapping[str, Any]) -> dict[str, str]:
        """Recreate app data sources with fresh ids; returns old->new source ids."""
        raw = integrations.get("sources")
        if not isinstance(raw, list):
            return {}
        source_map: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            old_id = str(item.get("source_id") or "")
            if not old_id:
                continue
            new_id = new_short_id(16)
            params = item.get("params")
            clean_params = dict(params) if isinstance(params, Mapping) else {}
            # A restore starts a fresh cursor; replaying the old one can skip data.
            clean_params.pop("cursor", None)
            mapping = item.get("mapping")
            try:
                self.save_source(
                    row,
                    source_id=new_id,
                    table_key=str(item.get("table_key") or MAIN_TABLE_KEY),
                    kind=str(item.get("kind") or ""),
                    mode=str(item.get("mode") or ""),
                    # Recreated disabled; the schedule row carries the interval.
                    schedule_seconds=0,
                    params=clean_params,
                    mapping=mapping if isinstance(mapping, Mapping) else {},
                    created_by=row.owner_user_id,
                )
            except Exception:  # noqa: BLE001 - one bad source must not abort restore
                logger.warning("restore skipped a data source for %s", row.app_id, exc_info=True)
                continue
            source_map[old_id] = new_id
        return source_map

    def _restore_schedules(
        self, row: AppRecord, integrations: Mapping[str, Any], source_map: Mapping[str, str]
    ) -> None:
        raw = integrations.get("schedules")
        restored = 0
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("action") or "") != SCHEDULE_ACTION_SYNC_SOURCE:
                    # Snapshot schedules need a restored publication, which is
                    # intentionally not part of the archive.
                    continue
                target = item.get("target")
                old_source = (
                    str(target.get("source_id") or "") if isinstance(target, Mapping) else ""
                )
                new_source = source_map.get(old_source)
                if not new_source:
                    continue
                try:
                    self.save_schedule(
                        row,
                        action=SCHEDULE_ACTION_SYNC_SOURCE,
                        interval_seconds=int(item.get("interval_seconds") or 0),
                        created_by=row.owner_user_id,
                        enabled=False,
                        target={"source_id": new_source},
                    )
                    restored += 1
                except Exception:  # noqa: BLE001 - keep restoring the rest
                    logger.warning("restore skipped a schedule for %s", row.app_id, exc_info=True)
        if restored:
            return
        # Compatibility: v3 backups from before schedules were recorded still
        # carry schedule_seconds on the source declaration.
        raw_sources = integrations.get("sources")
        if not isinstance(raw_sources, list):
            return
        for item in raw_sources:
            if not isinstance(item, Mapping):
                continue
            old_id = str(item.get("source_id") or "")
            new_id = source_map.get(old_id)
            interval = int(item.get("schedule_seconds") or 0)
            if not new_id or interval <= 0:
                continue
            try:
                self.save_schedule(
                    row,
                    action=SCHEDULE_ACTION_SYNC_SOURCE,
                    interval_seconds=interval,
                    created_by=row.owner_user_id,
                    enabled=False,
                    target={"source_id": new_id},
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "restore skipped a fallback schedule for %s",
                    row.app_id,
                    exc_info=True,
                )

    async def _restore_integrations(self, row: AppRecord, manifest: Mapping[str, Any]) -> None:
        """Recreate integration declarations disabled; secrets are never archived."""
        raw = manifest.get("integrations")
        if not isinstance(raw, Mapping):
            return
        source_map = self._restore_sources(row, raw)
        self._restore_schedules(row, raw, source_map)
        webhooks = raw.get("webhooks")
        if (
            isinstance(webhooks, list)
            and self._webhook_repo is not None
            and self._external_source_repo is not None
            and self._external_subscription_repo is not None
            and self._secret_repo is not None
        ):
            from octop.infra.apps.webhooks import AppWebhookService  # noqa: PLC0415

            service = AppWebhookService(
                app_service=self,
                webhook_repo=self._webhook_repo,
                source_repo=self._external_source_repo,
                subscription_repo=self._external_subscription_repo,
                secret_repo=self._secret_repo,
            )
            for item in webhooks:
                if not isinstance(item, Mapping):
                    continue
                try:
                    await service.create(
                        row,
                        name=str(item.get("name") or "Restored webhook"),
                        url=str(item.get("url") or ""),
                        event_types=[str(value) for value in item.get("event_types") or []],
                        enabled=False,
                        actor_user_id=row.owner_user_id,
                    )
                except Exception:  # noqa: BLE001 - one bad declaration must not abort restore
                    logger.warning(
                        "restore skipped an outbound webhook for %s", row.app_id, exc_info=True
                    )

        ingress = raw.get("ingress")
        if (
            isinstance(ingress, list)
            and self._ingress_repo is not None
            and self._external_source_repo is not None
            and self._external_subscription_repo is not None
            and self._secret_repo is not None
        ):
            from octop.infra.apps.ingress import AppIngressService  # noqa: PLC0415

            ingress_service = AppIngressService(
                app_service=self,
                app_repo=self._app_repo,
                ingress_repo=self._ingress_repo,
                source_repo=self._external_source_repo,
                subscription_repo=self._external_subscription_repo,
                secret_repo=self._secret_repo,
            )
            for item in ingress:
                if not isinstance(item, Mapping):
                    continue
                raw_mapping = item.get("mapping")
                mapping = dict(raw_mapping) if isinstance(raw_mapping, Mapping) else {}
                for key in ("source_id", "app_source_id"):
                    old_source = str(mapping.get(key) or "")
                    if old_source in source_map:
                        mapping[key] = source_map[old_source]
                try:
                    await ingress_service.create(
                        row,
                        name=str(item.get("name") or "Restored ingress"),
                        mode=str(item.get("mode") or "app.inbox"),
                        event_type=str(item.get("event_type") or ""),
                        mapping=mapping,
                        max_per_minute=int(item.get("max_per_minute") or 0),
                        enabled=False,
                        actor_user_id=row.owner_user_id,
                    )
                except Exception:  # noqa: BLE001 - one bad declaration must not abort restore
                    logger.warning(
                        "restore skipped an inbound ingress for %s", row.app_id, exc_info=True
                    )

        services = raw.get("services")
        if isinstance(services, list):
            for item in services:
                if not isinstance(item, Mapping):
                    continue
                try:
                    self.save_service(
                        row,
                        name=str(item.get("name") or ""),
                        provider=str(item.get("provider") or "external"),
                        connector_instance_id=str(item.get("connector_instance_id") or ""),
                        allowed_tools=[str(value) for value in item.get("allowed_tools") or []],
                        allowed_url_prefixes=[
                            str(value) for value in item.get("allowed_url_prefixes") or []
                        ],
                        request_headers=(
                            item.get("request_headers")
                            if isinstance(item.get("request_headers"), Mapping)
                            else None
                        ),
                        cache_ttl_seconds=int(item.get("cache_ttl_seconds") or 300),
                        max_calls_per_minute=int(item.get("max_calls_per_minute") or 30),
                        public_live=bool(item.get("public_live", False)),
                        enabled=False,
                        actor_user_id=row.owner_user_id,
                        is_admin=self.is_admin(row.owner_user_id),
                    )
                except Exception:  # noqa: BLE001 - e.g. connector missing on this host
                    logger.warning(
                        "restore skipped an external service for %s", row.app_id, exc_info=True
                    )

    async def preflight_restore(
        self,
        *,
        agent_id: str,
        owner_user_id: int,
        data: bytes,
    ) -> dict[str, Any]:
        """Scan a backup's dependencies before creating any rows.

        Structural archive errors block restore; missing connectors/components
        and declarations that will be skipped are returned as warnings so the
        caller can show them and let the owner decide.
        """
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        def issue(
            bucket: list[dict[str, str]],
            *,
            severity: str,
            code: str,
            component: str,
            message: str,
            action: str = "",
            name: str = "",
        ) -> None:
            bucket.append(
                {
                    "severity": severity,
                    "code": code,
                    "component": component,
                    "name": name,
                    "message": message,
                    "action": action,
                }
            )

        agent = self._agent_repo.get(agent_id)
        if agent is None:
            issue(
                errors,
                severity="error",
                code="agent_missing",
                component="workspace",
                name=agent_id,
                message="target agent does not exist",
                action="choose an existing agent before restoring",
            )
        else:
            try:
                workspace = await asyncio.to_thread(self._workspaces.workspace_for_agent, agent_id)
            except Exception as exc:  # noqa: BLE001 - dependency probe failure
                issue(
                    errors,
                    severity="error",
                    code="workspace_unavailable",
                    component="workspace",
                    name=agent_id,
                    message=f"target agent workspace could not be opened: {exc}",
                    action="check the agent's storage backend, then retry",
                )
            else:
                if workspace is None:
                    issue(
                        errors,
                        severity="error",
                        code="workspace_unavailable",
                        component="workspace",
                        name=agent_id,
                        message="target agent workspace is not available on this host",
                        action="start the agent or check its storage backend, then retry",
                    )
        if len(data) > MAX_BACKUP_BYTES:
            issue(
                errors,
                severity="error",
                code="archive_too_large",
                component="archive",
                message=f"backup exceeds the {MAX_BACKUP_BYTES // (1024 * 1024)} MB limit",
            )
            return _preflight_result(errors, warnings, {})
        try:
            manifest, tables, _html, _preview, _assets = await asyncio.to_thread(
                _read_backup_zip, data
            )
        except (zipfile.BadZipFile, ValueError, OctopError) as exc:
            issue(
                errors,
                severity="error",
                code="archive_invalid",
                component="archive",
                message=f"invalid app backup: {exc}",
            )
            return _preflight_result(errors, warnings, {})
        if MAIN_TABLE_KEY not in tables:
            issue(
                errors,
                severity="error",
                code="missing_main_table",
                component="archive",
                message="backup has no main table",
            )
            return _preflight_result(errors, warnings, {})
        schemas: dict[str, Schema] = {}
        for key, (_label, schema_bytes, _csv, _row_ids) in tables.items():
            try:
                payload = json.loads(schema_bytes.decode("utf-8"))
                schemas[key] = parse_schema_payload(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, OctopError) as exc:
                issue(
                    errors,
                    severity="error",
                    code="table_schema_invalid",
                    component="table",
                    name=key,
                    message=f"table schema is invalid: {exc}",
                )
        if MAIN_TABLE_KEY not in schemas:
            return _preflight_result(errors, warnings, {})
        if len(schemas) > MAX_TABLES_PER_APP:
            issue(
                errors,
                severity="error",
                code="tables_limit",
                component="archive",
                message=f"backup has more than {MAX_TABLES_PER_APP} tables",
            )
        if errors:
            return _preflight_result(errors, warnings, {})

        # Validate data and row-id pairing up front: restore cleans up an app
        # on late failures, but a preflight should keep it from being created.
        table_rows: dict[str, list[dict[str, str]]] = {}
        table_ids: dict[str, list[str]] = {}
        for key, (_label, _schema_bytes, csv_bytes, row_ids_bytes) in tables.items():
            try:
                records = parse_import_file(f"{key}.csv", csv_bytes) if csv_bytes.strip() else []
            except (OctopError, ValueError) as exc:
                issue(
                    errors,
                    severity="error",
                    code="table_rows_invalid",
                    component="table",
                    name=key,
                    message=f"table data cannot be read: {exc}",
                )
                continue
            if len(records) > MAX_ROWS:
                issue(
                    errors,
                    severity="error",
                    code="table_rows_limit",
                    component="table",
                    name=key,
                    message=f"table exceeds the {MAX_ROWS}-row restore limit",
                )
                continue
            ids: list[str] = []
            if row_ids_bytes:
                try:
                    parsed_ids = json.loads(row_ids_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    issue(
                        errors,
                        severity="error",
                        code="row_ids_invalid",
                        component="table",
                        name=key,
                        message="row_ids.json is not valid JSON",
                    )
                    continue
                if not isinstance(parsed_ids, list) or not all(
                    isinstance(item, str) and item for item in parsed_ids
                ):
                    issue(
                        errors,
                        severity="error",
                        code="row_ids_invalid",
                        component="table",
                        name=key,
                        message="row_ids.json must be a list of non-empty strings",
                    )
                    continue
                ids = list(parsed_ids)
                if len(ids) != len(records):
                    issue(
                        errors,
                        severity="error",
                        code="row_ids_mismatch",
                        component="table",
                        name=key,
                        message="row ids do not match the number of data rows",
                    )
                    continue
                if len(set(ids)) != len(ids):
                    issue(
                        errors,
                        severity="error",
                        code="row_ids_duplicate",
                        component="table",
                        name=key,
                        message="row ids are not unique",
                    )
                    continue
            normalized: list[dict[str, str]] = []
            for index, record in enumerate(records):
                try:
                    normalized.append(validate_row(schemas[key], record))
                except OctopError as exc:
                    issue(
                        errors,
                        severity="error",
                        code="table_row_invalid",
                        component="table",
                        name=key,
                        message=f"row {index + 1} does not fit the schema: {exc.message}",
                    )
                    break
            else:
                total_bytes = sum(
                    len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in normalized
                )
                if total_bytes > MAX_CSV_BYTES:
                    issue(
                        errors,
                        severity="error",
                        code="table_bytes_limit",
                        component="table",
                        name=key,
                        message=(
                            f"table exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MB restore limit"
                        ),
                    )
                    continue
                table_rows[key] = normalized
                table_ids[key] = ids

        for key, schema in schemas.items():
            for column in schema.columns:
                if not column.ref_table:
                    continue
                if column.ref_table not in schemas:
                    issue(
                        errors,
                        severity="error",
                        code="reference_table_missing",
                        component="table",
                        name=key,
                        message=f"column {column.name!r} references missing table {column.ref_table!r}",
                    )
                    continue
                target_rows = table_rows.get(column.ref_table)
                if target_rows is None:
                    continue
                target_ids = table_ids.get(column.ref_table) or []
                if not target_ids and target_rows:
                    issue(
                        warnings,
                        severity="warning",
                        code="references_not_verifiable",
                        component="table",
                        name=key,
                        message=f"column {column.name!r} references a table without row ids",
                        action="check reference values after restore",
                    )
                    continue
                known = set(target_ids)
                for index, record in enumerate(table_rows.get(key, [])):
                    value = str(record.get(column.name) or "").strip()
                    if value and value not in known:
                        issue(
                            errors,
                            severity="error",
                            code="reference_broken",
                            component="table",
                            name=key,
                            message=(
                                f"row {index + 1} column {column.name!r} references "
                                f"missing row {value!r} in {column.ref_table!r}"
                            ),
                        )
                        break

        app_meta = manifest.get("app")
        if isinstance(app_meta, Mapping):
            try:
                validate_name(str(app_meta.get("name") or "").strip() or "Restored app")
                validate_description(str(app_meta.get("description") or ""))
            except OctopError as exc:
                issue(
                    errors,
                    severity="error",
                    code="app_metadata_invalid",
                    component="app",
                    message=exc.message,
                )
        raw_capabilities = manifest.get("capabilities")
        if isinstance(raw_capabilities, list):
            try:
                app_capabilities.validate(raw_capabilities)
            except OctopError as exc:
                issue(
                    errors,
                    severity="error",
                    code="capabilities_invalid",
                    component="app",
                    message=exc.message,
                )
        if _html:
            try:
                validate_html(_html)
            except OctopError as exc:
                issue(
                    errors,
                    severity="error",
                    code="html_invalid",
                    component="app",
                    message=exc.message,
                )
        if _preview:
            try:
                validate_preview_svg(_preview)
            except ValueError as exc:
                issue(
                    errors,
                    severity="error",
                    code="preview_invalid",
                    component="app",
                    message=str(exc),
                )
        for asset_name, asset_data in _assets:
            if (
                not ASSET_NAME_RE.match(asset_name.strip())
                or ".." in asset_name
                or not asset_data
                or len(asset_data) > MAX_ASSET_BYTES
                or _sniff_asset_mime(asset_data) is None
            ):
                issue(
                    errors,
                    severity="error",
                    code="asset_invalid",
                    component="app",
                    name=asset_name,
                    message="asset name, size or image format is not restorable",
                )
        if errors:
            return _preflight_result(errors, warnings, {})

        integrations = manifest.get("integrations")
        if not isinstance(integrations, Mapping):
            integrations = {}
        summary = {
            "tables": len(tables),
            "sources": 0,
            "ingress": 0,
            "webhooks": 0,
            "services": 0,
            "schedules": 0,
        }

        def connector_check(
            instance_id: str,
            *,
            component: str,
            name: str,
            required: bool = False,
        ) -> None:
            if not instance_id:
                return
            if self._connector_repo is None:
                issue(
                    warnings if not required else errors,
                    severity="error" if required else "warning",
                    code="connectors_unavailable",
                    component=component,
                    name=name,
                    message="this host has no connector repository",
                    action="configure connectors on the target host",
                )
                return
            instance = self._connector_repo.get(instance_id)
            if instance is None:
                issue(
                    warnings,
                    severity="warning",
                    code="missing_connector",
                    component=component,
                    name=name,
                    message=f"connector instance {instance_id!r} does not exist on this host",
                    action=(
                        "re-create this service with a target-host connector after restore"
                        if component == "service"
                        else "rebind this data source to a target-host connector after restore"
                    ),
                )
                return
            if not (instance.user_id == owner_user_id or instance.shared):
                issue(
                    warnings,
                    severity="warning",
                    code="connector_not_accessible",
                    component=component,
                    name=name,
                    message=f"connector instance {instance_id!r} is not owned by the restoring user",
                    action=(
                        "re-create this service with an accessible connector after restore"
                        if component == "service"
                        else "rebind this data source to an accessible connector after restore"
                    ),
                )

        raw_sources = integrations.get("sources")
        if isinstance(raw_sources, list):
            summary["sources"] = len(raw_sources)
            for item in raw_sources:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("source_id") or "")
                table_key = str(item.get("table_key") or MAIN_TABLE_KEY)
                if table_key not in schemas:
                    issue(
                        warnings,
                        severity="warning",
                        code="source_table_missing",
                        component="source",
                        name=name,
                        message=f"data source targets missing table {table_key!r}",
                        action="remove or re-target this source after restore",
                    )
                kind = str(item.get("kind") or "")
                if kind == SOURCE_KIND_WORKSPACE_FILE:
                    params = item.get("params")
                    try:
                        _safe_import_path(
                            str(params.get("path") or "") if isinstance(params, Mapping) else ""
                        )
                    except OctopError as exc:
                        issue(
                            warnings,
                            severity="warning",
                            code="source_path_invalid",
                            component="source",
                            name=name,
                            message=exc.message,
                            action="fix the workspace file source after restore",
                        )
                elif kind == SOURCE_KIND_EXTERNAL:
                    params = item.get("params")
                    connector_check(
                        str(params.get("instance_id") or "") if isinstance(params, Mapping) else "",
                        component="source",
                        name=name,
                    )
                else:
                    issue(
                        warnings,
                        severity="warning",
                        code="source_kind_invalid",
                        component="source",
                        name=name,
                        message=f"unknown data source kind {kind!r}",
                        action="remove or recreate this source after restore",
                    )

        raw_schedules = integrations.get("schedules")
        if isinstance(raw_schedules, list):
            summary["schedules"] = len(raw_schedules)
            source_ids = {
                str(item.get("source_id") or "")
                for item in (raw_sources or [])
                if isinstance(item, Mapping)
            }
            for item in raw_schedules:
                if not isinstance(item, Mapping):
                    continue
                action = str(item.get("action") or "")
                if action == SCHEDULE_ACTION_SNAPSHOT_PUBLICATION:
                    issue(
                        warnings,
                        severity="warning",
                        code="snapshot_schedule_skipped",
                        component="schedule",
                        name=action,
                        message="snapshot refresh schedules need a restored public link",
                        action="re-create the public link and its schedule after restore",
                    )
                elif action == SCHEDULE_ACTION_SYNC_SOURCE:
                    target = item.get("target")
                    source_id = (
                        str(target.get("source_id") or "") if isinstance(target, Mapping) else ""
                    )
                    if source_id not in source_ids:
                        issue(
                            warnings,
                            severity="warning",
                            code="schedule_source_missing",
                            component="schedule",
                            name=action,
                            message=f"schedule references missing source {source_id!r}",
                            action="recreate the source/schedule after restore",
                        )

        raw_webhooks = integrations.get("webhooks")
        if isinstance(raw_webhooks, list):
            summary["webhooks"] = len(raw_webhooks)
            for item in raw_webhooks:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or "")
                try:
                    await asyncio.to_thread(_validate_webhook_url, str(item.get("url") or ""))
                except Exception as exc:  # noqa: BLE001 - bad config becomes a warning
                    issue(
                        warnings,
                        severity="warning",
                        code="webhook_url_invalid",
                        component="webhook",
                        name=name,
                        message=f"webhook URL is invalid on this host: {exc}",
                        action="fix the URL after restore; the declaration will be disabled",
                    )

        raw_ingress = integrations.get("ingress")
        if isinstance(raw_ingress, list):
            summary["ingress"] = len(raw_ingress)
            source_ids = {
                str(item.get("source_id") or "")
                for item in (raw_sources or [])
                if isinstance(item, Mapping)
            }
            for item in raw_ingress:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or "")
                mode = str(item.get("mode") or "")
                if mode not in {"app.inbox", "app.import", "app.refresh"}:
                    issue(
                        warnings,
                        severity="warning",
                        code="ingress_mode_invalid",
                        component="ingress",
                        name=name,
                        message=f"unknown ingress mode {mode!r}",
                        action="remove or recreate this ingress after restore",
                    )
                    continue
                mapping = item.get("mapping")
                if mode == "app.import" and isinstance(mapping, Mapping):
                    table_key = str(mapping.get("table") or MAIN_TABLE_KEY)
                    if table_key not in schemas:
                        issue(
                            warnings,
                            severity="warning",
                            code="ingress_table_missing",
                            component="ingress",
                            name=name,
                            message=f"ingress mapping targets missing table {table_key!r}",
                            action="fix the mapping after restore",
                        )
                    columns = mapping.get("columns")
                    if isinstance(columns, Mapping) and table_key in schemas:
                        missing = sorted(
                            {str(value) for value in columns.values()}
                            - set(schemas[table_key].names)
                        )
                        if missing:
                            issue(
                                warnings,
                                severity="warning",
                                code="ingress_columns_missing",
                                component="ingress",
                                name=name,
                                message="ingress mapping targets missing columns: "
                                + ", ".join(missing),
                                action="fix the mapping after restore",
                            )
                if mode == "app.refresh":
                    source_id = (
                        str(mapping.get("source_id") or mapping.get("app_source_id") or "")
                        if isinstance(mapping, Mapping)
                        else ""
                    )
                    if not source_id:
                        issue(
                            warnings,
                            severity="warning",
                            code="ingress_source_missing",
                            component="ingress",
                            name=name,
                            message="ingress refresh has no source_id in its mapping",
                            action="fix the mapping after restore; this ingress will be skipped",
                        )
                    elif source_id not in source_ids:
                        issue(
                            warnings,
                            severity="warning",
                            code="ingress_source_missing",
                            component="ingress",
                            name=name,
                            message=f"ingress refresh references missing source {source_id!r}",
                            action="recreate the source or fix the mapping after restore",
                        )

        raw_services = integrations.get("services")
        if isinstance(raw_services, list):
            summary["services"] = len(raw_services)
            for item in raw_services:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or "")
                connector_check(
                    str(item.get("connector_instance_id") or ""),
                    component="service",
                    name=name,
                )

        if summary["ingress"] or summary["webhooks"]:
            issue(
                warnings,
                severity="warning",
                code="secrets_require_rotation",
                component="integration",
                message="signing secrets are never archived; restored endpoints are disabled",
                action="rotate/copy the new secrets and enable the endpoints after restore",
            )
        return _preflight_result(errors, warnings, summary)

    async def restore_archive(
        self,
        *,
        agent_id: str,
        owner_user_id: int,
        data: bytes,
    ) -> LightAppRow:
        """Create a new app from a backup archive produced by :meth:`backup_archive`."""
        if len(data) > MAX_BACKUP_BYTES:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"backup exceeds the {MAX_BACKUP_BYTES // (1024 * 1024)} MB limit",
            )
        preflight = await self.preflight_restore(
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            data=data,
        )
        if not preflight["can_restore"]:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "backup failed dependency checks",
                restore_preflight=preflight,
            )
        try:
            manifest, tables, html, preview, assets = await asyncio.to_thread(
                _read_backup_zip, data
            )
        except zipfile.BadZipFile:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID, "backup is not a valid zip archive"
            ) from None
        except ValueError as exc:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"invalid app backup: {exc}") from None
        app_meta = manifest.get("app")
        assert isinstance(app_meta, dict)  # _read_backup_zip validated it
        name = str(app_meta.get("name") or "").strip() or "Restored app"
        description = str(app_meta.get("description") or "")
        raw_capabilities = manifest.get("capabilities")
        restored_capabilities = (
            app_capabilities.validate(raw_capabilities)
            if isinstance(raw_capabilities, list)
            else app_capabilities.LEGACY_CAPABILITIES
        )
        main = tables.get(MAIN_TABLE_KEY)
        if main is None:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "backup has no main table")
        try:
            schemas = {
                key: json.loads(schema_bytes.decode("utf-8"))
                for key, (_label, schema_bytes, _csv, _row_ids) in tables.items()
            }
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "backup contains an unreadable table schema",
            ) from None
        row = await self.create(
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            name=name,
            description=description,
            columns=schemas[MAIN_TABLE_KEY],
            capabilities=restored_capabilities,
        )
        try:
            for key, (label, _schema_bytes, _csv, _row_ids) in tables.items():
                if key == MAIN_TABLE_KEY:
                    continue
                await self.create_table(
                    row,
                    table_key=key,
                    label=label,
                    columns=schemas[key],
                )
            id_maps: dict[str, dict[str, str]] = {}
            for key, (_label, _schema_bytes, csv_bytes, row_ids_bytes) in tables.items():
                records = parse_import_file(f"{key}.csv", csv_bytes) if csv_bytes.strip() else []
                row_ids: list[str] = []
                if row_ids_bytes:
                    try:
                        parsed_ids = json.loads(row_ids_bytes.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        raise _invalid(
                            ErrorCode.APP_SOURCE_INVALID,
                            f"backup has unreadable row ids for table {key!r}",
                        ) from None
                    if not isinstance(parsed_ids, list) or not all(
                        isinstance(item, str) and item for item in parsed_ids
                    ):
                        raise _invalid(
                            ErrorCode.APP_SOURCE_INVALID,
                            f"backup has invalid row ids for table {key!r}",
                        )
                    row_ids = list(parsed_ids)
                if row_ids and len(row_ids) != len(records):
                    raise _invalid(
                        ErrorCode.APP_SOURCE_INVALID,
                        f"backup row ids do not match rows for table {key!r}",
                    )
                if records:
                    id_maps[key] = await self._restore_table_rows(
                        row,
                        key,
                        records,
                        row_ids=row_ids or None,
                    )
            await self._remap_restored_references(row, id_maps)
            raw_table_meta = manifest.get("tables")
            if isinstance(raw_table_meta, list):
                for meta in raw_table_meta:
                    if not isinstance(meta, dict):
                        continue
                    key = str(meta.get("table_key") or "")
                    if key:
                        await self._apply_restored_table_config(row, key, meta)
            await self._restore_integrations(row, manifest)
            await self._validate_restored_references(row)
            if html:
                await self.write_html(row, html)
            if preview:
                await self.write_preview(row, preview)
            for asset_name, asset_data in assets:
                await self.save_asset(row, asset_name, asset_data)
        except Exception:
            logger.warning("failed to restore app, cleaning up: %s", row.app_id, exc_info=True)
            await self.delete(row)
            raise
        return self.get(row.app_id)

    # ----- public links -----

    def get_publication(self, row: AppRecord) -> LightAppPublicationRow | None:
        return self._app_repo.get_publication(row.app_id)

    @staticmethod
    def _publication_secret_key(app_id: str) -> str:
        return f"light_app_publication_token:{app_id}"

    def create_publication(
        self,
        row: AppRecord,
        *,
        user_id: int,
        ttl_days: int = 0,
    ) -> tuple[str, int | None]:
        """Create (or rotate) the read-only public token; returns it once."""
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + ttl_days * 86400 if ttl_days > 0 else None
        self._app_repo.upsert_publication(
            row.app_id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            created_by=user_id,
            expires_at=expires_at,
        )
        if self._secret_repo is not None:
            self._secret_repo.set(self._publication_secret_key(row.app_id), token.encode("utf-8"))
        return token, expires_at

    def get_or_create_publication(
        self,
        row: AppRecord,
        *,
        user_id: int,
        rotate: bool = False,
        ttl_days: int = 0,
    ) -> tuple[str, int | None]:
        """Return the active public token, reusing it unless *rotate* is set.

        The publication table stores only the token hash.  A copy of the
        plaintext token lives in the secrets table so agent tools can hand the
        same link to the user again without invalidating the previous message.
        """
        publication = self.get_publication(row)
        if (
            publication is not None
            and publication.revoked_at is None
            and (publication.expires_at is None or publication.expires_at > int(time.time()))
            and not rotate
            and self._secret_repo is not None
        ):
            stored = self._secret_repo.get(self._publication_secret_key(row.app_id))
            if stored:
                return stored.decode("utf-8"), publication.expires_at
        return self.create_publication(row, user_id=user_id, ttl_days=ttl_days)

    async def _snapshot_parts(
        self,
        row: AppRecord,
        ws: BackendWorkspace,
    ) -> tuple[str, str]:
        """Current HTML plus the public table structure, in a stable encoding."""
        html = await ws.aread_text(app_path(row.app_id, FILE_HTML)) or ""
        if html:
            # Publishing freezes the HTML: validate here as well so a file
            # edited outside app_write_html never becomes a public snapshot.
            validate_html(html)
        tables: list[dict[str, Any]] = []
        for table in self.list_tables(row):
            if not table.public_visible or table.row_scope == "user":
                continue
            schema = await self._read_schema(ws, row.app_id, table.table_key)
            tables.append(
                {
                    "table_key": table.table_key,
                    "label": table.label,
                    "is_main": table.is_main,
                    "public_visible": table.public_visible,
                    "columns": [column.to_dict() for column in schema.columns],
                }
            )
        return html, json.dumps(
            tables,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def publish(
        self,
        row: AppRecord,
        *,
        user_id: int,
        rotate: bool = False,
        ttl_days: int = 0,
    ) -> tuple[str, int | None]:
        """Create/reuse the public token and freeze the current public version.

        The token stays stable unless ``rotate`` is set; the snapshot is always
        refreshed. Rows remain live; only the HTML and public table structure
        are frozen.
        """
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            ws = await self._workspace(row.agent_id)
            html, tables_json = await self._snapshot_parts(row, ws)
            if rotate:
                token, expires_at = self.create_publication(row, user_id=user_id, ttl_days=ttl_days)
            else:
                token, expires_at = self.get_or_create_publication(
                    row, user_id=user_id, rotate=False, ttl_days=ttl_days
                )
            now = int(time.time())
            self._app_repo.set_publication_snapshot(
                row.app_id,
                html=html,
                tables_json=tables_json,
                digest=hashlib.sha256((html + "\0" + tables_json).encode("utf-8")).hexdigest(),
                snapshot_at=now,
            )
            return token, expires_at

    async def publication_status(self, row: AppRecord) -> PublicationStatus:
        """Compare the stored snapshot with the current app version."""
        publication = self.get_publication(row)
        if publication is None:
            return PublicationStatus(published_at=None, stale=False)
        if publication.snapshot_at is None:
            return PublicationStatus(
                published_at=None,
                stale=True,
                reasons=("not_published",),
            )
        async with _lock_for(row.app_id):
            try:
                ws = await self._workspace(row.agent_id)
                html, tables_json = await self._snapshot_parts(row, ws)
            except OctopError:
                return PublicationStatus(
                    published_at=publication.snapshot_at,
                    stale=True,
                    reasons=("workspace",),
                )
        reasons: list[str] = []
        if publication.html_snapshot != html:
            reasons.append("html")
        if publication.tables_snapshot_json != tables_json:
            reasons.append("schema")
        return PublicationStatus(
            published_at=publication.snapshot_at,
            stale=bool(reasons),
            reasons=tuple(reasons),
        )

    def public_snapshot(
        self,
        row: AppRecord,
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """The frozen public version, or None for a legacy publication."""
        publication = self.get_publication(row)
        if publication is None or publication.snapshot_at is None:
            return None
        try:
            items = json.loads(publication.tables_snapshot_json or "[]")
        except ValueError:
            items = []
        if not isinstance(items, list):
            items = []
        live = {table.table_key: table for table in self.list_tables(row)}
        tables: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get("table_key")
            table = live.get(key) if isinstance(key, str) else None
            # Hiding a table (or making it per-user) takes effect immediately,
            # even with a snapshot.
            if table is None or not table.public_visible or table.row_scope == "user":
                continue
            tables.append({**item, "row_count": table.row_count})
        return publication.html_snapshot, tables

    def revoke_publication(self, row: AppRecord) -> bool:
        revoked = self._app_repo.revoke_publication(row.app_id)
        if self._secret_repo is not None:
            self._secret_repo.delete(self._publication_secret_key(row.app_id))
        if revoked and self._schedule_repo is not None:
            for item in self._schedule_repo.list_for_app(row.app_id):
                if item.action == SCHEDULE_ACTION_SNAPSHOT_PUBLICATION:
                    self._schedule_repo.delete(row.app_id, item.schedule_id)
        return revoked

    def resolve_public_app(self, token: str) -> AppRecord | None:
        clean = token.strip()
        if not clean:
            return None
        publication = self._app_repo.get_publication_by_hash(
            hashlib.sha256(clean.encode("utf-8")).hexdigest()
        )
        if publication is None or publication.revoked_at is not None:
            return None
        if publication.expires_at is not None and publication.expires_at <= int(time.time()):
            return None
        return self._app_repo.get(publication.app_id)

    # ----- one-time authenticated links -----

    ACCESS_LINK_TTL_SECONDS = 600
    _ACCESS_LINK_CLEANUP_SECONDS = 86400

    @staticmethod
    def _hash_access_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def create_access_link(
        self,
        row: AppRecord,
        *,
        user_id: int,
        channel_type: str = "",
        channel_subject_id: str = "",
        ttl_seconds: int = ACCESS_LINK_TTL_SECONDS,
        origin: str = "",
    ) -> tuple[str, int]:
        """Create a one-time personal sign-in code for this app; returns it once.

        ``origin`` records who asked for the code (``app_share_link`` for the
        agent tool). Direct service/script calls leave it empty and are flagged
        in the audit trail.
        """
        if self._access_code_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "access links are not configured")
        permission = self.permission_for(row, user_id, is_admin=self.is_admin(user_id))
        if permission is None:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        token = secrets.token_urlsafe(32)
        now = int(self._clock())
        expires_at = now + max(60, min(int(ttl_seconds), self._ACCESS_LINK_CLEANUP_SECONDS))
        origin_value = origin.strip() or "unknown"
        if origin_value not in {"app_share_link", "app_open"}:
            logger.warning(
                "access link created outside MiniApp tools app=%s user=%s origin=%s",
                row.app_id,
                user_id,
                origin_value,
            )
        self._access_code_repo.delete_expired(before=now - self._ACCESS_LINK_CLEANUP_SECONDS)
        self._access_code_repo.create(
            code_hash=self._hash_access_code(token),
            app_id=row.app_id,
            user_id=user_id,
            permission=permission,
            channel_type=channel_type.strip(),
            channel_subject_id=channel_subject_id.strip(),
            expires_at=expires_at,
        )
        self._audit_access(
            actor=f"user:{user_id}",
            action="light_app.access_link.create",
            target=row.app_id,
            payload={
                "permission": permission,
                "channel_type": channel_type.strip(),
                "expires_at": expires_at,
                "origin": origin_value,
            },
        )
        return token, expires_at

    def preview_access_link(self, code: str) -> AppAccessCodeRow | None:
        """Read a live access code without consuming it."""
        repo = self._access_code_repo
        clean = code.strip()
        if repo is None or not clean:
            return None
        row = repo.get_by_hash(self._hash_access_code(clean))
        if row is None or row.consumed_at is not None:
            return None
        if row.expires_at <= int(self._clock()):
            return None
        return row

    def consume_access_link(self, code: str) -> AppAccessCodeRow | None:
        """Consume a one-time access code atomically."""
        repo = self._access_code_repo
        clean = code.strip()
        if repo is None or not clean:
            return None
        return repo.consume(self._hash_access_code(clean), now=int(self._clock()))

    # ----- service bindings -----

    def list_bindings(self, row: AppRecord) -> list[BindingView]:
        """The implicit built-in dataset plus any stored connector/external bindings."""
        stored = self._binding_repo.list_for_app(row.app_id) if self._binding_repo else []
        return [
            BindingView(
                binding_id=BUILTIN_BINDING_ID,
                provider="builtin",
                service_id=BUILTIN_SERVICE_ID,
                config={},
                enabled=True,
                version=1,
                builtin=True,
            ),
            *[self._binding_view(item) for item in stored if item.binding_id != BUILTIN_BINDING_ID],
        ]

    def save_binding(
        self,
        row: AppRecord,
        *,
        binding_id: str,
        provider: str,
        service_id: str,
        config: Mapping[str, Any] | None = None,
        enabled: bool = True,
    ) -> BindingView:
        if self._binding_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "service bindings are not configured")
        clean_id = binding_id.strip()
        if clean_id == BUILTIN_BINDING_ID:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                f"binding id {BUILTIN_BINDING_ID!r} is reserved for the built-in dataset",
            )
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", clean_id) is None:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                "binding id must be 1-64 chars: lowercase letters, digits, '_' or '-'",
            )
        clean_provider = provider.strip().lower()
        if clean_provider not in BINDING_PROVIDERS:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                f"unknown binding provider: {provider!r}",
            )
        clean_service = service_id.strip()[:200]
        if not clean_service:
            raise _invalid(ErrorCode.APP_SHARE_INVALID, "service_id is required")
        if clean_provider == "builtin" and clean_service != BUILTIN_SERVICE_ID:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                f"builtin bindings must use {BUILTIN_SERVICE_ID!r}",
            )
        payload = dict(config or {})
        config_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(config_json.encode("utf-8")) > 64 * 1024:
            raise _invalid(ErrorCode.APP_SHARE_INVALID, "binding config exceeds 64 KB")
        stored = self._binding_repo.upsert(
            app_id=row.app_id,
            binding_id=clean_id,
            provider=clean_provider,
            service_id=clean_service,
            config_json=config_json,
            enabled=enabled,
        )
        return self._binding_view(stored)

    def delete_binding(self, row: AppRecord, binding_id: str) -> bool:
        if self._binding_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "service bindings are not configured")
        if binding_id == BUILTIN_BINDING_ID:
            raise _invalid(
                ErrorCode.APP_SHARE_INVALID,
                f"binding {BUILTIN_BINDING_ID!r} is implicit and cannot be deleted",
            )
        return self._binding_repo.delete(row.app_id, binding_id)

    # ----- R2.1 external services -----

    @staticmethod
    def _service_view(item: LightAppServiceRow) -> ServiceView:
        return ServiceView(
            service_id=item.service_id,
            name=item.name,
            provider=item.provider,
            connector_instance_id=item.connector_instance_id,
            allowed_tools=item.allowed_tools,
            allowed_url_prefixes=item.allowed_url_prefixes,
            request_headers=dict(item.request_headers),
            cache_ttl_seconds=item.cache_ttl_seconds,
            max_calls_per_minute=item.max_calls_per_minute,
            public_live=item.public_live,
            enabled=item.enabled,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def list_services(self, row: AppRecord) -> list[ServiceView]:
        if self._service_repo is None:
            return []
        return [self._service_view(item) for item in self._service_repo.list_for_app(row.app_id)]

    @staticmethod
    def _gateway_tool_names(kind: str) -> set[str]:
        return {str(meta.get("name") or "") for meta in mcp_tools_for_kind(kind)}

    def _ensure_public_connector(self, user_id: int, kind: str) -> str:
        """Reuse or auto-create one credential-free gateway connector.

        Templates and MiniApp service declarations may omit the connector
        instance for a zero-credential public source (``http-extract``,
        ``douban``). One instance per user/kind keeps the connector list
        uncluttered without requiring a detour to the Connectors page.
        """
        if self._connector_repo is None:
            raise OctopError(
                ErrorCode.INTERNAL_ERROR,
                "connectors are not configured on this server",
            )
        entry = get_catalog_entry(kind)
        if entry is None or entry.auth_kind != "none" or entry.mcp_mode != "gateway":
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"connector {kind!r} cannot be auto-provisioned; it needs credentials",
            )
        existing = self._connector_repo.get_by_user_kind(user_id, kind)
        if existing is not None:
            return existing.instance_id
        instance_id = new_ulid()
        self._connector_repo.create(
            instance_id=instance_id,
            user_id=user_id,
            kind=kind,
            display_name=f"{entry.name.resolve('en')} (MiniApp)",
            mcp_server_name=mcp_server_name(kind, instance_id),
            config_json=json.dumps(
                {"description": f"Auto-created for MiniApp {kind} services"},
                ensure_ascii=False,
            ),
        )
        if self._audit_repo is not None:
            self._audit_repo.write(
                actor=f"user:{user_id}",
                action="connector.instance.create",
                target=instance_id,
                payload=f"{kind} (MiniApp auto-provisioned)",
            )
        return instance_id

    def save_service(
        self,
        row: AppRecord,
        *,
        name: str,
        provider: str,
        connector_instance_id: str,
        connector_kind: str | None = None,
        allowed_tools: list[str],
        allowed_url_prefixes: list[str],
        request_headers: Mapping[str, str] | None = None,
        cache_ttl_seconds: int,
        max_calls_per_minute: int,
        public_live: bool,
        enabled: bool,
        actor_user_id: int,
        is_admin: bool,
    ) -> ServiceView:
        """Create or update one service declaration.

        It is both the call allowlist and the assembly binding: saving it
        mirrors a connector/external binding with the same page-facing name.
        """
        if self._service_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "app services are not configured")
        clean_name = name.strip().lower()
        if not SERVICE_NAME_RE.fullmatch(clean_name) or clean_name == BUILTIN_BINDING_ID:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "service name must be lowercase letters, digits, '-' or '_'",
            )
        clean_provider = provider.strip().lower()
        if clean_provider not in SERVICE_PROVIDERS:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID, "service provider must be connector or external"
            )
        instance_id = connector_instance_id.strip()
        instance_kind: str | None = None
        if clean_provider == "external" and not instance_id:
            # Public sources are first-class MiniApp services. Auto-provision a
            # credential-free gateway connector instead of making the user
            # visit the Connectors page first.
            auto_kind = (connector_kind or "http-extract").strip().lower()
            instance_id = self._ensure_public_connector(row.owner_user_id, auto_kind)
            instance_kind = auto_kind
        elif not instance_id:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "connector_instance_id is required")
        elif self._connector_repo is not None:
            instance = self._connector_repo.get(instance_id)
            if instance is None:
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"unknown connector: {instance_id!r}")
            instance_kind = instance.kind
            if not (is_admin or instance.user_id == row.owner_user_id or instance.shared):
                raise OctopError(
                    ErrorCode.APP_FORBIDDEN,
                    "only the connector owner can bind it to this app",
                )
        tools = list(dict.fromkeys(item.strip() for item in allowed_tools if item.strip()))
        if not tools:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "at least one read-only tool is required")
        prefixes = list(
            dict.fromkeys(item.strip().rstrip("/") for item in allowed_url_prefixes if item.strip())
        )
        if instance_kind == "douban" and not prefixes:
            # Cover URLs come from Douban's image hosts; pre-fill the service
            # allowlist so book covers work without extra manual setup.
            prefixes = list(COVER_URL_PREFIXES)
        for prefix in prefixes:
            if not prefix.startswith(("https://", "http://")):
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"invalid URL prefix: {prefix!r}")
        gateway_tools = self._gateway_tool_names(instance_kind) if instance_kind else set()
        if gateway_tools:
            unknown = set(tools) - gateway_tools
            if unknown:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    f"connector {instance_kind!r} does not provide tools: "
                    f"{', '.join(sorted(unknown))}",
                )
        if instance_kind == "http-extract" and not prefixes:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "http-extract services require at least one URL prefix",
            )
        existing = self._service_repo.get_by_name(row.app_id, clean_name)
        if request_headers is None:
            # Omitted means "keep what is stored"; older dashboard builds do
            # not send this field when toggling an existing service.
            headers = dict(existing.request_headers) if existing is not None else {}
            if headers and instance_kind != "http-extract":
                headers = {}
        else:
            try:
                headers = normalize_request_headers(request_headers)
            except ValueError as exc:
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, str(exc)) from None
            if headers and instance_kind != "http-extract":
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "request_headers are only supported by http-extract services",
                )
        if not 0 <= cache_ttl_seconds <= 86_400:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "cache_ttl_seconds must be 0..86400")
        if not 1 <= max_calls_per_minute <= 600:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "max_calls_per_minute must be 1..600",
            )
        if existing is None:
            stored = self._service_repo.create(
                app_id=row.app_id,
                name=clean_name,
                provider=clean_provider,
                connector_instance_id=instance_id,
                allowed_tools=tools,
                allowed_url_prefixes=prefixes,
                request_headers=headers,
                cache_ttl_seconds=cache_ttl_seconds,
                max_calls_per_minute=max_calls_per_minute,
                public_live=public_live,
                enabled=enabled,
                created_by=actor_user_id,
            )
        else:
            updated = self._service_repo.update(
                existing.service_id,
                provider=clean_provider,
                connector_instance_id=instance_id,
                allowed_tools=tools,
                allowed_url_prefixes=prefixes,
                request_headers=headers,
                cache_ttl_seconds=cache_ttl_seconds,
                max_calls_per_minute=max_calls_per_minute,
                public_live=public_live,
                enabled=enabled,
            )
            if updated is None:
                raise OctopError(ErrorCode.APP_NOT_FOUND, "service declaration not found")
            self._service_repo.clear_cache(existing.service_id)
            stored = updated
        if self._binding_repo is not None:
            self._binding_repo.upsert(
                app_id=row.app_id,
                binding_id=clean_name,
                provider=clean_provider,
                service_id=stored.service_id,
                config_json="{}",
                enabled=enabled,
            )
        return self._service_view(stored)

    def delete_service(self, row: AppRecord, name: str) -> bool:
        if self._service_repo is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "app services are not configured")
        clean_name = name.strip().lower()
        existing = self._service_repo.get_by_name(row.app_id, clean_name)
        if existing is None:
            return False
        self._service_repo.clear_cache(existing.service_id)
        if self._binding_repo is not None:
            self._binding_repo.delete(row.app_id, clean_name)
        return self._service_repo.delete(row.app_id, clean_name)

    async def call_service(
        self,
        row: AppRecord,
        *,
        name: str,
        tool: str,
        args: Mapping[str, Any] | None = None,
    ) -> ServiceCallResult:
        """Call one declared, allowlisted read-only connector tool.

        The declaration is enforced here as well as at bind time: tools must be
        allowlisted, URLs must match a prefix, live calls are rate-limited, and
        responses are cached with the declared TTL.
        """
        self.require_capability(row, "external.fetch")
        if self._service_repo is None or self._external_tool_runner is None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "external services are not available on this server",
            )
        declaration = self._service_repo.get_by_name(row.app_id, name.strip().lower())
        if declaration is None or not declaration.enabled:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"unknown or disabled service: {name!r}")
        clean_tool = tool.strip()
        if clean_tool not in declaration.allowed_tools:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"tool {clean_tool!r} is not allowed for service {declaration.name!r}",
            )
        clean_args = {str(key): value for key, value in (args or {}).items()}
        if "_headers" in clean_args:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "_headers is reserved for the service declaration",
            )
        url = clean_args.get("url")
        if isinstance(url, str) and url.strip():
            clean_url = url.strip()
            if not _service_url_allowed(declaration.allowed_url_prefixes, clean_url):
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "URL is outside the service's allowed prefixes",
                )
        args_json = json.dumps(
            clean_args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        args_hash = hashlib.sha256(args_json.encode("utf-8")).hexdigest()
        runner_args = dict(clean_args)
        if declaration.request_headers:
            runner_args["_headers"] = dict(declaration.request_headers)
        ttl = declaration.cache_ttl_seconds
        now = int(time.time())
        if ttl > 0:
            cached = self._service_repo.get_cached(
                declaration.service_id,
                clean_tool,
                args_hash,
                now=now,
            )
            if cached is not None:
                return ServiceCallResult(
                    value=_service_value(cached),
                    cached=True,
                    expires_at=None,
                )
            # Opportunistic cleanup keeps the cache table bounded without a
            # dedicated scheduler.
            self._service_repo.delete_expired(now=now)
        async with app_limits.service_slot(
            declaration.service_id,
            per_minute=declaration.max_calls_per_minute,
        ):
            output = await asyncio.to_thread(
                self._external_tool_runner.run,
                instance_id=declaration.connector_instance_id,
                tool=clean_tool,
                args=runner_args,
            )
        if not isinstance(output, str):
            output = str(output)
        if len(output.encode("utf-8")) > SERVICE_MAX_RESPONSE_BYTES:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "service response is larger than 5 MB",
            )
        expires_at = now + ttl if ttl > 0 else None
        if expires_at is not None:
            self._service_repo.put_cached(
                service_id=declaration.service_id,
                tool=clean_tool,
                args_hash=args_hash,
                response_json=output,
                expires_at=expires_at,
            )
        return ServiceCallResult(
            value=_service_value(output),
            cached=False,
            expires_at=expires_at,
        )

    def read_cached_service(
        self,
        row: AppRecord,
        *,
        name: str,
        tool: str,
        args: Mapping[str, Any] | None = None,
    ) -> ServiceCallResult:
        """Read a cached service response for a public link; never calls live."""
        if self._service_repo is None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "external services are not available on this server",
            )
        declaration = self._service_repo.get_by_name(row.app_id, name.strip().lower())
        if declaration is None or not declaration.enabled or not declaration.public_live:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"service {name!r} is not available on the public link",
            )
        clean_tool = tool.strip()
        if clean_tool not in declaration.allowed_tools:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"tool {clean_tool!r} is not allowed for service {declaration.name!r}",
            )
        clean_args = {str(key): value for key, value in (args or {}).items()}
        if "_headers" in clean_args:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "_headers is reserved for the service declaration",
            )
        url = clean_args.get("url")
        if (
            isinstance(url, str)
            and url.strip()
            and not _service_url_allowed(declaration.allowed_url_prefixes, url.strip())
        ):
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "URL is outside the service's allowed prefixes",
            )
        args_json = json.dumps(
            clean_args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        args_hash = hashlib.sha256(args_json.encode("utf-8")).hexdigest()
        cached = self._service_repo.get_cached(
            declaration.service_id,
            clean_tool,
            args_hash,
            now=int(time.time()),
        )
        if cached is None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "no cached response for this public service call",
            )
        return ServiceCallResult(value=_service_value(cached), cached=True, expires_at=None)

    @staticmethod
    def _event_view(row: AppEventRow) -> AppEventView:
        return AppEventView(
            event_id=row.event_id,
            event_type=row.event_type,
            source=row.source,
            summary=row.summary,
            payload=row.payload,
            created_at=row.created_at,
            acknowledged_at=row.acknowledged_at,
        )

    def append_event(
        self,
        row: AppRecord,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        source: str = "",
        summary: str = "",
        external_event_id: str | None = None,
    ) -> AppEventView:
        """Append one inbox event, keeping the newest 500 within 30 days.

        ``external_event_id`` makes retries idempotent: the same upstream event
        maps to the same app event id.
        """
        if self._event_repo is None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "app events are not available on this server",
            )
        event_id = f"ext_{external_event_id.strip()[:80]}" if external_event_id else None
        if event_id is not None:
            existing = self._event_repo.get_for_app(row.app_id, event_id)
            if existing is not None:
                return self._event_view(existing)
        clean_type = event_type.strip()[:100]
        if not clean_type:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "event_type is required")
        raw = json.dumps(payload or {}, ensure_ascii=False)
        if len(raw.encode("utf-8")) > APP_EVENT_MAX_PAYLOAD_BYTES:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "event payload exceeds 64 KB")
        stored = self._event_repo.create(
            app_id=row.app_id,
            event_type=clean_type,
            source=source.strip()[:200],
            summary=summary.strip()[:500],
            payload=payload or {},
            event_id=event_id,
        )
        self._event_repo.prune(
            row.app_id,
            before=int(time.time()) - APP_EVENT_RETENTION_SECONDS,
            keep=APP_EVENT_KEEP,
        )
        return self._event_view(stored)

    def list_events(
        self,
        row: AppRecord,
        *,
        since: int = 0,
        limit: int = 100,
    ) -> tuple[list[AppEventView], int]:
        if self._event_repo is None:
            return [], 0
        events, latest = self._event_repo.list_after(
            row.app_id,
            since=max(0, since),
            limit=limit,
        )
        return [self._event_view(item) for item in events], latest

    def ack_events(self, row: AppRecord, event_ids: list[str]) -> int:
        if self._event_repo is None:
            return 0
        return self._event_repo.ack(row.app_id, event_ids)

    async def fetch_service_image(
        self,
        row: AppRecord,
        *,
        name: str,
        url: str,
    ) -> str:
        """Fetch a public image through a declared service and store it as an asset."""
        self.require_capability(row, "external.fetch")
        if self._service_repo is None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "external services are not available on this server",
            )
        declaration = self._service_repo.get_by_name(row.app_id, name.strip().lower())
        if declaration is None or not declaration.enabled:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"unknown or disabled service: {name!r}")
        clean_url = url.strip()
        if not clean_url:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "url is required")
        if not _service_url_allowed(declaration.allowed_url_prefixes, clean_url):
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "URL is outside the service's allowed prefixes",
            )
        data = await asyncio.to_thread(_fetch_service_image, clean_url)
        asset_name = f"svc_{hashlib.sha256(clean_url.encode('utf-8')).hexdigest()[:20]}"
        return await self.save_asset(row, asset_name, data)

    async def export_dataset(self, row: AppRecord) -> bytes:
        """Export the built-in dataset as a portable JSON document.

        This is the first half of the promote path: the manifest and rows are
        self-describing, so an external service adapter can import them without
        reaching into Octop's control-plane tables.
        """
        current = self._app_repo.get(row.app_id) or row
        ws = await self._workspace(current.agent_id)
        tables: list[dict[str, Any]] = []
        for view in self.list_tables(current):
            schema = await self.read_schema(current, view.table_key)
            if current.storage_version >= 2:
                stored = self._app_repo.list_all_data_rows(current.app_id, view.table_key)
                rows = [
                    {
                        "values": item.values,
                        "created_by": item.created_by,
                        "row_id": item.row_id,
                        "version": item.version,
                    }
                    for item in stored
                ]
            else:
                legacy = await self._read_rows_file(ws, current.app_id, schema, view.table_key)
                rows = [{"values": values} for values in legacy]
            tables.append(
                {
                    "table_key": view.table_key,
                    "label": view.label,
                    "is_main": view.is_main,
                    "public_visible": view.public_visible,
                    "row_scope": view.row_scope,
                    "rules": view.rules,
                    "schema": schema.to_dict(),
                    "rows": rows,
                }
            )
        payload = {
            "format": "octop.miniapp.dataset",
            "version": 1,
            "exported_at": int(time.time()),
            "app": {
                "app_id": current.app_id,
                "name": current.name,
                "description": current.description,
                "agent_id": current.agent_id,
                "owner_user_id": current.owner_user_id,
            },
            "capabilities": self.capability_list(current),
            "tables": tables,
            "bindings": [
                {
                    "binding_id": item.binding_id,
                    "provider": item.provider,
                    "service_id": item.service_id,
                    "config": item.config,
                    "enabled": item.enabled,
                    "builtin": item.builtin,
                }
                for item in self.list_bindings(current)
            ],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(content) > MAX_BACKUP_BYTES:
            raise _invalid(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"dataset export exceeds {MAX_BACKUP_BYTES // (1024 * 1024)} MB",
                limit=MAX_BACKUP_BYTES,
            )
        return content

    async def import_dataset(
        self,
        payload: Mapping[str, Any],
        *,
        agent_id: str,
        owner_user_id: int,
    ) -> LightAppRow:
        """Create a new app from :meth:`export_dataset` output.

        Rows are attributed to the importing owner because external user ids
        do not necessarily exist on this host. Connector/external bindings are
        recreated disabled so the owner can review and re-bind credentials.
        """
        if payload.get("format") != "octop.miniapp.dataset":
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "not a MiniApp dataset export")
        try:
            version = int(payload.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        if version != 1:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "unsupported dataset export version")
        raw_tables = payload.get("tables")
        if not isinstance(raw_tables, list) or not raw_tables:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "dataset export has no tables")
        main = next(
            (
                item
                for item in raw_tables
                if isinstance(item, dict)
                and (item.get("is_main") is True or item.get("table_key") == MAIN_TABLE_KEY)
            ),
            None,
        )
        if main is None:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "dataset export has no main table")
        raw_app = payload.get("app")
        app_meta = raw_app if isinstance(raw_app, dict) else {}
        base_name = str(app_meta.get("name") or "Imported app").strip() or "Imported app"
        name = (base_name[:90] + " (imported)")[:100]
        description = str(app_meta.get("description") or "")
        raw_caps = payload.get("capabilities")
        capabilities = raw_caps if isinstance(raw_caps, list) else None
        main_schema = main.get("schema")
        if not isinstance(main_schema, dict):
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "main table has no schema")

        row = await self.create(
            agent_id=agent_id,
            owner_user_id=owner_user_id,
            name=name,
            description=description,
            columns=main_schema,
            capabilities=capabilities,
        )
        try:
            for table in raw_tables:
                if not isinstance(table, dict):
                    raise _invalid(ErrorCode.APP_SOURCE_INVALID, "invalid table entry")
                key = str(table.get("table_key") or MAIN_TABLE_KEY)
                scope = str(table.get("row_scope") or "app")
                public_visible = bool(table.get("public_visible", key == MAIN_TABLE_KEY))
                rules = table.get("rules")
                if key == MAIN_TABLE_KEY:
                    await self.set_table_row_scope(row, table=key, row_scope=scope)
                    await self.set_table_visibility(row, table=key, public_visible=public_visible)
                else:
                    schema = table.get("schema")
                    if not isinstance(schema, dict):
                        raise _invalid(
                            ErrorCode.APP_SOURCE_INVALID,
                            f"table {key!r} has no schema",
                        )
                    await self.create_table(
                        row,
                        table_key=key,
                        label=str(table.get("label") or ""),
                        columns=schema,
                        public_visible=public_visible,
                        row_scope=scope,
                    )
                if isinstance(rules, dict) and rules:
                    await self.set_table_rules(row, table=key, rules=rules)
                records = self._dataset_records(table.get("rows"))
                if records:
                    await self.import_rows(
                        row,
                        records,
                        table=key,
                        mode=IMPORT_MODE_REPLACE,
                        source="promote",
                        created_by=owner_user_id,
                    )
            raw_bindings = payload.get("bindings")
            if isinstance(raw_bindings, list):
                for item in raw_bindings:
                    if not isinstance(item, dict) or item.get("builtin"):
                        continue
                    binding_id = str(item.get("binding_id") or "")
                    if not binding_id or binding_id == BUILTIN_BINDING_ID:
                        continue
                    self.save_binding(
                        row,
                        binding_id=binding_id,
                        provider=str(item.get("provider") or ""),
                        service_id=str(item.get("service_id") or ""),
                        config=item.get("config") if isinstance(item.get("config"), dict) else {},
                        enabled=False,
                    )
        except Exception:
            await self.delete(row)
            raise
        return self.get(row.app_id)

    @staticmethod
    def _dataset_records(raw: object) -> list[dict[str, str]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "table rows must be a list")
        records: list[dict[str, str]] = []
        for item in raw:
            values = item.get("values") if isinstance(item, dict) else None
            if not isinstance(values, dict):
                values = item if isinstance(item, dict) else None
            if not isinstance(values, dict):
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, "invalid row entry")
            records.append({str(key): str(value) for key, value in values.items()})
        return records

    @staticmethod
    def _binding_view(item: LightAppBindingRow) -> BindingView:
        try:
            config = json.loads(item.config_json or "{}")
        except ValueError:
            config = {}
        return BindingView(
            binding_id=item.binding_id,
            provider=item.provider,
            service_id=item.service_id,
            config=dict(config) if isinstance(config, dict) else {},
            enabled=item.enabled,
            version=item.version,
            builtin=False,
        )

    # ----- data sources -----

    def list_sources(self, row: AppRecord) -> list[SourceView]:
        return [self._source_view(item) for item in self._app_repo.list_sources(row.app_id)]

    def get_source(self, row: AppRecord, source_id: str) -> SourceView:
        item = self._app_repo.get_source(row.app_id, source_id)
        if item is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return self._source_view(item)

    def save_source(
        self,
        row: AppRecord,
        *,
        source_id: str,
        table_key: str,
        kind: str,
        mode: str,
        schedule_seconds: int = 0,
        params: Mapping[str, Any] | None = None,
        mapping: Mapping[str, str] | None = None,
        created_by: int,
    ) -> SourceView:
        """Create or update one binding. Identity is ``source_id``; a table can
        hold at most one binding."""
        self._require_table(row, table_key)
        if kind not in SOURCE_KINDS:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"invalid source kind: {kind!r}",
            )
        if mode not in IMPORT_MODES:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"invalid source mode: {mode!r}",
            )
        schedule = int(schedule_seconds)
        if schedule < 0 or schedule > MAX_SCHEDULE_SECONDS:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"schedule_seconds must be 0..{MAX_SCHEDULE_SECONDS}",
            )
        clean_params = self._validate_source_params(kind, params or {})
        clean_mapping = {
            str(src).strip(): str(dst).strip()
            for src, dst in (mapping or {}).items()
            if str(src).strip() and str(dst).strip()
        }
        existing = self._app_repo.get_source(row.app_id, source_id)
        if existing is not None:
            try:
                old_params = json.loads(existing.params_json)
            except json.JSONDecodeError:
                old_params = {}
            if isinstance(old_params, dict) and old_params.get("cursor"):
                clean_params.setdefault("cursor", str(old_params["cursor"]))
        params_json = json.dumps(clean_params, ensure_ascii=False)
        mapping_json = json.dumps(clean_mapping, ensure_ascii=False)
        if existing is None:
            clash = any(
                item.table_key == table_key for item in self._app_repo.list_sources(row.app_id)
            )
            if clash:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    f"table {table_key!r} already has a data source",
                )
            item = self._app_repo.insert_source(
                source_id=source_id,
                app_id=row.app_id,
                table_key=table_key,
                kind=kind,
                mode=mode,
                schedule_seconds=schedule,
                params_json=params_json,
                mapping_json=mapping_json,
                created_by=created_by,
            )
        else:
            item = self._app_repo.update_source(
                row.app_id,
                source_id,
                table_key=table_key,
                kind=kind,
                mode=mode,
                schedule_seconds=schedule,
                params_json=params_json,
                mapping_json=mapping_json,
            )
        self._mirror_source_schedule(item, created_by=created_by)
        return self._source_view(item)

    def delete_source(self, row: AppRecord, source_id: str) -> None:
        if not self._app_repo.delete_source(row.app_id, source_id):
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        if self._schedule_repo is not None:
            self._schedule_repo.delete_by_source(source_id)

    def _mirror_source_schedule(
        self,
        item: LightAppSourceRow,
        *,
        created_by: int | None = None,
        enabled: bool = True,
    ) -> None:
        """Keep the ``sync_source`` schedule row in step with the binding."""
        if self._schedule_repo is None:
            return
        if item.schedule_seconds > 0:
            self._schedule_repo.upsert_source(
                app_id=item.app_id,
                source_id=item.source_id,
                interval_seconds=item.schedule_seconds,
                created_by=created_by or item.created_by,
                enabled=enabled,
            )
        else:
            self._schedule_repo.delete_by_source(item.source_id)

    def list_schedules(self, row: AppRecord) -> list[ScheduleView]:
        if self._schedule_repo is None:
            return []
        return [self._schedule_view(item) for item in self._schedule_repo.list_for_app(row.app_id)]

    def get_schedule(self, row: AppRecord, schedule_id: str) -> ScheduleView:
        """One schedule that belongs to *row*, or APP_NOT_FOUND."""
        if self._schedule_repo is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        item = self._schedule_repo.get(schedule_id)
        if item is None or item.app_id != row.app_id:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return self._schedule_view(item)

    def save_schedule(
        self,
        row: AppRecord,
        *,
        action: str,
        interval_seconds: int,
        created_by: int,
        enabled: bool = True,
        target: Mapping[str, Any] | None = None,
        schedule_id: str | None = None,
    ) -> ScheduleView:
        """Create or update one interval job.

        ``sync_source`` jobs are derived from data-source bindings and use a
        deterministic id; ``snapshot_publication`` only refreshes an existing
        public link, never creates one.
        """
        if self._schedule_repo is None:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "scheduling is not configured")
        if action not in SCHEDULE_ACTIONS:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"invalid schedule action: {action!r}")
        interval = int(interval_seconds)
        if interval < 1 or interval > MAX_SCHEDULE_SECONDS:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"interval_seconds must be 1..{MAX_SCHEDULE_SECONDS}",
            )
        existing = self._schedule_repo.get(schedule_id) if schedule_id else None
        if schedule_id and (existing is None or existing.app_id != row.app_id):
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        if existing is not None and existing.action != action:
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, "schedule action cannot be changed")

        if action == SCHEDULE_ACTION_SYNC_SOURCE:
            source_id = str((target or {}).get("source_id") or "")
            source = self._app_repo.get_source(row.app_id, source_id)
            if source is None:
                raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
            derived_id = f"sch_src_{source_id}"
            if schedule_id and schedule_id != derived_id:
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, "source schedules use a derived id")
            derived = self._schedule_repo.get(derived_id)
            if derived is None and not self._has_schedule_capacity(row.app_id):
                raise OctopError.localized(ErrorCode.APP_LIMIT_EXCEEDED)
            item = self._schedule_repo.upsert_source(
                app_id=row.app_id,
                source_id=source_id,
                interval_seconds=interval,
                created_by=created_by,
                enabled=enabled,
            )
            self._app_repo.set_source_schedule(source_id, interval if enabled else 0)
            return self._schedule_view(item)

        publication = self._app_repo.get_publication(row.app_id)
        if publication is None or publication.revoked_at is not None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "publish the app before scheduling a snapshot refresh",
            )
        if existing is None:
            existing = next(
                (
                    item
                    for item in self._schedule_repo.list_for_app(row.app_id)
                    if item.action == SCHEDULE_ACTION_SNAPSHOT_PUBLICATION
                ),
                None,
            )
        if existing is not None:
            updated = self._schedule_repo.update(
                existing.schedule_id,
                interval_seconds=interval,
                enabled=enabled,
            )
            if updated is None:
                raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
            return self._schedule_view(updated)
        if not self._has_schedule_capacity(row.app_id):
            raise OctopError.localized(ErrorCode.APP_LIMIT_EXCEEDED)
        item = self._schedule_repo.create(
            app_id=row.app_id,
            action=SCHEDULE_ACTION_SNAPSHOT_PUBLICATION,
            interval_seconds=interval,
            created_by=created_by,
            enabled=enabled,
            target={},
        )
        return self._schedule_view(item)

    def update_schedule(
        self,
        row: AppRecord,
        schedule_id: str,
        *,
        interval_seconds: int,
        enabled: bool,
    ) -> ScheduleView:
        if self._schedule_repo is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        item = self._schedule_repo.get(schedule_id)
        if item is None or item.app_id != row.app_id:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        interval = int(interval_seconds)
        if interval < 1 or interval > MAX_SCHEDULE_SECONDS:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                f"interval_seconds must be 1..{MAX_SCHEDULE_SECONDS}",
            )
        updated = self._schedule_repo.update(
            schedule_id,
            interval_seconds=interval,
            enabled=enabled,
        )
        if updated is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        if item.action == SCHEDULE_ACTION_SYNC_SOURCE:
            source_id = str(item.target.get("source_id") or "")
            if source_id:
                self._app_repo.set_source_schedule(source_id, interval if enabled else 0)
        return self._schedule_view(updated)

    def delete_schedule(self, row: AppRecord, schedule_id: str) -> None:
        if self._schedule_repo is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        item = self._schedule_repo.get(schedule_id)
        if item is None or item.app_id != row.app_id:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        self._schedule_repo.delete(row.app_id, schedule_id)
        if item.action == SCHEDULE_ACTION_SYNC_SOURCE:
            source_id = str(item.target.get("source_id") or "")
            if source_id:
                self._app_repo.set_source_schedule(source_id, 0)

    def _has_schedule_capacity(self, app_id: str) -> bool:
        assert self._schedule_repo is not None
        return self._schedule_repo.count_for_app(app_id) < MAX_SCHEDULES_PER_APP

    @staticmethod
    def _schedule_view(item: LightAppScheduleRow) -> ScheduleView:
        return ScheduleView(
            schedule_id=item.schedule_id,
            app_id=item.app_id,
            action=item.action,
            target=item.target,
            interval_seconds=item.interval_seconds,
            next_run_at=item.next_run_at,
            last_run_at=item.last_run_at,
            last_status=item.last_status,
            last_error=item.last_error,
            failure_count=item.failure_count,
            enabled=item.enabled,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    async def execute_schedule(
        self, row: AppRecord, action: str, target: Mapping[str, Any]
    ) -> None:
        """Run one schedule action; bookkeeping stays in this service."""
        if action == SCHEDULE_ACTION_SYNC_SOURCE:
            source_id = str(target.get("source_id") or "")
            if not source_id:
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, "sync_source needs a source_id")
            await self.run_source(row, source_id)
            return
        if action == SCHEDULE_ACTION_SNAPSHOT_PUBLICATION:
            await self.refresh_publication_snapshot(row)
            return
        raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"invalid schedule action: {action!r}")

    async def refresh_publication_snapshot(self, row: AppRecord) -> int:
        """Refresh the frozen HTML/table snapshot of an existing public link."""
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            publication = self._app_repo.get_publication(row.app_id)
            if publication is None or publication.revoked_at is not None:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    "no active public link to snapshot",
                )
            ws = await self._workspace(row.agent_id)
            html, tables_json = await self._snapshot_parts(row, ws)
            now = int(time.time())
            self._app_repo.set_publication_snapshot(
                row.app_id,
                html=html,
                tables_json=tables_json,
                digest=hashlib.sha256((html + "\0" + tables_json).encode("utf-8")).hexdigest(),
                snapshot_at=now,
            )
            return now

    async def run_source(self, row: AppRecord, source_id: str) -> ImportResult:
        """Run one binding now and record the outcome.

        Raises on failure after recording ``last_status='error'`` and
        re-scheduling (exponential backoff) so the auto-sync scheduler and
        manual triggers share the same bookkeeping.
        """
        item = self._app_repo.get_source(row.app_id, source_id)
        if item is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        run_at = int(self._clock())
        try:
            params = json.loads(item.params_json)
            if not isinstance(params, dict):
                params = {}
            raw_upsert = params.get("upsert_key")
            upsert_key = (
                [str(value) for value in raw_upsert]
                if isinstance(raw_upsert, list) and raw_upsert
                else None
            )
            include_pii = params.get("include_pii") is True
            if item.kind == SOURCE_KIND_WORKSPACE_FILE:
                result = await self.import_from_file(
                    row,
                    path=str(params.get("path") or ""),
                    table=item.table_key,
                    mode=item.mode,
                    mapping=json.loads(item.mapping_json),
                    upsert_key=upsert_key,
                    include_pii=include_pii,
                    created_by=item.created_by,
                    idempotency_source=item.source_id,
                    record_history=False,
                )
            elif item.kind == SOURCE_KIND_EXTERNAL:
                result = await self._run_external_source(
                    row,
                    item,
                    params,
                    upsert_key=upsert_key,
                    include_pii=include_pii,
                    idempotency_source=item.source_id,
                    record_history=False,
                )
            else:
                raise _invalid(
                    ErrorCode.APP_SOURCE_INVALID,
                    f"invalid source kind: {item.kind!r}",
                )
        except OctopError as exc:
            self._record_run(item, status=SOURCE_STATUS_ERROR, run_at=run_at, error=exc.message)
            raise
        except Exception as exc:
            self._record_run(item, status=SOURCE_STATUS_ERROR, run_at=run_at, error=str(exc))
            raise _invalid(ErrorCode.APP_SOURCE_INVALID, f"source run failed: {exc}") from exc
        self._record_run(
            item,
            status=SOURCE_STATUS_OK,
            run_at=run_at,
            error="",
            row_count=result.imported,
        )
        return result

    def _record_run(
        self,
        item: LightAppSourceRow,
        *,
        status: str,
        run_at: int,
        error: str = "",
        row_count: int | None = None,
    ) -> None:
        """Record the run outcome and the next scheduled attempt.

        Re-arm from completion time, not start time, so a run longer than its
        interval cannot create a backlog of immediately-due executions. A
        deterministic jitter spreads schedules that would otherwise fire in
        the same scan. Failures back off exponentially from the interval,
        capped at ``MAX_BACKOFF_SECONDS``; sources without a schedule get
        ``next_run_at`` written too, but the scan ignores them.
        """
        # Re-arm from the finish time, not the start: a run longer than its
        # interval must not create a backlog of immediately-due executions.
        finished_at = max(run_at, int(self._clock()))
        jitter = (
            schedule_jitter(f"sch_src_{item.source_id}", item.schedule_seconds)
            if item.schedule_seconds > 0
            else 0
        )
        if status == SOURCE_STATUS_OK or item.schedule_seconds <= 0:
            fail_count = 0
            next_run_at = finished_at + max(item.schedule_seconds, 0) + jitter
        else:
            fail_count = item.fail_count + 1
            delay = min(item.schedule_seconds * 2 ** (fail_count - 1), MAX_BACKOFF_SECONDS)
            next_run_at = finished_at + delay + jitter
        self._app_repo.set_source_run_state(
            item.source_id,
            status=status,
            run_at=run_at,
            error=error,
            row_count=row_count,
            next_run_at=next_run_at,
            fail_count=fail_count,
        )
        if self._schedule_repo is not None:
            self._schedule_repo.record_run(
                f"sch_src_{item.source_id}",
                run_at=run_at,
                status=status,
                next_run_at=next_run_at,
                failure_count=fail_count,
                error=error,
            )

    async def _run_external_source(
        self,
        row: AppRecord,
        item: Any,
        params: dict[str, Any],
        *,
        upsert_key: list[str] | None = None,
        include_pii: bool = False,
        idempotency_source: str | None = None,
        record_history: bool = True,
    ) -> ImportResult:
        if self._external_tool_runner is None:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "external data sources are not available on this server",
            )
        instance_id = str(params.get("instance_id") or "")
        tool = str(params.get("tool") or "")
        if not instance_id or not tool:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "external source needs instance_id and tool",
            )
        raw_args = params.get("args")
        args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
        result_path = str(params.get("result_path") or "").strip()
        cursor_arg = str(params.get("cursor_arg") or "").strip()
        cursor_path = str(params.get("cursor_path") or "").strip()
        cursor = params.get("cursor")
        if cursor_arg and cursor not in (None, ""):
            args[cursor_arg] = cursor
        output = await asyncio.to_thread(
            self._external_tool_runner.run,
            instance_id=instance_id,
            tool=tool,
            args={str(k): v for k, v in args.items()},
        )
        records = _extract_records(output, result_path)
        result = await self.import_rows(
            row,
            records,
            table=item.table_key,
            mode=item.mode,
            mapping=json.loads(item.mapping_json),
            upsert_key=upsert_key,
            include_pii=include_pii,
            created_by=item.created_by,
            record_history=record_history,
            client_request_id_prefix=(
                _batch_prefix(
                    idempotency_source,
                    records,
                    table=item.table_key,
                    mapping=json.loads(item.mapping_json),
                    upsert_key=upsert_key,
                    include_pii=include_pii,
                )
                if idempotency_source
                else None
            ),
        )
        if cursor_path:
            try:
                payload = json.loads(output)
            except json.JSONDecodeError:
                payload = None
            next_cursor = _json_path(payload, cursor_path)
            if next_cursor not in (None, ""):
                updated = dict(params)
                updated["cursor"] = str(next_cursor)[:1000]
                self._app_repo.set_source_params(
                    item.source_id,
                    json.dumps(updated, ensure_ascii=False),
                )
        return result

    def _validate_source_params(
        self,
        kind: str,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        if kind == SOURCE_KIND_WORKSPACE_FILE:
            try:
                path = _safe_import_path(str(params.get("path") or ""))
            except OctopError as exc:
                # A bad path inside a source binding reads as bad config.
                raise _invalid(ErrorCode.APP_SOURCE_INVALID, exc.message) from None
            clean_params: dict[str, Any] = {"path": path}
            upsert_key = self._clean_source_upsert_key(params)
            if upsert_key:
                clean_params["upsert_key"] = upsert_key
            if params.get("include_pii") is True:
                clean_params["include_pii"] = True
            return clean_params
        instance_id = str(params.get("instance_id") or "").strip()
        tool = str(params.get("tool") or "").strip()
        if not instance_id or not tool:
            raise _invalid(
                ErrorCode.APP_SOURCE_INVALID,
                "external source needs instance_id and tool",
            )
        args = params.get("args")
        result_path = str(params.get("result_path") or "").strip()
        clean: dict[str, Any] = {
            "instance_id": instance_id[:200],
            "tool": tool[:200],
        }
        if isinstance(args, dict) and args:
            clean["args"] = {str(k): v for k, v in list(args.items())[:50]}
        if result_path:
            clean["result_path"] = result_path[:200]
        for key in ("cursor_arg", "cursor_path"):
            value = str(params.get(key) or "").strip()
            if value:
                clean[key] = value[:100]
        cursor = params.get("cursor")
        if isinstance(cursor, str) and cursor:
            clean["cursor"] = cursor[:1000]
        upsert_key = self._clean_source_upsert_key(params)
        if upsert_key:
            clean["upsert_key"] = upsert_key
        if params.get("include_pii") is True:
            clean["include_pii"] = True
        return clean

    @staticmethod
    def _clean_source_upsert_key(params: Mapping[str, Any]) -> list[str]:
        raw = params.get("upsert_key")
        if not isinstance(raw, list):
            return []
        return [str(value).strip()[:100] for value in raw[:20] if str(value).strip()]

    def _source_view(self, item: LightAppSourceRow) -> SourceView:
        try:
            params = json.loads(item.params_json)
            if not isinstance(params, dict):
                params = {}
        except json.JSONDecodeError:
            params = {}
        try:
            mapping = json.loads(item.mapping_json)
            if not isinstance(mapping, dict):
                mapping = {}
        except json.JSONDecodeError:
            mapping = {}
        return SourceView(
            source_id=item.source_id,
            app_id=item.app_id,
            table_key=item.table_key,
            kind=item.kind,
            mode=item.mode,
            schedule_seconds=item.schedule_seconds,
            params=params,
            mapping={str(k): str(v) for k, v in mapping.items()},
            last_run_at=item.last_run_at,
            last_status=item.last_status,
            last_error=item.last_error or None,
            last_row_count=item.last_row_count,
            created_by=item.created_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    async def add_columns(
        self,
        row: AppRecord,
        additions: object,
        *,
        table: str = MAIN_TABLE_KEY,
    ) -> Schema:
        key = self._require_table(row, table)
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            self._ensure_main_table(row)
            current = self._app_repo.get(row.app_id) or row
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, key)
            if current.storage_version >= 2:
                new_schema = extend_schema(schema, additions)
                added = new_schema.columns[len(schema.columns) :]
                if self._app_repo.count_data_rows(current.app_id, key) and any(
                    col.required for col in added
                ):
                    raise _invalid(
                        ErrorCode.APP_SCHEMA_INVALID,
                        "new columns cannot be required while the app already has rows",
                    )
                self._app_repo.set_table_schema(
                    current.app_id,
                    key,
                    new_schema.to_json(),
                    new_schema.version,
                )
                self._app_repo.touch(current.app_id)
                schema_file, _ = table_file_names(key)
                try:
                    await ws.awrite_text(
                        app_path(current.app_id, schema_file),
                        new_schema.to_json(),
                    )
                except Exception:
                    # The DB copy is authoritative; the workspace schema.json
                    # is only a mirror for the app directory.
                    logger.warning(
                        "failed to refresh schema mirror for %s/%s",
                        current.app_id,
                        key,
                        exc_info=True,
                    )
                await self._after_write(ws, current.app_id)
                return new_schema
            rows = await self._read_rows_file(ws, current.app_id, schema, key)
            new_schema = extend_schema(schema, additions)
            added = new_schema.columns[len(schema.columns) :]
            if rows and any(col.required for col in added):
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    "new columns cannot be required while the app already has rows",
                )
            csv_text = render_csv(new_schema, rows)
            schema_file, data_file = table_file_names(key)
            await self._write_atomic(
                ws,
                current.app_id,
                {
                    schema_file: new_schema.to_json(),
                    data_file: csv_text,
                },
            )
            self._app_repo.touch(current.app_id)
            data_bytes = len(csv_text.encode("utf-8"))
            self._app_repo.set_table_stats(
                current.app_id,
                key,
                row_count=len(rows),
                data_bytes=data_bytes,
            )
            self._app_repo.refresh_app_totals(current.app_id)
            await self._after_write(ws, current.app_id)
            return new_schema

    async def alter_schema(
        self,
        row: AppRecord,
        *,
        table: str = MAIN_TABLE_KEY,
        renames: Mapping[str, str] | None = None,
        drops: list[str] | None = None,
        types: Mapping[str, Mapping[str, Any]] | None = None,
        pii: Mapping[str, str | None] | None = None,
    ) -> Schema:
        """Rename, drop or retype columns, migrating every existing row.

        A change is rejected as a whole when any row cannot be represented by
        the new schema; nothing is half-written. ``pii`` marks or clears the
        sensitive-data kind of existing columns (``None`` clears it).
        """
        key = self._require_table(row, table)
        clean_renames = {str(src): str(dst) for src, dst in (renames or {}).items()}
        clean_drops = {str(name) for name in (drops or [])}
        clean_types = {str(name): dict(spec) for name, spec in (types or {}).items()}
        clean_pii = {
            str(name): (str(kind).strip() if kind not in (None, "") else None)
            for name, kind in (pii or {}).items()
        }
        if not clean_renames and not clean_drops and not clean_types and not clean_pii:
            return await self._read_schema(await self._workspace(row.agent_id), row.app_id, key)
        await self._ensure_db_rows(row)
        async with _lock_for(row.app_id):
            self._require_live(row.app_id)
            current = self._app_repo.get(row.app_id) or row
            ws = await self._workspace(current.agent_id)
            schema = await self._read_schema(ws, current.app_id, key)
            unknown = (set(clean_renames) | clean_drops | set(clean_types) | set(clean_pii)) - set(
                schema.names
            )
            if unknown:
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    f"unknown columns: {', '.join(sorted(unknown))}",
                )
            new_columns = []
            seen: set[str] = set()
            for column in schema.columns:
                if column.name in clean_drops:
                    continue
                name = clean_renames.get(column.name, column.name)
                spec = clean_types.get(column.name, {})
                column_type = str(spec.get("type", column.type))
                payload: dict[str, Any] = {
                    "name": name,
                    "label": spec.get("label", column.label),
                    "type": column_type,
                    "required": spec.get("required", column.required),
                }
                if column_type == "enum":
                    payload["values"] = spec.get("values", list(column.values))
                if column_type == "reference":
                    payload["ref_table"] = spec.get("ref_table", column.ref_table)
                    payload["ref_label"] = spec.get("ref_label", column.ref_label)
                if column.name in clean_pii:
                    if clean_pii[column.name]:
                        payload["pii"] = clean_pii[column.name]
                elif column.pii:
                    payload["pii"] = column.pii
                parsed = parse_schema_payload([payload]).columns[0]
                if parsed.name in seen:
                    raise _invalid(
                        ErrorCode.APP_SCHEMA_INVALID,
                        f"duplicate column name after rename: {parsed.name!r}",
                    )
                seen.add(parsed.name)
                new_columns.append(parsed)
            if not new_columns:
                raise _invalid(
                    ErrorCode.APP_SCHEMA_INVALID,
                    "at least one column must remain",
                )
            new_schema = Schema(columns=tuple(new_columns), version=schema.version + 1)
            errors: list[str] = []
            reference_cache: dict[str, set[str]] = {}
            if current.storage_version >= 2:
                rewritten: list[tuple[str, dict[str, str]]] = []
                for data_row in self._app_repo.list_all_data_rows(current.app_id, key):
                    values = {
                        clean_renames.get(name, name): value
                        for name, value in data_row.values.items()
                        if name not in clean_drops
                    }
                    try:
                        normalized = validate_row(new_schema, values)
                        await self._validate_references(
                            current,
                            new_schema,
                            normalized,
                            cache=reference_cache,
                        )
                        rewritten.append((data_row.row_id, normalized))
                    except OctopError as exc:
                        if len(errors) < MAX_IMPORT_ERRORS:
                            errors.append(f"row {data_row.row_id}: {exc.message}")
                if errors:
                    raise _invalid(
                        ErrorCode.APP_ROW_INVALID,
                        "schema change rejected; existing rows do not fit the new schema",
                        errors=errors,
                    )
                self._app_repo.set_table_schema(
                    current.app_id,
                    key,
                    new_schema.to_json(),
                    new_schema.version,
                )
                self._app_repo.rewrite_data_rows(current.app_id, key, rewritten)
            else:
                rows = await self._read_rows_file(ws, current.app_id, schema, key)
                rewritten_rows: list[dict[str, str]] = []
                for index, values_item in enumerate(rows):
                    values = {
                        clean_renames.get(name, name): value
                        for name, value in values_item.items()
                        if name not in clean_drops
                    }
                    try:
                        rewritten_rows.append(validate_row(new_schema, values))
                    except OctopError as exc:
                        if len(errors) < MAX_IMPORT_ERRORS:
                            errors.append(f"row {index + 1}: {exc.message}")
                if errors:
                    raise _invalid(
                        ErrorCode.APP_ROW_INVALID,
                        "schema change rejected; existing rows do not fit the new schema",
                        errors=errors,
                    )
                await self._persist_rows(ws, current, key, new_schema, rewritten_rows)
            schema_file, _ = table_file_names(key)
            try:
                await ws.awrite_text(
                    app_path(current.app_id, schema_file),
                    new_schema.to_json(),
                )
            except Exception:
                logger.warning(
                    "failed to refresh schema mirror for %s/%s",
                    current.app_id,
                    key,
                    exc_info=True,
                )
            self._app_repo.touch(current.app_id)
            await self._after_write(ws, current.app_id)
            return new_schema

    # ----- sharing -----

    def list_collaborators(self, row: AppRecord) -> list[LightAppCollaboratorRow]:
        return self._app_repo.list_collaborators(row.app_id)

    def list_share_views(self, row: AppRecord) -> list[ShareView]:
        views: list[ShareView] = []
        for collab in self._app_repo.list_collaborators(row.app_id):
            account = self._user_repo.get(collab.user_id)
            if account is None:
                continue
            views.append(self._share_view(account, collab.permission))
        return views

    def set_shares(
        self,
        row: AppRecord,
        shares: list[tuple[str, str]],
    ) -> list[ShareView]:
        """Replace collaborators from username/permission pairs."""
        entries: list[tuple[int, str]] = []
        seen: set[int] = set()
        for username, permission in shares:
            name = username.strip()
            account = self._user_repo.get_member_by_username(name) if name else None
            if account is None or account.disabled:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"unknown or disabled user: {name}",
                )
            if account.id in seen:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"duplicate collaborator: {name}",
                )
            seen.add(account.id)
            entries.append((account.id, permission))
        cleaned = self._clean_collaborators(row, entries)
        # Guests are managed by the member APIs / join flow, not by the legacy
        # platform share dialog. Keep them across a full member replacement.
        guest_entries: list[tuple[int, str]] = []
        for collaborator in self._app_repo.list_collaborators(row.app_id):
            account = self._user_repo.get(collaborator.user_id)
            if account is not None and account.kind == "guest":
                guest_entries.append((collaborator.user_id, collaborator.permission))
        if len(guest_entries) > MAX_GUESTS:
            raise _invalid(
                ErrorCode.APP_LIMIT_EXCEEDED,
                f"at most {MAX_GUESTS} guest members",
            )
        combined = guest_entries + cleaned
        self._app_repo.set_collaborators(row.app_id, combined)
        self._app_repo.touch(row.app_id)
        return self.list_share_views(row)

    def _clean_collaborators(
        self,
        row: AppRecord,
        entries: list[tuple[int, str]],
    ) -> list[tuple[int, str]]:
        cleaned: list[tuple[int, str]] = []
        seen: set[int] = set()
        members = 0
        guests = 0
        for user_id, permission in entries:
            if user_id == row.owner_user_id:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    "the owner always has full access and cannot be a collaborator",
                )
            if user_id in seen:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    "each collaborator can appear only once",
                )
            account = self._user_repo.get(user_id)
            if account is None or account.disabled:
                raise _invalid(
                    ErrorCode.APP_SHARE_INVALID,
                    f"unknown or disabled user: {user_id}",
                )
            if account.kind == "guest":
                if permission != "participate":
                    raise _invalid(
                        ErrorCode.APP_SHARE_INVALID,
                        "guests can only hold participate permission",
                    )
                guests += 1
            else:
                permission = validate_collaborator_permission(permission)
                members += 1
            if members > MAX_COLLABORATORS:
                raise _invalid(
                    ErrorCode.APP_LIMIT_EXCEEDED,
                    f"at most {MAX_COLLABORATORS} members",
                )
            if guests > MAX_GUESTS:
                raise _invalid(
                    ErrorCode.APP_LIMIT_EXCEEDED,
                    f"at most {MAX_GUESTS} guest members",
                )
            seen.add(user_id)
            cleaned.append((user_id, permission))
        return cleaned

    def set_collaborators(
        self,
        row: AppRecord,
        entries: list[tuple[int, str]],
    ) -> list[LightAppCollaboratorRow]:
        cleaned = self._clean_collaborators(row, entries)
        self._app_repo.set_collaborators(row.app_id, cleaned)
        self._app_repo.touch(row.app_id)
        return self._app_repo.list_collaborators(row.app_id)

    # ----- presentation helpers -----

    @staticmethod
    def _share_view(account: UserRow, permission: str) -> ShareView:
        return ShareView(
            user_id=account.id,
            username=account.username,
            display_name=account.display_name or account.username,
            permission=permission,
        )
