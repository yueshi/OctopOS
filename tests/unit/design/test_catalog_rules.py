"""Tests for the furniture/appliance catalog and the rules it enables."""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.design.catalog import CATALOG, catalog_rows, default_footprint, resolve
from octop.infra.design.checks import check_drawing
from octop.infra.design.ir import parse_ir


def _plan(
    furniture: list[dict[str, Any]],
    *,
    mep: list[dict[str, Any]] | None = None,
) -> Any:
    return parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120},
                {"id": "W-003", "x1": 4000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 3000, "x2": 0, "y2": 0, "t": 120},
            ],
            "openings": [],
            "furniture": furniture,
            "rooms": [
                {
                    "id": "R-001",
                    "name": "客厅",
                    "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                }
            ],
            "mep": mep or [],
        }
    )


def _findings(ir: Any, rule_id: str) -> list[Any]:
    return [item for item in check_drawing(ir) if item.rule_id == rule_id]


# --- catalog ---------------------------------------------------------------


def test_catalog_resolves_aliases_and_case() -> None:
    assert resolve("Wardrobe").kind == "wardrobe"  # type: ignore[union-attr]
    assert resolve("washing machine").kind == "washer"  # type: ignore[union-attr]
    assert resolve("WASHER").kind == "washer"  # type: ignore[union-attr]
    assert resolve("bookcase").kind == "bookshelf"  # type: ignore[union-attr]


def test_catalog_defaults_fill_missing_footprints() -> None:
    ir = parse_ir({"furniture": [{"id": "F-001", "kind": "wardrobe", "x": 0, "y": 0}]})
    item = ir.furniture[0]
    assert (item.w, item.d) == (1800, 600)
    assert item.category == "storage"
    assert default_footprint("fridge") == (700, 700)


def test_catalog_rows_cover_every_entry() -> None:
    rows = catalog_rows()
    assert len(rows) == len(CATALOG)
    assert {"bed_double", "sofa", "fridge", "toilet", "washer"} <= {row["kind"] for row in rows}


# --- clearance rules -------------------------------------------------------


def test_appliance_front_clearance_is_checked() -> None:
    ir = _plan(
        [
            {"id": "F-001", "kind": "fridge", "x": 0, "y": 0, "w": 700, "d": 700},
            {"id": "F-002", "kind": "table", "x": 0, "y": 900, "w": 800, "d": 500},
        ]
    )
    findings = _findings(ir, "clearance.appliance_front")
    assert findings
    assert findings[0].severity == "significant"
    assert findings[0].required == 900


def test_bedside_table_needs_no_floor_clearance() -> None:
    # A bedside table is reached from the bed; the 700mm storage default would
    # be a false positive next to any bed.
    ir = _plan(
        [
            {"id": "F-001", "kind": "bedside_table", "x": 0, "y": 0, "w": 450, "d": 400},
            {"id": "F-002", "kind": "bed_double", "x": 0, "y": 400, "w": 1500, "d": 2000},
        ]
    )
    assert not _findings(ir, "clearance.wardrobe_front")


def test_sanitary_front_clearance_is_checked() -> None:
    ir = _plan(
        [
            {"id": "F-001", "kind": "toilet", "x": 0, "y": 0, "w": 400, "d": 700},
            {"id": "F-002", "kind": "cabinet", "x": 0, "y": 800, "w": 600, "d": 400},
        ]
    )
    assert _findings(ir, "clearance.sanitary_front")


def test_kitchen_aisle_is_checked_only_for_non_appliances() -> None:
    ir = _plan(
        [
            {"id": "F-001", "kind": "kitchen_counter", "x": 0, "y": 0, "w": 1800, "d": 600},
            {"id": "F-002", "kind": "table", "x": 200, "y": 800, "w": 900, "d": 500},
            # A stove is an appliance: the appliance rule owns it, not this one.
            {"id": "F-003", "kind": "stove", "x": 2000, "y": 0, "w": 700, "d": 500},
        ]
    )
    findings = _findings(ir, "clearance.kitchen_aisle")
    assert len(findings) == 1
    assert findings[0].elements[0] == "F-001"


# --- catalog visibility ----------------------------------------------------


