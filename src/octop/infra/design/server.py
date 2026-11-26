"""Standalone HTTP surface for the geometry kernel.

The MiniApp page reaches this service through an ``external.write``
extension service; the expert reaches the same logic through the MCP server
in :mod:`octop.infra.design.mcp_server`. Both are pure functions of the IR.

Run it with::

    python -m octop.infra.design --host 127.0.0.1 --port 8848

Set ``OCTOP_INTERIOR_KERNEL_TOKEN`` to require a bearer token.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

from octop.infra.design.checks import RULES, check_drawing
from octop.infra.design.ir import parse_ir
from octop.infra.design.ops import apply_ops
from octop.infra.design.takeoff import takeoff

TOKEN_ENV: Final = "OCTOP_INTERIOR_KERNEL_TOKEN"
MAX_BODY_BYTES: Final = 4 * 1024 * 1024


class KernelHandler(BaseHTTPRequestHandler):
    """JSON endpoints for the kernel."""

    server_version = "OctopInteriorGeometry/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # --- helpers ---

    def _token(self) -> str:
        return os.environ.get(TOKEN_ENV, "").strip()

    def _authorized(self) -> bool:
        expected = self._token()
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        return header.strip() == f"Bearer {expected}"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        idempotency = self.headers.get("Idempotency-Key")
        if idempotency:
            self.send_header("Idempotency-Key", idempotency)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            self._send(413, {"error": "request body too large"})
            return None
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send(400, {"error": "invalid JSON body"})
            return None
        if not isinstance(parsed, dict):
            self._send(400, {"error": "body must be a JSON object"})
            return None
        return parsed

    # --- routes ---

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in {"/health", "/healthz"}:
            self._send(200, {"ok": True, "rules": sorted(RULES)})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.rstrip("/")
        body = self._read_json()
        if body is None:
            return
        try:
            if path == "/check_drawing":
                self._send(200, _check_drawing(body))
            elif path == "/takeoff":
                self._send(200, _takeoff(body))
            elif path == "/ops":
                self._send(200, _ops(body))
            else:
                self._send(404, {"error": "not found"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})


def _check_drawing(body: dict[str, Any]) -> dict[str, Any]:
    ir = parse_ir(body.get("ir"))
    changed = body.get("changed_ids")
    changed_ids = [str(item) for item in changed] if isinstance(changed, list) else None
    diagnostics = check_drawing(ir, changed_ids)
    return {
        "diagnostics": [item.to_dict() for item in diagnostics],
        "checked": len(diagnostics),
        "incremental": changed_ids is not None,
    }


def _takeoff(body: dict[str, Any]) -> dict[str, Any]:
    ir = parse_ir(body.get("ir"))
    return takeoff(ir)


def _ops(body: dict[str, Any]) -> dict[str, Any]:
    ir = parse_ir(body.get("ir"))
    ops = body.get("ops")
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    return apply_ops(ir, [op for op in ops if isinstance(op, dict)]).to_dict()


def serve(host: str = "127.0.0.1", port: int = 8848) -> None:
    """Block serving the REST API."""
    server = ThreadingHTTPServer((host, port), KernelHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
