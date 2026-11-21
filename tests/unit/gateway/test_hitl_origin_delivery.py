"""Dashboard-resolved HITL resumes deliver their answer back to the origin IM."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.slash.dispatcher import SlashDispatcher


def _processor(gateway: object | None) -> GlobalProcessor:
    return GlobalProcessor(
        agent_manager=MagicMock(),
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        dispatcher=SlashDispatcher(),
        gateway=gateway,
    )


@pytest.mark.asyncio
async def test_deliver_resumed_reply_pushes_formatted_text_to_im() -> None:
    gateway = MagicMock()
    gateway.push_text_from_session = AsyncMock()
    processor = _processor(gateway)

    await processor.deliver_resumed_reply_to_origin(
        agent_id="agent-1",
        session_key="agent-1:weixin:wxid:dm",
        channel_type="weixin",
        text="**已打开**\nhttps://example.test/p/abc",
    )

    gateway.push_text_from_session.assert_awaited_once_with(
        "agent-1",
        "agent-1:weixin:wxid:dm",
        "已打开\nhttps://example.test/p/abc",
    )


@pytest.mark.asyncio
async def test_deliver_resumed_reply_skips_virtual_channels() -> None:
    gateway = MagicMock()
    gateway.push_text_from_session = AsyncMock()
    processor = _processor(gateway)

    await processor.deliver_resumed_reply_to_origin(
        agent_id="agent-1",
        session_key="agent-1:dashboard:1:dm",
        channel_type="dashboard",
        text="done",
    )

    gateway.push_text_from_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_resumed_reply_swallows_push_errors() -> None:
    gateway = MagicMock()
    gateway.push_text_from_session = AsyncMock(side_effect=ValueError("channel not found"))
    processor = _processor(gateway)

    await processor.deliver_resumed_reply_to_origin(
        agent_id="agent-1",
        session_key="agent-1:weixin:wxid:dm",
        channel_type="weixin",
        text="link",
    )
