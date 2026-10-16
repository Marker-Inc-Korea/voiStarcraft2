import importlib.util
from pathlib import Path
import unittest

from starcraft_commander.policy_modulation import PolicyModulationVector


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "integrations/micromachine/scripts/qa30_scenario_command.py"
)
SPEC = importlib.util.spec_from_file_location("qa30_scenario_command", SCRIPT)
COMMANDS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMANDS)


class ReassignmentCommandTests(unittest.TestCase):
    def test_transfer_preserves_role_policies_and_source_minimum(self):
        for scenario, policy in (
            ("22-reassign-marines", "never"),
            ("23-reassign-tanks", "siege_mode"),
        ):
            with self.subTest(scenario=scenario):
                initial = PolicyModulationVector.from_mapping(
                    COMMANDS.lifecycle_payload(scenario, "initial")
                ).to_dict()["operations"]
                edited = PolicyModulationVector.from_mapping(
                    COMMANDS.lifecycle_payload(scenario, "reassign")
                ).to_dict()["operations"]
                source, destination = initial
                after_source, after_destination = edited
                self.assertEqual(2, source["composition_requirements"][0]["count"])
                self.assertEqual(1, destination["composition_requirements"][0]["count"])
                for operation in initial + edited:
                    self.assertEqual(policy, operation["unit_roles"][0]["ability_policy"])
                self.assertEqual(source["unit_roles"], after_destination["unit_roles"])
                remaining = after_source["composition_requirements"][0]["count"]
                self.assertGreaterEqual(remaining, source["scope"]["min_units"])
                self.assertGreaterEqual(remaining, source["tactical_task"]["min_units"])
                self.assertEqual(1, remaining)
                self.assertEqual(2, after_destination["composition_requirements"][0]["count"])
                self.assertEqual(2, after_source["generation"])
                self.assertEqual(2, after_destination["generation"])

    def test_other_operations_keep_exact_default_minimum(self):
        operation = COMMANDS.operation("example", "defend_with_units", "ramp", count=3)
        self.assertEqual(3, operation["scope"]["min_units"])
        self.assertEqual(3, operation["tactical_task"]["min_units"])


if __name__ == "__main__":
    unittest.main()
