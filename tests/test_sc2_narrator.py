import json
import pathlib
import subprocess
import sys
import unittest

from starcraft_commander.contracts import (
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.narrator import (
    DEFAULT_SC2_NARRATOR,
    SC2_NARRATION_STATUSES,
    SC2KoreanNarrator,
    SC2NarrationResponse,
    SC2NarratorInterface,
    narrate_sc2_plan_result,
    narrate_sc2_state,
    render_sc2_state_lines,
)
from starcraft_commander.state_resolver import SC2CommanderState


def _action(
    action_type: SC2ActionType,
    subject: str,
    target: str = "",
    count: int = 1,
) -> SC2CommandAction:
    return SC2CommandAction(
        action_type=action_type,
        subject=subject,
        target=target,
        count=count,
    )


def build_full_action_plan() -> SC2ExecutionPlan:
    """One plan covering every public semantic SC2 action type."""

    return SC2ExecutionPlan(
        intent_name="DEFEND",
        ordered_actions=(
            _action(SC2ActionType.ASSIGN_WORKERS, "SCV", "minerals", 8),
            _action(SC2ActionType.TRAIN_UNIT, "MARINE", count=6),
            _action(SC2ActionType.BUILD_STRUCTURE, "SUPPLYDEPOT", "self_main"),
            _action(SC2ActionType.MOVE_GROUP, "Marine", "self_ramp", 4),
            _action(SC2ActionType.ATTACK_MOVE, "MARINE", "enemy_natural", 6),
            _action(SC2ActionType.REPAIR, "SCV", "Bunker", 2),
            _action(SC2ActionType.OBSERVE, "visible_state", "narrator_snapshot", 0),
        ),
    )


def build_observe_plan() -> SC2ExecutionPlan:
    return SC2ExecutionPlan(
        intent_name="SUMMARIZE_STATE",
        ordered_actions=(
            _action(SC2ActionType.OBSERVE, "visible_state", "narrator_snapshot", 0),
        ),
    )


def full_success_result(plan: SC2ExecutionPlan, audit: dict | None = None) -> SC2PlanExecutionResult:
    return SC2PlanExecutionResult(
        plan=plan,
        attempted_actions=plan.ordered_actions,
        applied_actions=plan.ordered_actions,
        audit=audit or {},
    )


def build_commander_state() -> SC2CommanderState:
    return SC2CommanderState(
        minerals=400,
        vespene=125,
        supply_used=30,
        supply_cap=39,
        supply_left=9,
        own_units={"SCV": 12, "MARINE": 6},
        own_structures={"BARRACKS": 2, "COMMANDCENTER": 2, "SUPPLYDEPOT": 3},
        structures_in_progress={"SUPPLYDEPOT": 1},
        visible_enemy_units={"ZERGLING": 4},
        visible_enemy_structures={"HATCHERY": 1},
        idle_worker_count=2,
        army_count=6,
        game_loop=672,
        game_time_seconds=30.0,
    )


class FakeFeasibilityResult:
    """Duck-typed SC2FeasibilityResult-shaped object; no real import needed."""

    def __init__(self) -> None:
        self.reasons = ("미네랄이 부족합니다.", "배럭이 아직 없습니다.")
        self.alternative = "보급고와 배럭부터 건설해 주세요."
        self.intent_name = "TRAIN_ARMY"


class SC2NarrationResponseContractTest(unittest.TestCase):
    def test_rejects_empty_response_text(self) -> None:
        for bad_text in ("", "   ", "\n\t"):
            with self.subTest(bad_text=bad_text):
                with self.assertRaises(ValueError):
                    SC2NarrationResponse(response_text=bad_text, status="executed")

    def test_rejects_unknown_status(self) -> None:
        for bad_status in ("done", "EXECUTED", "partial", ""):
            with self.subTest(bad_status=bad_status):
                with self.assertRaises(ValueError):
                    SC2NarrationResponse(response_text="텍스트", status=bad_status)

    def test_accepts_every_public_status(self) -> None:
        for status in sorted(SC2_NARRATION_STATUSES):
            with self.subTest(status=status):
                response = SC2NarrationResponse(response_text="텍스트", status=status)
                self.assertEqual(response.status, status)

    def test_detail_lines_coerced_to_string_tuple(self) -> None:
        response = SC2NarrationResponse(
            response_text="텍스트",
            status="executed",
            detail_lines=["줄 1", 2],
        )
        self.assertEqual(response.detail_lines, ("줄 1", "2"))

    def test_to_dict_is_json_ready(self) -> None:
        response = SC2NarrationResponse(
            response_text="텍스트",
            status="read_only",
            intent_name="SUMMARIZE_STATE",
            detail_lines=("줄 1",),
        )
        payload = response.to_dict()
        round_trip = json.loads(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(round_trip, payload)
        self.assertEqual(
            set(payload),
            {"response_text", "status", "intent_name", "detail_lines"},
        )
        self.assertEqual(payload["detail_lines"], ["줄 1"])


class SC2KoreanNarratorPlanResultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.narrator = SC2KoreanNarrator()

    def test_full_success_mentions_every_action_in_korean(self) -> None:
        result = full_success_result(build_full_action_plan())
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "executed")
        self.assertEqual(response.intent_name, "DEFEND")
        self.assertIn("명령을 실행했습니다", response.response_text)
        self.assertEqual(len(response.detail_lines), 7)

        expected_fragments = (
            "일꾼 8기를 미네랄 채취에 배정",
            "마린 6기 생산 명령",
            "보급고 건설 시작 (본진)",
            "마린 그룹을 본진 입구로 이동",
            "마린 그룹이 적 앞마당으로 공격 이동",
            "SCV 2기가 벙커 수리",
            "전장 상태 확인",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, response.response_text)

    def test_partial_result_is_clearly_flagged(self) -> None:
        plan = SC2ExecutionPlan(
            intent_name="BUILD_STRUCTURE",
            ordered_actions=(
                _action(SC2ActionType.TRAIN_UNIT, "MARINE", count=6),
                _action(SC2ActionType.BUILD_STRUCTURE, "SUPPLYDEPOT", "self_main"),
            ),
        )
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.ordered_actions,
            applied_actions=(plan.ordered_actions[0],),
            skipped_actions=(plan.ordered_actions[1],),
            errors=(
                SC2ExecutionError(
                    message="not enough minerals",
                    action_type=SC2ActionType.BUILD_STRUCTURE,
                    action_index=1,
                    exception_type="ValueError",
                ),
            ),
        )
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "partially_executed")
        self.assertNotEqual(response.status, "executed")
        for fragment in (
            "일부만 실행되었습니다",
            "마린 6기 생산 명령",
            "보급고 건설 시작 (본진)",
            "not enough minerals",
            "실행:",
            "보류:",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, response.response_text)

    def test_blocked_result_lists_every_error(self) -> None:
        plan = SC2ExecutionPlan(
            intent_name="HARASS",
            ordered_actions=(
                _action(SC2ActionType.ATTACK_MOVE, "HELLION", "enemy_mineral_line", 4),
                _action(SC2ActionType.MOVE_GROUP, "MARINE", "enemy_ramp", 2),
            ),
        )
        errors = (
            SC2ExecutionError(
                message=(
                    "bot runtime adapter implements neither 'attack_move' "
                    "nor 'execute_commander_action'."
                ),
                action_type=SC2ActionType.ATTACK_MOVE,
                action_index=0,
                exception_type="MissingBotCapability",
                metadata={"expected_method": "attack_move"},
            ),
            SC2ExecutionError(
                message="pathing service exploded",
                action_type=SC2ActionType.MOVE_GROUP,
                action_index=1,
                exception_type="RuntimeError",
            ),
        )
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.ordered_actions,
            applied_actions=(),
            skipped_actions=plan.ordered_actions,
            errors=errors,
        )
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "blocked")
        self.assertIn("실행기에 해당 기능이 연결되지 않았습니다", response.response_text)
        self.assertIn("attack_move", response.response_text)
        for error in errors:
            with self.subTest(error_message=error.message):
                self.assertIn(error.message, response.response_text)

    def test_missing_runtime_adapter_explains_missing_game_connection(self) -> None:
        plan = build_full_action_plan()
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=(),
            applied_actions=(),
            skipped_actions=plan.ordered_actions,
            errors=(
                SC2ExecutionError(
                    message=(
                        "SC2 runtime adapter has not been bound to a "
                        "BotAI-like object."
                    ),
                    exception_type="MissingRuntimeAdapter",
                ),
            ),
        )
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "blocked")
        self.assertIn("게임 연결이 없습니다", response.response_text)
        self.assertIn("다시 시도해 주세요", response.response_text)

    def test_nothing_applied_without_errors_is_still_blocked(self) -> None:
        plan = build_observe_plan()
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.ordered_actions,
            applied_actions=(),
            skipped_actions=plan.ordered_actions,
        )
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "blocked")
        self.assertTrue(response.response_text.strip())
        self.assertIn("이유:", response.response_text)

    def test_observe_only_success_renders_state_from_audit(self) -> None:
        state = build_commander_state()
        for audit_label, audit in (
            ("flat_payload", {"observations": {"0": state.to_dict()}}),
            ("nested_payload", {"observations": {"0": {"state": state.to_dict()}}}),
        ):
            with self.subTest(audit=audit_label):
                result = full_success_result(build_observe_plan(), audit=audit)
                response = self.narrator.narrate_plan_result(result)

                self.assertEqual(response.status, "read_only")
                self.assertEqual(response.intent_name, "SUMMARIZE_STATE")
                for fragment in (
                    "미네랄 400",
                    "가스 125",
                    "보급 30/39",
                    "여유 9",
                    "마린 6기",
                ):
                    self.assertIn(fragment, response.response_text)

    def test_observe_only_success_without_observations_stays_read_only(self) -> None:
        result = full_success_result(build_observe_plan())
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "read_only")
        self.assertIn("전장 상태를 확인했습니다", response.response_text)
        self.assertTrue(response.response_text.strip())


