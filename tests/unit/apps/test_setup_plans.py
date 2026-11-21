"""Durable MiniApp setup plans: draft, dry-run, import, cancel."""

from __future__ import annotations

import io
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import openpyxl
import pytest
from deepagents.backends import FilesystemBackend
from langgraph.config import var_child_runnable_config
from octop_harness.backends import BackendWorkspace

from octop.infra.apps.service import AppService
from octop.infra.apps.tools import build_app_tools
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.app_setup_plans import (
    PLAN_CANCELLED,
    PLAN_DRY_RUN_READY,
    PLAN_IMPORTED,
    AppSetupPlanRepo,
)
from octop.infra.db.repos.apps import LightAppRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.utils.ulid import new_ulid

CSV = "title,author\n三体,刘慈欣\n活着,余华\n"


@dataclass
class World:
    service: AppService
    repo: LightAppRepo
    plan_repo: AppSetupPlanRepo
    workspace: BackendWorkspace
    agent_id: str
    user_id: int
    db: SqlitePool
    audit_repo: AuditRepo


def _main_columns(plan) -> list[dict[str, object]]:
    tables = plan.table.get("tables") or []
    return list(tables[0]["columns"]) if tables else list(plan.table.get("columns") or [])


class _Workspaces:
    def __init__(self, agent_id: str, workspace: BackendWorkspace) -> None:
        self._agent_id = agent_id
        self._workspace = workspace

    def workspace_for_agent(self, agent_id: str) -> BackendWorkspace | None:
        return self._workspace if agent_id == self._agent_id else None


@pytest.fixture
def world(tmp_path: Path) -> World:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    user_repo = UserRepo(db)
    user_id = user_repo.create(username="alice", password_hash="h", role="user")
    agent_repo = AgentRepo(db)
    agent_id = new_ulid()
    agent_repo.create(agent_id=agent_id, user_id=user_id, name="bot")
    root = tmp_path / "ws"
    root.mkdir()
    workspace = BackendWorkspace(FilesystemBackend(root_dir=str(root), virtual_mode=True), root)
    repo = LightAppRepo(db)
    plan_repo = AppSetupPlanRepo(db)
    service = AppService(
        app_repo=repo,
        agent_repo=agent_repo,
        user_repo=user_repo,
        workspaces=_Workspaces(agent_id, workspace),
        secret_repo=SecretRepo(db),
        audit_repo=AuditRepo(db),
        setup_plan_repo=plan_repo,
    )
    return World(
        service=service,
        repo=repo,
        plan_repo=plan_repo,
        workspace=workspace,
        agent_id=agent_id,
        user_id=user_id,
        db=db,
        audit_repo=AuditRepo(db),
    )


async def _draft(world: World):
    await world.workspace.awrite_text("inbound/imports/books.csv", CSV)
    return await world.service.propose_setup_plan(
        agent_id=world.agent_id,
        user_id=world.user_id,
        thread_id=None,
        path="inbound/imports/books.csv",
        name="读书记录",
        columns=[
            {"name": "title", "label": "书名", "type": "string"},
            {"name": "author", "label": "作者", "type": "string"},
        ],
    )


@pytest.mark.asyncio
async def test_propose_confirm_execute_is_durable_and_idempotent(world: World) -> None:
    plan = await _draft(world)
    assert plan.status == "draft"
    assert plan.report["sheets"]
    assert world.repo.get_by_setup_plan_id(plan.plan_id) is None

    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="读书记录",
        columns=_main_columns(plan),
    )
    assert ready.status == PLAN_DRY_RUN_READY
    assert ready.app_id
    assert ready.dry_run["would_import"] == 2
    app = world.repo.get(ready.app_id)
    assert app is not None and app.row_count == 0
    assert world.repo.get_by_setup_plan_id(plan.plan_id).app_id == ready.app_id

    # Confirming again must not create a second app.
    with pytest.raises(OctopError):
        await world.service.confirm_setup_plan(
            plan.plan_id,
            user_id=world.user_id,
            revision=ready.revision,
            name="读书记录",
            columns=_main_columns(plan),
        )

    imported = await world.service.execute_setup_plan(
        plan.plan_id, user_id=world.user_id, revision=ready.revision
    )
    assert imported.status == PLAN_IMPORTED
    assert imported.result["imported"] == 2
    assert world.repo.get(ready.app_id).row_count == 2

    # A second execute is rejected by the state machine (already imported).
    with pytest.raises(OctopError):
        await world.service.execute_setup_plan(
            plan.plan_id, user_id=world.user_id, revision=ready.revision
        )


