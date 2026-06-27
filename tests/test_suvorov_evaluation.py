"""Tests for the Suvorov backend evaluation artifacts."""

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SUVOROV_DIR = REPO_ROOT / "integrations" / "suvorov"
MANIFEST = SUVOROV_DIR / "HOOK_MANIFEST.json"
README = SUVOROV_DIR / "README.md"
REPORT = REPO_ROOT / "docs" / "suvorov-backend-evaluation.md"


class SuvorovEvaluationTest(unittest.TestCase):
    def test_manifest_records_build_runtime_identity_and_decision(self) -> None:
        manifest = json.loads(MANIFEST.read_text())

        self.assertEqual(
            "08a295d71f545d04b047a70ac4e1d7413afed2a4",
            manifest["verified_upstream_commit"],
        )
        self.assertEqual(
            "96d15bab61ec58f58df98af33bfca9199f176cc0",
            manifest["verified_submodules"]["contrib/cpp-sc2"],
        )
        self.assertEqual("passed", manifest["build_evidence"]["build_result"])
        self.assertEqual(
            "passed_full_game_to_Game_over",
            manifest["runtime_evidence"]["alias_launch_result"],
        )
        self.assertEqual("Terran", manifest["runtime_evidence"]["observed_race"])
        self.assertEqual(8548, manifest["runtime_evidence"]["terminal_frame"])
        self.assertEqual("do_not_replace_micromachine", manifest["production_decision"])
        self.assertIn(
            "WaitJoinGame finished successfully.",
            manifest["runtime_evidence"]["runtime_stdout_terms"],
        )
        self.assertNotIn(
            "WaitJoinGame finished successfully.",
            manifest["runtime_evidence"]["history_log_terms"],
        )
        self.assertEqual(
            "/private/tmp/voi-suvorov-probe/suvorov-bot/history.log",
            manifest["runtime_evidence"]["history_log"],
        )

    def test_manifest_maps_policy_domains_to_suvorov_code_seams(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        hooks = manifest["manager_hooks"]
        domains = {hook["domain"] for hook in hooks}

        self.assertEqual(
            {
                "strategy",
                "production",
                "economy",
                "combat",
                "combat_scope",
                "supply",
                "protoss_macro",
            },
            domains,
        )
        required_sources = {
            "src/Dispatcher.cpp",
            "src/Builder.cpp",
            "src/plugins/Miner.cpp",
            "src/Hub.cpp",
            "src/plugins/QuarterMaster.cpp",
            "src/strategies/Strategy.cpp",
            "src/plugins/WarpSmith.cpp",
        }
        self.assertEqual(required_sources, {hook["source_path"] for hook in hooks})
        for hook in hooks:
            with self.subTest(domain=hook["domain"]):
                self.assertTrue(hook["keys"])
                self.assertTrue(hook["function"])
                self.assertTrue(hook["intended_effect"])

    def test_manifest_preserves_bounded_modulation_safety_contract(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        contract = manifest["bounded_modulation_contract"]

        self.assertIn("PolicyModulationVector", contract["reusable_python_contracts"])
        self.assertIn("raw-control rejection", contract["reusable_python_contracts"])
        self.assertIn("SuvorovModulationBackend", contract["required_new_adapter"])
        for forbidden in (
            "raw SC2 action control",
            "unit_tag targeting from providers",
            "direct attack_move commands",
            "python-sc2 method exposure",
            "s2client-api method exposure",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, contract["forbidden_controls"])

    def test_report_and_readme_state_limits_without_overclaiming_strength(self) -> None:
        report = REPORT.read_text()
        readme = README.read_text()
        combined = report + "\n" + readme

        required_terms = (
            "conditional secondary-backend candidate",
            "not a MicroMachine replacement",
            "Keep MicroMachine as the production default",
            "It is not currently a production replacement for MicroMachine",
            "Observed Terran run lost to CheatInsane Random Rush",
            "source-confirmed but not locally smoke-confirmed",
            "CreateParticipant(sc2::Race::Random",
            "PolicyModulationVector",
            "raw SC2 actions",
            "latest_telemetry.json",
            "active_modulation_ids",
            "consumed_axes",
            "Strategy::OnStep()",
            "m_attack_limit",
            "Suvorov managers remain authoritative over real game actions",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, combined)

    def test_race_support_mapping_separates_observed_from_source_confirmed(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        races = {
            race["race"]: race for race in manifest["race_support_source_mapping"]
        }

        self.assertTrue(races["Terran"]["runtime_observed"])
        self.assertFalse(races["Protoss"]["runtime_observed"])
        self.assertFalse(races["Zerg"]["runtime_observed"])
        self.assertEqual("MarinePush", races["Terran"]["strategy"])
        self.assertEqual("ChargelotPush", races["Protoss"]["strategy"])
        self.assertEqual("ZerglingFlood", races["Zerg"]["strategy"])

    def test_follow_up_gates_require_race_matrix_blackboard_and_telemetry(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        gates = "\n".join(manifest["minimum_follow_up_before_backend_adoption"])

        for term in (
            "race-selectable",
            "blackboard reader",
            "latest_telemetry.json",
            "Terran, Protoss, and Zerg smoke",
            "comparative MicroMachine vs Suvorov matrix",
        ):
            with self.subTest(term=term):
                self.assertIn(term, gates)


if __name__ == "__main__":
    unittest.main()
