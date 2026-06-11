import unittest

from toycraft_commander.units import (
    COMBAT_UNIT_NAMES,
    ENEMY_UNIT_NAMES,
    TERRAN_UNIT_NAMES,
    TRAINABLE_UNIT_NAMES,
    UNIT_NAME_ALIASES,
    UNIT_MODEL_BY_NAME,
    UNIT_MODELS,
    UNIT_NAMES,
    UNIT_NAMES_BY_PRODUCER,
    UnitCost,
    UnitModel,
    UnitStats,
    get_unit_models_by_faction,
    get_unit_models_by_producer,
    get_unit_models_by_role,
    get_unit_model,
    get_unit_names_by_producer,
    get_resolved_unit_model,
    is_combat_unit_name,
    is_enemy_unit_name,
    is_supported_unit_name,
    is_terran_unit_name,
    is_trainable_unit_name,
    is_unit_produced_by,
    resolve_unit_name,
)


class UnitModelTest(unittest.TestCase):
    def test_unit_inventory_defines_exactly_phase_zero_models(self) -> None:
        self.assertEqual(("SCV", "Marine", "Vulture", "Zealot"), UNIT_NAMES)
        self.assertEqual(("SCV", "Marine", "Vulture", "Zealot"), tuple(unit.name for unit in UNIT_MODELS))
        self.assertEqual(set(UNIT_NAMES), set(UNIT_MODEL_BY_NAME))

    def test_unit_costs_match_toycraft_mvp_values(self) -> None:
        expected_costs = {
            "SCV": {"minerals": 50, "gas": 0, "supply": 1, "build_time_seconds": 20},
            "Marine": {"minerals": 50, "gas": 0, "supply": 1, "build_time_seconds": 24},
            "Vulture": {"minerals": 75, "gas": 0, "supply": 2, "build_time_seconds": 30},
            "Zealot": {"minerals": 100, "gas": 0, "supply": 2, "build_time_seconds": 40},
        }

        for name, cost in expected_costs.items():
            with self.subTest(unit=name):
                self.assertEqual(cost, get_unit_model(name).cost.to_dict())

    def test_unit_stats_match_toycraft_mvp_values(self) -> None:
        expected_stats = {
            "SCV": {"hit_points": 60, "shields": 0, "armor": 0, "ground_damage": 5, "attack_range": 1},
            "Marine": {"hit_points": 40, "shields": 0, "armor": 0, "ground_damage": 6, "attack_range": 4},
            "Vulture": {"hit_points": 80, "shields": 0, "armor": 0, "ground_damage": 20, "attack_range": 5},
            "Zealot": {"hit_points": 100, "shields": 60, "armor": 1, "ground_damage": 16, "attack_range": 1},
        }

        for name, stats in expected_stats.items():
            with self.subTest(unit=name):
                self.assertEqual(stats, get_unit_model(name).stats.to_dict())

    def test_unit_metadata_supports_terran_mvp_and_enemy_pressure(self) -> None:
        self.assertEqual("Terran", get_unit_model("SCV").faction)
        self.assertEqual("Terran", get_unit_model("Marine").faction)
        self.assertEqual("Terran", get_unit_model("Vulture").faction)
        self.assertEqual("Protoss", get_unit_model("Zealot").faction)
        self.assertEqual("enemy_melee", get_unit_model("Zealot").role)

        for unit in UNIT_MODELS:
            with self.subTest(unit=unit.name):
                self.assertTrue(unit.producer.strip())
                self.assertTrue(unit.description.strip())

    def test_unit_lookup_groups_support_validator_checks(self) -> None:
        self.assertEqual(("SCV", "Marine", "Vulture"), TERRAN_UNIT_NAMES)
        self.assertEqual(("Zealot",), ENEMY_UNIT_NAMES)
        self.assertEqual(("SCV", "Marine", "Vulture"), TRAINABLE_UNIT_NAMES)
        self.assertEqual(("Marine", "Vulture", "Zealot"), COMBAT_UNIT_NAMES)
        self.assertEqual("Marine", UNIT_NAME_ALIASES["마린"])
        self.assertEqual("SCV", UNIT_NAME_ALIASES["일꾼"])
        self.assertEqual(
            {
                "Command Center": ("SCV",),
                "Barracks": ("Marine",),
                "Factory": ("Vulture",),
                "Gateway": ("Zealot",),
            },
            UNIT_NAMES_BY_PRODUCER,
        )

    def test_unit_lookup_predicates_accept_raw_validator_values(self) -> None:
        self.assertTrue(is_supported_unit_name("Marine"))
        self.assertTrue(is_supported_unit_name("marines"))
        self.assertTrue(is_supported_unit_name("마린"))
        self.assertTrue(is_terran_unit_name("SCV"))
        self.assertTrue(is_terran_unit_name("일꾼"))
        self.assertTrue(is_enemy_unit_name("Zealot"))
        self.assertTrue(is_trainable_unit_name("Vulture"))
        self.assertTrue(is_combat_unit_name("Marine"))
        self.assertTrue(is_unit_produced_by("Marine", "Barracks"))
        self.assertTrue(is_unit_produced_by("marines", "Barracks"))

        self.assertFalse(is_supported_unit_name("Medic"))
        self.assertFalse(is_supported_unit_name(1))
        self.assertFalse(is_terran_unit_name("Zealot"))
        self.assertFalse(is_enemy_unit_name("Marine"))
        self.assertFalse(is_trainable_unit_name("Zealot"))
        self.assertFalse(is_combat_unit_name("SCV"))
        self.assertFalse(is_unit_produced_by("Marine", "Factory"))
        self.assertFalse(is_unit_produced_by("Medic", "Barracks"))
        self.assertFalse(is_unit_produced_by("Marine", None))

    def test_unit_name_resolver_normalizes_validator_input(self) -> None:
        self.assertEqual("Marine", resolve_unit_name(" Marine "))
        self.assertEqual("Marine", resolve_unit_name("marines"))
        self.assertEqual("SCV", resolve_unit_name("에스시비"))
        self.assertEqual("Vulture", resolve_unit_name("벌처"))
        self.assertEqual("Zealot", resolve_unit_name("질럿"))
        self.assertIsNone(resolve_unit_name("Medic"))
        self.assertIsNone(resolve_unit_name(None))
        self.assertEqual(get_unit_model("Marine"), get_resolved_unit_model("마린"))

    def test_unit_lookup_helpers_return_canonical_models(self) -> None:
        self.assertEqual(("SCV", "Marine", "Vulture"), tuple(unit.name for unit in get_unit_models_by_faction("Terran")))
        self.assertEqual(("Zealot",), tuple(unit.name for unit in get_unit_models_by_faction("Protoss")))
        self.assertEqual(("SCV",), tuple(unit.name for unit in get_unit_models_by_role("worker")))
        self.assertEqual(("Marine",), get_unit_names_by_producer("Barracks"))
        self.assertEqual(("Marine",), tuple(unit.name for unit in get_unit_models_by_producer("Barracks")))

    def test_unit_model_serializes_nested_costs_and_stats(self) -> None:
        marine = get_unit_model("Marine").to_dict()

        self.assertEqual("Marine", marine["name"])
        self.assertEqual({"minerals": 50, "gas": 0, "supply": 1, "build_time_seconds": 24}, marine["cost"])
        self.assertEqual(
            {"hit_points": 40, "shields": 0, "armor": 0, "ground_damage": 6, "attack_range": 4},
            marine["stats"],
        )

    def test_effective_hit_points_include_shields(self) -> None:
        self.assertEqual(40, get_unit_model("Marine").stats.effective_hit_points)
        self.assertEqual(160, get_unit_model("Zealot").stats.effective_hit_points)

    def test_unit_cost_rejects_impossible_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "minerals must be a non-negative integer"):
            UnitCost(minerals=-1, gas=0, supply=1, build_time_seconds=20)
        with self.assertRaisesRegex(ValueError, "gas must be a non-negative integer"):
            UnitCost(minerals=50, gas=True, supply=1, build_time_seconds=20)
        with self.assertRaisesRegex(ValueError, "supply must be a positive integer"):
            UnitCost(minerals=50, gas=0, supply=0, build_time_seconds=20)
        with self.assertRaisesRegex(ValueError, "build_time_seconds must be a positive integer"):
            UnitCost(minerals=50, gas=0, supply=1, build_time_seconds=1.5)

    def test_unit_stats_reject_impossible_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "hit_points must be a positive integer"):
            UnitStats(hit_points=0, shields=0, armor=0, ground_damage=5, attack_range=1)
        with self.assertRaisesRegex(ValueError, "shields must be a non-negative integer"):
            UnitStats(hit_points=40, shields=-1, armor=0, ground_damage=5, attack_range=1)
        with self.assertRaisesRegex(ValueError, "armor must be a non-negative integer"):
            UnitStats(hit_points=40, shields=0, armor=True, ground_damage=5, attack_range=1)
        with self.assertRaisesRegex(ValueError, "ground_damage must be a non-negative integer"):
            UnitStats(hit_points=40, shields=0, armor=0, ground_damage=-1, attack_range=1)
        with self.assertRaisesRegex(ValueError, "attack_range must be a non-negative integer"):
            UnitStats(hit_points=40, shields=0, armor=0, ground_damage=5, attack_range=-1)

    def test_unit_model_rejects_empty_text_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "producer must be a non-empty string"):
            UnitModel(
                name="SCV",
                faction="Terran",
                role="worker",
                producer=" ",
                cost=UnitCost(minerals=50, gas=0, supply=1, build_time_seconds=20),
                stats=UnitStats(hit_points=60, shields=0, armor=0, ground_damage=5, attack_range=1),
                description="Worker",
            )
        with self.assertRaisesRegex(ValueError, "description must be a non-empty string"):
            UnitModel(
                name="SCV",
                faction="Terran",
                role="worker",
                producer="Command Center",
                cost=UnitCost(minerals=50, gas=0, supply=1, build_time_seconds=20),
                stats=UnitStats(hit_points=60, shields=0, armor=0, ground_damage=5, attack_range=1),
                description=" ",
            )

    def test_unknown_unit_lookup_is_rejected(self) -> None:
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft unit"):
            get_unit_model("Medic")

    def test_unknown_unit_group_lookup_is_rejected(self) -> None:
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft unit faction"):
            get_unit_models_by_faction("Zerg")
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft unit role"):
            get_unit_models_by_role("caster")
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft unit producer"):
            get_unit_names_by_producer("Starport")
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft unit producer"):
            get_unit_models_by_producer("Starport")


if __name__ == "__main__":
    unittest.main()
