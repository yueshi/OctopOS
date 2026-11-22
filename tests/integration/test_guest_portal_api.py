"""Guest portal, refresh rotation and scoped app sessions."""

from __future__ import annotations

import httpx

from octop.api.routers.apps import _service
from octop.infra.server import OctopServer

COLUMNS = [
    {"name": "title", "type": "string"},
    {"name": "status", "type": "enum", "values": ["new", "done"]},
]


async def _create_app(srv: OctopServer, agent_id: str, *, owner: str = "alice") -> str:
    assert srv.services is not None
    account = srv.services.user_repo.get_by_username(owner)
    assert account is not None
    row = await _service(srv).create(
        agent_id=agent_id,
        owner_user_id=account.id,
        name="Guests",
        columns=COLUMNS,
    )
    return row.app_id


async def _enable_external(
    client: httpx.AsyncClient,
    owner_auth: dict[str, str],
    agent_id: str,
) -> None:
    r = await client.patch(
        f"/api/agents/{agent_id}/external-sharing",
        headers=owner_auth,
        json={"allow_external_participants": True, "require_host_approval": False},
    )
    assert r.status_code == 200, r.text


async def _guest_join(
    client: httpx.AsyncClient,
    owner_auth: dict[str, str],
    app_id: str,
    *,
    requires_approval: bool = False,
    headers: dict[str, str] | None = None,
) -> dict:
    r = await client.post(
        f"/api/apps/{app_id}/join-codes",
        headers=owner_auth,
        json={
            "audience": "guest",
            "permission": "participate",
            "max_uses": 1,
            "requires_approval": requires_approval,
        },
    )
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = await client.post("/api/apps/join/redeem", json={"code": code}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_guest_join_portal_app_token_and_refresh(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _enable_external(client, alice_auth, agent_id)
    redeemed = await _guest_join(client, alice_auth, app_id)
    guest_token = redeemed["guest_token"]
    refresh_token = redeemed["refresh_token"]
    app_token = redeemed["app_token"]
    assert guest_token and refresh_token and app_token
    assert redeemed["status"] == "active"

    guest_headers = {"Authorization": f"Bearer {guest_token}"}
    r = await client.get("/api/guest/apps", headers=guest_headers)
    assert r.status_code == 200, r.text
    assert [(item["app_id"], item["role"]) for item in r.json()["apps"]] == [
        (app_id, "participate")
    ]

    # A valid guest token reuses the same guest identity on another join.
    second_app = await _create_app(srv, agent_id)
    second = await _guest_join(
        client,
        alice_auth,
        second_app,
        headers=guest_headers,
    )
    assert second["user_id"] == redeemed["user_id"]
    r = await client.get("/api/guest/apps", headers=guest_headers)
    assert {item["app_id"] for item in r.json()["apps"]} == {app_id, second_app}

    r = await client.post(f"/api/guest/apps/{app_id}/session", headers=guest_headers)
    assert r.status_code == 200, r.text
    assert r.json()["app_id"] == app_id

    app_headers = {"Authorization": f"Bearer {app_token}"}
    r = await client.get(f"/api/apps/{app_id}", headers=app_headers)
    assert r.status_code == 200, r.text
    assert r.json()["app_id"] == app_id

    r = await client.post(
        "/api/guest/session/refresh",
        json={"refresh_token": refresh_token},
    )
    assert r.status_code == 200, r.text
    rotated = r.json()
    assert rotated["guest_token"]
    assert rotated["refresh_token"] != refresh_token

    r = await client.post(
        "/api/guest/session/refresh",
        json={"refresh_token": refresh_token},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "APP_SESSION_INVALID"

    rotated_headers = {"Authorization": f"Bearer {rotated['guest_token']}"}
    r = await client.delete(f"/api/guest/memberships/{app_id}", headers=rotated_headers)
    assert r.status_code == 204
    r = await client.get("/api/guest/apps", headers=rotated_headers)
    assert [item["app_id"] for item in r.json()["apps"]] == [second_app]

    # The previously minted app token stops working once the membership is gone.
    r = await client.get(f"/api/apps/{app_id}", headers=app_headers)
    assert r.status_code == 403

    r = await client.delete("/api/guest/account", headers=rotated_headers)
    assert r.status_code == 204
    r = await client.get("/api/guest/apps", headers=rotated_headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_GUEST_DISABLED"


async def test_guest_can_revoke_current_session(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _enable_external(client, alice_auth, agent_id)
    redeemed = await _guest_join(client, alice_auth, app_id)
    headers = {"Authorization": f"Bearer {redeemed['guest_token']}"}

    r = await client.delete("/api/guest/sessions/current", headers=headers)
    assert r.status_code == 204
    r = await client.get("/api/guest/apps", headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "APP_SESSION_INVALID"


async def test_member_app_session_and_pending_member(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _enable_external(client, alice_auth, agent_id)

    r = await client.post(f"/api/apps/{app_id}/session", headers=alice_auth)
    assert r.status_code == 200, r.text
    session = r.json()
    assert session["role"] == "owner"
    r = await client.get(
        f"/api/apps/{app_id}",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/apps/{app_id}/members",
        headers=alice_auth,
        json={"username": "bob", "permission": "view"},
    )
    assert r.status_code == 200, r.text
    bob_id = r.json()["user_id"]
    r = await client.patch(
        f"/api/apps/{app_id}/members/{bob_id}",
        headers=alice_auth,
        json={"status": "pending"},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/apps/{app_id}/session", headers=bob_auth)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_MEMBER_PENDING"


async def test_guest_mints_link_code_with_app_token(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _enable_external(client, alice_auth, agent_id)
    redeemed = await _guest_join(client, alice_auth, app_id)
    app_headers = {"Authorization": f"Bearer {redeemed['app_token']}"}

    r = await client.post(f"/api/apps/{app_id}/link-code", headers=app_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["code"]) >= 6
    assert body["ttl_seconds"] == 300
    assert body["expires_at"] > 0
    assert srv.services is not None
    with srv.services.db.connect() as conn:
        row = conn.execute(
            "SELECT user_id, app_id FROM channel_link_codes ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert int(row["user_id"]) == redeemed["user_id"]
    assert str(row["app_id"]) == app_id
