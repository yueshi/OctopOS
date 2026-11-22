"""Page-facing capabilities for one MiniApp.

A capability gates what the sandboxed app page may ask the host to do. It
does not restrict the dashboard's own data panel or the agent tools: an owner
can still edit rows in the dashboard when the page only has ``data.read``.

An empty stored value means a legacy app created before capabilities existed;
it receives every capability the original SDK exposed. An explicitly stored
empty list denies every page action, which is useful for presentation-only
apps.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from octop.infra.errors import ErrorCode, OctopError

CAP_DATA_READ = "data.read"
CAP_DATA_WRITE = "data.write"
CAP_ASSET_READ = "asset.read"
CAP_EXTERNAL_OPEN = "external.open"
CAP_CHAT_COMPOSE = "chat.compose"
CAP_EVENTS_READ = "events.read"
CAP_AI_GENERATE = "ai.generate"
CAP_AI_TASK = "ai.agent_task"
CAP_GAME_LAUNCH = "game.launch"
CAP_EXTERNAL_FETCH = "external.fetch"
CAP_EVENTS_EMIT = "events.emit"
CAP_SCHEDULES_MANAGE = "schedules.manage"

ALL_CAPABILITIES: tuple[str, ...] = (
    CAP_DATA_READ,
    CAP_DATA_WRITE,
    CAP_ASSET_READ,
    CAP_EXTERNAL_OPEN,
    CAP_CHAT_COMPOSE,
    CAP_EVENTS_READ,
    CAP_AI_GENERATE,
    CAP_AI_TASK,
    CAP_GAME_LAUNCH,
    CAP_EXTERNAL_FETCH,
    CAP_EVENTS_EMIT,
    CAP_SCHEDULES_MANAGE,
)

# Frozen at the capabilities that existed when the manifest shipped. Do not
# derive this from ALL_CAPABILITIES: a capability added in a later release must
# be opt-in for new apps, never silently granted to every legacy app or to an
# app restored from an older backup.
LEGACY_CAPABILITIES: frozenset[str] = frozenset(
    {
        CAP_DATA_READ,
        CAP_DATA_WRITE,
        CAP_ASSET_READ,
        CAP_EXTERNAL_OPEN,
        CAP_CHAT_COMPOSE,
        CAP_EVENTS_READ,
        CAP_AI_GENERATE,
        CAP_GAME_LAUNCH,
    }
)
"""What apps created before the manifest existed keep using (the R1 set)."""

NEW_APP_CAPABILITIES: frozenset[str] = frozenset(
    {
        CAP_DATA_READ,
        CAP_DATA_WRITE,
        CAP_ASSET_READ,
        CAP_EXTERNAL_OPEN,
        CAP_CHAT_COMPOSE,
        CAP_EVENTS_READ,
    }
)
"""Safe default for new apps: reading/writing their own data, covers and links.

Costly or externally visible actions (LLM calls, games, connector fetches,
event egress, schedules) stay off until the app or its owner declares them.
"""

AGENT_SETTABLE_CAPABILITIES: frozenset[str] = frozenset(
    {
        CAP_DATA_READ,
        CAP_DATA_WRITE,
        CAP_ASSET_READ,
        CAP_EXTERNAL_OPEN,
        CAP_CHAT_COMPOSE,
        CAP_EVENTS_READ,
    }
)
"""Capabilities an agent tool may set without the owner's explicit approval.

High-risk actions stay out of this set: the page is allowed to use them only
after the owner enables them from the app detail page.
"""


def validate(values: Iterable[str]) -> frozenset[str]:
    """Return a cleaned capability set; reject names this build cannot enforce."""
    cleaned = frozenset(str(value).strip() for value in values if str(value).strip())
    unknown = cleaned - set(ALL_CAPABILITIES)
    if unknown:
        raise OctopError(
            ErrorCode.APP_CAPABILITY_INVALID,
            f"unknown capability: {', '.join(sorted(unknown))}",
        )
    return cleaned


def validate_agent_settable(values: Iterable[str]) -> frozenset[str]:
    """Validate a capability list an agent tool wants to apply.

    High-risk capabilities must be enabled by the owner in the dashboard; an
    agent tool cannot elevate an app on its own.
    """
    clean = validate(values)
    forbidden = clean - AGENT_SETTABLE_CAPABILITIES
    if forbidden:
        raise OctopError(
            ErrorCode.APP_CAPABILITY_INVALID,
            "capability requires owner approval: " + ", ".join(sorted(forbidden)),
        )
    return clean


def parse(raw: str | None) -> frozenset[str]:
    """Read a stored value; empty means the legacy all-on set.

    Unknown names are dropped rather than granting an action this build does
    not implement. A malformed value denies everything instead of failing open.
    """
    if not raw:
        return LEGACY_CAPABILITIES
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return frozenset()
    if not isinstance(parsed, list):
        return frozenset()
    return frozenset(
        value for value in parsed if isinstance(value, str) and value in ALL_CAPABILITIES
    )


def dump(values: Iterable[str]) -> str:
    """Serialize a validated set in stable order."""
    return json.dumps(sorted(validate(values)), ensure_ascii=False, separators=(",", ":"))


def describe(raw: str | None) -> list[str]:
    """Sorted capability names for API responses and tool results."""
    return sorted(parse(raw))


__all__ = [
    "AGENT_SETTABLE_CAPABILITIES",
    "ALL_CAPABILITIES",
    "CAP_AI_GENERATE",
    "CAP_AI_TASK",
    "CAP_ASSET_READ",
    "CAP_CHAT_COMPOSE",
    "CAP_DATA_READ",
    "CAP_DATA_WRITE",
    "CAP_EVENTS_EMIT",
    "CAP_EVENTS_READ",
    "CAP_EXTERNAL_FETCH",
    "CAP_EXTERNAL_OPEN",
    "CAP_GAME_LAUNCH",
    "CAP_SCHEDULES_MANAGE",
    "LEGACY_CAPABILITIES",
    "NEW_APP_CAPABILITIES",
    "describe",
    "dump",
    "parse",
    "validate",
    "validate_agent_settable",
]
