import json
import math
import unittest

from starcraft_commander.map_resolver import (
    SC2_EXTRA_SEMANTIC_TARGETS,
    SC2_MINERAL_LINE_RADIUS,
    SC2_SEMANTIC_TARGETS,
    SC2_SUPPORTED_SEMANTIC_TARGETS,
    MapPoint,
    MapTargetResolution,
    SC2MapResolver,
    SC2MapResolverInterface,
)


class FakePoint:
    """Point2-like fake exposing only .x/.y duck-typed attributes."""

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class FakeUnit:
    """Unit-like fake exposing coordinates only via .position."""

    def __init__(self, x: float, y: float) -> None:
        self.position = FakePoint(x, y)


class FakeRamp:
    def __init__(self, top_center: object = None, barracks: object = None) -> None:
        if top_center is not None:
            self.top_center = top_center
        if barracks is not None:
            self.barracks_correct_placement = barracks


class FakeGameInfo:
    def __init__(self, map_ramps: list) -> None:
        self.map_ramps = map_ramps


class FakeBot:
    """Minimal BotAI-like fake with a fixed two-player map layout."""

    def __init__(self) -> None:
        self.start_location = FakePoint(30.0, 30.0)
        self.enemy_start_locations = [FakePoint(170.0, 170.0)]
        self.main_base_ramp = FakeRamp(top_center=FakePoint(38.0, 36.0))
        self.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(45.0, 55.0),
            FakePoint(60.0, 90.0),
            FakePoint(140.0, 110.0),
            FakePoint(155.0, 145.0),
            FakePoint(170.0, 170.0),
        ]
        self.game_info = FakeGameInfo(
            [
                FakeRamp(top_center=FakePoint(38.0, 36.0)),
                FakeRamp(top_center=FakePoint(162.0, 164.0)),
            ]
        )
        self.mineral_field = [
            FakeUnit(24.0, 28.0),
            FakeUnit(24.0, 32.0),
            FakeUnit(26.0, 24.0),
            FakeUnit(176.0, 172.0),
            FakeUnit(176.0, 168.0),
            FakeUnit(174.0, 176.0),
            FakeUnit(100.0, 100.0),
        ]
        self.vespene_geyser = [
            FakeUnit(39.0, 21.0),
            FakeUnit(161.0, 179.0),
        ]


EXPECTED_FULL_MAP_TARGETS = {
    "self_main": (30.0, 30.0),
    "self_ramp": (38.0, 36.0),
    "self_natural": (45.0, 55.0),
    "enemy_main": (170.0, 170.0),
    "enemy_ramp": (162.0, 164.0),
    "enemy_natural": (155.0, 145.0),
    "enemy_mineral_line": (526.0 / 3.0, 172.0),
    "self_mineral_line": (74.0 / 3.0, 28.0),
    "self_geyser": (39.0, 21.0),
}


class SemanticTargetVocabularyTest(unittest.TestCase):
    def test_handoff_targets_are_the_seven_step_four_names(self) -> None:
        self.assertEqual(
            (
                "self_main",
                "self_ramp",
                "self_natural",
                "enemy_main",
                "enemy_ramp",
                "enemy_natural",
                "enemy_mineral_line",
            ),
            SC2_SEMANTIC_TARGETS,
        )

    def test_supported_vocabulary_adds_best_effort_extras(self) -> None:
        self.assertEqual(("self_mineral_line", "self_geyser"), SC2_EXTRA_SEMANTIC_TARGETS)
        self.assertEqual(
            SC2_SEMANTIC_TARGETS + SC2_EXTRA_SEMANTIC_TARGETS,
            SC2_SUPPORTED_SEMANTIC_TARGETS,
        )
        self.assertEqual(9, len(SC2_SUPPORTED_SEMANTIC_TARGETS))

    def test_mineral_line_radius_is_about_ten(self) -> None:
        self.assertAlmostEqual(10.0, SC2_MINERAL_LINE_RADIUS)


