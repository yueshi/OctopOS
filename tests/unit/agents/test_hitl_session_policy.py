"""Thread-scoped HITL bypass policy."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from octop_harness.security.models import SecurityPolicy

from octop.config import OctopConfig
from octop.infra.agents.manager import AgentManager
from octop.infra.agents.security.hitl_session import (
    HitlSessionPolicy,
    HitlSessionPolicyStore,
    apply_session_bypass,
    current_hitl_thread_id,
    hitl_thread_scope,
    parse_hitl_session_policy,
    thread_id_from_interrupt_request,
    thread_id_from_request,
)


@pytest.fixture
def manager(tmp_path: Path) -> AgentManager:
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.services import build_shared_services
    from octop.infra.utils.paths import PathLayout

    paths = PathLayout(tmp_path / ".octop")
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    return AgentManager(repos=services.repos, paths=services.paths)


def test_parse_modes() -> None:
    assert parse_hitl_session_policy(None).mode == "ask"
    assert parse_hitl_session_policy('{"mode":"allow_all"}').mode == "allow_all"
    policy = parse_hitl_session_policy({"mode": "allow_tools", "tools": ["execute"]})
    assert policy == HitlSessionPolicy(mode="allow_tools", tools=("execute",))
    assert parse_hitl_session_policy({"mode": "allow_tools", "tools": []}).mode == "ask"


def test_allows_never_skips_ask_user() -> None:
    policy = HitlSessionPolicy(mode="allow_all")
    assert policy.allows("execute")
    assert not policy.allows("ask_user_question")
    tools = HitlSessionPolicy(mode="allow_tools", tools=("execute",))
    assert tools.allows("execute")
    assert not tools.allows("write_file")


def test_without_tools_returns_ask_when_empty() -> None:
    policy = HitlSessionPolicy(mode="allow_tools", tools=("execute", "write_file"))
    assert policy.without_tools(["write_file"]).tools == ("execute",)
    assert policy.without_tools(["execute", "write_file"]).mode == "ask"


def test_thread_id_from_request() -> None:
    assert thread_id_from_request({"thread_id": " thr_1 "}) == "thr_1"
    assert thread_id_from_request({"configurable": {"thread_id": "thr_2"}}) == "thr_2"
    assert thread_id_from_request({}) is None


def test_thread_id_from_interrupt_request_reads_runtime_config() -> None:
    assert current_hitl_thread_id() is None
    req = SimpleNamespace(runtime=SimpleNamespace(config={"configurable": {"thread_id": "thr_lg"}}))
    assert thread_id_from_interrupt_request(req) == "thr_lg"
    with hitl_thread_scope("thr_bound"):
        assert thread_id_from_interrupt_request(req) == "thr_bound"


def test_thread_id_from_interrupt_request_reads_langgraph_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langgraph.config as lg_config

    monkeypatch.setattr(
        lg_config,
        "get_config",
        lambda: {"configurable": {"thread_id": "thr_cfg"}},
    )
    assert current_hitl_thread_id() is None
    assert thread_id_from_interrupt_request(None) == "thr_cfg"


def test_current_thread_id_is_unbound_outside_scope() -> None:
    assert current_hitl_thread_id() is None
    with hitl_thread_scope("thr_1"):
        assert current_hitl_thread_id() == "thr_1"
    assert current_hitl_thread_id() is None


def test_wrap_skips_when_thread_allows_all() -> None:
    store = HitlSessionPolicyStore()
    store.set("thr_1", HitlSessionPolicy(mode="allow_all"))
    wrapped = apply_session_bypass({"execute": {}, "ask_user_question": {}}, store)
    assert wrapped is not None
    assert "when" not in wrapped["ask_user_question"]
    with hitl_thread_scope("thr_1"):
        assert wrapped["execute"]["when"](None) is False
    with hitl_thread_scope("other"):
        assert wrapped["execute"]["when"](None) is True


def test_wrap_never_skips_mandatory_interrupts() -> None:
    store = HitlSessionPolicyStore()
    store.set("thr_1", HitlSessionPolicy(mode="allow_all"))
    entry = {"allowed_decisions": ["approve", "reject"]}
    wrapped = apply_session_bypass({"app_delete": entry}, store)
    assert wrapped == {"app_delete": entry}


def test_wrap_allows_setup_tools_but_not_destructive_app_tools() -> None:
    store = HitlSessionPolicyStore()
    store.set(
        "thr_1",
        HitlSessionPolicy(
            mode="allow_tools",
            tools=("app_import_rows", "app_remove_service"),
        ),
    )
    entry = {"allowed_decisions": ["approve", "reject"]}
    wrapped = apply_session_bypass(
        {
            "app_import_rows": entry,
            "app_remove_service": entry,
            "app_delete_row": entry,
            "app_delete": entry,
        },
        store,
    )
    assert wrapped is not None
    with hitl_thread_scope("thr_1"):
        assert wrapped["app_import_rows"]["when"](None) is False
        assert wrapped["app_remove_service"]["when"](None) is False
        # Destructive app tools never gain a bypass predicate.
        assert "when" not in wrapped["app_delete_row"]
        assert "when" not in wrapped["app_delete"]
    with hitl_thread_scope("other"):
        assert wrapped["app_import_rows"]["when"](None) is True


def test_wrap_skips_execute_from_langgraph_config_without_scope() -> None:
    store = HitlSessionPolicyStore()
    store.set("thr_1", HitlSessionPolicy(mode="allow_all"))
    wrapped = apply_session_bypass({"execute": {}}, store)
    assert wrapped is not None
    assert current_hitl_thread_id() is None
    req = SimpleNamespace(runtime=SimpleNamespace(config={"configurable": {"thread_id": "thr_1"}}))
    assert wrapped["execute"]["when"](req) is False
    other = SimpleNamespace(
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "other"}})
    )
    assert wrapped["execute"]["when"](other) is True


def test_wrap_respects_original_when() -> None:
    store = HitlSessionPolicyStore()
    store.set("thr_1", HitlSessionPolicy(mode="ask"))
    wrapped = apply_session_bypass(
        {"write_file": {"when": lambda _req: False}},
        store,
    )
    assert wrapped is not None
    with hitl_thread_scope("thr_1"):
        assert wrapped["write_file"]["when"](None) is False


def test_store_set_persists_once() -> None:
    repo = MagicMock()
    store = HitlSessionPolicyStore(repo)
    store.set("thr_1", {"mode": "allow_tools", "tools": ["bash"]})
    repo.update_composer.assert_called_once_with(
        "thr_1", hitl_policy='{"mode": "allow_tools", "tools": ["bash"]}'
    )
    assert store.get("thr_1").allows("bash")
    repo.get.assert_not_called()


@pytest.mark.asyncio
async def test_resume_hitl_binds_thread_scope() -> None:
    seen: list[str | None] = []

    async def fake_resume(agent_id: str, thread_id: str, decisions: list[dict[str, Any]]) -> Any:
        seen.append(current_hitl_thread_id())
        if False:
            yield {}

    manager = AgentManager.__new__(AgentManager)
    manager._harness_manager = SimpleNamespace(resume_hitl=fake_resume)
    manager._repos = SimpleNamespace(hitl_repo=None)
    manager._history_backfills = {}
    manager._reclaim_pending = set()
    manager._reclaim_holds = {}
    manager._reclaim_task = None
    manager._reclaim_wake = None
    manager._invocation_waiters = {}
    manager._active_invocations = {}
    manager._thread_execution_locks = {}
    manager._bootstrap_graph_refresh_pending = set()

    chunks = [item async for item in manager.resume_hitl("agt", "thr_live", [{"type": "approve"}])]
    assert chunks == []
    assert seen == ["thr_live"]
    assert current_hitl_thread_id() is None


def test_build_harness_config_skips_interrupt_for_allowed_thread(
    manager: AgentManager,
) -> None:
    from tests.unit.agents.test_agent_manager import _row, _seed_test_provider

    _seed_test_provider(manager)
    manager._security.save(
        SecurityPolicy.from_dict({"hitl": {"enabled": True, "tools": ["write_file"]}})
    )
    store = HitlSessionPolicyStore()
    store.set("thr_1", HitlSessionPolicy(mode="allow_all"))
    manager.set_hitl_session_store(store)
    cfg = manager._build_harness_config(_row())
    assert cfg.interrupt_on is not None
    write_cfg = cfg.interrupt_on["write_file"]
    assert callable(write_cfg.get("when"))
    with hitl_thread_scope("thr_1"):
        assert write_cfg["when"](None) is False
    with hitl_thread_scope("other"):
        assert write_cfg["when"](None) is True

    # Setup tools may be remembered for this thread; they also carry the
    # session-bypass (and, for import/add_service, argument-preflight) `when`.
    for name in ("app_import_rows", "app_add_service", "app_remove_service"):
        entry = cfg.interrupt_on[name]
        assert callable(entry.get("when"))
        with hitl_thread_scope("thr_1"):
            assert entry["when"](None) is False
        with hitl_thread_scope("other"):
            assert entry["when"](None) is True

    # Destructive app tools stay mandatory even for an allow-all thread.
    for name in ("app_delete", "app_delete_row", "app_alter_schema"):
        entry = cfg.interrupt_on[name]
        assert "when" not in entry
        assert entry["allowed_decisions"] == ["approve", "reject"]
