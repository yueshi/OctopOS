"""Deterministic checks over a design IR.

Every rule returns a :class:`Diagnostic` with a stable ``rule_id``, a status
(``non_compliant`` / ``not_verifiable`` / ``not_applicable`` / ``compliant``)
and a severity (``blocking`` / ``significant`` / ``improvement``). The expert
and the MiniApp page both consume this shape, so it must stay stable.

Rules never call a model and never estimate: if a rule cannot measure
something, it reports ``not_verifiable`` instead of guessing.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from octop.infra.design.catalog import MEP_TYPES, CatalogItem, resolve
from octop.infra.design.geometry import (
    Point,
    collinear_overlap_length,
    convex_polygons_overlap,
    distance,
    dot,
    point_in_polygon,
    point_to_polygon_distance,
    point_to_segment_distance,
    polygon_area,
    polygon_centroid,
    polygon_distance,
    ray_segment_distance,
    segments_intersect,
    snap_point,
    support_width,
)
from octop.infra.design.ir import IR, Furniture, Opening, Room, Wall, nearest_wall

STATUS_NON_COMPLIANT: Final = "non_compliant"
STATUS_NOT_VERIFIABLE: Final = "not_verifiable"
STATUS_NOT_APPLICABLE: Final = "not_applicable"

SEVERITY_BLOCKING: Final = "blocking"
SEVERITY_SIGNIFICANT: Final = "significant"
SEVERITY_IMPROVEMENT: Final = "improvement"

#: Minimum continuous route width between the entrance and every use zone.
ROUTE_MIN_WIDTH_MM: Final = 600.0
#: Clear space in front of wardrobes and cabinets.
STORAGE_FRONT_CLEARANCE_MM: Final = 700.0
#: Minimum window area over floor area.
WINDOW_FLOOR_RATIO: Final = 8.0
#: Assumed window opening height when the IR does not carry one.
WINDOW_HEIGHT_MM: Final = 1800.0
#: Assumed clear height under a window when the IR does not carry one.
WINDOW_SILL_MM: Final = 900.0
#: Splash zone around a shower or bath where electrical items are forbidden.
SPLASH_ZONE_MM: Final = 600.0
#: Placement slack every rule must survive.
PLACEMENT_TOLERANCE_MM: Final = 20.0
#: Two wall ends further apart than this are not considered the same joint.
JOINT_SEARCH_RADIUS_MM: Final = 300.0

#: A wet appliance should have water and drain within this distance.
WET_APPLIANCE_SERVICE_RADIUS_MM: Final = 1500.0

#: Clear space in front of a seat to stand up / to walk behind it.
SEAT_STAND_MM: Final = 750.0
SEAT_WALK_MM: Final = 900.0
#: How far a seat rule looks for a blocker.
SEAT_PROBE_MM: Final = 3000.0
#: Movable seats whose user pushes them back to stand; a sofa is not one.
SEAT_RULE_KINDS: Final = frozenset({"chair", "stool", "bench", "armchair"})

#: Continuous route width required from the entrance to every use zone.
ROUTE_WIDTH_MM: Final = 600.0
#: Grid step for the route raster (half of it is the measurement error).
ROUTE_GRID_MM: Final = 100.0

#: Room-name keywords used by the privacy rule (Chinese and English).
PRIVATE_ROOM_WORDS: Final = (
    "卧室",
    "主卧",
    "次卧",
    "儿童房",
    "老人房",
    "书房",
    "卫生间",
    "浴室",
    "客卫",
    "主卫",
    "卧室",
    "bedroom",
    "bath",
    "toilet",
    "study",
)
SHARED_ROOM_WORDS: Final = (
    "客厅",
    "起居",
    "餐厅",
    "厨房",
    "门厅",
    "玄关",
    "过道",
    "走廊",
    "living",
    "dining",
    "kitchen",
    "hall",
    "corridor",
    "entry",
    "lobby",
)

#: Kind-name sets the rules relied on before the catalog existed. Kept as a
#: fallback for kinds the catalog does not know yet.
STORAGE_KINDS: Final = frozenset({"wardrobe", "cabinet", "closet", "storage", "bookcase"})
WET_KINDS: Final = frozenset({"shower", "bathtub"})
ELECTRICAL_KINDS: Final = frozenset(
    {"socket", "switch", "appliance", "fridge", "washer", "dishwasher"}
)

#: Minimum room areas by kind, in m². Overridable per project.
MIN_ROOM_AREA_MM2: Final[dict[str, float]] = {
    "living": 12_000_000.0,
    "bedroom": 9_000_000.0,
    "kitchen": 5_000_000.0,
    "bathroom": 3_000_000.0,
    "balcony": 1_500_000.0,
}


def _limit(ir: IR, key: str, default: float) -> float:
    """Read a per-plan threshold override, falling back to the default."""
    value = ir.thresholds.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kind_front_clearance(ir: IR, info: CatalogItem) -> tuple[float, bool]:
    """Per-kind front clearance and whether it was stated for this kind.

    ``(0.0, True)`` means "this kind needs none" (a bedside table), which is
    different from "not stated" — the category default only applies then.
    """
    override = ir.thresholds.get(f"front_clearance.{info.kind}")
    if override is not None:
        return float(override), True
    if info.front_clearance is not None:
        return float(info.front_clearance), True
    return 0.0, False


#: Room kind by name, for rules that key on the kind rather than the label.
ROOM_KIND_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "living": ("客厅", "起居", "living"),
    "bedroom": ("卧室", "主卧", "次卧", "儿童房", "老人房", "bedroom"),
    "kitchen": ("厨房", "kitchen"),
    "bathroom": ("卫生间", "浴室", "客卫", "主卫", "bath", "toilet"),
    "balcony": ("阳台", "balcony"),
    "study": ("书房", "study"),
}


def _room_kind(room: Room) -> str:
    for kind, words in ROOM_KIND_WORDS.items():
        if _room_is(room, words):
            return kind
    return ""


#: Rooms that need their own exterior window.
DAYLIGHT_ROOM_WORDS: Final = (
    "客厅",
    "起居",
    "卧室",
    "主卧",
    "次卧",
    "儿童房",
    "书房",
    "厨房",
    "living",
    "bedroom",
    "study",
    "kitchen",
)

#: Service rooms should not hold these.
SERVICE_ROOM_WORDS: Final = ("厨房", "卫生间", "浴室", "客卫", "主卫", "kitchen", "bath", "toilet")
UNRELATED_IN_SERVICE: Final = frozenset(
    {"bed_double", "bed_king", "bed_single", "crib", "sofa", "dining_table", "wardrobe", "tv_stand"}
)

#: Furniture footprint share of a room, as a sanity band.
ROOM_OCCUPANCY_MIN: Final = 0.10
ROOM_OCCUPANCY_MAX: Final = 0.40

#: Circulation rooms are judged by the route rules, not by occupancy.
CIRCULATION_ROOM_WORDS: Final = (
    "门厅",
    "玄关",
    "过道",
    "走廊",
    "hall",
    "corridor",
    "entry",
    "lobby",
)

#: How far an entry sightline is traced.
ENTRY_SIGHT_RANGE_MM: Final = 10_000.0

#: Comfortable gap between a seat surface and the table top it serves.
SEAT_TABLE_DELTA_MIN: Final = 250.0
SEAT_TABLE_DELTA_MAX: Final = 320.0
#: How far a seat may be from the table it belongs to.
SEAT_TABLE_REACH_MM: Final = 1200.0

#: Items at least this tall have to be tilted up inside the room.
TALL_ITEM_MM: Final = 1800.0

#: Space a door handle needs when the leaf stands open at 90 degrees.
HANDLE_CLEARANCE_MM: Final = 100.0

#: Minimum gap between specific furniture pairs. Defaults, not regulation.
PAIR_CLEARANCE_MM: Final[dict[tuple[str, str], float]] = {
    ("bed_double", "bed_double"): 600.0,
    ("bed_double", "bed_single"): 600.0,
    ("bed_king", "bed_single"): 600.0,
    ("bed_king", "bed_double"): 600.0,
    ("bed_single", "bed_single"): 600.0,
    ("bed_double", "crib"): 600.0,
    ("bed_single", "crib"): 600.0,
    ("armchair", "coffee_table"): 300.0,
    ("coffee_table", "sofa"): 300.0,
    ("armchair", "sofa"): 300.0,
    ("bed_double", "desk"): 500.0,
    ("bed_single", "desk"): 500.0,
}


@dataclass(frozen=True)
class RuleSpec:
    """Static description of one rule, used for docs and reporting."""

    rule_id: str
    title: str
    severity: str
    unit: str = "mm"


@dataclass(frozen=True)
class Diagnostic:
    """One finding. ``measured``/``required`` are filled when meaningful."""

    rule_id: str
    status: str
    severity: str
    message: str
    elements: list[str] = field(default_factory=list)
    measured: float | None = None
    required: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rule_id": self.rule_id,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "elements": list(self.elements),
        }
        if self.measured is not None:
            payload["measured"] = round(self.measured, 1)
        if self.required is not None:
            payload["required"] = round(self.required, 1)
        return payload


RULES: dict[str, RuleSpec] = {
    spec.rule_id: spec
    for spec in (
        RuleSpec("geometry.wall_gap", "墙体之间断开", SEVERITY_BLOCKING),
        RuleSpec("geometry.wall_overlap", "墙体互相重叠", SEVERITY_BLOCKING),
        RuleSpec("geometry.room_not_enclosed", "房间边界不闭合", SEVERITY_BLOCKING),
        RuleSpec("geometry.opening_not_in_wall", "门窗开洞不在墙上", SEVERITY_BLOCKING),
        RuleSpec("geometry.opening_past_wall_end", "开洞超出墙端", SEVERITY_BLOCKING),
        RuleSpec("geometry.openings_overlap", "同一墙上的开洞重叠", SEVERITY_BLOCKING),
        RuleSpec("geometry.element_overlap", "家具互相重叠或压墙", SEVERITY_BLOCKING),
        RuleSpec("route.continuity", "使用区域之间无连续通道", SEVERITY_BLOCKING),
        RuleSpec("safety.splash_zone", "湿区溅水范围内有电气件", SEVERITY_BLOCKING),
        RuleSpec("clearance.wardrobe_front", "衣柜/柜前净空不足", SEVERITY_BLOCKING),
        RuleSpec("daylight.window_ratio", "窗地比不足", SEVERITY_SIGNIFICANT),
        RuleSpec("programme.min_area", "房间面积低于最小值", SEVERITY_SIGNIFICANT),
        RuleSpec("robustness.tolerance", "净空余量小于摆放容差", SEVERITY_SIGNIFICANT),
        RuleSpec("ergonomics.seat_height", "座高与台面高不匹配", SEVERITY_IMPROVEMENT, unit="mm"),
        RuleSpec("ventilation.service_room", "服务房间通风", SEVERITY_BLOCKING),
        RuleSpec("clearance.appliance_front", "电器前净空不足", SEVERITY_SIGNIFICANT),
        RuleSpec("clearance.sanitary_front", "洁具前净空不足", SEVERITY_SIGNIFICANT),
        RuleSpec("clearance.kitchen_aisle", "厨房操作通道不足", SEVERITY_SIGNIFICANT),
        RuleSpec("catalog.unknown_kind", "家具类型不在品类目录", SEVERITY_IMPROVEMENT),
        RuleSpec("mep.unknown_type", "水电点位类型未知", SEVERITY_IMPROVEMENT),
        RuleSpec("mep.not_recorded", "未录入水电点位", SEVERITY_SIGNIFICANT),
        RuleSpec("mep.wet_appliance_services", "用水电器缺少给排水点位", SEVERITY_IMPROVEMENT),
        RuleSpec("clearance.seat_stand", "座椅前站立空间不足", SEVERITY_SIGNIFICANT),
        RuleSpec("clearance.seat_walk", "座椅前通行空间不足", SEVERITY_IMPROVEMENT),
        RuleSpec("route.width", "600mm 净宽通路无法到达", SEVERITY_BLOCKING),
        RuleSpec("privacy.path", "公共区域需穿过私密房间", SEVERITY_SIGNIFICANT),
        RuleSpec("geometry.fixed_off_wall", "固定设备未贴墙", SEVERITY_SIGNIFICANT),
        RuleSpec("daylight.service_window", "居住房间缺少外窗", SEVERITY_SIGNIFICANT),
        RuleSpec("privacy.entry_sightline", "入户直视床或马桶", SEVERITY_SIGNIFICANT),
        RuleSpec("safety.blocked_egress", "门扇开启撞到家具", SEVERITY_BLOCKING),
        RuleSpec("room.occupancy", "房间家具占用率异常", SEVERITY_IMPROVEMENT),
        RuleSpec("programme.service_empty", "服务房间放了无关家具", SEVERITY_IMPROVEMENT),
        RuleSpec("assembly.tall_furniture", "高柜无法在房间内立起", SEVERITY_SIGNIFICANT),
        RuleSpec("clearance.pair_min", "家具配对间距不足", SEVERITY_SIGNIFICANT),
    )
}


def _mm(value: float) -> str:
    return f"{value:.0f}mm"


# --- geometry ----------------------------------------------------------------


def _wall_ends(wall: Wall) -> tuple[Point, Point]:
    return (wall.x1, wall.y1), (wall.x2, wall.y2)


def _wall_span(wall: Wall) -> tuple[float, float]:
    """Projected extent of the wall along its own direction."""
    length = wall.length
    if length < 1e-9:
        return (0.0, 0.0)
    unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
    values = [
        (point[0] - wall.x1) * unit[0] + (point[1] - wall.y1) * unit[1]
        for point in ((wall.x1, wall.y1), (wall.x2, wall.y2))
    ]
    return (min(values), max(values))


def _nearest_wall(ir: IR, point: Point) -> tuple[Wall | None, float]:
    return nearest_wall(ir, point)


def _room_of_point(rooms: list[Room], point: Point) -> Room | None:
    for room in rooms:
        if room.polygon and point_in_polygon(point, room.polygon):
            return room
    return None


def _wall_for_edge(a: Point, b: Point, walls: list[Wall], tolerance: float) -> Wall | None:
    """The wall that carries the room edge ``a``-``b``, if any."""
    edge_dx = b[0] - a[0]
    edge_dy = b[1] - a[1]
    for wall in walls:
        if wall.is_degenerate:
            continue
        if point_to_segment_distance(a, (wall.x1, wall.y1), (wall.x2, wall.y2)) > tolerance:
            continue
        if point_to_segment_distance(b, (wall.x1, wall.y1), (wall.x2, wall.y2)) > tolerance:
            continue
        wall_dx = wall.x2 - wall.x1
        wall_dy = wall.y2 - wall.y1
        if abs(edge_dx * wall_dy - edge_dy * wall_dx) > 1.0:
            continue
        return wall
    return None


def _edge_supported(rooms: list[Room], wall: Wall) -> bool:
    """True when some room edge lies along this wall."""
    for room in rooms:
        polygon = room.polygon
        for index in range(len(polygon)):
            a = polygon[index]
            b = polygon[(index + 1) % len(polygon)]
            if point_to_segment_distance((wall.x1, wall.y1), a, b) <= max(wall.t, 20.0) and (
                point_to_segment_distance((wall.x2, wall.y2), a, b) <= max(wall.t, 20.0)
            ):
                return True
    return False


# --- rules -------------------------------------------------------------------


def _check_wall_gaps(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    seen: set[tuple[str, str]] = set()
    for i, wall_a in enumerate(ir.walls):
        for wall_b in ir.walls[i + 1 :]:
            for end_a in _wall_ends(wall_a):
                for end_b in _wall_ends(wall_b):
                    gap = distance(end_a, end_b)
                    if gap <= 1.0 or gap > JOINT_SEARCH_RADIUS_MM:
                        continue
                    if snap_point(end_a) == snap_point(end_b):
                        continue
                    key = (wall_a.id, wall_b.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(
                        Diagnostic(
                            rule_id="geometry.wall_gap",
                            status=STATUS_NON_COMPLIANT,
                            severity=SEVERITY_BLOCKING,
                            message=(
                                f"{wall_a.id or '墙体'} 与 {wall_b.id or '墙体'} 之间有 "
                                f"{_mm(gap)} 的缺口，房间无法闭合"
                            ),
                            elements=[wall_a.id, wall_b.id],
                            measured=gap,
                            required=0.0,
                        )
                    )
    return findings


def _check_wall_overlaps(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for i, wall_a in enumerate(ir.walls):
        for wall_b in ir.walls[i + 1 :]:
            overlap = collinear_overlap_length(
                (wall_a.x1, wall_a.y1),
                (wall_a.x2, wall_a.y2),
                (wall_b.x1, wall_b.y1),
                (wall_b.x2, wall_b.y2),
            )
            if overlap <= 1.0:
                continue
            findings.append(
                Diagnostic(
                    rule_id="geometry.wall_overlap",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=f"{wall_a.id or '墙体'} 与 {wall_b.id or '墙体'} 重叠 {_mm(overlap)}",
                    elements=[wall_a.id, wall_b.id],
                    measured=overlap,
                    required=0.0,
                )
            )
    return findings


def _check_rooms(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for room in ir.rooms:
        if len(room.polygon) < 3:
            findings.append(
                Diagnostic(
                    rule_id="geometry.room_not_enclosed",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=f"房间 {room.name or room.id} 的边界不足三个顶点，无法闭合",
                    elements=[room.id] if room.id else [],
                )
            )
            continue
        area = abs(polygon_area(room.polygon))
        if area <= 1.0:
            findings.append(
                Diagnostic(
                    rule_id="geometry.room_not_enclosed",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=f"房间 {room.name or room.id} 的面积为 0，边界未闭合",
                    elements=[room.id] if room.id else [],
                    measured=0.0,
                    required=1.0,
                )
            )
        unsupported = 0
        for index in range(len(room.polygon)):
            a = room.polygon[index]
            b = room.polygon[(index + 1) % len(room.polygon)]
            if _wall_for_edge(a, b, ir.walls, ROOM_EDGE_TOLERANCE_MM) is None:
                unsupported += 1
        if unsupported:
            findings.append(
                Diagnostic(
                    rule_id="geometry.room_not_enclosed",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"房间 {room.name or room.id} 的边界有 {unsupported} 段没有对应墙体，"
                        "房间不闭合"
                    ),
                    elements=[room.id] if room.id else [],
                    measured=float(unsupported),
                    required=0.0,
                )
            )
    return findings


#: How far a room edge may sit from a wall centre line and still count as carried.
ROOM_EDGE_TOLERANCE_MM: Final = 120.0


def _check_openings(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for opening in ir.openings:
        wall, gap = _nearest_wall(ir, (opening.x, opening.y))
        if wall is None:
            findings.append(
                Diagnostic(
                    rule_id="geometry.opening_not_in_wall",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=f"门窗 {opening.id or '未命名'} 所在位置没有墙体",
                    elements=[opening.id] if opening.id else [],
                )
            )
            continue
        if gap > max(wall.t, 20.0):
            findings.append(
                Diagnostic(
                    rule_id="geometry.opening_not_in_wall",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"门窗 {opening.id or '未命名'} 距最近的墙体 {wall.id or '墙体'} "
                        f"{_mm(gap)}，没有落在墙上"
                    ),
                    elements=[opening.id, wall.id],
                    measured=gap,
                    required=max(wall.t, 20.0),
                )
            )
            continue
        findings.extend(_check_opening_within_wall(opening, wall))
    findings.extend(_check_opening_overlaps(ir))
    return findings


def _check_opening_within_wall(opening: Opening, wall: Wall) -> list[Diagnostic]:
    start = (opening.x, opening.y)
    end = opening.end
    low, high = _wall_span(wall)
    length = wall.length
    unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
    positions = [
        (point[0] - wall.x1) * unit[0] + (point[1] - wall.y1) * unit[1] for point in (start, end)
    ]
    beyond = 0.0
    for position in positions:
        if position < low:
            beyond = max(beyond, low - position)
        elif position > high:
            beyond = max(beyond, position - high)
    if beyond <= 1.0:
        return []
    return [
        Diagnostic(
            rule_id="geometry.opening_past_wall_end",
            status=STATUS_NON_COMPLIANT,
            severity=SEVERITY_BLOCKING,
            message=(
                f"门窗 {opening.id or '未命名'} 超出墙体 {wall.id or '墙体'} 端点 {_mm(beyond)}"
            ),
            elements=[opening.id, wall.id],
            measured=beyond,
            required=0.0,
        )
    ]


def _check_opening_overlaps(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for i, first in enumerate(ir.openings):
        for second in ir.openings[i + 1 :]:
            wall_a, _ = _nearest_wall(ir, (first.x, first.y))
            wall_b, _ = _nearest_wall(ir, (second.x, second.y))
            if wall_a is None or wall_b is None or wall_a.id != wall_b.id:
                continue
            overlap = _overlap_on_wall(first, second, wall_a)
            if overlap <= 1.0:
                continue
            findings.append(
                Diagnostic(
                    rule_id="geometry.openings_overlap",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"门窗 {first.id or '未命名'} 与 {second.id or '未命名'} "
                        f"在墙 {wall_a.id or '墙体'} 上重叠 {_mm(overlap)}"
                    ),
                    elements=[first.id, second.id, wall_a.id],
                    measured=overlap,
                    required=0.0,
                )
            )
    return findings


def _overlap_on_wall(first: Opening, second: Opening, wall: Wall) -> float:
    """Length two openings share, measured along their common wall."""
    length = wall.length
    if length < 1e-9:
        return 0.0
    unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
    spans = []
    for opening in (first, second):
        values = [
            (point[0] - wall.x1) * unit[0] + (point[1] - wall.y1) * unit[1]
            for point in ((opening.x, opening.y), opening.end)
        ]
        spans.append((min(values), max(values)))
    return min(spans[0][1], spans[1][1]) - max(spans[0][0], spans[1][0])


def _check_element_overlaps(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    by_id = {item.id: item for item in ir.furniture}
    for i, first in enumerate(ir.furniture):
        for second in ir.furniture[i + 1 :]:
            if _hosted_within(first, second.id, by_id) or _hosted_within(second, first.id, by_id):
                # A sink sits inside its counter run by design, and a hood sits
                # above the stove that is itself inset in the same run.
                continue
            if not convex_polygons_overlap(first.corners(), second.corners()):
                continue
            findings.append(
                Diagnostic(
                    rule_id="geometry.element_overlap",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=f"{first.name or first.id} 与 {second.name or second.id} 互相重叠",
                    elements=[first.id, second.id],
                )
            )
    for item in ir.furniture:
        if item.host:
            continue
        for wall in ir.walls:
            if wall.is_degenerate:
                continue
            if not _wall_cuts_polygon(wall, item.corners()):
                continue
            if ir.rooms and not _edge_supported(ir.rooms, wall):
                continue
            findings.append(
                Diagnostic(
                    rule_id="geometry.element_overlap",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=f"{item.name or item.id} 压在墙 {wall.id or '墙体'} 上",
                    elements=[item.id, wall.id],
                )
            )
    return findings


def _wall_cuts_polygon(wall: Wall, polygon: list[Point]) -> bool:
    """True when the wall runs through the polygon instead of along its edge."""
    if len(polygon) < 3:
        return False
    start = (wall.x1, wall.y1)
    end = (wall.x2, wall.y2)
    touches = point_in_polygon(start, polygon) or point_in_polygon(end, polygon)
    if not touches:
        for index in range(len(polygon)):
            if segments_intersect(start, end, polygon[index], polygon[(index + 1) % len(polygon)]):
                touches = True
                break
    if not touches:
        return False
    length = wall.length
    if length < 1e-9:
        return False
    normal = (-(wall.y2 - wall.y1) / length, (wall.x2 - wall.x1) / length)
    midpoint = ((wall.x1 + wall.x2) / 2, (wall.y1 + wall.y2) / 2)
    signs: set[int] = set()
    for corner in polygon:
        side = dot(normal, (corner[0] - midpoint[0], corner[1] - midpoint[1]))
        if abs(side) > 1.0:
            signs.add(1 if side > 0 else -1)
    return len(signs) > 1


def _front_clearance(
    ir: IR,
    *,
    rule_id: str,
    select: Callable[[Furniture], float | None],
    severity: str,
) -> list[Diagnostic]:
    """Generic "clear space in front of a wall-hugging item" rule.

    The measurement runs along the item's front normal, so a piece standing
    beside it does not count as blocking the way.
    """
    findings: list[Diagnostic] = []
    by_id = {item.id: item for item in ir.furniture}
    for item in ir.furniture:
        if item.host:
            continue
        required = select(item)
        if not required:
            continue
        wall, gap = _supporting_wall(item, ir.walls)
        if wall is None or gap > max(wall.t, 40.0):
            continue
        normal = _wall_normal_towards(wall, item.center)
        if normal is None:
            continue
        clearance, blocker_id = _clearance_ahead(ir, item, normal)
        if clearance >= required:
            continue
        info = resolve(item.kind)
        label = item.name or (info.label_zh if info else item.id)
        blocker = by_id.get(blocker_id)
        blocker_label = blocker.name or blocker.id if blocker else (blocker_id or "墙体")
        findings.append(
            Diagnostic(
                rule_id=rule_id,
                status=STATUS_NON_COMPLIANT,
                severity=severity,
                message=(
                    f"{label} 前方净空 {_mm(clearance)}，小于要求的 {_mm(required)}，"
                    f"被 {blocker_label} 挡住"
                ),
                elements=[item.id, blocker_id] if blocker_id else [item.id],
                measured=clearance,
                required=required,
            )
        )
    return findings


def _hosted_within(item: Furniture, host_id: str, by_id: dict[str, Furniture]) -> bool:
    """True when *item* sits inside *host_id*, directly or through a chain."""
    seen: set[str] = set()
    host = item.host
    while host and host not in seen:
        if host == host_id:
            return True
        seen.add(host)
        parent = by_id.get(host)
        host = parent.host if parent else ""
    return False


def _check_storage_clearance(ir: IR) -> list[Diagnostic]:
    def select(item: Furniture) -> float | None:
        info = resolve(item.kind)
        default = _limit(ir, "clearance.storage_front_mm", STORAGE_FRONT_CLEARANCE_MM)
        if info is not None:
            if info.category != "storage":
                return None
            required, stated = _kind_front_clearance(ir, info)
            return required or None if stated else default
        return default if item.kind.lower() in STORAGE_KINDS else None

    return _front_clearance(
        ir,
        rule_id="clearance.wardrobe_front",
        select=select,
        severity=SEVERITY_BLOCKING,
    )


def _check_appliance_clearance(ir: IR) -> list[Diagnostic]:
    def select(item: Furniture) -> float | None:
        info = resolve(item.kind)
        if info is None or not info.appliance:
            return None
        required, _stated = _kind_front_clearance(ir, info)
        return required or None

    return _front_clearance(
        ir,
        rule_id="clearance.appliance_front",
        select=select,
        severity=SEVERITY_SIGNIFICANT,
    )


def _check_sanitary_clearance(ir: IR) -> list[Diagnostic]:
    def select(item: Furniture) -> float | None:
        info = resolve(item.kind)
        if info is None or info.category != "sanitary":
            return None
        required, _stated = _kind_front_clearance(ir, info)
        return required or None

    return _front_clearance(
        ir,
        rule_id="clearance.sanitary_front",
        select=select,
        severity=SEVERITY_SIGNIFICANT,
    )


def _check_kitchen_aisle(ir: IR) -> list[Diagnostic]:
    def select(item: Furniture) -> float | None:
        info = resolve(item.kind)
        if info is None or info.category != "kitchen" or info.appliance:
            return None
        required, _stated = _kind_front_clearance(ir, info)
        return required or None

    return _front_clearance(
        ir,
        rule_id="clearance.kitchen_aisle",
        select=select,
        severity=SEVERITY_SIGNIFICANT,
    )


def _check_unknown_kinds(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    unknown: dict[str, list[str]] = {}
    for item in ir.furniture:
        kind = item.kind.strip()
        if not kind or resolve(kind) is not None:
            continue
        if kind.lower() in STORAGE_KINDS or kind.lower() in WET_KINDS:
            continue
        unknown.setdefault(kind, []).append(item.id)
    for kind, ids in sorted(unknown.items()):
        findings.append(
            Diagnostic(
                rule_id="catalog.unknown_kind",
                status=STATUS_NOT_VERIFIABLE,
                severity=SEVERITY_IMPROVEMENT,
                message=(
                    f"家具类型 {kind!r} 不在品类目录里，依赖品类的净空/水电规则无法判定；"
                    "请改用目录中的标准 kind"
                ),
                elements=ids,
            )
        )
    return findings


def _supporting_wall(item: Furniture, walls: list[Wall]) -> tuple[Wall | None, float]:
    """The wall an item is placed against, and the gap to its footprint.

    The nearest wall to the item's *centre* is the wrong anchor for a wide
    wardrobe, so the footprint decides: the wall its back face touches wins.
    """
    best: Wall | None = None
    best_gap = math.inf
    corners = item.corners()
    for wall in walls:
        if wall.is_degenerate:
            continue
        gap = min(
            point_to_segment_distance(corner, (wall.x1, wall.y1), (wall.x2, wall.y2))
            for corner in corners
        )
        if gap < best_gap:
            best_gap = gap
            best = wall
    return best, best_gap


def _wall_normal_towards(wall: Wall, point: Point) -> Point | None:
    """Unit normal of *wall* that points from the wall towards *point*."""
    length = wall.length
    if length < 1e-9:
        return None
    normal = (-(wall.y2 - wall.y1) / length, (wall.x2 - wall.x1) / length)
    midpoint = ((wall.x1 + wall.x2) / 2, (wall.y1 + wall.y2) / 2)
    to_point = (point[0] - midpoint[0], point[1] - midpoint[1])
    if dot(normal, to_point) < 0:
        normal = (-normal[0], -normal[1])
    return normal


def _front_edge_point(item: Furniture, normal: Point) -> Point:
    """The point on the item's face that looks into the room."""
    rad = math.radians(item.rot)
    axis_u = (math.cos(rad), math.sin(rad))
    axis_v = (-math.sin(rad), math.cos(rad))
    half = support_width(item.w / 2, item.d / 2, axis_u, axis_v, normal)
    center = item.center
    return (center[0] + normal[0] * half, center[1] + normal[1] * half)


