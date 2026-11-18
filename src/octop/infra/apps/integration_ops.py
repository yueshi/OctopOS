"""Read-only integration diagnostics for MiniApp agent tools.

The tools deliberately stay read-only: an agent can inspect sources,
subscriptions, deliveries and rows, but changing an ingress secret or target
still goes through the dashboard/API and a human confirmation.
"""

from __future__ import annotations

import json
import time
from typing import Any

from octop.infra.apps.service import AppRecord, AppService
from octop.infra.db.repos.apps import PERMISSION_EDIT_DATA, PERMISSION_RANK
from octop.infra.db.repos.external_deliveries import ExternalDeliveryRow
from octop.infra.db.repos.external_sources import ExternalSourceRow
from octop.infra.db.repos.external_subscriptions import ExternalSubscriptionRow
from octop.infra.db.services import RepoBundle

POLL_STALE_GRACE_SECONDS = 120
PENDING_STALE_SECONDS = 3600
MAX_RECENT_FAILURES = 5


def _json(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _source_summary(source: ExternalSourceRow, *, now: int) -> dict[str, Any]:
    config = _json(source.config_json)
    summary: dict[str, Any] = {
        "source_id": source.source_id,
        "name": source.name,
        "kind": source.kind,
        "enabled": source.enabled,
        "fail_count": source.fail_count,
        "last_error": source.last_error,
        "last_poll_at": source.last_poll_at,
        "next_run_at": source.next_run_at,
        "updated_at": source.updated_at,
    }
    if source.last_poll_at is not None:
        summary["last_poll_age_seconds"] = max(0, now - source.last_poll_at)
    if source.kind == "connector_poll":
        summary["poll"] = {
            key: config.get(key)
            for key in (
                "instance_id",
                "tool",
                "event_type",
                "interval_seconds",
                "cursor_arg",
                "cursor_path",
                "items_path",
                "id_path",
                "summary_path",
                "occurred_at_path",
            )
            if config.get(key) not in (None, "")
        }
        cursor = _json(source.cursor_json)
        summary["cursor"] = cursor.get("cursor") if "cursor" in cursor else None
    return summary


def _delivery_counts(rows: list[ExternalDeliveryRow]) -> dict[str, int]:
    counts = {"pending": 0, "delivering": 0, "delivered": 0, "failed": 0, "dead": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


_RESULT_KEYS = {
    "imported",
    "updated",
    "skipped",
    "would_import",
    "error_count",
    "warning_count",
    "failed_count",
    "partial",
    "blocked_pii",
    "pii_columns",
    "mode",
    "status_code",
}


def _delivery_summary(row: ExternalDeliveryRow) -> dict[str, Any]:
    result = {key: value for key, value in (row.result or {}).items() if key in _RESULT_KEYS}
    return {
        "delivery_id": row.delivery_id,
        "event_id": row.event_id,
        "status": row.status,
        "attempt": row.attempt,
        "last_error": row.last_error,
        "next_retry_at": row.next_retry_at,
        "delivered_at": row.delivered_at,
        "result": result,
    }


async def _subscription_summary(
    service: AppService,
    row: AppRecord,
    subscription: ExternalSubscriptionRow,
    source: ExternalSourceRow | None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    mapping = _json(subscription.mapping_json)
    filter_rules = _json(subscription.filter_json)
    target_ok = True
    missing_columns: list[str] = []
    if source is not None:
        owner_id = source.owner_user_id
        is_admin = service.is_admin(owner_id)
        permission = service.permission_for(row, owner_id, is_admin=is_admin)
        target_ok = (
            is_admin
            or row.owner_user_id == owner_id
            or (
                permission is not None
                and PERMISSION_RANK.get(permission, 0) >= PERMISSION_RANK[PERMISSION_EDIT_DATA]
            )
        )
        if not target_ok:
            issues.append(
                {
                    "severity": "error",
                    "code": "target_not_writable",
                    "message": (
                        f"source owner {owner_id} can no longer write MiniApp {row.app_id}"
                    ),
                    "suggested_action": (
                        "share the app with the source owner as edit_data, or "
                        "move the subscription to an owner that can write"
                    ),
                }
            )
    if subscription.mode in {"app.import", "app.refresh"}:
        table_key = str(mapping.get("table") or "main")
        tables = {item.table_key for item in service.list_tables(row)}
        if table_key not in tables:
            issues.append(
                {
                    "severity": "error",
                    "code": "mapping_table_missing",
                    "message": f"subscription maps to missing table {table_key!r}",
                    "suggested_action": "update mapping.table to an existing table",
                }
            )
        else:
            try:
                schema = await service.read_schema(row, table_key)
            except Exception:  # noqa: BLE001 - a schema read failure is the finding
                schema = None
            columns = mapping.get("columns")
            if schema is not None and isinstance(columns, dict):
                names = set(schema.names)
                missing_columns = sorted({str(value) for value in columns.values()} - names)
                if missing_columns:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "mapping_column_missing",
                            "message": (
                                "mapping targets columns that no longer exist: "
                                + ", ".join(missing_columns)
                            ),
                            "suggested_action": "update mapping.columns to the current schema",
                        }
                    )
    if subscription.mode == "app.refresh":
        source_id = str(mapping.get("source_id") or mapping.get("app_source_id") or "")
        if not source_id:
            issues.append(
                {
                    "severity": "error",
                    "code": "mapping_source_missing",
                    "message": "app.refresh has no mapping.source_id",
                    "suggested_action": "set mapping.source_id to an app data source",
                }
            )
        else:
            try:
                service.get_source(row, source_id)
            except Exception:  # noqa: BLE001 - missing binding is the finding
                issues.append(
                    {
                        "severity": "error",
                        "code": "mapping_source_missing",
                        "message": f"app.refresh references missing source {source_id!r}",
                        "suggested_action": "recreate the data source or update the mapping",
                    }
                )
    summary = {
        "subscription_id": subscription.subscription_id,
        "source_id": subscription.source_id,
        "mode": subscription.mode,
        "enabled": subscription.enabled,
        "filter": filter_rules,
        "mapping": mapping,
        "target_ok": target_ok,
        "missing_columns": missing_columns,
        "coalesce_seconds": subscription.coalesce_seconds,
        "max_per_minute": subscription.max_per_minute,
    }
    return summary, issues


def _poll_issue(source: ExternalSourceRow, *, now: int) -> dict[str, str] | None:
    if source.kind != "connector_poll" or not source.enabled:
        return None
    config = _json(source.config_json)
    try:
        interval = int(config.get("interval_seconds") or 300)
    except (TypeError, ValueError):
        interval = 300
    last = source.last_poll_at or 0
    if last and now - last > max(interval * 2 + POLL_STALE_GRACE_SECONDS, 600):
        return {
            "severity": "warning",
            "code": "source_poll_stale",
            "message": (f"connector poll has not run for {now - last}s (interval {interval}s)"),
            "suggested_action": "check the source error and whether the worker is running",
        }
    return None


async def integration_inventory(
    service: AppService,
    repos: RepoBundle,
    row: AppRecord,
    *,
    delivery_limit: int = 200,
) -> dict[str, Any]:
    """Sources, subscriptions, webhooks, schedules and services for one app."""
    now = int(time.time())
    subscriptions = repos.external_subscription_repo.list_for_target("app", row.app_id)
    source_ids = list(dict.fromkeys(item.source_id for item in subscriptions))
    sources = [
        item
        for source_id in source_ids
        if (item := repos.external_source_repo.get(source_id)) is not None
    ]
    webhooks = repos.app_webhook_repo.list_for_app(row.app_id)
    deliveries = repos.external_delivery_repo.list_for_target(
        "app", row.app_id, limit=delivery_limit
    )
    ingress = repos.app_ingress_repo.list_for_app(row.app_id)
    return {
        "app_id": row.app_id,
        "app_name": row.name,
        "ingress": [
            {
                "ingress_id": item.ingress_id,
                "name": item.name,
                "mode": item.mode,
                "enabled": item.enabled,
                "source_id": item.source_id,
                "subscription_id": item.subscription_id,
                "hook_path": f"/api/hooks/{item.source_id}",
            }
            for item in ingress
        ],
        "sources": [_source_summary(item, now=now) for item in sources],
        "subscriptions": [
            (
                await _subscription_summary(
                    service,
                    row,
                    item,
                    next((s for s in sources if s.source_id == item.source_id), None),
                )
            )[0]
            for item in subscriptions
        ],
        "outbound_webhooks": [
            {
                "webhook_id": item.webhook_id,
                "name": item.name,
                "url": item.url,
                "event_types": list(item.event_types),
                "enabled": item.enabled,
                "updated_at": item.updated_at,
                "deliveries": {
                    "window": len(
                        outbound := repos.external_delivery_repo.list_for_target(
                            "webhook", item.webhook_id, limit=50
                        )
                    ),
                    "counts": _delivery_counts(outbound),
                },
            }
            for item in webhooks
        ],
        "schedules": [
            {
                "schedule_id": item.schedule_id,
                "action": item.action,
                "interval_seconds": item.interval_seconds,
                "next_run_at": item.next_run_at,
                "last_run_at": item.last_run_at,
                "last_status": item.last_status,
                "last_error": item.last_error,
                "failure_count": item.failure_count,
                "enabled": item.enabled,
            }
            for item in repos.app_schedule_repo.list_for_app(row.app_id)
        ],
        "services": [
            {
                "service_id": item.service_id,
                "name": item.name,
                "provider": item.provider,
                "enabled": item.enabled,
                "public_live": item.public_live,
            }
            for item in repos.app_service_repo.list_for_app(row.app_id)
        ],
        "deliveries": {
            "window": len(deliveries),
            "counts": _delivery_counts(deliveries),
        },
    }


async def integration_health(
    service: AppService,
    repos: RepoBundle,
    row: AppRecord,
    *,
    delivery_limit: int = 200,
) -> dict[str, Any]:
    """Inventory plus actionable findings for one app's integrations."""
    now = int(time.time())
    inventory = await integration_inventory(service, repos, row, delivery_limit=delivery_limit)
    issues: list[dict[str, str]] = []
    subscriptions = repos.external_subscription_repo.list_for_target("app", row.app_id)
    sources = {
        item.source_id: item
        for item in (
            repos.external_source_repo.get(subscription.source_id) for subscription in subscriptions
        )
        if item is not None
    }
    for source in sources.values():
        if not source.enabled:
            issues.append(
                {
                    "severity": "error",
                    "code": "source_disabled",
                    "message": f"inbound source {source.name!r} is disabled",
                    "suggested_action": "enable the source after checking why it was stopped",
                }
            )
        if source.last_error:
            issues.append(
                {
                    "severity": "error",
                    "code": "source_last_error",
                    "message": f"source {source.name!r}: {source.last_error}",
                    "suggested_action": "fix the connector/HMAC/payload error, then rerun",
                }
            )
        poll_issue = _poll_issue(source, now=now)
        if poll_issue is not None:
            issues.append(poll_issue)
    for subscription in subscriptions:
        if not subscription.enabled:
            issues.append(
                {
                    "severity": "error",
                    "code": "subscription_disabled",
                    "message": f"subscription {subscription.subscription_id} is disabled",
                    "suggested_action": "enable it if the integration should still deliver",
                }
            )
        _summary, subscription_issues = await _subscription_summary(
            service,
            row,
            subscription,
            sources.get(subscription.source_id),
        )
        issues.extend(subscription_issues)

    deliveries = repos.external_delivery_repo.list_for_target(
        "app", row.app_id, limit=delivery_limit
    )
    dead = [item for item in deliveries if item.status == "dead"]
    failed = [item for item in deliveries if item.status == "failed"]
    stale_pending = [
        item
        for item in deliveries
        if item.status in {"pending", "delivering"}
        and now - item.created_at > PENDING_STALE_SECONDS
    ]
    if dead:
        issues.append(
            {
                "severity": "error",
                "code": "delivery_dead",
                "message": f"{len(dead)} delivery/deliveries are dead-lettered",
                "suggested_action": "inspect last_error, fix the cause, then retry the delivery",
            }
        )
    if failed:
        issues.append(
            {
                "severity": "warning",
                "code": "delivery_failed",
                "message": f"{len(failed)} delivery/deliveries are waiting to retry",
                "suggested_action": "check last_error; retries use exponential backoff",
            }
        )
    if stale_pending:
        issues.append(
            {
                "severity": "warning",
                "code": "delivery_pending_stale",
                "message": f"{len(stale_pending)} delivery/deliveries have been pending for over an hour",
                "suggested_action": "check whether the worker is running and the target is available",
            }
        )
    for item in deliveries:
        result = item.result or {}
        if result.get("partial") is True or result.get("blocked_pii"):
            issues.append(
                {
                    "severity": "warning",
                    "code": "import_partial",
                    "message": (
                        f"delivery {item.delivery_id} completed partially "
                        f"(error_count={result.get('error_count', 0)}, "
                        f"blocked_pii={result.get('blocked_pii', [])})"
                    ),
                    "suggested_action": "check the mapping and include_pii policy before retrying",
                }
            )
            break

    for webhook in repos.app_webhook_repo.list_for_app(row.app_id):
        if not webhook.enabled:
            issues.append(
                {
                    "severity": "warning",
                    "code": "webhook_disabled",
                    "message": f"outbound webhook {webhook.name!r} is disabled",
                    "suggested_action": "enable it if the app should still emit events",
                }
            )
        outbound = repos.external_delivery_repo.list_for_target(
            "webhook", webhook.webhook_id, limit=50
        )
        outbound_dead = [item for item in outbound if item.status == "dead"]
        if outbound_dead:
            issues.append(
                {
                    "severity": "error",
                    "code": "webhook_delivery_dead",
                    "message": (
                        f"outbound webhook {webhook.name!r} has "
                        f"{len(outbound_dead)} dead delivery/deliveries"
                    ),
                    "suggested_action": "check the endpoint URL, HMAC secret and receiver health",
                }
            )

    for schedule in repos.app_schedule_repo.list_for_app(row.app_id):
        if schedule.enabled and (schedule.last_status == "error" or schedule.failure_count):
            issues.append(
                {
                    "severity": "warning",
                    "code": "schedule_failing",
                    "message": (
                        f"schedule {schedule.schedule_id} has "
                        f"{schedule.failure_count} failure(s): {schedule.last_error}"
                    ),
                    "suggested_action": "inspect the action target; disable it if it is obsolete",
                }
            )

    recent_failures = [
        _delivery_summary(item)
        for item in deliveries
        if item.status in {"dead", "failed"} or item.last_error
    ][:MAX_RECENT_FAILURES]
    return {
        **inventory,
        "healthy": not any(item["severity"] == "error" for item in issues),
        "issues": issues,
        "recent_failures": recent_failures,
        "checked_at": now,
    }


async def integration_trace(
    service: AppService,
    repos: RepoBundle,
    row: AppRecord,
    reference: str,
    *,
    source_id: str | None = None,
) -> dict[str, Any]:
    """Trace one event (internal event id or external id) into this app."""
    event = repos.external_event_repo.get(reference)
    if event is None:
        event = repos.external_event_repo.get_by_external_id(reference, source_id=source_id)
    if event is None:
        raise ValueError(f"event not found: {reference}")

    deliveries = repos.external_delivery_repo.list_by_event(event.event_id)
    webhooks = {item.webhook_id for item in repos.app_webhook_repo.list_for_app(row.app_id)}
    app_subscription_ids = {
        item.subscription_id
        for item in repos.external_subscription_repo.list_for_target("app", row.app_id)
    }
    visible = [
        item
        for item in deliveries
        if (item.target_type == "app" and item.target_id == row.app_id)
        or (item.target_type == "webhook" and item.target_id in webhooks)
        or item.subscription_id in app_subscription_ids
    ]
    if not visible:
        raise ValueError("event does not target this MiniApp")

    inbox = repos.app_event_repo.get_for_app(row.app_id, event.event_id)
    imported_rows = repos.light_app_repo.list_data_rows_by_client_request_prefix(
        row.app_id, f"evt:{event.event_id}", limit=100
    )
    source = repos.external_source_repo.get(event.source_id)
    return {
        "event": {
            "event_id": event.event_id,
            "external_id": event.external_id,
            "event_type": event.event_type,
            "source_id": event.source_id,
            "occurred_at": event.occurred_at,
            "received_at": event.received_at,
            "summary": event.summary,
            "status": event.status,
        },
        "source": _source_summary(source, now=int(time.time())) if source else None,
        "deliveries": [_delivery_summary(item) for item in visible],
        "inbox": (
            {
                "event_id": inbox.event_id,
                "event_type": inbox.event_type,
                "summary": inbox.summary,
                "acknowledged_at": inbox.acknowledged_at,
            }
            if inbox is not None
            else None
        ),
        "rows": [
            {
                "row_id": item.row_id,
                "table_key": item.table_key,
                "values": item.values,
            }
            for item in imported_rows
        ],
    }
