"""Handoff Step 2 acceptance tests for the python-sc2 BotAI adapter.

These tests run without StarCraft II, python-sc2, faster-whisper, or
sounddevice installed. The BotAI runtime is a pure-Python recording fake and
``UnitTypeId`` resolution is injected through ``unit_type_resolver``.
"""

import asyncio
import importlib.util
import json
import pathlib
import subprocess
import sys
import types
import unittest

from starcraft_commander.contracts import (
    SC2ActionReport,
    SC2ActionType,
    SC2CommandAction,
)
from starcraft_commander.map_resolver import MapPoint, SC2MapResolver
from starcraft_commander.python_sc2_adapter import (
    MissingPythonSC2Error,
    PYTHON_SC2_UNIT_TYPE_HINT,
    PythonSC2BotAdapter,
    SC2_ADAPTER_ACTION_METHOD_NAMES,
    SC2_EXECUTOR_LIFECYCLE_METHOD_NAMES,
    SC2BotAdapterInterface,
)
from starcraft_commander.sc2_executor import SC2ActionPlanner, SC2RuntimeExecutor


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

PYTHON_SC2_INSTALLED = importlib.util.find_spec("sc2") is not None


def run(coro):
    return asyncio.run(coro)


def point_xy(point):
    if hasattr(point, "x") and hasattr(point, "y"):
        return (float(point.x), float(point.y))
    return (float(point[0]), float(point[1]))


def assert_build_calls_equal(testcase, expected, actual):
    testcase.assertEqual(len(expected), len(actual))
    for expected_call, actual_call in zip(expected, actual):
        testcase.assertEqual(expected_call[0], actual_call[0])
        testcase.assertEqual(point_xy(expected_call[1]), point_xy(actual_call[1]))


def assert_order_points_equal(testcase, expected, actual):
    testcase.assertEqual(len(expected), len(actual))
    for expected_order, actual_order in zip(expected, actual):
        testcase.assertEqual(expected_order[0], actual_order[0])
        testcase.assertIs(expected_order[1], actual_order[1])
        testcase.assertEqual(point_xy(expected_order[2]), point_xy(actual_order[2]))


class FakePoint:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)


class FakeUnit:
    def __init__(
        self,
        name,
        x=0.0,
        y=0.0,
        *,
        is_idle=True,
        is_ready=True,
        health=None,
        health_max=None,
    ):
        self.name = name
        self.position = FakePoint(x, y)
        self.is_idle = is_idle
        self.is_ready = is_ready
        if health is not None:
            self.health = health
        if health_max is not None:
            self.health_max = health_max
        self.issued_orders = []

    def __repr__(self):
        return f"FakeUnit({self.name!r})"

    def _record(self, kind, payload):
        order = (kind, self, payload)
        self.issued_orders.append(order)
        return order

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


class FakeBotAI:
    """Recording python-sc2 BotAI stand-in with complete observation surface."""

    def __init__(
        self,
        *,
        workers=None,
        units=None,
        structures=None,
        mineral_fields=None,
        geysers=None,
        affordable=True,
    ):
        self.workers = FakeUnitGroup(workers or [])
        self.units = FakeUnitGroup(
            units if units is not None else list(self.workers)
        )
        self.structures = FakeUnitGroup(structures or [])
        self.mineral_field = FakeUnitGroup(mineral_fields or [])
        self.vespene_geyser = FakeUnitGroup(geysers or [])
        self.start_location = FakePoint(10.0, 10.0)
        self.enemy_start_locations = [FakePoint(90.0, 90.0)]
        self.minerals = 400
        self.vespene = 100
        self.supply_used = 20
        self.supply_cap = 28
        self.supply_left = 8
        self.supply_army = 6
        self.state = types.SimpleNamespace(game_loop=672)
        self.time = 30.0
        self.enemy_units = FakeUnitGroup([])
        self.enemy_structures = FakeUnitGroup([])
        self.affordable = affordable
        self.issued = []
        self.build_calls = []
        self.can_afford_calls = []

    def can_afford(self, item):
        self.can_afford_calls.append(item)
        if isinstance(self.affordable, list):
            return self.affordable.pop(0) if self.affordable else False
        return self.affordable

    def do(self, command):
        self.issued.append(command)
        return None

    async def build(self, type_id, near=None):
        self.build_calls.append((type_id, near))
        return None


FAKE_UNIT_TYPE_IDS = {
    "SCV": "TYPE:SCV",
    "MARINE": "TYPE:MARINE",
    "HELLION": "TYPE:HELLION",
    "SUPPLYDEPOT": "TYPE:SUPPLYDEPOT",
    "BARRACKS": "TYPE:BARRACKS",
    "COMMANDCENTER": "TYPE:COMMANDCENTER",
    "REFINERY": "TYPE:REFINERY",
}


def fake_unit_type_resolver(name):
    return FAKE_UNIT_TYPE_IDS[name]


def make_map_resolver():
    return SC2MapResolver(
        positions={
            "self_main": MapPoint(10.0, 10.0),
            "self_ramp": MapPoint(20.0, 12.0),
            "self_natural": MapPoint(30.0, 30.0),
            "enemy_main": MapPoint(90.0, 90.0),
            "enemy_mineral_line": MapPoint(88.0, 95.0),
        }
    )


