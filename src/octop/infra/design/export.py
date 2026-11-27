"""Drawing export: turn the design IR into a CAD file.

DXF is written with ``ezdxf``. DWG is not something a Python library can
produce on its own, so it is delegated to the ODA File Converter (a free
external tool): the DXF is written to a temp directory, ODA converts it, and
the resulting DWG is returned. Without ODA configured the export fails with a
clear, actionable error instead of a corrupt file.

Units are millimetres throughout, matching the IR.
"""

from __future__ import annotations

import io
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ezdxf import units
from ezdxf.filemanagement import new as new_document
from ezdxf.layouts.layout import Modelspace

from octop.infra.design.geometry import point_in_polygon, polygon_centroid
from octop.infra.design.ir import IR, Opening, Room
from octop.infra.design.takeoff import takeoff

#: Environment variable pointing at the ODA File Converter executable.
ODA_ENV: Final = "OCTOP_ODA_CONVERTER"

#: Layer names and ACI colours.
LAYERS: Final[dict[str, int]] = {
    "WALL": 7,
    "DOOR": 4,
    "WINDOW": 5,
    "FURNITURE": 8,
    "ROOM": 3,
    "MEP": 1,
    "TEXT": 7,
    "NOTE": 7,
}

EXPORT_MEDIA_TYPES: Final[dict[str, str]] = {
    "dxf": "application/dxf",
    "dwg": "application/octet-stream",
}

MEP_GLYPHS: Final[dict[str, str]] = {
    "socket": "S",
    "switch": "L",
    "water": "W",
    "drain": "D",
    "ac": "A",
    "gas": "G",
}


class ExportUnavailable(RuntimeError):
    """The requested format needs tooling this deployment does not have."""


@dataclass(frozen=True)
class DrawingExport:
    """One exported file."""

    content: bytes
    filename: str
    media_type: str


def _safe_filename(title: str, suffix: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|\s]+', "-", (title or "").strip()).strip("-")
    return f"{stem or 'plan'}.{suffix}"


def _room_centroid(room: Room) -> tuple[float, float]:
    if len(room.polygon) >= 3:
        return polygon_centroid(room.polygon)
    if room.polygon:
        return room.polygon[0]
    return (0.0, 0.0)


def _door_hinge(door: Opening) -> tuple[float, float]:
    return door.end if door.hinge == "end" else (door.x, door.y)


def _door_leaf_angle(door: Opening, ir: IR) -> tuple[float, float]:
    """Angles (degrees) of the closed leaf and the inward normal at the hinge."""
    hinge = _door_hinge(door)
    leaf_angle = math.degrees(door.heading)
    # Pick the normal that points into the room the door opens from.
    normal = (-math.sin(door.heading), math.cos(door.heading))
    rooms = [room for room in ir.rooms if room.polygon]
    target = None
    for room in rooms:
        if point_in_polygon((door.x, door.y), room.polygon):
            target = room
            break
    if target is not None:
        centroid = _room_centroid(target)
        if (centroid[0] - hinge[0]) * normal[0] + (centroid[1] - hinge[1]) * normal[1] < 0:
            normal = (-normal[0], -normal[1])
    normal_angle = math.degrees(math.atan2(normal[1], normal[0]))
    return leaf_angle, normal_angle


def _add_header(msp: Modelspace, ir: IR, title: str) -> None:
    quantities = takeoff(ir)
    xs = [point[0] for wall in ir.walls for point in ((wall.x1, wall.y1), (wall.x2, wall.y2))]
    ys = [point[1] for wall in ir.walls for point in ((wall.x1, wall.y1), (wall.x2, wall.y2))]
    text_x = min(xs) if xs else 0.0
    text_y = (max(ys) if ys else 0.0) + 1200.0
    msp.add_text(
        title or "装修方案",
        dxfattribs={"layer": "TEXT", "height": 320.0},
    ).set_placement((text_x, text_y))
    msp.add_text(
        f"套内 {quantities['floor_area_m2']} m2 · 墙面 {quantities['wall_area_m2']} m2 · "
        f"踢脚线 {quantities['skirting_length_m']} m",
        dxfattribs={"layer": "NOTE", "height": 180.0},
    ).set_placement((text_x, text_y - 420.0))
    msp.add_text(
        "初步方案：几何由确定性内核校验，结构、消防、防水、暖通与电气需持证专业人员复核",
        dxfattribs={"layer": "NOTE", "height": 160.0},
    ).set_placement((text_x, text_y - 700.0))


def _add_walls(msp: Modelspace, ir: IR) -> None:
    for wall in ir.walls:
        if wall.is_degenerate:
            continue
        msp.add_lwpolyline(
            [(wall.x1, wall.y1), (wall.x2, wall.y2)],
            dxfattribs={"layer": "WALL", "const_width": wall.t},
        )


def _add_rooms(msp: Modelspace, ir: IR) -> None:
    for room in ir.rooms:
        if len(room.polygon) < 3:
            continue
        msp.add_lwpolyline(
            [(x, y) for x, y in room.polygon],
            close=True,
            dxfattribs={"layer": "ROOM"},
        )
        centroid = _room_centroid(room)
        area = abs(
            sum(
                room.polygon[index][0] * room.polygon[(index + 1) % len(room.polygon)][1]
                - room.polygon[(index + 1) % len(room.polygon)][0] * room.polygon[index][1]
                for index in range(len(room.polygon))
            )
            / 2
        )
        label = room.name or room.id
        if area > 0:
            label = f"{label} {area / 1_000_000:.1f}m2"
        msp.add_text(
            label,
            dxfattribs={"layer": "ROOM", "height": 220.0},
        ).set_placement((centroid[0] - 400.0, centroid[1]))


