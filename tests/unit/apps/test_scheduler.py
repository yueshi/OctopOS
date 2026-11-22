"""Unit tests for the light-app scheduler and its DB lease."""

from __future__ import annotations

import asyncio

import pytest

from octop.infra.apps.scheduler import AppSourceScheduler
from octop.infra.apps.service import ImportResult
from octop.infra.db.repos.app_schedules import schedule_jitter
from octop.infra.metrics import METRICS
from tests.unit.apps.test_service import (  # noqa: F401
    FixedClock,
    World,
    _create,
    world,
)


def _scheduler(world: World) -> AppSourceScheduler:  # noqa: F811
    return AppSourceScheduler(
        repo=world.repo,
        service=world.service,
        schedule_repo=world.schedule_repo,
        scan_seconds=3600,
    )


async def test_tick_runs_due_sources_and_rearms(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    await world.workspace.awrite_text("inbound/imports/s.csv", "title,qty,status\nmug,1,new\n")
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=60,
        params={"path": "inbound/imports/s.csv"},
        created_by=world.alice_id,
    )
    sched = _scheduler(world)

    # Saving a scheduled source mirrors it into a deterministic schedule row.
    item = world.schedule_repo.get("sch_src_s1")
    assert item is not None and item.action == "sync_source"
    # First runs are jittered so many new sources do not hit the connector in
    # the same tick; force this one due for the test.
    world.schedule_repo.update("sch_src_s1", interval_seconds=60, enabled=True, next_run_at=0)

    # Run imports the file.
    assert await sched.tick() == 1
    _, rows, total = await world.service.read_rows(row)
    assert total == 1 and rows[0]["title"] == "mug"
    view = world.service.get_source(row, "s1")
    assert view.last_status == "ok" and view.last_row_count == 1

    # Re-armed one interval out: not due until it passes.
    assert await sched.tick() == 0
    item = world.schedule_repo.get("sch_src_s1")
    assert item is not None and item.next_run_at > item.last_run_at  # type: ignore[operator]
    # Same content would be deduplicated by source idempotency; import a
    # genuinely new batch instead.
    await world.workspace.awrite_text("inbound/imports/s.csv", "title,qty,status\ncup,2,done\n")
    world.schedule_repo.update("sch_src_s1", interval_seconds=60, enabled=True, next_run_at=0)
    assert await sched.tick() == 1
    _, _, total = await world.service.read_rows(row)
    assert total == 2


async def test_tick_survives_failures(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=60,
        params={"path": "inbound/imports/missing.csv"},
        created_by=world.alice_id,
    )
    world.schedule_repo.update("sch_src_s1", interval_seconds=60, enabled=True, next_run_at=0)
    sched = _scheduler(world)

    # The failing run is contained: tick counts it, state records the error.
    assert await sched.tick() == 1
    view = world.service.get_source(row, "s1")
    assert view.last_status == "error" and view.last_error

    source = world.repo.get_source(app_id, "s1")
    assert source is not None and source.fail_count == 1
    item = world.schedule_repo.get("sch_src_s1")
    assert item is not None
    assert item.last_status == "error" and item.failure_count == 1
    assert item.next_run_at > item.last_run_at  # type: ignore[operator]
    assert not await sched.tick()


async def test_only_one_instance_runs_a_due_schedule(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    await world.workspace.awrite_text("inbound/imports/s.csv", "title,qty,status\nmug,1,new\n")
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=60,
        params={"path": "inbound/imports/s.csv"},
        created_by=world.alice_id,
    )
    world.schedule_repo.update("sch_src_s1", interval_seconds=60, enabled=True, next_run_at=0)
    first = _scheduler(world)
    second = _scheduler(world)

    ran = await asyncio.gather(first.tick(now=9_999_999_999), second.tick(now=9_999_999_999))
    assert sum(ran) == 1
    _, _, total = await world.service.read_rows(row)
    assert total == 1


