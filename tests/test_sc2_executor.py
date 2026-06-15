import asyncio
import json
import subprocess
import sys
import unittest

import starcraft_commander as package_exports
from starcraft_commander.contracts import SC2ActionReport
from starcraft_commander.sc2_executor import (
    DEFAULT_SC2_ACTION_PLANNER,
    SC2_ACTION_TYPES,
    SC2_INTENT_ACTION_TYPE_MAP,
    SC2_SEMANTIC_TARGET_NAMES,
    SC2_TARGET_ALIASES,
    SC2ActionPlanner,
    SC2ActionPlannerInterface,
    SC2ActionType,
    SC2CommandAction,
    SC2CommandPlan,
    SC2ExecutorBoundaryInterface,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
    SC2RuntimeExecutor,
    SC2RuntimeExecutorInterface,
    build_sc2_execution_plan,
)
from toycraft_commander.map import MAP_LOCATION_NAMES
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    GatherResourceIntent,
    HarassIntent,
    MoveCameraIntent,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)


CANONICAL_SC2_PLANNER_PAYLOADS = (
    GatherResourceIntent(resource="minerals", worker_count=4, base="main"),
    BuildStructureIntent(structure="Supply Depot", location="main ramp"),
    TrainWorkerIntent(count=2),
    TrainArmyIntent(unit_type="Marine", count=3),
    ScoutIntent(target="enemy main", unit_group="worker_scout"),
    SummarizeStateIntent(),
    DefendIntent(location="main ramp", unit_group="marines"),
    RepairIntent(target="front bunker", worker_count=2),
    ExpandIntent(location="natural expansion"),
    HarassIntent(target="enemy mineral line", unit_group="marines"),
)

LEGACY_INTERNAL_ACTION_TYPE_NAMES = frozenset(
    {
        "advance_time",
        "apply_damage",
        "assign_builder",
        "build",
        "execute_build_structure",
        "execute_gather_resource",
        "execute_harass",
        "execute_train_army",
        "execute_train_worker",
        "gather_resource",
        "move_units",
        "queue_construction",
        "queue_production",
        "reserve_supply",
        "spend_resources",
        "train",
    }
)