def _passable_spans(ir: IR, wall: Wall) -> list[tuple[float, float]]:
    """Parameter spans along *wall* where a door lets a person through."""
    length = wall.length
    if length < 1e-9:
        return []
    unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
    spans: list[tuple[float, float]] = []
    for opening in ir.openings:
        if opening.type not in {"door", "sliding_door", "opening"}:
            continue
        nearest, gap = _nearest_wall(ir, (opening.x, opening.y))
        if nearest is None or nearest.id != wall.id or gap > max(wall.t, 20.0):
            continue
        positions = [
            (point[0] - wall.x1) * unit[0] + (point[1] - wall.y1) * unit[1]
            for point in ((opening.x, opening.y), opening.end)
        ]
        spans.append((min(positions), max(positions)))
    return spans


def _clearance_ahead(ir: IR, item: Furniture, direction: Point) -> tuple[float, str]:
    """Distance to the first blocker along *direction*, and the blocker's id.

    Walls stop the ray; a door span in a wall is passable. Items hosted by
    *item* are part of it and never block it.
    """
    half = support_width(
        item.w / 2,
        item.d / 2,
        (math.cos(math.radians(item.rot)), math.sin(math.radians(item.rot))),
        (-math.sin(math.radians(item.rot)), math.cos(math.radians(item.rot))),
        direction,
    )
    origin = (item.center[0] + direction[0] * half, item.center[1] + direction[1] * half)
    best = SEAT_PROBE_MM
    blocker = ""
    by_id = {candidate.id: candidate for candidate in ir.furniture}

    for other in ir.furniture:
        if other.id == item.id or _hosted_within(other, item.id, by_id):
            continue
        corners = other.corners()
        for index in range(len(corners)):
            hit = ray_segment_distance(
                origin, direction, corners[index], corners[(index + 1) % len(corners)]
            )
            if hit is not None and hit < best:
                best = hit
                blocker = other.id

    for wall in ir.walls:
        if wall.is_degenerate:
            continue
        hit = ray_segment_distance(origin, direction, (wall.x1, wall.y1), (wall.x2, wall.y2))
        if hit is None:
            continue
        length = wall.length
        unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
        hit_point = (origin[0] + direction[0] * hit, origin[1] + direction[1] * hit)
        position = (hit_point[0] - wall.x1) * unit[0] + (hit_point[1] - wall.y1) * unit[1]
        if any(low <= position <= high for low, high in _passable_spans(ir, wall)):
            continue
        free = max(0.0, hit - wall.t / 2)
        if free < best:
            best = free
            blocker = wall.id
    return best, blocker