class SC2KoreanNarratorHonestyTest(unittest.TestCase):
    """Partial issuance, constraint disclosure, and Korean group subjects."""

    def setUp(self) -> None:
        self.narrator = SC2KoreanNarrator()

    def test_partial_issuance_is_narrated_with_issued_counts(self) -> None:
        plan = SC2ExecutionPlan(
            intent_name="GATHER_RESOURCE",
            ordered_actions=(
                _action(SC2ActionType.ASSIGN_WORKERS, "SCV", "minerals", 6),
            ),
        )
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.ordered_actions,
            applied_actions=plan.ordered_actions,
            errors=(
                SC2ExecutionError(
                    message="only 2 of 6 requested orders were issued",
                    action_type=SC2ActionType.ASSIGN_WORKERS,
                    action_index=0,
                    exception_type="PartialActionApplication",
                    metadata={"requested_count": 6, "issued_count": 2},
                ),
            ),
            audit={
                "action_reports": {
                    "0": {
                        "applied": True,
                        "requested_count": 6,
                        "issued_count": 2,
                        "is_partial": True,
                        "detail": "insufficient_workers",
                    }
                }
            },
        )
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "partially_executed")
        self.assertIn("일꾼 6기 중 2기만", response.response_text)
        self.assertIn("부분 실행", response.response_text)
        self.assertNotIn("일꾼 6기를", response.response_text)

    def test_partial_group_order_renders_issued_count_and_korean_subject(self) -> None:
        plan = SC2ExecutionPlan(
            intent_name="DEFEND",
            ordered_actions=(
                _action(SC2ActionType.ATTACK_MOVE, "6 Marines", "self_ramp"),
            ),
        )
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.ordered_actions,
            applied_actions=plan.ordered_actions,
            errors=(
                SC2ExecutionError(
                    message="only 2 of 6 requested orders were issued",
                    action_type=SC2ActionType.ATTACK_MOVE,
                    action_index=0,
                    exception_type="PartialActionApplication",
                    metadata={"requested_count": 6, "issued_count": 2},
                ),
            ),
            audit={
                "action_reports": {
                    "0": {
                        "applied": True,
                        "requested_count": 6,
                        "issued_count": 2,
                        "is_partial": True,
                        "detail": "insufficient_units",
                    }
                }
            },
        )
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "partially_executed")
        self.assertIn("마린 6기 중 2기만", response.response_text)
        self.assertNotIn("Marines", response.response_text)

    def test_refused_action_error_renders_korean_reason(self) -> None:
        plan = SC2ExecutionPlan(
            intent_name="DEFEND",
            ordered_actions=(
                _action(SC2ActionType.ATTACK_MOVE, "6 Marines", "self_ramp"),
            ),
        )
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.ordered_actions,
            applied_actions=(),
            skipped_actions=plan.ordered_actions,
            errors=(
                SC2ExecutionError(
                    message="action 'attack_move' was refused",
                    action_type=SC2ActionType.ATTACK_MOVE,
                    action_index=0,
                    exception_type="ActionRefused",
                    metadata={"detail": "insufficient_units"},
                ),
            ),
        )
        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "blocked")
        self.assertIn("요청한 유닛 그룹에 해당하는 아군 유닛이 없습니다", response.response_text)

    def test_refused_action_error_hides_raw_location_registry(self) -> None:
        plan = SC2ExecutionPlan(
            intent_name="BUILD_STRUCTURE",
            ordered_actions=(
                _action(SC2ActionType.BUILD_STRUCTURE, "SUPPLYDEPOT", "self_main"),
            ),
        )
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.ordered_actions,
            applied_actions=(),
            skipped_actions=plan.ordered_actions,
            errors=(
                SC2ExecutionError(
                    message=(
                        "unsupported SC2 target location: 'main base near ramp'. "
                        "Supported targets: self_main, self_ramp."
                    ),
                    action_type=SC2ActionType.BUILD_STRUCTURE,
                    action_index=0,
                    exception_type="ActionRefused",
                    metadata={
                        "detail": (
                            "unsupported SC2 target location: 'main base near ramp'. "
                            "Supported targets: self_main, self_ramp."
                        )
                    },
                ),
            ),
        )

        response = self.narrator.narrate_plan_result(result)

        self.assertEqual(response.status, "blocked")
        self.assertIn("위치 표현을 현재 지도 의미 좌표로 연결하지 못했습니다", response.response_text)
        self.assertNotIn("unsupported SC2 target location", response.response_text)
        self.assertNotIn("Supported targets", response.response_text)

    def test_unenforced_constraint_downgrades_success_with_disclosure(self) -> None:
        # Without standing-order support (the DEFAULT narrator) the
        # continuous-production constraint is honestly disclosed as dropped.
        plan = SC2ExecutionPlan(
            intent_name="TRAIN_WORKER",
            constraints=("keep SCV production continuous",),
            ordered_actions=(_action(SC2ActionType.TRAIN_UNIT, "SCV", count=1),),
        )
        result = full_success_result(plan)
        for label, narrator in (
            ("fresh default", self.narrator),
            ("module default", DEFAULT_SC2_NARRATOR),
            ("explicit empty set", SC2KoreanNarrator(enforced_constraints=frozenset())),
        ):
            with self.subTest(narrator=label):
                response = narrator.narrate_plan_result(result)
                self.assertEqual(response.status, "partially_executed")
                self.assertIn("SCV 1기 생산 명령", response.response_text)
                self.assertIn("지속 생산은 아직 지원되지 않아", response.response_text)

    def test_enforced_constraint_narrates_full_execution(self) -> None:
        # Once a standing-order controller genuinely enforces continuous
        # production, the disclosure would itself be dishonest: the
        # enforced-constraints narrator narrates plain full execution.
        plan = SC2ExecutionPlan(
            intent_name="TRAIN_WORKER",
            constraints=("keep SCV production continuous",),
            ordered_actions=(_action(SC2ActionType.TRAIN_UNIT, "SCV", count=1),),
        )
        narrator = SC2KoreanNarrator(
            enforced_constraints=frozenset({"keep SCV production continuous"})
        )
        response = narrator.narrate_plan_result(full_success_result(plan))

        self.assertEqual(response.status, "executed")
        self.assertIn("명령을 실행했습니다", response.response_text)
        self.assertIn("SCV 1기 생산 명령", response.response_text)
        self.assertNotIn("지속 생산은 아직 지원되지 않아", response.response_text)

    def test_enforced_constraints_only_cover_listed_constraints(self) -> None:
        # An unrelated enforced constraint must not suppress the disclosure.
        plan = SC2ExecutionPlan(
            intent_name="TRAIN_WORKER",
            constraints=("keep SCV production continuous",),
            ordered_actions=(_action(SC2ActionType.TRAIN_UNIT, "SCV", count=1),),
        )
        narrator = SC2KoreanNarrator(
            enforced_constraints=frozenset({"prevent supply block"})
        )
        response = narrator.narrate_plan_result(full_success_result(plan))

        self.assertEqual(response.status, "partially_executed")
        self.assertIn("지속 생산은 아직 지원되지 않아", response.response_text)

    def test_default_narrator_enforces_no_constraints(self) -> None:
        self.assertEqual(frozenset(), DEFAULT_SC2_NARRATOR.enforced_constraints)
        self.assertEqual(frozenset(), SC2KoreanNarrator().enforced_constraints)

    def test_enforced_constraints_rejects_single_string(self) -> None:
        # A bare string would be iterated character by character — reject it
        # loudly instead of silently enforcing nothing.
        with self.assertRaises(TypeError):
            SC2KoreanNarrator(enforced_constraints="keep SCV production continuous")

    def test_enforced_constraints_coerced_to_frozenset_of_strings(self) -> None:
        narrator = SC2KoreanNarrator(
            enforced_constraints=["keep SCV production continuous"]
        )
        self.assertEqual(
            frozenset({"keep SCV production continuous"}),
            narrator.enforced_constraints,
        )

    def test_plain_constraints_do_not_downgrade_success(self) -> None:
        plan = SC2ExecutionPlan(
            intent_name="TRAIN_ARMY",
            constraints=("train requested combat unit",),
            ordered_actions=(_action(SC2ActionType.TRAIN_UNIT, "MARINE", count=2),),
        )
        response = self.narrator.narrate_plan_result(full_success_result(plan))

        self.assertEqual(response.status, "executed")

    def test_group_subjects_are_rendered_in_korean(self) -> None:
        cases = {
            "6 Marines": "마린 6기",
            "1 Marine": "마린 1기",
            "2 Marines": "마린 2기",
            "1 SCV": "SCV 1기",
            "Marines": "마린 전 병력",
            "available combat units": "전투 가능 병력",
            "MARINE": "마린",
        }
        for subject, expected_korean in cases.items():
            with self.subTest(subject=subject):
                plan = SC2ExecutionPlan(
                    intent_name="DEFEND",
                    ordered_actions=(
                        _action(SC2ActionType.ATTACK_MOVE, subject, "self_ramp"),
                    ),
                )
                response = self.narrator.narrate_plan_result(
                    full_success_result(plan)
                )
                self.assertEqual(response.status, "executed")
                self.assertIn(expected_korean, response.response_text)
                self.assertNotIn("Marines", response.response_text)
                self.assertNotIn("available combat units", response.response_text)