class StarCraftCommanderPackageSurfaceTest(unittest.TestCase):
    def test_package_exports_real_sc2_executor_boundary(self) -> None:
        self.assertIs(DEFAULT_SC2_ACTION_PLANNER, package_exports.DEFAULT_SC2_ACTION_PLANNER)
        self.assertIs(SC2_ACTION_TYPES, package_exports.SC2_ACTION_TYPES)
        self.assertIs(
            SC2_INTENT_ACTION_TYPE_MAP,
            package_exports.SC2_INTENT_ACTION_TYPE_MAP,
        )
        self.assertIs(SC2ActionPlanner, package_exports.SC2ActionPlanner)
        self.assertIs(SC2ActionPlannerInterface, package_exports.SC2ActionPlannerInterface)
        self.assertIs(SC2ActionType, package_exports.SC2ActionType)
        self.assertIs(SC2CommandAction, package_exports.SC2CommandAction)
        self.assertIs(SC2CommandPlan, package_exports.SC2CommandPlan)
        self.assertIs(
            SC2ExecutorBoundaryInterface,
            package_exports.SC2ExecutorBoundaryInterface,
        )
        self.assertIs(SC2ExecutionError, package_exports.SC2ExecutionError)
        self.assertIs(SC2ExecutionPlan, package_exports.SC2ExecutionPlan)
        self.assertIs(SC2PlanExecutionResult, package_exports.SC2PlanExecutionResult)
        self.assertIs(SC2RuntimeExecutor, package_exports.SC2RuntimeExecutor)
        self.assertIs(SC2RuntimeExecutorInterface, package_exports.SC2RuntimeExecutorInterface)
        self.assertIs(build_sc2_execution_plan, package_exports.build_sc2_execution_plan)

    def test_package_exports_stable_public_sc2_action_type_set(self) -> None:
        self.assertEqual(
            frozenset(
                {
                    "assign_workers",
                    "train_unit",
                    "build_structure",
                    "move_group",
                    "attack_move",
                    "repair",
                    "observe",
                    "move_camera",
                },
            ),
            SC2_ACTION_TYPES,
        )
        self.assertEqual(
            SC2_ACTION_TYPES,
            frozenset(action_type.value for action_type in SC2ActionType),
        )

    def test_intent_action_type_mapping_uses_only_stable_public_names(self) -> None:
        self.assertEqual(
            {
                "GATHER_RESOURCE": ("assign_workers",),
                "BUILD_STRUCTURE": ("build_structure",),
                "TRAIN_WORKER": ("train_unit",),
                "TRAIN_ARMY": ("train_unit",),
                "SCOUT": ("move_group",),
                "SUMMARIZE_STATE": ("observe",),
                "DEFEND": ("attack_move",),
                "REPAIR": ("repair",),
                "EXPAND": ("build_structure",),
                "HARASS": ("attack_move",),
                "MOVE_CAMERA": ("move_camera",),
            },
            SC2_INTENT_ACTION_TYPE_MAP,
        )
        for intent_name, action_types in SC2_INTENT_ACTION_TYPE_MAP.items():
            with self.subTest(intent_name=intent_name):
                self.assertTrue(action_types)
                self.assertLessEqual(set(action_types), SC2_ACTION_TYPES)

    def test_contract_package_import_does_not_load_toycraft_or_sc2_runtime(self) -> None:
        script = (
            "import json, sys; "
            "import starcraft_commander; "
            "print(json.dumps({"
            "'toycraft_loaded': 'toycraft_commander' in sys.modules, "
            "'contracts_loaded': 'starcraft_commander.contracts' in sys.modules, "
            "'executor_loaded': 'starcraft_commander.sc2_executor' in sys.modules"
            "}, sort_keys=True))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)

        self.assertFalse(payload["toycraft_loaded"])
        self.assertTrue(payload["contracts_loaded"])
        self.assertFalse(payload["executor_loaded"])

    def test_contract_schema_uses_ordered_semantic_json_ready_fields(self) -> None:
        plan = SC2CommandPlan(
            intent_name="TRAIN_ARMY",
            priority="high",
            constraints=("barracks_ready",),
            ordered_actions=(
                SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=2),
                SC2CommandAction(
                    "attack_move",
                    subject="marines",
                    target="enemy_natural",
                ),
            ),
            audit={"source": "unit-test"},
        )

        payload = plan.to_dict()

        self.assertEqual("TRAIN_ARMY", payload["intent_name"])
        self.assertEqual(75, payload["priority"])
        self.assertEqual("high", payload["priority_label"])
        self.assertEqual(payload["ordered_actions"], payload["actions"])
        self.assertEqual(
            ["train_unit", "attack_move"],
            [action["action_type"] for action in payload["ordered_actions"]],
        )
        self.assertEqual(["barracks_ready"], payload["ordered_actions"][0]["constraints"])
        json.dumps(payload)

    def test_command_action_rejects_unknown_action_type_with_clear_error(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "SC2 command action action_type must be one of: .*Unknown action_type: 'warp_in_unit'",
        ):
            SC2CommandAction("warp_in_unit", subject="ZEALOT")

    def test_execution_error_rejects_unknown_action_type_with_clear_error(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "SC2 execution error action_type must be one of: .*Unknown action_type: 'mouse_click'",
        ):
            SC2ExecutionError(message="bad action", action_type="mouse_click")

    def test_result_contract_exposes_attempted_applied_skipped_errors_and_audit(self) -> None:
        action = SC2CommandAction(SC2ActionType.OBSERVE, subject="visible_state", count=0)
        plan = SC2ExecutionPlan(intent="SUMMARIZE_STATE", actions=(action,))
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted=(action,),
            skipped=(action,),
            errors=(
                SC2ExecutionError(
                    message="observe unavailable",
                    action_type=SC2ActionType.OBSERVE,
                    action_index=0,
                    exception_type="RuntimeError",
                ),
            ),
            audit={"runtime_adapter": "FakeBot"},
        )

        payload = result.to_dict()

        self.assertFalse(result.success)
        self.assertEqual(result.attempted_actions, result.attempted)
        self.assertEqual(result.applied_actions, result.applied)
        self.assertEqual(result.skipped_actions, result.skipped)
        self.assertEqual(payload["attempted"], payload["attempted_actions"])
        self.assertEqual(payload["skipped"], payload["skipped_actions"])
        self.assertEqual("observe unavailable", payload["errors"][0]["message"])
        self.assertEqual({"runtime_adapter": "FakeBot"}, payload["audit"])
        json.dumps(payload)

    def test_empty_result_is_safe_failure_not_success(self) -> None:
        plan = SC2ExecutionPlan(intent_name="EMPTY", ordered_actions=())
        result = SC2PlanExecutionResult(plan=plan)

        self.assertFalse(result.success)
        self.assertEqual([], result.to_dict()["attempted"])

    def test_plan_rejects_unknown_priority_labels_listing_valid_labels(self) -> None:
        for unknown_priority in ("blazing", "CRITICAL", "p0"):
            with self.subTest(priority=unknown_priority):
                with self.assertRaisesRegex(
                    ValueError,
                    "SC2 execution plan priority must be one of: "
                    f"low, normal, high, urgent. Unknown priority: '{unknown_priority}'",
                ):
                    SC2ExecutionPlan(
                        intent_name="TRAIN_WORKER",
                        ordered_actions=(),
                        priority=unknown_priority,
                    )

    def test_plan_accepts_every_known_priority_label(self) -> None:
        for label, value in (("low", 25), ("normal", 50), ("high", 75), ("urgent", 100)):
            with self.subTest(priority=label):
                plan = SC2ExecutionPlan(
                    intent_name="TRAIN_WORKER",
                    ordered_actions=(),
                    priority=label,
                )

                self.assertEqual(label, plan.priority)
                self.assertEqual(value, plan.priority_value)

    def test_plan_keeps_integer_priorities_as_custom_label(self) -> None:
        plan = SC2ExecutionPlan(intent_name="TRAIN_WORKER", ordered_actions=(), priority=60)

        self.assertEqual("custom", plan.priority)
        self.assertEqual(60, plan.priority_value)


