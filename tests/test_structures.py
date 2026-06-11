import unittest

from toycraft_commander.structures import (
    BUILDING_NAME_ALIASES,
    BUILDING_NAMES,
    STRUCTURE_MODEL_BY_NAME,
    STRUCTURE_MODELS,
    STRUCTURE_NAME_ALIASES,
    STRUCTURE_NAMES,
    STRUCTURE_NAMES_BY_CAPABILITY,
    SUPPLY_PROVIDER_STRUCTURE_NAMES,
    StructureCost,
    StructureModel,
    are_structure_prerequisites_satisfied,
    get_building_model,
    get_missing_structure_prerequisites,
    get_resolved_building_model,
    get_resolved_structure_model,
    get_structure_model,
    get_structure_models_by_capability,
    get_structure_names_by_capability,
    get_structure_prerequisites,
    is_structure_capable_of,
    is_supported_building_name,
    is_supported_structure_name,
    is_supply_provider_structure_name,
    resolve_building_name,
    resolve_structure_name,
)


class StructureModelTest(unittest.TestCase):
    def test_structure_inventory_defines_requested_phase_zero_models(self) -> None:
        self.assertEqual(("Barracks", "Factory", "Supply Depot", "Refinery"), STRUCTURE_NAMES)
        self.assertEqual(
            ("Barracks", "Factory", "Supply Depot", "Refinery"),
            tuple(structure.name for structure in STRUCTURE_MODELS),
        )
        self.assertEqual(set(STRUCTURE_NAMES), set(STRUCTURE_MODEL_BY_NAME))

    def test_structure_costs_match_toycraft_mvp_values(self) -> None:
        expected_costs = {
            "Barracks": {"minerals": 150, "gas": 0, "build_time_seconds": 60},
            "Factory": {"minerals": 200, "gas": 100, "build_time_seconds": 60},
            "Supply Depot": {"minerals": 100, "gas": 0, "build_time_seconds": 30},
            "Refinery": {"minerals": 100, "gas": 0, "build_time_seconds": 30},
        }

        for name, cost in expected_costs.items():
            with self.subTest(structure=name):
                self.assertEqual(cost, get_structure_model(name).cost.to_dict())

    def test_structure_capabilities_support_toycraft_commander_loop(self) -> None:
        expected_capabilities = {
            "Barracks": ("train_infantry", "production_prerequisite"),
            "Factory": ("train_vehicle", "tech_prerequisite"),
            "Supply Depot": ("increase_supply", "tech_prerequisite"),
            "Refinery": ("enable_gas_harvest",),
        }

        for name, capabilities in expected_capabilities.items():
            with self.subTest(structure=name):
                self.assertEqual(capabilities, get_structure_model(name).capabilities)

    def test_structure_prerequisites_and_supply_values_are_explicit(self) -> None:
        self.assertEqual(("Supply Depot",), get_structure_model("Barracks").prerequisites)
        self.assertEqual(("Barracks",), get_structure_model("Factory").prerequisites)
        self.assertEqual((), get_structure_model("Supply Depot").prerequisites)
        self.assertEqual((), get_structure_model("Refinery").prerequisites)
        self.assertEqual(8, get_structure_model("Supply Depot").supply_provided)

        for structure in STRUCTURE_MODELS:
            with self.subTest(structure=structure.name):
                self.assertEqual("Terran", structure.faction)
                self.assertGreaterEqual(structure.supply_provided, 0)
                self.assertTrue(structure.description.strip())

    def test_structure_lookup_groups_support_validator_checks(self) -> None:
        self.assertEqual(STRUCTURE_NAMES, BUILDING_NAMES)
        self.assertEqual(STRUCTURE_NAME_ALIASES, BUILDING_NAME_ALIASES)
        self.assertEqual(("Supply Depot",), SUPPLY_PROVIDER_STRUCTURE_NAMES)
        self.assertEqual(
            {
                "train_infantry": ("Barracks",),
                "train_vehicle": ("Factory",),
                "increase_supply": ("Supply Depot",),
                "enable_gas_harvest": ("Refinery",),
                "tech_prerequisite": ("Factory", "Supply Depot"),
                "production_prerequisite": ("Barracks",),
            },
            STRUCTURE_NAMES_BY_CAPABILITY,
        )

    def test_structure_lookup_predicates_accept_raw_validator_values(self) -> None:
        self.assertTrue(is_supported_structure_name("Barracks"))
        self.assertTrue(is_supported_structure_name(" barracks "))
        self.assertTrue(is_supported_structure_name("배럭"))
        self.assertTrue(is_supported_building_name("서플"))
        self.assertTrue(is_structure_capable_of("보급고", "increase_supply"))
        self.assertTrue(is_structure_capable_of("Factory", "train_vehicle"))
        self.assertTrue(is_supply_provider_structure_name("supply depot"))

        self.assertFalse(is_supported_structure_name("Bunker"))
        self.assertFalse(is_supported_building_name("Command Center"))
        self.assertFalse(is_supported_structure_name(1))
        self.assertFalse(is_structure_capable_of("Barracks", "train_vehicle"))
        self.assertFalse(is_structure_capable_of("Bunker", "production_prerequisite"))
        self.assertFalse(is_structure_capable_of("Barracks", None))
        self.assertFalse(is_supply_provider_structure_name("Barracks"))

    def test_structure_name_resolver_normalizes_validator_input(self) -> None:
        self.assertEqual("Barracks", resolve_structure_name(" Barracks "))
        self.assertEqual("Barracks", resolve_structure_name("병영"))
        self.assertEqual("Factory", resolve_structure_name("factories"))
        self.assertEqual("Supply Depot", resolve_structure_name("서플라이 디포"))
        self.assertEqual("Refinery", resolve_building_name("정제소"))
        self.assertIsNone(resolve_structure_name("Bunker"))
        self.assertIsNone(resolve_structure_name(None))
        self.assertEqual(get_structure_model("Refinery"), get_resolved_structure_model("리파이너리"))
        self.assertEqual(get_structure_model("Supply Depot"), get_resolved_building_model("depot"))

    def test_structure_lookup_helpers_return_canonical_models(self) -> None:
        self.assertEqual(get_structure_model("Barracks"), get_building_model("Barracks"))
        self.assertEqual(("Barracks",), get_structure_names_by_capability("train_infantry"))
        self.assertEqual(
            ("Factory",),
            tuple(
                structure.name
                for structure in get_structure_models_by_capability("train_vehicle")
            ),
        )
        self.assertEqual(("Supply Depot",), get_structure_prerequisites("배럭"))

        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft structure capability"):
            get_structure_names_by_capability("cloak")
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft structure"):
            get_resolved_structure_model("Bunker")

    def test_structure_prerequisite_helpers_report_missing_requirements(self) -> None:
        self.assertTrue(
            are_structure_prerequisites_satisfied("Barracks", ("Supply Depot",))
        )
        self.assertTrue(
            are_structure_prerequisites_satisfied("팩토리", ("서플", "배럭"))
        )
        self.assertTrue(are_structure_prerequisites_satisfied("Refinery", ()))
        self.assertFalse(are_structure_prerequisites_satisfied("Factory", ("Supply Depot",)))
        self.assertEqual(
            ("Barracks",),
            get_missing_structure_prerequisites("Factory", ("Supply Depot", "Bunker")),
        )
        self.assertEqual(
            ("Supply Depot",),
            get_missing_structure_prerequisites("Barracks", None),
        )

    def test_structure_model_serializes_nested_costs(self) -> None:
        barracks = get_structure_model("Barracks").to_dict()

        self.assertEqual("Barracks", barracks["name"])
        self.assertEqual({"minerals": 150, "gas": 0, "build_time_seconds": 60}, barracks["cost"])
        self.assertEqual(("train_infantry", "production_prerequisite"), barracks["capabilities"])
        self.assertEqual(("Supply Depot",), barracks["prerequisites"])

    def test_structure_cost_rejects_impossible_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "minerals must be a non-negative integer"):
            StructureCost(minerals=-1, gas=0, build_time_seconds=30)
        with self.assertRaisesRegex(ValueError, "gas must be a non-negative integer"):
            StructureCost(minerals=100, gas=True, build_time_seconds=30)
        with self.assertRaisesRegex(ValueError, "build_time_seconds must be a positive integer"):
            StructureCost(minerals=100, gas=0, build_time_seconds=0)

    def test_structure_model_rejects_invalid_metadata(self) -> None:
        cost = StructureCost(minerals=100, gas=0, build_time_seconds=30)

        with self.assertRaisesRegex(ValueError, "capabilities must include at least one capability"):
            StructureModel(
                name="Supply Depot",
                faction="Terran",
                cost=cost,
                capabilities=(),
                prerequisites=(),
                supply_provided=8,
                description="Supply structure",
            )
        with self.assertRaisesRegex(ValueError, "capabilities must not contain duplicates"):
            StructureModel(
                name="Supply Depot",
                faction="Terran",
                cost=cost,
                capabilities=("increase_supply", "increase_supply"),
                prerequisites=(),
                supply_provided=8,
                description="Supply structure",
            )
        with self.assertRaisesRegex(ValueError, "supply_provided must be a non-negative integer"):
            StructureModel(
                name="Supply Depot",
                faction="Terran",
                cost=cost,
                capabilities=("increase_supply",),
                prerequisites=(),
                supply_provided=True,
                description="Supply structure",
            )
        with self.assertRaisesRegex(ValueError, "description must be a non-empty string"):
            StructureModel(
                name="Supply Depot",
                faction="Terran",
                cost=cost,
                capabilities=("increase_supply",),
                prerequisites=(),
                supply_provided=8,
                description=" ",
            )

    def test_unknown_structure_lookup_is_rejected(self) -> None:
        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft structure"):
            get_structure_model("Bunker")


if __name__ == "__main__":
    unittest.main()
