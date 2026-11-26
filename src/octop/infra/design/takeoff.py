"""Quantity take-off derived from the geometry.

Every number here comes from the IR, never from a model. The output is the
``bom`` rows and the ``rooms`` rows the MiniApp page renders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from octop.infra.design.geometry import Point, polygon_area, polygon_perimeter
from octop.infra.design.ir import IR, nearest_wall

#: Assumed clear ceiling height when the IR does not carry one.
DEFAULT_CEILING_HEIGHT_MM: Final = 2800.0
#: Assumed window opening height.
DEFAULT_WINDOW_HEIGHT_MM: Final = 1800.0
#: Assumed skirting height, used to convert skirting length into area.
DEFAULT_SKIRTING_HEIGHT_MM: Final = 100.0


@dataclass(frozen=True)
class Quantity:
    """One take-off line."""

    category: str
    item: str
    qty: float
    unit: str
    source_element: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "item": self.item,
            "qty": round(self.qty, 1),
            "unit": self.unit,
            "source_element": self.source_element,
        }


def _wall_openings_area(ir: IR, wall_id: str) -> float:
    total = 0.0
    for opening in ir.openings:
        if opening.host and opening.host != wall_id:
            continue
        if not opening.host:
            nearest, _ = nearest_wall(ir, (opening.x, opening.y))
            if nearest is None or nearest.id != wall_id:
                continue
        total += opening.width * opening.h
    return total


def takeoff(ir: IR, ceiling_height_mm: float = DEFAULT_CEILING_HEIGHT_MM) -> dict[str, Any]:
    """Return the room areas and the quantity list for *ir*."""
    rooms: list[dict[str, Any]] = []
    quantities: list[Quantity] = []

    floor_area_mm2 = 0.0
    skirting_length = 0.0
    for room in ir.rooms:
        area = abs(polygon_area(room.polygon))
        perimeter = polygon_perimeter(room.polygon)
        floor_area_mm2 += area
        skirting_length += perimeter
        rooms.append(
            {
                "id": room.id,
                "name": room.name,
                "area_mm2": round(area, 1),
                "area_m2": round(area / 1_000_000, 2),
            }
        )
        if room.name:
            quantities.append(
                Quantity(
                    category="房间",
                    item=f"{room.name} 地面面积",
                    qty=round(area / 1_000_000, 2),
                    unit="m²",
                    source_element=room.id,
                )
            )

    wall_area_mm2 = 0.0
    wall_length = 0.0
    for wall in ir.walls:
        length = wall.length
        if length <= 0:
            continue
        wall_length += length
        gross = length * ceiling_height_mm
        wall_area_mm2 += max(0.0, gross - _wall_openings_area(ir, wall.id))

    door_count = sum(1 for opening in ir.openings if opening.type in {"door", "sliding_door"})
    window_count = sum(1 for opening in ir.openings if opening.is_window)

    quantities.extend(
        [
            Quantity("墙面", "墙面面积（净）", round(wall_area_mm2 / 1_000_000, 2), "m²"),
            Quantity("墙面", "墙体总长", round(wall_length / 1000, 2), "m"),
            Quantity("地面", "地面面积", round(floor_area_mm2 / 1_000_000, 2), "m²"),
            Quantity("地面", "顶面面积", round(floor_area_mm2 / 1_000_000, 2), "m²"),
            Quantity("踢脚线", "踢脚线长度", round(skirting_length / 1000, 2), "m"),
            Quantity(
                "踢脚线",
                "踢脚线面积",
                round(skirting_length * DEFAULT_SKIRTING_HEIGHT_MM / 1_000_000, 2),
                "m²",
            ),
            Quantity("门窗", "门", float(door_count), "樘"),
            Quantity("门窗", "窗", float(window_count), "樘"),
        ]
    )

    return {
        "rooms": rooms,
        "floor_area_m2": round(floor_area_mm2 / 1_000_000, 2),
        "wall_area_m2": round(wall_area_mm2 / 1_000_000, 2),
        "skirting_length_m": round(skirting_length / 1000, 2),
        "quantities": [item.to_dict() for item in quantities],
    }


def polygon_from_points(points: list[Point]) -> list[Point]:
    """Helper for callers that build rooms from raw coordinates."""
    return [(float(x), float(y)) for x, y in points]


def area_of(points: list[Point]) -> float:
    """Signed area helper kept next to the take-off for reuse."""
    return math.fabs(polygon_area(points))
