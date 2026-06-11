import unittest
from unittest.mock import Mock

import toycraft_commander as package_exports
from toycraft_commander.executor import (
    RESOURCE_GATHER_YIELD_PER_WORKER,
    TOYCRAFT_EXECUTION_RULES,
    ToyCraftExecutedAction,
    ToyCraftExecutionResult,
    ToyCraftStateDelta,
    ToyCraftStateDeltaSet,
    advance_toycraft_time,
    build_commander_response,
    build_toycraft_state_delta,
    execute_build_structure,
    execute_defend,
    execute_expand,
    execute_gather_resource,
    execute_harass,
    execute_summarize_state,
    execute_train_army,
    execute_train_worker,
    execute_toycraft_intent,
    narrate_state_summary,
    summarize_toycraft_state,
)
from toycraft_commander.feasibility import ConstructionOrder, ProductionOrder, ToyCraftState
from toycraft_commander.failure import (
    CommandFailureReason,
    CommandFailureReport,
    CommandFailureStage,
)
from toycraft_commander.interpreter import (
    SUMMARIZE_STATE_CONSTRAINT,
    interpret_command,
    interpret_command_text,
)
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    FeasibilityErrorReason,
    GatherResourceIntent,
    HarassIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)
from toycraft_commander.resources import ResourceState, SupplyState


def commander_state() -> ToyCraftState:
    return ToyCraftState(
        resources=ResourceState(minerals=450, gas=75),
        supply=SupplyState(used_supply=12, supply_capacity=23),
        units={"SCV": 8, "Marine": 4, "Vulture": 1},
        structures={
            "Command Center": 1,
            "Supply Depot": 1,
            "Barracks": 1,
            "Refinery": 1,
            "Bunker": 1,
        },
        busy_workers=2,
        busy_producers={"Barracks": 1},
        production_queues={"Command Center": 1, "Barracks": 2},
        claimed_locations=("main", "main base", "natural expansion"),
        damaged_targets=("front bunker",),
    )


class ToyCraftExecutorSurfaceTest(unittest.TestCase):
    def test_package_exports_executor_boundary(self) -> None:
        self.assertIs(ConstructionOrder, package_exports.ConstructionOrder)
        self.assertIs(CommandFailureReason, package_exports.CommandFailureReason)
        self.assertIs(CommandFailureReport, package_exports.CommandFailureReport)
        self.assertIs(CommandFailureStage, package_exports.CommandFailureStage)
        self.assertIs(ToyCraftExecutedAction, package_exports.ToyCraftExecutedAction)
        self.assertIs(ToyCraftExecutionResult, package_exports.ToyCraftExecutionResult)
        self.assertIs(ToyCraftStateDelta, package_exports.ToyCraftStateDelta)
        self.assertIs(ToyCraftStateDeltaSet, package_exports.ToyCraftStateDeltaSet)
        self.assertIs(
            RESOURCE_GATHER_YIELD_PER_WORKER,
            package_exports.RESOURCE_GATHER_YIELD_PER_WORKER,
        )
        self.assertIs(TOYCRAFT_EXECUTION_RULES, package_exports.TOYCRAFT_EXECUTION_RULES)
        self.assertIs(ProductionOrder, package_exports.ProductionOrder)
        self.assertIs(advance_toycraft_time, package_exports.advance_toycraft_time)
        self.assertIs(build_toycraft_state_delta, package_exports.build_toycraft_state_delta)
        self.assertIs(execute_build_structure, package_exports.execute_build_structure)
        self.assertIs(execute_defend, package_exports.execute_defend)
        self.assertIs(execute_expand, package_exports.execute_expand)
        self.assertIs(execute_harass, package_exports.execute_harass)
        self.assertIs(execute_toycraft_intent, package_exports.execute_toycraft_intent)
        self.assertIs(execute_gather_resource, package_exports.execute_gather_resource)
        self.assertIs(execute_summarize_state, package_exports.execute_summarize_state)
        self.assertIs(execute_train_army, package_exports.execute_train_army)
        self.assertIs(execute_train_worker, package_exports.execute_train_worker)
        self.assertIs(summarize_toycraft_state, package_exports.summarize_toycraft_state)
        self.assertIs(narrate_state_summary, package_exports.narrate_state_summary)
        self.assertIs(build_commander_response, package_exports.build_commander_response)

    def test_execution_rule_table_handles_implemented_rules(self) -> None:
        self.assertEqual(
            {
                "BUILD_STRUCTURE",
                "DEFEND",
                "EXPAND",
                "GATHER_RESOURCE",
                "HARASS",
                "SUMMARIZE_STATE",
                "TRAIN_ARMY",
                "TRAIN_WORKER",
            },
            set(TOYCRAFT_EXECUTION_RULES),
        )
        self.assertIs(TOYCRAFT_EXECUTION_RULES["BUILD_STRUCTURE"], execute_build_structure)
        self.assertIs(TOYCRAFT_EXECUTION_RULES["DEFEND"], execute_defend)
        self.assertIs(TOYCRAFT_EXECUTION_RULES["EXPAND"], execute_expand)
        self.assertIs(TOYCRAFT_EXECUTION_RULES["GATHER_RESOURCE"], execute_gather_resource)
        self.assertIs(TOYCRAFT_EXECUTION_RULES["HARASS"], execute_harass)
        self.assertIs(TOYCRAFT_EXECUTION_RULES["SUMMARIZE_STATE"], execute_summarize_state)
        self.assertIs(TOYCRAFT_EXECUTION_RULES["TRAIN_ARMY"], execute_train_army)
        self.assertIs(TOYCRAFT_EXECUTION_RULES["TRAIN_WORKER"], execute_train_worker)