def test_hosted_items_may_overlap_their_host() -> None:
    ir = _plan(
        [
            {"id": "F-001", "kind": "kitchen_counter", "x": 6600, "y": 1000, "w": 600, "d": 1800},
            {
                "id": "F-002",
                "kind": "sink",
                "x": 6650,
                "y": 1400,
                "w": 500,
                "d": 800,
                "host": "F-001",
            },
            {
                "id": "F-003",
                "kind": "stove",
                "x": 6650,
                "y": 2400,
                "w": 500,
                "d": 500,
                "host": "F-001",
            },
        ]
    )
    assert _findings(ir, "geometry.element_overlap") == []
    # The counter is what needs front clearance, and nothing blocks it.
    assert _findings(ir, "clearance.kitchen_aisle") == []
    # Hosted items do not become their own clearance problem either.
    assert _findings(ir, "clearance.appliance_front") == []


def test_hosting_is_transitive_and_not_a_blocker() -> None:
    ir = _plan(
        [
            {"id": "F-001", "kind": "kitchen_counter", "x": 6600, "y": 1000, "w": 600, "d": 1800},
            {
                "id": "F-002",
                "kind": "stove",
                "x": 6650,
                "y": 1400,
                "w": 500,
                "d": 700,
                "host": "F-001",
            },
            {
                "id": "F-003",
                "kind": "hood",
                "x": 6650,
                "y": 1400,
                "w": 500,
                "d": 700,
                "host": "F-002",
            },
            {
                "id": "F-004",
                "kind": "sink",
                "x": 6650,
                "y": 2400,
                "w": 500,
                "d": 400,
                "host": "F-001",
            },
        ]
    )
    # Hood -> stove -> counter is one nested group: no overlap between them.
    assert _findings(ir, "geometry.element_overlap") == []
    # The inset sink must not count as blocking the counter's own aisle.
    assert _findings(ir, "clearance.kitchen_aisle") == []


def test_hosting_does_not_exempt_unrelated_overlap() -> None:
    ir = _plan(
        [
            {"id": "F-001", "kind": "kitchen_counter", "x": 0, "y": 0, "w": 1800, "d": 600},
            {
                "id": "F-002",
                "kind": "sink",
                "x": 100,
                "y": 100,
                "w": 500,
                "d": 400,
                "host": "F-001",
            },
            {"id": "F-003", "kind": "table", "x": 0, "y": 200, "w": 800, "d": 500},
        ]
    )
    overlaps = _findings(ir, "geometry.element_overlap")
    assert overlaps
    pairs = {tuple(sorted(finding.elements)) for finding in overlaps}
    assert ("F-001", "F-003") in pairs


def test_unknown_kind_is_reported_instead_of_ignored() -> None:
    ir = _plan([{"id": "F-001", "kind": "magic_box", "x": 0, "y": 0}])
    findings = _findings(ir, "catalog.unknown_kind")
    assert findings
    assert findings[0].status == "not_verifiable"
    assert "magic_box" in findings[0].message


# --- MEP -------------------------------------------------------------------


def test_mep_socket_in_the_splash_zone_is_reported() -> None:
    ir = _plan(
        [{"id": "F-001", "kind": "shower", "x": 0, "y": 0, "w": 900, "d": 900}],
        mep=[{"id": "P-001", "type": "socket", "x": 800, "y": 450}],
    )
    findings = _findings(ir, "safety.splash_zone")
    assert findings
    assert findings[0].elements == ["P-001", "F-001"]


def test_mep_unknown_type_is_reported() -> None:
    ir = _plan(
        [],
        mep=[{"id": "P-001", "type": "teleport", "x": 100, "y": 100}],
    )
    assert _findings(ir, "mep.unknown_type")


def test_missing_mep_is_reported_once_not_per_appliance() -> None:
    ir = _plan(
        [
            {"id": "F-001", "kind": "washer", "x": 0, "y": 0, "w": 600, "d": 600},
            {"id": "F-002", "kind": "sink", "x": 1000, "y": 0, "w": 800, "d": 500},
        ]
    )
    findings = _findings(ir, "mep.not_recorded")
    assert len(findings) == 1
    assert set(findings[0].elements) == {"F-001", "F-002"}
    assert not _findings(ir, "mep.wet_appliance_services")


def test_wet_appliance_without_drain_is_reported() -> None:
    ir = _plan(
        [{"id": "F-001", "kind": "washer", "x": 0, "y": 0, "w": 600, "d": 600}],
        mep=[
            {"id": "P-001", "type": "water", "x": 100, "y": 100},
            {"id": "P-002", "type": "socket", "x": 2000, "y": 2000},
        ],
    )
    findings = _findings(ir, "mep.wet_appliance_services")
    assert findings
    assert "排水" in findings[0].message


