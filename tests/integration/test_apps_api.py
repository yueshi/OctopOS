"""Integration tests for the light-app HTTP API and sharing ACL."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
from langchain_core.messages import AIMessage

from octop.config import GameConfig
from octop.infra.apps import capabilities as app_capabilities
from octop.infra.apps.game_sessions import create_game_token
from octop.infra.apps.service import AppService
from octop.infra.apps.wiring import build_app_service
from octop.infra.server import OctopServer

COLUMNS: list[dict[str, object]] = [
    {"name": "title", "type": "string", "required": True},
    {"name": "status", "type": "enum", "values": ["new", "done"]},
]


def _service(srv: OctopServer) -> AppService:
    assert srv.services is not None
    assert srv.app_runtime is not None
    return build_app_service(
        repos=srv.services.repos,
        workspaces=srv.app_runtime.agent_registry,
    )


async def _create_app(
    srv: OctopServer,
    agent_id: str,
    username: str,
    *,
    capabilities: list[str] | None = None,
    columns: list[dict[str, object]] | None = None,
) -> str:
    assert srv.services is not None
    owner = srv.services.user_repo.get_by_username(username)
    assert owner is not None
    row = await _service(srv).create(
        agent_id=agent_id,
        owner_user_id=owner.id,
        name="Tracker",
        description="things",
        columns=columns or COLUMNS,
        capabilities=capabilities,
    )
    return row.app_id


async def test_owner_app_flow(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.get("/api/apps", headers=alice_auth)
    assert r.status_code == 200
    apps = r.json()["apps"]
    assert [item["app_id"] for item in apps] == [app_id]
    assert apps[0]["permission"] == "edit_structure"
    assert apps[0]["owner_username"] == "alice"
    assert apps[0]["has_html"] is False
    assert apps[0]["html_bytes"] == 0
    assert apps[0]["data_bytes"] > 0

    r = await client.get(f"/api/apps/{app_id}/files", headers=alice_auth)
    assert r.status_code == 200
    files = {item["name"]: item for item in r.json()["files"]}
    assert set(files) == {"app.html", "data.csv", "schema.json", "manifest.json"}
    assert files["data.csv"]["size"] > 0
    assert files["data.csv"]["path"] == f"apps/{app_id}/data.csv"

    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert r.status_code == 200
    detail = r.json()
    assert detail["name"] == "Tracker"
    assert detail["has_html"] is False
    assert [col["name"] for col in detail["columns"]] == ["title", "status"]
    assert [item["table_key"] for item in detail["tables"]] == ["main"]
    assert detail["tables"][0]["is_main"] is True
    assert [col["name"] for col in detail["tables"][0]["columns"]] == ["title", "status"]

    r = await client.get(f"/api/apps/{app_id}/tables", headers=alice_auth)
    assert r.status_code == 200
    assert [item["table_key"] for item in r.json()["tables"]] == ["main"]

    r = await client.get(f"/api/apps/{app_id}/html", headers=alice_auth)
    assert r.status_code == 200
    assert r.text == ""

    await _service(srv).write_html(_service(srv).get(app_id), "<h1>Hi</h1>")
    r = await client.get("/api/apps", headers=alice_auth)
    assert r.json()["apps"][0]["has_html"] is True
    assert r.json()["apps"][0]["html_bytes"] == len("<h1>Hi</h1>")

    r = await client.get(f"/api/apps/{app_id}/html", headers=alice_auth)
    assert r.status_code == 200
    assert r.text == "<h1>Hi</h1>"
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "A", "status": "new"}},
    )
    assert r.status_code == 200
    appended = r.json()
    assert appended["row_index"] == 0
    assert appended["row"] == {"title": "A", "status": "new"}
    assert appended["table"] == "main"
    assert appended["row_id"].startswith("row_")
    assert appended["version"] == 1

    r = await client.get(f"/api/apps/{app_id}/rows?limit=10", headers=alice_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["table"] == "main"
    assert body["rows"] == [{"title": "A", "status": "new"}]

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "B", "nope": "1"}},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_ROW_INVALID"

    r = await client.patch(
        f"/api/apps/{app_id}",
        headers=alice_auth,
        json={"name": "Renamed", "description": ""},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert r.json()["description"] == ""

    r = await client.delete(f"/api/apps/{app_id}", headers=alice_auth)
    assert r.status_code == 200
    assert r.json() == {"deleted": app_id}
    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "APP_NOT_FOUND"


async def test_sharing_grants_view_then_edit_data(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.get("/api/apps", headers=bob_auth)
    assert r.json()["apps"] == []
    for r in (
        await client.get(f"/api/apps/{app_id}", headers=bob_auth),
        await client.get(f"/api/apps/{app_id}/tables", headers=bob_auth),
        await client.get(f"/api/apps/{app_id}/rows", headers=bob_auth),
        await client.get(f"/api/apps/{app_id}/shares", headers=bob_auth),
        await client.post(
            f"/api/apps/{app_id}/rows",
            headers=bob_auth,
            json={"row": {"title": "B"}},
        ),
    ):
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "APP_FORBIDDEN"

    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "view"}]},
    )
    assert r.status_code == 200
    shares = r.json()["shares"]
    assert [(s["username"], s["permission"]) for s in shares] == [("bob", "view")]

    r = await client.get("/api/apps", headers=bob_auth)
    assert [(item["app_id"], item["permission"]) for item in r.json()["apps"]] == [(app_id, "view")]
    r = await client.get(f"/api/apps/{app_id}", headers=bob_auth)
    assert r.status_code == 200
    r = await client.get(f"/api/apps/{app_id}/rows", headers=bob_auth)
    assert r.status_code == 200
    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=bob_auth,
        json={"row": {"title": "B"}},
    )
    assert r.status_code == 403
    r = await client.patch(f"/api/apps/{app_id}", headers=bob_auth, json={"name": "B"})
    assert r.status_code == 403

    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_data"}]},
    )
    assert r.status_code == 200
    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=bob_auth,
        json={"row": {"title": "B"}},
    )
    assert r.status_code == 200
    assert r.json()["row_index"] == 0
    r = await client.get(f"/api/apps/{app_id}/rows", headers=bob_auth)
    assert r.json()["total"] == 1

    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": []},
    )
    assert r.json()["shares"] == []
    r = await client.get(f"/api/apps/{app_id}", headers=bob_auth)
    assert r.status_code == 403


async def test_edit_structure_collaborator_cannot_manage_or_publish(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """edit_structure is schema/data editing, not co-ownership."""
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_structure"}]},
    )
    assert r.status_code == 200

    # The collaborator can still edit data.
    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=bob_auth,
        json={"row": {"title": "B"}},
    )
    assert r.status_code == 200

    attempts: list[tuple[str, str, dict[str, object] | None]] = [
        ("GET", f"/api/apps/{app_id}/shares", None),
        ("PUT", f"/api/apps/{app_id}/shares", {"shares": []}),
        ("PUT", f"/api/apps/{app_id}/capabilities", {"capabilities": []}),
        ("PATCH", f"/api/apps/{app_id}/tables/main", {"public_visible": False}),
        ("POST", f"/api/apps/{app_id}/publication", {"ttl_days": 0}),
        ("DELETE", f"/api/apps/{app_id}/publication", None),
        ("POST", f"/api/apps/{app_id}/transfer", {"agent_id": agent_id}),
        ("DELETE", f"/api/apps/{app_id}", None),
    ]
    for method, path, payload in attempts:
        r = await client.request(
            method,
            path,
            headers=bob_auth,
            json=payload,
        )
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["code"] == "APP_FORBIDDEN"

    # The app still exists and remains usable by the owner.
    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert r.status_code == 200


async def test_multi_table_rows_endpoints(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    await service.create_table(
        service.get(app_id),
        table_key="orders",
        label="Orders",
        columns=[{"name": "item", "type": "string", "required": True}],
    )

    r = await client.get(f"/api/apps/{app_id}/tables", headers=alice_auth)
    assert r.status_code == 200
    tables = r.json()["tables"]
    assert [item["table_key"] for item in tables] == ["main", "orders"]
    assert tables[1]["label"] == "Orders"
    assert tables[1]["is_main"] is False
    assert [col["name"] for col in tables[1]["columns"]] == ["item"]

    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert [item["table_key"] for item in r.json()["tables"]] == ["main", "orders"]

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"item": "cup"}, "table": "orders"},
    )
    assert r.status_code == 200
    assert r.json()["table"] == "orders"

    r = await client.get(f"/api/apps/{app_id}/rows?table=orders", headers=alice_auth)
    body = r.json()
    assert body["table"] == "orders"
    assert body["total"] == 1
    assert body["rows"] == [{"item": "cup"}]

    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["total"] == 0

    # The card counter aggregates every table.
    r = await client.get("/api/apps", headers=alice_auth)
    assert r.json()["apps"][0]["row_count"] == 1

    r = await client.get(f"/api/apps/{app_id}/rows?table=nope", headers=alice_auth)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_SCHEMA_INVALID"


async def test_data_version_endpoint_tracks_writes(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.get(f"/api/apps/{app_id}/data-version", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == [{"table_key": "main", "data_version": 0, "row_count": 0}]

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "A", "status": "new"}},
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/data-version", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["tables"][0]["data_version"] >= 1
    assert r.json()["tables"][0]["row_count"] == 1

    service = _service(srv)
    await service.create_table(
        service.get(app_id),
        table_key="orders",
        columns=[{"name": "item", "type": "string"}],
    )
    r = await client.get(f"/api/apps/{app_id}/data-version", headers=alice_auth)
    assert r.status_code == 200, r.text
    versions = {item["table_key"]: item for item in r.json()["tables"]}
    assert set(versions) == {"main", "orders"}
    assert versions["orders"]["data_version"] == 0

    # View permission is enough; outsiders see nothing.
    r = await client.get(f"/api/apps/{app_id}/data-version", headers=bob_auth)
    assert r.status_code == 403
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "view"}]},
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/data-version", headers=bob_auth)
    assert r.status_code == 200, r.text


async def test_share_validation(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    for shares in (
        [{"username": "nobody", "permission": "view"}],
        [{"username": "bob", "permission": "view"}, {"username": "bob", "permission": "view"}],
        [{"username": "alice", "permission": "view"}],
    ):
        r = await client.put(
            f"/api/apps/{app_id}/shares",
            headers=alice_auth,
            json={"shares": shares},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "APP_SHARE_INVALID"


async def test_unknown_app_returns_404(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, _srv, alice_auth, _bob_auth, _agent_id = env_alice_bob_agent
    for app_id in ("app_nope", "not-an-app"):
        r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "APP_NOT_FOUND"


async def test_assistant_history_acl_and_guards(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    # History starts empty and is per-user, oldest first.
    r = await client.get(f"/api/apps/{app_id}/assistant/history", headers=alice_auth)
    assert r.status_code == 200
    assert r.json() == {"messages": []}

    # Non-viewers cannot reach the assistant endpoints.
    r = await client.get(f"/api/apps/{app_id}/assistant/history", headers=bob_auth)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_FORBIDDEN"
    r = await client.post(
        f"/api/apps/{app_id}/assistant", headers=bob_auth, json={"question": "hi"}
    )
    assert r.status_code == 403

    # Empty (whitespace-only) questions are rejected with 400.
    r = await client.post(
        f"/api/apps/{app_id}/assistant", headers=alice_auth, json={"question": "   "}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_QUESTION_INVALID"

    # Asking streams the scripted answer as SSE frames and records history.
    assert srv.services is not None
    alice = srv.services.user_repo.get_by_username("alice")
    assert alice is not None
    agent = srv.app_runtime.agent_registry.get_agent(agent_id)
    agent._assistant_llm.script = [
        AIMessage(
            content="There are 3 rows.",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
    ]
    r = await client.post(
        f"/api/apps/{app_id}/assistant", headers=alice_auth, json={"question": "how many rows?"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(block.split("data: ", 1)[1]) for block in r.text.strip().split("\n\n") if block
    ]
    assert [event["type"] for event in events] == ["token", "done"]
    assert "".join(event.get("text", "") for event in events) == "There are 3 rows."
    assert events[-1]["input_tokens"] == 10

    r = await client.get(f"/api/apps/{app_id}/assistant/history", headers=alice_auth)
    assert r.status_code == 200
    messages = r.json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "how many rows?"),
        ("assistant", "There are 3 rows."),
    ]

    # Seeded history is returned oldest-first for its owner only.
    repo = srv.services.light_app_repo
    repo.add_assistant_message(app_id, alice.id, role="user", content="how many?")
    repo.add_assistant_message(app_id, alice.id, role="assistant", content="3 rows")
    r = await client.get(f"/api/apps/{app_id}/assistant/history", headers=alice_auth)
    assert r.status_code == 200
    messages = r.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in messages] == [
        "how many rows?",
        "There are 3 rows.",
        "how many?",
        "3 rows",
    ]
    assert all(m["created_at"] > 0 for m in messages)


async def test_capabilities_api_and_legacy_default(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert r.status_code == 200, r.text
    granted = set(r.json()["capabilities"])
    assert app_capabilities.CAP_DATA_READ in granted
    assert app_capabilities.CAP_AI_GENERATE not in granted

    r = await client.put(
        f"/api/apps/{app_id}/capabilities",
        headers=alice_auth,
        json={
            "capabilities": [
                app_capabilities.CAP_DATA_READ,
                app_capabilities.CAP_AI_GENERATE,
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert set(r.json()["capabilities"]) == {
        app_capabilities.CAP_DATA_READ,
        app_capabilities.CAP_AI_GENERATE,
    }

    r = await client.put(
        f"/api/apps/{app_id}/capabilities",
        headers=bob_auth,
        json={"capabilities": []},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_FORBIDDEN"

    r = await client.put(
        f"/api/apps/{app_id}/capabilities",
        headers=alice_auth,
        json={"capabilities": ["not.a.capability"]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_CAPABILITY_INVALID"

    # A row from before the manifest existed keeps every R1 capability, but not
    # capabilities added later.
    assert srv.services is not None
    srv.services.light_app_repo.set_capabilities(app_id, "")
    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert set(r.json()["capabilities"]) == set(app_capabilities.LEGACY_CAPABILITIES)


async def test_generate_acl_and_output(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    # New apps do not grant the AI-generation capability by default.
    r = await client.post(f"/api/apps/{app_id}/generate", headers=alice_auth, json={"prompt": "hi"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_CAPABILITY_DENIED"

    service = _service(srv)
    service.set_capabilities(
        service.get(app_id),
        app_capabilities.ALL_CAPABILITIES,
    )

    # Generation follows the app's view permission.
    r = await client.post(f"/api/apps/{app_id}/generate", headers=bob_auth, json={"prompt": "hi"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_FORBIDDEN"

    # Whitespace-only prompts are rejected before the model is called.
    r = await client.post(
        f"/api/apps/{app_id}/generate", headers=alice_auth, json={"prompt": "   "}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_PROMPT_INVALID"

    # A valid prompt returns one non-streaming JSON body and bills usage.
    assert srv.app_runtime is not None
    agent = srv.app_runtime.agent_registry.get_agent(agent_id)
    agent._assistant_llm.script = [
        AIMessage(
            content="A concise answer.",
            usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        )
    ]
    r = await client.post(
        f"/api/apps/{app_id}/generate",
        headers=alice_auth,
        json={"prompt": "summarize", "system": "Be terse"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "A concise answer."
    assert body["model"] == "fake-model"
    assert body["input_tokens"] == 10
    assert body["output_tokens"] == 4


async def _upload_import_file(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    app_id: str,
    content: bytes = b"title,qty,status\ncup,3,new\npen,1,done\n",
    filename: str = "items.csv",
) -> str:
    r = await client.post(
        f"/api/apps/{app_id}/upload",
        headers=auth,
        files={"file": (filename, content, "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()["path"]


async def test_upload_and_import_flow(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    path = await _upload_import_file(client, alice_auth, app_id)
    assert path.startswith("inbound/imports/")

    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={"kind": "upload", "path": path, "mode": "append"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["table"], body["imported"], body["skipped"]) == ("main", 2, 0)
    assert body["errors"] == []

    # Appending the same file again duplicates rows (no dedup for manual imports).
    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={"kind": "upload", "path": path, "mode": "append"},
    )
    assert r.json()["imported"] == 2

    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["total"] == 4

    # Replace wipes the previous rows and honors validation skips.
    path2 = await _upload_import_file(
        client,
        alice_auth,
        app_id,
        content=b"title,status\nbox,done\nbad,pending\n",
    )
    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={"kind": "upload", "path": path2, "mode": "replace"},
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["imported"], body["skipped"]) == (1, 1)
    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["total"] == 1

    # Guards: unknown table, hidden path, unsupported mode payload.
    r = await client.post(
        f"/api/apps/{app_id}/tables/nope/import",
        headers=alice_auth,
        json={"kind": "upload", "path": path},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_SCHEMA_INVALID"
    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={"kind": "workspace_file", "path": ".octop/auth/tokens.json"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_ROW_INVALID"


async def test_import_permissions(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    path = await _upload_import_file(client, alice_auth, app_id)
    import_url = f"/api/apps/{app_id}/tables/main/import"

    # No share: everything 403.
    r = await client.post(import_url, headers=bob_auth, json={"kind": "upload", "path": path})
    assert r.status_code == 403

    # view: cannot upload or import.
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "view"}]},
    )
    assert r.status_code == 200
    r = await client.post(
        f"/api/apps/{app_id}/upload",
        headers=bob_auth,
        files={"file": ("a.csv", b"title\nx\n", "text/csv")},
    )
    assert r.status_code == 403
    r = await client.post(import_url, headers=bob_auth, json={"kind": "upload", "path": path})
    assert r.status_code == 403

    # edit_data: can upload and append-import, but not replace or workspace_file.
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_data"}]},
    )
    assert r.status_code == 200
    bob_path = await _upload_import_file(client, bob_auth, app_id)
    r = await client.post(import_url, headers=bob_auth, json={"kind": "upload", "path": bob_path})
    assert r.status_code == 200
    r = await client.post(
        import_url,
        headers=bob_auth,
        json={"kind": "upload", "path": bob_path, "mode": "replace"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_FORBIDDEN"
    r = await client.post(
        import_url,
        headers=bob_auth,
        json={"kind": "workspace_file", "path": "inbound/imports/x.csv"},
    )
    assert r.status_code == 403


async def test_sources_lifecycle_and_permissions(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.connectors.service import ConnectorService
    from octop.infra.db.repos.secrets import SecretRepo
    from octop.infra.db.repos.settings import SettingsRepo

    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    assert srv.services is not None

    # A gateway connector instance owned by alice (run uses its stored creds).
    repo = srv.services.repos.connector_repo
    repo.create(
        instance_id="conn1",
        user_id=srv.services.user_repo.get_by_username("alice").id,  # type: ignore[union-attr]
        kind="baidu-map",
        display_name="Map",
        mcp_server_name="baidu_map__conn1",
    )
    ConnectorService(
        repo=repo,
        secret_repo=SecretRepo(srv.services.db),
        settings_repo=SettingsRepo(srv.services.db),
        config=srv.services.config,
    ).encrypt_and_store(instance_id="conn1", payload={"api_key": "map-key"})
    monkeypatch.setattr(
        "octop.infra.apps.sources.mcp_tools_for_kind",
        lambda kind: [
            {"name": "map_search", "description": "Search", "inputSchema": {"type": "object"}},
        ],
    )

    # Empty list initially.
    r = await client.get(f"/api/apps/{app_id}/sources", headers=alice_auth)
    assert r.status_code == 200
    assert r.json()["sources"] == []

    # Tool listing for the binding dialog.
    r = await client.get(
        f"/api/apps/{app_id}/source-tools", headers=alice_auth, params={"instance_id": "conn1"}
    )
    assert r.status_code == 200, r.text
    tools = r.json()["tools"]
    assert [t["name"] for t in tools] == ["map_search"]
    assert tools[0]["read_only"] is True
    assert tools[0]["input_schema"] == {"type": "object"}

    # Bind an external source to main.
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_1",
        headers=alice_auth,
        json={
            "table_key": "main",
            "kind": "external",
            "mode": "append",
            "params": {
                "instance_id": "conn1",
                "tool": "map_search",
                "args": {"q": "lake"},
                "result_path": "",
            },
            "mapping": {"name": "title"},
        },
    )
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["table_key"] == "main" and view["kind"] == "external"
    assert view["last_status"] is None

    # Second binding on the same table clashes.
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_2",
        headers=alice_auth,
        json={
            "table_key": "main",
            "kind": "external",
            "params": {"instance_id": "conn1", "tool": "map_search"},
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_SOURCE_INVALID"

    # Unknown connector / unknown tool rejected at bind time.
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_3",
        headers=alice_auth,
        json={
            "table_key": "main",
            "kind": "external",
            "params": {"instance_id": "missing", "tool": "map_search"},
        },
    )
    assert r.status_code == 400
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_3",
        headers=alice_auth,
        json={
            "table_key": "main",
            "kind": "external",
            "params": {"instance_id": "conn1", "tool": "no_such"},
        },
    )
    assert r.status_code == 400

    # Run the external source: the fake gateway returns one matching row.
    def fake_call(kind: str, creds: dict, name: str, args: dict) -> str:
        return '[{"name": "West Lake"}]'

    monkeypatch.setattr("octop.infra.apps.sources.call_gateway_tool", fake_call)
    r = await client.post(f"/api/apps/{app_id}/sources/src_1/run", headers=alice_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["table"], body["imported"], body["skipped"]) == ("main", 1, 0)

    r = await client.get(f"/api/apps/{app_id}/sources", headers=alice_auth)
    view = r.json()["sources"][0]
    assert view["last_status"] == "ok" and view["last_row_count"] == 1

    # ACL: bob without a share cannot see or touch sources.
    r = await client.get(f"/api/apps/{app_id}/sources", headers=bob_auth)
    assert r.status_code == 403
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_9",
        headers=bob_auth,
        json={
            "table_key": "main",
            "kind": "workspace_file",
            "params": {"path": "a.csv"},
        },
    )
    assert r.status_code == 403

    # view share: can list, cannot bind or run.
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "view"}]},
    )
    assert r.status_code == 200
    r = await client.get(f"/api/apps/{app_id}/sources", headers=bob_auth)
    assert r.status_code == 200
    r = await client.post(f"/api/apps/{app_id}/sources/src_1/run", headers=bob_auth)
    assert r.status_code == 403

    # edit_data share: can run, still cannot bind (needs edit_structure).
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_data"}]},
    )
    assert r.status_code == 200
    r = await client.post(f"/api/apps/{app_id}/sources/src_1/run", headers=bob_auth)
    assert r.status_code == 200
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_9",
        headers=bob_auth,
        json={
            "table_key": "main",
            "kind": "workspace_file",
            "params": {"path": "a.csv"},
        },
    )
    assert r.status_code == 403

    # Delete the binding; runs then 404.
    r = await client.delete(f"/api/apps/{app_id}/sources/src_1", headers=alice_auth)
    assert r.status_code == 204
    r = await client.post(f"/api/apps/{app_id}/sources/src_1/run", headers=alice_auth)
    assert r.status_code == 404


async def test_schedule_api_lifecycle(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    assert srv.app_runtime is not None
    workspace = srv.app_runtime.agent_registry.workspace_for_agent(agent_id)
    assert workspace is not None
    await workspace.awrite_text("inbound/imports/s.csv", "title,status\nmug,new\n")

    # A scheduled source immediately mirrors into a schedule row.
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_1",
        headers=alice_auth,
        json={
            "table_key": "main",
            "kind": "workspace_file",
            "mode": "append",
            "schedule_seconds": 60,
            "params": {"path": "inbound/imports/s.csv"},
        },
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/schedules", headers=alice_auth)
    assert r.status_code == 200, r.text
    schedules = r.json()["schedules"]
    assert [(item["schedule_id"], item["action"]) for item in schedules] == [
        ("sch_src_src_1", "sync_source")
    ]
    sync_id = schedules[0]["schedule_id"]

    # Disabling the schedule turns the binding back into manual-only.
    r = await client.patch(
        f"/api/apps/{app_id}/schedules/{sync_id}",
        headers=alice_auth,
        json={"interval_seconds": 120, "enabled": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    r = await client.get(f"/api/apps/{app_id}/sources", headers=alice_auth)
    assert r.json()["sources"][0]["schedule_seconds"] == 0
    r = await client.patch(
        f"/api/apps/{app_id}/schedules/{sync_id}",
        headers=alice_auth,
        json={"interval_seconds": 120, "enabled": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["interval_seconds"] == 120

    # Snapshot refresh requires an existing public link.
    r = await client.post(
        f"/api/apps/{app_id}/schedules",
        headers=alice_auth,
        json={"action": "snapshot_publication", "interval_seconds": 300},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_SOURCE_INVALID"

    await _service(srv).write_html(_service(srv).get(app_id), "<h1>v1</h1>")
    r = await client.post(f"/api/apps/{app_id}/publication/publish", headers=alice_auth)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/apps/{app_id}/schedules",
        headers=alice_auth,
        json={"action": "snapshot_publication", "interval_seconds": 300},
    )
    assert r.status_code == 200, r.text
    snapshot = r.json()
    assert snapshot["action"] == "snapshot_publication"
    # POST is idempotent per action: repeating it updates the interval.
    r = await client.post(
        f"/api/apps/{app_id}/schedules",
        headers=alice_auth,
        json={"action": "snapshot_publication", "interval_seconds": 3600},
    )
    assert r.status_code == 200, r.text
    assert r.json()["schedule_id"] == snapshot["schedule_id"]
    assert r.json()["interval_seconds"] == 3600
    r = await client.get(f"/api/apps/{app_id}/schedules", headers=alice_auth)
    assert len(r.json()["schedules"]) == 2

    # Schedules follow app ACLs: bob needs a viewer share.
    r = await client.get(f"/api/apps/{app_id}/schedules", headers=bob_auth)
    assert r.status_code == 403
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "view"}]},
    )
    assert r.status_code == 200
    r = await client.get(f"/api/apps/{app_id}/schedules", headers=bob_auth)
    assert r.status_code == 200
    r = await client.post(
        f"/api/apps/{app_id}/schedules",
        headers=bob_auth,
        json={"action": "snapshot_publication", "interval_seconds": 300},
    )
    assert r.status_code == 403

    r = await client.delete(
        f"/api/apps/{app_id}/schedules/{snapshot['schedule_id']}", headers=alice_auth
    )
    assert r.status_code == 204
    r = await client.delete(f"/api/apps/{app_id}/schedules/{sync_id}", headers=alice_auth)
    assert r.status_code == 204
    r = await client.get(f"/api/apps/{app_id}/schedules", headers=alice_auth)
    assert r.json()["schedules"] == []
    r = await client.get(f"/api/apps/{app_id}/sources", headers=alice_auth)
    assert r.json()["sources"][0]["schedule_seconds"] == 0


async def test_snapshot_schedule_requires_owner(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    await service.write_html(service.get(app_id), "<h1>v1</h1>")
    r = await client.post(f"/api/apps/{app_id}/publication/publish", headers=alice_auth)
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_structure"}]},
    )
    assert r.status_code == 200, r.text

    # edit_structure still covers data-source sync schedules.
    r = await client.put(
        f"/api/apps/{app_id}/sources/src_1",
        headers=alice_auth,
        json={
            "table_key": "main",
            "kind": "workspace_file",
            "mode": "append",
            "schedule_seconds": 60,
            "params": {"path": "inbound/imports/s.csv"},
        },
    )
    assert r.status_code == 200, r.text
    r = await client.patch(
        f"/api/apps/{app_id}/schedules/sch_src_src_1",
        headers=bob_auth,
        json={"interval_seconds": 120, "enabled": True},
    )
    assert r.status_code == 200, r.text

    # Snapshot refresh republishes the current HTML/schema, so it follows the
    # owner/admin gate of publication.publish.
    r = await client.post(
        f"/api/apps/{app_id}/schedules",
        headers=bob_auth,
        json={"action": "snapshot_publication", "interval_seconds": 3600},
    )
    assert r.status_code == 403, r.text

    r = await client.post(
        f"/api/apps/{app_id}/schedules",
        headers=alice_auth,
        json={"action": "snapshot_publication", "interval_seconds": 3600},
    )
    assert r.status_code == 200, r.text
    schedule_id = r.json()["schedule_id"]

    r = await client.patch(
        f"/api/apps/{app_id}/schedules/{schedule_id}",
        headers=bob_auth,
        json={"interval_seconds": 7200, "enabled": False},
    )
    assert r.status_code == 403, r.text
    r = await client.delete(f"/api/apps/{app_id}/schedules/{schedule_id}", headers=bob_auth)
    assert r.status_code == 403, r.text

    assert srv.services is not None
    saved = srv.services.audit_repo.query(action="light_app.schedule.save", limit=10)
    assert saved
    saved_payload = json.loads(saved[0].payload or "{}")
    assert saved_payload["schedule_id"] == schedule_id
    assert saved_payload["action"] == "snapshot_publication"

    updated = srv.services.audit_repo.query(action="light_app.schedule.update", limit=10)
    assert updated
    assert json.loads(updated[0].payload or "{}")["action"] == "sync_source"

    r = await client.delete(f"/api/apps/{app_id}/schedules/{schedule_id}", headers=alice_auth)
    assert r.status_code == 204, r.text
    deleted = srv.services.audit_repo.query(action="light_app.schedule.delete", limit=10)
    assert deleted
    assert json.loads(deleted[0].payload or "{}")["schedule_id"] == schedule_id


async def test_import_dry_run_and_header_row(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    path = await _upload_import_file(
        client,
        alice_auth,
        app_id,
        content="2026 年 9 月工资表\ntitle,qty,status\ncup,3,new\n".encode(),
    )

    # A dry run counts the rows without writing them. The header hint points
    # at the real header line; without it the title line is taken as the
    # header and every row is rejected.
    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={
            "kind": "upload",
            "path": path,
            "mode": "append",
            "header_row": 1,
            "dry_run": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["would_import"] == 1
    assert body["imported"] == 0

    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["total"] == 0

    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={
            "kind": "upload",
            "path": path,
            "mode": "append",
            "header_row": 1,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1

    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["rows"][0]["title"] == "cup"


async def test_transfer_moves_the_app_between_agents(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.post(
        "/api/agents",
        headers=alice_auth,
        json={"name": "second-bot", "config": {"providers": ["openai"]}},
    )
    assert r.status_code == 201, r.text
    other_agent_id = r.json()["agent_id"]

    r = await client.post(
        f"/api/apps/{app_id}/transfer",
        headers=alice_auth,
        json={"agent_id": other_agent_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent_id"] == other_agent_id

    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert r.status_code == 200
    assert r.json()["agent_id"] == other_agent_id


async def test_agent_delete_is_refused_while_it_owns_apps(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    await _create_app(srv, agent_id, "alice")

    r = await client.delete(f"/api/agents/{agent_id}", headers=alice_auth)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "AGENT_HAS_APPS"

    # The agent is still there.
    r = await client.get(f"/api/agents/{agent_id}", headers=alice_auth)
    assert r.status_code == 200


async def test_asset_upload_and_fetch(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16

    r = await client.post(
        f"/api/apps/{app_id}/assets",
        headers=alice_auth,
        files={"file": ("cover.png", png, "image/png")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "cover.png"

    r = await client.get(f"/api/apps/{app_id}/assets", headers=alice_auth)
    assert [item["name"] for item in r.json()["assets"]] == ["cover.png"]

    r = await client.get(f"/api/apps/{app_id}/assets/cover.png", headers=alice_auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == png

    # A non-collaborator cannot fetch the asset.
    r = await client.get(f"/api/apps/{app_id}/assets/cover.png", headers=bob_auth)
    assert r.status_code == 403


async def test_asset_upload_rejects_non_images(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.post(
        f"/api/apps/{app_id}/assets",
        headers=alice_auth,
        files={"file": ("cover.svg", b"<svg></svg>", "image/svg+xml")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_SOURCE_INVALID"


async def test_row_edit_export_and_idempotent_append(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "A", "status": "new"}, "client_request_id": "req-1"},
    )
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["row_id"]
    assert created["version"] == 1

    # Retrying the same id is a no-op.
    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "A", "status": "new"}, "client_request_id": "req-1"},
    )
    assert r.json()["row_id"] == created["row_id"]
    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["total"] == 1

    meta = r.json()["row_meta"][0]
    assert meta["row_id"] == created["row_id"]
    assert meta["version"] == 1

    r = await client.patch(
        f"/api/apps/{app_id}/rows/{created['row_id']}",
        headers=alice_auth,
        json={"row": {"title": "B", "status": "done"}, "expected_version": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert r.json()["row"]["title"] == "B"

    # A stale version is a conflict, not a silent overwrite.
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{created['row_id']}",
        headers=alice_auth,
        json={"row": {"title": "C", "status": "new"}, "expected_version": 1},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "APP_CONFLICT"

    r = await client.get(f"/api/apps/{app_id}/export", headers=alice_auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text == "title,status\nB,done\n"

    r = await client.delete(
        f"/api/apps/{app_id}/rows/{created['row_id']}?expected_version=2",
        headers=alice_auth,
    )
    assert r.status_code == 200
    assert r.json()["deleted"] == created["row_id"]
    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["total"] == 0


async def test_alter_schema_endpoint(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "A", "status": "new"}},
    )

    r = await client.patch(
        f"/api/apps/{app_id}/schema",
        headers=alice_auth,
        json={"renames": {"title": "name"}},
    )
    assert r.status_code == 200, r.text
    assert [column["name"] for column in r.json()["columns"]] == ["name", "status"]

    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["rows"] == [{"name": "A", "status": "new"}]

    # Dropping a column removes its data.
    r = await client.patch(
        f"/api/apps/{app_id}/schema",
        headers=alice_auth,
        json={"drops": ["status"]},
    )
    assert r.status_code == 200, r.text
    assert [column["name"] for column in r.json()["columns"]] == ["name"]


async def test_import_upsert_key_over_http(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    path = await _upload_import_file(client, alice_auth, app_id)

    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={"kind": "upload", "path": path, "upsert_key": ["title"]},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["imported"], r.json()["updated"]) == (2, 0)

    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={"kind": "upload", "path": path, "upsert_key": ["title"]},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["imported"], r.json()["updated"]) == (0, 2)

    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.json()["total"] == 2


async def test_import_multiple_workspace_files(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    first = await _upload_import_file(
        client,
        alice_auth,
        app_id,
        content=b"title,status\nA,new\n",
        filename="sep.csv",
    )
    second = await _upload_import_file(
        client,
        alice_auth,
        app_id,
        content=b"title,status\nB,done\n",
        filename="oct.csv",
    )

    r = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=alice_auth,
        json={"kind": "workspace_file", "paths": [first, second]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2


async def test_create_app_from_expert_template(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    assert srv.app_runtime is not None
    workspace = srv.app_runtime.agent_registry.workspace_for_agent(agent_id)
    assert workspace is not None
    await workspace.awrite_text(
        "app_templates/reading/schema.json",
        json.dumps(
            {
                "version": 1,
                "columns": [{"name": "title", "label": "书名", "type": "text", "required": True}],
            }
        ),
    )
    await workspace.awrite_text(
        "app_templates/reading/app.html",
        "<!doctype html><html><body><h1>Books</h1></body></html>",
    )

    r = await client.get(f"/api/apps/templates?agent_id={agent_id}", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["templates"][0]["template"] == "reading"

    r = await client.post(
        "/api/apps/from-template",
        headers=alice_auth,
        json={"agent_id": agent_id, "template": "reading", "name": "My reading"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "My reading"
    assert body["row_count"] == 0
    assert body["has_html"] is True
    assert [column["name"] for column in body["columns"]] == ["title"]


async def test_csp_violation_is_audited_without_its_payload(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """A blocked URL may carry exfiltrated data; the audit keeps only its origin/path."""
    client, srv, alice_auth, bob_auth, _agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, _agent_id, "alice")
    body = {
        "kind": "csp_violation",
        "directive": "connect-src",
        "blocked_uri": "https://evil.example/collect?secret=ROW-1#frag",
        "disposition": "enforce",
    }

    r = await client.post(f"/api/apps/{app_id}/security-events", headers=alice_auth, json=body)
    assert r.status_code == 204, r.text

    assert srv.services is not None
    entries = srv.services.audit_repo.query(action="light_app.csp_violation", limit=5)
    assert entries, "no csp violation audit entry written"
    assert entries[0].target == app_id
    payload = json.loads(entries[0].payload or "{}")
    assert payload["directive"] == "connect-src"
    assert payload["blocked_uri"] == "https://evil.example/collect"
    assert "ROW-1" not in (entries[0].payload or "")

    # A failed freeze/integrity check is a separate event kind.
    r = await client.post(
        f"/api/apps/{app_id}/security-events",
        headers=alice_auth,
        json={
            "kind": "sandbox_integrity",
            "directive": "sandbox-integrity",
            "blocked_uri": "",
            "disposition": "enforce",
        },
    )
    assert r.status_code == 204, r.text
    integrity = srv.services.audit_repo.query(action="light_app.sandbox_integrity", limit=5)
    assert integrity, "no sandbox integrity audit entry written"
    assert integrity[0].target == app_id

    # Reporting is tied to the same view permission as the app.
    r = await client.post(f"/api/apps/{app_id}/security-events", headers=bob_auth, json=body)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_FORBIDDEN"


async def test_public_link_is_read_only_and_revocable(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    row = service.get(app_id)
    await service.append_row(row, {"title": "A", "status": "new"})
    await service.write_html(row, "<!doctype html><html><body><h1>Public</h1></body></html>")

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token
    assert r.json()["url"] == f"/p/{token}"

    # No JWT on the public endpoints.
    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Tracker"
    assert "Public" in r.json()["html"]

    r = await client.get(f"/api/public/apps/{token}/rows")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"title": "A", "status": "new"}]

    r = await client.get(f"/p/{token}")
    assert r.status_code == 200
    assert "octopApp" in r.text

    # Revoking takes effect immediately.
    r = await client.delete(f"/api/apps/{app_id}/publication", headers=alice_auth)
    assert r.status_code == 200
    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 404


async def test_public_rows_keep_snapshot_pii_after_live_flag_removed(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """Clearing a PII flag must not declassify an existing public link."""
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(
        srv,
        agent_id,
        "alice",
        columns=[
            {"name": "title", "type": "string", "required": True},
            {"name": "home", "type": "string", "pii": "address"},
        ],
    )
    service = _service(srv)
    row = service.get(app_id)
    await service.append_row(row, {"title": "A", "home": "HQ"})
    await service.write_html(row, "<!doctype html><html><body>PII</body></html>")

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = await client.get(f"/api/public/apps/{token}/rows")
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["home"] == "••••"
    assert "home" in r.json()["pii_columns"]

    # Clear the live PII flag without republishing: the snapshot still wins.
    await service.alter_schema(service.get(app_id), pii={"home": None})
    r = await client.get(f"/api/public/apps/{token}/rows")
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["home"] == "••••"
    assert "home" in r.json()["pii_columns"]

    # Republishing after declassification is the explicit opt-out.
    r = await client.post(
        f"/api/apps/{app_id}/publication/publish",
        headers=alice_auth,
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/public/apps/{token}/rows")
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["home"] == "HQ"
    assert "home" not in r.json()["pii_columns"]


async def test_public_page_runs_the_app_inside_the_navigation_sandbox(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """The public host page uses the same wrapper as the dashboard.

    A direct ``srcdoc`` would let the app navigate its own frame away and take
    the bridge with it; the wrapper's ``frame-src 'none'`` is what prevents it.
    """
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    row = service.get(app_id)
    await service.write_html(
        row,
        "<!doctype html><html><body><script>window.x=1</script></body></html>",
    )

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = await client.get(f"/p/{token}")
    assert r.status_code == 200
    assert 'id="wrapper"' in r.text
    # The wrapper denies every navigation of the app frame.
    assert "frame-src 'none'" in r.text
    # The app's script is embedded as a JSON string, so its closing tag is
    # escaped and cannot terminate the host page's own script. The payload is
    # JSON-encoded twice (app document → wrapper → host page), hence two
    # backslashes in the host source.
    assert "window.x=1</script>" not in r.text
    assert r"window.x=1<\\/script>" in r.text


async def test_publication_url_uses_configured_public_base_url(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """The dashboard copies this URL verbatim — it must carry the server's public origin."""
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    assert srv.config is not None
    srv.config = replace(srv.config, public_base_url="https://octop.example.test:8088")

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["url"] == f"https://octop.example.test:8088/p/{token}"


