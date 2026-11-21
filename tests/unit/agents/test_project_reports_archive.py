"""Tests for the project-reports archive helper."""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from octop.infra.agents.experts.catalog import default_library_root

ARCHIVE_SCRIPT = (
    default_library_root() / "project-reports/skills/pm-report-archive/scripts/archive.py"
)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _load_archive() -> ModuleType:
    spec = importlib.util.spec_from_file_location("project_reports_archive", ARCHIVE_SCRIPT)
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


ARCHIVE = _load_archive()


def _write_export(path: Path, periods: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["project", "period", "task_id", "task_name", "health"],
            lineterminator="\n",
        )
        writer.writeheader()
        for index, period in enumerate(periods):
            writer.writerow(
                {
                    "project": "A项目",
                    "period": period,
                    "task_id": f"T-{index}",
                    "task_name": f"任务 {index}",
                    "health": "on_track",
                }
            )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_split_keeps_cutoff_and_newer(tmp_path: Path) -> None:
    source = tmp_path / "export.csv"
    _write_export(source, ["2026-W44", "2026-W45", "2026-W46", "2026-W47"])
    summary = ARCHIVE.run(
        source,
        tmp_path / "out",
        keep_from="2026-W45",
        now=NOW,
    )
    assert summary["rows"] == 4
    assert summary["retained"] == 3
    assert summary["archived"] == 1
    assert summary["archived_periods"] == {"2026-W44": 1}
    retained = _read_csv(tmp_path / "out" / "tasks_retained.csv")
    archived = _read_csv(tmp_path / "out" / "tasks_archived.csv")
    assert [row["period"] for row in retained] == ["2026-W45", "2026-W46", "2026-W47"]
    assert [row["period"] for row in archived] == ["2026-W44"]
    assert list(retained[0]) == ["project", "period", "task_id", "task_name", "health"]


def test_week_labels_are_zero_padded(tmp_path: Path) -> None:
    source = tmp_path / "export.csv"
    _write_export(source, ["2026-W5", "2026-W46"])
    summary = ARCHIVE.run(source, tmp_path / "out", keep_from="2026-W05", now=NOW)
    assert summary["retained"] == 2
    assert summary["archived"] == 0
    assert summary["retained_periods"] == {"2026-W05": 1, "2026-W46": 1}


def test_mixed_period_kinds_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "export.csv"
    _write_export(source, ["2026-W46", "2026-11-19"])
    with pytest.raises(ValueError, match="does not match cutoff kind"):
        ARCHIVE.run(source, tmp_path / "out", keep_from="2026-W01", now=NOW)


def test_missing_period_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "export.csv"
    source.write_text("project,task_id\nA,T-1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no 'period' column"):
        ARCHIVE.run(source, tmp_path / "out", keep_from="2026-W01", now=NOW)


def test_empty_period_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "export.csv"
    source.write_text("project,period,task_id\nA,,T-1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 2 has an empty period"):
        ARCHIVE.run(source, tmp_path / "out", keep_from="2026-W01", now=NOW)


def test_gbk_export_is_decoded(tmp_path: Path) -> None:
    source = tmp_path / "export.csv"
    source.write_bytes("project,period,task_id\nA项目,2026-W46,T-1\n".encode("gb18030"))
    summary = ARCHIVE.run(source, tmp_path / "out", keep_from="2026-W01", now=NOW)
    assert summary["retained"] == 1
    assert _read_csv(tmp_path / "out" / "tasks_retained.csv")[0]["project"] == "A项目"
