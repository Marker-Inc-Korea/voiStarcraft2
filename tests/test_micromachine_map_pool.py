"""Tests for the MicroMachine production map-pool contract."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_map_pool import (
    DEFAULT_MAP_POOL_PATH,
    load_micromachine_map_pool,
    parse_micromachine_map_pool,
)


class MicroMachineMapPoolTest(unittest.TestCase):
    def test_default_manifest_defines_strict_production_pool(self) -> None:
        pool = load_micromachine_map_pool()

        self.assertEqual(48, pool.parent_issue)
        self.assertEqual("production", pool.default_tier)
        self.assertTrue(pool.qualification_requires_failed_zero)
        self.assertFalse(pool.production_allows_failures)

        production = pool.to_summary("production")
        self.assertEqual(["AcropolisLE.SC2Map"], production["map_files"])
        self.assertEqual(["Zerg", "Protoss", "Terran"], production["enemy_races"])
        self.assertEqual([1], production["enemy_difficulties"])
        self.assertFalse(production["allow_failures"])
        self.assertEqual([], production["maps"][0]["preflight_risk_codes"])
        self.assertEqual(2, production["maps"][0]["expected_start_locations"])

        diagnostic = pool.to_summary("diagnostic")
        self.assertEqual(
            ["Ladder2019Season3/ThunderbirdLE.SC2Map"],
            diagnostic["map_files"],
        )
        self.assertTrue(diagnostic["allow_failures"])
        self.assertEqual(
            ["geometry_risk", "placement_risk"],
            diagnostic["maps"][0]["preflight_risk_codes"],
        )
        blocker = diagnostic["maps"][0]["blocker"]
        self.assertIsInstance(blocker, dict)
        self.assertEqual(
            "thunderbird_walloff_geometry_no_production_deadlock",
            blocker["code"],
        )
        self.assertEqual("no_production_deadlock", blocker["runtime_failure_code"])
        self.assertIn(
            "/private/tmp/voi-mm-soak/issue-10-11-final-thunderbird-v2/soak_report.json",
            blocker["artifact_path"],
        )
        self.assertIn("ramp_detection", blocker["root_cause_candidates"])
        self.assertIn("no_production_deadlock", blocker["evidence_signatures"])
        excluded = [entry for entry in pool.maps if entry.classification == "excluded"]
        self.assertEqual(["Custom/UnknownOrUnvetted.SC2Map"], [entry.map_file for entry in excluded])

        extended = pool.to_summary("extended")
        self.assertEqual(["AcropolisLE.SC2Map"], extended["map_files"])
        self.assertEqual(["Zerg", "Protoss", "Terran"], extended["enemy_races"])
        self.assertEqual([1, 2], extended["enemy_difficulties"])
        self.assertFalse(extended["allow_failures"])

    def test_cli_prints_shell_friendly_matrix_defaults(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                "-m",
                "starcraft_commander.micromachine_map_pool",
                "--manifest",
                str(DEFAULT_MAP_POOL_PATH),
                "--tier",
                "production",
                "--field",
                "enemy_races",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual("Zerg Protoss Terran", completed.stdout.strip())

    def test_manifest_rejects_production_allow_failures(self) -> None:
        payload = self._default_payload()
        payload["contract"]["production_allows_failures"] = True

        with self.assertRaisesRegex(ValueError, "cannot allow failures"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_unsafe_production_classifications(self) -> None:
        payload = self._default_payload()
        payload["tiers"]["production"]["map_classifications"] = ["required", "diagnostic"]

        with self.assertRaisesRegex(ValueError, "production tier must include only required"):
            parse_micromachine_map_pool(payload)

        payload = self._default_payload()
        payload["tiers"]["production"]["map_classifications"] = ["excluded"]

        with self.assertRaisesRegex(ValueError, "production tier must include only required"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_non_production_default_tier(self) -> None:
        payload = self._default_payload()
        payload["contract"]["default_tier"] = "diagnostic"

        with self.assertRaisesRegex(ValueError, "default_tier must be production"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_unknown_race_and_difficulty(self) -> None:
        payload = self._default_payload()
        payload["tiers"]["production"]["enemy_races"] = ["Zerg", "Orc"]

        with self.assertRaisesRegex(ValueError, "unsupported enemy race"):
            parse_micromachine_map_pool(payload)

        payload = self._default_payload()
        payload["tiers"]["production"]["enemy_difficulties"] = [0]

        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_missing_required_map(self) -> None:
        payload = self._default_payload()
        for item in payload["maps"]:
            item["classification"] = "diagnostic"

        with self.assertRaisesRegex(ValueError, "at least one required map"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_missing_diagnostic_or_excluded_map(self) -> None:
        payload = self._default_payload()
        payload["maps"] = [
            item for item in payload["maps"] if item["classification"] != "diagnostic"
        ]

        with self.assertRaisesRegex(ValueError, "at least one diagnostic map"):
            parse_micromachine_map_pool(payload)

        payload = self._default_payload()
        payload["maps"] = [
            item for item in payload["maps"] if item["classification"] != "excluded"
        ]

        with self.assertRaisesRegex(ValueError, "at least one excluded map"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_duplicate_map_file(self) -> None:
        payload = self._default_payload()
        payload["maps"].append(copy.deepcopy(payload["maps"][0]))

        with self.assertRaisesRegex(ValueError, "duplicate map_file"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_malformed_preflight_metadata(self) -> None:
        payload = self._default_payload()
        payload["maps"][0]["preflight"]["expected_start_locations"] = 0

        with self.assertRaisesRegex(ValueError, "must be positive"):
            parse_micromachine_map_pool(payload)

        payload = self._default_payload()
        payload["maps"][0]["preflight"]["risk_codes"] = "geometry_risk"

        with self.assertRaisesRegex(ValueError, "must be a list"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_malformed_blocker_metadata(self) -> None:
        payload = self._default_payload()
        payload["maps"][1]["blocker"]["code"] = ""

        with self.assertRaisesRegex(ValueError, "code must be a non-empty string"):
            parse_micromachine_map_pool(payload)

        payload = self._default_payload()
        payload["maps"][1]["blocker"]["promotion_criteria"] = []

        with self.assertRaisesRegex(ValueError, "promotion_criteria must be a non-empty list"):
            parse_micromachine_map_pool(payload)

        payload = self._default_payload()
        payload["maps"][1]["blocker"] = "no_production_deadlock"

        with self.assertRaisesRegex(ValueError, "blocker .* must be an object"):
            parse_micromachine_map_pool(payload)

    def test_manifest_rejects_blocker_in_required_pool(self) -> None:
        payload = self._default_payload()
        payload["maps"][1]["classification"] = "required"

        with self.assertRaisesRegex(ValueError, "active blocker metadata must remain diagnostic"):
            parse_micromachine_map_pool(payload)

    def test_loads_explicit_manifest_path(self) -> None:
        payload = self._default_payload()
        payload["tiers"]["production"]["enemy_difficulties"] = [1, 2]

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "pool.json"
            manifest.write_text(json.dumps(payload))

            pool = load_micromachine_map_pool(manifest)

        self.assertEqual((1, 2), pool.tier("production").enemy_difficulties)

    def _default_payload(self) -> dict[str, object]:
        return copy.deepcopy(json.loads(DEFAULT_MAP_POOL_PATH.read_text()))


if __name__ == "__main__":
    unittest.main()
