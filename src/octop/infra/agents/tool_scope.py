"""Acting agent/user/channel scope for tool calls that resume outside a chat turn.

A normal turn carries ``agent_id``, ``user``, ``octop_source`` and ``session_key``
in LangGraph's ``configurable``. A HITL resume starts a fresh graph invocation
with only ``thread_id``, so tools that authorize against the caller must fall
back to this scope. The channel fields are restored from the pending approval
and session rows so an IM turn keeps its identity after resume.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolScope:
    agent_id: str
    user_id: int
    source: str = ""
    session_key: str = ""
    chat_type: str = ""
    identity_verified: bool = False
    owner_verified: bool = False
    sender_user_id: int | None = None
    sender_id: str = ""
    account_key: str = ""


_CURRENT_TOOL_SCOPE: ContextVar[ToolScope | None] = ContextVar(
    "octop_tool_scope",
    default=None,
)


@contextmanager
def tool_scope(
    agent_id: str,
    user_id: int | None,
    *,
    source: str = "",
    session_key: str = "",
    chat_type: str = "",
    identity_verified: bool = False,
    owner_verified: bool = False,
    sender_user_id: int | None = None,
    sender_id: str = "",
    account_key: str = "",
) -> Iterator[None]:
    """Bind the acting identity and IM context for tools invoked during a resume."""
    scope = (
        ToolScope(
            agent_id=agent_id,
            user_id=user_id,
            source=source,
            session_key=session_key,
            chat_type=chat_type,
            identity_verified=identity_verified,
            owner_verified=owner_verified,
            sender_user_id=sender_user_id,
            sender_id=sender_id,
            account_key=account_key,
        )
        if user_id is not None
        else None
    )
    token = _CURRENT_TOOL_SCOPE.set(scope)
    try:
        yield
    finally:
        _CURRENT_TOOL_SCOPE.reset(token)


def current_tool_scope() -> tuple[str, int] | None:
    """Backward-compatible ``(agent_id, user_id)`` tuple for tool authorization."""
    scope = _CURRENT_TOOL_SCOPE.get()
    return (scope.agent_id, scope.user_id) if scope is not None else None


def current_tool_context() -> ToolScope | None:
    """Full resume context, including channel/source/identity fields."""
    return _CURRENT_TOOL_SCOPE.get()


__all__ = [
    "ToolScope",
    "current_tool_context",
    "current_tool_scope",
    "tool_scope",
]
