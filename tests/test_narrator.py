import unittest

import toycraft_commander as package_exports
from toycraft_commander.feasibility import ToyCraftState, validate_intent_feasibility
from toycraft_commander.executor import execute_toycraft_intent
from toycraft_commander.interpreter import interpret_command_text
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    FeasibilityErrorReason,
    GatherResourceIntent,
    IntentValidationResult,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
    ValidationStatus,
)
from toycraft_commander.narrator import (
    NarratorFeasibilityIssue,
    NarratorFeasibilityOutcome,
    NarratorResponseKind,
    StateNarratorBlockedCommand,
    StateNarratorChangeSummary,
    StateNarratorDelta,
    StateNarratorInput,
    StateNarratorResponse,
    StateNarratorResponseMetadata,
    StateNarratorSnapshot,
    build_blocked_command_report,
    build_execution_narrator_response,
    build_execution_narrator_input,
    build_feasibility_narrator_outcome,
    build_rejected_narrator_input,
    build_state_change_summary,
    build_state_narrator_metadata,
    build_state_narrator_response,
    build_state_narrator_snapshot,
    render_blocked_narration,
    render_success_narration,
)
from toycraft_commander.resources import ResourceState, SupplyState


class StateNarratorContractSurfaceTest(unittest.TestCase):
    def test_package_exports_state_narrator_contract(self) -> None:
        self.assertIs(NarratorFeasibilityIssue, package_exports.NarratorFeasibilityIssue)
        self.assertIs(NarratorFeasibilityOutcome, package_exports.NarratorFeasibilityOutcome)
        self.assertIs(NarratorResponseKind, package_exports.NarratorResponseKind)
        self.assertIs(StateNarratorBlockedCommand, package_exports.StateNarratorBlockedCommand)
        self.assertIs(StateNarratorChangeSummary, package_exports.StateNarratorChangeSummary)
        self.assertIs(StateNarratorDelta, package_exports.StateNarratorDelta)
        self.assertIs(StateNarratorInput, package_exports.StateNarratorInput)
        self.assertIs(StateNarratorResponse, package_exports.StateNarratorResponse)
        self.assertIs(
            StateNarratorResponseMetadata,
            package_exports.StateNarratorResponseMetadata,
        )
        self.assertIs(StateNarratorSnapshot, package_exports.StateNarratorSnapshot)
        self.assertIs(
            build_blocked_command_report,
            package_exports.build_blocked_command_report,
        )
        self.assertIs(
            build_execution_narrator_response,
            package_exports.build_execution_narrator_response,
        )
        self.assertIs(
            build_execution_narrator_input,
            package_exports.build_execution_narrator_input,
        )
        self.assertIs(
            build_feasibility_narrator_outcome,
            package_exports.build_feasibility_narrator_outcome,
        )
        self.assertIs(
            build_rejected_narrator_input,
            package_exports.build_rejected_narrator_input,
        )
        self.assertIs(
            build_state_change_summary,
            package_exports.build_state_change_summary,
        )
        self.assertIs(
            build_state_narrator_metadata,
            package_exports.build_state_narrator_metadata,
        )
        self.assertIs(
            build_state_narrator_response,
            package_exports.build_state_narrator_response,
        )
        self.assertIs(render_blocked_narration, package_exports.render_blocked_narration)
        self.assertIs(render_success_narration, package_exports.render_success_narration)
        self.assertIs(
            build_state_narrator_snapshot,
            package_exports.build_state_narrator_snapshot,
        )


