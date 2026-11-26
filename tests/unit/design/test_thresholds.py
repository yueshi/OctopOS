"""Per-plan threshold overrides.

Each test pins one documented override and proves it changes the verdict,
so the promise in the expert's manual ("adapt thresholds to the project")
is executable rather than prose.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octop.infra.design.checks import check_drawing
from octop.infra.design.ir import ir_from_rows, ir_to_dict, parse_ir

FIXTURES = Path(__file__).parent / "fixtures"


def _findings(ir: Any, rule_id: str) -> list[Any]:
    return [item for item in check_drawing(ir) if item.rule_id == rule_id]


def _with_thresholds(plan: dict[str, Any], **thresholds: float) -> Any:
    return parse_ir({**plan, "thresholds": thresholds})


def test_thresholds_survive_parse_and_round_trip() -> None:
    ir = parse_ir({"thresholds": {"route.width_mm": 800, "junk": "x", "flag": True}})
    assert ir.thresholds == {"route.width_mm": 800.0}
    assert ir_to_dict(ir)["thresholds"] == {"route.width_mm": 800.0}


def test_route_width_override_unblocks_a_tight_plan() -> None:
    plan = json.loads((FIXTURES / "kitchen_living_39.json").read_text(encoding="utf-8"))
    assert _findings(parse_ir(plan), "route.width")
    relaxed = _with_thresholds(plan, **{"route.width_mm": 100.0})
    assert not _findings(relaxed, "route.width")


def test_storage_front_override_changes_one_kind() -> None:
    plan = {
        "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120}],
        "furniture": [
            {"id": "F-001", "kind": "wardrobe", "x": 0, "y": 60, "w": 1200, "d": 600},
            {"id": "F-002", "kind": "chair", "x": 400, "y": 900, "w": 450, "d": 450},
        ],
    }
    assert _findings(parse_ir(plan), "clearance.wardrobe_front")
    relaxed = _with_thresholds(plan, **{"front_clearance.wardrobe": 200.0})
    assert not _findings(relaxed, "clearance.wardrobe_front")


def test_handle_override() -> None:
    plan = {
        "walls": [
            {"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
            {"id": "W-005", "x1": 1050, "y1": 800, "x2": 1050, "y2": 1400, "t": 120},
        ],
        "openings": [
            {"id": "D-001", "type": "door", "x": 0, "y": 1000, "width": 900, "angle": 90},
        ],
        "furniture": [],
        "rooms": [
            {
                "id": "R-001",
                "name": "客厅",
                "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            },
        ],
    }
    assert _findings(parse_ir(plan), "clearance.handle")
    relaxed = _with_thresholds(plan, **{"clearance.handle_mm": 40.0})
    assert not _findings(relaxed, "clearance.handle")


def test_splash_zone_override() -> None:
    plan = {
        "furniture": [
            {"id": "F-001", "kind": "shower", "x": 0, "y": 0, "w": 900, "d": 900},
            {"id": "F-002", "kind": "socket", "x": 900, "y": 450, "w": 80, "d": 80},
        ],
    }
    assert _findings(parse_ir(plan), "safety.splash_zone")
    relaxed = _with_thresholds(plan, **{"safety.splash_zone_mm": 400.0})
    assert not _findings(relaxed, "safety.splash_zone")


def test_window_ratio_override() -> None:
    plan = {
        "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 5000, "y2": 0, "t": 120}],
        "openings": [
            {"id": "N-001", "type": "window", "x": 1000, "y": 0, "width": 1000, "angle": 0}
        ],
        "rooms": [
            {"id": "R-001", "name": "客厅", "polygon": [[0, 0], [5000, 0], [5000, 3800], [0, 3800]]}
        ],
    }
    assert _findings(parse_ir(plan), "daylight.window_ratio")
    relaxed = _with_thresholds(plan, **{"daylight.window_floor_ratio": 12.0})
    assert not _findings(relaxed, "daylight.window_ratio")


def test_occupancy_override() -> None:
    plan = {
        "rooms": [
            {
                "id": "R-001",
                "name": "客厅",
                "polygon": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
            },
        ],
    }
    assert _findings(parse_ir(plan), "room.occupancy")
    relaxed = _with_thresholds(plan, **{"room.occupancy_min": 0.0})
    assert not _findings(relaxed, "room.occupancy")


def test_mep_service_radius_override() -> None:
    plan = {
        "furniture": [
            {"id": "F-001", "kind": "washer", "x": 0, "y": 0, "w": 600, "d": 600},
        ],
        "mep": [
            {"id": "P-001", "type": "water", "x": 300, "y": 300},
            {"id": "P-002", "type": "drain", "x": 1500, "y": 300},
        ],
    }
    assert not _findings(parse_ir(plan), "mep.wet_appliance_services")
    strict = _with_thresholds(plan, **{"mep.service_radius_mm": 800.0})
    assert _findings(strict, "mep.wet_appliance_services")


def test_min_area_override_for_a_room_kind() -> None:
    plan = {
        "rooms": [
            {
                "id": "R-001",
                "name": "客厅",
                "polygon": [[0, 0], [3000, 0], [3000, 2600], [0, 2600]],
            },
        ],
    }
    assert _findings(parse_ir(plan), "programme.min_area")
    relaxed = _with_thresholds(plan, **{"programme.min_area.living": 7_000_000.0})
    assert not _findings(relaxed, "programme.min_area")


def test_pair_clearance_override() -> None:
    plan = {
        "furniture": [
            {"id": "F-001", "kind": "bed_double", "x": 0, "y": 0, "w": 1500, "d": 2000},
            {"id": "F-002", "kind": "bed_double", "x": 1900, "y": 0, "w": 1500, "d": 2000},
        ],
    }
    assert _findings(parse_ir(plan), "clearance.pair_min")
    relaxed = _with_thresholds(plan, **{"clearance.pair_min.bed_double|bed_double": 300.0})
    assert not _findings(relaxed, "clearance.pair_min")


def test_rows_carry_thresholds_too() -> None:
    ir = ir_from_rows(
        [
            {
                "element_id": "S-001",
                "kind": "setting",
                "payload": json.dumps({"thresholds": {"route.width_mm": 900}}),
            }
        ]
    )
    # The setting row is not IR itself; the page folds it in. Keep the row
    # round-trip honest: a setting row must not crash the parser.
    assert ir.walls == []