def test_ir_round_trips_mep_points() -> None:
    from octop.infra.design.ir import ir_to_dict

    ir = _plan([], mep=[{"id": "P-001", "type": "drain", "x": 10, "y": 20, "height": 300}])
    payload = ir_to_dict(ir)
    assert payload["mep"] == [
        {"id": "P-001", "type": "drain", "x": 10.0, "y": 20.0, "height": 300.0}
    ]


def test_every_reported_rule_is_registered() -> None:
    from octop.infra.design.checks import RULES

    ir = _plan(
        [
            {"id": "F-001", "kind": "magic_box", "x": 0, "y": 0},
            {"id": "F-002", "kind": "fridge", "x": 500, "y": 0, "w": 700, "d": 700},
            {"id": "F-003", "kind": "table", "x": 500, "y": 900, "w": 800, "d": 500},
        ],
        mep=[{"id": "P-001", "type": "teleport", "x": 1, "y": 1}],
    )
    for finding in check_drawing(ir):
        assert finding.rule_id in RULES


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("washer", "laundry"),
        ("dishwasher", "kitchen"),
        ("toilet", "sanitary"),
        ("bed_double", "bed"),
        ("desk", "table"),
    ],
)
def test_categories_are_stable(kind: str, category: str) -> None:
    item = resolve(kind)
    assert item is not None
    assert item.category == category


# --- seats, routes and privacy --------------------------------------------


def _two_room_plan(
    *,
    front: list[dict[str, Any]],
    door_y: int = 1200,
    door_w: int = 900,
) -> Any:
    """Two 4x3 rooms split at x=4000, joined by one door."""
    return parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 8000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 8000, "y1": 0, "x2": 8000, "y2": 3000, "t": 120},
                {"id": "W-003", "x1": 8000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 3000, "x2": 0, "y2": 0, "t": 120},
                {"id": "W-005", "x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120},
            ],
            "openings": [
                {"id": "D-001", "type": "door", "x": 0, "y": 1000, "width": 900, "angle": 90},
                {
                    "id": "D-002",
                    "type": "door",
                    "x": 4000,
                    "y": door_y,
                    "width": door_w,
                    "angle": 90,
                },
            ],
            "furniture": front,
            "rooms": [
                {
                    "id": "R-001",
                    "name": "门厅",
                    "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                },
                {
                    "id": "R-002",
                    "name": "客厅",
                    "polygon": [[4000, 0], [8000, 0], [8000, 3000], [4000, 3000]],
                },
            ],
        }
    )


def test_seat_needs_room_to_stand_up() -> None:
    # The chair faces the table to the north, so the user pushes it back to the
    # south and needs standing space there.
    ir = parse_ir(
        {
            "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120}],
            "openings": [],
            "rooms": [],
            "furniture": [
                {
                    "id": "F-001",
                    "kind": "chair",
                    "x": 500,
                    "y": 1000,
                    "w": 450,
                    "d": 450,
                    "facing": 90,
                },
                {"id": "F-002", "kind": "dining_table", "x": 500, "y": 1550, "w": 800, "d": 500},
                {"id": "F-003", "kind": "cabinet", "x": 500, "y": 400, "w": 800, "d": 400},
            ],
        }
    )
    findings = _findings(ir, "clearance.seat_stand")
    assert findings
    assert findings[0].measured is not None and findings[0].measured < 750


def test_seat_with_walking_room_is_an_improvement_not_a_failure() -> None:
    # Facing east, so the space behind the chair is to the west.
    ir = parse_ir(
        {
            "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120}],
            "openings": [],
            "rooms": [],
            "furniture": [
                {
                    "id": "F-001",
                    "kind": "chair",
                    "x": 1500,
                    "y": 1000,
                    "w": 450,
                    "d": 450,
                    "facing": 0,
                },
                {"id": "F-002", "kind": "cabinet", "x": 400, "y": 1000, "w": 300, "d": 400},
            ],
        }
    )
    assert not _findings(ir, "clearance.seat_stand")
    assert _findings(ir, "clearance.seat_walk")


