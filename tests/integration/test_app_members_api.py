"""HTTP surface for MiniApp members, invites and join codes."""

from __future__ import annotations

import httpx
import pytest

from octop.infra.apps.wiring import build_app_service
from octop.infra.server import OctopServer

COLUMNS = [
    {"name": "title", "type": "string"},
    {"name": "status", "type": "enum", "values": ["new", "done"]},
]


def _service(srv: OctopServer):
    assert srv.services is not None
    assert srv.app_runtime is not None
    return build_app_service(
        repos=srv.services.repos,
        workspaces=srv.app_runtime.agent_registry,
    )


async def _create_app(srv: OctopServer, agent_id: str, *, owner: str = "alice") -> str:
    assert srv.services is not None
    account = srv.services.user_repo.get_by_username(owner)
    assert account is not None
    row = await _service(srv).create(
        agent_id=agent_id,
        owner_user_id=account.id,
        name="Members",
        columns=COLUMNS,
    )
    return row.app_id


async def _set_policy(
    client: httpx.AsyncClient,
    owner_auth: dict[str, str],
    agent_id: str,
    *,
    allow: bool = True,
    require_approval: bool = False,
    max_guests: int = 50,
) -> None:
    r = await client.patch(
        f"/api/agents/{agent_id}/external-sharing",
        headers=owner_auth,
        json={
            "allow_external_participants": allow,
            "max_guests_per_app": max_guests,
            "require_host_approval": require_approval,
        },
    )
    assert r.status_code == 200, r.text


async def test_members_api_lifecycle_and_permissions(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id)

    r = await client.get(f"/api/apps/{app_id}/members", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["members"] == []

    r = await client.post(
        f"/api/apps/{app_id}/members",
        headers=alice_auth,
        json={"username": "bob", "permission": "view"},
    )
    assert r.status_code == 200, r.text
    member = r.json()
    assert member["username"] == "bob"
    assert member["kind"] == "member"
    assert member["status"] == "active"
    assert member["joined_via"] == "share"

    # A plain view collaborator can use the app but cannot manage members.
    r = await client.get(f"/api/apps/{app_id}/members", headers=bob_auth)
    assert r.status_code == 403

    r = await client.patch(
        f"/api/apps/{app_id}/members/{member['user_id']}",
        headers=alice_auth,
        json={"permission": "edit_data", "status": "pending"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["permission"] == "edit_data"
    assert r.json()["status"] == "pending"

    r = await client.patch(
        f"/api/apps/{app_id}/members/{member['user_id']}",
        headers=alice_auth,
        json={"status": "active"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    r = await client.delete(
        f"/api/apps/{app_id}/members/{member['user_id']}",
        headers=alice_auth,
    )
    assert r.status_code == 204
    r = await client.get(f"/api/apps/{app_id}/members", headers=alice_auth)
    assert r.json()["members"] == []


async def test_agent_owner_can_read_members_but_not_manage(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    # Alice owns the agent; Bob owns this app on Alice's agent.
    app_id = await _create_app(srv, agent_id, owner="bob")

    r = await client.get(f"/api/apps/{app_id}/members", headers=alice_auth)
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/apps/{app_id}/members",
        headers=alice_auth,
        json={"username": "alice", "permission": "view"},
    )
    assert r.status_code == 403

    r = await client.get(f"/api/apps/{app_id}/members", headers=bob_auth)
    assert r.status_code == 200, r.text


async def test_invite_preview_and_redeem_are_public(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id)

    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=alice_auth,
        json={
            "audience": "guest",
            "permission": "participate",
            "invitee_hint": "Eva",
            "ttl_seconds": 600,
            "max_uses": 1,
        },
    )
    assert r.status_code == 200, r.text
    invite = r.json()
    assert invite["host_status"] == "not_required"
    assert "/join?invite=" in invite["invite_url"]
    token = invite["invite_url"].split("invite=", 1)[1]

    r = await client.post("/api/apps/invites/preview", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["app_name"] == "Members"
    assert r.json()["permission"] == "participate"

    r = await client.post("/api/apps/invites/redeem", json={"token": token})
    assert r.status_code == 200, r.text
    redeemed = r.json()
    assert redeemed["app_id"] == app_id
    assert redeemed["role"] == "participate"
    assert redeemed["status"] == "active"

    r = await client.post("/api/apps/invites/redeem", json={"token": token})
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "APP_INVITE_INVALID"

    r = await client.get(f"/api/apps/{app_id}/invites", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["invites"][0]["used_count"] == 1


async def test_host_approval_allows_agent_owner(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, owner="bob")
    await _set_policy(client, alice_auth, agent_id, require_approval=True)

    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=bob_auth,
        json={"audience": "guest", "permission": "participate"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["host_status"] == "pending"
    invite_id = r.json()["invite_id"]

    r = await client.patch(
        f"/api/apps/{app_id}/invites/{invite_id}",
        headers=alice_auth,
        json={"host_status": "approved"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["host_status"] == "approved"


async def test_member_invite_requires_login(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id)
    r = await client.post(
        f"/api/apps/{app_id}/invites",
        headers=alice_auth,
        json={"audience": "member", "permission": "view"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["invite_url"].split("invite=", 1)[1]

    r = await client.post("/api/apps/invites/redeem", json={"token": token})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_FAILED"

    r = await client.post(
        "/api/apps/invites/redeem",
        headers=bob_auth,
        json={"token": token},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "view"


async def test_join_code_requires_approval_then_activates(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id)
    r = await client.post(
        f"/api/apps/{app_id}/join-codes",
        headers=alice_auth,
        json={
            "audience": "guest",
            "permission": "participate",
            "max_uses": 1,
            "requires_approval": True,
        },
    )
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert code

    r = await client.post("/api/apps/join/preview", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json()["requires_approval"] is True

    r = await client.post("/api/apps/join/redeem", json={"code": code})
    assert r.status_code == 200, r.text
    redeemed = r.json()
    assert redeemed["status"] == "pending"
    assert redeemed["guest_token"]
    assert redeemed["app_token"] is None

    r = await client.patch(
        f"/api/apps/{app_id}/members/{redeemed['user_id']}",
        headers=alice_auth,
        json={"status": "active"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    # After approval the guest session can mint the app token.
    r = await client.post(
        f"/api/guest/apps/{app_id}/session",
        headers={"Authorization": f"Bearer {redeemed['guest_token']}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "participate"

    r = await client.post("/api/apps/join/redeem", json={"code": code})
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "APP_JOIN_CODE_INVALID"


async def test_join_code_list_hides_plaintext(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id)
    r = await client.post(
        f"/api/apps/{app_id}/join-codes",
        headers=alice_auth,
        json={"audience": "guest", "permission": "participate", "max_uses": 3},
    )
    assert r.status_code == 200, r.text
    assert r.json()["code"]

    r = await client.get(f"/api/apps/{app_id}/join-codes", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["join_codes"][0]["code"] is None
    assert r.json()["join_codes"][0]["max_uses"] == 3


@pytest.mark.parametrize(
    ("audience", "permission", "code"),
    [
        ("guest", "view", "APP_SHARE_INVALID"),
        ("member", "edit_data", "APP_SHARE_INVALID"),
    ],
)
async def test_join_code_rejects_roles_above_audience(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
    audience: str,
    permission: str,
    code: str,
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id)
    await _set_policy(client, alice_auth, agent_id)
    r = await client.post(
        f"/api/apps/{app_id}/join-codes",
        headers=alice_auth,
        json={"audience": audience, "permission": permission},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == code