class SC2ActionPlannerTest(unittest.TestCase):
    def test_planner_implements_interface(self) -> None:
        self.assertIsInstance(DEFAULT_SC2_ACTION_PLANNER, SC2ActionPlannerInterface)

    def test_all_canonical_intents_emit_stable_public_action_type_names(self) -> None:
        for payload in CANONICAL_SC2_PLANNER_PAYLOADS:
            with self.subTest(intent_name=payload.intent):
                plan = build_sc2_execution_plan(payload)
                serialized_action_types = tuple(
                    action["action_type"]
                    for action in plan.to_dict()["ordered_actions"]
                )

                self.assertEqual(
                    SC2_INTENT_ACTION_TYPE_MAP[payload.intent],
                    serialized_action_types,
                )
                self.assertLessEqual(set(serialized_action_types), SC2_ACTION_TYPES)

    def test_generated_plans_reject_legacy_or_internal_action_type_names(self) -> None:
        for payload in CANONICAL_SC2_PLANNER_PAYLOADS:
            with self.subTest(intent_name=payload.intent):
                plan = build_sc2_execution_plan(payload)
                action_types = tuple(
                    action["action_type"]
                    for action in plan.to_dict()["ordered_actions"]
                )

                self.assertFalse(
                    LEGACY_INTERNAL_ACTION_TYPE_NAMES.intersection(action_types),
                    f"{payload.intent} emitted legacy/internal action types: {action_types}",
                )

    def test_train_worker_maps_to_command_center_scv_api_plan(self) -> None:
        plan = build_sc2_execution_plan(TrainWorkerIntent(count=2))

        self.assertEqual("TRAIN_WORKER", plan.intent)
        self.assertTrue(plan.requires_live_sc2)
        self.assertEqual(1, len(plan.actions))
        action = plan.actions[0]
        self.assertEqual(SC2ActionType.TRAIN_UNIT, action.action_type)
        self.assertEqual("SCV", action.subject)
        self.assertEqual(2, action.count)
        self.assertEqual("COMMANDCENTER", action.metadata["producer"])

    def test_build_structure_maps_to_sc2_structure_and_location_alias(self) -> None:
        plan = build_sc2_execution_plan(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
        )

        self.assertEqual("BUILD_STRUCTURE", plan.intent)
        self.assertEqual(SC2ActionType.BUILD_STRUCTURE, plan.actions[0].action_type)
        self.assertEqual("SUPPLYDEPOT", plan.actions[0].subject)
        self.assertEqual("self_ramp", plan.actions[0].target)

    def test_build_refinery_accepts_llm_natural_gas_locations(self) -> None:
        cases = (
            "본진 베스핀 가스",
            "베스핀 가스 geyser",
            "본진 가스 geyser",
            "nearest available geyser at main base",
            "본진 가스 간헐천",
            "가스 간헐천",
            "가스",
        )
        for location in cases:
            with self.subTest(location=location):
                plan = build_sc2_execution_plan(
                    BuildStructureIntent(structure="Refinery", location=location),
                )
                self.assertEqual("REFINERY", plan.actions[0].subject)
                self.assertEqual("self_geyser", plan.actions[0].target)

    def test_build_structure_accepts_llm_freeform_self_locations(self) -> None:
        cases = (
            ("main base near ramp", "self_ramp"),
            ("본진 근방", "self_main"),
            ("본진 건설 가능한 위치", "self_main"),
            ("본진 아래", "self_main"),
            ("아군 앞마당", "self_natural"),
        )
        for location, expected_target in cases:
            with self.subTest(location=location):
                plan = build_sc2_execution_plan(
                    BuildStructureIntent(structure="Barracks", location=location),
                )
                self.assertEqual("BARRACKS", plan.actions[0].subject)
                self.assertEqual(expected_target, plan.actions[0].target)

    def test_build_structure_preserves_relative_location_policy_metadata(self) -> None:
        plan = build_sc2_execution_plan(
            BuildStructureIntent(
                structure="Supply Depot",
                location="main ramp",
                placement_policy={
                    "anchor": "mineral line",
                    "anchor_target": "self_mineral_line",
                    "spatial_relation": "away_from",
                },
            ),
        )

        self.assertEqual(
            {
                "anchor": "mineral line",
                "anchor_target": "self_mineral_line",
                "spatial_relation": "away_from",
            },
            plan.actions[0].metadata["placement_policy"],
        )

    def test_train_army_maps_marine_to_barracks(self) -> None:
        plan = build_sc2_execution_plan(TrainArmyIntent(unit_type="Marine", count=3))

        self.assertEqual(SC2ActionType.TRAIN_UNIT, plan.actions[0].action_type)
        self.assertEqual("MARINE", plan.actions[0].subject)
        self.assertEqual("BARRACKS", plan.actions[0].metadata["producer"])

    def test_gather_resource_maps_to_worker_assignment(self) -> None:
        plan = build_sc2_execution_plan(
            GatherResourceIntent(resource="minerals", worker_count=4, base="main"),
        )

        self.assertEqual(SC2ActionType.ASSIGN_WORKERS, plan.actions[0].action_type)
        self.assertEqual("SCV", plan.actions[0].subject)
        self.assertEqual("minerals", plan.actions[0].target)
        self.assertEqual(4, plan.actions[0].count)

    def test_scout_defend_and_harass_map_to_unit_control_api_plans(self) -> None:
        scout = build_sc2_execution_plan(
            ScoutIntent(target="enemy main", unit_group="worker_scout"),
        )
        defend = build_sc2_execution_plan(
            DefendIntent(location="main ramp", unit_group="marines"),
        )
        harass = build_sc2_execution_plan(
            HarassIntent(target="enemy mineral line", unit_group="hellions"),
        )

        self.assertEqual(SC2ActionType.MOVE_GROUP, scout.actions[0].action_type)
        self.assertEqual("enemy_main", scout.actions[0].target)
        self.assertEqual(SC2ActionType.ATTACK_MOVE, defend.actions[0].action_type)
        self.assertEqual("self_ramp", defend.actions[0].target)
        self.assertEqual(SC2ActionType.ATTACK_MOVE, harass.actions[0].action_type)
        self.assertEqual("enemy_mineral_line", harass.actions[0].target)

    def test_summarize_state_is_observe_only_plan(self) -> None:
        plan = build_sc2_execution_plan(SummarizeStateIntent())

        self.assertEqual(SC2ActionType.OBSERVE, plan.actions[0].action_type)
        self.assertEqual(0, plan.actions[0].count)

    def test_mapping_payload_dicts_keeps_sc2_planner_decoupled_from_toy_runtime(self) -> None:
        plan = build_sc2_execution_plan(
            {
                "intent": "TRAIN_ARMY",
                "unit_type": "Marine",
                "count": 1,
                "priority": "high",
                "constraints": ["do_not_sacrifice_army"],
            },
        )

        self.assertEqual("high", plan.priority)
        self.assertEqual(("do_not_sacrifice_army",), plan.constraints)
        self.assertEqual("MARINE", plan.actions[0].subject)

    def test_plan_serializes_for_ui_or_api_logs(self) -> None:
        plan = build_sc2_execution_plan(TrainWorkerIntent(count=1))
        payload = plan.to_dict()

        self.assertEqual("TRAIN_WORKER", payload["intent"])
        self.assertEqual("train_unit", payload["actions"][0]["action_type"])
        self.assertIn("SC2 executor plans semantic API commands", payload["notes"][0])

    def test_target_aliases_cover_every_toycraft_canonical_map_location(self) -> None:
        self.assertLessEqual(frozenset(MAP_LOCATION_NAMES), frozenset(SC2_TARGET_ALIASES))
        self.assertIn("main base fallback", SC2_TARGET_ALIASES)
        for alias, canonical in (
            ("우리 본진", "self_main"),
            ("우리본진", "self_main"),
            ("우리 입구", "self_ramp"),
            ("우리 본진 입구", "self_ramp"),
            ("우리 가스", "self_geyser"),
            ("우리 앞마당", "self_natural"),
        ):
            with self.subTest(alias=alias):
                self.assertEqual(canonical, SC2_TARGET_ALIASES[alias])
        self.assertLessEqual(
            frozenset(SC2_TARGET_ALIASES.values()),
            SC2_SEMANTIC_TARGET_NAMES,
        )

    def test_semantic_target_name_registry_is_stable(self) -> None:
        self.assertEqual(
            frozenset(
                {
                    "self_main",
                    "self_ramp",
                    "self_choke",
                    "self_natural",
                    "self_third",
                    "self_mineral_line",
                    "self_geyser",
                    "enemy_main",
                    "enemy_ramp",
                    "enemy_choke",
                    "enemy_front",
                    "enemy_natural",
                    "enemy_third",
                    "enemy_mineral_line",
                    "scout_location",
                    "last_seen_enemy_area",
                },
            ),
            SC2_SEMANTIC_TARGET_NAMES,
        )

    def test_location_intents_reject_unknown_targets_with_alternatives(self) -> None:
        unknown_payloads = (
            BuildStructureIntent(structure="Supply Depot", location="atlantis"),
            ExpandIntent(location="atlantis"),
            ScoutIntent(target="atlantis", unit_group="worker_scout"),
            DefendIntent(location="atlantis", unit_group="marines"),
            HarassIntent(target="atlantis", unit_group="marines"),
        )

        for payload in unknown_payloads:
            with self.subTest(intent_name=payload.intent):
                with self.assertRaises(ValueError) as caught:
                    build_sc2_execution_plan(payload)

                message = str(caught.exception)
                self.assertIn("unsupported SC2 target location: 'atlantis'", message)
                self.assertIn("Supported targets:", message)
                self.assertIn("natural expansion", message)
                self.assertIn("enemy_main", message)

    def test_every_canonical_map_location_plans_to_semantic_target(self) -> None:
        for location_name in MAP_LOCATION_NAMES:
            with self.subTest(location=location_name):
                plan = build_sc2_execution_plan(
                    DefendIntent(location=location_name, unit_group="marines"),
                )

                self.assertIn(plan.actions[0].target, SC2_SEMANTIC_TARGET_NAMES)

    def test_already_semantic_targets_pass_through_unchanged(self) -> None:
        for semantic_target in sorted(SC2_SEMANTIC_TARGET_NAMES):
            with self.subTest(target=semantic_target):
                plan = build_sc2_execution_plan(
                    ScoutIntent(target=semantic_target, unit_group="worker_scout"),
                )

                self.assertEqual(semantic_target, plan.actions[0].target)

    def test_natural_language_targets_use_shared_alias_normalization(self) -> None:
        cases = (
            (
                BuildStructureIntent(
                    structure="Supply Depot",
                    location="Main Base",
                ),
                "self_main",
            ),
            (
                DefendIntent(location="우리 본 진", unit_group="marines"),
                "self_main",
            ),
            (
                ScoutIntent(target="적 앞 마당", unit_group="worker_scout"),
                "enemy_natural",
            ),
            (
                HarassIntent(target="상대 일꾼 라인", unit_group="marines"),
                "enemy_mineral_line",
            ),
            (
                MoveCameraIntent(target="본진 입 구"),
                "self_ramp",
            ),
        )

        for payload, expected_target in cases:
            with self.subTest(intent=payload.intent, expected_target=expected_target):
                plan = build_sc2_execution_plan(payload)

                self.assertEqual(expected_target, plan.actions[0].target)

    def test_repair_target_and_gather_resource_stay_verbatim(self) -> None:
        repair = build_sc2_execution_plan(
            RepairIntent(target="front bunker", worker_count=2),
        )
        gather = build_sc2_execution_plan(
            GatherResourceIntent(resource="minerals", worker_count=4, base="main"),
        )

        self.assertEqual("front bunker", repair.actions[0].target)
        self.assertEqual("minerals", gather.actions[0].target)