def test_sofa_is_not_judged_by_the_seat_rule() -> None:
    # A sofa backs onto the wall; nobody needs to stand behind it.
    ir = parse_ir(
        {
            "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 4000, "y2": 0, "t": 120}],
            "openings": [],
            "rooms": [],
            "furniture": [
                {
                    "id": "F-001",
                    "kind": "sofa",
                    "x": 500,
                    "y": 60,
                    "w": 2000,
                    "d": 900,
                    "facing": 90,
                },
            ],
        }
    )
    assert not _findings(ir, "clearance.seat_stand")
    assert not _findings(ir, "clearance.seat_walk")


def test_seat_without_facing_uses_the_best_side() -> None:
    ir = parse_ir(
        {
            "walls": [],
            "openings": [],
            "rooms": [],
            "furniture": [
                {"id": "F-001", "kind": "chair", "x": 1000, "y": 1000, "w": 450, "d": 450}
            ],
        }
    )
    assert not _findings(ir, "clearance.seat_stand")
    assert not _findings(ir, "clearance.seat_walk")


def test_route_width_is_blocked_by_furniture_in_the_doorway() -> None:
    ir = _two_room_plan(
        front=[
            {"id": "F-001", "kind": "wardrobe", "x": 4100, "y": 1450, "w": 600, "d": 650},
        ]
    )
    findings = _findings(ir, "route.width")
    assert findings
    assert findings[0].elements == ["R-002"]


def test_route_width_passes_for_a_clear_door() -> None:
    ir = _two_room_plan(front=[])
    assert not _findings(ir, "route.width")


def test_route_width_starts_at_the_door_not_in_a_furniture_pocket() -> None:
    """A sealed nook near the origin must not become the flood's start cell."""
    ir = parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 11000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 11000, "y1": 0, "x2": 11000, "y2": 4000, "t": 120},
                {"id": "W-003", "x1": 11000, "y1": 4000, "x2": 0, "y2": 4000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 4000, "x2": 0, "y2": 0, "t": 120},
                {"id": "W-005", "x1": 8000, "y1": 0, "x2": 8000, "y2": 4000, "t": 120},
                # a sealed nook in the kitchen's south-west corner
                {"id": "W-006", "x1": 8000, "y1": 1800, "x2": 9800, "y2": 1800, "t": 120},
                {"id": "W-007", "x1": 9800, "y1": 0, "x2": 9800, "y2": 1800, "t": 120},
            ],
            "openings": [
                {"id": "D-001", "type": "door", "x": 11000, "y": 3000, "width": 900, "angle": 90},
                {"id": "D-002", "type": "door", "x": 8000, "y": 3000, "width": 900, "angle": 90},
            ],
            "furniture": [],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "客厅",
                    "polygon": [[0, 0], [8000, 0], [8000, 4000], [0, 4000]],
                },
                {
                    "id": "R-002",
                    "name": "厨房",
                    "polygon": [[8000, 0], [11000, 0], [11000, 4000], [8000, 4000]],
                },
            ],
        }
    )
    assert not _findings(ir, "route.width")


def test_shared_room_reached_through_a_bedroom_is_reported() -> None:
    ir = parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 6000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 6000, "y1": 0, "x2": 6000, "y2": 3000, "t": 120},
                {"id": "W-003", "x1": 6000, "y1": 3000, "x2": 0, "y2": 3000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 3000, "x2": 0, "y2": 0, "t": 120},
                {"id": "W-005", "x1": 2000, "y1": 0, "x2": 2000, "y2": 3000, "t": 120},
                {"id": "W-006", "x1": 4000, "y1": 0, "x2": 4000, "y2": 3000, "t": 120},
            ],
            "openings": [
                {"id": "D-001", "type": "door", "x": 0, "y": 1000, "width": 900, "angle": 90},
                {"id": "D-002", "type": "door", "x": 2000, "y": 1000, "width": 900, "angle": 90},
                {"id": "D-003", "type": "door", "x": 4000, "y": 1000, "width": 900, "angle": 90},
            ],
            "furniture": [],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "门厅",
                    "polygon": [[0, 0], [2000, 0], [2000, 3000], [0, 3000]],
                },
                {
                    "id": "R-002",
                    "name": "卧室",
                    "polygon": [[2000, 0], [4000, 0], [4000, 3000], [2000, 3000]],
                },
                {
                    "id": "R-003",
                    "name": "客厅",
                    "polygon": [[4000, 0], [6000, 0], [6000, 3000], [4000, 3000]],
                },
            ],
        }
    )
    findings = _findings(ir, "privacy.path")
    assert findings
    assert findings[0].elements[0] == "R-003"


