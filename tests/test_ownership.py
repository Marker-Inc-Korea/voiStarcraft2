import unittest

from toycraft_commander.ownership import (
    ENEMY_CONTROLLED_ENTITY_NAMES,
    ENEMY_OWNER,
    ENTITY_NAMES_BY_KIND,
    ENTITY_NAMES_BY_OWNER,
    PLAYER_CONTROLLED_ENTITY_NAMES,
    PLAYER_OWNER,
    get_entity_names_by_kind,
    get_entity_names_by_owner,
    get_entity_owner,
    is_enemy_controlled_entity_name,
    is_enemy_controlled_unit,
    is_player_controlled_entity_name,
    is_player_controlled_unit_or_structure,
    resolve_enemy_controlled_entity_name,
    resolve_entity_name,
    resolve_player_controlled_entity_name,
)
from toycraft_commander.structures import (
    PLAYER_CONTROLLED_STRUCTURE_NAMES,
    is_player_controlled_structure_name,
)
from toycraft_commander.units import (
    PLAYER_CONTROLLED_UNIT_NAMES,
    is_player_controlled_unit_name,
)


class OwnershipLookupTest(unittest.TestCase):
    def test_player_controlled_inventory_combines_terran_units_and_structures(self) -> None:
        self.assertEqual(("SCV", "Marine", "Vulture"), PLAYER_CONTROLLED_UNIT_NAMES)
        self.assertEqual(
            ("Barracks", "Factory", "Supply Depot", "Refinery"),
            PLAYER_CONTROLLED_STRUCTURE_NAMES,
        )
        self.assertEqual(
            (
                "SCV",
                "Marine",
                "Vulture",
                "Barracks",
                "Factory",
                "Supply Depot",
                "Refinery",
            ),
            PLAYER_CONTROLLED_ENTITY_NAMES,
        )
        self.assertEqual(("Zealot",), ENEMY_CONTROLLED_ENTITY_NAMES)

    def test_ownership_helpers_accept_korean_and_english_aliases(self) -> None:
        self.assertTrue(is_player_controlled_unit_name("마린"))
        self.assertTrue(is_player_controlled_structure_name("서플"))
        self.assertTrue(is_player_controlled_entity_name("일꾼"))
        self.assertTrue(is_player_controlled_entity_name("배럭"))
        self.assertTrue(is_player_controlled_unit_or_structure("factory"))
        self.assertEqual("SCV", resolve_player_controlled_entity_name("에스시비"))
        self.assertEqual("Supply Depot", resolve_player_controlled_entity_name("보급고"))

    def test_enemy_and_unsupported_entities_are_not_player_controlled(self) -> None:
        self.assertTrue(is_enemy_controlled_entity_name("질럿"))
        self.assertTrue(is_enemy_controlled_unit("Zealot"))
        self.assertEqual("Zealot", resolve_enemy_controlled_entity_name("zealots"))
        self.assertFalse(is_player_controlled_entity_name("Zealot"))
        self.assertFalse(is_player_controlled_unit_name("질럿"))
        self.assertFalse(is_player_controlled_structure_name("Bunker"))
        self.assertFalse(is_player_controlled_entity_name("Medic"))
        self.assertIsNone(resolve_player_controlled_entity_name("질럿"))
        self.assertIsNone(resolve_enemy_controlled_entity_name("마린"))

    def test_entity_resolver_and_owner_lookup_return_canonical_values(self) -> None:
        self.assertEqual("Marine", resolve_entity_name("marines"))
        self.assertEqual("Barracks", resolve_entity_name("병영"))
        self.assertEqual(PLAYER_OWNER, get_entity_owner("Marine"))
        self.assertEqual(PLAYER_OWNER, get_entity_owner("Refinery"))
        self.assertEqual(ENEMY_OWNER, get_entity_owner("Zealot"))

        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft owned entity"):
            get_entity_owner("Bunker")

    def test_group_lookup_helpers_return_stable_ordered_names(self) -> None:
        self.assertEqual(
            {
                "player": PLAYER_CONTROLLED_ENTITY_NAMES,
                "enemy": ENEMY_CONTROLLED_ENTITY_NAMES,
            },
            ENTITY_NAMES_BY_OWNER,
        )
        self.assertEqual(
            {
                "unit": ("SCV", "Marine", "Vulture", "Zealot"),
                "structure": ("Barracks", "Factory", "Supply Depot", "Refinery"),
            },
            ENTITY_NAMES_BY_KIND,
        )
        self.assertEqual(PLAYER_CONTROLLED_ENTITY_NAMES, get_entity_names_by_owner("player"))
        self.assertEqual(ENEMY_CONTROLLED_ENTITY_NAMES, get_entity_names_by_owner("enemy"))
        self.assertEqual(("SCV", "Marine", "Vulture", "Zealot"), get_entity_names_by_kind("unit"))

        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft entity owner"):
            get_entity_names_by_owner("neutral")
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft entity kind"):
            get_entity_names_by_kind("resource")


if __name__ == "__main__":
    unittest.main()