class SC2KoreanNarratorStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.narrator = SC2KoreanNarrator()

    def test_state_summary_covers_every_section(self) -> None:
        response = self.narrator.narrate_state(build_commander_state())

        self.assertEqual(response.status, "read_only")
        expected_fragments = (
            "미네랄 400",
            "가스 125",
            "보급 30/39",
            "여유 9",
            "일꾼 12기",
            "유휴 2기",
            "마린 6기",
            "사령부 2동",
            "배럭 2동",
            "보급고 3동",
            "건설 중",
            "보급고 1동",
            "저글링 4기",
            "해처리 1동",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, response.response_text)
        self.assertNotIn("정찰 정보가 불완전", response.response_text)

    def test_incomplete_observation_adds_warning(self) -> None:
        state = SC2CommanderState(
            minerals=50,
            observation_notes=("bot.vespene is missing; defaulted to 0.",),
        )
        response = self.narrator.narrate_state(state)

        self.assertEqual(response.status, "read_only")
        self.assertIn("정찰 정보가 불완전합니다", response.response_text)

    def test_empty_state_uses_korean_empty_markers(self) -> None:
        response = self.narrator.narrate_state(SC2CommanderState())

        for fragment in ("병력 없음", "건물 없음", "발견된 적 없음"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, response.response_text)

    def test_render_sc2_state_lines_match_detail_lines(self) -> None:
        state = build_commander_state()
        response = self.narrator.narrate_state(state)
        self.assertEqual(response.detail_lines, render_sc2_state_lines(state))