# --- remaining geometric rules -------------------------------------------


def _two_room_with_window() -> Any:
    return parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 8000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 8000, "y1": 0, "x2": 8000, "y2": 4000, "t": 120},
                {"id": "W-003", "x1": 8000, "y1": 4000, "x2": 0, "y2": 4000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 4000, "x2": 0, "y2": 0, "t": 120},
                {"id": "W-005", "x1": 4000, "y1": 0, "x2": 4000, "y2": 4000, "t": 120},
            ],
            "openings": [
                {"id": "D-001", "type": "door", "x": 0, "y": 1800, "width": 900, "angle": 90},
                {"id": "D-002", "type": "door", "x": 4000, "y": 1800, "width": 900, "angle": 90},
                {"id": "N-001", "type": "window", "x": 1000, "y": 0, "width": 1200, "angle": 0},
            ],
            "furniture": [],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "客厅",
                    "polygon": [[0, 0], [4000, 0], [4000, 4000], [0, 4000]],
                },
                {
                    "id": "R-002",
                    "name": "卧室",
                    "polygon": [[4000, 0], [8000, 0], [8000, 4000], [4000, 4000]],
                },
            ],
        }
    )


def test_fixed_appliance_must_touch_a_wall() -> None:
    ir = parse_ir(
        {
            "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 6000, "y2": 0, "t": 120}],
            "openings": [],
            "rooms": [],
            "furniture": [
                {"id": "F-001", "kind": "fridge", "x": 2000, "y": 1500, "w": 700, "d": 700}
            ],
        }
    )
    findings = _findings(ir, "geometry.fixed_off_wall")
    assert findings
    assert findings[0].severity == "significant"


def test_fixed_appliance_against_a_wall_passes() -> None:
    ir = parse_ir(
        {
            "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 6000, "y2": 0, "t": 120}],
            "openings": [],
            "rooms": [],
            "furniture": [{"id": "F-001", "kind": "fridge", "x": 2000, "y": 0, "w": 700, "d": 700}],
        }
    )
    assert not _findings(ir, "geometry.fixed_off_wall")


def test_room_without_its_own_window_is_reported() -> None:
    ir = _two_room_with_window()
    findings = _findings(ir, "daylight.service_window")
    assert [item.elements for item in findings] == [["R-002"]]


def test_interior_window_does_not_count_as_daylight() -> None:
    ir = _two_room_with_window()
    # Move the window onto the interior partition: it must not light a room.
    ir = parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 8000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 8000, "y1": 0, "x2": 8000, "y2": 4000, "t": 120},
                {"id": "W-003", "x1": 8000, "y1": 4000, "x2": 0, "y2": 4000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 4000, "x2": 0, "y2": 0, "t": 120},
                {"id": "W-005", "x1": 4000, "y1": 0, "x2": 4000, "y2": 4000, "t": 120},
            ],
            "openings": [
                {"id": "D-001", "type": "door", "x": 0, "y": 1800, "width": 900, "angle": 90},
                {"id": "N-001", "type": "window", "x": 4000, "y": 2000, "width": 1200, "angle": 90},
            ],
            "furniture": [],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "客厅",
                    "polygon": [[0, 0], [4000, 0], [4000, 4000], [0, 4000]],
                },
                {
                    "id": "R-002",
                    "name": "卧室",
                    "polygon": [[4000, 0], [8000, 0], [8000, 4000], [4000, 4000]],
                },
            ],
        }
    )
    findings = _findings(ir, "daylight.service_window")
    assert {tuple(item.elements) for item in findings} == {("R-001",), ("R-002",)}


def test_entry_sightline_to_the_bed_is_reported() -> None:
    ir = parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 6000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 6000, "y1": 0, "x2": 6000, "y2": 4000, "t": 120},
                {"id": "W-003", "x1": 6000, "y1": 4000, "x2": 0, "y2": 4000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 4000, "x2": 0, "y2": 0, "t": 120},
            ],
            "openings": [
                {"id": "D-001", "type": "door", "x": 0, "y": 1800, "width": 900, "angle": 90},
            ],
            "furniture": [
                {"id": "F-001", "kind": "bed_double", "x": 2000, "y": 1000, "w": 1500, "d": 2000},
            ],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "卧室",
                    "polygon": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
                },
            ],
        }
    )
    findings = _findings(ir, "privacy.entry_sightline")
    assert findings
    assert "F-001" in findings[0].elements