def _seat_directions(item: Furniture) -> list[Point]:
    """Direction(s) to measure for a seat.

    A seated person stands by pushing the chair back, so the relevant space is
    *behind* the seat: the opposite of the declared facing. Without a facing,
    any side will do, so all four are tried and the best one wins.
    """
    if item.facing is not None:
        rad = math.radians(item.facing)
        return [(-math.cos(rad), -math.sin(rad))]
    rad = math.radians(item.rot)
    u = (math.cos(rad), math.sin(rad))
    v = (-math.sin(rad), math.cos(rad))
    return [u, (-u[0], -u[1]), v, (-v[0], -v[1])]


def _check_seat_clearance(ir: IR) -> list[Diagnostic]:
    """A seat needs room to stand up (and better, to walk behind)."""
    findings: list[Diagnostic] = []
    stand = _limit(ir, "clearance.seat_stand_mm", SEAT_STAND_MM)
    walk_limit = _limit(ir, "clearance.seat_walk_mm", SEAT_WALK_MM)
    for item in ir.furniture:
        info = resolve(item.kind)
        if item.host or info is None or info.kind not in SEAT_RULE_KINDS:
            continue
        best = max(_clearance_ahead(ir, item, d)[0] for d in _seat_directions(item))
        if best >= walk_limit:
            continue
        walk = best >= stand
        findings.append(
            Diagnostic(
                rule_id="clearance.seat_walk" if walk else "clearance.seat_stand",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_IMPROVEMENT if walk else SEVERITY_SIGNIFICANT,
                message=(
                    f"{item.name or info.label_zh} 背后净空 {_mm(best)}，"
                    + (
                        f"可站立但通行不足 {_mm(walk_limit)}"
                        if walk
                        else f"不足站立所需的 {_mm(stand)}"
                    )
                ),
                elements=[item.id],
                measured=best,
                required=walk_limit if walk else stand,
            )
        )
    return findings


