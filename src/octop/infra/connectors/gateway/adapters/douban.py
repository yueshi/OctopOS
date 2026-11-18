"""Douban Books gateway — public, read-only book search and metadata.

Douban has no official open book API. This adapter uses the public pages that
the web client itself calls:

- ``GET https://book.douban.com/j/subject_suggest?q=...`` for search suggestions
- ``GET https://book.douban.com/subject/{id}/`` (or ``/isbn/{isbn}/``) for detail

It is intentionally conservative: browser-like headers, small timeouts, a
response-size cap, no crawling/looping beyond a few redirects, and no writes.
Callers should cache results (the MiniApp service layer already does) and obey
Douban's robots/ToS for their deployment.

``get_cover`` only validates and normalizes a public Douban image URL. Image
bytes are fetched by the MiniApp host's ``service(name).image(url)`` path,
which applies the same SSRF and size guards and stores an asset.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from octop_harness.security.ssrf import validate_agent_fetch_url

BASE_URL = "https://book.douban.com"
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 4
DEFAULT_TIMEOUT = 15.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36 OctopConnector/0.1"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": f"{BASE_URL}/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}

_SUBJECT_RE = re.compile(r"book\.douban\.com/subject/(\d+)")
_ISBN_RE = re.compile(r"^\d{9}[\dXx]$|^\d{13}$")
_JSON_LD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_META_RE = re.compile(
    r'<meta[^>]+property="(?P<name>[^"]+)"[^>]+content="(?P<value>[^"]*)"',
    re.IGNORECASE,
)
_INFO_RE = re.compile(r'<div id="info"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
_INTRO_RE = re.compile(r'<div class="intro"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
_RATING_RE = re.compile(
    r'<strong[^>]+property="v:average"[^>]*>\s*([\d.]+)\s*</strong>',
    re.IGNORECASE,
)
_VOTES_RE = re.compile(
    r'<span[^>]+property="v:votes"[^>]*>\s*([\d,]+)\s*</span>',
    re.IGNORECASE,
)
_TAG_RE = re.compile(
    r'<a[^>]+class="[^"]*\btag\b[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

COVER_URL_PREFIXES: tuple[str, ...] = (
    "https://img1.doubanio.com",
    "https://img2.doubanio.com",
    "https://img3.doubanio.com",
    "https://img9.doubanio.com",
    "https://img1.douban.com",
    "https://img2.douban.com",
    "https://img3.douban.com",
)
"""Public cover hosts; used as the default service URL allowlist."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_books",
        "description": "Search public Douban Books metadata by keyword.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Book title, author or ISBN fragment.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results, 1-20, default 10.",
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_book",
        "description": "Get one public Douban book by ISBN, subject id or subject URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "isbn": {"type": "string", "description": "ISBN-10 or ISBN-13."},
                "subject_id": {"type": "string", "description": "Douban subject id."},
                "url": {
                    "type": "string",
                    "description": "https://book.douban.com/subject/{id}/ URL.",
                },
            },
        },
    },
    {
        "name": "get_cover",
        "description": "Validate and normalize a public Douban cover image URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "https://img*.doubanio.com/... cover URL.",
                },
            },
            "required": ["url"],
        },
    },
]


def list_tools() -> list[dict[str, Any]]:
    return TOOLS


