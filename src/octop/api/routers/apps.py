"""HTTP API for agent-built MiniApps.

Apps are created and edited by agents through built-in tools; this router only
exposes them so the dashboard and collaborators can render the UI and append
rows. Ownership and sharing are enforced through ``AppService``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from octop.api.common.agent import require_agent_row
from octop.api.common.upload_limit import read_upload_capped
from octop.api.deps import current_user, get_server
from octop.api.routers.chat.sse import format_sse
from octop.config import DEFAULT_MAX_UPLOAD_MB, upload_mb_to_bytes
from octop.infra.apps import capabilities as app_capabilities
from octop.infra.apps.access import ROLE_OWNER, Action, Decision, can_view_pii
from octop.infra.apps.assistant import AppAssistant
from octop.infra.apps.dataset import BuiltinDatasetService
from octop.infra.apps.game_sessions import create_game_token
from octop.infra.apps.generation import AppGenerator
from octop.infra.apps.ingress import AppIngressService, IngressView
from octop.infra.apps.limits import (
    acquire_app_llm,
    app_llm_slot,
    csp_violation_slot,
    release_app_llm,
)
from octop.infra.apps.models import MAIN_TABLE_KEY, Schema
from octop.infra.apps.preview import validate_preview_svg
from octop.infra.apps.sandbox import sanitize_blocked_uri
from octop.infra.apps.service import (
    IMPORT_MODE_REPLACE,
    MAX_ASSET_BYTES,
    MAX_BACKUP_BYTES,
    SCHEDULE_ACTION_SNAPSHOT_PUBLICATION,
    AppRecord,
    AppService,
    AppTableView,
    ImportResult,
    ScheduleView,
    ServiceView,
    SourceView,
)
from octop.infra.apps.session import app_card_ids
from octop.infra.apps.sources import GatewayToolRunner
from octop.infra.apps.task_types import list_task_types
from octop.infra.apps.tasks import AppTaskService
from octop.infra.apps.webhooks import AppWebhookService
from octop.infra.apps.wiring import (
    build_app_service,
    build_ingress_service,
    build_webhook_service,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.users.principals import principal_of
from octop.infra.utils.locale import Locale, resolve_request_locale
from octop.infra.utils.ulid import new_short_id

router = APIRouter(prefix="/apps")

# Every route below must declare its access action in
# ``app_route_actions.APP_ROUTE_ACTIONS``; the registry test fails otherwise.

SharePermission = Literal["view", "edit_data", "edit_structure"]


class AppColumn(BaseModel):
    name: str = Field(description="Machine name used as the CSV column and row key.")
    label: str = Field(description="Human-readable column label.")
    type: str = Field(description="Value type (string, integer, boolean, date, enum, …).")
    required: bool = Field(default=False, description="Whether the column must be non-empty.")
    values: list[str] = Field(default_factory=list, description="Allowed values for enum columns.")
    pii: str | None = Field(
        default=None,
        description="Sensitive-data kind; values are masked for viewers and public links.",
    )
    ref_table: str | None = Field(
        default=None,
        description="Target table key for a reference column.",
    )
    ref_label: str | None = Field(
        default=None,
        description="Target column used as the display label for a reference.",
    )


class RestoreIssueOut(BaseModel):
    severity: Literal["error", "warning"] = Field(
        description="Errors block restore; warnings mark checks that will be skipped or need follow-up."
    )
    code: str = Field(description="Stable machine-readable issue code.")
    component: str = Field(
        description="Archive part that failed the check: archive, table, source, schedule, "
        "webhook, ingress, service or integration."
    )
    name: str = Field(default="", description="Declaration name, when the component has one.")
    message: str = Field(description="Human-readable description of the problem.")
    action: str = Field(default="", description="What the owner should do about it.")


class RestorePreflightOut(BaseModel):
    ok: bool = Field(description="True when no errors or warnings were found.")
    can_restore: bool = Field(
        description="Whether the archive may be imported without blocking errors."
    )
    error_count: int = Field(default=0, description="Number of blocking issues.")
    warning_count: int = Field(default=0, description="Number of non-blocking issues.")
    issues: list[RestoreIssueOut] = Field(
        default_factory=list,
        description="Blocking errors first, then warnings, each with an owner action.",
    )
    summary: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of scanned tables, sources, ingress, webhooks, services and schedules.",
    )


class AppSummary(BaseModel):
    app_id: str
    name: str
    description: str
    agent_id: str = Field(description="Agent that owns the app workspace.")
    agent_name: str = ""
    agent_owner_user_id: int | None = Field(
        default=None,
        description="Owner of the hosting agent; may approve external participants.",
    )
    owner_user_id: int
    owner_username: str = ""
    permission: str = Field(description="Caller's permission: view, edit_data or edit_structure.")
    row_count: int = Field(description="Rows across every table of the app.")
    html_bytes: int = Field(default=0, description="Size of the generated app.html in bytes.")
    data_bytes: int = Field(
        default=0,
        description="Size of the app's CSV files in bytes, summed over every table.",
    )
    has_html: bool = Field(default=False, description="Whether the agent has written the UI yet.")
    has_preview: bool = Field(
        default=False, description="Whether the agent drew a preview image for the app."
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Page-facing capabilities the MiniApp may request from the host.",
    )
    previous_html_at: int | None = Field(
        default=None,
        description="Unix time of the last replaced HTML, when a restore is available.",
    )
    created_at: int
    updated_at: int


class AppFileOut(BaseModel):
    name: str = Field(description="File name inside the app directory.")
    path: str = Field(description="Workspace-relative path of the file.")
    size: int | None = Field(
        default=None, description="File size in bytes, when the backend reports it."
    )


class AppTableOut(BaseModel):
    table_key: str = Field(description="Machine key of the table.")
    label: str = Field(default="", description="Display name of the table.")
    row_count: int = Field(default=0, description="Rows currently stored in the table.")
    data_version: int = Field(
        default=0,
        description="Monotonic token bumped on every row write; poll it for refreshes.",
    )
    is_main: bool = Field(default=False, description="Whether this is the app's main table.")
    public_visible: bool = Field(
        default=True,
        description="Whether the table is exposed by the app's public read-only link.",
    )
    member_visible: bool = Field(
        default=False,
        description="Whether app members with participate permission may see this table.",
    )
    row_scope: str = Field(
        default="app",
        description="'app' shares rows with collaborators; 'user' isolates them per user.",
    )
    rules: dict[str, Any] = Field(
        default_factory=dict,
        description="Declarative table rules; currently a state machine.",
    )
    columns: list[AppColumn] = Field(
        default_factory=list,
        description="Column definitions of the table.",
    )


class AppTablesResponse(BaseModel):
    tables: list[AppTableOut]


class AppTableVersionOut(BaseModel):
    table_key: str = Field(description="Machine key of the table.")
    data_version: int = Field(description="Monotonic token bumped on every row write.")
    row_count: int = Field(description="Rows currently stored in the table.")


class AppDataVersionOut(BaseModel):
    tables: list[AppTableVersionOut]


class DatasetColumnOut(BaseModel):
    name: str
    label: str
    type: str
    required: bool
    pii: str | None = None


class DatasetTableOut(BaseModel):
    table_key: str
    label: str
    row_count: int
    is_main: bool
    public_visible: bool
    row_scope: str
    columns: list[DatasetColumnOut]


class DatasetOut(BaseModel):
    app_id: str
    service_id: str
    operations: list[str]
    row_scope: str = Field(description="App-wide default; each table reports its own row_scope.")
    tables: list[DatasetTableOut]


class DatasetQueryBody(BaseModel):
    table: str = Field(default=MAIN_TABLE_KEY, description="Logical table key.")
    limit: int = Field(default=200, ge=1, le=500, description="Rows to return.")
    offset: int = Field(default=0, ge=0, description="Rows to skip.")


class ImportDatasetBody(BaseModel):
    agent_id: str = Field(description="Target agent that will host the imported app.")
    dataset: dict[str, Any] = Field(description="Output of GET /apps/{id}/dataset/export.")


class DatasetQueryOut(BaseModel):
    table: str
    columns: list[DatasetColumnOut]
    rows: list[dict[str, str]]
    total: int
    limit: int
    offset: int
    pii_columns: list[str]
    masked_pii_rows: list[str] = Field(
        default_factory=list,
        description="Row ids whose sensitive values are masked for this caller.",
    )


class BindingOut(BaseModel):
    binding_id: str
    provider: str
    service_id: str
    config: dict[str, Any]
    enabled: bool
    version: int
    builtin: bool


class BindingsResponse(BaseModel):
    bindings: list[BindingOut]


class SaveBindingBody(BaseModel):
    provider: str = Field(description="builtin, connector or external.")
    service_id: str = Field(description="Service identifier; builtin is builtin.dataset.")
    config: dict[str, Any] = Field(default_factory=dict, description="Provider-specific config.")
    enabled: bool = Field(default=True, description="Whether the binding is active.")


class AppServiceOut(BaseModel):
    service_id: str = Field(description="Public service declaration id.")
    name: str = Field(description="Page-facing name used by octopApp.service().")
    provider: str = Field(description="connector or external.")
    connector_instance_id: str = Field(description="Connector instance that runs the tool.")
    allowed_tools: list[str] = Field(description="Read-only tools the page may call.")
    allowed_url_prefixes: list[str] = Field(
        description="URL prefixes accepted for tools that fetch a URL."
    )
    request_headers: dict[str, str] = Field(
        description="Allowlisted request headers sent by the public HTTP adapter."
    )
    cache_ttl_seconds: int = Field(description="Response cache TTL; 0 disables caching.")
    max_calls_per_minute: int = Field(description="Live call budget per minute.")
    public_live: bool = Field(description="Reserved for public-link live calls.")
    enabled: bool = Field(description="Whether the page may call this service.")
    created_at: int
    updated_at: int


class AppServicesResponse(BaseModel):
    services: list[AppServiceOut]


class SaveAppServiceBody(BaseModel):
    provider: Literal["connector", "external"] = Field(
        description="connector: authenticated connector; external: public source."
    )
    connector_instance_id: str = Field(
        default="",
        description=(
            "Existing connector instance id. Leave empty for a credential-free "
            "public source; the server reuses or auto-creates a connector."
        ),
    )
    connector_kind: str | None = Field(
        default=None,
        description=(
            "Connector kind to auto-provision when connector_instance_id is "
            "empty (http-extract by default, or douban for book metadata)."
        ),
    )
    allowed_tools: list[str] = Field(
        min_length=1,
        description="Read-only tool names the page may call.",
    )
    allowed_url_prefixes: list[str] = Field(
        default_factory=list,
        description="Required for URL-fetching tools; http(s) prefixes only.",
    )
    request_headers: dict[str, str] | None = Field(
        default=None,
        description=(
            "Allowlisted headers for http-extract: User-Agent, Referer, Accept, "
            "Accept-Language. Omit to keep the stored value on update."
        ),
    )
    cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    max_calls_per_minute: int = Field(default=30, ge=1, le=600)
    public_live: bool = Field(
        default=False,
        description="Reserved for the R2.1b public-link live feature.",
    )
    enabled: bool = Field(default=True)


class ServiceCallBody(BaseModel):
    tool: str = Field(description="Tool name; must be in the declaration allowlist.")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments; URL fetches must match allowed_url_prefixes.",
    )


class ServiceCallOut(BaseModel):
    value: Any = Field(description="Tool result; JSON when the adapter returns JSON.")
    cached: bool = Field(description="Whether the result came from the TTL cache.")
    expires_at: int | None = Field(
        default=None,
        description="Cache expiry, when the result was cached.",
    )


class ServiceImageBody(BaseModel):
    url: str = Field(description="Public image URL inside the service's allowed prefixes.")


class ServiceImageOut(BaseModel):
    asset: str = Field(description="Stored asset name; pass it to octopApp.asset().")


class AppEventOut(BaseModel):
    event_id: str
    event_type: str
    source: str
    summary: str
    payload: dict[str, Any]
    created_at: int
    acknowledged_at: int | None


class AppEventsResponse(BaseModel):
    events: list[AppEventOut]
    latest_id: int


class AckEventsBody(BaseModel):
    event_ids: list[str] = Field(
        default_factory=list,
        description="Event ids to mark acknowledged (max 200).",
    )


class AckEventsOut(BaseModel):
    acknowledged: int


class WebhookOut(BaseModel):
    webhook_id: str
    name: str
    url: str
    event_types: list[str] = Field(description="Exact event types or patterns like order.* / *.")
    enabled: bool
    created_at: int
    updated_at: int


class WebhookCreatedOut(WebhookOut):
    secret: str = Field(description="HMAC signing secret; shown once at creation/rotation.")


class WebhookUpdatedOut(WebhookOut):
    secret: str | None = Field(
        default=None,
        description="New signing secret when rotate_secret was requested.",
    )


class CreateWebhookBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(description="http(s) destination URL.")
    event_types: list[str] = Field(
        min_length=1,
        description="Exact types or patterns (order.*, *).",
    )
    enabled: bool = True


class UpdateWebhookBody(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    url: str | None = None
    event_types: list[str] | None = None
    enabled: bool | None = None
    rotate_secret: bool = False


class DeleteWebhookOut(BaseModel):
    deleted: bool


class IngressOut(BaseModel):
    ingress_id: str
    name: str
    mode: str = Field(description="Subscription mode: app.inbox, app.import or app.refresh.")
    event_type: str = Field(description="Optional exact event type filter.")
    mapping: dict[str, Any] = Field(
        description="Subscription mapping; app.import uses table/columns, app.refresh uses source_id."
    )
    max_per_minute: int
    enabled: bool
    source_id: str
    hook_url: str = Field(description="Absolute or relative webhook URL for senders.")
    created_at: int
    updated_at: int


class IngressCreatedOut(IngressOut):
    secret: str = Field(description="HMAC signing secret; shown once at creation.")


class IngressSecretOut(BaseModel):
    secret: str = Field(description="New HMAC signing secret; shown once.")
    hook_url: str


class CreateIngressBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mode: str = Field(description="app.inbox, app.import or app.refresh.")
    event_type: str = Field(default="", max_length=100)
    mapping: dict[str, Any] = Field(default_factory=dict)
    max_per_minute: int = Field(default=0, ge=0, le=600)
    enabled: bool = True


class UpdateIngressBody(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    mode: str | None = None
    event_type: str | None = Field(default=None, max_length=100)
    mapping: dict[str, Any] | None = None
    max_per_minute: int | None = Field(default=None, ge=0, le=600)
    enabled: bool | None = None


class TestIngressBody(BaseModel):
    event_type: str = Field(default="", max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class TestIngressOut(BaseModel):
    event_id: str
    event_type: str
    deliveries: int
    rate_limited: int


class EmitEventBody(BaseModel):
    event_type: str = Field(description="Event type such as order.approved.")
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(default="", max_length=500)
    client_event_id: str | None = Field(
        default=None,
        max_length=200,
        description="Optional idempotency key; a retry with the same id is deduped.",
    )


class EmitWebhookResult(BaseModel):
    webhook_id: str
    event_id: str = Field(description="Stored external-event id for this webhook.")
    duplicate: bool
    deliveries: int
    rate_limited: int


class EmitEventOut(BaseModel):
    external_id: str = Field(description="Dedupe key for the emitted event.")
    emitted: int
    duplicates: int
    deliveries: int
    rate_limited: int
    webhooks: list[EmitWebhookResult]


class DeleteBindingResponse(BaseModel):
    deleted: str


class TableVisibilityBody(BaseModel):
    public_visible: bool = Field(
        description="Expose this table on the app's public read-only link."
    )
    member_visible: bool | None = Field(
        default=None,
        description=(
            "Whether app members with participate permission may see this table. "
            "Leave unset to keep the current value."
        ),
    )
    row_scope: Literal["app", "user"] | None = Field(
        default=None,
        description=(
            "Who sees rows inside the app. 'app' shares them with every "
            "collaborator; 'user' shows each normal user only the rows they "
            "created. Owner/admin always sees every row. Public exposure is "
            "controlled separately by ``public_visible``; there is no "
            "'published' row scope, and 'user' tables never appear on the "
            "public link."
        ),
    )


class TableRulesBody(BaseModel):
    rules: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Declarative rules. v1 supports "
            '{"state_field": "status", "transitions": {"new": ["done"]}}.'
        ),
    )


class AppFilesResponse(BaseModel):
    files: list[AppFileOut]


class AppListResponse(BaseModel):
    apps: list[AppSummary]


class AppDetail(AppSummary):
    columns: list[AppColumn] = Field(description="Columns of the main table, in display order.")
    has_html: bool = Field(description="Whether the agent has written app.html yet.")
    tables: list[AppTableOut] = Field(
        default_factory=list,
        description="Tables of the app; the first one is the main table.",
    )


class FailedRowOut(BaseModel):
    row: int = Field(description="1-based row number in the source file.")
    values: dict[str, str] = Field(description="Mapped values that failed validation.")
    error: str = Field(description="Why the row was rejected.")


class QuickCreateResponse(BaseModel):
    app: AppDetail
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    pii_columns: list[str] = Field(default_factory=list)
    blocked_pii: list[str] = Field(default_factory=list)
    failed_rows: list[FailedRowOut] = Field(default_factory=list)


class GameSessionBody(BaseModel):
    game_id: str = Field(description="Configured external game id.")


class GameSessionOut(BaseModel):
    session_id: str
    game_id: str
    name: str
    url: str = Field(description="External game entry URL.")
    token: str = Field(description="Short-lived token for the result endpoint.")
    expires_at: int = Field(description="Unix expiry for the token.")
    sandbox: str = Field(description="Iframe sandbox policy for this game.")


class SecurityEventIn(BaseModel):
    """One security event reported by a sandboxed MiniApp page."""

    kind: Literal["csp_violation", "sandbox_integrity"] = Field(
        description="Event kind: a CSP violation or a failed sandbox-integrity check.",
    )
    directive: str = Field(
        default="",
        max_length=200,
        description="CSP directive that was violated (e.g. `connect-src`).",
    )
    blocked_uri: str = Field(
        default="",
        max_length=2000,
        description="URI the browser blocked. Query strings are stripped before audit.",
    )
    disposition: str = Field(
        default="",
        max_length=32,
        description="CSP disposition: `enforce` or `report`.",
    )


class RowMeta(BaseModel):
    row_id: str = Field(
        default="",
        description="Stable row id for update/delete; empty for legacy CSV apps.",
    )
    version: int = Field(default=1, description="Row version for optimistic concurrency.")
    source: str = Field(
        default="manual",
        description="How the row got here: manual, import, migration, connector, agent.",
    )
    pii_masked: bool = Field(
        default=False,
        description="True when this row's sensitive values are masked for the caller.",
    )


class AppRowsResponse(BaseModel):
    columns: list[AppColumn]
    rows: list[dict[str, str]]
    row_meta: list[RowMeta] = Field(
        default_factory=list,
        description="Parallel to rows: row id and version for editing.",
    )
    total: int
    limit: int
    offset: int
    table: str = Field(default="main", description="Table the rows came from.")
    pii_columns: list[str] = Field(
        default_factory=list,
        description="Sensitive columns whose values are masked for this caller.",
    )
    masked_pii_rows: list[str] = Field(
        default_factory=list,
        description="Row ids whose sensitive values are masked for this caller.",
    )
    refs: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "Reference labels keyed by column then row id, when expand_refs=true. "
            "Dangling or invisible targets are omitted."
        ),
    )


class AppendRowBody(BaseModel):
    row: dict[str, Any] = Field(description="One row keyed by column name.")
    table: str = Field(default="main", description="Table to append to.")
    client_request_id: str | None = Field(
        default=None,
        max_length=200,
        description="Idempotency key; retrying with the same id does not duplicate the row.",
    )


class AppendRowResponse(BaseModel):
    row_index: int = Field(description="Zero-based index of the appended row.")
    row: dict[str, str]
    table: str = Field(default="main", description="Table the row was appended to.")
    row_id: str = Field(default="", description="Stable row id for later updates.")
    version: int = Field(default=1, description="Version of the appended row.")


class UpdateRowBody(BaseModel):
    row: dict[str, Any] = Field(description="Complete row keyed by column name.")
    expected_version: int | None = Field(
        default=None,
        description="Reject the update when the stored version differs.",
    )


class UpdateRowResponse(BaseModel):
    row_id: str
    row: dict[str, str]
    version: int


class DeleteRowResponse(BaseModel):
    deleted: str


class RowHistoryEntry(BaseModel):
    history_id: int
    table: str = Field(description="Table the row belongs to.")
    row_id: str
    version: int = Field(description="Row version at the time of the change.")
    action: str = Field(description="update, delete or replace.")
    actor: str = Field(description="user:<id> or _system.")
    source: str = Field(default="", description="manual, agent, import or external.")
    changed_at: int = Field(description="Unix timestamp of the change.")
    before: dict[str, str] | None = Field(
        default=None,
        description="Values before the change; null when no prior state exists.",
    )
    after: dict[str, str] | None = Field(
        default=None,
        description="Values after the change; null for deletes and replace-imports.",
    )


class RowHistoryResponse(BaseModel):
    entries: list[RowHistoryEntry]
    limit: int
    offset: int


class AggregateGroup(BaseModel):
    key: str = Field(description="Group value; empty string for a blank group.")
    value: float = Field(description="Aggregated value (count for agg=count).")
    count: int = Field(description="Rows in this group.")


class AggregateResponse(BaseModel):
    table: str
    group_by: str | None = Field(
        default=None, description="Grouping column, null for one overall group."
    )
    metric: str | None = Field(default=None, description="Aggregated column, null for count.")
    agg: str = Field(description="One of sum, avg, count, min, max.")
    groups: list[AggregateGroup]
    matched_rows: int = Field(description="Rows that matched the table and filters.")
    truncated: bool = Field(description="Whether more groups were omitted by limit.")


class ColumnTypeSpec(BaseModel):
    type: str | None = Field(default=None, description="New column type.")
    label: str | None = Field(default=None, description="New display label.")
    required: bool | None = Field(default=None, description="Required flag.")
    values: list[str] | None = Field(default=None, description="Allowed values for an enum.")
    ref_table: str | None = Field(
        default=None, description="Target table key when type is reference."
    )
    ref_label: str | None = Field(
        default=None, description="Target label column when type is reference."
    )


class AlterSchemaBody(BaseModel):
    renames: dict[str, str] = Field(
        default_factory=dict,
        description="Old column name -> new column name.",
    )
    drops: list[str] = Field(
        default_factory=list,
        description="Columns to remove; their data is deleted.",
    )
    types: dict[str, ColumnTypeSpec] = Field(
        default_factory=dict,
        description="Column name -> new type/label/required/enum values.",
    )
    pii: dict[str, str | None] = Field(
        default_factory=dict,
        description=(
            "Column name -> sensitive-data kind (id_card, bank_card, phone, "
            "address, salary, email, secret). Pass null to clear."
        ),
    )


class UpdateAppBody(BaseModel):
    name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")


class CapabilitiesBody(BaseModel):
    capabilities: list[str] = Field(
        description="Page-facing capabilities to grant; an empty list denies all page actions."
    )


class DeleteAppResponse(BaseModel):
    deleted: str


class ShareEntry(BaseModel):
    username: str
    permission: SharePermission = "view"


class SetSharesBody(BaseModel):
    shares: list[ShareEntry] = Field(description="Replacement collaborator list.")


class ShareOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    permission: str


class SharesResponse(BaseModel):
    shares: list[ShareOut]


def _audit(
    server: OctopServer,
    *,
    actor: str,
    action: str,
    target: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if server.services is None:
        return
    server.services.audit_repo.write(
        actor=actor,
        action=action,
        target=target,
        payload=json.dumps(payload, ensure_ascii=False) if payload else None,
    )


def _service(server: OctopServer) -> AppService:
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return build_app_service(
        repos=server.services.repos,
        workspaces=server.app_runtime.agent_registry,
    )


def _columns(schema: Schema) -> list[AppColumn]:
    return [
        AppColumn(
            name=col.name,
            label=col.label,
            type=col.type,
            required=col.required,
            values=list(col.values),
            pii=col.pii,
            ref_table=col.ref_table,
            ref_label=col.ref_label,
        )
        for col in schema.columns
    ]


def _parse_row_filters(raw: list[str]) -> dict[str, str]:
    """Turn repeated ``column=value`` query params into a filter mapping.

    The value may itself contain ``=``; only the first one separates the
    column from the value.
    """
    filters: dict[str, str] = {}
    for item in raw:
        column, separator, value = str(item).partition("=")
        name = column.strip()
        if not separator or not name:
            raise OctopError(
                ErrorCode.APP_ROW_INVALID,
                f"invalid filter, expected column=value: {item!r}",
            )
        filters[name] = value.strip()
    return filters


def _summary(service: AppService, row: AppRecord, permission: str) -> AppSummary:
    # The access kernel distinguishes ``owner`` from ``edit_structure`` so it
    # can gate owner-only actions; the public contract exposes owner/admin as
    # ``edit_structure`` and the dashboard keys its management UI off that.
    if permission == ROLE_OWNER:
        permission = "edit_structure"
    view = service.view(row, permission)
    return AppSummary(**asdict(view), has_html=view.has_html)


def _summary_from_view(view: Any) -> AppSummary:
    return AppSummary(**asdict(view), has_html=view.has_html)


async def _table_out(service: AppService, row: AppRecord, table: AppTableView) -> AppTableOut:
    """Table metadata plus its column definitions (read from the workspace)."""
    columns: list[AppColumn] = []
    try:
        schema = await service.read_schema(row, table.table_key)
    except OctopError:
        schema = None
    if schema is not None:
        columns = _columns(schema)
    return AppTableOut(
        table_key=table.table_key,
        label=table.label,
        row_count=table.row_count,
        data_version=table.data_version,
        is_main=table.is_main,
        public_visible=table.public_visible,
        member_visible=table.member_visible,
        row_scope=table.row_scope,
        rules=table.rules,
        columns=columns,
    )


async def _table_outs(service: AppService, row: AppRecord) -> list[AppTableOut]:
    return [await _table_out(service, row, table) for table in service.list_tables(row)]


def _visible_tables(service: AppService, row: AppRecord, role: str | None) -> list[AppTableView]:
    """Tables a principal may see; hidden tables never reach the client."""
    tables = service.list_tables(row)
    if role == "participate":
        return [table for table in tables if table.member_visible]
    return tables


async def _app_detail(service: AppService, row: AppRecord) -> AppDetail:
    schema = await service.read_schema(row)
    base = _summary(service, row, "edit_structure")
    return AppDetail(
        **base.model_dump(exclude={"has_html", "has_preview"}),
        columns=_columns(schema),
        has_html=await service.has_html(row),
        has_preview=await service.has_preview(row),
        tables=await _table_outs(service, row),
    )


_AUDIT_NEEDED: dict[Action, str] = {
    Action.CREATE_ROW: "edit_data",
    Action.UPDATE_ROW: "edit_data",
    Action.DELETE_ROW: "edit_data",
    Action.MANAGE_ASSETS: "edit_data",
    Action.RUN_SOURCE: "edit_data",
    Action.UPLOAD_IMPORT: "edit_data",
    Action.RUN_GAME: "edit_data",
}
_AUDIT_ACTIONS = frozenset(
    {
        Action.CREATE_ROW,
        Action.UPDATE_ROW,
        Action.DELETE_ROW,
        Action.MANAGE_STRUCTURE,
        Action.MANAGE_APP_SETTINGS,
        Action.MANAGE_ASSETS,
        Action.MANAGE_MEMBERS,
        Action.MANAGE_SHARES,
        Action.MANAGE_BINDINGS,
        Action.MANAGE_INGRESS,
        Action.MANAGE_SCHEDULE,
        Action.EXPORT_DATASET,
        Action.RUN_SOURCE,
        Action.PUBLISH,
        Action.DELETE_APP,
    }
)


def _authorize(
    server: OctopServer,
    user: User,
    app_id: str,
    action: Action,
    *,
    table_key: str | None = None,
    row_id: str | None = None,
) -> tuple[AppService, AppRecord, Decision]:
    """Authorize one app action through the shared access kernel."""
    service = _service(server)
    row = service.get(app_id)
    row_record = service.get_data_row(app_id, row_id) if row_id else None
    if row_record is None and action in {Action.UPDATE_ROW, Action.DELETE_ROW}:
        raise OctopError.localized(ErrorCode.APP_ROW_NOT_FOUND)
    if table_key is None and row_record is not None:
        table_key = row_record.table_key
    decision = service.authorize(
        principal_of(user),
        row,
        action,
        table_key=table_key,
        row_record=row_record,
    )
    if not decision.allowed:
        if decision.reason == "table hidden":
            code = ErrorCode.APP_TABLE_NOT_FOUND
        elif decision.reason in {"table not found", "table required"}:
            code = ErrorCode.APP_SCHEMA_INVALID
        elif (
            action in {Action.UPDATE_ROW, Action.DELETE_ROW}
            and row_record is not None
            and service.table_row_scope(row, row_record.table_key) == "user"
            and decision.reason == "action denied"
        ):
            # A user-scoped row owned by someone else is indistinguishable
            # from a missing row; do not confirm its existence.
            code = ErrorCode.APP_ROW_NOT_FOUND
        else:
            code = ErrorCode.APP_FORBIDDEN
        raise OctopError.localized(code)
    if user.is_admin and row.owner_user_id != user.id and action in _AUDIT_ACTIONS:
        service.audit_admin_override(
            user_id=user.id,
            agent_id=row.agent_id,
            app_id=row.app_id,
            needed=_AUDIT_NEEDED.get(action, "edit_structure"),
            app_owner_id=row.owner_user_id,
            via="http",
        )
    return service, row, decision


def _schedule_write_access(
    server: OctopServer,
    user: User,
    app_id: str,
    schedule_id: str,
) -> tuple[AppService, AppRecord, ScheduleView]:
    """Resolve one schedule and apply the gate for its action.

    ``snapshot_publication`` republishes the current HTML/schema, so it is an
    owner/admin management action like ``publication.publish``. Source sync
    stays on ``edit_structure``, matching the data-source binding it mirrors.
    """
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_STRUCTURE)
    view = service.get_schedule(row, schedule_id)
    if view.action == SCHEDULE_ACTION_SNAPSHOT_PUBLICATION:
        service, row, _decision = _authorize(server, user, app_id, Action.PUBLISH)
    return service, row, view


def _public_link(server: OctopServer, token: str) -> str:
    """Absolute public link when ``public_base_url`` is configured, else a relative path.

    The dashboard copies this value verbatim, so it must carry the public origin
    (scheme, host and port) the server is reached at — not the browser's.
    """
    base = (getattr(server.config, "public_base_url", "") or "").strip().rstrip("/")
    path = f"/p/{token}"
    return f"{base}{path}" if base else path


def _webhooks(server: OctopServer, service: AppService) -> AppWebhookService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return build_webhook_service(repos=server.services.repos, app_service=service)


def _ingress(server: OctopServer, service: AppService) -> AppIngressService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return build_ingress_service(repos=server.services.repos, app_service=service)


def _ingress_hook_url(server: OctopServer, source_id: str) -> str:
    base = (getattr(server.config, "public_base_url", "") or "").strip().rstrip("/")
    path = f"/api/hooks/{source_id}"
    return f"{base}{path}" if base else path


def _assistant(server: OctopServer, service: AppService) -> AppAssistant:
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return AppAssistant(
        service=service,
        app_repo=server.services.light_app_repo,
        agents=server.app_runtime.agent_registry,
        usage_repo=server.services.usage_repo,
        policy_repo=server.services.user_policy_repo,
    )


def _generator(server: OctopServer) -> AppGenerator:
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return AppGenerator(
        agents=server.app_runtime.agent_registry,
        usage_repo=server.services.usage_repo,
        policy_repo=server.services.user_policy_repo,
    )


def _task_service(server: OctopServer) -> AppTaskService:
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return AppTaskService(
        app_service=_service(server),
        app_repo=server.services.light_app_repo,
        task_repo=server.services.repos.app_task_repo,
        agents=server.app_runtime.agent_registry,
        usage_repo=server.services.usage_repo,
        policy_repo=server.services.user_policy_repo,
        audit_repo=server.services.audit_repo,
    )


def _tool_runner(server: OctopServer) -> GatewayToolRunner:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    from octop.infra.connectors.service import ConnectorService  # noqa: PLC0415

    return GatewayToolRunner(
        repo=server.services.repos.connector_repo,
        service=ConnectorService(
            repo=server.services.repos.connector_repo,
            secret_repo=server.services.secret_repo,
            settings_repo=server.services.settings_repo,
            config=server.services.config,
        ),
    )


def _setup_plan_out(
    plan: Any,
    *,
    include_report: bool = False,
    audit: list[SetupPlanAuditOut] | None = None,
) -> SetupPlanOut:
    table = plan.table if isinstance(plan.table, dict) else {}
    raw_tables = table.get("tables")
    if not isinstance(raw_tables, list) or not raw_tables:
        raw_tables = [table] if table.get("columns") else []
    raw_services = table.get("services")
    services = (
        [SetupPlanServiceOut(**item) for item in raw_services if isinstance(item, dict)]
        if isinstance(raw_services, list)
        else []
    )
    raw_capabilities = table.get("capabilities")
    capabilities = (
        [str(item) for item in raw_capabilities] if isinstance(raw_capabilities, list) else None
    )
    out_tables = [
        SetupPlanTableOut(
            key=str(item.get("key") or ""),
            label=str(item.get("label") or ""),
            sheet=str(item.get("sheet") or ""),
            header_row=int(item.get("header_row") or 0),
            columns=[
                AppColumn(**column)
                for column in (item.get("columns") or [])
                if isinstance(column, dict)
            ],
            mapping={str(key): str(value) for key, value in (item.get("mapping") or {}).items()}
            if isinstance(item.get("mapping"), dict)
            else {},
            upsert_key=[str(value) for value in (item.get("upsert_key") or [])],
            include_pii=bool(item.get("include_pii")),
        )
        for item in raw_tables
        if isinstance(item, dict)
    ]
    main = out_tables[0] if out_tables else None
    columns = main.columns if main is not None else []
    expired = plan.expires_at is not None and plan.expires_at < int(time.time())
    status = (
        "expired"
        if expired and plan.status in ("draft", "confirmed", "dry_run_ready", "dry_run_failed")
        else plan.status
    )
    stale_confirmed = plan.status == "confirmed" and plan.updated_at < int(time.time()) - 300
    if status in ("draft", "dry_run_failed") or stale_confirmed:
        actions = ["confirm", "cancel"]
    elif status == "dry_run_ready":
        actions = ["execute", "cancel"]
    elif status == "import_partial":
        actions = ["execute"]
    else:
        actions = []
    return SetupPlanOut(
        plan_id=plan.plan_id,
        status=status,
        revision=plan.revision,
        name=plan.name,
        file=plan.source_path,
        sheet=main.sheet if main is not None else "",
        header_row=main.header_row if main is not None else 0,
        columns=columns,
        include_pii=main.include_pii if main is not None else False,
        tables=out_tables,
        services=services,
        capabilities=capabilities,
        allowed_capabilities=sorted(app_capabilities.AGENT_SETTABLE_CAPABILITIES),
        app_id=plan.app_id,
        dry_run=plan.dry_run,
        result=plan.result,
        error=plan.error,
        expires_at=plan.expires_at,
        report=plan.report if include_report else None,
        allowed_actions=actions,
        audit=audit or [],
    )


def _setup_plan_summary(plan: Any) -> SetupPlanSummaryOut:
    table = plan.table if isinstance(plan.table, dict) else {}
    raw_tables = table.get("tables")
    if not isinstance(raw_tables, list) or not raw_tables:
        raw_tables = [table] if table.get("columns") else []
    raw_services = table.get("services")
    services = raw_services if isinstance(raw_services, list) else []
    raw_capabilities = table.get("capabilities")
    capabilities = (
        [str(item) for item in raw_capabilities] if isinstance(raw_capabilities, list) else None
    )
    dry = plan.dry_run if isinstance(plan.dry_run, dict) else {}
    result = plan.result if isinstance(plan.result, dict) else {}
    errors = [
        *[str(item) for item in (dry.get("errors") or [])[:5]],
        *[str(item) for item in (result.get("errors") or [])[:5]],
    ]
    return SetupPlanSummaryOut(
        plan_id=plan.plan_id,
        status=plan.status,
        name=plan.name,
        user_id=plan.user_id,
        agent_id=plan.agent_id,
        thread_id=plan.thread_id,
        app_id=plan.app_id,
        file=plan.source_path,
        table_count=len([item for item in raw_tables if isinstance(item, dict)]),
        service_count=len([item for item in services if isinstance(item, dict)]),
        capabilities=capabilities,
        created_at=plan.created_at,
        confirmed_at=plan.confirmed_at,
        executed_at=plan.executed_at,
        expires_at=plan.expires_at,
        error=plan.error,
        would_import=int(dry.get("would_import") or 0),
        imported=int(result.get("imported") or 0),
        updated=int(result.get("updated") or 0),
        skipped=int(result.get("skipped") or 0),
        errors=errors,
    )


def _setup_plan_audit(server: OctopServer, plan_id: str) -> list[SetupPlanAuditOut]:
    if server.services is None:
        return []
    rows = server.services.audit_repo.query(target=plan_id, limit=200)
    events: list[SetupPlanAuditOut] = []
    for row in reversed(rows):
        payload: dict[str, Any] | None = None
        if row.payload:
            try:
                parsed = json.loads(row.payload)
                payload = parsed if isinstance(parsed, dict) else {"value": parsed}
            except ValueError:
                payload = {"raw": row.payload}
        events.append(
            SetupPlanAuditOut(
                ts=row.ts,
                actor=row.actor,
                action=row.action,
                payload=payload,
            )
        )
    return events


def _service_with_runner(server: OctopServer) -> AppService:
    """AppService wired with the gateway tool runner (external sources)."""
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    return build_app_service(
        repos=server.services.repos,
        workspaces=server.app_runtime.agent_registry,
        external_tool_runner=_tool_runner(server),
    )


def _require_thread_access(server: OctopServer, agent_id: str, thread_id: str, user: User) -> None:
    """The caller must own both the agent and the conversation."""
    if server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    require_agent_row(agent_id, user=user, as_user=None, server=server)
    thread = server.app_runtime.gateway.thread_registry.get_thread(thread_id)
    if thread is None or thread.agent_id != agent_id:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"thread {thread_id!r} not found")
    if thread.user_id != user.id:
        raise OctopError(ErrorCode.FORBIDDEN, "thread not owned by user")


async def _thread_app_messages(server: OctopServer, agent_id: str, thread_id: str) -> list[Any]:
    """Every stored message that can carry an ``app_card`` in this thread."""
    if server.services is None or server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    from octop.infra.history.service import HistoryArchive  # noqa: PLC0415

    archive = getattr(server.app_runtime, "history_archive", None)
    if isinstance(archive, HistoryArchive):
        from octop.infra.history.reader import export_messages  # noqa: PLC0415

        return await export_messages(
            archive, server.app_runtime.agent_registry, agent_id, thread_id
        )
    repo = server.services.thread_message_repo
    if await asyncio.to_thread(repo.projection_status, thread_id) != "ready":
        # History is still being projected; the dashboard keeps its transcript
        # snapshot until the next open instead of scanning the checkpoint here.
        return []
    rows = await asyncio.to_thread(repo.rows_containing, thread_id, "app_card")
    return [json.loads(row.message_json) for row in rows]


@router.get(
    "",
    response_model=AppListResponse,
    summary="List MiniApps",
    description="Apps owned by the caller plus apps shared with them. Admins see every app.",
)
async def list_apps(
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppListResponse:
    service = _service(server)
    return AppListResponse(
        apps=[
            _summary_from_view(view)
            for view in await service.list_views_with_stats(user.id, is_admin=user.is_admin)
        ]
    )


@router.get(
    "/in-thread",
    response_model=AppListResponse,
    summary="List MiniApps in a conversation",
    description=(
        "Scans the whole thread transcript for ``app_card`` tool results and returns "
        "the MiniApps it referenced, most recently mentioned first. The chat dock's "
        "MiniApps tab needs this because it only keeps the latest history page."
    ),
)
async def list_in_thread_apps(
    agent_id: str = Query(description="Agent that owns the conversation."),
    thread_id: str = Query(description="Conversation thread id."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppListResponse:
    _require_thread_access(server, agent_id, thread_id, user)
    app_ids = app_card_ids(await _thread_app_messages(server, agent_id, thread_id))
    views = await _service(server).views_for_app_ids(app_ids, user.id, is_admin=user.is_admin)
    return AppListResponse(apps=[_summary_from_view(view) for view in views])


class AppTemplateOut(BaseModel):
    template: str = Field(description="Template id (legacy field).")
    template_id: str = Field(default="", description="Template id within its source.")
    label: str = Field(description="Display label.")
    name: str = Field(default="", description="Localized display name.")
    description: str = Field(default="", description="Localized one-line description.")
    category: str = Field(default="", description="Optional category (sales, finance, …).")
    version: int = Field(default=0, description="Template version.")
    has_html: bool = Field(description="Whether the template ships an app.html shell.")
    has_preview: bool = Field(
        default=False, description="Whether the template ships a preview image."
    )
    source: str = Field(default="agent", description="system or agent.")
    agent_id: str | None = Field(default=None, description="Owning agent for agent templates.")
    scope: str = Field(default="agent", description="global or agent.")


class AppTemplatesResponse(BaseModel):
    templates: list[AppTemplateOut]


class CreateFromTemplateBody(BaseModel):
    agent_id: str = Field(description="Agent whose workspace hosts the new app.")
    template: str = Field(description="Template id from the templates endpoint.")
    source: Literal["system", "agent"] = Field(
        default="agent", description="Template source: system (global) or agent (workspace)."
    )
    name: str | None = Field(default=None, max_length=100, description="New app name.")
    description: str = Field(default="", max_length=2000, description="New app description.")


class QuickInspectResponse(BaseModel):
    upload_path: str = Field(description="Workspace path to pass to quick/create.")
    sheet: str = Field(description="Sheet name inspected by default.")
    report: dict[str, Any] = Field(description="Deterministic inspection report.")
    suggested_view: dict[str, Any] = Field(description="Suggested dashboard view spec.")


class QuickSheetBody(BaseModel):
    sheet: str = Field(description="Sheet name from the inspection report.")
    columns: list[AppColumn] = Field(description="Confirmed fields for this sheet.")
    header_row: int | None = Field(
        default=None, ge=0, le=100, description="0-based header row override."
    )
    mapping: dict[str, str] | None = Field(
        default=None, description="Source header -> schema column map."
    )
    metric_column: str | None = Field(default=None, description="Numeric column for KPIs/charts.")
    group_column: str | None = Field(default=None, description="Categorical column for grouping.")
    view: Literal["dashboard", "ledger", "board"] = Field(
        default="dashboard", description="Generated view style for this sheet."
    )


class SetupPlanTableOut(BaseModel):
    key: str = Field(description="Table key; the first table is main.")
    label: str = Field(default="", description="Display label.")
    sheet: str = Field(default="", description="Worksheet imported into this table.")
    header_row: int = Field(default=0, description="0-based header row.")
    columns: list[AppColumn] = Field(default_factory=list, description="Proposed fields.")
    mapping: dict[str, str] = Field(default_factory=dict, description="Source column map.")
    upsert_key: list[str] = Field(default_factory=list, description="Dedupe columns.")
    include_pii: bool = Field(default=False, description="Import sensitive columns.")


class SetupPlanAuditOut(BaseModel):
    ts: int = Field(description="Unix timestamp of the audit event.")
    actor: str | None = Field(default=None, description="Actor label, e.g. user:2 or _system.")
    action: str = Field(description="Audit action, e.g. miniapp.setup_plan.confirm.")
    payload: dict[str, Any] | None = Field(
        default=None, description="Structured audit payload, when parseable."
    )


class SetupPlanServiceOut(BaseModel):
    name: str = Field(description="Page-facing service name.")
    provider: str = Field(description="connector or external.")
    connector_instance_id: str = Field(default="", description="Connector instance id.")
    connector_kind: str | None = Field(default=None, description="Public connector kind.")
    allowed_tools: list[str] = Field(default_factory=list, description="Allowed read-only tools.")
    allowed_url_prefixes: list[str] = Field(
        default_factory=list, description="Allowed http(s) URL prefixes."
    )
    request_headers: dict[str, str] = Field(
        default_factory=dict, description="Allowlisted presentation headers."
    )
    cache_ttl_seconds: int = Field(default=300, description="Response cache TTL.")
    max_calls_per_minute: int = Field(default=30, description="Live call budget.")
    enabled: bool = Field(default=True, description="Whether the page may call it.")


class SetupPlanServiceBody(SetupPlanServiceOut):
    """Editable service declaration submitted with a plan confirmation."""


class SetupPlanSummaryOut(BaseModel):
    plan_id: str = Field(description="Durable setup plan id.")
    status: str = Field(description="Current plan status.")
    name: str = Field(description="App name.")
    user_id: int = Field(description="Owner user id.")
    agent_id: str = Field(description="Owning agent id.")
    thread_id: str | None = Field(default=None, description="Originating thread id.")
    app_id: str | None = Field(default=None, description="Created app id.")
    file: str = Field(description="Workspace-relative source file.")
    table_count: int = Field(default=0, description="Number of planned tables.")
    service_count: int = Field(default=0, description="Number of planned services.")
    capabilities: list[str] | None = Field(
        default=None, description="Capabilities in the plan; null means app defaults."
    )
    created_at: int = Field(description="Creation timestamp.")
    confirmed_at: int | None = Field(default=None, description="Confirmation timestamp.")
    executed_at: int | None = Field(default=None, description="Import timestamp.")
    expires_at: int | None = Field(default=None, description="Expiry timestamp.")
    error: str = Field(default="", description="Last error summary.")
    would_import: int = Field(default=0, description="Rows the dry run would import.")
    imported: int = Field(default=0, description="Rows actually imported.")
    updated: int = Field(default=0, description="Rows updated by upsert.")
    skipped: int = Field(default=0, description="Rows skipped.")
    errors: list[str] = Field(default_factory=list, description="Dry-run/import errors.")


class SetupPlanListOut(BaseModel):
    plans: list[SetupPlanSummaryOut] = Field(default_factory=list)
    total: int = Field(default=0, description="Matching plans across all pages.")
    limit: int = Field(description="Page size.")
    offset: int = Field(description="Page offset.")


class SetupPlanOut(BaseModel):
    plan_id: str = Field(description="Durable setup plan id.")
    status: str = Field(description="draft, dry_run_ready, imported, cancelled, …")
    revision: int = Field(description="Schema revision; stale clients receive 409.")
    name: str = Field(description="Proposed app name.")
    file: str = Field(description="Workspace-relative source file.")
    sheet: str = Field(default="", description="Worksheet selected for the import.")
    header_row: int = Field(default=0, description="0-based header row.")
    columns: list[AppColumn] = Field(
        default_factory=list, description="Main table fields (compatibility field)."
    )
    include_pii: bool = Field(
        default=False, description="Main table PII decision (compatibility field)."
    )
    tables: list[SetupPlanTableOut] = Field(
        default_factory=list, description="All planned tables, main first."
    )
    services: list[SetupPlanServiceOut] = Field(
        default_factory=list, description="Service declarations applied after the import."
    )
    capabilities: list[str] | None = Field(
        default=None,
        description="Page capabilities to apply; null means leave the app defaults.",
    )
    allowed_capabilities: list[str] = Field(
        default_factory=list,
        description="Agent-settable capability names available for editing.",
    )
    app_id: str | None = Field(default=None, description="App created after confirmation.")
    dry_run: dict[str, Any] = Field(default_factory=dict, description="Dry-run summary.")
    result: dict[str, Any] = Field(default_factory=dict, description="Real import result.")
    error: str = Field(default="", description="Last failure reason, if any.")
    expires_at: int | None = Field(
        default=None, description="Unix timestamp when the plan expires."
    )
    report: dict[str, Any] | None = Field(
        default=None, description="Deterministic inspection report (GET only)."
    )
    allowed_actions: list[str] = Field(
        default_factory=list,
        description="Actions the current user may take next: confirm, execute, cancel.",
    )
    audit: list[SetupPlanAuditOut] = Field(
        default_factory=list, description="Plan audit events (detail endpoint only)."
    )


class SetupPlanTableBody(BaseModel):
    key: str | None = Field(default=None, description="Table key; first table becomes main.")
    label: str = Field(default="", description="Display label.")
    sheet: str = Field(description="Worksheet to import.")
    header_row: int | None = Field(default=None, ge=0, le=100, description="0-based header.")
    columns: list[AppColumn] = Field(description="Final field definitions.")
    mapping: dict[str, str] | None = Field(default=None, description="Source column map.")
    upsert_key: list[str] | None = Field(default=None, description="Dedupe columns.")
    include_pii: bool | None = Field(default=None, description="Import sensitive columns.")


class SetupPlanConfirmBody(BaseModel):
    revision: int = Field(description="Revision the client is confirming.")
    name: str = Field(description="Final app name.", max_length=100)
    columns: list[AppColumn] = Field(
        default_factory=list,
        description="Final fields for a single-table plan (compatibility field).",
    )
    include_pii: bool | None = Field(
        default=None, description="Override the proposed main-table PII decision."
    )
    tables: list[SetupPlanTableBody] | None = Field(
        default=None, description="Multi-table plan; when present, columns is ignored."
    )
    services: list[SetupPlanServiceBody] | None = Field(
        default=None, description="Final service declarations; omit to keep the proposal."
    )
    capabilities: list[str] | None = Field(
        default=None, description="Final capability list; omit to keep the proposal."
    )


class SetupPlanExecuteBody(BaseModel):
    revision: int = Field(description="Revision being executed.")


class QuickCreateBody(BaseModel):
    agent_id: str = Field(description="Agent that will host the new app.")
    path: str = Field(description="Workspace path returned by quick/inspect.")
    columns: list[AppColumn] = Field(
        default_factory=list, description="Confirmed fields for a single-sheet import."
    )
    sheets: list[QuickSheetBody] | None = Field(
        default=None,
        description="Multi-sheet import; when present, top-level columns/sheet are ignored.",
    )
    view: Literal["dashboard", "ledger", "board"] = Field(
        default="dashboard", description="Generated view style."
    )
    name: str = Field(description="New app name.")
    description: str = Field(default="", max_length=2000, description="New app description.")
    sheet: str | None = Field(default=None, description="Sheet to import; defaults to first.")
    header_row: int | None = Field(
        default=None, ge=0, le=100, description="0-based header row, when overriding detection."
    )
    mapping: dict[str, str] | None = Field(
        default=None, description="Source header -> schema column map for quick imports."
    )
    metric_column: str | None = Field(
        default=None, description="Numeric column used for KPIs and charts."
    )
    group_column: str | None = Field(
        default=None, description="Categorical column used for the bar chart and board."
    )
    include_pii: bool = Field(default=False, description="Import columns detected as sensitive.")
    upsert_key: list[str] | None = Field(
        default=None, description="Columns whose combined value identifies an existing row."
    )


def _assert_agent_access(service: AppService, user: User, agent_id: str) -> None:
    if user.is_admin or service.is_agent_accessible(agent_id, user.id):
        return
    raise OctopError(ErrorCode.APP_FORBIDDEN, "agent is not accessible to this user")


@router.get(
    "/templates",
    response_model=AppTemplatesResponse,
    summary="List app templates",
    description=(
        "Lists system-wide templates plus the shells seeded into the agent's "
        "``app_templates/`` directory. Create one with POST /apps/from-template."
    ),
)
async def list_app_templates(
    request: Request,
    agent_id: str = Query(description="Agent whose workspace holds agent templates."),
    source: Literal["all", "system", "agent"] = Query(
        "agent", description="all: system + agent; system: bundled only; agent: workspace only."
    ),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppTemplatesResponse:
    service = _service(server)
    _assert_agent_access(service, user, agent_id)
    templates = await service.list_templates(
        agent_id, source=source, locale=str(resolve_request_locale(request))
    )
    return AppTemplatesResponse(
        templates=[AppTemplateOut(**item) for item in templates],
    )


@router.get(
    "/templates/{template_id}/preview",
    summary="Fetch a template preview image",
    description=(
        "Serves the preview image (the SVG cover) of a system or agent "
        "template, so the creation gallery can show what the template looks "
        "like."
    ),
)
async def get_app_template_preview(
    template_id: str,
    agent_id: str = Query(description="Agent whose workspace holds agent templates."),
    source: Literal["system", "agent"] = Query(
        "agent", description="Template source: system (global) or agent (workspace)."
    ),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service = _service(server)
    _assert_agent_access(service, user, agent_id)
    svg = await service.template_preview(agent_id, template_id, source)
    if svg is None:
        raise OctopError(ErrorCode.APP_NOT_FOUND, "template has no preview image")
    try:
        clean = validate_preview_svg(svg)
    except ValueError:
        raise OctopError(ErrorCode.APP_NOT_FOUND, "template has no preview image") from None
    return _svg_response(clean)


@router.post(
    "/from-template",
    response_model=AppDetail,
    status_code=201,
    summary="Create an app from an expert template",
    description=(
        "Creates an empty app from an expert-provided schema + HTML shell. "
        "The template never carries user data. Requires access to the agent."
    ),
)
async def create_app_from_template(
    body: CreateFromTemplateBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service = _service(server)
    _assert_agent_access(service, user, body.agent_id)
    row = await service.create_from_template(
        agent_id=body.agent_id,
        owner_user_id=user.id,
        template=body.template,
        source=body.source,
        name=body.name,
        description=body.description,
    )
    return await _app_detail(service, row)


@router.post(
    "/quick/inspect",
    response_model=QuickInspectResponse,
    summary="Inspect a ledger for quick creation",
    description=(
        "Parses an uploaded spreadsheet, stores it under the agent's "
        "``inbound/imports/`` and returns inferred fields plus a dashboard "
        "preview. No app is created yet."
    ),
)
async def quick_inspect_app(
    file: UploadFile = File(...),  # noqa: B008
    agent_id: str = Form(description="Agent whose workspace stores the upload."),
    sheet: str | None = Form(default=None, description="Sheet name or 0-based index."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> QuickInspectResponse:
    service = _service(server)
    _assert_agent_access(service, user, agent_id)
    max_bytes = (
        int(server.services.config.max_upload_bytes) if server.services is not None else None
    )
    fallback = upload_mb_to_bytes(DEFAULT_MAX_UPLOAD_MB)
    data = await read_upload_capped(file, max_bytes=max_bytes or fallback)
    result = await service.quick_inspect(
        agent_id,
        filename=file.filename or "upload.xlsx",
        data=data,
        sheet=sheet,
    )
    return QuickInspectResponse(**result)


@router.post(
    "/quick/create",
    response_model=QuickCreateResponse,
    status_code=201,
    summary="Create a MiniApp from an inspected ledger",
    description=(
        "Creates the app, imports the uploaded rows and renders a deterministic "
        "dashboard, ledger or board. The app is deleted if rendering fails."
    ),
)
async def quick_create_app(
    body: QuickCreateBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> QuickCreateResponse:
    service = _service(server)
    _assert_agent_access(service, user, body.agent_id)
    if body.sheets:
        row, result = await service.quick_create_multi(
            agent_id=body.agent_id,
            owner_user_id=user.id,
            path=body.path,
            sheets=[sheet.model_dump(exclude_none=True) for sheet in body.sheets],
            name=body.name,
            description=body.description,
            include_pii=body.include_pii,
        )
        audit_payload = {"path": body.path, "sheets": [sheet.sheet for sheet in body.sheets]}
    else:
        if not body.columns:
            raise OctopError(ErrorCode.APP_ROW_INVALID, "at least one column is required")
        row, result = await service.quick_create(
            agent_id=body.agent_id,
            owner_user_id=user.id,
            path=body.path,
            columns=[column.model_dump(exclude_none=True) for column in body.columns],
            view=body.view,
            name=body.name,
            description=body.description,
            sheet=body.sheet,
            header_row=body.header_row,
            mapping=body.mapping,
            metric_column=body.metric_column,
            group_column=body.group_column,
            include_pii=body.include_pii,
            upsert_key=body.upsert_key,
        )
        audit_payload = {"path": body.path, "view": body.view}
    _audit(
        server,
        actor=f"user:{user.id}",
        action="miniapp.quick_create",
        target=row.app_id,
        payload=audit_payload,
    )
    return QuickCreateResponse(
        app=await _app_detail(service, row),
        imported=result.imported,
        updated=result.updated,
        skipped=result.skipped,
        errors=result.errors,
        warnings=result.warnings,
        pii_columns=result.pii_columns,
        blocked_pii=result.blocked_pii,
        failed_rows=[FailedRowOut(**item) for item in result.failed_rows],
    )


@router.get(
    "/setup-plans",
    response_model=SetupPlanListOut,
    summary="List MiniApp setup plans",
    description=(
        "Admin/support read-only list of durable setup plans with status, source "
        "file, table/service counts and dry-run/import summaries. Use the detail "
        "endpoint for the audit timeline and per-step results."
    ),
)
async def list_setup_plans(
    status: str | None = Query(default=None, description="Exact plan status."),
    agent_id: str | None = Query(default=None, description="Filter by agent."),
    user_id: int | None = Query(default=None, description="Filter by owner user id."),
    app_id: str | None = Query(default=None, description="Filter by created app id."),
    thread_id: str | None = Query(default=None, description="Filter by thread id."),
    has_error: bool | None = Query(
        default=None, description="Only plans with (true) or without (false) an error."
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SetupPlanListOut:
    if not user.is_admin:
        raise OctopError(ErrorCode.APP_FORBIDDEN, "admin access is required")
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    repo = server.services.repos.app_setup_plan_repo
    plans = repo.list_plans(
        status=status,
        agent_id=agent_id,
        user_id=user_id,
        app_id=app_id,
        thread_id=thread_id,
        has_error=has_error,
        limit=limit,
        offset=offset,
    )
    total = repo.count_plans(
        status=status,
        agent_id=agent_id,
        user_id=user_id,
        app_id=app_id,
        thread_id=thread_id,
        has_error=has_error,
    )
    return SetupPlanListOut(
        plans=[_setup_plan_summary(plan) for plan in plans],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/setup-plans/{plan_id}",
    response_model=SetupPlanOut,
    summary="Read a MiniApp setup plan",
    description=(
        "Returns the durable plan state, the inspection report and the actions "
        "the current user may take. The chat plan card polls this endpoint so "
        "history always renders the latest status instead of a stale prompt."
    ),
)
async def get_setup_plan(
    plan_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SetupPlanOut:
    service = _service(server)
    plan = await service.get_setup_plan(plan_id, user.id)
    return _setup_plan_out(
        plan,
        include_report=True,
        audit=_setup_plan_audit(server, plan.plan_id),
    )


@router.post(
    "/setup-plans/{plan_id}/confirm",
    response_model=SetupPlanOut,
    summary="Confirm a MiniApp setup plan",
    description=(
        "Freezes the confirmed schema, creates the empty app and runs a read-only "
        "dry-run. No user data is written; a failure rolls the empty app back."
    ),
)
async def confirm_setup_plan(
    plan_id: str,
    body: SetupPlanConfirmBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SetupPlanOut:
    service = _service(server)
    plan = await service.confirm_setup_plan(
        plan_id,
        user_id=user.id,
        revision=body.revision,
        name=body.name,
        columns=[column.model_dump(exclude_none=True) for column in body.columns],
        include_pii=body.include_pii,
        tables=(
            [table.model_dump(exclude_none=True) for table in body.tables]
            if body.tables is not None
            else None
        ),
        services=(
            [service.model_dump(exclude_none=True) for service in body.services]
            if body.services is not None
            else None
        ),
        capabilities=body.capabilities,
    )
    return _setup_plan_out(plan)


@router.post(
    "/setup-plans/{plan_id}/execute",
    response_model=SetupPlanOut,
    summary="Execute a confirmed MiniApp setup plan",
    description=(
        "Runs the real import for a dry-run-ready plan. The plan revision and "
        "content-addressed import prefix make retries idempotent."
    ),
)
async def execute_setup_plan(
    plan_id: str,
    body: SetupPlanExecuteBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SetupPlanOut:
    service = _service(server)
    plan = await service.execute_setup_plan(
        plan_id,
        user_id=user.id,
        revision=body.revision,
    )
    return _setup_plan_out(plan)


@router.post(
    "/setup-plans/{plan_id}/cancel",
    response_model=SetupPlanOut,
    summary="Cancel a MiniApp setup plan",
    description=(
        "Cancels a plan before its real import and removes the empty app. After "
        "import, use POST /apps/{app_id}/delete with its normal approval."
    ),
)
async def cancel_setup_plan(
    plan_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SetupPlanOut:
    service = _service(server)
    plan = await service.cancel_setup_plan(plan_id, user_id=user.id)
    return _setup_plan_out(plan)


@router.post(
    "/{app_id}/game-sessions",
    response_model=GameSessionOut,
    status_code=201,
    summary="Create an external game session",
    description=(
        "Issues a short-lived token for a configured external game. The game "
        "opens in the platform side panel and writes results through "
        "POST /api/game-sessions/{token}/results."
    ),
)
async def create_app_game_session(
    app_id: str,
    body: GameSessionBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> GameSessionOut:
    service, row, _decision = _authorize(server, user, app_id, Action.RUN_GAME)
    service.require_capability(row, app_capabilities.CAP_GAME_LAUNCH)
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server is not started")
    game = next(
        (item for item in server.services.config.games if item.id == body.game_id),
        None,
    )
    if game is None:
        raise OctopError(ErrorCode.APP_NOT_FOUND, f"unknown game: {body.game_id!r}")
    secret = server.services.secret_repo.get("jwt")
    if secret is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
    session_id = f"game_{new_short_id(16)}"
    token, expires_at = create_game_token(
        secret,
        app_id=row.app_id,
        user_id=user.id,
        game_id=game.id,
        session_id=session_id,
        is_admin=user.is_admin,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="miniapp.game_session",
        target=row.app_id,
        payload={"game_id": game.id, "session_id": session_id},
    )
    return GameSessionOut(
        session_id=session_id,
        game_id=game.id,
        name=game.name,
        url=game.url,
        token=token,
        expires_at=expires_at,
        sandbox=game.sandbox,
    )


@router.post(
    "/restore/preflight",
    response_model=RestorePreflightOut,
    summary="Check an app backup before restoring",
    description=(
        "Scans a zip produced by GET /apps/{app_id}/backup without writing any "
        "rows: archive structure, table schemas/data/row ids, reference "
        "integrity, HTML/preview, mapping targets, connector instances, "
        "source/schedule references and webhook URLs. Blocking errors prevent "
        "POST /apps/restore; warnings list declarations that will be disabled "
        "or skipped and follow-up actions. Requires access to the target agent."
    ),
)
async def preflight_restore_app(
    file: UploadFile = File(...),  # noqa: B008
    agent_id: str = Query(description="Agent whose workspace will host the restored app."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> RestorePreflightOut:
    service = _service(server)
    _assert_agent_access(service, user, agent_id)
    data = await read_upload_capped(
        file,
        max_bytes=MAX_BACKUP_BYTES,
        code=ErrorCode.APP_SOURCE_INVALID,
    )
    report = await service.preflight_restore(
        agent_id=agent_id,
        owner_user_id=user.id,
        data=data,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.restore_preflight",
        target=agent_id,
        payload={
            "bytes": len(data),
            "error_count": report["error_count"],
            "warning_count": report["warning_count"],
        },
    )
    return RestorePreflightOut(**report)


@router.post(
    "/restore",
    response_model=AppDetail,
    status_code=201,
    summary="Restore an app from a backup",
    description=(
        "Creates a new app from a zip produced by GET /apps/{app_id}/backup. "
        "The archive and its dependencies are checked before anything is "
        "written (blocking errors reject the restore); ids are not reused. "
        "Requires access to the target agent."
    ),
)
async def restore_app(
    file: UploadFile = File(...),  # noqa: B008
    agent_id: str = Query(description="Agent whose workspace will host the restored app."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service = _service(server)
    _assert_agent_access(service, user, agent_id)
    data = await read_upload_capped(
        file,
        max_bytes=MAX_BACKUP_BYTES,
        code=ErrorCode.APP_SOURCE_INVALID,
    )
    row = await service.restore_archive(
        agent_id=agent_id,
        owner_user_id=user.id,
        data=data,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.restore",
        target=row.app_id,
        payload={"bytes": len(data)},
    )
    schema = await service.read_schema(row)
    base = _summary(service, row, "edit_structure")
    return AppDetail(
        **base.model_dump(exclude={"has_html", "has_preview"}),
        columns=_columns(schema),
        has_html=await service.has_html(row),
        has_preview=await service.has_preview(row),
        tables=await _table_outs(service, row),
    )


@router.get(
    "/{app_id}",
    response_model=AppDetail,
    summary="Get MiniApp details",
)
async def get_app(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_open_total")
    service, row, decision = _authorize(server, user, app_id, Action.VIEW_APP)
    tables = _visible_tables(service, row, decision.role)
    schema = await service.read_schema(row, tables[0].table_key if tables else MAIN_TABLE_KEY)
    base = _summary(service, row, decision.role or "view")
    return AppDetail(
        # The detail view checks the workspace directly: the cached size is a
        # hint for list views, the file itself is the truth.
        **base.model_dump(exclude={"has_html", "has_preview"}),
        columns=_columns(schema) if tables else [],
        has_html=await service.has_html(row),
        has_preview=await service.has_preview(row),
        tables=[await _table_out(service, row, table) for table in tables],
    )


@router.get(
    "/{app_id}/tables",
    response_model=AppTablesResponse,
    summary="List app tables",
    description=(
        "Lists every table of the app (the main table first) with its columns and "
        "row count. Use the returned keys with the rows endpoints."
    ),
)
async def list_app_tables(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppTablesResponse:
    service, row, decision = _authorize(server, user, app_id, Action.VIEW_APP)
    tables = _visible_tables(service, row, decision.role)
    return AppTablesResponse(tables=[await _table_out(service, row, table) for table in tables])


@router.get(
    "/{app_id}/data-version",
    response_model=AppDataVersionOut,
    summary="Poll table data versions",
    description=(
        "Lightweight change token for live refresh: one ``data_version`` per "
        "table, bumped on every row write. The host polls this and forwards "
        "``data-changed`` to the MiniApp iframe. Requires view permission."
    ),
)
async def get_app_data_version(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDataVersionOut:
    service, row, decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return AppDataVersionOut(
        tables=[
            AppTableVersionOut(
                table_key=table.table_key,
                data_version=table.data_version,
                row_count=table.row_count,
            )
            for table in _visible_tables(service, row, decision.role)
        ]
    )


@router.post(
    "/dataset/import",
    response_model=AppDetail,
    summary="Import a built-in dataset export",
    description=(
        "Creates a new app from GET /apps/{id}/dataset/export. Rows are attributed "
        "to the importing user; connector/external bindings are recreated disabled."
    ),
)
async def import_app_dataset(
    body: ImportDatasetBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service = _service(server)
    _assert_agent_access(service, user, body.agent_id)
    row = await service.import_dataset(
        body.dataset,
        agent_id=body.agent_id,
        owner_user_id=user.id,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.dataset.import",
        target=row.app_id,
        payload={"source_app_id": body.dataset.get("app", {}).get("app_id")},
    )
    return await _app_detail(service, row)


@router.get(
    "/{app_id}/dataset",
    response_model=DatasetOut,
    summary="Describe the built-in dataset service",
    description=(
        "The platform-provided ``builtin.dataset`` service for small apps: tables, "
        "columns and supported read operations. Requires view permission."
    ),
)
async def get_app_dataset(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DatasetOut:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    descriptor = await BuiltinDatasetService(service).describe(row)
    return DatasetOut(**asdict(descriptor))


@router.post(
    "/{app_id}/dataset/query",
    response_model=DatasetQueryOut,
    summary="Query the built-in dataset service",
    description=(
        "Reads one page from the platform-provided ``builtin.dataset`` service. "
        "Viewers receive PII-masked rows. Requires view permission."
    ),
)
async def query_app_dataset(
    app_id: str,
    body: DatasetQueryBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DatasetQueryOut:
    service, row, decision = _authorize(server, user, app_id, Action.VIEW_ROW, table_key=body.table)
    schema, rows, total, masked_pii_rows = await BuiltinDatasetService(service).query(
        row,
        table=body.table,
        limit=body.limit,
        offset=body.offset,
        redact_pii=decision.pii_policy != "visible",
        pii_policy=decision.pii_policy,
        viewer_user_id=user.id,
        include_all_rows=decision.row_scope == "all",
    )
    columns = [
        DatasetColumnOut(
            name=column.name,
            label=column.label,
            type=column.type,
            required=column.required,
            pii=column.pii,
        )
        for column in schema.columns
    ]
    return DatasetQueryOut(
        table=body.table,
        columns=columns,
        rows=rows,
        total=total,
        limit=body.limit,
        offset=body.offset,
        pii_columns=[column.name for column in columns if column.pii],
        masked_pii_rows=masked_pii_rows,
    )


@router.get(
    "/{app_id}/dataset/export",
    summary="Export the built-in dataset",
    description=(
        "Exports schema, rows, row scope, rules and bindings as a portable JSON "
        "document for promoting the app to an external service. Requires owner "
        "or admin because the export contains unmasked data."
    ),
    responses={200: {"content": {"application/json": {}}}},
)
async def export_app_dataset(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, _decision = _authorize(server, user, app_id, Action.EXPORT_DATASET)
    content = await service.export_dataset(row)
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_export_total")
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.dataset.export",
        target=app_id,
        payload={"bytes": len(content)},
    )
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="miniapp-{app_id}-dataset.json"'},
    )


@router.get(
    "/{app_id}/bindings",
    response_model=BindingsResponse,
    summary="List MiniApp service bindings",
    description=(
        "Lists the implicit built-in dataset binding plus stored connector/external "
        "bindings. Requires view permission."
    ),
)
async def list_app_bindings(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> BindingsResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return BindingsResponse(
        bindings=[BindingOut(**asdict(item)) for item in service.list_bindings(row)]
    )


@router.put(
    "/{app_id}/bindings/{binding_id}",
    response_model=BindingOut,
    summary="Create or update a MiniApp service binding",
    description="Requires owner or admin.",
)
async def save_app_binding(
    app_id: str,
    binding_id: str,
    body: SaveBindingBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> BindingOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_BINDINGS)
    view = service.save_binding(
        row,
        binding_id=binding_id,
        provider=body.provider,
        service_id=body.service_id,
        config=body.config,
        enabled=body.enabled,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.binding.save",
        target=app_id,
        payload={
            "binding_id": view.binding_id,
            "provider": view.provider,
            "service_id": view.service_id,
            "enabled": view.enabled,
        },
    )
    return BindingOut(**asdict(view))


@router.delete(
    "/{app_id}/bindings/{binding_id}",
    response_model=DeleteBindingResponse,
    summary="Delete a MiniApp service binding",
    description="The implicit builtin.dataset binding cannot be deleted. Requires owner or admin.",
)
async def delete_app_binding(
    app_id: str,
    binding_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DeleteBindingResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_BINDINGS)
    if not service.delete_binding(row, binding_id):
        raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.binding.delete",
        target=app_id,
        payload={"binding_id": binding_id},
    )
    return DeleteBindingResponse(deleted=binding_id)


def _service_out(view: ServiceView) -> AppServiceOut:
    return AppServiceOut(
        service_id=view.service_id,
        name=view.name,
        provider=view.provider,
        connector_instance_id=view.connector_instance_id,
        allowed_tools=list(view.allowed_tools),
        allowed_url_prefixes=list(view.allowed_url_prefixes),
        request_headers=dict(view.request_headers),
        cache_ttl_seconds=view.cache_ttl_seconds,
        max_calls_per_minute=view.max_calls_per_minute,
        public_live=view.public_live,
        enabled=view.enabled,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get(
    "/{app_id}/services",
    response_model=AppServicesResponse,
    summary="List external service declarations",
    description=(
        "Declared read-only connector services the MiniApp page can call. Requires view permission."
    ),
)
async def list_app_services(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppServicesResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return AppServicesResponse(services=[_service_out(view) for view in service.list_services(row)])


@router.put(
    "/{app_id}/services/{name}",
    response_model=AppServiceOut,
    summary="Declare or update an external service",
    description=(
        "Owner/admin only. The declaration is the page allowlist (tools, URL "
        "prefixes, cache and rate limit) and mirrors a connector/external "
        "binding with the same name."
    ),
)
async def save_app_service(
    app_id: str,
    name: str,
    body: SaveAppServiceBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppServiceOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_BINDINGS)
    view = service.save_service(
        row,
        name=name,
        provider=body.provider,
        connector_instance_id=body.connector_instance_id,
        connector_kind=body.connector_kind,
        allowed_tools=body.allowed_tools,
        allowed_url_prefixes=body.allowed_url_prefixes,
        request_headers=body.request_headers,
        cache_ttl_seconds=body.cache_ttl_seconds,
        max_calls_per_minute=body.max_calls_per_minute,
        public_live=body.public_live,
        enabled=body.enabled,
        actor_user_id=user.id,
        is_admin=user.is_admin,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.service.save",
        target=app_id,
        payload={
            "name": view.name,
            "provider": view.provider,
            "connector_instance_id": view.connector_instance_id,
            "allowed_tools": list(view.allowed_tools),
            "enabled": view.enabled,
        },
    )
    return _service_out(view)


@router.delete(
    "/{app_id}/services/{name}",
    response_model=DeleteBindingResponse,
    summary="Delete an external service declaration",
    description="Owner/admin only. Removes its cache and mirrored binding.",
)
async def delete_app_service(
    app_id: str,
    name: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DeleteBindingResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_BINDINGS)
    if not service.delete_service(row, name):
        raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.service.delete",
        target=app_id,
        payload={"name": name.strip().lower()},
    )
    return DeleteBindingResponse(deleted=name.strip().lower())


@router.post(
    "/{app_id}/services/{name}/call",
    response_model=ServiceCallOut,
    summary="Call a declared external service",
    description=(
        "Runs one allowlisted read-only connector tool on behalf of the app "
        "page. Requires view permission and the ``external.fetch`` capability; "
        "URLs must match the declaration's prefixes. Responses are cached."
    ),
)
async def call_app_service(
    app_id: str,
    name: str,
    body: ServiceCallBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ServiceCallOut:
    _service_check, row, _decision = _authorize(server, user, app_id, Action.SERVICE_CALL)
    result = await _service_with_runner(server).call_service(
        row,
        name=name,
        tool=body.tool,
        args=body.args,
    )
    return ServiceCallOut(
        value=result.value,
        cached=result.cached,
        expires_at=result.expires_at,
    )


@router.get(
    "/{app_id}/events",
    response_model=AppEventsResponse,
    summary="Read MiniApp inbox events",
    description=(
        "Events delivered to this app, ordered by cursor. Pass the last seen "
        "``latest_id`` as ``since`` on the next poll. Requires view permission."
    ),
)
async def list_app_events(
    app_id: str,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppEventsResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    events, latest = service.list_events(row, since=since, limit=limit)
    return AppEventsResponse(
        events=[
            AppEventOut(
                event_id=item.event_id,
                event_type=item.event_type,
                source=item.source,
                summary=item.summary,
                payload=item.payload,
                created_at=item.created_at,
                acknowledged_at=item.acknowledged_at,
            )
            for item in events
        ],
        latest_id=latest,
    )


@router.post(
    "/{app_id}/events/ack",
    response_model=AckEventsOut,
    summary="Acknowledge MiniApp inbox events",
    description="Marks events as processed; requires view permission.",
)
async def ack_app_events(
    app_id: str,
    body: AckEventsBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AckEventsOut:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return AckEventsOut(acknowledged=service.ack_events(row, body.event_ids))


@router.get(
    "/{app_id}/webhooks",
    response_model=list[WebhookOut],
    summary="List outbound webhooks",
    description=(
        "Owner-declared outbound webhook destinations for this MiniApp. The "
        "signing secret is only returned at creation or rotation."
    ),
)
async def list_app_webhooks(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> list[WebhookOut]:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return [WebhookOut(**view.__dict__) for view in _webhooks(server, service).list_for_app(row)]


@router.post(
    "/{app_id}/webhooks",
    response_model=WebhookCreatedOut,
    status_code=201,
    summary="Create an outbound webhook",
    description=(
        "Declares one destination URL, an event-type allowlist and a generated "
        "HMAC signing secret. Requires owner or admin and the events.emit capability "
        "for the page to actually emit."
    ),
)
async def create_app_webhook(
    app_id: str,
    body: CreateWebhookBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> WebhookCreatedOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    view, secret = await _webhooks(server, service).create(
        row,
        name=body.name,
        url=body.url,
        event_types=body.event_types,
        enabled=body.enabled,
        actor_user_id=user.id,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.webhook.create",
        target=app_id,
        payload={"webhook_id": view.webhook_id, "event_types": list(view.event_types)},
    )
    return WebhookCreatedOut(**view.__dict__, secret=secret)


@router.patch(
    "/{app_id}/webhooks/{webhook_id}",
    response_model=WebhookUpdatedOut,
    summary="Update an outbound webhook",
    description="Requires owner or admin. Set rotate_secret to issue a new signing secret.",
)
async def update_app_webhook(
    app_id: str,
    webhook_id: str,
    body: UpdateWebhookBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> WebhookUpdatedOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    view, secret = await _webhooks(server, service).update(
        row,
        webhook_id,
        name=body.name,
        url=body.url,
        event_types=body.event_types,
        enabled=body.enabled,
        rotate_secret=body.rotate_secret,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.webhook.update",
        target=app_id,
        payload={"webhook_id": webhook_id, "secret_rotated": secret is not None},
    )
    return WebhookUpdatedOut(**view.__dict__, secret=secret)


@router.delete(
    "/{app_id}/webhooks/{webhook_id}",
    response_model=DeleteWebhookOut,
    summary="Delete an outbound webhook",
    description="Removes the destination, its subscription and its internal event source.",
)
async def delete_app_webhook(
    app_id: str,
    webhook_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DeleteWebhookOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    deleted = _webhooks(server, service).delete(row, webhook_id)
    if deleted:
        _audit(
            server,
            actor=f"user:{user.id}",
            action="light_app.webhook.delete",
            target=app_id,
            payload={"webhook_id": webhook_id},
        )
    return DeleteWebhookOut(deleted=deleted)


@router.post(
    "/{app_id}/events/emit",
    response_model=EmitEventOut,
    summary="Emit an outbound MiniApp event",
    description=(
        "Ingests one page-originated event into the owner-declared outbound "
        "webhooks. Requires the events.emit capability (owner-enabled). "
        "Delivery is at-least-once; use client_event_id for idempotent retries."
    ),
)
async def emit_app_event(
    app_id: str,
    body: EmitEventBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> EmitEventOut:
    service, row, _decision = _authorize(server, user, app_id, Action.EMIT_EVENT)
    event_service = (
        server.app_runtime.external_event_service if server.app_runtime is not None else None
    )
    if event_service is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "external event service is not started")
    result = await _webhooks(server, service).emit(
        row,
        event_service,
        event_type=body.event_type,
        payload=body.payload,
        summary=body.summary,
        client_event_id=body.client_event_id,
    )
    return EmitEventOut(**result)


def _ingress_out(server: OctopServer, view: IngressView) -> IngressOut:
    return IngressOut(
        **view.__dict__,
        hook_url=_ingress_hook_url(server, view.source_id),
    )


@router.get(
    "/{app_id}/ingress",
    response_model=list[IngressOut],
    summary="List app-owned inbound endpoints",
    description=(
        "Lists inbound sources owned by this MiniApp. Each endpoint owns its "
        "event source, signing secret and subscription; the secret is only "
        "returned at creation or rotation."
    ),
)
async def list_app_ingress(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> list[IngressOut]:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return [_ingress_out(server, view) for view in _ingress(server, service).list(row)]


@router.post(
    "/{app_id}/ingress",
    response_model=IngressCreatedOut,
    status_code=201,
    summary="Create an app-owned inbound endpoint",
    description=(
        "Creates an app-ingress source, subscription and HMAC secret. Senders "
        "POST to hook_url with X-Octop-Timestamp and X-Octop-Signature. "
        "Requires owner or admin."
    ),
)
async def create_app_ingress(
    app_id: str,
    body: CreateIngressBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> IngressCreatedOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    view, secret = await _ingress(server, service).create(
        row,
        name=body.name,
        mode=body.mode,
        event_type=body.event_type,
        mapping=body.mapping,
        max_per_minute=body.max_per_minute,
        enabled=body.enabled,
        actor_user_id=user.id,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.ingress.create",
        target=app_id,
        payload={"ingress_id": view.ingress_id, "mode": view.mode},
    )
    return IngressCreatedOut(
        **_ingress_out(server, view).model_dump(),
        secret=secret,
    )


@router.patch(
    "/{app_id}/ingress/{ingress_id}",
    response_model=IngressOut,
    summary="Update an app-owned inbound endpoint",
    description="Requires owner or admin.",
)
async def update_app_ingress(
    app_id: str,
    ingress_id: str,
    body: UpdateIngressBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> IngressOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    view = await _ingress(server, service).update(
        row,
        ingress_id,
        name=body.name,
        mode=body.mode,
        event_type=body.event_type,
        mapping=body.mapping,
        max_per_minute=body.max_per_minute,
        enabled=body.enabled,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.ingress.update",
        target=app_id,
        payload={"ingress_id": ingress_id, "enabled": view.enabled},
    )
    return _ingress_out(server, view)


@router.delete(
    "/{app_id}/ingress/{ingress_id}",
    response_model=DeleteWebhookOut,
    summary="Delete an app-owned inbound endpoint",
    description="Removes the endpoint, its subscription and its event source.",
)
async def delete_app_ingress(
    app_id: str,
    ingress_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DeleteWebhookOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    deleted = _ingress(server, service).delete(row, ingress_id)
    if deleted:
        _audit(
            server,
            actor=f"user:{user.id}",
            action="light_app.ingress.delete",
            target=app_id,
            payload={"ingress_id": ingress_id},
        )
    return DeleteWebhookOut(deleted=deleted)


@router.post(
    "/{app_id}/ingress/{ingress_id}/rotate-secret",
    response_model=IngressSecretOut,
    summary="Rotate an inbound signing secret",
    description="Returns the new secret once. Requires owner or admin.",
)
async def rotate_app_ingress_secret(
    app_id: str,
    ingress_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> IngressSecretOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    ingress = _ingress(server, service)
    secret = await ingress.rotate_secret(row, ingress_id)
    view = next(item for item in ingress.list(row) if item.ingress_id == ingress_id)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.ingress.rotate_secret",
        target=app_id,
        payload={"ingress_id": ingress_id},
    )
    return IngressSecretOut(
        secret=secret,
        hook_url=_ingress_hook_url(server, view.source_id),
    )


@router.post(
    "/{app_id}/ingress/{ingress_id}/test",
    response_model=TestIngressOut,
    summary="Send a test event through an inbound endpoint",
    description=(
        "Ingests one event directly (no HMAC) and lets the normal delivery "
        "worker write it. Requires owner or admin."
    ),
)
async def test_app_ingress(
    app_id: str,
    ingress_id: str,
    body: TestIngressBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> TestIngressOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_INGRESS)
    event_service = (
        server.app_runtime.external_event_service if server.app_runtime is not None else None
    )
    if event_service is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "external event service is not started")
    result = await _ingress(server, service).test_event(
        row,
        ingress_id,
        event_service,
        event_type=body.event_type,
        payload=body.payload,
    )
    if server.app_runtime is not None:
        worker = server.app_runtime.external_event_worker
        if worker is not None:
            worker.wake()
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.ingress.test",
        target=app_id,
        payload={"ingress_id": ingress_id, "event_id": result.event.event_id},
    )
    return TestIngressOut(
        event_id=result.event.event_id,
        event_type=result.event.event_type,
        deliveries=result.delivery_count,
        rate_limited=result.rate_limited,
    )


@router.post(
    "/{app_id}/services/{name}/image",
    response_model=ServiceImageOut,
    summary="Fetch and store a service image",
    description=(
        "Fetches a public image inside the service's allowed URL prefixes, stores "
        "it as an app asset, and returns the asset name for octopApp.asset(). "
        "Requires view permission and the external.fetch capability."
    ),
)
async def fetch_app_service_image(
    app_id: str,
    name: str,
    body: ServiceImageBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ServiceImageOut:
    service, row, _decision = _authorize(server, user, app_id, Action.SERVICE_CALL)
    asset = await service.fetch_service_image(row, name=name, url=body.url)
    return ServiceImageOut(asset=asset)


@router.patch(
    "/{app_id}/tables/{table_key}",
    response_model=AppTableOut,
    summary="Set table visibility and row scope",
    description=(
        "Expose or hide one table on the app's public read-only link, choose "
        "whether members with participate permission can see it, and/or set "
        "its row scope: ``app`` (every collaborator sees all rows) or ``user`` "
        "(each user sees only their own rows; the public link hides the table). "
        "Requires owner or admin."
    ),
)
async def set_app_table_visibility(
    app_id: str,
    table_key: str,
    body: TableVisibilityBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppTableOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_APP_SETTINGS)
    if body.row_scope is not None:
        await service.set_table_row_scope(row, table=table_key, row_scope=body.row_scope)
    if body.member_visible is not None:
        await service.set_table_member_visibility(
            row,
            table=table_key,
            member_visible=body.member_visible,
        )
    view = await service.set_table_visibility(
        row,
        table=table_key,
        public_visible=body.public_visible,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.table.visibility",
        target=app_id,
        payload={
            "table": table_key,
            "public_visible": body.public_visible,
            "member_visible": body.member_visible,
            "row_scope": body.row_scope,
        },
    )
    return await _table_out(service, row, view)


@router.get(
    "/{app_id}/html",
    summary="Get the app HTML",
    description=(
        "Returns the single-file HTML written by the agent. Render it in a sandboxed "
        'iframe (`sandbox="allow-scripts"`); an empty body means no UI has been generated yet.'
    ),
    responses={200: {"content": {"text/html": {}}}},
)
async def get_app_html(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    html = await service.read_html(row, validate=True)
    # The HTML is agent-generated. Render it in the dashboard's sandboxed
    # iframe; a direct visit must not run its scripts on the API origin, so
    # mirror the workspace preview sandbox headers.
    return Response(
        content=html or "",
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{app_id}/security-events",
    status_code=204,
    summary="Report a page sandbox security event",
    description=(
        "Records a Content-Security-Policy violation reported by a MiniApp page. "
        "The dashboard forwards these from inside the sandbox; the URI's query "
        "string and fragment are stripped before the event is audited. Requires "
        "view permission and is rate-limited per app and user."
    ),
)
async def report_security_event(
    app_id: str,
    body: SecurityEventIn,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    _service_row, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    async with csp_violation_slot(f"{row.app_id}:{user.id}"):
        payload = {
            "kind": body.kind,
            "directive": body.directive,
            "blocked_uri": sanitize_blocked_uri(body.blocked_uri),
            "disposition": body.disposition,
        }
        if body.kind == "sandbox_integrity":
            METRICS.inc("light_app_sandbox_integrity_total")
            action = "light_app.sandbox_integrity"
        else:
            METRICS.inc("light_app_csp_violation_total")
            action = "light_app.csp_violation"
        _audit(
            server,
            actor=f"user:{user.id}",
            action=action,
            target=row.app_id,
            payload=payload,
        )
    return Response(status_code=204)


@router.get(
    "/{app_id}/files",
    response_model=AppFilesResponse,
    summary="List app files",
    description=(
        "Lists the files that make up the app (interface, every table's CSV and "
        "schema, and the manifest) with their sizes, so the owner can see where "
        "the app lives."
    ),
)
async def list_app_files(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppFilesResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    files = await service.list_files(row)
    return AppFilesResponse(
        files=[AppFileOut(name=item.name, path=item.path, size=item.size) for item in files],
    )


class AssetOut(BaseModel):
    name: str = Field(description="Asset file name inside the app's assets directory.")
    size: int = Field(default=0, description="Asset size in bytes.")


class AssetListResponse(BaseModel):
    assets: list[AssetOut]


@router.get(
    "/{app_id}/assets",
    response_model=AssetListResponse,
    summary="List app assets",
    description="Lists the images stored next to the app (covers, icons).",
)
async def list_app_assets(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AssetListResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    names = await service.list_assets(row)
    return AssetListResponse(assets=[AssetOut(name=name) for name in names])


@router.post(
    "/{app_id}/assets",
    response_model=AssetOut,
    status_code=201,
    summary="Upload an app asset",
    description=(
        "Stores a PNG, JPEG, WebP or GIF image (at most 5 MB) next to the app. "
        "The app page cannot load remote images, so covers and icons are "
        "stored here. Requires edit_data."
    ),
)
async def upload_app_asset(
    app_id: str,
    file: UploadFile = File(...),  # noqa: B008
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AssetOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_ASSETS)
    data = await read_upload_capped(
        file,
        max_bytes=MAX_ASSET_BYTES,
        code=ErrorCode.APP_SOURCE_INVALID,
    )
    name = await service.save_asset(row, file.filename or "asset.png", data)
    return AssetOut(name=name, size=len(data))


@router.get(
    "/{app_id}/assets/{name}",
    summary="Fetch an app asset",
    description=(
        "Serves one stored image. The app page fetches it through this "
        "endpoint, so the app ACL applies. Requires view."
    ),
)
async def get_app_asset(
    app_id: str,
    name: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    mime, data = await service.read_asset(row, name)
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=300"},
    )


def _svg_response(svg: str) -> Response:
    """Serve agent-drawn SVG markup as an image.

    The markup is validated on write; the sandboxing CSP is the second layer
    that keeps a preview inert when it is opened directly.
    """
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Content-Security-Policy": (
                "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get(
    "/{app_id}/preview",
    summary="Fetch an app preview image",
    description=(
        "Serves the app's preview image: the SVG cover the agent draws for "
        "the app list. Requires view."
    ),
)
async def get_app_preview(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    svg = await service.read_preview(row)
    if svg is None:
        raise OctopError(ErrorCode.APP_NOT_FOUND, "app has no preview image")
    try:
        clean = validate_preview_svg(svg)
    except ValueError:
        # The file may have been written straight into the workspace, so the
        # markup is checked again before it is served.
        raise OctopError(ErrorCode.APP_NOT_FOUND, "app has no preview image") from None
    return _svg_response(clean)


@router.get(
    "/{app_id}/rows",
    response_model=AppRowsResponse,
    summary="Read app rows",
    description=(
        "Reads one page of rows. Repeat ``filter=column=value`` to narrow the "
        "result (all filters must match); use ``sort=column`` or "
        "``sort=-column`` to order it. Sensitive columns cannot be filtered or "
        "sorted for a viewer, because their values are masked anyway."
    ),
)
async def get_app_rows(
    app_id: str,
    limit: int = Query(200, ge=1, le=500, description="Rows to return."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
    table: str = Query(MAIN_TABLE_KEY, description="Table key to read from."),
    filter_: list[str] = Query(
        default=[],
        alias="filter",
        description="Repeatable ``column=value`` filter; every filter must match.",
    ),
    sort: str | None = Query(
        default=None,
        description="Column to sort by; prefix with ``-`` for descending.",
    ),
    expand_refs: bool = Query(
        default=False,
        description="Resolve display labels for reference columns into ``refs``.",
    ),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppRowsResponse:
    service, row, decision = _authorize(server, user, app_id, Action.VIEW_ROW, table_key=table)
    redact_pii = decision.pii_policy != "visible"
    include_all_rows = decision.row_scope == "all"
    filters = _parse_row_filters(filter_)
    if filters or sort:
        schema, rows, row_meta, total = await service.query_rows_detailed(
            row,
            limit=limit,
            offset=offset,
            table=table,
            filters=filters,
            sort=sort,
            pii_policy=decision.pii_policy,
            viewer_user_id=user.id,
            include_all_rows=include_all_rows,
        )
    else:
        schema, rows, row_meta, total = await service.read_rows_detailed(
            row,
            limit=limit,
            offset=offset,
            table=table,
            pii_policy=decision.pii_policy,
            viewer_user_id=user.id,
            include_all_rows=include_all_rows,
        )
    refs = (
        await service.reference_labels(
            row,
            schema,
            rows,
            viewer_user_id=user.id,
            include_all_rows=include_all_rows,
            redact_pii=redact_pii,
        )
        if expand_refs
        else {}
    )
    masked_pii_rows = [
        meta["row_id"] for meta in row_meta if meta.get("pii_masked") and meta.get("row_id")
    ]
    pii_masked = any(meta.get("pii_masked") for meta in row_meta)
    return AppRowsResponse(
        columns=_columns(schema),
        rows=rows,
        row_meta=[RowMeta(**meta) for meta in row_meta],
        total=total,
        limit=limit,
        offset=offset,
        table=table,
        pii_columns=(
            [column.name for column in schema.columns if column.pii] if pii_masked else []
        ),
        masked_pii_rows=masked_pii_rows,
        refs=refs,
    )


@router.post(
    "/{app_id}/rows",
    response_model=AppendRowResponse,
    summary="Append a row",
    description="Requires the edit_data or edit_structure permission.",
)
async def append_app_row(
    app_id: str,
    body: AppendRowBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppendRowResponse:
    service, row, _decision = _authorize(
        server, user, app_id, Action.CREATE_ROW, table_key=body.table
    )
    index, stored = await service.append_row_record(
        row,
        body.row,
        table=body.table,
        client_request_id=body.client_request_id,
        created_by=user.id,
    )
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_row_write_total")
    return AppendRowResponse(
        row_index=index,
        row=stored.values,
        table=body.table,
        row_id=stored.row_id,
        version=stored.version,
    )


@router.patch(
    "/{app_id}/rows/{row_id}",
    response_model=UpdateRowResponse,
    summary="Update a row",
    description=(
        "Replaces one row. Pass expected_version to reject a stale edit with 409. "
        "Requires edit_data."
    ),
)
async def update_app_row(
    app_id: str,
    row_id: str,
    body: UpdateRowBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> UpdateRowResponse:
    service, row, decision = _authorize(server, user, app_id, Action.UPDATE_ROW, row_id=row_id)
    result = await service.update_row(
        row,
        row_id,
        body.row,
        expected_version=body.expected_version,
        viewer_user_id=user.id,
        include_all_rows=decision.row_scope == "all",
        actor_user_id=user.id,
    )
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_row_write_total")
    return UpdateRowResponse(
        row_id=str(result["row_id"]),
        row=result["values"],
        version=int(result["version"]),
    )


@router.delete(
    "/{app_id}/rows/{row_id}",
    response_model=DeleteRowResponse,
    summary="Delete a row",
    description="Deletes one row. Pass expected_version to reject a stale edit. Requires edit_data.",
)
async def delete_app_row(
    app_id: str,
    row_id: str,
    expected_version: int | None = Query(default=None, description="Reject when the row changed."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DeleteRowResponse:
    service, row, decision = _authorize(server, user, app_id, Action.DELETE_ROW, row_id=row_id)
    deleted = await service.delete_row(
        row,
        row_id,
        expected_version=expected_version,
        viewer_user_id=user.id,
        include_all_rows=decision.row_scope == "all",
        actor_user_id=user.id,
    )
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_row_write_total")
    return DeleteRowResponse(deleted=deleted)


@router.get(
    "/{app_id}/rows/{row_id}/history",
    response_model=RowHistoryResponse,
    summary="Get row change history",
    description=(
        "Returns the append-only change ledger for one row, newest first. "
        "Sensitive columns are masked for viewers; owners and admins see the "
        "stored values. Requires view permission."
    ),
)
async def get_app_row_history(
    app_id: str,
    row_id: str,
    limit: int = Query(default=100, ge=1, le=500, description="History entries to return."),
    offset: int = Query(default=0, ge=0, description="Entries to skip."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> RowHistoryResponse:
    service, row, decision = _authorize(server, user, app_id, Action.VIEW_HISTORY, row_id=row_id)
    row_record = service.get_data_row(app_id, row_id)
    entries = await service.row_history(
        row,
        row_id,
        limit=limit,
        offset=offset,
        redact_pii=not can_view_pii(principal_of(user), decision, row_record),
        viewer_user_id=user.id,
        include_all_rows=decision.row_scope == "all",
    )
    return RowHistoryResponse(
        entries=[RowHistoryEntry(**entry) for entry in entries],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{app_id}/aggregate",
    response_model=AggregateResponse,
    summary="Aggregate app rows",
    description=(
        "Groups one table by a column and aggregates a numeric metric server-side. "
        "Use agg=count without a metric, or sum/avg/min/max with metric. "
        "Sensitive columns cannot be grouped, measured or filtered. Requires view."
    ),
)
async def aggregate_app_rows(
    app_id: str,
    table: str = Query(default=MAIN_TABLE_KEY, description="Table key to aggregate."),
    group_by: str | None = Query(default=None, description="Column to group by."),
    metric: str | None = Query(default=None, description="Numeric column to aggregate."),
    agg: str = Query(default="count", description="sum, avg, count, min or max."),
    filter_: list[str] = Query(
        default=[],
        alias="filter",
        description="Repeatable ``column=value`` filter; every filter must match.",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum groups to return."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AggregateResponse:
    service, row, decision = _authorize(server, user, app_id, Action.AGGREGATE, table_key=table)
    result = await service.aggregate_rows(
        row,
        table=table,
        group_by=group_by,
        metric=metric,
        agg=agg,
        filters=_parse_row_filters(filter_),
        limit=limit,
        viewer_user_id=user.id,
        include_all_rows=decision.row_scope == "all",
    )
    return AggregateResponse(**result)


@router.get(
    "/{app_id}/export",
    summary="Export a table as CSV",
    description=(
        "Renders the current rows of one table as a CSV snapshot. Requires view. "
        "This is an export, not the live storage."
    ),
)
async def export_app_table(
    app_id: str,
    table: str = Query(default=MAIN_TABLE_KEY, description="Table key to export."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, decision = _authorize(server, user, app_id, Action.EXPORT, table_key=table)
    redact_pii = decision.pii_policy != "visible"
    filename, content = await service.export_rows(
        row,
        table=table,
        redact_pii=redact_pii,
        viewer_user_id=user.id,
        include_all_rows=decision.row_scope == "all",
    )
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_export_total")
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.export",
        target=app_id,
        payload={"table": table, "bytes": len(content), "pii_masked": redact_pii},
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put(
    "/{app_id}/tables/{table_key}/rules",
    response_model=AppTableOut,
    summary="Set declarative table rules",
    description=(
        "v1 supports a state machine: ``state_field`` plus allowed "
        "``transitions``. Invalid transitions are rejected on row update. "
        "Requires owner or admin."
    ),
)
async def set_app_table_rules(
    app_id: str,
    table_key: str,
    body: TableRulesBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppTableOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_APP_SETTINGS)
    view = await service.set_table_rules(row, table=table_key, rules=body.rules)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.table.rules",
        target=app_id,
        payload={"table": table_key, "rules": body.rules},
    )
    return await _table_out(service, row, view)


@router.get(
    "/{app_id}/backup",
    summary="Download an app backup",
    description=(
        "Bundles app.html, every table's schema and rows, and the assets into "
        "one zip. Restore it with POST /apps/restore. Requires owner or admin "
        "because the archive contains unmasked data, including rows from "
        "user-scoped tables."
    ),
    responses={200: {"content": {"application/zip": {}}}},
)
async def backup_app(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_APP_SETTINGS)
    filename, content = await service.backup_archive(row)
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_export_total")
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.backup",
        target=app_id,
        payload={"bytes": len(content)},
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch(
    "/{app_id}/schema",
    response_model=AppDetail,
    summary="Rename, drop or retype columns",
    description=(
        "Migrates every existing row to the new schema in one transaction. The "
        "change is rejected when a row cannot be represented (for example a "
        "non-numeric value when changing a column to integer). Requires "
        "edit_structure."
    ),
)
async def alter_app_schema(
    app_id: str,
    body: AlterSchemaBody,
    table: str = Query(default=MAIN_TABLE_KEY, description="Table key to alter."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service, row, decision = _authorize(server, user, app_id, Action.MANAGE_STRUCTURE)
    await service.alter_schema(
        row,
        table=table,
        renames=body.renames,
        drops=body.drops,
        types={name: spec.model_dump(exclude_none=True) for name, spec in body.types.items()},
        pii=body.pii,
    )
    updated = service.get(app_id)
    schema = await service.read_schema(updated, table)
    base = _summary(service, updated, decision.role or "view")
    return AppDetail(
        **base.model_dump(exclude={"has_html", "has_preview"}),
        columns=_columns(schema),
        has_html=await service.has_html(updated),
        has_preview=await service.has_preview(updated),
        tables=await _table_outs(service, updated),
    )


@router.patch(
    "/{app_id}",
    response_model=AppDetail,
    summary="Update app name or description",
    description="Requires the edit_structure permission (owner or admin).",
)
async def update_app(
    app_id: str,
    body: UpdateAppBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service, row, decision = _authorize(server, user, app_id, Action.MANAGE_STRUCTURE)
    row = await service.update_metadata(row, name=body.name, description=body.description)
    schema = await service.read_schema(row)
    base = _summary(service, row, decision.role or "view")
    return AppDetail(
        **base.model_dump(exclude={"has_html", "has_preview"}),
        columns=_columns(schema),
        has_html=await service.has_html(row),
        has_preview=await service.has_preview(row),
        tables=await _table_outs(service, row),
    )


@router.put(
    "/{app_id}/capabilities",
    response_model=AppDetail,
    summary="Update MiniApp page capabilities",
    description=(
        "Replaces the capability manifest that gates what the sandboxed app page may "
        "ask the host to do. This does not restrict the agent tools or the dashboard's "
        "own data panel. Requires owner or admin."
    ),
)
async def update_app_capabilities(
    app_id: str,
    body: CapabilitiesBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_APP_SETTINGS)
    row = service.set_capabilities(row, body.capabilities)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.capabilities.update",
        target=app_id,
        payload={"capabilities": service.capability_list(row)},
    )
    return await _app_detail(service, row)


@router.post(
    "/{app_id}/restore-html",
    response_model=AppDetail,
    summary="Restore the previous MiniApp HTML",
    description=(
        "Puts back the HTML replaced by the last app_write_html. Calling it again "
        "swaps the two versions. Requires edit_structure."
    ),
)
async def restore_app_html(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_STRUCTURE)
    restored = await service.restore_html(row)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.html.restore",
        target=app_id,
    )
    return await _app_detail(service, restored)


@router.delete(
    "/{app_id}",
    response_model=DeleteAppResponse,
    summary="Delete a MiniApp",
    description="Deletes the app files and metadata. Requires owner or admin.",
)
async def delete_app(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> DeleteAppResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.DELETE_APP)
    await service.delete(row)
    return DeleteAppResponse(deleted=row.app_id)


@router.get(
    "/{app_id}/shares",
    response_model=SharesResponse,
    summary="List collaborators",
    description="Requires owner or admin.",
)
async def list_app_shares(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SharesResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_SHARES)
    return SharesResponse(
        shares=[ShareOut(**asdict(view)) for view in service.list_share_views(row)]
    )


@router.put(
    "/{app_id}/shares",
    response_model=SharesResponse,
    summary="Replace collaborators",
    description="Replaces the full collaborator list. Requires owner or admin.",
)
async def set_app_shares(
    app_id: str,
    body: SetSharesBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SharesResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_SHARES)
    views = service.set_shares(row, [(share.username, share.permission) for share in body.shares])
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.share",
        target=app_id,
        payload={"shares": [share.model_dump() for share in body.shares]},
    )
    return SharesResponse(shares=[ShareOut(**asdict(view)) for view in views])


class PublicationOut(BaseModel):
    active: bool = Field(description="Whether a non-revoked, non-expired link exists.")
    url: str | None = Field(default=None, description="Public URL when a token was just created.")
    token: str | None = Field(
        default=None, description="Plaintext token, returned only on creation."
    )
    expires_at: int | None = Field(default=None, description="Unix expiry, or null for no expiry.")
    published_at: int | None = Field(
        default=None,
        description="Unix time of the frozen public version; null for a legacy link.",
    )
    snapshot_stale: bool = Field(
        default=False,
        description="Whether the app changed since the public snapshot was taken.",
    )
    stale_reasons: list[str] = Field(
        default_factory=list,
        description="What changed: html, schema, not_published or workspace.",
    )


class CreatePublicationBody(BaseModel):
    ttl_days: int = Field(
        default=0,
        ge=0,
        le=3650,
        description="Link lifetime in days; 0 means no expiry.",
    )


@router.get(
    "/{app_id}/publication",
    response_model=PublicationOut,
    summary="Get the public link status",
    description="Read-only public link status. Requires view.",
)
async def get_app_publication(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> PublicationOut:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    publication = service.get_publication(row)
    active = (
        publication is not None
        and publication.revoked_at is None
        and (publication.expires_at is None or publication.expires_at > int(time.time()))
    )
    status = await service.publication_status(row)
    return PublicationOut(
        active=active,
        expires_at=publication.expires_at if publication else None,
        published_at=status.published_at,
        snapshot_stale=status.stale,
        stale_reasons=list(status.reasons),
    )


@router.post(
    "/{app_id}/publication",
    response_model=PublicationOut,
    summary="Create or rotate the public link",
    description=(
        "Creates a read-only public link and returns the token once. "
        "Rotating the link invalidates the previous one. Requires owner or admin."
    ),
)
async def create_app_publication(
    app_id: str,
    body: CreatePublicationBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> PublicationOut:
    service, row, _decision = _authorize(server, user, app_id, Action.PUBLISH)
    token, expires_at = await service.publish(
        row,
        user_id=user.id,
        rotate=True,
        ttl_days=body.ttl_days,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.publication.create",
        target=app_id,
        payload={"expires_at": expires_at},
    )
    status = await service.publication_status(row)
    return PublicationOut(
        active=True,
        url=_public_link(server, token),
        token=token,
        expires_at=expires_at,
        published_at=status.published_at,
        snapshot_stale=status.stale,
        stale_reasons=list(status.reasons),
    )


@router.post(
    "/{app_id}/publication/publish",
    response_model=PublicationOut,
    summary="Publish the current MiniApp version",
    description=(
        "Refreshes the public snapshot (HTML and public table structure) without "
        "rotating the token. Rows remain live. Requires owner or admin."
    ),
)
async def publish_app_publication(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> PublicationOut:
    service, row, _decision = _authorize(server, user, app_id, Action.PUBLISH)
    token, expires_at = await service.publish(row, user_id=user.id, rotate=False)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.publication.publish",
        target=app_id,
    )
    status = await service.publication_status(row)
    return PublicationOut(
        active=True,
        url=_public_link(server, token),
        expires_at=expires_at,
        published_at=status.published_at,
        snapshot_stale=status.stale,
        stale_reasons=list(status.reasons),
    )


@router.delete(
    "/{app_id}/publication",
    response_model=PublicationOut,
    summary="Revoke the public link",
    description="Revokes the current link; an already shared URL returns 404. Requires owner or admin.",
)
async def revoke_app_publication(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> PublicationOut:
    service, row, _decision = _authorize(server, user, app_id, Action.PUBLISH)
    service.revoke_publication(row)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.publication.revoke",
        target=app_id,
    )
    return PublicationOut(active=False)


class TransferAppBody(BaseModel):
    agent_id: str = Field(
        min_length=1,
        description="The agent that should host the app from now on.",
    )


@router.post(
    "/{app_id}/transfer",
    response_model=AppDetail,
    summary="Move an app to another agent",
    description=(
        "Copies the app files into another agent's workspace and moves the "
        "app there. Use it to hand an app over to a different expert. "
        "Requires owner or admin."
    ),
)
async def transfer_app(
    app_id: str,
    body: TransferAppBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppDetail:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_MEMBERS)
    moved = await service.transfer(row, target_agent_id=body.agent_id, actor_user_id=user.id)
    schema = await service.read_schema(moved)
    base = _summary(service, moved, "edit_structure")
    return AppDetail(
        **base.model_dump(exclude={"has_html", "has_preview"}),
        columns=_columns(schema),
        has_html=await service.has_html(moved),
        has_preview=await service.has_preview(moved),
        tables=await _table_outs(service, moved),
    )


class AskAssistantBody(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=4000,
        description="The question about this app's data.",
    )


class AssistantChartSeriesOut(BaseModel):
    key: str = Field(description="Data key inside each chart point.")
    label: str = Field(description="Human-readable series name.")


class AssistantChartOut(BaseModel):
    type: Literal["bar", "line", "pie", "area", "scatter", "stacked_bar"] = Field(
        description="Chart type the dashboard should render."
    )
    title: str = Field(description="Chart title.")
    x: str = Field(description="Column used for the x axis.")
    x_label: str = Field(default="", description="Human-readable x column label.")
    series: list[AssistantChartSeriesOut] = Field(description="One entry per numeric series.")
    data: list[dict[str, Any]] = Field(
        description="Chart points; each has `x` plus one key per series."
    )
    sampled: bool = Field(
        default=False,
        description="True when the chart is based on the first 500 rows only.",
    )
    sample_size: int = Field(default=0, description="Rows used to build the chart.")
    matched_rows: int = Field(default=0, description="Rows matching the chart filters.")
    truncated: bool = Field(
        default=False,
        description="True when the series was capped to the requested point limit.",
    )


class AssistantCitationOut(BaseModel):
    table: str = Field(description="Table key the cited row belongs to.")
    row_id: str = Field(description="Cited row id.")


class AssistantProcessStepOut(BaseModel):
    kind: str = Field(description='"thinking" or "tool".')
    text: str | None = Field(
        default=None,
        description="Thinking text when kind=thinking.",
    )
    call_id: str | None = Field(
        default=None,
        description="Model tool-call id when kind=tool.",
    )
    name: str | None = Field(
        default=None,
        description="Tool name when kind=tool.",
    )
    arguments: dict[str, Any] | None = Field(
        default=None,
        description="Tool arguments when kind=tool.",
    )
    result: str | None = Field(
        default=None,
        description="Tool result (clipped) when kind=tool.",
    )
    status: str | None = Field(
        default=None,
        description='"ok" or "error" when kind=tool.',
    )


class AssistantMessageOut(BaseModel):
    role: str = Field(description="user or assistant.")
    content: str
    created_at: int
    charts: list[AssistantChartOut] = Field(
        default_factory=list,
        description="Chart specs attached to an assistant answer, if any.",
    )
    process: list[AssistantProcessStepOut] = Field(
        default_factory=list,
        description="Ordered thinking and tool-call steps for this answer.",
    )
    citations: list[AssistantCitationOut] = Field(
        default_factory=list,
        description="Rows cited by this answer.",
    )


class AssistantHistoryResponse(BaseModel):
    messages: list[AssistantMessageOut] = Field(
        description="The caller's conversation with this app's assistant, oldest first.",
    )


@router.get(
    "/{app_id}/assistant/history",
    response_model=AssistantHistoryResponse,
    summary="Get app assistant history",
    description="The caller's own conversation with this app's assistant, oldest first.",
)
async def get_app_assistant_history(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AssistantHistoryResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.AI)
    turns = _assistant(server, service).history(row, user.id)
    return AssistantHistoryResponse(
        messages=[
            AssistantMessageOut(
                role=t.role,
                content=t.content,
                created_at=t.created_at,
                charts=[AssistantChartOut.model_validate(chart) for chart in t.charts],
                process=[AssistantProcessStepOut.model_validate(step) for step in t.process],
                citations=[AssistantCitationOut.model_validate(item) for item in t.citations],
            )
            for t in turns
        ],
    )


@router.post(
    "/{app_id}/assistant",
    summary="Ask the app assistant (SSE)",
    description=(
        "Streams SSE frames (event: chunk) whose data carries "
        "{type: token|thinking|tool_start|tool_end|chart|done|error}. Read-only "
        "over the app's data; consumes the owning agent's model quota. Requires "
        "the view permission."
    ),
)
async def ask_app_assistant(
    app_id: str,
    body: AskAssistantBody,
    request: Request,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> StreamingResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.AI)
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_assistant_total")
    question = body.question.strip()
    if not question:
        raise OctopError(ErrorCode.APP_QUESTION_INVALID, "question must not be empty")
    assistant = _assistant(server, service)
    locale: Locale = resolve_request_locale(request)
    # Resolve the agent and model before streaming so availability problems
    # (agent not running, no usable model) surface as regular HTTP errors.
    prepared = assistant.prepare(row, user_id=user.id, question=question, locale=locale)

    # Take the slot before the streaming response starts, so a busy app gets
    # a real 429 instead of a broken 200 stream.
    acquire_app_llm(row.app_id)

    async def gen() -> AsyncIterator[str]:
        try:
            async for event in assistant.stream(prepared):
                yield format_sse("chunk", event)
        finally:
            release_app_llm(row.app_id)

    return StreamingResponse(gen(), media_type="text/event-stream")


class GenerateBody(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=8000,
        description="The instruction or text the model should process.",
    )
    system: str = Field(
        default="",
        max_length=4000,
        description="Optional extra system instruction supplied by the app.",
    )


class GenerateResponse(BaseModel):
    text: str = Field(description="Generated text, truncated to 16k characters.")
    model: str = Field(description="Model reference that answered.")
    input_tokens: int = Field(description="Prompt tokens billed to the owning agent.")
    output_tokens: int = Field(description="Completion tokens billed to the owning agent.")


@router.post(
    "/{app_id}/generate",
    response_model=GenerateResponse,
    summary="Generate text with the app's AI model",
    description=(
        "One-shot, non-streaming text generation using the owning agent's "
        "default model. App HTML calls it through `octopApp.generate(prompt, "
        "{system})`; the iframe never sees a JWT. Requires the view permission "
        "and consumes the owning agent's model quota. Public share links do not "
        "expose this endpoint."
    ),
)
async def generate_app_text(
    app_id: str,
    body: GenerateBody,
    request: Request,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> GenerateResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.AI)
    service.require_capability(row, app_capabilities.CAP_AI_GENERATE)
    prompt = body.prompt.strip()
    if not prompt:
        raise OctopError(ErrorCode.APP_PROMPT_INVALID, "prompt must not be empty")
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_generate_total")
    locale = resolve_request_locale(request)
    async with app_llm_slot(row.app_id):
        generated = await _generator(server).generate(
            row,
            user_id=user.id,
            prompt=prompt,
            system=body.system,
            locale=locale,
        )
    # 成本归创建者，调用者身份单独留痕（可追溯，不参与计费归属）。
    assert server.services is not None
    server.services.audit_repo.write(
        actor=user.username,
        action="app.generate",
        target=row.app_id,
        payload=json.dumps(
            {
                "owner_agent_id": row.agent_id,
                "owner_user_id": row.owner_user_id,
                "caller_user_id": user.id,
                "model": generated.model_ref,
                "input_tokens": generated.input_tokens,
                "output_tokens": generated.output_tokens,
            },
            ensure_ascii=False,
        ),
    )
    return GenerateResponse(
        text=generated.text,
        model=generated.model_ref,
        input_tokens=generated.input_tokens,
        output_tokens=generated.output_tokens,
    )


class UploadImportResponse(BaseModel):
    path: str = Field(description="Workspace-relative path to pass to the import endpoint.")
    filename: str
    size: int


class ImportBody(BaseModel):
    kind: Literal["upload", "workspace_file"] = Field(
        description=(
            "upload: a file previously stored via the upload endpoint; "
            "workspace_file: an existing workspace file (owner/admin only)."
        ),
    )
    path: str = Field(
        default="",
        description="Workspace-relative path of the file to import.",
    )
    paths: list[str] = Field(
        default_factory=list,
        description=(
            "Import several workspace files into the same table, in order. "
            "Use either path or paths."
        ),
    )
    mode: Literal["append", "replace"] = Field(
        default="append",
        description="append adds rows after the existing ones; replace overwrites the table.",
    )
    mapping: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional source-column -> table-column map; unmapped source columns "
            "are ignored, unmapped table columns are left empty."
        ),
    )
    sheet: str | None = Field(
        default=None,
        description=(
            "Worksheet to import from: a sheet name or a 0-based index. "
            "Defaults to the first sheet."
        ),
    )
    header_row: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "0-based index of the header line, for files that start with a "
            "title or a note. Defaults to the first line."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description="Validate and count the rows without writing them.",
    )
    upsert_key: list[str] | None = Field(
        default=None,
        description=(
            "Columns whose combined value identifies an existing row. Rows with "
            "a matching key are updated instead of duplicated."
        ),
    )
    include_pii: bool = Field(
        default=False,
        description=(
            "Import columns detected as sensitive. When false, their values are "
            "dropped and only the rest of the row is written."
        ),
    )


class ImportResponse(BaseModel):
    table: str
    imported: int = Field(description="Rows written into the table.")
    skipped: int = Field(description="Rows dropped because they failed validation.")
    errors: list[str] = Field(description="Up to 5 row-level validation failures.")
    updated: int = Field(default=0, description="Rows replaced in place by upsert_key.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal notes, e.g. columns that look like sensitive data.",
    )
    would_import: int = Field(
        default=0,
        description="Rows a dry run would have written; 0 for a real import.",
    )
    dry_run: bool = Field(default=False, description="True when nothing was written.")
    pii_columns: list[str] = Field(
        default_factory=list,
        description="Columns detected or marked as sensitive during this import.",
    )
    blocked_pii: list[str] = Field(
        default_factory=list,
        description="Sensitive columns skipped because include_pii was false.",
    )
    failed_rows: list[FailedRowOut] = Field(
        default_factory=list,
        description="Rejected rows with their errors, for correcting and retrying.",
    )


@router.post(
    "/{app_id}/upload",
    response_model=UploadImportResponse,
    summary="Upload an import file",
    description=(
        "Stores a CSV/TSV/JSON/Excel file under the agent workspace "
        "``inbound/imports/`` and returns its path for the import endpoint. "
        "Requires the edit_data permission."
    ),
)
async def upload_app_import_file(
    app_id: str,
    file: UploadFile = File(...),  # noqa: B008
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> UploadImportResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.UPLOAD_IMPORT)
    max_bytes = (
        int(server.services.config.max_upload_bytes) if server.services is not None else None
    )
    fallback = upload_mb_to_bytes(DEFAULT_MAX_UPLOAD_MB)
    data = await read_upload_capped(file, max_bytes=max_bytes or fallback)
    path = await service.save_import_file(row, filename=file.filename or "upload.bin", data=data)
    return UploadImportResponse(path=path, filename=file.filename or "upload.bin", size=len(data))


@router.post(
    "/{app_id}/tables/{table_key}/import",
    response_model=ImportResponse,
    summary="Import rows into a table",
    description=(
        "Parses a CSV/TSV/JSON/Excel file and writes its rows into one table of "
        "the app. Rows that fail validation are skipped and counted. append "
        "requires edit_data; replace and workspace-file sources require "
        "edit_structure (owner or admin)."
    ),
)
async def import_app_table(
    app_id: str,
    table_key: str,
    body: ImportBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ImportResponse:
    needed = (
        "edit_structure"
        if body.mode == IMPORT_MODE_REPLACE or body.kind == "workspace_file"
        else "edit_data"
    )
    action = Action.MANAGE_STRUCTURE if needed == "edit_structure" else Action.CREATE_ROW
    service, row, _decision = _authorize(server, user, app_id, action, table_key=table_key)
    if body.paths and body.path:
        raise OctopError(ErrorCode.APP_ROW_INVALID, "use either path or paths, not both")
    if not body.paths and not body.path:
        raise OctopError(ErrorCode.APP_ROW_INVALID, "an import path is required")
    if body.paths:
        result: ImportResult = await service.import_from_files(
            row,
            paths=body.paths,
            table=table_key,
            mode=body.mode,
            mapping=body.mapping,
            sheet=body.sheet,
            header_row=body.header_row,
            dry_run=body.dry_run,
            upsert_key=body.upsert_key,
            include_pii=body.include_pii,
            created_by=user.id,
        )
    else:
        result = await service.import_from_file(
            row,
            path=body.path,
            table=table_key,
            mode=body.mode,
            mapping=body.mapping,
            sheet=body.sheet,
            header_row=body.header_row,
            dry_run=body.dry_run,
            upsert_key=body.upsert_key,
            include_pii=body.include_pii,
            created_by=user.id,
        )
    from octop.infra.metrics import METRICS  # noqa: PLC0415

    METRICS.inc("light_app_row_write_total", result.imported + result.updated)
    return ImportResponse(
        table=table_key,
        imported=result.imported,
        skipped=result.skipped,
        errors=result.errors,
        updated=result.updated,
        warnings=result.warnings,
        would_import=result.would_import,
        dry_run=result.dry_run,
        pii_columns=result.pii_columns,
        blocked_pii=result.blocked_pii,
        failed_rows=[FailedRowOut(**item) for item in result.failed_rows],
    )


class SourceOut(BaseModel):
    source_id: str
    table_key: str = Field(description="Table the source feeds.")
    kind: str = Field(description="workspace_file or external.")
    mode: str = Field(description="append or replace.")
    schedule_seconds: int = Field(description="0 means manual-only.")
    params: dict[str, Any] = Field(
        description="workspace_file: {path}; external: {instance_id, tool, args?, result_path?}.",
    )
    mapping: dict[str, str] = Field(description="Source-column to table-column map.")
    last_run_at: int | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_row_count: int | None = None
    created_by: int
    created_at: int
    updated_at: int


class SourcesResponse(BaseModel):
    sources: list[SourceOut]


class SaveSourceBody(BaseModel):
    table_key: str = Field(description="Table the source feeds.")
    kind: Literal["workspace_file", "external"]
    mode: Literal["append", "replace"] = "append"
    schedule_seconds: int = Field(
        default=0,
        ge=0,
        description="Auto-sync interval; 0 means manual-only (scheduler ships with M5).",
    )
    params: dict[str, Any] = Field(default_factory=dict)
    mapping: dict[str, str] = Field(default_factory=dict)


class SourceToolsOut(BaseModel):
    name: str
    description: str = ""
    read_only: bool = Field(
        description="Whether the tool declares readOnlyHint (or declares nothing)."
    )
    input_schema: dict[str, Any] = Field(default_factory=dict)


class SourceToolsResponse(BaseModel):
    tools: list[SourceToolsOut]


class SourceRunResponse(BaseModel):
    table: str
    imported: int
    skipped: int
    errors: list[str]
    warnings: list[str] = Field(default_factory=list)
    pii_columns: list[str] = Field(default_factory=list)
    blocked_pii: list[str] = Field(default_factory=list)
    failed_rows: list[FailedRowOut] = Field(default_factory=list)


def _source_out(view: SourceView) -> SourceOut:
    return SourceOut(
        source_id=view.source_id,
        table_key=view.table_key,
        kind=view.kind,
        mode=view.mode,
        schedule_seconds=view.schedule_seconds,
        params=view.params,
        mapping=view.mapping,
        last_run_at=view.last_run_at,
        last_status=view.last_status,
        last_error=view.last_error,
        last_row_count=view.last_row_count,
        created_by=view.created_by,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


class ScheduleOut(BaseModel):
    schedule_id: str
    action: str = Field(description="sync_source or snapshot_publication.")
    target: dict[str, Any] = Field(description="Action target; sync_source carries source_id.")
    interval_seconds: int
    next_run_at: int = Field(description="Unix seconds; the scheduler picks it up when due.")
    last_run_at: int | None = None
    last_status: str = ""
    last_error: str = ""
    failure_count: int = 0
    enabled: bool
    created_at: int
    updated_at: int


class SchedulesResponse(BaseModel):
    schedules: list[ScheduleOut]


class SaveScheduleBody(BaseModel):
    action: Literal["sync_source", "snapshot_publication"]
    interval_seconds: int = Field(ge=1, description="Run interval in seconds.")
    enabled: bool = True
    target: dict[str, Any] = Field(
        default_factory=dict,
        description="sync_source: {source_id}; snapshot_publication: {}.",
    )


class UpdateScheduleBody(BaseModel):
    interval_seconds: int = Field(ge=1)
    enabled: bool = True


def _schedule_out(view: ScheduleView) -> ScheduleOut:
    return ScheduleOut(
        schedule_id=view.schedule_id,
        action=view.action,
        target=view.target,
        interval_seconds=view.interval_seconds,
        next_run_at=view.next_run_at,
        last_run_at=view.last_run_at,
        last_status=view.last_status,
        last_error=view.last_error,
        failure_count=view.failure_count,
        enabled=view.enabled,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get(
    "/{app_id}/schedules",
    response_model=SchedulesResponse,
    summary="List application schedules",
    description="Interval jobs such as source sync and public-snapshot refresh.",
)
async def list_app_schedules(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SchedulesResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return SchedulesResponse(
        schedules=[_schedule_out(view) for view in service.list_schedules(row)]
    )


@router.post(
    "/{app_id}/schedules",
    response_model=ScheduleOut,
    summary="Create or update an application schedule",
    description=(
        "source sync schedules use a derived id and mirror the source's "
        "schedule_seconds; snapshot schedules only refresh an existing public "
        "link. Source sync requires edit_structure; snapshot refresh requires "
        "owner or admin."
    ),
)
async def save_app_schedule(
    app_id: str,
    body: SaveScheduleBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ScheduleOut:
    action = (
        Action.PUBLISH
        if body.action == SCHEDULE_ACTION_SNAPSHOT_PUBLICATION
        else Action.MANAGE_STRUCTURE
    )
    service, row, _decision = _authorize(server, user, app_id, action)
    view = service.save_schedule(
        row,
        action=body.action,
        interval_seconds=body.interval_seconds,
        created_by=user.id,
        enabled=body.enabled,
        target=body.target,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.schedule.save",
        target=app_id,
        payload={
            "schedule_id": view.schedule_id,
            "action": view.action,
            "interval_seconds": view.interval_seconds,
            "enabled": view.enabled,
        },
    )
    return _schedule_out(view)


@router.patch(
    "/{app_id}/schedules/{schedule_id}",
    response_model=ScheduleOut,
    summary="Update an application schedule",
    description=("Source sync requires edit_structure; snapshot refresh requires owner or admin."),
)
async def update_app_schedule(
    app_id: str,
    schedule_id: str,
    body: UpdateScheduleBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> ScheduleOut:
    service, row, existing = _schedule_write_access(server, user, app_id, schedule_id)
    view = service.update_schedule(
        row,
        schedule_id,
        interval_seconds=body.interval_seconds,
        enabled=body.enabled,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.schedule.update",
        target=app_id,
        payload={
            "schedule_id": schedule_id,
            "action": existing.action,
            "interval_seconds": view.interval_seconds,
            "enabled": view.enabled,
        },
    )
    return _schedule_out(view)


@router.delete(
    "/{app_id}/schedules/{schedule_id}",
    status_code=204,
    summary="Delete an application schedule",
    description=(
        "Deleting a source-sync schedule leaves the binding on manual sync. "
        "Source sync requires edit_structure; snapshot refresh requires owner or admin."
    ),
)
async def delete_app_schedule(
    app_id: str,
    schedule_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, existing = _schedule_write_access(server, user, app_id, schedule_id)
    service.delete_schedule(row, schedule_id)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.schedule.delete",
        target=app_id,
        payload={"schedule_id": schedule_id, "action": existing.action},
    )
    return Response(status_code=204)


@router.get(
    "/{app_id}/sources",
    response_model=SourcesResponse,
    summary="List data-source bindings",
)
async def list_app_sources(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SourcesResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    return SourcesResponse(sources=[_source_out(view) for view in service.list_sources(row)])


@router.put(
    "/{app_id}/sources/{source_id}",
    response_model=SourceOut,
    summary="Create or update a data-source binding",
    description=(
        "Binds one source to one table (a table holds at most one binding). "
        "Requires edit_structure. External sources run with the app owner's "
        "connector credentials."
    ),
)
async def save_app_source(
    app_id: str,
    source_id: str,
    body: SaveSourceBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SourceOut:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_STRUCTURE)
    if body.kind == "external":
        # Bind-time validation (defense in depth: the runner re-checks
        # read-only on every call).
        instance_id = str(body.params.get("instance_id") or "")
        tool = str(body.params.get("tool") or "")
        meta = _tool_runner(server).tool_meta(instance_id, tool)
        if meta is None:
            raise OctopError(
                ErrorCode.APP_SOURCE_INVALID,
                f"tool {tool!r} is not available on connector {instance_id!r}",
            )
        if not meta["read_only"]:
            raise OctopError(
                ErrorCode.APP_SOURCE_INVALID,
                f"tool {meta['name']!r} is not read-only and cannot back a data source",
            )
    view = service.save_source(
        row,
        source_id=source_id,
        table_key=body.table_key,
        kind=body.kind,
        mode=body.mode,
        schedule_seconds=body.schedule_seconds,
        params=body.params,
        mapping=body.mapping,
        created_by=user.id,
    )
    return _source_out(view)


@router.delete(
    "/{app_id}/sources/{source_id}",
    status_code=204,
    summary="Delete a data-source binding",
)
async def delete_app_source(
    app_id: str,
    source_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Response:
    service, row, _decision = _authorize(server, user, app_id, Action.MANAGE_STRUCTURE)
    service.delete_source(row, source_id)
    return Response(status_code=204)


@router.post(
    "/{app_id}/sources/{source_id}/run",
    response_model=SourceRunResponse,
    summary="Run a data source now",
    description="Re-imports from the bound source into its table. Requires edit_data.",
)
async def run_app_source(
    app_id: str,
    source_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SourceRunResponse:
    service, row, _decision = _authorize(server, user, app_id, Action.RUN_SOURCE)
    view = service.get_source(row, source_id)
    if view.kind == "external":
        service = _service_with_runner(server)
    result = await service.run_source(row, source_id)
    return SourceRunResponse(
        table=view.table_key,
        imported=result.imported,
        skipped=result.skipped,
        errors=result.errors,
        warnings=result.warnings,
        pii_columns=result.pii_columns,
        blocked_pii=result.blocked_pii,
        failed_rows=[FailedRowOut(**item) for item in result.failed_rows],
    )


@router.get(
    "/{app_id}/source-tools",
    response_model=SourceToolsResponse,
    summary="List connector tools usable as data sources",
    description=(
        "Tools of one in-process gateway connector instance (query: instance_id), "
        "for the binding dialog. Requires edit_structure."
    ),
)
async def list_app_source_tools(
    app_id: str,
    instance_id: str = Query(description="Connector instance id."),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> SourceToolsResponse:
    _authorize(server, user, app_id, Action.MANAGE_STRUCTURE)
    runner = _tool_runner(server)
    return SourceToolsResponse(
        tools=[
            SourceToolsOut(
                name=str(meta["name"]),
                description=str(meta.get("description") or ""),
                read_only=bool(meta["read_only"]),
                input_schema=dict(meta.get("input_schema") or {}),
            )
            for meta in runner.list_tool_meta(instance_id)
        ]
    )


class AppTaskTypeOut(BaseModel):
    name: str = Field(description="Registered task type.")
    label: str = Field(description="Human-readable label.")
    tier: int = Field(description="Execution tier: 1 = single model call, 2 = bounded tools.")
    output_fields: list[str] = Field(description="Structured result fields.")
    supports_write: bool = Field(description="Whether a write_column may receive the result.")


class AppTaskCreateBody(BaseModel):
    task_type: str = Field(
        description=(
            "Registered task type: ``summarize_row`` (Tier 1) or "
            "``table_digest`` (Tier 2, bounded read-only tools)."
        )
    )
    table: str = Field(
        MAIN_TABLE_KEY,
        description="Table key; must match the row's table when supplied.",
    )
    row_id: str = Field(description="Row the task operates on.")
    input: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Task-type-specific input. ``summarize_row`` accepts an optional "
            "``write_column`` naming a non-PII string column to receive the summary."
        ),
    )
    idempotency_key: str = Field(
        "",
        description="Optional idempotency key scoped to this app.",
    )


class AppTaskOut(BaseModel):
    task_id: str = Field(description="Public task id.")
    app_id: str = Field(description="Owning app.")
    table: str = Field(description="Table key.")
    row_id: str = Field(description="Row the task operates on.")
    task_type: str = Field(description="Registered task type.")
    status: str = Field(description="queued / running / succeeded / failed / cancelled.")
    result: dict[str, Any] | None = Field(
        default=None, description="Structured result once the task succeeds."
    )
    error: str = Field("", description="Failure reason when status is failed.")
    created_at: int = Field(description="Unix seconds when the task was created.")
    started_at: int | None = Field(default=None, description="Unix seconds when execution began.")
    finished_at: int | None = Field(default=None, description="Unix seconds when the task ended.")


def _task_out(task: Any) -> AppTaskOut:
    return AppTaskOut(
        task_id=task.task_id,
        app_id=task.app_id,
        table=task.table_key,
        row_id=task.row_id,
        task_type=task.task_type,
        status=task.status,
        result=task.result,
        error=task.error,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


@router.get(
    "/{app_id}/task-types",
    response_model=list[AppTaskTypeOut],
    summary="List registered AI task types",
    description=(
        "Returns the code-registered task types an app may request, including "
        "their execution tier. Requires app view."
    ),
)
async def list_app_task_types(
    app_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> list[AppTaskTypeOut]:
    _authorize(server, user, app_id, Action.VIEW_APP)
    return [
        AppTaskTypeOut(
            name=spec.name,
            label=spec.label,
            tier=spec.tier,
            output_fields=list(spec.output_fields),
            supports_write=bool(spec.write_key),
        )
        for spec in list_task_types()
    ]


@router.post(
    "/{app_id}/tasks",
    response_model=AppTaskOut,
    summary="Create an AI task for one row",
    description=(
        "Queues a named, schema-bound AI task. The app must enable the "
        "``ai.agent_task`` capability. The page cannot send a free-form prompt; "
        "``task_type`` must exist in the code registry. Requires AI access."
    ),
)
async def create_app_task(
    app_id: str,
    body: AppTaskCreateBody,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppTaskOut:
    _service, row, _decision = _authorize(server, user, app_id, Action.AI)
    task = await _task_service(server).create(
        row,
        user_id=user.id,
        task_type=body.task_type,
        row_id=body.row_id,
        table=body.table,
        input=body.input,
        idempotency_key=body.idempotency_key,
    )
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.task.create",
        target=app_id,
        payload={"task_id": task.task_id, "task_type": task.task_type},
    )
    return _task_out(task)


@router.get(
    "/{app_id}/tasks",
    response_model=list[AppTaskOut],
    summary="List AI tasks for an app",
    description="Most recent first. Optional ``status`` filter. Requires app view.",
)
async def list_app_tasks(
    app_id: str,
    status: str | None = Query(default=None, description="Filter by task status."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> list[AppTaskOut]:
    _service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    tasks = _task_service(server).list(
        row, user_id=user.id, status=status, limit=limit, offset=offset
    )
    return [_task_out(task) for task in tasks]


@router.get(
    "/{app_id}/tasks/{task_id}",
    response_model=AppTaskOut,
    summary="Get one AI task",
    description="Returns the current state and structured result. Requires app view.",
)
async def get_app_task(
    app_id: str,
    task_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppTaskOut:
    _service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    task = _task_service(server).get(row, task_id, user_id=user.id)
    return _task_out(task)


@router.post(
    "/{app_id}/tasks/{task_id}/cancel",
    response_model=AppTaskOut,
    summary="Cancel an AI task",
    description=(
        "Cancels a queued task, or flags a running one for the runner. "
        "The task creator or the app owner may cancel."
    ),
)
async def cancel_app_task(
    app_id: str,
    task_id: str,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> AppTaskOut:
    _service, row, _decision = _authorize(server, user, app_id, Action.VIEW_APP)
    task = _task_service(server).cancel(row, task_id, user_id=user.id)
    _audit(
        server,
        actor=f"user:{user.id}",
        action="light_app.task.cancel",
        target=app_id,
        payload={"task_id": task_id},
    )
    return _task_out(task)
