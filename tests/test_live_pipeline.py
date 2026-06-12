"""Handoff Step 5 acceptance tests for the live SC2 command pipeline.

These tests run without StarCraft II, python-sc2, faster-whisper, or
sounddevice installed. The runtime is a pure-Python recording fake BotAI wired
through the real adapter, executor, validator, planner, interpreter, and
narrator components.
"""

import json
import unittest
from types import SimpleNamespace

from starcraft_commander.contracts import SC2ExecutionPlan, SC2PlanExecutionResult
from starcraft_commander.live_pipeline import (
    SC2_COMMAND_OUTCOME_STATUSES,
    SC2CommandOutcome,
    SC2CommandSession,
    process_commander_text,
    split_compound_command,
)
from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
from starcraft_commander.sc2_executor import SC2RuntimeExecutor
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
        # half must surface (here as an honest clarification: bare "정찰
        # 보내" carries no scout target context).
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
        self.assertEqual("clarification", scout_part.status)

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


if __name__ == "__main__":
    unittest.main()
