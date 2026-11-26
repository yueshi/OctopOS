"""Regression baselines for two real plans.

Each fixture freezes what the kernel reports today, so tuning a threshold or
changing a rule shows up as a diff here instead of silently passing. When a
finding legitimately changes, update the snapshot together with the fixture
and say why in the commit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octop.infra.design.checks import check_drawing
from octop.infra.design.ir import parse_ir
from octop.infra.design.takeoff import takeoff

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return parse_ir(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _snapshot(findings: list[Any]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    return [(item.rule_id, item.severity, item.status, tuple(item.elements)) for item in findings]


def test_two_bedroom_81_is_clean_apart_from_professional_checks() -> None:
    ir = _load("two_bedroom_81.json")
    assert (len(ir.walls), len(ir.openings), len(ir.furniture), len(ir.mep), len(ir.rooms)) == (
        10,
        13,
        19,
        8,
        7,
    )
    findings = check_drawing(ir)
    assert _snapshot(findings) == [
        (
            "ventilation.service_room",
            "blocking",
            "not_verifiable",
            (),
        )
    ]
    blocking = [
        item for item in findings if item.severity == "blocking" and item.status == "non_compliant"
    ]
    assert blocking == []

    quantities = takeoff(ir)
    assert quantities["floor_area_m2"] == pytest.approx(84.24, abs=0.01)
    assert quantities["wall_area_m2"] == pytest.approx(157.59, abs=0.05)
    assert quantities["skirting_length_m"] == pytest.approx(96.0, abs=0.05)
    items = {item["item"]: item["qty"] for item in quantities["quantities"]}
    assert items["门"] == 7
    assert items["窗"] == 6


def test_kitchen_living_39_keeps_its_known_findings() -> None:
    ir = _load("kitchen_living_39.json")
    assert (len(ir.walls), len(ir.openings), len(ir.furniture), len(ir.mep), len(ir.rooms)) == (
        5,
        3,
        10,
        5,
        2,
    )
    findings = check_drawing(ir)
    assert _snapshot(findings) == [
        # The fridge sits right behind the only door, so the kitchen is
        # unreachable through a 600mm route.
        ("route.width", "blocking", "non_compliant", ("R-002",)),
        ("ventilation.service_room", "blocking", "not_verifiable", ()),
        # The side table stands in front of the fridge.
        ("clearance.appliance_front", "significant", "non_compliant", ("F-008", "F-009")),
        # The washer has a water point but no drain.
        ("mep.wet_appliance_services", "improvement", "non_compliant", ("F-010",)),
    ]

    quantities = takeoff(ir)
    assert quantities["floor_area_m2"] == pytest.approx(38.88, abs=0.01)
    assert quantities["wall_area_m2"] == pytest.approx(78.39, abs=0.05)
    assert quantities["skirting_length_m"] == pytest.approx(36.0, abs=0.05)


def test_fixtures_stay_parseable_as_rows() -> None:
    """The same JSON must survive the row round-trip the expert uses."""
    from octop.infra.design.ir import ir_from_rows, ir_to_dict

    for name in ("two_bedroom_81.json", "kitchen_living_39.json"):
        plan = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        rows: list[dict[str, Any]] = []
        for kind, key in (
            ("wall", "walls"),
            ("opening", "openings"),
            ("furniture", "furniture"),
            ("mep", "mep"),
            ("room", "rooms"),
        ):
            for item in plan.get(key, []):
                rows.append(
                    {
                        "element_id": item.get("id") or item.get("room_id"),
                        "kind": kind,
                        "payload": json.dumps(item, ensure_ascii=False),
                    }
                )
        rebuilt = ir_from_rows(rows)
        assert len(rebuilt.walls) == len(plan.get("walls", []))
        assert len(rebuilt.furniture) == len(plan.get("furniture", []))
        assert len(rebuilt.mep) == len(plan.get("mep", []))
        assert len(rebuilt.rooms) == len(plan.get("rooms", []))
        assert ir_to_dict(rebuilt)["version"] == 1