def _add_openings(msp: Modelspace, ir: IR) -> None:
    for door in ir.openings:
        if door.type not in {"door", "sliding_door", "opening"}:
            continue
        hinge = _door_hinge(door)
        leaf = (math.cos(door.heading), math.sin(door.heading))
        if door.hinge == "end":
            leaf = (-leaf[0], -leaf[1])
        leaf_end = (hinge[0] + leaf[0] * door.width, hinge[1] + leaf[1] * door.width)
        msp.add_line(hinge, leaf_end, dxfattribs={"layer": "DOOR"})
        if door.type == "door":
            leaf_angle, normal_angle = _door_leaf_angle(door, ir)
            # Sweep the smaller way from the leaf to the inward normal.
            start, end = leaf_angle, normal_angle
            while end < start:
                end += 360.0
            if end - start > 180.0:
                start, end = end, start + 360.0
            msp.add_arc(
                center=hinge,
                radius=door.width,
                start_angle=start,
                end_angle=end,
                dxfattribs={"layer": "DOOR"},
            )

    for window in ir.openings:
        if not window.is_window:
            continue
        window_end = window.end
        msp.add_lwpolyline(
            [(window.x, window.y), (window_end[0], window_end[1])],
            dxfattribs={"layer": "WINDOW", "const_width": 80.0},
        )


def _add_furniture(msp: Modelspace, ir: IR) -> None:
    for item in ir.furniture:
        corners = item.corners()
        attributes: dict[str, object] = {"layer": "FURNITURE"}
        if item.host and "DASHED" in {layer.dxf.name for layer in msp.doc.layers}:
            attributes["linetype"] = "DASHED"
        msp.add_lwpolyline(corners, close=True, dxfattribs=attributes)
        label = item.name or item.kind
        if label and not item.host:
            center = item.center
            msp.add_text(label, dxfattribs={"layer": "TEXT", "height": 160.0}).set_placement(
                (center[0] - 300.0, center[1])
            )


def _add_mep(msp: Modelspace, ir: IR) -> None:
    for point in ir.mep:
        msp.add_circle(center=(point.x, point.y), radius=110.0, dxfattribs={"layer": "MEP"})
        msp.add_text(
            MEP_GLYPHS.get(point.type, "?"),
            dxfattribs={"layer": "MEP", "height": 120.0},
        ).set_placement((point.x - 45.0, point.y - 45.0))


def build_dxf(ir: IR, *, title: str = "") -> bytes:
    """Render *ir* as a DXF document in millimetres."""
    doc = new_document("R2010", setup=True)
    doc.units = units.MM
    doc.header["$INSUNITS"] = 4
    for name, color in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    msp = doc.modelspace()
    _add_header(msp, ir, title)
    _add_walls(msp, ir)
    _add_rooms(msp, ir)
    _add_openings(msp, ir)
    _add_furniture(msp, ir)
    _add_mep(msp, ir)

    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def resolve_oda_converter(explicit: str | None = None) -> str | None:
    """The configured ODA File Converter executable, if any."""
    candidate = (explicit or os.environ.get(ODA_ENV, "")).strip()
    if not candidate:
        return None
    if Path(candidate).is_file():
        return candidate
    found = shutil.which(candidate)
    return found


def _convert_to_dwg(dxf_bytes: bytes, converter: str, *, timeout: int = 180) -> bytes:
    with tempfile.TemporaryDirectory(prefix="octop-dwg-") as workdir:
        source = Path(workdir) / "in"
        target = Path(workdir) / "out"
        source.mkdir()
        target.mkdir()
        (source / "plan.dxf").write_bytes(dxf_bytes)
        command = [converter, str(source), str(target), "ACAD2018", "DWG", "0", "1", "*.dxf"]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExportUnavailable(f"DWG 转换失败：{exc}") from exc
        # ODA's exit code is unreliable — it can report success for a failed
        # conversion and failure while still writing the file. The output is
        # the only trustworthy signal.
        produced = sorted(target.glob("*.dwg"))
        if produced:
            return produced[0].read_bytes()
        detail = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace").strip()
        message = "DWG 转换未生成文件"
        if completed.returncode != 0:
            message += f"（退出码 {completed.returncode}）"
        if detail:
            message += f"：{detail[:300]}"
        raise ExportUnavailable(message)


def export_drawing(
    ir: IR,
    fmt: str,
    *,
    title: str = "",
    oda_converter: str | None = None,
) -> DrawingExport:
    """Export *ir* as DXF or DWG."""
    clean = (fmt or "").strip().lower()
    if clean == "dxf":
        content = build_dxf(ir, title=title)
        return DrawingExport(
            content=content,
            filename=_safe_filename(title, "dxf"),
            media_type=EXPORT_MEDIA_TYPES["dxf"],
        )
    if clean != "dwg":
        raise ValueError(f"unsupported drawing format: {fmt!r}")
    converter = resolve_oda_converter(oda_converter)
    if converter is None:
        raise ExportUnavailable(
            "DWG 导出需要 ODA File Converter；请设置环境变量 "
            f"{ODA_ENV} 指向可执行文件，或改用 DXF 格式"
        )
    dxf_bytes = build_dxf(ir, title=title)
    content = _convert_to_dwg(dxf_bytes, converter)
    return DrawingExport(
        content=content,
        filename=_safe_filename(title, "dwg"),
        media_type=EXPORT_MEDIA_TYPES["dwg"],
    )
