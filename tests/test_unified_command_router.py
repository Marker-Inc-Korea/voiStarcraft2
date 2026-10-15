"""Tests for the unified macro/micro command and MCP tool boundary."""

from __future__ import annotations

import asyncio

from starcraft_commander.contracts import (
    SC2ActionType,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.mcp_server import VoiStarcraftMCPServer
from starcraft_commander.micromachine_live_session import (
    KeywordPolicyModulationProvider,
    MicroMachineLiveTextSession,
)
from starcraft_commander.micromachine_runtime import MicroMachineInMemoryBlackboard
from starcraft_commander.map_resolver import MapPoint, SC2MapResolver
from starcraft_commander.policy_modulation import PolicyModulationVector
from starcraft_commander.policy_modulation_provider import (
    compile_policy_modulation_provider_output,
)
from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
from starcraft_commander.sc2_executor import SC2RuntimeExecutor
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


def test_mcp_query_game_is_discoverable_and_calls_read_only_action() -> None:
    """Keep the query_game MCP discovery and call boundary wired together."""

    class _QueryGameExecutor:
        bot = object()

        def __init__(self) -> None:
            self.seen_plan: SC2ExecutionPlan | None = None

        async def execute(self, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
            self.seen_plan = plan
            return SC2PlanExecutionResult(
                plan=plan,
                attempted_actions=plan.actions,
                applied_actions=plan.actions,
                audit={"evidence": "query-game-test"},
            )

    executor = _QueryGameExecutor()
    server = VoiStarcraftMCPServer(
        create_command_tool_registry(direct_executor=executor)
    )

    async def exercise() -> tuple[dict[str, object], dict[str, object]]:
        listed = await server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        called = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "sc2.direct.query_game",
                    "arguments": {
                        "subject": "TERRAN_MARINE",
                        "metadata": {
                            "query": "can_afford",
                            "subject_kind": "unit",
                        },
                    },
                },
            }
        )
        assert listed is not None
        assert called is not None
        return listed, called

    listed, called = asyncio.run(exercise())
    query_tool = next(
        item
        for item in listed["result"]["tools"]  # type: ignore[index]
        if item["name"] == "sc2.direct.query_game"
    )
    query_schema = query_tool["inputSchema"]
    query_enum = query_schema["properties"]["metadata"]["properties"]["query"]["enum"]
    assert "can_afford" in query_enum
    assert "already_pending_upgrade" in query_enum

    assert called["result"]["isError"] is False  # type: ignore[index]
    structured = called["result"]["structuredContent"]  # type: ignore[index]
    assert structured["name"] == "sc2.direct.query_game"
    assert structured["ok"] is True
    action = executor.seen_plan.actions[0]  # type: ignore[union-attr]
    assert action.action_type is SC2ActionType.QUERY_GAME
    assert action.subject == "TERRAN_MARINE"
    assert action.metadata["query"] == "can_afford"
    assert "direct_command_lifecycle" not in structured["result"]["result"]["audit"]


