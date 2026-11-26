"""Atomic IR operations.

The expert turns a sentence ("move the wardrobe to the west wall") into a
small list of operations; the kernel applies them to the IR so the result is
computed, not described. Operations are pure: they take an IR and return a
new IR plus what was applied and what was rejected.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from octop.infra.design.ir import IR, ir_to_dict, parse_ir


@dataclass
class OpResult:
    """Outcome of one operation batch."""

    ir: IR
    applied: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ir": ir_to_dict(self.ir),
            "applied": self.applied,
            "rejected": list(self.rejected),
        }


def _clone(ir: IR) -> IR:
    raw = ir_to_dict(ir)
    return parse_ir(copy.deepcopy(raw))


def apply_ops(ir: IR, ops: list[dict[str, Any]]) -> OpResult:
    """Apply *ops* to *ir* and return the new IR.

    Supported operations:

    ``add``    ``{"op": "add", "kind": "furniture", "element": {...}}``
    ``update`` ``{"op": "update", "id": "F-001", "patch": {"x": 1200}}``
    ``move``   ``{"op": "move", "id": "F-001", "dx": 200, "dy": 0}``
    ``delete`` ``{"op": "delete", "id": "F-001"}``
    """
    result = _clone(ir)
    applied = 0
    rejected: list[dict[str, Any]] = []

    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            rejected.append({"index": index, "reason": "operation must be an object"})
            continue
        name = str(op.get("op") or "").strip()
        try:
            if name == "add":
                _add(result, op)
            elif name == "update":
                _update(result, op)
            elif name == "move":
                _move(result, op)
            elif name == "delete":
                _delete(result, op)
            else:
                rejected.append({"index": index, "reason": f"unknown operation: {name!r}"})
                continue
        except ValueError as exc:
            rejected.append({"index": index, "reason": str(exc)})
            continue
        applied += 1

    return OpResult(ir=result, applied=applied, rejected=rejected)


def _add(ir: IR, op: dict[str, Any]) -> None:
    kind = str(op.get("kind") or "").strip().lower()
    element = op.get("element")
    if not isinstance(element, dict):
        raise ValueError("add requires an 'element' object")
    if kind == "wall":
        from octop.infra.design.ir import parse_wall

        ir.walls.append(parse_wall(element))
    elif kind == "opening":
        from octop.infra.design.ir import parse_opening

        ir.openings.append(parse_opening(element))
    elif kind == "furniture":
        from octop.infra.design.ir import parse_furniture

        ir.furniture.append(parse_furniture(element))
    elif kind == "room":
        from octop.infra.design.ir import parse_room

        ir.rooms.append(parse_room(element))
    else:
        raise ValueError(f"unknown element kind: {kind!r}")


def _locate(ir: IR, element_id: str) -> tuple[str, Any]:
    for wall in ir.walls:
        if wall.id == element_id:
            return ("wall", wall)
    for opening in ir.openings:
        if opening.id == element_id:
            return ("opening", opening)
    for item in ir.furniture:
        if item.id == element_id:
            return ("furniture", item)
    for room in ir.rooms:
        if room.id == element_id:
            return ("room", room)
    raise ValueError(f"unknown element id: {element_id!r}")


def _update(ir: IR, op: dict[str, Any]) -> None:
    element_id = str(op.get("id") or "").strip()
    patch = op.get("patch")
    if not element_id or not isinstance(patch, dict):
        raise ValueError("update requires 'id' and a 'patch' object")
    kind, target = _locate(ir, element_id)
    if kind == "wall":
        from octop.infra.design.ir import parse_wall

        merged = {**target.__dict__, **patch, "id": element_id}
        replacement = parse_wall(merged)
        ir.walls[ir.walls.index(target)] = replacement
    elif kind == "opening":
        from octop.infra.design.ir import parse_opening

        merged = {**target.__dict__, **patch, "id": element_id}
        ir.openings[ir.openings.index(target)] = parse_opening(merged)
    elif kind == "furniture":
        from octop.infra.design.ir import parse_furniture

        merged = {**target.__dict__, **patch, "id": element_id}
        ir.furniture[ir.furniture.index(target)] = parse_furniture(merged)
    elif kind == "room":
        from octop.infra.design.ir import parse_room

        merged = {**target.__dict__, **patch, "id": element_id}
        ir.rooms[ir.rooms.index(target)] = parse_room(merged)


def _move(ir: IR, op: dict[str, Any]) -> None:
    element_id = str(op.get("id") or "").strip()
    if not element_id:
        raise ValueError("move requires 'id'")
    dx = float(op.get("dx") or 0.0)
    dy = float(op.get("dy") or 0.0)
    kind, target = _locate(ir, element_id)
    if kind == "furniture":
        from octop.infra.design.ir import parse_furniture

        merged = {
            **target.__dict__,
            "x": target.x + dx,
            "y": target.y + dy,
            "id": element_id,
        }
        ir.furniture[ir.furniture.index(target)] = parse_furniture(merged)
    elif kind == "opening":
        from octop.infra.design.ir import parse_opening

        merged = {
            **target.__dict__,
            "x": target.x + dx,
            "y": target.y + dy,
            "id": element_id,
        }
        ir.openings[ir.openings.index(target)] = parse_opening(merged)
    elif kind == "room":
        from octop.infra.design.ir import parse_room

        shifted = [(x + dx, y + dy) for x, y in target.polygon]
        merged = {**target.__dict__, "polygon": shifted, "id": element_id}
        ir.rooms[ir.rooms.index(target)] = parse_room(merged)
    else:
        raise ValueError("move is only supported for openings, furniture and rooms")


def _delete(ir: IR, op: dict[str, Any]) -> None:
    element_id = str(op.get("id") or "").strip()
    if not element_id:
        raise ValueError("delete requires 'id'")
    kind, target = _locate(ir, element_id)
    if kind == "wall":
        ir.walls.remove(target)
    elif kind == "opening":
        ir.openings.remove(target)
    elif kind == "furniture":
        ir.furniture.remove(target)
    elif kind == "room":
        ir.rooms.remove(target)
