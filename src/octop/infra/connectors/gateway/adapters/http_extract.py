"""Generic read-only HTTP source for light-app data sources.

No credentials: this connector is for public JSON/text/HTML endpoints (open
metadata APIs, status pages, public RSS-to-JSON bridges). Every hop is checked
against the SSRF guard and the response body is capped before it reaches a
source mapping. Service declarations may add a small allowlist of presentation
headers (User-Agent, Referer, Accept, Accept-Language) for sites that reject
the default client.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from octop_harness.security.ssrf import validate_agent_fetch_url

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 20.0
MAX_HEADER_VALUE_BYTES = 1024

ALLOWED_REQUEST_HEADERS: tuple[str, ...] = (
    "User-Agent",
    "Referer",
    "Accept",
    "Accept-Language",
)
_HEADER_NAMES = {name.lower(): name for name in ALLOWED_REQUEST_HEADERS}


def normalize_request_headers(raw: object) -> dict[str, str]:
    """Validate the host-injected headers a service declaration may send.

    Only presentation headers are accepted. Credentials must go through the
    encrypted connector store, never through a plain service declaration.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("request headers must be an object")
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        canonical = _HEADER_NAMES.get(name.lower())
        if canonical is None:
            raise ValueError(f"request header {name!r} is not allowed")
        text = str(value).strip()
        if not text:
            continue
        if "\r" in text or "\n" in text:
            raise ValueError(f"request header {name!r} must not contain a newline")
        try:
            encoded = text.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError(f"request header {name!r} must be latin-1") from None
        if len(encoded) > MAX_HEADER_VALUE_BYTES:
            raise ValueError(f"request header {name!r} is too long")
        cleaned[canonical] = text
    return cleaned


TOOLS: list[dict[str, Any]] = [
    {
        "name": "fetch_json",
        "description": "GET a public JSON endpoint and return its body.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL to fetch."},
                "timeout": {"type": "number", "description": "Seconds, default 20."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_text",
        "description": "GET a public text endpoint (HTML is converted to markdown).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL to fetch."},
                "timeout": {"type": "number", "description": "Seconds, default 20."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_html",
        "description": "GET a public HTML page and return its raw HTML.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL to fetch."},
                "timeout": {"type": "number", "description": "Seconds, default 20."},
            },
            "required": ["url"],
        },
    },
]


def list_tools() -> list[dict[str, Any]]:
    return TOOLS


def call_tool(creds: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    del creds  # public endpoints do not use the connector credential store
    url = str(args.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")
    timeout = float(args.get("timeout") or DEFAULT_TIMEOUT)
    headers = normalize_request_headers(args.get("_headers"))
    response = _fetch(url, timeout=timeout, headers=headers)
    body = response.content
    if len(body) > MAX_BYTES:
        raise ValueError(f"response is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if name == "fetch_json":
        text = response.text
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"response is not valid JSON: {exc}") from None
        return json.dumps(payload, ensure_ascii=False)
    if name == "fetch_text":
        content_type = response.headers.get("content-type", "").lower()
        text = response.text
        if "html" in content_type:
            from markdownify import markdownify

            text = markdownify(text, heading_style="ATX").strip()
        return text[:200_000]
    if name == "fetch_html":
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and "xml" not in content_type:
            raise ValueError("response is not HTML")
        return response.text[:200_000]
    raise ValueError(f"unknown tool: {name}")


def probe_credentials(creds: dict[str, Any]) -> None:
    del creds
    return None


def _fetch(
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    current = validate_agent_fetch_url(url)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            validate_agent_fetch_url(current)
            response = client.get(current, headers=headers or None)
            if 300 <= response.status_code < 400:
                location = (response.headers.get("location") or "").strip()
                if not location:
                    return response
                current = str(response.url.join(location))
                continue
            response.raise_for_status()
            return response
    raise ValueError("too many redirects")
