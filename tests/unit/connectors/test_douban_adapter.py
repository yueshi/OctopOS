"""Unit tests for the public Douban Books gateway adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from octop.infra.connectors.gateway.adapters import douban

SAMPLE_HTML = """
<html>
<head>
  <meta property="og:title" content="三体" />
  <meta property="og:image" content="https://img3.doubanio.com/view/subject/l/public/s2768378.jpg" />
  <meta property="book:isbn" content="9787536692930" />
  <meta property="book:author" content="刘慈欣" />
  <script type="application/ld+json">
    {"@context":"http://schema.org","@type":"Book","name":"三体",
     "author":[{"@type":"Person","name":"刘慈欣"}],
     "isbn":"9787536692930","url":"https://book.douban.com/subject/2567698/"}
  </script>
</head>
<body>
  <div id="info">
    <span class="pl">出版社:</span> <a href="/press/2162">重庆出版社</a><br>
    <span class="pl">出版时间:</span> 2008-1<br>
    <span class="pl">页数:</span> 302<br>
    <span class="pl">定价:</span> 23.00<br>
    <span class="pl">装帧:</span> 平装<br>
  </div>
  <strong class="ll rating_num" property="v:average"> 8.9 </strong>
  <span property="v:votes">519,426</span>
  <div class="intro"><p>文化大革命如火如荼进行的同时。</p></div>
</body>
</html>
"""


def test_tools_are_read_only_book_operations() -> None:
    names = [tool["name"] for tool in douban.list_tools()]
    assert names == ["search_books", "get_book", "get_cover"]


def test_registry_exposes_douban_tools() -> None:
    from octop.infra.connectors.gateway.registry import mcp_tools_for_kind

    names = [tool["name"] for tool in mcp_tools_for_kind("douban")]
    assert names == ["search_books", "get_book", "get_cover"]


def test_search_books_parses_subject_suggest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        douban,
        "_get_json",
        lambda _url: [
            {
                "title": "三体",
                "url": "https://book.douban.com/subject/2567698/",
                "pic": "https://img3.doubanio.com/view/subject/s/public/s2768378.jpg",
                "author_name": "刘慈欣",
                "year": "2008",
                "type": "b",
                "id": "2567698",
            },
            {"title": "not a book", "type": "x", "id": "1"},
        ],
    )

    payload = json.loads(douban.search_books({"keyword": "三体", "limit": 5}))
    assert payload["count"] == 1
    assert payload["results"][0]["id"] == "2567698"
    assert payload["results"][0]["authors"] == ["刘慈欣"]
    assert payload["results"][0]["cover"].startswith("https://img3.doubanio.com/")


def test_get_book_parses_meta_info_and_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        douban,
        "_fetch",
        lambda _url: (
            SimpleNamespace(text=SAMPLE_HTML),
            "https://book.douban.com/subject/2567698/",
        ),
    )

    book = json.loads(douban.get_book({"isbn": "978-7-5366-9293-0"}))
    assert book["id"] == "2567698"
    assert book["title"] == "三体"
    assert book["authors"] == ["刘慈欣"]
    assert book["isbn"] == "9787536692930"
    assert book["publisher"] == "重庆出版社"
    assert book["publish_date"] == "2008-1"
    assert book["pages"] == "302"
    assert book["price"] == "23.00"
    assert book["binding"] == "平装"
    assert book["rating"] == 8.9
    assert book["votes"] == 519426
    assert "文化大革命" in book["summary"]
    assert book["cover"].endswith("s2768378.jpg")


def test_get_book_requires_a_target() -> None:
    with pytest.raises(ValueError, match="isbn, subject_id or url"):
        douban.get_book({})


def test_get_cover_accepts_only_public_douban_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(douban, "validate_agent_fetch_url", lambda url: url)
    payload = json.loads(
        douban.get_cover({"url": "https://img3.doubanio.com/view/subject/s/public/s2768378.jpg"})
    )
    assert payload["host"] == "img3.doubanio.com"

    with pytest.raises(ValueError, match="public Douban cover"):
        douban.get_cover({"url": "https://evil.example/cover.jpg"})


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        douban.call_tool({}, "write_book", {})