async def test_claim_and_heartbeat_are_lease_guarded(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=60,
        params={"path": "inbound/imports/s.csv"},
        created_by=world.alice_id,
    )
    repo = world.schedule_repo
    due = 9_999_999_999
    lease = repo.claim("sch_src_s1", now=due, lease_seconds=300)
    assert lease == due + 300
    assert repo.claim("sch_src_s1", now=due, lease_seconds=300) is None
    renewed = repo.heartbeat("sch_src_s1", lease_until=lease, now=due + 60, lease_seconds=300)
    assert renewed == due + 360
    assert repo.heartbeat("sch_src_s1", lease_until=lease, now=due, lease_seconds=300) is None
    assert repo.finish(
        "sch_src_s1",
        lease_until=renewed,
        run_at=due,
        status="ok",
        next_run_at=due + 60,
        failure_count=0,
    )
    assert repo.get("sch_src_s1").next_run_at == due + 60  # type: ignore[union-attr]


async def test_deleting_a_source_removes_its_schedule(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=60,
        params={"path": "inbound/imports/s.csv"},
        created_by=world.alice_id,
    )
    assert world.schedule_repo.get("sch_src_s1") is not None
    world.service.delete_source(row, "s1")
    assert world.schedule_repo.get("sch_src_s1") is None


async def test_start_and_shutdown(world: World) -> None:  # noqa: F811
    sched = _scheduler(world)
    sched.start()
    task = sched._task
    assert task is not None
    sched.start()  # idempotent: keeps the same task
    assert sched._task is task
    await asyncio.sleep(0)
    await sched.shutdown()
    assert sched._task is None
    await sched.shutdown()  # second call is a no-op


