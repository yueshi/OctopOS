"""Persisted HITL decisions must survive reload as transcript cards."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.gateway.process.processor import GlobalProcessor


class _FakeRepo:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def append_if_ready(self, thread_id: str, messages: list[Any]) -> int:
        _ = thread_id
        self.items.extend(messages)
        return len(messages)


def _record() -> SimpleNamespace:
    return SimpleNamespace(
        pending_id="p1",
        action_requests=[{"name": "execute", "args": {"cmd": "ls"}, "description": "Run ls"}],
        review_configs=[{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
    )


@pytest.mark.asyncio
async def test_record_hitl_decision_appends_resolved_card() -> None:
    processor = GlobalProcessor.__new__(GlobalProcessor)
    repo = _FakeRepo()
    processor._thread_message_repo = repo  # type: ignore[attr-defined]

    await processor.record_hitl_decision(
        thread_id="thr1",
        record=_record(),
        decisions=[{"type": "approve"}],
        status="approved",
        policy={"mode": "allow_tools", "tools": ["execute"]},
    )

    assert len(repo.items) == 1
    item = repo.items[0]
    assert item.message_id == "hitl:p1"
    wire = json.loads(item.message_json)
    hitl = wire["data"]["additional_kwargs"]["octop_hitl"]
    assert hitl["status"] == "approved"
    assert hitl["resolution"] == "allow_tool"
    assert hitl["pending_id"] == "p1"
    assert hitl["action_requests"][0]["name"] == "execute"


@pytest.mark.asyncio
async def test_record_hitl_decision_keeps_rejection_reason() -> None:
    processor = GlobalProcessor.__new__(GlobalProcessor)
    repo = _FakeRepo()
    processor._thread_message_repo = repo  # type: ignore[attr-defined]

    await processor.record_hitl_decision(
        thread_id="thr1",
        record=_record(),
        decisions=[{"type": "reject", "message": "not this time"}],
        status="rejected",
    )

    wire = json.loads(repo.items[0].message_json)
    hitl = wire["data"]["additional_kwargs"]["octop_hitl"]
    assert hitl["status"] == "rejected"
    assert "resolution" not in hitl
    assert hitl["message"] == "not this time"
