"""Tests for MicroMachine long-run soak classification."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from starcraft_commander.micromachine_soak import (
    MicroMachineSoakConfig,
    MicroMachineSoakObservation,
    classify_micromachine_soak,
    has_required_macro_evidence,
    missing_macro_evidence,
)


MACRO_LOG = "\n".join(
    (
        "Connected to 127.0.0.1:8167",
        "WaitJoinGame finished successfully.",
        "894: constructAssignedBuildings | build command type=TERRAN_SUPPLYDEPOT",
        "TERRAN_SUPPLYDEPOT UnderConstruction",
        "3550: constructAssignedBuildings | build command type=TERRAN_BARRACKS",
        "TERRAN_BARRACKS UnderConstruction",
        "4590: create | create unit item=Marine result=1",
        "4940: constructAssignedBuildings | build command type=TERRAN_REFINERY",
        "TERRAN_REFINERY UnderConstruction",
        "11200: drawProductionInformation | Production Information",
        "Gas Worker Target:3",
        "Mineral income:       512",
        "Gas income:       67",
        "6100: create | create unit item=Marine result=1",
    )
)


def _telemetry(
    frame: int,
    *,
    policy_active: bool = True,
    update_id: str = "soak-aggressive-pressure",
) -> dict[str, object]:
    return {
        "protocol_version": "voi-mm-bridge/v1",
        "frame": frame,
        "bot_name": "MicroMachine",
        "race": "Terran",
        "active_modulation_ids": [update_id],
        "last_failure": None,
        "managers": {
            "GameCommander": {
                "policy_active": policy_active,
                "update_id": update_id,
            },
            "CombatCommander": {
                "active": True,
                "bounded_intervention": True,
                "aggression": 0.55,
            },
            "ScoutManager": {
                "active": True,
                "bounded_intervention": True,
                "scout_priority": 0.7,
            },
        },
    }


def _modulation(
    expires_at_frame: int = 20_000,
    *,
    update_id: str = "soak-aggressive-pressure",
) -> dict[str, object]:
    return {
        "protocol_version": "voi-mm-bridge/v1",
        "update_id": update_id,
        "issued_at_frame": 6_000,
        "expires_at_frame": expires_at_frame,
        "vector": {"goal": "micromachine_aggressive_pressure"},
    }


class MicroMachineSoakConfigTest(unittest.TestCase):
    def test_config_defaults_are_json_ready_and_validate_ranges(self) -> None:
        config = MicroMachineSoakConfig()

        self.assertEqual(12_000, config.target_frame)
        self.assertEqual(9_000, config.production_deadlock_frame)
        self.assertEqual(8_000, config.production_stall_frames)
        self.assertEqual(2_000, config.income_stall_frames)
        self.assertEqual(128, config.modulation_consumption_grace_frames)
        json.dumps(config.to_dict())

        with self.assertRaisesRegex(ValueError, "target_frame"):
            MicroMachineSoakConfig(target_frame=0)
        with self.assertRaisesRegex(ValueError, "max_placement_failures"):
            MicroMachineSoakConfig(max_placement_failures=-1)
        with self.assertRaisesRegex(ValueError, "income_stall_frames"):
            MicroMachineSoakConfig(income_stall_frames=0)


class MicroMachineSoakClassifierTest(unittest.TestCase):
    def test_macro_evidence_requires_building_unit_and_positive_income(self) -> None:
        self.assertTrue(has_required_macro_evidence(MACRO_LOG))

        missing_gas = MACRO_LOG.replace("Gas income:       67", "Gas income:       0")
        missing_minerals = MACRO_LOG.replace(
            "Mineral income:       512", "Mineral income:       0"
        )

        self.assertFalse(has_required_macro_evidence(missing_gas))
        self.assertIn("positive gas income", missing_macro_evidence(missing_gas))
        self.assertFalse(has_required_macro_evidence(missing_minerals))
        self.assertIn("positive mineral income", missing_macro_evidence(missing_minerals))

    def test_passes_when_target_frame_macro_and_manager_intervention_are_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    artifact_dir=root,
                    bot_running=True,
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual("passed", report.status)
            self.assertEqual(12_500, report.latest_frame)
            self.assertEqual("micromachine.log", report.artifact_manifest["bot_log"])

    def test_detects_crash_disconnect_and_repeated_placement_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_text = "\n".join(
                (
                    MACRO_LOG,
                    "Connection reset by peer",
                    "Failed to place Barracks",
                    "Failed to place Barracks",
                    "Failed to place Barracks",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=_telemetry(7_000))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    bot_exit_code=11,
                    bot_running=False,
                ),
                MicroMachineSoakConfig(target_frame=12_000, max_placement_failures=1),
            )

            self.assert_failure_codes(
                report,
                {
                    "micromachine_crash",
                    "micromachine_process_stopped",
                    "sc2_disconnect",
                    "repeated_placement_failures",
                },
            )

    def test_detects_telemetry_stall_and_no_production_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_text = "Connected to 127.0.0.1:8167\n"
            self._write_runtime(root, log_text=log_text, telemetry=_telemetry(6_000))
            stale_mtime = time.time() - 300
            os.utime(root / "latest_telemetry.json", (stale_mtime, stale_mtime))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    now_seconds=time.time(),
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    telemetry_stall_seconds=10,
                    production_deadlock_frame=5_000,
                ),
            )

            self.assert_failure_codes(report, {"telemetry_stall", "no_production_deadlock"})

    def test_uses_archive_when_latest_telemetry_is_temporarily_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=_telemetry(7_000))
            (root / "latest_telemetry.json").write_text("{")

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    now_seconds=time.time(),
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assertNotIn("telemetry_missing", {failure.code for failure in report.failures})
            self.assertEqual(7_000, report.latest_frame)

    def test_detects_missing_intervention_production_stall_and_stale_modulation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers.pop("CombatCommander")
            telemetry["active_modulation_ids"] = []
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(expires_at_frame=10_000),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, production_stall_frames=100),
            )

            self.assert_failure_codes(
                report,
                {
                    "manager_intervention_missing",
                    "production_stall",
                    "stale_modulation",
                },
            )

    def test_detects_income_stall_near_target_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale_income_log = MACRO_LOG.replace(
                "11200: drawProductionInformation", "6048: drawProductionInformation"
            )
            self._write_runtime(root, log_text=stale_income_log, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, income_stall_frames=2_000),
            )

            self.assert_failure_codes(report, {"income_stall"})
            income_stall = [failure for failure in report.failures if failure.code == "income_stall"]
            self.assertEqual(
                ["recent positive mineral income"],
                income_stall[0].evidence["missing"],
            )

    def test_detects_recent_gas_stall_when_target_is_inline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gas_stall_log = "\n".join(
                (
                    MACRO_LOG.replace("Gas income:       67", "Gas income:       0"),
                    "11250: drawProductionInformation | Production Information",
                    "11251: Gas Worker Target:3",
                    "11252: Mineral income:       512",
                    "11253: Gas income:       0",
                )
            )
            self._write_runtime(root, log_text=gas_stall_log, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, income_stall_frames=2_000),
            )

            self.assert_failure_codes(report, {"income_stall"})
            income_stall = [failure for failure in report.failures if failure.code == "income_stall"]
            self.assertEqual(
                ["recent positive gas income"],
                income_stall[0].evidence["missing"],
            )

    def test_income_stall_allows_recent_worker_combat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            combat_log = MACRO_LOG.replace(
                "6100: create | create unit item=Marine result=1",
                "6100: create | create unit item=Marine result=1\n"
                "11200: drawProductionInformation | Production Information\n"
                "Gas Worker Target:0\n"
                "Mineral income:       0\n"
                "Gas income:       0\n"
                "Worker jobs M/G/B/C/I/S/N:7/0/0/10/2/1/-21",
            )
            self._write_runtime(root, log_text=combat_log, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, income_stall_frames=2_000),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_production_stall_allows_recent_combat_activity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            combat_log = "\n".join(
                (
                    MACRO_LOG,
                    "12450: updateAttackSquads | MainAttackSquad new order = Attack (42, 42)",
                    "12460: drawProductionInformation | Production Information",
                    "Worker jobs M/G/B/C/I/S/N:7/0/0/10/2/1/-21",
                )
            )
            self._write_runtime(root, log_text=combat_log, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, production_stall_frames=100),
            )

            self.assertNotIn("production_stall", {failure.code for failure in report.failures})

    def test_detects_latest_modulation_refresh_not_consumed_by_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=_telemetry(12_500),
                modulation={
                    **_modulation(expires_at_frame=20_000),
                    "update_id": "soak-aggressive-pressure-11500",
                    "issued_at_frame": 11_500,
                },
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    modulation_consumption_grace_frames=128,
                ),
            )

            self.assert_failure_codes(report, {"stale_modulation"})
            stale = [failure for failure in report.failures if failure.code == "stale_modulation"]
            self.assertIn("not been consumed", stale[0].message)

    def test_uses_modulation_archive_when_latest_modulation_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-aggressive-pressure-11500"
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=_telemetry(12_500, update_id=update_id),
                modulation={
                    **_modulation(update_id=update_id),
                    "issued_at_frame": 11_500,
                },
            )
            (root / "latest_modulation.json").write_text("{")

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_detects_missing_modulation_artifact_at_target_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=_telemetry(12_500))
            (root / "latest_modulation.json").unlink()
            (root / "modulation_updates.jsonl").unlink()

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"stale_modulation"})
            stale = [failure for failure in report.failures if failure.code == "stale_modulation"]
            self.assertIn("missing or unreadable", stale[0].message)

    def test_cli_report_serialization_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=_telemetry(12_500))
            report = classify_micromachine_soak(
                MicroMachineSoakObservation(blackboard_dir=root, bot_log=root / "micromachine.log"),
                MicroMachineSoakConfig(target_frame=12_000),
            )
            report_path = root / "soak_report.json"

            report.write_json(report_path)

            payload = json.loads(report_path.read_text())
            self.assertTrue(payload["ok"])
            self.assertEqual("passed", payload["status"])
            self.assertEqual(12_000, payload["config"]["target_frame"])

    def assert_failure_codes(
        self,
        report,
        expected_codes: set[str],
    ) -> None:
        codes = {failure.code for failure in report.failures}
        self.assertTrue(expected_codes.issubset(codes), report.to_dict())
        self.assertFalse(report.ok)

    def _write_runtime(
        self,
        root: Path,
        *,
        log_text: str,
        telemetry: dict[str, object],
        modulation: dict[str, object] | None = None,
    ) -> None:
        (root / "micromachine.log").write_text(log_text + "\n")
        (root / "latest_telemetry.json").write_text(json.dumps(telemetry) + "\n")
        (root / "telemetry.jsonl").write_text(json.dumps(telemetry) + "\n")
        (root / "latest_modulation.json").write_text(
            json.dumps(modulation or _modulation()) + "\n"
        )
        (root / "modulation_updates.jsonl").write_text(
            json.dumps(modulation or _modulation()) + "\n"
        )


if __name__ == "__main__":
    unittest.main()
