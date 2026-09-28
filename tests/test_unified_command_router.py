"""Tests for the unified macro/micro command and MCP tool boundary."""

from __future__ import annotations

import asyncio

from starcraft_commander.contracts import (
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.mcp_server import VoiStarcraftMCPServer
from starcraft_commander.micromachine_live_session import (
    KeywordPolicyModulationProvider,
    MicroMachineLiveTextSession,
)
from starcraft_commander.micromachine_runtime import MicroMachineInMemoryBlackboard
from starcraft_commander.policy_modulation import PolicyModulationVector
from starcraft_commander.unified_command_router import (
    ToolCall,
    create_command_tool_registry,
    route_and_execute,
    route_policy_vector,
)


def vector(payload: dict[str, object]) -> PolicyModulationVector:
    return PolicyModulationVector.from_mapping(payload)


def test_macro_routes_only_to_micromachine_policy() -> None:
    route = route_policy_vector(
        vector(
            {
                "goal": "marine production",
                "command_layer": "macro",
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_MARINE"],
                },
            }
        )
    )

    assert route.layer.value == "macro"
    assert route.micro_kind is None
    assert [call.name for call in route.tool_calls] == ["micromachine.policy"]
    assert route.direct_plan is None


def test_operation_routes_to_direct_sc2_only() -> None:
    route = route_policy_vector(
        vector(
            {
                "goal": "squad attack",
                "command_layer": "operation",
                "operations": [
                    {
                        "operation_id": "op-1",
                        "goal": "attack enemy natural",
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "location_intent": "enemy_natural",
                            "min_units": 4,
                        },
                        "scope": {
                            "army_group": "main",
                            "unit_classes": ["TERRAN_MARINE"],
                            "min_units": 4,
                        },
                        "composition_requirements": [
                            {"unit_type": "TERRAN_MARINE", "count": 4}
                        ],
                    }
                ],
            }
        ),
        update_id="op-1",
        current_frame=120,
    )

    assert route.micro_kind.value == "operation"
    assert [call.name for call in route.tool_calls] == ["sc2.direct.execute"]
    assert route.direct_plan is not None
    assert route.direct_plan.actions[0].action_type is SC2ActionType.ATTACK_MOVE


def test_ability_routes_to_execute_ability_and_emergency_routes_to_retreat() -> None:
    ability = route_policy_vector(
        vector(
            {
                "goal": "stim",
                "command_layer": "micro",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "marine_stimpack",
                    "unit_classes": ["TERRAN_MARINE"],
                    "location_intent": "enemy_natural",
                },
            }
        )
    )
    emergency = route_policy_vector(
        vector(
            {
                "goal": "retreat",
                "command_layer": "emergency",
                "override_level": "emergency",
                "ttl_seconds": 45,
                "emergency": {"force_retreat": True},
            }
        )
    )

    assert ability.micro_kind.value == "micro"
    assert ability.direct_plan.actions[-1].action_type is SC2ActionType.EXECUTE_ABILITY
    assert ability.direct_plan.actions[-1].metadata["ability"] == "marine_stimpack"
    assert emergency.micro_kind.value == "emergency"
    assert emergency.direct_plan.actions[-1].action_type is SC2ActionType.MOVE_GROUP
    assert emergency.direct_plan.actions[-1].metadata["emergency"] is True


def test_registry_supports_ordered_call_many_and_runtime_not_attached() -> None:
    registry = create_command_tool_registry()
    results = registry.call_many(
        [
            ToolCall(
                "sc2.direct.move_group",
                {"subject": "MARINE", "target": "enemy_natural", "count": 4},
            ),
            ToolCall("sc2.direct.observe"),
        ]
    )

    assert [result.name for result in results] == [
        "sc2.direct.move_group",
        "sc2.direct.observe",
    ]
    assert all(result.ok is False for result in results)
    assert all(result.result.get("status") == "runtime_not_attached" for result in results)


def test_mcp_lists_and_calls_composable_tools() -> None:
    registry = create_command_tool_registry()
    server = VoiStarcraftMCPServer(registry)

    async def exercise() -> tuple[dict[str, object], dict[str, object]]:
        listed = await server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        called = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "voiStarcraft2/tools/call_many",
                "params": {
                    "calls": [
                        {
                            "name": "sc2.direct.move_group",
                            "arguments": {
                                "subject": "MARINE",
                                "target": "enemy_natural",
                                "count": 2,
                            },
                        },
                        {"name": "sc2.direct.observe", "arguments": {}},
                    ]
                },
            }
        )
        assert listed is not None
        assert called is not None
        return listed, called

    listed, called = asyncio.run(exercise())
    tool_names = {
        item["name"] for item in listed["result"]["tools"]  # type: ignore[index]
    }
    assert "micromachine.operation" not in tool_names
    assert "sc2.direct.execute_ability" in tool_names
    assert called["result"]["structuredContent"]["results"][0]["ok"] is False  # type: ignore[index]


def test_live_session_exposes_unified_route_without_double_publish() -> None:
    backend = MicroMachineInMemoryBlackboard()
    session = MicroMachineLiveTextSession(
        backend,
        KeywordPolicyModulationProvider(),
    )

    result = session.submit_text(
        "적 본진에 전술핵을 사용해",
        current_frame=100,
        update_id="unified-live-1",
    )

    assert result.ok is False
    assert result.unified_route is not None
    assert result.unified_route["micro_kind"] == "micro"
    assert [item["name"] for item in result.tool_results] == ["sc2.direct.execute"]
    assert result.tool_results[0]["result"]["status"] == "runtime_not_attached"
    assert len(backend.update_archive) == 0


class RecordingDirectExecutor:
    bot = object()

    async def execute(self, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        return SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.actions,
            applied_actions=plan.actions,
            audit={"evidence": "recording-runtime"},
        )


def test_route_and_execute_reports_direct_execution_evidence() -> None:
    route = route_and_execute(
        vector(
            {
                "goal": "move squad",
                "command_layer": "operation",
                "operations": [
                    {
                        "operation_id": "op-2",
                        "goal": "scout",
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "location_intent": "enemy_natural",
                        },
                    }
                ],
            }
        ),
        create_command_tool_registry(direct_executor=RecordingDirectExecutor()),
        update_id="op-2",
    )

    direct = route["tool_results"][0]
    assert direct["ok"] is True
    assert direct["result"]["status"] == "executed"
    assert direct["result"]["result"]["audit"]["evidence"] == "recording-runtime"
