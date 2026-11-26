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

from octop.infra.design.catalog import MEP_TYPES, resolve
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

    ``select`` returns the required clearance for an item, or ``None`` to skip
    it. The measurement is taken from the item's front face towards the room,
    so a wide wardrobe anchored to a wall is measured from its own face.
    """
    findings: list[Diagnostic] = []
    for item in ir.furniture:
        if item.host:
            # Access is measured at the host, not at the inset item.
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
        front_edge = _front_edge_point(item, normal)
        by_id = {candidate.id: candidate for candidate in ir.furniture}
        blockers: list[tuple[Furniture, float]] = []
        for other in ir.furniture:
            if other.id == item.id:
                continue
            if _hosted_within(other, item.id, by_id):
                # The sink inside this counter is not something standing in
                # front of it.
                continue
            distance = point_to_polygon_distance(front_edge, other.corners())
            if distance < required:
                blockers.append((other, distance))
        if not blockers:
            continue
        closest = min(blockers, key=lambda pair: pair[1])
        info = resolve(item.kind)
        label = item.name or (info.label_zh if info else item.id)
        findings.append(
            Diagnostic(
                rule_id=rule_id,
                status=STATUS_NON_COMPLIANT,
                severity=severity,
                message=(
                    f"{label} 前方净空 {_mm(closest[1])}，小于要求的 {_mm(required)}，"
                    f"被 {closest[0].name or closest[0].id} 占用"
                ),
                elements=[item.id, *[other.id for other, _ in blockers]],
                measured=closest[1],
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
        if info is not None:
            if info.category != "storage":
                return None
            if info.front_clearance is not None:
                return info.front_clearance or None
            return STORAGE_FRONT_CLEARANCE_MM
        return STORAGE_FRONT_CLEARANCE_MM if item.kind.lower() in STORAGE_KINDS else None

    return _front_clearance(
        ir,
        rule_id="clearance.wardrobe_front",
        select=select,
        severity=SEVERITY_BLOCKING,
    )


def _check_appliance_clearance(ir: IR) -> list[Diagnostic]:
    def select(item: Furniture) -> float | None:
        info = resolve(item.kind)
        if info is None or not info.appliance or not info.front_clearance:
            return None
        return info.front_clearance

    return _front_clearance(
        ir,
        rule_id="clearance.appliance_front",
        select=select,
        severity=SEVERITY_SIGNIFICANT,
    )


def _check_sanitary_clearance(ir: IR) -> list[Diagnostic]:
    def select(item: Furniture) -> float | None:
        info = resolve(item.kind)
        if info is None or info.category != "sanitary" or not info.front_clearance:
            return None
        return info.front_clearance

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
        return info.front_clearance

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


def _direction_clearance(ir: IR, item: Furniture, direction: Point) -> float:
    """How far the space in front of *item* stays clear in *direction*."""
    half = support_width(
        item.w / 2,
        item.d / 2,
        (math.cos(math.radians(item.rot)), math.sin(math.radians(item.rot))),
        (-math.sin(math.radians(item.rot)), math.cos(math.radians(item.rot))),
        direction,
    )
    origin = (item.center[0] + direction[0] * half, item.center[1] + direction[1] * half)
    best = SEAT_PROBE_MM

    for other in ir.furniture:
        if other.id == item.id:
            continue
        corners = other.corners()
        for index in range(len(corners)):
            hit = ray_segment_distance(
                origin, direction, corners[index], corners[(index + 1) % len(corners)]
            )
            if hit is not None and hit < best:
                best = hit

    for wall in ir.walls:
        if wall.is_degenerate:
            continue
        hit = ray_segment_distance(origin, direction, (wall.x1, wall.y1), (wall.x2, wall.y2))
        if hit is None:
            continue
        # A door in this wall is an opening, not a blocker.
        length = wall.length
        unit = ((wall.x2 - wall.x1) / length, (wall.y2 - wall.y1) / length)
        hit_point = (origin[0] + direction[0] * hit, origin[1] + direction[1] * hit)
        position = (hit_point[0] - wall.x1) * unit[0] + (hit_point[1] - wall.y1) * unit[1]
        if any(low <= position <= high for low, high in _passable_spans(ir, wall)):
            continue
        free = max(0.0, hit - wall.t / 2)
        if free < best:
            best = free
    return best


def _seat_directions(item: Furniture) -> list[Point]:
    if item.facing is not None:
        rad = math.radians(item.facing)
        return [(math.cos(rad), math.sin(rad))]
    rad = math.radians(item.rot)
    u = (math.cos(rad), math.sin(rad))
    v = (-math.sin(rad), math.cos(rad))
    return [u, (-u[0], -u[1]), v, (-v[0], -v[1])]


def _check_seat_clearance(ir: IR) -> list[Diagnostic]:
    """A seat needs room to stand up (and better, to walk behind)."""
    findings: list[Diagnostic] = []
    for item in ir.furniture:
        info = resolve(item.kind)
        if item.host or info is None or info.category != "seat":
            continue
        best = max(_direction_clearance(ir, item, d) for d in _seat_directions(item))
        if best >= SEAT_WALK_MM:
            continue
        walk = best >= SEAT_STAND_MM
        findings.append(
            Diagnostic(
                rule_id="clearance.seat_walk" if walk else "clearance.seat_stand",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_IMPROVEMENT if walk else SEVERITY_SIGNIFICANT,
                message=(
                    f"{item.name or info.label_zh} 前净空 {_mm(best)}，"
                    + (
                        f"可站立但通行不足 {_mm(SEAT_WALK_MM)}"
                        if walk
                        else f"不足站立所需的 {_mm(SEAT_STAND_MM)}"
                    )
                ),
                elements=[item.id],
                measured=best,
                required=SEAT_WALK_MM if walk else SEAT_STAND_MM,
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
    half_width = ROUTE_WIDTH_MM / 2
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
                    f"在 {_mm(ROUTE_WIDTH_MM)} 净宽要求下，从入口无法到达{room.name or room.id}"
                    "（门洞存在，但通路被家具或墙体收窄）"
                ),
                elements=[room.id],
                measured=ROUTE_WIDTH_MM,
                required=ROUTE_WIDTH_MM,
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
    window_area = sum(
        opening.width * WINDOW_HEIGHT_MM for opening in ir.openings if opening.is_window
    )
    required = floor_area / WINDOW_FLOOR_RATIO
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
        kind = room.name.strip().lower()
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
    wet = [item for item in ir.furniture if _is_wet(item)]
    for wet_item in wet:
        for item in ir.furniture:
            if item.id == wet_item.id or not _is_electrical(item):
                continue
            gap = distance(wet_item.center, item.center)
            if gap > SPLASH_ZONE_MM:
                continue
            findings.append(
                Diagnostic(
                    rule_id="safety.splash_zone",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"{item.name or item.id} 距 {wet_item.name or wet_item.id} 仅 {_mm(gap)}，"
                        f"落在 {_mm(SPLASH_ZONE_MM)} 溅水范围内"
                    ),
                    elements=[item.id, wet_item.id],
                    measured=gap,
                    required=SPLASH_ZONE_MM,
                )
            )
        for point in ir.mep:
            if point.type not in {"socket", "switch"}:
                continue
            gap = distance(wet_item.center, (point.x, point.y))
            if gap > SPLASH_ZONE_MM:
                continue
            findings.append(
                Diagnostic(
                    rule_id="safety.splash_zone",
                    status=STATUS_NON_COMPLIANT,
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"水电点位 {point.id or '未命名'}（{point.type}）距 "
                        f"{wet_item.name or wet_item.id} 仅 {_mm(gap)}，"
                        f"落在 {_mm(SPLASH_ZONE_MM)} 溅水范围内"
                    ),
                    elements=[point.id, wet_item.id],
                    measured=gap,
                    required=SPLASH_ZONE_MM,
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
                    f"{item.name or info.label_zh} 附近 {_mm(WET_APPLIANCE_SERVICE_RADIUS_MM)} 内"
                    f"缺少{'、'.join(missing)}点位"
                ),
                elements=[item.id],
                measured=WET_APPLIANCE_SERVICE_RADIUS_MM,
                required=WET_APPLIANCE_SERVICE_RADIUS_MM,
            )
        )
    return findings


def _has_point_near(ir: IR, item: Furniture, point_type: str) -> bool:
    return any(
        point.type == point_type
        and distance(item.center, (point.x, point.y)) <= WET_APPLIANCE_SERVICE_RADIUS_MM
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
