"""In-process registry of Dashboard WebSocket connections."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


def stamp_thread_id(frame: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Return *frame* with ``thread_id`` set so clients can drop cross-talk."""
    tid = thread_id.strip()
    if not tid or frame.get("thread_id") == tid:
        return frame
    stamped = dict(frame)
    stamped["thread_id"] = tid
    return stamped


#: A reconnect should be able to redraw what was already streamed without
#: shipping an unbounded history. Text and reasoning are capped independently.
MAX_SNAPSHOT_CHARS = 500_000

#: Frames that mean the turn produced something. Only these may reset the idle
#: clock — notably *not* ``turn.progress`` itself, or the heartbeat would keep
#: the stall watchdog from ever firing.
ACTIVITY_FRAME_TYPES = frozenset(
    {
        "turn.started",
        "turn.resumed",
        "token",
        "reasoning",
        "tool_call_chunk",
        "tool_result",
        "usage",
        "done",
        "error",
    }
)


@dataclass
class TurnBuffer:
    """Accumulated stream state for one active turn."""

    turn_id: str = ""
    started_at: float = 0.0
    last_event_at: float = 0.0
    text_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    text_chars: int = 0
    reasoning_chars: int = 0
    phase: str = "model"
    last_tool: str = ""
    truncated: bool = False

    def add_text(self, chunk: str) -> None:
        if not chunk:
            return
        self.text_parts.append(chunk)
        self.text_chars += len(chunk)
        while self.text_chars > MAX_SNAPSHOT_CHARS and len(self.text_parts) > 1:
            self.text_chars -= len(self.text_parts.pop(0))
            self.truncated = True

    def add_reasoning(self, chunk: str) -> None:
        if not chunk:
            return
        self.reasoning_parts.append(chunk)
        self.reasoning_chars += len(chunk)
        while self.reasoning_chars > MAX_SNAPSHOT_CHARS and len(self.reasoning_parts) > 1:
            self.reasoning_chars -= len(self.reasoning_parts.pop(0))
            self.truncated = True


