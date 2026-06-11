import unittest

import toycraft_commander as package_exports
from toycraft_commander.map import (
    MAP_LOCATION_ALIASES,
    MAP_LOCATION_BY_NAME,
    MAP_LOCATION_NAMES,
    MAP_LOCATIONS,
    TARGETABLE_POSITION_BY_NAME,
    TARGETABLE_POSITION_NAMES,
    MapLocation,
    TargetablePosition,
    TilePosition,
    get_location_tile,
    get_map_location,
    get_map_locations_by_kind,
    get_resolved_map_location,
    get_target_tile,
    get_targetable_position,
    is_supported_location_name,
    is_targetable_position,
    resolve_location_name,
    resolve_targetable_position,
    resolve_tile_position,
)


class MapTargetLookupTest(unittest.TestCase):
    def test_map_registry_covers_phase_zero_command_locations(self) -> None:
        self.assertEqual(
            (
                "main",
                "main base",
                "main ramp",
                "main geyser",
                "natural approach",
                "natural choke",
                "natural expansion",
                "enemy main",
                "enemy natural",
                "enemy front",
                "enemy mineral line",
                "front bunker",
            ),
            MAP_LOCATION_NAMES,
        )
        self.assertEqual(set(MAP_LOCATION_NAMES), set(MAP_LOCATION_BY_NAME))
        self.assertEqual(MAP_LOCATION_NAMES, TARGETABLE_POSITION_NAMES)

        for location in MAP_LOCATIONS:
            with self.subTest(location=location.name):
                self.assertIsInstance(location, MapLocation)
                self.assertIsInstance(location.tile, TilePosition)
                self.assertTrue(location.description.strip())
                self.assertTrue(location.targetable)

    def test_location_aliases_accept_korean_and_compact_english_text(self) -> None:
        self.assertEqual("main base", resolve_location_name("본진"))
        self.assertEqual("main ramp", resolve_location_name(" 본진 입구 "))
        self.assertEqual("main geyser", resolve_location_name("본진 가스"))
        self.assertEqual("natural choke", resolve_location_name("앞마당 입구"))
        self.assertEqual("natural expansion", resolve_location_name("앞마당"))
        self.assertEqual("enemy main", resolve_location_name("적 본진"))
        self.assertEqual("enemy natural", resolve_location_name("적 앞마당"))
        self.assertEqual("enemy mineral line", resolve_location_name("상대 미네랄"))
        self.assertEqual("front bunker", resolve_location_name("전방 벙커"))
        self.assertEqual("main base", resolve_location_name("main base fallback"))
        self.assertEqual("main ramp", resolve_location_name("MAIN   RAMP"))
        self.assertIn("본진입구", MAP_LOCATION_ALIASES)

        self.assertIsNone(resolve_location_name("섬멀티"))
        self.assertIsNone(resolve_location_name(None))
        self.assertFalse(is_supported_location_name("섬멀티"))

    def test_location_lookup_helpers_return_canonical_locations_and_tiles(self) -> None:
        main_ramp = get_map_location("main ramp")

        self.assertEqual("main ramp", main_ramp.name)
        self.assertEqual("ramp", main_ramp.kind)
        self.assertEqual(TilePosition(34, 30), main_ramp.tile)
        self.assertEqual(main_ramp, get_resolved_map_location("입구"))
        self.assertEqual(TilePosition(18, 27), get_location_tile("가스"))
        self.assertEqual({"x": 34, "y": 30}, get_location_tile("main ramp").to_dict())

        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft map location"):
            get_map_location("island expansion")
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft map location"):
            get_resolved_map_location("섬멀티")

    def test_tile_position_resolver_accepts_tiles_mappings_and_named_locations(
        self,
    ) -> None:
        self.assertEqual(TilePosition(1, 2), resolve_tile_position(TilePosition(1, 2)))
        self.assertEqual(TilePosition(3, 4), resolve_tile_position((3, 4)))
        self.assertEqual(TilePosition(5, 6), resolve_tile_position([5, 6]))
        self.assertEqual(TilePosition(7, 8), resolve_tile_position({"x": 7, "y": 8}))
        self.assertEqual(TilePosition(64, 48), resolve_tile_position("앞마당"))

        self.assertIsNone(resolve_tile_position((1, 2, 3)))
        self.assertIsNone(resolve_tile_position((-1, 2)))
        self.assertIsNone(resolve_tile_position({"x": True, "y": 2}))
        self.assertIsNone(resolve_tile_position("섬멀티"))

    def test_targetable_position_helpers_gate_command_targets(self) -> None:
        enemy_line = get_targetable_position("적 미네랄 라인")

        self.assertIsInstance(enemy_line, TargetablePosition)
        self.assertEqual("enemy mineral line", enemy_line.name)
        self.assertEqual("enemy_position", enemy_line.kind)
        self.assertEqual(TilePosition(132, 104), enemy_line.tile)
        self.assertEqual(enemy_line, resolve_targetable_position("enemy mineral line"))
        self.assertEqual(TilePosition(52, 44), get_target_tile("front bunker"))
        self.assertTrue(is_targetable_position("상대 입구"))
        self.assertFalse(is_targetable_position("섬멀티"))
        self.assertEqual(set(TARGETABLE_POSITION_NAMES), set(TARGETABLE_POSITION_BY_NAME))

        with self.assertRaisesRegex(
            KeyError,
            "Unsupported ToyCraft targetable position",
        ):
            get_targetable_position("섬멀티")

    def test_location_group_lookup_returns_stable_ordered_groups(self) -> None:
        self.assertEqual(
            ("main", "main base", "natural expansion"),
            tuple(location.name for location in get_map_locations_by_kind("base")),
        )
        self.assertEqual(
            ("enemy main", "enemy natural", "enemy front", "enemy mineral line"),
            tuple(
                location.name
                for location in get_map_locations_by_kind("enemy_position")
            ),
        )

        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft map location kind"):
            get_map_locations_by_kind("island")

    def test_package_exports_map_lookup_surface(self) -> None:
        self.assertIs(TilePosition, package_exports.TilePosition)
        self.assertIs(MapLocation, package_exports.MapLocation)
        self.assertIs(TargetablePosition, package_exports.TargetablePosition)
        self.assertEqual(MAP_LOCATION_NAMES, package_exports.MAP_LOCATION_NAMES)
        self.assertIs(MAP_LOCATION_BY_NAME, package_exports.MAP_LOCATION_BY_NAME)
        self.assertIs(TARGETABLE_POSITION_BY_NAME, package_exports.TARGETABLE_POSITION_BY_NAME)
        self.assertIs(resolve_location_name, package_exports.resolve_location_name)
        self.assertIs(resolve_tile_position, package_exports.resolve_tile_position)
        self.assertIs(get_targetable_position, package_exports.get_targetable_position)
        self.assertIs(is_targetable_position, package_exports.is_targetable_position)

    def test_map_dataclasses_reject_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "x must be a non-negative integer"):
            TilePosition(-1, 0)
        with self.assertRaisesRegex(ValueError, "y must be a non-negative integer"):
            TilePosition(0, True)
        with self.assertRaisesRegex(ValueError, "targetable must be a boolean"):
            MapLocation(
                name="main",
                kind="base",
                tile=TilePosition(1, 1),
                targetable=1,
                description="Main",
            )
        with self.assertRaisesRegex(ValueError, "description must be a non-empty string"):
            TargetablePosition(
                name="main",
                kind="location",
                tile=TilePosition(1, 1),
                description=" ",
            )


if __name__ == "__main__":
    unittest.main()
