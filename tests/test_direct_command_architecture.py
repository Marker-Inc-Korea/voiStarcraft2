from __future__ import annotations

import asyncio
import time

from starcraft_commander.direct_command_lifecycle import (
    DirectCommandLifecycle,
    DirectCommandState,
)
from starcraft_commander.direct_command_registry import (
    DirectCommandRegistry,
    SquadDefinition,
    TargetPin,
)
from starcraft_commander.mcp_server import VoiStarcraftMCPServer
from starcraft_commander.micromachine_live_session import (
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
)
from starcraft_commander.micromachine_runtime import MicroMachineInMemoryBlackboard
from starcraft_commander.policy_modulation import PolicyModulationVector
from starcraft_commander.unified_command_router import (
    ToolSpec,
    CommanderToolRegistry,
    create_command_tool_registry,
    route_policy_vector,
    SC2_CAPABILITY_CATALOG,
)
from starcraft_commander.policy_modulation import PolicyModulationVector


def _vector(payload: dict[str, object]) -> PolicyModulationVector:
    return PolicyModulationVector.from_mapping(payload)


def test_non_macro_route_is_direct_only() -> None:
    route = route_policy_vector(
        _vector(
            {
                "goal": "attack",
                "command_layer": "operation",
                "operations": [
                    {
                        "operation_id": "attack-1",
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "location_intent": "enemy_natural",
                        },
                    }
                ],
            }
        )
    )
    assert [call.name for call in route.tool_calls] == ["sc2.direct.execute"]


def test_legacy_micromachine_tactical_tools_are_opt_in_only() -> None:
    default_names = {tool.name for tool in create_command_tool_registry().list_tools()}
    assert not default_names.intersection(
        {"micromachine.operation", "micromachine.ability", "micromachine.emergency"}
    )
    compatibility_names = {
        tool.name
        for tool in create_command_tool_registry(include_legacy_tools=True).list_tools()
    }
    assert {
        "micromachine.operation",
        "micromachine.ability",
        "micromachine.emergency",
    } <= compatibility_names


def test_emergency_lowers_stop_hold_and_retreat_actions() -> None:
    route = route_policy_vector(
        _vector(
            {
                "goal": "retreat",
                "override_level": "emergency",
                "command_layer": "emergency",
                "ttl_seconds": 45,
                "emergency": {
                    "cancel_attacks": True,
                    "hold_position": True,
                    "force_retreat": True,
                },
            }
        )
    )
    assert route.direct_plan is not None
    assert [action.action_type.value for action in route.direct_plan.actions] == [
        "stop_group",
        "hold_position",
        "move_group",
    ]


def test_mcp_capability_catalog_and_direct_tools_are_complete() -> None:
    registry = create_command_tool_registry()
    names = {tool.name for tool in registry.list_tools()}
    assert {
        "sc2.direct.capabilities",
        "sc2.direct.registry",
        "sc2.direct.move_camera",
        "sc2.direct.stop_group",
        "sc2.direct.hold_position",
        "sc2.direct.retreat",
        "sc2.direct.lifecycle",
    } <= names

    async def exercise() -> dict[str, object]:
        result = await VoiStarcraftMCPServer(registry).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "voiStarcraft2/capabilities/list"}
        )
        assert result is not None
        return result

    result = asyncio.run(exercise())
    assert result["result"]["capabilities"]
    catalog_names = {item.name for item in SC2_CAPABILITY_CATALOG}
    assert catalog_names <= names


def test_call_many_async_runs_independent_tools_in_parallel() -> None:
    started: list[str] = []

    async def handler(arguments):
        started.append(str(arguments["name"]))
        await asyncio.sleep(0.02)
        return {"ok": True, "name": arguments["name"]}

    registry = CommanderToolRegistry(
        [
            ToolSpec("a", "a", {"type": "object"}, handler),
            ToolSpec("b", "b", {"type": "object"}, handler),
        ]
    )

    async def exercise():
        began = time.perf_counter()
        results = await registry.call_many_async(
            [{"name": "a", "arguments": {"name": "a"}}, {"name": "b", "arguments": {"name": "b"}}]
        )
        return time.perf_counter() - began, results

    duration, results = asyncio.run(exercise())
    assert duration < 0.035
    assert [result.result["name"] for result in results] == ["a", "b"]
    assert started == ["a", "b"]


