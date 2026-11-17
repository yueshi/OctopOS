"""Generated-file flattening for the desktop Files window."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from octop.infra.agents.threads.artifact import collect_recent_artifacts


def _row(
    thread_id: str,
    *,
    title: str = "",
    last_active: int = 0,
    artifacts: list[Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        thread_id=thread_id,
        title=title,
        last_active=last_active,
        artifacts=artifacts or [],
    )


def test_relative_artifact_becomes_workspace_relative(tmp_path: Path) -> None:
    rows = [
        _row(
            "thr_1",
            title="Report",
            last_active=10,
            artifacts=[{"path": "outbound/report.html", "agent_id": "agt_1"}],
        )
    ]
    items = collect_recent_artifacts(rows, tmp_path, default_agent_id="agt_1")
    assert len(items) == 1
    item = items[0]
    assert item.path == "outbound/report.html"
    assert item.name == "report.html"
    assert item.in_workspace is True
    assert item.thread_id == "thr_1"
    assert item.thread_title == "Report"
    assert item.agent_id == "agt_1"


def test_absolute_workspace_path_is_relativized(tmp_path: Path) -> None:
    absolute = tmp_path / "outbound" / "chart.png"
    rows = [
        _row(
            "thr_1",
            artifacts=[{"path": str(absolute), "agent_id": "agt_1"}],
        )
    ]
    items = collect_recent_artifacts(rows, tmp_path, default_agent_id="agt_1")
    assert items[0].path == "outbound/chart.png"
    assert items[0].in_workspace is True


def test_absolute_outside_workspace_stays_absolute(tmp_path: Path) -> None:
    outside = Path(tmp_path.anchor) / "octop-outside-report.html"
    rows = [
        _row(
            "thr_1",
            artifacts=[{"path": str(outside), "agent_id": "agt_1"}],
        )
    ]
    items = collect_recent_artifacts(rows, tmp_path, default_agent_id="agt_1")
    assert items[0].in_workspace is False
    assert items[0].path == outside.as_posix()


def test_newest_thread_and_newest_file_first(tmp_path: Path) -> None:
    rows = [
        _row(
            "thr_old",
            last_active=1,
            artifacts=[
                {"path": "outbound/old-a.html", "agent_id": "agt_1"},
                {"path": "outbound/old-b.html", "agent_id": "agt_1"},
            ],
        ),
        _row(
            "thr_new",
            last_active=2,
            artifacts=[{"path": "outbound/new.html", "agent_id": "agt_1"}],
        ),
    ]
    items = collect_recent_artifacts(rows, tmp_path, default_agent_id="agt_1")
    assert [item.path for item in items] == [
        "outbound/new.html",
        "outbound/old-b.html",
        "outbound/old-a.html",
    ]


def test_dedupe_and_limit(tmp_path: Path) -> None:
    artifact = {"path": "outbound/report.html", "agent_id": "agt_1"}
    rows = [
        _row("thr_2", last_active=2, artifacts=[artifact]),
        _row("thr_1", last_active=1, artifacts=[artifact]),
    ]
    items = collect_recent_artifacts(rows, tmp_path, default_agent_id="agt_1")
    assert len(items) == 1
    limited = collect_recent_artifacts(
        [
            _row(
                "thr_1",
                artifacts=[
                    {"path": "outbound/a.html", "agent_id": "agt_1"},
                    {"path": "outbound/b.html", "agent_id": "agt_1"},
                ],
            )
        ],
        tmp_path,
        default_agent_id="agt_1",
        limit=1,
    )
    assert [item.path for item in limited] == ["outbound/b.html"]


def test_thread_filter_and_system_paths(tmp_path: Path) -> None:
    rows = [
        _row(
            "thr_1",
            last_active=1,
            artifacts=[
                {"path": ".octop/auth/token", "agent_id": "agt_1"},
                {"path": ".env", "agent_id": "agt_1"},
                {"path": "_builtin_skills/x.html", "agent_id": "agt_1"},
                {"path": "outbound/keep.html", "agent_id": "agt_1"},
            ],
        ),
        _row(
            "thr_2",
            last_active=2,
            artifacts=[{"path": "outbound/other.html", "agent_id": "agt_1"}],
        ),
    ]
    items = collect_recent_artifacts(
        rows,
        tmp_path,
        default_agent_id="agt_1",
        thread_id="thr_1",
    )
    assert [item.path for item in items] == ["outbound/keep.html"]


def test_legacy_bare_path_is_normalized_under_workspace(tmp_path: Path) -> None:
    rows = [_row("thr_1", artifacts=["outbound/legacy.md"])]
    items = collect_recent_artifacts(rows, tmp_path, default_agent_id="agt_1")
    assert items[0].path == "outbound/legacy.md"
    assert items[0].agent_id == "agt_1"
    assert items[0].in_workspace is True
