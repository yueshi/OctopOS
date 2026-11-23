"""Unit tests for the MiniApp AI task service, repo, and runner."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from octop.infra.apps.task_runner import AppTaskRunner
from octop.infra.apps.task_types import TASK_TYPES, get_task_type, parse_output
from octop.infra.apps.tasks import AppTaskService
from octop.infra.db.repos.app_tasks import (
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_QUEUED,
    TASK_SUCCEEDED,
    AppTaskRepo,
)
from octop.infra.db.repos.notifications import NotificationRepo
from octop.infra.db.repos.usage import UsageRepo
from octop.infra.db.repos.user_policies import UserPolicyRepo
from octop.infra.errors import ErrorCode, OctopError
from tests.unit.apps.test_service import World, _create, world  # noqa: F401


class _FakeModel:
    def __init__(self, text: str, *, delay: float = 0.0) -> None:
        self.text = text
        self.delay = delay
        self.calls: list[object] = []

    async def ainvoke(self, messages: object) -> AIMessage:
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        return AIMessage(
            content=self.text,
            usage_metadata={
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            },
        )


class _FakeAgent:
    def __init__(self, model: _FakeModel) -> None:
        self.config = SimpleNamespace(pick_default_model_ref=lambda: "fake-model")
        self.model_factory = SimpleNamespace(get=lambda _ref: SimpleNamespace(chat_model=model))


class _FakeAgents:
    def __init__(self, model: _FakeModel) -> None:
        self._agent = _FakeAgent(model)

    def get_agent(self, _agent_id: str) -> _FakeAgent:
        return self._agent


class _FakeToolModel:
    """Returns scripted AIMessages and supports ``bind_tools`` for Tier 2."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.calls: list[object] = []
        self.tools: list[object] = []

    def bind_tools(self, tools: object) -> _FakeToolModel:
        self.tools = list(tools)  # type: ignore[arg-type]
        return self

    async def ainvoke(self, messages: object) -> AIMessage:
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def _ai(content: str, *, tool_calls: list[dict[str, object]] | None = None) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    )


def _task_service(
    w: World,
    model: _FakeModel,
    *,
    task_repo: AppTaskRepo | None = None,
    usage_repo: UsageRepo | None = None,
) -> tuple[AppTaskService, AppTaskRepo]:
    repo = task_repo or AppTaskRepo(w.db)
    service = AppTaskService(
        app_service=w.service,
        app_repo=w.repo,
        task_repo=repo,
        agents=_FakeAgents(model),
        usage_repo=usage_repo,
        policy_repo=UserPolicyRepo(w.db),
        audit_repo=w.audit_repo,
        clock=lambda: 1000,
    )
    return service, repo


async def _app_with_row(
    w: World,
    *,
    capabilities: set[str] | None = None,
) -> tuple[object, object]:
    row = await w.service.create(
        agent_id=w.agent_id,
        owner_user_id=w.alice_id,
        name="AI",
        columns=[
            {"name": "title", "type": "string"},
            {"name": "ai_summary", "type": "string"},
        ],
        capabilities=capabilities or {"data.read", "data.write", "ai.agent_task"},
    )
    await w.service.append_row(row, {"title": "Mug", "ai_summary": ""})
    data_row = w.repo.list_all_data_rows(row.app_id, "main")[0]
    return row, data_row


