"""Handoff Step 5 acceptance tests for the live SC2 command pipeline.

These tests run without StarCraft II, python-sc2, faster-whisper, or
sounddevice installed. The runtime is a pure-Python recording fake BotAI wired
through the real adapter, executor, validator, planner, interpreter, and
narrator components.
"""

import json
import subprocess
import sys
import unittest
from types import SimpleNamespace

from starcraft_commander.contracts import SC2ExecutionPlan, SC2PlanExecutionResult
from starcraft_commander.event_memory import CommanderEventMemory
from starcraft_commander.feasibility import DEFAULT_SC2_FEASIBILITY_VALIDATOR
from starcraft_commander.live_pipeline import (
    SC2_COMMAND_OUTCOME_STATUSES,
    SC2CommandOutcome,
    SC2CommandSession,
    is_compound_or_macro_intent,
    process_commander_text,
    split_compound_command,
)
from starcraft_commander.narrator import SC2KoreanNarrator
from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
from starcraft_commander.sc2_executor import DEFAULT_SC2_ACTION_PLANNER, SC2RuntimeExecutor
from starcraft_commander.standing_orders import (
    CONSTRAINT_TO_STANDING_ORDER,
    StandingOrderController,
)
from toycraft_commander.interpreter import (
    DEFAULT_COMMAND_INTERPRETER,
    MOVE_CAMERA_CHOKE_TARGET_SLOT,
    MOVE_CAMERA_ENEMY_ENTRANCE_TARGET_SLOT,
    MOVE_CAMERA_LAST_SEEN_ENEMY_AREA_TARGET_SLOT,
    MOVE_CAMERA_NATURAL_EXPANSION_TARGET_SLOT,
    MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT,
    MOVE_CAMERA_SCOUT_LOCATION_TARGET_SLOT,
    MOVE_CAMERA_THIRD_BASE_TARGET_SLOT,
    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
    UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
)
from toycraft_commander.failure import build_parsing_failure_report
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    MoveCameraIntent,
    ScoutIntent,
)


MVP_COMPOUND_COMMAND = "마린 6기 입구로 보내고 SCV 계속 찍어"

KOREAN_QNA_CONTEXT_FIXTURES = (
    {
        "category": "state_summary",
        "question": "지금 어떻게 되어있지?",
        "intent": "SUMMARIZE_STATE",
        "topic": None,
        "fragments": ("전장 상태", "미네랄 275", "보급"),
    },
    {
        "category": "next_action",
        "question": "지금 뭐 해야 해?",
        "intent": "ANSWER_QUESTION",
        "topic": "next_action_help",
        "fragments": ("현재 관측", "미네랄 275", "유휴 SCV", "추천 흐름", "읽기 전용"),
    },
    {
        "category": "failure_reason",
        "question": "왜 안돼?",
        "intent": "ANSWER_QUESTION",
        "topic": "failure_reason_help",
        "fragments": (
            "현재 관측",
            "현재 막힐 가능성이 큰 이유",
            "보급이 막힘",
            "최근 실패 기록은 없어서",
            "읽기 전용",
        ),
    },
    {
        "category": "targeting_location",
        "question": "어디를 대상으로 지정할 수 있어?",
        "intent": "ANSWER_QUESTION",
        "topic": "building_location_help",
        "fragments": (
            "semantic target",
            "선택 유닛: Marine 1",
            "보이는 적: ZERGLING 1, HATCHERY 1",
            "본진(self_main",
            "읽기 전용",
        ),
    },
    {
        "category": "townhall_state",
        "question": "사령부 상태 알려줘",
        "intent": "ANSWER_QUESTION",
        "topic": "townhall_state_help",
        "fragments": (
            "현재 사령부/기지 상태",
            "후보 1개",
            "본진 사령부(30.0, 30.0)",
            "완성 사령부 1",
            "읽기 전용",
        ),
    },
    {
        "category": "camera",
        "question": "카메라 움직일 수 있어?",
        "intent": "ANSWER_QUESTION",
        "topic": "camera_help",
        "fragments": (
            "MOVE_CAMERA",
            "현재 카메라: (44.0, 48.0)",
            "카메라 API: 현재 런타임에서 MOVE_CAMERA 실행을 지원",
            "semantic target",
            "읽기 전용",
        ),
    },
    {
        "category": "voice",
        "question": "음성지원도 되나?",
        "intent": "ANSWER_QUESTION",
        "topic": "voice_help",
        "fragments": ("음성 입력", "--voice", "마이크 권한"),
    },
    {
        "category": "llm",
        "question": "llm이랑 대화 가능?",
        "intent": "ANSWER_QUESTION",
        "topic": "llm_help",
        "fragments": (
            "LLM-first 대화형 입력",
            "지원 명령 예시",
            "지원 질문 예시",
            "semantic target",
            "읽기 전용",
        ),
    },
    {
        "category": "capability",
        "question": "어떤 명령을 할 수 있어?",
        "intent": "ANSWER_QUESTION",
        "topic": "capability_help",
        "fragments": ("지원 명령 예시", "지원 질문 예시", "확인 질문이 필요한 경우", "읽기 전용"),
    },
    {
        "category": "meta",
        "question": "너는 뭐 하는 봇이야?",
        "intent": "ANSWER_QUESTION",
        "topic": "commander_meta_help",
        "fragments": ("LLM-first StarCraft 커맨더", "안전 계층", "지원 명령 예시"),
    },
    {
        "category": "cancel",
        "question": "취소",
        "intent": "ANSWER_QUESTION",
        "topic": "cancel_help",
        "fragments": ("취소 명령은 아직", "게임 액션을 내지 않습니다", "CANCEL 액션"),
    },
)

GENERIC_QNA_FAILURE_FRAGMENTS = (
    "10개 MVP",
    "LLM 해석에 실패",
    "지원하지 않는 명령입니다",
    "다시 말해 주세요",
    "시스템 프롬프트",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "sk-",
    "test-key",
)

KOREAN_BUILD_EXAMPLE_COMMAND_FIXTURES = (
    {
        "command_text": "본진 입구에 보급고 지어",
        "payload": BuildStructureIntent(
            priority="normal",
            constraints=("construct requested Terran structure",),
            structure="Supply Depot",
            location="main ramp",
            placement_policy={
                "anchor": "main ramp",
                "anchor_target": "self_ramp",
                "spatial_relation": "near",
                "source": "deterministic_fixture",
                "source_text": "본진입구에보급고지어",
            },
        ),
        "anchor_target": "self_ramp",
        "spatial_relation": "near",
        "type_id": "SUPPLYDEPOT",
        "point": (38.0, 33.0),
    },
    {
        "command_text": "앞마당에 사령부 지어",
        "payload": BuildStructureIntent(
            priority="normal",
            constraints=("construct requested Terran structure",),
            structure="Command Center",
            location="natural expansion",
            placement_policy={
                "anchor": "natural expansion",
                "anchor_target": "self_natural",
                "spatial_relation": "near",
                "source": "deterministic_fixture",
                "source_text": "앞마당에사령부지어",
                "base_selection": {
                    "selector": "natural",
                    "label": "natural expansion",
                    "target": "self_natural",
                    "location": "natural expansion",
                    "source": "deterministic_fixture",
                    "source_text": "앞마당에사령부지어",
                    "confidence": 1.0,
                },
            },
        ),
        "anchor_target": "self_natural",
        "spatial_relation": "near",
        "type_id": "COMMANDCENTER",
        "point": (45.0, 52.0),
    },
    {
        "command_text": "본진 가스에 정제소 지어",
        "payload": BuildStructureIntent(
            priority="high",
            constraints=("construct requested Terran structure",),
            structure="Refinery",
            location="main geyser",
            placement_policy={
                "anchor": "main geyser",
                "anchor_target": "self_geyser",
                "spatial_relation": "on",
                "source": "deterministic_fixture",
                "source_text": "본진가스에정제소지어",
            },
        ),
        "anchor_target": "self_geyser",
        "spatial_relation": "on",
        "type_id": "REFINERY",
        "geyser": (36.0, 24.0),
    },
    {
        "command_text": "본진에서 떨어진 곳에 보급고 지어",
        "payload": BuildStructureIntent(
            priority="normal",
            constraints=("construct requested Terran structure",),
            structure="Supply Depot",
            location="natural expansion",
            placement_policy={
                "anchor": "main base",
                "anchor_target": "self_main",
                "spatial_relation": "far_from",
                "source": "deterministic_fixture",
                "source_text": "본진에서떨어진곳에보급고지어",
                "base_selection": {
                    "selector": "main",
                    "label": "main base",
                    "target": "self_main",
                    "location": "main base",
                    "source": "deterministic_fixture",
                    "source_text": "본진에서떨어진곳에보급고지어",
                    "confidence": 1.0,
                },
            },
        ),
        "anchor_target": "self_main",
        "spatial_relation": "far_from",
        "type_id": "SUPPLYDEPOT",
        "point": (31.69001047392453, 32.47868202842264),
    },
)


class FakePoint:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)


class FakeUnit:
    def __init__(self, name, x=0.0, y=0.0, *, is_idle=True, is_ready=True):
        self.name = name
        self.position = FakePoint(x, y)
        self.is_idle = is_idle
        self.is_ready = is_ready
        self.issued_orders = []

    def _record(self, kind, payload):
        if hasattr(payload, "x") and hasattr(payload, "y"):
            payload = (float(payload.x), float(payload.y))
        self.issued_orders.append((kind, payload))
        return (kind, self.name, payload)

    def gather(self, target):
        return self._record("gather", target)

    def move(self, point):
        return self._record("move", point)

    def attack(self, point):
        return self._record("attack", point)

    def repair(self, target):
        return self._record("repair", target)

    def train(self, type_id):
        return self._record("train", type_id)


class FakeUnitGroup(list):
    @property
    def idle(self):
        return FakeUnitGroup(unit for unit in self if getattr(unit, "is_idle", False))

    @property
    def ready(self):
        return FakeUnitGroup(unit for unit in self if getattr(unit, "is_ready", False))


class LivePipelineFakeBot:
    """Recording BotAI fake with a complete observation and map surface."""

    def __init__(
        self,
        *,
        minerals=400,
        supply_left=1,
        workers=12,
        marines=0,
        selected_units=(),
        enemy_units=(),
        enemy_structures=(),
        camera_position=None,
        supports_camera=False,
        supports_build=False,
    ):
        self.start_location = FakePoint(30.0, 30.0)
        self.enemy_start_locations = [FakePoint(130.0, 130.0)]
        self.main_base_ramp = SimpleNamespace(top_center=FakePoint(38.0, 36.0))
        self.game_info = SimpleNamespace(
            map_ramps=(
                SimpleNamespace(top_center=FakePoint(38.0, 36.0)),
                SimpleNamespace(top_center=FakePoint(122.0, 124.0)),
            )
        )
        self.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(45.0, 52.0),
            FakePoint(130.0, 130.0),
        ]
        self.mineral_field = FakeUnitGroup(
            (FakeUnit("MineralField", 24.0, 28.0), FakeUnit("MineralField", 136.0, 130.0))
        )
        self.vespene_geyser = FakeUnitGroup((FakeUnit("VespeneGeyser", 36.0, 24.0),))

        worker_units = [FakeUnit("SCV", 26.0 + index, 28.0) for index in range(workers)]
        marine_units = [FakeUnit("Marine", 32.0 + index, 30.0) for index in range(marines)]
        self.workers = FakeUnitGroup(worker_units)
        self.units = FakeUnitGroup((*worker_units, *marine_units))
        self.structures = FakeUnitGroup((FakeUnit("CommandCenter", 30.0, 30.0),))
        self.enemy_units = FakeUnitGroup(enemy_units)
        self.enemy_structures = FakeUnitGroup(enemy_structures)
        self.selected_units = FakeUnitGroup(selected_units)
        if camera_position is not None:
            self.camera_position = camera_position
        self.supports_camera = supports_camera

        self.minerals = minerals
        self.vespene = 0
        self.supply_used = 14
        self.supply_cap = 15
        self.supply_left = supply_left
        self.supply_army = marines
        self.state = SimpleNamespace(game_loop=448)
        self.time = 20.0
        self.issued_commands = []
        self.execution_events = []
        self.camera_moves = []
        self.supports_build = supports_build
        self.build_calls = []

    def unit_type_id_resolver(self, type_name):
        return type_name

    def can_afford(self, item):
        return True

    def do(self, command):
        self.issued_commands.append(command)
        self.execution_events.append(("do", command))
        return None

    def center_camera(self, point):
        if not self.supports_camera:
            return False
        self.issued_commands.append(("center_camera", (float(point.x), float(point.y))))
        self.execution_events.append(("center_camera", (float(point.x), float(point.y))))
        return True

    def move_camera(self, point):
        self.camera_moves.append(point)
        self.execution_events.append(("move_camera", (float(point.x), float(point.y))))
        return True

    async def build(self, type_id, near=None):
        if not self.supports_build:
            return False
        self.build_calls.append((type_id, near))
        point = (float(near.x), float(near.y)) if hasattr(near, "x") else near
        self.execution_events.append(("build", type_id, point))
        return None


def make_session(bot, **overrides):
    adapter = PythonSC2BotAdapter(bot=bot)
    options = {"executor": SC2RuntimeExecutor(bot=adapter)}
    options.update(overrides)
    return SC2CommandSession(**options)


def _normalize_issued_commands(commands):
    normalized = []
    for kind, unit_name, payload in commands:
        if hasattr(payload, "x") and hasattr(payload, "y"):
            payload = (float(payload.x), float(payload.y))
        elif hasattr(payload, "name"):
            payload = payload.name
        normalized.append((kind, unit_name, payload))
    return tuple(normalized)


class RecordingValidator:
    """Spy wrapper proving command parts pass through feasibility validation."""

    def __init__(self, delegate=DEFAULT_SC2_FEASIBILITY_VALIDATOR):
        self._delegate = delegate
        self.calls = []

    def validate_payload(self, payload, state):
        self.calls.append(getattr(payload, "intent", None))
        return self._delegate.validate_payload(payload, state)


class RecordingPlanner:
    """Spy wrapper proving command parts pass through deterministic planning."""

    def __init__(self, delegate=DEFAULT_SC2_ACTION_PLANNER):
        self._delegate = delegate
        self.calls = []

    def build_plan(self, payload):
        self.calls.append(getattr(payload, "intent", None))
        return self._delegate.build_plan(payload)


class RecordingExecutor:
    """Spy wrapper proving only planned SC2 execution plans reach runtime."""

    def __init__(self, delegate):
        self._delegate = delegate
        self.calls = []

    @property
    def bot(self):
        return self._delegate.bot

    @property
    def is_started(self):
        return self._delegate.is_started

    async def start(self, bot=None):
        return await self._delegate.start(bot)

    async def stop(self):
        return await self._delegate.stop()

    async def execute(self, plan):
        self.calls.append(plan.intent_name)
        return await self._delegate.execute(plan)


class MismatchedComboResultExecutor(RecordingExecutor):
    """Executor fake that returns a different plan than the one it received."""

    async def execute(self, plan):
        self.calls.append(plan.intent_name)
        mismatched_plan = SC2ExecutionPlan(
            intent_name="TRAIN_ARMY",
            priority=plan.priority,
            ordered_actions=plan.ordered_actions,
            constraints=plan.constraints,
            requires_live_sc2=plan.requires_live_sc2,
            notes=plan.notes,
            audit=plan.audit,
        )
        return SC2PlanExecutionResult(
            plan=mismatched_plan,
            attempted_actions=mismatched_plan.ordered_actions,
            applied_actions=mismatched_plan.ordered_actions,
        )


class StaticInterpreter:
    """Fake interpreter seam returning one fixed payload for any text."""

    def __init__(self, payload):
        self._payload = payload

    def interpret_text(self, command_text):
        return self._payload

    def interpret(self, command_text):
        return SimpleNamespace(
            command_text=command_text,
            payload=self._payload,
            clarification_required=False,
            clarification_prompt="",
            reason="",
            alternatives=(),
            candidates=(),
        )


class FailureClassifiedCameraInterpreter:
    """Fake interpreter leaking a camera payload on a failed classification."""

    def __init__(self):
        self._payload = MoveCameraIntent(
            priority="normal",
            constraints=("move camera to semantic target",),
            target="main ramp",
        )

    def interpret_text(self, command_text):
        return self.interpret(command_text).payload

    def interpret(self, command_text):
        return SimpleNamespace(
            command_text=command_text,
            payload=self._payload,
            clarification_required=True,
            clarification_prompt=(
                "카메라 이동 요청이 실패로 분류되어 실행하지 않았습니다. "
                "필요한 정보(target): 어느 위치로 카메라를 이동할까요?"
            ),
            reason="카메라 대상이 실패로 분류되었습니다.",
            alternatives=("본진 입구로 카메라 옮겨", "적 입구 보여줘"),
            candidates=(),
            failure=build_parsing_failure_report(
                command_text=command_text,
                code="camera_classification_failed",
                message="카메라 대상이 실패로 분류되었습니다.",
                alternatives=("본진 입구로 카메라 옮겨", "적 입구 보여줘"),
                intent="MOVE_CAMERA",
                metadata={"missing_fields": ["target"]},
            ),
        )


class DeterministicBuildFixtureInterpreter:
    """Exact-match interpreter fixture for AC 7 Korean build examples."""

    def __init__(self, fixtures):
        self._payloads = {
            fixture["command_text"]: fixture["payload"] for fixture in fixtures
        }
        self.calls = []

    def interpret_text(self, command_text):
        return self.interpret(command_text).payload

    def interpret(self, command_text):
        self.calls.append(command_text)
        if command_text not in self._payloads:
            raise AssertionError(f"unexpected fixture command: {command_text!r}")
        return SimpleNamespace(
            command_text=command_text,
            payload=self._payloads[command_text],
            clarification_required=False,
            clarification_prompt="",
            reason="deterministic fixture",
            alternatives=(),
            candidates=(),
        )


class UnavailableMapResolver:
    """Fake resolver seam returning one structured unavailable target result."""

    def __init__(
        self,
        *,
        target="self_choke",
        reason="Ambiguous semantic camera target.",
        alternatives=("self_choke", "enemy_choke"),
    ):
        self.target = target
        self.reason = reason
        self.alternatives = alternatives

    def resolve(self, target_name):
        return SimpleNamespace(
            target=self.target or target_name,
            available=False,
            position=None,
            reason=self.reason,
            alternatives=self.alternatives,
        )

    def resolve_point(self, target_name):
        return None


class ComboPlanningInterpreter:
    """Rules-backed fake exposing the optional LLM combo planning seam."""

    def __init__(self, steps):
        self._steps = tuple(steps)
        self.combo_plan_requests = []

    def interpret_text(self, command_text):
        return self.interpret(command_text).payload

    def interpret(self, command_text):
        return DEFAULT_COMMAND_INTERPRETER.interpret(command_text)

    def plan_combo(self, command_text):
        self.combo_plan_requests.append(command_text)
        return SimpleNamespace(command_text=command_text, steps=self._steps)


class FailingComboPlanningInterpreter(ComboPlanningInterpreter):
    """Rules-backed fake whose optional LLM combo planner fails."""

    def __init__(self):
        super().__init__(())

    def plan_combo(self, command_text):
        raise RuntimeError("simulated combo planner failure")


class InvalidTargetComboPlanningInterpreter(ComboPlanningInterpreter):
    """Fake combo planner whose later step resolves to an unplannable target."""

    def __init__(self):
        super().__init__(("정찰보내", "비밀 기지 정찰"))

    def interpret(self, command_text):
        if command_text == "비밀 기지 정찰":
            return SimpleNamespace(
                command_text=command_text,
                payload=ScoutIntent(target="secret moon base", unit_group="SCV"),
                clarification_required=False,
                clarification_prompt="",
                reason="",
                alternatives=(),
                candidates=(),
            )
        return super().interpret(command_text)