async def test_snapshot_schedule_refreshes_an_existing_publication(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    await world.service.write_html(row, "<h1>v1</h1>")

    # No public link yet: refuse to create a schedule that could only fail.
    try:
        world.service.save_schedule(
            row,
            action="snapshot_publication",
            interval_seconds=60,
            created_by=world.alice_id,
        )
    except Exception as exc:  # noqa: BLE001
        assert "publish" in str(exc)
    else:  # pragma: no cover - the guard must reject
        raise AssertionError("snapshot schedule should require a publication")

    await world.service.publish(row, user_id=world.alice_id)
    view = world.service.save_schedule(
        row,
        action="snapshot_publication",
        interval_seconds=60,
        created_by=world.alice_id,
    )
    await world.service.write_html(row, "<h1>v2</h1>")
    assert world.service.public_snapshot(row) is not None
    assert "<h1>v1</h1>" in world.service.public_snapshot(row)[0]  # type: ignore[index]
    world.schedule_repo.update(view.schedule_id, interval_seconds=60, enabled=True, next_run_at=0)

    assert await _scheduler(world).tick() == 1
    assert "<h1>v2</h1>" in world.service.public_snapshot(row)[0]  # type: ignore[index]
    item = world.schedule_repo.get(view.schedule_id)
    assert item is not None and item.last_status == "ok"


async def test_source_rearm_uses_finish_time(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = await _create(world)
    row = world.service.get(app_id)
    clock = FixedClock(1000)
    world.service._clock = clock
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="external",
        mode="append",
        schedule_seconds=60,
        params={"instance_id": "conn", "tool": "fetch"},
        created_by=world.alice_id,
    )

    async def slow_run(*_args: object, **_kwargs: object) -> ImportResult:
        clock.value = 1600
        return ImportResult(imported=0, skipped=0, errors=[])

    monkeypatch.setattr(world.service, "_run_external_source", slow_run)
    await world.service.run_source(row, "s1")

    item = world.repo.get_source(app_id, "s1")
    assert item is not None and item.next_run_at is not None
    # A run longer than the interval must re-arm from 1600, not from 1000.
    assert item.next_run_at == 1600 + 60 + schedule_jitter("sch_src_s1", 60)


async def test_action_timeout_marks_error_and_releases_lease(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = await _create(world)
    item = world.schedule_repo.create(
        app_id=app_id,
        action="snapshot_publication",
        interval_seconds=60,
        created_by=world.alice_id,
        next_run_at=1000,
    )

    async def hang(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(world.service, "execute_schedule", hang)
    before = METRICS.snapshot()
    sched = AppSourceScheduler(
        repo=world.repo,
        service=world.service,
        schedule_repo=world.schedule_repo,
        clock=lambda: 1000,
        action_timeouts={"snapshot_publication": 0.05},
    )
    assert await sched.tick(now=1000) == 1

    updated = world.schedule_repo.get(item.schedule_id)
    assert updated is not None
    assert updated.last_status == "error"
    assert "timed out" in updated.last_error
    assert updated.lease_until is None
    assert updated.failure_count == 1
    after = METRICS.snapshot()
    assert after["light_app_schedule_runs_total"] == before["light_app_schedule_runs_total"] + 1
    assert (
        after["light_app_schedule_timeouts_total"]
        == before["light_app_schedule_timeouts_total"] + 1
    )


async def test_interval_change_resets_failure_state(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    item = world.schedule_repo.create(
        app_id=app_id,
        action="snapshot_publication",
        interval_seconds=60,
        created_by=world.alice_id,
        next_run_at=1000,
    )
    world.schedule_repo.record_run(
        item.schedule_id,
        run_at=1000,
        status="error",
        next_run_at=1060,
        failure_count=3,
        error="boom",
    )
    view = world.service.update_schedule(
        row,
        item.schedule_id,
        interval_seconds=120,
        enabled=True,
    )
    assert view.failure_count == 0
    assert view.last_error == ""
    updated = world.schedule_repo.get(item.schedule_id)
    assert updated is not None
    assert updated.interval_seconds == 120
    assert updated.failure_count == 0
    assert updated.next_run_at > item.next_run_at


def test_schedule_jitter_is_stable_and_bounded() -> None:
    value = schedule_jitter("sch_src_s1", 60)
    assert value == schedule_jitter("sch_src_s1", 60)
    assert 0 <= value <= 30
    assert 0 <= schedule_jitter("sch_other", 86_400) <= 300


async def test_revoking_the_public_link_removes_its_snapshot_schedule(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    await world.service.write_html(row, "<h1>v1</h1>")
    await world.service.publish(row, user_id=world.alice_id)
    view = world.service.save_schedule(
        row,
        action="snapshot_publication",
        interval_seconds=60,
        created_by=world.alice_id,
    )
    assert world.schedule_repo.get(view.schedule_id) is not None
    assert world.service.revoke_publication(row) is True
    assert world.schedule_repo.get(view.schedule_id) is None


async def test_saving_a_source_with_a_new_interval_resets_failure_state(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    row = world.service.get(app_id)
    params = {"path": "inbound/imports/s.csv"}
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=60,
        params=params,
        created_by=world.alice_id,
    )
    world.schedule_repo.record_run(
        "sch_src_s1",
        run_at=1000,
        status="error",
        next_run_at=1060,
        failure_count=3,
        error="boom",
    )
    world.service.save_source(
        row,
        source_id="s1",
        table_key="main",
        kind="workspace_file",
        mode="append",
        schedule_seconds=120,
        params=params,
        created_by=world.alice_id,
    )
    item = world.schedule_repo.get("sch_src_s1")
    assert item is not None
    assert item.interval_seconds == 120
    assert item.failure_count == 0
    assert item.last_error == ""
    assert item.next_run_at > 1060


async def test_tick_claims_each_item_with_a_fresh_run_at(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long first action must not leave the next lease already expired."""
    clock = FixedClock(1000)
    first_app = await _create(world, name="First")
    second_app = await _create(world, name="Second")
    for app_id, source_id in ((first_app, "s1"), (second_app, "s2")):
        row = world.service.get(app_id)
        path = f"inbound/imports/{source_id}.csv"
        await world.workspace.awrite_text(path, "title,qty,status\nmug,1,new\n")
        world.service.save_source(
            row,
            source_id=source_id,
            table_key="main",
            kind="workspace_file",
            mode="append",
            schedule_seconds=60,
            params={"path": path},
            created_by=world.alice_id,
        )
        world.schedule_repo.update(
            f"sch_src_{source_id}", interval_seconds=60, enabled=True, next_run_at=0
        )

    claimed_at: list[int] = []
    original_claim = world.schedule_repo.claim

    def claim(
        schedule_id: str, *, now: int, lease_seconds: int, due_only: bool = True
    ) -> int | None:
        claimed_at.append(now)
        return original_claim(schedule_id, now=now, lease_seconds=lease_seconds, due_only=due_only)

    async def slow_action(*_args: object, **_kwargs: object) -> ImportResult:
        clock.value += 600
        await asyncio.sleep(0)
        return ImportResult(imported=0, skipped=0, errors=[])

    monkeypatch.setattr(world.schedule_repo, "claim", claim)
    monkeypatch.setattr(world.service, "execute_schedule", slow_action)

    sched = AppSourceScheduler(
        repo=world.repo,
        service=world.service,
        schedule_repo=world.schedule_repo,
        clock=clock,
    )
    assert await sched.tick() == 2
    assert claimed_at == [1000, 1600]


async def test_heartbeat_keeps_renewing_after_a_transient_error(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = await _create(world)
    item = world.schedule_repo.create(
        app_id=app_id,
        action="snapshot_publication",
        interval_seconds=60,
        created_by=world.alice_id,
        next_run_at=1000,
    )
    calls: list[int] = []
    original_heartbeat = world.schedule_repo.heartbeat

    def flaky_heartbeat(
        schedule_id: str,
        *,
        lease_until: int,
        now: int,
        lease_seconds: int,
    ) -> int | None:
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return original_heartbeat(
            schedule_id,
            lease_until=lease_until,
            now=now,
            lease_seconds=lease_seconds,
        )

    async def slow_action(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0.05)

    monkeypatch.setattr(world.schedule_repo, "heartbeat", flaky_heartbeat)
    monkeypatch.setattr(world.service, "execute_schedule", slow_action)
    monkeypatch.setattr("octop.infra.apps.scheduler.HEARTBEAT_SECONDS", 0.01)

    sched = AppSourceScheduler(
        repo=world.repo,
        service=world.service,
        schedule_repo=world.schedule_repo,
        clock=lambda: 1000,
    )
    assert await sched.tick(now=1000) == 1
    assert len(calls) >= 2
    updated = world.schedule_repo.get(item.schedule_id)
    assert updated is not None and updated.last_status == "ok"


async def test_lease_loss_cancels_the_running_action(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = await _create(world)
    item = world.schedule_repo.create(
        app_id=app_id,
        action="snapshot_publication",
        interval_seconds=60,
        created_by=world.alice_id,
        next_run_at=1000,
    )
    cancelled = asyncio.Event()

    async def hang(*_args: object, **_kwargs: object) -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(world.service, "execute_schedule", hang)
    monkeypatch.setattr(world.schedule_repo, "heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("octop.infra.apps.scheduler.HEARTBEAT_SECONDS", 0.01)

    before = METRICS.snapshot()["light_app_schedule_lease_lost_total"]
    sched = AppSourceScheduler(
        repo=world.repo,
        service=world.service,
        schedule_repo=world.schedule_repo,
        clock=lambda: 1000,
    )
    assert await sched.tick(now=1000) == 1
    assert cancelled.is_set()
    updated = world.schedule_repo.get(item.schedule_id)
    assert updated is not None and updated.last_status == ""
    after = METRICS.snapshot()["light_app_schedule_lease_lost_total"]
    assert after == before + 1


async def test_snapshot_schedule_is_unique_per_app(world: World) -> None:  # noqa: F811
    app_id = await _create(world)
    first = world.schedule_repo.create_snapshot(
        app_id=app_id,
        interval_seconds=60,
        created_by=world.alice_id,
    )
    second = world.schedule_repo.create_snapshot(
        app_id=app_id,
        interval_seconds=120,
        created_by=world.alice_id,
    )
    assert second.schedule_id == first.schedule_id
    assert second.interval_seconds == 120
    assert world.schedule_repo.count_for_app(app_id) == 1