def _room_is(room: Room, words: tuple[str, ...]) -> bool:
    name = (room.name or "").lower()
    return any(word.lower() in name for word in words)


def _room_adjacency(ir: IR) -> dict[str, set[str]]:
    rooms = [room for room in ir.rooms if room.polygon]
    adjacency: dict[str, set[str]] = {room.id: set() for room in rooms}
    for opening in ir.openings:
        if opening.type not in {"door", "sliding_door", "opening"}:
            continue
        touching = [
            room
            for room in rooms
            if point_in_polygon((opening.x, opening.y), room.polygon)
            or point_in_polygon(opening.end, room.polygon)
        ]
        for i, first in enumerate(touching):
            for second in touching[i + 1 :]:
                adjacency.setdefault(first.id, set()).add(second.id)
                adjacency.setdefault(second.id, set()).add(first.id)
    return adjacency


def _reachable(
    adjacency: dict[str, set[str]],
    start: str,
    *,
    blocked: set[str] | None = None,
) -> set[str]:
    """Rooms reachable from *start* without passing through *blocked*."""
    blocked = blocked or set()
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for neighbour in adjacency.get(current, set()):
            if neighbour in seen or neighbour in blocked:
                continue
            seen.add(neighbour)
            stack.append(neighbour)
    return seen


