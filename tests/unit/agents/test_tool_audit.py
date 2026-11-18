"""Unified MiniApp tool-call audit: no raw args, stable fingerprint."""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from octop.infra.agents.middleware.tool_audit import ToolAuditMiddleware
from octop.infra.agents.tool_scope import tool_scope


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def write(self, *, actor, action, target=None, payload=None) -> None:
        self.rows.append(
            {
                "actor": actor,
                "action": action,
                "target": target,
                "payload": json.loads(payload or "{}"),
            }
        )


def _request(name: str, args: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "call-1"})


async def test_audit_records_risk_without_raw_values() -> None:
    audit = _Audit()
    middleware = ToolAuditMiddleware(audit_repo=audit, agent_id="agent-1")
    request = _request(
        "app_append_row",
        {
            "app_id": "app_1",
            "row": {"phone": "13800000000", "note": "private"},
            "secret": "top-secret",
        },
    )

    async def handler(_request):
        return ToolMessage(content="ok", tool_call_id="call-1")

    with tool_scope("agent-1", 7):
        await middleware.awrap_tool_call(request, handler)

    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "miniapp.agent_tool"
    assert row["target"] == "app_1"
    payload = row["payload"]
    assert payload["tool"] == "app_append_row"
    assert payload["risk"] == "write"
    assert payload["user_id"] == 7
    assert payload["status"] == "ok"
    assert payload["arg_keys"] == ["app_id", "row", "secret"]
    assert "13800000000" not in json.dumps(payload)
    assert "top-secret" not in json.dumps(payload)
    assert payload["args_sha256"]


async def test_audit_skips_non_app_tools_and_marks_errors() -> None:
    audit = _Audit()
    middleware = ToolAuditMiddleware(audit_repo=audit, agent_id="agent-1")

    async def ok_handler(_request):
        return ToolMessage(content="ok", tool_call_id="call-1")

    with tool_scope("agent-1", 7):
        await middleware.awrap_tool_call(_request("read_file", {"path": "x"}), ok_handler)
    assert audit.rows == []

    async def error_handler(_request):
        return ToolMessage(content="bad", tool_call_id="call-1", status="error")

    with tool_scope("agent-1", 7):
        await middleware.awrap_tool_call(
            _request("app_set_capabilities", {"app_id": "app_1", "capabilities": []}),
            error_handler,
        )
    assert audit.rows[0]["payload"]["status"] == "error"
    assert audit.rows[0]["payload"]["hitl_required"] is True


async def test_audit_records_exceptions_and_reraises() -> None:
    audit = _Audit()
    middleware = ToolAuditMiddleware(audit_repo=audit, agent_id="agent-1")

    async def boom(_request):
        raise RuntimeError("db down")

    with tool_scope("agent-1", 7):
        try:
            await middleware.awrap_tool_call(_request("app_delete", {"app_id": "app_1"}), boom)
        except RuntimeError:
            pass
        else:  # pragma: no cover - the exception must propagate
            raise AssertionError("exception was swallowed")

    payload = audit.rows[0]["payload"]
    assert payload["status"] == "exception"
    assert payload["error_type"] == "RuntimeError"
