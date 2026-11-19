"""App-owned inbound ingress for MiniApps.

Each ingress owns one ``app_ingress`` external source and one app subscription,
so its URL, signing secret and delivery mode live with the app instead of the
global External Events page. The source kind is internal: the public source API
still only accepts ``webhook`` and ``connector_poll``.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from octop.infra.connectors.crypto import encrypt_credentials
from octop.infra.db.repos.app_ingress import (
    LightAppIngressRepo,
    LightAppIngressRow,
)
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.external_sources import ExternalSourceRepo
from octop.infra.db.repos.external_subscriptions import ExternalSubscriptionRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.events.service import SOURCE_KIND_APP_INGRESS
from octop.infra.utils.ulid import new_short_id

if TYPE_CHECKING:
    from octop.infra.apps.service import AppRecord, AppService
    from octop.infra.events.service import ExternalEventService, IngestResult

INGRESS_MODES = ("app.inbox", "app.import", "app.refresh")
MAX_NAME_LEN = 100
MAX_EVENT_TYPE_LEN = 100
MAX_MAPPING_BYTES = 32 * 1024
MAX_PER_MINUTE_LIMIT = 600
EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")


def _invalid(message: str, **details: object) -> OctopError:
    return OctopError(ErrorCode.APP_SOURCE_INVALID, message, details=details)


@dataclass(frozen=True)
class IngressView:
    ingress_id: str
    name: str
    mode: str
    event_type: str
    mapping: dict[str, Any]
    max_per_minute: int
    enabled: bool
    source_id: str
    created_at: int
    updated_at: int

    @classmethod
    def from_rows(cls, row: LightAppIngressRow, subscription: Any) -> IngressView:
        mapping = _json_dict(subscription.mapping_json)
        filter_rules = _json_dict(subscription.filter_json)
        return cls(
            ingress_id=row.ingress_id,
            name=row.name,
            mode=row.mode,
            event_type=str(filter_rules.get("event_type") or ""),
            mapping=mapping,
            max_per_minute=int(subscription.max_per_minute or 0),
            enabled=row.enabled,
            source_id=row.source_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _json_dict(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _clean_mapping(raw: object) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise _invalid("mapping must be an object")
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_MAPPING_BYTES:
        raise _invalid("mapping is too large")
    return dict(raw)


def _clean_event_type(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if len(value) > MAX_EVENT_TYPE_LEN or not EVENT_TYPE_RE.fullmatch(value):
        raise _invalid(
            "event_type must use letters, digits, '_', '-' or '.', and at most 100 chars"
        )
    return value


class AppIngressService:
    def __init__(
        self,
        *,
        app_service: AppService,
        app_repo: LightAppRepo,
        ingress_repo: LightAppIngressRepo,
        source_repo: ExternalSourceRepo,
        subscription_repo: ExternalSubscriptionRepo,
        secret_repo: SecretRepo,
    ) -> None:
        self._app_service = app_service
        self._app_repo = app_repo
        self._ingress_repo = ingress_repo
        self._source_repo = source_repo
        self._subscription_repo = subscription_repo
        self._secret_repo = secret_repo

    def _subscription(self, row: LightAppIngressRow) -> Any:
        subscription = self._subscription_repo.get(row.subscription_id)
        if subscription is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "ingress subscription missing")
        return subscription

    def list(self, row: AppRecord) -> list[IngressView]:
        return [
            IngressView.from_rows(item, self._subscription(item))
            for item in self._ingress_repo.list_for_app(row.app_id)
        ]

    def _owned(self, row: AppRecord, ingress_id: str) -> LightAppIngressRow:
        item = self._ingress_repo.get(ingress_id)
        if item is None or item.app_id != row.app_id:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "ingress not found")
        return item

    async def _validate_mapping(self, row: AppRecord, mode: str, mapping: dict[str, Any]) -> None:
        if mode not in INGRESS_MODES:
            raise _invalid(f"unsupported ingress mode: {mode!r}")
        if mode == "app.refresh":
            source_id = str(mapping.get("source_id") or mapping.get("app_source_id") or "")
            if not source_id:
                raise _invalid("app.refresh requires mapping.source_id")
            try:
                self._app_service.get_source(row, source_id)
            except OctopError as exc:
                raise _invalid(f"app.refresh source not found: {source_id}") from exc
            return
        if mode != "app.import":
            return
        table_key = str(mapping.get("table") or "main")
        tables = {item.table_key for item in self._app_service.list_tables(row)}
        if table_key not in tables:
            raise _invalid(f"mapping.table is not a table: {table_key!r}")
        columns = mapping.get("columns")
        if columns is None:
            return
        if not isinstance(columns, dict):
            raise _invalid("mapping.columns must be an object")
        schema = await self._app_service.read_schema(row, table_key)
        missing = sorted({str(value) for value in columns.values()} - set(schema.names))
        if missing:
            raise _invalid("mapping.columns target missing columns: " + ", ".join(missing))

    async def create(
        self,
        row: AppRecord,
        *,
        name: str,
        mode: str,
        event_type: str = "",
        mapping: object = None,
        max_per_minute: int = 0,
        enabled: bool = True,
        actor_user_id: int,
    ) -> tuple[IngressView, str]:
        clean_name = name.strip()[:MAX_NAME_LEN]
        if not clean_name:
            raise _invalid("ingress name is required")
        clean_event_type = _clean_event_type(event_type)
        clean_mapping = _clean_mapping(mapping)
        await self._validate_mapping(row, mode, clean_mapping)
        limit = max(0, min(int(max_per_minute), MAX_PER_MINUTE_LIMIT))
        ingress_id = f"ing_{new_short_id(14)}"
        secret = secrets.token_urlsafe(32)
        secret_blob = encrypt_credentials(self._secret_repo, {"secret": secret})
        source = self._source_repo.create(
            owner_user_id=row.owner_user_id,
            name=f"MiniApp ingress: {row.name}"[:120],
            kind=SOURCE_KIND_APP_INGRESS,
            config_json=json.dumps(
                {"app_id": row.app_id, "ingress_id": ingress_id},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            secret_blob=secret_blob,
        )
        try:
            subscription = self._subscription_repo.create(
                source_id=source.source_id,
                target_type="app",
                target_id=row.app_id,
                mode=mode,
                created_by=actor_user_id,
                filter_json=json.dumps(
                    {"event_type": clean_event_type} if clean_event_type else {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                mapping_json=json.dumps(clean_mapping, ensure_ascii=False, separators=(",", ":")),
                max_per_minute=limit,
            )
            try:
                stored = self._ingress_repo.create(
                    app_id=row.app_id,
                    name=clean_name,
                    mode=mode,
                    source_id=source.source_id,
                    subscription_id=subscription.subscription_id,
                    created_by=actor_user_id,
                    enabled=enabled,
                    ingress_id=ingress_id,
                )
            except Exception:
                self._subscription_repo.delete(subscription.subscription_id)
                raise
        except Exception:
            self._source_repo.delete(source.source_id)
            raise
        if not enabled:
            self._source_repo.update(source.source_id, enabled=False)
            updated_subscription = self._subscription_repo.update(
                subscription.subscription_id, enabled=False
            )
            if updated_subscription is not None:
                subscription = updated_subscription
        return IngressView.from_rows(stored, subscription), secret

    async def update(
        self,
        row: AppRecord,
        ingress_id: str,
        *,
        name: str | None = None,
        mode: str | None = None,
        event_type: str | None = None,
        mapping: object = None,
        max_per_minute: int | None = None,
        enabled: bool | None = None,
    ) -> IngressView:
        item = self._owned(row, ingress_id)
        subscription = self._subscription(item)
        clean_name = None
        if name is not None:
            clean_name = name.strip()[:MAX_NAME_LEN]
            if not clean_name:
                raise _invalid("ingress name is required")
        clean_mode = mode or item.mode
        clean_event_type = (
            _clean_event_type(event_type)
            if event_type is not None
            else str(_json_dict(subscription.filter_json).get("event_type") or "")
        )
        clean_mapping = (
            _clean_mapping(mapping)
            if mapping is not None
            else _json_dict(subscription.mapping_json)
        )
        await self._validate_mapping(row, clean_mode, clean_mapping)
        limit = (
            max(0, min(int(max_per_minute), MAX_PER_MINUTE_LIMIT))
            if max_per_minute is not None
            else int(subscription.max_per_minute or 0)
        )
        updated_subscription = self._subscription_repo.update(
            subscription.subscription_id,
            mode=clean_mode,
            filter_json=json.dumps(
                {"event_type": clean_event_type} if clean_event_type else {},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            mapping_json=json.dumps(clean_mapping, ensure_ascii=False, separators=(",", ":")),
            max_per_minute=limit,
            enabled=enabled,
        )
        if updated_subscription is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "ingress subscription missing")
        if enabled is not None:
            self._source_repo.update(item.source_id, enabled=enabled)
        updated = self._ingress_repo.update(
            ingress_id,
            name=clean_name,
            mode=clean_mode,
            enabled=enabled,
        )
        if updated is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "ingress not found")
        return IngressView.from_rows(updated, updated_subscription)

    def delete(self, row: AppRecord, ingress_id: str) -> bool:
        item = self._owned(row, ingress_id)
        self._ingress_repo.delete(ingress_id)
        self._subscription_repo.delete(item.subscription_id)
        self._source_repo.delete(item.source_id)
        return True

    async def rotate_secret(self, row: AppRecord, ingress_id: str) -> str:
        item = self._owned(row, ingress_id)
        secret = secrets.token_urlsafe(32)
        self._source_repo.update(
            item.source_id,
            secret_blob=encrypt_credentials(self._secret_repo, {"secret": secret}),
        )
        self._ingress_repo.update(ingress_id)
        return secret

    async def test_event(
        self,
        row: AppRecord,
        ingress_id: str,
        event_service: ExternalEventService,
        *,
        event_type: str = "",
        payload: dict[str, Any] | None = None,
    ) -> IngestResult:
        item = self._owned(row, ingress_id)
        if not item.enabled:
            raise _invalid("ingress is disabled")
        source = self._source_repo.get(item.source_id)
        if source is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "ingress source missing")
        subscription = self._subscription(item)
        configured = str(_json_dict(subscription.filter_json).get("event_type") or "")
        clean_type = _clean_event_type(event_type) or configured or "app.ingress.test"
        if payload is not None and not isinstance(payload, dict):
            raise _invalid("payload must be an object")
        body = dict(payload or {})
        body.setdefault("summary", "Test event")
        return event_service.ingest(
            source=source,
            event_type=clean_type,
            external_id=f"test-{new_short_id(16)}",
            occurred_at=None,
            payload=body,
        )


__all__ = ["AppIngressService", "IngressView", "INGRESS_MODES"]
