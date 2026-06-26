"""Tests for MicroMachine map preflight checks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_map_pool import DEFAULT_MAP_POOL_PATH
from starcraft_commander.micromachine_preflight import (
    MicroMachineMapPreflightConfig,
    preflight_micromachine_map,
    write_preflight_failure_soak_report,
)


class MicroMachinePreflightTest(unittest.TestCase):
    def test_required_acropolis_passes_after_base97364_probe_and_soak_fix(self) -> None:
        report = preflight_micromachine_map(
            MicroMachineMapPreflightConfig(map_file="AcropolisLE.SC2Map")
        )

        self.assertTrue(report["ok"])
        self.assertEqual("passed", report["status"])
        self.assertEqual("required", report["classification"])
        self.assertEqual("qualified_baseline", report["manifest_status"])
        self.assertEqual([], report["failure_codes"])
        self.assertFalse(report["skip_runtime"])
        self.assertFalse(report["production_blocking"])

    def test_thunderbird_diagnostic_reports_geometry_and_placement_risk(self) -> None:
        report = preflight_micromachine_map(
            MicroMachineMapPreflightConfig(
                map_file="Ladder2019Season3/ThunderbirdLE.SC2Map",
                qualification_tier="diagnostic",
            )
        )

        self.assertFalse(report["ok"])
        self.assertEqual("diagnostic", report["classification"])
        self.assertEqual(
            ["geometry_risk", "placement_risk"],
            report["failure_codes"],
        )
        self.assertTrue(report["skip_runtime"])
        self.assertFalse(report["production_blocking"])
        blocker = report["blocker"]
        self.assertIsInstance(blocker, dict)
        self.assertEqual(
            "thunderbird_walloff_geometry_no_production_deadlock",
            blocker["code"],
        )
        self.assertEqual("no_production_deadlock", blocker["runtime_failure_code"])
        self.assertIn("ramp_walloff_build_placement", blocker["root_cause_area"])
        self.assertIn("Unusual ramp detected, tiles to block = 0", blocker["evidence_signatures"])
        self.assertIn("SOAK_MATRIX_QUALIFICATION_TIER=diagnostic", blocker["reproduction_command"])
        self.assertTrue(
            any("12000 frames" in item for item in blocker["promotion_criteria"])
        )

    def test_required_tier_rejects_diagnostic_map(self) -> None:
        report = preflight_micromachine_map(
            MicroMachineMapPreflightConfig(
                map_file="Ladder2019Season3/ThunderbirdLE.SC2Map",
                qualification_tier="production",
            )
        )

        self.assertFalse(report["ok"])
        self.assertIn("unsupported_map", report["failure_codes"])
        self.assertTrue(report["production_blocking"])

    def test_unknown_map_is_unsupported(self) -> None:
        report = preflight_micromachine_map(
            MicroMachineMapPreflightConfig(map_file="Unknown.SC2Map")
        )

        self.assertFalse(report["ok"])
        self.assertEqual("unknown", report["classification"])
        self.assertEqual(["unsupported_map"], report["failure_codes"])

    def test_configured_map_roots_detect_missing_and_present_maps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing = preflight_micromachine_map(
                MicroMachineMapPreflightConfig(
                    map_file="AcropolisLE.SC2Map",
                    map_roots=(root,),
                )
            )
            (root / "AcropolisLE.SC2Map").write_text("fake map fixture")
            present = preflight_micromachine_map(
                MicroMachineMapPreflightConfig(
                    map_file="AcropolisLE.SC2Map",
                    map_roots=(root,),
                )
            )

        self.assertFalse(missing["ok"])
        self.assertEqual(["missing_map"], missing["failure_codes"])
        self.assertTrue(present["ok"])
        self.assertEqual([], present["failure_codes"])

    def test_writes_soak_compatible_failure_report(self) -> None:
        report = preflight_micromachine_map(
            MicroMachineMapPreflightConfig(
                map_file="Ladder2019Season3/ThunderbirdLE.SC2Map",
                qualification_tier="diagnostic",
                manifest_path=DEFAULT_MAP_POOL_PATH,
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "soak_report.json"
            write_preflight_failure_soak_report(
                report,
                output,
                enemy_race="Zerg",
                enemy_difficulty=1,
                target_frame=12000,
                timeout_seconds=1200,
            )
            payload = json.loads(output.read_text())

        self.assertFalse(payload["ok"])
        self.assertEqual("preflight_failed", payload["termination_reason"])
        self.assertEqual(["geometry_risk", "placement_risk"], payload["failure_codes"])
        self.assertEqual(report, payload["preflight"])


if __name__ == "__main__":
    unittest.main()