def test_door_swing_hitting_furniture_is_reported() -> None:
    ir = parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 6000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 6000, "y1": 0, "x2": 6000, "y2": 4000, "t": 120},
                {"id": "W-003", "x1": 6000, "y1": 4000, "x2": 0, "y2": 4000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 4000, "x2": 0, "y2": 0, "t": 120},
            ],
            "openings": [
                {"id": "D-001", "type": "door", "x": 0, "y": 1000, "width": 900, "angle": 90},
            ],
            "furniture": [
                {"id": "F-001", "kind": "cabinet", "x": 300, "y": 1200, "w": 400, "d": 400},
            ],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "客厅",
                    "polygon": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
                },
            ],
        }
    )
    findings = _findings(ir, "safety.blocked_egress")
    assert findings
    assert findings[0].severity == "blocking"


def test_empty_room_occupancy_is_flagged() -> None:
    ir = parse_ir(
        {
            "walls": [],
            "openings": [],
            "furniture": [{"id": "F-001", "kind": "chair", "x": 100, "y": 100, "w": 450, "d": 450}],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "客厅",
                    "polygon": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
                },
            ],
        }
    )
    assert _findings(ir, "room.occupancy")


def test_circulation_rooms_are_not_scored_for_occupancy() -> None:
    ir = parse_ir(
        {
            "furniture": [],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "门厅",
                    "polygon": [[0, 0], [2000, 0], [2000, 3000], [0, 3000]],
                },
            ],
        }
    )
    assert not _findings(ir, "room.occupancy")


def test_pair_clearance_does_not_cross_a_wall() -> None:
    # Beds 300mm apart, but in different rooms: no finding.
    ir = parse_ir(
        {
            "furniture": [
                {"id": "F-001", "kind": "bed_double", "x": 0, "y": 0, "w": 1500, "d": 2000},
                {"id": "F-002", "kind": "bed_double", "x": 1800, "y": 0, "w": 1500, "d": 2000},
            ],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "主卧",
                    "polygon": [[0, 0], [1700, 0], [1700, 3000], [0, 3000]],
                },
                {
                    "id": "R-002",
                    "name": "卧室",
                    "polygon": [[1700, 0], [4000, 0], [4000, 3000], [1700, 3000]],
                },
            ],
        }
    )
    assert not _findings(ir, "clearance.pair_min")


def test_bed_in_the_kitchen_is_reported() -> None:
    ir = parse_ir(
        {
            "walls": [],
            "openings": [],
            "furniture": [
                {"id": "F-001", "kind": "bed_single", "x": 100, "y": 100, "w": 1000, "d": 2000}
            ],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "厨房",
                    "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                },
            ],
        }
    )
    findings = _findings(ir, "programme.service_empty")
    assert findings
    assert "F-001" in findings[0].elements


# --- heights ---------------------------------------------------------------


def test_heights_default_from_the_catalog() -> None:
    ir = parse_ir(
        {
            "furniture": [{"id": "F-001", "kind": "wardrobe", "x": 0, "y": 0}],
            "openings": [
                {"id": "D-001", "type": "door", "x": 0, "y": 0, "width": 900},
                {"id": "N-001", "type": "window", "x": 0, "y": 0, "width": 900},
            ],
        }
    )
    assert ir.furniture[0].h == 2200
    assert ir.openings[0].h == 2100
    assert ir.openings[1].h == 1800
    assert ir.ceiling_height == 2800


def test_window_area_uses_the_declared_height() -> None:
    base = {
        "walls": [{"id": "W-001", "x1": 0, "y1": 0, "x2": 5000, "y2": 0, "t": 120}],
        "openings": [
            {"id": "N-001", "type": "window", "x": 1000, "y": 0, "width": 1000, "angle": 0}
        ],
        "rooms": [
            {"id": "R-001", "name": "客厅", "polygon": [[0, 0], [5000, 0], [5000, 3800], [0, 3800]]}
        ],
    }
    short = parse_ir(base)
    assert _findings(short, "daylight.window_ratio")
    tall = parse_ir({**base, "openings": [{**base["openings"][0], "h": 2400}]})
    assert not _findings(tall, "daylight.window_ratio")