async def test_backup_download_and_restore_api(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    row = service.get(app_id)
    await service.append_row(row, {"title": "A", "status": "new"})
    await service.write_html(row, "<!doctype html><html><body><h1>Backup</h1></body></html>")

    r = await client.get(f"/api/apps/{app_id}/backup", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert r.content.startswith(b"PK")

    r = await client.post(
        "/api/apps/restore",
        params={"agent_id": agent_id},
        headers=alice_auth,
        files={"file": ("backup.zip", r.content, "application/zip")},
    )
    assert r.status_code == 201, r.text
    restored = r.json()
    assert restored["app_id"] != app_id
    assert restored["agent_id"] == agent_id
    assert restored["name"] == "Tracker"
    assert restored["row_count"] == 1
    assert restored["has_html"] is True

    r = await client.get(f"/api/apps/{restored['app_id']}/rows", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"title": "A", "status": "new"}]

    r = await client.post(
        "/api/apps/restore",
        params={"agent_id": agent_id},
        headers=alice_auth,
        files={"file": ("backup.zip", b"not a zip", "application/zip")},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "APP_SOURCE_INVALID"


async def test_pii_is_masked_for_viewers_and_public_links(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    assert srv.services is not None
    owner = srv.services.user_repo.get_by_username("alice")
    assert owner is not None
    service = _service(srv)
    row = await service.create(
        agent_id=agent_id,
        owner_user_id=owner.id,
        name="Contacts",
        columns=[
            {"name": "title", "type": "string"},
            {"name": "phone", "type": "string", "pii": "phone"},
        ],
    )
    await service.append_row(row, {"title": "A", "phone": "13800138000"})
    app_id = row.app_id

    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "view"}]},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/apps/{app_id}/rows", headers=bob_auth)
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"title": "A", "phone": "••••"}]
    assert r.json()["pii_columns"] == ["phone"]

    r = await client.get(f"/api/apps/{app_id}/export", headers=bob_auth)
    assert r.status_code == 200, r.text
    assert "13800138000" not in r.text
    assert "••••" in r.text

    # Editors still see the real value.
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_data"}]},
    )
    assert r.status_code == 200
    r = await client.get(f"/api/apps/{app_id}/rows", headers=bob_auth)
    assert r.json()["rows"] == [{"title": "A", "phone": "13800138000"}]
    assert r.json()["pii_columns"] == []

    # Only structure editors may download an unmasked backup.
    r = await client.get(f"/api/apps/{app_id}/backup", headers=bob_auth)
    assert r.status_code == 403
    r = await client.get(f"/api/apps/{app_id}/backup", headers=alice_auth)
    assert r.status_code == 200
    assert r.content.startswith(b"PK")

    # Public links are always read-only and masked.
    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    token = r.json()["token"]
    r = await client.get(f"/api/public/apps/{token}/rows")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"title": "A", "phone": "••••"}]
    assert "13800138000" not in r.text


