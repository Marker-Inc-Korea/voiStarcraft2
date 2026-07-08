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
    count_placement_failures,
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
        "11280: enforceBarracksProductionContinuity | continuity accepted unit training order=Marine",
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
                "actual_command_issued_count": 3,
                "last_action_frame": 6_260,
                "last_issued_action_frame": 6_260,
                "last_issued_action": "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5",
                "main_attack_actual_command_issued_count": 3,
                "main_attack_last_action_frame": 6_260,
                "main_attack_last_issued_action": "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5",
                "main_attack_order_status": "Attack",
                "main_attack_home_distance": 18.0,
                "main_attack_max_home_distance": 28.0,
                "scout_home_distance": 10.0,
                "scout_max_home_distance": 14.0,
            },
            "ProductionManager": {
                "active": True,
                "bounded_intervention": True,
                "policy_update_id": update_id,
                "policy_issued_at_frame": 6_000,
                "strategy_doctrine": "bio_pressure",
                "queue_bias_marine": 0.55,
                "last_doctrine": "bio_pressure",
                "last_doctrine_action": "marine_pressure",
                "last_doctrine_queue_item": "Marine",
                "last_doctrine_evidence": "queued",
                "last_doctrine_update_id": update_id,
                "last_doctrine_frame": 6_200,
                "last_doctrine_fresh": True,
                "actual_production_command_issued_count": 1,
                "last_actual_production_command": "train_command|Marine",
                "last_actual_production_command_kind": "train_command",
                "last_actual_production_command_item": "Marine",
                "last_actual_production_command_update_id": update_id,
                "last_actual_production_command_frame": 6_240,
                "supply_blocked_frames": 0,
                "last_supply_block_frame": 0,
                "supply_recovery_queued_count": 0,
                "last_supply_recovery_frame": 0,
                "last_supply_recovery_status": "none",
                "last_supply_recovery_reason": "none",
                "supply_provider_under_construction_count": 0,
                "last_supply_provider_command_frame": 0,
                "last_supply_provider_command_kind": "none",
                "last_supply_provider_command_update_id": "",
                "consumed_axes": (
                    "strategy.doctrine,production.queue_biases.*,"
                    "production.composition_biases.*,"
                    "production.production_facility_biases.*,tech.unit_biases.*"
                ),
            },
            "ScoutManager": {
                "active": True,
                "bounded_intervention": True,
                "scout_priority": 0.7,
                "actual_command_issued_count": 3,
                "last_actual_command": "move|scout_enemy_region_known_move|x=33.5|y=138.5",
                "last_actual_command_frame": 6_260,
                "last_target_distance": 30.0,
                "last_home_distance": 24.0,
                "max_home_distance": 32.0,
                "last_enemy_base_distance": 14.0,
                "min_enemy_base_distance": 14.0,
                "deep_scout_frame_count": 24,
            },
            "WorkerManager": {
                "active": True,
                "repeat_order_guard_active": True,
                "repeat_order_guard_frames": 32,
                "repeat_order_suppressed_count": 0,
                "self_position_command_block_count": 0,
                "root_cause_status": "none",
                "root_cause_reason": "none",
                "trace_contract_version": 1,
                "trace_event_count": 12,
                "last_trace_frame": frame,
                "last_trace_status": "accepted_candidate",
                "last_trace_reason": "mineral_assignment",
                "last_trace_target_kind": "unit",
                "last_trace_target_tag": 4324589569,
                "last_trace_distance_sq": 140.0,
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
        "vector": {
            "goal": "micromachine_aggressive_pressure",
            "tags": ["aggressive_pressure", "bounded_intervention"],
        },
    }