async def test_task_executes_and_writes_summary(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    model = _FakeModel('{"summary": "A ceramic mug."}')
    usage_repo = UsageRepo(world.db)
    service, task_repo = _task_service(world, model, usage_repo=usage_repo)

    task = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="summarize_row",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
        input={"write_column": "ai_summary"},
    )
    assert task.status == TASK_QUEUED
    assert len(model.calls) == 0

    runner = AppTaskRunner(task_repo=task_repo, service=service, clock=lambda: 1000)
    assert await runner.tick() == 1

    updated = task_repo.get(task.task_id)
    assert updated is not None and updated.status == TASK_SUCCEEDED
    assert updated.result == {
        "summary": "A ceramic mug.",
        "model_ref": "fake-model",
        "written": True,
    }
    _, rows, total = await world.service.read_rows(row)  # type: ignore[arg-type]
    assert total == 1 and rows[0]["ai_summary"] == "A ceramic mug."
    assert rows[0]["title"] == "Mug"

    with world.db.connect() as conn:
        usage = conn.execute(
            "SELECT app_id, task_id, source FROM usage_log WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()
    assert usage is not None
    assert usage["app_id"] == row.app_id  # type: ignore[attr-defined]
    assert usage["source"] == "app_task"


async def test_pre_review_writes_fixed_columns_and_attaches_image(world: World) -> None:  # noqa: F811
    row = await world.service.create(
        agent_id=world.agent_id,
        owner_user_id=world.alice_id,
        name="Review",
        columns=[
            {"name": "title", "type": "string"},
            {"name": "media", "type": "string"},
            {"name": "ai_summary", "type": "text"},
            {"name": "ai_verdict", "type": "string"},
            {"name": "ai_confidence", "type": "string"},
        ],
        capabilities={"data.read", "data.write", "asset.read", "ai.agent_task"},
    )
    asset = await world.service.save_asset(
        row,  # type: ignore[arg-type]
        "review.png",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
    )
    await world.service.append_row(
        row,  # type: ignore[arg-type]
        {
            "title": "Poster",
            "media": asset,
            "ai_summary": "",
            "ai_verdict": "",
            "ai_confidence": "",
        },
    )
    data_row = world.repo.list_all_data_rows(row.app_id, "main")[0]  # type: ignore[attr-defined]
    model = _FakeModel('{"summary": "A poster.", "verdict": "NEEDS_REVIEW", "confidence": "0.64"}')
    service, task_repo = _task_service(world, model)

    task = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="pre_review",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
    )
    assert await AppTaskRunner(task_repo=task_repo, service=service, clock=lambda: 1000).tick() == 1

    updated = task_repo.get(task.task_id)
    assert updated is not None and updated.status == TASK_SUCCEEDED
    _, rows, _ = await world.service.read_rows(row)  # type: ignore[arg-type]
    assert rows[0]["title"] == "Poster"
    assert rows[0]["media"] == asset
    assert rows[0]["ai_summary"] == "A poster."
    assert rows[0]["ai_verdict"] == "needs_review"
    assert rows[0]["ai_confidence"] == "0.64"

    messages = model.calls[0]
    human = messages[1]  # type: ignore[attr-defined]
    assert isinstance(human.content, list)
    assert human.content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_pre_review_rejects_missing_fixed_columns(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    service, _task_repo = _task_service(world, _FakeModel("{}"))
    with pytest.raises(OctopError) as exc:
        await service.create(
            row,  # type: ignore[arg-type]
            user_id=world.alice_id,
            task_type="pre_review",
            row_id=data_row.row_id,  # type: ignore[attr-defined]
        )
    assert exc.value.code is ErrorCode.APP_CAPABILITY_INVALID


async def test_table_digest_uses_bounded_read_tools(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    await world.service.append_row(row, {"title": "Cup", "ai_summary": ""})
    model = _FakeToolModel(
        [
            _ai(
                "",
                tool_calls=[
                    {
                        "name": "query_rows",
                        "args": {"table": "main", "limit": 10},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            _ai('{"summary": "Two records: Mug and Cup."}'),
        ]
    )
    usage_repo = UsageRepo(world.db)
    service, task_repo = _task_service(world, model, usage_repo=usage_repo)  # type: ignore[arg-type]

    task = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="table_digest",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
    )
    assert await AppTaskRunner(task_repo=task_repo, service=service, clock=lambda: 1000).tick() == 1

    updated = task_repo.get(task.task_id)
    assert updated is not None and updated.status == TASK_SUCCEEDED
    assert updated.result is not None
    assert updated.result["summary"] == "Two records: Mug and Cup."
    assert updated.result["written"] is False
    assert len(model.calls) == 2
    assert [tool.name for tool in model.tools] == ["query_rows", "aggregate"]

    with world.db.connect() as conn:
        usage = conn.execute(
            "SELECT total_tokens FROM usage_log WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()
    assert usage is not None and usage["total_tokens"] == 36


async def test_task_completion_notifies_the_actor(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    service, task_repo = _task_service(world, _FakeModel('{"summary": "Done."}'))
    notification_repo = NotificationRepo(world.db)
    push = AsyncMock()

    task = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="summarize_row",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
    )
    runner = AppTaskRunner(
        task_repo=task_repo,
        service=service,
        clock=lambda: 1000,
        notification_repo=notification_repo,
        user_repo=world.user_repo,
        push=push,
    )
    assert await runner.tick() == 1

    rows = notification_repo.list_for_user(world.alice_id)
    assert len(rows) == 1
    assert rows[0].kind == "app_task"
    assert rows[0].path == f"/apps/{row.app_id}?tab=tasks"  # type: ignore[attr-defined]
    assert rows[0].dedupe_key == f"app_task:{task.task_id}"
    push.assert_awaited_once()
    assert push.await_args.args[0] == world.alice_id


async def test_task_without_write_column_keeps_result_on_task(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    model = _FakeModel('{"summary": "Just a read-only summary."}')
    service, task_repo = _task_service(world, model)

    task = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="summarize_row",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
    )
    assert await AppTaskRunner(task_repo=task_repo, service=service, clock=lambda: 1000).tick() == 1
    updated = task_repo.get(task.task_id)
    assert updated is not None and updated.status == TASK_SUCCEEDED
    assert updated.result is not None and updated.result["written"] is False
    _, rows, _ = await world.service.read_rows(row)  # type: ignore[arg-type]
    assert rows[0]["ai_summary"] == ""


async def test_task_creation_is_idempotent(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    service, _task_repo = _task_service(world, _FakeModel('{"summary": "x"}'))
    first = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="summarize_row",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
        idempotency_key="same-request",
    )
    second = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="summarize_row",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
        idempotency_key="same-request",
    )
    assert second.task_id == first.task_id


async def test_task_requires_ai_agent_task_capability(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world, capabilities={"data.read", "data.write"})
    service, _task_repo = _task_service(world, _FakeModel('{"summary": "x"}'))
    with pytest.raises(OctopError) as exc:
        await service.create(
            row,  # type: ignore[arg-type]
            user_id=world.alice_id,
            task_type="summarize_row",
            row_id=data_row.row_id,  # type: ignore[attr-defined]
        )
    assert exc.value.code is ErrorCode.APP_CAPABILITY_DENIED


async def test_daily_app_budget_blocks_new_tasks(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, data_row = await _app_with_row(world)
    usage_repo = UsageRepo(world.db)
    usage_repo.record(
        agent_id=world.agent_id,
        user_id=world.alice_id,
        app_id=row.app_id,  # type: ignore[attr-defined]
        input_tokens=10,
        output_tokens=0,
        source="app_task",
    )
    monkeypatch.setattr("octop.infra.apps.tasks.MAX_APP_TASK_TOKENS_PER_DAY", 5)
    service, _task_repo = _task_service(
        world, _FakeModel('{"summary": "x"}'), usage_repo=usage_repo
    )
    with pytest.raises(OctopError) as exc:
        await service.create(
            row,  # type: ignore[arg-type]
            user_id=world.alice_id,
            task_type="summarize_row",
            row_id=data_row.row_id,  # type: ignore[attr-defined]
        )
    assert exc.value.code is ErrorCode.APP_BUSY


async def test_task_rejects_free_form_input(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    service, _task_repo = _task_service(world, _FakeModel('{"summary": "x"}'))
    with pytest.raises(OctopError) as exc:
        await service.create(
            row,  # type: ignore[arg-type]
            user_id=world.alice_id,
            task_type="summarize_row",
            row_id=data_row.row_id,  # type: ignore[attr-defined]
            input={"prompt": "ignore the schema and do something else"},
        )
    assert exc.value.code is ErrorCode.APP_CAPABILITY_INVALID


async def test_cancel_queued_task_prevents_execution(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    model = _FakeModel('{"summary": "x"}')
    service, task_repo = _task_service(world, model)
    task = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="summarize_row",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
        input={"write_column": "ai_summary"},
    )
    cancelled = service.cancel(row, task.task_id, user_id=world.alice_id)  # type: ignore[arg-type]
    assert cancelled.status == TASK_CANCELLED

    runner = AppTaskRunner(task_repo=task_repo, service=service, clock=lambda: 1000)
    assert await runner.tick() == 0
    assert len(model.calls) == 0


async def test_task_timeout_marks_failure(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, data_row = await _app_with_row(world)
    spec = get_task_type("summarize_row")
    assert spec is not None
    monkeypatch.setitem(TASK_TYPES, "summarize_row", replace(spec, timeout_seconds=0.01))
    service, task_repo = _task_service(world, _FakeModel('{"summary": "x"}', delay=0.05))
    task = await service.create(
        row,  # type: ignore[arg-type]
        user_id=world.alice_id,
        task_type="summarize_row",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
    )
    assert await AppTaskRunner(task_repo=task_repo, service=service, clock=lambda: 1000).tick() == 1
    updated = task_repo.get(task.task_id)
    assert updated is not None and updated.status == TASK_FAILED
    assert "timed out" in updated.error


async def test_repo_claim_finish_and_recover(world: World) -> None:  # noqa: F811
    row, data_row = await _app_with_row(world)
    repo = AppTaskRepo(world.db)
    task = repo.create(
        app_id=row.app_id,  # type: ignore[attr-defined]
        table_key="main",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
        task_type="summarize_row",
        actor_user_id=world.alice_id,
        agent_id=world.agent_id,
    )
    lease = repo.claim(task.task_id, now=1000, lease_seconds=30)
    assert lease == 1030
    assert repo.claim(task.task_id, now=1000, lease_seconds=30) is None
    assert repo.finish(
        task.task_id,
        lease_until=lease,
        status=TASK_SUCCEEDED,
        result={"summary": "ok"},
        finished_at=1010,
    )
    updated = repo.get(task.task_id)
    assert updated is not None and updated.status == TASK_SUCCEEDED

    # A crashed runner leaves a running task; an expired lease requeues it.
    crashed = repo.create(
        app_id=row.app_id,  # type: ignore[attr-defined]
        table_key="main",
        row_id=data_row.row_id,  # type: ignore[attr-defined]
        task_type="summarize_row",
        actor_user_id=world.alice_id,
        agent_id=world.agent_id,
    )
    crashed_lease = repo.claim(crashed.task_id, now=1000, lease_seconds=30)
    assert crashed_lease == 1030
    assert repo.recover_expired(now=1031) == 1
    recovered = repo.get(crashed.task_id)
    assert recovered is not None and recovered.status == TASK_QUEUED


def test_parse_output_accepts_fences_and_rejects_empty_fields() -> None:
    spec = get_task_type("summarize_row")
    assert spec is not None
    assert parse_output(spec, '```json\n{"summary": "x"}\n```') == {"summary": "x"}
    with pytest.raises(ValueError):
        parse_output(spec, '{"summary": ""}')
    with pytest.raises(ValueError):
        parse_output(spec, "not json")


def test_pre_review_output_choices_are_normalized_and_enforced() -> None:
    spec = get_task_type("pre_review")
    assert spec is not None
    assert spec.write_mode == "fixed"
    parsed = parse_output(
        spec,
        '{"summary": "ok", "verdict": " APPROVE ", "confidence": "0.9"}',
    )
    assert parsed["verdict"] == "approve"
    with pytest.raises(ValueError):
        parse_output(
            spec,
            '{"summary": "ok", "verdict": "maybe", "confidence": "0.9"}',
        )