async def test_system_templates_are_listed_and_creatable(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, _srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent

    r = await client.get(f"/api/apps/templates?agent_id={agent_id}&source=all", headers=alice_auth)
    assert r.status_code == 200
    templates = {item["template_id"]: item for item in r.json()["templates"]}
    assert templates["customer-followup"]["source"] == "system"
    assert templates["customer-followup"]["scope"] == "global"
    assert templates["customer-followup"]["name"]

    r = await client.post(
        "/api/apps/from-template",
        headers=alice_auth,
        json={
            "agent_id": agent_id,
            "template": "customer-followup",
            "source": "system",
            "name": "我的客户",
        },
    )
    assert r.status_code == 201
    detail = r.json()
    assert detail["name"] == "我的客户"
    assert "customer" in [column["name"] for column in detail["columns"]]
    assert detail["has_html"] is True


async def test_quick_inspect_and_create_flow(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, _srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    csv = "日期,区域,金额\n2026-01-01,华东,120\n2026-01-02,华南,80\n2026-01-03,华东,50\n".encode()

    r = await client.post(
        "/api/apps/quick/inspect",
        headers=alice_auth,
        data={"agent_id": agent_id},
        files={"file": ("sales.csv", csv, "text/csv")},
    )
    assert r.status_code == 200, r.text
    inspected = r.json()
    assert inspected["sheet"]
    sheet = inspected["report"]["sheets"][0]
    assert len(sheet["columns"]) == 3

    columns = [
        {
            "name": column["name_suggestion"],
            "label": column["source"],
            "type": "string" if column["type"] == "enum" else column["type"],
            "required": False,
        }
        for column in sheet["columns"]
    ]
    mapping = {column["source"]: column["name_suggestion"] for column in sheet["columns"]}

    r = await client.post(
        "/api/apps/quick/create",
        headers=alice_auth,
        json={
            "agent_id": agent_id,
            "path": inspected["upload_path"],
            "columns": columns,
            "view": "dashboard",
            "name": "销售看板",
            "sheet": inspected["sheet"],
            "mapping": mapping,
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["imported"] == 3
    assert created["app"]["has_html"] is True
    app_id = created["app"]["app_id"]

    r = await client.get(f"/api/apps/{app_id}/rows?limit=10&offset=0", headers=alice_auth)
    assert r.status_code == 200
    rows = r.json()["rows"]
    region_key = next(column["name"] for column in columns if column["label"] == "区域")
    assert {row[region_key] for row in rows} == {"华东", "华南"}

    r = await client.get(f"/api/apps/{app_id}/html", headers=alice_auth)
    assert r.status_code == 200
    assert "octopApp.listRows" in r.text
    assert "销售看板" in r.text


async def test_quick_create_detects_header_and_skips_totals(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, _srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    csv = "9月工资表\n工号,工资\nE001,8000\nE002,7000\n合计,15000\n".encode()

    r = await client.post(
        "/api/apps/quick/inspect",
        headers=alice_auth,
        data={"agent_id": agent_id},
        files={"file": ("salary.csv", csv, "text/csv")},
    )
    assert r.status_code == 200, r.text
    inspected = r.json()
    sheet = inspected["report"]["sheets"][0]
    assert sheet["header_row"] == 1
    assert "totals_row_detected" in sheet["warnings"]

    r = await client.post(
        "/api/apps/quick/create",
        headers=alice_auth,
        json={
            "agent_id": agent_id,
            "path": inspected["upload_path"],
            "sheets": [
                {
                    "sheet": inspected["sheet"],
                    "columns": [
                        {"name": "employee", "label": "工号", "type": "string"},
                        {"name": "salary", "label": "工资", "type": "number"},
                    ],
                    "mapping": {"工号": "employee", "工资": "salary"},
                    "view": "ledger",
                }
            ],
            "name": "工资台账",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["imported"] == 2

    r = await client.get(
        f"/api/apps/{created['app']['app_id']}/rows?limit=10&offset=0",
        headers=alice_auth,
    )
    assert r.status_code == 200, r.text
    assert [row["employee"] for row in r.json()["rows"]] == ["E001", "E002"]


async def test_preview_image_is_served_and_acl_protected(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630"><rect/></svg>'

    r = await client.get(f"/api/apps/{app_id}/preview", headers=alice_auth)
    assert r.status_code == 404

    row = _service(srv).get(app_id)
    await _service(srv).write_preview(row, svg)

    r = await client.get(f"/api/apps/{app_id}/preview", headers=alice_auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.text == svg
    assert "sandbox" in r.headers["content-security-policy"]

    r = await client.get("/api/apps", headers=alice_auth)
    assert r.json()["apps"][0]["has_preview"] is True

    # A non-collaborator cannot read the preview.
    r = await client.get(f"/api/apps/{app_id}/preview", headers=bob_auth)
    assert r.status_code == 403


async def test_template_preview_is_served(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, _srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent

    r = await client.get(
        f"/api/apps/templates?agent_id={agent_id}&source=system", headers=alice_auth
    )
    assert r.status_code == 200
    templates = {item["template_id"]: item for item in r.json()["templates"]}
    assert templates["expense-tracker"]["has_preview"] is True

    r = await client.get(
        f"/api/apps/templates/expense-tracker/preview?source=system&agent_id={agent_id}",
        headers=alice_auth,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.text.startswith("<svg")

    r = await client.get(
        f"/api/apps/templates/does-not-exist/preview?source=system&agent_id={agent_id}",
        headers=alice_auth,
    )
    assert r.status_code == 404


async def test_preview_written_into_the_workspace_is_not_served_when_unsafe(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """The markup is checked when it is served, not only when it is written."""
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    assert srv.app_runtime is not None
    workspace = srv.app_runtime.agent_registry.workspace_for_agent(agent_id)
    assert workspace is not None
    await workspace.awrite_text(
        f"apps/{app_id}/preview.svg",
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    )

    r = await client.get(f"/api/apps/{app_id}/preview", headers=alice_auth)
    assert r.status_code == 404


async def test_quick_multi_sheet_create_flow(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    import io

    from openpyxl import Workbook

    client, _srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in {
        "orders": [
            ["订单号", "区域", "金额"],
            ["A-1", "华东", 120],
            ["A-2", "华南", 80],
        ],
        "details": [
            ["订单号", "商品", "数量"],
            ["A-1", "纸杯", 10],
            ["A-1", "吸管", 20],
            ["A-2", "纸杯", 5],
        ],
    }.items():
        sheet = workbook.create_sheet(title=name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)

    r = await client.post(
        "/api/apps/quick/inspect",
        headers=alice_auth,
        data={"agent_id": agent_id},
        files={
            "file": (
                "orders.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert r.status_code == 200, r.text
    inspected = r.json()
    sheets = []
    for sheet in inspected["report"]["sheets"]:
        columns = [
            {
                "name": column["name_suggestion"],
                "label": column["source"],
                "type": "string" if column["type"] == "enum" else column["type"],
                "required": False,
            }
            for column in sheet["columns"]
        ]
        sheets.append(
            {
                "sheet": sheet["name"],
                "columns": columns,
                "mapping": {
                    column["source"]: column["name_suggestion"] for column in sheet["columns"]
                },
                "view": "ledger",
            }
        )

    r = await client.post(
        "/api/apps/quick/create",
        headers=alice_auth,
        json={
            "agent_id": agent_id,
            "path": inspected["upload_path"],
            "sheets": sheets,
            "name": "多表订单",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["imported"] == 5
    app_id = created["app"]["app_id"]
    table_keys = [item["table_key"] for item in created["app"]["tables"]]
    assert table_keys == ["main", "table_2"]

    r = await client.get(f"/api/apps/{app_id}/tables", headers=alice_auth)
    assert r.status_code == 200
    assert [item["table_key"] for item in r.json()["tables"]] == ["main", "table_2"]

    r = await client.get(
        f"/api/apps/{app_id}/rows?table=table_2&limit=10&offset=0",
        headers=alice_auth,
    )
    assert r.status_code == 200
    assert r.json()["total"] == 3


async def test_game_session_requires_the_page_capability(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """`game.launch` is enforced on the server, not only in the page bridge."""
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.post(
        f"/api/apps/{app_id}/game-sessions",
        headers=alice_auth,
        json={"game_id": "duck"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APP_CAPABILITY_DENIED"

    # With the capability granted, the next check is the configured game itself.
    service = _service(srv)
    service.set_capabilities(service.get(app_id), app_capabilities.ALL_CAPABILITIES)
    r = await client.post(
        f"/api/apps/{app_id}/game-sessions",
        headers=alice_auth,
        json={"game_id": "missing"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "APP_NOT_FOUND"


async def test_external_game_session_and_result_flow(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(
        srv,
        agent_id,
        "alice",
        capabilities=list(app_capabilities.ALL_CAPABILITIES),
    )
    assert srv.services is not None

    # Games live in config.json; the endpoint reads them from services.
    object.__setattr__(
        srv.services,
        "config",
        replace(
            srv.services.config,
            games=[
                GameConfig(
                    id="duck",
                    name="小鸭养成",
                    url="https://games.example.com/duck/index.html",
                    origin="https://games.example.com",
                )
            ],
        ),
    )

    r = await client.post(
        f"/api/apps/{app_id}/game-sessions",
        headers=alice_auth,
        json={"game_id": "duck"},
    )
    assert r.status_code == 201, r.text
    session = r.json()
    assert session["game_id"] == "duck"
    assert session["name"] == "小鸭养成"
    assert session["url"] == "https://games.example.com/duck/index.html"
    assert session["sandbox"].startswith("allow-scripts")
    assert session["session_id"].startswith("game_")
    assert session["expires_at"] > 0

    # The game token is the only credential on the result endpoint.
    body = {
        "session_id": session["session_id"],
        "operations": [
            {
                "table": "main",
                "mode": "append",
                "rows": [{"title": "duck", "status": "new"}],
            }
        ],
    }
    r = await client.post(f"/api/game-sessions/{session['token']}/results", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1

    # A retry with the same session/row must not duplicate the row.
    r = await client.post(f"/api/game-sessions/{session['token']}/results", json=body)
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/rows?limit=10", headers=alice_auth)
    assert r.json()["total"] == 1

    # Upsert updates the existing row by key.
    r = await client.post(
        f"/api/game-sessions/{session['token']}/results",
        json={
            "session_id": session["session_id"],
            "operations": [
                {
                    "table": "main",
                    "mode": "upsert",
                    "upsert_key": ["title"],
                    "rows": [{"title": "duck", "status": "done"}],
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    r = await client.get(f"/api/apps/{app_id}/rows?limit=10", headers=alice_auth)
    assert r.json()["rows"] == [{"title": "duck", "status": "done"}]

    # A different session id inside the body cannot reuse the token.
    r = await client.post(
        f"/api/game-sessions/{session['token']}/results",
        json={"session_id": "game_other", "operations": []},
    )
    assert r.status_code == 401

    # No write access -> no game session.
    r = await client.post(
        f"/api/apps/{app_id}/game-sessions",
        headers=bob_auth,
        json={"game_id": "duck"},
    )
    assert r.status_code == 403

    # Even a well-formed token cannot bypass the app ACL at write time.
    secret = srv.services.secret_repo.get("jwt")
    assert secret is not None
    bob = srv.services.user_repo.get_by_username("bob")
    assert bob is not None
    bob_token, _ = create_game_token(
        secret,
        app_id=app_id,
        user_id=bob.id,
        game_id="duck",
        session_id="game_bob",
    )
    r = await client.post(
        f"/api/game-sessions/{bob_token}/results",
        json={"session_id": "game_bob", "operations": []},
    )
    assert r.status_code == 403

    # Unknown games are rejected before a token is issued.
    r = await client.post(
        f"/api/apps/{app_id}/game-sessions",
        headers=alice_auth,
        json={"game_id": "nope"},
    )
    assert r.status_code == 404


async def test_public_link_only_exposes_public_tables(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    row = service.get(app_id)
    await service.create_table(
        row,
        table_key="members",
        columns=[{"name": "name", "type": "string"}],
    )

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert [item["table_key"] for item in r.json()["tables"]] == ["main"]

    r = await client.get(f"/api/public/apps/{token}/rows?table=members")
    assert r.status_code == 404, r.text

    r = await client.patch(
        f"/api/apps/{app_id}/tables/members",
        headers=alice_auth,
        json={"public_visible": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["public_visible"] is True

    # The published surface is frozen: a newly public table appears only
    # after the owner republishes.
    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert [item["table_key"] for item in r.json()["tables"]] == ["main"]

    r = await client.get(f"/api/public/apps/{token}/rows?table=members")
    assert r.status_code == 404, r.text

    r = await client.post(
        f"/api/apps/{app_id}/publication/publish",
        headers=alice_auth,
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert [item["table_key"] for item in r.json()["tables"]] == ["main", "members"]

    r = await client.get(f"/api/public/apps/{token}/rows?table=members")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == []

    # Hiding it is a kill switch: it takes effect without republishing.
    r = await client.patch(
        f"/api/apps/{app_id}/tables/members",
        headers=alice_auth,
        json={"public_visible": False},
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/public/apps/{token}")
    assert [item["table_key"] for item in r.json()["tables"]] == ["main"]
    r = await client.get(f"/api/public/apps/{token}/rows?table=members")
    assert r.status_code == 404, r.text


async def test_public_link_freezes_html_until_republished(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    row = service.get(app_id)
    await service.write_html(row, "<html><body><h1>v1</h1></body></html>")

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert "<h1>v1</h1>" in r.json()["html"]

    await service.write_html(row, "<html><body><h1>v2</h1></body></html>")
    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert "<h1>v1</h1>" in r.json()["html"]
    assert "<h1>v2</h1>" not in r.json()["html"]

    r = await client.get(f"/api/apps/{app_id}/publication", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["snapshot_stale"] is True
    assert "html" in r.json()["stale_reasons"]

    r = await client.post(
        f"/api/apps/{app_id}/publication/publish",
        headers=alice_auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["snapshot_stale"] is False

    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert "<h1>v2</h1>" in r.json()["html"]


async def test_restore_previous_html_swaps_versions(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    row = service.get(app_id)

    r = await client.post(f"/api/apps/{app_id}/restore-html", headers=alice_auth)
    assert r.status_code == 404

    await service.write_html(row, "<html><body><h1>v1</h1></body></html>")
    await service.write_html(row, "<html><body><h1>v2</h1></body></html>")

    r = await client.get(f"/api/apps/{app_id}", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["previous_html_at"] is not None

    r = await client.post(f"/api/apps/{app_id}/restore-html", headers=bob_auth)
    assert r.status_code == 403

    r = await client.post(f"/api/apps/{app_id}/restore-html", headers=alice_auth)
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/html", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert "<h1>v1</h1>" in r.text

    # A second restore swaps back to the version that was just replaced.
    r = await client.post(f"/api/apps/{app_id}/restore-html", headers=alice_auth)
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/html", headers=alice_auth)
    assert "<h1>v2</h1>" in r.text


async def test_alter_schema_can_mark_existing_columns_as_pii(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.patch(
        f"/api/apps/{app_id}/schema",
        headers=alice_auth,
        json={"pii": {"title": "phone"}},
    )
    assert r.status_code == 200, r.text
    columns = {item["name"]: item for item in r.json()["columns"]}
    assert columns["title"]["pii"] == "phone"


async def test_in_thread_apps_scan_the_whole_history(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """The dock list must find apps opened before the latest history page."""
    from octop.infra.db.repos.thread_messages import ThreadMessageInput

    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    assert srv.services is not None
    first = await _create_app(srv, agent_id, "alice")
    owner = srv.services.user_repo.get_by_username("alice")
    assert owner is not None
    second = await _service(srv).create(
        agent_id=agent_id,
        owner_user_id=owner.id,
        name="Second",
        description="",
        columns=COLUMNS,
    )

    created = await client.post(f"/api/agents/{agent_id}/threads", headers=alice_auth)
    assert created.status_code == 201, created.text
    thread_id = created.json()["thread_id"]

    def card(app_id: str) -> str:
        return json.dumps(
            {
                "type": "tool",
                "data": {
                    "content": json.dumps(
                        {
                            "octop_ui": {"renderer": "app_card", "version": 1},
                            "data": {"app_id": app_id, "name": app_id},
                        }
                    ),
                    "tool_call_id": f"call_{app_id}",
                    "name": "app_open",
                },
            }
        )

    repo = srv.services.thread_message_repo
    repo.append_legacy_interval(
        thread_id,
        [
            ThreadMessageInput(
                message_id="m-old", role="tool", message_json=card(first), created_at=1
            ),
            ThreadMessageInput(
                message_id="m-new",
                role="tool",
                message_json=card(second.app_id),
                created_at=2,
            ),
        ],
    )
    repo.mark_projection(thread_id, "ready")

    r = await client.get(
        "/api/apps/in-thread",
        headers=alice_auth,
        params={"agent_id": agent_id, "thread_id": thread_id},
    )
    assert r.status_code == 200, r.text
    assert [item["app_id"] for item in r.json()["apps"]] == [second.app_id, first]
    assert r.json()["apps"][1]["name"] == "Tracker"

    r = await client.get(
        "/api/apps/in-thread",
        headers=bob_auth,
        params={"agent_id": agent_id, "thread_id": thread_id},
    )
    assert r.status_code == 403, r.text


async def test_access_request_owner_approval(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.post(
        "/api/apps/access-requests",
        headers=bob_auth,
        json={
            "agent_id": agent_id,
            "app_id": app_id,
            "needed": "edit_structure",
            "note": "need to redesign it",
        },
    )
    assert r.status_code == 200, r.text
    request = r.json()
    assert request["status"] == "pending"
    assert request["requester_username"] == "bob"
    assert request["app_owner_username"] == "alice"
    assert request["agent_owner_username"] == "alice"
    request_id = request["request_id"]

    r = await client.get(
        "/api/apps/access-requests",
        headers=alice_auth,
        params={"scope": "owner", "app_id": app_id},
    )
    assert r.status_code == 200, r.text
    assert [row["request_id"] for row in r.json()["requests"]] == [request_id]

    # Only the owner (or an admin) may resolve it.
    r = await client.post(f"/api/apps/access-requests/{request_id}/approve", headers=bob_auth)
    assert r.status_code == 403

    r = await client.post(f"/api/apps/access-requests/{request_id}/approve", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    r = await client.get(f"/api/apps/{app_id}", headers=bob_auth)
    assert r.status_code == 200, r.text
    assert r.json()["permission"] == "edit_structure"

    r = await client.get(
        "/api/apps/access-requests",
        headers=alice_auth,
        params={"scope": "owner", "app_id": app_id},
    )
    assert r.status_code == 200
    assert r.json()["requests"] == []


async def test_access_request_split_owners_two_step_approval(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    from tests.support.auth import auth_header, create_user

    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    admin_auth = await auth_header(client)
    carol_auth = await create_user(client, admin_auth, username="carol")

    # Share briefly so bob can create an app under alice's expert, then unshare
    # to reproduce the split-owner state.
    r = await client.patch(f"/api/agents/{agent_id}", headers=alice_auth, json={"is_shared": True})
    assert r.status_code == 200, r.text
    app_id = await _create_app(srv, agent_id, "bob")
    r = await client.patch(f"/api/agents/{agent_id}", headers=alice_auth, json={"is_shared": False})
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/apps/access-requests",
        headers=carol_auth,
        json={"agent_id": agent_id, "app_id": app_id, "needed": "edit_data"},
    )
    assert r.status_code == 200, r.text
    request = r.json()
    assert request["app_owner_username"] == "bob"
    assert request["agent_owner_username"] == "alice"
    request_id = request["request_id"]

    # Both owners see the pending request.
    for auth in (bob_auth, alice_auth):
        r = await client.get(
            "/api/apps/access-requests",
            headers=auth,
            params={"scope": "owner", "app_id": app_id},
        )
        assert r.status_code == 200, r.text
        assert [row["request_id"] for row in r.json()["requests"]] == [request_id]

    # The app owner grants the app ACL, but cannot mark it approved while the
    # expert is still unshared.
    r = await client.post(f"/api/apps/access-requests/{request_id}/approve", headers=bob_auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    r = await client.get(f"/api/apps/{app_id}", headers=carol_auth)
    assert r.status_code == 200, r.text
    assert r.json()["permission"] == "edit_data"
    assert srv.services is not None
    agent = srv.services.agent_repo.get(agent_id)
    assert agent is not None and agent.is_shared == 0

    # The expert owner completes the other half.
    r = await client.post(f"/api/apps/access-requests/{request_id}/approve", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    agent = srv.services.agent_repo.get(agent_id)
    assert agent is not None and agent.is_shared == 1
    assert srv.services.user_repo.get_by_username("carol") is not None
    carol = srv.services.user_repo.get_by_username("carol")
    assert carol is not None
    assert _service(srv).is_agent_accessible(agent_id, carol.id)


async def test_admin_http_writes_are_audited(
    env_admin_alice: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str]],
) -> None:
    client, srv, admin_auth, alice_auth = env_admin_alice
    r = await client.post(
        "/api/agents",
        headers=alice_auth,
        json={
            "name": "alice-audit-bot",
            "config": {
                "providers": ["openai"],
                "default_model": "openai:gpt-4o-mini",
            },
        },
    )
    r.raise_for_status()
    agent_id = r.json()["agent_id"]
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=admin_auth,
        json={"row": {"title": "admin wrote this"}},
    )
    assert r.status_code == 200, r.text

    assert srv.services is not None
    entries = srv.services.audit_repo.query(action="miniapp.admin_override", limit=10)
    assert entries
    assert entries[0].target == app_id
    payload = json.loads(entries[0].payload or "{}")
    assert payload["via"] == "http"
    assert payload["needed"] == "edit_data"


async def test_builtin_dataset_descriptor(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    await service.append_row(service.get(app_id), {"title": "A", "status": "new"})

    r = await client.get(f"/api/apps/{app_id}/dataset", headers=alice_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["service_id"] == "builtin.dataset"
    assert body["operations"] == ["describe", "query"]
    assert body["row_scope"] == "app"
    main = body["tables"][0]
    assert main["table_key"] == "main"
    assert main["row_count"] == 1
    assert main["row_scope"] == "app"
    columns = {column["name"]: column for column in main["columns"]}
    assert columns["title"]["required"] is True

    r = await client.post(
        f"/api/apps/{app_id}/dataset/query",
        headers=alice_auth,
        json={"table": "main"},
    )
    assert r.status_code == 200, r.text
    queried = r.json()
    assert queried["total"] == 1
    assert queried["rows"] == [{"title": "A", "status": "new"}]

    r = await client.get(f"/api/apps/{app_id}/dataset", headers=bob_auth)
    assert r.status_code == 403
    r = await client.post(
        f"/api/apps/{app_id}/dataset/query",
        headers=bob_auth,
        json={"table": "main"},
    )
    assert r.status_code == 403


async def test_binding_crud_and_implicit_builtin(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.get(f"/api/apps/{app_id}/bindings", headers=alice_auth)
    assert r.status_code == 200, r.text
    bindings = r.json()["bindings"]
    assert bindings == [
        {
            "binding_id": "dataset",
            "provider": "builtin",
            "service_id": "builtin.dataset",
            "config": {},
            "enabled": True,
            "version": 1,
            "builtin": True,
        }
    ]

    r = await client.put(
        f"/api/apps/{app_id}/bindings/crm",
        headers=alice_auth,
        json={
            "provider": "external",
            "service_id": "crm-service",
            "config": {"base_url": "https://crm.example.com"},
            "enabled": True,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["binding_id"] == "crm"
    assert r.json()["version"] == 1

    r = await client.put(
        f"/api/apps/{app_id}/bindings/crm",
        headers=alice_auth,
        json={
            "provider": "external",
            "service_id": "crm-service",
            "config": {"base_url": "https://crm.example.com"},
            "enabled": False,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert r.json()["enabled"] is False

    r = await client.get(f"/api/apps/{app_id}/bindings", headers=alice_auth)
    assert [item["binding_id"] for item in r.json()["bindings"]] == ["dataset", "crm"]

    # edit_structure collaborators cannot change bindings.
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_structure"}]},
    )
    assert r.status_code == 200
    r = await client.put(
        f"/api/apps/{app_id}/bindings/other",
        headers=bob_auth,
        json={"provider": "connector", "service_id": "x"},
    )
    assert r.status_code == 403

    r = await client.delete(f"/api/apps/{app_id}/bindings/dataset", headers=alice_auth)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "APP_SHARE_INVALID"

    r = await client.delete(f"/api/apps/{app_id}/bindings/crm", headers=alice_auth)
    assert r.status_code == 200
    assert r.json() == {"deleted": "crm"}
    r = await client.get(f"/api/apps/{app_id}/bindings", headers=alice_auth)
    assert [item["binding_id"] for item in r.json()["bindings"]] == ["dataset"]


async def test_user_row_scope_isolates_rows_and_hides_public(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_data"}]},
    )
    assert r.status_code == 200

    r = await client.patch(
        f"/api/apps/{app_id}/tables/main",
        headers=alice_auth,
        json={"public_visible": True, "row_scope": "user"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["row_scope"] == "user"

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "alice"}},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=bob_auth,
        json={"row": {"title": "bob"}},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/apps/{app_id}/rows", headers=bob_auth)
    assert r.status_code == 200, r.text
    assert [row["title"] for row in r.json()["rows"]] == ["bob"]
    assert r.json()["total"] == 1
    bob_row_id = r.json()["row_meta"][0]["row_id"]
    bob_version = r.json()["row_meta"][0]["version"]

    r = await client.get(f"/api/apps/{app_id}/rows", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert [row["title"] for row in r.json()["rows"]] == ["alice", "bob"]
    assert r.json()["total"] == 2
    alice_row_id = next(
        meta["row_id"]
        for row, meta in zip(r.json()["rows"], r.json()["row_meta"], strict=True)
        if row["title"] == "alice"
    )

    # A collaborator cannot touch another user's row.
    r = await client.delete(f"/api/apps/{app_id}/rows/{alice_row_id}", headers=bob_auth)
    assert r.status_code == 404
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{alice_row_id}",
        headers=bob_auth,
        json={"row": {"title": "hijack"}},
    )
    assert r.status_code == 404
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{bob_row_id}",
        headers=bob_auth,
        json={"row": {"title": "bob2"}, "expected_version": bob_version},
    )
    assert r.status_code == 200, r.text

    # User-scoped tables are never exposed on the public link.
    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == []
    r = await client.get(f"/api/public/apps/{token}/rows")
    assert r.status_code == 404


async def test_row_scope_rejects_published(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    # Public exposure is public_visible's job; there is no published row scope.
    r = await client.patch(
        f"/api/apps/{app_id}/tables/main",
        headers=alice_auth,
        json={"row_scope": "published"},
    )
    assert r.status_code == 422, r.text


async def test_declarative_state_machine_rules(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")

    r = await client.put(
        f"/api/apps/{app_id}/tables/main/rules",
        headers=alice_auth,
        json={
            "rules": {
                "state_field": "status",
                "transitions": {"new": ["done"], "done": []},
            }
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["rules"]["transitions"]["new"] == ["done"]

    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "A", "status": "new"}},
    )
    assert r.status_code == 200, r.text
    row_id = r.json()["row_id"]
    version = r.json()["version"]

    r = await client.patch(
        f"/api/apps/{app_id}/rows/{row_id}",
        headers=alice_auth,
        json={
            "row": {"title": "A", "status": "done"},
            "expected_version": version,
        },
    )
    assert r.status_code == 200, r.text
    version = r.json()["version"]

    # done -> new is not declared, so the update is rejected.
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{row_id}",
        headers=alice_auth,
        json={
            "row": {"title": "A", "status": "new"},
            "expected_version": version,
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "APP_ROW_INVALID"

    # edit_data collaborators cannot change the rules.
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_data"}]},
    )
    assert r.status_code == 200
    r = await client.put(
        f"/api/apps/{app_id}/tables/main/rules",
        headers=bob_auth,
        json={"rules": {}},
    )
    assert r.status_code == 403


async def test_dataset_export_contains_schema_rows_rules_and_bindings(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    await service.append_row(service.get(app_id), {"title": "A", "status": "new"})

    r = await client.put(
        f"/api/apps/{app_id}/tables/main/rules",
        headers=alice_auth,
        json={"rules": {"state_field": "status", "transitions": {"new": ["done"]}}},
    )
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/apps/{app_id}/bindings/crm",
        headers=alice_auth,
        json={"provider": "external", "service_id": "crm-service"},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/apps/{app_id}/dataset/export", headers=alice_auth)
    assert r.status_code == 200, r.text
    payload = json.loads(r.content)
    assert payload["format"] == "octop.miniapp.dataset"
    assert payload["version"] == 1
    main = payload["tables"][0]
    assert main["table_key"] == "main"
    assert main["row_scope"] == "app"
    assert main["rules"]["state_field"] == "status"
    assert main["rows"][0]["values"]["title"] == "A"
    assert [item["binding_id"] for item in payload["bindings"]] == ["dataset", "crm"]

    # Exports contain unmasked data; only owner/admin may call them.
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_structure"}]},
    )
    assert r.status_code == 200
    r = await client.get(f"/api/apps/{app_id}/dataset/export", headers=bob_auth)
    assert r.status_code == 403


async def test_dataset_export_import_round_trip(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    await service.append_row(service.get(app_id), {"title": "A", "status": "new"})
    r = await client.patch(
        f"/api/apps/{app_id}/tables/main",
        headers=alice_auth,
        json={"public_visible": True, "row_scope": "user"},
    )
    assert r.status_code == 200
    r = await client.put(
        f"/api/apps/{app_id}/tables/main/rules",
        headers=alice_auth,
        json={"rules": {"state_field": "status", "transitions": {"new": ["done"]}}},
    )
    assert r.status_code == 200
    r = await client.put(
        f"/api/apps/{app_id}/bindings/crm",
        headers=alice_auth,
        json={"provider": "external", "service_id": "crm-service", "config": {"x": 1}},
    )
    assert r.status_code == 200

    r = await client.get(f"/api/apps/{app_id}/dataset/export", headers=alice_auth)
    assert r.status_code == 200
    dataset = json.loads(r.content)

    r = await client.post(
        "/api/apps/dataset/import",
        headers=alice_auth,
        json={"agent_id": agent_id, "dataset": dataset},
    )
    assert r.status_code == 200, r.text
    imported = r.json()
    assert imported["app_id"] != app_id
    assert imported["name"].endswith("(imported)")
    assert imported["tables"][0]["row_scope"] == "user"
    assert imported["tables"][0]["rules"]["state_field"] == "status"

    r = await client.get(f"/api/apps/{imported['app_id']}/rows", headers=alice_auth)
    assert r.status_code == 200
    assert [row["title"] for row in r.json()["rows"]] == ["A"]

    r = await client.get(f"/api/apps/{imported['app_id']}/bindings", headers=alice_auth)
    assert r.status_code == 200
    crm = next(item for item in r.json()["bindings"] if item["binding_id"] == "crm")
    assert crm["enabled"] is False

    r = await client.post(
        "/api/apps/dataset/import",
        headers=alice_auth,
        json={"agent_id": agent_id, "dataset": {"format": "wrong"}},
    )
    assert r.status_code == 400


async def test_r2_zero_backend_small_app_acceptance(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """R2.0 acceptance: a small app closes the loop without any extra backend.

    Creation, multi-table assembly, per-user rows, declarative rules, the
    built-in dataset service, service bindings, export/import and the public
    link all go through the HTTP surface a dashboard or agent would use.
    """
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    service = _service(srv)
    app_id = await _create_app(srv, agent_id, "alice")
    await service.create_table(
        service.get(app_id),
        table_key="catalog",
        label="Catalog",
        columns=[{"name": "item", "type": "string", "required": True}],
    )

    # Main table is per-user and carries a declarative state machine.
    r = await client.patch(
        f"/api/apps/{app_id}/tables/main",
        headers=alice_auth,
        json={"public_visible": True, "row_scope": "user"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["row_scope"] == "user"
    r = await client.put(
        f"/api/apps/{app_id}/tables/main/rules",
        headers=alice_auth,
        json={
            "rules": {
                "state_field": "status",
                "transitions": {"new": ["done"], "done": []},
            }
        },
    )
    assert r.status_code == 200, r.text

    # The catalog is app-wide and public.
    r = await client.patch(
        f"/api/apps/{app_id}/tables/catalog",
        headers=alice_auth,
        json={"public_visible": True},
    )
    assert r.status_code == 200, r.text

    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "edit_data"}]},
    )
    assert r.status_code == 200, r.text

    for headers, title in ((alice_auth, "alice-a"), (bob_auth, "bob-a")):
        r = await client.post(
            f"/api/apps/{app_id}/rows",
            headers=headers,
            json={"row": {"title": title, "status": "new"}},
        )
        assert r.status_code == 200, r.text
    for headers, item in ((alice_auth, "cup"), (bob_auth, "pen")):
        r = await client.post(
            f"/api/apps/{app_id}/rows",
            headers=headers,
            json={"row": {"item": item}, "table": "catalog"},
        )
        assert r.status_code == 200, r.text

    # Per-user isolation works for both list and the dataset service.
    r = await client.get(f"/api/apps/{app_id}/rows", headers=bob_auth)
    assert r.status_code == 200, r.text
    assert [row["title"] for row in r.json()["rows"]] == ["bob-a"]
    r = await client.post(
        f"/api/apps/{app_id}/dataset/query",
        headers=bob_auth,
        json={"table": "main"},
    )
    assert r.status_code == 200, r.text
    assert [row["title"] for row in r.json()["rows"]] == ["bob-a"]
    assert r.json()["total"] == 1
    r = await client.post(
        f"/api/apps/{app_id}/dataset/query",
        headers=bob_auth,
        json={"table": "catalog"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2

    # Rules allow the declared transition and reject the reverse one.
    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"title": "alice-b", "status": "new"}},
    )
    assert r.status_code == 200, r.text
    row_id = r.json()["row_id"]
    version = r.json()["version"]
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{row_id}",
        headers=alice_auth,
        json={
            "row": {"title": "alice-b", "status": "done"},
            "expected_version": version,
        },
    )
    assert r.status_code == 200, r.text
    version = r.json()["version"]
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{row_id}",
        headers=alice_auth,
        json={
            "row": {"title": "alice-b", "status": "new"},
            "expected_version": version,
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "APP_ROW_INVALID"

    # The built-in dataset service exposes every assembled table and its scope.
    r = await client.get(f"/api/apps/{app_id}/dataset", headers=alice_auth)
    assert r.status_code == 200, r.text
    descriptor = r.json()
    assert descriptor["service_id"] == "builtin.dataset"
    assert descriptor["operations"] == ["describe", "query"]
    tables = {table["table_key"]: table for table in descriptor["tables"]}
    assert set(tables) == {"main", "catalog"}
    assert tables["main"]["row_scope"] == "user"
    assert tables["main"]["public_visible"] is True
    assert tables["catalog"]["row_scope"] == "app"
    assert tables["catalog"]["public_visible"] is True

    # Service assembly: builtin plus one external binding, no backend process.
    r = await client.put(
        f"/api/apps/{app_id}/bindings/crm",
        headers=alice_auth,
        json={
            "provider": "external",
            "service_id": "crm-service",
            "config": {"base_url": "https://crm.example.com"},
        },
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/bindings", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert [item["binding_id"] for item in r.json()["bindings"]] == [
        "dataset",
        "crm",
    ]
    assert r.json()["bindings"][0]["builtin"] is True

    # Export and re-import: schema, row scope, rules and bindings survive.
    r = await client.get(f"/api/apps/{app_id}/dataset/export", headers=alice_auth)
    assert r.status_code == 200, r.text
    dataset = json.loads(r.content)
    r = await client.post(
        "/api/apps/dataset/import",
        headers=alice_auth,
        json={"agent_id": agent_id, "dataset": dataset},
    )
    assert r.status_code == 200, r.text
    imported = r.json()
    imported_tables = {table["table_key"]: table for table in imported["tables"]}
    assert imported_tables["main"]["row_scope"] == "user"
    assert imported_tables["main"]["rules"]["state_field"] == "status"
    r = await client.get(f"/api/apps/{imported['app_id']}/rows", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3
    r = await client.get(f"/api/apps/{imported['app_id']}/bindings", headers=alice_auth)
    assert r.status_code == 200, r.text
    crm = next(item for item in r.json()["bindings"] if item["binding_id"] == "crm")
    assert crm["enabled"] is False

    # The public link serves only the app-wide public table.
    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = await client.get(f"/api/public/apps/{token}")
    assert r.status_code == 200, r.text
    assert [table["table_key"] for table in r.json()["tables"]] == ["catalog"]
    r = await client.get(f"/api/public/apps/{token}/rows?table=catalog")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    r = await client.get(f"/api/public/apps/{token}/rows?table=main")
    assert r.status_code == 404, r.text


async def test_read_rows_supports_filters_and_sort(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(
        srv,
        agent_id,
        "alice",
        columns=[
            {"name": "title", "type": "string", "required": True},
            {"name": "qty", "type": "integer"},
            {"name": "status", "type": "enum", "values": ["new", "done"]},
        ],
    )
    for title, qty, status in (("B", "10", "new"), ("A", "9", "done"), ("C", "100", "new")):
        r = await client.post(
            f"/api/apps/{app_id}/rows",
            headers=alice_auth,
            json={"row": {"title": title, "qty": qty, "status": status}},
        )
        assert r.status_code == 200, r.text

    r = await client.get(
        f"/api/apps/{app_id}/rows?limit=10&filter=status=new",
        headers=alice_auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert [item["title"] for item in body["rows"]] == ["B", "C"]

    r = await client.get(f"/api/apps/{app_id}/rows?limit=10&sort=qty", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert [item["title"] for item in r.json()["rows"]] == ["A", "B", "C"]

    r = await client.get(f"/api/apps/{app_id}/rows?limit=10&sort=-qty", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert [item["title"] for item in r.json()["rows"]] == ["C", "B", "A"]

    # A malformed filter is rejected instead of being ignored.
    r = await client.get(f"/api/apps/{app_id}/rows?filter=oops", headers=alice_auth)
    assert r.status_code == 400, r.text

    # An unknown column is rejected too.
    r = await client.get(f"/api/apps/{app_id}/rows?filter=nope=1", headers=alice_auth)
    assert r.status_code == 400, r.text

    r = await client.get(f"/api/apps/{app_id}/rows?limit=10&sort=nope", headers=alice_auth)
    assert r.status_code == 400, r.text


async def test_update_row_works_across_tables(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    app = service.get(app_id)
    await service.create_table(
        app,
        table_key="orders",
        label="Orders",
        columns=[{"name": "item", "type": "string", "required": True}],
    )
    r = await client.post(
        f"/api/apps/{app_id}/rows",
        headers=alice_auth,
        json={"row": {"item": "纸杯"}, "table": "orders"},
    )
    assert r.status_code == 200, r.text
    row_id = r.json()["row_id"]

    # The row id identifies the row; the table does not have to be named.
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{row_id}",
        headers=alice_auth,
        json={"row": {"item": "奶茶"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["row"] == {"item": "奶茶"}

    r = await client.get(f"/api/apps/{app_id}/rows?limit=10&table=orders", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"item": "奶茶"}]

    # A stale version is rejected, so a page cannot silently overwrite.
    r = await client.patch(
        f"/api/apps/{app_id}/rows/{row_id}",
        headers=alice_auth,
        json={"row": {"item": "红茶"}, "expected_version": 99},
    )
    assert r.status_code == 409, r.text


async def test_service_declaration_crud(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    from octop.infra.utils.ulid import new_ulid

    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    assert srv.services is not None
    alice = srv.services.user_repo.get_by_username("alice")
    assert alice is not None
    instance_id = new_ulid()
    srv.services.repos.connector_repo.create(
        instance_id=instance_id,
        user_id=alice.id,
        kind="http-extract",
        display_name="HTTP",
        mcp_server_name=f"http_{instance_id}",
    )

    body = {
        "provider": "external",
        "connector_instance_id": instance_id,
        "allowed_tools": ["fetch_json"],
        "allowed_url_prefixes": ["https://api.example.com"],
        "cache_ttl_seconds": 300,
        "max_calls_per_minute": 5,
        "enabled": True,
    }
    r = await client.put(
        f"/api/apps/{app_id}/services/douban",
        headers=alice_auth,
        json=body,
    )
    assert r.status_code == 200, r.text
    service = r.json()
    assert service["name"] == "douban"
    assert service["allowed_tools"] == ["fetch_json"]

    r = await client.get(f"/api/apps/{app_id}/services", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert [item["name"] for item in r.json()["services"]] == ["douban"]

    r = await client.get(f"/api/apps/{app_id}/bindings", headers=alice_auth)
    assert r.status_code == 200, r.text
    binding = next(item for item in r.json()["bindings"] if item["binding_id"] == "douban")
    assert binding["provider"] == "external"
    assert binding["service_id"] == service["service_id"]

    # Viewers can list declarations but not manage them.
    r = await client.get(f"/api/apps/{app_id}/services", headers=bob_auth)
    assert r.status_code == 403
    r = await client.put(
        f"/api/apps/{app_id}/shares",
        headers=alice_auth,
        json={"shares": [{"username": "bob", "permission": "view"}]},
    )
    assert r.status_code == 200
    r = await client.get(f"/api/apps/{app_id}/services", headers=bob_auth)
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/apps/{app_id}/services/other",
        headers=bob_auth,
        json={**body, "provider": "connector"},
    )
    assert r.status_code == 403

    r = await client.delete(f"/api/apps/{app_id}/services/douban", headers=alice_auth)
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/apps/{app_id}/services", headers=alice_auth)
    assert r.json()["services"] == []
    r = await client.get(f"/api/apps/{app_id}/bindings", headers=alice_auth)
    assert [item["binding_id"] for item in r.json()["bindings"]] == ["dataset"]


async def test_service_call_api(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.apps.sources import GatewayToolRunner
    from octop.infra.utils.ulid import new_ulid

    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_run(
        self: GatewayToolRunner,
        *,
        instance_id: str,
        tool: str,
        args: dict[str, object],
    ) -> str:
        del self
        calls.append((instance_id, tool, dict(args)))
        return '{"ok": true}'

    monkeypatch.setattr(GatewayToolRunner, "run", fake_run)
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    assert srv.services is not None
    alice = srv.services.user_repo.get_by_username("alice")
    assert alice is not None
    instance_id = new_ulid()
    srv.services.repos.connector_repo.create(
        instance_id=instance_id,
        user_id=alice.id,
        kind="http-extract",
        display_name="HTTP",
        mcp_server_name=f"http_{instance_id}",
    )
    r = await client.put(
        f"/api/apps/{app_id}/services/douban",
        headers=alice_auth,
        json={
            "provider": "external",
            "connector_instance_id": instance_id,
            "allowed_tools": ["fetch_json"],
            "allowed_url_prefixes": ["https://api.example.com"],
            "cache_ttl_seconds": 300,
            "max_calls_per_minute": 5,
        },
    )
    assert r.status_code == 200, r.text

    r = await client.put(
        f"/api/apps/{app_id}/capabilities",
        headers=alice_auth,
        json={"capabilities": ["data.read", "data.write", "external.fetch"]},
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/apps/{app_id}/services/douban/call",
        headers=alice_auth,
        json={"tool": "fetch_json", "args": {"url": "https://api.example.com/books"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"] == {"ok": True}
    assert body["cached"] is False
    assert body["expires_at"] is not None
    assert calls == [(instance_id, "fetch_json", {"url": "https://api.example.com/books"})]

    r = await client.post(
        f"/api/apps/{app_id}/services/douban/call",
        headers=alice_auth,
        json={"tool": "fetch_json", "args": {"url": "https://api.example.com/books"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["cached"] is True
    assert len(calls) == 1

    r = await client.post(
        f"/api/apps/{app_id}/services/douban/call",
        headers=alice_auth,
        json={"tool": "fetch_json", "args": {"url": "https://evil.example.net/x"}},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "APP_SOURCE_INVALID"

    r = await client.post(
        f"/api/apps/{app_id}/services/douban/call",
        headers=alice_auth,
        json={"tool": "fetch_text", "args": {"url": "https://api.example.com/x"}},
    )
    assert r.status_code == 400, r.text

    # A new app does not get external.fetch by default.
    other_app = await _create_app(srv, agent_id, "alice")
    r = await client.post(
        f"/api/apps/{other_app}/services/douban/call",
        headers=alice_auth,
        json={"tool": "fetch_json", "args": {"url": "https://api.example.com/x"}},
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "APP_CAPABILITY_DENIED"


async def test_public_link_serves_app_assets(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    png = b"\x89PNG\r\n\x1a\npublic-cover"
    name = await service.save_asset(service.get(app_id), "cover.png", png)

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = await client.get(f"/api/public/apps/{token}/assets/{name}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content == png

    r = await client.get(f"/api/public/apps/{token}/assets/missing.png")
    assert r.status_code == 404


async def test_public_service_call_reads_only_cached_responses(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.apps.sources import GatewayToolRunner
    from octop.infra.utils.ulid import new_ulid

    calls: list[str] = []

    def fake_run(
        self: GatewayToolRunner,
        *,
        instance_id: str,
        tool: str,
        args: dict[str, object],
    ) -> str:
        del self, instance_id, tool, args
        calls.append("live")
        return '{"ok": true}'

    monkeypatch.setattr(GatewayToolRunner, "run", fake_run)
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    assert srv.services is not None
    alice = srv.services.user_repo.get_by_username("alice")
    assert alice is not None
    instance_id = new_ulid()
    srv.services.repos.connector_repo.create(
        instance_id=instance_id,
        user_id=alice.id,
        kind="http-extract",
        display_name="HTTP",
        mcp_server_name=f"http_{instance_id}",
    )
    body = {
        "provider": "external",
        "connector_instance_id": instance_id,
        "allowed_tools": ["fetch_json"],
        "allowed_url_prefixes": ["https://api.example.com"],
        "cache_ttl_seconds": 300,
        "max_calls_per_minute": 5,
        "public_live": True,
    }
    r = await client.put(
        f"/api/apps/{app_id}/services/douban",
        headers=alice_auth,
        json=body,
    )
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/apps/{app_id}/capabilities",
        headers=alice_auth,
        json={"capabilities": ["data.read", "data.write", "external.fetch"]},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/apps/{app_id}/services/douban/call",
        headers=alice_auth,
        json={"tool": "fetch_json", "args": {"url": "https://api.example.com/books"}},
    )
    assert r.status_code == 200, r.text
    assert calls == ["live"]

    r = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=alice_auth,
        json={"ttl_days": 0},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = await client.post(
        f"/api/public/apps/{token}/services/douban/call",
        json={"tool": "fetch_json", "args": {"url": "https://api.example.com/books"}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"value": {"ok": True}, "cached": True}
    assert calls == ["live"]

    r = await client.post(
        f"/api/public/apps/{token}/services/douban/call",
        json={"tool": "fetch_json", "args": {"url": "https://api.example.com/other"}},
    )
    assert r.status_code == 400, r.text

    # Public call requires the owner's explicit public_live opt-in.
    r = await client.put(
        f"/api/apps/{app_id}/services/private",
        headers=alice_auth,
        json={**body, "public_live": False},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/public/apps/{token}/services/private/call",
        json={"tool": "fetch_json", "args": {"url": "https://api.example.com/books"}},
    )
    assert r.status_code == 400, r.text


async def test_app_event_inbox_api(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _create_app(srv, agent_id, "alice")
    service = _service(srv)
    row = service.get(app_id)
    first = service.append_event(
        row,
        event_type="order.created",
        payload={"id": "A"},
        source="orders",
        summary="new order",
    )
    service.append_event(row, event_type="order.paid", payload={"id": "A"})

    r = await client.get(f"/api/apps/{app_id}/events", headers=alice_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [item["event_id"] for item in body["events"]] == [
        first.event_id,
        body["events"][1]["event_id"],
    ]
    assert body["events"][0]["summary"] == "new order"
    latest = body["latest_id"]

    r = await client.get(f"/api/apps/{app_id}/events?since={latest}", headers=alice_auth)
    assert r.status_code == 200, r.text
    assert r.json()["events"] == []

    r = await client.post(
        f"/api/apps/{app_id}/events/ack",
        headers=alice_auth,
        json={"event_ids": [first.event_id]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged"] == 1
    r = await client.get(f"/api/apps/{app_id}/events", headers=alice_auth)
    assert r.json()["events"][0]["acknowledged_at"] is not None

    r = await client.get(f"/api/apps/{app_id}/events", headers=bob_auth)
    assert r.status_code == 403
