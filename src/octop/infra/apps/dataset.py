"""The built-in dataset service exposed to MiniApp assembly.

Small apps should not need a separate backend. The control-plane tables are
already that backend; this module names the contract so bindings, the dashboard
and future external services can all speak the same shape.

Only the read side is exposed today. Mutations still go through AppService and
its ACL checks until the binding layer is added.
"""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.apps.models import Schema
from octop.infra.apps.service import AppRecord, AppService

BUILTIN_DATASET_SERVICE_ID = "builtin.dataset"
BUILTIN_DATASET_OPERATIONS: tuple[str, ...] = ("describe", "query")
ROW_SCOPE_APP = "app"
"""Default row scope: all collaborators see all rows. Tables may opt into
``user`` so each user only sees rows they created."""


@dataclass(frozen=True)
class DatasetColumn:
    name: str
    label: str
    type: str
    required: bool
    pii: str | None


@dataclass(frozen=True)
class DatasetTable:
    table_key: str
    label: str
    row_count: int
    is_main: bool
    public_visible: bool
    row_scope: str
    columns: tuple[DatasetColumn, ...]


@dataclass(frozen=True)
class DatasetDescriptor:
    app_id: str
    service_id: str
    operations: tuple[str, ...]
    row_scope: str
    tables: tuple[DatasetTable, ...]


class BuiltinDatasetService:
    """Read-side adapter over AppService's existing table/row storage."""

    def __init__(self, service: AppService) -> None:
        self._service = service

    async def describe(self, row: AppRecord) -> DatasetDescriptor:
        tables: list[DatasetTable] = []
        for table in self._service.list_tables(row):
            schema = await self._service.read_schema(row, table.table_key)
            tables.append(
                DatasetTable(
                    table_key=table.table_key,
                    label=table.label,
                    row_count=table.row_count,
                    is_main=table.is_main,
                    public_visible=table.public_visible,
                    row_scope=table.row_scope,
                    columns=tuple(
                        DatasetColumn(
                            name=column.name,
                            label=column.label,
                            type=column.type,
                            required=column.required,
                            pii=column.pii,
                        )
                        for column in schema.columns
                    ),
                )
            )
        return DatasetDescriptor(
            app_id=row.app_id,
            service_id=BUILTIN_DATASET_SERVICE_ID,
            operations=BUILTIN_DATASET_OPERATIONS,
            row_scope=ROW_SCOPE_APP,
            tables=tuple(tables),
        )

    async def query(
        self,
        row: AppRecord,
        *,
        table: str,
        limit: int,
        offset: int,
        redact_pii: bool,
        pii_policy: str | None = None,
        viewer_user_id: int | None = None,
        include_all_rows: bool = False,
    ) -> tuple[Schema, list[dict[str, str]], int, list[str]]:
        schema, rows, meta, total = await self._service.read_rows_detailed(
            row,
            table=table,
            limit=limit,
            offset=offset,
            redact_pii=redact_pii,
            pii_policy=pii_policy,
            viewer_user_id=viewer_user_id,
            include_all_rows=include_all_rows,
        )
        masked = [item["row_id"] for item in meta if item.get("pii_masked") and item["row_id"]]
        return schema, rows, total, masked


__all__ = [
    "BUILTIN_DATASET_OPERATIONS",
    "BUILTIN_DATASET_SERVICE_ID",
    "BuiltinDatasetService",
    "DatasetColumn",
    "DatasetDescriptor",
    "DatasetTable",
    "ROW_SCOPE_APP",
]
