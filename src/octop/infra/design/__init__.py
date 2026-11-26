"""Deterministic geometry kernel for the interior-design MiniApp.

The kernel is a pure function of the design IR: it never mutates state and
never asks a model for a number. Two entry points consume it:

- the REST/MCP server in :mod:`octop.infra.design.server`, which the MiniApp
  page reaches through an ``external.write`` extension service and the expert
  reaches through a custom MCP connector;
- the unit tests under ``tests/unit/design``.

Units are millimetres and square millimetres throughout. The IR plane uses a
y-up coordinate system with the origin at the bottom-left of the plan.
"""

from octop.infra.design.checks import RULES, check_drawing
from octop.infra.design.ir import IR, Furniture, Opening, Room, Wall, parse_ir
from octop.infra.design.takeoff import takeoff

__all__ = [
    "RULES",
    "Furniture",
    "IR",
    "Opening",
    "Room",
    "Wall",
    "check_drawing",
    "parse_ir",
    "takeoff",
]
