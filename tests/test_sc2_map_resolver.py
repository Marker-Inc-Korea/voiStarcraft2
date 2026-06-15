import json
import math
import unittest

import starcraft_commander as package_exports
from starcraft_commander.map_resolver import (
    SC2_CANONICAL_TARGET_ALIASES,
    SC2_BASE_CLUSTER_RESOURCE_RADIUS,
    SC2_EXTRA_SEMANTIC_TARGETS,
    SC2_GEOMETRY_VISIBILITY_VALUES,
    SC2_MINERAL_LINE_RADIUS,
    SC2_NEAR_PLACEMENT_RADIUS,
    SC2_SEMANTIC_TARGETS,
    SC2_SUPPORTED_SEMANTIC_TARGETS,
    MapAnchorPositionResolution,
    MapBaseCluster,
    MapGeometryInference,
    MapGeometryObservation,
    MapPoint,
    MapTargetResolution,
    SC2MapResolver,
    SC2MapResolverInterface,
    SC2RuntimeMapResolver,
    SemanticTargetCatalogEntry,
)


class FakePoint:
    """Point2-like fake exposing only .x/.y duck-typed attributes."""

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class FakeTypeId:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeUnit:
    """Unit-like fake exposing coordinates only via .position."""

    def __init__(
        self,
        x: float,
        y: float,
        *,
        name: str | None = None,
        type_id_name: str | None = None,
    ) -> None:
        self.position = FakePoint(x, y)
        if name is not None:
            self.name = name
        if type_id_name is not None:
            self.type_id = FakeTypeId(type_id_name)


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
        self.enemy_structures = []


EXPECTED_FULL_MAP_TARGETS = {
    "self_main": (30.0, 30.0),
    "self_ramp": (38.0, 36.0),
    "self_natural": (45.0, 55.0),
    "enemy_main": (170.0, 170.0),
    "enemy_ramp": (162.0, 164.0),
    "enemy_front": (162.0, 164.0),
    "enemy_natural": (155.0, 145.0),
    "enemy_mineral_line": (526.0 / 3.0, 172.0),
    "self_choke": (38.0, 36.0),
    "self_third": (60.0, 90.0),
    "self_mineral_line": (74.0 / 3.0, 28.0),
    "self_geyser": (39.0, 21.0),
    "enemy_choke": (162.0, 164.0),
    "enemy_third": (140.0, 110.0),
}


class SemanticTargetVocabularyTest(unittest.TestCase):
    def test_core_semantic_targets_include_enemy_front_access(self) -> None:
        self.assertEqual(
            (
                "self_main",
                "self_ramp",
                "self_natural",
                "enemy_main",
                "enemy_ramp",
                "enemy_front",
                "enemy_natural",
                "enemy_mineral_line",
            ),
            SC2_SEMANTIC_TARGETS,
        )

    def test_supported_vocabulary_adds_best_effort_extras(self) -> None:
        self.assertEqual(
            (
                "self_choke",
                "self_third",
                "self_mineral_line",
                "self_geyser",
                "enemy_choke",
                "enemy_third",
                "scout_location",
                "last_seen_enemy_area",
            ),
            SC2_EXTRA_SEMANTIC_TARGETS,
        )
        self.assertEqual(
            SC2_SEMANTIC_TARGETS + SC2_EXTRA_SEMANTIC_TARGETS,
            SC2_SUPPORTED_SEMANTIC_TARGETS,
        )
        self.assertEqual(16, len(SC2_SUPPORTED_SEMANTIC_TARGETS))

    def test_mineral_line_radius_is_about_ten(self) -> None:
        self.assertAlmostEqual(10.0, SC2_MINERAL_LINE_RADIUS)

    def test_base_cluster_resource_radius_covers_main_geyser_spacing(self) -> None:
        self.assertAlmostEqual(15.0, SC2_BASE_CLUSTER_RESOURCE_RADIUS)

    def test_near_placement_radius_is_bounded(self) -> None:
        self.assertAlmostEqual(6.0, SC2_NEAR_PLACEMENT_RADIUS)

    def test_sub_ac_5_4_1_aliases_are_in_canonical_catalog(self) -> None:
        expected_aliases = {
            "self_main": {"self_main", "main", "base", "본진", "우리 본진"},
            "self_natural": {"self_natural", "natural", "앞마당", "우리 앞마당"},
            "self_ramp": {"self_ramp", "main_ramp", "본진 입구"},
            "self_choke": {"self_choke", "natural choke", "초크"},
            "self_third": {"self_third", "third base", "삼룡이"},
            "self_mineral_line": {
                "self_mineral_line",
                "main_mineral_line",
                "본진 미네랄 라인",
            },
            "self_geyser": {"self_geyser", "main_geyser", "본진 가스"},
            "enemy_main": {"enemy_main", "enemy base", "enemy_base", "적 본진"},
            "enemy_ramp": {"enemy_ramp", "enemy ramp", "적 램프"},
            "enemy_choke": {"enemy_choke", "enemy choke", "적 초크"},
            "enemy_front": {"enemy_front", "enemy front", "적 입구"},
            "enemy_natural": {"enemy_natural", "enemy natural"},
            "enemy_third": {"enemy_third", "enemy third", "적 세번째 멀티"},
            "enemy_mineral_line": {"enemy_mineral_line", "enemy mineral line"},
            "scout_location": {"scout_location", "scout location", "정찰 위치"},
            "last_seen_enemy_area": {
                "last_seen_enemy_area",
                "last seen enemy area",
                "마지막 적 위치",
            },
        }
        for target, aliases in expected_aliases.items():
            with self.subTest(target=target):
                self.assertIn(target, SC2_SUPPORTED_SEMANTIC_TARGETS)
                self.assertLessEqual(
                    aliases,
                    set(SC2_CANONICAL_TARGET_ALIASES[target]),
                )


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
                "reason_code": "",
                "source": "",
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
                "reason_code": "cannot_derive_enemy_ramp",
                "source": "",
                "alternatives": ["self_main", "enemy_main"],
            },
            json.loads(json.dumps(resolution.to_dict())),
        )

    def test_resolver_resolution_to_dict_carries_auditable_source(self) -> None:
        resolver = SC2MapResolver(
            positions={"self_main": MapPoint(30.0, 30.0)},
            sources={"self_main": "unit-test observations"},
        )

        payload = resolver.resolve("self_main").to_dict()

        self.assertEqual("unit-test observations", payload["source"])

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


