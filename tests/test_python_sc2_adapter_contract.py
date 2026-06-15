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
from starcraft_commander.map_resolver import (
    MapPoint,
    SC2MapResolver,
    SC2RuntimeMapResolver,
)
from starcraft_commander.python_sc2_adapter import (
    MissingPythonSC2Error,
    PYTHON_SC2_UNIT_TYPE_HINT,
    PythonSC2BotAdapter,
    SC2_ADAPTER_ACTION_METHOD_NAMES,
    SC2_EXECUTOR_LIFECYCLE_METHOD_NAMES,
    SC2BotAdapterInterface,
)
from starcraft_commander.sc2_executor import (
    SC2ActionPlanner,
    SC2RuntimeExecutor,
    SC2_STRUCTURE_TYPE_IDS,
)


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
        safe_points=None,
        visible_points=None,
        pathable_points=None,
        buildable_points=None,
        can_place_points=None,
        expansion_locations=None,
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
        self.expansion_locations_list = FakeUnitGroup(
            expansion_locations
            if expansion_locations is not None
            else [FakePoint(10.0, 10.0), FakePoint(30.0, 30.0)]
        )
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
        self.safe_points = dict(safe_points or {})
        self.visible_points = dict(visible_points or {})
        self.pathable_points = dict(pathable_points or {})
        self.buildable_points = dict(buildable_points or {})
        self.can_place_points = dict(can_place_points or {})
        self.issued = []
        self.build_calls = []
        self.can_afford_calls = []
        self.safety_checks = []
        self.visibility_checks = []
        self.pathing_checks = []
        self.placement_grid_checks = []
        self.can_place_calls = []

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

    def is_position_safe(self, point):
        self.safety_checks.append(point)
        return self.safe_points.get(point_xy(point), True)

    def is_visible(self, point):
        self.visibility_checks.append(point)
        return self.visible_points.get(point_xy(point), True)

    def in_pathing_grid(self, point):
        self.pathing_checks.append(point)
        return self.pathable_points.get(point_xy(point), True)

    def in_placement_grid(self, point):
        self.placement_grid_checks.append(point)
        return self.buildable_points.get(point_xy(point), True)

    async def can_place(self, type_id, point):
        self.can_place_calls.append((type_id, point))
        return self.can_place_points.get(point_xy(point), True)


