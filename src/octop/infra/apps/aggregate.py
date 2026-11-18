"""Server-side grouped aggregation for MiniApp tables.

Pages and the assistant must not pull a whole table into the browser just to
sum a column. This module validates a group-by/metric request against the app
schema and returns the real aggregate; the HTTP route only supplies the rows.
Sensitive (PII) columns can be neither grouped nor measured.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from octop.infra.apps.charts import (
    CHART_AGGS,
    NUMERIC_TYPES,
    aggregate_values,
    cell_text,
    to_number,
)
from octop.infra.apps.models import Schema
from octop.infra.errors import ErrorCode, OctopError

DEFAULT_AGGREGATE_LIMIT = 50
MAX_AGGREGATE_LIMIT = 200


def _invalid(message: str, **details: object) -> OctopError:
    return OctopError(ErrorCode.APP_SCHEMA_INVALID, message, details=details)


def aggregate_rows(
    schema: Schema,
    rows: Sequence[Mapping[str, Any]],
    *,
    group_by: str | None = None,
    metric: str | None = None,
    agg: str = "count",
    limit: int = DEFAULT_AGGREGATE_LIMIT,
) -> dict[str, Any]:
    """Group *rows* by one column and aggregate one numeric metric.

    ``agg="count"`` does not need a metric. Empty group keys are kept: a blank
    status is a real bucket in a ledger, not a reason to drop the row.
    """
    aggregation = str(agg or "count").strip().lower()
    if aggregation not in CHART_AGGS:
        raise _invalid(f"unsupported aggregation: {agg!r}")

    group_name = str(group_by or "").strip()
    if group_name:
        group_column = schema.column(group_name)
        if group_column is None:
            raise _invalid(f"unknown group column: {group_name!r}")
        if group_column.pii:
            raise _invalid(f"group column {group_name!r} contains sensitive data")

    metric_name = str(metric or "").strip()
    if aggregation == "count":
        metric_name = ""
    else:
        if not metric_name:
            raise _invalid(f"metric is required for {aggregation}")
        metric_column = schema.column(metric_name)
        if metric_column is None:
            raise _invalid(f"unknown metric column: {metric_name!r}")
        if metric_column.pii:
            raise _invalid(f"metric column {metric_name!r} contains sensitive data")
        if metric_column.type not in NUMERIC_TYPES:
            raise _invalid(f"metric column {metric_name!r} is not numeric ({metric_column.type})")

    safe_limit = max(1, min(int(limit or DEFAULT_AGGREGATE_LIMIT), MAX_AGGREGATE_LIMIT))
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = cell_text(row.get(group_name)) if group_name else ""
        bucket = buckets.setdefault(key, {"count": 0, "values": []})
        bucket["count"] += 1
        if aggregation != "count":
            value = to_number(row.get(metric_name))
            if value is not None:
                bucket["values"].append(value)

    groups: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        if aggregation == "count":
            value = float(bucket["count"])
        else:
            values = bucket["values"]
            value = aggregate_values(values, aggregation) if values else 0.0
        groups.append({"key": key, "value": value, "count": int(bucket["count"])})

    groups.sort(key=lambda item: (-float(item["value"]), str(item["key"])))
    truncated = len(groups) > safe_limit
    return {
        "group_by": group_name or None,
        "metric": metric_name or None,
        "agg": aggregation,
        "groups": groups[:safe_limit],
        "matched_rows": len(rows),
        "truncated": truncated,
    }


__all__ = [
    "DEFAULT_AGGREGATE_LIMIT",
    "MAX_AGGREGATE_LIMIT",
    "aggregate_rows",
]