def make_adapter(bot, **overrides):
    options = {
        "map_resolver": make_map_resolver(),
        "unit_type_resolver": fake_unit_type_resolver,
    }
    options.update(overrides)
    return PythonSC2BotAdapter(bot=bot, **options)


def action(action_type, subject, *, target="", count=1, metadata=None):
    return SC2CommandAction(
        action_type=action_type,
        subject=subject,
        target=target,
        count=count,
        metadata=metadata or {},
    )


class AdapterContractTest(unittest.TestCase):
    def test_adapter_defines_no_executor_lifecycle_hooks(self) -> None:
        adapter = make_adapter(FakeBotAI())
        self.assertEqual(
            frozenset({"start", "close", "stop", "on_start", "on_end"}),
            SC2_EXECUTOR_LIFECYCLE_METHOD_NAMES,
        )
        for name in sorted(SC2_EXECUTOR_LIFECYCLE_METHOD_NAMES):
            with self.subTest(lifecycle_hook=name):
                self.assertFalse(hasattr(adapter, name))
                self.assertFalse(hasattr(PythonSC2BotAdapter, name))

    def test_adapter_implements_every_semantic_action_method(self) -> None:
        adapter = make_adapter(FakeBotAI())
        self.assertEqual(
            (
                "assign_workers",
                "build_structure",
                "train_unit",
                "move_group",
                "attack_move",
                "repair",
                "observe",
            ),
            SC2_ADAPTER_ACTION_METHOD_NAMES,
        )
        self.assertIsInstance(adapter, SC2BotAdapterInterface)
        for name in SC2_ADAPTER_ACTION_METHOD_NAMES:
            with self.subTest(action_method=name):
                self.assertTrue(callable(getattr(adapter, name)))

    def test_constructor_validates_collaborators(self) -> None:
        cases = (
            ("bot", {"bot": None}, ValueError),
            (
                "map_resolver",
                {"bot": FakeBotAI(), "map_resolver": object()},
                TypeError,
            ),
            (
                "state_resolver",
                {"bot": FakeBotAI(), "state_resolver": object()},
                TypeError,
            ),
            (
                "unit_type_resolver",
                {"bot": FakeBotAI(), "unit_type_resolver": "SCV"},
                TypeError,
            ),
        )
        for label, kwargs, expected in cases:
            with self.subTest(invalid_field=label):
                with self.assertRaises(expected):
                    PythonSC2BotAdapter(**kwargs)

    def test_to_dict_is_json_ready(self) -> None:
        adapter = make_adapter(FakeBotAI())
        payload = adapter.to_dict()
        self.assertEqual(payload, json.loads(json.dumps(payload)))
        self.assertEqual("FakeBotAI", payload["runtime_adapter"])
        self.assertTrue(payload["map_resolver_ready"])
        self.assertTrue(payload["unit_type_resolver_injected"])
        self.assertEqual(
            list(SC2_ADAPTER_ACTION_METHOD_NAMES),
            payload["action_methods"],
        )


