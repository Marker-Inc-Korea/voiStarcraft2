"""Tests for MicroMachine matrix failure triage summaries."""

import json
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_triage import (
    render_triage_markdown,
    triage_matrix_report,
    write_triage_outputs,
)


class MicroMachineTriageTest(unittest.TestCase):
    def test_triage_ranks_production_failures_and_preserves_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "01-AcropolisLE-SC2Map-Zerg-d1"
            case_dir.mkdir()
            (case_dir / "micromachine.log").write_text(
                "9000: build command type=TERRAN_BARRACKS\n"
                "no_production_deadlock\n"
                "Invalid setup detected\n"
            )
            matrix = root / "matrix_report.json"
            matrix.write_text(
                json.dumps(
                    {
                        "qualification_tier": "production",
                        "allow_failures": False,
                        "case_count": 2,
                        "passed": 1,
                        "failed": 1,
                        "cases": [
                            {
                                "case_id": case_dir.name,
                                "case_dir": str(case_dir),
                                "report": str(case_dir / "soak_report.json"),
                                "preflight_report": str(case_dir / "preflight_report.json"),
                                "ok": False,
                                "failure_phase": "production_runtime_failure",
                                "preflight_status": "passed",
                                "preflight_ok": True,
                                "map_file": "AcropolisLE.SC2Map",
                                "enemy_race": "Zerg",
                                "enemy_difficulty": 1,
                                "failure_codes": ["no_production_deadlock"],
                                "failures": [{"code": "no_production_deadlock"}],
                                "artifact_manifest": {"bot_log": "micromachine.log"},
                            },
                            {
                                "case_id": "02-AcropolisLE-SC2Map-Protoss-d1",
                                "ok": True,
                                "failure_codes": [],
                            },
                        ],
                    }
                )
                + "\n"
            )

            triage = triage_matrix_report(matrix)
            markdown = render_triage_markdown(triage)

        self.assertFalse(triage["ok"])
        self.assertEqual(1, triage["failed_case_count"])
        failure = triage["ranked_failures"][0]
        self.assertTrue(failure["production_impact"])
        self.assertEqual("no_production_deadlock", failure["category"])
        self.assertEqual("ramp_walloff_build_placement", failure["owner_hint"])
        self.assertIn("Invalid setup detected", failure["log_signatures"])
        self.assertIn("micromachine.log", failure["artifacts"]["bot_log"])
        self.assertIn(
            'SOAK_MATRIX_MAP_FILES="AcropolisLE.SC2Map"',
            failure["reproduction_command"],
        )
        self.assertIn("MicroMachine Failure Triage", markdown)
        self.assertIn("no_production_deadlock", markdown)

    def test_preflight_blocker_reproduction_command_wins(self) -> None:
        report = {
            "qualification_tier": "diagnostic",
            "allow_failures": True,
            "cases": [
                {
                    "case_id": "01-ThunderbirdLE-SC2Map-Zerg-d1",
                    "ok": False,
                    "failure_phase": "preflight_failure",
                    "failure_codes": ["geometry_risk", "placement_risk"],
                    "preflight": {
                        "status": "failed",
                        "ok": False,
                        "blocker": {
                            "reproduction_command": (
                                "SOAK_MATRIX_RUN_ID=diagnostic-thunderbird-001 "
                                "integrations/micromachine/scripts/soak_matrix_macos_local.sh"
                            ),
                            "evidence_signatures": [
                                "Unusual ramp detected, tiles to block = 0"
                            ],
                        },
                    },
                }
            ],
        }

        triage = triage_matrix_report(report)
        failure = triage["ranked_failures"][0]

        self.assertEqual("geometry_preflight", failure["category"])
        self.assertEqual("map_geometry", failure["owner_hint"])
        self.assertIn("diagnostic-thunderbird-001", failure["reproduction_command"])
        self.assertEqual(
            ["Unusual ramp detected, tiles to block = 0"],
            failure["log_signatures"],
        )

    def test_write_outputs_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            triage = {
                "status": "passed",
                "ok": True,
                "qualification_tier": "production",
                "case_count": 1,
                "failed_case_count": 0,
                "ranked_failures": [],
            }
            output_json = root / "triage.json"
            output_md = root / "triage.md"

            write_triage_outputs(
                triage,
                output_json=output_json,
                output_markdown=output_md,
            )

            self.assertEqual(triage, json.loads(output_json.read_text()))
            self.assertIn("No failed cases", output_md.read_text())


if __name__ == "__main__":
    unittest.main()
