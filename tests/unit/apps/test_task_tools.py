"""Agent tools for creating and inspecting MiniApp AI tasks."""

from __future__ import annotations

import json

from octop.infra.apps.tasks import AppTaskService
from octop.infra.apps.tools import build_app_tools
from tests.unit.apps.test_tools import (  # noqa: F401
    COLUMNS,
    World,
    _configurable,
    world,
)


def _tools(w: World) -> dict[str, object]:
    task_service = AppTaskService(
        app_service=w.service,
        app_repo=w.repos.light_app_repo,
        task_repo=w.repos.app_task_repo,
        agents=None,
        usage_repo=w.repos.usage_repo,
        policy_repo=w.repos.user_policy_repo,
        audit_repo=w.repos.audit_repo,
    )
    return {tool.name: tool for tool in build_app_tools(w.service, task_service=task_service)}


async def test_agent_task_tools_lifecycle(world: World) -> None:  # noqa: F811
    app = await world.service.create(
        agent_id=world.agent_id,
        owner_user_id=world.alice_id,
        name="Tasks",
        columns=COLUMNS,
        capabilities={"data.read", "data.write", "ai.agent_task"},
    )
    await world.service.append_row(app, {"title": "Mug", "status": "new"})
    row = world.repos.light_app_repo.list_all_data_rows(app.app_id, "main")[0]
    tools = _tools(world)

    with _configurable(agent_id=world.agent_id, user=str(world.alice_id)):
        created = json.loads(
            await tools["app_run_task"].ainvoke(  # type: ignore[attr-defined]
                {
                    "app_id": app.app_id,
                    "task_type": "summarize_row",
                    "row_id": row.row_id,
                }
            )
        )
        assert created["task"]["status"] == "queued"
        assert created["task"]["task_type"] == "summarize_row"
        task_id = created["task"]["task_id"]

        fetched = json.loads(
            await tools["app_get_task"].ainvoke(  # type: ignore[attr-defined]
                {"app_id": app.app_id, "task_id": task_id}
            )
        )
        assert fetched["task_id"] == task_id
        assert fetched["status"] == "queued"

        listed = json.loads(
            await tools["app_list_tasks"].ainvoke(  # type: ignore[attr-defined]
                {"app_id": app.app_id}
            )
        )
        assert [task["task_id"] for task in listed["tasks"]] == [task_id]

        cancelled = json.loads(
            await tools["app_cancel_task"].ainvoke(  # type: ignore[attr-defined]
                {"app_id": app.app_id, "task_id": task_id}
            )
        )
        assert cancelled["task"]["status"] == "cancelled"
