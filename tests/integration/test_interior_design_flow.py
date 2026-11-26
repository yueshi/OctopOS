"""End-to-end flow for the interior-design MiniApp.

Three paths are exercised against a live geometry kernel:

1. the MiniApp page path — the app calls the kernel through an
   ``external.write`` extension service;
2. the expert path — the expert calls the kernel over MCP;
3. the IR contract — the rows in the ``main`` table mean the same thing to
   the kernel and to the page that draws them.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from octop.infra.apps.service import AppService
from octop.infra.apps.wiring import build_app_service
from octop.infra.design.ir import ir_from_rows
from octop.infra.design.server import KernelHandler
from octop.infra.server import OctopServer

ROOMS_COLUMNS: list[dict[str, Any]] = [
    {"name": "room_id", "label": "房间 ID", "type": "string", "required": True},
    {"name": "name", "label": "房间名", "type": "string"},
    {"name": "area_mm2", "label": "面积 (mm²)", "type": "number"},
    {"name": "payload", "label": "多边形", "type": "text"},
    {"name": "source", "label": "来源", "type": "string"},
]

DIAGNOSTICS_COLUMNS: list[dict[str, Any]] = [
    {"name": "rule_id", "label": "规则 ID", "type": "string", "required": True},
    {
        "name": "severity",
        "label": "严重度",
        "type": "enum",
        "required": True,
        "values": ["blocking", "significant", "improvement"],
    },
    {
        "name": "status",
        "label": "状态",
        "type": "enum",
        "values": ["non_compliant", "not_verifiable", "not_applicable", "compliant"],
    },
    {"name": "message", "label": "说明", "type": "text"},
    {"name": "elements", "label": "涉及元素", "type": "string"},
    {"name": "acknowledged", "label": "已接受", "type": "boolean"},
]

BOM_COLUMNS: list[dict[str, Any]] = [
    {"name": "category", "label": "类别", "type": "string"},
    {"name": "item", "label": "项目", "type": "string"},
    {"name": "qty", "label": "数量", "type": "number"},
    {"name": "unit", "label": "单位", "type": "string"},
    {"name": "source_element", "label": "来源元素", "type": "string"},
]

OPERATIONS_COLUMNS: list[dict[str, Any]] = [
    {"name": "seq", "label": "序号", "type": "integer"},
    {"name": "op", "label": "操作", "type": "string"},
    {"name": "target", "label": "对象", "type": "string"},
    {"name": "args", "label": "参数", "type": "text"},
    {"name": "created_at", "label": "时间", "type": "datetime"},
]


def _plan_rows() -> list[dict[str, Any]]:
    """A living room with a window, a door and a wardrobe blocking its front."""
    return [
        {
            "element_id": "W-001",
            "kind": "wall",
            "name": "南墙",
            "payload": json.dumps({"x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120}),
            "source": "用户户型图",
            "status": "confirmed",
        },
        {
            "element_id": "W-002",
            "kind": "wall",
            "name": "东墙",
            "payload": json.dumps({"x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120}),
            "source": "用户户型图",
            "status": "confirmed",
        },
        {
            "element_id": "W-003",
            "kind": "wall",
            "name": "北墙",
            "payload": json.dumps({"x1": 4000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120}),
            "source": "用户户型图",
            "status": "confirmed",
        },
        {
            "element_id": "W-004",
            "kind": "wall",
            "name": "西墙",
            "payload": json.dumps({"x1": 0, "y1": 3000, "x2": 0, "y2": 0, "t": 120}),
            "source": "用户户型图",
            "status": "confirmed",
        },
        {
            "element_id": "D-001",
            "kind": "opening",
            "name": "入户门",
            "payload": json.dumps(
                {"type": "door", "x": 2000, "y": 0, "width": 900, "angle": 0, "host": "W-001"}
            ),
            "source": "用户户型图",
            "status": "confirmed",
        },
        {
            "element_id": "N-001",
            "kind": "opening",
            "name": "客厅窗",
            "payload": json.dumps(
                {
                    "type": "window",
                    "x": 2500,
                    "y": 3000,
                    "width": 1500,
                    "angle": 180,
                    "host": "W-003",
                }
            ),
            "source": "用户户型图",
            "status": "confirmed",
        },
        {
            "element_id": "F-001",
            "kind": "furniture",
            "name": "衣柜",
            "payload": json.dumps(
                {"x": 0, "y": 0, "w": 2000, "d": 600, "rot": 0, "kind": "wardrobe"}
            ),
            "source": "默认布置",
            "status": "assumption",
        },
        {
            "element_id": "F-002",
            "kind": "furniture",
            "name": "餐桌",
            "payload": json.dumps(
                {"x": 800, "y": 900, "w": 800, "d": 500, "rot": 0, "kind": "table"}
            ),
            "source": "默认布置",
            "status": "assumption",
        },
    ]


@pytest.fixture()
def kernel_base_url() -> Any:
    """A live geometry kernel on an ephemeral port."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), KernelHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _service(srv: OctopServer) -> AppService:
    assert srv.services is not None
    assert srv.app_runtime is not None
    return build_app_service(
        repos=srv.services.repos,
        workspaces=srv.app_runtime.agent_registry,
    )


