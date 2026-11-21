"""End-to-end test for the bundled project-reports expert solution."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from openpyxl import Workbook

from octop.infra.agents.experts.catalog import default_library_root
from octop.infra.apps.wiring import build_app_service

EXPERT_DIR = default_library_root() / "project-reports"
SAMPLE = EXPERT_DIR / "skills/pm-report-ingest/references/samples/A项目-2026-W46.csv"
INGEST_SCRIPT = EXPERT_DIR / "skills/pm-report-ingest/scripts/ingest.py"
ARCHIVE_SCRIPT = EXPERT_DIR / "skills/pm-report-archive/scripts/archive.py"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _load_ingest() -> ModuleType:
    return _load_module("project_reports_e2e_ingest", INGEST_SCRIPT)


def _load_archive() -> ModuleType:
    return _load_module("project_reports_e2e_archive", ARCHIVE_SCRIPT)


def _write_weekly_xlsx(path: Path) -> None:
    """A real-world-ish workbook: title row, merged project cells, total row."""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "任务明细"
    sheet.append(["项目周报"])
    sheet.append(
        [
            "项目",
            "周期",
            "任务编号",
            "任务名称",
            "负责人",
            "状态",
            "进度",
            "优先级",
            "开始日期",
            "截止日期",
            "备注",
        ]
    )
    sheet.append(
        [
            "B项目",
            "2026-W47",
            "U-1",
            "数据迁移",
            "赵六",
            "进行中",
            "40%",
            "高",
            "2026-11-16",
            "2026-11-25",
            "",
        ]
    )
    sheet.append(
        [
            None,
            "2026-W47",
            "U-2",
            "安全评审",
            "钱七",
            "阻塞",
            "20%",
            "中",
            "2026-11-17",
            "2026-11-27",
            "",
        ]
    )
    sheet.merge_cells("A3:A4")
    sheet.append(["合计"] + [""] * 10)
    workbook.save(path)


async def test_project_reports_solution_end_to_end(env, tmp_path: Path) -> None:
    client, server, auth = env
    assert server.services is not None
    assert server.app_runtime is not None
    ingest = _load_ingest()

    experts = await client.get("/api/experts", headers=auth)
    assert experts.status_code == 200, experts.text
    card = next(
        (item for item in experts.json() if item["id"] == "project-reports"),
        None,
    )
    assert card is not None
    assert card["label"]["zh"] == "项目台账助手"
    assert len(card["quick_prompts"]) == 4
    assert len(card["task_examples"]["zh"]) == 6

    created = await client.post(
        "/api/agents/from-expert/project-reports",
        headers=auth,
        json={"name": "项目台账助手"},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    workspace = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
    assert workspace is not None
    template_html = await workspace.aread_text("app_templates/project-reports/app.html")
    assert template_html and "octopApp" in template_html
    script_text = await workspace.aread_text("skills/pm-report-ingest/scripts/ingest.py")
    assert script_text and "Normalize project weekly/daily report workbooks" in script_text
    for rel in (
        "SOUL.md",
        "AGENTS.md",
        "skills/pm-report-ingest/references/tables.json",
        "skills/pm-report-ingest/references/profile.default.json",
        "skills/pm-report-upgrade/SKILL.md",
        "skills/pm-report-archive/SKILL.md",
        "skills/pm-report-archive/scripts/archive.py",
    ):
        assert await workspace.aread_text(rel) is not None, rel

    app_resp = await client.post(
        "/api/apps/from-template",
        headers=auth,
        json={
            "agent_id": agent_id,
            "template": "project-reports",
            "source": "agent",
            "name": "项目报告台账",
        },
    )
    assert app_resp.status_code == 201, app_resp.text
    app_id = app_resp.json()["app_id"]

    tables = json.loads(
        (EXPERT_DIR / "skills/pm-report-ingest/references/tables.json").read_text(encoding="utf-8")
    )
    service = build_app_service(
        repos=server.services.repos,
        workspaces=server.app_runtime.agent_registry,
    )
    row = service.get(app_id)
    for table_def in (tables["period_reports"], tables["projects"]):
        await service.create_table(
            row,
            table_key=table_def["key"],
            label=table_def["label"],
            columns=table_def["columns"],
            public_visible=table_def["public"],
        )
    table_resp = await client.get(f"/api/apps/{app_id}/tables", headers=auth)
    assert table_resp.status_code == 200, table_resp.text
    assert {"main", "period_reports", "projects"} <= {
        item["table_key"] for item in table_resp.json()["tables"]
    }

    profile = ingest.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    shutil.copyfile(SAMPLE, reports_dir / SAMPLE.name)
    _write_weekly_xlsx(reports_dir / "B项目-2026-W47.xlsx")
    summary = ingest.run(
        [reports_dir],
        profile=profile,
        out_dir=tmp_path / "normalized",
        now=NOW,
        root=tmp_path,
    )
    assert summary["tasks"] == 5
    assert summary["failed_rows"] == 0
    assert summary["periods"] == ["2026-W46", "2026-W47"]
    await workspace.aupload_bytes(
        "reports/normalized/tasks.csv",
        (tmp_path / "normalized/tasks.csv").read_bytes(),
    )
    await workspace.aupload_bytes(
        "reports/normalized/period_reports.csv",
        (tmp_path / "normalized/period_reports.csv").read_bytes(),
    )

    imported = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=auth,
        json={
            "kind": "workspace_file",
            "path": "reports/normalized/tasks.csv",
            "upsert_key": ["project", "period", "task_id"],
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 5
    again = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=auth,
        json={
            "kind": "workspace_file",
            "path": "reports/normalized/tasks.csv",
            "upsert_key": ["project", "period", "task_id"],
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["imported"] == 0
    assert again.json()["updated"] == 5

    reports = await client.post(
        f"/api/apps/{app_id}/tables/period_reports/import",
        headers=auth,
        json={
            "kind": "workspace_file",
            "path": "reports/normalized/period_reports.csv",
            "upsert_key": ["batch_id"],
        },
    )
    assert reports.status_code == 200, reports.text
    assert reports.json()["imported"] == 2

    await workspace.awrite_text(
        "reports/normalized/projects.csv",
        "project,project_name,owner,status\nA项目,A 项目正式名称,项目经理甲,active\n",
    )
    projects = await client.post(
        f"/api/apps/{app_id}/tables/projects/import",
        headers=auth,
        json={
            "kind": "workspace_file",
            "path": "reports/normalized/projects.csv",
            "upsert_key": ["project"],
        },
    )
    assert projects.status_code == 200, projects.text
    assert projects.json()["imported"] == 1
    project_rows = await client.get(
        f"/api/apps/{app_id}/rows?table=projects&limit=10", headers=auth
    )
    assert project_rows.status_code == 200, project_rows.text
    assert project_rows.json()["rows"][0]["project_name"] == "A 项目正式名称"

    rows = await client.get(f"/api/apps/{app_id}/rows?table=main&limit=50", headers=auth)
    assert rows.status_code == 200, rows.text
    body = rows.json()
    assert body["total"] == 5
    by_id = {item["task_id"]: item for item in body["rows"]}
    assert by_id["T-1"]["health"] == "delayed"
    assert by_id["T-2"]["status"] == "done"
    assert by_id["U-1"]["health"] == "at_risk"
    assert by_id["U-2"]["health"] == "delayed"

    aggregate = await client.get(
        f"/api/apps/{app_id}/aggregate?table=main&group_by=project&agg=count",
        headers=auth,
    )
    assert aggregate.status_code == 200, aggregate.text
    groups = {item["key"]: item["count"] for item in aggregate.json()["groups"]}
    assert groups == {"A项目": 3, "B项目": 2}

    templates = await client.get(
        f"/api/apps/templates?agent_id={agent_id}&source=agent", headers=auth
    )
    assert templates.status_code == 200, templates.text
    template_ids = {item["template_id"] for item in templates.json()["templates"]}
    assert "project-reports" in template_ids
    preview = await client.get(
        f"/api/apps/templates/project-reports/preview?agent_id={agent_id}&source=agent",
        headers=auth,
    )
    assert preview.status_code == 200, preview.text
    assert "<svg" in preview.text

    published = await client.post(
        f"/api/apps/{app_id}/publication",
        headers=auth,
        json={"ttl_days": 7},
    )
    assert published.status_code == 200, published.text
    token = published.json()["token"]
    public_app = await client.get(f"/api/public/apps/{token}")
    assert public_app.status_code == 200, public_app.text
    public_tables = {item["table_key"] for item in public_app.json()["tables"]}
    assert "main" in public_tables
    assert "period_reports" not in public_tables
    assert "projects" not in public_tables
    public_rows = await client.get(f"/api/public/apps/{token}/rows?table=main&limit=50")
    assert public_rows.status_code == 200, public_rows.text
    assert public_rows.json()["total"] == 5
    private_rows = await client.get(f"/api/public/apps/{token}/rows?table=period_reports")
    assert private_rows.status_code == 404
    public_page = await client.get(f"/p/{token}")
    assert public_page.status_code == 200
    assert "项目报告台账" in public_page.text

    archive = _load_archive()
    export = await client.get(f"/api/apps/{app_id}/export?table=main", headers=auth)
    assert export.status_code == 200, export.text
    export_path = tmp_path / "archive" / "export.csv"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_bytes(export.content)
    split = archive.run(
        export_path,
        tmp_path / "archive-out",
        keep_from="2026-W47",
        now=NOW,
    )
    assert split["rows"] == 5
    assert split["retained"] == 2
    assert split["archived"] == 3
    assert split["archived_periods"] == {"2026-W46": 3}
    await workspace.aupload_bytes(
        "reports/normalized/tasks_retained.csv",
        (tmp_path / "archive-out/tasks_retained.csv").read_bytes(),
    )
    dry = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=auth,
        json={
            "kind": "workspace_file",
            "path": "reports/normalized/tasks_retained.csv",
            "mode": "replace",
            "dry_run": True,
        },
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["would_import"] == 2
    replaced = await client.post(
        f"/api/apps/{app_id}/tables/main/import",
        headers=auth,
        json={
            "kind": "workspace_file",
            "path": "reports/normalized/tasks_retained.csv",
            "mode": "replace",
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["imported"] == 2
    after = await client.get(f"/api/apps/{app_id}/rows?table=main&limit=50", headers=auth)
    assert after.status_code == 200, after.text
    assert after.json()["total"] == 2
    assert {row["period"] for row in after.json()["rows"]} == {"2026-W47"}
    public_after = await client.get(f"/api/public/apps/{token}/rows?table=main&limit=50")
    assert public_after.status_code == 200, public_after.text
    assert public_after.json()["total"] == 2
