"""Tests for security settings store."""

from __future__ import annotations

from unittest.mock import MagicMock

from octop_harness.middleware.filesystem_guard import filesystem_guard_block_reason
from octop_harness.security.models import SecurityPolicy

from octop.infra.agents.security import (
    SecuritySettingsStore,
    enforce_mandatory_filesystem_rule,
    tool_execution_may_pause,
)


def test_load_defaults_when_missing() -> None:
    repo = MagicMock()
    repo.get.return_value = None
    store = SecuritySettingsStore(settings_repo=repo)
    policy = store.load()
    assert policy.hitl.enabled is False
    assert policy.tool_guard.enabled is True
    assert policy.tool_guard.mode == "warn"
    assert policy.resolve_interrupt_on() is None


def test_save_round_trip() -> None:
    repo = MagicMock()
    repo.get.return_value = None
    store = SecuritySettingsStore(settings_repo=repo)
    policy = SecurityPolicy.from_dict({"hitl": {"enabled": False}})
    saved = store.save(policy)
    assert saved.hitl.enabled is False
    repo.set.assert_called_once()
    key, raw = repo.set.call_args[0]
    assert key == "security_policy"
    assert '"enabled": false' in raw or '"enabled": false' in raw.replace(" ", "")


def test_harness_policy_always_interrupts_mandatory_tools() -> None:
    repo = MagicMock()
    repo.get.return_value = None
    store = SecuritySettingsStore(settings_repo=repo)

    # Admin HITL is off: only the mandatory deletion gate survives.
    policy = store.harness_policy()
    assert policy.hitl.enabled is True
    assert policy.resolve_interrupt_on() == {
        "app_delete": {"allowed_decisions": ["approve", "reject"]}
    }

    # Admin HITL on with the default tool set keeps those tools and adds the gate.
    repo.get.return_value = '{"hitl": {"enabled": true, "tools": "default"}}'
    policy = store.harness_policy()
    tools = set(policy.resolve_interrupt_on() or {})
    assert {"bash", "write_file", "app_delete"} <= tools

    # Custom tool lists are preserved without duplicating the gate.
    repo.get.return_value = (
        '{"hitl": {"enabled": true, "tools": ["app_delete"],'
        ' "allowed_decisions": ["approve", "reject"]}}'
    )
    policy = store.harness_policy()
    assert policy.hitl.tools == ["app_delete"]


def test_harness_policy_always_denies_app_dir_writes() -> None:
    repo = MagicMock()
    repo.get.return_value = None
    store = SecuritySettingsStore(settings_repo=repo)

    policy = store.harness_policy()
    permissions = policy.resolve_permissions()
    assert permissions is not None

    blocked = filesystem_guard_block_reason(
        permissions,
        tool_name="write_file",
        params={"file_path": "apps/app_abc123/app.html"},
    )
    assert blocked is not None
    assert "/apps/app_abc123/app.html" in blocked

    # Reads stay available for debugging; unrelated workspace paths stay writable.
    assert (
        filesystem_guard_block_reason(
            permissions,
            tool_name="read_file",
            params={"file_path": "apps/app_abc123/app.html"},
        )
        is None
    )
    assert (
        filesystem_guard_block_reason(
            permissions,
            tool_name="write_file",
            params={"file_path": "notes/todo.md"},
        )
        is None
    )


def test_app_dir_deny_is_first_even_when_admin_disables_or_allows() -> None:
    repo = MagicMock()
    repo.get.return_value = (
        '{"filesystem": {"enabled": false, "rules": ['
        '{"operations": ["write"], "paths": ["/apps/**"], "mode": "allow"}]}}'
    )
    store = SecuritySettingsStore(settings_repo=repo)

    permissions = store.harness_policy().resolve_permissions()
    assert permissions is not None
    assert (
        filesystem_guard_block_reason(
            permissions,
            tool_name="write_file",
            params={"file_path": "apps/app_abc123/app.html"},
        )
        is not None
    )


def test_agent_override_cannot_remove_app_dir_deny() -> None:
    override = SecurityPolicy.from_dict(
        {
            "filesystem": {
                "enabled": False,
                "rules": [{"operations": ["write"], "paths": ["/apps/**"], "mode": "allow"}],
            }
        }
    )
    policy = enforce_mandatory_filesystem_rule(override)
    permissions = policy.resolve_permissions()
    assert permissions is not None
    assert (
        filesystem_guard_block_reason(
            permissions,
            tool_name="write_file",
            params={"file_path": "apps/app_abc123/app.html"},
        )
        is not None
    )


def test_tool_execution_may_pause() -> None:
    assert tool_execution_may_pause(SecurityPolicy.defaults()) is False
    assert tool_execution_may_pause(SecurityPolicy.from_dict({"hitl": {"enabled": True}})) is True
    assert (
        tool_execution_may_pause(
            SecurityPolicy.from_dict({"tool_guard": {"enabled": True, "mode": "require_approval"}})
        )
        is True
    )
    assert (
        tool_execution_may_pause(
            SecurityPolicy.from_dict({"tool_guard": {"enabled": False, "mode": "require_approval"}})
        )
        is False
    )
