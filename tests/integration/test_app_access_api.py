"""One-time MiniApp authenticated link API."""

from __future__ import annotations

import httpx

from octop.infra.apps.service import AppService
from octop.infra.apps.wiring import build_app_service
from octop.infra.server import OctopServer

COLUMNS: list[dict[str, object]] = [{"name": "title", "type": "string", "required": True}]


def _service(srv: OctopServer) -> AppService:
    assert srv.services is not None
    assert srv.app_runtime is not None
    return build_app_service(
        repos=srv.services.repos,
        workspaces=srv.app_runtime.agent_registry,
    )


async def test_preview_does_not_consume_and_exchange_returns_jwt(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    assert srv.services is not None
    alice = srv.services.user_repo.get_by_username("alice")
    assert alice is not None
    service = _service(srv)
    app = await service.create(
        agent_id=agent_id,
        owner_user_id=alice.id,
        name="Tracker",
        columns=COLUMNS,
    )
    code, _expires_at = service.create_access_link(
        app,
        user_id=alice.id,
        channel_type="weixin",
        channel_subject_id="wx-alice",
        origin="test",
    )

    first = await client.post("/api/auth/app-access/preview", json={"code": code})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    body = first.json()
    assert body["app_id"] == app.app_id
    assert body["app_name"] == "Tracker"
    assert body["username"] == "alice"

    # Preview is read-only: a second preview and the exchange still work.
    assert (
        await client.post("/api/auth/app-access/preview", json={"code": code})
    ).status_code == 200
    exchanged = await client.post("/api/auth/app-access/exchange", json={"code": code})
    assert exchanged.status_code == 200
    login = exchanged.json()
    assert login["access_token"]
    assert login["redirect"] == f"/apps/{app.app_id}"
    assert login["user"]["username"] == "alice"
    assert login["scope"] == "app"
    assert login["app_id"] == app.app_id
    assert login["expires_in"] == 7200

    auth = {"Authorization": f"Bearer {login['access_token']}"}
    me = await client.get("/api/auth/me", headers=auth)
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    # The session opens this app but not the rest of the dashboard.
    detail = await client.get(f"/api/apps/{app.app_id}", headers=auth)
    assert detail.status_code == 200
    blocked = await client.get("/api/agents", headers=auth)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "APP_SCOPE_FORBIDDEN"

    second = await client.post("/api/auth/app-access/exchange", json={"code": code})
    assert second.status_code == 410
    assert second.json()["error"]["code"] == "APP_ACCESS_LINK_INVALID"


async def test_guest_access_link_returns_a_guest_session(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, _alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    assert srv.services is not None
    alice = srv.services.user_repo.get_by_username("alice")
    assert alice is not None
    guest_id = srv.services.user_repo.create(
        username="guest-link",
        password_hash=None,
        role="user",
        kind="guest",
    )
    service = _service(srv)
    app = await service.create(
        agent_id=agent_id,
        owner_user_id=alice.id,
        name="Guest tracker",
        columns=COLUMNS,
    )
    srv.services.light_app_repo.upsert_collaborator(
        app.app_id, guest_id, "participate", status="active"
    )
    code, _ = service.create_access_link(
        app,
        user_id=guest_id,
        channel_type="feishu",
        account_key="app_1",
        sender_id="ou_guest",
        origin="test",
    )

    exchanged = await client.post("/api/auth/app-access/exchange", json={"code": code})
    assert exchanged.status_code == 200, exchanged.text
    login = exchanged.json()
    assert login["guest_token"]
    assert login["refresh_token"]
    auth = {"Authorization": f"Bearer {login['access_token']}"}
    detail = await client.get(f"/api/apps/{app.app_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["permission"] == "participate"
    # The app token is scoped to this app; the guest portal has its own token.
    portal = await client.get("/api/guest/apps", headers=auth)
    assert portal.status_code == 403
    assert portal.json()["error"]["code"] == "APP_SCOPE_FORBIDDEN"


async def test_invalid_access_code_is_410(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, _srv, _alice_auth, _bob_auth, _agent_id = env_alice_bob_agent
    r = await client.post("/api/auth/app-access/preview", json={"code": "nope"})
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "APP_ACCESS_LINK_INVALID"
