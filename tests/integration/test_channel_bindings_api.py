"""IM identity binding HTTP surface."""

from __future__ import annotations

import httpx

from octop.infra.auth.channel_binding import bind_identity
from octop.infra.server import OctopServer


async def test_bind_code_and_binding_lifecycle(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, _agent_id = env_alice_bob_agent
    assert srv.services is not None
    alice = srv.services.user_repo.get_by_username("alice")
    assert alice is not None

    created = await client.post("/api/auth/channel-bindings/code", headers=alice_auth)
    assert created.status_code == 200
    body = created.json()
    assert body["code"]
    assert body["ttl_seconds"] == 300
    assert body["expires_at"] > 0

    empty = await client.get("/api/auth/channel-bindings", headers=alice_auth)
    assert empty.status_code == 200
    assert empty.json() == {"identities": []}

    # Simulate the /bind command consuming the code from the IM private chat.
    bind_identity(
        identity_repo=srv.services.repos.channel_identity_repo,
        code_repo=srv.services.repos.channel_bind_code_repo,
        code=body["code"],
        channel_type="weixin",
        subject_id="wx-alice",
    )

    listed = await client.get("/api/auth/channel-bindings", headers=alice_auth)
    assert listed.status_code == 200
    identities = listed.json()["identities"]
    assert identities == [
        {
            "channel_type": "weixin",
            "subject_id": "wx-alice",
            "verified_at": identities[0]["verified_at"],
        }
    ]

    # Another user cannot remove it.
    denied = await client.delete(
        "/api/auth/channel-bindings",
        headers=bob_auth,
        params={"channel_type": "weixin", "subject_id": "wx-alice"},
    )
    assert denied.status_code == 404

    removed = await client.delete(
        "/api/auth/channel-bindings",
        headers=alice_auth,
        params={"channel_type": "weixin", "subject_id": "wx-alice"},
    )
    assert removed.status_code == 204
    assert (await client.get("/api/auth/channel-bindings", headers=alice_auth)).json() == {
        "identities": []
    }


async def test_bind_endpoints_require_auth(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, _srv, _alice_auth, _bob_auth, _agent_id = env_alice_bob_agent
    assert (await client.post("/api/auth/channel-bindings/code")).status_code == 401
    assert (await client.get("/api/auth/channel-bindings")).status_code == 401
