"""Global SecurityPolicy persistence in the settings table."""

from __future__ import annotations

import json
import logging
from typing import Any

from octop_harness.security.models import SecurityPolicy

from octop.infra.agents.security.hitl_session import MANDATORY_HITL_TOOLS
from octop.infra.db.repos.settings import SettingsRepo

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "security_policy"

# MiniApp workspace files are service-owned. ``app_*`` tools write through
# AppService, not through the generic filesystem tools, so denying writes here
# cannot break app creation/editing. Reads stay allowed: agents still inspect
# HTML/schema files with read_file when debugging.
MANDATORY_APP_DIR_DENY: dict[str, object] = {
    "operations": ["write"],
    "paths": ["/apps/app_*/**"],
    "mode": "deny",
}


def _with_mandatory_filesystem_rule(data: dict[str, Any]) -> dict[str, Any]:
    """Force the app-directory write deny rule to be first-match.

    Admin policy may add its own rules; the app-directory rule must stay
    ahead of them so ``write_file``/``edit_file``/``delete`` cannot rewrite
    ``apps/app_*`` files behind AppService's validation and audit.
    """
    filesystem = data.get("filesystem")
    if not isinstance(filesystem, dict):
        filesystem = {}
    raw_rules = filesystem.get("rules")
    rules: list[object] = list(raw_rules) if isinstance(raw_rules, list) else []
    rules = [
        rule
        for rule in rules
        if not (
            isinstance(rule, dict)
            and rule.get("paths") == MANDATORY_APP_DIR_DENY["paths"]
            and rule.get("operations") == MANDATORY_APP_DIR_DENY["operations"]
            and rule.get("mode") == MANDATORY_APP_DIR_DENY["mode"]
        )
    ]
    rules.insert(0, dict(MANDATORY_APP_DIR_DENY))
    filesystem["enabled"] = True
    filesystem["rules"] = rules
    data["filesystem"] = filesystem
    return data


def enforce_mandatory_filesystem_rule(policy: SecurityPolicy) -> SecurityPolicy:
    """Re-apply mandatory filesystem rules after agent-level policy merging."""
    return SecurityPolicy.from_dict(_with_mandatory_filesystem_rule(policy.to_dict()))


def _default_policy() -> SecurityPolicy:
    """Octop defaults when no ``security_policy`` row exists in settings."""
    data = SecurityPolicy.defaults().to_dict()
    data["hitl"]["enabled"] = False
    data["tool_guard"] = {"enabled": True, "mode": "warn"}
    return SecurityPolicy.from_dict(data)


class SecuritySettingsStore:
    """Read/write global :class:`SecurityPolicy` in the settings table."""

    def __init__(self, *, settings_repo: SettingsRepo) -> None:
        self._settings = settings_repo

    def load(self) -> SecurityPolicy:
        raw = self._settings.get(_SETTINGS_KEY)
        if not raw:
            return _default_policy()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("invalid security_policy JSON in settings; using defaults")
            return _default_policy()
        if not isinstance(data, dict):
            return _default_policy()
        return SecurityPolicy.from_dict(data)

    def save(self, policy: SecurityPolicy | dict[str, Any]) -> SecurityPolicy:
        resolved = (
            policy if isinstance(policy, SecurityPolicy) else SecurityPolicy.from_dict(policy)
        )
        self._settings.set(_SETTINGS_KEY, json.dumps(resolved.to_dict(), ensure_ascii=False))
        return resolved

    def harness_policy(self) -> SecurityPolicy:
        """Policy handed to ``HarnessAgentManager`` at boot.

        ``SecurityPolicy.apply_to_config`` *replaces* ``interrupt_on`` with
        ``resolve_interrupt_on()`` every time the harness registers or rebuilds
        an agent. Any interrupt Octop adds after that point (the mandatory
        ``app_delete`` gate, connector write approvals) is therefore lost.
        Mandatory gates must live in the policy itself so they survive that
        re-application even when admin HITL is off.
        """
        policy = self.load()
        data = _with_mandatory_filesystem_rule(policy.to_dict())
        resolved = SecurityPolicy.from_dict(data).resolve_interrupt_on() or {}
        missing = MANDATORY_HITL_TOOLS - resolved.keys()
        if missing:
            data["hitl"] = {
                **data["hitl"],
                "enabled": True,
                # Materialize the effective tool list: "default" is expanded inside
                # resolve_interrupt_on, which would otherwise drop the extra tools.
                "tools": sorted({*resolved, *missing}),
            }
        return SecurityPolicy.from_dict(data)


def tool_execution_may_pause(policy: SecurityPolicy) -> bool:
    """True when a tool call can pause for human approval.

    Covers the admin HITL switch and command-guard ``require_approval``.
    ``ask_user_question`` is a separate collaboration pause and is ignored here.
    """
    if policy.hitl.enabled:
        return True
    guard = policy.tool_guard
    return bool(guard.enabled and guard.mode == "require_approval")
