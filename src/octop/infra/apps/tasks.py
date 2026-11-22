"""MiniApp AI task service: create, authorize, execute, audit.

A task is a named, schema-bound operation. The page chooses a registered
``task_type`` and a row; it never sends a free-form prompt. Tier 1 runs one
model call; later tiers can add bounded agent turns behind the same contract.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from octop.infra.apps import access as app_access
from octop.infra.apps import capabilities as app_capabilities
from octop.infra.apps.model_access import resolve_app_model
from octop.infra.apps.models import MAIN_TABLE_KEY, Schema
from octop.infra.apps.service import AppRecord, AppService
from octop.infra.apps.task_types import TaskType, build_prompt, get_task_type, parse_output
from octop.infra.db.repos.app_tasks import AppTaskRepo, AppTaskRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.resource_policy import assert_token_quota_available
from octop.infra.utils.llm_text import llm_text_and_thinking

logger = logging.getLogger(__name__)

# ``list`` is a method on AppTaskService, so use module-level aliases for
# annotations inside the class body.
MessageList = list[Any]
ToolList = list[StructuredTool]

MAX_ACTIVE_TASKS_PER_APP = 20
MAX_APP_TASK_TOKENS_PER_DAY = 200_000
MAX_WRITE_CHARS = 4_000
MAX_TOOL_RESULT_CHARS = 20_000


class _QueryRowsArgs(BaseModel):
    table: str = Field(default=MAIN_TABLE_KEY, description="Table key; defaults to main.")
    filters: dict[str, str] | None = Field(
        default=None, description="Optional equality filters keyed by column name."
    )
    sort: str | None = Field(
        default=None, description="Sort column; prefix with '-' for descending."
    )
    limit: int = Field(default=50, ge=1, le=100, description="Rows to return (1-100).")
    offset: int = Field(default=0, ge=0, description="Rows to skip.")


class _AggregateArgs(BaseModel):
    table: str = Field(default=MAIN_TABLE_KEY, description="Table key; defaults to main.")
    group_by: str | None = Field(default=None, description="Optional group-by column.")
    metric: str | None = Field(default=None, description="Optional metric column.")
    agg: str = Field(default="count", description="Aggregation: sum, avg, count, min or max.")
    filters: dict[str, str] | None = Field(
        default=None, description="Optional equality filters keyed by column name."
    )


class AppTaskCancelled(Exception):
    """Raised inside execution when the actor cancelled before write-back."""


@dataclass(frozen=True)
class AppTaskResult:
    result: dict[str, Any]
    model_ref: str
    written: bool


def _invalid(message: str) -> OctopError:
    return OctopError(ErrorCode.APP_CAPABILITY_INVALID, message)


class AppTaskService:
    def __init__(
        self,
        *,
        app_service: AppService,
        app_repo: Any,
        task_repo: AppTaskRepo,
        agents: Any,
        usage_repo: Any | None = None,
        policy_repo: Any | None = None,
        audit_repo: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._app_service = app_service
        self._app_repo = app_repo
        self._task_repo = task_repo
        self._agents = agents
        self._usage_repo = usage_repo
        self._policy_repo = policy_repo
        self._audit_repo = audit_repo
        self._clock = clock or time.time

    # ----- create / read / cancel ----------------------------------------

    async def create(
        self,
        row: AppRecord,
        *,
        user_id: int,
        task_type: str,
        row_id: str,
        table: str = MAIN_TABLE_KEY,
        input: Mapping[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> AppTaskRow:
        spec = get_task_type(task_type)
        if spec is None:
            raise _invalid(f"unknown task type: {task_type!r}")
        self._app_service.require_capability(row, app_capabilities.CAP_AI_TASK)

        row_record = self._app_repo.get_data_row(row.app_id, row_id)
        if row_record is None:
            raise OctopError.localized(ErrorCode.APP_ROW_NOT_FOUND)
        table_key = row_record.table_key or table
        if table and table != table_key:
            raise _invalid("row does not belong to the requested table")

        raw_input = dict(input or {})
        unknown_input = set(raw_input) - {"write_column"}
        if unknown_input:
            raise _invalid("unsupported task input: " + ", ".join(sorted(unknown_input)))
        write_column = str(raw_input.get("write_column") or "").strip()
        clean_key = idempotency_key.strip()
        if len(clean_key) > 128:
            raise _invalid("idempotency_key must be at most 128 characters")
        if write_column:
            schema = await self._app_service.read_schema(row, table_key)
            column = schema.column(write_column)
            if column is None or column.type != "string" or column.pii:
                raise _invalid("write_column must be a non-PII string column")
            decision = self._app_service.authorize_actor(
                user_id,
                row,
                app_access.Action.UPDATE_ROW,
                table_key=table_key,
                row_record=row_record,
            )
        else:
            decision = self._app_service.authorize_actor(
                user_id,
                row,
                app_access.Action.VIEW_ROW,
                table_key=table_key,
                row_record=row_record,
            )
        if not decision.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)

        if self._policy_repo is not None and self._usage_repo is not None:
            assert_token_quota_available(self._policy_repo, self._usage_repo, row.owner_user_id)

        if self._usage_repo is not None:
            now = int(self._clock())
            used_today = self._usage_repo.tokens_for_app_since(row.app_id, now - (now % 86_400))
            if used_today >= MAX_APP_TASK_TOKENS_PER_DAY:
                raise OctopError(
                    ErrorCode.APP_BUSY,
                    "daily AI token budget exhausted for this app",
                )

        if self._task_repo.count_active_for_app(row.app_id) >= MAX_ACTIVE_TASKS_PER_APP:
            raise OctopError(ErrorCode.APP_BUSY, "too many active AI tasks for this app")

        task = self._task_repo.create(
            app_id=row.app_id,
            table_key=table_key,
            row_id=row_id,
            task_type=spec.name,
            actor_user_id=user_id,
            agent_id=row.agent_id,
            input=raw_input,
            idempotency_key=clean_key,
        )
        self._audit(
            f"user:{user_id}",
            "miniapp.agent_task.create",
            task,
            {"task_type": task.task_type, "table": task.table_key},
        )
        return task

    def get(self, row: AppRecord, task_id: str, *, user_id: int) -> AppTaskRow:
        task = self._task_for_app(row, task_id)
        self._require_view(row, user_id, task)
        return task

    def list(
        self,
        row: AppRecord,
        *,
        user_id: int,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AppTaskRow]:
        self._require_app_view(row, user_id)
        return self._task_repo.list_for_app(row.app_id, status=status, limit=limit, offset=offset)

    def cancel(self, row: AppRecord, task_id: str, *, user_id: int) -> AppTaskRow:
        task = self._task_for_app(row, task_id)
        if task.actor_user_id != user_id and row.owner_user_id != user_id:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
        if not self._task_repo.cancel(task_id):
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        self._audit(f"user:{user_id}", "miniapp.agent_task.cancel", task)
        updated = self._task_repo.get(task_id)
        if updated is None:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return updated

    # ----- execution -----------------------------------------------------

    async def execute(self, task: AppTaskRow) -> AppTaskResult:
        spec = get_task_type(task.task_type)
        if spec is None:
            raise ValueError(f"unknown task type: {task.task_type!r}")
        row = self._app_service.get(task.app_id)
        row_record = self._app_repo.get_data_row(task.app_id, task.row_id)
        if row_record is None:
            raise ValueError("row no longer exists")
        if task.cancel_requested:
            raise AppTaskCancelled(task.task_id)

        write_column = str(task.input.get("write_column") or "").strip()
        decision = self._app_service.authorize_actor(
            task.actor_user_id,
            row,
            app_access.Action.UPDATE_ROW if write_column else app_access.Action.VIEW_ROW,
            table_key=task.table_key,
            row_record=row_record,
        )
        if not decision.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)

        schema = await self._app_service.read_schema(row, task.table_key)
        visible = [column for column in schema.columns if not column.pii]
        prompt = build_prompt(spec, visible, row_record.values)
        llm, model_ref = resolve_app_model(self._agents, row.agent_id)
        base_messages = [SystemMessage(content=spec.system_prompt), HumanMessage(content=prompt)]

        if spec.tier >= 2:
            parsed, usage = await self._run_bounded_agent(llm, task, spec, row, prompt)
        else:
            parsed, usage = await self._ask_json(llm, spec, base_messages)

        self._record_usage(task, row, model_ref, usage)
        if task.cancel_requested:
            raise AppTaskCancelled(task.task_id)

        written = await self._write_back(task, spec, row, row_record, schema, parsed, decision)
        return AppTaskResult(result=parsed, model_ref=model_ref, written=written)

    async def _ask_json(
        self,
        llm: Any,
        spec: TaskType,
        messages: MessageList,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        answer = await llm.ainvoke(messages)
        text, _thinking = llm_text_and_thinking(answer)
        try:
            parsed = parse_output(spec, text)
        except ValueError:
            # One repair round; the second failure is a real task failure.
            answer = await llm.ainvoke(
                [
                    *messages,
                    AIMessage(content=text[:4_000]),
                    HumanMessage(
                        content=(
                            "Your previous answer was not valid. Return only the JSON "
                            "object, with no prose or code fences."
                        )
                    ),
                ]
            )
            text, _thinking = llm_text_and_thinking(answer)
            parsed = parse_output(spec, text)
        usage = getattr(answer, "usage_metadata", None) or {}
        return parsed, {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }

    async def _run_bounded_agent(
        self,
        llm: Any,
        task: AppTaskRow,
        spec: TaskType,
        row: AppRecord,
        prompt: str,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Tier 2: a bounded, read-only tool loop over the app's own tables."""
        tools = self._build_tier2_tools(task, row)
        tools_by_name = {tool.name: tool for tool in tools}
        bound = llm.bind_tools(tools)
        messages: MessageList = [
            SystemMessage(content=spec.system_prompt),
            HumanMessage(content=prompt),
        ]
        usage_in = usage_out = 0
        tool_calls = 0

        for _round in range(max(1, spec.max_rounds)):
            answer = await bound.ainvoke(messages)
            usage = getattr(answer, "usage_metadata", None) or {}
            usage_in += int(usage.get("input_tokens") or 0)
            usage_out += int(usage.get("output_tokens") or 0)
            calls = list(getattr(answer, "tool_calls", None) or [])
            if not calls:
                text, _thinking = llm_text_and_thinking(answer)
                try:
                    parsed = parse_output(spec, text)
                    return parsed, {
                        "input_tokens": usage_in,
                        "output_tokens": usage_out,
                    }
                except ValueError:
                    parsed, extra = await self._ask_json(
                        llm,
                        spec,
                        [
                            *messages,
                            answer,
                            HumanMessage(
                                content=(
                                    "Return the final JSON object now, with no prose "
                                    "or code fences."
                                )
                            ),
                        ],
                    )
                    return parsed, {
                        "input_tokens": usage_in + extra["input_tokens"],
                        "output_tokens": usage_out + extra["output_tokens"],
                    }

            messages.append(answer)
            for call in calls:
                name = str(call.get("name") or "")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                if tool_calls >= spec.max_tool_calls:
                    content = json.dumps({"error": "tool budget exhausted"})
                else:
                    tool = tools_by_name.get(name)
                    if tool is None:
                        content = json.dumps({"error": f"unknown tool: {name}"})
                    else:
                        tool_calls += 1
                        try:
                            content = await tool.ainvoke(args)
                        except Exception as exc:  # noqa: BLE001 - feed the error back
                            content = json.dumps({"error": str(exc)[:500]})
                messages.append(
                    ToolMessage(
                        content=str(content)[:MAX_TOOL_RESULT_CHARS],
                        tool_call_id=str(call.get("id") or name),
                    )
                )
            if task.cancel_requested:
                raise AppTaskCancelled(task.task_id)

        parsed, extra = await self._ask_json(
            llm,
            spec,
            [
                *messages,
                HumanMessage(
                    content="Return the final JSON object now, with no prose or code fences."
                ),
            ],
        )
        return parsed, {
            "input_tokens": usage_in + extra["input_tokens"],
            "output_tokens": usage_out + extra["output_tokens"],
        }

    def _build_tier2_tools(self, task: AppTaskRow, row: AppRecord) -> ToolList:
        service = self._app_service

        async def query_rows(
            table: str = MAIN_TABLE_KEY,
            filters: dict[str, str] | None = None,
            sort: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> str:
            table_decision = service.authorize_actor(
                task.actor_user_id,
                row,
                app_access.Action.VIEW_ROW,
                table_key=table,
            )
            if not table_decision.allowed:
                return json.dumps({"error": "forbidden"}, ensure_ascii=False)
            try:
                _schema, rows, row_meta, total = await service.query_rows_detailed(
                    row,
                    table=table,
                    filters=filters,
                    sort=sort,
                    limit=min(int(limit), 100),
                    offset=int(offset),
                    pii_policy=table_decision.pii_policy,
                    viewer_user_id=task.actor_user_id,
                    include_all_rows=table_decision.row_scope == "all",
                )
            except Exception as exc:  # noqa: BLE001 - feed the error back
                return json.dumps({"error": str(exc)[:500]}, ensure_ascii=False)
            return json.dumps(
                {"rows": rows, "row_meta": row_meta, "total": total},
                ensure_ascii=False,
            )[:MAX_TOOL_RESULT_CHARS]

        async def aggregate(
            table: str = MAIN_TABLE_KEY,
            group_by: str | None = None,
            metric: str | None = None,
            agg: str = "count",
            filters: dict[str, str] | None = None,
        ) -> str:
            table_decision = service.authorize_actor(
                task.actor_user_id,
                row,
                app_access.Action.AGGREGATE,
                table_key=table,
            )
            if not table_decision.allowed:
                return json.dumps({"error": "forbidden"}, ensure_ascii=False)
            try:
                result = await service.aggregate_rows(
                    row,
                    table=table,
                    group_by=group_by,
                    metric=metric,
                    agg=agg,
                    filters=filters,
                    viewer_user_id=task.actor_user_id,
                    include_all_rows=table_decision.row_scope == "all",
                )
            except Exception as exc:  # noqa: BLE001 - feed the error back
                return json.dumps({"error": str(exc)[:500]}, ensure_ascii=False)
            return json.dumps(result, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS]

        return [
            StructuredTool.from_function(
                coroutine=query_rows,
                name="query_rows",
                description=(
                    "Read rows from one app table with optional equality filters, "
                    "sorting, and paging. PII is masked for the requesting user."
                ),
                args_schema=_QueryRowsArgs,
            ),
            StructuredTool.from_function(
                coroutine=aggregate,
                name="aggregate",
                description=(
                    "Compute a server-side aggregate (sum/avg/count/min/max) for one "
                    "app table, optionally grouped by a column."
                ),
                args_schema=_AggregateArgs,
            ),
        ]

    async def _write_back(
        self,
        task: AppTaskRow,
        spec: TaskType,
        row: AppRecord,
        row_record: Any,
        schema: Schema,
        parsed: dict[str, Any],
        decision: app_access.Decision,
    ) -> bool:
        write_column = str(task.input.get("write_column") or "").strip()
        if not write_column:
            return False
        column = schema.column(write_column)
        if column is None or column.type != "string" or column.pii:
            raise _invalid("write_column is no longer writable")
        value = str(parsed[spec.write_key])[:MAX_WRITE_CHARS]
        await self._app_service.update_row(
            row,
            task.row_id,
            {write_column: value},
            table=task.table_key,
            expected_version=row_record.version,
            viewer_user_id=task.actor_user_id,
            include_all_rows=decision.row_scope == "all",
            actor_user_id=task.actor_user_id,
            source="app_task",
        )
        return True

    def finish(
        self,
        task: AppTaskRow,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
        lease_until: int,
    ) -> bool:
        finished = self._task_repo.finish(
            task.task_id,
            lease_until=lease_until,
            status=status,
            result=result,
            error=error,
            finished_at=int(self._clock()),
        )
        self._audit(
            f"user:{task.actor_user_id}",
            "miniapp.agent_task.finish",
            task,
            {"status": status, "error": error[:500]},
        )
        return finished

    # ----- helpers -------------------------------------------------------

    def _task_for_app(self, row: AppRecord, task_id: str) -> AppTaskRow:
        task = self._task_repo.get(task_id)
        if task is None or task.app_id != row.app_id:
            raise OctopError.localized(ErrorCode.APP_NOT_FOUND)
        return task

    def _require_app_view(self, row: AppRecord, user_id: int) -> None:
        decision = self._app_service.authorize_actor(user_id, row, app_access.Action.VIEW_APP)
        if not decision.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)

    def _require_view(self, row: AppRecord, user_id: int, task: AppTaskRow) -> None:
        row_record = self._app_repo.get_data_row(row.app_id, task.row_id)
        decision = self._app_service.authorize_actor(
            user_id,
            row,
            app_access.Action.VIEW_ROW if row_record is not None else app_access.Action.VIEW_APP,
            table_key=task.table_key if row_record is not None else None,
            row_record=row_record,
        )
        if not decision.allowed:
            raise OctopError.localized(ErrorCode.APP_FORBIDDEN)

    def _record_usage(
        self,
        task: AppTaskRow,
        row: AppRecord,
        model_ref: str,
        usage: Mapping[str, Any],
    ) -> None:
        if self._usage_repo is None:
            return
        try:
            self._usage_repo.record(
                agent_id=row.agent_id,
                # The app owner pays for app AI; the task actor is audited separately.
                user_id=row.owner_user_id,
                app_id=task.app_id,
                task_id=task.task_id,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                model=model_ref,
                source="app_task",
            )
        except Exception:
            logger.warning("failed to record app task usage", exc_info=True)

    def _audit(
        self,
        actor: str,
        action: str,
        task: AppTaskRow,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._audit_repo is None:
            return
        self._audit_repo.write(
            actor=actor,
            action=action,
            target=task.app_id,
            payload=json.dumps(
                {"task_id": task.task_id, "task_type": task.task_type, **(payload or {})},
                ensure_ascii=False,
            ),
        )


__all__ = [
    "AppTaskCancelled",
    "AppTaskResult",
    "AppTaskService",
    "MAX_ACTIVE_TASKS_PER_APP",
]
