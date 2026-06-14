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
from starcraft_commander.live_pipeline import (
    SC2_COMMAND_OUTCOME_STATUSES,
    SC2CommandOutcome,
    SC2CommandSession,
    process_commander_text,
    split_compound_command,
)
from starcraft_commander.narrator import SC2KoreanNarrator
from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
from starcraft_commander.sc2_executor import SC2RuntimeExecutor
from starcraft_commander.standing_orders import (
    CONSTRAINT_TO_STANDING_ORDER,
    StandingOrderController,
)
from toycraft_commander.interpreter import (
    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
    UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
)


MVP_COMPOUND_COMMAND = "마린 6기 입구로 보내고 SCV 계속 찍어"


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

    def __init__(self, *, minerals=400, supply_left=1, workers=12, marines=0):
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
        self.enemy_units = FakeUnitGroup()
        self.enemy_structures = FakeUnitGroup()

        self.minerals = minerals
        self.vespene = 0
        self.supply_used = 14
        self.supply_cap = 15
        self.supply_left = supply_left
        self.supply_army = marines
        self.state = SimpleNamespace(game_loop=448)
        self.time = 20.0
        self.issued_commands = []

    def unit_type_id_resolver(self, type_name):
        return type_name

    def can_afford(self, item):
        return True

    def do(self, command):
        self.issued_commands.append(command)
        return None


def make_session(bot, **overrides):
    adapter = PythonSC2BotAdapter(bot=bot)
    options = {"executor": SC2RuntimeExecutor(bot=adapter)}
    options.update(overrides)
    return SC2CommandSession(**options)


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
        session = make_session(LivePipelineFakeBot())

        outcomes = await session.process_text("어떤 명령을 할 수 있어?")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("read_only", outcome.status)
        self.assertEqual("capability_help", outcome.intent_dsl["topic"])
        self.assertIn("상태 확인", outcome.narration)
        self.assertIn("정찰", outcome.narration)

    async def test_llm_status_question_gets_read_only_answer(self) -> None:
        session = make_session(LivePipelineFakeBot())

        outcomes = await session.process_text("지금 llm이랑 대화가능?")

        self.assertEqual(1, len(outcomes))
        outcome = outcomes[0]
        self.assertEqual("read_only", outcome.status)
        self.assertEqual("llm_help", outcome.intent_dsl["topic"])
        self.assertIn("LLM 필수 모드", outcome.narration)
        self.assertIn("Live GUI", outcome.narration)

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
            "location": "우리 본진",
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