@pytest.mark.asyncio
async def test_cancel_removes_the_empty_app(world: World) -> None:
    plan = await _draft(world)
    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="读书记录",
        columns=_main_columns(plan),
    )
    assert ready.app_id
    cancelled = await world.service.cancel_setup_plan(plan.plan_id, user_id=world.user_id)
    assert cancelled.status == PLAN_CANCELLED
    assert world.repo.get(ready.app_id) is None


@pytest.mark.asyncio
async def test_confirm_rejects_a_stale_revision(world: World) -> None:
    plan = await _draft(world)
    with pytest.raises(OctopError):
        await world.service.confirm_setup_plan(
            plan.plan_id,
            user_id=world.user_id,
            revision=plan.revision + 1,
            name="读书记录",
            columns=_main_columns(plan),
        )
    assert world.plan_repo.get(plan.plan_id).status == "draft"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_other_user_cannot_read_or_cancel(world: World) -> None:
    plan = await _draft(world)
    with pytest.raises(OctopError):
        await world.service.get_setup_plan(plan.plan_id, user_id=world.user_id + 999)
    with pytest.raises(OctopError):
        await world.service.cancel_setup_plan(plan.plan_id, user_id=world.user_id + 999)


@pytest.mark.asyncio
async def test_source_change_invalidates_the_plan(world: World) -> None:
    plan = await _draft(world)
    await world.workspace.awrite_text("inbound/imports/books.csv", "title,author\n新书,某人\n")
    with pytest.raises(OctopError):
        await world.service.confirm_setup_plan(
            plan.plan_id,
            user_id=world.user_id,
            revision=plan.revision,
            name="读书记录",
            columns=_main_columns(plan),
        )
    assert world.plan_repo.get(plan.plan_id).status == "draft"  # type: ignore[union-attr]


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


@pytest.mark.asyncio
async def test_active_plan_blocks_direct_app_create(world: World) -> None:
    ThreadRepo(world.db).insert(
        thread_id="thr_setup",
        agent_id=world.agent_id,
        user_id=world.user_id,
        channel_type="dashboard",
        session_key="setup",
    )
    await world.workspace.awrite_text("inbound/imports/books.csv", CSV)
    await world.service.propose_setup_plan(
        agent_id=world.agent_id,
        user_id=world.user_id,
        thread_id="thr_setup",
        path="inbound/imports/books.csv",
        name="读书记录",
        columns=[{"name": "title", "label": "书名", "type": "string"}],
    )
    create = next(tool for tool in build_app_tools(world.service) if tool.name == "app_create")
    with _configurable(
        agent_id=world.agent_id,
        user=str(world.user_id),
        thread_id="thr_setup",
    ):
        result = json.loads(
            await create.ainvoke(
                {"name": "另一个应用", "columns": [{"name": "title", "type": "string"}]}
            )
        )
    assert "setup plan" in result["error"]


@pytest.mark.asyncio
async def test_stale_confirmed_plan_can_retry_without_a_second_app(world: World) -> None:
    plan = await _draft(world)
    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="读书记录",
        columns=_main_columns(plan),
    )
    with world.db.transaction() as conn:
        conn.execute(
            "UPDATE app_setup_plans SET status = 'confirmed', updated_at = ? WHERE plan_id = ?",
            (int(time.time()) - 400, plan.plan_id),
        )
    retried = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=ready.revision,
        name="different name is ignored",
        columns=[{"name": "ignored", "label": "ignored", "type": "string"}],
    )
    assert retried.status == PLAN_DRY_RUN_READY
    assert retried.app_id == ready.app_id
    assert world.repo.get_by_setup_plan_id(plan.plan_id).app_id == ready.app_id  # type: ignore[union-attr]