class MapPointTest(unittest.TestCase):
    def test_to_tuple_and_to_dict_are_json_ready(self) -> None:
        point = MapPoint(3, 4.5)
        self.assertEqual((3.0, 4.5), point.to_tuple())
        self.assertEqual({"x": 3.0, "y": 4.5}, point.to_dict())
        self.assertEqual({"x": 3.0, "y": 4.5}, json.loads(json.dumps(point.to_dict())))
        self.assertIsInstance(point.x, float)
        self.assertIsInstance(point.y, float)

    def test_distance_to_is_euclidean(self) -> None:
        self.assertAlmostEqual(5.0, MapPoint(0, 0).distance_to(MapPoint(3, 4)))

    def test_rejects_non_real_or_non_finite_coordinates(self) -> None:
        for label, bad_kwargs, error in (
            ("string x", {"x": "3", "y": 4}, TypeError),
            ("bool y", {"x": 3, "y": True}, TypeError),
            ("nan x", {"x": math.nan, "y": 0}, ValueError),
            ("inf y", {"x": 0, "y": math.inf}, ValueError),
        ):
            with self.subTest(label=label):
                with self.assertRaises(error):
                    MapPoint(**bad_kwargs)


class MapTargetResolutionTest(unittest.TestCase):
    def test_available_resolution_to_dict_shape(self) -> None:
        resolution = MapTargetResolution(
            target="self_main",
            available=True,
            position=MapPoint(30.0, 30.0),
        )
        self.assertEqual(
            {
                "target": "self_main",
                "available": True,
                "position": {"x": 30.0, "y": 30.0},
                "reason": "",
                "alternatives": [],
            },
            json.loads(json.dumps(resolution.to_dict())),
        )

    def test_unavailable_resolution_to_dict_shape(self) -> None:
        resolution = MapTargetResolution(
            target="enemy_ramp",
            available=False,
            position=None,
            reason="ramp data missing",
            alternatives=("self_main", "enemy_main"),
        )
        self.assertEqual(
            {
                "target": "enemy_ramp",
                "available": False,
                "position": None,
                "reason": "ramp data missing",
                "alternatives": ["self_main", "enemy_main"],
            },
            json.loads(json.dumps(resolution.to_dict())),
        )

    def test_invariants_reject_inconsistent_resolutions(self) -> None:
        point = MapPoint(1.0, 2.0)
        for label, kwargs in (
            ("empty target", {"target": " ", "available": False, "position": None, "reason": "x"}),
            ("available without position", {"target": "t", "available": True, "position": None}),
            (
                "available with reason",
                {"target": "t", "available": True, "position": point, "reason": "why"},
            ),
            (
                "available with alternatives",
                {
                    "target": "t",
                    "available": True,
                    "position": point,
                    "alternatives": ("self_main",),
                },
            ),
            (
                "unavailable with position",
                {"target": "t", "available": False, "position": point, "reason": "why"},
            ),
            ("unavailable without reason", {"target": "t", "available": False, "position": None}),
        ):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    MapTargetResolution(**kwargs)


