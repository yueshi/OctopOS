"""Durable notification inbox helpers shared by the API and gateway."""

from __future__ import annotations

import asyncio
from typing import Any

from octop.infra.db.repos.notifications import NotificationRepo, NotificationRow


def notification_payload(row: NotificationRow) -> dict[str, Any]:
    """Serialize one inbox row for the dashboard notification socket."""
    return {
        "notification_id": row.notification_id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "path": row.path,
        "dedupe_key": row.dedupe_key,
        "read": row.read,
        "created_at": row.created_at,
    }


def notification_frame(row: NotificationRow) -> dict[str, Any]:
    return {"type": "notification", "notification": notification_payload(row)}


async def create_notification(
    repo: NotificationRepo,
    *,
    user_id: int,
    kind: str,
    title: str,
    body: str = "",
    path: str | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """Persist a row off the event loop and return its socket frame."""
    row = await asyncio.to_thread(
        repo.create,
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        path=path,
        dedupe_key=dedupe_key,
    )
    return notification_frame(row)
