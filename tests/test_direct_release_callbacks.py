"""Focused regression coverage for Direct release listener ownership."""

from __future__ import annotations

import asyncio
import os

from starcraft_commander.direct_command_lifecycle import DirectCommandLifecycle
from starcraft_commander.direct_release import DirectLeaseEventWriter
from starcraft_commander.micromachine_live_session import (
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
)
from starcraft_commander.micromachine_runtime import MicroMachineInMemoryBlackboard
from starcraft_commander.sc2_executor import (
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionPlan,
    SC2RuntimeExecutor,
)


def _session(*, lifecycle: DirectCommandLifecycle, executor: object | None = None, callback=None):
    return MicroMachineLiveTextSession(
        MicroMachineInMemoryBlackboard(),
        StaticJsonPolicyModulationProvider({"goal": "observe", "command_layer": "macro"}),
        direct_executor=executor,
        direct_lifecycle=lifecycle,
        on_direct_release=callback,
    )


def _complete(lifecycle: DirectCommandLifecycle, command_id: str) -> None:
    lifecycle.dispatch(
        command_id=command_id,
        issued_at_frame=0,
        ttl_seconds=5,
        completion_conditions=("done",),
    )
    lifecycle.observe(command_id, frame=1, evidence={"done": True})


def test_shared_callback_is_exactly_once_in_executor_then_session_order() -> None:
    released: list[str] = []

    def callback(lease):
        released.append(lease.command_id)

    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    executor = SC2RuntimeExecutor(direct_lifecycle=lifecycle, on_direct_release=callback)
    _session(lifecycle=lifecycle, executor=executor, callback=callback)

    _complete(lifecycle, "executor-first")

    assert released == ["executor-first"]


def test_native_event_writer_serializes_terminal_release_with_epoch(tmp_path) -> None:
    path = tmp_path / "direct-release.events"
    writer = DirectLeaseEventWriter(path, "a" * 32)
    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    executor = SC2RuntimeExecutor(direct_lifecycle=lifecycle, on_direct_release=writer)
    lifecycle.dispatch(command_id="native-complete", issued_at_frame=0, ttl_seconds=5, completion_conditions=("done",))
    lifecycle.observe("native-complete", frame=1, evidence={"done": True})
    line = path.read_text().strip()
    assert "protocol_version=voi-mm-bridge/v1" in line
    assert "state=completed" in line and "runtime_instance_id=" + "a" * 32 in line
    assert executor.lifecycle_errors == ()


def test_native_event_writer_rejects_record_delimiters(tmp_path) -> None:
    writer = DirectLeaseEventWriter(tmp_path / "events", "a" * 32)
    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    executor = SC2RuntimeExecutor(direct_lifecycle=lifecycle, on_direct_release=writer)
    lifecycle.dispatch(command_id="bad;id", issued_at_frame=0, ttl_seconds=1)
    lifecycle.observe("bad;id", frame=2)
    assert len(executor.lifecycle_errors) == 1


def test_native_event_writer_env_sink_is_wired_into_executor(tmp_path, monkeypatch) -> None:
    path = tmp_path / "env.events"
    monkeypatch.setenv("VOI_MICROMACHINE_RELEASE_EVENTS", str(path))
    monkeypatch.setenv("VOI_MICROMACHINE_RUNTIME_INSTANCE_ID", "b" * 32)
    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    SC2RuntimeExecutor(direct_lifecycle=lifecycle)
    lifecycle.dispatch(command_id="native-expired", issued_at_frame=0, ttl_seconds=1)
    lifecycle.observe("native-expired", frame=2)
    assert "state=expired" in path.read_text()


def test_shared_callback_is_exactly_once_when_session_listener_precedes_executor() -> None:
    released: list[str] = []

    def callback(lease):
        released.append(lease.command_id)

    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    _session(lifecycle=lifecycle, callback=callback)
    executor = SC2RuntimeExecutor(direct_lifecycle=lifecycle)

    _complete(lifecycle, "session-first")

    assert released == ["session-first"]
    assert executor.lifecycle_errors == ()


def test_explicit_lifecycle_rebind_keeps_preexisting_listener_and_workflow_gate() -> None:
    released: list[str] = []
    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    lifecycle.on_release = lambda lease: released.append(lease.command_id)
    executor = SC2RuntimeExecutor(direct_lifecycle=DirectCommandLifecycle(game_loops_per_second=1))

    # Binding a separately-created lifecycle must preserve its listener in the
    # executor-owned registry; subsequent session registration must not wrap it
    # outside the workflow gate.
    _session(lifecycle=lifecycle, executor=executor)

    _complete(lifecycle, "rebound")

    assert released == ["rebound"]
    assert executor.direct_lifecycle is lifecycle