class SC2MapResolverFromBotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.resolver = SC2MapResolver.from_bot(self.bot)

    def test_resolver_satisfies_runtime_checkable_interface(self) -> None:
        self.assertIsInstance(self.resolver, SC2MapResolverInterface)

    def test_every_semantic_target_resolves_to_expected_coordinates(self) -> None:
        self.assertEqual(
            set(SC2_SUPPORTED_SEMANTIC_TARGETS),
            set(EXPECTED_FULL_MAP_TARGETS),
        )
        for target, (expected_x, expected_y) in EXPECTED_FULL_MAP_TARGETS.items():
            with self.subTest(target=target):
                resolution = self.resolver.resolve(target)
                self.assertTrue(resolution.available)
                self.assertEqual(target, resolution.target)
                self.assertEqual("", resolution.reason)
                self.assertEqual((), resolution.alternatives)
                assert resolution.position is not None
                self.assertAlmostEqual(expected_x, resolution.position.x)
                self.assertAlmostEqual(expected_y, resolution.position.y)

    def test_resolve_point_returns_same_coordinates(self) -> None:
        for target, (expected_x, expected_y) in EXPECTED_FULL_MAP_TARGETS.items():
            with self.subTest(target=target):
                point = self.resolver.resolve_point(target)
                assert point is not None
                self.assertAlmostEqual(expected_x, point.x)
                self.assertAlmostEqual(expected_y, point.y)

    def test_natural_expansions_are_not_the_mains(self) -> None:
        self_natural = self.resolver.resolve_point("self_natural")
        enemy_natural = self.resolver.resolve_point("enemy_natural")
        assert self_natural is not None and enemy_natural is not None
        self.assertGreater(self_natural.distance_to(MapPoint(30.0, 30.0)), 1.0)
        self.assertGreater(enemy_natural.distance_to(MapPoint(170.0, 170.0)), 1.0)

    def test_executor_target_aliases_resolve_to_canonical_targets(self) -> None:
        for alias, canonical in (
            ("main", "self_main"),
            ("main ramp", "self_ramp"),
            ("natural expansion", "self_natural"),
            ("enemy main", "enemy_main"),
            ("enemy front", "enemy_ramp"),
            ("enemy natural", "enemy_natural"),
            ("enemy mineral line", "enemy_mineral_line"),
        ):
            with self.subTest(alias=alias):
                resolution = self.resolver.resolve(alias)
                self.assertTrue(resolution.available)
                self.assertEqual(canonical, resolution.target)

    def test_unknown_target_rejected_with_available_alternatives(self) -> None:
        for unknown in ("enemy_third", "island base", ""):
            with self.subTest(unknown=unknown):
                resolution = self.resolver.resolve(unknown)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertIn("Unsupported semantic map target", resolution.reason)
                self.assertEqual(
                    SC2_SUPPORTED_SEMANTIC_TARGETS,
                    resolution.alternatives,
                )
                self.assertIsNone(self.resolver.resolve_point(unknown))

    def test_registry_is_built_once_and_ignores_later_bot_mutation(self) -> None:
        self.bot.start_location = FakePoint(99.0, 99.0)
        self.bot.mineral_field = []
        resolution = self.resolver.resolve("self_main")
        assert resolution.position is not None
        self.assertEqual((30.0, 30.0), resolution.position.to_tuple())
        self.assertTrue(self.resolver.resolve("self_mineral_line").available)

    def test_resolver_to_dict_is_json_ready(self) -> None:
        payload = json.loads(json.dumps(self.resolver.to_dict()))
        self.assertEqual(
            list(SC2_SUPPORTED_SEMANTIC_TARGETS),
            payload["available_targets"],
        )
        self.assertEqual({"x": 30.0, "y": 30.0}, payload["positions"]["self_main"])
        self.assertEqual({}, payload["unavailable"])


