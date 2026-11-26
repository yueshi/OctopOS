"""Planar geometry helpers (standard library only).

Everything here works on plain ``(x, y)`` tuples in millimetres. The helpers
are deliberately small and total: a rule that cannot measure something must
say "not verifiable" rather than guess.
"""

from __future__ import annotations

import math
from typing import Final

Point = tuple[float, float]

#: Endpoint coordinates are snapped to this grid before matching.
SNAP_MM: Final = 1.0


def snap(value: float) -> float:
    """Quantise a coordinate so two ends of the same wall meet exactly."""
    return round(value / SNAP_MM) * SNAP_MM


def snap_point(point: Point) -> Point:
    return (snap(point[0]), snap(point[1]))


def polygon_area(points: list[Point]) -> float:
    """Signed area of a polygon (shoelace). Positive is counter-clockwise."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2


def polygon_perimeter(points: list[Point]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    count = len(points)
    for index in range(count):
        total += distance(points[index], points[(index + 1) % count])
    return total


def polygon_distance(a: list[Point], b: list[Point]) -> float:
    """Shortest gap between two polygons (0 when they overlap or touch)."""
    if len(a) < 3 or len(b) < 3:
        return math.inf
    for point in a:
        if point_in_polygon(point, b):
            return 0.0
    for point in b:
        if point_in_polygon(point, a):
            return 0.0
    best = math.inf
    for point in a:
        best = min(best, point_to_polygon_distance(point, b))
    for point in b:
        best = min(best, point_to_polygon_distance(point, a))
    return best


def polygon_centroid(points: list[Point]) -> Point:
    if not points:
        return (0.0, 0.0)
    area = polygon_area(points)
    if abs(area) < 1e-9:
        return points[0]
    cx = cy = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    factor = 1 / (6 * area)
    return (cx * factor, cy * factor)


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def point_to_segment_distance(point: Point, a: Point, b: Point) -> float:
    """Shortest distance from *point* to the segment ``a``-``b``."""
    ax, ay = a
    bx, by = b
    px, py = point
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_on_segment(point: Point, a: Point, b: Point, tolerance: float = 1.0) -> bool:
    return point_to_segment_distance(point, a, b) <= tolerance


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def subtract(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _orientation(a: Point, b: Point, c: Point) -> float:
    value = cross(subtract(b, a), subtract(c, a))
    if abs(value) < 1e-9:
        return 0.0
    return 1.0 if value > 0 else -1.0


def segments_intersect(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    """True when the two segments share at least one point."""
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and point_on_segment(b1, a1, a2):
        return True
    if o2 == 0 and point_on_segment(b2, a1, a2):
        return True
    if o3 == 0 and point_on_segment(a1, b1, b2):
        return True
    return bool(o4 == 0 and point_on_segment(a2, b1, b2))


def collinear_overlap_length(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    """Length shared by two collinear segments (0 when they are not collinear)."""
    direction = subtract(a2, a1)
    length = math.hypot(direction[0], direction[1])
    if length < 1e-9:
        return 0.0
    unit = (direction[0] / length, direction[1] / length)
    for point in (b1, b2):
        offset = cross(subtract(point, a1), unit)
        if abs(offset) > 1.0:
            return 0.0
    projections = [
        (point[0] - a1[0]) * unit[0] + (point[1] - a1[1]) * unit[1] for point in (b1, b2)
    ]
    b_low = min(projections)
    b_high = max(projections)
    a_low = 0.0
    a_high = length
    low = max(a_low, b_low)
    high = min(a_high, b_high)
    return max(0.0, high - low)


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    """Ray-casting test; points on the boundary count as inside."""
    if len(polygon) < 3:
        return False
    inside = False
    count = len(polygon)
    px, py = point
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if point_on_segment(point, (x1, y1), (x2, y2)):
            return True
        if (y1 > py) != (y2 > py):
            t = (py - y1) / (y2 - y1)
            x_cross = x1 + t * (x2 - x1)
            if px < x_cross:
                inside = not inside
    return inside


def ray_segment_distance(origin: Point, direction: Point, a: Point, b: Point) -> float | None:
    """Distance along a ray to a segment, or ``None`` when it does not hit.

    ``direction`` must be a unit vector; hits behind the origin are ignored.
    """
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    denominator = direction[0] * dy - direction[1] * dx
    if abs(denominator) < 1e-9:
        return None
    origin_to_a = (a[0] - origin[0], a[1] - origin[1])
    t = (origin_to_a[0] * dy - origin_to_a[1] * dx) / denominator
    u = (origin_to_a[0] * direction[1] - origin_to_a[1] * direction[0]) / denominator
    if t <= 1e-6 or u < -1e-6 or u > 1 + 1e-6:
        return None
    return t


def point_to_polygon_distance(point: Point, polygon: list[Point]) -> float:
    """Shortest distance from *point* to a polygon's boundary."""
    if len(polygon) < 2:
        return math.inf
    best = math.inf
    count = len(polygon)
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        best = min(best, point_to_segment_distance(point, a, b))
    return best


def support_width(
    half_extent_a: float, half_extent_b: float, axis_a: Point, axis_b: Point, axis: Point
) -> float:
    """Extent of a rotated rectangle along *axis*."""
    return half_extent_a * abs(dot(axis_a, axis)) + half_extent_b * abs(dot(axis_b, axis))


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def convex_polygons_overlap(a: list[Point], b: list[Point]) -> bool:
    """Separating-axis test for two convex polygons."""
    if len(a) < 3 or len(b) < 3:
        return False
    for polygon, other in ((a, b), (b, a)):
        count = len(polygon)
        for index in range(count):
            p1 = polygon[index]
            p2 = polygon[(index + 1) % count]
            axis = (-(p2[1] - p1[1]), p2[0] - p1[0])
            length = math.hypot(axis[0], axis[1])
            if length < 1e-9:
                continue
            axis = (axis[0] / length, axis[1] / length)
            a_range = _project(polygon, axis)
            b_range = _project(other, axis)
            if a_range[1] < b_range[0] - 1e-6 or b_range[1] < a_range[0] - 1e-6:
                return False
    return True


def _project(points: list[Point], axis: Point) -> tuple[float, float]:
    values = [point[0] * axis[0] + point[1] * axis[1] for point in points]
    return (min(values), max(values))


def segment_interval(a1: Point, a2: Point) -> tuple[float, float]:
    """Parameter interval of a segment projected on its own direction."""
    length = distance(a1, a2)
    if length < 1e-9:
        return (0.0, 0.0)
    unit = ((a2[0] - a1[0]) / length, (a2[1] - a1[1]) / length)
    values = [(point[0] - a1[0]) * unit[0] + (point[1] - a1[1]) * unit[1] for point in (a1, a2)]
    return (min(values), max(values))
