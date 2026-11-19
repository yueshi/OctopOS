"""Risk tiers and least-privilege filtering for MiniApp agent tools.

Owner/admin permission is necessary but not sufficient: an agent may be given
only read access, or only the data/content subsets. ``app_tool_max_risk`` and
``app_tool_groups`` in the agent config narrow the attached tool list. Tools
marked ``sensitive`` also require HITL approval (see ``MANDATORY_HITL_TOOLS``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

RISK_READ = "read"
RISK_WRITE = "write"
RISK_SENSITIVE = "sensitive"
RISK_ORDER = {RISK_READ: 0, RISK_WRITE: 1, RISK_SENSITIVE: 2}

GROUP_CONTENT = "content"
GROUP_DATA = "data"
GROUP_INTEGRATION = "integration"
GROUP_SECURITY = "security"
GROUP_LIFECYCLE = "lifecycle"
APP_TOOL_GROUPS_ALL = (
    GROUP_CONTENT,
    GROUP_DATA,
    GROUP_INTEGRATION,
    GROUP_SECURITY,
    GROUP_LIFECYCLE,
)

# Read/reversible writes are safe to run directly; sensitive operations need
# explicit user approval because they can destroy data, expose it, or change
# the app's security posture.
APP_TOOL_RISKS: dict[str, str] = {
    "app_inspect_file": RISK_READ,
    "app_list": RISK_READ,
    "app_list_bindings": RISK_READ,
    "app_list_services": RISK_READ,
    "app_open": RISK_READ,
    "app_read_data": RISK_READ,
    "app_integration_list": RISK_READ,
    "app_integration_health": RISK_READ,
    "app_integration_trace": RISK_READ,
    "app_create": RISK_WRITE,
    "app_create_from_template": RISK_WRITE,
    "app_add_table": RISK_WRITE,
    "app_update_schema": RISK_WRITE,
    "app_write_html": RISK_WRITE,
    "app_append_row": RISK_WRITE,
    "app_update_row": RISK_WRITE,
    "app_save_asset": RISK_WRITE,
    "app_set_preview": RISK_WRITE,
    "app_alter_schema": RISK_SENSITIVE,
    "app_delete_row": RISK_SENSITIVE,
    "app_import_rows": RISK_SENSITIVE,
    "app_set_table_visibility": RISK_SENSITIVE,
    "app_set_capabilities": RISK_SENSITIVE,
    "app_add_service": RISK_SENSITIVE,
    "app_remove_service": RISK_SENSITIVE,
    "app_share_link": RISK_SENSITIVE,
    "app_integration_create_ingress": RISK_SENSITIVE,
    "app_integration_update_ingress": RISK_SENSITIVE,
    "app_integration_delete_ingress": RISK_SENSITIVE,
    "app_integration_test_ingress": RISK_SENSITIVE,
    "app_integration_create_webhook": RISK_SENSITIVE,
    "app_integration_update_webhook": RISK_SENSITIVE,
    "app_integration_delete_webhook": RISK_SENSITIVE,
    "app_integration_test_webhook": RISK_SENSITIVE,
    "app_delete": RISK_SENSITIVE,
}

APP_TOOL_CATEGORIES: dict[str, str] = {
    "app_inspect_file": GROUP_DATA,
    "app_list": GROUP_LIFECYCLE,
    "app_list_bindings": GROUP_INTEGRATION,
    "app_list_services": GROUP_INTEGRATION,
    "app_open": GROUP_CONTENT,
    "app_read_data": GROUP_DATA,
    "app_integration_list": GROUP_INTEGRATION,
    "app_integration_health": GROUP_INTEGRATION,
    "app_integration_trace": GROUP_INTEGRATION,
    "app_create": GROUP_LIFECYCLE,
    "app_create_from_template": GROUP_LIFECYCLE,
    "app_add_table": GROUP_CONTENT,
    "app_update_schema": GROUP_CONTENT,
    "app_write_html": GROUP_CONTENT,
    "app_append_row": GROUP_DATA,
    "app_update_row": GROUP_DATA,
    "app_save_asset": GROUP_CONTENT,
    "app_set_preview": GROUP_CONTENT,
    "app_alter_schema": GROUP_SECURITY,
    "app_delete_row": GROUP_DATA,
    "app_import_rows": GROUP_DATA,
    "app_set_table_visibility": GROUP_SECURITY,
    "app_set_capabilities": GROUP_SECURITY,
    "app_add_service": GROUP_INTEGRATION,
    "app_remove_service": GROUP_INTEGRATION,
    "app_share_link": GROUP_SECURITY,
    "app_integration_create_ingress": GROUP_INTEGRATION,
    "app_integration_update_ingress": GROUP_INTEGRATION,
    "app_integration_delete_ingress": GROUP_INTEGRATION,
    "app_integration_test_ingress": GROUP_INTEGRATION,
    "app_integration_create_webhook": GROUP_INTEGRATION,
    "app_integration_update_webhook": GROUP_INTEGRATION,
    "app_integration_delete_webhook": GROUP_INTEGRATION,
    "app_integration_test_webhook": GROUP_INTEGRATION,
    "app_delete": GROUP_LIFECYCLE,
}

APP_HITL_TOOL_NAMES = frozenset(
    name for name, risk in APP_TOOL_RISKS.items() if risk == RISK_SENSITIVE
)


def tool_risk(name: str) -> str:
    return APP_TOOL_RISKS.get(name, RISK_SENSITIVE)


def _allowed_groups(raw: object) -> set[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        values: Iterable[object] = raw.split(",")
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        logger.warning("ignoring invalid app_tool_groups=%r", raw)
        return set()
    allowed = {str(value).strip() for value in values if str(value).strip() in APP_TOOL_GROUPS_ALL}
    unknown = {
        str(value).strip()
        for value in values
        if str(value).strip() and str(value).strip() not in APP_TOOL_GROUPS_ALL
    }
    if unknown:
        logger.warning("ignoring unknown app_tool_groups values: %s", sorted(unknown))
    return allowed


def _max_risk(raw: object) -> str:
    value = str(raw or RISK_SENSITIVE).strip().lower()
    if value not in RISK_ORDER:
        logger.warning("invalid app_tool_max_risk=%r; falling back to read", raw)
        return RISK_READ
    return value


def filter_app_tools(tools: list[Any], cfg: Mapping[str, Any]) -> list[Any]:
    """Apply the agent's least-privilege policy to the built-in app tools.

    Omitted ``app_tool_groups`` means every group; omitted
    ``app_tool_max_risk`` preserves the historical all-tools behavior. An
    explicitly empty group list attaches no app tools.
    """
    groups = _allowed_groups(cfg.get("app_tool_groups"))
    limit = _max_risk(cfg.get("app_tool_max_risk"))
    allowed_rank = RISK_ORDER[limit]
    output: list[Any] = []
    for tool in tools:
        name = str(getattr(tool, "name", ""))
        if not name:
            continue
        risk = tool_risk(name)
        group = APP_TOOL_CATEGORIES.get(name, GROUP_CONTENT)
        if groups is not None and group not in groups:
            continue
        if RISK_ORDER[risk] > allowed_rank:
            continue
        output.append(tool)
    return output


__all__ = [
    "APP_HITL_TOOL_NAMES",
    "APP_TOOL_CATEGORIES",
    "APP_TOOL_GROUPS_ALL",
    "APP_TOOL_RISKS",
    "GROUP_CONTENT",
    "GROUP_DATA",
    "GROUP_INTEGRATION",
    "GROUP_LIFECYCLE",
    "GROUP_SECURITY",
    "RISK_ORDER",
    "RISK_READ",
    "RISK_SENSITIVE",
    "RISK_WRITE",
    "filter_app_tools",
    "tool_risk",
]