FAKE_UNIT_TYPE_IDS = {
    "SCV": "TYPE:SCV",
    "MARINE": "TYPE:MARINE",
    "HELLION": "TYPE:HELLION",
    "SUPPLYDEPOT": "TYPE:SUPPLYDEPOT",
    "BARRACKS": "TYPE:BARRACKS",
    "BUNKER": "TYPE:BUNKER",
    "COMMANDCENTER": "TYPE:COMMANDCENTER",
    "FACTORY": "TYPE:FACTORY",
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
            "self_mineral_line": MapPoint(12.0, 11.0),
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
                "move_camera",
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


class MoveCameraTest(unittest.TestCase):
    def test_move_camera_invokes_runtime_camera_capability(self) -> None:
        class RuntimeCameraBot(FakeBotAI):
            def __init__(self):
                super().__init__()
                self.camera_moves = []

            def move_camera(self, point):
                self.camera_moves.append(point)
                return True

        bot = RuntimeCameraBot()
        adapter = make_adapter(bot)

        result = run(
            adapter.move_camera(
                action(
                    SC2ActionType.MOVE_CAMERA,
                    "camera",
                    target="self_ramp",
                    count=0,
                )
            )
        )

        self.assertIsInstance(result, SC2ActionReport)
        self.assertTrue(result.applied)
        self.assertEqual(1, result.requested_count)
        self.assertEqual(1, result.issued_count)
        self.assertEqual(1, len(bot.camera_moves))
        self.assertEqual((20.0, 12.0), point_xy(bot.camera_moves[0]))
        self.assertEqual([], bot.issued)


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
    def _supported_building_cases(self):
        ramp = MapPoint(20.0, 12.0)
        natural = MapPoint(30.0, 30.0)
        return {
            "Barracks": {
                "subject": "BARRACKS",
                "target": "self_ramp",
                "center": ramp,
                "fallback": MapPoint(20.0, 11.0),
            },
            "Bunker": {
                "subject": "BUNKER",
                "target": "self_ramp",
                "center": ramp,
                "fallback": MapPoint(20.0, 11.0),
            },
            "Command Center": {
                "subject": "COMMANDCENTER",
                "target": "self_natural",
                "center": natural,
                "fallback": MapPoint(30.0, 29.0),
            },
            "Factory": {
                "subject": "FACTORY",
                "target": "self_ramp",
                "center": ramp,
                "fallback": MapPoint(20.0, 11.0),
            },
            "Refinery": {
                "subject": "REFINERY",
                "target": "main geyser",
            },
            "Supply Depot": {
                "subject": "SUPPLYDEPOT",
                "target": "self_ramp",
                "center": ramp,
                "fallback": MapPoint(20.0, 11.0),
            },
        }

    def _bounded_point_policy(self, center: MapPoint) -> dict[str, object]:
        return {
            "position": center.to_dict(),
            "search_radius": 1,
        }

    def _blocked_radius_one_candidates(
        self,
        center: MapPoint,
    ) -> dict[tuple[float, float], bool]:
        return {
            center.to_tuple(): False,
            (center.x, center.y - 1.0): False,
            (center.x + 1.0, center.y): False,
            (center.x, center.y + 1.0): False,
            (center.x - 1.0, center.y): False,
        }

    def test_each_supported_building_type_has_successful_placement(self) -> None:
        cases = self._supported_building_cases()
        self.assertEqual(set(SC2_STRUCTURE_TYPE_IDS), set(cases))

        for structure, case in cases.items():
            with self.subTest(structure=structure):
                if structure == "Refinery":
                    geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
                    bot = FakeBotAI(
                        workers=[FakeUnit("SCV", 10, 10)],
                        geysers=[geyser],
                    )
                else:
                    bot = FakeBotAI(workers=[FakeUnit("SCV")])
                adapter = make_adapter(bot)

                result = run(
                    adapter.build_structure(
                        action(
                            SC2ActionType.BUILD_STRUCTURE,
                            case["subject"],
                            target=case["target"],
                            metadata={"source_structure": structure},
                        )
                    )
                )

                self.assertTrue(result)
                self.assertEqual(1, len(bot.build_calls))
                type_id, near = bot.build_calls[0]
                self.assertEqual(f"TYPE:{case['subject']}", type_id)
                if structure == "Refinery":
                    self.assertIs(near, geyser)
                else:
                    self.assertEqual(case["center"].to_tuple(), point_xy(near))

    def test_each_supported_building_type_falls_back_to_next_valid_candidate(
        self,
    ) -> None:
        cases = self._supported_building_cases()
        self.assertEqual(set(SC2_STRUCTURE_TYPE_IDS), set(cases))

        for structure, case in cases.items():
            with self.subTest(structure=structure):
                if structure == "Refinery":
                    taken_geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
                    free_geyser = FakeUnit("VespeneGeyser", 15.0, 10.0)
                    existing_refinery = FakeUnit("Refinery", 12.0, 10.0)
                    bot = FakeBotAI(
                        workers=[FakeUnit("SCV", 10, 10)],
                        geysers=[taken_geyser, free_geyser],
                        structures=[existing_refinery],
                    )
                    metadata = {
                        "source_structure": structure,
                        "placement_policy": self._bounded_point_policy(
                            MapPoint(12.0, 10.0)
                        ),
                    }
                else:
                    center = case["center"]
                    bot = FakeBotAI(
                        workers=[FakeUnit("SCV")],
                        buildable_points={center.to_tuple(): False},
                    )
                    metadata = {
                        "source_structure": structure,
                        "placement_policy": self._bounded_point_policy(center),
                    }
                adapter = make_adapter(bot)

                result = run(
                    adapter.build_structure(
                        action(
                            SC2ActionType.BUILD_STRUCTURE,
                            case["subject"],
                            target=case["target"],
                            metadata=metadata,
                        )
                    )
                )

                self.assertTrue(result)
                self.assertEqual(1, len(bot.build_calls))
                type_id, near = bot.build_calls[0]
                self.assertEqual(f"TYPE:{case['subject']}", type_id)
                if structure == "Refinery":
                    self.assertIs(near, free_geyser)
                else:
                    self.assertEqual(case["fallback"].to_tuple(), point_xy(near))

    def test_each_supported_building_type_reports_no_valid_placement(self) -> None:
        cases = self._supported_building_cases()
        self.assertEqual(set(SC2_STRUCTURE_TYPE_IDS), set(cases))

        for structure, case in cases.items():
            with self.subTest(structure=structure):
                if structure == "Refinery":
                    taken_geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
                    existing_refinery = FakeUnit("Refinery", 12.0, 10.0)
                    bot = FakeBotAI(
                        workers=[FakeUnit("SCV", 10, 10)],
                        geysers=[taken_geyser],
                        structures=[existing_refinery],
                    )
                    metadata = {"source_structure": structure}
                else:
                    center = case["center"]
                    blocked = self._blocked_radius_one_candidates(center)
                    bot = FakeBotAI(
                        workers=[FakeUnit("SCV")],
                        buildable_points=blocked,
                    )
                    metadata = {
                        "source_structure": structure,
                        "placement_policy": self._bounded_point_policy(center),
                    }
                adapter = make_adapter(bot)

                result = run(
                    adapter.build_structure(
                        action(
                            SC2ActionType.BUILD_STRUCTURE,
                            case["subject"],
                            target=case["target"],
                            metadata=metadata,
                        )
                    )
                )

                self.assertIsInstance(result, SC2ActionReport)
                self.assertFalse(result)
                if structure == "Refinery":
                    self.assertIn("invalid_refinery_target", result.detail)
                    self.assertIn("no_free_geyser", result.detail)
                else:
                    self.assertIn("no_safe_placement", result.detail)
                    self.assertIn("not_buildable", result.detail)
                self.assertEqual([], bot.build_calls)

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

    def test_ramp_build_falls_back_to_main_base_when_ramp_not_visible(self) -> None:
        bot = FakeBotAI(
            workers=[FakeUnit("SCV")],
        )
        bot.is_visible = lambda point: point_xy(point)[0] < 13.0
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
            [("TYPE:SUPPLYDEPOT", MapPoint(10.0, 10.0))],
            bot.build_calls,
        )

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

    def test_build_placement_policy_anchor_selects_resolved_target_point(self) -> None:
        bot = FakeBotAI(workers=[FakeUnit("SCV")])
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_ramp",
                    metadata={
                        "source_structure": "Supply Depot",
                        "placement_policy": {
                            "anchor": "mineral line",
                            "anchor_target": "self_mineral_line",
                            "spatial_relation": "away_from",
                        },
                    },
                )
            )
        )
        self.assertTrue(result)
        self.assertIsInstance(result, SC2ActionReport)
        assert isinstance(result, SC2ActionReport)
        self.assertEqual("", result.detail)
        self.assertEqual("", result.audit["failure_reason"])
        self.assertEqual("", result.audit["failure_reason_code"])
        self.assertEqual(
            {
                "anchor": "mineral line",
                "anchor_target": "self_mineral_line",
                "spatial_relation": "away_from",
            },
            result.audit["placement_policy"],
        )
        self.assertEqual(
            "placement_policy.anchor_target",
            result.audit["anchor_source"]["source"],
        )
        self.assertEqual(
            "python-sc2 observations",
            result.audit["resolved_placement_policy"]["anchor_source"],
        )
        self.assertEqual(
            "self_mineral_line",
            result.audit["resolved_target_policy"]["anchor_target"],
        )
        self.assertAlmostEqual(
            9.316718427000252,
            result.audit["resolved_target_policy"]["resolved_point"]["x"],
        )
        self.assertAlmostEqual(
            9.658359213500127,
            result.audit["resolved_target_policy"]["resolved_point"]["y"],
        )
        search_result = result.audit["search_result"]
        self.assertEqual("selected", search_result["status"])
        self.assertEqual("", search_result["reason_code"])
        self.assertIsNotNone(search_result["selected_tile"])
        self.assertEqual(
            search_result["selected_tile"],
            search_result["selected_result"]["tile"],
        )
        self.assertEqual("", search_result["selected_result"]["reason_code"])
        self.assertEqual(
            "python-sc2 build placement search",
            search_result["selected_result"]["source"],
        )
        self.assertIsNone(search_result["no_match"])
        self.assertEqual(1, len(bot.build_calls))
        type_id, near = bot.build_calls[0]
        self.assertEqual("TYPE:SUPPLYDEPOT", type_id)
        self.assertAlmostEqual(9.316718427000252, point_xy(near)[0])
        self.assertAlmostEqual(9.658359213500127, point_xy(near)[1])

    def test_build_near_placement_policy_selects_bounded_near_point(self) -> None:
        bot = FakeBotAI(workers=[FakeUnit("SCV")])
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_natural",
                    metadata={
                        "source_structure": "Supply Depot",
                        "placement_policy": {
                            "anchor": "natural expansion",
                            "anchor_target": "self_natural",
                            "spatial_relation": "near",
                        },
                    },
                )
            )
        )
        self.assertTrue(result)
        assert_build_calls_equal(
            self,
            [("TYPE:SUPPLYDEPOT", MapPoint(30.0, 27.0))],
            bot.build_calls,
        )

    def test_build_toward_placement_policy_selects_directional_point(self) -> None:
        bot = FakeBotAI(workers=[FakeUnit("SCV")])
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_ramp",
                    metadata={
                        "source_structure": "Supply Depot",
                        "placement_policy": {
                            "anchor": "main ramp",
                            "anchor_target": "self_ramp",
                            "spatial_relation": "toward",
                        },
                    },
                )
            )
        )
        self.assertTrue(result)
        self.assertEqual(1, len(bot.build_calls))
        type_id, near = bot.build_calls[0]
        self.assertEqual("TYPE:SUPPLYDEPOT", type_id)
        self.assertAlmostEqual(15.88348405414552, point_xy(near)[0])
        self.assertAlmostEqual(11.176696810829104, point_xy(near)[1])

    def test_build_search_filters_safety_visibility_pathing_and_buildability(self) -> None:
        bot = FakeBotAI(
            workers=[FakeUnit("SCV")],
            safe_points={(30.0, 27.0): False},
            visible_points={(30.0, 26.0): False},
            pathable_points={(31.0, 27.0): False},
            buildable_points={(30.0, 28.0): False},
            can_place_points={(29.0, 27.0): False},
        )
        adapter = make_adapter(bot)

        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_natural",
                    metadata={
                        "source_structure": "Supply Depot",
                        "placement_policy": {
                            "anchor": "natural expansion",
                            "anchor_target": "self_natural",
                            "spatial_relation": "near",
                        },
                    },
                )
            )
        )

        self.assertTrue(result)
        assert_build_calls_equal(
            self,
            [("TYPE:SUPPLYDEPOT", MapPoint(31.0, 26.0))],
            bot.build_calls,
        )
        self.assertGreaterEqual(len(bot.safety_checks), 6)
        self.assertGreaterEqual(len(bot.visibility_checks), 5)
        self.assertGreaterEqual(len(bot.pathing_checks), 4)
        self.assertGreaterEqual(len(bot.placement_grid_checks), 3)
        self.assertGreaterEqual(len(bot.can_place_calls), 2)

    def test_build_search_refuses_when_no_safe_buildable_tile_exists(self) -> None:
        blocked = {
            (30.0, 29.0): False,
            (30.0, 28.0): False,
            (31.0, 29.0): False,
            (30.0, 30.0): False,
            (29.0, 29.0): False,
            (31.0, 28.0): False,
            (31.0, 30.0): False,
            (29.0, 30.0): False,
            (29.0, 28.0): False,
        }
        bot = FakeBotAI(workers=[FakeUnit("SCV")], buildable_points=blocked)
        adapter = make_adapter(bot)

        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_natural",
                    metadata={
                        "source_structure": "Supply Depot",
                        "placement_policy": {
                            "anchor": "natural expansion",
                            "anchor_target": "self_natural",
                            "spatial_relation": "near",
                            "search_radius": 1,
                        },
                    },
                )
            )
        )

        self.assertIsInstance(result, SC2ActionReport)
        self.assertFalse(result.applied)
        self.assertFalse(result)
        self.assertIn("no_safe_placement", result.detail)
        self.assertIn("not_buildable", result.detail)
        self.assertEqual(result.detail, result.audit["failure_reason"])
        self.assertEqual("no_safe_placement", result.audit["failure_reason_code"])
        self.assertEqual(
            {
                "anchor": "natural expansion",
                "anchor_target": "self_natural",
                "spatial_relation": "near",
                "search_radius": 1,
            },
            result.audit["placement_policy"],
        )
        self.assertEqual(
            "placement_policy.anchor_target",
            result.audit["anchor_source"]["source"],
        )
        self.assertEqual(
            "self_natural",
            result.audit["resolved_target_policy"]["anchor_target"],
        )
        search_result = result.audit["search_result"]
        self.assertEqual("no_match", search_result["status"])
        self.assertEqual("no_safe_placement", search_result["reason_code"])
        self.assertIsNone(search_result["selected_tile"])
        self.assertIsNone(search_result["selected_result"])
        self.assertIn("no_safe_placement", search_result["no_match"]["reason"])
        self.assertEqual(
            "no_safe_placement",
            search_result["no_match"]["reason_code"],
        )
        self.assertEqual(1.0, search_result["search_radius"])
        self.assertEqual(1.0, search_result["no_match"]["search_radius"])
        self.assertGreater(search_result["rejected_count"], 0)
        self.assertEqual(
            search_result["rejected_count"],
            search_result["no_match"]["rejected_count"],
        )
        self.assertEqual([], bot.build_calls)

    def test_supply_depot_prefers_safe_nearby_space_away_from_townhall(self) -> None:
        command_center = FakeUnit("Command Center", 20.0, 10.2)
        bot = FakeBotAI(workers=[FakeUnit("SCV")], structures=[command_center])
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
            [("TYPE:SUPPLYDEPOT", MapPoint(21.0, 12.0))],
            bot.build_calls,
        )

    def test_build_placement_policy_refuses_unresolved_korean_anchor(self) -> None:
        bot = FakeBotAI(workers=[FakeUnit("SCV")])
        adapter = make_adapter(bot)

        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "SUPPLYDEPOT",
                    target="self_ramp",
                    metadata={
                        "source_structure": "Supply Depot",
                        "placement_policy": {
                            "anchor": "섬 멀티",
                            "spatial_relation": "near",
                        },
                    },
                )
            )
        )

        self.assertIsInstance(result, SC2ActionReport)
        self.assertFalse(result.applied)
        self.assertFalse(result)
        self.assertEqual(1, result.requested_count)
        self.assertEqual(0, result.issued_count)
        self.assertIn("unresolved_anchor", result.detail)
        self.assertIn("Unsupported map anchor", result.detail)
        self.assertIn("섬 멀티", result.detail)
        self.assertEqual("unsupported_map_anchor", result.audit["failure_reason_code"])
        self.assertEqual(
            "unsupported_map_anchor",
            result.audit["anchor_source"]["resolver_reason_code"],
        )
        self.assertEqual([], bot.build_calls)

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

    def test_command_center_refuses_non_expansion_location_before_expand_now(self) -> None:
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
                    target="self_ramp",
                    metadata={"source_structure": "Command Center"},
                )
            )
        )

        self.assertIsInstance(result, SC2ActionReport)
        self.assertFalse(result)
        self.assertIn("invalid_command_center_location", result.detail)
        self.assertIn("not_expansion_location", result.detail)
        self.assertEqual([], expand_now_calls)
        self.assertEqual([], bot.build_calls)

    def test_command_center_refuses_occupied_expansion(self) -> None:
        bot = FakeBotAI(structures=[FakeUnit("Command Center", 30.0, 30.0)])
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

        self.assertIsInstance(result, SC2ActionReport)
        self.assertFalse(result)
        self.assertIn("expansion_occupied_by_own_townhall", result.detail)
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

    def test_refinery_refuses_non_geyser_anchor_instead_of_nearest_geyser(self) -> None:
        geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
        bot = FakeBotAI(workers=[FakeUnit("SCV", 10, 10)], geysers=[geyser])
        adapter = make_adapter(bot)
        result = run(
            adapter.build_structure(
                action(
                    SC2ActionType.BUILD_STRUCTURE,
                    "REFINERY",
                    target="self_ramp",
                    metadata={"source_structure": "Refinery"},
                )
            )
        )

        self.assertIsInstance(result, SC2ActionReport)
        self.assertFalse(result)
        self.assertIn("invalid_refinery_target", result.detail)
        self.assertIn("target_is_not_geyser", result.detail)
        self.assertEqual([], bot.build_calls)

    def test_refinery_allows_second_main_gas_farther_than_tight_snap_radius(self) -> None:
        taken_geyser = FakeUnit("VespeneGeyser", 12.0, 10.0)
        free_geyser = FakeUnit("VespeneGeyser", 18.0, 13.0)
        existing_refinery = FakeUnit("Refinery", 12.0, 10.0)
        bot = FakeBotAI(
            workers=[FakeUnit("SCV", 10, 10)],
            geysers=[taken_geyser, free_geyser],
            structures=[existing_refinery],
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

        self.assertTrue(result)
        self.assertEqual([("TYPE:REFINERY", free_geyser)], bot.build_calls)

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

    def test_lazy_map_resolver_uses_runtime_catalog_lookup(self) -> None:
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
        self.assertIsInstance(adapter.map_resolver, SC2RuntimeMapResolver)
        assert_order_points_equal(
            self,
            [("attack", marine, MapPoint(90.0, 90.0))],
            bot.issued,
        )

    def test_lazy_runtime_map_resolver_does_not_reuse_stale_coordinates(self) -> None:
        marine = FakeUnit("Marine", 12, 12)
        bot = FakeBotAI(workers=[FakeUnit("SCV")], units=[marine])
        adapter = make_adapter(bot, map_resolver=None)

        first = run(
            adapter.attack_move(
                action(SC2ActionType.ATTACK_MOVE, "MARINE", target="enemy_main")
            )
        )
        bot.enemy_start_locations = [FakePoint(70.0, 75.0)]
        second = run(
            adapter.attack_move(
                action(SC2ActionType.ATTACK_MOVE, "MARINE", target="enemy_main")
            )
        )

        self.assertTrue(first)
        self.assertTrue(second)
        assert_order_points_equal(
            self,
            [
                ("attack", marine, MapPoint(90.0, 90.0)),
                ("attack", marine, MapPoint(70.0, 75.0)),
            ],
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
