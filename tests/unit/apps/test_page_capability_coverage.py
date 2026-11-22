"""Keep the page-facing bridge and the capability manifests in sync.

The bridge is the first enforcement point for MiniApp page actions: a method
with no capability mapping is denied at runtime. These tests make "add a new
SDK call" and "forget its capability" a test failure instead of a silent
security hole.
"""

from __future__ import annotations

import re
from pathlib import Path

from octop.infra.apps import capabilities as caps

_ROOT = Path(__file__).resolve().parents[3]
_FRAME = _ROOT / "dashboard" / "src" / "components" / "MiniAppFrame"

_SYSTEM_METHODS = {"systemQuery", "systemAction", "openManager"}
_ALWAYS_ALLOWED = {"me"}


def _read(name: str) -> str:
    return (_FRAME / name).read_text(encoding="utf-8")


def _cap_block(source: str) -> str:
    return source.split("export const CAP = {", 1)[1].split("} as const;", 1)[0]


def _method_map_block(source: str) -> str:
    return source.split("const METHOD_CAPABILITY", 1)[1].split("};", 1)[0]


def _sdk_block(source: str) -> str:
    return source.split("window.octopApp = {", 1)[1].split("\n  };", 1)[0]


def test_frontend_and_backend_capability_names_match() -> None:
    source = _read("capabilities.ts")
    frontend = set(re.findall(r'[A-Z_]+:\s+"([^"]+)"', _cap_block(source)))
    assert frontend == set(caps.ALL_CAPABILITIES)


def test_frontend_legacy_capabilities_match_backend() -> None:
    source = _read("capabilities.ts")
    values = dict(re.findall(r'([A-Z_]+):\s+"([^"]+)"', _cap_block(source)))
    legacy_block = source.split("export const LEGACY_CAPABILITIES", 1)[1].split("];", 1)[0]
    refs = re.findall(r"CAP\.([A-Z_]+)", legacy_block)
    assert {values[key] for key in refs} == set(caps.LEGACY_CAPABILITIES)


def test_every_sdk_bridge_call_has_a_capability() -> None:
    capabilities_source = _read("capabilities.ts")
    sdk_source = _read("sdk.ts")
    mapped = set(
        re.findall(r"^\s+([A-Za-z]+):", _method_map_block(capabilities_source), re.MULTILINE)
    )
    called = set(re.findall(r'call\("([A-Za-z]+)"', _sdk_block(sdk_source)))
    missing = called - mapped - _ALWAYS_ALLOWED
    assert not missing, f"SDK bridge calls without a capability mapping: {sorted(missing)}"


def test_capability_map_has_no_stale_entries() -> None:
    capabilities_source = _read("capabilities.ts")
    sdk_source = _read("sdk.ts")
    mapped = set(
        re.findall(r"^\s+([A-Za-z]+):", _method_map_block(capabilities_source), re.MULTILINE)
    )
    used = set(re.findall(r'call\("([A-Za-z]+)"', _sdk_block(sdk_source)))
    stale = mapped - used - _SYSTEM_METHODS
    assert not stale, f"capability mappings for methods the SDK never calls: {sorted(stale)}"
