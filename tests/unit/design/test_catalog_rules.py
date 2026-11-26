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
