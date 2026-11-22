"""Audit every MiniApp tool call with actor, risk and an argument fingerprint.

The audit row deliberately contains no raw arguments or results: rows and tool
results can contain PII or a one-time secret. It keeps the tool name, app id,
risk tier, argument keys and a SHA-256 over sanitized arguments so a reviewer
can correlate a change without copying sensitive data into the audit log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.infra.agents.tool_scope import current_tool_context, current_tool_scope
from octop.infra.apps.im_guard import im_tool_denial
from octop.infra.apps.tool_policy import (
    APP_HITL_TOOL_NAMES,
    APP_TOOL_CATEGORIES,
    APP_TOOL_RISKS,
)

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_HINTS = (
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
)


class AuditWriter(Protocol):
    def write(
        self,
        *,
        actor: str | None,
        action: str,
        target: str | None = None,
        payload: str | None = None,
    ) -> None: ...


def _sanitize(value: object) -> object:
    if isinstance(value, Mapping):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            cleaned[str(key)] = (
                "<redacted>"
                if any(hint in lowered for hint in _SENSITIVE_KEY_HINTS)
                else _sanitize(item)
            )
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _configurable() -> dict[str, Any]:
    try:
        return dict(get_config().get("configurable") or {})
    except RuntimeError:
        return {}


def _actor(agent_id: str, configurable: dict[str, Any] | None = None) -> tuple[str, int | None]:
    scope = current_tool_scope()
    if scope is not None:
        return scope
    if configurable is None:
        configurable = _configurable()
    resolved_agent = str(configurable.get("agent_id") or agent_id)
    raw_user = configurable.get("user")
    user_id: int | None = None
    try:
        if raw_user is not None:
            user_id = int(raw_user)
    except (TypeError, ValueError):
        user_id = None
    return resolved_agent, user_id


class ToolAuditMiddleware(AgentMiddleware[Any, Any]):
    """Record one audit row per MiniApp tool call (no raw args/results)."""

    def __init__(self, *, audit_repo: AuditWriter, agent_id: str = "") -> None:
        super().__init__()
        self._audit = audit_repo
        self._agent_id = agent_id

    def _payload(
        self,
        request: ToolCallRequest,
        *,
        status: str,
        duration_ms: int,
        error_type: str = "",
    ) -> tuple[str, str] | None:
        tool_call = request.tool_call
        name = str(tool_call.get("name") or "").rsplit("/", 1)[-1]
        if name not in APP_TOOL_RISKS:
            return None
        raw_args = tool_call.get("args")
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        app_id = args.get("app_id")
        configurable = _configurable()
        resolved_agent, user_id = _actor(self._agent_id, configurable)
        scope = current_tool_context()
        config_source = str(configurable.get("octop_source") or "").strip()
        config_session = str(configurable.get("session_key") or "").strip()
        source = (
            scope.source.strip() if scope is not None and scope.source.strip() else config_source
        )
        session_key = (
            scope.session_key.strip()
            if scope is not None and scope.session_key.strip()
            else config_session
        )
        identity_verified = bool(scope and scope.identity_verified) or bool(
            configurable.get("octop_identity_verified")
        )
        owner_verified = bool(scope and scope.owner_verified) or bool(
            configurable.get("octop_owner_verified")
        )
        chat_type = (
            scope.chat_type
            if scope is not None and scope.chat_type.strip()
            else str(configurable.get("octop_chat_type") or "")
        )
        sender_id = (
            scope.sender_id
            if scope is not None and scope.sender_id.strip()
            else str(configurable.get("octop_sender_id") or "")
        )
        account_key = (
            scope.account_key
            if scope is not None and scope.account_key.strip()
            else str(configurable.get("octop_account_key") or "")
        )
        fingerprint = hashlib.sha256(
            json.dumps(_sanitize(args), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        payload = {
            "tool": name,
            "risk": APP_TOOL_RISKS[name],
            "group": APP_TOOL_CATEGORIES.get(name, ""),
            # HITL tier, not the per-call outcome: argument-conditional
            # ``when`` predicates can still skip the card for a call that
            # stays in this set.
            "hitl_required": name in APP_HITL_TOOL_NAMES,
            "agent_id": resolved_agent,
            "user_id": user_id,
            "app_id": str(app_id) if isinstance(app_id, str) else "",
            "source": source,
            "session_key_present": bool(session_key),
            "identity_verified": identity_verified,
            "owner_verified": owner_verified,
            "chat_type": chat_type,
            "sender_id": sender_id,
            "account_key": account_key,
            "arg_keys": sorted(str(key) for key in args),
            "args_sha256": fingerprint,
            "status": status,
            "duration_ms": duration_ms,
        }
        if error_type:
            payload["error_type"] = error_type
        target = str(app_id) if isinstance(app_id, str) and app_id else name
        return target, json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _im_denial(self, request: ToolCallRequest) -> ToolMessage | None:
        """Return a localized error when an IM sender may not use this tool."""
        tool_call = request.tool_call
        name = str(tool_call.get("name") or "").rsplit("/", 1)[-1]
        if name not in APP_TOOL_RISKS:
            return None
        configurable = _configurable()
        scope = current_tool_context()
        source = (
            scope.source
            if scope is not None and scope.source.strip()
            else str(configurable.get("octop_source") or "")
        )
        chat_type = (
            scope.chat_type
            if scope is not None and scope.chat_type.strip()
            else str(configurable.get("octop_chat_type") or "")
        )
        owner_verified = bool(scope and scope.owner_verified) or bool(
            configurable.get("octop_owner_verified")
        )
        locale = str(configurable.get("octop_locale") or "zh")
        message = im_tool_denial(
            name,
            source=source,
            chat_type=chat_type,
            owner_verified=owner_verified,
            locale=locale,
        )
        if message is None:
            return None
        return ToolMessage(
            content=message,
            tool_call_id=str(tool_call.get("id") or ""),
            name=name,
            status="error",
        )

    def _write(
        self,
        request: ToolCallRequest,
        *,
        status: str,
        started: float,
        error_type: str = "",
    ) -> None:
        try:
            payload = self._payload(
                request,
                status=status,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_type=error_type,
            )
            if payload is None:
                return
            target, body = payload
            resolved_agent, user_id = _actor(self._agent_id)
            actor = f"user:{user_id}" if user_id is not None else f"agent:{resolved_agent}"
            self._audit.write(
                actor=actor,
                action="miniapp.agent_tool",
                target=target,
                payload=body,
            )
        except Exception:  # noqa: BLE001 - audit must never break the tool
            logger.warning("MiniApp tool audit write failed", exc_info=True)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        started = time.monotonic()
        denied = self._im_denial(request)
        if denied is not None:
            self._write(request, status="denied", started=started, error_type="ImToolDenied")
            return denied
        try:
            result = handler(request)
        except Exception as exc:
            self._write(request, status="exception", started=started, error_type=type(exc).__name__)
            raise
        status = "error" if isinstance(result, ToolMessage) and result.status == "error" else "ok"
        self._write(request, status=status, started=started)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        started = time.monotonic()
        denied = self._im_denial(request)
        if denied is not None:
            self._write(request, status="denied", started=started, error_type="ImToolDenied")
            return denied
        try:
            result = await handler(request)
        except Exception as exc:
            self._write(request, status="exception", started=started, error_type=type(exc).__name__)
            raise
        status = "error" if isinstance(result, ToolMessage) and result.status == "error" else "ok"
        self._write(request, status=status, started=started)
        return result


__all__ = ["ToolAuditMiddleware"]