def test_seat_and_table_height_gap_is_checked() -> None:
    def plan(table_height: float | None) -> Any:
        payload: dict[str, Any] = {"x": 600, "y": 0, "w": 1400, "d": 800, "kind": "dining_table"}
        if table_height is not None:
            payload["h"] = table_height
        return parse_ir(
            {
                "furniture": [
                    {"id": "F-001", "kind": "chair", "x": 0, "y": 0, "w": 450, "d": 450},
                    {"id": "F-002", **payload},
                ]
            }
        )

    assert not _findings(plan(None), "ergonomics.seat_height")
    findings = _findings(plan(1000), "ergonomics.seat_height")
    assert findings
    assert findings[0].measured == pytest.approx(550)


def test_tall_wardrobe_must_stand_up_under_the_ceiling() -> None:
    def plan(height: float) -> Any:
        return parse_ir(
            {
                "ceiling_height": 2400,
                "furniture": [
                    {
                        "id": "F-001",
                        "kind": "wardrobe",
                        "x": 0,
                        "y": 0,
                        "w": 1800,
                        "d": 600,
                        "h": height,
                    }
                ],
            }
        )

    assert not _findings(plan(2200), "assembly.tall_furniture")
    findings = _findings(plan(2350), "assembly.tall_furniture")
    assert findings
    assert findings[0].required == pytest.approx(2400)


# --- handle and pair clearance -------------------------------------------


def _door_plan(handle_wall_x: int, *, hinge: str = "start") -> Any:
    return parse_ir(
        {
            "walls": [
                {"id": "W-001", "x1": 0, "y1": 0, "x2": 6000, "y2": 0, "t": 120},
                {"id": "W-002", "x1": 6000, "y1": 0, "x2": 6000, "y2": 4000, "t": 120},
                {"id": "W-003", "x1": 6000, "y1": 4000, "x2": 0, "y2": 4000, "t": 120},
                {"id": "W-004", "x1": 0, "y1": 4000, "x2": 0, "y2": 0, "t": 120},
                # a return wall the open leaf can hit
                {
                    "id": "W-005",
                    "x1": handle_wall_x,
                    "y1": 800,
                    "x2": handle_wall_x,
                    "y2": 1400,
                    "t": 120,
                },
            ],
            "openings": [
                {
                    "id": "D-001",
                    "type": "door",
                    "x": 0,
                    "y": 1000,
                    "width": 900,
                    "angle": 90,
                    "hinge": hinge,
                },
            ],
            "furniture": [],
            "rooms": [
                {
                    "id": "R-001",
                    "name": "客厅",
                    "polygon": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
                },
            ],
        }
    )


def test_door_handle_clearance_is_checked() -> None:
    # Leaf opens to x=900; a return wall at 950 leaves the handle nothing.
    assert _findings(_door_plan(950), "clearance.handle")
    # At 1200 the handle keeps ~240mm and passes.
    assert not _findings(_door_plan(1200), "clearance.handle")


def test_hinge_side_moves_the_handle() -> None:
    # Hinged at the far jamb, the leaf ends at y=1900, so a wall at x=950 is
    # irrelevant and the handle is clear.
    assert not _findings(_door_plan(950, hinge="end"), "clearance.handle")


def test_pair_clearance_between_beds() -> None:
    close = parse_ir(
        {
            "furniture": [
                {"id": "F-001", "kind": "bed_double", "x": 0, "y": 0, "w": 1500, "d": 2000},
                {"id": "F-002", "kind": "bed_double", "x": 1800, "y": 0, "w": 1500, "d": 2000},
            ]
        }
    )
    findings = _findings(close, "clearance.pair_min")
    assert findings
    assert findings[0].measured == pytest.approx(300)

    far = parse_ir(
        {
            "furniture": [
                {"id": "F-001", "kind": "bed_double", "x": 0, "y": 0, "w": 1500, "d": 2000},
                {"id": "F-002", "kind": "bed_double", "x": 2200, "y": 0, "w": 1500, "d": 2000},
            ]
        }
    )
    assert not _findings(far, "clearance.pair_min")


def test_pair_clearance_ignores_pairs_without_a_threshold() -> None:
    ir = parse_ir(
        {
            "furniture": [
                {"id": "F-001", "kind": "chair", "x": 0, "y": 0, "w": 450, "d": 450},
                {"id": "F-002", "kind": "cabinet", "x": 500, "y": 0, "w": 600, "d": 400},
            ]
        }
    )
    assert not _findings(ir, "clearance.pair_min")