class SC2MapResolverDegradationTest(unittest.TestCase):
    def test_from_bot_never_raises_on_empty_object(self) -> None:
        resolver = SC2MapResolver.from_bot(object())
        self.assertEqual((), resolver.available_targets)
        self.assertEqual(SC2_SUPPORTED_SEMANTIC_TARGETS, resolver.unavailable_targets)
        for target in SC2_SUPPORTED_SEMANTIC_TARGETS:
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertNotEqual("", resolution.reason.strip())
                self.assertEqual((), resolution.alternatives)

    def test_missing_main_base_ramp_degrades_only_self_ramp(self) -> None:
        bot = FakeBot()
        del bot.main_base_ramp
        resolver = SC2MapResolver.from_bot(bot)
        resolution = resolver.resolve("self_ramp")
        self.assertFalse(resolution.available)
        self.assertIn("main_base_ramp", resolution.reason)
        self.assertIn("self_main", resolution.alternatives)
        self.assertNotIn("self_ramp", resolution.alternatives)
        self.assertTrue(resolver.resolve("enemy_ramp").available)

    def test_self_ramp_falls_back_to_barracks_correct_placement(self) -> None:
        bot = FakeBot()
        bot.main_base_ramp = FakeRamp(barracks=FakePoint(40.0, 34.0))
        resolver = SC2MapResolver.from_bot(bot)
        point = resolver.resolve_point("self_ramp")
        assert point is not None
        self.assertEqual((40.0, 34.0), point.to_tuple())

    def test_raising_bot_property_is_treated_as_missing(self) -> None:
        class ExplosiveRampBot:
            def __init__(self) -> None:
                base = FakeBot()
                self.start_location = base.start_location
                self.enemy_start_locations = base.enemy_start_locations
                self.expansion_locations_list = base.expansion_locations_list
                self.game_info = base.game_info
                self.mineral_field = base.mineral_field
                self.vespene_geyser = base.vespene_geyser

            @property
            def main_base_ramp(self):
                raise RuntimeError("python-sc2 ramp derivation failed")

        resolver = SC2MapResolver.from_bot(ExplosiveRampBot())
        resolution = resolver.resolve("self_ramp")
        self.assertFalse(resolution.available)
        self.assertIn("main_base_ramp", resolution.reason)

    def test_missing_enemy_start_degrades_enemy_targets(self) -> None:
        bot = FakeBot()
        bot.enemy_start_locations = []
        resolver = SC2MapResolver.from_bot(bot)
        for target in ("enemy_main", "enemy_ramp", "enemy_natural", "enemy_mineral_line"):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertNotEqual("", resolution.reason.strip())
        self.assertTrue(resolver.resolve("self_main").available)
        self.assertTrue(resolver.resolve("self_natural").available)

    def test_mineral_line_is_unavailable_without_nearby_minerals(self) -> None:
        bot = FakeBot()
        bot.mineral_field = [FakeUnit(100.0, 100.0)]
        resolver = SC2MapResolver.from_bot(bot)
        for target in ("self_mineral_line", "enemy_mineral_line"):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertIn("mineral_field", resolution.reason)

    def test_alternatives_list_only_currently_available_targets(self) -> None:
        bot = FakeBot()
        bot.mineral_field = []
        bot.vespene_geyser = []
        resolver = SC2MapResolver.from_bot(bot)
        resolution = resolver.resolve("enemy_mineral_line")
        self.assertFalse(resolution.available)
        self.assertEqual(
            (
                "self_main",
                "self_ramp",
                "self_natural",
                "enemy_main",
                "enemy_ramp",
                "enemy_natural",
            ),
            resolution.alternatives,
        )


class SC2MapResolverConstructionTest(unittest.TestCase):
    def test_unlisted_supported_targets_become_unavailable_entries(self) -> None:
        resolver = SC2MapResolver(positions={"self_main": MapPoint(1.0, 2.0)})
        self.assertEqual(("self_main",), resolver.available_targets)
        resolution = resolver.resolve("enemy_ramp")
        self.assertFalse(resolution.available)
        self.assertNotEqual("", resolution.reason.strip())
        self.assertEqual(("self_main",), resolution.alternatives)

    def test_point_like_positions_are_coerced_to_map_points(self) -> None:
        resolver = SC2MapResolver(positions={"self_main": FakePoint(5.0, 6.0)})
        point = resolver.resolve_point("self_main")
        self.assertIsInstance(point, MapPoint)
        assert point is not None
        self.assertEqual((5.0, 6.0), point.to_tuple())

    def test_rejects_invalid_registries(self) -> None:
        for label, kwargs, error in (
            (
                "unsupported position key",
                {"positions": {"enemy_third": MapPoint(1.0, 1.0)}},
                ValueError,
            ),
            (
                "unsupported reason key",
                {"unavailable_reasons": {"island": "no"}},
                ValueError,
            ),
            (
                "overlapping availability",
                {
                    "positions": {"self_main": MapPoint(1.0, 1.0)},
                    "unavailable_reasons": {"self_main": "missing"},
                },
                ValueError,
            ),
            (
                "blank reason",
                {"unavailable_reasons": {"self_main": "  "}},
                ValueError,
            ),
            (
                "non-point position",
                {"positions": {"self_main": "not-a-point"}},
                TypeError,
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(error):
                    SC2MapResolver(**kwargs)


if __name__ == "__main__":
    unittest.main()
