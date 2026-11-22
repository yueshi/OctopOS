"""IM-side gate for MiniApp tools.

IM senders are persisted as the agent owner (sessions, history, memory), so
the tool actor alone cannot prove that the human in the chat is the owner.
Until PR-11 this was only enforced for personal sign-in links. This module
extends the same rule to every app data tool:

- dashboard / CLI / cron / MCP sources are trusted (they have their own auth);
- a private chat with a sender bound to the agent owner may use all tools;
- another sender (or any group chat) may only receive a link/open payload.
"""

from __future__ import annotations

from octop.i18n import tr

IM_ALWAYS_ALLOWED: frozenset[str] = frozenset({"app_share_link", "app_open"})

# Sources that are not an IM conversation. They already authenticate the
# actor through the dashboard/CLI/cron path, so the IM identity check does not
# apply.
TRUSTED_TOOL_SOURCES: frozenset[str] = frozenset(
    {"", "api", "cli", "cron", "dashboard", "mcp", "system", "web"}
)


def tool_source_is_im(source: str) -> bool:
    header = source.split("/", 1)[0].strip().lower()
    return bool(header) and header not in TRUSTED_TOOL_SOURCES


def im_tool_denial(
    tool_name: str,
    *,
    source: str,
    chat_type: str,
    owner_verified: bool,
    locale: str = "zh",
) -> str | None:
    """Return a localized denial message, or ``None`` when the call is allowed."""
    if tool_name in IM_ALWAYS_ALLOWED or not tool_source_is_im(source):
        return None
    if chat_type.strip().lower() != "dm":
        return tr("tools.im_group_denied", locale)
    if not owner_verified:
        return tr("tools.im_owner_required", locale)
    return None


__all__ = ["IM_ALWAYS_ALLOWED", "im_tool_denial", "tool_source_is_im"]