async def _owner_id(srv: OctopServer, username: str) -> int:
    assert srv.services is not None
    owner = srv.services.user_repo.get_by_username(username)
    assert owner is not None
    return owner.id


async def _build_app(srv: OctopServer, agent_id: str, username: str) -> tuple[AppService, str]:
    """Create the app from the system template and add the four side tables."""
    service = _service(srv)
    owner_id = await _owner_id(srv, username)
    row = await service.create_from_template(
        agent_id=agent_id,
        owner_user_id=owner_id,
        template="interior-plan",
        source="system",
        name="示例装修方案",
        # Mirrors the dashboard owner path: the template's declared
        # capabilities (including external.write) are granted. The agent tool
        # path leaves them off, which is the documented friction.
        grant_template_capabilities=True,
    )
    for key, label, columns in (
        ("rooms", "房间", ROOMS_COLUMNS),
        ("diagnostics", "诊断", DIAGNOSTICS_COLUMNS),
        ("bom", "工程量", BOM_COLUMNS),
        ("operations", "操作日志", OPERATIONS_COLUMNS),
    ):
        await service.create_table(
            row,
            table_key=key,
            label=label,
            columns=columns,
            public_visible=False,
        )
    return service, row.app_id


async def _seed_main(service: AppService, app_id: str, rows: list[dict[str, Any]]) -> None:
    for values in rows:
        await service.append_row(service.get(app_id), values, table="main")


