"""Risk tiers and least-privilege filtering for MiniApp tools."""

from __future__ import annotations

from types import SimpleNamespace

from octop.infra.agents.security.hitl_session import (
    FORCED_HITL_TOOLS,
    MANDATORY_HITL_TOOLS,
)
from octop.infra.apps.tool_policy import (
    APP_HITL_TOOL_NAMES,
    APP_MANDATORY_HITL_TOOLS,
    APP_SETUP_HITL_TOOLS,
    APP_TOOL_CATEGORIES,
    APP_TOOL_RISKS,
    RISK_READ,
    RISK_SENSITIVE,
    filter_app_tools,
)
from octop.infra.apps.tools import APP_TOOL_NAMES


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_every_app_tool_has_risk_and_group() -> None:
    assert set(APP_TOOL_NAMES) == set(APP_TOOL_RISKS)
    assert set(APP_TOOL_NAMES) == set(APP_TOOL_CATEGORIES)


def test_sensitive_tools_require_hitl() -> None:
    # HITL tools are forced even when the admin switch is off; only the setup
    # subset may be remembered per thread. HITL is no longer derived from the
    # sensitive risk tier, so owner-reversible tools can skip the card while
    # staying filtered as sensitive for tool attachment.
    assert APP_HITL_TOOL_NAMES <= FORCED_HITL_TOOLS
    sensitive_tools = {name for name, risk in APP_TOOL_RISKS.items() if risk == RISK_SENSITIVE}
    assert sensitive_tools >= APP_HITL_TOOL_NAMES
    assert APP_SETUP_HITL_TOOLS <= APP_HITL_TOOL_NAMES
    assert APP_MANDATORY_HITL_TOOLS == APP_HITL_TOOL_NAMES - APP_SETUP_HITL_TOOLS
    assert {"app_delete"} | APP_MANDATORY_HITL_TOOLS == MANDATORY_HITL_TOOLS
    assert {
        "app_delete",
        "app_alter_schema",
        "app_delete_row",
        "app_set_table_visibility",
        "app_integration_create_ingress",
        "app_integration_update_ingress",
        "app_integration_test_ingress",
        "app_integration_create_webhook",
        "app_integration_update_webhook",
    } <= APP_HITL_TOOL_NAMES
    assert {
        "app_import_rows",
        "app_add_service",
        "app_remove_service",
    } == APP_SETUP_HITL_TOOLS
    assert {
        "app_set_capabilities",
        "app_integration_delete_ingress",
        "app_integration_delete_webhook",
        "app_integration_test_webhook",
    }.isdisjoint(APP_HITL_TOOL_NAMES)
    assert APP_TOOL_RISKS["app_set_capabilities"] == RISK_SENSITIVE
    assert "app_update_row" not in APP_HITL_TOOL_NAMES


def test_read_only_limit_keeps_only_read_tools() -> None:
    tools = [_Tool(name) for name in APP_TOOL_NAMES]
    filtered = filter_app_tools(tools, {"app_tool_max_risk": "read"})
    assert filtered
    assert all(APP_TOOL_RISKS[tool.name] == RISK_READ for tool in filtered)
    assert "app_read_data" in {tool.name for tool in filtered}
    assert "app_append_row" not in {tool.name for tool in filtered}
    assert "app_alter_schema" not in {tool.name for tool in filtered}


def test_group_filter_and_empty_deny() -> None:
    tools = [_Tool(name) for name in APP_TOOL_NAMES]
    data = filter_app_tools(tools, {"app_tool_groups": ["data"]})
    assert {tool.name for tool in data} == {
        name for name in APP_TOOL_NAMES if APP_TOOL_CATEGORIES[name] == "data"
    }
    assert filter_app_tools(tools, {"app_tool_groups": []}) == []


def test_invalid_risk_fails_closed() -> None:
    tools = [_Tool(name) for name in APP_TOOL_NAMES]
    filtered = filter_app_tools(tools, {"app_tool_max_risk": "nonsense"})
    assert all(APP_TOOL_RISKS[tool.name] == RISK_READ for tool in filtered)
    assert SimpleNamespace(name="app_append_row").name not in {tool.name for tool in filtered}
