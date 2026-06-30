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
from starcraft_commander.micromachine_soak import (
    MicroMachineSoakConfig,
    MicroMachineSoakObservation,
    classify_micromachine_soak,
)
from starcraft_commander.micromachine_soak_history import aggregate_matrix_run


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

    def test_triage_preserves_runtime_classifier_health_and_profile_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "01-AcropolisLE-SC2Map-Zerg-d1"
            case_dir.mkdir()
            (case_dir / "preflight_report.json").write_text(
                json.dumps({"status": "passed", "ok": True, "failure_codes": []})
                + "\n"
            )
            telemetry = {
                "frame": 9_000,
                "active_modulation_ids": ["dsl-rush-001"],
                "managers": {
                    "GameCommander": {
                        "policy_active": True,
                        "update_id": "dsl-rush-001",
                    },
                    "CombatCommander": {"bounded_intervention": True},
                    "ProductionManager": {
                        "bounded_intervention": True,
                        "policy_update_id": "dsl-rush-001",
                        "policy_issued_at_frame": 6_000,
                        "strategy_doctrine": "bio_pressure",
                        "last_doctrine": "bio_pressure",
                        "last_doctrine_action": "marine_pressure",
                        "last_doctrine_queue_item": "Marine",
                        "last_doctrine_evidence": "queued",
                        "last_doctrine_update_id": "dsl-rush-001",
                        "last_doctrine_frame": 8_500,
                        "last_doctrine_fresh": True,
                    },
                    "ScoutManager": {"bounded_intervention": True},
                    "WorkerManager": {
                        "active": True,
                        "repeat_order_guard_active": True,
                        "repeat_order_guard_frames": 32,
                        "repeat_order_suppressed_count": 0,
                        "self_position_command_block_count": 0,
                        "root_cause_status": "none",
                        "root_cause_reason": "none",
                        "trace_contract_version": 1,
                        "trace_event_count": 8,
                        "last_trace_frame": 9_000,
                        "last_trace_status": "accepted_candidate",
                        "last_trace_reason": "mineral_assignment",
                        "last_trace_target_kind": "unit",
                    },
                },
            }
            modulation = {
                "update_id": "dsl-rush-001",
                "issued_at_frame": 8_000,
                "expires_at_frame": 20_000,
                "vector": {"tags": ["pressure-timing"]},
            }
            (case_dir / "latest_telemetry.json").write_text(
                json.dumps(telemetry) + "\n"
            )
            (case_dir / "telemetry.jsonl").write_text(json.dumps(telemetry) + "\n")
            (case_dir / "latest_modulation.json").write_text(
                json.dumps(modulation) + "\n"
            )
            (case_dir / "modulation_updates.jsonl").write_text(
                json.dumps(modulation) + "\n"
            )
            (case_dir / "micromachine.log").write_text("Connected to SC2\n")
            classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=case_dir,
                    bot_log=case_dir / "micromachine.log",
                    artifact_dir=case_dir,
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    production_deadlock_frame=8_000,
                    expected_profile_tags=("pressure-timing",),
                ),
            ).write_json(case_dir / "soak_report.json")

            matrix = aggregate_matrix_run(
                root,
                target_frame=12_000,
                timeout_seconds=1_200,
                qualification_tier="production",
                allow_failures=False,
                strategy_profiles=("default_defensive_to_aggressive",),
            )
            triage = triage_matrix_report(matrix)

        failure = triage["ranked_failures"][0]
        self.assertEqual(1, len(failure["classifier_failures"]))
        classifier_failure = failure["classifier_failures"][0]
        self.assertEqual("no_production_deadlock", classifier_failure["code"])
        self.assertEqual("terminal", classifier_failure["severity"])
        self.assertEqual(9_000, classifier_failure["evidence"]["latest_frame"])
        self.assertIn("missing_terms", classifier_failure["evidence"])
        self.assertIsNone(classifier_failure["attempt"])
        self.assertIsNone(classifier_failure["attempt_status"])
        self.assertEqual(
            {
                "latest_frame": 9_000,
                "target_reached": False,
                "macro_evidence_ok": False,
                "manager_intervention_ok": True,
                "preflight_ok": True,
            },
            failure["telemetry_health"],
        )
        self.assertEqual(
            {
                "strategy_profiles": ["default_defensive_to_aggressive"],
                "expected_profile_tags": ["pressure-timing"],
                "active_modulation_ids": ["dsl-rush-001"],
            },
            failure["profile_context"],
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