def call_tool(creds: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    del creds  # public pages, no credentials
    if name == "search_books":
        return search_books(args)
    if name == "get_book":
        return get_book(args)
    if name == "get_cover":
        return get_cover(args)
    raise ValueError(f"unknown tool: {name}")


def probe_credentials(creds: dict[str, Any]) -> None:
    del creds
    search_books({"keyword": "三体", "limit": 1})


def search_books(args: dict[str, Any]) -> str:
    keyword = str(args.get("keyword") or "").strip()
    if not keyword:
        raise ValueError("keyword is required")
    limit = max(1, min(int(args.get("limit") or 10), 20))
    url = f"{BASE_URL}/j/subject_suggest?q={quote(keyword)}"
    payload = _get_json(url)
    if not isinstance(payload, list):
        raise ValueError("Douban search did not return a list; the page may be blocked")
    results: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or str(item.get("type") or "") != "b":
            continue
        subject_id = str(item.get("id") or "").strip()
        title = _clean(item.get("title"))
        if not subject_id or not title:
            continue
        results.append(
            {
                "id": subject_id,
                "title": title,
                "authors": _authors_from_suggest(item.get("author_name")),
                "year": _clean(item.get("year")),
                "url": f"{BASE_URL}/subject/{subject_id}/",
                "cover": _clean(item.get("pic")),
            }
        )
        if len(results) >= limit:
            break
    return json.dumps(
        {"query": keyword, "count": len(results), "results": results},
        ensure_ascii=False,
    )


def get_book(args: dict[str, Any]) -> str:
    target = _book_target(args)
    response, final_url = _fetch(target)
    html = response.text
    subject_id = _subject_id(final_url)
    if not subject_id:
        raise ValueError("Douban did not resolve to a book subject page")

    meta = _meta_map(html)
    json_ld = _json_ld(html)
    title = _clean(json_ld.get("name")) or _clean(meta.get("og:title"))
    if not title:
        raise ValueError("Douban book page has no title; the page may be blocked")

    authors = _json_ld_authors(json_ld)
    if not authors:
        authors = _authors_from_suggest(meta.get("book:author"))
    info = _info_fields(html)
    rating_text = _first_group(_RATING_RE, html)
    votes_text = _first_group(_VOTES_RE, html)
    summary = _strip_tags(_first_group(_INTRO_RE, html, flags=re.DOTALL))
    cover = _clean(meta.get("og:image"))
    if not cover:
        image = json_ld.get("image")
        if isinstance(image, str):
            cover = _clean(image)
        elif isinstance(image, dict):
            cover = _clean(image.get("url"))

    book = {
        "id": subject_id,
        "title": title,
        "authors": authors,
        "isbn": _clean(json_ld.get("isbn"))
        or _clean(meta.get("book:isbn"))
        or info.get("isbn", ""),
        "url": f"{BASE_URL}/subject/{subject_id}/",
        "cover": cover,
        "rating": _number(rating_text),
        "votes": _integer(votes_text),
        "publisher": info.get("publisher", ""),
        "publish_date": info.get("publish_date", ""),
        "pages": info.get("pages", ""),
        "price": info.get("price", ""),
        "binding": info.get("binding", ""),
        "series": info.get("series", ""),
        "summary": summary[:4000],
        "tags": _tags(html),
    }
    return json.dumps(book, ensure_ascii=False)


def get_cover(args: dict[str, Any]) -> str:
    url = _clean(args.get("url"))
    if not url:
        raise ValueError("url is required")
    normalized = _validate_cover_url(url)
    return json.dumps(
        {"url": normalized, "host": urlparse(normalized).hostname or ""},
        ensure_ascii=False,
    )


def _book_target(args: dict[str, Any]) -> str:
    url = _clean(args.get("url"))
    if url:
        return _validate_book_url(url)
    subject_id = _clean(args.get("subject_id"))
    if subject_id:
        if not subject_id.isdigit():
            raise ValueError("subject_id must be numeric")
        return f"{BASE_URL}/subject/{subject_id}/"
    isbn = _clean(args.get("isbn")).replace("-", "")
    if isbn:
        if not _ISBN_RE.fullmatch(isbn):
            raise ValueError("isbn must be a valid ISBN-10 or ISBN-13")
        return f"{BASE_URL}/isbn/{isbn}/"
    raise ValueError("one of isbn, subject_id or url is required")


def _validate_book_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "book.douban.com":
        raise ValueError("only https://book.douban.com/ URLs are allowed")
    if not _SUBJECT_RE.search(url) and not parsed.path.startswith("/isbn/"):
        raise ValueError("URL must be a Douban book subject or ISBN URL")
    validate_agent_fetch_url(url)
    return url


def _validate_cover_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host.endswith(".doubanio.com") or (host.startswith("img") and host.endswith(".douban.com"))
    ):
        raise ValueError("only public Douban cover URLs are allowed")
    validate_agent_fetch_url(url)
    return url


