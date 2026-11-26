"""Tests for the deterministic interior-design geometry kernel."""

from __future__ import annotations

import json
from typing import Any

import pytest

from octop.infra.design.checks import RULES, check_drawing
from octop.infra.design.ir import IR, ir_from_rows, parse_ir
from octop.infra.design.ops import apply_ops
from octop.infra.design.takeoff import takeoff


def _plan() -> dict[str, Any]:
    """A clean 4m x 3m living room with one door and one window."""
    return {
        "version": 1,
        "walls": [
            {"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
            {"id": "W-002", "x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120},
            {"id": "W-003", "x1": 4000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120},
            {"id": "W-004", "x1": 0, "y1": 3000, "x2": 0, "y2": 0, "t": 120},
        ],
        "openings": [
            {
                "id": "D-001",
                "type": "door",
                "x": 2000,
                "y": 0,
                "width": 900,
                "angle": 0,
                "host": "W-001",
            },
            {
                "id": "N-001",
                "type": "window",
                "x": 2500,
                "y": 3000,
                "width": 1500,
                "angle": 180,
                "host": "W-003",
            },
        ],
        "furniture": [
            {
                "id": "F-001",
                "x": 400,
                "y": 1600,
                "w": 2000,
                "d": 900,
                "rot": 0,
                "kind": "sofa",
                "name": "沙发",
            }
        ],
        "rooms": [
            {
                "id": "R-001",
                "name": "客厅",
                "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "area_mm2": 12000000,
            }
        ],
    }


def _rule_ids(ir: IR) -> list[str]:
    return [item.rule_id for item in check_drawing(ir)]


def _finding(ir: IR, rule_id: str) -> Any:
    for item in check_drawing(ir):
        if item.rule_id == rule_id:
            return item
    return None


def test_clean_plan_has_no_blocking_finding() -> None:
    ir = parse_ir(_plan())
    blocking = [
        item
        for item in check_drawing(ir)
        if item.severity == "blocking" and item.status == "non_compliant"
    ]
    assert blocking == []


def test_rule_registry_covers_every_reported_rule() -> None:
    ir = parse_ir(_plan())
    for item in check_drawing(ir):
        assert item.rule_id in RULES


def test_wall_gap_is_reported() -> None:
    plan = _plan()
    plan["walls"][0]["x2"] = 3950
    finding = _finding(parse_ir(plan), "geometry.wall_gap")
    assert finding is not None
    assert finding.severity == "blocking"
    assert finding.measured is not None and finding.measured == pytest.approx(50, abs=1)


def test_overlapping_walls_are_reported() -> None:
    plan = _plan()
    plan["walls"].append({"id": "W-005", "x1": 1000, "y1": 0, "x2": 2000, "y2": 0, "t": 120})
    finding = _finding(parse_ir(plan), "geometry.wall_overlap")
    assert finding is not None
    assert finding.measured == pytest.approx(1000, abs=1)


def test_opening_away_from_any_wall_is_reported() -> None:
    plan = _plan()
    plan["openings"][0]["x"] = 2000
    plan["openings"][0]["y"] = 1500
    finding = _finding(parse_ir(plan), "geometry.opening_not_in_wall")
    assert finding is not None
    assert finding.severity == "blocking"


def test_opening_past_the_wall_end_is_reported() -> None:
    plan = _plan()
    plan["openings"][1]["x"] = 500
    plan["openings"][1]["angle"] = 180
    finding = _finding(parse_ir(plan), "geometry.opening_past_wall_end")
    assert finding is not None
    assert finding.measured == pytest.approx(1000, abs=1)


def test_two_openings_overlapping_on_one_wall_are_reported() -> None:
    plan = _plan()
    plan["openings"].append(
        {
            "id": "N-002",
            "type": "window",
            "x": 2600,
            "y": 3000,
            "width": 1500,
            "angle": 180,
            "host": "W-003",
        }
    )
    finding = _finding(parse_ir(plan), "geometry.openings_overlap")
    assert finding is not None
    assert finding.measured == pytest.approx(1400, abs=1)


def test_overlapping_furniture_is_reported() -> None:
    plan = _plan()
    plan["furniture"].append(
        {"id": "F-002", "x": 600, "y": 1700, "w": 1000, "d": 600, "rot": 0, "kind": "table"}
    )
    finding = _finding(parse_ir(plan), "geometry.element_overlap")
    assert finding is not None
    assert set(finding.elements) == {"F-001", "F-002"}


def test_wardrobe_without_front_clearance_is_reported() -> None:
    plan = _plan()
    plan["furniture"] = [
        {
            "id": "F-010",
            "x": 0,
            "y": 200,
            "w": 2000,
            "d": 600,
            "rot": 0,
            "kind": "wardrobe",
            "name": "衣柜",
        }
    ]
    plan["furniture"].append(
        {"id": "F-011", "x": 800, "y": 900, "w": 800, "d": 500, "rot": 0, "kind": "table"}
    )
    finding = _finding(parse_ir(plan), "clearance.wardrobe_front")
    assert finding is not None
    assert finding.severity == "blocking"


def test_window_ratio_below_one_eighth_is_reported() -> None:
    plan = _plan()
    plan["openings"] = [plan["openings"][0]]
    plan["rooms"][0]["polygon"] = [[0, 0], [8000, 0], [8000, 3000], [0, 3000]]
    finding = _finding(parse_ir(plan), "daylight.window_ratio")
    assert finding is not None
    assert finding.severity == "significant"


def test_room_without_door_is_unreachable() -> None:
    plan = _plan()
    plan["walls"].extend(
        [
            {"id": "W-005", "x1": 7000, "y1": 0, "x2": 7000, "y2": 3000, "t": 120},
            {"id": "W-006", "x1": 7000, "y1": 3000, "x2": 4000, "y2": 3000, "t": 120},
            {"id": "W-007", "x1": 7000, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
        ]
    )
    plan["rooms"].append(
        {
            "id": "R-002",
            "name": "卧室",
            "polygon": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]],
            "area_mm2": 9000000,
        }
    )
    finding = _finding(parse_ir(plan), "route.continuity")
    assert finding is not None
    assert "卧室" in finding.message


def test_electrical_item_in_the_splash_zone_is_reported() -> None:
    plan = _plan()
    plan["furniture"] = [
        {
            "id": "F-020",
            "x": 1000,
            "y": 200,
            "w": 900,
            "d": 900,
            "rot": 0,
            "kind": "shower",
            "name": "淋浴",
        },
        {
            "id": "F-021",
            "x": 1300,
            "y": 200,
            "w": 100,
            "d": 100,
            "rot": 0,
            "kind": "socket",
            "name": "插座",
        },
    ]
    finding = _finding(parse_ir(plan), "safety.splash_zone")
    assert finding is not None
    assert finding.severity == "blocking"


def test_incremental_mode_only_returns_touched_findings() -> None:
    plan = _plan()
    plan["furniture"].append(
        {"id": "F-002", "x": 600, "y": 1700, "w": 1000, "d": 600, "rot": 0, "kind": "table"}
    )
    ir = parse_ir(plan)
    everything = check_drawing(ir)
    incremental = check_drawing(ir, ["F-002"])
    assert len(incremental) < len(everything)
    assert all("F-002" in item.elements for item in incremental)


def test_not_verifiable_rules_are_reported_without_claiming_failure() -> None:
    ir = parse_ir(_plan())
    finding = _finding(ir, "ventilation.service_room")
    assert finding is not None
    assert finding.status == "not_verifiable"


def test_takeoff_reports_areas_and_quantities() -> None:
    ir = parse_ir(_plan())
    result = takeoff(ir)
    assert result["floor_area_m2"] == pytest.approx(12.0, abs=0.01)
    assert result["rooms"][0]["area_m2"] == pytest.approx(12.0, abs=0.01)
    assert result["skirting_length_m"] == pytest.approx(14.0, abs=0.01)
    items = {item["item"]: item for item in result["quantities"]}
    assert items["门"]["qty"] == pytest.approx(1.0)
    assert items["窗"]["qty"] == pytest.approx(1.0)
    assert items["地面面积"]["qty"] == pytest.approx(12.0, abs=0.01)


def test_ir_can_be_built_from_app_rows() -> None:
    rows = [
        {
            "element_id": "W-001",
            "kind": "wall",
            "name": "南墙",
            "payload": json.dumps({"x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120}),
        },
        {
            "element_id": "F-001",
            "kind": "furniture",
            "name": "沙发",
            "payload": json.dumps({"x": 400, "y": 1600, "w": 2000, "d": 900, "kind": "sofa"}),
        },
    ]
    ir = ir_from_rows(rows)
    assert len(ir.walls) == 1
    assert len(ir.furniture) == 1
    assert ir.furniture[0].name == "沙发"


def test_apply_ops_moves_deletes_and_reports_rejections() -> None:
    ir = parse_ir(_plan())
    result = apply_ops(
        ir,
        [
            {"op": "move", "id": "F-001", "dx": 100, "dy": 50},
            {"op": "delete", "id": "N-001"},
            {"op": "delete", "id": "does-not-exist"},
            {"op": "explode", "id": "F-001"},
        ],
    )
    assert result.applied == 2
    assert len(result.rejected) == 2
    payload = result.to_dict()["ir"]
    assert payload["furniture"][0]["x"] == pytest.approx(500)
    assert all(opening["id"] != "N-001" for opening in payload["openings"])


def test_apply_ops_adds_an_element() -> None:
    ir = parse_ir(_plan())
    result = apply_ops(
        ir,
        [
            {
                "op": "add",
                "kind": "furniture",
                "element": {"id": "F-030", "x": 100, "y": 100, "w": 500, "d": 400, "kind": "chair"},
            }
        ],
    )
    assert result.applied == 1
    payload = result.to_dict()["ir"]
    assert any(item["id"] == "F-030" for item in payload["furniture"])
