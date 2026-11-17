"""Workspace HTML preview CSP / MIME helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from octop.api.routers.workspace_preview import (
    _csp_host,
    _html_csp,
    _is_inline,
    _media_type,
)


def _request(host: str) -> Any:
    return SimpleNamespace(headers={"host": host})


def test_valid_host_is_used_for_relative_assets() -> None:
    assert _csp_host(_request("127.0.0.1:8088")) == "127.0.0.1:8088"
    assert _csp_host(_request("example.com")) == "example.com"
    assert _csp_host(_request("[::1]:8088")) == "[::1]:8088"


def test_invalid_host_falls_back_to_strict_policy() -> None:
    assert _csp_host(_request("evil.example; script-src *")) == ""
    assert _csp_host(_request("bad host")) == ""


def test_html_csp_blocks_external_network() -> None:
    csp = _html_csp("")
    assert csp.startswith("sandbox allow-scripts allow-forms")
    assert "connect-src 'none'" in csp
    assert "img-src data: blob:" in csp
    assert "script-src 'unsafe-inline';" in csp
    assert "base-uri 'none'" in csp


def test_html_csp_allows_only_the_preview_host() -> None:
    csp = _html_csp("example.com:8088")
    assert "script-src 'unsafe-inline' example.com:8088;" in csp
    assert "img-src example.com:8088 data: blob:" in csp


def test_media_types_and_inline_rules() -> None:
    assert _media_type("text/html") == "text/html; charset=utf-8"
    assert _media_type("application/javascript") == "application/javascript; charset=utf-8"
    assert _media_type("image/png") == "image/png"
    assert _is_inline("text/css")
    assert _is_inline("image/svg+xml")
    assert not _is_inline("application/octet-stream")