class SC2KoreanNarratorRejectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.narrator = SC2KoreanNarrator()

    def test_fake_feasibility_result_includes_reasons_and_alternative(self) -> None:
        fake = FakeFeasibilityResult()
        response = self.narrator.narrate_rejection(fake)

        self.assertEqual(response.status, "blocked")
        self.assertEqual(response.intent_name, "TRAIN_ARMY")
        self.assertIn("실행하지 않았습니다", response.response_text)
        self.assertIn("이유:", response.response_text)
        self.assertIn("대안:", response.response_text)
        self.assertIn(fake.alternative, response.response_text)
        for reason in fake.reasons:
            with self.subTest(reason=reason):
                self.assertIn(reason, response.response_text)

    def test_plain_string_rejection_gets_default_alternative(self) -> None:
        response = self.narrator.narrate_rejection("지원하지 않는 명령입니다.")

        self.assertEqual(response.status, "blocked")
        self.assertIn("지원하지 않는 명령입니다.", response.response_text)
        self.assertIn("이유:", response.response_text)
        self.assertIn("대안:", response.response_text)

    def test_shapeless_object_still_yields_actionable_rejection(self) -> None:
        response = self.narrator.narrate_rejection(object())

        self.assertEqual(response.status, "blocked")
        self.assertIn("이유:", response.response_text)
        self.assertIn("대안:", response.response_text)


