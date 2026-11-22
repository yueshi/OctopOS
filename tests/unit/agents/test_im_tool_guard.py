"""IM sender gate for MiniApp tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.messages import ToolMessage

from octop.infra.agents.middleware import tool_audit
from octop.infra.agents.middleware.tool_audit import ToolAuditMiddleware
from octop.infra.apps.im_guard import im_tool_denial


def _request(tool: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool, "id": "call-1", "args": {}},
    )


def test_dashboard_and_cli_sources_are_trusted() -> None:
    for source in ("dashboard/1", "web/1", "cli", "cron", ""):
        assert (
            im_tool_denial(
                "app_read_data",
                source=source,
                chat_type="dm",
                owner_verified=False,
            )
            is None
        )


def test_link_tools_are_allowed_for_external_senders() -> None:
    for tool in ("app_share_link", "app_open"):
        assert (
            im_tool_denial(
                tool,
                source="feishu/ch1",
                chat_type="group",
                owner_verified=False,
            )
            is None
        )


def test_data_tools_require_owner_verified_dm() -> None:
    assert (
        im_tool_denial(
            "app_read_data",
            source="feishu/ch1",
            chat_type="group",
            owner_verified=True,
        )
        is not None
    )
    assert (
        im_tool_denial(
            "app_read_data",
            source="feishu/ch1",
            chat_type="dm",
            owner_verified=False,
        )
        is not None
    )
    assert (
        im_tool_denial(
            "app_read_data",
            source="feishu/ch1",
            chat_type="dm",
            owner_verified=True,
        )
        is None
    )


def test_middleware_denies_before_calling_the_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_audit,
        "_configurable",
        lambda: {
            "octop_source": "feishu/ch1",
            "octop_chat_type": "dm",
            "octop_owner_verified": False,
            "octop_locale": "en",
        },
    )
    middleware = ToolAuditMiddleware(audit_repo=MagicMock(), agent_id="agent-1")
    handler = MagicMock()
    result = middleware.wrap_tool_call(_request("app_read_data"), handler)
    handler.assert_not_called()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "not bound" in str(result.content)


def test_middleware_allows_owner_dm(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_audit,
        "_configurable",
        lambda: {
            "octop_source": "feishu/ch1",
            "octop_chat_type": "dm",
            "octop_owner_verified": True,
            "octop_locale": "en",
        },
    )
    middleware = ToolAuditMiddleware(audit_repo=MagicMock(), agent_id="agent-1")
    expected = ToolMessage(content="ok", tool_call_id="call-1", name="app_read_data")
    handler = MagicMock(return_value=expected)
    assert middleware.wrap_tool_call(_request("app_read_data"), handler) is expected
    handler.assert_called_once()