def _multi_xlsx() -> bytes:
    workbook = openpyxl.Workbook()
    customers = workbook.active
    customers.title = "客户"
    customers.append(["name", "phone"])
    customers.append(["张三", "13800000000"])
    customers.append(["李四", "13900000000"])
    orders = workbook.create_sheet("订单")
    orders.append(["item", "amount"])
    orders.append(["纸杯", "12.5"])
    orders.append(["咖啡豆", "88"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_multi_sheet_plan_creates_and_imports_all_tables(world: World) -> None:
    await world.workspace.aupload_bytes("inbound/imports/crm.xlsx", _multi_xlsx())
    tables = [
        {
            "key": "main",
            "label": "客户",
            "sheet": "客户",
            "columns": [
                {"name": "name", "label": "姓名", "type": "string"},
                {"name": "phone", "label": "电话", "type": "string"},
            ],
            "include_pii": True,
        },
        {
            "key": "orders",
            "label": "订单",
            "sheet": "订单",
            "columns": [
                {"name": "item", "label": "商品", "type": "string"},
                {"name": "amount", "label": "金额", "type": "number"},
            ],
        },
    ]
    plan = await world.service.propose_setup_plan(
        agent_id=world.agent_id,
        user_id=world.user_id,
        thread_id=None,
        path="inbound/imports/crm.xlsx",
        name="客户订单",
        tables=tables,
    )
    assert [item["key"] for item in plan.table["tables"]] == ["main", "orders"]
    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="客户订单",
        tables=tables,
    )
    assert ready.status == PLAN_DRY_RUN_READY
    assert ready.dry_run["would_import"] == 4
    assert set(ready.dry_run["tables"]) == {"main", "orders"}
    imported = await world.service.execute_setup_plan(
        plan.plan_id, user_id=world.user_id, revision=ready.revision
    )
    assert imported.status == PLAN_IMPORTED
    assert imported.result["imported"] == 4
    app = world.repo.get(imported.app_id)
    assert app is not None
    assert app.row_count == 4
    assert [table.table_key for table in world.service.list_tables(app)] == ["main", "orders"]


@pytest.mark.asyncio
async def test_plan_capabilities_are_applied_after_import(world: World) -> None:
    await world.workspace.awrite_text("inbound/imports/books.csv", CSV)
    plan = await world.service.propose_setup_plan(
        agent_id=world.agent_id,
        user_id=world.user_id,
        thread_id=None,
        path="inbound/imports/books.csv",
        name="读书记录",
        columns=[
            {"name": "title", "label": "书名", "type": "string"},
            {"name": "author", "label": "作者", "type": "string"},
        ],
        capabilities=["data.read", "data.write"],
    )
    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="读书记录",
        columns=_main_columns(plan),
    )
    imported = await world.service.execute_setup_plan(
        plan.plan_id, user_id=world.user_id, revision=ready.revision
    )
    assert imported.status == PLAN_IMPORTED
    app = world.repo.get(imported.app_id)
    assert app is not None
    assert set(json.loads(app.capabilities_json)) == {"data.read", "data.write"}
    assert imported.result["steps"]["capabilities"] == "ok"


@pytest.mark.asyncio
async def test_plan_rejects_owner_only_capability_and_invalid_service(world: World) -> None:
    await world.workspace.awrite_text("inbound/imports/books.csv", CSV)
    columns = [
        {"name": "title", "label": "书名", "type": "string"},
        {"name": "author", "label": "作者", "type": "string"},
    ]
    with pytest.raises(OctopError):
        await world.service.propose_setup_plan(
            agent_id=world.agent_id,
            user_id=world.user_id,
            thread_id=None,
            path="inbound/imports/books.csv",
            name="读书记录",
            columns=columns,
            capabilities=["external.fetch"],
        )
    with pytest.raises(OctopError):
        await world.service.propose_setup_plan(
            agent_id=world.agent_id,
            user_id=world.user_id,
            thread_id=None,
            path="inbound/imports/books.csv",
            name="读书记录",
            columns=columns,
            services=[{"name": "douban", "provider": "connector", "allowed_tools": ["search"]}],
        )