class SC2NarratorModuleSurfaceTest(unittest.TestCase):
    def test_default_narrator_satisfies_protocol(self) -> None:
        for narrator in (DEFAULT_SC2_NARRATOR, SC2KoreanNarrator()):
            with self.subTest(narrator=type(narrator).__name__):
                self.assertIsInstance(narrator, SC2NarratorInterface)

    def test_module_convenience_functions_delegate_to_default(self) -> None:
        result = full_success_result(build_full_action_plan())
        state = build_commander_state()

        plan_response = narrate_sc2_plan_result(result)
        state_response = narrate_sc2_state(state)

        self.assertEqual(plan_response, DEFAULT_SC2_NARRATOR.narrate_plan_result(result))
        self.assertEqual(state_response, DEFAULT_SC2_NARRATOR.narrate_state(state))

    def test_every_narration_response_holds_public_invariants(self) -> None:
        responses = {
            "full_success": narrate_sc2_plan_result(
                full_success_result(build_full_action_plan())
            ),
            "read_only": narrate_sc2_plan_result(
                full_success_result(
                    build_observe_plan(),
                    audit={"observations": {"0": build_commander_state().to_dict()}},
                )
            ),
            "state": narrate_sc2_state(build_commander_state()),
            "rejection": DEFAULT_SC2_NARRATOR.narrate_rejection("이번 명령은 모호합니다."),
        }
        for label, response in responses.items():
            with self.subTest(response=label):
                self.assertIsInstance(response, SC2NarrationResponse)
                self.assertTrue(response.response_text.strip())
                self.assertIn(response.status, SC2_NARRATION_STATUSES)
                self.assertIsInstance(response.detail_lines, tuple)
                payload = response.to_dict()
                self.assertEqual(
                    json.loads(json.dumps(payload, ensure_ascii=False)),
                    payload,
                )

    def test_import_does_not_require_sc2_or_toycraft(self) -> None:
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        code = (
            "import sys\n"
            "import starcraft_commander.narrator\n"
            "banned = [name for name in sys.modules if name == 'sc2' "
            "or name.startswith('sc2.') or name == 'toycraft_commander' "
            "or name.startswith('toycraft_commander.')]\n"
            "assert not banned, banned\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