class AssignWorkersTest(unittest.TestCase):
    def test_assigns_idle_workers_to_nearest_mineral_field(self) -> None:
        idle_a = FakeUnit("SCV", 9, 9, is_idle=True)
        idle_b = FakeUnit("SCV", 11, 11, is_idle=True)
        busy = FakeUnit("SCV", 10, 10, is_idle=False)
        near_minerals = FakeUnit("MineralField", 12.0, 10.0)
        far_minerals = FakeUnit("MineralField", 40.0, 40.0)
        bot = FakeBotAI(
            workers=[idle_a, idle_b, busy],
            mineral_fields=[far_minerals, near_minerals],
        )
        adapter = make_adapter(bot)
        result = run(
            adapter.assign_workers(
                action(
                    SC2ActionType.ASSIGN_WORKERS,
                    "SCV",
                    target="minerals",
                    count=2,
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual(
            [
                ("gather", idle_a, near_minerals),
                ("gather", idle_b, near_minerals),
            ],
            bot.issued,
        )
        self.assertEqual([], busy.issued_orders)

    def test_assigns_gas_workers_to_completed_refinery_only(self) -> None:
        worker = FakeUnit("SCV", 10, 10)
        refinery = FakeUnit("Refinery", 14.0, 10.0)
        geyser = FakeUnit("VespeneGeyser", 13.0, 10.0)
        bot = FakeBotAI(
            workers=[worker],
            structures=[refinery],
            geysers=[geyser],
        )
        adapter = make_adapter(bot)
        result = run(
            adapter.assign_workers(
                action(SC2ActionType.ASSIGN_WORKERS, "SCV", target="gas", count=1)
            )
        )
        self.assertTrue(result)
        self.assertEqual([("gather", worker, refinery)], bot.issued)

    def test_gas_without_completed_refinery_refuses_honestly(self) -> None:
        # The live game silently rejects gather orders on a bare geyser or an
        # in-construction refinery; the adapter must refuse, never issue.
        geyser = FakeUnit("VespeneGeyser", 13.0, 10.0)
        cases = (
            ("bare_geyser_only", []),
            (
                "in_progress_refinery",
                [FakeUnit("Refinery", 14.0, 10.0, is_ready=False)],
            ),
        )
        for label, structures in cases:
            with self.subTest(case=label):
                bot = FakeBotAI(
                    workers=[FakeUnit("SCV", 10, 10)],
                    structures=structures,
                    geysers=[geyser],
                )
                adapter = make_adapter(bot)
                result = run(
                    adapter.assign_workers(
                        action(
                            SC2ActionType.ASSIGN_WORKERS,
                            "SCV",
                            target="gas",
                            count=1,
                        )
                    )
                )
                self.assertFalse(result)
                self.assertEqual("no_gather_target", result.detail)
                self.assertEqual([], bot.issued)

    def test_minerals_gather_uses_first_entity_when_positions_missing(self) -> None:
        # Point-less mineral entities fall back to the deterministic first
        # entry instead of crashing or refusing.
        field_a = types.SimpleNamespace(name="MineralField")
        field_b = types.SimpleNamespace(name="MineralField")
        worker = FakeUnit("SCV", 10, 10)
        bot = FakeBotAI(workers=[worker])
        bot.mineral_field = [field_a, field_b]
        adapter = make_adapter(bot)
        result = run(
            adapter.assign_workers(
                action(SC2ActionType.ASSIGN_WORKERS, "SCV", target="minerals", count=1)
            )
        )
        self.assertTrue(result)
        self.assertEqual([("gather", worker, field_a)], bot.issued)

    def test_partial_worker_assignment_is_surfaced_not_swallowed(self) -> None:
        # Fewer workers than requested must be reported as a partial
        # application, never as unqualified success.
        workers = [FakeUnit("SCV", 9, 9), FakeUnit("SCV", 11, 11)]
        bot = FakeBotAI(
            workers=workers,
            mineral_fields=[FakeUnit("MineralField", 12.0, 10.0)],
        )
        adapter = make_adapter(bot)
        result = run(
            adapter.assign_workers(
                action(
                    SC2ActionType.ASSIGN_WORKERS,
                    "SCV",
                    target="minerals",
                    count=5,
                )
            )
        )
        self.assertIsInstance(result, SC2ActionReport)
        self.assertTrue(result.applied)
        self.assertTrue(result.is_partial)
        self.assertEqual(5, result.requested_count)
        self.assertEqual(2, result.issued_count)
        self.assertFalse(bool(result), "partial issuance must not be truthy success")
        self.assertEqual(2, len(bot.issued))

    def test_falls_back_to_all_workers_when_none_idle(self) -> None:
        worker = FakeUnit("SCV", 10, 10, is_idle=False)
        bot = FakeBotAI(
            workers=[worker],
            mineral_fields=[FakeUnit("MineralField", 12.0, 10.0)],
        )
        adapter = make_adapter(bot)
        result = run(
            adapter.assign_workers(
                action(
                    SC2ActionType.ASSIGN_WORKERS,
                    "SCV",
                    target="minerals",
                    count=1,
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual(1, len(bot.issued))

    def test_refusals_issue_nothing(self) -> None:
        minerals = FakeUnit("MineralField", 12.0, 10.0)
        cases = (
            (
                "no_workers",
                FakeBotAI(workers=[], mineral_fields=[minerals]),
                "minerals",
                2,
            ),
            (
                "zero_count",
                FakeBotAI(workers=[FakeUnit("SCV")], mineral_fields=[minerals]),
                "minerals",
                0,
            ),
            (
                "unknown_resource",
                FakeBotAI(workers=[FakeUnit("SCV")], mineral_fields=[minerals]),
                "crystals",
                2,
            ),
            (
                "no_gather_target",
                FakeBotAI(workers=[FakeUnit("SCV")], mineral_fields=[]),
                "minerals",
                2,
            ),
        )
        for label, bot, resource, count in cases:
            with self.subTest(refusal=label):
                adapter = make_adapter(bot)
                result = run(
                    adapter.assign_workers(
                        action(
                            SC2ActionType.ASSIGN_WORKERS,
                            "SCV",
                            target=resource,
                            count=count,
                        )
                    )
                )
                self.assertFalse(result)
                self.assertEqual([], bot.issued)


class BuildStructureTest(unittest.TestCase):
    def test_builds_structure_near_resolved_target(self) -> None:
        bot = FakeBotAI(workers=[FakeUnit("SCV")])
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_ramp",
                    metadata={"source_structure": "Supply Depot"},
                )
            )
        )
        self.assertTrue(result)
        assert_build_calls_equal(
            self,
            [("TYPE:SUPPLYDEPOT", MapPoint(20.0, 12.0))],
            bot.build_calls,
        )
        self.assertEqual(["TYPE:SUPPLYDEPOT"], bot.can_afford_calls)

    def test_accepts_alias_target_names(self) -> None:
        bot = FakeBotAI()
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="main ramp",
                    metadata={"source_structure": "Supply Depot"},
                )
            )
        )
        self.assertTrue(result)
        assert_build_calls_equal(
            self,
            [("TYPE:SUPPLYDEPOT", MapPoint(20.0, 12.0))],
            bot.build_calls,
        )

    def test_refuses_unaffordable_build(self) -> None:
        bot = FakeBotAI(affordable=False)
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_ramp",
                )
            )
        )
        self.assertFalse(result)
        self.assertEqual([], bot.build_calls)

    def test_refuses_unresolvable_target(self) -> None:
        bot = FakeBotAI()
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="enemy_ramp",
                )
            )
        )
        self.assertFalse(result)
        self.assertEqual([], bot.build_calls)

    def test_refuses_when_bot_cannot_build(self) -> None:
        bot = types.SimpleNamespace()  # no build/expand_now/can_afford methods
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_ramp",
                )
            )
        )
        self.assertFalse(result)

    def test_expand_prefers_expand_now_for_command_center(self) -> None:
        bot = FakeBotAI()
        expand_now_calls = []

        async def expand_now():
            expand_now_calls.append(True)
            return None

        bot.expand_now = expand_now
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "COMMANDCENTER",
                    target="self_natural",
                    metadata={"source_structure": "Command Center"},
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual([True], expand_now_calls)
        self.assertEqual([], bot.build_calls)

    def test_uses_bot_unit_type_id_resolver_fallback(self) -> None:
        bot = FakeBotAI()
        bot.unit_type_id_resolver = lambda name: ("BOT-TYPE", name)
        adapter = make_adapter(bot, unit_type_resolver=None)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_ramp",
                )
            )
        )
        self.assertTrue(result)
        assert_build_calls_equal(
            self,
            [(("BOT-TYPE", "SUPPLYDEPOT"), MapPoint(20.0, 12.0))],
            bot.build_calls,
        )

    def test_refinery_targets_geyser_unit_never_position(self) -> None:
        # Real python-sc2 requires the geyser *unit* as the gas build target;
        # a Point2 either fails placement or raises inside BotAI.build.
        geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
        bot = FakeBotAI(workers=[FakeUnit("SCV", 10, 10)], geysers=[geyser])
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "REFINERY",
                    target="main geyser",
                    metadata={"source_structure": "Refinery"},
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual([("TYPE:REFINERY", geyser)], bot.build_calls)

    def test_refinery_prefers_worker_build_gas_when_available(self) -> None:
        geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
        worker = FakeUnit("SCV", 10, 10)
        build_gas_calls = []

        def build_gas(target):
            build_gas_calls.append(target)
            return ("build_gas", worker, target)

        worker.build_gas = build_gas
        bot = FakeBotAI(workers=[worker], geysers=[geyser])
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "REFINERY",
                    target="main geyser",
                    metadata={"source_structure": "Refinery"},
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual([geyser], build_gas_calls)
        self.assertEqual([], bot.build_calls)

    def test_refinery_refuses_without_free_geyser(self) -> None:
        taken_geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
        existing_refinery = FakeUnit("Refinery", 12.0, 10.0)
        cases = (
            ("no_geysers", [], []),
            ("all_geysers_taken", [taken_geyser], [existing_refinery]),
        )
        for label, geysers, structures in cases:
            with self.subTest(case=label):
                bot = FakeBotAI(
                    workers=[FakeUnit("SCV", 10, 10)],
                    geysers=geysers,
                    structures=structures,
                )
                adapter = make_adapter(bot)
                result = run(
                    adapter.build_structure(
                        action(
                            SC2ActionType.BUILD_STRUCTURE,
                            "REFINERY",
                            target="main geyser",
                            metadata={"source_structure": "Refinery"},
                        )
                    )
                )
                self.assertFalse(result)
                self.assertEqual([], bot.build_calls)

    @unittest.skipIf(PYTHON_SC2_INSTALLED, "python-sc2 is installed")
    def test_missing_python_sc2_raises_actionable_error(self) -> None:
        adapter = make_adapter(FakeBotAI(), unit_type_resolver=None)
        with self.assertRaises(MissingPythonSC2Error) as captured:
            run(
                adapter.build_structure(
                    action(
                        SC2ActionType.BUILD_STRUCTURE,
                        "SUPPLYDEPOT",
                        target="self_ramp",
                    )
                )
            )
        message = str(captured.exception)
        self.assertEqual(PYTHON_SC2_UNIT_TYPE_HINT, message)
        self.assertIn("burnysc2", message)
        self.assertIn("unit_type_resolver", message)


