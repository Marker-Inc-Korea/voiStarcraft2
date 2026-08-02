"""Tests for reproducible MicroMachine build identity reports."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_build_identity import (
    MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
    MICROMACHINE_REQUIRED_NATIVE_TESTS,
    MicroMachineBuildIdentityConfig,
    _ctest_registry_attestation,
    build_argument_parser,
    build_micromachine_build_identity,
    build_runtime_workspace_identity,
    inspect_git_worktree_state,
    micromachine_build_identity_admission_error,
    read_build_identity,
    write_build_identity_report,
    write_micromachine_build_attestation,
    write_micromachine_embedded_build_identity_header,
    write_micromachine_source_attestation,
)


class MicroMachineBuildIdentityTest(unittest.TestCase):
    def test_live_admission_requires_the_supported_schema(self) -> None:
        self.assertEqual(78, MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION)
        passing = {
            "schema_version": MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
            "identity": "sha256:fixture",
            "ok": True,
            "failures": [],
        }

        self.assertEqual(
            "",
            micromachine_build_identity_admission_error(passing, passing),
        )
        for side in ("recorded", "current"):
            with self.subTest(side=side):
                recorded = dict(passing)
                current = dict(passing)
                target = recorded if side == "recorded" else current
                target["schema_version"] = (
                    MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION - 1
                )
                error = micromachine_build_identity_admission_error(
                    recorded,
                    current,
                )
                self.assertIn(f"unsupported {side}", error)
                self.assertIn(
                    str(MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION),
                    error,
                )

    def test_runtime_workspace_identity_covers_dirty_python_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "starcraft_commander"
            package.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")
            runtime = package / "runtime.py"
            runtime.write_text("VALUE = 1\n")

            first = build_runtime_workspace_identity(root)
            runtime.write_text("VALUE = 2\n")
            second = build_runtime_workspace_identity(root)
            untracked = package / "new_runtime.py"
            untracked.write_text("NEW_VALUE = 1\n")
            third = build_runtime_workspace_identity(root)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(second["identity"], third["identity"])
            self.assertEqual(
                [
                    "pyproject.toml",
                    "starcraft_commander/new_runtime.py",
                    "starcraft_commander/runtime.py",
                ],
                [entry["path"] for entry in third["files"]],
            )

    def test_expected_build_identity_is_stable_and_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)

            report = build_micromachine_build_identity(config)
            output = root / "identity.json"
            write_build_identity_report(report, output)

            self.assertTrue(report["ok"], report)
            self.assertEqual(
                MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
                report["schema_version"],
            )
            self.assertTrue(str(report["identity"]).startswith("sha256:"))
            self.assertEqual(report["identity"], read_build_identity(output))
            self.assertIn(
                "micromachine_atomic_telemetry_publication_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_atomic_telemetry_publication_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_contextual_transfer_choice_projection_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_contextual_transfer_choice_projection_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_autonomous_owner_composition_evidence_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_autonomous_owner_composition_evidence_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_battlefield_review_closure_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_battlefield_review_closure_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_bounded_terminal_operation_hud_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_bounded_terminal_operation_hud_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_deterministic_pre_live_journey_adapter_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_deterministic_pre_live_journey_adapter_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_production_path_journey_review_closure_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_production_path_journey_review_closure_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "native_test_registry_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_state_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_operation_state_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_addon_recovery_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_addon_recovery_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_grounded_addon_candidate_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_grounded_addon_candidate_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_guaranteed_producer_grounding_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_guaranteed_producer_grounding_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_emergency_land_query_fallback_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_emergency_land_query_fallback_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_grounded_production_observed_targeting_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_grounded_production_observed_targeting_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_exact_composition_production_progress_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_exact_composition_production_progress_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_production_resource_operation_persistence_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_production_resource_operation_persistence_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_live_operation_unblock_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_live_operation_unblock_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_stable_flank_stage_latch_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_stable_flank_stage_latch_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_production_staging_observed_operation_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_production_staging_observed_operation_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_addon_query_footprint_validation_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_addon_query_footprint_validation_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_authoritative_addon_placement_query_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_authoritative_addon_placement_query_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_authoritative_addon_execution_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_authoritative_addon_execution_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_continuous_army_macro_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_continuous_army_macro_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_continuous_army_economy_scaling_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_continuous_army_economy_scaling_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_standing_composition_reinforcement_waves_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_standing_composition_reinforcement_waves_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_offensive_sweep_self_base_exclusion_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_offensive_sweep_self_base_exclusion_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_bounded_placement_query_cache_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_bounded_placement_query_cache_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_production_facility_stability_tank_recovery_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_production_facility_stability_tank_recovery_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_balanced_composition_wave_production_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_balanced_composition_wave_production_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_exact_composition_production_unblock_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_exact_composition_production_unblock_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_continuous_combat_production_relaunch_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_continuous_combat_production_relaunch_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_resource_throughput_expansion_backoff_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_resource_throughput_expansion_backoff_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_startup_telemetry_initialization_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_startup_telemetry_initialization_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_gas_worker_completion_cap_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_gas_worker_completion_cap_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_stable_offensive_sweep_target_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_stable_offensive_sweep_target_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_adaptive_support_composition_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_adaptive_support_composition_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_scoped_adaptive_combat_closure_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_operation_scoped_adaptive_combat_closure_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_review_closure_operation_identity_full_composition_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_review_closure_operation_identity_full_composition_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_semantic_operation_production_closure_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_semantic_operation_production_closure_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_adaptive_pressure_stable_operation_key_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_adaptive_pressure_stable_operation_key_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_tactical_nuke_command_hierarchy_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_tactical_nuke_command_hierarchy_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_location_intent_target_lock_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_location_intent_target_lock_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_terran_ability_execution_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_explicit_terran_ability_execution_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_scout_command_epoch_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_explicit_scout_command_epoch_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_ability_caster_production_priority_patch",
                report["paths"],
            )
            self.assertIn(
                (
                    "micromachine_explicit_ability_caster_production_priority_"
                    "patch_sha256"
                ),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_ability_observation_confirmation_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_explicit_ability_observation_confirmation_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_ability_production_isolation_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_explicit_ability_production_isolation_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_ability_attempt_lifecycle_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_explicit_ability_attempt_lifecycle_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_ability_review_closure_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_explicit_ability_review_closure_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_authoritative_addon_runtime_clearance_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_authoritative_addon_runtime_clearance_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_banshee_unit_specific_cloak_command_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_banshee_unit_specific_cloak_command_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_allied_cloak_observation_confirmation_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_allied_cloak_observation_confirmation_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_ability_caster_ownership_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_explicit_ability_caster_ownership_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_explicit_ability_staging_single_flight_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_explicit_ability_staging_single_flight_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_all_terran_combat_scouts_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_all_terran_combat_scouts_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_parallel_operations_ingame_hud_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_parallel_operations_ingame_hud_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_parallel_operation_lifecycle_review_closure_patch",
                report["paths"],
            )
            self.assertIn(
                (
                    "micromachine_parallel_operation_lifecycle_review_"
                    "closure_patch_sha256"
                ),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_authoritative_parallel_operation_lifecycle_patch",
                report["paths"],
            )
            self.assertIn(
                (
                    "micromachine_authoritative_parallel_operation_"
                    "lifecycle_patch_sha256"
                ),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_production_ownership_restore_proof_patch",
                report["paths"],
            )
            self.assertIn(
                (
                    "micromachine_operation_production_ownership_restore_"
                    "proof_patch_sha256"
                ),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_production_review_closure_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_operation_production_review_closure_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_production_fifo_zero_owner_cleanup_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_production_fifo_zero_owner_cleanup_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_edit_ownership_handoff_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_operation_edit_ownership_handoff_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_edit_review_closure_patch",
                report["paths"],
            )
            self.assertIn(
                "micromachine_operation_edit_review_closure_patch_sha256",
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_transfer_atomic_admission_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_operation_transfer_atomic_admission_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_transfer_runtime_preservation_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_operation_transfer_runtime_preservation_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_transfer_transactional_closure_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_operation_transfer_transactional_closure_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_operation_transfer_final_review_closure_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_operation_transfer_final_review_closure_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                ("micromachine_operation_transfer_idempotence_active_evidence_patch"),
                report["paths"],
            )
            self.assertIn(
                (
                    "micromachine_operation_transfer_idempotence_"
                    "active_evidence_patch_sha256"
                ),
                report["checksums"],
            )
            self.assertIn(
                (
                    "micromachine_runtime_convergence_defense_"
                    "placement_information_patch"
                ),
                report["paths"],
            )
            self.assertIn(
                (
                    "micromachine_runtime_convergence_defense_"
                    "placement_information_patch_sha256"
                ),
                report["checksums"],
            )
            self.assertIn(
                "micromachine_all_terran_harass_capability_evidence_patch",
                report["paths"],
            )
            self.assertIn(
                ("micromachine_all_terran_harass_capability_evidence_patch_sha256"),
                report["checksums"],
            )
            self.assertIn(
                ("micromachine_authoritative_battlefield_ownership_readiness_patch"),
                report["paths"],
            )
            self.assertIn(
                (
                    "micromachine_authoritative_battlefield_ownership_"
                    "readiness_patch_sha256"
                ),
                report["checksums"],
            )
            self.assertIn("source_attestation", report["paths"])
            self.assertIn("s2client_build_dir", report["paths"])
            self.assertIn("source_attestation_sha256", report["checksums"])
            self.assertIn("s2client_build_state_sha256", report["checksums"])
            self.assertEqual(
                report["checksums"]["micromachine_patch_sha256"],
                build_micromachine_build_identity(config)["checksums"][
                    "micromachine_patch_sha256"
                ],
            )
            json.dumps(report)

    def test_missing_binary_marks_build_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=False)

            report = build_micromachine_build_identity(config)
            output = root / "identity.json"
            write_build_identity_report(report, output)

            self.assertFalse(report["ok"])
            self.assertEqual(report["identity"], read_build_identity(output))
            self.assertIn(
                "missing_binary",
                {failure["code"] for failure in report["failures"]},
            )

    def test_missing_git_provenance_marks_build_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True, git_provenance=False)

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "missing_micromachine_git_provenance",
                {failure["code"] for failure in report["failures"]},
            )
            self.assertIn(
                "missing_s2client_git_provenance",
                {failure["code"] for failure in report["failures"]},
            )

    def test_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_patch.write_text("different patch\n")

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"]["micromachine_patch_sha256"],
                second["checksums"]["micromachine_patch_sha256"],
            )

    def test_tactical_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_tactical_patch.write_text("different tactical patch\n")

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"]["micromachine_tactical_patch_sha256"],
                second["checksums"]["micromachine_tactical_patch_sha256"],
            )

    def test_production_fix_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_production_fix_patch.write_text(
                "different production fix patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"]["micromachine_production_fix_patch_sha256"],
                second["checksums"]["micromachine_production_fix_patch_sha256"],
            )

    def test_live_operation_unblock_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_live_operation_unblock_patch.write_text(
                "different live operation unblock patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"]["micromachine_live_operation_unblock_patch_sha256"],
                second["checksums"]["micromachine_live_operation_unblock_patch_sha256"],
            )

    def test_stable_flank_stage_latch_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_stable_flank_stage_latch_patch.write_text(
                "different stable flank stage latch patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_stable_flank_stage_latch_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_stable_flank_stage_latch_patch_sha256"
                ],
            )

    def test_production_staging_observed_operation_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_production_staging_observed_operation_patch.write_text(
                "different production staging observed operation patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_production_staging_observed_operation_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_production_staging_observed_operation_patch_sha256"
                ],
            )

    def test_addon_query_footprint_validation_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_addon_query_footprint_validation_patch.write_text(
                "different addon query footprint validation patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_addon_query_footprint_validation_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_addon_query_footprint_validation_patch_sha256"
                ],
            )

    def test_authoritative_addon_placement_query_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_authoritative_addon_placement_query_patch.write_text(
                "different authoritative addon placement query patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_authoritative_addon_placement_query_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_authoritative_addon_placement_query_patch_sha256"
                ],
            )

    def test_authoritative_addon_execution_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_authoritative_addon_execution_patch.write_text(
                "different authoritative addon execution patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_authoritative_addon_execution_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_authoritative_addon_execution_patch_sha256"
                ],
            )

    def test_operation_state_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_operation_state_patch.write_text(
                "different operation state patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"]["micromachine_operation_state_patch_sha256"],
                second["checksums"]["micromachine_operation_state_patch_sha256"],
            )

    def test_addon_recovery_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_addon_recovery_patch.write_text(
                "different addon recovery patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"]["micromachine_addon_recovery_patch_sha256"],
                second["checksums"]["micromachine_addon_recovery_patch_sha256"],
            )

    def test_grounded_addon_candidate_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_grounded_addon_candidate_patch.write_text(
                "different grounded addon candidate patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_grounded_addon_candidate_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_grounded_addon_candidate_patch_sha256"
                ],
            )

    def test_guaranteed_producer_grounding_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_guaranteed_producer_grounding_patch.write_text(
                "different guaranteed producer grounding patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_guaranteed_producer_grounding_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_guaranteed_producer_grounding_patch_sha256"
                ],
            )

    def test_emergency_land_query_fallback_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_emergency_land_query_fallback_patch.write_text(
                "different emergency land query fallback patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_emergency_land_query_fallback_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_emergency_land_query_fallback_patch_sha256"
                ],
            )

    def test_grounded_production_observed_targeting_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_grounded_production_observed_targeting_patch.write_text(
                "different grounded production observed targeting patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_grounded_production_observed_targeting_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_grounded_production_observed_targeting_patch_sha256"
                ],
            )

    def test_adaptive_support_composition_patch_checksum_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_adaptive_support_composition_patch.write_text(
                "different adaptive support composition patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_adaptive_support_composition_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_adaptive_support_composition_patch_sha256"
                ],
            )

    def test_operation_scoped_adaptive_combat_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_operation_scoped_adaptive_combat_closure_patch.write_text(
                "different operation scoped adaptive combat closure patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_operation_scoped_adaptive_combat_closure_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_operation_scoped_adaptive_combat_closure_patch_sha256"
                ],
            )

    def test_review_closure_operation_identity_full_composition_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_review_closure_operation_identity_full_composition_patch.write_text(
                "different review closure operation identity patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_review_closure_operation_identity_full_composition_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_review_closure_operation_identity_full_composition_patch_sha256"
                ],
            )

    def test_semantic_operation_production_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_semantic_operation_production_closure_patch.write_text(
                "different semantic operation production closure patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_semantic_operation_production_closure_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_semantic_operation_production_closure_patch_sha256"
                ],
            )

    def test_missing_required_semantic_patch_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_semantic_operation_production_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_semantic_operation_production_closure_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_adaptive_pressure_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_adaptive_pressure_stable_operation_key_patch.write_text(
                "different adaptive pressure stable operation key patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_adaptive_pressure_stable_operation_key_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_adaptive_pressure_stable_operation_key_patch_sha256"
                ],
            )

    def test_tactical_nuke_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_tactical_nuke_command_hierarchy_patch.write_text(
                "different tactical nuke command hierarchy patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_tactical_nuke_command_hierarchy_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_tactical_nuke_command_hierarchy_patch_sha256"
                ],
            )

    def test_missing_tactical_nuke_patch_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_tactical_nuke_command_hierarchy_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_tactical_nuke_command_hierarchy_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_tactical_nuke_patch_cli_defaults_to_patch_0036(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0036-tactical-nuke-command-hierarchy.patch",
            Path(args.micromachine_tactical_nuke_command_hierarchy_patch).name,
        )

    def test_location_intent_target_lock_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_location_intent_target_lock_patch.write_text(
                "different location intent target lock patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_location_intent_target_lock_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_location_intent_target_lock_patch_sha256"
                ],
            )

    def test_location_intent_target_lock_cli_defaults_to_patch_0037(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0037-location-intent-target-lock.patch",
            Path(args.micromachine_location_intent_target_lock_patch).name,
        )

    def test_explicit_terran_ability_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_explicit_terran_ability_execution_patch.write_text(
                "different explicit Terran ability execution patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_explicit_terran_ability_execution_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_explicit_terran_ability_execution_patch_sha256"
                ],
            )

    def test_missing_explicit_terran_ability_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_terran_ability_execution_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_terran_ability_execution_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_terran_ability_cli_defaults_to_patch_0038(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0038-explicit-terran-ability-execution.patch",
            Path(args.micromachine_explicit_terran_ability_execution_patch).name,
        )

    def test_explicit_scout_command_epoch_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_explicit_scout_command_epoch_patch.write_text(
                "different explicit scout command epoch patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_explicit_scout_command_epoch_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_explicit_scout_command_epoch_patch_sha256"
                ],
            )

    def test_missing_explicit_scout_command_epoch_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_scout_command_epoch_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_scout_command_epoch_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_scout_command_epoch_cli_defaults_to_patch_0039(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0039-explicit-scout-command-epoch.patch",
            Path(args.micromachine_explicit_scout_command_epoch_patch).name,
        )

    def test_standing_production_continuity_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_standing_production_continuity_closure_patch.write_text(
                "different standing production continuity patch\n"
            )

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][
                    "micromachine_standing_production_continuity_closure_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_standing_production_continuity_closure_patch_sha256"
                ],
            )

    def test_missing_standing_production_continuity_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_standing_production_continuity_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_standing_production_continuity_closure_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_standing_production_continuity_cli_defaults_to_patch_0040(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0040-standing-production-continuity-closure.patch",
            Path(args.micromachine_standing_production_continuity_closure_patch).name,
        )

    def test_explicit_ability_caster_priority_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_explicit_ability_caster_production_priority_patch.write_text(
                "different explicit ability caster priority patch\n"
            )

            second = build_micromachine_build_identity(config)

            checksum = (
                "micromachine_explicit_ability_caster_production_priority_patch_sha256"
            )
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_explicit_ability_caster_priority_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_ability_caster_production_priority_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_ability_caster_production_"
                        "priority_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_ability_caster_priority_cli_defaults_to_patch_0041(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0041-explicit-ability-caster-production-priority.patch",
            Path(
                args.micromachine_explicit_ability_caster_production_priority_patch
            ).name,
        )

    def test_explicit_ability_observation_confirmation_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_explicit_ability_observation_confirmation_patch.write_text(
                "different explicit ability observation confirmation patch\n"
            )

            second = build_micromachine_build_identity(config)

            checksum = (
                "micromachine_explicit_ability_observation_confirmation_patch_sha256"
            )
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_explicit_ability_observation_confirmation_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_ability_observation_confirmation_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_ability_observation_confirmation_"
                        "patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_ability_observation_confirmation_cli_defaults_to_patch_0042(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0042-explicit-ability-observation-confirmation.patch",
            Path(
                args.micromachine_explicit_ability_observation_confirmation_patch
            ).name,
        )

    def test_explicit_ability_production_isolation_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_explicit_ability_production_isolation_patch.write_text(
                "different explicit ability production isolation patch\n"
            )

            second = build_micromachine_build_identity(config)

            checksum = "micromachine_explicit_ability_production_isolation_patch_sha256"
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_explicit_ability_production_isolation_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_ability_production_isolation_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_ability_production_isolation_"
                        "patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_ability_production_isolation_cli_defaults_to_patch_0043(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0043-explicit-ability-production-isolation.patch",
            Path(args.micromachine_explicit_ability_production_isolation_patch).name,
        )

    def test_explicit_ability_attempt_lifecycle_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_explicit_ability_attempt_lifecycle_patch.write_text(
                "different explicit ability attempt lifecycle patch\n"
            )

            second = build_micromachine_build_identity(config)

            checksum = "micromachine_explicit_ability_attempt_lifecycle_patch_sha256"
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_explicit_ability_attempt_lifecycle_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_ability_attempt_lifecycle_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_ability_attempt_lifecycle_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_ability_attempt_lifecycle_cli_defaults_to_patch_0044(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0044-explicit-ability-attempt-lifecycle.patch",
            Path(args.micromachine_explicit_ability_attempt_lifecycle_patch).name,
        )

    def test_explicit_ability_review_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_explicit_ability_review_closure_patch_sha256"

            config.micromachine_explicit_ability_review_closure_patch.write_text(
                "changed review closure\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_explicit_ability_review_closure_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_ability_review_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_ability_review_closure_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_ability_review_closure_cli_defaults_to_patch_0045(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0045-explicit-ability-review-closure.patch",
            Path(args.micromachine_explicit_ability_review_closure_patch).name,
        )

    def test_authoritative_addon_runtime_clearance_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_authoritative_addon_runtime_clearance_patch_sha256"

            config.micromachine_authoritative_addon_runtime_clearance_patch.write_text(
                "changed authoritative addon runtime clearance\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_authoritative_addon_runtime_clearance_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_authoritative_addon_runtime_clearance_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_authoritative_addon_runtime_clearance_"
                        "patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_authoritative_addon_runtime_clearance_cli_defaults_to_patch_0046(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0046-authoritative-addon-runtime-clearance.patch",
            Path(args.micromachine_authoritative_addon_runtime_clearance_patch).name,
        )

    def test_banshee_unit_specific_cloak_command_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_banshee_unit_specific_cloak_command_patch_sha256"

            config.micromachine_banshee_unit_specific_cloak_command_patch.write_text(
                "changed Banshee unit-specific cloak command\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_banshee_unit_specific_cloak_command_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_banshee_unit_specific_cloak_command_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_banshee_unit_specific_cloak_command_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_banshee_unit_specific_cloak_command_cli_defaults_to_patch_0047(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0047-banshee-unit-specific-cloak-command.patch",
            Path(args.micromachine_banshee_unit_specific_cloak_command_patch).name,
        )

    def test_allied_cloak_observation_confirmation_cli_defaults_to_patch_0048(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0048-allied-cloak-observation-confirmation.patch",
            Path(args.micromachine_allied_cloak_observation_confirmation_patch).name,
        )

    def test_missing_allied_cloak_observation_confirmation_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_allied_cloak_observation_confirmation_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_allied_cloak_observation_confirmation_"
                        "patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_ability_caster_ownership_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_explicit_ability_caster_ownership_patch_sha256"

            config.micromachine_explicit_ability_caster_ownership_patch.write_text(
                "changed explicit ability caster ownership\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_explicit_ability_caster_ownership_cli_defaults_to_patch_0049(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0049-explicit-ability-caster-ownership.patch",
            Path(args.micromachine_explicit_ability_caster_ownership_patch).name,
        )

    def test_missing_explicit_ability_caster_ownership_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_ability_caster_ownership_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_ability_caster_ownership_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_explicit_ability_staging_single_flight_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_explicit_ability_staging_single_flight_patch_sha256"
            )

            config.micromachine_explicit_ability_staging_single_flight_patch.write_text(
                "changed explicit ability staging single flight\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_explicit_ability_staging_single_flight_cli_defaults_to_patch_0050(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0050-explicit-ability-staging-single-flight.patch",
            Path(args.micromachine_explicit_ability_staging_single_flight_patch).name,
        )

    def test_missing_explicit_ability_staging_single_flight_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_explicit_ability_staging_single_flight_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_explicit_ability_staging_single_flight_"
                        "patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_all_terran_combat_scouts_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_all_terran_combat_scouts_patch_sha256"

            config.micromachine_all_terran_combat_scouts_patch.write_text(
                "changed all Terran combat scouts\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_all_terran_combat_scouts_cli_defaults_to_patch_0051(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0051-all-terran-combat-scouts.patch",
            Path(args.micromachine_all_terran_combat_scouts_patch).name,
        )

    def test_missing_all_terran_combat_scouts_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_all_terran_combat_scouts_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": ("micromachine_all_terran_combat_scouts_patch_sha256"),
                },
                report["failures"],
            )

    def test_parallel_operations_ingame_hud_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_parallel_operations_ingame_hud_patch_sha256"

            config.micromachine_parallel_operations_ingame_hud_patch.write_text(
                "changed parallel operations in-game HUD\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_parallel_operations_ingame_hud_cli_defaults_to_patch_0052(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0052-parallel-operations-ingame-hud.patch",
            Path(args.micromachine_parallel_operations_ingame_hud_patch).name,
        )

    def test_missing_parallel_operations_ingame_hud_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_parallel_operations_ingame_hud_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_parallel_operations_ingame_hud_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_parallel_operation_lifecycle_review_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_parallel_operation_lifecycle_review_closure_patch_sha256"
            )

            config.micromachine_parallel_operation_lifecycle_review_closure_patch.write_text(
                "changed parallel operation lifecycle review closure\n"
            )
            self.rebuild_fixture_binary(config)
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_parallel_operation_lifecycle_review_closure_cli_defaults_to_patch_0053(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0053-parallel-operation-lifecycle-review-closure.patch",
            Path(
                args.micromachine_parallel_operation_lifecycle_review_closure_patch
            ).name,
        )

    def test_missing_parallel_operation_lifecycle_review_closure_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_parallel_operation_lifecycle_review_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_parallel_operation_lifecycle_review_"
                        "closure_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_authoritative_parallel_operation_lifecycle_cli_defaults_to_patch_0054(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0054-authoritative-parallel-operation-lifecycle.patch",
            Path(
                args.micromachine_authoritative_parallel_operation_lifecycle_patch
            ).name,
        )

    def test_operation_production_ownership_restore_proof_cli_defaults_to_patch_0055(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0055-operation-production-ownership-and-restore-proof.patch",
            Path(
                args.micromachine_operation_production_ownership_restore_proof_patch
            ).name,
        )

    def test_missing_operation_production_ownership_restore_proof_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_production_ownership_restore_proof_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                {
                    "code": "missing_required_build_input",
                    "checksum": (
                        "micromachine_operation_production_ownership_restore_"
                        "proof_patch_sha256"
                    ),
                },
                report["failures"],
            )

    def test_operation_production_ownership_restore_proof_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_operation_production_ownership_restore_proof_patch_sha256"
            )

            config.micromachine_operation_production_ownership_restore_proof_patch.write_text(
                "changed operation production ownership restore proof\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )
            self.assertIn(
                "embedded_binary_identity_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_embedded_build_input_identity_cli_defaults_to_patch_0056(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0056-embedded-build-input-identity.patch",
            Path(args.micromachine_embedded_build_input_identity_patch).name,
        )

    def test_tech_gas_cli_defaults_to_patch_0057(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0057-tech-gas-before-second-barracks.patch",
            Path(args.micromachine_tech_gas_before_second_barracks_patch).name,
        )

    def test_operation_production_review_closure_cli_defaults_to_patch_0058(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0058-operation-production-review-closure.patch",
            Path(args.micromachine_operation_production_review_closure_patch).name,
        )

    def test_production_fifo_zero_owner_cleanup_cli_defaults_to_patch_0059(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0059-production-fifo-and-zero-owner-cleanup.patch",
            Path(args.micromachine_production_fifo_zero_owner_cleanup_patch).name,
        )

    def test_operation_edit_ownership_handoff_cli_defaults_to_patch_0060(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0060-operation-edit-ownership-handoff.patch",
            Path(args.micromachine_operation_edit_ownership_handoff_patch).name,
        )

    def test_operation_edit_review_closure_cli_defaults_to_patch_0061(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0061-operation-edit-review-closure.patch",
            Path(args.micromachine_operation_edit_review_closure_patch).name,
        )

    def test_operation_transfer_atomic_admission_cli_defaults_to_patch_0062(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0062-operation-transfer-atomic-admission.patch",
            Path(args.micromachine_operation_transfer_atomic_admission_patch).name,
        )

    def test_operation_transfer_runtime_preservation_cli_defaults_to_patch_0063(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0063-operation-transfer-runtime-preservation.patch",
            Path(args.micromachine_operation_transfer_runtime_preservation_patch).name,
        )

    def test_operation_transfer_transactional_closure_cli_defaults_to_patch_0064(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0064-operation-transfer-transactional-closure.patch",
            Path(args.micromachine_operation_transfer_transactional_closure_patch).name,
        )

    def test_operation_transfer_final_review_closure_cli_defaults_to_patch_0065(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0065-operation-transfer-final-review-closure.patch",
            Path(args.micromachine_operation_transfer_final_review_closure_patch).name,
        )

    def test_operation_transfer_idempotence_cli_defaults_to_patch_0066(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0066-operation-transfer-idempotence-and-active-evidence.patch",
            Path(
                args.micromachine_operation_transfer_idempotence_active_evidence_patch
            ).name,
        )

    def test_runtime_convergence_cli_defaults_to_patch_0067(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0067-runtime-convergence-defense-placement-information.patch",
            Path(
                args.micromachine_runtime_convergence_defense_placement_information_patch
            ).name,
        )

    def test_all_terran_harass_cli_defaults_to_patch_0068(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0068-all-terran-harass-capability-evidence.patch",
            Path(args.micromachine_all_terran_harass_capability_evidence_patch).name,
        )

    def test_battlefield_projection_cli_defaults_to_patch_0069(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0069-authoritative-battlefield-ownership-readiness.patch",
            Path(
                args.micromachine_authoritative_battlefield_ownership_readiness_patch
            ).name,
        )

    def test_battlefield_projection_review_cli_defaults_to_patch_0070(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0070-battlefield-projection-review-closure.patch",
            Path(args.micromachine_battlefield_projection_review_closure_patch).name,
        )

    def test_battlefield_identity_transfer_cli_defaults_to_patch_0071(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0071-battlefield-identity-transfer-integrity.patch",
            Path(args.micromachine_battlefield_identity_transfer_integrity_patch).name,
        )

    def test_atomic_telemetry_cli_defaults_to_patch_0072(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0072-atomic-telemetry-publication.patch",
            Path(args.micromachine_atomic_telemetry_publication_patch).name,
        )

    def test_contextual_transfer_cli_defaults_to_patch_0073(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0073-contextual-transfer-choice-projection.patch",
            Path(
                args.micromachine_contextual_transfer_choice_projection_patch
            ).name,
        )

    def test_autonomous_composition_cli_defaults_to_patch_0074(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0074-autonomous-owner-composition-evidence.patch",
            Path(
                args.micromachine_autonomous_owner_composition_evidence_patch
            ).name,
        )

    def test_battlefield_review_closure_cli_defaults_to_patch_0075(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0075-battlefield-review-closure.patch",
            Path(args.micromachine_battlefield_review_closure_patch).name,
        )

    def test_bounded_terminal_operation_hud_cli_defaults_to_patch_0076(self) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0076-bounded-terminal-operation-hud.patch",
            Path(args.micromachine_bounded_terminal_operation_hud_patch).name,
        )

    def test_deterministic_pre_live_journey_adapter_cli_defaults_to_patch_0077(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0077-deterministic-pre-live-journey-adapter.patch",
            Path(
                args.micromachine_deterministic_pre_live_journey_adapter_patch
            ).name,
        )

    def test_production_path_journey_review_cli_defaults_to_patch_0078(
        self,
    ) -> None:
        args = build_argument_parser().parse_args([])

        self.assertEqual(
            "0078-production-path-journey-review-closure.patch",
            Path(
                args.micromachine_production_path_journey_review_closure_patch
            ).name,
        )

    def test_operation_edit_ownership_handoff_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_operation_edit_ownership_handoff_patch_sha256"

            config.micromachine_operation_edit_ownership_handoff_patch.write_text(
                "changed operation edit ownership handoff\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )
            self.assertIn(
                "embedded_binary_identity_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_operation_edit_ownership_handoff_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_edit_ownership_handoff_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                ("micromachine_operation_edit_ownership_handoff_patch_sha256"),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_operation_edit_review_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_operation_edit_review_closure_patch_sha256"

            config.micromachine_operation_edit_review_closure_patch.write_text(
                "changed operation edit review closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )
            self.assertIn(
                "embedded_binary_identity_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_operation_edit_review_closure_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_edit_review_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_operation_edit_review_closure_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_operation_transfer_atomic_admission_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_operation_transfer_atomic_admission_patch_sha256"

            config.micromachine_operation_transfer_atomic_admission_patch.write_text(
                "changed operation transfer atomic admission\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )
            self.assertIn(
                "embedded_binary_identity_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_operation_transfer_atomic_admission_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_transfer_atomic_admission_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                ("micromachine_operation_transfer_atomic_admission_patch_sha256"),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_operation_transfer_runtime_preservation_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_operation_transfer_runtime_preservation_patch_sha256"
            )

            config.micromachine_operation_transfer_runtime_preservation_patch.write_text(
                "changed operation transfer runtime preservation\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )
            self.assertIn(
                "embedded_binary_identity_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_operation_transfer_runtime_preservation_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_transfer_runtime_preservation_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                ("micromachine_operation_transfer_runtime_preservation_patch_sha256"),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_operation_transfer_transactional_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_operation_transfer_transactional_closure_patch_sha256"
            )

            config.micromachine_operation_transfer_transactional_closure_patch.write_text(
                "changed operation transfer transactional closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )
            self.assertIn(
                "embedded_binary_identity_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_operation_transfer_transactional_closure_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_transfer_transactional_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                ("micromachine_operation_transfer_transactional_closure_patch_sha256"),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_operation_transfer_final_review_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_operation_transfer_final_review_closure_patch_sha256"
            )

            config.micromachine_operation_transfer_final_review_closure_patch.write_text(
                "changed operation transfer final review closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )
            self.assertIn(
                "embedded_binary_identity_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_operation_transfer_final_review_closure_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_transfer_final_review_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                ("micromachine_operation_transfer_final_review_closure_patch_sha256"),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_operation_transfer_idempotence_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_operation_transfer_idempotence_"
                "active_evidence_patch_sha256"
            )

            config.micromachine_operation_transfer_idempotence_active_evidence_patch.write_text(
                "changed operation transfer idempotence closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_operation_transfer_idempotence_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_operation_transfer_idempotence_active_evidence_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                (
                    "micromachine_operation_transfer_idempotence_"
                    "active_evidence_patch_sha256"
                ),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_runtime_convergence_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_runtime_convergence_defense_"
                "placement_information_patch_sha256"
            )

            config.micromachine_runtime_convergence_defense_placement_information_patch.write_text(
                "changed runtime convergence closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_runtime_convergence_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_runtime_convergence_defense_placement_information_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                (
                    "micromachine_runtime_convergence_defense_"
                    "placement_information_patch_sha256"
                ),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_all_terran_harass_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_all_terran_harass_capability_evidence_patch_sha256"

            config.micromachine_all_terran_harass_capability_evidence_patch.write_text(
                "changed all-Terran harass capability evidence\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_all_terran_harass_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_all_terran_harass_capability_evidence_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                ("micromachine_all_terran_harass_capability_evidence_patch_sha256"),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_battlefield_projection_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_authoritative_battlefield_ownership_"
                "readiness_patch_sha256"
            )

            config.micromachine_authoritative_battlefield_ownership_readiness_patch.write_text(
                "changed authoritative battlefield projection\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_battlefield_projection_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_authoritative_battlefield_ownership_readiness_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                (
                    "micromachine_authoritative_battlefield_ownership_"
                    "readiness_patch_sha256"
                ),
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_battlefield_projection_review_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_battlefield_projection_review_closure_patch_sha256"

            config.micromachine_battlefield_projection_review_closure_patch.write_text(
                "changed battlefield projection review closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_battlefield_projection_review_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_battlefield_projection_review_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_battlefield_projection_review_closure_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_battlefield_identity_transfer_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_battlefield_identity_transfer_integrity_patch_sha256"
            )

            config.micromachine_battlefield_identity_transfer_integrity_patch.write_text(
                "changed battlefield identity transfer integrity\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_battlefield_identity_transfer_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_battlefield_identity_transfer_integrity_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_battlefield_identity_transfer_integrity_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_atomic_telemetry_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_atomic_telemetry_publication_patch_sha256"

            config.micromachine_atomic_telemetry_publication_patch.write_text(
                "changed atomic telemetry publication\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_atomic_telemetry_patch_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_atomic_telemetry_publication_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_atomic_telemetry_publication_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_contextual_transfer_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_contextual_transfer_choice_projection_patch_sha256"
            )

            config.micromachine_contextual_transfer_choice_projection_patch.write_text(
                "changed contextual transfer choice projection\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_contextual_transfer_patch_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_contextual_transfer_choice_projection_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_contextual_transfer_choice_projection_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_autonomous_composition_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_autonomous_owner_composition_evidence_patch_sha256"
            )

            config.micromachine_autonomous_owner_composition_evidence_patch.write_text(
                "changed autonomous owner composition evidence\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_autonomous_composition_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_autonomous_owner_composition_evidence_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_autonomous_owner_composition_evidence_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_battlefield_review_closure_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_battlefield_review_closure_patch_sha256"

            config.micromachine_battlefield_review_closure_patch.write_text(
                "changed battlefield review closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_battlefield_review_closure_patch_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_battlefield_review_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_battlefield_review_closure_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_bounded_terminal_operation_hud_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_bounded_terminal_operation_hud_patch_sha256"

            config.micromachine_bounded_terminal_operation_hud_patch.write_text(
                "changed bounded terminal operation hud\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )

    def test_missing_bounded_terminal_operation_hud_patch_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_bounded_terminal_operation_hud_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_bounded_terminal_operation_hud_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_deterministic_pre_live_journey_adapter_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_deterministic_pre_live_journey_adapter_patch_sha256"
            )

            config.micromachine_deterministic_pre_live_journey_adapter_patch.write_text(
                "changed deterministic pre-live journey adapter\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "source_attestation_input_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_deterministic_pre_live_journey_adapter_patch_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_deterministic_pre_live_journey_adapter_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_deterministic_pre_live_journey_adapter_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_production_path_journey_review_patch_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = (
                "micromachine_production_path_journey_review_closure_patch_sha256"
            )

            config.micromachine_production_path_journey_review_closure_patch.write_text(
                "changed production path journey review closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "source_attestation_input_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_production_path_journey_review_patch_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.micromachine_production_path_journey_review_closure_patch.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_production_path_journey_review_closure_patch_sha256",
                {
                    failure.get("checksum")
                    for failure in report["failures"]
                    if failure["code"] == "missing_required_build_input"
                },
            )

    def test_pre_live_journey_native_tests_are_required(self) -> None:
        self.assertEqual(
            {
                "voi_pre_live_journey_adapter": (
                    "voi_pre_live_journey_adapter_test"
                ),
                "voi_production_path": "voi_production_path_test",
            },
            {
                name: MICROMACHINE_REQUIRED_NATIVE_TESTS[name]
                for name in (
                    "voi_pre_live_journey_adapter",
                    "voi_production_path",
                )
            },
        )

    def test_operation_hud_selection_native_tests_are_required(self) -> None:
        self.assertEqual(
            {
                "voi_operation_hud_selection": "voi_operation_hud_selection_test",
                "voi_operation_hud_selection_ndebug": (
                    "voi_operation_hud_selection_ndebug_test"
                ),
            },
            {
                name: MICROMACHINE_REQUIRED_NATIVE_TESTS[name]
                for name in (
                    "voi_operation_hud_selection",
                    "voi_operation_hud_selection_ndebug",
                )
            },
        )

    def test_atomic_telemetry_native_test_requires_its_canonical_artifact(
        self,
    ) -> None:
        for mutation in ("missing", "renamed"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    config = self.build_config(root, binary=True)
                    executable = (
                        config.micromachine_build_dir
                        / "bin"
                        / MICROMACHINE_REQUIRED_NATIVE_TESTS[
                            "voi_atomic_telemetry"
                        ]
                    )
                    if mutation == "missing":
                        executable.unlink()
                    else:
                        executable.rename(
                            executable.with_name(
                                "voi_atomic_telemetry_test.renamed"
                            )
                        )

                    report = build_micromachine_build_identity(config)

                    self.assertFalse(report["ok"], report)
                    self.assertIn(
                        (
                            "missing_or_invalid_native_test",
                            "voi_atomic_telemetry",
                        ),
                        {
                            (failure["code"], failure.get("test"))
                            for failure in report["failures"]
                        },
                    )

    def test_ctest_registry_rejects_noncanonical_command_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build_dir = Path(directory).resolve()
            ctest_path = build_dir / "ctest"
            canonical_paths = {
                name: str(build_dir / "bin" / executable)
                for name, executable in MICROMACHINE_REQUIRED_NATIVE_TESTS.items()
            }
            cases = {
                "missing parent alias": (
                    build_dir
                    / "bin"
                    / "missing"
                    / ".."
                    / MICROMACHINE_REQUIRED_NATIVE_TESTS["voi_atomic_telemetry"]
                ),
                "symlink directory alias": (
                    build_dir
                    / "linked-bin"
                    / MICROMACHINE_REQUIRED_NATIVE_TESTS["voi_atomic_telemetry"]
                ),
            }
            (build_dir / "bin").mkdir()
            (build_dir / "linked-bin").symlink_to(
                build_dir / "bin",
                target_is_directory=True,
            )

            for name, alias in cases.items():
                with self.subTest(name=name):
                    registry = {
                        "tests": [
                            {
                                "name": test_name,
                                "command": [
                                    str(alias)
                                    if test_name == "voi_atomic_telemetry"
                                    else canonical_paths[test_name]
                                ],
                            }
                            for test_name in sorted(canonical_paths)
                        ]
                    }
                    registry_json = json.dumps(registry, separators=(",", ":"))
                    ctest_path.write_text(
                        "#!/bin/sh\n"
                        f"printf '%s\\n' {json.dumps(registry_json)}\n"
                        "exit 0\n"
                    )
                    ctest_path.chmod(0o755)

                    attestation, failures = _ctest_registry_attestation(
                        ctest_path=ctest_path,
                        build_dir=build_dir,
                    )

                    self.assertIsNone(attestation)
                    self.assertIn(
                        "ctest_registry_identity_mismatch",
                        {failure["code"] for failure in failures},
                    )

    def test_operation_production_review_closure_patch_changes_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            checksum = "micromachine_operation_production_review_closure_patch_sha256"

            config.micromachine_operation_production_review_closure_patch.write_text(
                "changed operation production review closure\n"
            )
            second = build_micromachine_build_identity(config)

            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"][checksum],
                second["checksums"][checksum],
            )
            self.assertIn(
                "embedded_identity_header_mismatch",
                {failure["code"] for failure in second["failures"]},
            )

    def test_missing_source_attestation_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.source_attestation_path.unlink()

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "missing_source_attestation",
                {failure["code"] for failure in report["failures"]},
            )

    def test_source_attestation_without_build_finalization_is_not_accepted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            write_micromachine_source_attestation(config)

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "missing_build_attestation",
                {failure["code"] for failure in report["failures"]},
            )

    def test_binary_replacement_after_finalization_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.binary_path.write_text("#!/bin/sh\nexit 7\n")
            config.binary_path.chmod(0o755)

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "binary_attestation_mismatch",
                {failure["code"] for failure in report["failures"]},
            )

    def test_non_executable_binary_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            config.binary_path.chmod(0o644)

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "binary_not_executable",
                {failure["code"] for failure in report["failures"]},
            )

    def test_source_mutation_after_attestation_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            (config.micromachine_dir / "README.md").write_text(
                "fixture changed after build\n"
            )

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "micromachine_source_state_mismatch",
                {failure["code"] for failure in report["failures"]},
            )

    def test_index_hidden_source_mutation_marks_identity_not_ok(self) -> None:
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    config = self.build_config(root, binary=True)
                    subprocess.run(
                        [
                            "/usr/bin/git",
                            "-C",
                            str(config.micromachine_dir),
                            "update-index",
                            flag,
                            "README.md",
                        ],
                        check=True,
                        capture_output=True,
                    )
                    (config.micromachine_dir / "README.md").write_text(
                        "hidden source mutation\n"
                    )

                    report = build_micromachine_build_identity(config)

                    self.assertFalse(report["ok"], report)
                    self.assertIn(
                        "micromachine_source_state_mismatch",
                        {failure["code"] for failure in report["failures"]},
                    )

    def test_ignored_untracked_source_mutation_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            (config.micromachine_dir / ".git" / "info" / "exclude").write_text(
                "ignored-runtime.cpp\n"
            )
            (config.micromachine_dir / "ignored-runtime.cpp").write_text(
                "int hidden_runtime_change = 1;\n"
            )

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "micromachine_source_state_mismatch",
                {failure["code"] for failure in report["failures"]},
            )

    def test_tracked_symlink_cannot_hide_source_in_excluded_build_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            hidden_source = config.micromachine_build_dir / "generated-source.cpp"
            hidden_source.write_text("int generated_source = 1;\n")
            source_link = config.micromachine_dir / "src-link.cpp"
            source_link.symlink_to(hidden_source)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(config.micromachine_dir),
                    "add",
                    "src-link.cpp",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(config.micromachine_dir),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "tracked excluded-root symlink",
                ],
                check=True,
                capture_output=True,
            )

            inspection = inspect_git_worktree_state(
                config.micromachine_dir,
                excluded_roots=(config.micromachine_build_dir,),
            )

            self.assertIsNotNone(inspection)
            self.assertEqual(
                ["excluded-root-target:src-link.cpp"],
                inspection["unsafe_symlink_entries"],
            )

    def test_micromachine_build_root_symlink_is_rejected_without_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            build_root = config.micromachine_build_dir
            external_build = root / "external-micromachine-build"
            build_root.rename(external_build)
            build_root.symlink_to(external_build, target_is_directory=True)
            sentinel = root / "attacker-executed"
            external_binary = external_build / "bin" / "MicroMachine"
            external_binary.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n")
            external_binary.chmod(0o755)

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "invalid_micromachine_build_root",
                {failure["code"] for failure in report["failures"]},
            )
            self.assertFalse(sentinel.exists())

    def test_build_script_creates_secure_root_before_source_attestation(
        self,
    ) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "integrations"
            / "micromachine"
            / "scripts"
            / "build_macos_local.sh"
        ).read_text()
        cleanup = script.index('rm -f \\\n  "${MICROMACHINE_BUILD_IDENTITY_REPORT}"')
        create_root = script.index('mkdir -p "${MICROMACHINE_BUILD_DIR}"', cleanup)
        initialize = script.index("--initialize-source-attestation", create_root)

        self.assertLess(cleanup, create_root)
        self.assertLess(create_root, initialize)

    def test_build_script_binds_and_runs_one_resolved_ctest_executable(
        self,
    ) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "integrations"
            / "micromachine"
            / "scripts"
            / "build_macos_local.sh"
        ).read_text()

        resolve = script.index(
            'CTEST_COMMAND="$(resolve_regular_executable "${CTEST_COMMAND}" "CTest")"'
        )
        configure = script.index(
            '-DCMAKE_CTEST_COMMAND:INTERNAL="${CTEST_COMMAND}"',
            resolve,
        )
        execute = script.index(
            '"${CTEST_COMMAND}" --test-dir "${MICROMACHINE_BUILD_DIR}"',
            configure,
        )
        finalize = script.index("--finalize-build-attestation", execute)

        self.assertLess(resolve, configure)
        self.assertLess(configure, execute)
        self.assertLess(execute, finalize)

    def test_build_script_preflight_rejects_linked_build_root_before_cleanup(
        self,
    ) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "integrations"
            / "micromachine"
            / "scripts"
            / "build_macos_local.sh"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            micromachine = root / "MicroMachine"
            s2client = root / "s2client-api"
            external = root / "external-build"
            micromachine.mkdir()
            s2client.mkdir()
            (external / "bin").mkdir(parents=True)
            protected = {
                external / "voi_build_identity.json": "identity\n",
                external / "voi_source_attestation.json": "attestation\n",
                external / "bin" / "MicroMachine": "binary\n",
            }
            for path, payload in protected.items():
                path.write_text(payload)
            (micromachine / "build-latest-api").symlink_to(
                external,
                target_is_directory=True,
            )

            completed = subprocess.run(
                ["bash", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "ROOT_DIR": str(root),
                    "MICROMACHINE_DIR": str(micromachine),
                    "S2CLIENT_DIR": str(s2client),
                    "VOI_BUILD_PREFLIGHT_ONLY": "1",
                },
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("contains a symlink", completed.stderr)
            for path, payload in protected.items():
                self.assertEqual(payload, path.read_text())

    def test_ci_actions_are_pinned_to_immutable_commits(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        ).read_text()
        uses_values = [
            line.split("uses:", 1)[1].strip()
            for line in workflow.splitlines()
            if "uses:" in line
        ]

        self.assertTrue(uses_values)
        for value in uses_values:
            with self.subTest(value=value):
                self.assertRegex(value, r"^[^@\s]+@[0-9a-f]{40}$")

    def test_s2client_build_root_symlink_retarget_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            build_root = config.resolved_s2client_build_dir
            original = build_root.with_name("build-original")
            replacement = build_root.with_name("build-replacement")
            build_root.rename(original)
            shutil.copytree(original, replacement)
            build_root.symlink_to(replacement, target_is_directory=True)

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "s2client_build_state_mismatch",
                {failure["code"] for failure in report["failures"]},
            )

    def test_source_swap_and_restore_during_build_transaction_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            write_micromachine_source_attestation(config)
            source = config.micromachine_dir / "README.md"
            original = source.with_name("README.original")
            attacker = source.with_name("README.attacker")
            expected_bytes = source.read_bytes()

            source.rename(original)
            attacker.write_text("compiler-window attacker bytes\n")
            attacker.rename(source)
            source.unlink()
            original.rename(source)

            self.assertEqual(expected_bytes, source.read_bytes())
            with self.assertRaisesRegex(
                ValueError,
                "micromachine_source_build_transaction_mismatch",
            ):
                write_micromachine_build_attestation(config)

    def test_s2client_build_root_swap_and_restore_during_link_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            write_micromachine_source_attestation(config)
            build_root = config.resolved_s2client_build_dir
            original = build_root.with_name("build-original")

            build_root.rename(original)
            shutil.copytree(original, build_root)
            (build_root / "bin").mkdir(exist_ok=True)
            (build_root / "bin" / "libsc2api.a").write_text(
                "link-window attacker archive\n"
            )
            shutil.rmtree(build_root)
            original.rename(build_root)

            with self.assertRaisesRegex(
                ValueError,
                "s2client_(source|build)_build_transaction_mismatch",
            ):
                write_micromachine_build_attestation(config)

    def test_runtime_bot_config_mutation_does_not_invalidate_binary_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            bot_config = config.micromachine_dir / "bin" / "BotConfig.txt"
            bot_config.parent.mkdir(parents=True, exist_ok=True)
            bot_config.write_text('{"SC2API": {"EnemyDifficulty": 10}}\n')

            report = build_micromachine_build_identity(config)

            self.assertTrue(report["ok"], report)

    def test_micromachine_build_artifacts_do_not_mutate_attested_source_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            (config.micromachine_build_dir / "late-build.log").write_text(
                "build output\n"
            )

            report = build_micromachine_build_identity(config)

            self.assertTrue(report["ok"], report)

    def test_s2client_build_mutation_after_attestation_marks_identity_not_ok(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            (config.resolved_s2client_build_dir / "generated.pb.cc").write_text(
                "mutated generated build output\n"
            )

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "s2client_build_state_mismatch",
                {failure["code"] for failure in report["failures"]},
            )

    def test_attested_commit_mismatch_marks_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            attestation = json.loads(config.source_attestation_path.read_text())
            attestation["micromachine_commit"] = "not-the-observed-commit"
            config.source_attestation_path.write_text(
                json.dumps(attestation, indent=2, sort_keys=True) + "\n"
            )

            report = build_micromachine_build_identity(config)

            self.assertFalse(report["ok"])
            self.assertIn(
                "micromachine_attested_commit_mismatch",
                {failure["code"] for failure in report["failures"]},
            )

    def test_read_report_cli_treats_malformed_json_as_invalid_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "identity.json"
            report.write_text("")

            completed = subprocess.run(
                [
                    "python3",
                    "-m",
                    "starcraft_commander.micromachine_build_identity",
                    "--read-report",
                    str(report),
                    "--field",
                    "failure-codes",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual("invalid_build_identity_report", completed.stdout.strip())

    def rebuild_fixture_binary(
        self,
        config: MicroMachineBuildIdentityConfig,
    ) -> None:
        embedded_identity = write_micromachine_embedded_build_identity_header(config)
        config.binary_path.write_text(
            "#!/bin/sh\n"
            'if [ "${1:-}" = "--voi-build-input-identity" ]; then\n'
            f"  printf '%s\\n' '{embedded_identity}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        )
        config.binary_path.chmod(0o755)
        write_micromachine_source_attestation(config)
        write_micromachine_build_attestation(config)

    def build_config(
        self,
        root: Path,
        *,
        binary: bool,
        git_provenance: bool = True,
    ) -> MicroMachineBuildIdentityConfig:
        micromachine_dir = root / "MicroMachine"
        s2client_dir = root / "s2client-api"
        build_dir = micromachine_dir / "build"
        binary_path = build_dir / "bin" / "MicroMachine"
        binary_path.parent.mkdir(parents=True)
        micromachine_dir.mkdir(exist_ok=True)
        s2client_dir.mkdir()
        if binary:
            binary_path.write_text("#!/bin/sh\nexit 0\n")
            binary_path.chmod(0o755)
        micromachine_commit = "missing"
        s2client_commit = "missing"
        if git_provenance:
            micromachine_commit = self.init_git_repo(micromachine_dir)
            s2client_commit = self.init_git_repo(s2client_dir)
        micromachine_patch = root / "micromachine.patch"
        micromachine_tactical_patch = root / "micromachine-tactical.patch"
        micromachine_production_fix_patch = root / "micromachine-production-fix.patch"
        micromachine_operation_state_patch = root / "micromachine-operation-state.patch"
        micromachine_addon_recovery_patch = root / "micromachine-addon-recovery.patch"
        micromachine_grounded_addon_candidate_patch = (
            root / "micromachine-grounded-addon-candidate.patch"
        )
        micromachine_guaranteed_producer_grounding_patch = (
            root / "micromachine-guaranteed-producer-grounding.patch"
        )
        micromachine_emergency_land_query_fallback_patch = (
            root / "micromachine-emergency-land-query-fallback.patch"
        )
        micromachine_grounded_production_observed_targeting_patch = (
            root / "micromachine-grounded-production-observed-targeting.patch"
        )
        micromachine_exact_composition_production_progress_patch = (
            root / "micromachine-exact-composition-production-progress.patch"
        )
        micromachine_production_resource_operation_persistence_patch = (
            root / "micromachine-production-resource-operation-persistence.patch"
        )
        micromachine_live_operation_unblock_patch = (
            root / "micromachine-live-operation-unblock.patch"
        )
        micromachine_stable_flank_stage_latch_patch = (
            root / "micromachine-stable-flank-stage-latch.patch"
        )
        micromachine_production_staging_observed_operation_patch = (
            root / "micromachine-production-staging-observed-operation.patch"
        )
        micromachine_addon_query_footprint_validation_patch = (
            root / "micromachine-addon-query-footprint-validation.patch"
        )
        micromachine_authoritative_addon_placement_query_patch = (
            root / "micromachine-authoritative-addon-placement-query.patch"
        )
        micromachine_authoritative_addon_execution_patch = (
            root / "micromachine-authoritative-addon-execution.patch"
        )
        micromachine_continuous_army_macro_patch = (
            root / "micromachine-continuous-army-macro.patch"
        )
        micromachine_continuous_army_economy_scaling_patch = (
            root / "micromachine-continuous-army-economy-scaling.patch"
        )
        micromachine_standing_composition_reinforcement_waves_patch = (
            root / "micromachine-standing-composition-reinforcement-waves.patch"
        )
        micromachine_offensive_sweep_self_base_exclusion_patch = (
            root / "micromachine-offensive-sweep-self-base-exclusion.patch"
        )
        micromachine_bounded_placement_query_cache_patch = (
            root / "micromachine-bounded-placement-query-cache.patch"
        )
        micromachine_production_facility_stability_tank_recovery_patch = (
            root / "micromachine-production-facility-stability-tank-recovery.patch"
        )
        micromachine_balanced_composition_wave_production_patch = (
            root / "micromachine-balanced-composition-wave-production.patch"
        )
        micromachine_exact_composition_production_unblock_patch = (
            root / "micromachine-exact-composition-production-unblock.patch"
        )
        micromachine_continuous_combat_production_relaunch_patch = (
            root / "micromachine-continuous-combat-production-relaunch.patch"
        )
        micromachine_resource_throughput_expansion_backoff_patch = (
            root / "micromachine-resource-throughput-expansion-backoff.patch"
        )
        micromachine_startup_telemetry_initialization_patch = (
            root / "micromachine-startup-telemetry-initialization.patch"
        )
        micromachine_gas_worker_completion_cap_patch = (
            root / "micromachine-gas-worker-completion-cap.patch"
        )
        micromachine_stable_offensive_sweep_target_patch = (
            root / "micromachine-stable-offensive-sweep-target.patch"
        )
        micromachine_adaptive_support_composition_patch = (
            root / "micromachine-adaptive-support-composition.patch"
        )
        micromachine_operation_scoped_adaptive_combat_closure_patch = (
            root / "micromachine-operation-scoped-adaptive-combat-closure.patch"
        )
        micromachine_review_closure_operation_identity_full_composition_patch = (
            root
            / "micromachine-review-closure-operation-identity-full-composition.patch"
        )
        micromachine_semantic_operation_production_closure_patch = (
            root / "micromachine-semantic-operation-production-closure.patch"
        )
        micromachine_adaptive_pressure_stable_operation_key_patch = (
            root / "micromachine-adaptive-pressure-stable-operation-key.patch"
        )
        micromachine_tactical_nuke_command_hierarchy_patch = (
            root / "micromachine-tactical-nuke-command-hierarchy.patch"
        )
        micromachine_location_intent_target_lock_patch = (
            root / "micromachine-location-intent-target-lock.patch"
        )
        micromachine_explicit_terran_ability_execution_patch = (
            root / "micromachine-explicit-terran-ability-execution.patch"
        )
        micromachine_explicit_scout_command_epoch_patch = (
            root / "micromachine-explicit-scout-command-epoch.patch"
        )
        micromachine_standing_production_continuity_closure_patch = (
            root / "micromachine-standing-production-continuity-closure.patch"
        )
        micromachine_explicit_ability_caster_production_priority_patch = (
            root / "micromachine-explicit-ability-caster-production-priority.patch"
        )
        micromachine_explicit_ability_observation_confirmation_patch = (
            root / "micromachine-explicit-ability-observation-confirmation.patch"
        )
        micromachine_explicit_ability_production_isolation_patch = (
            root / "micromachine-explicit-ability-production-isolation.patch"
        )
        micromachine_explicit_ability_attempt_lifecycle_patch = (
            root / "micromachine-explicit-ability-attempt-lifecycle.patch"
        )
        micromachine_explicit_ability_review_closure_patch = (
            root / "micromachine-explicit-ability-review-closure.patch"
        )
        micromachine_authoritative_addon_runtime_clearance_patch = (
            root / "micromachine-authoritative-addon-runtime-clearance.patch"
        )
        micromachine_banshee_unit_specific_cloak_command_patch = (
            root / "micromachine-banshee-unit-specific-cloak-command.patch"
        )
        micromachine_allied_cloak_observation_confirmation_patch = (
            root / "micromachine-allied-cloak-observation-confirmation.patch"
        )
        micromachine_explicit_ability_caster_ownership_patch = (
            root / "micromachine-explicit-ability-caster-ownership.patch"
        )
        micromachine_explicit_ability_staging_single_flight_patch = (
            root / "micromachine-explicit-ability-staging-single-flight.patch"
        )
        micromachine_all_terran_combat_scouts_patch = (
            root / "micromachine-all-terran-combat-scouts.patch"
        )
        micromachine_parallel_operations_ingame_hud_patch = (
            root / "micromachine-parallel-operations-ingame-hud.patch"
        )
        micromachine_parallel_operation_lifecycle_review_closure_patch = (
            root / "micromachine-parallel-operation-lifecycle-review-closure.patch"
        )
        micromachine_authoritative_parallel_operation_lifecycle_patch = (
            root / "micromachine-authoritative-parallel-operation-lifecycle.patch"
        )
        micromachine_operation_production_ownership_restore_proof_patch = (
            root / "micromachine-operation-production-ownership-restore-proof.patch"
        )
        micromachine_embedded_build_input_identity_patch = (
            root / "micromachine-embedded-build-input-identity.patch"
        )
        micromachine_operation_production_review_closure_patch = (
            root / "micromachine-operation-production-review-closure.patch"
        )
        micromachine_production_fifo_zero_owner_cleanup_patch = (
            root / "micromachine-production-fifo-zero-owner-cleanup.patch"
        )
        micromachine_operation_edit_ownership_handoff_patch = (
            root / "micromachine-operation-edit-ownership-handoff.patch"
        )
        micromachine_operation_edit_review_closure_patch = (
            root / "micromachine-operation-edit-review-closure.patch"
        )
        micromachine_operation_transfer_atomic_admission_patch = (
            root / "micromachine-operation-transfer-atomic-admission.patch"
        )
        micromachine_operation_transfer_runtime_preservation_patch = (
            root / "micromachine-operation-transfer-runtime-preservation.patch"
        )
        micromachine_operation_transfer_transactional_closure_patch = (
            root / "micromachine-operation-transfer-transactional-closure.patch"
        )
        micromachine_operation_transfer_final_review_closure_patch = (
            root / "micromachine-operation-transfer-final-review-closure.patch"
        )
        micromachine_operation_transfer_idempotence_active_evidence_patch = (
            root / "micromachine-operation-transfer-idempotence-active-evidence.patch"
        )
        micromachine_runtime_convergence_defense_placement_information_patch = (
            root
            / "micromachine-runtime-convergence-defense-placement-information.patch"
        )
        micromachine_all_terran_harass_capability_evidence_patch = (
            root / "micromachine-all-terran-harass-capability-evidence.patch"
        )
        micromachine_authoritative_battlefield_ownership_readiness_patch = (
            root / "micromachine-authoritative-battlefield-ownership-readiness.patch"
        )
        micromachine_battlefield_projection_review_closure_patch = (
            root / "micromachine-battlefield-projection-review-closure.patch"
        )
        micromachine_battlefield_identity_transfer_integrity_patch = (
            root / "micromachine-battlefield-identity-transfer-integrity.patch"
        )
        micromachine_atomic_telemetry_publication_patch = (
            root / "micromachine-atomic-telemetry-publication.patch"
        )
        micromachine_contextual_transfer_choice_projection_patch = (
            root / "micromachine-contextual-transfer-choice-projection.patch"
        )
        micromachine_autonomous_owner_composition_evidence_patch = (
            root / "micromachine-autonomous-owner-composition-evidence.patch"
        )
        micromachine_battlefield_review_closure_patch = (
            root / "micromachine-battlefield-review-closure.patch"
        )
        micromachine_bounded_terminal_operation_hud_patch = (
            root / "micromachine-bounded-terminal-operation-hud.patch"
        )
        micromachine_deterministic_pre_live_journey_adapter_patch = (
            root / "micromachine-deterministic-pre-live-journey-adapter.patch"
        )
        micromachine_production_path_journey_review_closure_patch = (
            root / "micromachine-production-path-journey-review-closure.patch"
        )
        s2client_patch = root / "s2client.patch"
        hook_manifest = root / "HOOK_MANIFEST.json"
        map_pool = root / "MICROMACHINE_MAP_POOL.json"
        blackboard_header = root / "voi_policy_blackboard.hpp"
        source_attestation = root / "voi_source_attestation.json"
        for path in (
            micromachine_patch,
            micromachine_tactical_patch,
            micromachine_production_fix_patch,
            micromachine_operation_state_patch,
            micromachine_addon_recovery_patch,
            micromachine_grounded_addon_candidate_patch,
            micromachine_guaranteed_producer_grounding_patch,
            micromachine_emergency_land_query_fallback_patch,
            micromachine_grounded_production_observed_targeting_patch,
            micromachine_exact_composition_production_progress_patch,
            micromachine_production_resource_operation_persistence_patch,
            micromachine_live_operation_unblock_patch,
            micromachine_stable_flank_stage_latch_patch,
            micromachine_production_staging_observed_operation_patch,
            micromachine_addon_query_footprint_validation_patch,
            micromachine_authoritative_addon_placement_query_patch,
            micromachine_authoritative_addon_execution_patch,
            micromachine_continuous_army_macro_patch,
            micromachine_continuous_army_economy_scaling_patch,
            micromachine_standing_composition_reinforcement_waves_patch,
            micromachine_offensive_sweep_self_base_exclusion_patch,
            micromachine_bounded_placement_query_cache_patch,
            micromachine_production_facility_stability_tank_recovery_patch,
            micromachine_balanced_composition_wave_production_patch,
            micromachine_exact_composition_production_unblock_patch,
            micromachine_continuous_combat_production_relaunch_patch,
            micromachine_resource_throughput_expansion_backoff_patch,
            micromachine_startup_telemetry_initialization_patch,
            micromachine_gas_worker_completion_cap_patch,
            micromachine_stable_offensive_sweep_target_patch,
            micromachine_adaptive_support_composition_patch,
            micromachine_operation_scoped_adaptive_combat_closure_patch,
            micromachine_review_closure_operation_identity_full_composition_patch,
            micromachine_semantic_operation_production_closure_patch,
            micromachine_adaptive_pressure_stable_operation_key_patch,
            micromachine_tactical_nuke_command_hierarchy_patch,
            micromachine_location_intent_target_lock_patch,
            micromachine_explicit_terran_ability_execution_patch,
            micromachine_explicit_scout_command_epoch_patch,
            micromachine_standing_production_continuity_closure_patch,
            micromachine_explicit_ability_caster_production_priority_patch,
            micromachine_explicit_ability_observation_confirmation_patch,
            micromachine_explicit_ability_production_isolation_patch,
            micromachine_explicit_ability_attempt_lifecycle_patch,
            micromachine_explicit_ability_review_closure_patch,
            micromachine_authoritative_addon_runtime_clearance_patch,
            micromachine_banshee_unit_specific_cloak_command_patch,
            micromachine_allied_cloak_observation_confirmation_patch,
            micromachine_explicit_ability_caster_ownership_patch,
            micromachine_explicit_ability_staging_single_flight_patch,
            micromachine_all_terran_combat_scouts_patch,
            micromachine_parallel_operations_ingame_hud_patch,
            micromachine_parallel_operation_lifecycle_review_closure_patch,
            micromachine_authoritative_parallel_operation_lifecycle_patch,
            micromachine_operation_production_ownership_restore_proof_patch,
            micromachine_embedded_build_input_identity_patch,
            micromachine_operation_production_review_closure_patch,
            micromachine_production_fifo_zero_owner_cleanup_patch,
            micromachine_operation_edit_ownership_handoff_patch,
            micromachine_operation_edit_review_closure_patch,
            micromachine_operation_transfer_atomic_admission_patch,
            micromachine_operation_transfer_runtime_preservation_patch,
            micromachine_operation_transfer_transactional_closure_patch,
            micromachine_operation_transfer_final_review_closure_patch,
            micromachine_operation_transfer_idempotence_active_evidence_patch,
            micromachine_runtime_convergence_defense_placement_information_patch,
            micromachine_all_terran_harass_capability_evidence_patch,
            micromachine_authoritative_battlefield_ownership_readiness_patch,
            micromachine_battlefield_projection_review_closure_patch,
            micromachine_battlefield_identity_transfer_integrity_patch,
            micromachine_atomic_telemetry_publication_patch,
            micromachine_contextual_transfer_choice_projection_patch,
            micromachine_autonomous_owner_composition_evidence_patch,
            micromachine_battlefield_review_closure_patch,
            micromachine_bounded_terminal_operation_hud_patch,
            micromachine_deterministic_pre_live_journey_adapter_patch,
            micromachine_production_path_journey_review_closure_patch,
            s2client_patch,
            hook_manifest,
            map_pool,
            blackboard_header,
        ):
            path.write_text(f"{path.name}\n")
        config = MicroMachineBuildIdentityConfig(
            micromachine_dir=micromachine_dir,
            s2client_dir=s2client_dir,
            micromachine_build_dir=build_dir,
            micromachine_commit=micromachine_commit,
            s2client_commit=s2client_commit,
            micromachine_patch=micromachine_patch,
            micromachine_tactical_patch=micromachine_tactical_patch,
            micromachine_production_fix_patch=micromachine_production_fix_patch,
            micromachine_operation_state_patch=micromachine_operation_state_patch,
            micromachine_addon_recovery_patch=micromachine_addon_recovery_patch,
            micromachine_grounded_addon_candidate_patch=(
                micromachine_grounded_addon_candidate_patch
            ),
            micromachine_guaranteed_producer_grounding_patch=(
                micromachine_guaranteed_producer_grounding_patch
            ),
            micromachine_emergency_land_query_fallback_patch=(
                micromachine_emergency_land_query_fallback_patch
            ),
            micromachine_grounded_production_observed_targeting_patch=(
                micromachine_grounded_production_observed_targeting_patch
            ),
            micromachine_exact_composition_production_progress_patch=(
                micromachine_exact_composition_production_progress_patch
            ),
            micromachine_production_resource_operation_persistence_patch=(
                micromachine_production_resource_operation_persistence_patch
            ),
            micromachine_live_operation_unblock_patch=(
                micromachine_live_operation_unblock_patch
            ),
            micromachine_stable_flank_stage_latch_patch=(
                micromachine_stable_flank_stage_latch_patch
            ),
            micromachine_production_staging_observed_operation_patch=(
                micromachine_production_staging_observed_operation_patch
            ),
            micromachine_addon_query_footprint_validation_patch=(
                micromachine_addon_query_footprint_validation_patch
            ),
            micromachine_authoritative_addon_placement_query_patch=(
                micromachine_authoritative_addon_placement_query_patch
            ),
            micromachine_authoritative_addon_execution_patch=(
                micromachine_authoritative_addon_execution_patch
            ),
            micromachine_continuous_army_macro_patch=(
                micromachine_continuous_army_macro_patch
            ),
            micromachine_continuous_army_economy_scaling_patch=(
                micromachine_continuous_army_economy_scaling_patch
            ),
            micromachine_standing_composition_reinforcement_waves_patch=(
                micromachine_standing_composition_reinforcement_waves_patch
            ),
            micromachine_offensive_sweep_self_base_exclusion_patch=(
                micromachine_offensive_sweep_self_base_exclusion_patch
            ),
            micromachine_bounded_placement_query_cache_patch=(
                micromachine_bounded_placement_query_cache_patch
            ),
            micromachine_production_facility_stability_tank_recovery_patch=(
                micromachine_production_facility_stability_tank_recovery_patch
            ),
            micromachine_balanced_composition_wave_production_patch=(
                micromachine_balanced_composition_wave_production_patch
            ),
            micromachine_exact_composition_production_unblock_patch=(
                micromachine_exact_composition_production_unblock_patch
            ),
            micromachine_continuous_combat_production_relaunch_patch=(
                micromachine_continuous_combat_production_relaunch_patch
            ),
            micromachine_resource_throughput_expansion_backoff_patch=(
                micromachine_resource_throughput_expansion_backoff_patch
            ),
            micromachine_startup_telemetry_initialization_patch=(
                micromachine_startup_telemetry_initialization_patch
            ),
            micromachine_gas_worker_completion_cap_patch=(
                micromachine_gas_worker_completion_cap_patch
            ),
            micromachine_stable_offensive_sweep_target_patch=(
                micromachine_stable_offensive_sweep_target_patch
            ),
            micromachine_adaptive_support_composition_patch=(
                micromachine_adaptive_support_composition_patch
            ),
            micromachine_operation_scoped_adaptive_combat_closure_patch=(
                micromachine_operation_scoped_adaptive_combat_closure_patch
            ),
            micromachine_review_closure_operation_identity_full_composition_patch=(
                micromachine_review_closure_operation_identity_full_composition_patch
            ),
            micromachine_semantic_operation_production_closure_patch=(
                micromachine_semantic_operation_production_closure_patch
            ),
            micromachine_adaptive_pressure_stable_operation_key_patch=(
                micromachine_adaptive_pressure_stable_operation_key_patch
            ),
            micromachine_tactical_nuke_command_hierarchy_patch=(
                micromachine_tactical_nuke_command_hierarchy_patch
            ),
            micromachine_location_intent_target_lock_patch=(
                micromachine_location_intent_target_lock_patch
            ),
            micromachine_explicit_terran_ability_execution_patch=(
                micromachine_explicit_terran_ability_execution_patch
            ),
            micromachine_explicit_scout_command_epoch_patch=(
                micromachine_explicit_scout_command_epoch_patch
            ),
            micromachine_standing_production_continuity_closure_patch=(
                micromachine_standing_production_continuity_closure_patch
            ),
            micromachine_explicit_ability_caster_production_priority_patch=(
                micromachine_explicit_ability_caster_production_priority_patch
            ),
            micromachine_explicit_ability_observation_confirmation_patch=(
                micromachine_explicit_ability_observation_confirmation_patch
            ),
            micromachine_explicit_ability_production_isolation_patch=(
                micromachine_explicit_ability_production_isolation_patch
            ),
            micromachine_explicit_ability_attempt_lifecycle_patch=(
                micromachine_explicit_ability_attempt_lifecycle_patch
            ),
            micromachine_explicit_ability_review_closure_patch=(
                micromachine_explicit_ability_review_closure_patch
            ),
            micromachine_authoritative_addon_runtime_clearance_patch=(
                micromachine_authoritative_addon_runtime_clearance_patch
            ),
            micromachine_banshee_unit_specific_cloak_command_patch=(
                micromachine_banshee_unit_specific_cloak_command_patch
            ),
            micromachine_allied_cloak_observation_confirmation_patch=(
                micromachine_allied_cloak_observation_confirmation_patch
            ),
            micromachine_explicit_ability_caster_ownership_patch=(
                micromachine_explicit_ability_caster_ownership_patch
            ),
            micromachine_explicit_ability_staging_single_flight_patch=(
                micromachine_explicit_ability_staging_single_flight_patch
            ),
            micromachine_all_terran_combat_scouts_patch=(
                micromachine_all_terran_combat_scouts_patch
            ),
            micromachine_parallel_operations_ingame_hud_patch=(
                micromachine_parallel_operations_ingame_hud_patch
            ),
            micromachine_parallel_operation_lifecycle_review_closure_patch=(
                micromachine_parallel_operation_lifecycle_review_closure_patch
            ),
            micromachine_authoritative_parallel_operation_lifecycle_patch=(
                micromachine_authoritative_parallel_operation_lifecycle_patch
            ),
            micromachine_operation_production_ownership_restore_proof_patch=(
                micromachine_operation_production_ownership_restore_proof_patch
            ),
            micromachine_embedded_build_input_identity_patch=(
                micromachine_embedded_build_input_identity_patch
            ),
            micromachine_operation_production_review_closure_patch=(
                micromachine_operation_production_review_closure_patch
            ),
            micromachine_production_fifo_zero_owner_cleanup_patch=(
                micromachine_production_fifo_zero_owner_cleanup_patch
            ),
            micromachine_operation_edit_ownership_handoff_patch=(
                micromachine_operation_edit_ownership_handoff_patch
            ),
            micromachine_operation_edit_review_closure_patch=(
                micromachine_operation_edit_review_closure_patch
            ),
            micromachine_operation_transfer_atomic_admission_patch=(
                micromachine_operation_transfer_atomic_admission_patch
            ),
            micromachine_operation_transfer_runtime_preservation_patch=(
                micromachine_operation_transfer_runtime_preservation_patch
            ),
            micromachine_operation_transfer_transactional_closure_patch=(
                micromachine_operation_transfer_transactional_closure_patch
            ),
            micromachine_operation_transfer_final_review_closure_patch=(
                micromachine_operation_transfer_final_review_closure_patch
            ),
            micromachine_operation_transfer_idempotence_active_evidence_patch=(
                micromachine_operation_transfer_idempotence_active_evidence_patch
            ),
            micromachine_runtime_convergence_defense_placement_information_patch=(
                micromachine_runtime_convergence_defense_placement_information_patch
            ),
            micromachine_all_terran_harass_capability_evidence_patch=(
                micromachine_all_terran_harass_capability_evidence_patch
            ),
            micromachine_authoritative_battlefield_ownership_readiness_patch=(
                micromachine_authoritative_battlefield_ownership_readiness_patch
            ),
            micromachine_battlefield_projection_review_closure_patch=(
                micromachine_battlefield_projection_review_closure_patch
            ),
            micromachine_battlefield_identity_transfer_integrity_patch=(
                micromachine_battlefield_identity_transfer_integrity_patch
            ),
            micromachine_atomic_telemetry_publication_patch=(
                micromachine_atomic_telemetry_publication_patch
            ),
            micromachine_contextual_transfer_choice_projection_patch=(
                micromachine_contextual_transfer_choice_projection_patch
            ),
            micromachine_autonomous_owner_composition_evidence_patch=(
                micromachine_autonomous_owner_composition_evidence_patch
            ),
            micromachine_battlefield_review_closure_patch=(
                micromachine_battlefield_review_closure_patch
            ),
            micromachine_bounded_terminal_operation_hud_patch=(
                micromachine_bounded_terminal_operation_hud_patch
            ),
            micromachine_deterministic_pre_live_journey_adapter_patch=(
                micromachine_deterministic_pre_live_journey_adapter_patch
            ),
            micromachine_production_path_journey_review_closure_patch=(
                micromachine_production_path_journey_review_closure_patch
            ),
            s2client_patch=s2client_patch,
            hook_manifest=hook_manifest,
            map_pool=map_pool,
            blackboard_header=blackboard_header,
            source_attestation=source_attestation,
        )
        if git_provenance:
            config.resolved_s2client_build_dir.mkdir(parents=True)
            (config.resolved_s2client_build_dir / "libsc2api.a").write_text(
                "fixture s2client archive\n"
            )
            fixture_ctest = build_dir / "tools" / "ctest"
            fixture_ctest.parent.mkdir(parents=True, exist_ok=True)
            registry = {
                "tests": [
                    {
                        "name": test_name,
                        "command": [
                            str((build_dir / "bin" / executable_name).resolve())
                        ],
                    }
                    for test_name, executable_name in sorted(
                        MICROMACHINE_REQUIRED_NATIVE_TESTS.items()
                    )
                ]
            }
            registry_json = json.dumps(registry, separators=(",", ":"))
            fixture_ctest.write_text(
                "#!/bin/sh\n"
                'if [ "${3:-}" = "--show-only=json-v1" ]; then\n'
                f"  printf '%s\\n' {json.dumps(registry_json)}\n"
                "fi\n"
                "exit 0\n"
            )
            fixture_ctest.chmod(0o755)
            (build_dir / "CMakeCache.txt").write_text(
                f"CMAKE_CTEST_COMMAND:INTERNAL={fixture_ctest.resolve()}\n"
            )
            for test_name, executable_name in sorted(
                MICROMACHINE_REQUIRED_NATIVE_TESTS.items()
            ):
                executable = build_dir / "bin" / executable_name
                executable.write_text(f"#!/bin/sh\n# native-test:{test_name}\nexit 0\n")
                executable.chmod(0o755)
            if binary:
                self.rebuild_fixture_binary(config)
            else:
                write_micromachine_embedded_build_identity_header(config)
                write_micromachine_source_attestation(config)
        return config

    def init_git_repo(self, path: Path) -> str:
        subprocess.run(
            ["git", "-C", str(path), "init"], check=True, capture_output=True
        )
        (path / "README.md").write_text("fixture\n")
        subprocess.run(
            ["git", "-C", str(path), "add", "README.md"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "fixture",
            ],
            check=True,
            capture_output=True,
        )
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
