"""Pre-approval validation for app tools that need a human decision.

HITL runs in ``after_model`` before the tool executes, so without a preflight
check a call that is guaranteed to fail (a ``connector`` service without an
instance) would still pop an approval card and then fail. These helpers let the
interrupt ``when`` predicate skip that card; the tool then returns the
validation error to the model without any side effect.

The same predicate also skips owner-reversible actions whose arguments make
them safe: hiding a table, disabling an integration, or testing an inbox-only
ingress. Those are audited and permission-checked by the tool itself; a second
confirmation only trains users to approve blindly. ``app_share_link`` is not
in the HITL set at all: the tool enforces owner/admin and app ACL itself, and
the channel-bound personal auth link must not require an extra approval.
"""

from __future__ import annotations

from typing import Any

_SERVICE_PROVIDERS = frozenset({"connector", "external"})


def _truthy_flag(value: Any) -> bool:
    """Parse a boolean tool argument without trusting its Python type.

    Pydantic coerces ``"true"`` / ``"1"`` to True before execution, so the
    predicate must treat those as True too or a destructive flag could slip
    through without an approval card.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _tool_call(req: Any) -> tuple[str, dict[str, Any]] | None:
    """Read ``(name, args)`` from a harness ToolCallRequest or a mapping."""
    tool_call = getattr(req, "tool_call", None)
    if not isinstance(tool_call, dict):
        if isinstance(req, dict):
            nested = req.get("tool_call")
            tool_call = nested if isinstance(nested, dict) else req
        else:
            return None
    name = str(tool_call.get("name") or "")
    raw_args = tool_call.get("args")
    return name, raw_args if isinstance(raw_args, dict) else {}


def _test_ingress_needs_approval(args: dict[str, Any], ingress_repo: Any | None) -> bool:
    """Ask unless the target ingress is an ``app.inbox`` sink.

    ``app.import`` / ``app.refresh`` test events write or refresh app data and
    keep the card. A missing ingress or a mismatched app id fails inside the
    tool before any side effect, so it does not cost the user an approval.
    """
    if ingress_repo is None:
        return True
    ingress_id = str(args.get("ingress_id") or "").strip()
    if not ingress_id:
        return False
    try:
        row = ingress_repo.get(ingress_id)
    except Exception:  # noqa: BLE001 - a lookup failure must keep the card
        return True
    if row is None:
        return False
    app_id = str(args.get("app_id") or "").strip()
    if app_id and str(getattr(row, "app_id", "")) != app_id:
        return False
    return str(getattr(row, "mode", "")) != "app.inbox"


def approval_needed(req: Any, *, ingress_repo: Any | None = None) -> bool:
    """False when the call does not need a human decision before running.

    That covers calls that will be rejected before touching app data, plus
    owner-reversible actions whose arguments are already known to be safe.
    Only positively detected safe/error cases skip; anything uncertain keeps
    the approval card.
    """
    call = _tool_call(req)
    if call is None:
        return True
    name, args = call
    if name == "app_import_rows":
        # A dry run validates and counts rows but never writes them.
        return args.get("dry_run") is not True
    if name == "app_set_table_visibility":
        # Hiding a table only reduces public exposure. Missing/false keeps the
        # safe direction; any truthy value asks.
        if "public" not in args:
            return True
        return _truthy_flag(args.get("public"))
    if name in ("app_integration_update_ingress", "app_integration_update_webhook"):
        # A pure disable only reduces exposure. Combining the disable with a
        # mapping/URL change must still ask: the later re-enable card would
        # not show the earlier change.
        if "enabled" not in args or _truthy_flag(args.get("enabled")):
            return True
        extra = (
            ("name", "mode", "event_type", "mapping", "max_per_minute")
            if name == "app_integration_update_ingress"
            else ("name", "url", "event_types")
        )
        return any(args.get(key) is not None for key in extra)
    if name == "app_integration_test_ingress":
        return _test_ingress_needs_approval(args, ingress_repo)
    if name != "app_add_service":
        return True
    if not str(args.get("app_id") or "").strip():
        return False

    # app_add_service: provider=connector needs an existing instance.
    provider = str(args.get("provider") or "").strip().lower()
    if provider not in _SERVICE_PROVIDERS:
        return False
    if provider == "connector" and not str(args.get("connector_instance_id") or "").strip():
        return False
    tools = args.get("allowed_tools")
    return isinstance(tools, list) and any(str(item).strip() for item in tools)


__all__ = ["approval_needed"]