def _check_privacy_path(ir: IR) -> list[Diagnostic]:
    """Shared rooms must not be reached by walking through a bedroom or bath."""
    rooms = [room for room in ir.rooms if room.polygon]
    if not rooms:
        return []
    entrance = _entrance_room(ir, rooms)
    if entrance is None:
        return []
    adjacency = _room_adjacency(ir)
    full = _reachable(adjacency, entrance.id)
    private = {room.id for room in rooms if _room_is(room, PRIVATE_ROOM_WORDS)}
    shared = [room for room in rooms if _room_is(room, SHARED_ROOM_WORDS)]
    if not private or not shared:
        return []
    allowed = _reachable(adjacency, entrance.id, blocked=private)
    findings: list[Diagnostic] = []
    for room in shared:
        if room.id not in full or room.id in allowed:
            continue
        findings.append(
            Diagnostic(
                rule_id="privacy.path",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_SIGNIFICANT,
                message=(
                    f"从入口到{room.name}必须穿过私密房间"
                    f"（{'、'.join(sorted(room_id for room_id in private))}）"
                ),
                elements=[room.id, *sorted(private)],
            )
        )
    return findings


def _check_route_width(ir: IR) -> list[Diagnostic]:
    """A continuous ``ROUTE_WIDTH_MM`` route must reach every reachable room.

    Free space is eroded by half the required width, then flooded from the
    entrance cell: a room counts as reachable when any cell inside it survives
    the erosion. Cells are 100 mm, so the measured width carries that error.
    """
    rooms = [room for room in ir.rooms if room.polygon]
    if len(rooms) < 2:
        return []
    adjacency = _room_adjacency(ir)
    entrance = _entrance_room(ir, rooms)
    if entrance is None:
        return []
    connected = _reachable(adjacency, entrance.id)

    xs = [point[0] for room in rooms for point in room.polygon]
    ys = [point[1] for room in rooms for point in room.polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    step = ROUTE_GRID_MM
    width = _limit(ir, "route.width_mm", ROUTE_WIDTH_MM)
    half_width = width / 2
    cols = int((max_x - min_x) / step) + 1
    rows = int((max_y - min_y) / step) + 1
    if cols <= 0 or rows <= 0 or cols * rows > 400_000:
        return []

    def cell_center(col: int, row: int) -> Point:
        return (min_x + (col + 0.5) * step, min_y + (row + 0.5) * step)

    def cell_range(low_x: float, low_y: float, high_x: float, high_y: float) -> tuple[range, range]:
        first_col = max(0, int((low_x - min_x) / step) - 1)
        last_col = min(cols - 1, int((high_x - min_x) / step) + 1)
        first_row = max(0, int((low_y - min_y) / step) - 1)
        last_row = min(rows - 1, int((high_y - min_y) / step) + 1)
        return range(first_col, last_col + 1), range(first_row, last_row + 1)

    free: list[list[bool]] = []
    for row in range(rows):
        line: list[bool] = []
        for col in range(cols):
            point = cell_center(col, row)
            line.append(any(point_in_polygon(point, room.polygon) for room in rooms))
        free.append(line)

    # Mark blocked cells from each obstacle's bounding box instead of testing
    # every obstacle against every cell.
    for item in ir.furniture:
        corners = item.corners()
        item_xs = [point[0] for point in corners]
        item_ys = [point[1] for point in corners]
        cols_range, rows_range = cell_range(
            min(item_xs) - half_width,
            min(item_ys) - half_width,
            max(item_xs) + half_width,
            max(item_ys) + half_width,
        )
        for row in rows_range:
            for col in cols_range:
                if not free[row][col]:
                    continue
                if point_to_polygon_distance(cell_center(col, row), corners) <= half_width:
                    free[row][col] = False

    for wall in ir.walls:
        if wall.is_degenerate:
            continue
        spans = _passable_spans(ir, wall)
        length = wall.length
        unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
        cols_range, rows_range = cell_range(
            min(wall.x1, wall.x2) - half_width - wall.t,
            min(wall.y1, wall.y2) - half_width - wall.t,
            max(wall.x1, wall.x2) + half_width + wall.t,
            max(wall.y1, wall.y2) + half_width + wall.t,
        )
        for row in rows_range:
            for col in cols_range:
                if not free[row][col]:
                    continue
                x, y = cell_center(col, row)
                gap = point_to_segment_distance((x, y), (wall.x1, wall.y1), (wall.x2, wall.y2))
                if gap > half_width + wall.t / 2:
                    continue
                position = (x - wall.x1) * unit[0] + (y - wall.y1) * unit[1]
                if any(low <= position <= high for low, high in spans):
                    continue
                free[row][col] = False

    # Start at the entrance door, not at whichever free cell happens to be
    # scanned first: a furniture ring can enclose a free pocket in a corner,
    # and starting inside it would report every other room as unreachable.
    door = _entrance_door(ir)
    anchor = (door.x, door.y) if door is not None else None
    start_cell = None
    if anchor is not None:
        best_distance = float("inf")
        for row in range(rows):
            for col in range(cols):
                if not free[row][col]:
                    continue
                point = cell_center(col, row)
                if not any(point_in_polygon(point, room.polygon) for room in rooms):
                    continue
                gap = distance(point, anchor)
                if gap < best_distance:
                    best_distance = gap
                    start_cell = (row, col)
    else:
        for row in range(rows):
            for col in range(cols):
                if free[row][col] and point_in_polygon(cell_center(col, row), entrance.polygon):
                    start_cell = (row, col)
                    break
            if start_cell:
                break
    if start_cell is None:
        return [
            Diagnostic(
                rule_id="route.width",
                status=STATUS_NOT_VERIFIABLE,
                severity=SEVERITY_BLOCKING,
                message="入口处没有满足净宽要求的空间，无法判定通路",
                elements=[entrance.id],
            )
        ]

    seen = {start_cell}
    stack = [start_cell]
    while stack:
        row, col = stack.pop()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = row + dr, col + dc
            if 0 <= nr < rows and 0 <= nc < cols and free[nr][nc] and (nr, nc) not in seen:
                seen.add((nr, nc))
                stack.append((nr, nc))

    findings: list[Diagnostic] = []
    for room in rooms:
        if room.id not in connected or room.id == entrance.id:
            continue
        reached = False
        for row, col in seen:
            x = min_x + (col + 0.5) * step
            y = min_y + (row + 0.5) * step
            if point_in_polygon((x, y), room.polygon):
                reached = True
                break
        if reached:
            continue
        findings.append(
            Diagnostic(
                rule_id="route.width",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_BLOCKING,
                message=(
                    f"在 {_mm(width)} 净宽要求下，从入口无法到达{room.name or room.id}"
                    "（门洞存在，但通路被家具或墙体收窄）"
                ),
                elements=[room.id],
                measured=width,
                required=width,
            )
        )
    return findings


def _wall_has_rooms_on_both_sides(ir: IR, wall: Wall) -> bool:
    """True for an interior wall: rooms sit on both sides of it."""
    length = wall.length
    if length < 1e-9:
        return False
    normal = (-(wall.y2 - wall.y1) / length, (wall.x2 - wall.x1) / length)
    midpoint = ((wall.x1 + wall.x2) / 2, (wall.y1 + wall.y2) / 2)
    sides: set[int] = set()
    for room in ir.rooms:
        for point in room.polygon:
            side = dot(normal, (point[0] - midpoint[0], point[1] - midpoint[1]))
            if abs(side) > wall.t:
                sides.add(1 if side > 0 else -1)
            if len(sides) == 2:
                return True
    return False


def _inward_normal(ir: IR, opening: Opening) -> tuple[Point, Room] | None:
    """Normal of the wall through *opening*, pointing into the adjacent room."""
    rooms = [room for room in ir.rooms if room.polygon]
    if not rooms:
        return None
    room = _room_of_point(rooms, (opening.x, opening.y))
    if room is None:
        return None
    direction = (math.cos(opening.heading), math.sin(opening.heading))
    normal = (-direction[1], direction[0])
    centroid = polygon_centroid(room.polygon)
    towards = (centroid[0] - opening.x, centroid[1] - opening.y)
    if dot(normal, towards) < 0:
        normal = (-normal[0], -normal[1])
    return normal, room


def _ray_hit_polygon(origin: Point, direction: Point, polygon: list[Point]) -> float | None:
    best: float | None = None
    for index in range(len(polygon)):
        hit = ray_segment_distance(
            origin, direction, polygon[index], polygon[(index + 1) % len(polygon)]
        )
        if hit is not None and (best is None or hit < best):
            best = hit
    return best


def _wall_stop_distance(ir: IR, origin: Point, direction: Point) -> float:
    """Distance along the ray to the first wall that actually blocks."""
    best = ENTRY_SIGHT_RANGE_MM
    for wall in ir.walls:
        if wall.is_degenerate:
            continue
        hit = ray_segment_distance(origin, direction, (wall.x1, wall.y1), (wall.x2, wall.y2))
        if hit is None:
            continue
        length = wall.length
        unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
        point = (origin[0] + direction[0] * hit, origin[1] + direction[1] * hit)
        position = (point[0] - wall.x1) * unit[0] + (point[1] - wall.y1) * unit[1]
        if any(low <= position <= high for low, high in _passable_spans(ir, wall)):
            continue
        best = min(best, max(0.0, hit - wall.t / 2))
    return best


def _check_fixed_off_wall(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for item in ir.furniture:
        if item.host:
            continue
        info = resolve(item.kind)
        if info is None or not info.fixed:
            continue
        wall, gap = _supporting_wall(item, ir.walls)
        if wall is not None and gap <= max(wall.t, 40.0):
            continue
        findings.append(
            Diagnostic(
                rule_id="geometry.fixed_off_wall",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_SIGNIFICANT,
                message=(
                    f"{item.name or info.label_zh} 未贴墙安装"
                    + (f"（距最近墙 {_mm(gap)}）" if wall is not None else "（附近没有墙）")
                    + "；固定设备需靠墙以便接水电"
                ),
                elements=[item.id],
                measured=gap if wall is not None else None,
                required=0.0,
            )
        )
    return findings


def _window_serves_room(ir: IR, opening: Opening, room: Room) -> bool:
    """A window only lights a room when it sits on that room's *exterior* wall."""
    wall, _gap = _nearest_wall(ir, (opening.x, opening.y))
    if wall is None:
        return False
    if _wall_has_rooms_on_both_sides(ir, wall):
        return False
    return point_to_polygon_distance((opening.x, opening.y), room.polygon) <= max(wall.t, 60.0)


def _check_service_window(ir: IR) -> list[Diagnostic]:
    windows = [opening for opening in ir.openings if opening.is_window]
    findings: list[Diagnostic] = []
    for room in ir.rooms:
        if not room.polygon or not _room_is(room, DAYLIGHT_ROOM_WORDS):
            continue
        if any(_window_serves_room(ir, window, room) for window in windows):
            continue
        findings.append(
            Diagnostic(
                rule_id="daylight.service_window",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_SIGNIFICANT,
                message=f"{room.name} 没有直接采光的外窗（窗必须开在该房间自己的外墙上）",
                elements=[room.id],
            )
        )
    return findings


def _check_entry_sightline(ir: IR) -> list[Diagnostic]:
    door = _entrance_door(ir)
    if door is None:
        return []
    normal = _inward_normal(ir, door)
    if normal is None:
        return []
    direction, _room = normal
    origin = (door.x, door.y)
    limit = _wall_stop_distance(ir, origin, direction)
    targets: list[Furniture] = []
    by_id = {item.id: item for item in ir.furniture}
    for item in ir.furniture:
        info = resolve(item.kind)
        if info is None or item.host:
            continue
        if info.category not in {"bed", "sanitary"}:
            continue
        if info.category == "sanitary" and info.kind != "toilet":
            continue
        hit = _ray_hit_polygon(origin, direction, item.corners())
        if hit is not None and hit <= limit:
            targets.append(item)
    if not targets:
        return []
    names = "、".join(item.name or (by_id[item.id].kind) for item in targets)
    return [
        Diagnostic(
            rule_id="privacy.entry_sightline",
            status=STATUS_NON_COMPLIANT,
            severity=SEVERITY_SIGNIFICANT,
            message=f"从入户门直视到：{names}；建议用玄关柜、屏风或改门位遮挡",
            elements=[door.id, *[item.id for item in targets]],
        )
    ]


def _check_door_swing(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    by_id = {item.id: item for item in ir.furniture}
    for door in ir.openings:
        if door.type != "door":
            continue
        normal = _inward_normal(ir, door)
        if normal is None:
            continue
        inward, _room = normal
        hinge = (door.x, door.y)
        leaf = (math.cos(door.heading), math.sin(door.heading))
        collided: Furniture | None = None
        for step in range(0, 91, 15):
            rad = math.radians(step)
            direction = (
                math.cos(rad) * leaf[0] + math.sin(rad) * inward[0],
                math.cos(rad) * leaf[1] + math.sin(rad) * inward[1],
            )
            for fraction in (0.5, 1.0):
                point = (
                    hinge[0] + door.width * fraction * direction[0],
                    hinge[1] + door.width * fraction * direction[1],
                )
                for item in ir.furniture:
                    if item.host:
                        continue
                    if point_in_polygon(point, item.corners()):
                        collided = item
                        break
                if collided:
                    break
            if collided:
                break
        if collided is None:
            continue
        info = resolve(collided.kind)
        label = collided.name or (info.label_zh if info else by_id[collided.id].kind)
        findings.append(
            Diagnostic(
                rule_id="safety.blocked_egress",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_BLOCKING,
                message=(
                    f"门 {door.id or '未命名'} 开启时撞到{label}；"
                    f"{_mm(door.width)} 门扇需要完整开启空间"
                ),
                elements=[door.id, collided.id],
                measured=door.width,
                required=door.width,
            )
        )
    return findings


def _check_room_occupancy(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for room in ir.rooms:
        area = abs(polygon_area(room.polygon))
        if area <= 0 or _room_is(room, CIRCULATION_ROOM_WORDS):
            continue
        used = 0.0
        for item in ir.furniture:
            if item.host:
                continue
            if point_in_polygon(item.center, room.polygon):
                used += item.w * item.d
        ratio = used / area
        low = _limit(ir, "room.occupancy_min", ROOM_OCCUPANCY_MIN)
        high = _limit(ir, "room.occupancy_max", ROOM_OCCUPANCY_MAX)
        if low <= ratio <= high:
            continue
        findings.append(
            Diagnostic(
                rule_id="room.occupancy",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_IMPROVEMENT,
                message=(
                    f"{room.name or room.id} 家具占用率 {ratio * 100:.0f}%，"
                    f"建议保持在 {low * 100:.0f}%–{high * 100:.0f}%"
                ),
                elements=[room.id],
                measured=ratio,
                required=high if ratio > high else low,
            )
        )
    return findings


def _check_service_room_use(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for room in ir.rooms:
        if not room.polygon or not _room_is(room, SERVICE_ROOM_WORDS):
            continue
        intruders = [
            item
            for item in ir.furniture
            if not item.host
            and item.kind in UNRELATED_IN_SERVICE
            and point_in_polygon(item.center, room.polygon)
        ]
        if not intruders:
            continue
        findings.append(
            Diagnostic(
                rule_id="programme.service_empty",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_IMPROVEMENT,
                message=(
                    f"{room.name} 里放了不该有的家具："
                    + "、".join(item.name or item.kind for item in intruders)
                ),
                elements=[room.id, *[item.id for item in intruders]],
            )
        )
    return findings


def _check_seat_height(ir: IR) -> list[Diagnostic]:
    """A seat and the table it serves need a comfortable height gap."""
    findings: list[Diagnostic] = []
    delta_min = _limit(ir, "ergonomics.seat_table_delta_min", SEAT_TABLE_DELTA_MIN)
    delta_max = _limit(ir, "ergonomics.seat_table_delta_max", SEAT_TABLE_DELTA_MAX)
    reach = _limit(ir, "ergonomics.seat_table_reach_mm", SEAT_TABLE_REACH_MM)
    tables = [
        item for item in ir.furniture if not item.host and item.kind in {"dining_table", "desk"}
    ]
    for item in ir.furniture:
        info = resolve(item.kind)
        if item.host or info is None or info.category != "seat":
            continue
        seat_height = info.seat_height or item.h
        nearest: Furniture | None = None
        best_gap = reach
        for table in tables:
            gap = distance(item.center, table.center)
            if gap < best_gap:
                best_gap = gap
                nearest = table
        if nearest is None:
            continue
        table_info = resolve(nearest.kind)
        delta = nearest.h - seat_height
        if delta_min <= delta <= delta_max:
            continue
        findings.append(
            Diagnostic(
                rule_id="ergonomics.seat_height",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_IMPROVEMENT,
                message=(
                    f"{item.name or info.label_zh} 座高 {_mm(seat_height)}，"
                    f"{nearest.name or (table_info.label_zh if table_info else nearest.kind)} "
                    f"高 {_mm(nearest.h)}，相差 {_mm(delta)}，"
                    f"建议 {_mm(delta_min)}–{_mm(delta_max)}"
                ),
                elements=[item.id, nearest.id],
                measured=delta,
                required=delta_max if delta > delta_max else delta_min,
            )
        )
    return findings


def _check_tall_furniture(ir: IR) -> list[Diagnostic]:
    """Tall pieces must be able to stand up under the ceiling."""
    findings: list[Diagnostic] = []
    ceiling = ir.ceiling_height or 2800.0
    tall = _limit(ir, "assembly.tall_item_mm", TALL_ITEM_MM)
    for item in ir.furniture:
        if item.host or item.h < tall:
            continue
        info = resolve(item.kind)
        if info is None or not (info.fixed or info.category in {"storage", "kitchen"}):
            continue
        # Tilt about the long bottom edge: the short side is what swings up.
        diagonal = math.hypot(min(item.w, item.d), item.h)
        if diagonal <= ceiling + 1.0:
            continue
        findings.append(
            Diagnostic(
                rule_id="assembly.tall_furniture",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_SIGNIFICANT,
                message=(
                    f"{item.name or info.label_zh} 高 {_mm(item.h)}、短边 {_mm(min(item.w, item.d))}，"
                    f"立起需要 {_mm(diagonal)}，层高只有 {_mm(ceiling)}"
                ),
                elements=[item.id],
                measured=diagonal,
                required=ceiling,
            )
        )
    return findings


def _check_door_handle(ir: IR) -> list[Diagnostic]:
    """A fully open door leaf must leave the handle clear of walls and furniture."""
    findings: list[Diagnostic] = []
    for door in ir.openings:
        if door.type != "door":
            continue
        normal = _inward_normal(ir, door)
        if normal is None:
            continue
        inward, _room = normal
        hinge = door.end if door.hinge == "end" else (door.x, door.y)
        handle = (hinge[0] + inward[0] * door.width, hinge[1] + inward[1] * door.width)

        clearance = math.inf
        for wall in ir.walls:
            if wall.is_degenerate:
                continue
            # A leaf folded back along a parallel side wall is normal; only a
            # wall standing across the open leaf can trap the handle.
            wall_unit = ((wall.x2 - wall.x1) / wall.length, (wall.y2 - wall.y1) / wall.length)
            if abs(dot(inward, wall_unit)) > 0.9:
                continue
            gap = point_to_segment_distance(handle, (wall.x1, wall.y1), (wall.x2, wall.y2))
            clearance = min(clearance, max(0.0, gap - wall.t / 2))
        for item in ir.furniture:
            if item.host:
                continue
            clearance = min(clearance, point_to_polygon_distance(handle, item.corners()))
        required = _limit(ir, "clearance.handle_mm", HANDLE_CLEARANCE_MM)
        if clearance >= required:
            continue
        findings.append(
            Diagnostic(
                rule_id="clearance.handle",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_IMPROVEMENT,
                message=(
                    f"门 {door.id or '未命名'} 全开时把手侧净空 {_mm(clearance)}，"
                    f"小于 {_mm(required)}"
                ),
                elements=[door.id],
                measured=clearance,
                required=required,
            )
        )
    return findings


def _room_id_for(item: Furniture, rooms: list[Room]) -> str:
    for room in rooms:
        if room.polygon and point_in_polygon(item.center, room.polygon):
            return room.id
    return ""


def _check_pair_clearance(ir: IR) -> list[Diagnostic]:
    """Specific furniture pairs need a minimum gap between them.

    Only pairs in the same room are compared: a wall between them means the
    gap is not something anyone walks through.
    """
    findings: list[Diagnostic] = []
    rooms = [room for room in ir.rooms if room.polygon]
    for index, first in enumerate(ir.furniture):
        if first.host:
            continue
        for second in ir.furniture[index + 1 :]:
            if second.host:
                continue
            if rooms:
                first_room = _room_id_for(first, rooms)
                second_room = _room_id_for(second, rooms)
                if first_room and second_room and first_room != second_room:
                    continue
            pair_key = "|".join(sorted((first.kind, second.kind)))
            required = ir.thresholds.get(f"clearance.pair_min.{pair_key}")
            if required is None:
                required = PAIR_CLEARANCE_MM.get((first.kind, second.kind))
            if required is None or required <= 0:
                continue
            gap = polygon_distance(first.corners(), second.corners())
            if gap >= required:
                continue
            first_info = resolve(first.kind)
            second_info = resolve(second.kind)
            findings.append(
                Diagnostic(
                    rule_id="clearance.pair_min",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_SIGNIFICANT,
                    message=(
                        f"{first.name or (first_info.label_zh if first_info else first.kind)} 与 "
                        f"{second.name or (second_info.label_zh if second_info else second.kind)} "
                        f"间距 {_mm(gap)}，小于建议的 {_mm(required)}"
                    ),
                    elements=[first.id, second.id],
                    measured=gap,
                    required=required,
                )
            )
    return findings


def _check_window_ratio(ir: IR) -> list[Diagnostic]:
    floor_area = sum(abs(polygon_area(room.polygon)) for room in ir.rooms)
    if floor_area <= 0.0:
        return [
            Diagnostic(
                rule_id="daylight.window_ratio",
                status=STATUS_NOT_VERIFIABLE,
                severity=SEVERITY_SIGNIFICANT,
                message="没有房间面积，无法核算窗地比",
                elements=[],
            )
        ]
    window_area = sum(opening.width * opening.h for opening in ir.openings if opening.is_window)
    required = floor_area / _limit(ir, "daylight.window_floor_ratio", WINDOW_FLOOR_RATIO)
    if window_area >= required:
        return []
    return [
        Diagnostic(
            rule_id="daylight.window_ratio",
            status=STATUS_NON_COMPLIANT,
            severity=SEVERITY_SIGNIFICANT,
            message=(
                f"窗地比不足：窗面积 {window_area / 1_000_000:.2f}m²，"
                f"地面面积 {floor_area / 1_000_000:.2f}m²，要求至少 {required / 1_000_000:.2f}m²"
            ),
            elements=[opening.id for opening in ir.openings if opening.is_window],
            measured=window_area,
            required=required,
        )
    ]


def _check_min_area(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for room in ir.rooms:
        kind = _room_kind(room)
        if not kind:
            continue
        required = ir.thresholds.get(f"programme.min_area.{kind}")
        if required is None:
            required = MIN_ROOM_AREA_MM2.get(kind)
        if required is None:
            continue
        area = abs(polygon_area(room.polygon))
        if area >= required:
            continue
        findings.append(
            Diagnostic(
                rule_id="programme.min_area",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_SIGNIFICANT,
                message=(
                    f"{room.name} 面积 {area / 1_000_000:.2f}m²，"
                    f"低于最小值 {required / 1_000_000:.2f}m²"
                ),
                elements=[room.id] if room.id else [],
                measured=area,
                required=required,
            )
        )
    return findings


def _check_route_continuity(ir: IR) -> list[Diagnostic]:
    """Every room must be reachable from the entrance through openings."""
    rooms = [room for room in ir.rooms if room.polygon]
    if len(rooms) < 2:
        return []
    adjacency: dict[str, set[str]] = {room.id: set() for room in rooms}
    for opening in ir.openings:
        if opening.type not in {"door", "sliding_door"}:
            continue
        touching = [
            room
            for room in rooms
            if point_in_polygon((opening.x, opening.y), room.polygon)
            or point_in_polygon(opening.end, room.polygon)
        ]
        for i, first in enumerate(touching):
            for second in touching[i + 1 :]:
                adjacency.setdefault(first.id, set()).add(second.id)
                adjacency.setdefault(second.id, set()).add(first.id)
    entrance = _entrance_room(ir, rooms)
    if entrance is None:
        return []
    seen: set[str] = {entrance.id}
    stack = [entrance.id]
    while stack:
        current = stack.pop()
        for neighbour in adjacency.get(current, set()):
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    unreachable = [room for room in rooms if room.id not in seen]
    if not unreachable:
        return []
    return [
        Diagnostic(
            rule_id="route.continuity",
            status=STATUS_NON_COMPLIANT,
            severity=SEVERITY_BLOCKING,
            message=(
                f"以下区域从入口无法通过门洞到达：{', '.join(room.name or room.id for room in unreachable)}"
            ),
            elements=[room.id for room in unreachable],
        )
    ]


def _entrance_door(ir: IR) -> Opening | None:
    for opening in ir.openings:
        if opening.type == "door":
            return opening
    return None


def _entrance_room(ir: IR, rooms: list[Room]) -> Room | None:
    for opening in ir.openings:
        if opening.type != "door":
            continue
        room = _room_of_point(rooms, (opening.x, opening.y))
        if room is not None:
            return room
    return rooms[0] if rooms else None


def _is_wet(item: Furniture) -> bool:
    info = resolve(item.kind)
    if info is not None:
        return info.wet
    return item.kind.lower() in WET_KINDS


def _is_electrical(item: Furniture) -> bool:
    info = resolve(item.kind)
    if info is not None:
        return info.electrical
    return item.kind.lower() in ELECTRICAL_KINDS


def _check_splash_zone(ir: IR) -> list[Diagnostic]:
    """No socket or switch in the splash zone of a shower or bath.

    Electrical points are read from both the MEP list and any legacy socket
    placed as furniture, so older plans keep being checked.
    """
    findings: list[Diagnostic] = []
    zone = _limit(ir, "safety.splash_zone_mm", SPLASH_ZONE_MM)
    wet = [item for item in ir.furniture if _is_wet(item)]
    for wet_item in wet:
        for item in ir.furniture:
            if item.id == wet_item.id or not _is_electrical(item):
                continue
            gap = distance(wet_item.center, item.center)
            if gap > zone:
                continue
            findings.append(
                Diagnostic(
                    rule_id="safety.splash_zone",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"{item.name or item.id} 距 {wet_item.name or wet_item.id} 仅 {_mm(gap)}，"
                        f"落在 {_mm(zone)} 溅水范围内"
                    ),
                    elements=[item.id, wet_item.id],
                    measured=gap,
                    required=zone,
                )
            )
        for point in ir.mep:
            if point.type not in {"socket", "switch"}:
                continue
            gap = distance(wet_item.center, (point.x, point.y))
            if gap > zone:
                continue
            findings.append(
                Diagnostic(
                    rule_id="safety.splash_zone",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"水电点位 {point.id or '未命名'}（{point.type}）距 "
                        f"{wet_item.name or wet_item.id} 仅 {_mm(gap)}，"
                        f"落在 {_mm(zone)} 溅水范围内"
                    ),
                    elements=[point.id, wet_item.id],
                    measured=gap,
                    required=zone,
                )
            )
    return findings


def _check_mep_types(ir: IR) -> list[Diagnostic]:
    unknown = [point for point in ir.mep if point.type not in MEP_TYPES]
    if not unknown:
        return []
    return [
        Diagnostic(
            rule_id="mep.unknown_type",
            status=STATUS_NOT_VERIFIABLE,
            severity=SEVERITY_IMPROVEMENT,
            message=(
                "水电点位类型不在支持范围内，相关规则无法判定："
                + ", ".join(sorted({point.type or "(空)" for point in unknown}))
            ),
            elements=[point.id for point in unknown],
        )
    ]


def _check_wet_appliance_services(ir: IR) -> list[Diagnostic]:
    """Water-using appliances need a supply and a drain nearby."""
    needing = [
        item
        for item in ir.furniture
        if (info := resolve(item.kind)) is not None and (info.needs_water or info.needs_drain)
    ]
    if not needing:
        return []
    if not ir.mep:
        return [
            Diagnostic(
                rule_id="mep.not_recorded",
                status=STATUS_NOT_VERIFIABLE,
                severity=SEVERITY_SIGNIFICANT,
                message=(
                    f"方案里有 {len(needing)} 件用水器具，但没有录入任何水电点位；"
                    "给水/排水是否到位无法判定"
                ),
                elements=[item.id for item in needing],
            )
        ]
    findings: list[Diagnostic] = []
    for item in needing:
        info = resolve(item.kind)
        assert info is not None
        missing: list[str] = []
        if info.needs_water and not _has_point_near(ir, item, "water"):
            missing.append("给水")
        if info.needs_drain and not _has_point_near(ir, item, "drain"):
            missing.append("排水")
        if not missing:
            continue
        findings.append(
            Diagnostic(
                rule_id="mep.wet_appliance_services",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_IMPROVEMENT,
                message=(
                    f"{item.name or info.label_zh} 附近 "
                    f"{_mm(_limit(ir, 'mep.service_radius_mm', WET_APPLIANCE_SERVICE_RADIUS_MM))} 内"
                    f"缺少{'、'.join(missing)}点位"
                ),
                elements=[item.id],
                measured=_limit(ir, "mep.service_radius_mm", WET_APPLIANCE_SERVICE_RADIUS_MM),
                required=_limit(ir, "mep.service_radius_mm", WET_APPLIANCE_SERVICE_RADIUS_MM),
            )
        )
    return findings


def _has_point_near(ir: IR, item: Furniture, point_type: str) -> bool:
    radius = _limit(ir, "mep.service_radius_mm", WET_APPLIANCE_SERVICE_RADIUS_MM)
    return any(
        point.type == point_type and distance(item.center, (point.x, point.y)) <= radius
        for point in ir.mep
    )


def _check_robustness(ir: IR) -> list[Diagnostic]:
    """Flag clearances whose margin is thinner than the placement tolerance."""
    findings: list[Diagnostic] = []
    for finding in _check_storage_clearance(ir):
        findings.append(
            Diagnostic(
                rule_id="robustness.tolerance",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_SIGNIFICANT,
                message=(
                    f"{finding.message}；余量不足 {_mm(PLACEMENT_TOLERANCE_MM)}，摆放偏差即会失效"
                ),
                elements=finding.elements,
                measured=finding.measured,
                required=finding.required,
            )
        )
    return findings


def _always_not_verifiable() -> list[Diagnostic]:
    return [
        Diagnostic(
            rule_id="ventilation.service_room",
            status=STATUS_NOT_VERIFIABLE,
            severity=SEVERITY_BLOCKING,
            message="卫生间与厨房的通风是否符合当地规范，需现场或专业核算",
            elements=[],
        )
    ]


# --- entry point -------------------------------------------------------------


def check_drawing(ir: IR, changed_ids: Sequence[str] | None = None) -> list[Diagnostic]:
    """Run every rule over *ir*.

    When *changed_ids* is given, only findings that touch one of those
    elements are returned — the incremental mode the repair loop relies on.
    """
    findings: list[Diagnostic] = []
    findings.extend(_check_wall_gaps(ir))
    findings.extend(_check_wall_overlaps(ir))
    findings.extend(_check_rooms(ir))
    findings.extend(_check_openings(ir))
    findings.extend(_check_element_overlaps(ir))
    findings.extend(_check_storage_clearance(ir))
    findings.extend(_check_appliance_clearance(ir))
    findings.extend(_check_sanitary_clearance(ir))
    findings.extend(_check_kitchen_aisle(ir))
    findings.extend(_check_unknown_kinds(ir))
    findings.extend(_check_fixed_off_wall(ir))
    findings.extend(_check_service_window(ir))
    findings.extend(_check_entry_sightline(ir))
    findings.extend(_check_door_swing(ir))
    findings.extend(_check_room_occupancy(ir))
    findings.extend(_check_seat_height(ir))
    findings.extend(_check_tall_furniture(ir))
    findings.extend(_check_door_handle(ir))
    findings.extend(_check_pair_clearance(ir))
    findings.extend(_check_service_room_use(ir))
    findings.extend(_check_mep_types(ir))
    findings.extend(_check_wet_appliance_services(ir))
    findings.extend(_check_window_ratio(ir))
    findings.extend(_check_min_area(ir))
    findings.extend(_check_route_continuity(ir))
    findings.extend(_check_route_width(ir))
    findings.extend(_check_privacy_path(ir))
    findings.extend(_check_seat_clearance(ir))
    findings.extend(_check_splash_zone(ir))
    findings.extend(_check_robustness(ir))
    findings.extend(_always_not_verifiable())

    order = {SEVERITY_BLOCKING: 0, SEVERITY_SIGNIFICANT: 1, SEVERITY_IMPROVEMENT: 2}
    findings.sort(key=lambda item: (order.get(item.severity, 3), item.rule_id))

    if changed_ids is not None:
        touched = {str(item) for item in changed_ids}
        findings = [finding for finding in findings if touched.intersection(finding.elements)]
    return findings