class ToyCraftStructuredExecutionOutcomeTest(unittest.TestCase):
    def test_successful_gather_outcome_captures_actions_and_before_after_deltas(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        result = execute_toycraft_intent(
            GatherResourceIntent(resource="minerals", worker_count=2, base="main"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual(
            (
                ToyCraftExecutedAction(
                    action_type="assign_workers",
                    target="main.minerals",
                    amount=2,
                    metadata={"unit": "SCV"},
                ),
                ToyCraftExecutedAction(
                    action_type="gather_resource",
                    target="minerals",
                    amount=16,
                    metadata={"worker_count": 2, "base": "main"},
                ),
            ),
            result.executed_actions,
        )
        self.assertTrue(result.state_delta.has_changes)
        self.assertIn(
            ToyCraftStateDelta(
                path="resources.minerals",
                before=50,
                after=66,
                delta=16,
            ),
            result.state_delta.changes,
        )
        self.assertIn(
            ToyCraftStateDelta(
                path="busy_workers",
                before=0,
                after=2,
                delta=2,
            ),
            result.state_delta.changes,
        )
        self.assertEqual(("minerals +16", "busy_workers +2"), result.state_delta.raw_changes)

        payload = result.state_delta.to_dict()

        self.assertTrue(payload["has_changes"])
        self.assertIn(
            {
                "path": "resources.minerals",
                "before": 50,
                "after": 66,
                "delta": 16,
            },
            payload["changes"],
        )
        self.assertEqual(
            {
                "action_type": "gather_resource",
                "target": "minerals",
                "metadata": {"worker_count": 2, "base": "main"},
                "amount": 16,
            },
            result.executed_actions[1].to_dict(),
        )

    def test_successful_combat_outcome_captures_map_deltas_and_combat_actions(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=10, supply_capacity=23),
            units={"SCV": 6, "Marine": 2, "Vulture": 1},
            structures={"Command Center": 1, "Barracks": 1, "Bunker": 1},
        )

        result = execute_toycraft_intent(
            DefendIntent(location="main ramp", unit_group="available combat units"),
            state,
        )

        self.assertEqual(
            ("move_units", "apply_damage", "mitigate_pressure"),
            tuple(action.action_type for action in result.executed_actions),
        )
        self.assertIn(
            ToyCraftStateDelta(
                path="unit_positions.Marine",
                before=0,
                after="main ramp",
            ),
            result.state_delta.changes,
        )
        self.assertIn(
            ToyCraftStateDelta(
                path="target_damage.main ramp",
                before=0,
                after=10,
                delta=10,
            ),
            result.state_delta.changes,
        )
        self.assertIn(
            ToyCraftStateDelta(
                path="pressure_mitigation.main ramp",
                before=0,
                after=10,
                delta=10,
            ),
            result.state_delta.changes,
        )

    def test_read_only_and_rejected_outcomes_do_not_capture_executed_actions(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        summary_result = execute_toycraft_intent(SummarizeStateIntent(), state)
        rejected_result = execute_toycraft_intent(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            state,
        )

        self.assertTrue(summary_result.executed)
        self.assertTrue(summary_result.read_only)
        self.assertEqual((), summary_result.executed_actions)
        self.assertFalse(summary_result.state_delta.has_changes)
        self.assertFalse(rejected_result.executed)
        self.assertEqual((), rejected_result.executed_actions)
        self.assertFalse(rejected_result.state_delta.has_changes)
        self.assertEqual((), rejected_result.state_delta.raw_changes)

    def test_validation_failure_surfaces_structured_reasons_without_state_change(
        self,
    ) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        result = execute_toycraft_intent(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            state,
        )

        self.assertFalse(result.executed)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertIsNotNone(result.failure)
        self.assertEqual(CommandFailureStage.VALIDATION, result.failure.stage)
        self.assertFalse(result.failure.executed)
        self.assertFalse(result.failure.state_mutated)
        self.assertEqual(
            (FeasibilityErrorReason.MISSING_PREREQUISITE.value,),
            result.failure.reason_codes,
        )
        self.assertEqual("validation", result.failure.to_dict()["stage"])
        self.assertEqual((), result.state_changes)
        self.assertFalse(result.state_delta.has_changes)

    def test_missing_required_dsl_fields_are_clarified_before_execution(self) -> None:
        state = commander_state()
        payload = {
            "intent": "TRAIN_ARMY",
            "priority": "normal",
            "constraints": [],
        }

        result = execute_toycraft_intent(payload, state)

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual((), result.executed_actions)
        self.assertEqual((), result.state_changes)
        self.assertFalse(result.state_delta.has_changes)
        self.assertEqual(("unit_type", "count"), result.validation.missing_fields)
        self.assertEqual(
            FeasibilityErrorReason.MISSING_REQUIRED_FIELD,
            result.validation.reason_code,
        )
        self.assertIn("unit_type, count", result.validation.reason)
        self.assertIn("unit_type, count", result.narration)
        self.assertIsNotNone(result.failure)
        self.assertEqual(("unit_type", "count"), result.failure.primary_reason.fields)
        self.assertEqual(
            (FeasibilityErrorReason.MISSING_REQUIRED_FIELD.value,),
            result.failure.reason_codes,
        )

    def test_rule_execution_failure_surfaces_structured_reasons_without_state_change(
        self,
    ) -> None:
        state = commander_state()

        result = execute_toycraft_intent(
            ScoutIntent(target="enemy front", unit_group="1 SCV"),
            state,
        )

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertIsNotNone(result.failure)
        self.assertEqual(CommandFailureStage.RULE_EXECUTION, result.failure.stage)
        self.assertFalse(result.failure.state_mutated)
        self.assertEqual(
            (FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE.value,),
            result.failure.reason_codes,
        )
        self.assertEqual("SCOUT", result.failure.intent)
        self.assertEqual((), result.executed_actions)
        self.assertFalse(result.state_delta.has_changes)

    def test_state_delta_builder_rejects_non_state_inputs(self) -> None:
        state = ToyCraftState()

        with self.assertRaisesRegex(TypeError, "before_state"):
            build_toycraft_state_delta(object(), state)

        with self.assertRaisesRegex(TypeError, "after_state"):
            build_toycraft_state_delta(state, object())


class GatherResourceExecutionTest(unittest.TestCase):
    def test_korean_resource_allocation_command_updates_economy_state(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=80, gas=0),
            supply=SupplyState(used_supply=6, supply_capacity=15),
            units={"SCV": 6},
            structures={"Command Center": 1},
            busy_workers=1,
            claimed_locations=("main", "main base"),
        )

        payload = interpret_command_text("미네랄에 일꾼 세 기 붙여")
        result = execute_toycraft_intent(payload, state)

        self.assertEqual(
            GatherResourceIntent(
                priority="normal",
                constraints=("assign workers to requested resource",),
                resource="minerals",
                worker_count=3,
                base="main",
            ),
            payload,
        )
        self.assertTrue(result.executed)
        self.assertEqual(state, result.before_state)
        self.assertEqual(80, state.resources.minerals)
        self.assertEqual(104, result.after_state.resources.minerals)
        self.assertEqual(4, result.after_state.busy_workers)
        self.assertEqual(("minerals +24", "busy_workers +3"), result.state_changes)
        self.assertIn("미네랄 24", result.narration)

        response = build_commander_response(
            result,
            command_text="미네랄에 일꾼 세 기 붙여",
        )

        self.assertIn("실행 완료", response)
        self.assertIn("자원 채취", response)
        self.assertIn("미네랄 +24", response)
        self.assertIn("작업 중 SCV +3", response)
        self.assertIn("현재 자원은 미네랄 104, 가스 0", response)

    def test_valid_mineral_gathering_assigns_workers_and_adds_minerals(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
            busy_workers=1,
            claimed_locations=("main", "main base"),
        )

        result = execute_toycraft_intent(
            GatherResourceIntent(resource="minerals", worker_count=2, base="main"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertFalse(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(50, state.resources.minerals)
        self.assertEqual(1, state.busy_workers)
        self.assertEqual(66, result.after_state.resources.minerals)
        self.assertEqual(0, result.after_state.resources.gas)
        self.assertEqual(3, result.after_state.busy_workers)
        self.assertEqual(("minerals +16", "busy_workers +2"), result.state_changes)
        self.assertIn("SCV 2기", result.narration)
        self.assertIn("미네랄 16", result.narration)

    def test_valid_gas_gathering_requires_refinery_then_adds_gas(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=12),
            units={"SCV": 4},
            structures={"Command Center": 1, "Refinery": 1},
            claimed_locations=("main", "main base"),
        )

        result = execute_toycraft_intent(
            {
                "intent": "GATHER_RESOURCE",
                "priority": "high",
                "constraints": ["saturate refinery"],
                "resource": "gas",
                "worker_count": 3,
                "base": "main",
            },
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual(50, result.after_state.resources.minerals)
        self.assertEqual(24, result.after_state.resources.gas)
        self.assertEqual(3, result.after_state.busy_workers)
        self.assertEqual(("gas +12", "busy_workers +3"), result.state_changes)
        self.assertIn("가스 12", result.narration)

    def test_invalid_gathering_command_is_rejected_without_state_change(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            units={"SCV": 4},
            structures={"Command Center": 1},
            claimed_locations=("main", "main base"),
        )

        result = execute_toycraft_intent(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            state,
        )

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual(FeasibilityErrorReason.MISSING_PREREQUISITE, result.validation.reason_code)
        self.assertIn("실행하지 않았습니다", result.narration)
        self.assertIn("Refinery", result.narration)


class SummarizeStateExecutionTest(unittest.TestCase):
    def test_korean_summarize_command_interprets_executes_and_returns_response(
        self,
    ) -> None:
        state = commander_state()

        payload = interpret_command_text("현재 우리 병력하고 자원 현황 보고해")
        result = execute_toycraft_intent(payload, state)
        response = build_commander_response(result)

        self.assertEqual(
            SummarizeStateIntent(
                priority="normal",
                constraints=(SUMMARIZE_STATE_CONSTRAINT,),
            ),
            payload,
        )
        self.assertTrue(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertIn("실행 완료", response)
        self.assertIn("현재 상황을 보고합니다", response)
        self.assertIn("미네랄 450", response)
        self.assertIn("보급은 12/23", response)
        self.assertIn("전투 병력은 Marine 4기, Vulture 1기", response)

    def test_summarize_state_executes_read_only_and_returns_structured_summary(self) -> None:
        state = commander_state()

        result = execute_toycraft_intent(SummarizeStateIntent(), state)

        self.assertIsInstance(result, ToyCraftExecutionResult)
        self.assertTrue(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual("SUMMARIZE_STATE", result.intent)
        self.assertTrue(result.validation.executable)
        self.assertEqual((), result.state_changes)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual({"minerals": 450, "gas": 75}, result.summary["resources"])
        self.assertEqual(
            {"used_supply": 12, "supply_capacity": 23, "available_supply": 11},
            result.summary["supply"],
        )
        self.assertEqual({"SCV": 8, "Marine": 4, "Vulture": 1}, result.summary["units"])
        self.assertEqual(6, result.summary["available_workers"])
        self.assertEqual(["front bunker"], result.summary["damaged_targets"])

    def test_summarize_state_accepts_raw_dsl_payload_through_validation_gate(self) -> None:
        state = commander_state()

        result = execute_toycraft_intent(
            {
                "intent": "SUMMARIZE_STATE",
                "priority": "normal",
                "constraints": ["summarize current ToyCraft state"],
            },
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual("SUMMARIZE_STATE", result.intent)
        self.assertEqual(state, result.after_state)
        self.assertIn("현재 상황입니다", result.narration)

    def test_commander_facing_narration_mentions_core_state(self) -> None:
        result = execute_toycraft_intent(SummarizeStateIntent(), commander_state())

        response = build_commander_response(result)

        self.assertNotEqual(result.narration, response)
        self.assertIn("실행 완료", response)
        self.assertIn("미네랄 450", response)
        self.assertIn("가스 75", response)
        self.assertIn("보급은 12/23", response)
        self.assertIn("SCV는 총 8기", response)
        self.assertIn("가용 6", response)
        self.assertIn("Marine 4기", response)
        self.assertIn("Vulture 1기", response)
        self.assertIn("Bunker 1기", response)
        self.assertIn("front bunker", response)

    def test_commander_facing_narration_mentions_empty_state_fallbacks(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        result = execute_toycraft_intent(SummarizeStateIntent(), state)
        response = build_commander_response(result)

        self.assertTrue(result.executed)
        self.assertIn("미네랄 50", response)
        self.assertIn("가스 0", response)
        self.assertIn("보급은 4/15", response)
        self.assertIn("전투 병력은 전투 병력 없음", response)
        self.assertIn("생산 대기열은 대기열 없음", response)
        self.assertIn("수리 필요 대상은 손상 대상 없음", response)

    def test_invalid_summary_data_is_rejected_before_narration(self) -> None:
        with self.assertRaisesRegex(ValueError, "resources must be a mapping"):
            narrate_state_summary({"resources": None})

    def test_impossible_command_is_not_executed_and_keeps_state_unchanged(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500),
            supply=SupplyState(used_supply=15, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        result = execute_toycraft_intent(TrainArmyIntent(unit_type="Marine", count=1), state)

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertIn(
            FeasibilityErrorReason.UNAVAILABLE_PRODUCER,
            result.validation.reason_codes,
        )
        self.assertIn("실행하지 않았습니다", result.narration)
        self.assertTrue(result.validation.alternative.strip())

    def test_unsupported_raw_command_is_rejected_without_state_change(self) -> None:
        state = commander_state()

        result = execute_toycraft_intent(
            {"intent": "LAUNCH_NUKE", "priority": "urgent", "constraints": []},
            state,
        )

        self.assertFalse(result.executed)
        self.assertEqual("LAUNCH_NUKE", result.intent)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual(FeasibilityErrorReason.UNSUPPORTED_INTENT, result.validation.reason_code)
        self.assertIn("실행하지 않았습니다", result.narration)
        self.assertTrue(result.validation.alternative.strip())


class ImpossibleCommandExecutionSafetyTest(unittest.TestCase):
    def test_impossible_typed_commands_are_rejected_and_keep_state_unchanged(self) -> None:
        cases = (
            (
                "cannot afford a bunker",
                BuildStructureIntent(structure="Bunker", location="natural choke"),
                ToyCraftState(
                    resources=ResourceState(minerals=50, gas=0),
                    units={"SCV": 4},
                    structures={"Command Center": 1, "Supply Depot": 1, "Barracks": 1},
                ),
                FeasibilityErrorReason.INSUFFICIENT_MINERALS,
            ),
            (
                "cannot train worker at supply cap",
                TrainWorkerIntent(count=1),
                ToyCraftState(
                    resources=ResourceState(minerals=500, gas=0),
                    supply=SupplyState(used_supply=15, supply_capacity=15),
                    units={"SCV": 4},
                    structures={"Command Center": 1},
                ),
                FeasibilityErrorReason.INSUFFICIENT_SUPPLY,
            ),
            (
                "cannot expand onto claimed natural",
                ExpandIntent(location="natural expansion"),
                ToyCraftState(
                    resources=ResourceState(minerals=1000, gas=0),
                    units={"SCV": 4},
                    structures={"Command Center": 1},
                    claimed_locations=("main", "natural expansion"),
                ),
                FeasibilityErrorReason.LOCATION_UNAVAILABLE,
            ),
            (
                "cannot defend enemy territory",
                DefendIntent(location="enemy natural", unit_group="2 Marines"),
                commander_state(),
                FeasibilityErrorReason.INVALID_TARGET,
            ),
            (
                "cannot execute conflicting constraints",
                SummarizeStateIntent(constraints=("attack and retreat",)),
                commander_state(),
                FeasibilityErrorReason.CONSTRAINT_CONFLICT,
            ),
        )

        for case_name, payload, state, expected_reason in cases:
            with self.subTest(case=case_name):
                original_state = state

                result = execute_toycraft_intent(payload, state)

                self.assertFalse(result.executed)
                self.assertTrue(result.read_only)
                self.assertEqual(original_state, state)
                self.assertEqual(original_state, result.before_state)
                self.assertEqual(original_state, result.after_state)
                self.assertEqual(expected_reason, result.validation.reason_code)
                self.assertTrue(result.validation.reason.strip())
                self.assertTrue(result.validation.alternative.strip())
                self.assertIn("실행하지 않았습니다", result.narration)


class ConflictingCommandExecutionSafetyTest(unittest.TestCase):
    def test_conflicting_commands_are_rejected_before_economy_or_combat_mutations(
        self,
    ) -> None:
        cases = (
            (
                "do not spend and spend for bunker",
                BuildStructureIntent(
                    priority="high",
                    constraints=("spend minerals but save minerals",),
                    structure="Bunker",
                    location="natural choke",
                ),
                ToyCraftState(
                    resources=ResourceState(minerals=500, gas=50),
                    supply=SupplyState(used_supply=10, supply_capacity=23),
                    units={"SCV": 8, "Marine": 4},
                    structures={
                        "Command Center": 1,
                        "Supply Depot": 1,
                        "Barracks": 1,
                    },
                ),
            ),
            (
                "harass without leaving base",
                HarassIntent(
                    priority="high",
                    constraints=("harass enemy mineral line but do not leave base",),
                    target="enemy mineral line",
                    unit_group="2 Marines",
                ),
                ToyCraftState(
                    resources=ResourceState(minerals=500, gas=50),
                    supply=SupplyState(used_supply=10, supply_capacity=23),
                    units={"SCV": 8, "Marine": 4},
                    structures={
                        "Command Center": 1,
                        "Supply Depot": 1,
                        "Barracks": 1,
                    },
                ),
            ),
            (
                "attack and retreat at the same time",
                DefendIntent(
                    priority="urgent",
                    constraints=("attack and retreat at the same time",),
                    location="main ramp",
                    unit_group="2 Marines",
                ),
                commander_state(),
            ),
        )

        for case_name, payload, state in cases:
            with self.subTest(case=case_name):
                result = execute_toycraft_intent(payload, state)

                self.assertFalse(result.executed)
                self.assertTrue(result.read_only)
                self.assertEqual(state, result.before_state)
                self.assertEqual(state, result.after_state)
                self.assertEqual((), result.state_changes)
                self.assertEqual(
                    FeasibilityErrorReason.CONSTRAINT_CONFLICT,
                    result.validation.reason_code,
                )
                self.assertIn("Constraint conflict", result.validation.reason)
                self.assertIn("split the order", result.validation.alternative)
                self.assertIn("실행하지 않았습니다", result.narration)


class UnsupportedNaturalLanguageCommandSafetyTest(unittest.TestCase):
    def test_unsupported_text_commands_stop_before_execution_and_keep_state(self) -> None:
        unsupported_commands = (
            "핵 쏴",
            "벌처 두 기 뽑아",
            "드론 생산해",
            "저글링 러시 가",
            "맵 전체 자동으로 끝내",
        )

        for command_text in unsupported_commands:
            with self.subTest(command_text=command_text):
                state = commander_state()
                execute_spy = Mock(side_effect=execute_toycraft_intent)

                interpretation = interpret_command(command_text)
                if interpretation.payload is None:
                    after_state = state
                else:
                    result = execute_spy(interpretation.payload, state)
                    after_state = result.after_state

                self.assertIsNone(interpretation.payload)
                self.assertTrue(interpretation.clarification_required)
                self.assertTrue(interpretation.clarification_prompt.strip())
                self.assertIn("지원하지", interpretation.clarification_prompt)
                self.assertIn("실행하지 않았습니다", interpretation.clarification_prompt)
                execute_spy.assert_not_called()
                self.assertEqual(state, after_state)


class ResourceSpendingExecutionTest(unittest.TestCase):
    def test_valid_supply_depot_spends_minerals_assigns_builder_and_queues_construction(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=250, gas=0),
            supply=SupplyState(used_supply=14, supply_capacity=15),
            units={"SCV": 6},
            structures={"Command Center": 1},
            busy_workers=1,
        )

        result = execute_toycraft_intent(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertFalse(result.read_only)
        self.assertEqual(250, state.resources.minerals)
        self.assertEqual(150, result.after_state.resources.minerals)
        self.assertEqual(0, result.after_state.resources.gas)
        self.assertEqual(2, result.after_state.busy_workers)
        self.assertEqual(0, result.after_state.structure_count("Supply Depot"))
        self.assertEqual(1, result.after_state.construction_count("Supply Depot"))
        self.assertEqual(
            (
                ConstructionOrder(
                    structure_name="Supply Depot",
                    location="main ramp",
                    remaining_seconds=30,
                    assigned_workers=1,
                ),
            ),
            result.after_state.construction_queue,
        )
        self.assertEqual(15, result.after_state.supply.supply_capacity)
        self.assertEqual(14, result.after_state.supply.used_supply)
        self.assertEqual(
            (
                "minerals -100",
                "gas -0",
                "busy_workers +1",
                "construction_queue.Supply Depot +1",
                "construction_time_seconds +30",
            ),
            result.state_changes,
        )
        self.assertIn("Supply Depot", result.narration)
        self.assertIn("SCV 1기", result.narration)
        self.assertIn("미네랄 100", result.narration)
        self.assertIn("30초", result.narration)

    def test_valid_train_worker_spends_minerals_reserves_supply_and_queues_scv(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=200, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        result = execute_toycraft_intent(TrainWorkerIntent(count=2), state)

        self.assertTrue(result.executed)
        self.assertEqual(100, result.after_state.resources.minerals)
        self.assertEqual(0, result.after_state.resources.gas)
        self.assertEqual(6, result.after_state.supply.used_supply)
        self.assertEqual(15, result.after_state.supply.supply_capacity)
        self.assertEqual({"Command Center": 2}, result.after_state.production_queues)
        self.assertEqual({"SCV": 4}, result.after_state.units)
        self.assertEqual(
            (
                "minerals -100",
                "gas -0",
                "used_supply +2",
                "production_queues.Command Center +2",
            ),
            result.state_changes,
        )
        self.assertIn("SCV 2기 생산을 예약", result.narration)

    def test_valid_train_worker_preserves_busy_producer_and_appends_command_center_queue(
        self,
    ) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=175, gas=25),
            supply=SupplyState(used_supply=9, supply_capacity=15),
            units={"SCV": 7},
            structures={"Command Center": 2, "Barracks": 1},
            busy_producers={"Command Center": 1, "Barracks": 1},
            production_queues={"Command Center": 2, "Barracks": 3},
        )

        result = execute_toycraft_intent(TrainWorkerIntent(count=2), state)

        self.assertTrue(result.executed)
        self.assertEqual(75, result.after_state.resources.minerals)
        self.assertEqual(25, result.after_state.resources.gas)
        self.assertEqual(11, result.after_state.supply.used_supply)
        self.assertEqual(
            {"Command Center": 1, "Barracks": 1},
            result.after_state.busy_producers,
        )
        self.assertEqual(
            {"Command Center": 4, "Barracks": 3},
            result.after_state.production_queues,
        )
        self.assertEqual(
            (
                "minerals -100",
                "gas -0",
                "used_supply +2",
                "production_queues.Command Center +2",
            ),
            result.state_changes,
        )

    def test_valid_train_army_spends_resources_reserves_supply_and_queues_marines(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=8, supply_capacity=20),
            units={"SCV": 6, "Marine": 2},
            structures={"Command Center": 1, "Barracks": 1},
            production_queues={"Barracks": 1},
        )

        result = execute_toycraft_intent(
            TrainArmyIntent(unit_type="Marine", count=3),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual(150, result.after_state.resources.minerals)
        self.assertEqual(50, result.after_state.resources.gas)
        self.assertEqual(11, result.after_state.supply.used_supply)
        self.assertEqual({"Barracks": 4}, result.after_state.production_queues)
        self.assertEqual({"SCV": 6, "Marine": 2}, result.after_state.units)
        self.assertEqual(
            (
                "minerals -150",
                "gas -0",
                "used_supply +3",
                "production_queues.Barracks +3",
            ),
            result.state_changes,
        )
        self.assertIn("Marine 3기 생산을 예약", result.narration)

    def test_valid_train_army_uses_barracks_queue_without_touching_command_center_queue(
        self,
    ) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=250, gas=10),
            supply=SupplyState(used_supply=10, supply_capacity=18),
            units={"SCV": 8, "Marine": 3},
            structures={"Command Center": 1, "Barracks": 2},
            busy_producers={"Barracks": 1},
            production_queues={"Command Center": 1, "Barracks": 6},
        )

        result = execute_toycraft_intent(
            TrainArmyIntent(unit_type="Marine", count=2),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual(150, result.after_state.resources.minerals)
        self.assertEqual(10, result.after_state.resources.gas)
        self.assertEqual(12, result.after_state.supply.used_supply)
        self.assertEqual({"Barracks": 1}, result.after_state.busy_producers)
        self.assertEqual(
            {"Command Center": 1, "Barracks": 8},
            result.after_state.production_queues,
        )
        self.assertEqual(
            (
                "minerals -100",
                "gas -0",
                "used_supply +2",
                "production_queues.Barracks +2",
            ),
            result.state_changes,
        )

    def test_valid_expand_spends_command_center_cost_and_queues_construction(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500, gas=25),
            supply=SupplyState(used_supply=8, supply_capacity=23),
            units={"SCV": 8},
            structures={"Command Center": 1, "Supply Depot": 1},
            claimed_locations=("main", "main base"),
        )

        result = execute_toycraft_intent(
            ExpandIntent(location="natural expansion"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual(100, result.after_state.resources.minerals)
        self.assertEqual(25, result.after_state.resources.gas)
        self.assertEqual(1, result.after_state.structure_count("Command Center"))
        self.assertEqual(1, result.after_state.construction_count("Command Center"))
        self.assertEqual(
            (
                ConstructionOrder(
                    structure_name="Command Center",
                    location="natural expansion",
                    remaining_seconds=100,
                    assigned_workers=1,
                ),
            ),
            result.after_state.construction_queue,
        )
        self.assertEqual(1, result.after_state.busy_workers)
        self.assertEqual(
            ("main", "main base"),
            result.after_state.claimed_locations,
        )
        self.assertEqual(
            (
                "minerals -400",
                "gas -0",
                "busy_workers +1",
                "construction_queue.Command Center +1",
                "construction_time_seconds +100",
            ),
            result.state_changes,
        )
        self.assertIn("natural expansion", result.narration)
        self.assertIn("확장을 시작", result.narration)
        self.assertIn("100초", result.narration)

    def test_invalid_resource_spending_command_remains_read_only(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=75, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1, "Supply Depot": 1},
        )

        result = execute_toycraft_intent(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            state,
        )

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual(FeasibilityErrorReason.INSUFFICIENT_MINERALS, result.validation.reason_code)
        self.assertIn("실행하지 않았습니다", result.narration)


class CombatExecutionTest(unittest.TestCase):
    def test_valid_harass_applies_damage_and_updates_attacker_and_target_state(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=10, supply_capacity=23),
            units={"SCV": 6, "Marine": 4, "Vulture": 1},
            structures={"Command Center": 1, "Barracks": 1, "Bunker": 1},
            target_damage={"enemy mineral line": 12},
        )

        result = execute_toycraft_intent(
            HarassIntent(target="enemy mineral line", unit_group="2 Marines"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertFalse(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual({"Marine": "enemy mineral line"}, result.after_state.unit_positions)
        self.assertEqual(24, result.after_state.target_damage["enemy mineral line"])
        self.assertEqual({"SCV": 6, "Marine": 4, "Vulture": 1}, result.after_state.units)
        self.assertEqual(
            (
                "unit_positions.Marine -> enemy mineral line",
                "target_damage.enemy mineral line +12",
                "combat.견제 공격 Marinex2",
            ),
            result.state_changes,
        )
        self.assertIn("견제를 걸었습니다", result.narration)
        self.assertIn("12 피해", result.narration)
        self.assertIn("24/80", result.narration)
        self.assertIn("아군 손실은 없습니다", result.narration)

    def test_valid_defend_applies_damage_to_incoming_pressure_and_moves_units(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=10, supply_capacity=23),
            units={"SCV": 6, "Marine": 2, "Vulture": 1},
            structures={"Command Center": 1, "Barracks": 1, "Bunker": 1},
        )

        result = execute_toycraft_intent(
            DefendIntent(location="main ramp", unit_group="available combat units"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual({"Marine": "main ramp"}, result.after_state.unit_positions)
        self.assertEqual(10, result.after_state.target_damage["main ramp"])
        self.assertEqual(10, result.after_state.pressure_mitigation["main ramp"])
        self.assertEqual(
            (
                "unit_positions.Marine -> main ramp",
                "target_damage.main ramp +10",
                "pressure_mitigation.main ramp +10",
                "combat.방어 교전 Marinex2",
            ),
            result.state_changes,
        )
        self.assertIn("방어 위치로 이동", result.narration)
        self.assertIn("누적 피해는 10/160", result.narration)
        self.assertIn("누적 완화는 10", result.narration)
        self.assertIn("아군 손실은 없습니다", result.narration)

    def test_valid_defend_accumulates_pressure_mitigation_without_consuming_units(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=10, supply_capacity=23),
            units={"SCV": 6, "Marine": 3, "Vulture": 1},
            structures={"Command Center": 1, "Barracks": 1},
            target_damage={"natural choke": 20},
            pressure_mitigation={"natural choke": 15},
        )

        result = execute_toycraft_intent(
            DefendIntent(location="natural choke", unit_group="3 Marines"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual({"Marine": "natural choke"}, result.after_state.unit_positions)
        self.assertEqual(35, result.after_state.target_damage["natural choke"])
        self.assertEqual(30, result.after_state.pressure_mitigation["natural choke"])
        self.assertEqual({"SCV": 6, "Marine": 3, "Vulture": 1}, result.after_state.units)
        self.assertEqual(
            (
                "unit_positions.Marine -> natural choke",
                "target_damage.natural choke +15",
                "pressure_mitigation.natural choke +15",
                "combat.방어 교전 Marinex3",
            ),
            result.state_changes,
        )
        self.assertIn("위협 15을 완화", result.narration)
        self.assertIn("누적 완화는 30", result.narration)
        self.assertIn("아군 손실은 없습니다", result.narration)

    def test_high_risk_harass_narrates_damage_and_unit_losses(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=7, supply_capacity=23),
            units={"SCV": 6, "Marine": 1},
            structures={"Command Center": 1, "Barracks": 1},
        )

        result = execute_toycraft_intent(
            HarassIntent(target="enemy main", unit_group="1 Marine"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual({"SCV": 6}, result.after_state.units)
        self.assertEqual(6, result.after_state.supply.used_supply)
        self.assertEqual({}, result.after_state.unit_positions)
        self.assertEqual(5, result.after_state.target_damage["enemy main"])
        self.assertEqual(
            (
                "unit_positions.Marine -> enemy main",
                "target_damage.enemy main +5",
                "unit_losses.Marine -1",
                "used_supply -1",
                "combat.견제 공격 Marinex1",
            ),
            result.state_changes,
        )
        self.assertIn("5 피해", result.narration)
        self.assertIn("아군 손실은 Marine 1기", result.narration)

    def test_thin_defense_narrates_mitigated_damage_and_unit_losses(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=7, supply_capacity=23),
            units={"SCV": 6, "Marine": 1},
            structures={"Command Center": 1, "Barracks": 1, "Bunker": 1},
        )

        result = execute_toycraft_intent(
            DefendIntent(location="main ramp", unit_group="1 Marine"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual({"SCV": 6}, result.after_state.units)
        self.assertEqual(6, result.after_state.supply.used_supply)
        self.assertEqual({}, result.after_state.unit_positions)
        self.assertEqual(5, result.after_state.target_damage["main ramp"])
        self.assertEqual(5, result.after_state.pressure_mitigation["main ramp"])
        self.assertEqual(
            (
                "unit_positions.Marine -> main ramp",
                "target_damage.main ramp +5",
                "pressure_mitigation.main ramp +5",
                "unit_losses.Marine -1",
                "used_supply -1",
                "combat.방어 교전 Marinex1",
            ),
            result.state_changes,
        )
        self.assertIn("5 피해", result.narration)
        self.assertIn("위협 5을 완화", result.narration)
        self.assertIn("아군 손실은 Marine 1기", result.narration)

    def test_lethal_harass_marks_enemy_target_defeated_without_removing_attackers(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=14, supply_capacity=23),
            units={"SCV": 6, "Vulture": 4},
            structures={"Command Center": 1, "Factory": 1},
        )

        result = execute_toycraft_intent(
            HarassIntent(target="enemy mineral line", unit_group="4 Vultures"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual({"Vulture": "enemy mineral line"}, result.after_state.unit_positions)
        self.assertEqual(80, result.after_state.target_damage["enemy mineral line"])
        self.assertEqual(("enemy mineral line",), result.after_state.defeated_targets)
        self.assertEqual({"SCV": 6, "Vulture": 4}, result.after_state.units)
        self.assertEqual(
            (
                "unit_positions.Vulture -> enemy mineral line",
                "target_damage.enemy mineral line +80",
                "combat.견제 공격 Vulturex4",
                "defeated_targets.enemy mineral line +1",
            ),
            result.state_changes,
        )
        self.assertIn("목표 압박을 무력화했습니다", result.narration)

    def test_lethal_defense_marks_pressure_defeated(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=8, supply_capacity=23),
            units={"SCV": 6, "Vulture": 1},
            structures={"Command Center": 1, "Factory": 1},
            target_damage={"main ramp": 141},
            pressure_mitigation={"main ramp": 30},
        )

        result = execute_toycraft_intent(
            DefendIntent(location="main ramp", unit_group="1 Vulture"),
            state,
        )

        self.assertTrue(result.executed)
        self.assertEqual(160, result.after_state.target_damage["main ramp"])
        self.assertEqual(49, result.after_state.pressure_mitigation["main ramp"])
        self.assertEqual(("main ramp",), result.after_state.defeated_targets)
        self.assertEqual(
            (
                "unit_positions.Vulture -> main ramp",
                "target_damage.main ramp +19",
                "pressure_mitigation.main ramp +19",
                "combat.방어 교전 Vulturex1",
                "defeated_targets.main ramp +1",
            ),
            result.state_changes,
        )

    def test_zero_count_units_are_removed_from_state_snapshots(self) -> None:
        state = ToyCraftState(
            units={"SCV": 6, "Marine": 0, "마린": 0, "Vulture": 1},
            structures={"Command Center": 1},
        )

        self.assertEqual({"SCV": 6, "Vulture": 1}, state.units)
        self.assertEqual(0, state.unit_count("Marine"))

    def test_invalid_harass_still_rejects_before_combat_state_change(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            units={"SCV": 6, "Marine": 2},
            structures={"Command Center": 1, "Barracks": 1},
        )

        result = execute_toycraft_intent(
            HarassIntent(target="main ramp", unit_group="2 Marines"),
            state,
        )

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual(FeasibilityErrorReason.INVALID_TARGET, result.validation.reason_code)
        self.assertIn("실행하지 않았습니다", result.narration)


class ProgressExecutionTest(unittest.TestCase):
    def test_training_start_records_serial_timed_orders_without_granting_units(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=200, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        result = execute_toycraft_intent(TrainWorkerIntent(count=2), state)

        self.assertTrue(result.executed)
        self.assertEqual({"SCV": 4}, result.after_state.units)
        self.assertEqual({"Command Center": 2}, result.after_state.production_queues)
        self.assertEqual(
            (
                ProductionOrder(
                    unit_name="SCV",
                    producer="Command Center",
                    remaining_seconds=20,
                ),
                ProductionOrder(
                    unit_name="SCV",
                    producer="Command Center",
                    remaining_seconds=40,
                ),
            ),
            result.after_state.production_orders,
        )

    def test_partial_progress_advances_timers_without_completing_prematurely(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=400, gas=0),
            supply=SupplyState(used_supply=14, supply_capacity=15),
            units={"SCV": 6},
            structures={"Command Center": 1},
            claimed_locations=("main", "main base"),
        )
        training = execute_toycraft_intent(TrainWorkerIntent(count=1), state)
        building = execute_toycraft_intent(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            training.after_state,
        )

        result = advance_toycraft_time(building.after_state, 19)

        self.assertTrue(result.executed)
        self.assertEqual({"SCV": 6}, result.after_state.units)
        self.assertEqual(0, result.after_state.structure_count("Supply Depot"))
        self.assertEqual(15, result.after_state.supply.used_supply)
        self.assertEqual(15, result.after_state.supply.supply_capacity)
        self.assertEqual(
            (
                ProductionOrder(
                    unit_name="SCV",
                    producer="Command Center",
                    remaining_seconds=1,
                ),
            ),
            result.after_state.production_orders,
        )
        self.assertEqual(
            (
                ConstructionOrder(
                    structure_name="Supply Depot",
                    location="main ramp",
                    remaining_seconds=11,
                    assigned_workers=1,
                ),
            ),
            result.after_state.construction_queue,
        )
        self.assertEqual({"Command Center": 1}, result.after_state.production_queues)
        self.assertIn("아직 완료된 생산이나 건설은 없습니다", result.narration)

    def test_exact_progress_completes_unit_and_clears_production_queue(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=200, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        queued = execute_toycraft_intent(TrainWorkerIntent(count=1), state).after_state

        result = advance_toycraft_time(queued, 20)

        self.assertTrue(result.executed)
        self.assertEqual({"SCV": 5}, result.after_state.units)
        self.assertEqual(5, result.after_state.supply.used_supply)
        self.assertEqual({}, result.after_state.production_queues)
        self.assertEqual((), result.after_state.production_orders)
        self.assertEqual(
            (
                "production_time_seconds -20",
                "units.SCV +1",
                "production_queues.Command Center -1",
            ),
            result.state_changes,
        )
        self.assertIn("완료된 유닛은 SCV 1", result.narration)

    def test_exact_progress_completes_structure_and_clears_construction_queue(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=150, gas=0),
            supply=SupplyState(used_supply=14, supply_capacity=15),
            units={"SCV": 6},
            structures={"Command Center": 1},
            busy_workers=1,
            construction_queue=(
                ConstructionOrder(
                    structure_name="Supply Depot",
                    location="main ramp",
                    remaining_seconds=30,
                    assigned_workers=1,
                ),
            ),
            claimed_locations=("main", "main base"),
        )

        result = advance_toycraft_time(state, 30)

        self.assertTrue(result.executed)
        self.assertEqual(1, result.after_state.structure_count("Supply Depot"))
        self.assertEqual(23, result.after_state.supply.supply_capacity)
        self.assertEqual(0, result.after_state.busy_workers)
        self.assertEqual((), result.after_state.construction_queue)
        self.assertIn("main ramp", result.after_state.claimed_locations)
        self.assertEqual(
            (
                "construction_time_seconds -30",
                "structures.Supply Depot +1",
                "construction_queue.Supply Depot -1",
                "busy_workers -1",
            ),
            result.state_changes,
        )
        self.assertIn("완료된 구조물은 Supply Depot 1", result.narration)

    def test_large_progress_completes_finished_work_and_keeps_unfinished_remainder(
        self,
    ) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=100, gas=0),
            supply=SupplyState(used_supply=6, supply_capacity=15),
            units={"SCV": 4, "Marine": 1},
            structures={"Command Center": 1, "Barracks": 1},
            busy_workers=2,
            production_queues={"Command Center": 1, "Barracks": 1},
            production_orders=(
                ProductionOrder(
                    unit_name="SCV",
                    producer="Command Center",
                    remaining_seconds=5,
                ),
                ProductionOrder(
                    unit_name="Marine",
                    producer="Barracks",
                    remaining_seconds=24,
                ),
            ),
            construction_queue=(
                ConstructionOrder(
                    structure_name="Supply Depot",
                    location="main ramp",
                    remaining_seconds=10,
                    assigned_workers=1,
                ),
                ConstructionOrder(
                    structure_name="Factory",
                    location="main base",
                    remaining_seconds=60,
                    assigned_workers=1,
                ),
            ),
            claimed_locations=("main", "main base"),
        )

        result = advance_toycraft_time(state, 25)

        self.assertTrue(result.executed)
        self.assertEqual({"SCV": 5, "Marine": 2}, result.after_state.units)
        self.assertEqual({"Supply Depot": 1, "Command Center": 1, "Barracks": 1}, result.after_state.structures)
        self.assertEqual(23, result.after_state.supply.supply_capacity)
        self.assertEqual(1, result.after_state.busy_workers)
        self.assertEqual({}, result.after_state.production_queues)
        self.assertEqual((), result.after_state.production_orders)
        self.assertEqual(
            (
                ConstructionOrder(
                    structure_name="Factory",
                    location="main base",
                    remaining_seconds=35,
                    assigned_workers=1,
                ),
            ),
            result.after_state.construction_queue,
        )
        self.assertIn("완료된 유닛은 SCV 1, Marine 1", result.narration)
        self.assertIn("완료된 구조물은 Supply Depot 1", result.narration)


if __name__ == "__main__":
    unittest.main()
