import unittest

from toycraft_commander.resources import (
    RESOURCE_FIELD_BY_NAME,
    RESOURCE_FIELD_NAMES,
    RESOURCE_FIELDS,
    SUPPLY_FIELD_BY_NAME,
    SUPPLY_FIELD_NAMES,
    SUPPLY_FIELDS,
    ResourceState,
    SupplyState,
    get_available_gas,
    get_available_minerals,
    get_available_resource_amount,
    get_available_supply,
    get_missing_resources,
    get_missing_supply,
    get_required_resource_amount,
    has_available_resources,
    has_available_supply,
    has_resource_amount,
)
from toycraft_commander.structures import get_structure_model
from toycraft_commander.units import get_unit_model


class ResourceModelTest(unittest.TestCase):
    def test_resource_model_defines_exactly_minerals_and_gas(self) -> None:
        self.assertEqual(("minerals", "gas"), RESOURCE_FIELD_NAMES)
        self.assertEqual(("minerals", "gas"), tuple(field.name for field in RESOURCE_FIELDS))

    def test_each_resource_field_has_integer_type_and_zero_minimum(self) -> None:
        for name in RESOURCE_FIELD_NAMES:
            with self.subTest(resource=name):
                field = RESOURCE_FIELD_BY_NAME[name]
                self.assertEqual("int", field.type_name)
                self.assertEqual(0, field.minimum)
                self.assertTrue(field.description.strip())

    def test_resource_state_defaults_to_empty_bank(self) -> None:
        self.assertEqual({"minerals": 0, "gas": 0}, ResourceState().to_dict())

    def test_resource_state_accepts_non_negative_integers(self) -> None:
        self.assertEqual({"minerals": 50, "gas": 25}, ResourceState(minerals=50, gas=25).to_dict())

    def test_resource_state_rejects_impossible_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "minerals must be a non-negative integer"):
            ResourceState(minerals=-1)
        with self.assertRaisesRegex(ValueError, "gas must be a non-negative integer"):
            ResourceState(gas=1.5)
        with self.assertRaisesRegex(ValueError, "minerals must be a non-negative integer"):
            ResourceState(minerals=True)

    def test_supply_model_defines_used_supply_and_supply_capacity(self) -> None:
        self.assertEqual(("used_supply", "supply_capacity"), SUPPLY_FIELD_NAMES)
        self.assertEqual(
            ("used_supply", "supply_capacity"),
            tuple(field.name for field in SUPPLY_FIELDS),
        )

    def test_each_supply_field_has_integer_type_and_minimum(self) -> None:
        expected_minimums = {"used_supply": 0, "supply_capacity": 1}

        for name in SUPPLY_FIELD_NAMES:
            with self.subTest(supply=name):
                field = SUPPLY_FIELD_BY_NAME[name]
                self.assertEqual("int", field.type_name)
                self.assertEqual(expected_minimums[name], field.minimum)
                self.assertTrue(field.description.strip())

    def test_supply_state_defaults_to_starting_terran_capacity(self) -> None:
        self.assertEqual(
            {"used_supply": 0, "supply_capacity": 15},
            SupplyState().to_dict(),
        )

    def test_supply_state_accepts_valid_integer_supply(self) -> None:
        self.assertEqual(
            {"used_supply": 12, "supply_capacity": 23},
            SupplyState(used_supply=12, supply_capacity=23).to_dict(),
        )

    def test_supply_state_rejects_impossible_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "used_supply must be an integer greater than or equal to 0"):
            SupplyState(used_supply=-1)
        with self.assertRaisesRegex(ValueError, "supply_capacity must be an integer greater than or equal to 1"):
            SupplyState(supply_capacity=0)
        with self.assertRaisesRegex(ValueError, "used_supply must be an integer greater than or equal to 0"):
            SupplyState(used_supply=True)
        with self.assertRaisesRegex(ValueError, "used_supply cannot exceed supply_capacity"):
            SupplyState(used_supply=16, supply_capacity=15)

    def test_resource_lookup_helpers_return_available_amounts(self) -> None:
        resource_state = ResourceState(minerals=125, gas=50)

        self.assertEqual(125, get_available_resource_amount(resource_state, "minerals"))
        self.assertEqual(50, get_available_resource_amount(resource_state, "gas"))
        self.assertEqual(125, get_available_minerals(resource_state))
        self.assertEqual(50, get_available_gas(resource_state))
        self.assertTrue(has_resource_amount(resource_state, "minerals", 100))
        self.assertFalse(has_resource_amount(resource_state, "gas", 75))

    def test_resource_lookup_helpers_support_cost_objects_and_mappings(self) -> None:
        resource_state = ResourceState(minerals=150, gas=0)
        marine_cost = get_unit_model("Marine").cost
        depot_cost = get_structure_model("Supply Depot").cost

        self.assertEqual(50, get_required_resource_amount(marine_cost, "minerals"))
        self.assertEqual(0, get_required_resource_amount(marine_cost, "gas"))
        self.assertTrue(has_available_resources(resource_state, marine_cost))
        self.assertTrue(has_available_resources(resource_state, depot_cost))
        self.assertTrue(
            has_available_resources(
                resource_state,
                {"minerals": 150, "gas": 0},
            )
        )
        self.assertFalse(has_available_resources(resource_state, minerals=151))
        self.assertEqual(
            {"minerals": 50, "gas": 100},
            get_missing_resources(resource_state, get_structure_model("Factory").cost),
        )

    def test_supply_lookup_helpers_report_free_capacity_and_shortfall(self) -> None:
        supply_state = SupplyState(used_supply=12, supply_capacity=15)

        self.assertEqual(3, get_available_supply(supply_state))
        self.assertTrue(has_available_supply(supply_state, 3))
        self.assertFalse(has_available_supply(supply_state, 4))
        self.assertEqual(0, get_missing_supply(supply_state, 2))
        self.assertEqual(2, get_missing_supply(supply_state, 5))

    def test_resource_lookup_helpers_reject_invalid_validator_values(self) -> None:
        resource_state = ResourceState(minerals=125, gas=50)

        with self.assertRaisesRegex(KeyError, "Unsupported ToyCraft resource"):
            get_available_resource_amount(resource_state, "oil")
        with self.assertRaisesRegex(ValueError, "minerals must be a non-negative integer"):
            has_resource_amount(resource_state, "minerals", True)
        with self.assertRaisesRegex(ValueError, "gas must be a non-negative integer"):
            has_available_resources(resource_state, gas=-1)
        with self.assertRaisesRegex(ValueError, "required_supply must be a non-negative integer"):
            has_available_supply(SupplyState(), True)
        with self.assertRaisesRegex(ValueError, "required_supply must be a non-negative integer"):
            get_missing_supply(SupplyState(), -1)


if __name__ == "__main__":
    unittest.main()