class WebSocketHub:
    """Maps connection ids to async send callbacks for Dashboard sockets.

    Tracks per-thread subscriptions (chat) and optional per-user bindings
    (dashboard-wide toasts). Reconnecting clients resume live chunks via
    in-memory "turn active" flags. Each connection is bound to at most one
    thread at a time.
    """

    def __init__(self) -> None:
        self._connections: dict[str, SendFn] = {}
        self._thread_subscribers: dict[str, set[str]] = {}
        self._conn_thread: dict[str, str] = {}
        self._user_conns: dict[int, set[str]] = {}
        self._conn_user: dict[str, int] = {}
        self._active_turns: set[str] = set()
        self._turn_buffers: dict[str, TurnBuffer] = {}

    def register(
        self,
        connection_id: str,
        send_fn: SendFn,
        *,
        user_id: int | None = None,
    ) -> None:
        self._unbind_user(connection_id)
        self._connections[connection_id] = send_fn
        if user_id is None:
            return
        self._conn_user[connection_id] = user_id
        self._user_conns.setdefault(user_id, set()).add(connection_id)

    def unregister(self, connection_id: str) -> None:
        self.unsubscribe_connection(connection_id)
        self._unbind_user(connection_id)
        self._connections.pop(connection_id, None)

    def _unbind_user(self, connection_id: str) -> None:
        user_id = self._conn_user.pop(connection_id, None)
        if user_id is None:
            return
        conns = self._user_conns.get(user_id)
        if conns is None:
            return
        conns.discard(connection_id)
        if not conns:
            self._user_conns.pop(user_id, None)

    def subscribe(self, thread_id: str, connection_id: str) -> None:
        """Add *connection_id* as a subscriber for *thread_id*.

        Switching threads unsubscribes this connection from the previous
        thread. Other connections on the same thread keep receiving.
        """
        tid = thread_id.strip()
        if not tid or connection_id not in self._connections:
            return
        prev = self._conn_thread.get(connection_id)
        if prev == tid:
            return
        if prev is not None:
            self._drop_subscriber(prev, connection_id)
        self._thread_subscribers.setdefault(tid, set()).add(connection_id)
        self._conn_thread[connection_id] = tid

    def unsubscribe_connection(self, connection_id: str) -> None:
        tid = self._conn_thread.pop(connection_id, None)
        if tid is not None:
            self._drop_subscriber(tid, connection_id)

    def _drop_subscriber(self, thread_id: str, connection_id: str) -> None:
        conns = self._thread_subscribers.get(thread_id)
        if conns is None:
            return
        conns.discard(connection_id)
        if not conns:
            self._thread_subscribers.pop(thread_id, None)

    def mark_turn_active(self, thread_id: str) -> None:
        tid = thread_id.strip()
        if tid:
            now = time.time()
            self._active_turns.add(tid)
            self._turn_buffers[tid] = TurnBuffer(started_at=now, last_event_at=now)

    def mark_turn_idle(self, thread_id: str) -> None:
        tid = thread_id.strip()
        self._active_turns.discard(tid)
        self._turn_buffers.pop(tid, None)

    def is_turn_active(self, thread_id: str) -> bool:
        return thread_id.strip() in self._active_turns

    def has_subscribers(self, thread_id: str) -> bool:
        """True when at least one Dashboard socket is bound to *thread_id*."""
        tid = thread_id.strip()
        return bool(tid and self._thread_subscribers.get(tid))

    def record_frame(self, thread_id: str, frame: dict[str, Any]) -> None:
        """Accumulate streamed text/reasoning so a reconnect can redraw it."""
        tid = thread_id.strip()
        if not tid:
            return
        kind = str(frame.get("type") or "")
        if kind not in ACTIVITY_FRAME_TYPES:
            return
        buffer = self._turn_buffers.get(tid)
        if buffer is None:
            if kind not in {"turn.started", "turn.resumed"}:
                return
            buffer = TurnBuffer(started_at=time.time())
            self._turn_buffers[tid] = buffer
        now = time.time()
        buffer.last_event_at = now
        if kind in {"turn.started", "turn.resumed"}:
            turn_id = str(frame.get("turn_id") or "").strip()
            if turn_id:
                buffer.turn_id = turn_id
            buffer.phase = "model"
            buffer.last_tool = ""
        elif kind == "token":
            buffer.add_text(str(frame.get("content") or ""))
        elif kind == "reasoning":
            buffer.add_reasoning(str(frame.get("content") or ""))
        elif kind == "tool_call_chunk":
            name = str(frame.get("name") or "").strip()
            if name:
                buffer.phase = "tool"
                buffer.last_tool = name
        elif kind == "tool_result":
            buffer.phase = "model"
            buffer.last_tool = ""

    def turn_snapshot(self, thread_id: str) -> dict[str, Any] | None:
        """A one-shot resume payload, or ``None`` when no turn is active."""
        tid = thread_id.strip()
        if not tid or tid not in self._active_turns:
            return None
        buffer = self._turn_buffers.get(tid)
        if buffer is None or not buffer.turn_id:
            return None
        return {
            "type": "turn.snapshot",
            "turn_id": buffer.turn_id,
            "text": "".join(buffer.text_parts),
            "reasoning": "".join(buffer.reasoning_parts),
            "phase": buffer.phase,
            "last_tool": buffer.last_tool,
            "elapsed_s": round(max(0.0, time.time() - buffer.started_at), 1),
            "truncated": buffer.truncated,
            "thread_id": tid,
        }

    def turn_progress(self, thread_id: str) -> dict[str, Any] | None:
        """Heartbeat for an active turn; ``None`` once it is idle."""
        tid = thread_id.strip()
        if not tid or tid not in self._active_turns:
            return None
        buffer = self._turn_buffers.get(tid)
        if buffer is None:
            return None
        now = time.time()
        return {
            "type": "turn.progress",
            "turn_id": buffer.turn_id,
            "phase": buffer.phase,
            "last_tool": buffer.last_tool,
            "elapsed_s": round(max(0.0, now - buffer.started_at), 1),
            "idle_s": round(max(0.0, now - buffer.last_event_at), 1),
            "text_chars": buffer.text_chars,
            "reasoning_chars": buffer.reasoning_chars,
            "thread_id": tid,
        }

    async def push(self, connection_id: str, frame: dict[str, Any]) -> None:
        send_fn = self._connections.get(connection_id)
        if send_fn is None:
            logger.debug("ws hub: connection %s not found", connection_id)
            return
        try:
            await send_fn(frame)
        except Exception:
            logger.exception("ws hub: push failed for %s", connection_id)

    async def push_to_thread(self, thread_id: str, frame: dict[str, Any]) -> None:
        tid = thread_id.strip()
        outbound = stamp_thread_id(frame, tid)
        # Record before the subscriber check: a page that reloaded has no
        # socket for a moment, and must still find the stream on return.
        self.record_frame(tid, outbound)
        conns = self._thread_subscribers.get(tid)
        if not conns:
            logger.debug("ws hub: no subscriber for thread %s", thread_id)
            return
        targets = list(conns)
        if len(targets) == 1:
            await self.push(targets[0], outbound)
            return
        await asyncio.gather(*(self.push(conn_id, outbound) for conn_id in targets))

    async def push_to_user(self, user_id: int, frame: dict[str, Any]) -> None:
        conns = self._user_conns.get(user_id)
        if not conns:
            logger.debug("ws hub: no subscriber for user %s", user_id)
            return
        targets = list(conns)
        if len(targets) == 1:
            await self.push(targets[0], frame)
            return
        await asyncio.gather(*(self.push(conn_id, frame) for conn_id in targets))

    async def push_json(self, connection_id: str, payload: str) -> None:
        try:
            frame = json.loads(payload)
        except (TypeError, ValueError):
            logger.warning("ws hub: invalid json for %s", connection_id)
            return
        if isinstance(frame, dict):
            await self.push(connection_id, frame)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


__all__ = ["SendFn", "WebSocketHub", "stamp_thread_id"]