@pytest.mark.asyncio
async def test_confirm_can_override_capabilities(world: World) -> None:
    plan = await _draft(world)
    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="读书记录",
        columns=_main_columns(plan),
        capabilities=["data.read"],
    )
    imported = await world.service.execute_setup_plan(
        plan.plan_id, user_id=world.user_id, revision=ready.revision
    )
    app = world.repo.get(imported.app_id)
    assert app is not None
    assert json.loads(app.capabilities_json) == ["data.read"]


@pytest.mark.asyncio
async def test_service_preflight_validates_tools_prefixes_and_duplicates(world: World) -> None:
    await world.workspace.awrite_text("inbound/imports/books.csv", CSV)
    columns = [
        {"name": "title", "label": "书名", "type": "string"},
        {"name": "author", "label": "作者", "type": "string"},
    ]
    valid = {
        "name": "papers",
        "provider": "external",
        "connector_kind": "http-extract",
        "allowed_tools": ["fetch_json"],
        "allowed_url_prefixes": ["https://api.example.com/"],
    }
    plan = await world.service.propose_setup_plan(
        agent_id=world.agent_id,
        user_id=world.user_id,
        thread_id=None,
        path="inbound/imports/books.csv",
        name="读书记录",
        columns=columns,
        services=[valid],
    )
    assert plan.table["services"][0]["name"] == "papers"
    assert plan.table["services"][0]["allowed_url_prefixes"] == ["https://api.example.com"]

    for bad in (
        {**valid, "allowed_url_prefixes": ["ftp://example.com"]},
        {**valid, "allowed_tools": ["not-a-tool"]},
        {**valid, "allowed_tools": []},
    ):
        with pytest.raises(OctopError):
            await world.service.propose_setup_plan(
                agent_id=world.agent_id,
                user_id=world.user_id,
                thread_id=None,
                path="inbound/imports/books.csv",
                name="读书记录",
                columns=columns,
                services=[bad],
            )
    with pytest.raises(OctopError):
        await world.service.propose_setup_plan(
            agent_id=world.agent_id,
            user_id=world.user_id,
            thread_id=None,
            path="inbound/imports/books.csv",
            name="读书记录",
            columns=columns,
            services=[valid, valid],
        )


@pytest.mark.asyncio
async def test_setup_plan_writes_propose_confirm_and_execute_audit(world: World) -> None:
    plan = await _draft(world)
    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="读书记录",
        columns=_main_columns(plan),
    )
    await world.service.execute_setup_plan(
        plan.plan_id, user_id=world.user_id, revision=ready.revision
    )
    actions = {row.action for row in world.audit_repo.query(target=plan.plan_id, limit=50)}
    assert {
        "miniapp.setup_plan.propose",
        "miniapp.setup_plan.confirm",
        "miniapp.setup_plan.execute",
    } <= actions


@pytest.mark.asyncio
async def test_expired_plan_is_persisted_and_audited(world: World) -> None:
    plan = await _draft(world)
    ready = await world.service.confirm_setup_plan(
        plan.plan_id,
        user_id=world.user_id,
        revision=plan.revision,
        name="读书记录",
        columns=_main_columns(plan),
    )
    with world.db.transaction() as conn:
        conn.execute(
            "UPDATE app_setup_plans SET expires_at = ? WHERE plan_id = ?",
            (int(time.time()) - 1, plan.plan_id),
        )
    expired = await world.service.get_setup_plan(plan.plan_id, user_id=world.user_id)
    assert expired.status == "expired"
    assert world.repo.get(ready.app_id) is None
    actions = {row.action for row in world.audit_repo.query(target=plan.plan_id, limit=50)}
    assert "miniapp.setup_plan.expire" in actions


@pytest.mark.asyncio
async def test_supersede_closes_the_plan_and_audits_it(world: World) -> None:
    plan = await _draft(world)
    superseded = await world.service.supersede_setup_plan(
        plan.plan_id,
        reason="app_create_from_template",
        app_id="app_other",
    )
    assert superseded is not None
    assert superseded.status == "superseded"
    actions = {row.action for row in world.audit_repo.query(target=plan.plan_id, limit=50)}
    assert "miniapp.setup_plan.supersede" in actions
