"""DXF/DWG export from the design IR."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import ezdxf
import pytest

from octop.infra.design.export import (
    ExportUnavailable,
    build_dxf,
    export_drawing,
    resolve_oda_converter,
)
from octop.infra.design.ir import parse_ir

posix_only = pytest.mark.skipif(os.name != "posix", reason="fake converter is a shell script")

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture() -> object:
    plan = json.loads((FIXTURES / "two_bedroom_81.json").read_text(encoding="utf-8"))
    return parse_ir(plan)


def test_dxf_has_the_expected_layers_and_entities() -> None:
    ir = _fixture()
    data = build_dxf(ir, title="89㎡ 两居")
    assert b"0\nSECTION" in data[:20]
    doc = ezdxf.read(io.StringIO(data.decode("utf-8")))
    layers = {layer.dxf.name for layer in doc.layers}
    assert {"WALL", "DOOR", "WINDOW", "FURNITURE", "ROOM", "MEP", "TEXT"} <= layers

    entities = list(doc.modelspace())
    by_layer: dict[str, int] = {}
    for entity in entities:
        by_layer[entity.dxf.layer] = by_layer.get(entity.dxf.layer, 0) + 1
    assert by_layer["WALL"] >= len(ir.walls)
    assert by_layer["ROOM"] >= len(ir.rooms) * 2  # outline + label
    assert by_layer["FURNITURE"] >= len(ir.furniture)
    assert by_layer["MEP"] >= len(ir.mep) * 2  # circle + glyph
    texts = [entity.dxf.text for entity in entities if entity.dxftype() == "TEXT"]
    assert any("主卧" in text for text in texts)
    assert any("初步方案" in text for text in texts)


def test_dxf_export_names_the_file_after_the_plan() -> None:
    export = export_drawing(_fixture(), "dxf", title="89㎡ 两居")
    assert export.filename.endswith(".dxf")
    assert "/" not in export.filename
    assert export.media_type == "application/dxf"
    assert b"0\nSECTION" in export.content[:20]


def test_unsupported_format_is_rejected() -> None:
    with pytest.raises(ValueError):
        export_drawing(_fixture(), "pdf")


def test_dwg_without_a_converter_explains_the_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOP_ODA_CONVERTER", raising=False)
    assert resolve_oda_converter() is None
    with pytest.raises(ExportUnavailable) as excinfo:
        export_drawing(_fixture(), "dwg")
    assert "ODA File Converter" in str(excinfo.value)


@posix_only
def test_dwg_uses_the_configured_converter(tmp_path: Path) -> None:
    fake = tmp_path / "oda"
    fake.write_text('#!/bin/sh\ncp "$1/plan.dxf" "$2/plan.dwg"\n', encoding="utf-8")
    fake.chmod(0o755)

    export = export_drawing(_fixture(), "dwg", title="计划", oda_converter=str(fake))
    assert export.filename == "计划.dwg"
    assert b"0\nSECTION" in export.content[:20]
    assert export.media_type == "application/octet-stream"
