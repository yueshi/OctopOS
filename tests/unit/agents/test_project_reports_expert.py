"""Contract and normalizer tests for the bundled project-reports expert."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from openpyxl import Workbook

from octop.infra.agents.experts.catalog import (
    ExpertCatalog,
    default_library_root,
    parse_task_examples,
)
from octop.infra.apps.models import parse_schema_payload, validate_html
from octop.infra.apps.preview import validate_preview_svg

EXPERT_DIR = default_library_root() / "project-reports"
TEMPLATE_DIR = EXPERT_DIR / "app_templates" / "project-reports"
INGEST_SCRIPT = EXPERT_DIR / "skills" / "pm-report-ingest" / "scripts" / "ingest.py"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _load_ingest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("project_reports_ingest", INGEST_SCRIPT)
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


INGEST = _load_ingest()


def _write_workbook(
    path: Path,
    rows: list[list[object]],
    *,
    sheet: str = "任务明细",
    header: list[str] | None = None,
) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(["项目周报"])
    worksheet.append(
        header
        or [
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
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_expert_package_files_and_manifest() -> None:
    for name in ("SOUL.md", "AGENTS.md", "manifest.json"):
        assert (EXPERT_DIR / name).is_file(), name
    for skill in (
        "pm-report-init",
        "pm-report-ingest",
        "pm-report-review",
        "pm-report-upgrade",
        "pm-report-archive",
    ):
        assert (EXPERT_DIR / "skills" / skill / "SKILL.md").is_file(), skill
    assert (EXPERT_DIR / "skills/pm-report-ingest/scripts/ingest.py").is_file()
    assert (EXPERT_DIR / "skills/pm-report-archive/scripts/archive.py").is_file()
    assert (EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json").is_file()
    assert (EXPERT_DIR / "skills/pm-report-ingest/references/tables.json").is_file()

    manifest = json.loads((EXPERT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "project-reports"
    assert manifest["prompt_files"] == ["SOUL.md", "AGENTS.md"]
    assert len(manifest["quick_prompts"]) == 4
    assert all(
        prompt["title"]["zh"] and prompt["title"]["en"] for prompt in manifest["quick_prompts"]
    )
    examples = parse_task_examples(manifest)
    assert examples is not None
    assert len(examples["zh"]) == len(examples["en"]) == 6


def test_expert_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()
    expert = catalog.get("project-reports")
    assert expert is not None
    assert expert.summary.label_zh == "项目台账助手"
    assert "skills/pm-report-ingest/SKILL.md" in expert.files
    assert "skills/pm-report-ingest/scripts/ingest.py" in expert.files
    assert "skills/pm-report-upgrade/SKILL.md" in expert.files
    assert "skills/pm-report-archive/scripts/archive.py" in expert.files
    assert "app_templates/project-reports/schema.json" in expert.files


def test_template_schema_and_html_are_valid() -> None:
    schema = json.loads((TEMPLATE_DIR / "schema.json").read_text(encoding="utf-8"))
    parsed = parse_schema_payload(schema)
    names = set(parsed.names)
    assert {
        "project",
        "period",
        "task_id",
        "task_name",
        "status",
        "progress",
        "health",
        "days_late",
        "batch_id",
    } <= names

    html = (TEMPLATE_DIR / "app.html").read_text(encoding="utf-8")
    validate_html(html)
    for token in ("listRows", "aggregate", "period_reports", "onDataChange", "main"):
        assert token in html, token
    assert "http://" not in html
    assert "https://" not in html

    preview = (TEMPLATE_DIR / "preview.svg").read_text(encoding="utf-8")
    validate_preview_svg(preview)


def test_tables_reference_matches_plan() -> None:
    tables = json.loads(
        (EXPERT_DIR / "skills/pm-report-ingest/references/tables.json").read_text(encoding="utf-8")
    )
    period = tables["period_reports"]
    assert period["key"] == "period_reports"
    assert period["public"] is False
    names = {column["name"] for column in period["columns"]}
    assert {"batch_id", "period", "source_file", "source_hash", "status"} <= names

    projects = tables["projects"]
    assert projects["key"] == "projects"
    assert projects["public"] is False
    project_names = {column["name"] for column in projects["columns"]}
    assert {"project", "project_name", "owner", "status"} <= project_names


def test_profile_defaults_load() -> None:
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    assert profile.status_map["进行中"] == "doing"
    assert profile.status_map["已完成"] == "done"
    assert profile.task_id_fallback == ("project", "task_name")
    assert "合计" in profile.skip_row_patterns[0].pattern
    assert "手机号" in profile.pii_columns


def test_periods_are_zero_padded() -> None:
    assert INGEST._normalize_period("2026-W5") == "2026-W05"
    assert INGEST._normalize_period("2026-w46") == "2026-W46"
    assert INGEST._normalize_period("2026-11-19") == "2026-11-19"
    assert INGEST._normalize_period("2026-11-19 00:00:00") == "2026-11-19"


def test_progress_parsing_is_not_over_eager() -> None:
    assert INGEST._parse_progress("80%") == 80
    assert INGEST._parse_progress("0.8") == 80
    assert INGEST._parse_progress("1") == 1
    assert INGEST._parse_progress("120") == 100


def test_shipped_sample_runs_end_to_end(tmp_path: Path) -> None:
    sample = EXPERT_DIR / "skills/pm-report-ingest/references/samples/A项目-2026-W46.csv"
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    summary = INGEST.run(
        [sample],
        profile=profile,
        out_dir=tmp_path / "out",
        now=NOW,
        root=sample.parent,
    )
    assert summary["tasks"] == 3
    assert summary["failed_rows"] == 0
    tasks = _read_csv(tmp_path / "out" / "tasks.csv")
    assert {row["task_id"] for row in tasks} == {"T-1", "T-2", "T-3"}


def test_normalizes_a_weekly_file(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_workbook(
        reports / "A项目-2026-W46.xlsx",
        [
            ["T-1", "接口联调", "张三", "进行中", "80%", "高", "2026-10-01", "2026-11-10", ""],
            ["T-2", "需求评审", "李四", "已完成", "100%", "中", "2026-10-02", "2026-11-20", ""],
            ["", "合计", "", "", "", "", "", "", ""],
        ],
    )
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    summary = INGEST.run(
        [reports],
        profile=profile,
        out_dir=tmp_path / "out",
        now=NOW,
        root=tmp_path,
    )
    assert summary["files"] == 1
    assert summary["tasks"] == 2
    assert summary["failed_rows"] == 0

    tasks = _read_csv(tmp_path / "out" / "tasks.csv")
    assert [row["task_id"] for row in tasks] == ["T-1", "T-2"]
    first = tasks[0]
    assert first["project"] == "A项目"
    assert first["period"] == "2026-W46"
    assert first["status"] == "doing"
    assert first["progress"] == "80"
    assert first["priority"] == "high"
    assert first["health"] == "delayed"
    assert first["days_late"] == "5"

    reports_rows = _read_csv(tmp_path / "out" / "period_reports.csv")
    assert len(reports_rows) == 1
    assert reports_rows[0]["project"] == "A项目"
    assert reports_rows[0]["period_type"] == "weekly"
    assert reports_rows[0]["status"] == "imported"
    assert reports_rows[0]["row_count"] == "2"


def test_combined_file_uses_row_dimensions(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_workbook(
        reports / "周报-2026-W46.xlsx",
        [
            ["A项目", "2026-W46", "T-1", "接口联调", "张三", "进行中", "50", "高", "", "", ""],
            ["B项目", "2026-W46", "T-2", "上线验收", "李四", "已完成", "100", "中", "", "", ""],
        ],
        header=[
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
        ],
    )
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    summary = INGEST.run(
        [reports],
        profile=profile,
        out_dir=tmp_path / "out",
        now=NOW,
        root=tmp_path,
    )
    assert summary["tasks"] == 2
    tasks = _read_csv(tmp_path / "out" / "tasks.csv")
    assert {row["project"] for row in tasks} == {"A项目", "B项目"}
    reports_rows = _read_csv(tmp_path / "out" / "period_reports.csv")
    assert reports_rows[0]["project"] == ""


def test_failed_rows_are_reported_without_dropping_the_file(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_workbook(
        reports / "A项目-2026-W46.xlsx",
        [
            ["T-1", "接口联调", "张三", "进行中", "80%", "高", "2026-10-01", "2026-11-10", ""],
            ["T-2", "需求评审", "李四", "未知状态", "50%", "中", "", "", ""],
        ],
    )
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    summary = INGEST.run(
        [reports],
        profile=profile,
        out_dir=tmp_path / "out",
        now=NOW,
        root=tmp_path,
    )
    assert summary["tasks"] == 1
    assert summary["failed_rows"] == 1
    assert "unknown status" in summary["failed"][0]["error"]
    reports_rows = _read_csv(tmp_path / "out" / "period_reports.csv")
    assert reports_rows[0]["status"] == "partial"


def test_duplicate_task_rows_are_collapsed(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_workbook(
        reports / "A项目-2026-W46.xlsx",
        [
            ["T-1", "接口联调", "张三", "进行中", "40%", "高", "", "", ""],
            ["T-1", "接口联调", "张三", "进行中", "80%", "高", "", "", ""],
        ],
    )
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    summary = INGEST.run(
        [reports], profile=profile, out_dir=tmp_path / "out", now=NOW, root=tmp_path
    )
    assert summary["tasks"] == 1
    assert any("duplicate task rows collapsed" in warning for warning in summary["warnings"])
    assert _read_csv(tmp_path / "out" / "period_reports.csv")[0]["row_count"] == "1"


def test_batch_id_is_stable_for_the_same_file(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_workbook(
        reports / "A项目-2026-W46.xlsx",
        [["T-1", "接口联调", "张三", "进行中", "80%", "高", "", "", ""]],
    )
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    first = INGEST.run(
        [reports], profile=profile, out_dir=tmp_path / "out1", now=NOW, root=tmp_path
    )
    second = INGEST.run(
        [reports], profile=profile, out_dir=tmp_path / "out2", now=NOW, root=tmp_path
    )
    assert first["tasks"] == second["tasks"] == 1
    row1 = _read_csv(tmp_path / "out1" / "period_reports.csv")[0]
    row2 = _read_csv(tmp_path / "out2" / "period_reports.csv")[0]
    assert row1["batch_id"] == row2["batch_id"]


def test_pii_columns_are_skipped_by_default(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "A项目-2026-W46.csv").write_text(
        "任务编号,任务名称,状态,手机号\nT-1,接口联调,进行中,13800000000\n",
        encoding="utf-8",
    )
    profile = INGEST.Profile.from_dict(
        {
            "version": 1,
            "file_pattern": "^(?P<project>.+?)-(?P<period>\\d{4}-W\\d{1,2})\\.csv$",
            "columns": {
                "任务编号": "task_id",
                "任务名称": "task_name",
                "状态": "status",
                "手机号": "notes",
            },
            "status_map": {"进行中": "doing"},
            "pii_columns": ["手机号"],
        }
    )
    summary = INGEST.run(
        [reports], profile=profile, out_dir=tmp_path / "out", now=NOW, root=tmp_path
    )
    assert summary["tasks"] == 1
    assert any("skipped PII column" in warning for warning in summary["warnings"])
    tasks = _read_csv(tmp_path / "out" / "tasks.csv")
    assert tasks[0]["notes"] == ""


def test_merged_header_cells_are_expanded(tmp_path: Path) -> None:
    path = tmp_path / "merged.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet["A1"] = "A项目"
    worksheet.merge_cells("A1:B1")
    worksheet["A2"] = "任务"
    worksheet["B2"] = "状态"
    worksheet["A3"] = "接口联调"
    worksheet["B3"] = "进行中"
    workbook.save(path)
    profile = INGEST.Profile.from_dict(
        {
            "version": 1,
            "sheet": "Sheet1",
            "columns": {"任务": "task_name", "状态": "status"},
            "status_map": {"进行中": "doing"},
        }
    )
    sheet, matrix = INGEST.read_matrix(path, profile)
    assert sheet == "Sheet1"
    assert matrix[0][0] == "A项目"
    assert matrix[0][1] == "A项目"


def test_gbk_csv_is_decoded(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "A项目-2026-W46.csv").write_bytes(
        "任务编号,任务名称,状态\nT-1,接口联调,进行中\n".encode("gb18030")
    )
    profile = INGEST.Profile.from_dict(
        {
            "version": 1,
            "file_pattern": "^(?P<project>.+?)-(?P<period>\\d{4}-W\\d{1,2})\\.csv$",
            "columns": {"任务编号": "task_id", "任务名称": "task_name", "状态": "status"},
            "status_map": {"进行中": "doing"},
        }
    )
    summary = INGEST.run(
        [reports], profile=profile, out_dir=tmp_path / "out", now=NOW, root=tmp_path
    )
    assert summary["tasks"] == 1
    assert _read_csv(tmp_path / "out" / "tasks.csv")[0]["task_name"] == "接口联调"


def test_missing_files_raise(tmp_path: Path) -> None:
    profile = INGEST.load_profile(
        EXPERT_DIR / "skills/pm-report-ingest/references/profile.default.json"
    )
    with pytest.raises(FileNotFoundError):
        INGEST.run([tmp_path / "empty"], profile=profile, out_dir=tmp_path / "out", root=tmp_path)
