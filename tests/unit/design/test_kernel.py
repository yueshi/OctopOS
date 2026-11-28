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
    # Against the south wall, facing into the room; the table is in its face.
    plan["furniture"] = [
        {
            "id": "F-010",
            "x": 200,
            "y": 60,
            "w": 2000,
            "d": 600,
            "rot": 0,
            "kind": "wardrobe",
            "name": "衣柜",
        },
        {"id": "F-011", "x": 600, "y": 900, "w": 800, "d": 500, "rot": 0, "kind": "table"},
    ]
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


def _two_room_door_plan(order: tuple[str, ...]) -> dict[str, Any]:
    """A living room and a smaller bedroom sharing a wall with one door."""
    plan = _plan()
    plan["walls"].extend(
        [
            {"id": "W-005", "x1": 7000, "y1": 0, "x2": 7000, "y2": 3000, "t": 120},
            {"id": "W-006", "x1": 7000, "y1": 3000, "x2": 4000, "y2": 3000, "t": 120},
            {"id": "W-007", "x1": 7000, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
        ]
    )
    plan["openings"].append(
        {
            "id": "D-002",
            "type": "door",
            "x": 4000,
            "y": 1000,
            "width": 800,
            "angle": 90,
            "host": "W-002",
        }
    )
    # The table sits immediately behind the door on the living-room side. A
    # swing into the living room would hit it; a swing into the bedroom would not.
    plan["furniture"].append(
        {
            "id": "F-002",
            "x": 3350,
            "y": 900,
            "w": 600,
            "d": 800,
            "rot": 0,
            "kind": "dining_table",
            "name": "餐桌",
        }
    )
    rooms = {
        "living": {
            "id": "R-001",
            "name": "客厅",
            "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            "area_mm2": 12000000,
        },
        "bedroom": {
            "id": "R-002",
            "name": "儿童房",
            "polygon": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]],
            "area_mm2": 9000000,
        },
    }
    plan["rooms"] = [rooms[key] for key in order]
    return plan


@pytest.mark.parametrize("order", [("living", "bedroom"), ("bedroom", "living")])
def test_door_swing_into_the_smaller_room_does_not_depend_on_room_order(
    order: tuple[str, ...],
) -> None:
    finding = _finding(parse_ir(_two_room_door_plan(order)), "safety.blocked_egress")
    assert finding is None


def _balcony_plan(balcony: dict[str, Any]) -> dict[str, Any]:
    """A bedroom whose south window has a balcony on the other side of the wall."""
    return {
        "walls": [
            {"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
            {"id": "W-002", "x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120},
            {"id": "W-003", "x1": 4000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120},
            {"id": "W-004", "x1": 0, "y1": -1500, "x2": 0, "y2": 3000, "t": 120},
            {"id": "W-005", "x1": 0, "y1": -1500, "x2": 3000, "y2": -1500, "t": 120},
            {"id": "W-006", "x1": 3000, "y1": -1500, "x2": 3000, "y2": 0, "t": 120},
        ],
        "openings": [
            {
                "id": "N-001",
                "type": "window",
                "x": 1000,
                "y": 0,
                "width": 1200,
                "angle": 0,
                "host": "W-001",
            }
        ],
        "furniture": [
            {
                "id": "F-001",
                "x": 100,
                "y": -1400,
                "w": 600,
                "d": 600,
                "rot": 0,
                "kind": "washer",
                "name": "洗衣机",
            }
        ],
        "rooms": [
            {
                "id": "R-001",
                "name": "主卧",
                "kind": "bedroom",
                "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "area_mm2": 12000000,
            },
            {
                "id": "R-002",
                "polygon": [[0, -1500], [3000, -1500], [3000, 0], [0, 0]],
                "area_mm2": 4500000,
                **balcony,
            },
        ],
    }


@pytest.mark.parametrize(
    "balcony",
    [
        {"name": "阳台", "kind": "balcony"},
        {"name": "阳台"},
    ],
)
def test_balcony_is_semi_exterior_for_checks_but_not_for_area(balcony: dict[str, Any]) -> None:
    plan = _balcony_plan(balcony)
    ir = parse_ir(plan)
    diagnostics = check_drawing(ir)
    findings = {item.rule_id: item for item in diagnostics}
    # The balcony must not turn the bedroom's exterior wall into an interior one.
    assert "daylight.service_window" not in findings
    # Nor is it an ordinary room target for route/occupancy checks.
    assert "route.continuity" not in findings
    assert "route.width" not in findings
    assert all(
        "R-002" not in item.elements for item in diagnostics if item.rule_id == "room.occupancy"
    )
    # Its area still belongs in the take-off.
    assert takeoff(ir)["floor_area_m2"] == pytest.approx(16.5)


def test_room_exterior_flag_is_read_from_kind_name_and_explicit_field() -> None:
    ir = parse_ir(
        {
            "rooms": [
                {
                    "id": "R-1",
                    "name": "阳台",
                    "polygon": [[0, 0], [1000, 0], [1000, 1000]],
                },
                {
                    "id": "R-2",
                    "name": "儿童房",
                    "kind": "bedroom",
                    "polygon": [[0, 0], [1000, 0], [1000, 1000]],
                },
                {
                    "id": "R-3",
                    "name": "Terrace",
                    "polygon": [[0, 0], [1000, 0], [1000, 1000]],
                },
                {
                    "id": "R-4",
                    "name": "多功能房",
                    "kind": "study",
                    "exterior": True,
                    "polygon": [[0, 0], [1000, 0], [1000, 1000]],
                },
            ]
        }
    )
    assert [room.is_exterior for room in ir.rooms] == [True, False, True, True]


def test_room_polygon_accepts_the_legacy_poly_key() -> None:
    room = {
        "id": "R-1",
        "name": "主卧",
        "kind": "bedroom",
        "poly": [[0, 0], [1000, 0], [1000, 1000]],
    }
    assert len(parse_ir({"rooms": [room]}).rooms[0].polygon) == 3
    row = {
        "element_id": "R-1",
        "kind": "room",
        "name": "主卧",
        "payload": json.dumps({"name": "主卧", "kind": "bedroom", "poly": room["poly"]}),
    }
    assert len(ir_from_rows([row]).rooms[0].polygon) == 3