def test_mcp_query_map_discovery_call_and_fail_closed_targets() -> None:
    """Exercise the eight-query map MCP surface through a live adapter seam."""

    class _MapBot:
        def get_next_expansion(self):
            return MapPoint(42.5, 17.25)

        def get_terrain_height(self, point):
            return point.x + point.y

        def get_terrain_z_height(self, point):
            return point.x - point.y

        def has_creep(self, point):
            return point.x >= 10.0

        def in_map_bounds(self, point):
            return point.x < 100.0 and point.y < 100.0

        def in_pathing_grid(self, point):
            return point.x >= 10.0 and point.y >= 10.0

        def in_placement_grid(self, point):
            return point.x >= 10.0 and point.y >= 10.0

        def is_visible(self, point):
            return point.x == 10.0 and point.y == 10.0

    adapter = PythonSC2BotAdapter(
        bot=_MapBot(),
        map_resolver=SC2MapResolver(
            positions={"self_main": MapPoint(10.0, 10.0)}
        ),
    )
    server = VoiStarcraftMCPServer(
        create_command_tool_registry(direct_executor=SC2RuntimeExecutor(bot=adapter))
    )

    async def exercise() -> tuple[dict[str, object], list[dict[str, object]]]:
        listed = await server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert listed is not None

        async def call(arguments: dict[str, object]) -> dict[str, object]:
            response = await server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "sc2.direct.query_map",
                        "arguments": arguments,
                    },
                }
            )
            assert response is not None
            return response

        expansion = await call({"metadata": {"query": "get_next_expansion"}})
        terrain = await call(
            {
                "subject": "map",
                "target": "self_main",
                "metadata": {"query": "get_terrain_height"},
            }
        )
        pathing = await call(
            {
                "subject": "map",
                "target": "self_main",
                "metadata": {"query": "in_pathing_grid"},
            }
        )
        unsupported = await call(
            {
                "subject": "map",
                "target": "self_main",
                "metadata": {"query": "raw_python_method"},
            }
        )
        missing_target = await call(
            {"subject": "map", "metadata": {"query": "in_pathing_grid"}}
        )
        raw_target = await call(
            {
                "subject": "map",
                "target": "1234",
                "metadata": {"query": "in_pathing_grid"},
            }
        )
        return listed, [expansion, terrain, pathing, unsupported, missing_target, raw_target]

    listed, responses = asyncio.run(exercise())
    query_tool = next(
        item
        for item in listed["result"]["tools"]  # type: ignore[index]
        if item["name"] == "sc2.direct.query_map"
    )
    query_enum = query_tool["inputSchema"]["properties"]["metadata"]["properties"]["query"]["enum"]
    assert query_enum == [
        "get_next_expansion",
        "get_terrain_height",
        "get_terrain_z_height",
        "has_creep",
        "in_map_bounds",
        "in_pathing_grid",
        "in_placement_grid",
        "is_visible",
    ]
    assert query_tool["inputSchema"]["required"] == ["metadata"]

    expansion, terrain, pathing, unsupported, missing_target, raw_target = responses
    for response in (expansion, terrain, pathing):
        assert response["result"]["isError"] is False  # type: ignore[index]
        structured = response["result"]["structuredContent"]  # type: ignore[index]
        assert structured["ok"] is True
        assert "direct_command_lifecycle" not in structured["result"]["result"]["audit"]
    expansion_audit = expansion["result"]["structuredContent"]["result"]["result"]["audit"]  # type: ignore[index]
    terrain_audit = terrain["result"]["structuredContent"]["result"]["result"]["audit"]  # type: ignore[index]
    pathing_audit = pathing["result"]["structuredContent"]["result"]["result"]["audit"]  # type: ignore[index]
    assert expansion_audit["observations"]["0"]["query"] == "get_next_expansion"
    assert terrain_audit["observations"]["0"]["target"] == "self_main"
    assert pathing_audit["observations"]["0"]["value"] is True

    for response, error_name in (
        (unsupported, "unsupported_query"),
        (missing_target, "missing_target"),
        (raw_target, "unresolvable_target"),
    ):
        assert response["result"]["isError"] is True  # type: ignore[index]
        structured = response["result"]["structuredContent"]  # type: ignore[index]
        assert structured["ok"] is False
        audit = structured["result"]["result"]["audit"]
        assert "direct_command_lifecycle" not in audit
        observation = audit["observations"]["0"]
        assert observation["error"] == error_name


def test_query_game_upgrade_subject_is_preserved_for_upgrade_id_resolution() -> None:
    vector = compile_policy_modulation_provider_output(
        {
            "goal": "check upgrade progress",
            "command_layer": "operation",
            "tactical_task": {
                "task_type": "query_game",
                "query": "already_pending_upgrade",
                "subject_kind": "upgrade",
                "production_targets": ["TERRAN_INFANTRYWEAPONSLEVEL1"],
            },
        }
    ).vector
    assert vector is not None
    route = route_policy_vector(vector, update_id="query-upgrade", current_frame=1)
    assert route.direct_plan is not None
    action = route.direct_plan.actions[0]
    assert action.action_type is SC2ActionType.QUERY_GAME
    assert action.subject == "TERRAN_INFANTRYWEAPONSLEVEL1"
    assert action.metadata["subject_kind"] == "upgrade"


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