class SC2RuntimeExecutorTest(unittest.TestCase):
    def test_runtime_executor_implements_interface(self) -> None:
        self.assertIsInstance(SC2RuntimeExecutor(), SC2RuntimeExecutorInterface)
        self.assertIsInstance(SC2RuntimeExecutor(), SC2ExecutorBoundaryInterface)

    def test_lifecycle_execute_contract_uses_bound_bot_and_preserves_order(self) -> None:
        class FakeBot:
            def __init__(self) -> None:
                self.calls = []

            async def on_start(self):
                self.calls.append(("on_start",))

            async def train_unit(self, action):
                self.calls.append(("train_unit", action.subject, action.count))
                return True

            async def attack_move(self, action):
                self.calls.append(("attack_move", action.subject, action.target))
                return True

            async def on_end(self):
                self.calls.append(("on_end",))

        async def run_lifecycle():
            bot = FakeBot()
            executor = SC2RuntimeExecutor()
            plan = SC2ExecutionPlan(
                intent="demo",
                actions=(
                    SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=2),
                    SC2CommandAction(
                        SC2ActionType.ATTACK_MOVE,
                        subject="marines",
                        target="enemy_natural",
                    ),
                ),
            )

            await executor.start(bot)
            result = await executor.execute(plan)
            await executor.close()
            return bot, executor, result

        bot, executor, result = asyncio.run(run_lifecycle())

        self.assertFalse(executor.is_started)
        self.assertTrue(result.success)
        self.assertEqual(
            [
                ("on_start",),
                ("train_unit", "MARINE", 2),
                ("attack_move", "marines", "enemy_natural"),
                ("on_end",),
            ],
            bot.calls,
        )
        self.assertEqual(
            ["train_unit", "attack_move"],
            [action.action_type.value for action in result.attempted_actions],
        )
        json.dumps(result.to_dict())

    def test_lifecycle_execute_without_bound_bot_returns_structured_failure(self) -> None:
        plan = build_sc2_execution_plan(TrainWorkerIntent(count=1))

        result = asyncio.run(SC2RuntimeExecutor().execute(plan))

        self.assertFalse(result.success)
        self.assertEqual((), result.attempted_actions)
        self.assertEqual(plan.actions, result.skipped_actions)
        self.assertEqual("MissingRuntimeAdapter", result.errors[0].exception_type)
        self.assertEqual(None, result.to_dict()["audit"]["runtime_adapter"])
        json.dumps(result.to_dict())

    def test_runtime_executor_calls_bot_action_methods(self) -> None:
        class FakeBot:
            def __init__(self) -> None:
                self.calls = []

            async def train_unit(self, action):
                self.calls.append(("train_unit", action.subject, action.count))
                return True

            async def attack_move(self, action):
                self.calls.append(("attack_move", action.subject, action.target))
                return True

        plan = SC2ExecutionPlan(
            intent="demo",
            priority="normal",
            actions=(
                SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=2),
                SC2CommandAction(
                    SC2ActionType.ATTACK_MOVE,
                    subject="marines",
                    target="enemy_natural",
                ),
            ),
        )
        bot = FakeBot()

        result = asyncio.run(SC2RuntimeExecutor().execute_plan(bot, plan))

        self.assertTrue(result.success)
        self.assertEqual(
            [
                ("train_unit", "MARINE", 2),
                ("attack_move", "marines", "enemy_natural"),
            ],
            bot.calls,
        )
        self.assertEqual(2, len(result.applied_actions))

    def test_runtime_executor_records_structured_error_for_missing_bot_capability(self) -> None:
        class FakeBot:
            pass

        plan = build_sc2_execution_plan(TrainWorkerIntent(count=1))

        result = asyncio.run(SC2RuntimeExecutor().execute_plan(FakeBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual((), result.applied_actions)
        self.assertEqual(plan.actions, result.skipped_actions)
        self.assertEqual(1, len(result.errors))
        error = result.errors[0]
        self.assertEqual("MissingBotCapability", error.exception_type)
        self.assertEqual(SC2ActionType.TRAIN_UNIT, error.action_type)
        self.assertEqual(0, error.action_index)
        self.assertEqual({"expected_method": "train_unit"}, dict(error.metadata))
        self.assertIn("execute_commander_action", error.message)
        json.dumps(result.to_dict())

    def test_partial_capability_skips_only_unhandled_actions_with_errors(self) -> None:
        class FakeBot:
            async def train_unit(self, action):
                return True

        plan = SC2ExecutionPlan(
            intent="demo",
            actions=(
                SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=2),
                SC2CommandAction(
                    SC2ActionType.ATTACK_MOVE,
                    subject="marines",
                    target="enemy_natural",
                ),
            ),
        )

        result = asyncio.run(SC2RuntimeExecutor().execute_plan(FakeBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual((plan.actions[0],), result.applied_actions)
        self.assertEqual((plan.actions[1],), result.skipped_actions)
        self.assertEqual(1, len(result.errors))
        self.assertEqual("MissingBotCapability", result.errors[0].exception_type)
        self.assertEqual(1, result.errors[0].action_index)
        self.assertEqual(
            {"expected_method": "attack_move"},
            dict(result.errors[0].metadata),
        )

    def test_start_clears_lifecycle_errors_from_previous_cycle(self) -> None:
        class FailingStartBot:
            async def on_start(self):
                raise RuntimeError("hook boom")

            async def train_unit(self, action):
                return True

        class HealthyBot:
            async def train_unit(self, action):
                return True

        plan = SC2ExecutionPlan(
            intent="demo",
            actions=(SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE"),),
        )

        async def run_two_cycles():
            executor = SC2RuntimeExecutor()
            await executor.start(FailingStartBot())
            poisoned = await executor.execute(plan)
            await executor.start(HealthyBot())
            clean = await executor.execute(plan)
            return poisoned, clean

        poisoned, clean = asyncio.run(run_two_cycles())

        self.assertFalse(poisoned.success)
        self.assertEqual("RuntimeError", poisoned.errors[0].exception_type)
        self.assertEqual(
            {"lifecycle_hook": "on_start"},
            dict(poisoned.errors[0].metadata),
        )
        self.assertTrue(clean.success)
        self.assertEqual((), clean.errors)

    def test_observe_mapping_return_surfaces_observation_in_audit(self) -> None:
        snapshot = {"minerals": 350, "vespene": 64, "supply_used": 18}

        class ObservingBot:
            async def observe(self, action):
                return dict(snapshot)

        plan = build_sc2_execution_plan(SummarizeStateIntent())

        result = asyncio.run(SC2RuntimeExecutor().execute_plan(ObservingBot(), plan))

        self.assertTrue(result.success)
        self.assertEqual(plan.actions, result.applied_actions)
        self.assertEqual({"0": snapshot}, result.audit["observations"])
        self.assertEqual({"0": snapshot}, result.to_dict()["audit"]["observations"])
        json.dumps(result.to_dict())

    def test_action_return_value_semantics(self) -> None:
        cases = (
            (None, True, None),
            (True, True, None),
            (1, True, None),
            (False, False, None),
            (0, False, None),
            ({}, True, {}),
            ({"supply_used": 18}, True, {"supply_used": 18}),
        )

        plan = SC2ExecutionPlan(
            intent="demo",
            actions=(SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE"),),
        )

        for return_value, expect_applied, expect_observation in cases:
            with self.subTest(return_value=return_value):

                class FakeBot:
                    async def train_unit(self, action):
                        return return_value

                result = asyncio.run(SC2RuntimeExecutor().execute_plan(FakeBot(), plan))

                if expect_applied:
                    self.assertEqual(plan.actions, result.applied_actions)
                    self.assertEqual((), result.skipped_actions)
                else:
                    self.assertEqual((), result.applied_actions)
                    self.assertEqual(plan.actions, result.skipped_actions)
                if expect_observation is None:
                    self.assertEqual({}, result.audit["observations"])
                else:
                    self.assertEqual(
                        {"0": expect_observation},
                        result.audit["observations"],
                    )

    def test_raising_bot_method_is_captured_as_structured_error(self) -> None:
        class FakeBot:
            async def train_unit(self, action):
                raise RuntimeError("supply blocked")

        plan = SC2ExecutionPlan(
            intent="demo",
            actions=(SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE"),),
        )

        result = asyncio.run(SC2RuntimeExecutor().execute_plan(FakeBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual(plan.actions, result.skipped_actions)
        self.assertEqual("RuntimeError", result.errors[0].exception_type)
        self.assertEqual("supply blocked", result.errors[0].message)
        self.assertEqual(0, result.errors[0].action_index)


class SC2ActionReportContractTest(unittest.TestCase):
    def test_report_truthiness_matches_full_application_only(self) -> None:
        cases = (
            ("full", SC2ActionReport(True, requested_count=3, issued_count=3), True),
            ("uncounted", SC2ActionReport(True), True),
            ("partial", SC2ActionReport(True, requested_count=3, issued_count=1), False),
            ("refused", SC2ActionReport(False, requested_count=3, issued_count=0), False),
        )
        for label, report, expected_truthiness in cases:
            with self.subTest(case=label):
                self.assertEqual(expected_truthiness, bool(report))

    def test_is_partial_requires_applied_and_both_counts(self) -> None:
        cases = (
            ("partial", SC2ActionReport(True, requested_count=3, issued_count=1), True),
            ("full", SC2ActionReport(True, requested_count=3, issued_count=3), False),
            ("no_requested", SC2ActionReport(True, issued_count=1), False),
            ("refused", SC2ActionReport(False, requested_count=3, issued_count=0), False),
        )
        for label, report, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(expected, report.is_partial)

    def test_report_validates_counts_and_serializes_json_ready(self) -> None:
        with self.assertRaises(ValueError):
            SC2ActionReport(True, requested_count=-1)
        with self.assertRaises(TypeError):
            SC2ActionReport(True, issued_count="2")
        payload = SC2ActionReport(
            True,
            requested_count=6,
            issued_count=2,
            detail="insufficient_units",
            audit={"failure_reason": "insufficient_units"},
        ).to_dict()
        self.assertEqual(payload, json.loads(json.dumps(payload)))
        self.assertTrue(payload["is_partial"])
        self.assertEqual("insufficient_units", payload["audit"]["failure_reason"])


class SC2RuntimeExecutorReportTest(unittest.TestCase):
    """Structured adapter reports flow into errors, audit, and success."""

    def make_plan(self):
        return SC2ExecutionPlan(
            intent="demo",
            actions=(
                SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=3),
            ),
        )

    def test_partial_report_downgrades_success_with_structured_error(self) -> None:
        class PartialBot:
            async def train_unit(self, action):
                return SC2ActionReport(
                    True, requested_count=3, issued_count=1, detail="unaffordable"
                )

        plan = self.make_plan()
        result = asyncio.run(SC2RuntimeExecutor().execute_plan(PartialBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual(plan.actions, result.applied_actions)
        self.assertEqual((), result.skipped_actions)
        self.assertEqual(1, len(result.errors))
        error = result.errors[0]
        self.assertEqual("PartialActionApplication", error.exception_type)
        self.assertEqual(3, error.metadata["requested_count"])
        self.assertEqual(1, error.metadata["issued_count"])
        self.assertEqual(
            {"applied": True, "requested_count": 3, "issued_count": 1,
             "is_partial": True, "detail": "unaffordable", "audit": {}},
            result.audit["action_reports"]["0"],
        )
        json.dumps(result.to_dict())

    def test_full_report_is_clean_success_with_audit_entry(self) -> None:
        class FullBot:
            async def train_unit(self, action):
                return SC2ActionReport(True, requested_count=3, issued_count=3)

        plan = self.make_plan()
        result = asyncio.run(SC2RuntimeExecutor().execute_plan(FullBot(), plan))

        self.assertTrue(result.success)
        self.assertEqual((), result.errors)
        self.assertFalse(result.audit["action_reports"]["0"]["is_partial"])

    def test_refused_report_with_detail_surfaces_action_refused_error(self) -> None:
        class RefusingBot:
            async def train_unit(self, action):
                return SC2ActionReport(
                    False,
                    requested_count=3,
                    issued_count=0,
                    detail="no_ready_idle_producer",
                    audit={"failure_reason": "no_ready_idle_producer"},
                )

        plan = self.make_plan()
        result = asyncio.run(SC2RuntimeExecutor().execute_plan(RefusingBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual(
            "no_ready_idle_producer",
            result.audit["action_reports"]["0"]["audit"]["failure_reason"],
        )
        self.assertEqual(
            "no_ready_idle_producer",
            result.errors[0].metadata["audit"]["failure_reason"],
        )
        self.assertEqual(plan.actions, result.skipped_actions)
        self.assertEqual("ActionRefused", result.errors[0].exception_type)
        self.assertEqual(
            "no_ready_idle_producer",
            result.errors[0].metadata["detail"],
        )

    def test_lifecycle_error_is_reported_once_then_drained(self) -> None:
        # One transient hook failure must not poison every later execution
        # in the same lifecycle cycle.
        class FailingStartBot:
            def __init__(self):
                self.failed = False

            async def on_start(self):
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("start hook failed once")

            async def train_unit(self, action):
                return True

        plan = self.make_plan()

        async def run_cycle():
            executor = SC2RuntimeExecutor()
            await executor.start(FailingStartBot())
            first = await executor.execute(plan)
            second = await executor.execute(plan)
            return first, second

        first, second = asyncio.run(run_cycle())

        self.assertFalse(first.success)
        self.assertEqual("RuntimeError", first.errors[0].exception_type)
        self.assertTrue(second.success)
        self.assertEqual((), second.errors)
