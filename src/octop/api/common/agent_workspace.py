"""Resolve agent ``workspace_dir`` for API handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from octop.infra.agents.workspace.dir import agent_facing_workspace_dir_from_config


def resolve_agent_workspace_dir(server: Any, agent_id: str) -> Path:
    """Prefer ``config.workspace_dir`` via agent registry; else OctopOS default layout."""
    runtime = getattr(server, "app_runtime", None)
    registry = getattr(runtime, "agent_registry", None) if runtime is not None else None
    if registry is not None and hasattr(registry, "resolve_workspace_dir"):
        return cast(Path, registry.resolve_workspace_dir(agent_id))
    paths = getattr(server, "paths", None) or server.services.paths
    return cast(Path, paths.ensure_agent_workspace(agent_id))


def agent_facing_workspace_dir(server: Any, agent_id: str) -> Path:
    """Agent-visible workspace_dir for artifact path joins (not host root_dir map)."""
    runtime = getattr(server, "app_runtime", None)
    registry = getattr(runtime, "agent_registry", None) if runtime is not None else None
    if registry is not None and hasattr(registry, "get_config"):
        facing = agent_facing_workspace_dir_from_config(registry.get_config(agent_id))
        if facing:
            return Path(facing)
    return resolve_agent_workspace_dir(server, agent_id)


__all__ = ["agent_facing_workspace_dir", "resolve_agent_workspace_dir"]
