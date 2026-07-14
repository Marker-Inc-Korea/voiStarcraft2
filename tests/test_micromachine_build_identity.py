"""Tests for reproducible MicroMachine build identity reports."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_build_identity import (
    MicroMachineBuildIdentityConfig,
    build_micromachine_build_identity,
    read_build_identity,
    write_build_identity_report,
)


class MicroMachineBuildIdentityTest(unittest.TestCase):
    def test_expected_build_identity_is_stable_and_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)

            report = build_micromachine_build_identity(config)
            output = root / "identity.json"
            write_build_identity_report(report, output)

            self.assertTrue(report["ok"], report)
            self.assertEqual(32, report["schema_version"])
            self.assertTrue(str(report["identity"]).startswith("sha256:"))
            self.assertEqual(report["identity"], read_build_identity(output))
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
            self.assertEqual(
                report["checksums"]["micromachine_patch_sha256"],
                build_micromachine_build_identity(config)["checksums"]["micromachine_patch_sha256"],
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
                first["checksums"][
                    "micromachine_live_operation_unblock_patch_sha256"
                ],
                second["checksums"][
                    "micromachine_live_operation_unblock_patch_sha256"
                ],
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

    def test_guaranteed_producer_grounding_patch_checksum_changes_identity(self) -> None:
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

    def test_emergency_land_query_fallback_patch_checksum_changes_identity(self) -> None:
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
            binary_path.write_text("fake binary\n")
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
        s2client_patch = root / "s2client.patch"
        hook_manifest = root / "HOOK_MANIFEST.json"
        map_pool = root / "MICROMACHINE_MAP_POOL.json"
        blackboard_header = root / "voi_policy_blackboard.hpp"
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
            s2client_patch,
            hook_manifest,
            map_pool,
            blackboard_header,
        ):
            path.write_text(f"{path.name}\n")
        return MicroMachineBuildIdentityConfig(
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
            s2client_patch=s2client_patch,
            hook_manifest=hook_manifest,
            map_pool=map_pool,
            blackboard_header=blackboard_header,
        )

    def init_git_repo(self, path: Path) -> str:
        subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
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
