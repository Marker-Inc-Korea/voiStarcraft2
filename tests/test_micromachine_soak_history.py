"""Tests for MicroMachine soak matrix history aggregation."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_soak_history import (
    SoakHistoryConfig,
    aggregate_matrix_run,
    aggregate_soak_history,
    render_soak_history_markdown,
    write_dashboard_outputs,
)


class MicroMachineSoakHistoryTest(unittest.TestCase):
    def test_aggregate_matrix_run_flattens_failures_and_counts_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passed = root / "01-Acropolis-Zerg-d1"
            failed = root / "02-Acropolis-Protoss-d1"
            missing = root / "03-Acropolis-Terran-d1"
            passed.mkdir()
            failed.mkdir()
            missing.mkdir()
            (passed / "preflight_report.json").write_text(
                json.dumps({"status": "passed", "ok": True, "failure_codes": []})
            )
            (failed / "preflight_report.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "ok": False,
                        "failure_codes": ["geometry_risk"],
                    }
                )
            )
            self.write_soak_report(
                passed / "soak_report.json",
                ok=True,
                status="passed",
                latest_frame=12_500,
                map_file="AcropolisLE.SC2Map",
                enemy_race="Zerg",
                enemy_difficulty=1,
            )
            self.write_soak_report(
                failed / "soak_report.json",
                ok=False,
                status="failed",
                latest_frame=8_000,
                map_file="AcropolisLE.SC2Map",
                enemy_race="Protoss",
                enemy_difficulty=1,
                failures=[{"code": "no_production_deadlock"}],
                attempts=[
                    {
                        "attempt": 1,
                        "status": "failed",
                        "failures": [{"code": "telemetry_stall"}],
                    }
                ],
            )

            report = aggregate_matrix_run(
                root,
                target_frame=12_000,
                timeout_seconds=1_200,
                qualification_tier="diagnostic",
                allow_failures=True,
                strategy_profiles=("default_defensive_to_aggressive",),
            )

            self.assertFalse(report["ok"])
            self.assertEqual("failed", report["status"])
            self.assertEqual("diagnostic", report["qualification_tier"])
            self.assertTrue(report["allow_failures"])
            self.assertEqual(["default_defensive_to_aggressive"], report["strategy_profiles"])
            self.assertEqual(3, report["case_count"])
            self.assertEqual(1, report["passed"])
            self.assertEqual(2, report["failed"])
            self.assertEqual(
                ["no_production_deadlock", "telemetry_stall"],
                report["cases"][1]["failure_codes"],
            )
            self.assertEqual("passed", report["cases"][0]["preflight_status"])
            self.assertEqual(12_000, report["cases"][0]["target_frame"])
            self.assertEqual(1_200, report["cases"][0]["timeout_seconds"])
            self.assertEqual("diagnostic", report["cases"][0]["qualification_tier"])
            self.assertEqual(
                ["default_defensive_to_aggressive"],
                report["cases"][0]["strategy_profiles"],
            )
            self.assertEqual("failed", report["cases"][1]["preflight_status"])
            self.assertEqual(["geometry_risk"], report["cases"][1]["preflight_failure_codes"])
            self.assertEqual("passed", report["cases"][0]["failure_phase"])
            self.assertEqual("preflight_failure", report["cases"][1]["failure_phase"])
            self.assertEqual("missing_report", report["cases"][2]["status"])
            self.assertEqual("missing_report", report["cases"][2]["failure_phase"])
            self.assertEqual(["missing_report"], report["cases"][2]["failure_codes"])
            self.assertEqual("Acropolis", report["cases"][2]["map_file"])
            self.assertEqual("Terran", report["cases"][2]["enemy_race"])
            self.assertEqual(1, report["cases"][2]["enemy_difficulty"])

    def test_required_failure_is_not_hidden_by_later_diagnostic_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required_failed = root / "01-Acropolis-Zerg-d1"
            diagnostic_passed = root / "02-Thunderbird-Zerg-d1"
            required_failed.mkdir()
            diagnostic_passed.mkdir()
            self.write_soak_report(
                required_failed / "soak_report.json",
                ok=False,
                status="failed",
                latest_frame=7_000,
                map_file="AcropolisLE.SC2Map",
                enemy_race="Zerg",
                enemy_difficulty=1,
                failures=[{"code": "no_production_deadlock"}],
            )
            self.write_soak_report(
                diagnostic_passed / "soak_report.json",
                ok=True,
                status="passed",
                latest_frame=12_500,
                map_file="Ladder2019Season3/ThunderbirdLE.SC2Map",
                enemy_race="Zerg",
                enemy_difficulty=1,
            )

            report = aggregate_matrix_run(
                root,
                target_frame=12_000,
                timeout_seconds=1_200,
                qualification_tier="production",
                allow_failures=False,
                strategy_profiles=("default_defensive_to_aggressive",),
            )

            self.assertFalse(report["ok"])
            self.assertEqual("failed", report["status"])
            self.assertEqual(1, report["passed"])
            self.assertEqual(1, report["failed"])
            self.assertEqual(["no_production_deadlock"], report["cases"][0]["failure_codes"])
            self.assertEqual(
                "Ladder2019Season3/ThunderbirdLE.SC2Map",
                report["cases"][1]["map_file"],
            )

    def test_history_dashboard_counts_failures_maps_and_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "run-001"
            second = root / "run-002"
            first.mkdir()
            second.mkdir()
            (first / "matrix_report.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "passed",
                        "target_frame": 12_000,
                        "timeout_seconds": 1_200,
                        "qualification_tier": "production",
                        "allow_failures": False,
                        "case_count": 1,
                        "passed": 1,
                        "failed": 0,
                        "cases": [
                            {
                                "case_id": "case-a",
                                "ok": True,
                                "map_file": "AcropolisLE.SC2Map",
                                "enemy_race": "Zerg",
                                "enemy_difficulty": 1,
                                "failure_codes": [],
                            }
                        ],
                    }
                )
            )
            (second / "matrix_report.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "status": "failed",
                        "target_frame": 12_000,
                        "timeout_seconds": 1_200,
                        "qualification_tier": "diagnostic",
                        "allow_failures": True,
                        "case_count": 1,
                        "passed": 0,
                        "failed": 1,
                        "cases": [
                            {
                                "case_id": "case-b",
                                "ok": False,
                                "map_file": "ThunderbirdLE.SC2Map",
                                "enemy_race": "Protoss",
                                "enemy_difficulty": 1,
                                "failure_codes": ["no_production_deadlock"],
                            }
                        ],
                    }
                )
            )

            dashboard = aggregate_soak_history(SoakHistoryConfig(roots=(root,)))
            markdown = render_soak_history_markdown(dashboard)

            self.assertFalse(dashboard["ok"])
            self.assertEqual(2, dashboard["run_count"])
            self.assertEqual(1, dashboard["passed_runs"])
            self.assertEqual(1, dashboard["failed_runs"])
            self.assertEqual("failed", dashboard["streaks"]["current_status"])
            self.assertEqual(0, dashboard["streaks"]["current_pass_streak"])
            self.assertEqual(1, dashboard["streaks"]["current_fail_streak"])
            self.assertIn(
                ("production", False),
                {
                    (run.get("qualification_tier"), run.get("allow_failures"))
                    for run in dashboard["runs"]
                },
            )
            self.assertIn(
                ("diagnostic", True),
                {
                    (run.get("qualification_tier"), run.get("allow_failures"))
                    for run in dashboard["runs"]
                },
            )
            self.assertEqual(
                [{"value": "no_production_deadlock", "count": 1}],
                dashboard["failure_codes"],
            )
            self.assertIn(
                "ThunderbirdLE.SC2Map",
                {entry["value"] for entry in dashboard["maps"]},
            )
            self.assertIn("MicroMachine Soak History", markdown)
            self.assertIn("no_production_deadlock", markdown)

    def test_production_signoff_passes_all_green_required_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(
                root / "run-green",
                ok=True,
                status="passed",
                qualification_tier="production",
                build_identity="build-a",
                cases=[
                    self.matrix_case("AcropolisLE.SC2Map", "Zerg", 1, ok=True),
                    self.matrix_case("AcropolisLE.SC2Map", "Protoss", 1, ok=True),
                    self.matrix_case("AcropolisLE.SC2Map", "Terran", 1, ok=True),
                ],
            )

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(
                    roots=(root,),
                    required_map_files=("AcropolisLE.SC2Map",),
                    required_enemy_races=("Zerg", "Protoss", "Terran"),
                    required_enemy_difficulties=(1,),
                    required_strategy_profiles=("default_defensive_to_aggressive",),
                    required_build_identity="build-a",
                )
            )
            markdown = render_soak_history_markdown(dashboard)

            signoff = dashboard["production_signoff"]
            self.assertTrue(signoff["ok"])
            self.assertEqual("passed", signoff["status"])
            self.assertEqual(3, signoff["coverage"]["required_count"])
            self.assertEqual(3, signoff["coverage"]["observed_count"])
            self.assertEqual([], signoff["blockers"])
            self.assertIn("Production Signoff", markdown)
            self.assertIn("Status: `passed`", markdown)

    def test_production_signoff_blocks_missing_required_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(
                root / "run-partial",
                ok=True,
                status="passed",
                qualification_tier="production",
                build_identity="build-a",
                cases=[self.matrix_case("AcropolisLE.SC2Map", "Zerg", 1, ok=True)],
            )

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(
                    roots=(root,),
                    required_map_files=("AcropolisLE.SC2Map",),
                    required_enemy_races=("Zerg", "Protoss"),
                    required_enemy_difficulties=(1,),
                    required_strategy_profiles=("default_defensive_to_aggressive",),
                )
            )

            signoff = dashboard["production_signoff"]
            self.assertFalse(signoff["ok"])
            self.assertEqual("blocked", signoff["status"])
            self.assertEqual(1, signoff["coverage"]["missing_count"])
            self.assertIn(
                "missing_required_coverage",
                {blocker["code"] for blocker in signoff["blockers"]},
            )

    def test_production_signoff_excludes_disabled_and_diagnostic_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(
                root / "run-diagnostic",
                ok=True,
                status="passed",
                qualification_tier="diagnostic",
                allow_failures=True,
                cases=[self.matrix_case("AcropolisLE.SC2Map", "Zerg", 1, ok=True)],
            )
            self.write_matrix_report(
                root / "run-disabled",
                ok=False,
                status="disabled",
                qualification_tier="production",
                enabled=False,
                cases=[],
            )

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(
                    roots=(root,),
                    required_map_files=("AcropolisLE.SC2Map",),
                    required_enemy_races=("Zerg",),
                    required_enemy_difficulties=(1,),
                    required_strategy_profiles=("default_defensive_to_aggressive",),
                )
            )

            signoff = dashboard["production_signoff"]
            self.assertFalse(signoff["ok"])
            self.assertEqual(0, signoff["eligible_run_count"])
            self.assertEqual(2, signoff["excluded_run_count"])
            self.assertEqual(
                {"disabled", "non_signoff_tier"},
                {entry["reason"] for entry in signoff["excluded_runs"]},
            )
            self.assertIn(
                "no_eligible_production_runs",
                {blocker["code"] for blocker in signoff["blockers"]},
            )

    def test_production_signoff_blocks_failed_required_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(
                root / "run-failed",
                ok=False,
                status="failed",
                qualification_tier="production",
                failed=1,
                cases=[
                    self.matrix_case(
                        "AcropolisLE.SC2Map",
                        "Zerg",
                        1,
                        ok=False,
                        failure_codes=["no_production_deadlock"],
                    )
                ],
            )

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(
                    roots=(root,),
                    required_map_files=("AcropolisLE.SC2Map",),
                    required_enemy_races=("Zerg",),
                    required_enemy_difficulties=(1,),
                    required_strategy_profiles=("default_defensive_to_aggressive",),
                )
            )

            signoff = dashboard["production_signoff"]
            self.assertFalse(signoff["ok"])
            self.assertIn(
                "failed_required_case",
                {blocker["code"] for blocker in signoff["blockers"]},
            )

    def test_production_signoff_blocks_build_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(
                root / "run-old-build",
                ok=True,
                status="passed",
                qualification_tier="production",
                build_identity="old-build",
                cases=[self.matrix_case("AcropolisLE.SC2Map", "Zerg", 1, ok=True)],
            )

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(
                    roots=(root,),
                    required_map_files=("AcropolisLE.SC2Map",),
                    required_enemy_races=("Zerg",),
                    required_enemy_difficulties=(1,),
                    required_strategy_profiles=("default_defensive_to_aggressive",),
                    required_build_identity="new-build",
                )
            )

            signoff = dashboard["production_signoff"]
            self.assertFalse(signoff["ok"])
            self.assertEqual(["old-build"], signoff["build_identity"]["observed"])
            self.assertIn(
                "build_mismatch",
                {blocker["code"] for blocker in signoff["blockers"]},
            )

    def test_production_signoff_blocks_missing_build_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(
                root / "run-unrecorded-build",
                ok=True,
                status="passed",
                qualification_tier="production",
                build_identity="unrecorded",
                cases=[self.matrix_case("AcropolisLE.SC2Map", "Zerg", 1, ok=True)],
            )

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(
                    roots=(root,),
                    required_map_files=("AcropolisLE.SC2Map",),
                    required_enemy_races=("Zerg",),
                    required_enemy_difficulties=(1,),
                    required_strategy_profiles=("default_defensive_to_aggressive",),
                )
            )

            signoff = dashboard["production_signoff"]
            self.assertFalse(signoff["ok"])
            self.assertIn(
                "missing_build_identity",
                {blocker["code"] for blocker in signoff["blockers"]},
            )

    def test_production_signoff_blocks_invalid_build_identity_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_matrix_report(
                root / "run-invalid-build",
                ok=True,
                status="passed",
                qualification_tier="production",
                build_identity="sha256:bad-build",
                build_identity_ok=False,
                build_identity_failure_codes=["missing_binary"],
                cases=[self.matrix_case("AcropolisLE.SC2Map", "Zerg", 1, ok=True)],
            )

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(
                    roots=(root,),
                    required_map_files=("AcropolisLE.SC2Map",),
                    required_enemy_races=("Zerg",),
                    required_enemy_difficulties=(1,),
                    required_strategy_profiles=("default_defensive_to_aggressive",),
                )
            )

            signoff = dashboard["production_signoff"]
            self.assertFalse(signoff["ok"])
            blocker_codes = {blocker["code"] for blocker in signoff["blockers"]}
            self.assertIn("invalid_build_identity", blocker_codes)
            invalid = next(
                blocker
                for blocker in signoff["blockers"]
                if blocker["code"] == "invalid_build_identity"
            )
            self.assertEqual(["missing_binary"], invalid["failure_codes"])

    def test_history_dashboard_recent_limit_uses_mtime_not_lexicographic_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "gha-999-1"
            newer = root / "gha-1000-1"
            older.mkdir()
            newer.mkdir()
            for run_dir, ok in ((older, True), (newer, False)):
                (run_dir / "matrix_report.json").write_text(
                    json.dumps(
                        {
                            "ok": ok,
                            "status": "passed" if ok else "failed",
                            "case_count": 1,
                            "passed": 1 if ok else 0,
                            "failed": 0 if ok else 1,
                            "cases": [
                                {
                                    "case_id": run_dir.name,
                                    "failure_codes": (
                                        [] if ok else ["newer_failure"]
                                    ),
                                }
                            ],
                        }
                    )
                )
            os.utime(older / "matrix_report.json", (100.0, 100.0))
            os.utime(newer / "matrix_report.json", (200.0, 200.0))

            dashboard = aggregate_soak_history(
                SoakHistoryConfig(roots=(root,), recent_limit=1)
            )

            self.assertEqual("gha-1000-1", dashboard["runs"][0]["run_id"])
            self.assertEqual(
                [{"value": "newer_failure", "count": 1}],
                dashboard["failure_codes"],
            )

    def test_write_dashboard_outputs_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = {
                "status": "passed",
                "ok": True,
                "run_count": 1,
                "passed_runs": 1,
                "failed_runs": 0,
                "case_count": 1,
                "passed_cases": 1,
                "failed_cases": 0,
                "failure_codes": [],
                "runs": [],
            }
            output_json = root / "dashboard.json"
            output_markdown = root / "dashboard.md"

            write_dashboard_outputs(
                dashboard,
                output_json=output_json,
                output_markdown=output_markdown,
            )

            self.assertEqual(dashboard, json.loads(output_json.read_text()))
            self.assertIn("Status: `passed`", output_markdown.read_text())

    def test_matrix_script_disabled_mode_writes_artifacts_without_sc2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "disabled-run"
            script = Path("integrations/micromachine/scripts/soak_matrix_macos_local.sh")

            completed = subprocess.run(
                [str(script)],
                check=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "PYTHONPATH": ".",
                    "SOAK_MATRIX_ENABLED": "0",
                    "SOAK_MATRIX_RUN_DIR": str(run_dir),
                    "SOAK_MATRIX_REPORT": str(run_dir / "matrix_report.json"),
                    "SOAK_MATRIX_HISTORY_JSON": str(run_dir / "history.json"),
                    "SOAK_MATRIX_HISTORY_MD": str(run_dir / "history.md"),
                },
                capture_output=True,
                text=True,
            )

            self.assertIn("MicroMachine matrix disabled", completed.stdout)
            report = json.loads((run_dir / "matrix_report.json").read_text())
            self.assertEqual("disabled", report["status"])
            self.assertEqual("production", report["qualification_tier"])
            self.assertFalse(report["allow_failures"])
            self.assertEqual(["default_defensive_to_aggressive"], report["strategy_profiles"])
            self.assertFalse(report["ok"])
            history = json.loads((run_dir / "history.json").read_text())
            self.assertEqual("disabled", history["status"])
            self.assertEqual(1, history["run_count"])
            self.assertEqual("disabled", history["runs"][0]["status"])
            self.assertFalse(history["runs"][0]["enabled"])
            self.assertEqual("disabled", history["streaks"]["current_status"])
            self.assertEqual("blocked", history["production_signoff"]["status"])
            self.assertEqual(
                [{"run_id": "disabled-run", "reason": "disabled"}],
                history["production_signoff"]["excluded_runs"],
            )
            self.assertIn("disabled", (run_dir / "history.md").read_text())

    def test_matrix_script_disabled_mode_keeps_artifacts_for_malformed_build_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "malformed-build-run"
            identity_report = root / "empty-build-identity.json"
            identity_report.write_text("")
            script = Path("integrations/micromachine/scripts/soak_matrix_macos_local.sh")

            subprocess.run(
                [str(script)],
                check=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "PYTHONPATH": ".",
                    "SOAK_MATRIX_ENABLED": "0",
                    "SOAK_MATRIX_BUILD_IDENTITY_REPORT": str(identity_report),
                    "SOAK_MATRIX_RUN_DIR": str(run_dir),
                    "SOAK_MATRIX_REPORT": str(run_dir / "matrix_report.json"),
                    "SOAK_MATRIX_HISTORY_JSON": str(run_dir / "history.json"),
                    "SOAK_MATRIX_HISTORY_MD": str(run_dir / "history.md"),
                },
                capture_output=True,
                text=True,
            )

            report = json.loads((run_dir / "matrix_report.json").read_text())
            self.assertEqual("unrecorded", report["build_identity"])
            self.assertFalse(report["build_identity_ok"])
            self.assertEqual(["disabled"], report["build_identity_failure_codes"])
            history = json.loads((run_dir / "history.json").read_text())
            self.assertEqual(1, history["run_count"])
            self.assertEqual("disabled", history["runs"][0]["status"])

    def write_soak_report(
        self,
        path: Path,
        *,
        ok: bool,
        status: str,
        latest_frame: int,
        map_file: str,
        enemy_race: str,
        enemy_difficulty: int,
        failures: list[dict[str, object]] | None = None,
        attempts: list[dict[str, object]] | None = None,
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "ok": ok,
                    "status": status,
                    "latest_frame": latest_frame,
                    "macro_evidence_ok": ok,
                    "manager_intervention_ok": ok,
                    "target_reached": ok,
                    "map_file": map_file,
                    "enemy_race": enemy_race,
                    "enemy_difficulty": enemy_difficulty,
                    "failures": failures or [],
                    "attempts": attempts or [],
                    "artifact_manifest": {"bot_log": "micromachine.log"},
                },
                sort_keys=True,
            )
            + "\n"
        )

    def matrix_case(
        self,
        map_file: str,
        enemy_race: str,
        enemy_difficulty: int,
        *,
        ok: bool,
        failure_codes: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "case_id": f"{map_file}-{enemy_race}-d{enemy_difficulty}",
            "ok": ok,
            "status": "passed" if ok else "failed",
            "map_file": map_file,
            "enemy_race": enemy_race,
            "enemy_difficulty": enemy_difficulty,
            "strategy_profiles": ["default_defensive_to_aggressive"],
            "failure_codes": failure_codes or [],
        }

    def write_matrix_report(
        self,
        run_dir: Path,
        *,
        ok: bool,
        status: str,
        qualification_tier: str,
        cases: list[dict[str, object]],
        allow_failures: bool = False,
        enabled: bool = True,
        failed: int | None = None,
        build_identity: str | None = None,
        build_identity_ok: bool | None = None,
        build_identity_failure_codes: list[str] | None = None,
    ) -> None:
        run_dir.mkdir()
        failed_count = failed if failed is not None else sum(1 for case in cases if not case["ok"])
        payload = {
            "ok": ok,
            "status": status,
            "enabled": enabled,
            "target_frame": 12_000,
            "timeout_seconds": 1_200,
            "qualification_tier": qualification_tier,
            "allow_failures": allow_failures,
            "strategy_profiles": ["default_defensive_to_aggressive"],
            "build_identity": build_identity,
            "build_identity_ok": build_identity_ok,
            "build_identity_failure_codes": build_identity_failure_codes or [],
            "case_count": len(cases),
            "passed": len(cases) - failed_count,
            "failed": failed_count,
            "cases": cases,
        }
        (run_dir / "matrix_report.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n"
        )


if __name__ == "__main__":
    unittest.main()