class MicroMachineSoakConfigTest(unittest.TestCase):
    def test_config_defaults_are_json_ready_and_validate_ranges(self) -> None:
        config = MicroMachineSoakConfig()

        self.assertEqual(12_000, config.target_frame)
        self.assertEqual(9_000, config.production_deadlock_frame)
        self.assertEqual(6_000, config.production_stall_frames)
        self.assertEqual(672, config.supply_recovery_grace_frames)
        self.assertEqual(2_000, config.income_stall_frames)
        self.assertEqual(1_200, config.bootstrap_no_start_units_frame)
        self.assertEqual(0, config.max_worker_self_position_blocks)
        self.assertEqual(0, config.max_worker_repeat_order_suppressions)
        self.assertEqual(128, config.modulation_consumption_grace_frames)
        json.dumps(config.to_dict())

        with self.assertRaisesRegex(ValueError, "target_frame"):
            MicroMachineSoakConfig(target_frame=0)
        with self.assertRaisesRegex(ValueError, "max_placement_failures"):
            MicroMachineSoakConfig(max_placement_failures=-1)
        with self.assertRaisesRegex(ValueError, "max_placement_failures"):
            MicroMachineSoakConfig(max_placement_failures=0)
        with self.assertRaisesRegex(ValueError, "max_worker_self_position_blocks"):
            MicroMachineSoakConfig(max_worker_self_position_blocks=-1)
        with self.assertRaisesRegex(ValueError, "max_worker_repeat_order_suppressions"):
            MicroMachineSoakConfig(max_worker_repeat_order_suppressions=-1)
        with self.assertRaisesRegex(ValueError, "income_stall_frames"):
            MicroMachineSoakConfig(income_stall_frames=0)
        with self.assertRaisesRegex(ValueError, "supply_recovery_grace_frames"):
            MicroMachineSoakConfig(supply_recovery_grace_frames=0)
        with self.assertRaisesRegex(ValueError, "bootstrap_no_start_units_frame"):
            MicroMachineSoakConfig(bootstrap_no_start_units_frame=0)


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

    def test_dedupes_identical_framed_placement_failures_only(self) -> None:
        log_text = "\n".join(
            (
                "9396: manageBuildOrderQueue | Failed to place TERRAN_SUPPLYDEPOT during initial build order. Skipping.",
                "9396: manageBuildOrderQueue | Failed to place TERRAN_SUPPLYDEPOT during initial build order. Skipping.",
                "9728: manageBuildOrderQueue | Failed to place TERRAN_SUPPLYDEPOT during initial build order. Skipping.",
                "Failed to place Barracks",
                "Failed to place Barracks",
            )
        )

        self.assertEqual(4, count_placement_failures(log_text))

    def test_rejects_placement_failures_at_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_text = "\n".join(
                (
                    MACRO_LOG,
                    "7749: manageBuildOrderQueue | Failed to place TERRAN_SUPPLYDEPOT during initial build order. Skipping.",
                    "8094: manageBuildOrderQueue | Failed to place TERRAN_SUPPLYDEPOT during initial build order. Skipping.",
                    "9101: manageBuildOrderQueue | Failed to place TERRAN_SUPPLYDEPOT during initial build order. Skipping.",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=_telemetry(12_100))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    bot_running=False,
                ),
                MicroMachineSoakConfig(target_frame=12_000, max_placement_failures=3),
            )

            self.assert_failure_codes(report, {"repeated_placement_failures"})

    def test_rejects_unrecovered_supply_block_after_macro_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production.update(
                {
                    "supply_blocked_frames": 18,
                    "last_supply_block_frame": 10_000,
                    "supply_recovery_queued_count": 0,
                    "last_supply_recovery_frame": 0,
                    "last_supply_recovery_status": "none",
                    "last_supply_recovery_reason": "none",
                    "supply_provider_under_construction_count": 0,
                    "last_supply_provider_command_frame": 0,
                    "last_supply_provider_command_kind": "none",
                }
            )
            log_text = "\n".join(
                (
                    MACRO_LOG,
                    "10000: manageBuildOrderQueue | Supply blocked | 0x00000007",
                    "10032: manageBuildOrderQueue | Supply blocked | 0x00000007",
                    "10112: manageBuildOrderQueue | Supply blocked | 0x00000007",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"supply_block_unrecovered"})

    def test_rejects_supply_recovery_pending_at_target_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production.update(
                {
                    "supply_blocked_frames": 32,
                    "last_supply_block_frame": 12_480,
                    "supply_recovery_queued_count": 6,
                    "last_supply_recovery_frame": 12_482,
                    "last_supply_recovery_status": "promoted_existing_queue",
                    "last_supply_recovery_reason": "supply_block",
                    "supply_provider_under_construction_count": 0,
                    "last_supply_provider_command_frame": 10_120,
                    "last_supply_provider_command_kind": "build_command",
                }
            )
            log_text = "\n".join(
                (
                    MACRO_LOG,
                    "12480: manageBuildOrderQueue | Supply blocked | 0x00000007",
                    "12482: queueSupplyProviderRecovery | Supply provider recovery queued after supply block. status=promoted_existing_queue reason=supply_block",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"supply_recovery_pending_at_target"})

    def test_accepts_supply_block_recovered_by_supply_depot_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production.update(
                {
                    "supply_blocked_frames": 4,
                    "last_supply_block_frame": 10_000,
                    "supply_recovery_queued_count": 1,
                    "last_supply_recovery_frame": 10_040,
                    "last_supply_recovery_status": "queued",
                    "last_supply_recovery_reason": "supply_block",
                    "supply_provider_under_construction_count": 0,
                    "last_supply_provider_command_frame": 10_120,
                    "last_supply_provider_command_kind": "build_command",
                    "last_supply_provider_command_update_id": "soak-aggressive-pressure",
                }
            )
            log_text = "\n".join(
                (
                    MACRO_LOG,
                    "10000: manageBuildOrderQueue | Supply blocked | 0x00000007",
                    "10040: queueSupplyProviderRecovery | Supply provider recovery queued after supply block. status=queued reason=supply_block",
                    "10120: constructAssignedBuildings | build command type=TERRAN_SUPPLYDEPOT",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assertTrue(report.ok, report.to_dict())

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

    def test_detects_joined_game_without_starting_self_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = {
                "protocol_version": "voi-mm-bridge/v1",
                "frame": 1_524,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "CCBot": {
                        "bootstrap_status": "waiting_for_initial_observation",
                        "player_id": 1,
                        "self_count": 0,
                        "resource_depot_count": 0,
                        "game_info_width": 144,
                        "game_info_height": 160,
                        "enemy_start_location_count": 1,
                    }
                },
                "active_modulation_ids": [],
                "last_failure": "bootstrap_waiting",
            }
            self._write_runtime(
                root,
                log_text="Connected to 127.0.0.1:8167\nWaitJoinGame finished successfully.",
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"bootstrap_no_start_units"})

    def test_detects_worker_self_position_command_root_cause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["WorkerManager"] = {
                "active": True,
                "repeat_order_guard_active": True,
                "repeat_order_guard_frames": 32,
                "repeat_order_suppressed_count": 0,
                "self_position_command_block_count": 1,
                "last_self_position_worker_tag": 123,
                "last_self_position_ability": 16,
                "last_self_position_target_kind": "position",
                "last_self_position_target_x": 41.5,
                "last_self_position_target_y": 32.0,
                "last_self_position_distance_sq": 0.01,
                "last_worker_current_order_ability": 0,
                "last_worker_current_order_target_tag": 0,
                "root_cause_status": "self_position_move_blocked",
                "root_cause_reason": "idle_recovery_idle_spot",
                "trace_contract_version": 1,
                "trace_event_count": 18,
                "last_trace_frame": 12_500,
                "last_trace_status": "self_position_blocked",
                "last_trace_reason": "idle_recovery_idle_spot",
                "last_trace_target_kind": "unit_move_position",
            }
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"worker_self_position_command"})
            failure = [
                item
                for item in report.failures
                if item.code == "worker_self_position_command"
            ][0]
            self.assertEqual("idle_recovery_idle_spot", failure.evidence["root_cause_reason"])
            self.assertEqual(123, failure.evidence["worker_tag"])

    def test_allows_legitimate_worker_build_position_at_builder_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            workers = managers["WorkerManager"]
            assert isinstance(workers, dict)
            workers.update(
                {
                    "self_position_command_block_count": 0,
                    "root_cause_status": "none",
                    "root_cause_reason": "none",
                    "last_trace_status": "accepted_candidate",
                    "last_trace_reason": "build_position_command",
                    "last_trace_target_kind": "build_position",
                    "last_trace_target_tag": 0,
                    "last_trace_distance_sq": 0.001,
                }
            )
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            codes = {failure.code for failure in report.failures}
            self.assertNotIn("worker_self_position_command", codes, report.to_dict())

    def test_detects_accepted_worker_move_position_at_self(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            workers = managers["WorkerManager"]
            assert isinstance(workers, dict)
            workers.update(
                {
                    "self_position_command_block_count": 0,
                    "root_cause_status": "none",
                    "root_cause_reason": "none",
                    "last_trace_status": "accepted_candidate",
                    "last_trace_reason": "idle_recovery_idle_spot",
                    "last_trace_target_kind": "unit_move_position",
                    "last_trace_target_tag": 0,
                    "last_trace_distance_sq": 0.001,
                }
            )
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"worker_self_position_command"})
            failure = [
                item
                for item in report.failures
                if item.code == "worker_self_position_command"
            ][0]
            self.assertEqual("unit_move_position", failure.evidence["last_trace_target_kind"])
            self.assertEqual(0, failure.evidence["last_trace_target_tag"])

    def test_detects_missing_worker_root_cause_telemetry_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["WorkerManager"] = {
                "active": True,
                "repeat_order_guard_active": True,
                "repeat_order_guard_frames": 32,
                "repeat_order_suppressed_count": 0,
                "self_position_command_block_count": 0,
                "root_cause_reason": "none",
                "trace_contract_version": 1,
                "trace_event_count": 12,
                "last_trace_frame": 12_500,
                "last_trace_status": "accepted_candidate",
                "last_trace_reason": "mineral_assignment",
                "last_trace_target_kind": "unit",
                "last_trace_target_tag": 4324589569,
                "last_trace_distance_sq": 140.0,
            }
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"worker_root_cause_telemetry_missing"})
            failure = [
                item
                for item in report.failures
                if item.code == "worker_root_cause_telemetry_missing"
            ][0]
            self.assertEqual(["root_cause_status"], failure.evidence["missing_fields"])

    def test_detects_noop_worker_trace_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            workers = managers["WorkerManager"]
            assert isinstance(workers, dict)
            workers["trace_event_count"] = 0
            workers["last_trace_frame"] = 0
            workers["last_trace_status"] = "none"
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"worker_trace_contract_invalid"})

    def test_detects_missing_worker_manager_root_cause_telemetry_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers.pop("WorkerManager")
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"worker_root_cause_telemetry_missing"})
            failure = [
                item
                for item in report.failures
                if item.code == "worker_root_cause_telemetry_missing"
            ][0]
            self.assertEqual(["WorkerManager"], failure.evidence["missing_fields"])

    def test_detects_scout_duplicate_worker_move_root_cause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["WorkerManager"] = {
                "active": True,
                "repeat_order_guard_active": True,
                "repeat_order_guard_frames": 32,
                "repeat_order_suppressed_count": 8,
                "last_repeat_order_worker_tag": 4350279681,
                "last_repeat_order_ability": 16,
                "last_repeat_order_target_kind": "unit_move_position",
                "last_repeat_order_target_x": 31.75,
                "last_repeat_order_target_y": 140.5,
                "self_position_command_block_count": 0,
                "root_cause_status": "duplicate_command_safety_blocked",
                "root_cause_reason": "scout_enemy_region_known_move",
                "trace_contract_version": 1,
                "trace_event_count": 31,
                "last_trace_frame": 12_500,
                "last_trace_status": "duplicate_blocked",
                "last_trace_reason": "scout_enemy_region_known_move",
                "last_trace_target_kind": "unit_move_position",
            }
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"scout_duplicate_worker_move_command"})
            failure = [
                item
                for item in report.failures
                if item.code == "scout_duplicate_worker_move_command"
            ][0]
            self.assertEqual(
                "scout_enemy_region_known_move",
                failure.evidence["root_cause_reason"],
            )
            self.assertEqual(4350279681, failure.evidence["worker_tag"])

    def test_detects_generic_worker_repeat_order_suppression_root_cause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            workers = managers["WorkerManager"]
            assert isinstance(workers, dict)
            workers["repeat_order_suppressed_count"] = 2
            workers["last_repeat_order_worker_tag"] = 777
            workers["last_repeat_order_ability"] = 16
            workers["last_repeat_order_target_kind"] = "unit_move_position"
            workers["last_repeat_order_target_x"] = 42.0
            workers["last_repeat_order_target_y"] = 32.0
            workers["root_cause_status"] = "duplicate_command_safety_blocked"
            workers["root_cause_reason"] = "mineral_distance_optimization_move"
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"worker_repeat_order_suppression"})
            failure = [
                item
                for item in report.failures
                if item.code == "worker_repeat_order_suppression"
            ][0]
            self.assertEqual("mineral_distance_optimization_move", failure.evidence["root_cause_reason"])
            self.assertEqual(777, failure.evidence["worker_tag"])

    def test_detects_archived_scout_duplicate_even_when_latest_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archived = _telemetry(8_000)
            latest = _telemetry(12_500)
            archived_managers = archived["managers"]
            latest_managers = latest["managers"]
            assert isinstance(archived_managers, dict)
            assert isinstance(latest_managers, dict)
            archived_managers["WorkerManager"] = {
                "active": True,
                "repeat_order_guard_active": True,
                "repeat_order_guard_frames": 32,
                "repeat_order_suppressed_count": 4,
                "last_repeat_order_worker_tag": 987,
                "last_repeat_order_ability": 16,
                "last_repeat_order_target_kind": "unit_move_position",
                "last_repeat_order_target_x": 31.75,
                "last_repeat_order_target_y": 140.5,
                "self_position_command_block_count": 0,
                "root_cause_status": "duplicate_command_safety_blocked",
                "root_cause_reason": "scout_under_attack_continue_enemy_base",
                "trace_contract_version": 1,
                "trace_event_count": 22,
                "last_trace_frame": 8_000,
                "last_trace_status": "duplicate_blocked",
                "last_trace_reason": "scout_under_attack_continue_enemy_base",
                "last_trace_target_kind": "unit_move_position",
            }
            latest_managers["WorkerManager"] = {
                "active": True,
                "repeat_order_guard_active": True,
                "repeat_order_guard_frames": 32,
                "repeat_order_suppressed_count": 0,
                "self_position_command_block_count": 0,
                "root_cause_status": "none",
                "root_cause_reason": "none",
                "trace_contract_version": 1,
                "trace_event_count": 24,
                "last_trace_frame": 12_500,
                "last_trace_status": "accepted_candidate",
                "last_trace_reason": "mineral_assignment",
                "last_trace_target_kind": "unit",
            }
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=latest,
                telemetry_archive=[archived, latest],
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"scout_duplicate_worker_move_command"})
            failure = [
                item
                for item in report.failures
                if item.code == "scout_duplicate_worker_move_command"
            ][0]
            self.assertEqual(
                "scout_under_attack_continue_enemy_base",
                failure.evidence["root_cause_reason"],
            )
            self.assertEqual(987, failure.evidence["worker_tag"])

    def test_start_unit_classifier_waits_until_bootstrap_threshold_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = {
                "protocol_version": "voi-mm-bridge/v1",
                "frame": 600,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "CCBot": {
                        "bootstrap_status": "waiting_for_initial_observation",
                        "player_id": 1,
                        "self_count": 0,
                        "resource_depot_count": 0,
                        "game_info_width": 144,
                        "game_info_height": 160,
                        "enemy_start_location_count": 1,
                    }
                },
                "active_modulation_ids": [],
                "last_failure": "bootstrap_waiting",
            }
            self._write_runtime(
                root,
                log_text="Connected to 127.0.0.1:8167\nWaitJoinGame finished successfully.",
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    bootstrap_no_start_units_frame=1_200,
                ),
            )

            self.assertNotIn(
                "bootstrap_no_start_units",
                {failure.code for failure in report.failures},
            )

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

    def test_detects_missing_production_manager_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers.pop("ProductionManager")
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"manager_intervention_missing"})

    def test_rejects_production_axis_only_false_pass_without_queue_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["bounded_intervention"] = True
            production["strategy_doctrine"] = "mech_transition"
            production["last_doctrine_action"] = "none"
            production["last_doctrine_queue_item"] = "none"
            production["last_doctrine_frame"] = 0
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"manager_intervention_missing"})

    def test_rejects_production_doctrine_without_direct_queue_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["last_doctrine_evidence"] = "intent_only"
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"manager_intervention_missing"})

    def test_rejects_stale_production_doctrine_from_previous_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500, update_id="current-update")
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = "current-update"
            production["last_doctrine_update_id"] = "previous-update"
            production["last_doctrine_fresh"] = True
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(update_id="current-update"),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"manager_intervention_missing"})

    def test_rejects_archived_production_action_from_previous_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archived = _telemetry(8_000, update_id="previous-update")
            latest = _telemetry(12_500, update_id="current-update")
            managers = latest["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["last_doctrine_action"] = "none"
            production["last_doctrine_queue_item"] = "none"
            production["last_doctrine_frame"] = 0
            production["last_doctrine_update_id"] = "current-update"
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=latest,
                telemetry_archive=[archived, latest],
                modulation=_modulation(update_id="current-update"),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"manager_intervention_missing"})

    def test_rejects_production_doctrine_mismatch_false_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["strategy_doctrine"] = "mech_transition"
            production["last_doctrine"] = "bio_pressure"
            production["last_doctrine_fresh"] = True
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"manager_intervention_missing"})

    def test_accepts_expected_non_marine_strategy_consumption(self) -> None:
        cases = (
            ("tank_defensive_hold", "factory_techlab", "FactoryTechLab"),
            ("mech_transition", "hellion_harassment", "Hellion"),
            ("drop_harassment", "starport_transition", "Starport"),
            ("expand_macro", "expand_macro", "CommandCenter"),
        )
        for doctrine, action, item in cases:
            with self.subTest(doctrine=doctrine):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    telemetry = _telemetry(12_500, update_id=f"soak-{doctrine}")
                    managers = telemetry["managers"]
                    assert isinstance(managers, dict)
                    production = managers["ProductionManager"]
                    assert isinstance(production, dict)
                    production["policy_update_id"] = f"soak-{doctrine}"
                    production["strategy_doctrine"] = doctrine
                    production["last_doctrine"] = doctrine
                    production["last_doctrine_action"] = action
                    production["last_doctrine_queue_item"] = item
                    production["last_doctrine_update_id"] = f"soak-{doctrine}"
                    production["actual_production_command_issued_count"] = 1
                    production["last_actual_production_command"] = f"build_command|{item}"
                    production["last_actual_production_command_kind"] = "build_command"
                    production["last_actual_production_command_item"] = item
                    production["last_actual_production_command_update_id"] = f"soak-{doctrine}"
                    production["last_actual_production_command_frame"] = 6_260
                    self._write_runtime(
                        root,
                        log_text=MACRO_LOG,
                        telemetry=telemetry,
                        modulation=_modulation(update_id=f"soak-{doctrine}"),
                    )

                    report = classify_micromachine_soak(
                        MicroMachineSoakObservation(
                            blackboard_dir=root,
                            bot_log=root / "micromachine.log",
                        ),
                        MicroMachineSoakConfig(
                            target_frame=12_000,
                            expected_strategy_doctrine=doctrine,
                            expected_production_actions=(action,),
                            expected_production_items=(item,),
                        ),
                    )

                    self.assertTrue(report.ok, report.to_dict())

    def test_accepts_existing_queue_strategy_consumption_with_actual_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-bio_pressure"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = update_id
            production["strategy_doctrine"] = "bio_pressure"
            production["last_doctrine"] = "bio_pressure"
            production["last_doctrine_action"] = "bio_marauder_support"
            production["last_doctrine_queue_item"] = "Marauder"
            production["last_doctrine_evidence"] = "queued_existing"
            production["last_doctrine_update_id"] = update_id
            production["actual_production_command_issued_count"] = 1
            production["last_actual_production_command"] = "train_command|Marauder"
            production["last_actual_production_command_kind"] = "train_command"
            production["last_actual_production_command_item"] = "Marauder"
            production["last_actual_production_command_update_id"] = update_id
            production["last_actual_production_command_frame"] = 6_240
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="bio_pressure",
                    expected_production_actions=("bio_marauder_support",),
                    expected_production_items=("Marauder",),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_accepts_command_issued_strategy_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-expand_macro"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = update_id
            production["strategy_doctrine"] = "expand_macro"
            production["last_doctrine"] = "expand_macro"
            production["last_doctrine_action"] = "expand_macro"
            production["last_doctrine_queue_item"] = "CommandCenter"
            production["last_doctrine_evidence"] = "command_issued"
            production["last_doctrine_update_id"] = update_id
            production["last_doctrine_frame"] = 6_240
            production["actual_production_command_issued_count"] = 1
            production["last_actual_production_command"] = "build_command|CommandCenter"
            production["last_actual_production_command_kind"] = "build_command"
            production["last_actual_production_command_item"] = "CommandCenter"
            production["last_actual_production_command_update_id"] = update_id
            production["last_actual_production_command_frame"] = 6_240
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="expand_macro",
                    expected_production_actions=("expand_macro",),
                    expected_production_items=("CommandCenter",),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_accepts_non_production_scouting_strategy_with_actual_scout_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-scouting_map_control"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = update_id
            production["strategy_doctrine"] = "scouting_map_control"
            production["last_doctrine"] = "none"
            production["last_doctrine_action"] = "none"
            production["last_doctrine_queue_item"] = "none"
            production["last_doctrine_evidence"] = "none"
            production["last_doctrine_update_id"] = ""
            production["last_doctrine_frame"] = 0
            production["last_doctrine_fresh"] = False
            production["actual_production_command_issued_count"] = 0
            production["last_actual_production_command"] = "none|none"
            production["last_actual_production_command_kind"] = "none"
            production["last_actual_production_command_item"] = "none"
            production["last_actual_production_command_update_id"] = ""
            production["last_actual_production_command_frame"] = 0
            managers["ScoutManager"] = {
                "active": True,
                "bounded_intervention": True,
                "scout_priority": 0.9,
                "status": "Enemy base unknown, exploring",
                "actual_command_issued_count": 1,
                "last_actual_command": "move|scout_unknown_far_start_location_move|x=33.5|y=138.5",
                "last_actual_command_frame": 6_260,
                "last_target_distance": 42.0,
                "last_home_distance": 28.0,
                "max_home_distance": 34.0,
                "last_enemy_base_distance": 0.0,
                "min_enemy_base_distance": 0.0,
                "deep_scout_frame_count": 0,
                "consumed_axes": "scouting.scout_priority,scouting.risk_tolerance",
            }
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: Scout policy target selected")),
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="scouting_map_control",
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_accepts_raw_api_actual_production_command_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-tank_defensive_hold"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = update_id
            production["policy_issued_at_frame"] = 6_000
            production["strategy_doctrine"] = "tank_defensive_hold"
            production["last_doctrine"] = "tank_defensive_hold"
            production["last_doctrine_action"] = "factory_techlab"
            production["last_doctrine_queue_item"] = "FactoryTechLab"
            production["last_doctrine_update_id"] = update_id
            production["last_doctrine_frame"] = 6_200
            production["last_doctrine_fresh"] = True
            production["actual_production_command_issued_count"] = 1
            production["last_actual_production_command"] = "addon_build_command|TERRAN_FACTORYTECHLAB"
            production["last_actual_production_command_kind"] = "addon_build_command"
            production["last_actual_production_command_item"] = "TERRAN_FACTORYTECHLAB"
            production["last_actual_production_command_update_id"] = update_id
            production["last_actual_production_command_frame"] = 6_240
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="tank_defensive_hold",
                    expected_production_actions=("factory_techlab",),
                    expected_production_items=("FactoryTechLab",),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_rejects_tank_strategy_when_only_factory_was_issued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-tank_defensive_hold"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = update_id
            production["policy_issued_at_frame"] = 6_000
            production["strategy_doctrine"] = "tank_defensive_hold"
            production["last_doctrine"] = "tank_defensive_hold"
            production["last_doctrine_action"] = "factory_transition"
            production["last_doctrine_queue_item"] = "Factory"
            production["last_doctrine_update_id"] = update_id
            production["last_doctrine_frame"] = 6_200
            production["last_doctrine_fresh"] = True
            production["actual_production_command_issued_count"] = 1
            production["last_actual_production_command"] = "build_command|TERRAN_FACTORY"
            production["last_actual_production_command_kind"] = "build_command"
            production["last_actual_production_command_item"] = "TERRAN_FACTORY"
            production["last_actual_production_command_update_id"] = update_id
            production["last_actual_production_command_frame"] = 6_240
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="tank_defensive_hold",
                    expected_production_actions=("factory_transition",),
                    expected_production_items=("Factory",),
                ),
            )

            self.assert_failure_codes(report, {"strategy_actual_command_missing"})
            failure = [
                item
                for item in report.failures
                if item.code == "strategy_actual_command_missing"
            ][0]
            self.assertEqual(
                ["FactoryTechLab", "SiegeTank"],
                failure.evidence["expected_actual_production_items"],
            )
            self.assertEqual(["Factory"], failure.evidence["observed_actual_items"])

    def test_rejects_expected_strategy_queue_without_actual_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-tank_defensive_hold"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = update_id
            production["strategy_doctrine"] = "tank_defensive_hold"
            production["last_doctrine"] = "tank_defensive_hold"
            production["last_doctrine_action"] = "factory_transition"
            production["last_doctrine_queue_item"] = "Factory"
            production["last_doctrine_update_id"] = update_id
            production["actual_production_command_issued_count"] = 0
            production["last_actual_production_command"] = "none|none"
            production["last_actual_production_command_kind"] = "none"
            production["last_actual_production_command_item"] = "none"
            production["last_actual_production_command_update_id"] = ""
            production["last_actual_production_command_frame"] = 0
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="tank_defensive_hold",
                    expected_production_actions=("factory_transition",),
                    expected_production_items=("Factory",),
                ),
            )

            self.assert_failure_codes(report, {"strategy_actual_command_missing"})
            failure = [
                item
                for item in report.failures
                if item.code == "strategy_actual_command_missing"
            ][0]
            self.assertEqual(
                ["FactoryTechLab", "SiegeTank"],
                failure.evidence["expected_actual_production_items"],
            )
            self.assertEqual([], failure.evidence["observed_actual_items"])

    def test_rejects_expected_strategy_consumption_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="tank_defensive_hold",
                    expected_production_actions=("factory_transition",),
                    expected_production_items=("Factory",),
                ),
            )

            self.assert_failure_codes(report, {"strategy_consumption_mismatch"})
            failure = [
                item
                for item in report.failures
                if item.code == "strategy_consumption_mismatch"
            ][0]
            self.assertEqual(
                "tank_defensive_hold",
                failure.evidence["expected_strategy_doctrine"],
            )
            self.assertIn("bio_pressure", failure.evidence["observed_doctrines"])

    def test_rejects_expected_strategy_from_stale_archive_when_latest_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archived = _telemetry(8_000, update_id="previous-update")
            archived_managers = archived["managers"]
            assert isinstance(archived_managers, dict)
            archived_production = archived_managers["ProductionManager"]
            assert isinstance(archived_production, dict)
            archived_production["policy_update_id"] = "previous-update"
            archived_production["policy_issued_at_frame"] = 7_500
            archived_production["strategy_doctrine"] = "tank_defensive_hold"
            archived_production["last_doctrine"] = "tank_defensive_hold"
            archived_production["last_doctrine_action"] = "factory_transition"
            archived_production["last_doctrine_queue_item"] = "Factory"
            archived_production["last_doctrine_evidence"] = "queued"
            archived_production["last_doctrine_update_id"] = "previous-update"
            archived_production["last_doctrine_frame"] = 7_900

            latest = _telemetry(12_500, update_id="current-update")
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=latest,
                telemetry_archive=[archived, latest],
                modulation=_modulation(update_id="current-update"),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="tank_defensive_hold",
                    expected_production_actions=("factory_transition",),
                    expected_production_items=("Factory",),
                ),
            )

            self.assert_failure_codes(report, {"strategy_consumption_mismatch"})

    def test_rejects_bio_pressure_marine_only_when_support_path_expected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="bio_pressure",
                    expected_production_actions=(
                        "bio_marauder_techlab",
                        "bio_marauder_support",
                        "starport_transition",
                        "medivac_drop_support",
                    ),
                    expected_production_items=(
                        "BarracksTechLab",
                        "Marauder",
                        "Starport",
                        "Medivac",
                    ),
                ),
            )

            self.assert_failure_codes(report, {"strategy_consumption_mismatch"})

    def test_rejects_bio_pressure_support_strategy_with_only_marine_actual_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-bio-pressure-support"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["policy_update_id"] = update_id
            production["strategy_doctrine"] = "bio_pressure"
            production["last_doctrine"] = "bio_pressure"
            production["last_doctrine_action"] = "bio_marauder_support"
            production["last_doctrine_queue_item"] = "Marauder"
            production["last_doctrine_evidence"] = "queued"
            production["last_doctrine_update_id"] = update_id
            production["last_doctrine_frame"] = 6_200
            production["last_doctrine_fresh"] = True
            production["actual_production_command_issued_count"] = 1
            production["last_actual_production_command"] = "train_command|Marine"
            production["last_actual_production_command_kind"] = "train_command"
            production["last_actual_production_command_item"] = "Marine"
            production["last_actual_production_command_update_id"] = update_id
            production["last_actual_production_command_frame"] = 6_240
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_strategy_doctrine="bio_pressure",
                    expected_production_actions=("bio_marauder_support",),
                    expected_production_items=("Marauder",),
                ),
            )

            self.assert_failure_codes(report, {"strategy_actual_command_missing"})
            [failure] = [
                item
                for item in report.failures
                if item.code == "strategy_actual_command_missing"
            ]
            self.assertNotIn("Marine", failure.evidence["expected_actual_production_items"])
            self.assertIn("Marauder", failure.evidence["expected_actual_production_items"])
            self.assertIn("Marine", failure.evidence["observed_actual_items"])

    def test_rejects_non_fresh_production_doctrine_false_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["last_doctrine_fresh"] = False
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000),
            )

            self.assert_failure_codes(report, {"manager_intervention_missing"})

    def test_waits_for_modulation_consumption_grace_during_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(0, policy_active=False)
            telemetry["active_modulation_ids"] = []
            self._write_runtime(
                root,
                log_text="Connected to 127.0.0.1:8167\nWaitJoinGame finished successfully.",
                telemetry=telemetry,
                modulation={
                    **_modulation(update_id="soak-defensive-hold"),
                    "issued_at_frame": 0,
                },
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    bot_running=True,
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    modulation_consumption_grace_frames=128,
                ),
            )

            self.assertNotIn("stale_modulation", {failure.code for failure in report.failures})
            self.assertFalse(report.ok)

    def test_detects_unconsumed_modulation_after_consumption_grace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(129, policy_active=False)
            telemetry["active_modulation_ids"] = []
            self._write_runtime(
                root,
                log_text="Connected to 127.0.0.1:8167\nWaitJoinGame finished successfully.",
                telemetry=telemetry,
                modulation={
                    **_modulation(update_id="soak-defensive-hold"),
                    "issued_at_frame": 0,
                },
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    bot_running=True,
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    modulation_consumption_grace_frames=128,
                ),
            )

            self.assert_failure_codes(report, {"stale_modulation"})

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

    def test_income_stall_allows_recent_harvest_telemetry_when_score_rate_is_zero(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale_income_log = MACRO_LOG.replace(
                "11200: drawProductionInformation", "6048: drawProductionInformation"
            )
            telemetry = _telemetry(12_500)
            telemetry["economy"] = {
                "minerals": 0,
                "vespene": 0,
                "mineral_income": 0,
                "vespene_income": 0,
                "self_worker_count": 9,
                "idle_worker_count": 0,
                "harvest_gather_order_count": 8,
                "harvest_return_order_count": 1,
                "sample_worker_orders": [
                    {
                        "ability": 3666,
                        "target_type": 666,
                        "target_mineral_contents": 1100,
                        "target_dist_sq": 2,
                    }
                ],
            }
            self._write_runtime(root, log_text=stale_income_log, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, income_stall_frames=2_000),
            )

            self.assertNotIn("income_stall", {failure.code for failure in report.failures})

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

    def test_income_stall_allows_recent_worker_combat_even_with_gas_demand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            combat_log = "\n".join(
                (
                    MACRO_LOG,
                    "11449: drawProductionInformation | Production Information",
                    "Gas Worker Target:3",
                    "Mineral income:       0",
                    "Gas income:       0",
                    "Worker jobs M/G/B/C/I/S/N:0/0/0/8/5/0/-17",
                    "11949: monitorUnitActions | Actions (total, APM, last frame): 3300, 695, 4",
                )
            )
            self._write_runtime(root, log_text=combat_log, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, income_stall_frames=2_000),
            )

            self.assertNotIn("income_stall", {failure.code for failure in report.failures})

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

    def test_detects_target_frame_with_stale_unit_production_as_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale_opening_log = MACRO_LOG.replace(
                "4590: create | create unit item=Marine result=1", ""
            ).replace("6100: create | create unit item=Marine result=1", "").replace(
                "11280: enforceBarracksProductionContinuity | continuity accepted unit training order=Marine",
                "",
            )
            stale_unit_log = "\n".join(
                (
                    stale_opening_log,
                    "2081: create | create unit item=Marine result=1",
                    "8070: drawProductionInformation | Production Information",
                    "Barracks                     [-160] (B)",
                    "Stimpack                     [-170] (B)",
                    "Free Mineral:     2250",
                    "Free Gas:         656",
                    "Gas income:       179",
                    "8641: updateAttackSquads | Cancel offensive (-70.13%, 1 ally supply vs 0 enemy supply)",
                    "12350: drawProductionInformation | Production Information",
                    "Barracks                     [-160] (B)",
                    "Stimpack                     [-170] (B)",
                )
            )
            self._write_runtime(root, log_text=stale_unit_log, telemetry=_telemetry(12_350))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(target_frame=12_000, production_stall_frames=8_000),
            )

            self.assert_failure_codes(report, {"unit_production_stall"})

    def test_rejects_continuity_train_attempt_as_unit_production_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_text = "\n".join(
                (
                    MACRO_LOG.replace(
                        "11280: enforceBarracksProductionContinuity | continuity accepted unit training order=Marine",
                        "",
                    ),
                    "11280: enforceBarracksProductionContinuity | continuity train command item=Marine",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=_telemetry(12_100))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                    bot_running=False,
                ),
                MicroMachineSoakConfig(target_frame=12_000, production_stall_frames=100),
            )

            self.assert_failure_codes(report, {"unit_production_stall"})

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
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            production = managers["ProductionManager"]
            assert isinstance(production, dict)
            production["last_doctrine_frame"] = 11_600
            self._write_runtime(
                root,
                log_text=MACRO_LOG,
                telemetry=telemetry,
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

    def test_detects_missing_expected_strategy_profile_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=_telemetry(12_500))

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_profile_tags=("aggressive_pressure", "tech_transition"),
                ),
            )

            self.assert_failure_codes(report, {"strategy_profile_missing"})
            missing = [
                failure
                for failure in report.failures
                if failure.code == "strategy_profile_missing"
            ][0]
            self.assertEqual(["tech_transition"], missing.evidence["missing"])

    def test_requires_expected_tactical_effect_evidence_at_target_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["actual_command_issued_count"] = 0
            combat["last_action_frame"] = 0
            combat["last_issued_action_frame"] = 0
            combat["last_issued_action"] = ""
            combat["main_attack_actual_command_issued_count"] = 0
            combat["main_attack_last_action_frame"] = 0
            combat["main_attack_last_issued_action"] = ""
            combat["main_attack_order_status"] = "Waiting"
            self._write_runtime(root, log_text=MACRO_LOG, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_effect_missing"})
            failure = [
                item for item in report.failures if item.code == "tactical_effect_missing"
            ][0]
            self.assertEqual(["pressure"], failure.evidence["missing_effects"])
            self.assertEqual("missing", failure.evidence["status"])

    def test_passes_expected_tactical_effect_when_behavior_log_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["Squad"] = {
                "active": True,
                "contain_bias": 0.35,
                "scope_location_intent": "enemy_natural",
                "selected_target_class": "worker_line",
                "consumed_axes": "squad.contain_bias,combat.target_priority_biases.*",
            }
            log_text = "\n".join(
                (
                    MACRO_LOG,
                    "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                    "12455: calcTargets | target worker_line selected by policy modulation",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure", "contain", "target_priority"),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())
            assert report.tactical_evidence is not None
            self.assertEqual("passed", report.tactical_evidence.status)
            self.assertEqual((), report.tactical_evidence.missing_effects)

    def test_accepts_pressure_when_generic_latest_action_is_scout_but_main_attack_command_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["last_issued_action_frame"] = 6_300
            combat["last_issued_action"] = (
                "MoveToGoalOrder|squad=Scout|type=2|x=142.5|y=33.5"
            )
            combat["main_attack_actual_command_issued_count"] = 3
            combat["main_attack_last_action_frame"] = 6_260
            combat["main_attack_last_issued_action"] = (
                "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
            )
            managers["Squad"] = {
                "active": True,
                "contain_bias": 0.35,
                "scope_location_intent": "enemy_natural",
                "selected_target_class": "worker_line",
                "consumed_axes": "squad.contain_bias,combat.target_priority_biases.*",
            }
            self._write_runtime(
                root,
                log_text="\n".join(
                    (
                        MACRO_LOG,
                        "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                        "12455: calcTargets | target worker_line selected by policy modulation",
                    )
                ),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure", "contain", "target_priority"),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_rejects_pressure_command_without_live_main_attack_movement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["main_attack_home_distance"] = 3.0
            combat["main_attack_max_home_distance"] = 4.0
            self._write_runtime(
                root,
                log_text="\n".join(
                    (
                        MACRO_LOG,
                        "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                    )
                ),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})
            failure = [
                item
                for item in report.failures
                if item.code == "tactical_actual_command_missing"
            ][0]
            self.assertEqual(12.0, failure.evidence["combat"]["required_main_attack_max_home_distance"])
            self.assertEqual(4.0, failure.evidence["combat"]["main_attack_max_home_distance"])

    def test_rejects_tactical_effect_without_actual_combat_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["actual_command_issued_count"] = 0
            combat["last_action_frame"] = 0
            combat["last_issued_action_frame"] = 0
            combat["last_issued_action"] = ""
            combat["main_attack_actual_command_issued_count"] = 0
            combat["main_attack_last_action_frame"] = 0
            combat["main_attack_last_issued_action"] = ""
            managers["Squad"] = {
                "active": True,
                "contain_bias": 0.35,
                "scope_location_intent": "enemy_natural",
                "selected_target_class": "worker_line",
                "consumed_axes": "squad.contain_bias,combat.target_priority_biases.*",
            }
            log_text = "\n".join(
                (
                    MACRO_LOG,
                    "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                )
            )
            self._write_runtime(root, log_text=log_text, telemetry=telemetry)

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

    def test_rejects_pressure_effect_when_latest_combat_command_is_not_main_attack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["actual_command_issued_count"] = 3
            combat["last_action_frame"] = 6_260
            combat["last_issued_action_frame"] = 6_260
            combat["last_issued_action"] = (
                "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
            )
            combat["main_attack_actual_command_issued_count"] = 3
            combat["main_attack_last_action_frame"] = 6_260
            combat["main_attack_last_issued_action"] = (
                "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
            )
            combat["main_attack_order_status"] = "Waiting"
            managers["Squad"] = {
                "active": True,
                "contain_bias": 0.35,
                "scope_location_intent": "enemy_natural",
                "selected_target_class": "worker_line",
                "consumed_axes": "squad.contain_bias,combat.target_priority_biases.*",
            }
            self._write_runtime(
                root,
                log_text="\n".join(
                    (
                        MACRO_LOG,
                        "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                    )
                ),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

    def test_rejects_pressure_effect_when_only_stale_action_frame_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["actual_command_issued_count"] = 3
            combat["last_action_frame"] = 6_260
            combat["last_issued_action_frame"] = 0
            combat["last_issued_action"] = (
                "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
            )
            combat["main_attack_actual_command_issued_count"] = 3
            combat["main_attack_last_action_frame"] = 0
            combat["main_attack_last_issued_action"] = (
                "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
            )
            combat["main_attack_order_status"] = "Attack"
            self._write_runtime(
                root,
                log_text="\n".join(
                    (
                        MACRO_LOG,
                        "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                    )
                ),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

    def test_rejects_pressure_effect_when_only_generic_main_attack_action_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["actual_command_issued_count"] = 3
            combat["last_action_frame"] = 6_260
            combat["last_issued_action_frame"] = 6_260
            combat["last_issued_action"] = (
                "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
            )
            combat["main_attack_actual_command_issued_count"] = 0
            combat["main_attack_last_action_frame"] = 0
            combat["main_attack_last_issued_action"] = ""
            combat["main_attack_order_status"] = "Attack"
            self._write_runtime(
                root,
                log_text="\n".join(
                    (
                        MACRO_LOG,
                        "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                    )
                ),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

    def test_rejects_pressure_effect_when_main_attack_frame_missing_but_generic_frame_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["actual_command_issued_count"] = 3
            combat["last_action_frame"] = 6_260
            combat["last_issued_action_frame"] = 6_260
            combat["last_issued_action"] = (
                "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
            )
            combat["main_attack_actual_command_issued_count"] = 3
            combat["main_attack_last_action_frame"] = 0
            combat["main_attack_last_issued_action"] = (
                "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
            )
            combat["main_attack_order_status"] = "Attack"
            self._write_runtime(
                root,
                log_text="\n".join(
                    (
                        MACRO_LOG,
                        "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                    )
                ),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("pressure",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

    def test_accepts_expected_scout_effect_with_scout_command_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["ScoutManager"] = {
                "active": True,
                "bounded_intervention": True,
                "scout_priority": 0.9,
                "status": "Enemy base unknown, exploring",
                "actual_command_issued_count": 1,
                "last_actual_command": "move|scout_unknown_far_start_location_move|x=33.5|y=138.5",
                "last_actual_command_frame": 6_260,
                "last_target_distance": 42.0,
                "last_home_distance": 28.0,
                "max_home_distance": 34.0,
                "last_enemy_base_distance": 0.0,
                "min_enemy_base_distance": 0.0,
                "deep_scout_frame_count": 0,
                "consumed_axes": "scouting.scout_priority,scouting.risk_tolerance",
            }
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: Scout policy target selected")),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_rejects_scout_with_units_task_when_only_squad_assignment_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-marine-scout"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["TacticalTask"] = {
                "active": True,
                "task_type": "scout_with_units",
                "task_id": "marine-scout-3",
                "status": "executing",
                "reason": "CombatCommander assigned a fresh Scout squad order for this task",
                "consumed_by": "CombatCommander,Squad",
                "actual_command_issued_count": 3,
                "last_actual_command": "ScoutSquadOrder|assigned_units=3",
                "last_actual_command_frame": 6_260,
            }
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["scout_scope_status"] = "Consumed"
            combat["scout_scope_assigned_unit_count"] = 3
            combat["scout_actual_command_issued_count"] = 0
            combat["scout_last_issued_action"] = ""
            combat["scout_last_action_frame"] = 0
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: Scout squad assigned")),
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})
            failure = [
                item
                for item in report.failures
                if item.code == "tactical_actual_command_missing"
            ][0]
            self.assertEqual(["scout_actual_command"], failure.evidence["missing"])
            self.assertEqual(
                "squad=Scout",
                failure.evidence["scout"]["required_actual_command"],
            )

    def test_rejects_scout_with_units_task_when_only_scout_manager_fallback_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-marine-scout"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["TacticalTask"] = {
                "active": True,
                "task_type": "scout_with_units",
                "task_id": "marine-scout-3",
                "status": "executing",
                "reason": "Scout target selected, but no Scout squad command issued",
                "consumed_by": "CombatCommander,Squad",
                "actual_command_issued_count": 0,
                "last_actual_command": "",
                "last_actual_command_frame": 0,
            }
            managers["ScoutManager"] = {
                "active": True,
                "bounded_intervention": True,
                "scout_priority": 0.9,
                "status": "legacy scout worker exploring",
                "actual_command_issued_count": 2,
                "last_actual_command": "move|scout_enemy_region_known_move|x=33.5|y=138.5",
                "last_actual_command_frame": 6_260,
                "last_target_distance": 42.0,
                "last_home_distance": 28.0,
                "max_home_distance": 34.0,
                "last_enemy_base_distance": 0.0,
                "min_enemy_base_distance": 0.0,
                "deep_scout_frame_count": 12,
            }
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["scout_actual_command_issued_count"] = 0
            combat["scout_last_issued_action"] = ""
            combat["scout_last_action_frame"] = 0
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: legacy ScoutManager moved worker")),
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

    def test_accepts_scout_with_units_task_only_with_actual_scout_squad_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-marine-scout"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["TacticalTask"] = {
                "active": True,
                "task_type": "scout_with_units",
                "task_id": "marine-scout-3",
                "status": "executing",
                "reason": "CombatCommander issued a fresh Scout squad unit command for this task",
                "consumed_by": "CombatCommander,Squad",
                "actual_command_issued_count": 1,
                "last_actual_command": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                "last_actual_command_frame": 6_260,
            }
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["scout_scope_status"] = "Consumed"
            combat["scout_scope_assigned_unit_count"] = 3
            combat["scout_actual_command_issued_count"] = 1
            combat["scout_last_issued_action"] = "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
            combat["scout_last_action_frame"] = 6_260
            combat["scout_home_distance"] = 10.0
            combat["scout_max_home_distance"] = 16.0
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: Scout squad action issued")),
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assertTrue(report.ok, report.to_dict())

    def test_rejects_scout_with_units_command_without_live_combat_scout_movement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_id = "soak-marine-scout"
            telemetry = _telemetry(12_500, update_id=update_id)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            managers["TacticalTask"] = {
                "active": True,
                "task_type": "scout_with_units",
                "task_id": "marine-scout-3",
                "status": "executing",
                "reason": "CombatCommander issued a Scout squad command",
                "consumed_by": "CombatCommander,Squad",
                "actual_command_issued_count": 1,
                "last_actual_command": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                "last_actual_command_frame": 6_260,
            }
            combat = managers["CombatCommander"]
            assert isinstance(combat, dict)
            combat["scout_scope_status"] = "Consumed"
            combat["scout_scope_assigned_unit_count"] = 3
            combat["scout_actual_command_issued_count"] = 1
            combat["scout_last_issued_action"] = "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
            combat["scout_last_action_frame"] = 6_260
            combat["scout_home_distance"] = 2.0
            combat["scout_max_home_distance"] = 3.0
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: Scout squad action issued")),
                telemetry=telemetry,
                modulation=_modulation(update_id=update_id),
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})
            failure = [
                item
                for item in report.failures
                if item.code == "tactical_actual_command_missing"
            ][0]
            self.assertEqual(8.0, failure.evidence["scout"]["required_scout_max_home_distance"])
            self.assertEqual(3.0, failure.evidence["scout"]["scout_max_home_distance"])

    def test_rejects_scout_effect_without_actual_scout_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            scout = managers["ScoutManager"]
            assert isinstance(scout, dict)
            scout["actual_command_issued_count"] = 0
            scout["last_actual_command"] = ""
            scout["last_actual_command_frame"] = 0
            scout["status"] = "policy scout target selected"
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: Scout policy target selected")),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

    def test_rejects_shallow_scout_command_without_depth_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = _telemetry(12_500)
            managers = telemetry["managers"]
            assert isinstance(managers, dict)
            scout = managers["ScoutManager"]
            assert isinstance(scout, dict)
            scout["status"] = "Enemy base unknown, shallow command only"
            scout["actual_command_issued_count"] = 4
            scout["last_actual_command"] = "move|scout_unknown_start_location_move|x=31.0|y=32.0"
            scout["last_actual_command_frame"] = 6_260
            scout["last_target_distance"] = 3.0
            scout["last_home_distance"] = 2.0
            scout["max_home_distance"] = 4.0
            scout["last_enemy_base_distance"] = 0.0
            scout["min_enemy_base_distance"] = 0.0
            scout["deep_scout_frame_count"] = 0
            self._write_runtime(
                root,
                log_text="\n".join((MACRO_LOG, "6260: Scout policy target selected")),
                telemetry=telemetry,
            )

            report = classify_micromachine_soak(
                MicroMachineSoakObservation(
                    blackboard_dir=root,
                    bot_log=root / "micromachine.log",
                ),
                MicroMachineSoakConfig(
                    target_frame=12_000,
                    expected_tactical_effects=("scout",),
                ),
            )

            self.assert_failure_codes(report, {"tactical_actual_command_missing"})

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
        telemetry_archive: list[dict[str, object]] | None = None,
    ) -> None:
        (root / "micromachine.log").write_text(log_text + "\n")
        (root / "latest_telemetry.json").write_text(json.dumps(telemetry) + "\n")
        archive_entries = telemetry_archive or [telemetry]
        (root / "telemetry.jsonl").write_text(
            "".join(json.dumps(entry) + "\n" for entry in archive_entries)
        )
        (root / "latest_modulation.json").write_text(
            json.dumps(modulation or _modulation()) + "\n"
        )
        (root / "modulation_updates.jsonl").write_text(
            json.dumps(modulation or _modulation()) + "\n"
        )


if __name__ == "__main__":
    unittest.main()
