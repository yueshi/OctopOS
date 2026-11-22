"""Agent host policy and external-participant approval flows."""

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
        name="Hosted",
        columns=COLUMNS,
    )
    return row.app_id


async def _set_policy(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    agent_id: str,
    *,
    allow: bool = True,
    require_approval: bool = False,
    max_guests: int = 50,
) -> dict:
    r = await client.patch(
        f"/api/agents/{agent_id}/external-sharing",
        headers=auth,
        json={
            "allow_external_participants": allow,
            "max_guests_per_app": max_guests,
            "require_host_approval": require_approval,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _join_code(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    app_id: str,
) -> str:
    r = await client.post(
        f"/api/apps/{app_id}/join-codes",
        headers=auth,
        json={"audience": "guest", "permission": "participate", "max_uses": 1},
    )
    assert r.status_code == 200, r.text
    return r.json()["code"]


async def test_policy_defaults_and_permissions(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    await _create_app(srv, agent_id)

    r = await client.get(f"/api/agents/{agent_id}/external-sharing", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "allow_external_participants": False,
        "max_guests_per_app": 50,
        "require_host_approval": True,
        "can_manage": True,
    }

    # Bob neither owns the agent nor hosts an app on it yet.
    r = await client.get(f"/api/agents/{agent_id}/external-sharing", headers=bob_auth)
    assert r.status_code == 403
    r = await client.patch(
        f"/api/agents/{agent_id}/external-sharing",
        headers=bob_auth,
        json={"allow_external_participants": True},
    )
    assert r.status_code == 403

    updated = await _set_policy(
        client,
        alice_auth,
        agent_id,
        allow=True,
        require_approval=True,
        max_guests=10,
    )
    assert updated["allow_external_participants"] is True
    assert updated["max_guests_per_app"] == 10

    # An app owner hosted on the agent may read the policy but not change it.
    bob_app = await _create_app(srv, agent_id, owner="bob")
    r = await client.get(f"/api/agents/{agent_id}/external-sharing", headers=bob_auth)
    assert r.status_code == 200, r.text
    r = await client.patch(
        f"/api/agents/{agent_id}/external-sharing",
        headers=bob_auth,
        json={"allow_external_participants": False},
    )
    assert r.status_code == 403
    assert bob_app


async def test_default_policy_blocks_external_participants(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)

    r = await client.post(
        f"/api/apps/{app_id}/guests",
        headers=alice_auth,
        json={"display_name": "Eva"},
    )
    assert r.status_code == 403

    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=alice_auth,
        json={"audience": "guest", "permission": "participate"},
    )
    assert r.status_code == 403

    r = await client.post(
        f"/api/apps/{app_id}/join-codes",
        headers=alice_auth,
        json={"audience": "guest", "permission": "participate"},
    )
    assert r.status_code == 403

    r = await client.post(
        f"/api/apps/{app_id}/members",
        headers=alice_auth,
        json={"username": "bob", "permission": "view"},
    )
    assert r.status_code == 403

    # A member invite can be created, but an external member cannot redeem it.
    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=alice_auth,
        json={"audience": "member", "permission": "view"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["invite_url"].split("invite=", 1)[1]
    r = await client.post(
        "/api/apps/invites/redeem",
        headers=bob_auth,
        json={"token": token},
    )
    assert r.status_code == 403


async def test_external_member_invite_needs_host_then_redeems(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id, require_approval=True)

    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=alice_auth,
        json={"audience": "member", "permission": "view"},
    )
    assert r.status_code == 200, r.text
    invite = r.json()
    token = invite["invite_url"].split("invite=", 1)[1]

    r = await client.post(
        "/api/apps/invites/redeem",
        headers=bob_auth,
        json={"token": token},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_HOST_APPROVAL_REQUIRED"

    r = await client.patch(
        f"/api/apps/{app_id}/invites/{invite['invite_id']}",
        headers=alice_auth,
        json={"host_status": "approved"},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/apps/invites/redeem",
        headers=bob_auth,
        json={"token": token},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "view"


async def test_guest_cap_comes_from_policy(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id, max_guests=1)

    first = await _join_code(client, alice_auth, app_id)
    r = await client.post("/api/apps/join/redeem", json={"code": first})
    assert r.status_code == 200, r.text

    second = await _join_code(client, alice_auth, app_id)
    r = await client.post("/api/apps/join/redeem", json={"code": second})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "APP_LIMIT_EXCEEDED"


async def test_invite_waits_for_host_approval(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id, require_approval=True)

    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=alice_auth,
        json={"audience": "guest", "permission": "participate"},
    )
    assert r.status_code == 200, r.text
    invite = r.json()
    assert invite["host_status"] == "pending"
    token = invite["invite_url"].split("invite=", 1)[1]

    r = await client.post("/api/apps/invites/preview", json={"token": token})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_HOST_APPROVAL_REQUIRED"

    r = await client.patch(
        f"/api/apps/{app_id}/invites/{invite['invite_id']}",
        headers=alice_auth,
        json={"host_status": "approved"},
    )
    assert r.status_code == 200, r.text
    r = await client.post("/api/apps/invites/preview", json={"token": token})
    assert r.status_code == 200, r.text
    r = await client.post("/api/apps/invites/redeem", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


async def test_external_member_add_waits_for_approval(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id, require_approval=True)

    r = await client.post(
        f"/api/apps/{app_id}/members",
        headers=alice_auth,
        json={"username": "bob", "permission": "view"},
    )
    assert r.status_code == 200, r.text
    member = r.json()
    assert member["status"] == "pending"
    r = await client.post(f"/api/apps/{app_id}/session", headers=bob_auth)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_MEMBER_PENDING"

    r = await client.patch(
        f"/api/apps/{app_id}/members/{member['user_id']}",
        headers=alice_auth,
        json={"status": "active"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    r = await client.post(f"/api/apps/{app_id}/session", headers=bob_auth)
    assert r.status_code == 200, r.text


async def test_agent_owner_approves_pending_join(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, owner="bob")
    await _set_policy(client, alice_auth, agent_id, require_approval=True)

    # The host can read the invite queue and resolve host approval even though
    # it does not own the MiniApp.
    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=bob_auth,
        json={"audience": "guest", "permission": "participate"},
    )
    assert r.status_code == 200, r.text
    invite = r.json()
    assert invite["host_status"] == "pending"
    r = await client.get(f"/api/apps/{app_id}/invites", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert [item["invite_id"] for item in r.json()["invites"]] == [invite["invite_id"]]
    r = await client.patch(
        f"/api/apps/{app_id}/invites/{invite['invite_id']}",
        headers=alice_auth,
        json={"host_status": "approved"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["host_status"] == "approved"

    code = await _join_code(client, bob_auth, app_id)
    r = await client.post("/api/apps/join/preview", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json()["requires_approval"] is False
    assert r.json()["host_approval_required"] is True

    r = await client.post("/api/apps/join/redeem", json={"code": code})
    assert r.status_code == 200, r.text
    redeemed = r.json()
    assert redeemed["status"] == "pending"
    guest_headers = {"Authorization": f"Bearer {redeemed['guest_token']}"}

    # The pending membership cannot mint an app token yet.
    r = await client.post(f"/api/guest/apps/{app_id}/session", headers=guest_headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_MEMBER_PENDING"

    # The agent owner (host), who is not the app owner, approves status only.
    r = await client.patch(
        f"/api/apps/{app_id}/members/{redeemed['user_id']}",
        headers=alice_auth,
        json={"status": "active"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    r = await client.post(f"/api/guest/apps/{app_id}/session", headers=guest_headers)
    assert r.status_code == 200, r.text
