"""Acting scope restored for tools invoked after a HITL resume."""

from __future__ import annotations

from octop.infra.agents.tool_scope import (
    current_tool_context,
    current_tool_scope,
    tool_scope,
)


def test_tool_scope_restores_channel_context() -> None:
    assert current_tool_scope() is None
    assert current_tool_context() is None

    with tool_scope(
        "agent-1",
        7,
        source="weixin/ch-1",
        session_key="agent-1:weixin:wx-1:dm",
        chat_type="dm",
        identity_verified=True,
    ):
        assert current_tool_scope() == ("agent-1", 7)
        context = current_tool_context()
        assert context is not None
        assert context.source == "weixin/ch-1"
        assert context.session_key == "agent-1:weixin:wx-1:dm"
        assert context.chat_type == "dm"
        assert context.identity_verified is True

    assert current_tool_scope() is None
    assert current_tool_context() is None