def test_async_release_failure_is_owned_and_reported_after_drain() -> None:
    async def scenario() -> tuple[tuple, tuple]:
        lifecycle = DirectCommandLifecycle(game_loops_per_second=1)

        async def broken(_lease):
            await asyncio.sleep(0)
            raise RuntimeError("resume unavailable")

        executor = SC2RuntimeExecutor(direct_lifecycle=lifecycle, on_direct_release=broken)
        _complete(lifecycle, "async-failure")
        # The synchronous lifecycle tick schedules the callback; the explicit
        # drain is the ownership boundary for the running event loop.
        assert executor.lifecycle_errors == ()
        await executor.drain_direct_release_callbacks()
        return executor.lifecycle_errors, executor.direct_release_listeners.errors

    errors, listener_errors = asyncio.run(scenario())
    assert len(errors) == 1
    assert "resume unavailable" in errors[0].message
    assert list(errors) == listener_errors


def test_close_drains_async_release_callback_after_cancel() -> None:
    async def scenario() -> list[str]:
        lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
        released: list[str] = []

        async def callback(lease):
            await asyncio.sleep(0)
            released.append(lease.command_id)

        executor = SC2RuntimeExecutor(direct_lifecycle=lifecycle, on_direct_release=callback)
        lifecycle.dispatch(
            command_id="cancelled",
            issued_at_frame=0,
            ttl_seconds=5,
            completion_conditions=("done",),
        )
        lifecycle.cancel("cancelled")
        assert released == []
        await executor.close()
        return released

    assert asyncio.run(scenario()) == ["cancelled"]


def test_standalone_session_async_failure_is_recorded_without_duplicate_callback() -> None:
    async def scenario() -> tuple[list[str], tuple[str, ...]]:
        lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
        calls: list[str] = []

        async def broken(lease):
            calls.append(lease.command_id)
            raise RuntimeError("session resume failed")

        session = _session(lifecycle=lifecycle, callback=broken)
        _complete(lifecycle, "session-async-failure")
        await session.drain_direct_release_callbacks()
        return calls, session.direct_release_errors

    calls, errors = asyncio.run(scenario())
    assert calls == ["session-async-failure"]
    assert len(errors) == 1
    assert "session resume failed" in errors[0]


def test_workflow_gate_defers_parent_release_until_child_finishes() -> None:
    class WorkflowBot:
        async def train_unit(self, _action):
            return True

    async def scenario() -> list[str]:
        released: list[str] = []
        lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
        executor = SC2RuntimeExecutor(
            bot=WorkflowBot(),
            direct_lifecycle=lifecycle,
            on_direct_release=lambda lease: released.append(lease.command_id),
        )
        lifecycle.dispatch(
            command_id="parent",
            issued_at_frame=0,
            ttl_seconds=5,
            completion_conditions=("building_completed",),
        )
        child = SC2ExecutionPlan(
            intent_name="workflow:train",
            ordered_actions=(
                SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE"),
            ),
            audit={"completion_conditions": ["order_issued"]},
        )
        executor.register_dependent_plans(
            "parent",
            [{"resume_on": "building_completed", "plan": child.to_dict()}],
        )

        observed = executor.tick_direct_commands(
            1,
            {"parent": {"building_completed": True}},
        )
        assert observed[0]["state"] == "completed"
        assert released == []

        results = await executor.drain_completed_workflows(current_frame=1)
        assert len(results) == 1 and results[0].success
        executor.tick_direct_commands(2, {"parent:step:0": {"order_issued": True}})
        return released

    assert asyncio.run(scenario()) == ["parent"]


def test_session_tick_uses_executor_workflow_gate_when_lifecycles_are_shared() -> None:
    class WorkflowBot:
        async def train_unit(self, _action):
            return True

    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    executor = SC2RuntimeExecutor(bot=WorkflowBot(), direct_lifecycle=lifecycle)
    released: list[str] = []
    session = _session(lifecycle=lifecycle, executor=executor, callback=lambda lease: released.append(lease.command_id))
    lifecycle.dispatch(
        command_id="session-parent",
        issued_at_frame=0,
        ttl_seconds=5,
        completion_conditions=("building_completed",),
    )
    child = {
        "actions": [{"action_type": "train_unit", "subject": "Marine", "count": 1}],
        "audit": {"completion_conditions": ["order_issued"]},
    }
    executor.register_dependent_plans(
        "session-parent",
        [{"resume_on": "building_completed", "plan": child}],
    )

    session.tick_direct_commands(
        1,
        {"session-parent": {"building_completed": True}},
    )
    assert released == []
    assert asyncio.run(executor.drain_completed_workflows(current_frame=1))
    session.tick_direct_commands(2, {"session-parent:step:0": {"order_issued": True}})
    assert released == ["session-parent"]
