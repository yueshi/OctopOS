"""Unit tests for the credential-less HTTP source adapter."""

from __future__ import annotations

import pytest
from octop_harness.security.ssrf import UnsafeAgentFetchUrlError

from octop.infra.connectors.gateway.adapters import http_extract


def test_lists_public_fetch_tools() -> None:
    names = [tool["name"] for tool in http_extract.list_tools()]
    assert names == ["fetch_json", "fetch_text", "fetch_html"]
    assert all("url" in tool["inputSchema"]["required"] for tool in http_extract.list_tools())


def test_rejects_private_targets_before_network() -> None:
    with pytest.raises(UnsafeAgentFetchUrlError):
        http_extract.call_tool({}, "fetch_json", {"url": "http://127.0.0.1:8080/x"})


def test_normalizes_allowlisted_request_headers() -> None:
    assert http_extract.normalize_request_headers(
        {
            "user-agent": "Mozilla/5.0",
            "Referer": "https://book.douban.com/",
            "Accept-Language": "zh-CN",
        }
    ) == {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://book.douban.com/",
        "Accept-Language": "zh-CN",
    }


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer nope"},
        {"User-Agent": "a\r\nb"},
        {"User-Agent": "中文"},
        {"User-Agent": "x" * (http_extract.MAX_HEADER_VALUE_BYTES + 1)},
    ],
)
def test_rejects_unsafe_request_headers(headers: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        http_extract.normalize_request_headers(headers)


class _Response:
    status_code = 200
    url = "https://example.com/page"

    def __init__(self, *, text: str, content_type: str) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None


def test_call_passes_declared_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_fetch(
        url: str,
        *,
        timeout: float,
        headers: object = None,
    ) -> _Response:
        seen["url"] = url
        seen["headers"] = headers
        return _Response(text="{}", content_type="application/json")

    monkeypatch.setattr(http_extract, "_fetch", fake_fetch)
    http_extract.call_tool(
        {},
        "fetch_json",
        {
            "url": "https://example.com/data",
            "_headers": {"user-agent": "Mozilla/5.0"},
        },
    )
    assert seen == {
        "url": "https://example.com/data",
        "headers": {"User-Agent": "Mozilla/5.0"},
    }


def test_fetch_html_returns_raw_html(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><head><title>三体</title></head><body>ok</body></html>"
    monkeypatch.setattr(
        http_extract,
        "_fetch",
        lambda url, *, timeout, headers=None: _Response(
            text=html, content_type="text/html; charset=utf-8"
        ),
    )
    assert http_extract.call_tool({}, "fetch_html", {"url": "https://example.com/book"}) == html


def test_fetch_html_rejects_non_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        http_extract,
        "_fetch",
        lambda url, *, timeout, headers=None: _Response(text="{}", content_type="application/json"),
    )
    with pytest.raises(ValueError, match="not HTML"):
        http_extract.call_tool({}, "fetch_html", {"url": "https://example.com/data"})
