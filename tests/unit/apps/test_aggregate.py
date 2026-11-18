"""Unit tests for server-side group-by aggregation."""

from __future__ import annotations

import pytest

from octop.infra.apps.aggregate import aggregate_rows
from octop.infra.apps.models import parse_schema_payload
from octop.infra.errors import ErrorCode, OctopError

SCHEMA = parse_schema_payload(
    [
        {"name": "title", "type": "string"},
        {"name": "status", "type": "enum", "values": ["new", "done"]},
        {"name": "amount", "type": "number"},
        {"name": "phone", "type": "string", "pii": "phone"},
    ]
)

ROWS = [
    {"title": "A", "status": "new", "amount": "10", "phone": "13800138000"},
    {"title": "B", "status": "done", "amount": "20", "phone": "13900139000"},
    {"title": "C", "status": "done", "amount": "30", "phone": "13700137000"},
    {"title": "D", "status": "", "amount": "", "phone": ""},
]


def _code(exc: pytest.ExceptionInfo[OctopError]) -> ErrorCode:
    return exc.value.code


def test_count_by_status_includes_blank_group() -> None:
    result = aggregate_rows(SCHEMA, ROWS, group_by="status", agg="count")
    assert result["agg"] == "count"
    assert result["metric"] is None
    assert result["matched_rows"] == 4
    assert result["groups"] == [
        {"key": "done", "value": 2.0, "count": 2},
        {"key": "", "value": 1.0, "count": 1},
        {"key": "new", "value": 1.0, "count": 1},
    ]


def test_sum_and_avg_ignore_non_numeric_cells() -> None:
    total = aggregate_rows(SCHEMA, ROWS, group_by="status", metric="amount", agg="sum")
    assert total["groups"][0] == {"key": "done", "value": 50.0, "count": 2}
    assert total["groups"][1] == {"key": "new", "value": 10.0, "count": 1}

    average = aggregate_rows(SCHEMA, ROWS, group_by="status", metric="amount", agg="avg")
    assert average["groups"][0] == {"key": "done", "value": 25.0, "count": 2}

    overall = aggregate_rows(SCHEMA, ROWS, metric="amount", agg="sum")
    assert overall["group_by"] is None
    assert overall["groups"] == [{"key": "", "value": 60.0, "count": 4}]


def test_limit_truncates_groups() -> None:
    result = aggregate_rows(SCHEMA, ROWS, group_by="status", agg="count", limit=1)
    assert len(result["groups"]) == 1
    assert result["truncated"] is True


def test_rejects_sensitive_group_or_metric() -> None:
    with pytest.raises(OctopError) as exc:
        aggregate_rows(SCHEMA, ROWS, group_by="phone", agg="count")
    assert _code(exc) == ErrorCode.APP_SCHEMA_INVALID

    with pytest.raises(OctopError) as exc:
        aggregate_rows(SCHEMA, ROWS, metric="phone", agg="sum")
    assert _code(exc) == ErrorCode.APP_SCHEMA_INVALID


def test_rejects_unknown_or_non_numeric_columns_and_aggregation() -> None:
    with pytest.raises(OctopError) as exc:
        aggregate_rows(SCHEMA, ROWS, group_by="missing", agg="count")
    assert _code(exc) == ErrorCode.APP_SCHEMA_INVALID

    with pytest.raises(OctopError) as exc:
        aggregate_rows(SCHEMA, ROWS, metric="title", agg="sum")
    assert _code(exc) == ErrorCode.APP_SCHEMA_INVALID

    with pytest.raises(OctopError) as exc:
        aggregate_rows(SCHEMA, ROWS, metric="amount", agg="median")
    assert _code(exc) == ErrorCode.APP_SCHEMA_INVALID

    with pytest.raises(OctopError) as exc:
        aggregate_rows(SCHEMA, ROWS, agg="sum")
    assert _code(exc) == ErrorCode.APP_SCHEMA_INVALID