def test_direct_lifecycle_releases_on_completion_expiry_and_cancel() -> None:
    lifecycle = DirectCommandLifecycle(game_loops_per_second=1)
    active = lifecycle.dispatch(
        command_id="attack",
        issued_at_frame=10,
        ttl_seconds=5,
        completion_conditions=("target_reached",),
    )
    assert active.control_owned
    completed = lifecycle.observe("attack", frame=11, evidence={"target_reached": True})
    assert completed.state is DirectCommandState.COMPLETED
    assert not completed.control_owned

    expired = lifecycle.dispatch(command_id="scout", issued_at_frame=2, ttl_seconds=3)
    assert lifecycle.observe("scout", frame=5).state is DirectCommandState.EXPIRED
    cancelled = lifecycle.dispatch(command_id="panic", issued_at_frame=1, ttl_seconds=3)
    assert lifecycle.cancel("panic").state is DirectCommandState.CANCELLED
    assert expired.expires_at_frame == 5
    assert cancelled.control_owned


def test_named_squad_and_target_pin_registry_rejects_unknown_squads() -> None:
    registry = DirectCommandRegistry(
        squads=(SquadDefinition("1분대", "4 MARINE"),),
        target_pins=(TargetPin("앞마당 입구", "enemy_natural_ramp"),),
    )
    assert registry.resolve_squad("1분대").unit_query == "4 MARINE"
    assert registry.resolve_target("앞마당 입구") == "enemy_natural_ramp"
    try:
        registry.resolve_squad("없는 분대")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown squads must not resolve to arbitrary units")


class _RecordingExecutor:
    bot = object()

    async def execute(self, plan):
        from starcraft_commander.contracts import SC2PlanExecutionResult

        return SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.actions,
            applied_actions=plan.actions,
            audit={"evidence": "test-runtime"},
        )


def test_live_session_keeps_direct_lease_for_observation_and_cancel() -> None:
    session = MicroMachineLiveTextSession(
        MicroMachineInMemoryBlackboard(),
        StaticJsonPolicyModulationProvider(
            {
                "goal": "scout",
                "command_layer": "operation",
                "operations": [
                    {
                        "operation_id": "scout-1",
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "location_intent": "enemy_natural",
                        },
                    }
                ],
            }
        ),
        direct_executor=_RecordingExecutor(),
    )
    result = session.submit_text("정찰 분대를 적 앞마당으로 보내", current_frame=10, update_id="scout-1")
    assert result.ok is True
    assert result.command_queue["direct_control_owner"] == "direct_sc2"
    assert session.cancel_direct_command("scout-1")["control_owned"] is False


def test_direct_executor_attaches_active_lease_and_runtime_failure_releases() -> None:
    registry = create_command_tool_registry(direct_executor=_RecordingExecutor())
    plan = route_policy_vector(
        _vector(
            {
                "goal": "attack",
                "command_layer": "operation",
                "operations": [
                    {
                        "operation_id": "attack-lease",
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "location_intent": "enemy_natural",
                        },
                    }
                ],
            }
        ),
        update_id="attack-lease",
        current_frame=12,
    )
    result = registry.call("sc2.direct.execute", plan.tool_calls[0].arguments)
    assert result.ok is True
    lease = result.result["result"]["audit"]["direct_command_lifecycle"]
    assert lease["state"] == "active"
    assert lease["control_owned"] is True

    class _BrokenExecutor:
        bot = object()

        async def execute(self, _plan):
            raise RuntimeError("socket closed")

    broken = create_command_tool_registry(direct_executor=_BrokenExecutor())
    failed = broken.call("sc2.direct.execute", plan.tool_calls[0].arguments)
    assert failed.ok is False
    assert failed.result["lifecycle"]["state"] == "failed"
    assert failed.result["lifecycle"]["control_owned"] is False
