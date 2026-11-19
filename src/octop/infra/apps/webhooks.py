"""Owner-declared outbound webhooks for MiniApps.

The page never chooses a destination. An owner creates a webhook with a URL,
an event-type allowlist and a generated signing secret; each emitted event is
ingested through the existing external-event outbox and delivered by the
shared worker with at-least-once retries.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from octop_harness.security.ssrf import validate_agent_fetch_url

from octop.infra.apps import capabilities as app_capabilities
from octop.infra.apps import limits as app_limits
from octop.infra.connectors.crypto import encrypt_credentials
from octop.infra.db.repos.app_webhooks import (
    LightAppWebhookRepo,
    LightAppWebhookRow,
)
from octop.infra.db.repos.external_sources import ExternalSourceRepo
from octop.infra.db.repos.external_subscriptions import ExternalSubscriptionRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_short_id

if TYPE_CHECKING:
    from octop.infra.apps.service import AppRecord, AppService
    from octop.infra.events.service import ExternalEventService, IngestResult

SOURCE_KIND_APP_OUTBOUND = "app_outbound"
MODE_WEBHOOK_POST = "webhook.post"
TARGET_WEBHOOK = "webhook"
MAX_NAME_LEN = 100
MAX_EVENT_TYPES = 50
MAX_EVENT_TYPE_LEN = 100
MAX_SUMMARY_LEN = 500
MAX_PAYLOAD_BYTES = 64 * 1024
EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_.*\-]{1,100}$")


def _invalid(message: str) -> OctopError:
    return OctopError(ErrorCode.EXTERNAL_EVENT_INVALID, message)


@dataclass(frozen=True)
class WebhookView:
    webhook_id: str
    name: str
    url: str
    event_types: tuple[str, ...]
    enabled: bool
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: LightAppWebhookRow) -> WebhookView:
        return cls(
            webhook_id=row.webhook_id,
            name=row.name,
            url=row.url,
            event_types=row.event_types,
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _clean_event_types(values: list[str]) -> list[str]:
    cleaned = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
    if not cleaned:
        raise _invalid("at least one event type is required")
    if len(cleaned) > MAX_EVENT_TYPES:
        raise _invalid(f"at most {MAX_EVENT_TYPES} event types are supported")
    for item in cleaned:
        if len(item) > MAX_EVENT_TYPE_LEN or not EVENT_TYPE_RE.fullmatch(item):
            raise _invalid(f"invalid event type: {item!r}")
    return cleaned


def _validate_url(raw: str) -> str:
    url = raw.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise _invalid("webhook URL must be an http(s) URL")
    if parsed.username or parsed.password:
        raise _invalid("webhook URL must not contain credentials")
    try:
        validate_agent_fetch_url(url)
    except Exception as exc:  # noqa: BLE001 - SSRF guard is a validation failure
        raise _invalid(f"webhook URL is not allowed: {exc}") from exc
    return url


def _matches(pattern: str, event_type: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return event_type.startswith(pattern[:-1])
    return pattern == event_type


class AppWebhookService:
    def __init__(
        self,
        *,
        app_service: AppService,
        webhook_repo: LightAppWebhookRepo,
        source_repo: ExternalSourceRepo,
        subscription_repo: ExternalSubscriptionRepo,
        secret_repo: SecretRepo,
    ) -> None:
        self._app_service = app_service
        self._webhook_repo = webhook_repo
        self._source_repo = source_repo
        self._subscription_repo = subscription_repo
        self._secret_repo = secret_repo

    def list_for_app(self, row: AppRecord) -> list[WebhookView]:
        return [WebhookView.from_row(item) for item in self._webhook_repo.list_for_app(row.app_id)]

    async def create(
        self,
        row: AppRecord,
        *,
        name: str,
        url: str,
        event_types: list[str],
        enabled: bool,
        actor_user_id: int,
    ) -> tuple[WebhookView, str]:
        clean_name = name.strip()[:MAX_NAME_LEN]
        if not clean_name:
            raise _invalid("webhook name is required")
        # The SSRF guard resolves the hostname; keep DNS off the event loop.
        clean_url = await asyncio.to_thread(_validate_url, url)
        clean_types = _clean_event_types(event_types)
        webhook_id = f"wh_{new_short_id(14)}"
        secret = secrets.token_urlsafe(32)
        secret_blob = encrypt_credentials(self._secret_repo, {"secret": secret})
        source = self._source_repo.create(
            owner_user_id=row.owner_user_id,
            name=f"MiniApp outbound: {row.name}"[:120],
            kind=SOURCE_KIND_APP_OUTBOUND,
            config_json=json.dumps(
                {"app_id": row.app_id}, ensure_ascii=False, separators=(",", ":")
            ),
        )
        try:
            subscription = self._subscription_repo.create(
                source_id=source.source_id,
                target_type=TARGET_WEBHOOK,
                target_id=webhook_id,
                mode=MODE_WEBHOOK_POST,
                created_by=actor_user_id,
            )
            try:
                stored = self._webhook_repo.create(
                    app_id=row.app_id,
                    name=clean_name,
                    url=clean_url,
                    event_types=clean_types,
                    source_id=source.source_id,
                    subscription_id=subscription.subscription_id,
                    secret_blob=secret_blob,
                    created_by=actor_user_id,
                    enabled=enabled,
                    webhook_id=webhook_id,
                )
            except Exception:
                self._subscription_repo.delete(subscription.subscription_id)
                raise
        except Exception:
            self._source_repo.delete(source.source_id)
            raise
        if not enabled:
            self._source_repo.update(source.source_id, enabled=False)
            self._subscription_repo.update(subscription.subscription_id, enabled=False)
        return WebhookView.from_row(stored), secret

    async def update(
        self,
        row: AppRecord,
        webhook_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        event_types: list[str] | None = None,
        enabled: bool | None = None,
        rotate_secret: bool = False,
    ) -> tuple[WebhookView, str | None]:
        existing = self._webhook_repo.get(webhook_id)
        if existing is None or existing.app_id != row.app_id:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "webhook not found")
        clean_name = name.strip()[:MAX_NAME_LEN] if name is not None else None
        if clean_name is not None and not clean_name:
            raise _invalid("webhook name is required")
        # The SSRF guard resolves the hostname; keep DNS off the event loop.
        clean_url = await asyncio.to_thread(_validate_url, url) if url is not None else None
        clean_types = _clean_event_types(event_types) if event_types is not None else None
        secret: str | None = None
        secret_blob: bytes | None = None
        if rotate_secret:
            secret = secrets.token_urlsafe(32)
            secret_blob = encrypt_credentials(self._secret_repo, {"secret": secret})
        updated = self._webhook_repo.update(
            webhook_id,
            name=clean_name,
            url=clean_url,
            event_types=clean_types,
            enabled=enabled,
            secret_blob=secret_blob,
        )
        if updated is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "webhook not found")
        return WebhookView.from_row(updated), secret

    def delete(self, row: AppRecord, webhook_id: str) -> bool:
        existing = self._webhook_repo.get(webhook_id)
        if existing is None or existing.app_id != row.app_id:
            return False
        self._webhook_repo.delete(webhook_id)
        self._subscription_repo.delete(existing.subscription_id)
        self._source_repo.delete(existing.source_id)
        return True

    async def emit(
        self,
        row: AppRecord,
        event_service: ExternalEventService,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        summary: str = "",
        client_event_id: str | None = None,
    ) -> dict[str, Any]:
        self._app_service.require_capability(row, app_capabilities.CAP_EVENTS_EMIT)
        clean_type = event_type.strip()
        if not EVENT_TYPE_RE.fullmatch(clean_type):
            raise _invalid("event_type must match [A-Za-z0-9_.*-] and be at most 100 chars")
        if payload is not None and not isinstance(payload, dict):
            raise _invalid("payload must be an object")
        body = dict(payload or {})
        if summary:
            body["summary"] = summary.strip()[:MAX_SUMMARY_LEN]
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise _invalid(f"event payload exceeds {MAX_PAYLOAD_BYTES // 1024} KB")
        hooks = [
            hook
            for hook in self._webhook_repo.list_for_app(row.app_id)
            if hook.enabled and any(_matches(pattern, clean_type) for pattern in hook.event_types)
        ]
        if not hooks:
            raise _invalid(f"no enabled outbound webhook accepts {clean_type!r}")
        external_id = (client_event_id or "").strip()
        if not external_id:
            external_id = f"app-{new_short_id(20)}"
        emitted: list[dict[str, Any]] = []
        async with app_limits.event_emit_slot(row.app_id):
            for hook in hooks:
                source = self._source_repo.get(hook.source_id)
                if source is None:
                    continue
                result = event_service.ingest(
                    source=source,
                    event_type=clean_type,
                    external_id=external_id,
                    occurred_at=None,
                    payload=body,
                )
                emitted.append(
                    {
                        "webhook_id": hook.webhook_id,
                        "event_id": result.event.event_id,
                        "duplicate": result.duplicate,
                        "deliveries": result.delivery_count,
                        "rate_limited": result.rate_limited,
                    }
                )
        return {
            "external_id": external_id,
            "emitted": len(emitted),
            "duplicates": sum(1 for item in emitted if item["duplicate"]),
            "deliveries": sum(int(item["deliveries"]) for item in emitted),
            "rate_limited": sum(int(item["rate_limited"]) for item in emitted),
            "webhooks": emitted,
        }

    async def test(
        self,
        row: AppRecord,
        webhook_id: str,
        event_service: ExternalEventService,
        *,
        event_type: str = "",
        payload: dict[str, Any] | None = None,
    ) -> IngestResult:
        """Ingest one owner-triggered test event into a single webhook."""
        hook = self._webhook_repo.get(webhook_id)
        if hook is None or hook.app_id != row.app_id:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "webhook not found")
        if not hook.enabled:
            raise _invalid("webhook is disabled")
        source = self._source_repo.get(hook.source_id)
        if source is None:
            raise OctopError(ErrorCode.APP_NOT_FOUND, "webhook source missing")
        clean_type = event_type.strip()
        if clean_type:
            if not EVENT_TYPE_RE.fullmatch(clean_type):
                raise _invalid("event_type must match [A-Za-z0-9_.*-] and be at most 100 chars")
        else:
            pattern = hook.event_types[0] if hook.event_types else "*"
            if pattern == "*":
                clean_type = "webhook.test"
            elif pattern.endswith(".*"):
                clean_type = f"{pattern[:-1]}test"
            else:
                clean_type = pattern
        if payload is not None and not isinstance(payload, dict):
            raise _invalid("payload must be an object")
        body = dict(payload or {})
        body.setdefault("summary", "Webhook test event")
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise _invalid(f"event payload exceeds {MAX_PAYLOAD_BYTES // 1024} KB")
        return event_service.ingest(
            source=source,
            event_type=clean_type,
            external_id=f"test-{new_short_id(20)}",
            occurred_at=None,
            payload=body,
        )


__all__ = ["AppWebhookService", "WebhookView"]
