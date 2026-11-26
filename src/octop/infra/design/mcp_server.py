"""MCP surface for the geometry kernel.

The expert connects to this server through a ``custom-mcp`` connector
(``streamable_http``) and calls the same three operations the MiniApp page
uses over REST.

Run it with::

    python -m octop.infra.design --mcp --host 127.0.0.1 --port 8849
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from octop.infra.design.checks import RULES
from octop.infra.design.checks import check_drawing as run_checks
from octop.infra.design.ir import parse_ir
from octop.infra.design.ops import apply_ops as run_ops
from octop.infra.design.takeoff import takeoff as run_takeoff

INSTRUCTIONS = """\
Deterministic geometry kernel for interior design plans.

All numbers come from the geometry in the supplied IR (millimetres, y-up).
The IR carries `walls` (x1,y1,x2,y2,t), `openings` (type,x,y,width,angle,host),
`furniture` (x,y,w,d,rot,kind) and `rooms` (id,name,polygon).

Call check_drawing after every geometry change. When only some elements
changed, pass them as changed_ids so only the affected rules are re-run.
"""


def build_server(host: str = "127.0.0.1", port: int = 8849) -> FastMCP:
    """Create the MCP server exposing the kernel tools."""
    mcp = FastMCP(
        "octop-interior-geometry",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        streamable_http_path="/mcp",
    )

    @mcp.tool()
    def check_drawing(ir: dict[str, Any], changed_ids: list[str] | None = None) -> dict[str, Any]:
        """Validate a plan and return structured diagnostics.

        Each diagnostic carries rule_id, status (non_compliant /
        not_verifiable / not_applicable), severity (blocking / significant /
        improvement), a human message and the element ids involved.
        """
        parsed = parse_ir(ir)
        found = run_checks(parsed, changed_ids)
        return {
            "diagnostics": [item.to_dict() for item in found],
            "checked": len(found),
            "rules": sorted(RULES),
        }

    @mcp.tool()
    def takeoff(ir: dict[str, Any]) -> dict[str, Any]:
        """Derive room areas and the quantity take-off from the geometry."""
        return run_takeoff(parse_ir(ir))

    @mcp.tool()
    def apply_ops(ir: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply atomic IR operations and return the resulting IR.

        Operations: add (kind + element), update (id + patch), move (id + dx/dy),
        delete (id).
        """
        return run_ops(parse_ir(ir), ops).to_dict()

    return mcp


def serve(host: str = "127.0.0.1", port: int = 8849) -> None:
    """Block serving the MCP API."""
    build_server(host, port).run(transport="streamable-http")