def _fetch(url: str) -> tuple[httpx.Response, str]:
    current = url
    with httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers=HEADERS,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _assert_public_douban_url(current)
            response = client.get(current)
            if 300 <= response.status_code < 400 and response.headers.get("location"):
                current = str(httpx.URL(current).join(response.headers["location"]))
                continue
            if response.status_code == 403:
                raise ValueError(
                    "Douban refused the request (403); try again later or configure a cache"
                )
            response.raise_for_status()
            if len(response.content) > MAX_BYTES:
                raise ValueError(f"Douban response is larger than {MAX_BYTES // (1024 * 1024)} MB")
            return response, current
    raise ValueError("too many Douban redirects")


def _get_json(url: str) -> Any:
    response, _ = _fetch(url)
    try:
        return json.loads(response.text)
    except ValueError as exc:
        raise ValueError(f"Douban did not return JSON: {exc}") from None


def _assert_public_douban_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "book.douban.com" or host.endswith(".douban.com")):
        raise ValueError(f"unexpected Douban redirect host: {host or url}")
    validate_agent_fetch_url(url)


def _subject_id(url: str) -> str:
    match = _SUBJECT_RE.search(url)
    return match.group(1) if match else ""


def _meta_map(html: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for match in _META_RE.finditer(html):
        meta[match.group("name")] = match.group("value")
    return meta


def _json_ld(html: str) -> dict[str, Any]:
    for match in _JSON_LD_RE.finditer(html):
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("@type") == "Book":
            return payload
    return {}


def _json_ld_authors(json_ld: dict[str, Any]) -> list[str]:
    raw = json_ld.get("author")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    authors: list[str] = []
    for item in raw:
        name = _clean(item.get("name")) if isinstance(item, dict) else _clean(item)
        if name and name not in authors:
            authors.append(name)
    return authors


def _authors_from_suggest(raw: object) -> list[str]:
    text = _clean(raw)
    if not text:
        return []
    return [item.strip() for item in text.split("/") if item.strip()]


def _info_fields(html: str) -> dict[str, str]:
    match = _INFO_RE.search(html)
    if not match:
        return {}
    info_html = match.group(1)
    labels = {
        "副标题": "subtitle",
        "原作名": "original_title",
        "译者": "translator",
        "出版社": "publisher",
        "出品方": "producer",
        "出版时间": "publish_date",
        "页数": "pages",
        "定价": "price",
        "装帧": "binding",
        "丛书": "series",
        "ISBN": "isbn",
    }
    output: dict[str, str] = {}
    for label, key in labels.items():
        pattern = re.compile(
            rf'<span class="pl">\s*{re.escape(label)}[:：]?\s*</span>\s*(.*?)(?:<br\s*/?>|</span>\s*<br)',
            re.DOTALL,
        )
        item = pattern.search(info_html)
        if item:
            value = _strip_tags(item.group(1))
            if value:
                output[key] = value
    return output


def _tags(html: str) -> list[str]:
    output: list[str] = []
    for match in _TAG_RE.finditer(html):
        tag = _strip_tags(match.group(1))
        if tag and tag not in output:
            output.append(tag)
        if len(output) >= 20:
            break
    return output


def _first_group(
    pattern: re.Pattern[str],
    text: str,
    *,
    flags: int = 0,
) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _clean(value: object) -> str:
    if value is None:
        return ""
    return _strip_tags(str(value))


def _strip_tags(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: str) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


__all__ = [
    "COVER_URL_PREFIXES",
    "call_tool",
    "get_book",
    "get_cover",
    "list_tools",
    "probe_credentials",
    "search_books",
]
