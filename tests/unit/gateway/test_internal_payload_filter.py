"""Dashboard raw stream chunks must strip octop-memory's internal audit JSON."""

from __future__ import annotations

from octop.infra.gateway.process.processor import _strip_internal_memory_chunk

_RETENTION = (
    '{"matches":[{"atom_id":"a1","relation":"unrelated","document":"USER.md",'
    '"quote":"","confidence":"high","same_subject":false,"same_scope":false}]}'
)


def test_strips_retention_json_and_keeps_answer_text() -> None:
    chunk = {"type": "token", "content": _RETENTION + "答案"}
    assert _strip_internal_memory_chunk(chunk) == {"type": "token", "content": "答案"}


def test_drops_chunk_when_only_the_payload_was_streamed() -> None:
    assert _strip_internal_memory_chunk({"type": "token", "content": _RETENTION}) is None


def test_keeps_normal_token_chunks_untouched() -> None:
    chunk = {"type": "token", "content": "normal answer"}
    assert _strip_internal_memory_chunk(chunk) is chunk
