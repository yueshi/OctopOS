"""Tests for the kernel's HTTP surface."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from octop.infra.design.server import KernelHandler


@pytest.fixture()
def server() -> Any:
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


def _post(base: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _plan() -> dict[str, Any]:
    return {
        "walls": [
            {"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
            {"id": "W-002", "x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120},
            {"id": "W-003", "x1": 4000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120},
            {"id": "W-004", "x1": 0, "y1": 3000, "x2": 0, "y2": 0, "t": 120},
        ],
        "openings": [
            {"id": "D-001", "type": "door", "x": 2000, "y": 0, "width": 900, "angle": 0},
        ],
        "furniture": [],
        "rooms": [
            {"id": "R-001", "name": "客厅", "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}
        ],
    }


def test_health_lists_the_rules(server: str) -> None:
    with urllib.request.urlopen(f"{server}/health", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["ok"] is True
    assert "geometry.wall_gap" in payload["rules"]


def test_check_drawing_endpoint_returns_diagnostics(server: str) -> None:
    payload = _post(server, "/check_drawing", {"ir": _plan()})
    assert "diagnostics" in payload
    assert isinstance(payload["checked"], int)


def test_check_drawing_honours_changed_ids(server: str) -> None:
    payload = _post(
        server,
        "/check_drawing",
        {"ir": _plan(), "changed_ids": ["W-001"]},
    )
    assert payload["incremental"] is True


def test_takeoff_endpoint_returns_quantities(server: str) -> None:
    payload = _post(server, "/takeoff", {"ir": _plan()})
    assert payload["floor_area_m2"] == pytest.approx(12.0, abs=0.01)
    assert payload["quantities"]


def test_ops_endpoint_applies_operations(server: str) -> None:
    payload = _post(
        server,
        "/ops",
        {
            "ir": _plan(),
            "ops": [
                {
                    "op": "add",
                    "kind": "furniture",
                    "element": {"id": "F-001", "x": 100, "y": 100, "w": 500, "d": 400},
                }
            ],
        },
    )
    assert payload["applied"] == 1
    assert any(item["id"] == "F-001" for item in payload["ir"]["furniture"])


def test_unknown_path_returns_404(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(server, "/nope", {})
    assert excinfo.value.code == 404


def test_invalid_json_body_returns_400(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/check_drawing",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 400