class StateNarratorInputTest(unittest.TestCase):
    def test_execution_result_builds_complete_narrator_input_contract(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        result = execute_toycraft_intent(
            GatherResourceIntent(
                priority="high",
                constraints=("boost minerals",),
                resource="minerals",
                worker_count=2,
                base="main",
            ),
            state,
        )

        narrator_input = build_execution_narrator_input(
            result,
            command_text="미네랄에 일꾼 두 기 붙여",
        )

        self.assertEqual("미네랄에 일꾼 두 기 붙여", narrator_input.command_text)
        self.assertEqual("GATHER_RESOURCE", narrator_input.intent)
        self.assertEqual("high", narrator_input.priority)
        self.assertEqual(("boost minerals",), narrator_input.constraints)
        self.assertTrue(narrator_input.executed)
        self.assertFalse(narrator_input.read_only)
        self.assertTrue(narrator_input.feasibility.executable)
        self.assertEqual(ValidationStatus.EXECUTABLE, narrator_input.feasibility.status)
        self.assertEqual(("minerals +16", "busy_workers +2"), narrator_input.state_changes)
        self.assertTrue(narrator_input.change_summary.has_changes)
        self.assertEqual(
            StateNarratorDelta(
                name="resources.minerals",
                before=50,
                after=66,
                delta=16,
            ),
            narrator_input.change_summary.resource_deltas[0],
        )
        self.assertIn(
            StateNarratorDelta(
                name="busy_workers",
                before=0,
                after=2,
                delta=2,
            ),
            narrator_input.change_summary.entity_deltas,
        )
        self.assertEqual(50, narrator_input.before_state.resources["minerals"])
        self.assertEqual(66, narrator_input.after_state.resources["minerals"])
        self.assertEqual(4, narrator_input.before_state.supply["used_supply"])
        self.assertEqual(11, narrator_input.after_state.supply["available_supply"])

        payload = narrator_input.to_dict()

        self.assertEqual("executable", payload["feasibility"]["status"])
        self.assertEqual(["boost minerals"], payload["constraints"])
        self.assertEqual(["minerals +16", "busy_workers +2"], payload["state_changes"])
        self.assertEqual(
            {
                "name": "resources.minerals",
                "before": 50,
                "after": 66,
                "delta": 16,
            },
            payload["change_summary"]["resource_deltas"][0],
        )
        self.assertEqual(
            ["minerals +16", "busy_workers +2"],
            payload["change_summary"]["raw_changes"],
        )
        self.assertEqual(66, payload["after_state"]["resources"]["minerals"])

    def test_rejected_feasibility_builds_read_only_narrator_input_contract(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        validation = validate_intent_feasibility(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            state,
        )

        narrator_input = build_rejected_narrator_input(
            validation,
            state,
            command_text="가스에 일꾼 하나 붙여",
        )

        self.assertEqual("가스에 일꾼 하나 붙여", narrator_input.command_text)
        self.assertEqual("GATHER_RESOURCE", narrator_input.intent)
        self.assertFalse(narrator_input.executed)
        self.assertTrue(narrator_input.read_only)
        self.assertFalse(narrator_input.feasibility.executable)
        self.assertEqual(ValidationStatus.REJECTED, narrator_input.feasibility.status)
        self.assertEqual(
            (FeasibilityErrorReason.MISSING_PREREQUISITE,),
            narrator_input.feasibility.reason_codes,
        )
        self.assertEqual(narrator_input.before_state, narrator_input.after_state)
        self.assertFalse(narrator_input.change_summary.has_changes)
        self.assertEqual((), narrator_input.change_summary.resource_deltas)
        self.assertEqual((), narrator_input.change_summary.entity_deltas)
        self.assertEqual((), narrator_input.change_summary.map_deltas)
        self.assertEqual(50, narrator_input.after_state.resources["minerals"])
        self.assertTrue(narrator_input.feasibility.reason.strip())
        self.assertTrue(narrator_input.feasibility.alternative.strip())

        payload = narrator_input.to_dict()

        self.assertEqual("rejected", payload["feasibility"]["status"])
        self.assertEqual(
            ["missing_prerequisite"],
            payload["feasibility"]["reason_codes"],
        )
        self.assertEqual(payload["before_state"], payload["after_state"])

    def test_rejected_narrator_input_rejects_executable_validation(self) -> None:
        state = ToyCraftState()
        validation = validate_intent_feasibility(
            GatherResourceIntent(resource="minerals", worker_count=1, base="main"),
            state,
        )

        with self.assertRaisesRegex(ValueError, "requires rejected validation"):
            build_rejected_narrator_input(validation, state)

    def test_build_structure_summary_groups_resource_and_entity_deltas(self) -> None:
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

        narrator_input = build_execution_narrator_input(result)

        self.assertIn(
            StateNarratorDelta(
                name="resources.minerals",
                before=250,
                after=150,
                delta=-100,
            ),
            narrator_input.change_summary.resource_deltas,
        )
        self.assertIn(
            StateNarratorDelta(
                name="construction_queue",
                before=(),
                after=(
                    {
                        "structure_name": "Supply Depot",
                        "location": "main ramp",
                        "remaining_seconds": 30,
                        "assigned_workers": 1,
                    },
                ),
            ),
            narrator_input.change_summary.entity_deltas,
        )
        self.assertEqual((), narrator_input.change_summary.map_deltas)

    def test_combat_summary_groups_map_deltas(self) -> None:
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

        narrator_input = build_execution_narrator_input(result)

        self.assertEqual((), narrator_input.change_summary.resource_deltas)
        self.assertIn(
            StateNarratorDelta(
                name="unit_positions.Marine",
                before=0,
                after="main ramp",
            ),
            narrator_input.change_summary.map_deltas,
        )
        self.assertIn(
            StateNarratorDelta(
                name="target_damage.main ramp",
                before=0,
                after=10,
                delta=10,
            ),
            narrator_input.change_summary.map_deltas,
        )
        self.assertIn(
            StateNarratorDelta(
                name="pressure_mitigation.main ramp",
                before=0,
                after=10,
                delta=10,
            ),
            narrator_input.change_summary.map_deltas,
        )

    def test_manual_summary_builder_rejects_non_snapshot_inputs(self) -> None:
        state = build_state_narrator_snapshot(ToyCraftState())

        with self.assertRaisesRegex(TypeError, "before_state"):
            build_state_change_summary(object(), state)

        with self.assertRaisesRegex(TypeError, "after_state"):
            build_state_change_summary(state, object())


class StateNarratorResponseTest(unittest.TestCase):
    def test_execution_response_contract_contains_text_and_metadata(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        result = execute_toycraft_intent(
            GatherResourceIntent(
                priority="high",
                constraints=("boost minerals",),
                resource="minerals",
                worker_count=2,
                base="main",
            ),
            state,
        )

        response = build_execution_narrator_response(
            result,
            command_text="미네랄에 일꾼 두 기 붙여",
        )

        self.assertEqual(
            render_success_narration(build_execution_narrator_input(result)),
            response.response_text,
        )
        self.assertNotEqual(result.narration, response.response_text)
        self.assertIn("실행 완료", response.response_text)
        self.assertIn("자원 채취", response.response_text)
        self.assertIn("미네랄 +16", response.response_text)
        self.assertIn("작업 중 SCV +2", response.response_text)
        self.assertIn("현재 자원은 미네랄 66, 가스 0", response.response_text)
        self.assertIsNone(response.blocked_command)
        self.assertEqual("executed", response.metadata.response_kind)
        self.assertEqual("GATHER_RESOURCE", response.metadata.intent)
        self.assertEqual("high", response.metadata.priority)
        self.assertEqual(("boost minerals",), response.metadata.constraints)
        self.assertTrue(response.metadata.executed)
        self.assertFalse(response.metadata.read_only)
        self.assertTrue(response.metadata.state_changed)
        self.assertEqual(("minerals +16", "busy_workers +2"), response.metadata.state_changes)

        payload = response.to_dict()

        self.assertEqual(response.response_text, payload["response_text"])
        self.assertEqual("executed", payload["metadata"]["response_kind"])
        self.assertEqual("executable", payload["metadata"]["validation_status"])
        self.assertEqual(["boost minerals"], payload["metadata"]["constraints"])
        self.assertIsNone(payload["blocked_command"])

    def test_success_renderer_describes_build_structure_outcome(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=250, gas=0),
            supply=SupplyState(used_supply=14, supply_capacity=15),
            units={"SCV": 6},
            structures={"Command Center": 1},
        )
        result = execute_toycraft_intent(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            state,
        )

        response = build_execution_narrator_response(result)

        self.assertIn("건설 명령", response.response_text)
        self.assertIn("main ramp", response.response_text)
        self.assertIn("Supply Depot", response.response_text)
        self.assertIn("미네랄 -100", response.response_text)
        self.assertIn("완료까지 30초", response.response_text)
        self.assertEqual("executed", response.metadata.response_kind)

    def test_success_renderer_describes_training_outcome(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=200, gas=0),
            supply=SupplyState(used_supply=8, supply_capacity=15),
            units={"SCV": 6},
            structures={"Command Center": 1, "Barracks": 1},
        )
        result = execute_toycraft_intent(
            TrainArmyIntent(unit_type="Marine", count=2),
            state,
        )

        response = build_execution_narrator_response(result)

        self.assertIn("생산 명령", response.response_text)
        self.assertIn("Barracks", response.response_text)
        self.assertIn("Marine 2기", response.response_text)
        self.assertIn("미네랄 -100", response.response_text)
        self.assertIn("보급 +2", response.response_text)
        self.assertIn("보급은 10/15", response.response_text)

    def test_success_renderer_describes_combat_outcome(self) -> None:
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

        response = build_execution_narrator_response(result)

        self.assertIn("방어 명령", response.response_text)
        self.assertIn("Marine을 main ramp로 이동", response.response_text)
        self.assertIn("main ramp 피해 +10", response.response_text)
        self.assertIn("main ramp 압박 완화 +10", response.response_text)
        self.assertIn("현재 전투 위치는 Marine main ramp", response.response_text)

    def test_read_only_response_contract_marks_state_unchanged(self) -> None:
        state = ToyCraftState()
        result = execute_toycraft_intent(SummarizeStateIntent(), state)

        response = build_execution_narrator_response(
            result,
            command_text="상태 알려줘",
        )

        self.assertEqual("read_only", response.metadata.response_kind)
        self.assertTrue(response.metadata.executed)
        self.assertTrue(response.metadata.read_only)
        self.assertFalse(response.metadata.state_changed)
        self.assertEqual(result.summary, response.metadata.summary)
        self.assertIsNone(response.blocked_command)
        self.assertIn("현재 상황을 보고합니다", response.response_text)
        self.assertIn("자원은 미네랄", response.response_text)

    def test_success_renderer_rejects_blocked_input(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        validation = validate_intent_feasibility(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            state,
        )
        narrator_input = build_rejected_narrator_input(validation, state)

        with self.assertRaisesRegex(ValueError, "success narration"):
            render_success_narration(narrator_input)

    def test_success_renderer_rejects_non_narrator_input(self) -> None:
        with self.assertRaisesRegex(TypeError, "narrator_input"):
            render_success_narration(object())

    def test_blocked_response_contract_reports_reason_and_alternative(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        validation = validate_intent_feasibility(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            state,
        )
        narrator_input = build_rejected_narrator_input(
            validation,
            state,
            command_text="가스에 일꾼 하나 붙여",
        )

        response = build_state_narrator_response(
            narrator_input,
            response_text="실행하지 않았습니다. Refinery가 필요합니다.",
        )

        self.assertEqual("blocked", response.metadata.response_kind)
        self.assertFalse(response.metadata.executed)
        self.assertTrue(response.metadata.read_only)
        self.assertFalse(response.metadata.state_changed)
        self.assertIsNotNone(response.blocked_command)
        self.assertEqual(validation.reason, response.blocked_command.reason)
        self.assertEqual(validation.alternative, response.blocked_command.alternative)
        self.assertEqual(
            (FeasibilityErrorReason.MISSING_PREREQUISITE,),
            response.blocked_command.reason_codes,
        )
        self.assertEqual(validation.issues[0].message, response.blocked_command.issues[0].message)

        payload = response.to_dict()

        self.assertEqual("blocked", payload["metadata"]["response_kind"])
        self.assertEqual("rejected", payload["metadata"]["validation_status"])
        self.assertEqual(["missing_prerequisite"], payload["blocked_command"]["reason_codes"])
        self.assertTrue(payload["blocked_command"]["reason"])
        self.assertTrue(payload["blocked_command"]["alternative"])
        self.assertIn("추천 행동", response.response_text)
        self.assertIn(validation.alternative, response.response_text)

    def test_blocked_response_defaults_to_player_facing_failure_template(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        validation = validate_intent_feasibility(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            state,
        )
        narrator_input = build_rejected_narrator_input(
            validation,
            state,
            command_text="가스에 일꾼 하나 붙여",
        )

        response = build_state_narrator_response(narrator_input)

        self.assertEqual(render_blocked_narration(narrator_input), response.response_text)
        self.assertEqual("blocked", response.metadata.response_kind)
        self.assertFalse(response.metadata.executed)
        self.assertFalse(response.metadata.state_changed)
        self.assertIn("실행하지 않았습니다", response.response_text)
        self.assertIn("자원 채취 명령은 상태를 바꾸지 않았습니다", response.response_text)
        self.assertIn("입력 명령: 가스에 일꾼 하나 붙여", response.response_text)
        self.assertIn("선행 조건이 아직 준비되지 않았습니다", response.response_text)
        self.assertIn("Refinery", response.response_text)
        self.assertIn("추천 행동", response.response_text)
        self.assertIn("현재 상태: 미네랄 50, 가스 0", response.response_text)
        self.assertEqual(narrator_input.before_state, narrator_input.after_state)

    def test_blocked_fallback_narration_includes_actionable_next_step(self) -> None:
        state = ToyCraftState()
        validation = IntentValidationResult(
            executable=False,
            reason="No matching command route reached execution.",
        )
        narrator_input = build_rejected_narrator_input(
            validation,
            state,
            command_text="무언가 해",
            intent="UNKNOWN",
        )

        response = build_state_narrator_response(narrator_input)

        self.assertIsNotNone(response.blocked_command)
        self.assertIn("명령을 하나로 구체화", response.blocked_command.alternative)
        self.assertIn("예: 상태 알려줘", response.blocked_command.alternative)
        self.assertIn("추천 행동", response.response_text)
        self.assertIn("예: 상태 알려줘", response.response_text)
        self.assertIn("일꾼 계속 찍어", response.response_text)

    def test_blocked_failure_template_lists_multiple_reasons(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=25, gas=0),
            supply=SupplyState(used_supply=15, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        validation = validate_intent_feasibility(
            TrainArmyIntent(unit_type="Marine", count=2),
            state,
        )
        narrator_input = build_rejected_narrator_input(
            validation,
            state,
            command_text="마린 두 기 찍어",
        )

        narration = render_blocked_narration(narrator_input)

        self.assertIn("병력 생산 명령은 상태를 바꾸지 않았습니다", narration)
        self.assertIn("생산 건물이 없거나 대기열을 받을 수 없습니다", narration)
        self.assertIn("미네랄이 부족합니다", narration)
        self.assertIn("보급이 막혔습니다", narration)
        self.assertIn("추천 행동", narration)
        self.assertIn("현재 상태: 미네랄 25, 가스 0, 보급 15/15", narration)

    def test_blocked_renderer_rejects_success_input(self) -> None:
        state = ToyCraftState()
        result = execute_toycraft_intent(
            GatherResourceIntent(resource="minerals", worker_count=1, base="main"),
            state,
        )
        narrator_input = build_execution_narrator_input(result)

        with self.assertRaisesRegex(ValueError, "blocked narration"):
            render_blocked_narration(narrator_input)

    def test_blocked_renderer_rejects_non_narrator_input(self) -> None:
        with self.assertRaisesRegex(TypeError, "narrator_input"):
            render_blocked_narration(object())

    def test_blocked_report_rejects_executable_input(self) -> None:
        state = ToyCraftState()
        result = execute_toycraft_intent(
            GatherResourceIntent(resource="minerals", worker_count=1, base="main"),
            state,
        )
        narrator_input = build_execution_narrator_input(result)

        with self.assertRaisesRegex(ValueError, "rejected feasibility"):
            build_blocked_command_report(narrator_input)

    def test_response_contract_rejects_empty_text(self) -> None:
        state = ToyCraftState()
        result = execute_toycraft_intent(
            GatherResourceIntent(resource="minerals", worker_count=1, base="main"),
            state,
        )
        narrator_input = build_execution_narrator_input(result)

        with self.assertRaisesRegex(ValueError, "response_text"):
            build_state_narrator_response(narrator_input, response_text=" ")


class MultiStateChangeCommandNarrationTest(unittest.TestCase):
    def test_training_command_narrates_resource_supply_and_queue_changes(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=25),
            supply=SupplyState(used_supply=8, supply_capacity=15),
            units={"SCV": 6},
            structures={"Command Center": 1, "Barracks": 1},
        )
        result = execute_toycraft_intent(
            TrainArmyIntent(unit_type="Marine", count=3),
            state,
        )

        narrator_input = build_execution_narrator_input(
            result,
            command_text="마린 세 기 생산해",
        )
        response = build_state_narrator_response(narrator_input)

        self.assertTrue(result.executed)
        self.assertEqual(
            (
                "minerals -150",
                "gas -0",
                "used_supply +3",
                "production_queues.Barracks +3",
            ),
            response.metadata.state_changes,
        )
        self.assertIn(
            StateNarratorDelta(
                name="resources.minerals",
                before=300,
                after=150,
                delta=-150,
            ),
            narrator_input.change_summary.resource_deltas,
        )
        self.assertIn(
            StateNarratorDelta(
                name="supply.used_supply",
                before=8,
                after=11,
                delta=3,
            ),
            narrator_input.change_summary.resource_deltas,
        )
        self.assertIn(
            StateNarratorDelta(
                name="production_queues.Barracks",
                before=0,
                after=3,
                delta=3,
            ),
            narrator_input.change_summary.entity_deltas,
        )
        self.assertEqual((), narrator_input.change_summary.map_deltas)
        self.assertIn("생산 명령", response.response_text)
        self.assertIn("Barracks에 Marine 3기 대기열", response.response_text)
        self.assertIn("미네랄 -150", response.response_text)
        self.assertIn("보급 +3", response.response_text)
        self.assertIn("보급은 11/15", response.response_text)

        payload = response.to_dict()

        self.assertTrue(payload["metadata"]["state_changed"])
        self.assertEqual(
            [
                "minerals -150",
                "gas -0",
                "used_supply +3",
                "production_queues.Barracks +3",
            ],
            payload["metadata"]["state_changes"],
        )

    def test_defense_command_narrates_position_damage_and_mitigation_changes(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=300, gas=50),
            supply=SupplyState(used_supply=10, supply_capacity=23),
            units={"SCV": 6, "Marine": 4, "Vulture": 1},
            structures={"Command Center": 1, "Barracks": 1, "Bunker": 1},
            target_damage={"main ramp": 20},
            pressure_mitigation={"main ramp": 5},
        )
        result = execute_toycraft_intent(
            DefendIntent(location="main ramp", unit_group="available combat units"),
            state,
        )

        narrator_input = build_execution_narrator_input(
            result,
            command_text="본진 입구 수비해",
        )
        response = build_state_narrator_response(narrator_input)

        self.assertTrue(result.executed)
        self.assertEqual(
            (
                "unit_positions.Marine -> main ramp",
                "target_damage.main ramp +20",
                "pressure_mitigation.main ramp +20",
                "combat.방어 교전 Marinex4",
            ),
            response.metadata.state_changes,
        )
        self.assertEqual((), narrator_input.change_summary.resource_deltas)
        self.assertIn(
            StateNarratorDelta(
                name="unit_positions.Marine",
                before=0,
                after="main ramp",
            ),
            narrator_input.change_summary.map_deltas,
        )
        self.assertIn(
            StateNarratorDelta(
                name="target_damage.main ramp",
                before=20,
                after=40,
                delta=20,
            ),
            narrator_input.change_summary.map_deltas,
        )
        self.assertIn(
            StateNarratorDelta(
                name="pressure_mitigation.main ramp",
                before=5,
                after=25,
                delta=20,
            ),
            narrator_input.change_summary.map_deltas,
        )
        self.assertIn("방어 명령", response.response_text)
        self.assertIn("Marine을 main ramp로 이동", response.response_text)
        self.assertIn("main ramp 피해 +20", response.response_text)
        self.assertIn("main ramp 압박 완화 +20", response.response_text)
        self.assertIn("현재 전투 위치는 Marine main ramp", response.response_text)

        payload = narrator_input.to_dict()

        self.assertEqual("본진 입구 수비해", payload["command_text"])
        self.assertEqual("DEFEND", payload["intent"])
        self.assertEqual(
            [
                "unit_positions.Marine -> main ramp",
                "target_damage.main ramp +20",
                "pressure_mitigation.main ramp +20",
                "combat.방어 교전 Marinex4",
            ],
            payload["change_summary"]["raw_changes"],
        )
        self.assertEqual(
            {"main ramp": 40},
            payload["after_state"]["target_damage"],
        )
        self.assertEqual(
            {"main ramp": 25},
            payload["after_state"]["pressure_mitigation"],
        )


class SuccessfulCommandNarrationIntegrationTest(unittest.TestCase):
    def test_interpreted_successful_commands_narrate_actions_and_state_changes(self) -> None:
        cases = (
            (
                "미네랄에 일꾼 세 기 붙여",
                "GATHER_RESOURCE",
                (
                    "자원 채취 지시",
                    "미네랄 +24",
                    "작업 중 SCV +3",
                    "현재 자원은 미네랄 474, 가스 75",
                    "가용 SCV는 3기, 작업 중 SCV는 5기",
                ),
                ("minerals +24", "busy_workers +3"),
            ),
            (
                "배럭에서 마린 두 기 찍어",
                "TRAIN_ARMY",
                (
                    "생산 명령",
                    "Barracks에 Marine 2기 대기열",
                    "미네랄 -100",
                    "보급 +2",
                    "보급은 14/23",
                ),
                (
                    "minerals -100",
                    "used_supply +2",
                    "production_queues.Barracks +2",
                ),
            ),
            (
                "본진 입구 수비해",
                "DEFEND",
                (
                    "방어 명령",
                    "Marine을 main ramp로 이동",
                    "main ramp 피해 +20",
                    "main ramp 압박 완화 +20",
                    "현재 전투 위치는 Marine main ramp",
                ),
                (
                    "unit_positions.Marine -> main ramp",
                    "target_damage.main ramp +20",
                    "pressure_mitigation.main ramp +20",
                ),
            ),
            (
                "마린 두 기로 적 미네랄 라인 견제해",
                "HARASS",
                (
                    "견제 명령",
                    "Marine을 enemy mineral line로 이동",
                    "enemy mineral line 피해 +12",
                    "현재 전투 위치는 Marine enemy mineral line",
                ),
                (
                    "unit_positions.Marine -> enemy mineral line",
                    "target_damage.enemy mineral line +12",
                ),
            ),
        )

        for command_text, expected_intent, narration_phrases, state_changes in cases:
            with self.subTest(command_text=command_text):
                payload = interpret_command_text(command_text)
                self.assertIsNotNone(payload)
                state = _successful_demo_state()

                result = execute_toycraft_intent(payload, state)
                response = build_execution_narrator_response(
                    result,
                    command_text=command_text,
                )

                self.assertTrue(result.executed)
                self.assertFalse(result.read_only)
                self.assertEqual(expected_intent, response.metadata.intent)
                self.assertEqual(command_text, response.metadata.command_text)
                self.assertEqual("executed", response.metadata.response_kind)
                self.assertTrue(response.metadata.state_changed)
                self.assertIsNone(response.blocked_command)
                for phrase in narration_phrases:
                    self.assertIn(phrase, response.response_text)
                for state_change in state_changes:
                    self.assertIn(state_change, response.metadata.state_changes)

    def test_additional_successful_commander_results_cover_production_expansion_and_report(
        self,
    ) -> None:
        cases = (
            (
                "본진 입구에 서플라이 디포 지어",
                _successful_demo_state(),
                "BUILD_STRUCTURE",
                "executed",
                (
                    "건설 명령",
                    "main ramp에 Supply Depot 건설을 예약",
                    "미네랄 -100",
                    "완료까지 30초",
                    "현재 자원은 미네랄 350, 가스 75",
                ),
                (
                    "minerals -100",
                    "busy_workers +1",
                    "construction_queue.Supply Depot +1",
                ),
            ),
            (
                "일꾼 계속 찍어",
                _successful_demo_state(),
                "TRAIN_WORKER",
                "executed",
                (
                    "생산 명령",
                    "Command Center에 SCV 1기 대기열",
                    "미네랄 -50",
                    "보급 +1",
                    "보급은 13/23",
                ),
                (
                    "minerals -50",
                    "used_supply +1",
                    "production_queues.Command Center +1",
                ),
            ),
            (
                "앞마당 가져가",
                _expansion_ready_demo_state(),
                "EXPAND",
                "executed",
                (
                    "확장 명령",
                    "natural expansion에 Command Center 건설을 예약",
                    "미네랄 -400",
                    "완료까지 100초",
                    "현재 자원은 미네랄 50, 가스 75",
                ),
                (
                    "minerals -400",
                    "busy_workers +1",
                    "construction_queue.Command Center +1",
                ),
            ),
            (
                "상태 알려줘",
                _successful_demo_state(),
                "SUMMARIZE_STATE",
                "read_only",
                (
                    "현재 상황을 보고합니다",
                    "현재 자원은 미네랄 450, 가스 75",
                    "보급은 12/23",
                    "전투 병력은 Marine 4기, Vulture 1기",
                    "수리 필요 대상은 front bunker",
                ),
                (),
            ),
        )

        for (
            command_text,
            state,
            expected_intent,
            expected_response_kind,
            narration_phrases,
            state_changes,
        ) in cases:
            with self.subTest(command_text=command_text):
                payload = interpret_command_text(command_text)
                self.assertIsNotNone(payload)

                result = execute_toycraft_intent(payload, state)
                response = build_execution_narrator_response(
                    result,
                    command_text=command_text,
                )

                self.assertTrue(result.executed)
                self.assertEqual(expected_response_kind == "read_only", result.read_only)
                self.assertEqual(expected_intent, response.metadata.intent)
                self.assertEqual(command_text, response.metadata.command_text)
                self.assertEqual(expected_response_kind, response.metadata.response_kind)
                self.assertEqual(bool(state_changes), response.metadata.state_changed)
                self.assertIsNone(response.blocked_command)
                for phrase in narration_phrases:
                    self.assertIn(phrase, response.response_text)
                for state_change in state_changes:
                    self.assertIn(state_change, response.metadata.state_changes)


class FailedAndInfeasibleCommandNarrationIntegrationTest(unittest.TestCase):
    def test_interpreted_infeasible_command_narrates_reason_without_state_change(
        self,
    ) -> None:
        command_text = "가스에 일꾼 하나 붙여"
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )
        payload = interpret_command_text(command_text)
        self.assertIsNotNone(payload)

        result = execute_toycraft_intent(payload, state)
        narrator_input = build_execution_narrator_input(
            result,
            command_text=command_text,
        )
        response = build_state_narrator_response(narrator_input)

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual((), result.state_changes)
        self.assertEqual((), result.executed_actions)
        self.assertFalse(result.state_delta.has_changes)
        self.assertEqual(narrator_input.before_state, narrator_input.after_state)
        self.assertFalse(response.metadata.executed)
        self.assertTrue(response.metadata.read_only)
        self.assertFalse(response.metadata.state_changed)
        self.assertEqual("blocked", response.metadata.response_kind)
        self.assertIsNotNone(response.blocked_command)
        self.assertEqual(
            (FeasibilityErrorReason.MISSING_PREREQUISITE,),
            response.blocked_command.reason_codes,
        )
        self.assertIn("실행하지 않았습니다", response.response_text)
        self.assertIn("자원 채취 명령은 상태를 바꾸지 않았습니다", response.response_text)
        self.assertIn("선행 조건이 아직 준비되지 않았습니다", response.response_text)
        self.assertIn("Refinery", response.response_text)
        self.assertIn("추천 행동", response.response_text)
        self.assertIn("현재 상태: 미네랄 50, 가스 0, 보급 4/15", response.response_text)

    def test_rule_execution_failure_narrates_phase_zero_boundary_without_state_change(
        self,
    ) -> None:
        command_text = "SCV 하나로 정찰 보내"
        state = _successful_demo_state()
        payload = interpret_command_text(command_text)
        self.assertIsNotNone(payload)

        result = execute_toycraft_intent(payload, state)
        narrator_input = build_execution_narrator_input(
            result,
            command_text=command_text,
        )
        response = build_state_narrator_response(narrator_input)

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual((), result.state_changes)
        self.assertEqual((), result.executed_actions)
        self.assertFalse(result.state_delta.has_changes)
        self.assertFalse(response.metadata.state_changed)
        self.assertEqual("SCOUT", response.metadata.intent)
        self.assertIsNotNone(response.blocked_command)
        self.assertEqual(
            (FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE,),
            response.blocked_command.reason_codes,
        )
        self.assertIn("정찰 명령은 상태를 바꾸지 않았습니다", response.response_text)
        self.assertIn("Phase 0 ToyCraft 범위를 벗어난 명령입니다", response.response_text)
        self.assertIn("Terran MVP 유닛과 구조물만 사용", response.response_text)
        self.assertIn("추천 행동", response.response_text)

    def test_unsupported_raw_dsl_narrates_rejection_without_state_change(self) -> None:
        state = _successful_demo_state()

        result = execute_toycraft_intent(
            {"intent": "LAUNCH_NUKE", "priority": "urgent", "constraints": []},
            state,
        )
        narrator_input = build_execution_narrator_input(
            result,
            command_text="핵 쏴",
        )
        response = build_state_narrator_response(narrator_input)

        self.assertFalse(result.executed)
        self.assertTrue(result.read_only)
        self.assertEqual(state, result.before_state)
        self.assertEqual(state, result.after_state)
        self.assertEqual((), result.state_changes)
        self.assertEqual((), result.executed_actions)
        self.assertFalse(result.state_delta.has_changes)
        self.assertFalse(response.metadata.state_changed)
        self.assertEqual("LAUNCH_NUKE", response.metadata.intent)
        self.assertIsNotNone(response.blocked_command)
        self.assertEqual(
            (FeasibilityErrorReason.UNSUPPORTED_INTENT,),
            response.blocked_command.reason_codes,
        )
        self.assertIn("LAUNCH_NUKE 명령은 상태를 바꾸지 않았습니다", response.response_text)
        self.assertIn("Phase 0에서 지원하지 않는 명령입니다", response.response_text)
        self.assertIn("자원 채취, 건설, 생산, 정찰", response.response_text)
        self.assertIn("추천 행동", response.response_text)

    def test_representative_failed_command_results_narrate_safe_rejections(
        self,
    ) -> None:
        cases = (
            (
                "핵 쏴",
                {"intent": "LAUNCH_NUKE", "priority": "urgent", "constraints": []},
                _successful_demo_state(),
                "LAUNCH_NUKE",
                (FeasibilityErrorReason.UNSUPPORTED_INTENT,),
                "Intent payload has an unsupported or missing intent.",
                "Use one of the canonical intents",
                (
                    "LAUNCH_NUKE 명령은 상태를 바꾸지 않았습니다",
                    "Phase 0에서 지원하지 않는 명령입니다",
                    "자원 채취, 건설, 생산, 정찰",
                ),
            ),
            (
                "앞마당 입구에 벙커 지어",
                BuildStructureIntent(structure="Bunker", location="natural choke"),
                ToyCraftState(
                    resources=ResourceState(minerals=50, gas=0),
                    units={"SCV": 4},
                    structures={"Command Center": 1, "Supply Depot": 1, "Barracks": 1},
                ),
                "BUILD_STRUCTURE",
                (FeasibilityErrorReason.INSUFFICIENT_MINERALS,),
                "Need 50 more minerals.",
                "Gather more minerals",
                (
                    "건설 명령은 상태를 바꾸지 않았습니다",
                    "미네랄이 부족합니다",
                    "SCV를 미네랄에 더 붙이거나",
                ),
            ),
            (
                "일꾼 하나 더 찍어",
                TrainWorkerIntent(count=1),
                ToyCraftState(
                    resources=ResourceState(minerals=500, gas=0),
                    supply=SupplyState(used_supply=15, supply_capacity=15),
                    units={"SCV": 4},
                    structures={"Command Center": 1},
                ),
                "TRAIN_WORKER",
                (FeasibilityErrorReason.INSUFFICIENT_SUPPLY,),
                "Need 1 more supply.",
                "Build a Supply Depot",
                (
                    "SCV 생산 명령은 상태를 바꾸지 않았습니다",
                    "보급이 막혔습니다",
                    "Supply Depot",
                ),
            ),
            (
                "적 앞마당 수비해",
                DefendIntent(location="enemy natural", unit_group="2 Marines"),
                _successful_demo_state(),
                "DEFEND",
                (FeasibilityErrorReason.INVALID_TARGET,),
                "enemy natural is an enemy target, not a friendly defense location.",
                "Defend a friendly location",
                (
                    "방어 명령은 상태를 바꾸지 않았습니다",
                    "대상이 현재 명령에 맞지 않습니다",
                    "방어는 아군 위치",
                ),
            ),
            (
                "공격하면서 후퇴해",
                SummarizeStateIntent(constraints=("attack and retreat",)),
                _successful_demo_state(),
                "SUMMARIZE_STATE",
                (FeasibilityErrorReason.CONSTRAINT_CONFLICT,),
                "Constraint conflict: cannot satisfy both 'attack' and 'retreat'.",
                "Remove the conflicting constraint",
                (
                    "상태 보고 명령은 상태를 바꾸지 않았습니다",
                    "조건이 서로 충돌합니다",
                    "조건을 제거하거나 명령을 둘로 나눠",
                ),
            ),
        )

        for (
            command_text,
            payload,
            state,
            expected_intent,
            expected_reason_codes,
            expected_specific_reason,
            expected_actionable_alternative,
            expected_phrases,
        ) in cases:
            with self.subTest(command_text=command_text):
                result = execute_toycraft_intent(payload, state)
                narrator_input = build_execution_narrator_input(
                    result,
                    command_text=command_text,
                )
                response = build_state_narrator_response(narrator_input)

                self.assertFalse(result.executed)
                self.assertTrue(result.read_only)
                self.assertEqual(state, result.before_state)
                self.assertEqual(state, result.after_state)
                self.assertEqual((), result.state_changes)
                self.assertEqual((), result.executed_actions)
                self.assertFalse(result.state_delta.has_changes)
                self.assertEqual(narrator_input.before_state, narrator_input.after_state)
                self.assertEqual("blocked", response.metadata.response_kind)
                self.assertEqual(expected_intent, response.metadata.intent)
                self.assertEqual(command_text, response.metadata.command_text)
                self.assertFalse(response.metadata.executed)
                self.assertTrue(response.metadata.read_only)
                self.assertFalse(response.metadata.state_changed)
                self.assertEqual(expected_reason_codes, response.metadata.reason_codes)
                self.assertIsNotNone(response.blocked_command)
                self.assertEqual(
                    expected_reason_codes,
                    response.blocked_command.reason_codes,
                )
                self.assertEqual(expected_specific_reason, response.blocked_command.reason)
                self.assertEqual(
                    expected_specific_reason,
                    response.blocked_command.issues[0].message,
                )
                self.assertTrue(response.blocked_command.reason.strip())
                self.assertTrue(response.blocked_command.alternative.strip())
                self.assertIn(
                    expected_actionable_alternative,
                    response.blocked_command.alternative,
                )
                self.assertIn(
                    f"세부 판정: {expected_specific_reason}",
                    response.response_text,
                )
                self.assertIn("실행하지 않았습니다", response.response_text)
                self.assertIn("추천 행동", response.response_text)
                self.assertIn("현재 상태:", response.response_text)
                for phrase in expected_phrases:
                    self.assertIn(phrase, response.response_text)


def _successful_demo_state() -> ToyCraftState:
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
        claimed_locations=("main", "main base"),
        damaged_targets=("front bunker",),
    )


def _expansion_ready_demo_state() -> ToyCraftState:
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
        claimed_locations=("main", "main base"),
        damaged_targets=("front bunker",),
    )


if __name__ == "__main__":
    unittest.main()
