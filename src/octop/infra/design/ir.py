"""Design IR: the structured state a renovation plan is made of.

The IR is the contract between the expert (which writes it), the MiniApp
page (which draws it) and this kernel (which checks it). Everything is plain
JSON so it can travel over HTTP, be stored in an app row, and be diffed.

Coordinates are millimetres on a y-up plane, origin at the bottom-left.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Final

from octop.infra.design.catalog import CatalogItem, normalize_kind, resolve
from octop.infra.design.geometry import point_to_segment_distance

#: Wall segments closer than this are treated as connected.
WALL_TOLERANCE_MM: Final = 20.0

#: Placement tolerance used by the robustness rule.
PLACEMENT_TOLERANCE_MM: Final = 20.0

OPENING_TYPES: Final = frozenset({"door", "window", "sliding_door", "opening"})


@dataclass(frozen=True)
class Wall:
    """A wall segment given by its two end points and its thickness."""

    id: str
    x1: float
    y1: float
    x2: float
    y2: float
    t: float = 120.0

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def is_degenerate(self) -> bool:
        return self.length <= WALL_TOLERANCE_MM


@dataclass(frozen=True)
class Opening:
    """A door or window cut into a wall."""

    id: str
    type: str
    x: float
    y: float
    width: float
    angle: float = 0.0
    host: str = ""
    #: Opening height in millimetres (door 2100, window 1800 by default).
    h: float = 1800.0
    #: Hinge side for a door: ``start`` (at x,y) or ``end`` (at the far jamb).
    hinge: str = "start"

    @property
    def is_window(self) -> bool:
        return self.type == "window"

    @property
    def heading(self) -> float:
        return math.radians(self.angle)

    @property
    def end(self) -> tuple[float, float]:
        return (
            self.x + math.cos(self.heading) * self.width,
            self.y + math.sin(self.heading) * self.width,
        )


@dataclass(frozen=True)
class Furniture:
    """A placed object, axis-aligned unless ``rot`` says otherwise."""

    id: str
    x: float
    y: float
    w: float
    d: float
    rot: float = 0.0
    kind: str = ""
    name: str = ""
    #: Id of the item this one sits on or inside (a sink in a counter run).
    #: Hosted items are allowed to overlap their host and borrow its clearance.
    host: str = ""
    #: Direction the user faces when using the item, in degrees (0 = +x).
    #: Unset means "no declared front"; seat rules then try every side.
    facing: float | None = None
    #: Overall height in millimetres (catalog default when not given).
    h: float = 800.0

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.d / 2)

    @property
    def catalog(self) -> CatalogItem | None:
        """The catalog entry for this item, or ``None`` when the kind is unknown."""
        return resolve(self.kind)

    @property
    def category(self) -> str:
        item = self.catalog
        return item.category if item else ""

    def corners(self) -> list[tuple[float, float]]:
        """The four footprint corners, rotated about the footprint centre."""
        rad = math.radians(self.rot)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        cx, cy = self.center
        half_w, half_d = self.w / 2, self.d / 2
        points = [
            (cx - half_w, cy - half_d),
            (cx + half_w, cy - half_d),
            (cx + half_w, cy + half_d),
            (cx - half_w, cy + half_d),
        ]
        return [
            (cx + (px - cx) * cos_r - (py - cy) * sin_r, cy + (px - cx) * sin_r + (py - cy) * cos_r)
            for px, py in points
        ]


@dataclass(frozen=True)
class Room:
    """A named space, either given by the expert or derived from the walls."""

    id: str
    name: str
    polygon: list[tuple[float, float]] = field(default_factory=list)
    area_mm2: float = 0.0


@dataclass(frozen=True)
class MEPPoint:
    """A water or electrical point, kept apart from furniture."""

    id: str
    type: str
    x: float
    y: float
    height: float = 0.0


@dataclass(frozen=True)
class IR:
    """The whole plan."""

    walls: list[Wall] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    furniture: list[Furniture] = field(default_factory=list)
    rooms: list[Room] = field(default_factory=list)
    mep: list[MEPPoint] = field(default_factory=list)
    #: Clear floor-to-ceiling height; used by the tall-furniture rule.
    ceiling_height: float = 2800.0
    #: Per-plan threshold overrides, e.g. ``route.width_mm``. Unset keys keep
    #: the documented defaults; see ``docs/interior-design-miniapp-plan.md``.
    thresholds: dict[str, float] = field(default_factory=dict)

    def wall(self, wall_id: str) -> Wall | None:
        for wall in self.walls:
            if wall.id == wall_id:
                return wall
        return None


def _num(raw: object, default: float = 0.0) -> float:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return value


def _points(raw: object) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((_num(item[0]), _num(item[1])))
    return out


def parse_wall(raw: dict[str, Any]) -> Wall:
    return Wall(
        id=str(raw.get("id") or ""),
        x1=_num(raw.get("x1")),
        y1=_num(raw.get("y1")),
        x2=_num(raw.get("x2")),
        y2=_num(raw.get("y2")),
        t=_num(raw.get("t"), 120.0) or 120.0,
    )


def parse_opening(raw: dict[str, Any]) -> Opening:
    kind = str(raw.get("type") or "door").strip() or "door"
    if kind not in OPENING_TYPES:
        kind = "opening"
    default_h = 2100.0 if kind in {"door", "sliding_door"} else 1800.0
    hinge = str(raw.get("hinge") or "start").strip().lower()
    if hinge not in {"start", "end"}:
        hinge = "start"
    return Opening(
        id=str(raw.get("id") or ""),
        type=kind,
        x=_num(raw.get("x")),
        y=_num(raw.get("y")),
        width=_num(raw.get("width"), 900.0) or 900.0,
        angle=_num(raw.get("angle")),
        host=str(raw.get("host") or ""),
        h=_num(raw.get("h"), default_h) or default_h,
        hinge=hinge,
    )


def parse_furniture(raw: dict[str, Any]) -> Furniture:
    kind = normalize_kind(str(raw.get("kind") or ""))
    item = resolve(kind)
    default_w = item.w if item else 600.0
    default_d = item.d if item else 400.0
    default_h = item.height if item else 800.0
    return Furniture(
        id=str(raw.get("id") or ""),
        x=_num(raw.get("x")),
        y=_num(raw.get("y")),
        w=_num(raw.get("w"), default_w) or default_w,
        d=_num(raw.get("d"), default_d) or default_d,
        rot=_num(raw.get("rot")),
        kind=kind,
        name=str(raw.get("name") or ""),
        host=str(raw.get("host") or ""),
        facing=(_num(raw.get("facing")) if raw.get("facing") is not None else None),
        h=_num(raw.get("h"), default_h) or default_h,
    )


def parse_mep_point(raw: dict[str, Any]) -> MEPPoint:
    return MEPPoint(
        id=str(raw.get("id") or ""),
        type=str(raw.get("type") or "").strip().casefold(),
        x=_num(raw.get("x")),
        y=_num(raw.get("y")),
        height=_num(raw.get("height")),
    )


def parse_room(raw: dict[str, Any]) -> Room:
    polygon = _points(raw.get("polygon"))
    return Room(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        polygon=polygon,
        area_mm2=_num(raw.get("area_mm2")),
    )


def parse_ir(raw: object) -> IR:
    """Build an :class:`IR` from the JSON payload the page and expert send."""
    if not isinstance(raw, dict):
        return IR()

    def items(key: str) -> list[dict[str, Any]]:
        value = raw.get(key)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    ceiling = raw.get("ceiling_height")
    thresholds: dict[str, float] = {}
    raw_thresholds = raw.get("thresholds")
    if isinstance(raw_thresholds, dict):
        for key, value in raw_thresholds.items():
            if isinstance(value, bool):
                continue
            try:
                thresholds[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    return IR(
        walls=[parse_wall(item) for item in items("walls")],
        openings=[parse_opening(item) for item in items("openings")],
        furniture=[parse_furniture(item) for item in items("furniture")],
        rooms=[parse_room(item) for item in items("rooms")],
        mep=[parse_mep_point(item) for item in items("mep")],
        ceiling_height=_num(ceiling, 2800.0) or 2800.0,
        thresholds=thresholds,
    )


def ir_to_dict(ir: IR) -> dict[str, Any]:
    """Serialise an :class:`IR` back to the wire format."""
    return {
        "version": 1,
        "walls": [
            {
                "id": w.id,
                "x1": w.x1,
                "y1": w.y1,
                "x2": w.x2,
                "y2": w.y2,
                "t": w.t,
            }
            for w in ir.walls
        ],
        "openings": [
            {
                "id": o.id,
                "type": o.type,
                "x": o.x,
                "y": o.y,
                "width": o.width,
                "angle": o.angle,
                "host": o.host,
                "h": o.h,
                "hinge": o.hinge,
            }
            for o in ir.openings
        ],
        "furniture": [
            {
                "id": f.id,
                "x": f.x,
                "y": f.y,
                "w": f.w,
                "d": f.d,
                "rot": f.rot,
                "kind": f.kind,
                "name": f.name,
                "host": f.host,
                "facing": f.facing,
                "h": f.h,
            }
            for f in ir.furniture
        ],
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "polygon": [list(point) for point in r.polygon],
                "area_mm2": r.area_mm2,
            }
            for r in ir.rooms
        ],
        "ceiling_height": ir.ceiling_height,
        "thresholds": dict(ir.thresholds),
        "mep": [
            {
                "id": m.id,
                "type": m.type,
                "x": m.x,
                "y": m.y,
                "height": m.height,
            }
            for m in ir.mep
        ],
    }


def nearest_wall(ir: IR, point: tuple[float, float]) -> tuple[Wall | None, float]:
    """Closest wall to *point* and the distance to its centre line."""
    best: Wall | None = None
    best_gap = math.inf
    for wall in ir.walls:
        if wall.is_degenerate:
            continue
        gap = point_to_segment_distance(point, (wall.x1, wall.y1), (wall.x2, wall.y2))
        if gap < best_gap:
            best_gap = gap
            best = wall
    return best, best_gap


def ir_from_rows(rows: list[dict[str, Any]]) -> IR:
    """Build an :class:`IR` from the MiniApp ``main`` table rows.

    Each row carries a ``kind`` discriminator and a JSON ``payload``; this is
    the same shape the expert writes, so the kernel can be fed straight from
    the app tables.
    """
    walls: list[Wall] = []
    openings: list[Opening] = []
    furniture: list[Furniture] = []
    rooms: list[Room] = []
    mep: list[MEPPoint] = []

    for row in rows:
        kind = str(row.get("kind") or "").strip().lower()
        payload = row.get("payload")
        data: dict[str, Any] = {}
        if isinstance(payload, str) and payload.strip():
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
        elif isinstance(payload, dict):
            data = dict(payload)

        element_id = str(row.get("element_id") or "")
        name = str(row.get("name") or "")
        data.setdefault("id", element_id)
        if name and not data.get("name"):
            data["name"] = name

        if kind == "wall":
            walls.append(parse_wall(data))
        elif kind == "opening":
            openings.append(parse_opening(data))
        elif kind == "furniture":
            furniture.append(parse_furniture(data))
        elif kind == "room":
            rooms.append(parse_room(data))
        elif kind == "mep":
            mep.append(parse_mep_point(data))

    return IR(walls=walls, openings=openings, furniture=furniture, rooms=rooms, mep=mep)