class MapAnchorPositionResolutionTest(unittest.TestCase):
    def test_package_exports_anchor_resolution_surface(self) -> None:
        self.assertIs(
            MapAnchorPositionResolution,
            package_exports.MapAnchorPositionResolution,
        )

    def test_available_anchor_resolution_to_dict_shape(self) -> None:
        resolution = MapAnchorPositionResolution(
            anchor="본진",
            available=True,
            position=MapPoint(30.0, 30.0),
            source="python-sc2 observations",
            target="self_main",
        )

        self.assertEqual(
            {
                "anchor": "본진",
                "available": True,
                "position": {"x": 30.0, "y": 30.0},
                "reason": "",
                "reason_code": "",
                "source": "python-sc2 observations",
                "target": "self_main",
                "alternatives": [],
            },
            json.loads(json.dumps(resolution.to_dict())),
        )

    def test_unavailable_anchor_resolution_to_dict_shape(self) -> None:
        resolution = MapAnchorPositionResolution(
            anchor="섬 멀티",
            available=False,
            position=None,
            reason="unsupported anchor",
            alternatives=("self_main", "self_ramp"),
        )

        self.assertEqual(
            {
                "anchor": "섬 멀티",
                "available": False,
                "position": None,
                "reason": "unsupported anchor",
                "reason_code": "anchor_unavailable",
                "source": "",
                "target": "",
                "alternatives": ["self_main", "self_ramp"],
            },
            json.loads(json.dumps(resolution.to_dict())),
        )

    def test_invariants_reject_inconsistent_anchor_resolutions(self) -> None:
        point = MapPoint(1.0, 2.0)
        for label, kwargs in (
            (
                "available without position",
                {"anchor": "a", "available": True, "position": None, "source": "s"},
            ),
            (
                "available with reason",
                {
                    "anchor": "a",
                    "available": True,
                    "position": point,
                    "reason": "why",
                    "source": "s",
                },
            ),
            (
                "available without source",
                {"anchor": "a", "available": True, "position": point},
            ),
            (
                "unavailable with position",
                {
                    "anchor": "a",
                    "available": False,
                    "position": point,
                    "reason": "why",
                },
            ),
            (
                "unavailable without reason",
                {"anchor": "a", "available": False, "position": None},
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    MapAnchorPositionResolution(**kwargs)


class SemanticTargetCatalogEntryTest(unittest.TestCase):
    def test_to_dict_is_json_ready(self) -> None:
        entry = SemanticTargetCatalogEntry(
            target="self_main",
            aliases=("main", "base"),
            available=True,
            position=MapPoint(30.0, 30.0),
        )
        self.assertEqual(
            {
                "target": "self_main",
                "aliases": ["main", "base"],
                "available": True,
                "position": {"x": 30.0, "y": 30.0},
                "failure_reason": "",
                "failure_reason_code": "",
                "source": "python-sc2 observations",
            },
            json.loads(json.dumps(entry.to_dict())),
        )

    def test_catalog_entry_invariants_reject_inconsistent_entries(self) -> None:
        point = MapPoint(1.0, 2.0)
        for label, kwargs in (
            ("unsupported target", {"target": "island_base", "failure_reason": "x"}),
            ("available without position", {"target": "self_main", "available": True}),
            (
                "available with reason",
                {
                    "target": "self_main",
                    "available": True,
                    "position": point,
                    "failure_reason": "why",
                },
            ),
            (
                "unavailable with position",
                {
                    "target": "self_main",
                    "available": False,
                    "position": point,
                    "failure_reason": "why",
                },
            ),
            ("unavailable without reason", {"target": "self_main"}),
            (
                "blank source",
                {"target": "self_main", "failure_reason": "why", "source": " "},
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    SemanticTargetCatalogEntry(**kwargs)


class MapGeometryModelTest(unittest.TestCase):
    def test_observation_to_dict_carries_confidence_and_visibility(self) -> None:
        observation = MapGeometryObservation(
            kind="mineral_patch",
            key="mineral_patch_1",
            position=MapPoint(24.0, 28.0),
            confidence=1,
            visibility="visible",
            source="python-sc2 observations",
            metadata={"resource": "mineral"},
        )

        self.assertEqual(
            {
                "kind": "mineral_patch",
                "key": "mineral_patch_1",
                "position": {"x": 24.0, "y": 28.0},
                "confidence": 1.0,
                "visibility": "visible",
                "source": "python-sc2 observations",
                "metadata": {"resource": "mineral"},
            },
            json.loads(json.dumps(observation.to_dict())),
        )

    def test_observation_invariants_reject_unsafe_metadata(self) -> None:
        point = MapPoint(1.0, 2.0)
        for label, kwargs, error in (
            (
                "unsupported kind",
                {
                    "kind": "watchtower",
                    "key": "x",
                    "position": point,
                    "confidence": 1.0,
                    "visibility": "visible",
                    "source": "s",
                },
                ValueError,
            ),
            (
                "blank key",
                {
                    "kind": "ramp",
                    "key": " ",
                    "position": point,
                    "confidence": 1.0,
                    "visibility": "visible",
                    "source": "s",
                },
                ValueError,
            ),
            (
                "confidence above one",
                {
                    "kind": "ramp",
                    "key": "x",
                    "position": point,
                    "confidence": 1.1,
                    "visibility": "visible",
                    "source": "s",
                },
                ValueError,
            ),
            (
                "unsupported visibility",
                {
                    "kind": "ramp",
                    "key": "x",
                    "position": point,
                    "confidence": 1.0,
                    "visibility": "secret",
                    "source": "s",
                },
                ValueError,
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(error):
                    MapGeometryObservation(**kwargs)

    def test_base_cluster_and_inference_are_json_ready(self) -> None:
        mineral = MapGeometryObservation(
            kind="mineral_patch",
            key="mineral_patch_1",
            position=MapPoint(24.0, 28.0),
            confidence=1.0,
            visibility="visible",
            source="python-sc2 observations",
        )
        geyser = MapGeometryObservation(
            kind="geyser",
            key="geyser_1",
            position=MapPoint(39.0, 21.0),
            confidence=1.0,
            visibility="visible",
            source="python-sc2 observations",
        )
        ramp = MapGeometryObservation(
            kind="ramp",
            key="self_ramp",
            position=MapPoint(38.0, 36.0),
            confidence=0.95,
            visibility="visible",
            source="python-sc2 observations",
        )
        cluster = MapBaseCluster(
            key="self_main",
            anchor=MapPoint(30.0, 30.0),
            confidence=1.0,
            visibility="visible",
            source="python-sc2 observations",
            mineral_patches=(mineral,),
            geysers=(geyser,),
            ramp=ramp,
            metadata={"owner": "self"},
        )
        inference = MapGeometryInference(
            base_clusters=(cluster,),
            player_main_base=cluster,
            ramps=(ramp,),
            mineral_patches=(mineral,),
            geysers=(geyser,),
        )

        payload = json.loads(json.dumps(inference.to_dict()))
        self.assertEqual("self_main", payload["base_clusters"][0]["key"])
        self.assertEqual("visible", payload["base_clusters"][0]["visibility"])
        self.assertEqual(1.0, payload["base_clusters"][0]["confidence"])
        self.assertEqual("self_ramp", payload["base_clusters"][0]["ramp"]["key"])
        self.assertEqual("self_main", payload["player_main_base"]["key"])
        self.assertEqual({"x": 30.0, "y": 30.0}, payload["player_main_base"]["anchor"])


class SC2MapResolverFromBotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.resolver = SC2MapResolver.from_bot(self.bot)

    def test_resolver_satisfies_runtime_checkable_interface(self) -> None:
        self.assertIsInstance(self.resolver, SC2MapResolverInterface)

    def test_every_semantic_target_resolves_to_expected_coordinates(self) -> None:
        self.assertEqual(
            set(self.resolver.available_targets),
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

    def test_unobserved_camera_memory_targets_are_unavailable_with_reasons(self) -> None:
        for target, reason_fragment in (
            ("scout_location", "scout location observation"),
            ("last_seen_enemy_area", "last-seen enemy observation"),
        ):
            with self.subTest(target=target):
                resolution = self.resolver.resolve(target)

                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertIn(reason_fragment, resolution.reason)
                self.assertEqual(f"cannot_derive_{target}", resolution.reason_code)
                self.assertEqual(self.resolver.available_targets, resolution.alternatives)

    def test_resolve_anchor_position_accepts_point_like_anchor(self) -> None:
        resolution = self.resolver.resolve_anchor_position(FakePoint(12.0, 34.0))

        self.assertTrue(resolution.available)
        self.assertEqual("point(12, 34)", resolution.anchor)
        self.assertEqual("explicit point-like anchor", resolution.source)
        assert resolution.position is not None
        self.assertEqual((12.0, 34.0), resolution.position.to_tuple())

    def test_resolve_anchor_position_accepts_semantic_aliases(self) -> None:
        for anchor, target, expected in (
            ("본진", "self_main", (30.0, 30.0)),
            ("본진 입구", "self_ramp", (38.0, 36.0)),
            ("본진 미네랄 라인", "self_mineral_line", (74.0 / 3.0, 28.0)),
            ("앞마당", "self_natural", (45.0, 55.0)),
        ):
            with self.subTest(anchor=anchor):
                resolution = self.resolver.resolve_anchor_position(anchor)

                self.assertTrue(resolution.available)
                self.assertEqual(target, resolution.target)
                assert resolution.position is not None
                self.assertEqual(expected, resolution.position.to_tuple())

    def test_korean_placement_examples_resolve_targets_and_coordinates(self) -> None:
        cases = (
            (
                "본진 입구에 보급고",
                {
                    "anchor": "본진 입구",
                    "anchor_target": "self_ramp",
                    "spatial_relation": "near",
                },
                "self_ramp",
                "near",
                (38.0, 36.0),
                (38.0, 33.0),
            ),
            (
                "앞마당 근처 보급고",
                {
                    "anchor": "앞마당",
                    "anchor_target": "self_natural",
                    "spatial_relation": "근처",
                },
                "self_natural",
                "near",
                (45.0, 55.0),
                (45.0, 52.0),
            ),
            (
                "입구 쪽으로 보급고",
                {
                    "anchor": "본진 입구",
                    "anchor_target": "self_ramp",
                    "spatial_relation": "쪽으로",
                },
                "self_ramp",
                "toward",
                (38.0, 36.0),
                (34.8, 33.6),
            ),
            (
                "미네랄에서 떨어지게 보급고",
                {
                    "anchor": "본진 미네랄 라인",
                    "anchor_target": "self_mineral_line",
                    "spatial_relation": "떨어지게",
                },
                "self_mineral_line",
                "away_from",
                (74.0 / 3.0, 28.0),
                (27.4756541993738, 29.053370324765176),
            ),
            (
                "본진 가스에 정제소",
                {
                    "anchor": "본진 가스",
                    "anchor_target": "self_geyser",
                    "spatial_relation": "on",
                },
                "self_geyser",
                None,
                (39.0, 21.0),
                (39.0, 21.0),
            ),
        )

        for (
            phrase,
            policy,
            expected_target,
            expected_relation,
            expected_anchor,
            expected_position,
        ) in cases:
            with self.subTest(phrase=phrase):
                resolution = self.resolver.resolve_anchor_position(policy)

                self.assertTrue(resolution.available)
                self.assertEqual(expected_target, resolution.target)
                self.assertEqual(expected_target, resolution.anchor)
                self.assertEqual("", resolution.reason_code)
                assert resolution.position is not None
                self.assertAlmostEqual(expected_position[0], resolution.position.x)
                self.assertAlmostEqual(expected_position[1], resolution.position.y)

                payload = resolution.to_dict()
                placement_policy = payload.get("placement_policy")
                if expected_relation is None:
                    self.assertIsNone(placement_policy)
                    continue

                self.assertEqual(expected_relation, placement_policy["spatial_relation"])
                self.assertEqual(expected_target, placement_policy["anchor_target"])
                self.assertEqual(
                    {"x": expected_anchor[0], "y": expected_anchor[1]},
                    placement_policy["anchor_position"],
                )
                self.assertEqual(
                    {"x": expected_position[0], "y": expected_position[1]},
                    placement_policy["selected_tile"],
                )
                self.assertEqual(
                    placement_policy["selected_tile"],
                    placement_policy["search_result"]["selected_tile"],
                )

    def test_parsed_semantic_target_object_resolves_from_current_game_state(
        self,
    ) -> None:
        bot = FakeBot()
        resolver = SC2RuntimeMapResolver(bot)
        parsed_target = {
            "semantic_target": {
                "target_key": "self_natural",
                "source": "llm",
                "confidence": 0.72,
                "anchor_point": {"x": 999.0, "y": 999.0},
            }
        }

        initial = resolver.resolve_anchor_position(parsed_target)
        self.assertTrue(initial.available)
        self.assertEqual("self_natural", initial.target)
        assert initial.position is not None
        self.assertEqual((45.0, 55.0), initial.position.to_tuple())

        bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(52.0, 58.0),
            FakePoint(170.0, 170.0),
            FakePoint(155.0, 145.0),
        ]
        updated = resolver.resolve_anchor_position(parsed_target)
        self.assertTrue(updated.available)
        self.assertEqual("self_natural", updated.target)
        assert updated.position is not None
        self.assertEqual((52.0, 58.0), updated.position.to_tuple())

    def test_parsed_target_key_resolves_through_semantic_catalog(self) -> None:
        resolution = self.resolver.resolve(
            {
                "target_key": "enemy natural",
                "source": "llm",
                "confidence": 0.81,
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("enemy_natural", resolution.target)
        assert resolution.position is not None
        self.assertEqual((155.0, 145.0), resolution.position.to_tuple())

    def test_parsed_geometry_and_expansion_metadata_resolve_to_coordinates(
        self,
    ) -> None:
        cases = (
            ({"geometry_key": "self_start_location"}, "self_start_location", (30.0, 30.0)),
            (
                {"base_location": {"base_key": "self_natural"}},
                "self_natural",
                (45.0, 55.0),
            ),
            (
                {"expansion": {"expansion_key": "neutral_base_4"}},
                "neutral_base_4",
                (140.0, 110.0),
            ),
        )

        for parsed_anchor, expected_target, expected_position in cases:
            with self.subTest(parsed_anchor=parsed_anchor):
                resolution = self.resolver.resolve_anchor_position(parsed_anchor)

                self.assertTrue(resolution.available)
                self.assertEqual(expected_target, resolution.target)
                assert resolution.position is not None
                self.assertEqual(expected_position, resolution.position.to_tuple())

    def test_parsed_object_with_only_coordinates_keeps_explicit_point_support(
        self,
    ) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {"position": {"x": 9.0, "y": 11.0}}
        )

        self.assertTrue(resolution.available)
        self.assertEqual("point(9, 11)", resolution.anchor)
        assert resolution.position is not None
        self.assertEqual((9.0, 11.0), resolution.position.to_tuple())

    def test_resolve_anchor_position_prefers_placement_policy_anchor_target(self) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {
                "anchor": "mineral line",
                "anchor_target": "self_mineral_line",
                "spatial_relation": "away_from",
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_mineral_line", resolution.anchor)
        self.assertEqual("self_mineral_line", resolution.target)
        assert resolution.position is not None
        self.assertAlmostEqual(27.4756541993738, resolution.position.x)
        self.assertAlmostEqual(29.053370324765176, resolution.position.y)
        anchor = MapPoint(74.0 / 3.0, 28.0)
        self.assertGreater(resolution.position.distance_to(anchor), 0.0)
        self.assertLessEqual(
            resolution.position.distance_to(anchor),
            SC2_NEAR_PLACEMENT_RADIUS,
        )
        reference = MapPoint(30.0, 30.0)
        self.assertLess(
            resolution.position.distance_to(reference),
            anchor.distance_to(reference),
        )
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("away_from", policy["spatial_relation"])
        self.assertEqual("self_mineral_line", policy["anchor_target"])
        self.assertEqual(
            "python-sc2 validated base/resource geometry",
            policy["anchor_source"],
        )
        self.assertEqual(
            {"x": resolution.position.x, "y": resolution.position.y},
            policy["resolved_position"],
        )
        self.assertEqual({"x": 74.0 / 3.0, "y": 28.0}, policy["anchor_position"])
        self.assertEqual({"x": 30.0, "y": 30.0}, policy["reference_position"])
        self.assertEqual(
            {"x": resolution.position.x, "y": resolution.position.y},
            policy["selected_tile"],
        )
        search_result = policy["search_result"]
        self.assertEqual("selected", search_result["status"])
        self.assertEqual(policy["selected_tile"], search_result["selected_tile"])
        self.assertEqual(
            policy["selected_tile"],
            search_result["selected_result"]["tile"],
        )
        self.assertEqual(
            "map resolver away-from placement search",
            search_result["selected_result"]["source"],
        )
        self.assertGreater(
            search_result["selected_result"]["distance_from_anchor"],
            0.0,
        )
        self.assertIsNone(search_result["no_match"])

    def test_resolve_anchor_position_away_policy_skips_blocked_candidate(
        self,
    ) -> None:
        blocked_candidate = MapPoint(27.4756541993738, 29.053370324765176)
        resolver = SC2MapResolver(
            positions={
                "self_main": MapPoint(30.0, 30.0),
                "self_mineral_line": MapPoint(74.0 / 3.0, 28.0),
            },
            geometry=MapGeometryInference(
                ramps=(
                    MapGeometryObservation(
                        kind="ramp",
                        key="blocked_pathing_tile",
                        position=blocked_candidate,
                        confidence=1.0,
                        visibility="visible",
                        source="test pathing constraint",
                    ),
                ),
            ),
        )

        resolution = resolver.resolve_anchor_position(
            {
                "anchor": "mineral line",
                "anchor_target": "self_mineral_line",
                "spatial_relation": "away_from",
            }
        )

        self.assertTrue(resolution.available)
        assert resolution.position is not None
        self.assertNotEqual(
            blocked_candidate.to_tuple(),
            resolution.position.to_tuple(),
        )
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("away_from", policy["spatial_relation"])
        self.assertGreater(len(policy["rejection_reasons"]), 0)
        self.assertTrue(
            any(
                "overlaps observed base/resource/ramp geometry" in reason
                for reason in policy["rejection_reasons"]
            )
        )
        self.assertLessEqual(
            resolution.position.distance_to(MapPoint(74.0 / 3.0, 28.0)),
            SC2_NEAR_PLACEMENT_RADIUS,
        )

    def test_resolve_anchor_position_near_policy_selects_bounded_candidate(self) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {
                "anchor": "natural expansion",
                "anchor_target": "self_natural",
                "spatial_relation": "near",
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_natural", resolution.anchor)
        self.assertEqual("self_natural", resolution.target)
        assert resolution.position is not None
        self.assertEqual((45.0, 52.0), resolution.position.to_tuple())
        anchor = MapPoint(45.0, 55.0)
        self.assertGreater(resolution.position.distance_to(anchor), 0.0)
        self.assertLessEqual(
            resolution.position.distance_to(anchor),
            SC2_NEAR_PLACEMENT_RADIUS,
        )
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("near", policy["spatial_relation"])
        self.assertEqual({"x": 45.0, "y": 55.0}, policy["anchor_position"])
        self.assertEqual({"x": 45.0, "y": 52.0}, policy["selected_tile"])
        self.assertEqual(SC2_NEAR_PLACEMENT_RADIUS, policy["search_radius"])
        search_result = policy["search_result"]
        self.assertEqual("selected", search_result["status"])
        self.assertEqual(policy["selected_tile"], search_result["selected_tile"])
        self.assertEqual(
            policy["selected_tile"],
            search_result["selected_result"]["tile"],
        )
        self.assertEqual(
            "map resolver near placement search",
            search_result["selected_result"]["source"],
        )
        self.assertEqual(
            0,
            search_result["selected_result"]["rejected_before_selection"],
        )
        self.assertIsNone(search_result["no_match"])

    def test_resolve_anchor_position_toward_policy_uses_actor_to_anchor_direction(
        self,
    ) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {
                "anchor": "natural expansion",
                "anchor_target": "self_natural",
                "spatial_relation": "toward",
                "actor_position": {"x": 30.0, "y": 30.0},
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_natural", resolution.anchor)
        self.assertEqual("self_natural", resolution.target)
        assert resolution.position is not None
        self.assertAlmostEqual(33.08697453256516, resolution.position.x)
        self.assertAlmostEqual(35.14495755427527, resolution.position.y)
        self.assertLessEqual(
            MapPoint(30.0, 30.0).distance_to(resolution.position),
            SC2_NEAR_PLACEMENT_RADIUS,
        )
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("toward", policy["spatial_relation"])
        self.assertEqual({"x": 30.0, "y": 30.0}, policy["origin_position"])
        self.assertEqual({"x": 45.0, "y": 55.0}, policy["anchor_position"])
        self.assertEqual(
            {"x": resolution.position.x, "y": resolution.position.y},
            policy["selected_tile"],
        )

    def test_resolve_anchor_position_toward_policy_uses_current_main_fallback(
        self,
    ) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {
                "anchor": "main ramp",
                "anchor_target": "self_ramp",
                "spatial_relation": "toward",
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_ramp", resolution.target)
        assert resolution.position is not None
        self.assertEqual((34.8, 33.6), resolution.position.to_tuple())
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual({"x": 30.0, "y": 30.0}, policy["origin_position"])
        self.assertEqual([], policy["rejection_reasons"])

    def test_spatial_away_from_selection_moves_from_anchor_toward_reference(
        self,
    ) -> None:
        resolver = SC2MapResolver(
            positions={
                "self_main": MapPoint(0.0, 0.0),
                "self_mineral_line": MapPoint(10.0, 0.0),
            },
        )

        resolution = resolver.resolve_anchor_position(
            {
                "anchor": "mineral line",
                "anchor_target": "self_mineral_line",
                "spatial_relation": "떨어지게",
                "search_radius": 6.0,
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_mineral_line", resolution.target)
        assert resolution.position is not None
        self.assertEqual((7.0, 0.0), resolution.position.to_tuple())
        anchor = MapPoint(10.0, 0.0)
        reference = MapPoint(0.0, 0.0)
        self.assertLess(
            resolution.position.distance_to(reference),
            anchor.distance_to(reference),
        )
        self.assertLessEqual(
            resolution.position.distance_to(anchor),
            SC2_NEAR_PLACEMENT_RADIUS,
        )
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("away_from", policy["spatial_relation"])
        self.assertEqual({"x": 0.0, "y": 0.0}, policy["reference_position"])
        self.assertEqual({"x": 7.0, "y": 0.0}, policy["selected_tile"])

    def test_spatial_toward_selection_moves_actor_toward_anchor(self) -> None:
        resolver = SC2MapResolver(
            positions={
                "self_ramp": MapPoint(10.0, 0.0),
            },
        )

        resolution = resolver.resolve_anchor_position(
            {
                "anchor": "main ramp",
                "anchor_target": "self_ramp",
                "spatial_relation": "쪽으로",
                "actor_position": {"x": 0.0, "y": 0.0},
                "search_radius": 6.0,
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_ramp", resolution.target)
        assert resolution.position is not None
        self.assertEqual((6.0, 0.0), resolution.position.to_tuple())
        origin = MapPoint(0.0, 0.0)
        anchor = MapPoint(10.0, 0.0)
        self.assertGreater(resolution.position.distance_to(origin), 0.0)
        self.assertLess(
            resolution.position.distance_to(anchor),
            origin.distance_to(anchor),
        )
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("toward", policy["spatial_relation"])
        self.assertEqual({"x": 0.0, "y": 0.0}, policy["origin_position"])
        self.assertEqual({"x": 10.0, "y": 0.0}, policy["anchor_position"])
        self.assertEqual({"x": 6.0, "y": 0.0}, policy["selected_tile"])

    def test_spatial_near_selection_uses_bounded_offset_from_anchor(self) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {
                "anchor": "natural expansion",
                "anchor_target": "self_natural",
                "spatial_relation": "근처",
                "search_radius": 4.0,
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_natural", resolution.target)
        assert resolution.position is not None
        self.assertEqual((45.0, 52.0), resolution.position.to_tuple())
        anchor = MapPoint(45.0, 55.0)
        self.assertGreater(resolution.position.distance_to(anchor), 0.0)
        self.assertLessEqual(resolution.position.distance_to(anchor), 4.0)
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("near", policy["spatial_relation"])
        self.assertEqual({"x": 45.0, "y": 55.0}, policy["anchor_position"])
        self.assertEqual(4.0, policy["search_radius"])
        self.assertEqual({"x": 45.0, "y": 52.0}, policy["selected_tile"])

    def test_resolve_anchor_position_accepts_nested_position_mapping(self) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {"position": {"x": 9.0, "y": 11.0}}
        )

        self.assertTrue(resolution.available)
        self.assertEqual("point(9, 11)", resolution.anchor)
        assert resolution.position is not None
        self.assertEqual((9.0, 11.0), resolution.position.to_tuple())

    def test_resolve_anchor_position_accepts_base_cluster_and_geometry_keys(self) -> None:
        for anchor, expected in (
            ("enemy_natural", (155.0, 145.0)),
            ("mineral_patch_1", (24.0, 28.0)),
            ("geyser_1", (39.0, 21.0)),
        ):
            with self.subTest(anchor=anchor):
                resolution = self.resolver.resolve_anchor_position(anchor)

                self.assertTrue(resolution.available)
                assert resolution.position is not None
                self.assertEqual(expected, resolution.position.to_tuple())

    def test_resolve_anchor_position_surfaces_unavailable_target_reason(self) -> None:
        bot = FakeBot()
        bot.mineral_field = []
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve_anchor_position("self_mineral_line")

        self.assertFalse(resolution.available)
        self.assertEqual("self_mineral_line", resolution.target)
        self.assertIsNone(resolution.position)
        self.assertIn("mineral_field", resolution.reason)
        self.assertEqual("cannot_derive_self_mineral_line", resolution.reason_code)
        self.assertNotIn("self_mineral_line", resolution.alternatives)

    def test_resolve_anchor_position_rejects_unknown_anchor_with_alternatives(self) -> None:
        resolution = self.resolver.resolve_anchor_position("섬 멀티")

        self.assertFalse(resolution.available)
        self.assertIsNone(resolution.position)
        self.assertIn("Unsupported map anchor", resolution.reason)
        self.assertEqual("unsupported_map_anchor", resolution.reason_code)
        self.assertIn("self_main", resolution.alternatives)
        self.assertIn("mineral_patch_1", resolution.alternatives)

    def test_base_selection_modifiers_resolve_known_base_instances(self) -> None:
        bot = FakeBot()
        bot.structures = [
            FakeUnit(30.0, 30.0, name="CommandCenter"),
            FakeUnit(45.0, 55.0, name="CommandCenter"),
            FakeUnit(60.0, 90.0, name="CommandCenter"),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        cases = (
            ("본진 사령부", "self_main", (30.0, 30.0)),
            ("앞마당 커맨드", "self_natural", (45.0, 55.0)),
            ("third base", "self_third", (60.0, 90.0)),
            ("새로 지은 사령부", "self_newest", (60.0, 90.0)),
            ("additional base 1", "self_additional_1", (60.0, 90.0)),
        )

        for anchor, expected_target, expected_position in cases:
            with self.subTest(anchor=anchor):
                resolution = resolver.resolve(anchor)

                self.assertTrue(resolution.available)
                self.assertEqual(expected_target, resolution.target)
                assert resolution.position is not None
                self.assertEqual(expected_position, resolution.position.to_tuple())

    def test_base_selection_anchor_policy_resolves_future_selectors(self) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {
                "anchor": "third base",
                "base_selection": {
                    "selector": "third",
                    "target": "self_third",
                    "location": "third base",
                },
                "spatial_relation": "near",
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_third", resolution.anchor)
        self.assertEqual("self_third", resolution.target)
        assert resolution.position is not None
        self.assertNotEqual((60.0, 90.0), resolution.position.to_tuple())
        policy = resolution.to_dict()["placement_policy"]
        self.assertEqual("near", policy["spatial_relation"])
        self.assertEqual({"x": 60.0, "y": 90.0}, policy["anchor_position"])

    def test_selected_semantic_base_fallback_uses_current_base_metadata(self) -> None:
        resolution = self.resolver.resolve_anchor_position(
            {
                "base_selection": {"selector": "selected"},
                "selected_semantic_base": "self_natural",
            }
        )

        self.assertTrue(resolution.available)
        self.assertEqual("self_natural", resolution.target)
        assert resolution.position is not None
        self.assertEqual((45.0, 55.0), resolution.position.to_tuple())

    def test_unavailable_base_selection_reports_precise_reason(self) -> None:
        bot = FakeBot()
        bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(45.0, 55.0),
            FakePoint(170.0, 170.0),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("additional base 2")

        self.assertFalse(resolution.available)
        self.assertEqual("self_additional_2", resolution.target)
        self.assertIn("no known base instance", resolution.reason)
        self.assertEqual("no_known_base_instance", resolution.reason_code)
        self.assertIn("self_main", resolution.alternatives)
        self.assertIn("self_natural", resolution.alternatives)

    def test_natural_expansions_are_not_the_mains(self) -> None:
        self_natural = self.resolver.resolve_point("self_natural")
        enemy_natural = self.resolver.resolve_point("enemy_natural")
        assert self_natural is not None and enemy_natural is not None
        self.assertGreater(self_natural.distance_to(MapPoint(30.0, 30.0)), 1.0)
        self.assertGreater(enemy_natural.distance_to(MapPoint(170.0, 170.0)), 1.0)

    def test_executor_target_aliases_resolve_to_canonical_targets(self) -> None:
        for alias, canonical in (
            ("main", "self_main"),
            ("base", "self_main"),
            ("self_main", "self_main"),
            ("본진", "self_main"),
            ("우리 본진", "self_main"),
            ("self_ramp", "self_ramp"),
            ("main_ramp", "self_ramp"),
            ("본진 입구", "self_ramp"),
            ("main ramp", "self_ramp"),
            ("우리 입구", "self_ramp"),
            ("self_natural", "self_natural"),
            ("natural", "self_natural"),
            ("앞마당", "self_natural"),
            ("natural expansion", "self_natural"),
            ("우리 앞마당", "self_natural"),
            ("self_mineral_line", "self_mineral_line"),
            ("main_mineral_line", "self_mineral_line"),
            ("본진 미네랄 라인", "self_mineral_line"),
            ("self_geyser", "self_geyser"),
            ("main_geyser", "self_geyser"),
            ("본진 가스", "self_geyser"),
            ("enemy_main", "enemy_main"),
            ("enemy main", "enemy_main"),
            ("enemy base", "enemy_main"),
            ("enemy_base", "enemy_main"),
            ("적 본진", "enemy_main"),
            ("enemy_ramp", "enemy_ramp"),
            ("enemy ramp", "enemy_ramp"),
            ("적 램프", "enemy_ramp"),
            ("enemy_front", "enemy_front"),
            ("enemy front", "enemy_front"),
            ("적 입구", "enemy_front"),
            ("enemy_natural", "enemy_natural"),
            ("enemy natural", "enemy_natural"),
            ("enemy third", "enemy_third"),
            ("적 세번째 멀티", "enemy_third"),
            ("enemy choke", "enemy_choke"),
            ("적 초크", "enemy_choke"),
            ("enemy_mineral_line", "enemy_mineral_line"),
            ("enemy mineral line", "enemy_mineral_line"),
        ):
            with self.subTest(alias=alias):
                resolution = self.resolver.resolve(alias)
                self.assertTrue(resolution.available)
                self.assertEqual(canonical, resolution.target)

    def test_unknown_target_rejected_with_available_alternatives(self) -> None:
        for unknown in ("island base", ""):
            with self.subTest(unknown=unknown):
                resolution = self.resolver.resolve(unknown)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertIn("Unsupported semantic map target", resolution.reason)
                self.assertEqual(
                    "unsupported_semantic_target",
                    resolution.reason_code,
                )
                self.assertEqual(
                    self.resolver.available_targets,
                    resolution.alternatives,
                )
                self.assertIsNone(self.resolver.resolve_point(unknown))

    def test_snapshot_registry_is_built_once_and_ignores_later_bot_mutation(self) -> None:
        self.bot.start_location = FakePoint(99.0, 99.0)
        self.bot.mineral_field = []
        resolution = self.resolver.resolve("self_main")
        assert resolution.position is not None
        self.assertEqual((30.0, 30.0), resolution.position.to_tuple())
        self.assertTrue(self.resolver.resolve("self_mineral_line").available)

    def test_lookup_alias_matches_resolve_for_snapshot_catalog(self) -> None:
        resolution = self.resolver.lookup("본진")

        self.assertTrue(resolution.available)
        self.assertEqual("self_main", resolution.target)
        assert resolution.position is not None
        self.assertEqual((30.0, 30.0), resolution.position.to_tuple())

    def test_catalog_lookup_normalizes_korean_and_english_aliases(self) -> None:
        for alias, canonical in (
            ("Main Base", "self_main"),
            ("우리 본 진", "self_main"),
            ("본진 입 구", "self_ramp"),
            ("Enemy Natural", "enemy_natural"),
            ("적 앞 마당", "enemy_natural"),
            ("enemy  mineral   line", "enemy_mineral_line"),
            ("상대 일꾼 라인", "enemy_mineral_line"),
        ):
            with self.subTest(alias=alias):
                resolution = self.resolver.resolve(alias)

                self.assertTrue(resolution.available)
                self.assertEqual(canonical, resolution.target)

    def test_korean_aliases_resolve_to_canonical_targets(self) -> None:
        for alias, canonical, expected_point in (
            ("본진", "self_main", (30.0, 30.0)),
            ("우리 본진", "self_main", (30.0, 30.0)),
            ("본진 입구", "self_ramp", (38.0, 36.0)),
            ("앞마당", "self_natural", (45.0, 55.0)),
            ("본진 미네랄 라인", "self_mineral_line", (74.0 / 3.0, 28.0)),
            ("본진 가스", "self_geyser", (39.0, 21.0)),
            ("적 본진", "enemy_main", (170.0, 170.0)),
            ("적 램프", "enemy_ramp", (162.0, 164.0)),
            ("적 입구", "enemy_front", (162.0, 164.0)),
            ("적 앞마당", "enemy_natural", (155.0, 145.0)),
            ("상대 일꾼 라인", "enemy_mineral_line", (526.0 / 3.0, 172.0)),
        ):
            with self.subTest(alias=alias):
                resolution = self.resolver.resolve(alias)

                self.assertTrue(resolution.available)
                self.assertEqual(canonical, resolution.target)
                assert resolution.position is not None
                self.assertAlmostEqual(expected_point[0], resolution.position.x)
                self.assertAlmostEqual(expected_point[1], resolution.position.y)

    def test_korean_natural_alias_uses_nearest_expansion_to_main_base(self) -> None:
        bot = FakeBot()
        bot.structures = [
            FakeUnit(30.0, 30.0, name="CommandCenter"),
            FakeUnit(45.0, 55.0, name="CommandCenter"),
            FakeUnit(80.0, 70.0, name="CommandCenter"),
        ]
        bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(47.0, 56.0),
            FakePoint(45.0, 55.0),
            FakePoint(80.0, 70.0),
            FakePoint(170.0, 170.0),
            FakePoint(155.0, 145.0),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("앞마당")

        self.assertTrue(resolution.available)
        self.assertEqual("self_natural", resolution.target)
        assert resolution.position is not None
        self.assertEqual((45.0, 55.0), resolution.position.to_tuple())

    def test_korean_main_and_natural_aliases_across_base_scenarios(self) -> None:
        single_base_bot = FakeBot()
        single_base_bot.structures = [
            FakeUnit(30.0, 30.0, name="CommandCenter"),
        ]

        multi_townhall_bot = FakeBot()
        multi_townhall_bot.structures = [
            FakeUnit(30.0, 30.0, name="CommandCenter"),
            FakeUnit(45.0, 55.0, name="CommandCenter"),
            FakeUnit(80.0, 70.0, name="CommandCenter"),
        ]

        ambiguous_expansion_bot = FakeBot()
        ambiguous_expansion_bot.structures = [
            FakeUnit(30.0, 30.0, name="CommandCenter"),
        ]
        ambiguous_expansion_bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(45.0, 55.0),
            FakePoint(55.0, 45.0),
            FakePoint(170.0, 170.0),
        ]

        cases = (
            ("single-base", single_base_bot, (30.0, 30.0), (45.0, 55.0)),
            ("multi-townhall", multi_townhall_bot, (30.0, 30.0), (45.0, 55.0)),
            (
                "ambiguous-expansion",
                ambiguous_expansion_bot,
                (30.0, 30.0),
                (45.0, 55.0),
            ),
        )
        for scenario, bot, expected_main, expected_natural in cases:
            with self.subTest(scenario=scenario, alias="본진"):
                main = SC2MapResolver.from_bot(bot).resolve("본진")

                self.assertTrue(main.available)
                self.assertEqual("self_main", main.target)
                assert main.position is not None
                self.assertEqual(expected_main, main.position.to_tuple())

            with self.subTest(scenario=scenario, alias="앞마당"):
                natural = SC2MapResolver.from_bot(bot).resolve("앞마당")

                self.assertTrue(natural.available)
                self.assertEqual("self_natural", natural.target)
                assert natural.position is not None
                self.assertEqual(expected_natural, natural.position.to_tuple())

    def test_english_aliases_resolve_to_canonical_targets(self) -> None:
        for alias, canonical, expected_point in (
            ("main base", "self_main", (30.0, 30.0)),
            ("main ramp", "self_ramp", (38.0, 36.0)),
            ("natural expansion", "self_natural", (45.0, 55.0)),
            ("main mineral line", "self_mineral_line", (74.0 / 3.0, 28.0)),
            ("main geyser", "self_geyser", (39.0, 21.0)),
            ("enemy base", "enemy_main", (170.0, 170.0)),
            ("enemy ramp", "enemy_ramp", (162.0, 164.0)),
            ("enemy front", "enemy_front", (162.0, 164.0)),
            ("enemy natural", "enemy_natural", (155.0, 145.0)),
            ("enemy third", "enemy_third", (140.0, 110.0)),
            ("natural choke", "self_choke", (38.0, 36.0)),
            ("enemy choke", "enemy_choke", (162.0, 164.0)),
            ("enemy mineral line", "enemy_mineral_line", (526.0 / 3.0, 172.0)),
        ):
            with self.subTest(alias=alias):
                resolution = self.resolver.lookup(alias)

                self.assertTrue(resolution.available)
                self.assertEqual(canonical, resolution.target)
                assert resolution.position is not None
                self.assertAlmostEqual(expected_point[0], resolution.position.x)
                self.assertAlmostEqual(expected_point[1], resolution.position.y)

    def test_missing_aliases_are_rejected_without_semantic_fabrication(self) -> None:
        for alias in (
            "상대 몰래멀티",
            "gold base",
        ):
            with self.subTest(alias=alias):
                resolution = self.resolver.resolve(alias)

                self.assertFalse(resolution.available)
                self.assertEqual(alias, resolution.target)
                self.assertIsNone(resolution.position)
                self.assertIn("Unsupported semantic map target", resolution.reason)
                self.assertEqual(self.resolver.available_targets, resolution.alternatives)

    def test_semantic_target_catalog_refreshes_from_bot_observations(self) -> None:
        catalog = {
            entry.target: entry for entry in self.resolver.semantic_target_catalog
        }
        for target in (
            "self_main",
            "self_natural",
            "self_ramp",
        ):
            with self.subTest(target=target):
                entry = catalog[target]
                expected_point = EXPECTED_FULL_MAP_TARGETS[target]
                self.assertTrue(entry.available)
                self.assertEqual("", entry.failure_reason)
                self.assertEqual("python-sc2 observations", entry.source)
                assert entry.position is not None
                self.assertAlmostEqual(expected_point[0], entry.position.x)
                self.assertAlmostEqual(expected_point[1], entry.position.y)

        for target in ("self_mineral_line", "self_geyser"):
            with self.subTest(resource_target=target):
                entry = catalog[target]
                expected_point = EXPECTED_FULL_MAP_TARGETS[target]
                self.assertTrue(entry.available)
                self.assertEqual("", entry.failure_reason)
                self.assertEqual(
                    "python-sc2 validated base/resource geometry",
                    entry.source,
                )
                assert entry.position is not None
                self.assertAlmostEqual(expected_point[0], entry.position.x)
                self.assertAlmostEqual(expected_point[1], entry.position.y)

        self.assertIn("base", catalog["self_main"].aliases)
        self.assertIn("앞마당", catalog["self_natural"].aliases)
        self.assertIn("본진 입구", catalog["self_ramp"].aliases)
        self.assertIn("본진 미네랄 라인", catalog["self_mineral_line"].aliases)
        self.assertIn("main_geyser", catalog["self_geyser"].aliases)

    def test_geometry_inference_snapshot_carries_auditable_metadata(self) -> None:
        geometry = self.resolver.geometry

        starts = {entry.key: entry for entry in geometry.start_locations}
        self.assertEqual({"self_start_location", "enemy_start_location_1"}, set(starts))
        self.assertEqual("visible", starts["self_start_location"].visibility)
        self.assertEqual("inferred", starts["enemy_start_location_1"].visibility)
        self.assertEqual(1.0, starts["self_start_location"].confidence)
        self.assertEqual(0.75, starts["enemy_start_location_1"].confidence)

        clusters = {cluster.key: cluster for cluster in geometry.base_clusters}
        for key in ("self_main", "self_natural", "enemy_natural", "enemy_main"):
            with self.subTest(cluster=key):
                self.assertIn(key, clusters)
                self.assertIn(clusters[key].visibility, SC2_GEOMETRY_VISIBILITY_VALUES)
                self.assertGreaterEqual(clusters[key].confidence, 0.0)
                self.assertLessEqual(clusters[key].confidence, 1.0)

        self_main_cluster = clusters["self_main"]
        self.assertIs(geometry.player_main_base, self_main_cluster)
        self.assertIs(self.resolver.player_main_base, self_main_cluster)
        self.assertEqual("visible", self_main_cluster.visibility)
        self.assertEqual(3, len(self_main_cluster.mineral_patches))
        self.assertEqual(1, len(self_main_cluster.geysers))
        assert self_main_cluster.ramp is not None
        self.assertEqual("self_ramp", self_main_cluster.ramp.key)

        enemy_main_cluster = clusters["enemy_main"]
        self.assertEqual("inferred", enemy_main_cluster.visibility)
        self.assertEqual(3, len(enemy_main_cluster.mineral_patches))
        self.assertEqual(1, len(enemy_main_cluster.geysers))
        assert enemy_main_cluster.ramp is not None
        self.assertEqual("enemy_ramp", enemy_main_cluster.ramp.key)

        self.assertEqual(7, len(geometry.mineral_patches))
        self.assertEqual(2, len(geometry.geysers))
        for observation in (
            *geometry.ramps,
            *geometry.mineral_patches,
            *geometry.geysers,
        ):
            with self.subTest(observation=observation.key):
                self.assertIn(observation.visibility, SC2_GEOMETRY_VISIBILITY_VALUES)
                self.assertGreaterEqual(observation.confidence, 0.0)
                self.assertLessEqual(observation.confidence, 1.0)
                self.assertNotEqual("", observation.source.strip())

    def test_resolver_to_dict_is_json_ready(self) -> None:
        payload = json.loads(json.dumps(self.resolver.to_dict()))
        self.assertEqual(
            list(self.resolver.available_targets),
            payload["available_targets"],
        )
        self.assertEqual({"x": 30.0, "y": 30.0}, payload["positions"]["self_main"])
        self.assertEqual(
            {
                "scout_location",
                "last_seen_enemy_area",
            },
            set(payload["unavailable"]),
        )
        self.assertEqual(
            list(SC2_SUPPORTED_SEMANTIC_TARGETS),
            [entry["target"] for entry in payload["semantic_target_catalog"]],
        )
        self.assertEqual(
            ["self_start_location", "enemy_start_location_1"],
            [entry["key"] for entry in payload["geometry"]["start_locations"]],
        )
        self.assertIn(
            "self_main",
            [entry["key"] for entry in payload["geometry"]["base_clusters"]],
        )
        self.assertEqual("self_main", payload["geometry"]["player_main_base"]["key"])
        self.assertEqual(
            {"x": 30.0, "y": 30.0},
            payload["geometry"]["player_main_base"]["anchor"],
        )


class SC2MapResolverDegradationTest(unittest.TestCase):
    def test_from_bot_never_raises_on_empty_object(self) -> None:
        resolver = SC2MapResolver.from_bot(object())
        self.assertEqual((), resolver.available_targets)
        self.assertIsNone(resolver.player_main_base)
        self.assertIsNone(resolver.geometry.player_main_base)
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
        for target in (
            "enemy_main",
            "enemy_ramp",
            "enemy_front",
            "enemy_natural",
            "enemy_mineral_line",
        ):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertNotEqual("", resolution.reason.strip())
        self.assertTrue(resolver.resolve("self_main").available)
        self.assertTrue(resolver.resolve("self_natural").available)

    def test_multiple_unscouted_enemy_starts_do_not_infer_enemy_base_targets(self) -> None:
        bot = FakeBot()
        bot.enemy_start_locations = [
            FakePoint(170.0, 170.0),
            FakePoint(20.0, 180.0),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        for target in (
            "enemy_main",
            "enemy_ramp",
            "enemy_front",
            "enemy_natural",
            "enemy_mineral_line",
        ):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertNotEqual("", resolution.reason.strip())

        self.assertIn("exactly one", resolver.resolve("enemy_main").reason)
        self.assertTrue(resolver.resolve("self_main").available)
        self.assertTrue(resolver.resolve("self_natural").available)

    def test_visible_enemy_townhall_derives_enemy_main_without_spawn_data(self) -> None:
        bot = FakeBot()
        bot.enemy_start_locations = []
        bot.enemy_structures = [
            FakeUnit(170.0, 170.0, name="Hatchery"),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        for alias in ("enemy_main", "enemy_base", "enemy base", "적 본진"):
            with self.subTest(alias=alias):
                resolution = resolver.resolve(alias)
                self.assertTrue(resolution.available)
                self.assertEqual("enemy_main", resolution.target)
                assert resolution.position is not None
                self.assertEqual((170.0, 170.0), resolution.position.to_tuple())

        self.assertTrue(resolver.resolve("enemy_ramp").available)
        self.assertTrue(resolver.resolve("enemy_front").available)
        self.assertTrue(resolver.resolve("enemy_natural").available)
        self.assertTrue(resolver.resolve("enemy_mineral_line").available)
        catalog = {
            entry.target: entry for entry in resolver.semantic_target_catalog
        }
        self.assertTrue(catalog["enemy_main"].available)
        self.assertIn("enemy_base", catalog["enemy_main"].aliases)
        for target in ("enemy_ramp", "enemy_front"):
            with self.subTest(target=target):
                entry = catalog[target]
                self.assertTrue(entry.available)
                self.assertEqual(
                    "python-sc2 enemy vision/scouting observations",
                    entry.source,
                )
                assert entry.position is not None
                self.assertEqual((162.0, 164.0), entry.position.to_tuple())

    def test_last_seen_enemy_townhall_derives_enemy_side_targets(self) -> None:
        bot = FakeBot()
        bot.enemy_start_locations = []
        bot.last_seen_enemy_structures = [
            FakeUnit(170.0, 170.0, name="Hatchery"),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        for target in (
            "enemy_main",
            "enemy_ramp",
            "enemy_front",
            "enemy_natural",
            "enemy_mineral_line",
        ):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertTrue(resolution.available)
                self.assertIsNotNone(resolution.position)

        enemy_main = resolver.resolve("enemy_main")
        assert enemy_main.position is not None
        self.assertEqual((170.0, 170.0), enemy_main.position.to_tuple())
        catalog = {
            entry.target: entry for entry in resolver.semantic_target_catalog
        }
        self.assertEqual(
            "python-sc2 enemy vision/scouting observations",
            catalog["enemy_main"].source,
        )
        clusters = {cluster.key: cluster for cluster in resolver.geometry.base_clusters}
        self.assertEqual("unseen", clusters["enemy_main"].visibility)
        self.assertEqual(
            "python-sc2 enemy vision/scouting observations",
            clusters["enemy_main"].source,
        )

    def test_last_seen_units_and_creep_disambiguate_multiple_enemy_starts(self) -> None:
        cases = (
            ("unit-history", "last_seen_enemy_units", [FakeUnit(169.0, 171.0)]),
            ("creep-history", "enemy_creep_positions", [FakePoint(171.0, 169.0)]),
        )
        for scenario, attr_name, evidence in cases:
            with self.subTest(scenario=scenario):
                bot = FakeBot()
                bot.enemy_start_locations = [
                    FakePoint(170.0, 170.0),
                    FakePoint(20.0, 180.0),
                ]
                setattr(bot, attr_name, evidence)
                resolver = SC2MapResolver.from_bot(bot)

                resolution = resolver.resolve("enemy_main")
                self.assertTrue(resolution.available)
                assert resolution.position is not None
                self.assertEqual((170.0, 170.0), resolution.position.to_tuple())
                self.assertTrue(resolver.resolve("enemy_ramp").available)
                self.assertTrue(resolver.resolve("enemy_natural").available)

    def test_inferred_opponent_base_position_derives_enemy_main_without_spawn_data(
        self,
    ) -> None:
        bot = FakeBot()
        bot.enemy_start_locations = []
        bot.inferred_opponent_base_positions = [FakePoint(170.0, 170.0)]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("적 본진")

        self.assertTrue(resolution.available)
        self.assertEqual("enemy_main", resolution.target)
        assert resolution.position is not None
        self.assertEqual((170.0, 170.0), resolution.position.to_tuple())
        self.assertTrue(resolver.resolve("last_seen_enemy_area").available)

    def test_close_own_natural_candidates_pick_nearest_to_main_base(self) -> None:
        ambiguous_bot = FakeBot()
        ambiguous_bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(45.0, 55.0),
            FakePoint(55.0, 45.0),
            FakePoint(170.0, 170.0),
            FakePoint(155.0, 145.0),
        ]
        ambiguous_resolver = SC2MapResolver.from_bot(ambiguous_bot)
        resolution = ambiguous_resolver.resolve("self_natural")
        self.assertTrue(resolution.available)
        assert resolution.position is not None
        self.assertEqual((45.0, 55.0), resolution.position.to_tuple())

        far_bot = FakeBot()
        far_bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(120.0, 120.0),
            FakePoint(170.0, 170.0),
        ]
        far_resolver = SC2MapResolver.from_bot(far_bot)
        self.assertFalse(far_resolver.resolve("self_natural").available)
        self.assertIn("distance bounds", far_resolver.resolve("self_natural").reason)

    def test_ambiguous_or_far_enemy_ramp_is_not_inferred_without_scouting(self) -> None:
        ambiguous_bot = FakeBot()
        ambiguous_bot.game_info = FakeGameInfo(
            [
                FakeRamp(top_center=FakePoint(162.0, 164.0)),
                FakeRamp(top_center=FakePoint(164.0, 162.0)),
            ]
        )
        ambiguous_resolver = SC2MapResolver.from_bot(ambiguous_bot)
        ramp_resolution = ambiguous_resolver.resolve("enemy_ramp")
        self.assertFalse(ramp_resolution.available)
        self.assertIn("single", ramp_resolution.reason)

        front_resolution = ambiguous_resolver.resolve("enemy_front")
        self.assertFalse(front_resolution.available)
        self.assertIn("safely inferred", front_resolution.reason)

        far_bot = FakeBot()
        far_bot.game_info = FakeGameInfo(
            [FakeRamp(top_center=FakePoint(120.0, 120.0))]
        )
        far_resolver = SC2MapResolver.from_bot(far_bot)
        self.assertFalse(far_resolver.resolve("enemy_ramp").available)
        self.assertIn("within 25", far_resolver.resolve("enemy_ramp").reason)

    def test_scouted_enemy_front_access_adds_ramp_and_front_catalog_entries(self) -> None:
        bot = FakeBot()
        bot.enemy_start_locations = []
        bot.scouted_enemy_front = FakePoint(150.0, 160.0)
        resolver = SC2MapResolver.from_bot(bot)

        for target in ("enemy_ramp", "enemy_front"):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertTrue(resolution.available)
                assert resolution.position is not None
                self.assertEqual((150.0, 160.0), resolution.position.to_tuple())

        catalog = {
            entry.target: entry for entry in resolver.semantic_target_catalog
        }
        for target in ("enemy_ramp", "enemy_front"):
            with self.subTest(catalog_target=target):
                entry = catalog[target]
                self.assertTrue(entry.available)
                self.assertEqual(
                    "python-sc2 enemy vision/scouting observations",
                    entry.source,
                )
                self.assertEqual("", entry.failure_reason)

    def test_enemy_natural_catalog_entry_is_marked_when_scouted_by_vision(self) -> None:
        bot = FakeBot()
        bot.enemy_structures = [
            FakeUnit(170.0, 170.0, name="CommandCenter"),
            FakeUnit(155.0, 145.0, name="CommandCenter"),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("enemy_natural")
        self.assertTrue(resolution.available)
        assert resolution.position is not None
        self.assertEqual((155.0, 145.0), resolution.position.to_tuple())

        catalog = {
            entry.target: entry for entry in resolver.semantic_target_catalog
        }
        entry = catalog["enemy_natural"]
        self.assertTrue(entry.available)
        self.assertEqual("python-sc2 enemy vision/scouting observations", entry.source)
        self.assertEqual("", entry.failure_reason)
        self.assertIn("enemy natural", entry.aliases)

    def test_enemy_natural_safe_inference_keeps_default_catalog_source(self) -> None:
        resolver = SC2MapResolver.from_bot(FakeBot())

        catalog = {
            entry.target: entry for entry in resolver.semantic_target_catalog
        }

        self.assertTrue(catalog["enemy_natural"].available)
        self.assertEqual("python-sc2 observations", catalog["enemy_natural"].source)

    def test_visible_non_townhall_enemy_structure_does_not_create_enemy_main(self) -> None:
        bot = FakeBot()
        bot.enemy_start_locations = []
        bot.enemy_structures = [
            FakeUnit(90.0, 90.0, type_id_name="Barracks"),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("enemy_main")
        self.assertFalse(resolution.available)
        self.assertIn("visible enemy townhall", resolution.reason)

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

    def test_geyser_is_unavailable_without_validated_base_resource(self) -> None:
        bot = FakeBot()
        bot.vespene_geyser = [FakeUnit(100.0, 100.0)]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("self_geyser")

        self.assertFalse(resolution.available)
        self.assertIsNone(resolution.position)
        self.assertIn("validated self_main base cluster", resolution.reason)
        self.assertIn("vespene_geyser", resolution.reason)

    def test_mineral_line_rejects_ambiguous_resource_cluster_attachment(self) -> None:
        bot = FakeBot()
        bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(39.0, 30.0),
            FakePoint(155.0, 145.0),
            FakePoint(170.0, 170.0),
        ]
        bot.mineral_field = [
            FakeUnit(34.0, 30.0),
            FakeUnit(176.0, 172.0),
            FakeUnit(176.0, 168.0),
            FakeUnit(174.0, 176.0),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("self_mineral_line")

        self.assertFalse(resolution.available)
        self.assertIsNone(resolution.position)
        self.assertIn("ambiguous base/resource geometry", resolution.reason)
        self.assertIn("mineral_patch_1", resolution.reason)

    def test_geyser_rejects_ambiguous_resource_cluster_attachment(self) -> None:
        bot = FakeBot()
        bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(39.0, 30.0),
            FakePoint(155.0, 145.0),
            FakePoint(170.0, 170.0),
        ]
        bot.vespene_geyser = [
            FakeUnit(34.0, 30.0),
            FakeUnit(161.0, 179.0),
        ]
        resolver = SC2MapResolver.from_bot(bot)

        resolution = resolver.resolve("self_geyser")

        self.assertFalse(resolution.available)
        self.assertIsNone(resolution.position)
        self.assertIn("ambiguous base/resource geometry", resolution.reason)
        self.assertIn("geyser_1", resolution.reason)

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
                "enemy_front",
                "enemy_natural",
                "self_choke",
                "self_third",
                "enemy_choke",
                "enemy_third",
            ),
            resolution.alternatives,
        )


class SC2RuntimeMapResolverTest(unittest.TestCase):
    def test_runtime_lookup_refreshes_current_world_positions(self) -> None:
        bot = FakeBot()
        resolver = SC2RuntimeMapResolver(bot)

        initial = resolver.lookup("self_main")
        assert initial.position is not None
        self.assertEqual((30.0, 30.0), initial.position.to_tuple())

        bot.start_location = FakePoint(99.0, 99.0)
        current = resolver.lookup("self_main")

        self.assertTrue(current.available)
        self.assertEqual("self_main", current.target)
        assert current.position is not None
        self.assertEqual((99.0, 99.0), current.position.to_tuple())
        assert resolver.player_main_base is not None
        self.assertEqual((99.0, 99.0), resolver.player_main_base.anchor.to_tuple())

    def test_runtime_alias_lookup_refreshes_when_target_world_positions_change(self) -> None:
        bot = FakeBot()
        resolver = SC2RuntimeMapResolver(bot)

        initial_main = resolver.lookup("본진")
        initial_ramp = resolver.lookup("main ramp")
        initial_minerals = resolver.lookup("본진 미네랄 라인")
        assert initial_main.position is not None
        assert initial_ramp.position is not None
        assert initial_minerals.position is not None
        self.assertEqual((30.0, 30.0), initial_main.position.to_tuple())
        self.assertEqual((38.0, 36.0), initial_ramp.position.to_tuple())
        self.assertEqual((74.0 / 3.0, 28.0), initial_minerals.position.to_tuple())

        bot.start_location = FakePoint(70.0, 70.0)
        bot.main_base_ramp = FakeRamp(top_center=FakePoint(78.0, 72.0))
        bot.expansion_locations_list = [
            FakePoint(70.0, 70.0),
            FakePoint(88.0, 84.0),
            FakePoint(170.0, 170.0),
            FakePoint(155.0, 145.0),
        ]
        bot.mineral_field = [
            FakeUnit(64.0, 68.0),
            FakeUnit(64.0, 72.0),
            FakeUnit(66.0, 66.0),
            FakeUnit(176.0, 172.0),
            FakeUnit(176.0, 168.0),
            FakeUnit(174.0, 176.0),
        ]
        bot.vespene_geyser = [FakeUnit(76.0, 58.0), FakeUnit(161.0, 179.0)]

        updated_main = resolver.lookup("본진")
        updated_ramp = resolver.lookup("main ramp")
        updated_natural = resolver.lookup("앞마당")
        updated_minerals = resolver.lookup("본진 미네랄 라인")

        self.assertEqual("self_main", updated_main.target)
        self.assertEqual("self_ramp", updated_ramp.target)
        self.assertEqual("self_natural", updated_natural.target)
        self.assertEqual("self_mineral_line", updated_minerals.target)
        assert updated_main.position is not None
        assert updated_ramp.position is not None
        assert updated_natural.position is not None
        assert updated_minerals.position is not None
        self.assertEqual((70.0, 70.0), updated_main.position.to_tuple())
        self.assertEqual((78.0, 72.0), updated_ramp.position.to_tuple())
        self.assertEqual((88.0, 84.0), updated_natural.position.to_tuple())
        self.assertEqual((194.0 / 3.0, 206.0 / 3.0), updated_minerals.position.to_tuple())

    def test_runtime_anchor_resolution_refreshes_current_world_positions(self) -> None:
        bot = FakeBot()
        resolver = SC2RuntimeMapResolver(bot)

        initial = resolver.resolve_anchor_position({"anchor_target": "self_ramp"})
        assert initial.position is not None
        self.assertEqual((38.0, 36.0), initial.position.to_tuple())

        bot.start_location = FakePoint(70.0, 70.0)
        bot.main_base_ramp = FakeRamp(top_center=FakePoint(78.0, 72.0))
        current = resolver.resolve_anchor_position({"anchor_target": "self_ramp"})

        self.assertTrue(current.available)
        self.assertEqual("self_ramp", current.target)
        assert current.position is not None
        self.assertEqual((78.0, 72.0), current.position.to_tuple())

    def test_runtime_catalog_reflects_new_resource_failures(self) -> None:
        bot = FakeBot()
        resolver = SC2RuntimeMapResolver(bot)
        self.assertTrue(resolver.lookup("self_mineral_line").available)

        bot.mineral_field = []
        current = resolver.lookup("self_mineral_line")

        self.assertFalse(current.available)
        self.assertIsNone(current.position)
        self.assertIn("mineral_field", current.reason)
        self.assertNotIn("self_mineral_line", current.alternatives)

    def test_runtime_catalog_properties_are_fresh(self) -> None:
        bot = FakeBot()
        resolver = SC2RuntimeMapResolver(bot)
        self.assertIn("self_mineral_line", resolver.available_targets)

        bot.mineral_field = []
        self.assertNotIn("self_mineral_line", resolver.available_targets)
        catalog = {entry.target: entry for entry in resolver.semantic_target_catalog}
        self.assertFalse(catalog["self_mineral_line"].available)
        self.assertIn("mineral_field", catalog["self_mineral_line"].failure_reason)


class SC2MapResolverSubAC534RegressionTest(unittest.TestCase):
    def test_inferred_valid_enemy_targets_are_resolvable_and_auditable(self) -> None:
        resolver = SC2MapResolver.from_bot(FakeBot())

        for target in ("enemy_main", "enemy_ramp", "enemy_front", "enemy_natural"):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertTrue(resolution.available)
                self.assertIsNotNone(resolution.position)
                self.assertEqual("", resolution.reason)

        geometry = resolver.geometry
        clusters = {cluster.key: cluster for cluster in geometry.base_clusters}
        ramps = {ramp.key: ramp for ramp in geometry.ramps}
        self.assertEqual("inferred", clusters["enemy_main"].visibility)
        self.assertEqual("inferred", clusters["enemy_natural"].visibility)
        self.assertEqual("inferred", ramps["enemy_ramp"].visibility)

        catalog = {
            entry.target: entry for entry in resolver.semantic_target_catalog
        }
        self.assertEqual(
            "python-sc2 observations",
            catalog["enemy_natural"].source,
        )
        self.assertEqual(
            "python-sc2 validated base/resource geometry",
            catalog["enemy_mineral_line"].source,
        )

    def test_undiscovered_but_inferable_targets_stay_marked_as_inferred(self) -> None:
        bot = FakeBot()
        bot.enemy_structures = []
        resolver = SC2MapResolver.from_bot(bot)

        for target in ("enemy_front", "enemy_natural", "enemy_mineral_line"):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertTrue(resolution.available)
                self.assertIsNotNone(resolution.position)

        geometry_payload = resolver.geometry.to_dict()
        enemy_ramp = next(
            ramp
            for ramp in geometry_payload["ramps"]
            if ramp["key"] == "enemy_ramp"
        )
        enemy_natural = next(
            cluster
            for cluster in geometry_payload["base_clusters"]
            if cluster["key"] == "enemy_natural"
        )
        self.assertEqual("inferred", enemy_ramp["visibility"])
        self.assertEqual("inferred", enemy_natural["visibility"])

    def test_ambiguous_geometry_refuses_targets_without_fabricating_points(self) -> None:
        bot = FakeBot()
        bot.expansion_locations_list = [
            FakePoint(30.0, 30.0),
            FakePoint(45.0, 55.0),
            FakePoint(55.0, 45.0),
            FakePoint(170.0, 170.0),
            FakePoint(155.0, 145.0),
        ]
        bot.game_info = FakeGameInfo(
            [
                FakeRamp(top_center=FakePoint(162.0, 164.0)),
                FakeRamp(top_center=FakePoint(164.0, 162.0)),
            ]
        )
        resolver = SC2MapResolver.from_bot(bot)

        self_natural = resolver.resolve("self_natural")
        self.assertTrue(self_natural.available)
        assert self_natural.position is not None
        self.assertEqual((45.0, 55.0), self_natural.position.to_tuple())

        for target, reason_fragment in (
            ("enemy_ramp", "single BotAI game_info.map_ramps"),
            ("enemy_front", "safely inferred"),
        ):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertIn(reason_fragment, resolution.reason)
                self.assertNotIn(target, resolution.alternatives)

    def test_missing_map_data_exposes_precise_failures_without_positions(self) -> None:
        bot = FakeBot()
        del bot.expansion_locations_list
        del bot.game_info
        bot.mineral_field = []
        bot.vespene_geyser = []
        resolver = SC2MapResolver.from_bot(bot)

        expected_reasons = {
            "self_natural": "expansion_locations_list",
            "enemy_natural": "expansion_locations_list",
            "enemy_ramp": "game_info.map_ramps",
            "enemy_front": "discovered or safely inferred",
            "enemy_mineral_line": "mineral_field/mineral_patch",
            "self_mineral_line": "mineral_field/mineral_patch",
            "self_geyser": "vespene_geyser",
        }
        for target, reason_fragment in expected_reasons.items():
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertIn(reason_fragment, resolution.reason)

        self.assertTrue(resolver.resolve("self_main").available)
        self.assertTrue(resolver.resolve("enemy_main").available)

    def test_unsupported_semantic_names_are_never_fabricated(self) -> None:
        resolver = SC2MapResolver.from_bot(FakeBot())

        for target in (
            "enemy third behind minerals",
            "상대 몰래멀티 뒤쪽",
        ):
            with self.subTest(target=target):
                resolution = resolver.resolve(target)
                self.assertFalse(resolution.available)
                self.assertIsNone(resolution.position)
                self.assertIn("Unsupported semantic map target", resolution.reason)
                self.assertEqual(resolver.available_targets, resolution.alternatives)
                self.assertIsNone(resolver.resolve_point(target))


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
                {"positions": {"island_base": MapPoint(1.0, 1.0)}},
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
                "unsupported source key",
                {"sources": {"island_base": "scouting"}},
                ValueError,
            ),
            (
                "blank source",
                {"sources": {"self_main": "  "}},
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
