"""Generated-file list and HTML preview capability URLs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest

from tests.support.auth import create_user

FROM_WORKSPACE = {"from_workspace": "true"}

HTML = (
    "<!doctype html><html><head><link rel='stylesheet' href='style.css'></head>"
    "<body><h1>Report</h1><img src='pic.svg'></body></html>"
)


async def _write(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    agent_id: str,
    path: str,
    content: str,
) -> None:
    r = await client.put(
        f"/api/agents/{agent_id}/workspace/file",
        params={**FROM_WORKSPACE, "path": path},
        headers=auth,
        json={"content": content},
    )
    assert r.status_code == 200, r.text


async def _preview_session(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    agent_id: str,
    path: str,
) -> dict[str, Any]:
    r = await client.post(
        f"/api/agents/{agent_id}/workspace/preview-session",
        headers=auth,
        json={"path": path},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_artifacts_endpoint_lists_generated_files(env_with_agent: Any) -> None:
    client, srv, auth, agent_id = env_with_agent
    await _write(client, auth, agent_id, "/outbound/report.html", HTML)
    agent = srv.app_runtime.agent_registry.get_agent(agent_id)
    absolute = str(Path(agent.workspace.workspace_dir) / "outbound" / "report.html")
    thread = (await client.post(f"/api/agents/{agent_id}/threads", headers=auth)).json()
    srv.app_runtime.gateway.thread_registry.append_artifacts(
        thread["thread_id"],
        [absolute],
        agent_id=agent_id,
    )

    r = await client.get(f"/api/agents/{agent_id}/artifacts", headers=auth)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["path"] == "outbound/report.html"
    assert item["name"] == "report.html"
    assert item["agent_id"] == agent_id
    assert item["thread_id"] == thread["thread_id"]
    assert item["in_workspace"] is True

    r = await client.get(
        f"/api/agents/{agent_id}/artifacts?limit=1&thread_id={thread['thread_id']}",
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1


async def test_artifacts_endpoint_rejects_non_owner(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    bob = await create_user(client, auth, username="bob")
    r = await client.get(f"/api/agents/{agent_id}/artifacts", headers=bob)
    assert r.status_code == 403


async def test_preview_session_serves_html_and_relative_assets(
    env_with_agent: Any,
) -> None:
    client, _srv, auth, agent_id = env_with_agent
    await _write(client, auth, agent_id, "/outbound/report.html", HTML)
    await _write(
        client,
        auth,
        agent_id,
        "/outbound/style.css",
        "body{background:rgb(1,2,3)}",
    )
    await _write(
        client,
        auth,
        agent_id,
        "/outbound/pic.svg",
        "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'/>",
    )

    session = await _preview_session(
        client,
        auth,
        agent_id,
        "outbound/report.html",
    )
    assert session["entry"] == "report.html"
    base = "/api" + session["base_path"]

    r = await client.get(base + session["entry"])
    assert r.status_code == 200, r.text
    assert r.text == HTML
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "sandbox allow-scripts allow-forms" in csp
    assert "connect-src 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"

    css = await client.get(base + "style.css")
    assert css.status_code == 200, css.text
    assert css.headers["content-type"].startswith("text/css")

    svg = await client.get(base + "pic.svg")
    assert svg.status_code == 200, svg.text
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["content-security-policy"] == "sandbox"


async def test_preview_session_directory_uses_index(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    await _write(client, auth, agent_id, "/site/index.html", HTML)

    session = await _preview_session(client, auth, agent_id, "site")
    assert session["entry"] == "index.html"
    r = await client.get("/api" + session["base_path"] + "index.html")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "bad_path",
    [
        "../.env",
        ".octop/auth/token",
        "_builtin_skills/foo/SKILL.md",
        "outbound/../secret.html",
    ],
)
async def test_preview_session_rejects_unsafe_paths(
    env_with_agent: Any,
    bad_path: str,
) -> None:
    client, _srv, auth, agent_id = env_with_agent
    await _write(client, auth, agent_id, "/outbound/report.html", HTML)
    r = await client.post(
        f"/api/agents/{agent_id}/workspace/preview-session",
        headers=auth,
        json={"path": bad_path},
    )
    assert r.status_code in (400, 403), r.text


async def test_preview_mount_rejects_traversal(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    await _write(client, auth, agent_id, "/outbound/report.html", HTML)
    session = await _preview_session(
        client,
        auth,
        agent_id,
        "outbound/report.html",
    )
    base = "/api" + session["base_path"]
    for raw in ("%2e%2e%2f.env", ".octop%2fauth", "..%2Fsecret.html"):
        r = await client.get(base + raw)
        assert r.status_code in (400, 403, 404), (raw, r.status_code, r.text)


async def test_preview_mount_rejects_expired_and_access_tokens(
    env_with_agent: Any,
) -> None:
    client, srv, _auth, agent_id = env_with_agent
    secret = srv.services.secret_repo.get("jwt")
    assert secret is not None
    now = int(time.time())
    expired = jwt.encode(
        {
            "typ": "workspace_preview",
            "agent_id": agent_id,
            "user_id": 1,
            "root": ".",
            "iat": now - 120,
            "exp": now - 60,
        },
        secret,
        algorithm="HS256",
    )
    r = await client.get(f"/api/workspace-preview/{expired}/report.html")
    assert r.status_code == 401, r.text

    access = jwt.encode(
        {
            "sub": "1",
            "uname": "admin",
            "role": "admin",
            "exp": now + 60,
        },
        secret,
        algorithm="HS256",
    )
    r = await client.get(f"/api/workspace-preview/{access}/report.html")
    assert r.status_code == 401, r.text


async def test_workspace_tree_browses_stopped_agent(env_with_agent: Any) -> None:
    client, _srv, auth, agent_id = env_with_agent
    await _write(client, auth, agent_id, "/outbound/report.html", HTML)
    r = await client.post(f"/api/agents/{agent_id}/stop", headers=auth)
    assert r.status_code == 204, r.text

    r = await client.get(
        f"/api/agents/{agent_id}/workspace/tree",
        params={**FROM_WORKSPACE, "path": "/outbound"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert any("report.html" in row["path"] for row in r.json())
