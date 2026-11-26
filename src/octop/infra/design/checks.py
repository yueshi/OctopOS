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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

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
    for i, first in enumerate(ir.furniture):
        for second in ir.furniture[i + 1 :]:
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


def _check_storage_clearance(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for item in ir.furniture:
        if item.kind.lower() not in STORAGE_KINDS:
            continue
        wall, gap = _supporting_wall(item, ir.walls)
        if wall is None or gap > max(wall.t, 40.0):
            continue
        normal = _wall_normal_towards(wall, item.center)
        if normal is None:
            continue
        front_edge = _front_edge_point(item, normal)
        blockers: list[tuple[Furniture, float]] = []
        for other in ir.furniture:
            if other.id == item.id:
                continue
            distance = point_to_polygon_distance(front_edge, other.corners())
            if distance < STORAGE_FRONT_CLEARANCE_MM:
                blockers.append((other, distance))
        if not blockers:
            continue
        closest = min(blockers, key=lambda pair: pair[1])
        findings.append(
            Diagnostic(
                rule_id="clearance.wardrobe_front",
                status=STATUS_NON_COMPLIANT,
                severity=SEVERITY_BLOCKING,
                message=(
                    f"{item.name or item.id} 前方净空 {_mm(closest[1])}，"
                    f"小于要求的 {_mm(STORAGE_FRONT_CLEARANCE_MM)}，"
                    f"被 {closest[0].name or closest[0].id} 占用"
                ),
                elements=[item.id, *[other.id for other, _ in blockers]],
                measured=closest[1],
                required=STORAGE_FRONT_CLEARANCE_MM,
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


def _entrance_room(ir: IR, rooms: list[Room]) -> Room | None:
    for opening in ir.openings:
        if opening.type != "door":
            continue
        room = _room_of_point(rooms, (opening.x, opening.y))
        if room is not None:
            return room
    return rooms[0] if rooms else None


def _check_splash_zone(ir: IR) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    wet = [item for item in ir.furniture if item.kind.lower() in WET_KINDS]
    electrical = [item for item in ir.furniture if item.kind.lower() in ELECTRICAL_KINDS]
    for wet_item in wet:
        for item in electrical:
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
    return findings


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
    findings.extend(_check_window_ratio(ir))
    findings.extend(_check_min_area(ir))
    findings.extend(_check_route_continuity(ir))
    findings.extend(_check_splash_zone(ir))
    findings.extend(_check_robustness(ir))
    findings.extend(_always_not_verifiable())

    order = {SEVERITY_BLOCKING: 0, SEVERITY_SIGNIFICANT: 1, SEVERITY_IMPROVEMENT: 2}
    findings.sort(key=lambda item: (order.get(item.severity, 3), item.rule_id))

    if changed_ids is not None:
        touched = {str(item) for item in changed_ids}
        findings = [finding for finding in findings if touched.intersection(finding.elements)]
    return findings