class SplitCompoundCommandTest(unittest.TestCase):
    def test_splits_compound_commands_on_korean_connectives(self) -> None:
        cases = {
            MVP_COMPOUND_COMMAND: ("마린 6기 입구로 보내", "SCV 계속 찍어"),
            "정찰 보내 그리고 입구 막아": ("정찰 보내", "입구 막아"),
            "그리고 마린 뽑아": ("마린 뽑아",),
            "일꾼 계속 찍어 하고 상태 알려줘": ("일꾼 계속 찍어", "상태 알려줘"),
            "마린 뽑으면서 정찰 보내": ("마린 뽑으", "정찰 보내"),
            "벙커 짓고 서플 올려": ("벙커 짓", "서플 올려"),
            "마린 뽑고 보급고 지어": ("마린 뽑", "보급고 지어"),
            "정찰 보내고 나서 보급고 지어": ("정찰 보내", "보급고 지어"),
            "마린 생산한 다음 정찰 보내": ("마린 생산", "정찰 보내"),
            "마린 생산하고 정찰 보내": ("마린 생산", "정찰 보내"),
            "정찰 보내, 보급고 지어": ("정찰 보내", "보급고 지어"),
            "SCV 계속 찍어; 마린 생산해": ("SCV 계속 찍어", "마린 생산해"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, split_compound_command(text))

    def test_does_not_split_simple_commands(self) -> None:
        for text in ("배럭 지어", "상태 알려줘", "SCV 계속 찍어", "입구 막아"):
            with self.subTest(text=text):
                self.assertEqual((text,), split_compound_command(text))

    def test_never_splits_inside_nouns_ending_in_go(self) -> None:
        # 보급고/창고 end in 고 but are nouns; splitting them shreds the
        # commander's build order into garbage fragments.
        cases = {
            "보급고 지어": ("보급고 지어",),
            "창고 정리해": ("창고 정리해",),
            "보급고 짓고 마린 뽑아": ("보급고 짓", "마린 뽑아"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, split_compound_command(text))

    def test_strips_parts_and_drops_empties(self) -> None:
        self.assertEqual((), split_compound_command("   "))
        self.assertEqual((), split_compound_command(None))
        self.assertEqual(
            ("정찰 보내", "입구 막아"),
            split_compound_command("  정찰 보내   그리고   입구 막아  "),
        )

    def test_detects_macro_and_compound_intent_without_splitting_single_commands(
        self,
    ) -> None:
        compound_cases = (
            "초반 운영 시작해",
            "초반 빌드 오더 시작해",
            "초반세팅해줘",
            "오프닝 작전 시작",
            "opening operation start",
            "정찰보내고 병영올려",
            "정찰 보내 그리고 보급고 지어",
            "SCV 계속 찍어 보급고 지어",
            "마린 생산해 정찰도 보내",
            "상태 보고하고 지금 할거 알려줘",
        )
        for text in compound_cases:
            with self.subTest(text=text):
                self.assertTrue(is_compound_or_macro_intent(text))

        single_cases = (
            "보급고 지어",
            "앞마당에 사령부 지어",
            "SCV 계속 찍어",
            "입구 막아",
            "지금 뭐 해야 해?",
            "카메라 움직일 수 있어?",
        )
        for text in single_cases:
            with self.subTest(text=text):
                self.assertFalse(is_compound_or_macro_intent(text))

    def test_progressive_and_object_hago_phrases_remain_single_command(
        self,
    ) -> None:
        cases = (
            "지금 뭐 하고 있어?",
            "마린 생산하고 있어?",
            "입구 막고 있어",
            "보급고 짓고 있어",
            "마린하고 SCV 상태 알려줘",
            "사령부하고 일꾼 보여줘",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual((text,), split_compound_command(text))
                self.assertFalse(is_compound_or_macro_intent(text))


class SC2CommandOutcomeContractTest(unittest.TestCase):
    def test_status_vocabulary_is_stable(self) -> None:
        self.assertEqual(
            frozenset(
                {"executed", "partially_executed", "blocked", "read_only", "clarification"}
            ),
            SC2_COMMAND_OUTCOME_STATUSES,
        )

    def test_rejects_unknown_status_and_empty_narration(self) -> None:
        with self.subTest(case="unknown status"):
            with self.assertRaises(ValueError):
                SC2CommandOutcome(
                    command_text="x", status="done", narration="내레이션"
                )
        with self.subTest(case="empty narration"):
            with self.assertRaises(ValueError):
                SC2CommandOutcome(command_text="x", status="blocked", narration="  ")

    def test_clarification_outcomes_cannot_carry_pipeline_artifacts(self) -> None:
        with self.assertRaises(ValueError):
            SC2CommandOutcome(
                command_text="x",
                status="clarification",
                narration="다시 말해 주세요.",
                intent_dsl={"intent": "TRAIN_WORKER"},
            )

    def test_executed_outcomes_require_plan_and_execution_result(self) -> None:
        for status in ("executed", "partially_executed", "read_only"):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    SC2CommandOutcome(
                        command_text="x", status=status, narration="실행했습니다."
                    )

    def test_clarification_outcome_to_dict_is_json_ready(self) -> None:
        outcome = SC2CommandOutcome(
            command_text="피아노 쳐줘",
            status="clarification",
            narration="다시 말해 주세요.",
        )
        payload = json.loads(json.dumps(outcome.to_dict(), ensure_ascii=False))
        self.assertEqual("clarification", payload["status"])
        self.assertIsNone(payload["intent_dsl"])
        self.assertIsNone(payload["plan"])
        self.assertIsNone(payload["execution_result"])
        self.assertIsNone(payload["feasibility"])


class LivePipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_continuous_train_command_discloses_unsupported_constraint(self) -> None:
        # "계속 찍어" carries a continuity constraint no runtime enforces:
        # exactly one train order goes out, so the outcome must disclose the
        # dropped constraint instead of narrating unqualified success.
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("SCV 계속 찍어")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("partially_executed", outcome.status)
        self.assertIn("SCV 1기 생산 명령", outcome.narration)
        self.assertIn("지속 생산은 아직 지원되지 않아", outcome.narration)
        self.assertEqual("TRAIN_WORKER", outcome.intent_dsl["intent"])
        self.assertIsInstance(outcome.plan, SC2ExecutionPlan)
        self.assertIsInstance(outcome.execution_result, SC2PlanExecutionResult)
        self.assertTrue(outcome.execution_result.success)
        self.assertTrue(outcome.feasibility.executable)
        self.assertEqual([("train", "CommandCenter", "SCV")], bot.issued_commands)

    async def test_state_summary_command_is_read_only(self) -> None:
        session = make_session(LivePipelineFakeBot())

        outcomes = await session.process_text("상태 알려줘")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("read_only", outcome.status)
        self.assertIn("전장 상태를 확인했습니다", outcome.narration)
        self.assertIn("미네랄 400", outcome.narration)
        self.assertEqual("SUMMARIZE_STATE", outcome.intent_dsl["intent"])
        self.assertTrue(outcome.execution_result.success)

    async def test_building_location_question_gets_read_only_answer(self) -> None:
        session = make_session(LivePipelineFakeBot())

        outcomes = await session.process_text("그리고 위치를 내가 지정할수도 있어? 건물에")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("read_only", outcome.status)
        self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
        self.assertEqual("building_location_help", outcome.intent_dsl["topic"])
        self.assertIn("의미 기반 위치", outcome.narration)
        self.assertIn("본진 입구에 보급고", outcome.narration)
        self.assertEqual("ANSWER_QUESTION", outcome.plan.intent_name)
        self.assertTrue(outcome.execution_result.success)

    async def test_targeting_question_uses_live_context(self) -> None:
        bot = LivePipelineFakeBot(
            selected_units=(FakeUnit("Marine", 32.0, 30.0),),
            enemy_units=(FakeUnit("Zergling", 120.0, 124.0),),
            enemy_structures=(FakeUnit("Hatchery", 130.0, 130.0),),
        )
        session = make_session(bot)

        outcome = (await session.process_text("어디를 대상으로 지정할 수 있어?"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("building_location_help", outcome.intent_dsl["topic"])
        self.assertIn("현재 관측", outcome.narration)
        self.assertIn("선택 유닛: Marine 1", outcome.narration)
        self.assertIn("보이는 적: ZERGLING 1, HATCHERY 1", outcome.narration)
        self.assertIn("semantic target", outcome.narration)
        self.assertIn("본진(self_main", outcome.narration)
        self.assertIn("적 본진(enemy_main", outcome.narration)
        self.assertIn("읽기 전용", outcome.narration)
        self.assertEqual([], bot.issued_commands)

    async def test_townhall_state_question_is_read_only_with_single_candidate(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcome = (await session.process_text("사령부 상태 알려줘"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
        self.assertEqual("townhall_state_help", outcome.intent_dsl["topic"])
        self.assertIn("현재 사령부/기지 상태", outcome.narration)
        self.assertIn("후보 1개", outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
        self.assertIn("완성 사령부 1", outcome.narration)
        self.assertIn("읽기 전용", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

    async def test_generic_townhall_state_question_clarifies_only_with_multiple_bases(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        session = make_session(bot)

        outcome = (await session.process_text("사령부 상태 알려줘"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertIn("어느 사령부/기지 상태", outcome.narration)
        self.assertIn("필요한 정보(target)", outcome.narration)
        self.assertIn("가능한 선택지", outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

    async def test_concrete_townhall_state_question_stays_read_only_with_multiple_bases(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        session = make_session(bot)

        outcome = (await session.process_text("본진 사령부 상태 알려줘"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("townhall_state_help", outcome.intent_dsl["topic"])
        self.assertIn("요청 대상: 본진 사령부", outcome.narration)
        self.assertIn("후보 1개", outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
        self.assertNotIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

    async def test_camera_question_uses_runtime_camera_context(self) -> None:
        bot = LivePipelineFakeBot(
            selected_units=(FakeUnit("SCV", 30.0, 30.0),),
            enemy_units=(FakeUnit("Zergling", 120.0, 124.0),),
            camera_position=FakePoint(44.0, 48.0),
            supports_camera=True,
        )
        session = make_session(bot)

        outcome = (await session.process_text("카메라 적 본진으로 움직일 수 있어?"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("camera_help", outcome.intent_dsl["topic"])
        self.assertIn("MOVE_CAMERA", outcome.narration)
        self.assertIn("카메라 API: 현재 런타임에서 MOVE_CAMERA 실행을 지원", outcome.narration)
        self.assertIn("현재 카메라: (44.0, 48.0)", outcome.narration)
        self.assertIn("선택 유닛: SCV 1", outcome.narration)
        self.assertIn("보이는 적: ZERGLING 1", outcome.narration)
        self.assertIn("semantic target", outcome.narration)
        self.assertIn("읽기 전용", outcome.narration)
        self.assertEqual([], bot.issued_commands)

    async def test_korean_qna_fixture_covers_supported_categories_with_context(
        self,
    ) -> None:
        expected_categories = {
            "state_summary",
            "next_action",
            "failure_reason",
            "targeting_location",
            "townhall_state",
            "camera",
            "voice",
            "llm",
            "capability",
            "meta",
            "cancel",
        }
        self.assertEqual(
            expected_categories,
            {fixture["category"] for fixture in KOREAN_QNA_CONTEXT_FIXTURES},
        )

        for fixture in KOREAN_QNA_CONTEXT_FIXTURES:
            with self.subTest(category=fixture["category"], question=fixture["question"]):
                bot = LivePipelineFakeBot(
                    minerals=275,
                    supply_left=0,
                    workers=5,
                    marines=2,
                    selected_units=(FakeUnit("Marine", 32.0, 30.0),),
                    enemy_units=(FakeUnit("Zergling", 120.0, 124.0),),
                    enemy_structures=(FakeUnit("Hatchery", 130.0, 130.0),),
                    camera_position=FakePoint(44.0, 48.0),
                    supports_camera=True,
                )
                session = make_session(bot)

                outcomes = await session.process_text(fixture["question"])

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual(fixture["intent"], outcome.intent_dsl["intent"])
                self.assertTrue(outcome.execution_result.success)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)
                if fixture["topic"] is not None:
                    self.assertEqual(fixture["topic"], outcome.intent_dsl["topic"])
                    self.assertTrue(outcome.intent_dsl["read_only"])
                    self.assertEqual("ANSWER_QUESTION", outcome.plan.intent_name)
                    plan_safety_text = " ".join(
                        (*outcome.plan.constraints, *outcome.plan.notes)
                    )
                    self.assertIn("read-only", plan_safety_text)
                for fragment in fixture["fragments"]:
                    with self.subTest(fragment=fragment):
                        self.assertIn(fragment, outcome.narration)
                for generic_fragment in GENERIC_QNA_FAILURE_FRAGMENTS:
                    with self.subTest(generic_fragment=generic_fragment):
                        self.assertNotIn(generic_fragment, outcome.narration)

    async def test_seed_qna_examples_bypass_command_clarification(self) -> None:
        qna_cases = (
            ("지금 뭐 해야 해?", "next_action_help", "추천 흐름"),
            ("지금 할거 알려줘", "next_action_help", "추천 흐름"),
            ("다음 할 일 알려줘", "next_action_help", "추천 흐름"),
            ("왜 안돼?", "failure_reason_help", "최근 실패 기록"),
            ("위치 지정 가능해?", "building_location_help", "semantic target"),
            ("카메라 움직일 수 있어?", "camera_help", "MOVE_CAMERA"),
            ("llm이랑 대화 가능?", "llm_help", "LLM-first 대화형 입력"),
        )

        for question, expected_topic, expected_context in qna_cases:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertNotEqual("clarification", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual(expected_topic, outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertIn(expected_context, outcome.narration)
                for generic_fragment in GENERIC_QNA_FAILURE_FRAGMENTS:
                    self.assertNotIn(generic_fragment, outcome.narration)
                self.assertNotEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
                    outcome.narration,
                )
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_question_typo_aliases_stay_read_only(self) -> None:
        qna_cases = (
            ("지금할거 알려줘", "next_action_help", "추천 흐름"),
            ("뭐해야돼?", "next_action_help", "추천 흐름"),
            ("왜 안되?", "failure_reason_help", "최근 실패 기록"),
        )

        for question, expected_topic, expected_context in qna_cases:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(bot, executor=executor)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual(expected_topic, outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertEqual("ANSWER_QUESTION", outcome.plan.intent_name)
                self.assertIn(expected_context, outcome.narration)
                for generic_fragment in GENERIC_QNA_FAILURE_FRAGMENTS:
                    self.assertNotIn(generic_fragment, outcome.narration)
                self.assertEqual([], executor.calls)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_natural_language_question_routes_before_executor(self) -> None:
        qna_cases = (
            ("지금 뭐 해야 해?", "next_action_help"),
            ("지금 할거 알려줘", "next_action_help"),
            ("다음 할 일 알려줘", "next_action_help"),
            ("왜 안돼?", "failure_reason_help"),
        )

        for question, expected_topic in qna_cases:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(bot, executor=executor)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual(expected_topic, outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertEqual("ANSWER_QUESTION", outcome.plan.intent_name)
                self.assertFalse(outcome.plan.requires_live_sc2)
                self.assertIn(
                    "answer commander question without issuing game actions",
                    outcome.plan.constraints,
                )
                self.assertIn(
                    "Question answers are read-only and never issue SC2 API commands.",
                    outcome.plan.notes,
                )
                self.assertEqual(expected_topic, outcome.plan.audit["topic"])
                self.assertTrue(outcome.execution_result.success)
                self.assertEqual(expected_topic, outcome.execution_result.audit["topic"])
                for generic_fragment in GENERIC_QNA_FAILURE_FRAGMENTS:
                    self.assertNotIn(generic_fragment, outcome.narration)
                self.assertEqual([], executor.calls)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_seed_required_korean_success_path_examples_are_covered(
        self,
    ) -> None:
        def assert_no_generic_failure(outcomes):
            for outcome in outcomes:
                self.assertNotEqual("clarification", outcome.status)
                for generic_fragment in GENERIC_QNA_FAILURE_FRAGMENTS:
                    if generic_fragment == "다시 말해 주세요":
                        continue
                    self.assertNotIn(generic_fragment, outcome.narration)
                self.assertNotEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
                    outcome.narration,
                )

        qna_cases = (
            ("지금 뭐 해야 해?", "next_action_help"),
            ("지금 할거 알려줘", "next_action_help"),
            ("다음 할 일 알려줘", "next_action_help"),
            ("왜 안돼?", "failure_reason_help"),
            ("위치 지정 가능해?", "building_location_help"),
            ("카메라 움직일 수 있어?", "camera_help"),
            ("llm이랑 대화 가능?", "llm_help"),
        )
        for question, expected_topic in qna_cases:
            with self.subTest(category="qna", command_text=question):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual(expected_topic, outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertTrue(outcome.execution_result.success)
                assert_no_generic_failure(outcomes)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

        macro_cases = (
            ("초반 운영 시작해", ("TRAIN_WORKER", "BUILD_STRUCTURE", "SCOUT")),
            ("정찰보내고 병영올려", ("SCOUT", "BUILD_STRUCTURE")),
            ("상태 보고하고 지금 할거 알려줘", ("SUMMARIZE_STATE", "ANSWER_QUESTION")),
            ("경제 안정화해", ("TRAIN_WORKER", "GATHER_RESOURCE", "BUILD_STRUCTURE")),
        )
        for command_text, expected_intents in macro_cases:
            with self.subTest(category="macro", command_text=command_text):
                bot = LivePipelineFakeBot(
                    minerals=1000,
                    supply_left=12,
                    supports_build=True,
                )
                bot.structures.append(FakeUnit("SupplyDepot", 34.0, 31.0))
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(
                    list(expected_intents),
                    [outcome.intent_dsl["intent"] for outcome in outcomes],
                )
                self.assertTrue(
                    all(
                        outcome.status in {"executed", "partially_executed", "read_only"}
                        for outcome in outcomes
                    )
                )
                self.assertTrue(
                    all(outcome.execution_result.success for outcome in outcomes)
                )
                assert_no_generic_failure(outcomes)

        build_interpreter = DeterministicBuildFixtureInterpreter(
            KOREAN_BUILD_EXAMPLE_COMMAND_FIXTURES
        )
        for fixture in KOREAN_BUILD_EXAMPLE_COMMAND_FIXTURES:
            command_text = fixture["command_text"]
            with self.subTest(category="semantic_build", command_text=command_text):
                bot = LivePipelineFakeBot(
                    minerals=1000,
                    supply_left=12,
                    supports_build=True,
                )
                session = make_session(bot, interpreter=build_interpreter)

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("executed", outcome.status)
                self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                placement_policy = outcome.plan.actions[0].metadata[
                    "placement_policy"
                ]
                self.assertEqual(
                    fixture["anchor_target"],
                    placement_policy["anchor_target"],
                )
                self.assertEqual(
                    fixture["spatial_relation"],
                    placement_policy["spatial_relation"],
                )
                self.assertEqual(1, len(bot.build_calls))
                self.assertEqual(fixture["type_id"], bot.build_calls[0][0])
                assert_no_generic_failure(outcomes)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

        camera_cases = (
            ("본진 보여줘", "main base", (30.0, 30.0)),
            ("본진 입구로 카메라 옮겨", "main ramp", (38.0, 36.0)),
            ("앞마당으로 화면 이동", "natural expansion", (45.0, 52.0)),
            ("적 입구 보여줘", "enemy front", (122.0, 124.0)),
        )
        for command_text, expected_target, expected_point in camera_cases:
            with self.subTest(category="camera", command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.scouted_enemy_front = FakePoint(122.0, 124.0)
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("executed", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertEqual(1, len(bot.camera_moves))
                destination = bot.camera_moves[0]
                self.assertEqual(
                    expected_point,
                    (float(destination.x), float(destination.y)),
                )
                assert_no_generic_failure(outcomes)
                self.assertEqual([], bot.issued_commands)

    async def test_korean_meta_advice_and_capability_questions_bypass_clarification(
        self,
    ) -> None:
        qna_cases = (
            ("너는 뭐 하는 봇이야?", "commander_meta_help", "LLM-first StarCraft 커맨더"),
            ("운영 조언해줘", "next_action_help", "추천 흐름"),
            ("어떤 명령을 할 수 있어?", "capability_help", "지원 명령 예시"),
        )

        for question, expected_topic, expected_fragment in qna_cases:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertNotEqual("clarification", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual(expected_topic, outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertEqual("ANSWER_QUESTION", outcome.plan.intent_name)
                self.assertTrue(outcome.execution_result.success)
                self.assertIn(expected_fragment, outcome.narration)
                self.assertNotEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
                    outcome.narration,
                )
                self.assertNotIn(
                    UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
                    outcome.narration,
                )
                for generic_fragment in GENERIC_QNA_FAILURE_FRAGMENTS:
                    self.assertNotIn(generic_fragment, outcome.narration)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_voice_support_question_gets_read_only_answer(self) -> None:
        session = make_session(LivePipelineFakeBot())

        outcomes = await session.process_text("음성지원도 되나?")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("read_only", outcome.status)
        self.assertEqual("voice_help", outcome.intent_dsl["topic"])
        self.assertIn("--voice", outcome.narration)
        self.assertIn("마이크 권한", outcome.narration)

    async def test_capability_question_gets_read_only_answer(self) -> None:
        capability_questions = (
            "어떤 명령을 할 수 있어?",
            "너 뭘 할수있어?",
            "무슨 명령 가능해?",
            "지원하는 명령 뭐야?",
            "가능한 명령어 알려줘",
            "지원 기능 뭐가 있어?",
            "너 뭐 할 줄 알아?",
            "뭘 도와줄 수 있어?",
        )

        for question in capability_questions:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual("capability_help", outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertIn("상태 확인", outcome.narration)
                self.assertIn("정찰", outcome.narration)
                self.assertIn("지원 명령 예시", outcome.narration)
                self.assertIn("지원 질문 예시", outcome.narration)
                self.assertIn("확인 질문이 필요한 경우", outcome.narration)
                self.assertIn("제한:", outcome.narration)
                self.assertIn("읽기 전용", outcome.narration)
                self.assertEqual([], bot.issued_commands)

    async def test_llm_status_question_gets_read_only_answer(self) -> None:
        session = make_session(LivePipelineFakeBot())

        for question in (
            "지금 llm이랑 대화가능?",
            "LLM이 지원하는 질문과 명령 알려줘",
            "LLM 설정 상태 알려줘",
        ):
            with self.subTest(question=question):
                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("llm_help", outcome.intent_dsl["topic"])
                self.assertIn("LLM-first 대화형 입력", outcome.narration)
                self.assertIn("지원 명령 예시", outcome.narration)
                self.assertIn("지원 질문 예시", outcome.narration)
                self.assertIn("확인 질문이 필요한 경우", outcome.narration)
                self.assertIn("semantic target", outcome.narration)
                self.assertIn("제한:", outcome.narration)
                self.assertIn("읽기 전용", outcome.narration)
                self.assertIn("LLM 설정 영역", outcome.narration)
                self.assertIn("실패 이유", outcome.narration)
                self.assertIn("dry-run", outcome.narration)
                self.assertNotIn("내부 해석 원문", outcome.narration)
                self.assertNotIn("시스템 지시문", outcome.narration)
                self.assertNotIn("OPENAI_API_KEY", outcome.narration)
                self.assertNotIn("ANTHROPIC_API_KEY", outcome.narration)
                self.assertNotIn("sk-", outcome.narration)
                self.assertIn("Live GUI", outcome.narration)
                self.assertEqual([], session._game_bot_for_question().issued_commands)

    async def test_next_action_question_gets_read_only_answer(self) -> None:
        advisory_questions = (
            "지금 할거 알려줘",
            "지금 뭐 해야 해?",
            "뭘 해야 해?",
            "이제 뭐 하면 돼?",
            "다음 할 일 알려줘",
            "이제 뭐하지?",
            "다음엔 뭐 할까?",
            "운영 조언해줘",
            "전략 추천해줄래?",
        )

        for question in advisory_questions:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual("next_action_help", outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertIn("추천 흐름", outcome.narration)
                self.assertEqual([], bot.issued_commands)

    async def test_meta_question_gets_read_only_answer(self) -> None:
        meta_questions = (
            "너는 뭐 하는 봇이야?",
            "이 커맨더 뭐야?",
            "이거 무슨 기능이야?",
        )

        for question in meta_questions:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual("commander_meta_help", outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertIn("LLM-first StarCraft 커맨더", outcome.narration)
                self.assertEqual([], bot.issued_commands)

    async def test_next_action_question_uses_current_state_context(self) -> None:
        bot = LivePipelineFakeBot(minerals=200, supply_left=8, workers=0)
        session = make_session(bot)

        outcome = (await session.process_text("지금 뭐 해야 해?"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("next_action_help", outcome.intent_dsl["topic"])
        self.assertIn("현재 관측", outcome.narration)
        self.assertIn("미네랄 200", outcome.narration)
        self.assertIn("보급 14/15(여유 8)", outcome.narration)
        self.assertIn("유휴 SCV 0", outcome.narration)
        self.assertIn("배럭", outcome.narration)
        self.assertIn("읽기 전용", outcome.narration)
        self.assertEqual([], bot.issued_commands)

    async def test_strategic_briefing_starts_with_korean_current_strategy(self) -> None:
        bot = LivePipelineFakeBot(minerals=200, supply_left=8, workers=12, marines=1)
        memory = CommanderEventMemory()
        memory.record(
            {
                "command_text": "마린 생산해",
                "status": "executed",
                "narration": "마린 생산을 시작했습니다.",
                "intent_name": "TRAIN_UNIT",
            }
        )
        session = make_session(bot, event_memory=memory)

        outcome = (await session.process_text("전략 추천해줄래?"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("next_action_help", outcome.intent_dsl["topic"])
        self.assertTrue(outcome.narration.startswith("현재 전략: 테란 생산 인프라"))
        self.assertLess(
            outcome.narration.index("현재 전략:"),
            outcome.narration.index("현재 관측"),
        )
        self.assertLess(
            outcome.narration.index("현재 전략:"),
            outcome.narration.index("추천 흐름"),
        )
        self.assertIn("읽기 전용", outcome.narration)
        self.assertEqual([], bot.issued_commands)

    async def test_failure_reason_questions_get_read_only_answer(self) -> None:
        failure_reason_questions = (
            "왜 안돼?",
            "왜 실패했어?",
            "실패 이유 알려줘",
            "방금 왜 실행 안 됐어?",
        )

        for question in failure_reason_questions:
            with self.subTest(question=question):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(question)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual("ANSWER_QUESTION", outcome.intent_dsl["intent"])
                self.assertEqual("failure_reason_help", outcome.intent_dsl["topic"])
                self.assertTrue(outcome.intent_dsl["read_only"])
                self.assertIn("최근 실패 기록", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertEqual([], bot.issued_commands)

    async def test_camera_capability_question_and_cancel_get_read_only_answers(self) -> None:
        session = make_session(LivePipelineFakeBot())

        for command_text, expected_topic in (
            ("카메라 움직일 수 있어?", "camera_help"),
            ("취소", "cancel_help"),
        ):
            with self.subTest(command_text=command_text):
                outcomes = await session.process_text(command_text)
                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("read_only", outcome.status)
                self.assertEqual(expected_topic, outcome.intent_dsl["topic"])

    async def test_camera_move_command_executes_move_camera_intent(self) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("본진 입구로 카메라 옮겨")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("executed", outcome.status)
        self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
        self.assertEqual("main ramp", outcome.intent_dsl["target"])
        self.assertEqual(
            MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT,
            outcome.intent_dsl["target_slot"],
        )
        self.assertEqual("self_ramp", outcome.plan.ordered_actions[0].target)
        self.assertEqual(
            MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT,
            outcome.plan.ordered_actions[0].metadata["target_slot"],
        )
        self.assertIn("카메라", outcome.narration)
        self.assertEqual(1, len(bot.camera_moves))
        self.assertEqual([], bot.issued_commands)

    async def test_camera_move_command_returns_unavailable_when_runtime_lacks_camera_capability(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        bot.move_camera = None
        bot.center_camera = None
        session = make_session(bot)

        outcome = (await session.process_text("본진 입구로 카메라 옮겨"))[0]

        self.assertEqual("blocked", outcome.status)
        self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
        self.assertFalse(outcome.execution_result.success)
        self.assertIn("카메라 이동 API를 제공하지 않습니다", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        report = outcome.execution_result.audit["action_reports"]["0"]
        self.assertFalse(report["applied"])
        self.assertEqual("missing_camera_capability", report["detail"])
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_required_korean_camera_unavailable_paths_are_auditable(
        self,
    ) -> None:
        missing_capability_cases = (
            ("본진 보여줘", "main base"),
            ("앞마당으로 화면 이동", "natural expansion"),
        )
        for command_text, expected_target in missing_capability_cases:
            with self.subTest(path="missing_camera_capability", command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.move_camera = None
                bot.center_camera = None
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("blocked", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertFalse(outcome.execution_result.success)
                self.assertIn("카메라 이동 API를 제공하지 않습니다", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertNotIn("LLM 해석에 실패", outcome.narration)
                report = outcome.execution_result.audit["action_reports"]["0"]
                self.assertFalse(report["applied"])
                self.assertEqual("missing_camera_capability", report["detail"])
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

        unscouted_bot = LivePipelineFakeBot()
        unscouted_session = make_session(unscouted_bot)

        unscouted_outcome = (await unscouted_session.process_text("적 입구 보여줘"))[0]

        self.assertEqual("blocked", unscouted_outcome.status)
        self.assertEqual("MOVE_CAMERA", unscouted_outcome.intent_dsl["intent"])
        self.assertEqual("enemy front", unscouted_outcome.intent_dsl["target"])
        self.assertFalse(unscouted_outcome.execution_result.success)
        self.assertIn("정찰/관측되지 않아", unscouted_outcome.narration)
        self.assertNotIn("10개 MVP", unscouted_outcome.narration)
        self.assertNotIn("LLM 해석에 실패", unscouted_outcome.narration)
        unscouted_report = unscouted_outcome.execution_result.audit["action_reports"]["0"]
        self.assertFalse(unscouted_report["applied"])
        self.assertEqual("unscouted_camera_target", unscouted_report["detail"])
        self.assertEqual("enemy_front", unscouted_report["audit"]["target"])
        self.assertEqual([], unscouted_bot.camera_moves)
        self.assertEqual([], unscouted_bot.issued_commands)

        ambiguous_bot = LivePipelineFakeBot()
        ambiguous_bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        ambiguous_session = make_session(ambiguous_bot)

        ambiguous_outcome = (
            await ambiguous_session.process_text("카메라 사령부로 옮겨")
        )[0]

        self.assertEqual("clarification", ambiguous_outcome.status)
        self.assertIn("어느 사령부", ambiguous_outcome.narration)
        self.assertIn("필요한 정보(target)", ambiguous_outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", ambiguous_outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", ambiguous_outcome.narration)
        self.assertNotIn("10개 MVP", ambiguous_outcome.narration)
        self.assertNotIn("LLM 해석에 실패", ambiguous_outcome.narration)
        self.assertIsNone(ambiguous_outcome.intent_dsl)
        self.assertIsNone(ambiguous_outcome.plan)
        self.assertIsNone(ambiguous_outcome.execution_result)
        self.assertIsNone(ambiguous_outcome.feasibility)
        self.assertEqual([], ambiguous_bot.camera_moves)
        self.assertEqual([], ambiguous_bot.issued_commands)

    async def test_failure_classified_camera_request_never_executes_camera(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            interpreter=FailureClassifiedCameraInterpreter(),
            validator=validator,
            planner=planner,
            executor=executor,
        )

        outcome = (await session.process_text("실패로 분류된 카메라 이동"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertIn("카메라 이동 요청이 실패로 분류", outcome.narration)
        self.assertIn("필요한 정보(target)", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], validator.calls)
        self.assertEqual([], planner.calls)
        self.assertEqual([], executor.calls)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_ramp_and_entrance_camera_phrases_execute_with_ramp_slot(
        self,
    ) -> None:
        cases = (
            (
                "입구로 화면 이동",
                "main ramp",
                MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT,
                "self_ramp",
                (38.0, 36.0),
            ),
            (
                "램프로 카메라 옮겨",
                "main ramp",
                MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT,
                "self_ramp",
                (38.0, 36.0),
            ),
            (
                "언덕 보여줘",
                "main ramp",
                MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT,
                "self_ramp",
                (38.0, 36.0),
            ),
            (
                "적 입구 보여줘",
                "enemy front",
                MOVE_CAMERA_ENEMY_ENTRANCE_TARGET_SLOT,
                "enemy_front",
                (122.0, 124.0),
            ),
            (
                "적 입구로 카메라 옮겨",
                "enemy front",
                MOVE_CAMERA_ENEMY_ENTRANCE_TARGET_SLOT,
                "enemy_front",
                (122.0, 124.0),
            ),
            (
                "상대 입구로 화면 이동",
                "enemy front",
                MOVE_CAMERA_ENEMY_ENTRANCE_TARGET_SLOT,
                "enemy_front",
                (122.0, 124.0),
            ),
        )

        for (
            command_text,
            expected_target,
            expected_target_slot,
            expected_plan_target,
            expected_point,
        ) in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                if expected_plan_target.startswith("enemy_"):
                    bot.scouted_enemy_front = FakePoint(*expected_point)
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("executed", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertEqual(
                    expected_target_slot,
                    outcome.intent_dsl["target_slot"],
                )
                self.assertEqual(
                    expected_plan_target,
                    outcome.plan.ordered_actions[0].target,
                )
                self.assertEqual(
                    expected_target_slot,
                    outcome.plan.ordered_actions[0].metadata["target_slot"],
                )
                self.assertEqual(1, len(bot.camera_moves))
                destination = bot.camera_moves[0]
                self.assertEqual(
                    expected_point,
                    (float(destination.x), float(destination.y)),
                )
                self.assertEqual([], bot.issued_commands)

    async def test_explicit_base_camera_commands_bypass_multi_base_ambiguity(
        self,
    ) -> None:
        cases = (
            ("본진 사령부로 카메라 옮겨", "main base", "", (30.0, 30.0)),
            ("본진으로 카메라 옮겨", "main base", "", (30.0, 30.0)),
            ("본진으로 화면 이동", "main base", "", (30.0, 30.0)),
            ("main base로 카메라 옮겨", "main base", "", (30.0, 30.0)),
            (
                "앞마당 사령부로 카메라 옮겨",
                "natural expansion",
                MOVE_CAMERA_NATURAL_EXPANSION_TARGET_SLOT,
                (45.0, 52.0),
            ),
            (
                "앞마당 커맨드로 화면 이동",
                "natural expansion",
                MOVE_CAMERA_NATURAL_EXPANSION_TARGET_SLOT,
                (45.0, 52.0),
            ),
            (
                "natural expansion으로 화면 이동",
                "natural expansion",
                MOVE_CAMERA_NATURAL_EXPANSION_TARGET_SLOT,
                (45.0, 52.0),
            ),
        )

        for command_text, expected_target, expected_target_slot, expected_point in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("executed", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                if expected_target_slot:
                    self.assertEqual(
                        expected_target_slot,
                        outcome.intent_dsl["target_slot"],
                    )
                    self.assertEqual(
                        expected_target_slot,
                        outcome.plan.ordered_actions[0].metadata["target_slot"],
                    )
                else:
                    self.assertNotIn("target_slot", outcome.intent_dsl)
                    self.assertNotIn(
                        "target_slot",
                        outcome.plan.ordered_actions[0].metadata,
                    )
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertNotIn("어느 사령부", outcome.narration)
                self.assertNotIn("combo_plan", outcome.narration)
                self.assertEqual(1, len(bot.camera_moves))
                destination = bot.camera_moves[0]
                self.assertEqual(
                    expected_point,
                    (float(destination.x), float(destination.y)),
                )
                self.assertEqual([], bot.issued_commands)

    async def test_semantic_camera_map_references_execute_to_resolved_targets(
        self,
    ) -> None:
        cases = (
            (
                "third base로 카메라 옮겨",
                "third base",
                "self_third",
                MOVE_CAMERA_THIRD_BASE_TARGET_SLOT,
                (65.0, 80.0),
            ),
            (
                "초크로 화면 이동",
                "natural choke",
                "self_choke",
                MOVE_CAMERA_CHOKE_TARGET_SLOT,
                (38.0, 36.0),
            ),
            (
                "적 초크 보여줘",
                "enemy choke",
                "enemy_choke",
                MOVE_CAMERA_CHOKE_TARGET_SLOT,
                (122.0, 124.0),
            ),
            (
                "정찰 위치 보여줘",
                "scout location",
                "scout_location",
                MOVE_CAMERA_SCOUT_LOCATION_TARGET_SLOT,
                (70.0, 75.0),
            ),
            (
                "마지막 적 위치 보여줘",
                "last seen enemy area",
                "last_seen_enemy_area",
                MOVE_CAMERA_LAST_SEEN_ENEMY_AREA_TARGET_SLOT,
                (105.0, 109.0),
            ),
        )

        for (
            command_text,
            expected_target,
            expected_plan_target,
            expected_target_slot,
            expected_point,
        ) in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.expansion_locations_list = [
                    FakePoint(30.0, 30.0),
                    FakePoint(45.0, 52.0),
                    FakePoint(65.0, 80.0),
                    FakePoint(95.0, 95.0),
                    FakePoint(115.0, 110.0),
                    FakePoint(130.0, 130.0),
                ]
                bot.scout_location = FakePoint(70.0, 75.0)
                bot.last_seen_enemy_area = FakePoint(105.0, 109.0)
                if expected_plan_target.startswith("enemy_"):
                    bot.scouted_enemy_front = FakePoint(*expected_point)
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("executed", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertEqual(
                    expected_target_slot,
                    outcome.intent_dsl["target_slot"],
                )
                self.assertEqual(
                    expected_plan_target,
                    outcome.plan.ordered_actions[0].target,
                )
                self.assertEqual(
                    expected_target_slot,
                    outcome.plan.ordered_actions[0].metadata["target_slot"],
                )
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertEqual(1, len(bot.camera_moves))
                destination = bot.camera_moves[0]
                self.assertEqual(
                    expected_point,
                    (float(destination.x), float(destination.y)),
                )
                self.assertEqual([], bot.issued_commands)

    async def test_korean_semantic_camera_targets_resolve_with_exact_coordinates(
        self,
    ) -> None:
        cases = (
            (
                "세번째 멀티로 카메라 옮겨",
                "third base",
                "self_third",
                MOVE_CAMERA_THIRD_BASE_TARGET_SLOT,
                (65.0, 80.0),
            ),
            (
                "정찰한 곳 보여줘",
                "scout location",
                "scout_location",
                MOVE_CAMERA_SCOUT_LOCATION_TARGET_SLOT,
                (70.0, 75.0),
            ),
            (
                "마지막으로 본 적 보여줘",
                "last seen enemy area",
                "last_seen_enemy_area",
                MOVE_CAMERA_LAST_SEEN_ENEMY_AREA_TARGET_SLOT,
                (105.0, 109.0),
            ),
        )

        for (
            command_text,
            expected_target,
            expected_plan_target,
            expected_target_slot,
            expected_point,
        ) in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.expansion_locations_list = [
                    FakePoint(30.0, 30.0),
                    FakePoint(45.0, 52.0),
                    FakePoint(65.0, 80.0),
                    FakePoint(95.0, 95.0),
                    FakePoint(115.0, 110.0),
                    FakePoint(130.0, 130.0),
                ]
                bot.scout_location = FakePoint(70.0, 75.0)
                bot.last_seen_enemy_area = FakePoint(105.0, 109.0)
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("executed", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertEqual(
                    expected_target_slot,
                    outcome.intent_dsl["target_slot"],
                )
                self.assertEqual(expected_plan_target, outcome.plan.ordered_actions[0].target)
                self.assertEqual(
                    expected_target_slot,
                    outcome.plan.ordered_actions[0].metadata["target_slot"],
                )
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertEqual(1, len(bot.camera_moves))
                destination = bot.camera_moves[0]
                self.assertEqual(
                    expected_point,
                    (float(destination.x), float(destination.y)),
                )
                self.assertEqual([], bot.issued_commands)

    async def test_unscouted_enemy_camera_targets_block_with_not_scouted_reason(
        self,
    ) -> None:
        cases = (
            ("적 본진 보여줘", "enemy main", "enemy_main"),
            ("상대 본진으로 화면 이동", "enemy main", "enemy_main"),
            ("적 앞마당 보여줘", "enemy natural", "enemy_natural"),
            ("상대 내추럴 보여줘", "enemy natural", "enemy_natural"),
            ("적 램프 보여줘", "enemy ramp", "enemy_ramp"),
            ("적 입구 보여줘", "enemy front", "enemy_front"),
            ("적 초크 보여줘", "enemy choke", "enemy_choke"),
            ("적 세번째 멀티 보여줘", "enemy third", "enemy_third"),
        )

        for command_text, expected_target, expected_plan_target in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.expansion_locations_list = [
                    FakePoint(30.0, 30.0),
                    FakePoint(45.0, 52.0),
                    FakePoint(65.0, 80.0),
                    FakePoint(95.0, 95.0),
                    FakePoint(115.0, 110.0),
                    FakePoint(130.0, 130.0),
                ]
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("blocked", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertEqual(expected_plan_target, outcome.plan.ordered_actions[0].target)
                self.assertTrue(outcome.feasibility.executable)
                self.assertFalse(outcome.execution_result.success)
                self.assertIn("정찰/관측되지 않아", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                report = outcome.execution_result.audit["action_reports"]["0"]
                self.assertFalse(report["applied"])
                self.assertEqual(1, report["requested_count"])
                self.assertEqual(0, report["issued_count"])
                self.assertEqual("unscouted_camera_target", report["detail"])
                self.assertEqual(expected_plan_target, report["audit"]["target"])
                self.assertIn("not been scouted", report["audit"]["reason"])
                self.assertIn("source", report["audit"])
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

    async def test_unscouted_korean_camera_memory_target_blocks_without_moving(
        self,
    ) -> None:
        cases = (
            ("정찰한 곳 보여줘", "scout location", MOVE_CAMERA_SCOUT_LOCATION_TARGET_SLOT),
            (
                "마지막으로 본 적 보여줘",
                "last seen enemy area",
                MOVE_CAMERA_LAST_SEEN_ENEMY_AREA_TARGET_SLOT,
            ),
        )

        for command_text, expected_target, expected_target_slot in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("blocked", outcome.status)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertEqual(
                    expected_target_slot,
                    outcome.intent_dsl["target_slot"],
                )
                self.assertFalse(outcome.execution_result.success)
                self.assertIn("정찰/관측되지 않아", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                report = outcome.execution_result.audit["action_reports"]["0"]
                self.assertEqual("unscouted_camera_target", report["detail"])
                self.assertIn("reason", report["audit"])
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

    async def test_unscouted_camera_memory_target_blocks_with_concrete_reason(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcome = (await session.process_text("마지막 적 위치 보여줘"))[0]

        self.assertEqual("blocked", outcome.status)
        self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
        self.assertEqual("last seen enemy area", outcome.intent_dsl["target"])
        self.assertFalse(outcome.execution_result.success)
        self.assertIn("정찰/관측되지 않아", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        report = outcome.execution_result.audit["action_reports"]["0"]
        self.assertEqual("unscouted_camera_target", report["detail"])
        self.assertIn("reason", report["audit"])
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_unknown_semantic_camera_target_blocks_with_audited_reason(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        resolver = UnavailableMapResolver(
            target="enemy_main",
            reason="Unsupported semantic map target: 'enemy_main'.",
            alternatives=("self_main", "self_ramp"),
        )
        adapter = PythonSC2BotAdapter(bot=bot, map_resolver=resolver)
        session = make_session(
            bot,
            interpreter=StaticInterpreter(
                MoveCameraIntent(
                    priority="normal",
                    constraints=("move camera to semantic target",),
                    target="enemy main",
                )
            ),
            executor=SC2RuntimeExecutor(bot=adapter),
        )

        outcome = (await session.process_text("알 수 없는 곳으로 카메라 옮겨"))[0]

        self.assertEqual("blocked", outcome.status)
        self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
        self.assertFalse(outcome.execution_result.success)
        self.assertIn("지원되는 semantic target이 아닙니다", outcome.narration)
        report = outcome.execution_result.audit["action_reports"]["0"]
        self.assertEqual("unknown_camera_target", report["detail"])
        self.assertEqual("enemy_main", report["audit"]["target"])
        self.assertIn("Unsupported semantic map target", report["audit"]["reason"])
        self.assertEqual(["self_main", "self_ramp"], report["audit"]["alternatives"])
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_ambiguous_semantic_camera_target_blocks_without_moving(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        resolver = UnavailableMapResolver(
            target="self_choke",
            reason="Ambiguous semantic camera target: multiple coordinate matches.",
            alternatives=("self_choke", "enemy_choke"),
        )
        adapter = PythonSC2BotAdapter(bot=bot, map_resolver=resolver)
        session = make_session(
            bot,
            interpreter=StaticInterpreter(
                MoveCameraIntent(
                    priority="normal",
                    constraints=("move camera to semantic target",),
                    target="natural choke",
                )
            ),
            executor=SC2RuntimeExecutor(bot=adapter),
        )

        outcome = (await session.process_text("초크로 화면 이동"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertIn("여러 후보", outcome.narration)
        self.assertIn("필요한 정보(target)", outcome.narration)
        self.assertIn("self_choke, enemy_choke", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_generic_command_center_camera_request_clarifies_only_with_multiple_bases(
        self,
    ) -> None:
        single_base_bot = LivePipelineFakeBot()
        single_base_session = make_session(single_base_bot)

        single_base_outcome = (
            await single_base_session.process_text("사령부로 카메라 옮겨")
        )[0]

        self.assertEqual("executed", single_base_outcome.status)
        self.assertEqual("MOVE_CAMERA", single_base_outcome.intent_dsl["intent"])
        self.assertEqual(1, len(single_base_bot.camera_moves))
        single_base_destination = single_base_bot.camera_moves[0]
        self.assertEqual(
            (30.0, 30.0),
            (float(single_base_destination.x), float(single_base_destination.y)),
        )

        ambiguous_camera_commands = (
            "사령부로 카메라 옮겨",
            "커맨드 센터로 화면 이동",
            "기지로 화면 이동",
            "멀티 보여줘",
            "확장으로 카메라 옮겨",
        )
        for command_text in ambiguous_camera_commands:
            with self.subTest(command_text=command_text):
                multi_base_bot = LivePipelineFakeBot()
                multi_base_bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
                multi_base_session = make_session(multi_base_bot)

                outcomes = await multi_base_session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("clarification", outcome.status)
                self.assertIn("어느 사령부", outcome.narration)
                self.assertIn("가능한 선택지", outcome.narration)
                self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
                self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertNotIn("LLM 해석에 실패", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], multi_base_bot.camera_moves)
                self.assertEqual([], multi_base_bot.issued_commands)

    async def test_camera_base_clarification_answer_moves_to_selected_base(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            validator=validator,
            planner=planner,
            executor=executor,
        )

        first_outcome = (await session.process_text("사령부로 카메라 옮겨"))[0]

        self.assertEqual("clarification", first_outcome.status)
        self.assertIn("어느 사령부", first_outcome.narration)
        self.assertIn("가능한 선택지", first_outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", first_outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", first_outcome.narration)
        self.assertIsNone(first_outcome.intent_dsl)
        self.assertIsNone(first_outcome.plan)
        self.assertIsNone(first_outcome.execution_result)
        self.assertIsNone(first_outcome.feasibility)
        self.assertEqual([], validator.calls)
        self.assertEqual([], planner.calls)
        self.assertEqual([], executor.calls)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

        second_outcome = (await session.process_text("앞마당 사령부"))[0]

        self.assertEqual("executed", second_outcome.status)
        self.assertEqual("MOVE_CAMERA", second_outcome.intent_dsl["intent"])
        self.assertEqual("natural expansion", second_outcome.intent_dsl["target"])
        self.assertTrue(second_outcome.feasibility.executable)
        self.assertTrue(second_outcome.execution_result.success)
        self.assertEqual(["MOVE_CAMERA"], validator.calls)
        self.assertEqual(["MOVE_CAMERA"], planner.calls)
        self.assertEqual(["MOVE_CAMERA"], executor.calls)
        self.assertEqual(1, len(bot.camera_moves))
        destination = bot.camera_moves[0]
        self.assertEqual((45.0, 52.0), (float(destination.x), float(destination.y)))
        self.assertEqual([], bot.issued_commands)

    async def test_camera_base_clarification_does_not_swallow_new_base_command(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        bot.structures.append(FakeUnit("SupplyDepot", 34.0, 31.0))
        session = make_session(bot)

        first_outcome = (await session.process_text("사령부로 카메라 옮겨"))[0]
        second_outcome = (await session.process_text("본진에 보급고 지어"))[0]

        self.assertEqual("clarification", first_outcome.status)
        self.assertIn(second_outcome.status, {"blocked", "executed"})
        self.assertEqual("BUILD_STRUCTURE", second_outcome.intent_dsl["intent"])
        self.assertEqual([], bot.camera_moves)
        self.assertIsNotNone(second_outcome.feasibility)
        self.assertTrue(second_outcome.feasibility.executable)

    async def test_unresolved_camera_base_clarification_reasks_concrete_target(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            validator=validator,
            planner=planner,
            executor=executor,
        )

        first_outcome = (await session.process_text("사령부로 카메라 옮겨"))[0]
        unresolved_outcome = (await session.process_text("거기"))[0]

        self.assertEqual("clarification", first_outcome.status)
        self.assertEqual("clarification", unresolved_outcome.status)
        self.assertEqual("거기", unresolved_outcome.command_text)
        self.assertIn("어느 사령부", unresolved_outcome.narration)
        self.assertIn("필요한 정보(target)", unresolved_outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", unresolved_outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", unresolved_outcome.narration)
        self.assertNotIn("10개 MVP", unresolved_outcome.narration)
        self.assertNotIn("LLM 해석에 실패", unresolved_outcome.narration)
        self.assertIsNone(unresolved_outcome.intent_dsl)
        self.assertIsNone(unresolved_outcome.plan)
        self.assertIsNone(unresolved_outcome.execution_result)
        self.assertIsNone(unresolved_outcome.feasibility)
        self.assertEqual([], validator.calls)
        self.assertEqual([], planner.calls)
        self.assertEqual([], executor.calls)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

        resolved_outcome = (await session.process_text("앞마당 사령부"))[0]

        self.assertEqual("executed", resolved_outcome.status)
        self.assertEqual("MOVE_CAMERA", resolved_outcome.intent_dsl["intent"])
        self.assertEqual("natural expansion", resolved_outcome.intent_dsl["target"])
        self.assertEqual(["MOVE_CAMERA"], validator.calls)
        self.assertEqual(["MOVE_CAMERA"], planner.calls)
        self.assertEqual(["MOVE_CAMERA"], executor.calls)

    async def test_question_after_camera_clarification_stays_read_only(self) -> None:
        bot = LivePipelineFakeBot()
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        session = make_session(bot)

        first_outcome = (await session.process_text("사령부로 카메라 옮겨"))[0]
        question_outcome = (await session.process_text("왜 안돼?"))[0]

        self.assertEqual("clarification", first_outcome.status)
        self.assertEqual("read_only", question_outcome.status)
        self.assertEqual("ANSWER_QUESTION", question_outcome.intent_dsl["intent"])
        self.assertEqual("failure_reason_help", question_outcome.intent_dsl["topic"])
        self.assertTrue(question_outcome.intent_dsl["read_only"])
        self.assertIn("최근 실패 기록", question_outcome.narration)
        self.assertNotIn("10개 MVP", question_outcome.narration)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_generic_base_camera_request_clarifies_with_multiple_bases(
        self,
    ) -> None:
        multi_base_bot = LivePipelineFakeBot()
        multi_base_bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        multi_base_session = make_session(multi_base_bot)

        outcome = (await multi_base_session.process_text("기지로 화면 이동"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertIn("어느 사령부", outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertEqual([], multi_base_bot.camera_moves)
        self.assertEqual([], multi_base_bot.issued_commands)

    async def test_ambiguous_base_camera_reference_skips_all_mutating_layers(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            validator=validator,
            planner=planner,
            executor=executor,
        )

        outcome = (await session.process_text("기지로 화면 이동"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertIn("어느 사령부", outcome.narration)
        self.assertIn("가능한 선택지", outcome.narration)
        self.assertIn("필요한 정보(target)", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], validator.calls)
        self.assertEqual([], planner.calls)
        self.assertEqual([], executor.calls)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_korean_camera_command_center_request_clarifies_with_multiple_bases(
        self,
    ) -> None:
        multi_base_bot = LivePipelineFakeBot()
        multi_base_bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        multi_base_session = make_session(multi_base_bot)

        outcome = (await multi_base_session.process_text("카메라 사령부로 옮겨"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertIn("어느 사령부", outcome.narration)
        self.assertIn("가능한 선택지", outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], multi_base_bot.camera_moves)
        self.assertEqual([], multi_base_bot.issued_commands)

    async def test_korean_command_center_camera_phrasings_ask_concrete_clarification(
        self,
    ) -> None:
        for command_text in (
            "사령부 보여줘",
            "커맨드 센터로 화면 이동",
        ):
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("clarification", outcome.status)
                self.assertEqual(command_text, outcome.command_text)
                self.assertIn("어느 사령부", outcome.narration)
                self.assertIn("가능한 선택지", outcome.narration)
                self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
                self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
                self.assertIn("필요한 정보", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

    async def test_bare_expansion_camera_reference_clarifies_with_multiple_bases(
        self,
    ) -> None:
        for command_text in (
            "멀티 보여줘",
            "확장으로 카메라 옮겨",
        ):
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("clarification", outcome.status)
                self.assertEqual(command_text, outcome.command_text)
                self.assertIn("어느 사령부", outcome.narration)
                self.assertIn("가능한 선택지", outcome.narration)
                self.assertIn("필요한 정보(target)", outcome.narration)
                self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
                self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

    async def test_generic_command_center_build_near_request_clarifies_with_multiple_bases(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        session = make_session(bot)

        outcome = (await session.process_text("사령부 근처에 배럭 지어"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertEqual("사령부 근처에 배럭 지어", outcome.command_text)
        self.assertIn("배럭을 짓는 요청은 유지하겠습니다", outcome.narration)
        self.assertIn("어느 사령부 근처", outcome.narration)
        self.assertIn("가능한 선택지", outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
        self.assertIn("필요한 정보", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_command_center_build_near_without_structure_asks_for_building(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        session = make_session(bot)

        outcome = (await session.process_text("사령부 근처에 지어"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertEqual("사령부 근처에 지어", outcome.command_text)
        self.assertIn("어떤 건물", outcome.narration)
        self.assertIn("필요한 정보(structure)", outcome.narration)
        self.assertIn("사령부 근처에 보급고 지어", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

    async def test_command_center_build_near_without_structure_clarifies_base_first(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        session = make_session(bot)

        outcome = (await session.process_text("사령부 근처에 지어"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertEqual("사령부 근처에 지어", outcome.command_text)
        self.assertIn("건물을 짓는 요청은 유지하겠습니다", outcome.narration)
        self.assertIn("어느 사령부 근처", outcome.narration)
        self.assertIn("가능한 선택지", outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", outcome.narration)
        self.assertIn("필요한 정보(location)", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_build_near_clarified_base_still_asks_for_missing_structure(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        session = make_session(bot)

        first_outcome = (await session.process_text("사령부 근처에 지어"))[0]
        second_outcome = (await session.process_text("앞마당 사령부"))[0]

        self.assertEqual("clarification", first_outcome.status)
        self.assertIn("어느 사령부 근처", first_outcome.narration)
        self.assertEqual("clarification", second_outcome.status)
        self.assertEqual("앞마당 사령부 근처에 지어", second_outcome.command_text)
        self.assertIn("어떤 건물", second_outcome.narration)
        self.assertIn("필요한 정보(structure)", second_outcome.narration)
        self.assertIn("앞마당 사령부 근처에 벙커 지어", second_outcome.narration)
        self.assertNotIn("10개 MVP", second_outcome.narration)
        self.assertIsNone(second_outcome.intent_dsl)
        self.assertIsNone(second_outcome.plan)
        self.assertIsNone(second_outcome.execution_result)
        self.assertIsNone(second_outcome.feasibility)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.camera_moves)
        self.assertEqual([], bot.issued_commands)

    async def test_unresolved_build_base_clarification_reasks_concrete_location(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        bot.structures.append(FakeUnit("SupplyDepot", 34.0, 31.0))
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            validator=validator,
            planner=planner,
            executor=executor,
        )

        first_outcome = (await session.process_text("사령부 근처에 보급고 지어"))[0]
        unresolved_outcome = (await session.process_text("아무데나"))[0]

        self.assertEqual("clarification", first_outcome.status)
        self.assertEqual("clarification", unresolved_outcome.status)
        self.assertEqual("아무데나", unresolved_outcome.command_text)
        self.assertIn("보급고를 짓는 요청은 유지하겠습니다", unresolved_outcome.narration)
        self.assertIn("어느 사령부 근처", unresolved_outcome.narration)
        self.assertIn("필요한 정보(location)", unresolved_outcome.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", unresolved_outcome.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", unresolved_outcome.narration)
        self.assertNotIn("10개 MVP", unresolved_outcome.narration)
        self.assertNotIn("LLM 해석에 실패", unresolved_outcome.narration)
        self.assertIsNone(unresolved_outcome.intent_dsl)
        self.assertIsNone(unresolved_outcome.plan)
        self.assertIsNone(unresolved_outcome.execution_result)
        self.assertIsNone(unresolved_outcome.feasibility)
        self.assertEqual([], validator.calls)
        self.assertEqual([], planner.calls)
        self.assertEqual([], executor.calls)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

        resolved_outcome = (await session.process_text("앞마당 사령부"))[0]

        self.assertEqual("blocked", resolved_outcome.status)
        self.assertEqual("앞마당 사령부 근처에 보급고 지어", resolved_outcome.command_text)
        self.assertEqual("BUILD_STRUCTURE", resolved_outcome.intent_dsl["intent"])
        self.assertEqual("Supply Depot", resolved_outcome.intent_dsl["structure"])
        self.assertEqual("natural expansion", resolved_outcome.intent_dsl["location"])
        self.assertEqual(["BUILD_STRUCTURE"], validator.calls)
        self.assertEqual(["BUILD_STRUCTURE"], planner.calls)
        self.assertEqual(["BUILD_STRUCTURE"], executor.calls)

    async def test_build_base_clarification_answer_preserves_pending_command(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        bot.structures.append(FakeUnit("SupplyDepot", 34.0, 31.0))
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            validator=validator,
            planner=planner,
            executor=executor,
        )

        clarification = (await session.process_text("사령부 근처에 보급고 지어"))[0]

        self.assertEqual("clarification", clarification.status)
        self.assertIn("보급고를 짓는 요청은 유지하겠습니다", clarification.narration)
        self.assertIn("어느 사령부 근처", clarification.narration)
        self.assertIn("필요한 정보(location)", clarification.narration)
        self.assertIn("가능한 선택지", clarification.narration)
        self.assertIn("본진 사령부(30.0, 30.0)", clarification.narration)
        self.assertIn("앞마당 사령부(45.0, 52.0)", clarification.narration)
        self.assertNotIn("10개 MVP", clarification.narration)
        self.assertIsNone(clarification.intent_dsl)
        self.assertIsNone(clarification.plan)
        self.assertIsNone(clarification.execution_result)
        self.assertIsNone(clarification.feasibility)
        self.assertEqual([], validator.calls)
        self.assertEqual([], planner.calls)
        self.assertEqual([], executor.calls)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

        outcome = (await session.process_text("앞마당 사령부"))[0]

        self.assertEqual("blocked", outcome.status)
        self.assertEqual("앞마당 사령부 근처에 보급고 지어", outcome.command_text)
        self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
        self.assertEqual("Supply Depot", outcome.intent_dsl["structure"])
        self.assertEqual("natural expansion", outcome.intent_dsl["location"])
        self.assertTrue(outcome.feasibility.executable)
        self.assertEqual("BUILD_STRUCTURE", outcome.plan.intent)
        self.assertEqual("self_natural", outcome.plan.ordered_actions[0].target)
        self.assertFalse(outcome.execution_result.success)
        self.assertIn("건설 거부", outcome.narration)
        self.assertIn("build_refused", outcome.narration)
        self.assertEqual(["BUILD_STRUCTURE"], validator.calls)
        self.assertEqual(["BUILD_STRUCTURE"], planner.calls)
        self.assertEqual(["BUILD_STRUCTURE"], executor.calls)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

    async def test_korean_command_center_camera_clarification_answers_execute_target(
        self,
    ) -> None:
        cases = (
            ("본진 사령부", "main base", (30.0, 30.0)),
            ("앞마당으로 카메라 옮겨", "natural expansion", (45.0, 52.0)),
        )
        for answer_text, expected_target, expected_point in cases:
            with self.subTest(answer_text=answer_text):
                bot = LivePipelineFakeBot()
                bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
                validator = RecordingValidator()
                planner = RecordingPlanner()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(
                    bot,
                    validator=validator,
                    planner=planner,
                    executor=executor,
                )

                clarification = (await session.process_text("카메라 사령부로 옮겨"))[0]

                self.assertEqual("clarification", clarification.status)
                self.assertIn("어느 사령부", clarification.narration)
                self.assertIn("필요한 정보(target)", clarification.narration)
                self.assertIn("가능한 선택지", clarification.narration)
                self.assertIn("본진 사령부(30.0, 30.0)", clarification.narration)
                self.assertIn("앞마당 사령부(45.0, 52.0)", clarification.narration)
                self.assertNotIn("10개 MVP", clarification.narration)
                self.assertIsNone(clarification.intent_dsl)
                self.assertIsNone(clarification.plan)
                self.assertIsNone(clarification.execution_result)
                self.assertIsNone(clarification.feasibility)
                self.assertEqual([], validator.calls)
                self.assertEqual([], planner.calls)
                self.assertEqual([], executor.calls)
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

                outcome = (await session.process_text(answer_text))[0]

                self.assertEqual("executed", outcome.status)
                self.assertEqual(answer_text, outcome.command_text)
                self.assertEqual("MOVE_CAMERA", outcome.intent_dsl["intent"])
                self.assertEqual(expected_target, outcome.intent_dsl["target"])
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertEqual(["MOVE_CAMERA"], validator.calls)
                self.assertEqual(["MOVE_CAMERA"], planner.calls)
                self.assertEqual(["MOVE_CAMERA"], executor.calls)
                self.assertEqual(1, len(bot.camera_moves))
                destination = bot.camera_moves[0]
                self.assertEqual(
                    expected_point,
                    (float(destination.x), float(destination.y)),
                )
                self.assertEqual([], bot.issued_commands)

    async def test_explicit_future_base_selectors_do_not_trigger_clarification(
        self,
    ) -> None:
        cases = (
            ("third base로 카메라 옮겨", "MOVE_CAMERA", "target", "third base"),
            (
                "새로 지은 사령부 주변에 보급고 지어",
                "BUILD_STRUCTURE",
                "location",
                "newest base",
            ),
            (
                "추가 사령부 1 근처에 보급고 지어",
                "BUILD_STRUCTURE",
                "location",
                "additional base 1",
            ),
        )

        for command_text, intent, field_name, expected_value in cases:
            with self.subTest(command_text=command_text):
                session = make_session(LivePipelineFakeBot(minerals=1000, supply_left=10))

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("blocked", outcome.status)
                self.assertEqual(intent, outcome.intent_dsl["intent"])
                self.assertEqual(expected_value, outcome.intent_dsl[field_name])
                self.assertRegex(outcome.narration, r"실행하지 (않았|못했)습니다")
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNotNone(outcome.feasibility)
                self.assertTrue(outcome.feasibility.executable)

    async def test_opening_macro_expands_to_multiple_safe_commands(self) -> None:
        trigger_phrases = (
            "초반 운영 시작해",
            "초반 빌드 오더 시작해",
            "초반세팅해줘",
            "오프닝 작전 시작",
            "opening operation start",
        )
        for trigger_phrase in trigger_phrases:
            with self.subTest(trigger_phrase=trigger_phrase):
                session = make_session(
                    LivePipelineFakeBot(minerals=900, supply_left=12)
                )

                outcomes = await session.process_text(trigger_phrase)

                self.assertEqual(3, len(outcomes))
                self.assertEqual(
                    ["TRAIN_WORKER", "BUILD_STRUCTURE", "SCOUT"],
                    [outcome.intent_dsl["intent"] for outcome in outcomes],
                )
                self.assertTrue(
                    all(outcome.status != "clarification" for outcome in outcomes)
                )

    async def test_scout_barracks_macro_expands_to_safe_commands(self) -> None:
        trigger_phrases = (
            "정찰보내고 병영올려",
            "정찰 보내고 병영 올려",
            "정찰 보내고 배럭 지어",
            "스카우트 보내고 배럭 올려",
            "scout and build barracks",
        )
        for trigger_phrase in trigger_phrases:
            with self.subTest(trigger_phrase=trigger_phrase):
                bot = LivePipelineFakeBot(minerals=900, supply_left=12)
                bot.structures.append(FakeUnit("SupplyDepot", 34.0, 31.0))
                session = make_session(bot)

                outcomes = await session.process_text(trigger_phrase)

                self.assertEqual(2, len(outcomes))
                self.assertEqual(
                    ["SCOUT", "BUILD_STRUCTURE"],
                    [outcome.intent_dsl["intent"] for outcome in outcomes],
                )
                self.assertTrue(
                    all(outcome.status != "clarification" for outcome in outcomes)
                )

    async def test_scout_barracks_macro_uses_static_template_when_planner_fails(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        bot.structures.append(FakeUnit("SupplyDepot", 34.0, 31.0))
        session = make_session(
            bot,
            interpreter=FailingComboPlanningInterpreter(),
        )

        outcomes = await session.process_text("정찰보내고 병영올려")

        self.assertEqual(2, len(outcomes))
        self.assertEqual(
            ["SCOUT", "BUILD_STRUCTURE"],
            [outcome.intent_dsl["intent"] for outcome in outcomes],
        )
        self.assertTrue(all(outcome.status != "clarification" for outcome in outcomes))

    async def test_economy_stabilization_macro_expands_to_safe_commands(self) -> None:
        trigger_phrases = (
            "경제 안정화해",
            "경제안정시켜",
            "자원 안정화",
            "경제 최적화",
            "stabilize economy",
        )
        for trigger_phrase in trigger_phrases:
            with self.subTest(trigger_phrase=trigger_phrase):
                session = make_session(
                    LivePipelineFakeBot(minerals=900, supply_left=12)
                )

                outcomes = await session.process_text(trigger_phrase)

                self.assertEqual(3, len(outcomes))
                self.assertEqual(
                    ["TRAIN_WORKER", "GATHER_RESOURCE", "BUILD_STRUCTURE"],
                    [outcome.intent_dsl["intent"] for outcome in outcomes],
                )
                self.assertTrue(
                    all(outcome.status != "clarification" for outcome in outcomes)
                )

    async def test_economy_stabilization_macro_uses_static_template_when_planner_fails(
        self,
    ) -> None:
        session = make_session(
            LivePipelineFakeBot(minerals=900, supply_left=12),
            interpreter=FailingComboPlanningInterpreter(),
        )

        outcomes = await session.process_text("경제 안정화해")

        self.assertEqual(3, len(outcomes))
        self.assertEqual(
            ["TRAIN_WORKER", "GATHER_RESOURCE", "BUILD_STRUCTURE"],
            [outcome.intent_dsl["intent"] for outcome in outcomes],
        )
        self.assertTrue(all(outcome.status != "clarification" for outcome in outcomes))

    async def test_detected_macro_invokes_combo_plan_before_static_macro_split(
        self,
    ) -> None:
        interpreter = ComboPlanningInterpreter(("정찰보내", "마린 생산해"))
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        bot.structures.append(FakeUnit("Barracks", 35.0, 32.0))
        session = make_session(
            bot,
            interpreter=interpreter,
        )

        outcomes = await session.process_text("초반 운영 시작해")

        self.assertEqual(["초반 운영 시작해"], interpreter.combo_plan_requests)
        self.assertEqual(2, len(outcomes))
        self.assertEqual(
            ["SCOUT", "TRAIN_ARMY"],
            [outcome.intent_dsl["intent"] for outcome in outcomes],
        )

    async def test_llm_combo_plan_steps_execute_through_existing_pipeline(self) -> None:
        session = make_session(
            LivePipelineFakeBot(minerals=900, supply_left=12),
            interpreter=ComboPlanningInterpreter(("정찰보내", "보급고 지어")),
        )

        outcomes = await session.process_text("정찰부터 하고 생산 건물도 올려")

        self.assertEqual(2, len(outcomes))
        self.assertEqual(
            ["SCOUT", "BUILD_STRUCTURE"],
            [o.intent_dsl["intent"] for o in outcomes],
        )
        self.assertTrue(
            all(outcome.status != "clarification" for outcome in outcomes)
        )

    async def test_macro_combo_examples_preserve_step_and_execution_order(
        self,
    ) -> None:
        cases = (
            {
                "command_text": "초반 운영 시작해",
                "steps": ("일꾼 계속 찍어", "보급고 지어", "정찰보내"),
                "intents": ("TRAIN_WORKER", "BUILD_STRUCTURE", "SCOUT"),
                "events": (
                    ("do", ("train", "CommandCenter", "SCV")),
                    ("build", "SUPPLYDEPOT", (30.0, 28.0)),
                    ("do", ("move", "SCV", (122.0, 124.0))),
                ),
                "standing_orders": True,
                "extra_structures": (),
            },
            {
                "command_text": "정찰보내고 병영올려",
                "steps": ("정찰보내", "병영올려"),
                "intents": ("SCOUT", "BUILD_STRUCTURE"),
                "events": (
                    ("do", ("move", "SCV", (122.0, 124.0))),
                    ("build", "BARRACKS", (30.0, 28.0)),
                ),
                "standing_orders": False,
                "extra_structures": (FakeUnit("SupplyDepot", 34.0, 31.0),),
            },
        )

        for case in cases:
            with self.subTest(command_text=case["command_text"]):
                bot = LivePipelineFakeBot(
                    minerals=900,
                    supply_left=12,
                    supports_build=True,
                )
                bot.structures.extend(case["extra_structures"])
                validator = RecordingValidator()
                planner = RecordingPlanner()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(
                    bot,
                    interpreter=ComboPlanningInterpreter(case["steps"]),
                    validator=validator,
                    planner=planner,
                    executor=executor,
                    standing_orders=(
                        StandingOrderController()
                        if case["standing_orders"]
                        else None
                    ),
                )

                outcomes = await session.process_text(case["command_text"])

                self.assertEqual(list(case["steps"]), [o.command_text for o in outcomes])
                self.assertEqual(
                    list(case["intents"]),
                    [o.intent_dsl["intent"] for o in outcomes],
                )
                self.assertEqual(list(case["intents"]), executor.calls)
                self.assertEqual(
                    list(case["intents"]) * 2,
                    validator.calls,
                )
                self.assertEqual(validator.calls, planner.calls)
                self.assertEqual(list(case["events"]), bot.execution_events)
                for index, outcome in enumerate(outcomes, start=1):
                    with self.subTest(step=index):
                        log = outcome.intent_dsl["combo_step_execution_log"]
                        self.assertEqual(index, log["step_index"])
                        self.assertEqual(len(case["steps"]), log["step_count"])
                        self.assertEqual(
                            f"combo-step-{index}-of-{len(case['steps'])}",
                            log["step_id"],
                        )
                        self.assertEqual(
                            case["steps"][index - 1],
                            log["input_command"],
                        )
                        self.assertEqual(
                            case["intents"][index - 1],
                            log["validation_result"]["plan_intent_name"],
                        )
                        self.assertEqual("completed", log["execution_result"]["status"])

    async def test_llm_combo_steps_reenter_validator_planner_and_executor(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            interpreter=ComboPlanningInterpreter(("정찰보내", "보급고 지어")),
            validator=validator,
            planner=planner,
            executor=executor,
        )

        outcomes = await session.process_text("정찰부터 하고 생산 건물도 올려")

        self.assertEqual(["SCOUT", "BUILD_STRUCTURE"], executor.calls)
        self.assertEqual(
            ["SCOUT", "BUILD_STRUCTURE", "SCOUT", "BUILD_STRUCTURE"],
            validator.calls,
        )
        self.assertEqual(validator.calls, planner.calls)
        self.assertEqual(
            ["SCOUT", "BUILD_STRUCTURE"],
            [outcome.intent_dsl["intent"] for outcome in outcomes],
        )

    async def test_llm_combo_step_logs_include_validation_execution_and_timing(
        self,
    ) -> None:
        session = make_session(
            LivePipelineFakeBot(minerals=900, supply_left=12),
            interpreter=ComboPlanningInterpreter(("정찰보내", "보급고 지어")),
        )

        outcomes = await session.process_text("정찰부터 하고 생산 건물도 올려")

        self.assertEqual(2, len(outcomes))
        for index, outcome in enumerate(outcomes, start=1):
            with self.subTest(step=index):
                log = outcome.intent_dsl["combo_step_execution_log"]
                self.assertEqual(index, log["step_index"])
                self.assertEqual(2, log["step_count"])
                self.assertEqual(f"combo-step-{index}-of-2", log["step_id"])
                self.assertEqual(outcome.command_text, log["input_command"])
                self.assertTrue(log["validation_result"]["executable"])
                self.assertEqual("executable", log["validation_result"]["status"])
                self.assertEqual(
                    outcome.plan.intent_name,
                    log["validation_result"]["plan_intent_name"],
                )
                self.assertEqual("completed", log["execution_result"]["status"])
                self.assertEqual(
                    outcome.execution_result.success,
                    log["execution_result"]["success"],
                )
                self.assertEqual(
                    outcome.plan.intent_name,
                    log["execution_result"]["intent_name"],
                )
                self.assertGreaterEqual(log["timing"]["total_ms"], 0.0)
                self.assertGreaterEqual(log["timing"]["validation_ms"], 0.0)
                self.assertGreaterEqual(log["timing"]["execution_ms"], 0.0)
                self.assertEqual(
                    log,
                    outcome.execution_result.audit["combo_step_execution_log"],
                )

        json.dumps([outcome.to_dict() for outcome in outcomes], ensure_ascii=False)

    async def test_llm_combo_step_post_validates_executor_result_before_next_step(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        executor = MismatchedComboResultExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        event_memory = CommanderEventMemory()
        session = make_session(
            bot,
            interpreter=ComboPlanningInterpreter(
                ("정찰보내", "보급고 지어", "SCV 계속 찍어")
            ),
            executor=executor,
            event_memory=event_memory,
        )

        outcomes = await session.process_text("정찰부터 하고 생산 건물도 올려")

        self.assertEqual(["SCOUT"], executor.calls)
        self.assertEqual(1, len(outcomes))
        self.assertEqual("blocked", outcomes[0].status)
        self.assertEqual("SCOUT", outcomes[0].intent_dsl["intent"])
        self.assertEqual("SCOUT", outcomes[0].plan.intent_name)
        self.assertEqual("TRAIN_ARMY", outcomes[0].execution_result.plan.intent_name)
        self.assertIn("ComboPlan 1/3단계에서 중단", outcomes[0].narration)
        self.assertIn("남은 2개 단계", outcomes[0].narration)
        self.assertIn("combo_plan 단계 실행 후 검증 실패", outcomes[0].narration)
        log = outcomes[0].intent_dsl["combo_step_execution_log"]
        self.assertEqual("contract_failed", log["execution_result"]["status"])
        self.assertIn("combo_plan 단계 실행 후 검증 실패", log["execution_result"]["reason"])
        summary = outcomes[0].intent_dsl["combo_plan_failure_summary"]
        self.assertEqual("stop_on_step_failure", summary["policy"])
        self.assertEqual("stop_remaining_steps", summary["decision"])
        self.assertEqual(1, summary["failed_step"]["step_index"])
        self.assertEqual("정찰보내", summary["failed_step"]["input_command"])
        self.assertEqual(2, summary["skipped_step_count"])
        self.assertEqual(
            ["보급고 지어", "SCV 계속 찍어"],
            [step["input_command"] for step in summary["skipped_steps"]],
        )
        self.assertEqual(
            summary,
            outcomes[0].execution_result.audit["combo_plan_failure_summary"],
        )
        self.assertEqual(
            log,
            outcomes[0].execution_result.audit["combo_step_execution_log"],
        )
        self.assertEqual(
            summary,
            event_memory.recent(1)[0].detail["intent_dsl"][
                "combo_plan_failure_summary"
            ],
        )
        self.assertEqual([], bot.issued_commands)

    async def test_llm_combo_planner_handles_collapsed_multi_action_text(self) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        bot.structures.append(FakeUnit("Barracks", 35.0, 32.0))
        session = make_session(
            bot,
            interpreter=ComboPlanningInterpreter(("마린 생산해", "정찰 보내")),
        )

        outcomes = await session.process_text("마린 생산해 정찰도 보내")

        self.assertEqual(2, len(outcomes))
        self.assertEqual(
            ["TRAIN_ARMY", "SCOUT"],
            [outcome.intent_dsl["intent"] for outcome in outcomes],
        )

    async def test_llm_combo_plan_preflights_feasibility_before_dispatch(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        session = make_session(
            bot,
            interpreter=ComboPlanningInterpreter(("정찰보내", "마린 생산해")),
        )

        outcomes = await session.process_text("정찰하고 병력도 뽑아")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("blocked", outcomes[0].status)
        self.assertEqual("TRAIN_ARMY", outcomes[0].intent_dsl["intent"])
        self.assertIn("병영", outcomes[0].narration)
        log = outcomes[0].intent_dsl["combo_step_execution_log"]
        self.assertEqual(2, log["step_index"])
        self.assertEqual(2, log["step_count"])
        self.assertEqual("마린 생산해", log["input_command"])
        self.assertFalse(log["validation_result"]["executable"])
        self.assertEqual("blocked", log["validation_result"]["status"])
        self.assertEqual("not_started", log["execution_result"]["status"])
        self.assertIn("preflight", log["execution_result"]["reason"])
        self.assertEqual(0.0, log["timing"]["execution_ms"])
        self.assertEqual([], bot.issued_commands)

    async def test_llm_combo_preflight_failure_skips_remaining_policy_tail(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        session = make_session(
            bot,
            interpreter=ComboPlanningInterpreter(
                ("정찰보내", "마린 생산해", "보급고 지어")
            ),
        )

        outcomes = await session.process_text("정찰하고 병력도 뽑고 보급도 지어")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("blocked", outcomes[0].status)
        self.assertIn("ComboPlan 2/3단계에서 중단", outcomes[0].narration)
        self.assertIn("남은 1개 단계", outcomes[0].narration)
        summary = outcomes[0].intent_dsl["combo_plan_failure_summary"]
        self.assertEqual("stop_on_step_failure", summary["policy"])
        self.assertEqual("stop_remaining_steps", summary["decision"])
        self.assertEqual(2, summary["failed_step"]["step_index"])
        self.assertEqual("마린 생산해", summary["failed_step"]["input_command"])
        self.assertEqual(1, summary["skipped_step_count"])
        self.assertEqual("보급고 지어", summary["skipped_steps"][0]["input_command"])
        self.assertEqual([], bot.issued_commands)

    async def test_llm_combo_plan_preflights_target_resolution_before_dispatch(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        session = make_session(
            bot,
            interpreter=InvalidTargetComboPlanningInterpreter(),
        )

        outcomes = await session.process_text("정찰하고 비밀 기지도 봐")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("blocked", outcomes[0].status)
        self.assertEqual("SCOUT", outcomes[0].intent_dsl["intent"])
        self.assertIn("secret moon base", outcomes[0].narration)
        self.assertEqual([], bot.issued_commands)

    async def test_unsupported_compact_compound_invokes_combo_plan(self) -> None:
        interpreter = ComboPlanningInterpreter(("마린 생산해", "정찰 보내"))
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        bot.structures.append(FakeUnit("Barracks", 35.0, 32.0))
        session = make_session(
            bot,
            interpreter=interpreter,
        )

        outcomes = await session.process_text("시즈업하고 탱크 뽑아")

        self.assertEqual(["시즈업하고 탱크 뽑아"], interpreter.combo_plan_requests)
        self.assertEqual(2, len(outcomes))
        self.assertEqual(
            ["TRAIN_ARMY", "SCOUT"],
            [outcome.intent_dsl["intent"] for outcome in outcomes],
        )
        self.assertTrue(all(outcome.status != "clarification" for outcome in outcomes))

    async def test_unsupported_compact_compound_without_planner_asks_for_combo(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        session = make_session(bot)

        outcomes = await session.process_text("핵쏘고 스캔해")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("clarification", outcomes[0].status)
        self.assertIn("combo_plan", outcomes[0].narration)
        self.assertEqual([], bot.issued_commands)

    async def test_unclear_compound_with_unresolved_game_part_clarifies_before_actions(
        self,
    ) -> None:
        cases = (
            "정찰 보내고 알아서 막아",
            "정찰 보내고 탱크 뽑아",
            "정찰 보내고 거기 지어",
        )
        for command_text in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot(minerals=900, supply_left=12)
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("clarification", outcome.status)
                self.assertEqual(command_text, outcome.command_text)
                self.assertIn("combo_plan", outcome.narration)
                self.assertIn("나눠 말해 주세요", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_korean_sequential_compound_connectors_execute_per_part(
        self,
    ) -> None:
        cases = (
            (
                "정찰 보내고 나서 보급고 지어",
                ("SCOUT", "BUILD_STRUCTURE"),
            ),
            (
                "마린 생산한 다음 정찰 보내",
                ("TRAIN_ARMY", "SCOUT"),
            ),
        )
        for command_text, expected_intents in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot(minerals=900, supply_left=12)
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(2, len(outcomes))
                self.assertEqual(
                    list(expected_intents),
                    [outcome.intent_dsl["intent"] for outcome in outcomes],
                )
                self.assertTrue(
                    all(outcome.status != "clarification" for outcome in outcomes)
                )

    async def test_progressive_question_remains_single_read_only_outcome(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("지금 뭐 하고 있어?")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("read_only", outcome.status)
        self.assertEqual("SUMMARIZE_STATE", outcome.intent_dsl["intent"])
        self.assertEqual([], bot.issued_commands)

    async def test_collapsed_multi_action_text_does_not_execute_one_guess(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        session = make_session(bot)

        outcomes = await session.process_text("SCV 계속 찍어 보급고 지어")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("clarification", outcomes[0].status)
        self.assertIn("combo_plan", outcomes[0].narration)
        self.assertEqual([], bot.issued_commands)

    async def test_invalid_llm_combo_plan_falls_back_to_clarification(self) -> None:
        session = make_session(
            LivePipelineFakeBot(),
            interpreter=ComboPlanningInterpreter(("핵 발사해", "정찰보내")),
        )

        outcomes = await session.process_text("알아서 세게 이겨")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("clarification", outcomes[0].status)

    async def test_failing_llm_combo_plan_falls_back_to_clarification(self) -> None:
        session = make_session(
            LivePipelineFakeBot(),
            interpreter=FailingComboPlanningInterpreter(),
        )

        outcomes = await session.process_text("알아서 세게 이겨")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("clarification", outcomes[0].status)

    async def test_ambiguous_distance_build_requests_clarify_without_building(
        self,
    ) -> None:
        cases = (
            (
                "더 멀게",
                "어디를 기준으로, 어느 방향으로 더 멀게 지을까요",
            ),
            (
                "더 멀게 지어",
                "어디를 기준으로, 어느 방향으로 더 멀게 지을까요",
            ),
            (
                "보급고 더 멀게 지어",
                "어디를 기준으로, 어느 방향으로 더 멀게 지을까요",
            ),
            (
                "보급고 더 멀게",
                "어디를 기준으로, 어느 방향으로 더 멀게 지을까요",
            ),
            (
                "본진에서 더 멀게 보급고 지어",
                "어느 방향으로 더 멀게 지을까요",
            ),
            (
                "근처에 보급고 지어",
                "어느 기준 위치나 방향으로 지을까요",
            ),
            (
                "쪽으로 배럭 지어",
                "어느 기준 위치나 방향으로 지을까요",
            ),
            (
                "떨어지게 보급고 지어",
                "어느 기준 위치나 방향으로 지을까요",
            ),
        )

        for command_text, expected_question in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual(command_text, outcome.command_text)
                self.assertEqual("clarification", outcome.status)
                self.assertIn("필요한 정보", outcome.narration)
                self.assertIn(expected_question, outcome.narration)
                self.assertIn("요청은 유지하겠습니다", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], bot.issued_commands)

    async def test_unanchored_relative_action_targets_clarify_before_execution(
        self,
    ) -> None:
        cases = (
            (
                "근처로 카메라 옮겨",
                MoveCameraIntent(
                    priority="normal",
                    constraints=("move camera to semantic target",),
                    target="main base",
                ),
                "카메라 이동",
            ),
            (
                "쪽으로 마린 보내",
                DefendIntent(
                    priority="normal",
                    constraints=("hold ramp against early pressure",),
                    location="main ramp",
                    unit_group="available combat units",
                ),
                "병력 이동/방어",
            ),
        )

        for command_text, guessed_payload, expected_label in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
                validator = RecordingValidator()
                planner = RecordingPlanner()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(
                    bot,
                    interpreter=StaticInterpreter(guessed_payload),
                    validator=validator,
                    planner=planner,
                    executor=executor,
                )

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual(command_text, outcome.command_text)
                self.assertEqual("clarification", outcome.status)
                self.assertIn(expected_label, outcome.narration)
                self.assertIn("필요한 정보(target)", outcome.narration)
                self.assertIn("어느 기준 위치나 대상으로 실행할까요", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], validator.calls)
                self.assertEqual([], planner.calls)
                self.assertEqual([], executor.calls)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_unanchored_relative_build_guess_clarifies_but_anchored_variants_execute(
        self,
    ) -> None:
        guessed_payload = BuildStructureIntent(
            priority="normal",
            constraints=("construct requested Terran structure",),
            structure="Supply Depot",
            location="main ramp",
        )
        bot = LivePipelineFakeBot(
            minerals=1000,
            supply_left=10,
            supports_build=True,
        )
        validator = RecordingValidator()
        planner = RecordingPlanner()
        executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
        )
        session = make_session(
            bot,
            interpreter=StaticInterpreter(guessed_payload),
            validator=validator,
            planner=planner,
            executor=executor,
        )

        outcome = (await session.process_text("근처에 보급고 지어"))[0]

        self.assertEqual("clarification", outcome.status)
        self.assertIn("어느 기준 위치나 방향으로 지을까요", outcome.narration)
        self.assertNotIn("10개 MVP", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], validator.calls)
        self.assertEqual([], planner.calls)
        self.assertEqual([], executor.calls)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual([], bot.camera_moves)

        executing_cases = (
            (
                "앞마당 근처 보급고 지어",
                "self_natural",
                "near",
                (45.0, 49.0),
            ),
            (
                "입구 쪽으로 보급고 지어",
                "self_ramp",
                "toward",
                (34.8, 33.6),
            ),
        )
        for command_text, expected_anchor_target, expected_relation, expected_point in (
            executing_cases
        ):
            with self.subTest(command_text=command_text):
                executing_bot = LivePipelineFakeBot(
                    minerals=1000,
                    supply_left=10,
                    supports_build=True,
                )
                executing_session = make_session(executing_bot)

                executing_outcome = (
                    await executing_session.process_text(command_text)
                )[0]

                self.assertEqual("executed", executing_outcome.status)
                self.assertEqual(
                    "BUILD_STRUCTURE",
                    executing_outcome.intent_dsl["intent"],
                )
                self.assertEqual(
                    "Supply Depot",
                    executing_outcome.intent_dsl["structure"],
                )
                self.assertTrue(executing_outcome.feasibility.executable)
                self.assertTrue(executing_outcome.execution_result.success)
                self.assertNotIn("어느 기준 위치나 방향", executing_outcome.narration)
                self.assertNotIn("10개 MVP", executing_outcome.narration)
                placement_policy = (
                    executing_outcome.plan.actions[0].metadata["placement_policy"]
                )
                self.assertEqual(
                    expected_anchor_target,
                    placement_policy["anchor_target"],
                )
                self.assertEqual(
                    expected_relation,
                    placement_policy["spatial_relation"],
                )
                self.assertEqual(1, len(executing_bot.build_calls))
                type_id, near = executing_bot.build_calls[0]
                self.assertEqual("SUPPLYDEPOT", type_id)
                self.assertAlmostEqual(expected_point[0], float(near.x))
                self.assertAlmostEqual(expected_point[1], float(near.y))
                self.assertEqual([], executing_bot.issued_commands)
                self.assertEqual([], executing_bot.camera_moves)

    async def test_korean_anchor_phrases_build_near_resolved_semantic_anchors(
        self,
    ) -> None:
        cases = (
            (
                "본진에서 멀게 보급고 지어",
                "self_main",
                (31.69001047392453, 32.47868202842264),
            ),
            (
                "미네랄에서 떨어지게 보급고 지어",
                "self_mineral_line",
                (26.84604989415154, 28.948683298050515),
            ),
            ("본진 입구에 보급고 지어", "self_ramp", (30.0, 27.0)),
            ("본진 입구에 서플라이 디포 지어", "self_ramp", (38.0, 33.0)),
            ("입구 쪽으로 보급고 지어", "self_ramp", (34.8, 33.6)),
            ("앞마당 근처 보급고 지어", "self_natural", (45.0, 49.0)),
            ("앞마당에 보급고 지어", "self_natural", (45.0, 49.0)),
            ("내추럴에 보급고 지어", "self_natural", (45.0, 49.0)),
            ("멀티에 보급고 지어", "self_natural", (45.0, 49.0)),
        )

        for command_text, expected_anchor_target, expected_point in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot(
                    minerals=1000,
                    supply_left=10,
                    supports_build=True,
                )
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("executed", outcome.status)
                self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
                self.assertEqual("Supply Depot", outcome.intent_dsl["structure"])
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                placement_policy = outcome.plan.actions[0].metadata["placement_policy"]
                self.assertEqual(expected_anchor_target, placement_policy["anchor_target"])
                action_report = outcome.execution_result.audit["action_reports"]["0"]
                placement_audit = action_report["audit"]
                self.assertEqual("", placement_audit["failure_reason"])
                self.assertEqual(
                    expected_anchor_target,
                    placement_audit["resolved_target_policy"]["anchor_target"],
                )
                anchor_source = placement_audit["resolved_target_policy"][
                    "anchor_source"
                ]
                self.assertTrue(str(anchor_source).strip())
                self.assertEqual(
                    anchor_source,
                    placement_audit["anchor_source"]["resolver_source"],
                )
                self.assertEqual(
                    expected_anchor_target,
                    placement_audit["placement_policy"]["anchor_target"],
                )
                resolved_policy = placement_audit["resolved_placement_policy"]
                self.assertEqual(
                    anchor_source,
                    resolved_policy["anchor_source"],
                )
                self.assertTrue(str(resolved_policy["anchor_target"]).strip())
                self.assertEqual(
                    {
                        "x": placement_audit["resolved_target_policy"][
                            "resolved_point"
                        ]["x"],
                        "y": placement_audit["resolved_target_policy"][
                            "resolved_point"
                        ]["y"],
                    },
                    resolved_policy["resolved_position"],
                )
                self.assertIsNotNone(
                    placement_audit["search_result"]["selected_tile"]
                )
                self.assertEqual(1, len(bot.build_calls))
                type_id, near = bot.build_calls[0]
                self.assertEqual("SUPPLYDEPOT", type_id)
                self.assertAlmostEqual(expected_point[0], float(near.x))
                self.assertAlmostEqual(expected_point[1], float(near.y))
                self.assertEqual([], bot.issued_commands)

    async def test_ac7_korean_build_examples_have_deterministic_fixtures(
        self,
    ) -> None:
        for fixture in KOREAN_BUILD_EXAMPLE_COMMAND_FIXTURES:
            with self.subTest(command_text=fixture["command_text"]):
                bot = LivePipelineFakeBot(
                    minerals=1000,
                    supply_left=10,
                    supports_build=True,
                )
                interpreter = DeterministicBuildFixtureInterpreter(
                    KOREAN_BUILD_EXAMPLE_COMMAND_FIXTURES
                )
                validator = RecordingValidator()
                planner = RecordingPlanner()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(
                    bot,
                    interpreter=interpreter,
                    validator=validator,
                    planner=planner,
                    executor=executor,
                )

                outcomes = await session.process_text(fixture["command_text"])

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual([fixture["command_text"]], interpreter.calls)
                self.assertEqual(fixture["command_text"], outcome.command_text)
                self.assertEqual(["BUILD_STRUCTURE"], validator.calls)
                self.assertEqual(["BUILD_STRUCTURE"], planner.calls)
                self.assertEqual(["BUILD_STRUCTURE"], executor.calls)
                self.assertEqual("executed", outcome.status)
                self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
                self.assertEqual(
                    fixture["payload"].structure,
                    outcome.intent_dsl["structure"],
                )
                self.assertEqual(
                    fixture["payload"].location,
                    outcome.intent_dsl["location"],
                )
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertNotIn("LLM 해석에 실패", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)

                placement_policy = outcome.plan.actions[0].metadata[
                    "placement_policy"
                ]
                self.assertEqual(
                    fixture["anchor_target"],
                    placement_policy["anchor_target"],
                )
                self.assertEqual(
                    fixture["spatial_relation"],
                    placement_policy["spatial_relation"],
                )
                action_reports = outcome.execution_result.audit.get(
                    "action_reports",
                    {},
                )
                if "0" in action_reports:
                    placement_audit = action_reports["0"]["audit"]
                    self.assertEqual("", placement_audit["failure_reason"])
                    self.assertEqual(
                        fixture["anchor_target"],
                        placement_audit["placement_policy"]["anchor_target"],
                    )
                self.assertEqual(1, len(bot.build_calls))
                type_id, near = bot.build_calls[0]
                self.assertEqual(fixture["type_id"], type_id)
                if "geyser" in fixture:
                    self.assertEqual("VespeneGeyser", near.name)
                    self.assertAlmostEqual(
                        fixture["geyser"][0],
                        float(near.position.x),
                    )
                    self.assertAlmostEqual(
                        fixture["geyser"][1],
                        float(near.position.y),
                    )
                else:
                    self.assertAlmostEqual(fixture["point"][0], float(near.x))
                    self.assertAlmostEqual(fixture["point"][1], float(near.y))
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_ac7_korean_example_ambiguous_variants_clarify_without_mutation(
        self,
    ) -> None:
        def multi_base_bot():
            bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
            bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
            return bot

        cases = (
            (
                "사령부 근처에 보급고 지어",
                multi_base_bot,
                (
                    "보급고를 짓는 요청은 유지하겠습니다",
                    "어느 사령부 근처",
                    "가능한 선택지",
                    "필요한 정보(location)",
                ),
            ),
            (
                "사령부로 카메라 옮겨",
                multi_base_bot,
                (
                    "어느 사령부",
                    "가능한 선택지",
                    "필요한 정보(target)",
                ),
            ),
            (
                "사령부 상태 알려줘",
                multi_base_bot,
                (
                    "어느 사령부/기지 상태",
                    "가능한 선택지",
                    "필요한 정보(target)",
                ),
            ),
            (
                "본진에서 더 멀게 보급고 지어",
                lambda: LivePipelineFakeBot(minerals=1000, supply_left=10),
                (
                    "필요한 정보",
                    "어느 방향으로 더 멀게 지을까요",
                    "요청은 유지하겠습니다",
                ),
            ),
            (
                "저기에 보급고 지어",
                lambda: LivePipelineFakeBot(minerals=1000, supply_left=10),
                (
                    "semantic target",
                    "지원되는",
                    "어디에 지을까요",
                    "본진 입구",
                    "실행하지 않았습니다",
                ),
            ),
        )

        for command_text, make_bot, expected_fragments in cases:
            with self.subTest(command_text=command_text):
                bot = make_bot()
                validator = RecordingValidator()
                planner = RecordingPlanner()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(
                    bot,
                    validator=validator,
                    planner=planner,
                    executor=executor,
                )

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual(command_text, outcome.command_text)
                self.assertEqual("clarification", outcome.status)
                for fragment in expected_fragments:
                    self.assertIn(fragment, outcome.narration)
                self.assertNotIn("LLM 해석에 실패", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], validator.calls)
                self.assertEqual([], planner.calls)
                self.assertEqual([], executor.calls)
                self.assertEqual([], bot.build_calls)
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

    async def test_required_korean_ambiguous_target_path_examples_clarify_without_mutation(
        self,
    ) -> None:
        def multi_base_bot():
            bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
            bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
            return bot

        cases = (
            (
                "더 멀게 지어",
                lambda: LivePipelineFakeBot(minerals=1000, supply_left=10),
                (
                    "건물을 더 멀게 짓는 요청은 유지하겠습니다",
                    "필요한 정보(location)",
                    "어디를 기준으로, 어느 방향으로 더 멀게 지을까요",
                ),
            ),
            (
                "저기에 지어",
                lambda: LivePipelineFakeBot(minerals=1000, supply_left=10),
                (
                    "건물을 짓는 요청은 유지하겠습니다",
                    "semantic target",
                    "필요한 정보(location)",
                    "어디에 지을까요",
                    "본진 입구",
                ),
            ),
            (
                "카메라 사령부로 옮겨",
                multi_base_bot,
                (
                    "어느 사령부",
                    "가능한 선택지",
                    "필요한 정보(target)",
                    "본진 사령부(30.0, 30.0)",
                    "앞마당 사령부(45.0, 52.0)",
                ),
            ),
            (
                "사령부 근처에 지어",
                multi_base_bot,
                (
                    "건물을 짓는 요청은 유지하겠습니다",
                    "어느 사령부 근처",
                    "필요한 정보(location)",
                    "본진 사령부(30.0, 30.0)",
                    "앞마당 사령부(45.0, 52.0)",
                ),
            ),
            (
                "사령부 근처에 배럭 지어",
                multi_base_bot,
                (
                    "배럭을 짓는 요청은 유지하겠습니다",
                    "어느 사령부 근처",
                    "필요한 정보(location)",
                    "본진 사령부(30.0, 30.0)",
                    "앞마당 사령부(45.0, 52.0)",
                ),
            ),
        )

        for command_text, make_bot, expected_fragments in cases:
            with self.subTest(command_text=command_text):
                bot = make_bot()
                validator = RecordingValidator()
                planner = RecordingPlanner()
                executor = RecordingExecutor(
                    SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=bot))
                )
                session = make_session(
                    bot,
                    validator=validator,
                    planner=planner,
                    executor=executor,
                )

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("clarification", outcome.status)
                self.assertEqual(command_text, outcome.command_text)
                for fragment in expected_fragments:
                    self.assertIn(fragment, outcome.narration)
                self.assertNotIn("LLM 해석에 실패", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], validator.calls)
                self.assertEqual([], planner.calls)
                self.assertEqual([], executor.calls)
                self.assertEqual([], bot.build_calls)
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

    async def test_ac7_korean_example_invalid_variants_expose_stable_failures(
        self,
    ) -> None:
        build_bot = LivePipelineFakeBot(
            minerals=0,
            supply_left=10,
            supports_build=True,
        )
        build_validator = RecordingValidator()
        build_planner = RecordingPlanner()
        build_executor = RecordingExecutor(
            SC2RuntimeExecutor(bot=PythonSC2BotAdapter(bot=build_bot))
        )
        build_session = make_session(
            build_bot,
            interpreter=DeterministicBuildFixtureInterpreter(
                KOREAN_BUILD_EXAMPLE_COMMAND_FIXTURES
            ),
            validator=build_validator,
            planner=build_planner,
            executor=build_executor,
        )

        build_outcome = (await build_session.process_text("앞마당에 사령부 지어"))[0]

        self.assertEqual("blocked", build_outcome.status)
        self.assertEqual("BUILD_STRUCTURE", build_outcome.intent_dsl["intent"])
        self.assertFalse(build_outcome.feasibility.executable)
        self.assertIn(
            "insufficient_minerals",
            build_outcome.feasibility.reason_codes,
        )
        self.assertIn("이유:", build_outcome.narration)
        self.assertIn("대안:", build_outcome.narration)
        self.assertNotIn("LLM 해석에 실패", build_outcome.narration)
        self.assertEqual(["BUILD_STRUCTURE"], build_validator.calls)
        self.assertEqual([], build_planner.calls)
        self.assertEqual([], build_executor.calls)
        self.assertEqual([], build_bot.build_calls)
        self.assertEqual([], build_bot.camera_moves)
        self.assertEqual([], build_bot.issued_commands)

        camera_bot = LivePipelineFakeBot()
        resolver = UnavailableMapResolver(
            target="enemy_main",
            reason="Unsupported semantic map target: enemy main is unscouted.",
            alternatives=("self_main", "self_ramp"),
        )
        camera_validator = RecordingValidator()
        camera_planner = RecordingPlanner()
        camera_executor = RecordingExecutor(
            SC2RuntimeExecutor(
                bot=PythonSC2BotAdapter(bot=camera_bot, map_resolver=resolver)
            )
        )
        camera_session = make_session(
            camera_bot,
            interpreter=StaticInterpreter(
                MoveCameraIntent(
                    priority="normal",
                    constraints=("move camera to semantic target",),
                    target="enemy main",
                )
            ),
            validator=camera_validator,
            planner=camera_planner,
            executor=camera_executor,
        )

        camera_outcome = (
            await camera_session.process_text("알 수 없는 곳으로 카메라 옮겨")
        )[0]

        self.assertEqual("blocked", camera_outcome.status)
        self.assertEqual("MOVE_CAMERA", camera_outcome.intent_dsl["intent"])
        self.assertTrue(camera_outcome.feasibility.executable)
        self.assertFalse(camera_outcome.execution_result.success)
        self.assertIn("아직 정찰/관측되지 않아", camera_outcome.narration)
        report = camera_outcome.execution_result.audit["action_reports"]["0"]
        self.assertEqual("unscouted_camera_target", report["detail"])
        self.assertEqual("enemy_main", report["audit"]["target"])
        self.assertIn(
            "Unsupported semantic map target",
            report["audit"]["reason"],
        )
        self.assertEqual(["MOVE_CAMERA"], camera_validator.calls)
        self.assertEqual(["MOVE_CAMERA"], camera_planner.calls)
        self.assertEqual(["MOVE_CAMERA"], camera_executor.calls)
        self.assertEqual([], camera_bot.build_calls)
        self.assertEqual([], camera_bot.camera_moves)
        self.assertEqual([], camera_bot.issued_commands)

    async def test_korean_away_from_main_build_phrases_use_explicit_policy(
        self,
    ) -> None:
        cases = (
            "본진 밖에 보급고 지어",
            "본진 바깥쪽에 보급고 지어",
            "본진 외곽에 보급고 지어",
            "본진에서 앞마당으로 멀게 보급고 지어",
        )

        for command_text in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot(
                    minerals=1000,
                    supply_left=10,
                    supports_build=True,
                )
                session = make_session(bot)

                outcome = (await session.process_text(command_text))[0]

                self.assertEqual("executed", outcome.status)
                self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
                self.assertEqual("Supply Depot", outcome.intent_dsl["structure"])
                self.assertEqual("natural expansion", outcome.intent_dsl["location"])
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                execution_policy = (
                    outcome.plan.actions[0].metadata["placement_policy"]
                )
                self.assertEqual("main base", execution_policy["anchor"])
                self.assertEqual("self_main", execution_policy["anchor_target"])
                self.assertEqual("far_from", execution_policy["spatial_relation"])
                self.assertTrue(str(execution_policy["source_text"]).strip())
                if "앞마당으로" in command_text:
                    self.assertEqual(
                        "natural expansion",
                        execution_policy["direction"],
                    )
                    self.assertEqual(
                        "self_natural",
                        execution_policy["direction_target"],
                    )
                self.assertEqual(1, len(bot.build_calls))
                type_id, near = bot.build_calls[0]
                self.assertEqual("SUPPLYDEPOT", type_id)
                self.assertAlmostEqual(31.69001047392453, float(near.x))
                self.assertAlmostEqual(32.47868202842264, float(near.y))
                self.assertEqual([], bot.issued_commands)

    async def test_main_geyser_build_phrase_uses_explicit_geyser_policy(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(
            minerals=1000,
            supply_left=10,
            supports_build=True,
        )
        session = make_session(bot)

        outcome = (await session.process_text("본진 가스에 정제소 지어"))[0]

        self.assertEqual("executed", outcome.status)
        self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
        self.assertEqual("Refinery", outcome.intent_dsl["structure"])
        self.assertEqual("main geyser", outcome.intent_dsl["location"])
        self.assertTrue(outcome.feasibility.executable)
        self.assertTrue(outcome.execution_result.success)
        placement_policy = outcome.plan.actions[0].metadata["placement_policy"]
        self.assertEqual("main geyser", placement_policy["anchor"])
        self.assertEqual("self_geyser", placement_policy["anchor_target"])
        self.assertEqual("on", placement_policy["spatial_relation"])
        self.assertEqual(1, len(bot.build_calls))
        type_id, near = bot.build_calls[0]
        self.assertEqual("REFINERY", type_id)
        self.assertEqual("VespeneGeyser", near.name)
        self.assertAlmostEqual(36.0, float(near.position.x))
        self.assertAlmostEqual(24.0, float(near.position.y))
        self.assertEqual([], bot.camera_moves)

    async def test_explicit_base_build_location_uses_requested_base_without_clarification(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(
            minerals=1000,
            supply_left=10,
            supports_build=True,
        )
        bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
        session = make_session(bot)

        outcome = (await session.process_text("앞마당 커맨드 주변에 보급고 지어"))[0]

        self.assertEqual("executed", outcome.status)
        self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
        self.assertEqual("Supply Depot", outcome.intent_dsl["structure"])
        self.assertEqual("natural expansion", outcome.intent_dsl["location"])
        self.assertTrue(outcome.feasibility.executable)
        self.assertTrue(outcome.execution_result.success)
        placement_policy = outcome.plan.actions[0].metadata["placement_policy"]
        self.assertEqual("self_natural", placement_policy["anchor_target"])
        self.assertEqual("natural", placement_policy["base_selection"]["selector"])
        self.assertEqual("near", placement_policy["spatial_relation"])
        self.assertEqual(1, len(bot.build_calls))
        type_id, near = bot.build_calls[0]
        self.assertEqual("SUPPLYDEPOT", type_id)
        self.assertAlmostEqual(45.0, float(near.x))
        self.assertAlmostEqual(49.0, float(near.y))
        self.assertEqual([], bot.issued_commands)

    async def test_explicit_korean_and_english_base_build_modifiers_skip_clarification(
        self,
    ) -> None:
        cases = (
            ("본진 사령부 주변에 보급고 지어", "main", "main base", "self_main"),
            (
                "main base command center near build supply depot",
                "main",
                "main base",
                "self_main",
            ),
            (
                "앞마당 커맨드 주변에 보급고 지어",
                "natural",
                "natural expansion",
                "self_natural",
            ),
            (
                "natural expansion command center near build supply depot",
                "natural",
                "natural expansion",
                "self_natural",
            ),
        )

        for command_text, expected_selector, expected_location, expected_target in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot(
                    minerals=1000,
                    supply_left=10,
                    supports_build=True,
                )
                bot.structures.append(FakeUnit("CommandCenter", 45.0, 52.0))
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("executed", outcome.status)
                self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
                self.assertEqual("Supply Depot", outcome.intent_dsl["structure"])
                self.assertEqual(expected_location, outcome.intent_dsl["location"])
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertNotIn("어느 사령부", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                placement_policy = outcome.plan.actions[0].metadata["placement_policy"]
                self.assertEqual(expected_target, placement_policy["anchor_target"])
                self.assertEqual(
                    expected_selector,
                    placement_policy["base_selection"]["selector"],
                )
                self.assertEqual("near", placement_policy["spatial_relation"])
                self.assertEqual(1, len(bot.build_calls))
                self.assertEqual("SUPPLYDEPOT", bot.build_calls[0][0])
                self.assertEqual([], bot.camera_moves)
                self.assertEqual([], bot.issued_commands)

    async def test_unresolved_korean_anchor_blocks_without_location_fallback(
        self,
    ) -> None:
        payload = {
            "intent": "BUILD_STRUCTURE",
            "priority": "normal",
            "structure": "Supply Depot",
            "location": "main ramp",
            "placement_policy": {
                "anchor": "섬 멀티",
                "spatial_relation": "near",
            },
        }
        bot = LivePipelineFakeBot(
            minerals=1000,
            supply_left=10,
            supports_build=True,
        )
        session = make_session(bot, interpreter=StaticInterpreter(payload))

        outcome = (await session.process_text("보급고 지어"))[0]

        self.assertEqual("blocked", outcome.status)
        self.assertEqual("BUILD_STRUCTURE", outcome.intent_dsl["intent"])
        self.assertTrue(outcome.feasibility.executable)
        self.assertFalse(outcome.execution_result.success)
        self.assertIn("unresolved_anchor", outcome.narration)
        self.assertIn("Unsupported map anchor", outcome.narration)
        self.assertIn("섬 멀티", outcome.narration)
        self.assertEqual([], bot.build_calls)
        self.assertEqual([], bot.issued_commands)

    async def test_state_and_advice_macro_splits_into_status_and_advice(self) -> None:
        cases = (
            "상태 보고하고 지금 할거 알려줘",
            "상태 확인하고 다음 할 일 알려줘",
            "상황 보고하고 다음 행동 알려줘",
            "전황 보고하고 지금 할거 알려줘",
            "status then next action",
        )
        for command_text in cases:
            with self.subTest(command_text=command_text):
                bot = LivePipelineFakeBot()
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(2, len(outcomes))
                self.assertEqual("read_only", outcomes[0].status)
                self.assertEqual("SUMMARIZE_STATE", outcomes[0].intent_dsl["intent"])
                self.assertEqual("read_only", outcomes[1].status)
                self.assertEqual("ANSWER_QUESTION", outcomes[1].intent_dsl["intent"])
                self.assertEqual("next_action_help", outcomes[1].intent_dsl["topic"])
                self.assertEqual([], bot.issued_commands)

    async def test_state_and_advice_macro_uses_static_template_when_planner_fails(
        self,
    ) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(
            bot,
            interpreter=FailingComboPlanningInterpreter(),
        )

        outcomes = await session.process_text("상태 보고하고 다음 할 일 알려줘")

        self.assertEqual(2, len(outcomes))
        self.assertEqual("SUMMARIZE_STATE", outcomes[0].intent_dsl["intent"])
        self.assertEqual("next_action_help", outcomes[1].intent_dsl["topic"])
        self.assertTrue(all(outcome.status == "read_only" for outcome in outcomes))
        self.assertEqual([], bot.issued_commands)

    async def test_state_then_command_text_is_not_rewritten_to_next_action_advice(
        self,
    ) -> None:
        bot = LivePipelineFakeBot(minerals=900, supply_left=12)
        session = make_session(bot)

        outcomes = await session.process_text("상태 보고하고 정찰 보내")

        self.assertEqual(2, len(outcomes))
        self.assertEqual("SUMMARIZE_STATE", outcomes[0].intent_dsl["intent"])
        self.assertEqual("SCOUT", outcomes[1].intent_dsl["intent"])
        self.assertNotEqual("ANSWER_QUESTION", outcomes[1].intent_dsl["intent"])

    async def test_current_state_question_maps_to_status_report(self) -> None:
        session = make_session(LivePipelineFakeBot())

        outcomes = await session.process_text("지금 어떻게 되어있지?")

        self.assertEqual(1, len(outcomes))
        self.assertEqual("read_only", outcomes[0].status)
        self.assertEqual("SUMMARIZE_STATE", outcomes[0].intent_dsl["intent"])
        self.assertNotIn("추천 흐름", outcomes[0].narration)
        self.assertNotIn("추천 행동", outcomes[0].narration)
        self.assertNotIn("조언", outcomes[0].narration)

    async def test_infeasible_command_is_blocked_with_reason_and_alternative(self) -> None:
        bot = LivePipelineFakeBot(minerals=0)
        session = make_session(bot)

        outcomes = await session.process_text("배럭 지어")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("blocked", outcome.status)
        self.assertIn("실행하지 않았습니다", outcome.narration)
        self.assertIn("이유:", outcome.narration)
        self.assertIn("대안:", outcome.narration)
        self.assertIn("미네랄", outcome.narration)
        self.assertFalse(outcome.feasibility.executable)
        self.assertIn("insufficient_minerals", outcome.feasibility.reason_codes)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertEqual([], bot.issued_commands)

    async def test_supported_direct_aliases_execute_or_block_with_concrete_reasons(
        self,
    ) -> None:
        executable_cases = (
            ("일꾼생산", "TRAIN_WORKER"),
            ("정찰보내", "SCOUT"),
            ("자원채취", "GATHER_RESOURCE"),
            ("놀고 있는 일꾼들 일시켜", "GATHER_RESOURCE"),
        )
        for command_text, expected_intent in executable_cases:
            with self.subTest(command_text=command_text, mode="execute"):
                bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("executed", outcome.status)
                self.assertEqual(expected_intent, outcome.intent_dsl["intent"])
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertNotEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
                    outcome.narration,
                )
                self.assertGreater(len(bot.issued_commands), 0)

        blocked_cases = (
            ("가스생산 시설 지어", "BUILD_STRUCTURE", "insufficient_minerals"),
            ("배프빈가스 지어", "BUILD_STRUCTURE", "insufficient_minerals"),
            ("배럴 지어", "BUILD_STRUCTURE", "missing_tech_requirement"),
            ("뵤ㅗ급로 지어", "BUILD_STRUCTURE", "insufficient_minerals"),
        )
        for command_text, expected_intent, expected_reason in blocked_cases:
            with self.subTest(command_text=command_text, mode="block"):
                bot = LivePipelineFakeBot(minerals=0, supply_left=10)
                session = make_session(bot)

                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("blocked", outcome.status)
                self.assertEqual(expected_intent, outcome.intent_dsl["intent"])
                self.assertIsNotNone(outcome.feasibility)
                self.assertFalse(outcome.feasibility.executable)
                self.assertIn(expected_reason, outcome.feasibility.reason_codes)
                self.assertIn("이유:", outcome.narration)
                self.assertIn("대안:", outcome.narration)
                self.assertNotEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
                    outcome.narration,
                )
                self.assertEqual([], bot.issued_commands)

    async def test_basic_command_examples_parse_and_execute_expected_outputs(
        self,
    ) -> None:
        cases = (
            {
                "command_text": "미네랄에 일꾼 두 기 붙여",
                "bot_kwargs": {"minerals": 1000, "supply_left": 10, "workers": 4},
                "expected_status": "executed",
                "expected_intent": {
                    "intent": "GATHER_RESOURCE",
                    "resource": "minerals",
                    "worker_count": 2,
                    "base": "main",
                },
                "expected_action": {
                    "action_type": "assign_workers",
                    "subject": "SCV",
                    "target": "minerals",
                    "count": 2,
                    "metadata": {"base": "main"},
                },
                "expected_action_report": {"requested_count": 2, "issued_count": 2},
                "expected_orders": (
                    ("gather", "SCV", "MineralField"),
                    ("gather", "SCV", "MineralField"),
                ),
            },
            {
                "command_text": "SCV 계속 찍어",
                "bot_kwargs": {"minerals": 1000, "supply_left": 10, "workers": 4},
                "expected_status": "partially_executed",
                "expected_intent": {
                    "intent": "TRAIN_WORKER",
                    "count": 1,
                },
                "expected_action": {
                    "action_type": "train_unit",
                    "subject": "SCV",
                    "target": "",
                    "count": 1,
                    "metadata": {"producer": "COMMANDCENTER"},
                },
                "expected_action_report": {"requested_count": 1, "issued_count": 1},
                "expected_orders": (("train", "CommandCenter", "SCV"),),
            },
            {
                "command_text": "정찰보내",
                "bot_kwargs": {"minerals": 1000, "supply_left": 10, "workers": 4},
                "expected_status": "executed",
                "expected_intent": {
                    "intent": "SCOUT",
                    "target": "enemy front",
                    "unit_group": "1 SCV",
                },
                "expected_action": {
                    "action_type": "move_group",
                    "subject": "1 SCV",
                    "target": "enemy_front",
                    "count": 1,
                    "metadata": {"role": "scout"},
                },
                "expected_action_report": {"requested_count": 1, "issued_count": 1},
                "expected_orders": (("move", "SCV", (122.0, 124.0)),),
            },
            {
                "command_text": "마린 2기 입구 지켜",
                "bot_kwargs": {
                    "minerals": 1000,
                    "supply_left": 10,
                    "workers": 4,
                    "marines": 2,
                },
                "expected_status": "executed",
                "expected_intent": {
                    "intent": "DEFEND",
                    "location": "main ramp",
                    "unit_group": "2 Marines",
                },
                "expected_action": {
                    "action_type": "attack_move",
                    "subject": "2 Marines",
                    "target": "self_ramp",
                    "count": 1,
                    "metadata": {"role": "defend"},
                },
                "expected_action_report": {"requested_count": 2, "issued_count": 2},
                "expected_orders": (
                    ("attack", "Marine", (38.0, 36.0)),
                    ("attack", "Marine", (38.0, 36.0)),
                ),
            },
        )
        generic_failure_fragments = ("10개 MVP", "LLM 해석에 실패", "지원하지 않는 명령")

        for case in cases:
            with self.subTest(command_text=case["command_text"]):
                bot = LivePipelineFakeBot(**case["bot_kwargs"])
                session = make_session(bot)

                outcomes = await session.process_text(case["command_text"])

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual(case["expected_status"], outcome.status)
                self.assertIsNotNone(outcome.intent_dsl)
                self.assertIsNotNone(outcome.plan)
                self.assertIsNotNone(outcome.execution_result)
                self.assertIsNotNone(outcome.feasibility)
                self.assertTrue(outcome.feasibility.executable)
                self.assertTrue(outcome.execution_result.success)
                self.assertEqual((), outcome.execution_result.errors)
                self.assertEqual((), outcome.execution_result.skipped_actions)

                for key, expected_value in case["expected_intent"].items():
                    self.assertEqual(expected_value, outcome.intent_dsl[key])

                plan = outcome.plan.to_dict()
                self.assertEqual(case["expected_intent"]["intent"], plan["intent"])
                self.assertEqual(1, len(plan["ordered_actions"]))
                planned_action = plan["ordered_actions"][0]
                for key, expected_value in case["expected_action"].items():
                    self.assertEqual(expected_value, planned_action[key])
                self.assertEqual(plan["priority"], planned_action["priority"])
                self.assertEqual(
                    outcome.intent_dsl["constraints"],
                    planned_action["constraints"],
                )

                execution = outcome.execution_result.to_dict()
                self.assertTrue(execution["success"])
                self.assertEqual(1, len(execution["applied_actions"]))
                applied_action = execution["applied_actions"][0]
                for key, expected_value in case["expected_action"].items():
                    self.assertEqual(expected_value, applied_action[key])
                self.assertEqual(planned_action, applied_action)
                self.assertEqual([], execution["skipped_actions"])
                self.assertEqual([], execution["errors"])
                self.assertEqual(
                    case["expected_action_report"],
                    {
                        key: execution["audit"]["action_reports"]["0"][key]
                        for key in ("requested_count", "issued_count")
                    },
                )
                self.assertEqual(
                    case["expected_orders"],
                    _normalize_issued_commands(bot.issued_commands),
                )
                self.assertEqual([], bot.camera_moves)
                for fragment in generic_failure_fragments:
                    self.assertNotIn(fragment, outcome.narration)

    async def test_unparseable_text_reuses_interpreter_clarification_wording(self) -> None:
        session = make_session(LivePipelineFakeBot())

        outcomes = await session.process_text("피아노 쳐줘")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("clarification", outcome.status)
        self.assertEqual(UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT, outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)

    async def test_deictic_build_text_asks_for_supported_semantic_target(self) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        for command_text in ("저기 보급고 지어", "저기에 지어", "거기 지어"):
            with self.subTest(command_text=command_text):
                outcomes = await session.process_text(command_text)

                self.assertEqual(1, len(outcomes))
                outcome = outcomes[0]
                self.assertEqual("clarification", outcome.status)
                self.assertIn("semantic target", outcome.narration)
                self.assertIn("지원되는", outcome.narration)
                self.assertIn("가능한 위치", outcome.narration)
                self.assertIn("어디에 지을까요", outcome.narration)
                self.assertIn("본진 입구", outcome.narration)
                self.assertIn("앞마당", outcome.narration)
                self.assertIn("실행하지 않았습니다", outcome.narration)
                self.assertNotIn("10개 MVP", outcome.narration)
                self.assertIsNone(outcome.intent_dsl)
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.execution_result)
                self.assertIsNone(outcome.feasibility)
                self.assertEqual([], bot.issued_commands)
                self.assertEqual([], bot.camera_moves)

    async def test_deictic_build_text_ignores_llm_combo_anchor_guess(self) -> None:
        bot = LivePipelineFakeBot(minerals=1000, supply_left=10)
        session = make_session(
            bot,
            interpreter=ComboPlanningInterpreter(("본진에 보급고 지어", "정찰보내")),
        )

        outcomes = await session.process_text("저기에 보급고 지어")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("clarification", outcome.status)
        self.assertIn("semantic target", outcome.narration)
        self.assertIn("어디에 지을까요", outcome.narration)
        self.assertIsNone(outcome.intent_dsl)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertIsNone(outcome.feasibility)
        self.assertEqual([], bot.issued_commands)

    async def test_mvp_compound_command_returns_one_outcome_per_part(self) -> None:
        bot = LivePipelineFakeBot(marines=6)
        session = make_session(bot)

        outcomes = await session.process_text(MVP_COMPOUND_COMMAND)

        self.assertEqual(2, len(outcomes))
        move_part, train_part = outcomes
        with self.subTest(part="marine move"):
            self.assertEqual("마린 6기 입구로 보내", move_part.command_text)
            self.assertEqual("executed", move_part.status)
            self.assertEqual("DEFEND", move_part.intent_dsl["intent"])
            self.assertEqual("6 Marines", move_part.intent_dsl["unit_group"])
            self.assertEqual("main ramp", move_part.intent_dsl["location"])
            # The narration is fully Korean: the unit group is translated.
            self.assertIn("마린 6기", move_part.narration)
            self.assertIn("공격 이동", move_part.narration)
            self.assertNotIn("Marines", move_part.narration)
            self.assertTrue(move_part.execution_result.success)
        with self.subTest(part="keep SCV production"):
            self.assertEqual("SCV 계속 찍어", train_part.command_text)
            self.assertEqual("partially_executed", train_part.status)
            self.assertIn("SCV 1기 생산 명령", train_part.narration)
            self.assertIn("지속 생산은 아직 지원되지 않아", train_part.narration)
        attack_commands = bot.issued_commands[:-1]
        self.assertEqual(6, len(attack_commands))
        for command in attack_commands:
            kind, unit_name, _point = command
            self.assertEqual("attack", kind)
            self.assertEqual("Marine", unit_name)
        self.assertEqual(("train", "CommandCenter", "SCV"), bot.issued_commands[-1])

    async def test_partial_marine_move_is_narrated_with_issued_count(self) -> None:
        # 6 Marines requested but only 2 exist: the outcome must be partial
        # and the narration must state the honest issued count.
        bot = LivePipelineFakeBot(marines=2)
        session = make_session(bot)

        outcomes = await session.process_text("마린 6기 입구로 보내")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("partially_executed", outcome.status)
        self.assertIn("마린 6기 중 2기만", outcome.narration)
        self.assertFalse(outcome.execution_result.success)
        attack_commands = [
            command for command in bot.issued_commands if command[0] == "attack"
        ]
        self.assertEqual(2, len(attack_commands))

    async def test_mixed_compound_command_never_drops_unsupported_part(self) -> None:
        # The supported part executes (with its constraint disclosure) and
        # the unsupported part comes back as an honest clarification instead
        # of vanishing inside one "executed" outcome.
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("SCV 계속 찍어 그리고 피아노 쳐줘")

        self.assertEqual(2, len(outcomes))
        train_part, piano_part = outcomes
        self.assertEqual("SCV 계속 찍어", train_part.command_text)
        self.assertEqual("partially_executed", train_part.status)
        self.assertIn("SCV 1기 생산 명령", train_part.narration)
        self.assertEqual("피아노 쳐줘", piano_part.command_text)
        self.assertEqual("clarification", piano_part.status)
        self.assertEqual([("train", "CommandCenter", "SCV")], bot.issued_commands)

    async def test_fully_unsupported_compound_returns_full_text_clarification(self) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("피아노 쳐줘 그리고 노래 불러줘")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("clarification", outcome.status)
        self.assertEqual("피아노 쳐줘 그리고 노래 불러줘", outcome.command_text)
        self.assertEqual(UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT, outcome.narration)
        self.assertEqual([], bot.issued_commands)

    async def test_same_family_compound_command_never_drops_second_part(self) -> None:
        # "마린 두 기 뽑고 정찰 보내" used to resolve the WHOLE text to one
        # TRAIN_ARMY payload, silently dropping the scout order. The scout
        # half must surface and now resolves to the default enemy-front scout.
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("마린 두 기 뽑고 정찰 보내")

        self.assertEqual(2, len(outcomes))
        train_part, scout_part = outcomes
        self.assertEqual("마린 두 기 뽑", train_part.command_text)
        self.assertEqual("TRAIN_ARMY", train_part.intent_dsl["intent"])
        # No Barracks on the fake bot: the train part blocks honestly.
        self.assertEqual("blocked", train_part.status)
        self.assertEqual("정찰 보내", scout_part.command_text)
        self.assertEqual("SCOUT", scout_part.intent_dsl["intent"])
        self.assertEqual("executed", scout_part.status)

    async def test_same_family_compound_with_resolvable_parts_executes_both(self) -> None:
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("마린 두 기 뽑고 적 본진 정찰 보내")

        self.assertEqual(2, len(outcomes))
        train_part, scout_part = outcomes
        self.assertEqual("TRAIN_ARMY", train_part.intent_dsl["intent"])
        self.assertEqual("blocked", train_part.status)
        self.assertEqual("SCOUT", scout_part.intent_dsl["intent"])
        self.assertEqual("executed", scout_part.status)

    async def test_noun_ending_in_go_keeps_build_part_intact(self) -> None:
        # "보급고" must never be shredded into "보급" + "지어" fragments.
        bot = LivePipelineFakeBot()
        session = make_session(bot)

        outcomes = await session.process_text("마린 뽑고 보급고 지어")

        self.assertEqual(2, len(outcomes))
        train_part, build_part = outcomes
        self.assertEqual("마린 뽑", train_part.command_text)
        self.assertEqual("TRAIN_ARMY", train_part.intent_dsl["intent"])
        self.assertEqual("보급고 지어", build_part.command_text)

    async def test_no_bot_session_blocks_conservatively(self) -> None:
        session = SC2CommandSession()

        outcomes = await session.process_text("SCV 계속 찍어")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("blocked", outcome.status)
        self.assertEqual(("unknown_state",), outcome.feasibility.reason_codes)
        self.assertIn("상태를 확인할 수 없어", outcome.narration)
        self.assertIn("대안:", outcome.narration)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)

    async def test_planner_value_error_becomes_blocked_outcome(self) -> None:
        bot = LivePipelineFakeBot(marines=2)
        payload = {
            "intent": "DEFEND",
            "unit_group": "available combat units",
            "location": "우주 어딘가",
        }
        session = make_session(bot, interpreter=StaticInterpreter(payload))

        outcomes = await session.process_text("이상한 곳 막아")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("blocked", outcome.status)
        self.assertIn("unsupported SC2 target location", outcome.narration)
        self.assertIn("Supported targets:", outcome.narration)
        self.assertIn("대안:", outcome.narration)
        self.assertTrue(outcome.feasibility.executable)
        self.assertIsNone(outcome.plan)
        self.assertIsNone(outcome.execution_result)
        self.assertEqual([], bot.issued_commands)

    async def test_korean_friendly_main_alias_from_llm_payload_executes(self) -> None:
        bot = LivePipelineFakeBot(marines=2)
        payload = {
            "intent": "DEFEND",
            "unit_group": "available combat units",
            "location": "우리 본 진",
        }
        session = make_session(bot, interpreter=StaticInterpreter(payload))

        outcomes = await session.process_text("지금 공격받고있으니깐 대응해 저그")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("executed", outcome.status)
        self.assertEqual("self_main", outcome.plan.actions[0].target)
        self.assertEqual(
            [
                ("attack", "Marine", (30.0, 30.0)),
                ("attack", "Marine", (30.0, 30.0)),
            ],
            bot.issued_commands,
        )

    async def test_executed_outcome_to_dict_json_round_trip(self) -> None:
        session = make_session(LivePipelineFakeBot())

        outcomes = await process_commander_text(session, "상태 알려줘")

        payload = json.loads(json.dumps(outcomes[0].to_dict(), ensure_ascii=False))
        self.assertEqual("read_only", payload["status"])
        self.assertEqual("상태 알려줘", payload["command_text"])
        self.assertEqual("SUMMARIZE_STATE", payload["intent_dsl"]["intent"])
        self.assertEqual("SUMMARIZE_STATE", payload["plan"]["intent_name"])
        self.assertTrue(payload["execution_result"]["success"])
        self.assertTrue(payload["feasibility"]["executable"])
        for key in ("command_text", "status", "narration", "intent_dsl", "plan"):
            with self.subTest(key=key):
                self.assertIn(key, payload)

    async def test_session_rejects_components_missing_required_seams(self) -> None:
        with self.assertRaises(TypeError):
            SC2CommandSession(interpreter=object())
        with self.assertRaises(TypeError):
            SC2CommandSession(narrator=object())

    async def test_session_rejects_invalid_optional_integrations(self) -> None:
        with self.subTest(seam="event_memory without record()"):
            with self.assertRaises(TypeError):
                SC2CommandSession(event_memory=object())
        with self.subTest(seam="standing_orders without controller surface"):
            with self.assertRaises(TypeError):
                SC2CommandSession(standing_orders=object())


class LivePipelineIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """W4 integration: standing orders + event memory inside the session."""

    def build_integrated_session(self, bot):
        memory = CommanderEventMemory()
        orders = StandingOrderController()
        session = make_session(bot, event_memory=memory, standing_orders=orders)
        return session, memory, orders

    async def test_continuous_train_with_controller_is_executed_with_suffix(self) -> None:
        # With a standing-order controller the continuous-production
        # constraint is genuinely enforced: the outcome is full execution
        # plus the honest Korean registration suffix, never the old
        # "지속 생산 미지원" disclosure.
        bot = LivePipelineFakeBot()
        session, _memory, orders = self.build_integrated_session(bot)

        outcomes = await session.process_text("SCV 계속 찍어")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("executed", outcome.status)
        self.assertIn("SCV 1기 생산 명령", outcome.narration)
        self.assertTrue(outcome.narration.endswith("상비 명령 등록: 지속 SCV 생산."))
        self.assertNotIn("지속 생산은 아직 지원되지 않아", outcome.narration)
        self.assertEqual(("keep_worker_production",), orders.active_kinds())
        self.assertEqual([("train", "CommandCenter", "SCV")], bot.issued_commands)

    async def test_registered_order_keeps_training_across_manual_ticks(self) -> None:
        # After "SCV 계속 찍어" registers the standing order, every manual
        # tick (the live bot calls this from on_step) keeps issuing train
        # orders to the fake Command Center — production really continues.
        bot = LivePipelineFakeBot(supply_left=5)
        session, _memory, orders = self.build_integrated_session(bot)
        await session.process_text("SCV 계속 찍어")
        baseline = len(bot.issued_commands)

        for tick_round in range(3):
            with self.subTest(tick_round=tick_round):
                ticks = await orders.tick(bot)
                self.assertEqual(1, len(ticks))
                self.assertTrue(ticks[0].issued)
                self.assertEqual(("train_scv",), ticks[0].actions_issued)

        train_commands = bot.issued_commands[baseline:]
        self.assertEqual([("train", "CommandCenter", "SCV")] * 3, train_commands)

    async def test_second_continuous_command_does_not_reannounce_registration(self) -> None:
        bot = LivePipelineFakeBot()
        session, _memory, orders = self.build_integrated_session(bot)

        first = (await session.process_text("SCV 계속 찍어"))[0]
        second = (await session.process_text("SCV 계속 찍어"))[0]

        self.assertIn("상비 명령 등록", first.narration)
        self.assertEqual("executed", second.status)
        self.assertNotIn("상비 명령 등록", second.narration)
        self.assertEqual(("keep_worker_production",), orders.active_kinds())

    async def test_blocked_command_never_registers_standing_orders(self) -> None:
        bot = LivePipelineFakeBot(minerals=0)
        session, memory, orders = self.build_integrated_session(bot)

        outcomes = await session.process_text("서플 막히지 않게 해줘")

        self.assertEqual("blocked", outcomes[0].status)
        self.assertEqual((), orders.active_kinds())
        self.assertNotIn("상비 명령 등록", outcomes[0].narration)
        # The blocked outcome is still honestly recorded into memory.
        events = memory.recent(1)
        self.assertEqual("blocked", events[0].status)

    async def test_failure_reason_question_uses_recent_blocked_memory(self) -> None:
        bot = LivePipelineFakeBot(minerals=0)
        session, memory, _orders = self.build_integrated_session(bot)
        await session.process_text("배럭 지어")

        outcome = (await session.process_text("왜 안돼?"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("failure_reason_help", outcome.intent_dsl["topic"])
        self.assertIn("`배럭 지어` 명령", outcome.narration)
        self.assertIn("이유:", outcome.narration)
        self.assertIn("읽기 전용", outcome.narration)
        self.assertEqual("read_only", memory.recent(1)[0].status)

    async def test_next_action_question_uses_controller_context(self) -> None:
        bot = LivePipelineFakeBot(supply_left=5)
        session, memory, orders = self.build_integrated_session(bot)
        await session.process_text("SCV 계속 찍어")
        baseline_commands = list(bot.issued_commands)

        outcome = (await session.process_text("다음 할 일 알려줘"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("next_action_help", outcome.intent_dsl["topic"])
        self.assertIn("현재 관측", outcome.narration)
        self.assertIn("현재 상비 명령: 지속 SCV 생산 활성.", outcome.narration)
        self.assertIn("최근 기록 1건 중 성공/정보 1건", outcome.narration)
        self.assertIn("읽기 전용", outcome.narration)
        self.assertEqual(("keep_worker_production",), orders.active_kinds())
        self.assertEqual(baseline_commands, bot.issued_commands)
        self.assertEqual("read_only", memory.recent(1)[0].status)

    async def test_failure_reason_question_uses_live_state_without_prior_failure(self) -> None:
        bot = LivePipelineFakeBot(minerals=25, supply_left=0, workers=0, marines=0)
        session, memory, _orders = self.build_integrated_session(bot)

        outcome = (await session.process_text("왜 안돼?"))[0]

        self.assertEqual("read_only", outcome.status)
        self.assertEqual("failure_reason_help", outcome.intent_dsl["topic"])
        self.assertIn("현재 관측", outcome.narration)
        self.assertIn("현재 막힐 가능성이 큰 이유", outcome.narration)
        self.assertIn("보급이 막힘", outcome.narration)
        self.assertIn("미네랄이 낮음(25)", outcome.narration)
        self.assertIn("최근 실패 기록은 없어서", outcome.narration)
        self.assertIn("읽기 전용", outcome.narration)
        self.assertEqual([], bot.issued_commands)
        self.assertEqual("read_only", memory.recent(1)[0].status)

    async def test_summarize_state_is_enriched_with_orders_and_recent_events(self) -> None:
        bot = LivePipelineFakeBot()
        session, _memory, _orders = self.build_integrated_session(bot)
        await session.process_text("SCV 계속 찍어")

        outcomes = await session.process_text("상태 알려줘")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("read_only", outcome.status)
        for fragment in (
            "전장 상태를 확인했습니다",
            "상비 명령: 지속 SCV 생산 활성",
            "최근 명령 1건:",
            "- #1 [executed]",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, outcome.narration)

    async def test_summarize_state_without_prior_commands_reports_empty_memory(self) -> None:
        bot = LivePipelineFakeBot()
        session, _memory, _orders = self.build_integrated_session(bot)

        outcome = (await session.process_text("상태 알려줘"))[0]

        self.assertIn("상비 명령: 없음", outcome.narration)
        self.assertIn("최근 명령 0건", outcome.narration)

    async def test_event_memory_records_every_outcome_with_game_time(self) -> None:
        bot = LivePipelineFakeBot(minerals=0)
        session, memory, _orders = self.build_integrated_session(bot)

        await session.process_text("배럭 지어")  # blocked (no minerals)
        await session.process_text("피아노 쳐줘")  # clarification

        events = memory.recent(10)
        self.assertEqual(2, len(events))
        blocked_event, clarification_event = events
        with self.subTest(event="blocked"):
            self.assertEqual(1, blocked_event.seq)
            self.assertEqual("배럭 지어", blocked_event.command_text)
            self.assertEqual("blocked", blocked_event.status)
            # Game time comes from the resolved state (fake bot.time = 20.0).
            self.assertEqual(20.0, blocked_event.game_time_seconds)
            self.assertEqual("BUILD_STRUCTURE", blocked_event.intent_name)
        with self.subTest(event="clarification"):
            self.assertEqual(2, clarification_event.seq)
            self.assertEqual("피아노 쳐줘", clarification_event.command_text)
            self.assertEqual("clarification", clarification_event.status)
            # No state is ever resolved for clarifications: no game time.
            self.assertIsNone(clarification_event.game_time_seconds)

    async def test_compound_command_records_one_event_per_part(self) -> None:
        bot = LivePipelineFakeBot()
        session, memory, _orders = self.build_integrated_session(bot)

        await session.process_text("SCV 계속 찍어 그리고 피아노 쳐줘")

        events = memory.recent(10)
        self.assertEqual(2, len(events))
        self.assertEqual("SCV 계속 찍어", events[0].command_text)
        self.assertEqual("executed", events[0].status)
        self.assertEqual("피아노 쳐줘", events[1].command_text)
        self.assertEqual("clarification", events[1].status)

    async def test_controller_session_upgrades_default_korean_narrator(self) -> None:
        session, _memory, _orders = self.build_integrated_session(
            LivePipelineFakeBot()
        )
        self.assertIsInstance(session.narrator, SC2KoreanNarrator)
        for constraint in CONSTRAINT_TO_STANDING_ORDER:
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, session.narrator.enforced_constraints)

    async def test_custom_narrator_is_never_replaced(self) -> None:
        class CustomNarrator:
            def narrate_plan_result(self, result):
                raise AssertionError("not exercised here")

            def narrate_state(self, state):
                raise AssertionError("not exercised here")

            def narrate_rejection(self, feasibility):
                raise AssertionError("not exercised here")

        custom = CustomNarrator()
        session = make_session(
            LivePipelineFakeBot(),
            narrator=custom,
            standing_orders=StandingOrderController(),
        )
        self.assertIs(custom, session.narrator)

    async def test_session_without_controller_keeps_honest_disclosure(self) -> None:
        # Memory alone must not change narration: without a controller the
        # continuous-production disclosure (and partial status) survives.
        bot = LivePipelineFakeBot()
        memory = CommanderEventMemory()
        session = make_session(bot, event_memory=memory)

        outcome = (await session.process_text("SCV 계속 찍어"))[0]

        self.assertEqual("partially_executed", outcome.status)
        self.assertIn("지속 생산은 아직 지원되지 않아", outcome.narration)
        self.assertNotIn("상비 명령 등록", outcome.narration)
        self.assertEqual("partially_executed", memory.recent(1)[0].status)


class PackageExportTest(unittest.TestCase):
    def test_package_lazily_exports_live_pipeline_symbols(self) -> None:
        import starcraft_commander

        for name in (
            "SC2CommandOutcome",
            "SC2CommandSession",
            "process_commander_text",
            "split_compound_command",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(starcraft_commander, name))
                self.assertIn(name, starcraft_commander.__all__)
        self.assertIs(SC2CommandSession, starcraft_commander.SC2CommandSession)
        self.assertIs(SC2CommandOutcome, starcraft_commander.SC2CommandOutcome)

    def test_unsupported_reason_constant_still_matches_interpreter(self) -> None:
        # The clarification path reuses interpreter wording; pin the reason
        # constant the pipeline depends on indirectly.
        self.assertIn("10 MVP", UNSUPPORTED_COMMAND_CLARIFICATION_REASON)

    def test_package_lazily_exports_phase_integration_symbols(self) -> None:
        import starcraft_commander

        for name in (
            "LLMCommandInterpreter",
            "LLMComboPlan",
            "LLMComboPlanStep",
            "HybridCommandInterpreter",
            "build_hybrid_interpreter",
            "CommanderEvent",
            "CommanderEventMemory",
            "WebGuiServer",
            "SessionLoopBridge",
            "StandingOrderController",
            "MissingLLMDependencyError",
            "is_anthropic_available",
            "require_anthropic",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(starcraft_commander, name))
                self.assertIn(name, starcraft_commander.__all__)
        self.assertIs(
            CommanderEventMemory, starcraft_commander.CommanderEventMemory
        )
        self.assertIs(
            StandingOrderController, starcraft_commander.StandingOrderController
        )

    def test_package_import_stays_dependency_free_with_new_exports(self) -> None:
        # The new lazy exports must not drag optional dependencies (or
        # ToyCraft) into a bare package import.
        script = (
            "import json, sys; "
            "import starcraft_commander; "
            "print(json.dumps({name: (name in sys.modules) for name in ("
            "'anthropic', 'sc2', 'faster_whisper', 'sounddevice', "
            "'toycraft_commander')}, sort_keys=True))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        for module_name, loaded in payload.items():
            with self.subTest(module=module_name):
                self.assertFalse(loaded)


if __name__ == "__main__":
    unittest.main()