async def test_app_is_created_from_the_template_with_its_tables(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    service, app_id = await _build_app(srv, agent_id, "alice")

    r = await client.get(f"/api/apps/{app_id}/tables", headers=alice_auth)
    assert r.status_code == 200
    tables = {item["table_key"] for item in r.json()["tables"]}
    assert tables == {"main", "rooms", "diagnostics", "bom", "operations"}

    r = await client.get(f"/api/apps/{app_id}/html", headers=alice_auth)
    assert r.status_code == 200
    assert "octopApp" in r.text


async def test_page_calls_the_kernel_through_the_extension_service(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
    kernel_base_url: str,
) -> None:
    """The exact path the MiniApp page takes: invoke_service → kernel → rows."""
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    service, app_id = await _build_app(srv, agent_id, "alice")
    await _seed_main(service, app_id, _plan_rows())

    owner_id = await _owner_id(srv, "alice")
    row = service.get(app_id)
    service.save_service(
        row,
        name="cad",
        provider="extension",
        mode="write",
        base_url=kernel_base_url,
        connector_instance_id="",
        allowed_tools=[],
        allowed_url_prefixes=[],
        max_calls_per_minute=30,
        public_live=False,
        enabled=True,
        write_tools=[
            {"name": "check_drawing", "method": "POST", "path": "/check_drawing"},
            {"name": "takeoff", "method": "POST", "path": "/takeoff"},
        ],
        timeout_seconds=30,
        max_request_bytes=1024 * 1024,
        max_response_bytes=5 * 1024 * 1024,
        idempotency_required=True,
        cache_ttl_seconds=0,
        allow_private_network=True,
        actor_user_id=owner_id,
        is_admin=True,
    )

    ir = _ir_payload()
    result = await service.invoke_service(
        row,
        name="cad",
        tool="check_drawing",
        args={"ir": ir},
        idempotency_key="e2e-check-1",
        actor_user_id=owner_id,
        actor_is_admin=True,
    )
    assert result.status == "succeeded"
    diagnostics = result.result["diagnostics"]
    assert diagnostics
    rule_ids = {item["rule_id"] for item in diagnostics}
    assert "clearance.wardrobe_front" in rule_ids
    assert "ventilation.service_room" in rule_ids

    # The page writes the diagnostics into the table so it can highlight them.
    for item in diagnostics:
        await service.append_row(
            row,
            {
                "rule_id": item["rule_id"],
                "severity": item["severity"],
                "status": item["status"],
                "message": item["message"],
                "elements": ",".join(item.get("elements") or []),
                "acknowledged": False,
            },
            table="diagnostics",
        )

    r = await client.get(f"/api/apps/{app_id}/rows?table=diagnostics", headers=alice_auth)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == len(diagnostics)
    assert {row["rule_id"] for row in rows} == rule_ids

    # Every element a diagnostic points at must exist in the IR table,
    # otherwise the page cannot highlight anything.
    r = await client.get(f"/api/apps/{app_id}/rows?table=main", headers=alice_auth)
    element_ids = {row["element_id"] for row in r.json()["rows"]}
    for row in rows:
        referenced = [item for item in row["elements"].split(",") if item]
        assert set(referenced).issubset(element_ids)


async def test_expert_reads_the_same_result_over_mcp() -> None:
    """The MCP path the expert uses: list tools, then check_drawing."""
    import socket
    import time

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from octop.infra.design.mcp_server import build_server

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    mcp = build_server("127.0.0.1", port)
    thread = threading.Thread(target=lambda: mcp.run(transport="streamable-http"), daemon=True)
    thread.start()

    # Wait for the listener before speaking MCP: the client wraps connection
    # errors in an ExceptionGroup, which the retry loop would not see.
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        pytest.fail("the kernel MCP server never started listening")

    url = f"http://127.0.0.1:{port}/mcp"
    async with (
        streamablehttp_client(url) as (read, write, _get_session_id),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"check_drawing", "takeoff", "apply_ops"}.issubset(names)

        result = await session.call_tool("check_drawing", {"ir": _ir_payload()})
        payload = json.loads(result.content[0].text)
        rule_ids = {item["rule_id"] for item in payload["diagnostics"]}
        assert "clearance.wardrobe_front" in rule_ids


async def test_rows_and_page_build_the_same_ir(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    """The IR the kernel sees is the IR the page would draw."""
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    service, app_id = await _build_app(srv, agent_id, "alice")
    await _seed_main(service, app_id, _plan_rows())

    r = await client.get(f"/api/apps/{app_id}/rows?table=main", headers=alice_auth)
    rows = r.json()["rows"]
    ir = ir_from_rows(rows)

    assert len(ir.walls) == 4
    assert len(ir.openings) == 2
    assert len(ir.furniture) == 2

    # The payload the page sends to the kernel carries the same geometry.
    page_ir = _ir_payload()
    assert len(page_ir["walls"]) == len(ir.walls)
    assert {wall["id"] for wall in page_ir["walls"]} == {wall.id for wall in ir.walls}
    assert page_ir["rooms"][0]["polygon"][0] == [0, 0]


def _ir_payload() -> dict[str, Any]:
    """The IR the page sends: same rows, grouped by kind."""
    return {
        "version": 1,
        "walls": [
            {"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
            {"id": "W-002", "x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120},
            {"id": "W-003", "x1": 4000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120},
            {"id": "W-004", "x1": 0, "y1": 3000, "x2": 0, "y2": 0, "t": 120},
        ],
        "openings": [
            {
                "id": "D-001",
                "type": "door",
                "x": 2000,
                "y": 0,
                "width": 900,
                "angle": 0,
                "host": "W-001",
            },
            {
                "id": "N-001",
                "type": "window",
                "x": 2500,
                "y": 3000,
                "width": 1500,
                "angle": 180,
                "host": "W-003",
            },
        ],
        "furniture": [
            {
                "id": "F-001",
                "x": 0,
                "y": 0,
                "w": 2000,
                "d": 600,
                "rot": 0,
                "kind": "wardrobe",
                "name": "衣柜",
            },
            {
                "id": "F-002",
                "x": 800,
                "y": 900,
                "w": 800,
                "d": 500,
                "rot": 0,
                "kind": "table",
                "name": "餐桌",
            },
        ],
        "rooms": [
            {"id": "R-001", "name": "客厅", "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}
        ],
    }
