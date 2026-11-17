"""Thread artifact path normalize, tool extraction, and API payload shaping."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from octop.infra.gateway.media.backend_files import (
    extract_workspace_rel,
    file_url_to_abs_path,
    is_host_absolute_path,
)
from octop.infra.gateway.media.tool_media import tool_name_base
from octop.infra.utils.thread_artifact import (
    ThreadArtifact,
    coerce_thread_artifact,
)

ARTIFACT_TOOL_BASES = frozenset(
    {
        "write_file",
        "edit_file",
        "send_file",
        "send_file_to_user",
        "desktop_screenshot",
        "mobile_screenshot",
    }
)

_PATH_KEYS = (
    "path",
    "file_path",
    "filepath",
    "dest",
    "target_path",
    "output_path",
)

_PATH_EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9._+-]{0,11}$")
_ABS_OCTOP_RE = re.compile(r"(?:/[\w.-]+)*/\.octop/agents/[^\s\"'<>]+", re.IGNORECASE)
_REL_DIR_RE = re.compile(
    r"(?:^|[\s\"'`])((?:outbound|inbound|generated)/[^\s\"'<>]+)",
    re.IGNORECASE,
)


def _artifact_path_allowed(posix: str) -> bool:
    if not posix or posix in {".", "/"}:
        return False
    if "/_builtin_skills/" in f"/{posix}/" or posix.lstrip("/").startswith("_builtin_skills"):
        return False
    base = posix.rstrip("/").split("/")[-1] or posix
    if re.fullmatch(r"[\d.]+", base):
        return False
    return bool(_PATH_EXT_RE.search(base))


def artifact_path_allowed(path: str) -> bool:
    """True when *path* looks like a user-facing workspace file artifact."""
    raw = _coerce_raw_path(path)
    return bool(raw) and _artifact_path_allowed(raw)


def _coerce_raw_path(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.startswith("file://"):
        return file_url_to_abs_path(raw).replace("\\", "/")
    return raw


def normalize_artifact_path(path: str, workspace_dir: Path) -> str:
    """Return a path for ``threads.artifacts``, or ``\"\"``.

    Absolute paths are stored as-is. Relative paths are joined under
    ``workspace_dir`` (the agent work area — not backend ``root_dir``).
    """
    raw = _coerce_raw_path(path)
    if not raw or not _artifact_path_allowed(raw):
        return ""
    if is_host_absolute_path(raw):
        return raw
    rel = extract_workspace_rel(raw) or raw.lstrip("/")
    if rel.startswith("workspace/"):
        rel = rel.removeprefix("workspace/")
    if not rel or not _artifact_path_allowed(rel):
        return ""
    # Keep the configured workspace spelling stable. On macOS, ``/home`` is a
    # firmlink and ``Path.resolve()`` rewrites it to ``/System/Volumes/Data/home``,
    # which breaks artifact de-duplication against already-absolute entries.
    ws = workspace_dir.expanduser()
    return str((ws / rel).as_posix())


def is_artifact_tool_name(name: str | None) -> bool:
    base = tool_name_base(name or "").lower()
    return base in ARTIFACT_TOOL_BASES


def extract_artifact_paths(
    *,
    tool_name: str = "",
    args: str | Mapping[str, Any] | None = None,
    result: Any = None,
    workspace_dir: Path | None = None,
) -> list[str]:
    """Return workspace paths from a write/edit/send/screenshot tool.

    Prefer structured path keys on tool *args*. Scan the tool result text only
    when args did not yield a path (screenshots, send_file without a path key).
    """
    if not is_artifact_tool_name(tool_name):
        return []
    ws = workspace_dir.expanduser() if workspace_dir is not None else None
    from_args = _dedupe_paths(_paths_from_args(args), ws)
    if from_args:
        return from_args
    return _dedupe_paths(_paths_from_content(result), ws)


def artifacts_for_response(
    artifacts: Sequence[object],
    workspace_dir: Path,
    *,
    default_agent_id: str = "",
) -> list[ThreadArtifact]:
    """Normalize stored artifacts into structured refs for API / UI.

    Entries that already carry ``agent_id`` keep their path as stored.
    Legacy bare paths are joined to *workspace_dir* and stamped with
    *default_agent_id* when set.
    """
    out: list[ThreadArtifact] = []
    seen: set[str] = set()
    default_aid = (default_agent_id or "").strip()
    for raw in artifacts:
        entry = coerce_thread_artifact(raw)
        if entry is None:
            continue
        if entry.agent_id:
            path = entry.path.strip()
            aid = entry.agent_id
        else:
            path = normalize_artifact_path(entry.path, workspace_dir)
            aid = default_aid
        if not path:
            continue
        resolved = ThreadArtifact(path=path, agent_id=aid)
        key = resolved.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(resolved)
    return out


def artifact_paths_for_response(refs: Sequence[ThreadArtifact]) -> list[str]:
    """Legacy ``artifacts`` field: unique paths in ref order."""
    out: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        path = ref.path.strip()
        if not path:
            continue
        key = path.casefold() if os.name == "nt" else path
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def artifact_refs_for_response(refs: Sequence[ThreadArtifact]) -> list[dict[str, str]]:
    """Structured ``artifact_refs`` field."""
    return [ref.as_ref() for ref in refs]


def thread_artifacts_payload(
    artifacts: Sequence[object],
    workspace_dir: Path,
    *,
    default_agent_id: str = "",
) -> dict[str, Any]:
    """API payload with both legacy path strings and structured refs."""
    refs = artifacts_for_response(
        artifacts,
        workspace_dir,
        default_agent_id=default_agent_id,
    )
    return {
        "artifacts": artifact_paths_for_response(refs),
        "artifact_refs": artifact_refs_for_response(refs),
    }


@dataclass(frozen=True)
class RecentArtifact:
    """One generated file surfaced by the desktop Files window."""

    path: str
    name: str
    agent_id: str
    thread_id: str
    thread_title: str
    last_active: int
    in_workspace: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "agent_id": self.agent_id,
            "thread_id": self.thread_id,
            "thread_title": self.thread_title,
            "last_active": self.last_active,
            "in_workspace": self.in_workspace,
        }


def _is_user_visible_artifact_path(posix: str) -> bool:
    parts = [part for part in posix.replace("\\", "/").split("/") if part]
    if not parts:
        return False
    for part in parts:
        if part in {".", ".."} or part.startswith(".") or part == "_builtin_skills":
            return False
    return True


def _artifact_workspace_path(path: str, workspace_dir: Path) -> tuple[str, bool]:
    """Return ``(display_path, in_workspace)`` for one stored artifact path."""
    raw = _coerce_raw_path(path)
    if not raw:
        return "", False
    if is_host_absolute_path(raw):
        try:
            rel = Path(raw).relative_to(workspace_dir)
        except ValueError:
            return raw, False
        rel_text = rel.as_posix()
        return (rel_text, True) if rel_text not in {"", "."} else ("", False)
    rel_text = raw.lstrip("/")
    return (rel_text, True) if rel_text else ("", False)


def collect_recent_artifacts(
    rows: Sequence[Any],
    workspace_dir: Path,
    *,
    default_agent_id: str = "",
    limit: int = 50,
    thread_id: str | None = None,
) -> list[RecentArtifact]:
    """Flatten thread rows into newest-first generated-file entries.

    Each row is a thread row carrying ``artifacts``, ``thread_id``, ``title``
    and ``last_active``. Files inside ``workspace_dir`` are returned with a
    workspace-relative path; outputs outside the workspace keep their absolute
    spelling and set ``in_workspace=False``.
    """
    if limit <= 0:
        return []
    ordered = [
        row
        for row in rows
        if thread_id is None or str(getattr(row, "thread_id", "") or "") == thread_id
    ]
    ordered.sort(key=lambda row: int(getattr(row, "last_active", 0) or 0), reverse=True)
    ws = workspace_dir.expanduser()
    out: list[RecentArtifact] = []
    seen: set[str] = set()
    for row in ordered:
        refs = artifacts_for_response(
            getattr(row, "artifacts", ()) or (),
            ws,
            default_agent_id=default_agent_id,
        )
        for ref in reversed(refs):
            display, in_workspace = _artifact_workspace_path(ref.path, ws)
            if not display or not _is_user_visible_artifact_path(display):
                continue
            agent_id = ref.agent_id or default_agent_id
            key_path = display.casefold() if os.name == "nt" else display
            key = f"{agent_id}\0{key_path}"
            if key in seen:
                continue
            seen.add(key)
            out.append(
                RecentArtifact(
                    path=display,
                    name=display.rsplit("/", 1)[-1],
                    agent_id=agent_id,
                    thread_id=str(getattr(row, "thread_id", "") or ""),
                    thread_title=str(getattr(row, "title", "") or ""),
                    last_active=int(getattr(row, "last_active", 0) or 0),
                    in_workspace=in_workspace,
                )
            )
            if len(out) >= limit:
                return out
    return out


def _dedupe_paths(paths: Sequence[str], workspace_dir: Path | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if workspace_dir is None:
            key = raw.strip().replace("\\", "/")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
            continue
        resolved = normalize_artifact_path(raw, workspace_dir)
        if not resolved:
            continue
        dedupe_key = resolved.casefold() if os.name == "nt" else resolved
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(resolved)
    return out


def _pick_path_from_object(parsed: Mapping[str, Any]) -> str:
    for key in _PATH_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _paths_from_args(raw: str | Mapping[str, Any] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        picked = _pick_path_from_object(raw)
        return [picked] if picked else []
    text = raw.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        picked = _pick_path_from_object(parsed)
        return [picked] if picked else _paths_from_text(text)
    return _paths_from_text(text)


def _paths_from_content(content: Any) -> list[str]:
    if content is None:
        return []
    if isinstance(content, str):
        stripped = content.strip()
        if stripped and stripped[0] in "{[":
            try:
                parsed = json.loads(stripped)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                return _paths_from_content(parsed)
            if isinstance(parsed, list):
                found: list[str] = []
                for item in parsed:
                    found.extend(_paths_from_content(item))
                return found
        return _paths_from_text(stripped)
    if isinstance(content, Mapping):
        found = []
        picked = _pick_path_from_object(content)
        if picked:
            found.append(picked)
        nested = content.get("source")
        if isinstance(nested, Mapping):
            url = nested.get("url")
            if isinstance(url, str) and url.strip():
                found.append(url.strip())
        return found
    if isinstance(content, list):
        found = []
        for item in content:
            found.extend(_paths_from_content(item))
        return found
    return []


def _paths_from_text(text: str) -> list[str]:
    if not text:
        return []
    abs_match = _ABS_OCTOP_RE.search(text)
    if abs_match:
        return [abs_match.group(0)]
    rel_match = _REL_DIR_RE.search(text)
    if rel_match:
        return [rel_match.group(1)]
    return []


__all__ = [
    "ARTIFACT_TOOL_BASES",
    "RecentArtifact",
    "ThreadArtifact",
    "artifact_path_allowed",
    "artifact_paths_for_response",
    "artifact_refs_for_response",
    "artifacts_for_response",
    "collect_recent_artifacts",
    "extract_artifact_paths",
    "is_artifact_tool_name",
    "normalize_artifact_path",
    "thread_artifacts_payload",
]
