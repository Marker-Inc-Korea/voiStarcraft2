"""Tests for the final MicroMachine production release gate."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from starcraft_commander.micromachine_release_gate import (
    MicroMachineReleaseGateConfig,
    build_release_gate_report,
    render_release_gate_markdown,
    write_release_gate_outputs,
)


class MicroMachineReleaseGateTest(unittest.TestCase):
    def test_release_gate_passes_complete_production_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_green_evidence(root, build_identity="build-a")

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )
            markdown = render_release_gate_markdown(report)

            self.assertTrue(report["ok"])
            self.assertEqual("passed", report["status"])
            self.assertEqual([], report["blockers"])
            self.assertEqual("build-a", report["required_build_identity"])
            self.assertEqual(1, len(report["matrix_reports"]))
            self.assertIn("User QA Remaining", markdown)
            self.assertIn("bounded policy modulation", markdown)

    def test_release_gate_blocks_missing_required_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(root / "run-green", build_identity="build-a")

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    max_evidence_age_seconds=None,
                )
            )

            codes = {blocker["code"] for blocker in report["blockers"]}
            self.assertFalse(report["ok"])
            self.assertIn("missing_build_identity_report", codes)
            self.assertIn("missing_unit_evidence", codes)
            self.assertIn("missing_triage_report", codes)

    def test_release_gate_blocks_failed_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_green_evidence(root, build_identity="build-a")
            self.write_matrix_report(
                root / "run-failed",
                build_identity="build-a",
                ok=False,
                status="failed",
                failed=1,
                case_ok=False,
                failure_codes=["no_production_deadlock"],
            )
            os.utime(root / "run-green" / "matrix_report.json", (100.0, 100.0))
            os.utime(root / "run-failed" / "matrix_report.json", (200.0, 200.0))

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )

            codes = {blocker["code"] for blocker in report["blockers"]}
            self.assertFalse(report["ok"])
            self.assertIn("matrix_report_failed", codes)
            self.assertIn("production_signoff_failed_required_case", codes)

    def test_release_gate_blocks_disabled_soak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_supporting_evidence(root, build_identity="build-a")
            run = root / "run-disabled"
            run.mkdir()
            (run / "matrix_report.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "status": "disabled",
                        "enabled": False,
                        "qualification_tier": "production",
                        "allow_failures": False,
                        "build_identity": "build-a",
                        "build_identity_ok": True,
                        "case_count": 0,
                        "passed": 0,
                        "failed": 0,
                        "cases": [],
                    },
                    sort_keys=True,
                )
                + "\n"
            )

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )

            codes = {blocker["code"] for blocker in report["blockers"]}
            self.assertFalse(report["ok"])
            self.assertIn("no_eligible_matrix_report", codes)
            self.assertIn("production_signoff_no_eligible_production_runs", codes)

    def test_release_gate_blocks_diagnostic_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_supporting_evidence(root, build_identity="build-a")
            self.write_matrix_report(
                root / "run-diagnostic",
                build_identity="build-a",
                qualification_tier="diagnostic",
                allow_failures=True,
            )

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )

            codes = {blocker["code"] for blocker in report["blockers"]}
            self.assertFalse(report["ok"])
            self.assertIn("no_eligible_matrix_report", codes)
            self.assertIn("production_signoff_no_eligible_production_runs", codes)

    def test_release_gate_blocks_build_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_green_evidence(root, build_identity="build-a")
            paths["build_identity"].write_text(
                json.dumps(
                    {
                        "ok": True,
                        "identity": "build-b",
                        "failures": [],
                        "expected": {},
                        "observed": {},
                        "checksums": {},
                    },
                    sort_keys=True,
                )
                + "\n"
            )

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )

            codes = {blocker["code"] for blocker in report["blockers"]}
            self.assertFalse(report["ok"])
            self.assertIn("matrix_build_mismatch", codes)
            self.assertIn("production_signoff_build_mismatch", codes)

    def test_release_gate_blocks_stale_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_green_evidence(root, build_identity="build-a")
            dashboard = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )["history_dashboard"]
            dashboard_path = root / "dashboard.json"
            dashboard_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "run_count": 1,
                        "case_count": 3,
                        "production_signoff": dashboard["production_signoff"],
                        "runs": [
                            {
                                "run_id": "run-green",
                                "report": str(root / "run-green" / "matrix_report.json"),
                                "ok": True,
                                "status": "passed",
                                "case_count": 3,
                                "passed": 3,
                                "failed": 0,
                                "qualification_tier": "production",
                                "allow_failures": False,
                                "build_identity": "build-a",
                            }
                        ],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            old = time.time() - 10
            os.utime(dashboard_path, (old, old))

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_dashboard=dashboard_path,
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=1,
                )
            )

            self.assertFalse(report["ok"])
            self.assertIn(
                "stale_evidence",
                {blocker["code"] for blocker in report["blockers"]},
            )

    def test_release_gate_blocks_stale_matrix_report_from_history_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_green_evidence(root, build_identity="build-a")
            old = time.time() - 10
            matrix_path = root / "run-green" / "matrix_report.json"
            os.utime(matrix_path, (old, old))

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=1,
                )
            )

            stale_blockers = [
                blocker
                for blocker in report["blockers"]
                if blocker["code"] == "stale_evidence"
            ]
            self.assertFalse(report["ok"])
            self.assertTrue(
                any(blocker["path"] == str(matrix_path) for blocker in stale_blockers)
            )

    def test_release_gate_recomputes_coverage_from_matrix_report_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_supporting_evidence(root, build_identity="build-a")
            matrix_dir = root / "run-forged-dashboard"
            matrix_dir.mkdir()
            matrix_path = matrix_dir / "matrix_report.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "enabled": True,
                        "target_frame": 12_000,
                        "timeout_seconds": 1_200,
                        "qualification_tier": "production",
                        "allow_failures": False,
                        "strategy_profiles": ["default_defensive_to_aggressive"],
                        "build_identity": "build-a",
                        "build_identity_ok": True,
                        "build_identity_failure_codes": [],
                        "case_count": 1,
                        "passed": 1,
                        "failed": 0,
                        "cases": [
                            self.matrix_case(
                                "Zerg",
                                case_ok=True,
                                failure_codes=[],
                            )
                        ],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            dashboard_path = root / "forged_dashboard.json"
            dashboard_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "run_count": 1,
                        "case_count": 3,
                        "production_signoff": self.green_signoff(),
                        "runs": [
                            {
                                "run_id": "run-forged-dashboard",
                                "report": str(matrix_path),
                                "ok": True,
                                "status": "passed",
                                "case_count": 3,
                                "passed": 3,
                                "failed": 0,
                                "qualification_tier": "production",
                                "allow_failures": False,
                                "build_identity": "build-a",
                            }
                        ],
                    },
                    sort_keys=True,
                )
                + "\n"
            )

            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_dashboard=dashboard_path,
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )

            codes = {blocker["code"] for blocker in report["blockers"]}
            self.assertFalse(report["ok"])
            self.assertIn("matrix_coverage_incomplete", codes)
            self.assertEqual(3, report["matrix_coverage"]["required_count"])
            self.assertEqual(1, report["matrix_coverage"]["observed_count"])
            self.assertEqual(2, report["matrix_coverage"]["missing_count"])

    def test_release_gate_outputs_and_cli_exit_codes_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_green_evidence(root, build_identity="build-a")
            output_json = root / "release_gate.json"
            output_markdown = root / "release_gate.md"
            report = build_release_gate_report(
                MicroMachineReleaseGateConfig(
                    history_roots=(root,),
                    build_identity_report=paths["build_identity"],
                    unit_evidence=paths["unit"],
                    triage_reports=(paths["triage"],),
                    max_evidence_age_seconds=None,
                )
            )

            write_release_gate_outputs(
                report,
                output_json=output_json,
                output_markdown=output_markdown,
            )

            self.assertEqual(report, json.loads(output_json.read_text()))
            self.assertIn("MicroMachine Production Release Gate", output_markdown.read_text())
            completed = subprocess.run(
                [
                    "python",
                    "-m",
                    "starcraft_commander.micromachine_release_gate",
                    "--history-root",
                    str(root),
                    "--build-identity-report",
                    str(paths["build_identity"]),
                    "--unit-evidence",
                    str(paths["unit"]),
                    "--triage-report",
                    str(paths["triage"]),
                    "--no-evidence-age-limit",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn('"ok": true', completed.stdout)

    def write_green_evidence(
        self,
        root: Path,
        *,
        build_identity: str,
    ) -> dict[str, Path]:
        paths = self.write_supporting_evidence(root, build_identity=build_identity)
        self.write_matrix_report(root / "run-green", build_identity=build_identity)
        return paths

    def write_supporting_evidence(
        self,
        root: Path,
        *,
        build_identity: str,
    ) -> dict[str, Path]:
        build_path = root / "build_identity.json"
        unit_path = root / "unit_evidence.json"
        triage_path = root / "triage_report.json"
        build_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "identity": build_identity,
                    "failures": [],
                    "expected": {},
                    "observed": {},
                    "checksums": {},
                },
                sort_keys=True,
            )
            + "\n"
        )
        unit_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "status": "passed",
                    "command": "uv run pytest -q",
                    "summary": "unit-contracts passed",
                },
                sort_keys=True,
            )
            + "\n"
        )
        triage_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "status": "passed",
                    "case_count": 3,
                    "failed_case_count": 0,
                    "categories": [],
                    "ranked_failures": [],
                },
                sort_keys=True,
            )
            + "\n"
        )
        return {"build_identity": build_path, "unit": unit_path, "triage": triage_path}

    def write_matrix_report(
        self,
        run_dir: Path,
        *,
        build_identity: str,
        ok: bool = True,
        status: str = "passed",
        failed: int = 0,
        case_ok: bool = True,
        failure_codes: list[str] | None = None,
        qualification_tier: str = "production",
        allow_failures: bool = False,
    ) -> None:
        run_dir.mkdir()
        cases = [
            self.matrix_case("Zerg", case_ok=case_ok, failure_codes=failure_codes),
            self.matrix_case("Protoss", case_ok=case_ok, failure_codes=failure_codes),
            self.matrix_case("Terran", case_ok=case_ok, failure_codes=failure_codes),
        ]
        failed_count = failed or sum(1 for case in cases if case["ok"] is not True)
        payload = {
            "ok": ok,
            "status": status,
            "enabled": True,
            "target_frame": 12_000,
            "timeout_seconds": 1_200,
            "qualification_tier": qualification_tier,
            "allow_failures": allow_failures,
            "strategy_profiles": ["default_defensive_to_aggressive"],
            "build_identity": build_identity,
            "build_identity_ok": True,
            "build_identity_failure_codes": [],
            "case_count": len(cases),
            "passed": len(cases) - failed_count,
            "failed": failed_count,
            "cases": cases,
        }
        (run_dir / "matrix_report.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n"
        )

    def matrix_case(
        self,
        race: str,
        *,
        case_ok: bool,
        failure_codes: list[str] | None,
    ) -> dict[str, object]:
        return {
            "case_id": f"AcropolisLE.SC2Map-{race}-d1",
            "ok": case_ok,
            "status": "passed" if case_ok else "failed",
            "map_file": "AcropolisLE.SC2Map",
            "enemy_race": race,
            "enemy_difficulty": 1,
            "strategy_profiles": ["default_defensive_to_aggressive"],
            "failure_codes": failure_codes or [],
        }

    def green_signoff(self) -> dict[str, object]:
        return {
            "ok": True,
            "status": "passed",
            "signoff_tier": "production",
            "eligible_run_count": 1,
            "excluded_run_count": 0,
            "eligible_runs": ["run-forged-dashboard"],
            "excluded_runs": [],
            "required": {
                "map_files": ["AcropolisLE.SC2Map"],
                "enemy_races": ["Zerg", "Protoss", "Terran"],
                "enemy_difficulties": [1],
                "strategy_profiles": ["default_defensive_to_aggressive"],
            },
            "coverage": {
                "required_count": 3,
                "observed_count": 3,
                "missing_count": 0,
                "missing": [],
            },
            "build_identity": {
                "required": "build-a",
                "observed": ["build-a"],
            },
            "blockers": [],
        }


if __name__ == "__main__":
    unittest.main()
