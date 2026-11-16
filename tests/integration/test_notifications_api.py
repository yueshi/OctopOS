"""Durable notification inbox API and HITL persistence."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from octop.api.routers.app_access import _push_owner, _push_requester
from octop.infra.db.repos.app_access import AppAccessRequestRow
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, ensure_users


@pytest.fixture
async def env(tmp_octop_home: Path) -> AsyncIterator[Any]:
    async with octop_client(tmp_octop_home) as (c, srv):
        await bootstrap_admin(c, tmp_octop_home)
        admin_auth = await auth_header(c)
        users = await ensure_users(c, admin_auth, "alice", "bob")
        yield c, srv, users["alice"], users["bob"]


async def test_notification_inbox_read_and_clear(env: Any) -> None:
    c, srv, alice_auth, bob_auth = env
    alice = srv.user_manager.get("alice")
    bob = srv.user_manager.get("bob")
    assert alice is not None and bob is not None
    repo = srv.services.notification_repo

    alice_row = repo.create(
        user_id=alice.id,
        kind="approval",
        title="Access request",
        body="bob → Tracker",
        path="/apps/app1",
        dedupe_key="access:req1",
    )
    repo.create(
        user_id=bob.id,
        kind="info",
        title="Bob only",
        dedupe_key="bob:1",
    )

    r = await c.get("/api/notifications", headers=alice_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["unread"] == 1
    assert [item["notification_id"] for item in body["notifications"]] == [
        alice_row.notification_id
    ]
    assert body["notifications"][0]["read"] is False

    r = await c.get("/api/notifications", headers=bob_auth)
    assert [item["title"] for item in r.json()["notifications"]] == ["Bob only"]

    r = await c.post(
        f"/api/notifications/{alice_row.notification_id}/read",
        headers=alice_auth,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert repo.unread_count(alice.id) == 0

    r = await c.delete("/api/notifications", headers=alice_auth)
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}
    assert repo.list_for_user(alice.id) == []


async def test_notification_read_all_and_ownership(env: Any) -> None:
    c, srv, alice_auth, bob_auth = env
    alice = srv.user_manager.get("alice")
    bob = srv.user_manager.get("bob")
    assert alice is not None and bob is not None
    repo = srv.services.notification_repo
    one = repo.create(user_id=alice.id, kind="info", title="one")
    two = repo.create(user_id=alice.id, kind="info", title="two")

    # Another user cannot mark or clear Alice's rows.
    r = await c.post(
        f"/api/notifications/{one.notification_id}/read",
        headers=bob_auth,
    )
    assert r.status_code == 404
    assert repo.get(alice.id, one.notification_id).read is False  # type: ignore[union-attr]

    r = await c.post("/api/notifications/read-all", headers=alice_auth)
    assert r.status_code == 200
    assert r.json() == {"updated": 2}
    assert repo.unread_count(alice.id) == 0
    assert repo.get(alice.id, two.notification_id).read is True  # type: ignore[union-attr]


async def test_access_request_pushes_persist_owner_and_requester(env: Any) -> None:
    c, srv, alice_auth, bob_auth = env
    alice = srv.user_manager.get("alice")
    bob = srv.user_manager.get("bob")
    admin = srv.user_manager.get("admin")
    assert alice is not None and bob is not None and admin is not None

    request = AppAccessRequestRow(
        request_id="req_demo",
        pk=1,
        app_id=None,
        agent_id="main",
        requester_user_id=alice.id,
        app_owner_user_id=bob.id,
        agent_owner_user_id=admin.id,
        needed="view",
        note="",
        status="approved",
        created_at=1,
        resolved_at=2,
        resolved_by=bob.id,
    )
    await _push_owner(srv, request)
    await _push_requester(srv, request)

    r = await c.get("/api/notifications", headers=bob_auth)
    rows = r.json()["notifications"]
    assert [row["dedupe_key"] for row in rows] == ["access:req_demo"]
    assert rows[0]["kind"] == "approval"

    r = await c.get("/api/notifications", headers=alice_auth)
    rows = r.json()["notifications"]
    assert [row["dedupe_key"] for row in rows] == ["access_resolved:req_demo"]
    assert rows[0]["kind"] == "success"


async def test_hitl_push_persists_one_notification(env: Any) -> None:
    c, srv, alice_auth, _bob_auth = env
    alice = srv.user_manager.get("alice")
    assert alice is not None
    frame = {
        "type": "approval_required",
        "agent_id": "ag1",
        "thread_id": "thr1",
        "pending_id": "hitl-1",
        "deep_link": "/chat/ag1/thr1",
    }

    await srv.app_runtime.gateway.notify_hitl_pending(alice.id, frame)
    await srv.app_runtime.gateway.notify_hitl_pending(alice.id, frame)

    r = await c.get("/api/notifications", headers=alice_auth)
    rows = r.json()["notifications"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "approval"
    assert rows[0]["dedupe_key"] == "approval:hitl-1"
    assert rows[0]["path"] == "/chat/ag1/thr1"
    assert "ag1" in rows[0]["body"]