class TrainUnitTest(unittest.TestCase):
    def test_trains_round_robin_across_ready_idle_producers(self) -> None:
        rax_a = FakeUnit("Barracks")
        rax_b = FakeUnit("Barracks")
        bot = FakeBotAI(structures=[rax_a, rax_b])
        adapter = make_adapter(bot)
        result = run(
            adapter.train_unit(
                action(
                    SC2ActionType.TRAIN_UNIT,
                    "MARINE",
                    count=3,
                    metadata={"producer": "BARRACKS"},
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual(
            [
                ("train", rax_a, "TYPE:MARINE"),
                ("train", rax_b, "TYPE:MARINE"),
                ("train", rax_a, "TYPE:MARINE"),
            ],
            bot.issued,
        )

    def test_caps_orders_at_requested_count(self) -> None:
        bot = FakeBotAI(structures=[FakeUnit("Barracks"), FakeUnit("Barracks")])
        adapter = make_adapter(bot)
        result = run(
            adapter.train_unit(
                action(
                    SC2ActionType.TRAIN_UNIT,
                    "MARINE",
                    count=1,
                    metadata={"producer": "BARRACKS"},
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual(1, len(bot.issued))

    def test_stops_when_budget_runs_out_without_overclaiming(self) -> None:
        bot = FakeBotAI(
            structures=[FakeUnit("Barracks"), FakeUnit("Barracks")],
            affordable=[True, False],
        )
        adapter = make_adapter(bot)
        result = run(
            adapter.train_unit(
                action(
                    SC2ActionType.TRAIN_UNIT,
                    "MARINE",
                    count=3,
                    metadata={"producer": "BARRACKS"},
                )
            )
        )
        self.assertIsInstance(result, SC2ActionReport)
        self.assertTrue(result.applied)
        self.assertTrue(result.is_partial)
        self.assertEqual(3, result.requested_count)
        self.assertEqual(1, result.issued_count)
        self.assertEqual("unaffordable", result.detail)
        self.assertFalse(bool(result), "partial issuance must not be truthy success")
        self.assertEqual(1, len(bot.issued))

    def test_train_stall_guard_refuses_producers_without_train(self) -> None:
        # A producer object without a callable train() must refuse instead
        # of looping forever.
        producer = types.SimpleNamespace(name="Barracks", is_idle=True, is_ready=True)
        bot = FakeBotAI()
        bot.structures = [producer]
        adapter = make_adapter(bot)
        result = run(
            adapter.train_unit(
                action(
                    SC2ActionType.TRAIN_UNIT,
                    "MARINE",
                    count=2,
                    metadata={"producer": "BARRACKS"},
                )
            )
        )
        self.assertFalse(result)
        self.assertEqual(0, result.issued_count)
        self.assertEqual([], bot.issued)

    def test_awaitable_and_none_can_afford_results_are_supported(self) -> None:
        async def async_can_afford(item):
            return True

        cases = (
            ("awaitable_can_afford", async_can_afford),
            ("none_means_yes", lambda item: None),
        )
        for label, can_afford in cases:
            with self.subTest(case=label):
                rax = FakeUnit("Barracks")
                bot = FakeBotAI(structures=[rax])
                bot.can_afford = can_afford
                adapter = make_adapter(bot)
                result = run(
                    adapter.train_unit(
                        action(
                            SC2ActionType.TRAIN_UNIT,
                            "MARINE",
                            count=1,
                            metadata={"producer": "BARRACKS"},
                        )
                    )
                )
                self.assertTrue(result)
                self.assertEqual([("train", rax, "TYPE:MARINE")], bot.issued)

    def test_manual_filter_when_structures_lack_ready_idle_chain(self) -> None:
        producer = FakeUnit("Command Center")
        bot = FakeBotAI()
        bot.structures = [producer]  # plain list: no .ready.idle chain
        adapter = make_adapter(bot)
        result = run(
            adapter.train_unit(
                action(
                    SC2ActionType.TRAIN_UNIT,
                    "SCV",
                    count=1,
                    metadata={"producer": "COMMANDCENTER"},
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual([("train", producer, "TYPE:SCV")], bot.issued)

    def test_refusals_issue_nothing(self) -> None:
        cases = (
            (
                "no_matching_producer",
                FakeBotAI(structures=[FakeUnit("Factory")]),
                {"producer": "BARRACKS"},
                1,
            ),
            (
                "busy_producers",
                FakeBotAI(structures=[FakeUnit("Barracks", is_idle=False)]),
                {"producer": "BARRACKS"},
                1,
            ),
            (
                "in_progress_producers",
                FakeBotAI(structures=[FakeUnit("Barracks", is_ready=False)]),
                {"producer": "BARRACKS"},
                1,
            ),
            (
                "unaffordable",
                FakeBotAI(structures=[FakeUnit("Barracks")], affordable=False),
                {"producer": "BARRACKS"},
                1,
            ),
            (
                "zero_count",
                FakeBotAI(structures=[FakeUnit("Barracks")]),
                {"producer": "BARRACKS"},
                0,
            ),
            ("missing_producer_metadata", FakeBotAI(structures=[FakeUnit("Barracks")]), {}, 1),
        )
        for label, bot, metadata, count in cases:
            with self.subTest(refusal=label):
                adapter = make_adapter(bot)
                result = run(
                    adapter.train_unit(
                        action(
                            SC2ActionType.TRAIN_UNIT,
                            "MARINE",
                            count=count,
                            metadata=metadata,
                        )
                    )
                )
                self.assertFalse(result)
                self.assertEqual([], bot.issued)


class MoveAndAttackGroupTest(unittest.TestCase):
    def make_army_bot(self):
        workers = [FakeUnit("SCV", 10, 10), FakeUnit("SCV", 11, 10)]
        marines = [
            FakeUnit("Marine", 20, 12),
            FakeUnit("Marine", 21, 12),
            FakeUnit("Marine", 22, 12),
        ]
        return FakeBotAI(workers=workers, units=workers + marines), workers, marines

    def test_move_group_moves_units_matching_subject_type(self) -> None:
        bot, workers, marines = self.make_army_bot()
        adapter = make_adapter(bot)
        result = run(
            adapter.move_group(
                action(SC2ActionType.MOVE_GROUP, "MARINE", target="self_ramp")
            )
        )
        self.assertTrue(result)
        assert_order_points_equal(
            self,
            [("move", marine, MapPoint(20.0, 12.0)) for marine in marines],
            bot.issued,
        )
        for worker in workers:
            self.assertEqual([], worker.issued_orders)

    def test_attack_move_uses_non_worker_units_for_free_text(self) -> None:
        bot, _, marines = self.make_army_bot()
        adapter = make_adapter(bot)
        result = run(
            adapter.attack_move(
                action(
                    SC2ActionType.ATTACK_MOVE,
                    "available combat units",
                    target="enemy_mineral_line",
                )
            )
        )
        self.assertTrue(result)
        assert_order_points_equal(
            self,
            [("attack", marine, MapPoint(88.0, 95.0)) for marine in marines],
            bot.issued,
        )

    def test_leading_count_caps_free_text_group(self) -> None:
        bot, _, marines = self.make_army_bot()
        adapter = make_adapter(bot)
        result = run(
            adapter.move_group(
                action(SC2ActionType.MOVE_GROUP, "2 Marines", target="enemy_main")
            )
        )
        self.assertTrue(result)
        assert_order_points_equal(
            self,
            [("move", marine, MapPoint(90.0, 90.0)) for marine in marines[:2]],
            bot.issued,
        )

    def test_counted_type_phrase_never_substitutes_other_unit_types(self) -> None:
        # "6 Marines" with a mixed army must select only Marines, never
        # redirect Hellions, and must surface the shortfall as partial.
        workers = [FakeUnit("SCV", 10, 10)]
        hellions = [FakeUnit("Hellion", 19 + index, 12) for index in range(3)]
        marines = [FakeUnit("Marine", 25 + index, 12) for index in range(2)]
        bot = FakeBotAI(workers=workers, units=workers + hellions + marines)
        adapter = make_adapter(bot)
        result = run(
            adapter.move_group(
                action(SC2ActionType.MOVE_GROUP, "6 Marines", target="enemy_main")
            )
        )
        self.assertIsInstance(result, SC2ActionReport)
        self.assertTrue(result.applied)
        self.assertTrue(result.is_partial)
        self.assertEqual(6, result.requested_count)
        self.assertEqual(2, result.issued_count)
        assert_order_points_equal(
            self,
            [("move", marine, MapPoint(90.0, 90.0)) for marine in marines],
            bot.issued,
        )
        for hellion in hellions:
            self.assertEqual([], hellion.issued_orders)

    def test_plural_type_phrase_refuses_when_no_units_of_type_exist(self) -> None:
        # 0 Marines plus 4 Hellions: ordering Hellions for "6 Marines" or
        # "Marines" would silently do something different from the narration.
        workers = [FakeUnit("SCV", 10, 10)]
        hellions = [FakeUnit("Hellion", 19 + index, 12) for index in range(4)]
        bot = FakeBotAI(workers=workers, units=workers + hellions)
        adapter = make_adapter(bot)
        for subject in ("6 Marines", "Marines", "1 Marine"):
            with self.subTest(subject=subject):
                bot.issued.clear()
                result = run(
                    adapter.attack_move(
                        action(
                            SC2ActionType.ATTACK_MOVE,
                            subject,
                            target="enemy_main",
                        )
                    )
                )
                self.assertFalse(result)
                self.assertEqual([], bot.issued)
                for hellion in hellions:
                    self.assertEqual([], hellion.issued_orders)

    def test_falsy_unit_order_results_are_refused(self) -> None:
        broken = FakeUnit("Marine", 20, 12)
        broken.move = lambda point: False
        bot = FakeBotAI(workers=[], units=[broken])
        adapter = make_adapter(bot)
        result = run(
            adapter.move_group(
                action(SC2ActionType.MOVE_GROUP, "MARINE", target="enemy_main")
            )
        )
        self.assertFalse(result)
        self.assertEqual([], bot.issued)

    def test_orders_count_without_bot_do_collection(self) -> None:
        marine = FakeUnit("Marine", 20, 12)
        bot = FakeBotAI(workers=[], units=[marine])
        bot.do = None  # bots without a callable do() still issue unit orders
        adapter = make_adapter(bot)
        result = run(
            adapter.move_group(
                action(SC2ActionType.MOVE_GROUP, "MARINE", target="enemy_main")
            )
        )
        self.assertTrue(result)
        self.assertEqual(1, len(marine.issued_orders))

    def test_worker_scout_free_text_selects_single_worker(self) -> None:
        bot, workers, _ = self.make_army_bot()
        adapter = make_adapter(bot)
        result = run(
            adapter.move_group(
                action(SC2ActionType.MOVE_GROUP, "1 SCV", target="enemy_main")
            )
        )
        self.assertTrue(result)
        assert_order_points_equal(
            self,
            [("move", workers[0], MapPoint(90.0, 90.0))],
            bot.issued,
        )

    def test_refusals_issue_nothing(self) -> None:
        bot, _, _ = self.make_army_bot()
        cases = (
            ("known_type_without_units", "HELLION", "enemy_main"),
            ("unresolvable_target", "MARINE", "enemy_ramp"),
            ("unknown_target_name", "MARINE", "behind the moon"),
        )
        for label, subject, target in cases:
            with self.subTest(refusal=label):
                bot.issued.clear()
                adapter = make_adapter(bot)
                result = run(
                    adapter.move_group(
                        action(SC2ActionType.MOVE_GROUP, subject, target=target)
                    )
                )
                self.assertFalse(result)
                self.assertEqual([], bot.issued)


class RepairTest(unittest.TestCase):
    def test_repairs_loosely_matched_damaged_structure(self) -> None:
        bunker = FakeUnit("Bunker", 20, 12, health=60.0, health_max=100.0)
        depot = FakeUnit("Supply Depot", 18, 12, health=400.0, health_max=400.0)
        workers = [FakeUnit("SCV", 10, 10), FakeUnit("SCV", 11, 10)]
        bot = FakeBotAI(workers=workers, structures=[depot, bunker])
        adapter = make_adapter(bot)
        result = run(
            adapter.repair(
                action(SC2ActionType.REPAIR, "SCV", target="front bunker", count=2)
            )
        )
        self.assertTrue(result)
        self.assertEqual(
            [("repair", workers[0], bunker), ("repair", workers[1], bunker)],
            bot.issued,
        )

    def test_generic_target_accepts_any_damaged_structure(self) -> None:
        depot = FakeUnit("Supply Depot", 18, 12, health=200.0, health_max=400.0)
        worker = FakeUnit("SCV", 10, 10)
        bot = FakeBotAI(workers=[worker], structures=[depot])
        adapter = make_adapter(bot)
        result = run(
            adapter.repair(
                action(SC2ActionType.REPAIR, "SCV", target="building", count=1)
            )
        )
        self.assertTrue(result)
        self.assertEqual([("repair", worker, depot)], bot.issued)

    def test_repairs_damaged_own_unit_when_no_structure_matches(self) -> None:
        # The damaged own-unit fallback scan: a Hellion at 40/90 health is a
        # valid named repair target even with healthy structures around.
        hellion = FakeUnit("Hellion", 22, 12, health=40.0, health_max=90.0)
        depot = FakeUnit("Supply Depot", 18, 12, health=400.0, health_max=400.0)
        worker = FakeUnit("SCV", 10, 10)
        bot = FakeBotAI(
            workers=[worker],
            units=[worker, hellion],
            structures=[depot],
        )
        adapter = make_adapter(bot)
        result = run(
            adapter.repair(
                action(SC2ActionType.REPAIR, "SCV", target="Hellion", count=1)
            )
        )
        self.assertTrue(result)
        self.assertEqual([("repair", worker, hellion)], bot.issued)

    def test_generic_target_refuses_when_only_units_are_damaged(self) -> None:
        # Generic targets such as "building" accept structures only.
        hellion = FakeUnit("Hellion", 22, 12, health=40.0, health_max=90.0)
        healthy_depot = FakeUnit("Supply Depot", 18, 12, health=400.0, health_max=400.0)
        worker = FakeUnit("SCV", 10, 10)
        bot = FakeBotAI(
            workers=[worker],
            units=[worker, hellion],
            structures=[healthy_depot],
        )
        adapter = make_adapter(bot)
        result = run(
            adapter.repair(
                action(SC2ActionType.REPAIR, "SCV", target="building", count=1)
            )
        )
        self.assertFalse(result)
        self.assertEqual([], bot.issued)

    def test_partial_repair_crew_is_surfaced(self) -> None:
        bunker = FakeUnit("Bunker", 20, 12, health=60.0, health_max=100.0)
        worker = FakeUnit("SCV", 10, 10)
        bot = FakeBotAI(workers=[worker], structures=[bunker])
        adapter = make_adapter(bot)
        result = run(
            adapter.repair(
                action(SC2ActionType.REPAIR, "SCV", target="front bunker", count=3)
            )
        )
        self.assertIsInstance(result, SC2ActionReport)
        self.assertTrue(result.applied)
        self.assertTrue(result.is_partial)
        self.assertEqual(3, result.requested_count)
        self.assertEqual(1, result.issued_count)
        self.assertEqual([("repair", worker, bunker)], bot.issued)

    def test_count_caps_repairing_workers(self) -> None:
        bunker = FakeUnit("Bunker", 20, 12, health=60.0, health_max=100.0)
        workers = [FakeUnit("SCV"), FakeUnit("SCV"), FakeUnit("SCV")]
        bot = FakeBotAI(workers=workers, structures=[bunker])
        adapter = make_adapter(bot)
        result = run(
            adapter.repair(
                action(SC2ActionType.REPAIR, "SCV", target="Bunker", count=1)
            )
        )
        self.assertTrue(result)
        self.assertEqual([("repair", workers[0], bunker)], bot.issued)

    def test_refusals_issue_nothing(self) -> None:
        healthy_bunker = FakeUnit("Bunker", 20, 12, health=100.0, health_max=100.0)
        damaged_bunker = FakeUnit("Bunker", 20, 12, health=50.0, health_max=100.0)
        cases = (
            (
                "undamaged_target",
                FakeBotAI(workers=[FakeUnit("SCV")], structures=[healthy_bunker]),
                "front bunker",
                1,
            ),
            (
                "no_matching_entity",
                FakeBotAI(workers=[FakeUnit("SCV")], structures=[damaged_bunker]),
                "missile turret",
                1,
            ),
            (
                "no_workers",
                FakeBotAI(workers=[], units=[], structures=[damaged_bunker]),
                "front bunker",
                1,
            ),
            (
                "zero_count",
                FakeBotAI(workers=[FakeUnit("SCV")], structures=[damaged_bunker]),
                "front bunker",
                0,
            ),
        )
        for label, bot, target, count in cases:
            with self.subTest(refusal=label):
                adapter = make_adapter(bot)
                result = run(
                    adapter.repair(
                        action(SC2ActionType.REPAIR, "SCV", target=target, count=count)
                    )
                )
                self.assertFalse(result)
                self.assertEqual([], bot.issued)


class ObserveTest(unittest.TestCase):
    def test_observe_returns_json_ready_state_mapping(self) -> None:
        workers = [FakeUnit("SCV"), FakeUnit("SCV")]
        bot = FakeBotAI(
            workers=workers,
            units=workers + [FakeUnit("Marine")],
            structures=[FakeUnit("Command Center"), FakeUnit("Barracks")],
        )
        adapter = make_adapter(bot)
        observation = run(
            adapter.observe(
                action(SC2ActionType.OBSERVE, "visible_state", count=0)
            )
        )
        self.assertEqual(observation, json.loads(json.dumps(observation)))
        self.assertEqual(400, observation["minerals"])
        self.assertEqual(100, observation["vespene"])
        self.assertEqual({"MARINE": 1, "SCV": 2}, observation["own_units"])
        self.assertEqual(
            {"BARRACKS": 1, "COMMANDCENTER": 1},
            observation["own_structures"],
        )
        self.assertTrue(observation["observation_complete"])


class PipelineSmokeTest(unittest.TestCase):
    """Full Step 2 acceptance: planner -> executor -> adapter -> fake BotAI."""

    def execute_payload(self, adapter, payload):
        plan = SC2ActionPlanner().build_plan(payload)
        executor = SC2RuntimeExecutor()

        async def flow():
            await executor.start(adapter)
            result = await executor.execute(plan)
            await executor.close()
            return result

        return run(flow()), executor

    def test_train_worker_payload_executes_successfully_end_to_end(self) -> None:
        command_center = FakeUnit("Command Center")
        bot = FakeBotAI(
            workers=[FakeUnit("SCV")],
            structures=[command_center],
        )
        adapter = make_adapter(bot)
        result, executor = self.execute_payload(
            adapter,
            {"intent": "TRAIN_WORKER", "count": 2},
        )
        self.assertTrue(result.success)
        self.assertEqual((), executor.lifecycle_errors)
        self.assertEqual((), result.errors)
        self.assertEqual(1, len(result.applied_actions))
        self.assertEqual({}, result.audit["observations"])
        self.assertEqual(
            [
                ("train", command_center, "TYPE:SCV"),
                ("train", command_center, "TYPE:SCV"),
            ],
            bot.issued,
        )

    def test_summarize_state_populates_observations_audit(self) -> None:
        bot = FakeBotAI(workers=[FakeUnit("SCV")])
        adapter = make_adapter(bot)
        result, _ = self.execute_payload(adapter, {"intent": "SUMMARIZE_STATE"})
        self.assertTrue(result.success)
        self.assertEqual(1, len(result.applied_actions))
        observation = result.audit["observations"]["0"]
        self.assertEqual(400, observation["minerals"])
        self.assertEqual({"SCV": 1}, observation["own_units"])
        self.assertEqual(
            observation,
            json.loads(json.dumps(observation)),
        )

    def test_infeasible_action_is_skipped_not_overclaimed(self) -> None:
        bot = FakeBotAI(structures=[])  # no producer for SCV training
        adapter = make_adapter(bot)
        result, _ = self.execute_payload(
            adapter,
            {"intent": "TRAIN_WORKER", "count": 1},
        )
        self.assertFalse(result.success)
        self.assertEqual(1, len(result.skipped_actions))
        self.assertEqual(0, len(result.applied_actions))

    def test_lazy_map_resolver_is_built_from_bot_on_first_use(self) -> None:
        marine = FakeUnit("Marine", 12, 12)
        bot = FakeBotAI(workers=[FakeUnit("SCV")], units=[marine])
        adapter = make_adapter(bot, map_resolver=None)
        self.assertIsNone(adapter.map_resolver)
        result = run(
            adapter.attack_move(
                action(SC2ActionType.ATTACK_MOVE, "MARINE", target="enemy_main")
            )
        )
        self.assertTrue(result)
        self.assertIsInstance(adapter.map_resolver, SC2MapResolver)
        assert_order_points_equal(
            self,
            [("attack", marine, MapPoint(90.0, 90.0))],
            bot.issued,
        )


class ImportIsolationTest(unittest.TestCase):
    def test_adapter_import_loads_no_optional_runtime_or_toycraft(self) -> None:
        script = (
            "import json, sys; "
            "import starcraft_commander.python_sc2_adapter; "
            "print(json.dumps({"
            "'sc2': 'sc2' in sys.modules, "
            "'faster_whisper': 'faster_whisper' in sys.modules, "
            "'sounddevice': 'sounddevice' in sys.modules, "
            "'toycraft': 'toycraft_commander' in sys.modules"
            "}, sort_keys=True))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(
            {
                "faster_whisper": False,
                "sc2": False,
                "sounddevice": False,
                "toycraft": False,
            },
            json.loads(completed.stdout),
        )


if __name__ == "__main__":
    unittest.main()
