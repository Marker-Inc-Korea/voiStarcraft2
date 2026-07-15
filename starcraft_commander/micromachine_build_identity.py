"""Build identity reports for patched MicroMachine production evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Final


REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
MICROMACHINE_RUNTIME_MUTABLE_PATHS: Final[tuple[str, ...]] = (
    "bin/BotConfig.txt",
)
DEFAULT_MICROMACHINE_COMMIT: Final[str] = "eb893161371dab975a0a7e600f9e250ac03ec1ef"
DEFAULT_S2CLIENT_COMMIT: Final[str] = "614acc00abb5355e4c94a1b0279b46e9d845b7ce"
DEFAULT_MICROMACHINE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0001-macos-latest-s2client-policy-blackboard.patch"
)
DEFAULT_MICROMACHINE_TACTICAL_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0002-live-tactical-operation-fixes.patch"
)
DEFAULT_MICROMACHINE_PRODUCTION_FIX_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0003-production-live-qa-blockers.patch"
)
DEFAULT_MICROMACHINE_OPERATION_STATE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0004-live-operation-state-machine.patch"
)
DEFAULT_MICROMACHINE_ADDON_RECOVERY_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0005-addon-relocation-recovery.patch"
)
DEFAULT_MICROMACHINE_GROUNDED_ADDON_CANDIDATE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0006-grounded-addon-candidate-fix.patch"
)
DEFAULT_MICROMACHINE_GUARANTEED_PRODUCER_GROUNDING_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0007-guaranteed-producer-grounding.patch"
)
DEFAULT_MICROMACHINE_EMERGENCY_LAND_QUERY_FALLBACK_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0008-emergency-land-query-fallback.patch"
)
DEFAULT_MICROMACHINE_GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0009-grounded-production-and-observed-targeting.patch"
)
DEFAULT_MICROMACHINE_EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0010-exact-composition-production-progress.patch"
)
DEFAULT_MICROMACHINE_PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0011-production-resource-operation-persistence.patch"
)
DEFAULT_MICROMACHINE_LIVE_OPERATION_UNBLOCK_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0012-live-operation-unblock.patch"
)
DEFAULT_MICROMACHINE_STABLE_FLANK_STAGE_LATCH_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0013-stable-flank-stage-latch.patch"
)
DEFAULT_MICROMACHINE_PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0014-production-staging-and-observed-operation.patch"
)
DEFAULT_MICROMACHINE_ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0015-addon-query-footprint-validation.patch"
)
DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0016-authoritative-addon-placement-query.patch"
)
DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_EXECUTION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0017-authoritative-addon-execution.patch"
)
DEFAULT_MICROMACHINE_CONTINUOUS_ARMY_MACRO_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0018-continuous-army-macro.patch"
)
DEFAULT_MICROMACHINE_CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0019-continuous-army-economy-scaling.patch"
)
DEFAULT_MICROMACHINE_STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0020-standing-composition-reinforcement-waves.patch"
)
DEFAULT_MICROMACHINE_OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0021-offensive-sweep-self-base-exclusion.patch"
)
DEFAULT_MICROMACHINE_BOUNDED_PLACEMENT_QUERY_CACHE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0022-bounded-placement-query-cache.patch"
)
DEFAULT_MICROMACHINE_PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH: Final[
    Path
] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0023-production-facility-stability-and-tank-recovery.patch"
)
DEFAULT_MICROMACHINE_BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0024-balanced-composition-wave-production.patch"
)
DEFAULT_MICROMACHINE_EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0025-exact-composition-production-unblock.patch"
)
DEFAULT_MICROMACHINE_CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0026-continuous-combat-production-relaunch.patch"
)
DEFAULT_MICROMACHINE_RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0027-resource-throughput-and-expansion-backoff.patch"
)
DEFAULT_MICROMACHINE_STARTUP_TELEMETRY_INITIALIZATION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0028-startup-telemetry-initialization.patch"
)
DEFAULT_MICROMACHINE_GAS_WORKER_COMPLETION_CAP_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0029-gas-worker-completion-and-cap.patch"
)
DEFAULT_MICROMACHINE_STABLE_OFFENSIVE_SWEEP_TARGET_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0030-stable-offensive-sweep-target.patch"
)
DEFAULT_MICROMACHINE_ADAPTIVE_SUPPORT_COMPOSITION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0031-adaptive-support-composition.patch"
)
DEFAULT_MICROMACHINE_OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0032-operation-scoped-adaptive-combat-closure.patch"
)
DEFAULT_MICROMACHINE_REVIEW_CLOSURE_OPERATION_IDENTITY_FULL_COMPOSITION_PATCH: Final[
    Path
] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0033-review-closure-operation-identity-and-full-composition.patch"
)
DEFAULT_MICROMACHINE_SEMANTIC_OPERATION_PRODUCTION_CLOSURE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0034-semantic-operation-production-closure.patch"
)
DEFAULT_MICROMACHINE_ADAPTIVE_PRESSURE_STABLE_OPERATION_KEY_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0035-adaptive-pressure-stable-operation-key.patch"
)
DEFAULT_MICROMACHINE_TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0036-tactical-nuke-command-hierarchy.patch"
)
DEFAULT_MICROMACHINE_LOCATION_INTENT_TARGET_LOCK_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0037-location-intent-target-lock.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0038-explicit-terran-ability-execution.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0039-explicit-scout-command-epoch.patch"
)
DEFAULT_MICROMACHINE_STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0040-standing-production-continuity-closure.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH: Final[
    Path
] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0041-explicit-ability-caster-production-priority.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH: Final[
    Path
] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0042-explicit-ability-observation-confirmation.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0043-explicit-ability-production-isolation.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0044-explicit-ability-attempt-lifecycle.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0045-explicit-ability-review-closure.patch"
)
DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0046-authoritative-addon-runtime-clearance.patch"
)
DEFAULT_MICROMACHINE_BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0047-banshee-unit-specific-cloak-command.patch"
)
DEFAULT_MICROMACHINE_ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0048-allied-cloak-observation-confirmation.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0049-explicit-ability-caster-ownership.patch"
)
DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0050-explicit-ability-staging-single-flight.patch"
)
DEFAULT_S2CLIENT_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0001-s2client-macos-launchservices.patch"
)
DEFAULT_HOOK_MANIFEST: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "HOOK_MANIFEST.json"
)
DEFAULT_MAP_POOL: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "MICROMACHINE_MAP_POOL.json"
)
DEFAULT_BLACKBOARD_HEADER: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "voi_policy_blackboard.hpp"
)


@dataclass(frozen=True)
class MicroMachineBuildIdentityConfig:
    """Inputs needed to produce a reproducible MicroMachine build identity."""

    micromachine_dir: Path
    s2client_dir: Path
    micromachine_build_dir: Path
    s2client_build_dir: Path | None = None
    micromachine_commit: str = DEFAULT_MICROMACHINE_COMMIT
    s2client_commit: str = DEFAULT_S2CLIENT_COMMIT
    micromachine_patch: Path = DEFAULT_MICROMACHINE_PATCH
    micromachine_tactical_patch: Path = DEFAULT_MICROMACHINE_TACTICAL_PATCH
    micromachine_production_fix_patch: Path = (
        DEFAULT_MICROMACHINE_PRODUCTION_FIX_PATCH
    )
    micromachine_operation_state_patch: Path = (
        DEFAULT_MICROMACHINE_OPERATION_STATE_PATCH
    )
    micromachine_addon_recovery_patch: Path = (
        DEFAULT_MICROMACHINE_ADDON_RECOVERY_PATCH
    )
    micromachine_grounded_addon_candidate_patch: Path = (
        DEFAULT_MICROMACHINE_GROUNDED_ADDON_CANDIDATE_PATCH
    )
    micromachine_guaranteed_producer_grounding_patch: Path = (
        DEFAULT_MICROMACHINE_GUARANTEED_PRODUCER_GROUNDING_PATCH
    )
    micromachine_emergency_land_query_fallback_patch: Path = (
        DEFAULT_MICROMACHINE_EMERGENCY_LAND_QUERY_FALLBACK_PATCH
    )
    micromachine_grounded_production_observed_targeting_patch: Path = (
        DEFAULT_MICROMACHINE_GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH
    )
    micromachine_exact_composition_production_progress_patch: Path = (
        DEFAULT_MICROMACHINE_EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH
    )
    micromachine_production_resource_operation_persistence_patch: Path = (
        DEFAULT_MICROMACHINE_PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH
    )
    micromachine_live_operation_unblock_patch: Path = (
        DEFAULT_MICROMACHINE_LIVE_OPERATION_UNBLOCK_PATCH
    )
    micromachine_stable_flank_stage_latch_patch: Path = (
        DEFAULT_MICROMACHINE_STABLE_FLANK_STAGE_LATCH_PATCH
    )
    micromachine_production_staging_observed_operation_patch: Path = (
        DEFAULT_MICROMACHINE_PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH
    )
    micromachine_addon_query_footprint_validation_patch: Path = (
        DEFAULT_MICROMACHINE_ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH
    )
    micromachine_authoritative_addon_placement_query_patch: Path = (
        DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH
    )
    micromachine_authoritative_addon_execution_patch: Path = (
        DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_EXECUTION_PATCH
    )
    micromachine_continuous_army_macro_patch: Path = (
        DEFAULT_MICROMACHINE_CONTINUOUS_ARMY_MACRO_PATCH
    )
    micromachine_continuous_army_economy_scaling_patch: Path = (
        DEFAULT_MICROMACHINE_CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH
    )
    micromachine_standing_composition_reinforcement_waves_patch: Path = (
        DEFAULT_MICROMACHINE_STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH
    )
    micromachine_offensive_sweep_self_base_exclusion_patch: Path = (
        DEFAULT_MICROMACHINE_OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH
    )
    micromachine_bounded_placement_query_cache_patch: Path = (
        DEFAULT_MICROMACHINE_BOUNDED_PLACEMENT_QUERY_CACHE_PATCH
    )
    micromachine_production_facility_stability_tank_recovery_patch: Path = (
        DEFAULT_MICROMACHINE_PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH
    )
    micromachine_balanced_composition_wave_production_patch: Path = (
        DEFAULT_MICROMACHINE_BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH
    )
    micromachine_exact_composition_production_unblock_patch: Path = (
        DEFAULT_MICROMACHINE_EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH
    )
    micromachine_continuous_combat_production_relaunch_patch: Path = (
        DEFAULT_MICROMACHINE_CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH
    )
    micromachine_resource_throughput_expansion_backoff_patch: Path = (
        DEFAULT_MICROMACHINE_RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH
    )
    micromachine_startup_telemetry_initialization_patch: Path = (
        DEFAULT_MICROMACHINE_STARTUP_TELEMETRY_INITIALIZATION_PATCH
    )
    micromachine_gas_worker_completion_cap_patch: Path = (
        DEFAULT_MICROMACHINE_GAS_WORKER_COMPLETION_CAP_PATCH
    )
    micromachine_stable_offensive_sweep_target_patch: Path = (
        DEFAULT_MICROMACHINE_STABLE_OFFENSIVE_SWEEP_TARGET_PATCH
    )
    micromachine_adaptive_support_composition_patch: Path = (
        DEFAULT_MICROMACHINE_ADAPTIVE_SUPPORT_COMPOSITION_PATCH
    )
    micromachine_operation_scoped_adaptive_combat_closure_patch: Path = (
        DEFAULT_MICROMACHINE_OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH
    )
    micromachine_review_closure_operation_identity_full_composition_patch: Path = (
        DEFAULT_MICROMACHINE_REVIEW_CLOSURE_OPERATION_IDENTITY_FULL_COMPOSITION_PATCH
    )
    micromachine_semantic_operation_production_closure_patch: Path = (
        DEFAULT_MICROMACHINE_SEMANTIC_OPERATION_PRODUCTION_CLOSURE_PATCH
    )
    micromachine_adaptive_pressure_stable_operation_key_patch: Path = (
        DEFAULT_MICROMACHINE_ADAPTIVE_PRESSURE_STABLE_OPERATION_KEY_PATCH
    )
    micromachine_tactical_nuke_command_hierarchy_patch: Path = (
        DEFAULT_MICROMACHINE_TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH
    )
    micromachine_location_intent_target_lock_patch: Path = (
        DEFAULT_MICROMACHINE_LOCATION_INTENT_TARGET_LOCK_PATCH
    )
    micromachine_explicit_terran_ability_execution_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH
    )
    micromachine_explicit_scout_command_epoch_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH
    )
    micromachine_standing_production_continuity_closure_patch: Path = (
        DEFAULT_MICROMACHINE_STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH
    )
    micromachine_explicit_ability_caster_production_priority_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH
    )
    micromachine_explicit_ability_observation_confirmation_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH
    )
    micromachine_explicit_ability_production_isolation_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH
    )
    micromachine_explicit_ability_attempt_lifecycle_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH
    )
    micromachine_explicit_ability_review_closure_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH
    )
    micromachine_authoritative_addon_runtime_clearance_patch: Path = (
        DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH
    )
    micromachine_banshee_unit_specific_cloak_command_patch: Path = (
        DEFAULT_MICROMACHINE_BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH
    )
    micromachine_allied_cloak_observation_confirmation_patch: Path = (
        DEFAULT_MICROMACHINE_ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH
    )
    micromachine_explicit_ability_caster_ownership_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH
    )
    micromachine_explicit_ability_staging_single_flight_patch: Path = (
        DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH
    )
    s2client_patch: Path = DEFAULT_S2CLIENT_PATCH
    hook_manifest: Path = DEFAULT_HOOK_MANIFEST
    map_pool: Path = DEFAULT_MAP_POOL
    blackboard_header: Path = DEFAULT_BLACKBOARD_HEADER
    source_attestation: Path | None = None

    @property
    def binary_path(self) -> Path:
        return self.micromachine_build_dir / "bin" / "MicroMachine"

    @property
    def source_attestation_path(self) -> Path:
        return self.source_attestation or (
            self.micromachine_build_dir / "voi_source_attestation.json"
        )

    @property
    def resolved_s2client_build_dir(self) -> Path:
        return self.s2client_build_dir or (self.s2client_dir / "build-latest")


def build_micromachine_build_identity(
    config: MicroMachineBuildIdentityConfig,
) -> dict[str, object]:
    """Create a machine-readable identity report without modifying worktrees."""

    observed_micro = _git_head(config.micromachine_dir)
    observed_s2 = _git_head(config.s2client_dir)
    observed_micro_source_state = _git_source_state_sha256(
        config.micromachine_dir,
        excluded_roots=(config.micromachine_build_dir,),
        excluded_paths=MICROMACHINE_RUNTIME_MUTABLE_PATHS,
    )
    observed_s2_source_state = _git_source_state_sha256(
        config.s2client_dir,
        excluded_roots=(config.resolved_s2client_build_dir,),
    )
    observed_s2_build_state = _directory_state_sha256(
        config.resolved_s2client_build_dir
    )
    binary_exists = config.binary_path.exists()
    binary_is_regular = (
        binary_exists
        and not config.binary_path.is_symlink()
        and stat.S_ISREG(config.binary_path.stat().st_mode)
    )
    binary_is_executable = binary_is_regular and os.access(config.binary_path, os.X_OK)
    binary_sha256 = _sha256_file(config.binary_path) if binary_is_regular else None
    binary_size = config.binary_path.stat().st_size if binary_is_regular else None
    failures: list[dict[str, object]] = []
    if observed_micro is None:
        failures.append(
            {
                "code": "missing_micromachine_git_provenance",
                "expected": config.micromachine_commit,
                "path": str(config.micromachine_dir),
            }
        )
    elif observed_micro != config.micromachine_commit:
        failures.append(
            {
                "code": "micromachine_commit_mismatch",
                "expected": config.micromachine_commit,
                "actual": observed_micro,
            }
        )
    if observed_s2 is None:
        failures.append(
            {
                "code": "missing_s2client_git_provenance",
                "expected": config.s2client_commit,
                "path": str(config.s2client_dir),
            }
        )
    elif observed_s2 != config.s2client_commit:
        failures.append(
            {
                "code": "s2client_commit_mismatch",
                "expected": config.s2client_commit,
                "actual": observed_s2,
            }
        )
    if not binary_exists:
        failures.append(
            {
                "code": "missing_binary",
                "path": str(config.binary_path),
            }
        )
    elif not binary_is_regular:
        failures.append(
            {
                "code": "invalid_binary_file",
                "path": str(config.binary_path),
            }
        )
    elif not binary_is_executable:
        failures.append(
            {
                "code": "binary_not_executable",
                "path": str(config.binary_path),
            }
        )

    checksums = {
        "micromachine_patch_sha256": _sha256_file(config.micromachine_patch),
        "micromachine_tactical_patch_sha256": _sha256_file(
            config.micromachine_tactical_patch
        ),
        "micromachine_production_fix_patch_sha256": _sha256_file(
            config.micromachine_production_fix_patch
        ),
        "micromachine_operation_state_patch_sha256": _sha256_file(
            config.micromachine_operation_state_patch
        ),
        "micromachine_addon_recovery_patch_sha256": _sha256_file(
            config.micromachine_addon_recovery_patch
        ),
        "micromachine_grounded_addon_candidate_patch_sha256": _sha256_file(
            config.micromachine_grounded_addon_candidate_patch
        ),
        "micromachine_guaranteed_producer_grounding_patch_sha256": _sha256_file(
            config.micromachine_guaranteed_producer_grounding_patch
        ),
        "micromachine_emergency_land_query_fallback_patch_sha256": _sha256_file(
            config.micromachine_emergency_land_query_fallback_patch
        ),
        "micromachine_grounded_production_observed_targeting_patch_sha256": (
            _sha256_file(
                config.micromachine_grounded_production_observed_targeting_patch
            )
        ),
        "micromachine_exact_composition_production_progress_patch_sha256": (
            _sha256_file(
                config.micromachine_exact_composition_production_progress_patch
            )
        ),
        "micromachine_production_resource_operation_persistence_patch_sha256": (
            _sha256_file(
                config.micromachine_production_resource_operation_persistence_patch
            )
        ),
        "micromachine_live_operation_unblock_patch_sha256": _sha256_file(
            config.micromachine_live_operation_unblock_patch
        ),
        "micromachine_stable_flank_stage_latch_patch_sha256": _sha256_file(
            config.micromachine_stable_flank_stage_latch_patch
        ),
        "micromachine_production_staging_observed_operation_patch_sha256": (
            _sha256_file(
                config.micromachine_production_staging_observed_operation_patch
            )
        ),
        "micromachine_addon_query_footprint_validation_patch_sha256": (
            _sha256_file(
                config.micromachine_addon_query_footprint_validation_patch
            )
        ),
        "micromachine_authoritative_addon_placement_query_patch_sha256": (
            _sha256_file(
                config.micromachine_authoritative_addon_placement_query_patch
            )
        ),
        "micromachine_authoritative_addon_execution_patch_sha256": _sha256_file(
            config.micromachine_authoritative_addon_execution_patch
        ),
        "micromachine_continuous_army_macro_patch_sha256": _sha256_file(
            config.micromachine_continuous_army_macro_patch
        ),
        "micromachine_continuous_army_economy_scaling_patch_sha256": _sha256_file(
            config.micromachine_continuous_army_economy_scaling_patch
        ),
        "micromachine_standing_composition_reinforcement_waves_patch_sha256": (
            _sha256_file(
                config.micromachine_standing_composition_reinforcement_waves_patch
            )
        ),
        "micromachine_offensive_sweep_self_base_exclusion_patch_sha256": (
            _sha256_file(
                config.micromachine_offensive_sweep_self_base_exclusion_patch
            )
        ),
        "micromachine_bounded_placement_query_cache_patch_sha256": _sha256_file(
            config.micromachine_bounded_placement_query_cache_patch
        ),
        "micromachine_production_facility_stability_tank_recovery_patch_sha256": (
            _sha256_file(
                config.micromachine_production_facility_stability_tank_recovery_patch
            )
        ),
        "micromachine_balanced_composition_wave_production_patch_sha256": (
            _sha256_file(
                config.micromachine_balanced_composition_wave_production_patch
            )
        ),
        "micromachine_exact_composition_production_unblock_patch_sha256": (
            _sha256_file(
                config.micromachine_exact_composition_production_unblock_patch
            )
        ),
        "micromachine_continuous_combat_production_relaunch_patch_sha256": (
            _sha256_file(
                config.micromachine_continuous_combat_production_relaunch_patch
            )
        ),
        "micromachine_resource_throughput_expansion_backoff_patch_sha256": (
            _sha256_file(
                config.micromachine_resource_throughput_expansion_backoff_patch
            )
        ),
        "micromachine_startup_telemetry_initialization_patch_sha256": (
            _sha256_file(
                config.micromachine_startup_telemetry_initialization_patch
            )
        ),
        "micromachine_gas_worker_completion_cap_patch_sha256": (
            _sha256_file(config.micromachine_gas_worker_completion_cap_patch)
        ),
        "micromachine_stable_offensive_sweep_target_patch_sha256": (
            _sha256_file(config.micromachine_stable_offensive_sweep_target_patch)
        ),
        "micromachine_adaptive_support_composition_patch_sha256": (
            _sha256_file(config.micromachine_adaptive_support_composition_patch)
        ),
        "micromachine_operation_scoped_adaptive_combat_closure_patch_sha256": (
            _sha256_file(
                config.micromachine_operation_scoped_adaptive_combat_closure_patch
            )
        ),
        "micromachine_review_closure_operation_identity_full_composition_patch_sha256": (
            _sha256_file(
                config.micromachine_review_closure_operation_identity_full_composition_patch
            )
        ),
        "micromachine_semantic_operation_production_closure_patch_sha256": (
            _sha256_file(
                config.micromachine_semantic_operation_production_closure_patch
            )
        ),
        "micromachine_adaptive_pressure_stable_operation_key_patch_sha256": (
            _sha256_file(
                config.micromachine_adaptive_pressure_stable_operation_key_patch
            )
        ),
        "micromachine_tactical_nuke_command_hierarchy_patch_sha256": (
            _sha256_file(
                config.micromachine_tactical_nuke_command_hierarchy_patch
            )
        ),
        "micromachine_location_intent_target_lock_patch_sha256": (
            _sha256_file(
                config.micromachine_location_intent_target_lock_patch
            )
        ),
        "micromachine_explicit_terran_ability_execution_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_terran_ability_execution_patch
            )
        ),
        "micromachine_explicit_scout_command_epoch_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_scout_command_epoch_patch
            )
        ),
        "micromachine_standing_production_continuity_closure_patch_sha256": (
            _sha256_file(
                config.micromachine_standing_production_continuity_closure_patch
            )
        ),
        "micromachine_explicit_ability_caster_production_priority_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_ability_caster_production_priority_patch
            )
        ),
        "micromachine_explicit_ability_observation_confirmation_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_ability_observation_confirmation_patch
            )
        ),
        "micromachine_explicit_ability_production_isolation_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_ability_production_isolation_patch
            )
        ),
        "micromachine_explicit_ability_attempt_lifecycle_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_ability_attempt_lifecycle_patch
            )
        ),
        "micromachine_explicit_ability_review_closure_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_ability_review_closure_patch
            )
        ),
        "micromachine_authoritative_addon_runtime_clearance_patch_sha256": (
            _sha256_file(
                config.micromachine_authoritative_addon_runtime_clearance_patch
            )
        ),
        "micromachine_banshee_unit_specific_cloak_command_patch_sha256": (
            _sha256_file(
                config.micromachine_banshee_unit_specific_cloak_command_patch
            )
        ),
        "micromachine_allied_cloak_observation_confirmation_patch_sha256": (
            _sha256_file(
                config.micromachine_allied_cloak_observation_confirmation_patch
            )
        ),
        "micromachine_explicit_ability_caster_ownership_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_ability_caster_ownership_patch
            )
        ),
        "micromachine_explicit_ability_staging_single_flight_patch_sha256": (
            _sha256_file(
                config.micromachine_explicit_ability_staging_single_flight_patch
            )
        ),
        "s2client_patch_sha256": _sha256_file(config.s2client_patch),
        "hook_manifest_sha256": _sha256_file(config.hook_manifest),
        "map_pool_sha256": _sha256_file(config.map_pool),
        "blackboard_header_sha256": _sha256_file(config.blackboard_header),
        "binary_sha256": binary_sha256,
    }
    for checksum_name, checksum in checksums.items():
        if checksum_name == "binary_sha256" or checksum is not None:
            continue
        failures.append(
            {
                "code": "missing_required_build_input",
                "checksum": checksum_name,
            }
        )

    source_attestation = _read_source_attestation(config.source_attestation_path)
    if source_attestation is None:
        failures.append(
            {
                "code": "missing_source_attestation",
                "path": str(config.source_attestation_path),
            }
        )
    else:
        expected_input_identity = _build_input_identity(checksums)
        failures.extend(
            _source_attestation_failures(
                config,
                source_attestation,
                expected_input_identity=expected_input_identity,
                observed_micro=observed_micro,
                observed_s2=observed_s2,
                observed_micro_source_state=observed_micro_source_state,
                observed_s2_source_state=observed_s2_source_state,
                observed_s2_build_state=observed_s2_build_state,
            )
        )
        binary_attestation = source_attestation.get("binary")
        if (
            source_attestation.get("stage") != "build_finalized"
            or not isinstance(binary_attestation, Mapping)
        ):
            failures.append(
                {
                    "code": "missing_build_attestation",
                    "path": str(config.source_attestation_path),
                }
            )
        else:
            attested_binary = {
                "path": binary_attestation.get("path"),
                "sha256": binary_attestation.get("sha256"),
                "size_bytes": binary_attestation.get("size_bytes"),
                "build_input_identity": binary_attestation.get(
                    "build_input_identity"
                ),
            }
            observed_binary = {
                "path": str(config.binary_path.resolve()),
                "sha256": binary_sha256,
                "size_bytes": binary_size,
                "build_input_identity": expected_input_identity,
            }
            if attested_binary != observed_binary:
                failures.append(
                    {
                        "code": "binary_attestation_mismatch",
                        "expected": attested_binary,
                        "actual": observed_binary,
                        "path": str(config.source_attestation_path),
                    }
                )

    checksums["source_attestation_sha256"] = _sha256_file(
        config.source_attestation_path
    )
    checksums["micromachine_source_state_sha256"] = observed_micro_source_state
    checksums["s2client_source_state_sha256"] = observed_s2_source_state
    checksums["s2client_build_state_sha256"] = observed_s2_build_state
    identity_material = {
        "micromachine_commit": config.micromachine_commit,
        "s2client_commit": config.s2client_commit,
        "observed_micromachine_commit": observed_micro,
        "observed_s2client_commit": observed_s2,
        **checksums,
    }
    identity = "sha256:" + _sha256_json(identity_material)
    return {
        "schema_version": 50,
        "identity": identity,
        "ok": not failures,
        "failures": failures,
        "expected": {
            "micromachine_commit": config.micromachine_commit,
            "s2client_commit": config.s2client_commit,
        },
        "observed": {
            "micromachine_commit": observed_micro,
            "s2client_commit": observed_s2,
            "micromachine_source_state_sha256": observed_micro_source_state,
            "s2client_source_state_sha256": observed_s2_source_state,
            "s2client_build_state_sha256": observed_s2_build_state,
            "binary_sha256": binary_sha256,
            "binary_size_bytes": binary_size,
            "binary_executable": binary_is_executable,
        },
        "paths": {
            "micromachine_dir": str(config.micromachine_dir),
            "s2client_dir": str(config.s2client_dir),
            "s2client_build_dir": str(config.resolved_s2client_build_dir),
            "micromachine_build_dir": str(config.micromachine_build_dir),
            "binary": str(config.binary_path),
            "micromachine_patch": str(config.micromachine_patch),
            "micromachine_tactical_patch": str(config.micromachine_tactical_patch),
            "micromachine_production_fix_patch": str(
                config.micromachine_production_fix_patch
            ),
            "micromachine_operation_state_patch": str(
                config.micromachine_operation_state_patch
            ),
            "micromachine_addon_recovery_patch": str(
                config.micromachine_addon_recovery_patch
            ),
            "micromachine_grounded_addon_candidate_patch": str(
                config.micromachine_grounded_addon_candidate_patch
            ),
            "micromachine_guaranteed_producer_grounding_patch": str(
                config.micromachine_guaranteed_producer_grounding_patch
            ),
            "micromachine_emergency_land_query_fallback_patch": str(
                config.micromachine_emergency_land_query_fallback_patch
            ),
            "micromachine_grounded_production_observed_targeting_patch": str(
                config.micromachine_grounded_production_observed_targeting_patch
            ),
            "micromachine_exact_composition_production_progress_patch": str(
                config.micromachine_exact_composition_production_progress_patch
            ),
            "micromachine_production_resource_operation_persistence_patch": str(
                config.micromachine_production_resource_operation_persistence_patch
            ),
            "micromachine_live_operation_unblock_patch": str(
                config.micromachine_live_operation_unblock_patch
            ),
            "micromachine_stable_flank_stage_latch_patch": str(
                config.micromachine_stable_flank_stage_latch_patch
            ),
            "micromachine_production_staging_observed_operation_patch": str(
                config.micromachine_production_staging_observed_operation_patch
            ),
            "micromachine_addon_query_footprint_validation_patch": str(
                config.micromachine_addon_query_footprint_validation_patch
            ),
            "micromachine_authoritative_addon_placement_query_patch": str(
                config.micromachine_authoritative_addon_placement_query_patch
            ),
            "micromachine_authoritative_addon_execution_patch": str(
                config.micromachine_authoritative_addon_execution_patch
            ),
            "micromachine_continuous_army_macro_patch": str(
                config.micromachine_continuous_army_macro_patch
            ),
            "micromachine_continuous_army_economy_scaling_patch": str(
                config.micromachine_continuous_army_economy_scaling_patch
            ),
            "micromachine_standing_composition_reinforcement_waves_patch": str(
                config.micromachine_standing_composition_reinforcement_waves_patch
            ),
            "micromachine_offensive_sweep_self_base_exclusion_patch": str(
                config.micromachine_offensive_sweep_self_base_exclusion_patch
            ),
            "micromachine_bounded_placement_query_cache_patch": str(
                config.micromachine_bounded_placement_query_cache_patch
            ),
            "micromachine_production_facility_stability_tank_recovery_patch": str(
                config.micromachine_production_facility_stability_tank_recovery_patch
            ),
            "micromachine_balanced_composition_wave_production_patch": str(
                config.micromachine_balanced_composition_wave_production_patch
            ),
            "micromachine_exact_composition_production_unblock_patch": str(
                config.micromachine_exact_composition_production_unblock_patch
            ),
            "micromachine_continuous_combat_production_relaunch_patch": str(
                config.micromachine_continuous_combat_production_relaunch_patch
            ),
            "micromachine_resource_throughput_expansion_backoff_patch": str(
                config.micromachine_resource_throughput_expansion_backoff_patch
            ),
            "micromachine_startup_telemetry_initialization_patch": str(
                config.micromachine_startup_telemetry_initialization_patch
            ),
            "micromachine_gas_worker_completion_cap_patch": str(
                config.micromachine_gas_worker_completion_cap_patch
            ),
            "micromachine_stable_offensive_sweep_target_patch": str(
                config.micromachine_stable_offensive_sweep_target_patch
            ),
            "micromachine_adaptive_support_composition_patch": str(
                config.micromachine_adaptive_support_composition_patch
            ),
            "micromachine_operation_scoped_adaptive_combat_closure_patch": str(
                config.micromachine_operation_scoped_adaptive_combat_closure_patch
            ),
            "micromachine_review_closure_operation_identity_full_composition_patch": str(
                config.micromachine_review_closure_operation_identity_full_composition_patch
            ),
            "micromachine_semantic_operation_production_closure_patch": str(
                config.micromachine_semantic_operation_production_closure_patch
            ),
            "micromachine_adaptive_pressure_stable_operation_key_patch": str(
                config.micromachine_adaptive_pressure_stable_operation_key_patch
            ),
            "micromachine_tactical_nuke_command_hierarchy_patch": str(
                config.micromachine_tactical_nuke_command_hierarchy_patch
            ),
            "micromachine_location_intent_target_lock_patch": str(
                config.micromachine_location_intent_target_lock_patch
            ),
            "micromachine_explicit_terran_ability_execution_patch": str(
                config.micromachine_explicit_terran_ability_execution_patch
            ),
            "micromachine_explicit_scout_command_epoch_patch": str(
                config.micromachine_explicit_scout_command_epoch_patch
            ),
            "micromachine_standing_production_continuity_closure_patch": str(
                config.micromachine_standing_production_continuity_closure_patch
            ),
            "micromachine_explicit_ability_caster_production_priority_patch": str(
                config.micromachine_explicit_ability_caster_production_priority_patch
            ),
            "micromachine_explicit_ability_observation_confirmation_patch": str(
                config.micromachine_explicit_ability_observation_confirmation_patch
            ),
            "micromachine_explicit_ability_production_isolation_patch": str(
                config.micromachine_explicit_ability_production_isolation_patch
            ),
            "micromachine_explicit_ability_attempt_lifecycle_patch": str(
                config.micromachine_explicit_ability_attempt_lifecycle_patch
            ),
            "micromachine_explicit_ability_review_closure_patch": str(
                config.micromachine_explicit_ability_review_closure_patch
            ),
            "micromachine_authoritative_addon_runtime_clearance_patch": str(
                config.micromachine_authoritative_addon_runtime_clearance_patch
            ),
            "micromachine_banshee_unit_specific_cloak_command_patch": str(
                config.micromachine_banshee_unit_specific_cloak_command_patch
            ),
            "micromachine_allied_cloak_observation_confirmation_patch": str(
                config.micromachine_allied_cloak_observation_confirmation_patch
            ),
            "micromachine_explicit_ability_caster_ownership_patch": str(
                config.micromachine_explicit_ability_caster_ownership_patch
            ),
            "micromachine_explicit_ability_staging_single_flight_patch": str(
                config.micromachine_explicit_ability_staging_single_flight_patch
            ),
            "s2client_patch": str(config.s2client_patch),
            "hook_manifest": str(config.hook_manifest),
            "map_pool": str(config.map_pool),
            "blackboard_header": str(config.blackboard_header),
            "source_attestation": str(config.source_attestation_path),
        },
        "checksums": checksums,
    }


def write_build_identity_report(
    report: Mapping[str, object],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def write_micromachine_source_attestation(
    config: MicroMachineBuildIdentityConfig,
) -> dict[str, object]:
    """Record the canonical patched source state before identity verification."""

    input_checksums = _source_attestation_input_checksums(config)
    missing_inputs = sorted(
        name for name, checksum in input_checksums.items() if checksum is None
    )
    if missing_inputs:
        raise ValueError(
            "cannot attest source state with missing build inputs: "
            + ", ".join(missing_inputs)
        )
    micromachine_source_state = _git_source_state_sha256(
        config.micromachine_dir,
        excluded_roots=(config.micromachine_build_dir,),
        excluded_paths=MICROMACHINE_RUNTIME_MUTABLE_PATHS,
    )
    s2client_source_state = _git_source_state_sha256(
        config.s2client_dir,
        excluded_roots=(config.resolved_s2client_build_dir,),
    )
    s2client_build_state = _directory_state_sha256(
        config.resolved_s2client_build_dir
    )
    micromachine_commit = _git_head(config.micromachine_dir)
    s2client_commit = _git_head(config.s2client_dir)
    if (
        micromachine_source_state is None
        or s2client_source_state is None
        or micromachine_commit is None
        or s2client_commit is None
    ):
        raise ValueError("cannot attest source state without git worktrees.")
    if s2client_build_state is None:
        raise ValueError("cannot attest missing s2client build state.")
    payload = {
        "schema_version": 3,
        "stage": "source_attested",
        "micromachine_commit": micromachine_commit,
        "s2client_commit": s2client_commit,
        "build_input_identity": _build_input_identity(input_checksums),
        "micromachine_source_state_sha256": micromachine_source_state,
        "s2client_source_state_sha256": s2client_source_state,
        "s2client_build_state_sha256": s2client_build_state,
    }
    _write_json_atomic(config.source_attestation_path, payload)
    return payload


def write_micromachine_build_attestation(
    config: MicroMachineBuildIdentityConfig,
) -> dict[str, object]:
    """Bind the completed executable to the previously attested source state."""

    source_attestation = _read_source_attestation(config.source_attestation_path)
    if source_attestation is None:
        raise ValueError("cannot finalize missing source attestation.")
    input_checksums = _source_attestation_input_checksums(config)
    missing_inputs = sorted(
        name for name, checksum in input_checksums.items() if checksum is None
    )
    if missing_inputs:
        raise ValueError(
            "cannot finalize build with missing build inputs: "
            + ", ".join(missing_inputs)
        )
    expected_input_identity = _build_input_identity(input_checksums)
    observed_micro = _git_head(config.micromachine_dir)
    observed_s2 = _git_head(config.s2client_dir)
    observed_micro_source_state = _git_source_state_sha256(
        config.micromachine_dir,
        excluded_roots=(config.micromachine_build_dir,),
        excluded_paths=MICROMACHINE_RUNTIME_MUTABLE_PATHS,
    )
    observed_s2_source_state = _git_source_state_sha256(
        config.s2client_dir,
        excluded_roots=(config.resolved_s2client_build_dir,),
    )
    observed_s2_build_state = _directory_state_sha256(
        config.resolved_s2client_build_dir
    )
    source_failures = _source_attestation_failures(
        config,
        source_attestation,
        expected_input_identity=expected_input_identity,
        observed_micro=observed_micro,
        observed_s2=observed_s2,
        observed_micro_source_state=observed_micro_source_state,
        observed_s2_source_state=observed_s2_source_state,
        observed_s2_build_state=observed_s2_build_state,
    )
    if source_failures:
        codes = ", ".join(str(failure["code"]) for failure in source_failures)
        raise ValueError(f"cannot finalize changed or invalid source state: {codes}")

    binary_path = config.binary_path
    if (
        not binary_path.exists()
        or binary_path.is_symlink()
        or not stat.S_ISREG(binary_path.stat().st_mode)
    ):
        raise ValueError("cannot finalize missing or non-regular MicroMachine binary.")
    if not os.access(binary_path, os.X_OK):
        raise ValueError("cannot finalize non-executable MicroMachine binary.")
    binary_sha256 = _sha256_file(binary_path)
    if binary_sha256 is None:
        raise ValueError("cannot hash MicroMachine binary.")

    payload = dict(source_attestation)
    payload.update(
        {
            "schema_version": 3,
            "stage": "build_finalized",
            "binary": {
                "path": str(binary_path.resolve()),
                "sha256": binary_sha256,
                "size_bytes": binary_path.stat().st_size,
                "build_input_identity": expected_input_identity,
            },
        }
    )
    _write_json_atomic(config.source_attestation_path, payload)
    return payload


def read_build_identity(path: Path | str) -> str | None:
    report_path = Path(path)
    if not report_path.exists():
        return None
    payload = json.loads(report_path.read_text())
    if not isinstance(payload, Mapping):
        return None
    identity = payload.get("identity")
    return identity if isinstance(identity, str) and identity else None


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit MicroMachine build identity.")
    parser.add_argument("--micromachine-dir")
    parser.add_argument("--s2client-dir")
    parser.add_argument("--micromachine-build-dir")
    parser.add_argument("--s2client-build-dir")
    parser.add_argument("--micromachine-commit", default=DEFAULT_MICROMACHINE_COMMIT)
    parser.add_argument("--s2client-commit", default=DEFAULT_S2CLIENT_COMMIT)
    parser.add_argument("--micromachine-patch", default=str(DEFAULT_MICROMACHINE_PATCH))
    parser.add_argument(
        "--micromachine-tactical-patch",
        default=str(DEFAULT_MICROMACHINE_TACTICAL_PATCH),
    )
    parser.add_argument(
        "--micromachine-production-fix-patch",
        default=str(DEFAULT_MICROMACHINE_PRODUCTION_FIX_PATCH),
    )
    parser.add_argument(
        "--micromachine-operation-state-patch",
        default=str(DEFAULT_MICROMACHINE_OPERATION_STATE_PATCH),
    )
    parser.add_argument(
        "--micromachine-addon-recovery-patch",
        default=str(DEFAULT_MICROMACHINE_ADDON_RECOVERY_PATCH),
    )
    parser.add_argument(
        "--micromachine-grounded-addon-candidate-patch",
        default=str(DEFAULT_MICROMACHINE_GROUNDED_ADDON_CANDIDATE_PATCH),
    )
    parser.add_argument(
        "--micromachine-guaranteed-producer-grounding-patch",
        default=str(DEFAULT_MICROMACHINE_GUARANTEED_PRODUCER_GROUNDING_PATCH),
    )
    parser.add_argument(
        "--micromachine-emergency-land-query-fallback-patch",
        default=str(DEFAULT_MICROMACHINE_EMERGENCY_LAND_QUERY_FALLBACK_PATCH),
    )
    parser.add_argument(
        "--micromachine-grounded-production-observed-targeting-patch",
        default=str(
            DEFAULT_MICROMACHINE_GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-exact-composition-production-progress-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-production-resource-operation-persistence-patch",
        default=str(
            DEFAULT_MICROMACHINE_PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-live-operation-unblock-patch",
        default=str(DEFAULT_MICROMACHINE_LIVE_OPERATION_UNBLOCK_PATCH),
    )
    parser.add_argument(
        "--micromachine-stable-flank-stage-latch-patch",
        default=str(DEFAULT_MICROMACHINE_STABLE_FLANK_STAGE_LATCH_PATCH),
    )
    parser.add_argument(
        "--micromachine-production-staging-observed-operation-patch",
        default=str(
            DEFAULT_MICROMACHINE_PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-addon-query-footprint-validation-patch",
        default=str(
            DEFAULT_MICROMACHINE_ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-authoritative-addon-placement-query-patch",
        default=str(
            DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-authoritative-addon-execution-patch",
        default=str(DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_EXECUTION_PATCH),
    )
    parser.add_argument(
        "--micromachine-continuous-army-macro-patch",
        default=str(DEFAULT_MICROMACHINE_CONTINUOUS_ARMY_MACRO_PATCH),
    )
    parser.add_argument(
        "--micromachine-continuous-army-economy-scaling-patch",
        default=str(DEFAULT_MICROMACHINE_CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH),
    )
    parser.add_argument(
        "--micromachine-standing-composition-reinforcement-waves-patch",
        default=str(
            DEFAULT_MICROMACHINE_STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-offensive-sweep-self-base-exclusion-patch",
        default=str(
            DEFAULT_MICROMACHINE_OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-bounded-placement-query-cache-patch",
        default=str(DEFAULT_MICROMACHINE_BOUNDED_PLACEMENT_QUERY_CACHE_PATCH),
    )
    parser.add_argument(
        "--micromachine-production-facility-stability-tank-recovery-patch",
        default=str(
            DEFAULT_MICROMACHINE_PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-balanced-composition-wave-production-patch",
        default=str(
            DEFAULT_MICROMACHINE_BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-exact-composition-production-unblock-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-continuous-combat-production-relaunch-patch",
        default=str(
            DEFAULT_MICROMACHINE_CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-resource-throughput-expansion-backoff-patch",
        default=str(
            DEFAULT_MICROMACHINE_RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-startup-telemetry-initialization-patch",
        default=str(
            DEFAULT_MICROMACHINE_STARTUP_TELEMETRY_INITIALIZATION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-gas-worker-completion-cap-patch",
        default=str(DEFAULT_MICROMACHINE_GAS_WORKER_COMPLETION_CAP_PATCH),
    )
    parser.add_argument(
        "--micromachine-stable-offensive-sweep-target-patch",
        default=str(DEFAULT_MICROMACHINE_STABLE_OFFENSIVE_SWEEP_TARGET_PATCH),
    )
    parser.add_argument(
        "--micromachine-adaptive-support-composition-patch",
        default=str(DEFAULT_MICROMACHINE_ADAPTIVE_SUPPORT_COMPOSITION_PATCH),
    )
    parser.add_argument(
        "--micromachine-operation-scoped-adaptive-combat-closure-patch",
        default=str(
            DEFAULT_MICROMACHINE_OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-review-closure-operation-identity-full-composition-patch",
        default=str(
            DEFAULT_MICROMACHINE_REVIEW_CLOSURE_OPERATION_IDENTITY_FULL_COMPOSITION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-semantic-operation-production-closure-patch",
        default=str(
            DEFAULT_MICROMACHINE_SEMANTIC_OPERATION_PRODUCTION_CLOSURE_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-adaptive-pressure-stable-operation-key-patch",
        default=str(
            DEFAULT_MICROMACHINE_ADAPTIVE_PRESSURE_STABLE_OPERATION_KEY_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-tactical-nuke-command-hierarchy-patch",
        default=str(
            DEFAULT_MICROMACHINE_TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-location-intent-target-lock-patch",
        default=str(
            DEFAULT_MICROMACHINE_LOCATION_INTENT_TARGET_LOCK_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-terran-ability-execution-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-scout-command-epoch-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-standing-production-continuity-closure-patch",
        default=str(
            DEFAULT_MICROMACHINE_STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-ability-caster-production-priority-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-ability-observation-confirmation-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-ability-production-isolation-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-ability-attempt-lifecycle-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-ability-review-closure-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-authoritative-addon-runtime-clearance-patch",
        default=str(
            DEFAULT_MICROMACHINE_AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-banshee-unit-specific-cloak-command-patch",
        default=str(
            DEFAULT_MICROMACHINE_BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-allied-cloak-observation-confirmation-patch",
        default=str(
            DEFAULT_MICROMACHINE_ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-ability-caster-ownership-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH
        ),
    )
    parser.add_argument(
        "--micromachine-explicit-ability-staging-single-flight-patch",
        default=str(
            DEFAULT_MICROMACHINE_EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH
        ),
    )
    parser.add_argument("--s2client-patch", default=str(DEFAULT_S2CLIENT_PATCH))
    parser.add_argument("--hook-manifest", default=str(DEFAULT_HOOK_MANIFEST))
    parser.add_argument("--map-pool", default=str(DEFAULT_MAP_POOL))
    parser.add_argument("--blackboard-header", default=str(DEFAULT_BLACKBOARD_HEADER))
    parser.add_argument("--source-attestation")
    parser.add_argument("--initialize-source-attestation", action="store_true")
    parser.add_argument("--finalize-build-attestation", action="store_true")
    parser.add_argument("--read-report")
    parser.add_argument("--output")
    parser.add_argument("--field", choices=("identity", "ok", "failure-codes"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.initialize_source_attestation and args.finalize_build_attestation:
        raise SystemExit(
            "--initialize-source-attestation and --finalize-build-attestation "
            "are mutually exclusive."
        )
    if args.read_report:
        payload = _read_report(Path(args.read_report))
        if args.field == "identity":
            print(payload.get("identity") or "unrecorded")
        elif args.field == "ok":
            print("1" if payload.get("ok") is True else "0")
        elif args.field == "failure-codes":
            print(" ".join(_failure_codes(payload)))
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if not args.micromachine_dir or not args.s2client_dir or not args.micromachine_build_dir:
        raise SystemExit("--micromachine-dir, --s2client-dir, and --micromachine-build-dir are required.")
    config = MicroMachineBuildIdentityConfig(
            micromachine_dir=Path(args.micromachine_dir),
            s2client_dir=Path(args.s2client_dir),
            micromachine_build_dir=Path(args.micromachine_build_dir),
            s2client_build_dir=(
                Path(args.s2client_build_dir)
                if args.s2client_build_dir
                else None
            ),
            micromachine_commit=args.micromachine_commit,
            s2client_commit=args.s2client_commit,
            micromachine_patch=Path(args.micromachine_patch),
            micromachine_tactical_patch=Path(args.micromachine_tactical_patch),
            micromachine_production_fix_patch=Path(
                args.micromachine_production_fix_patch
            ),
            micromachine_operation_state_patch=Path(
                args.micromachine_operation_state_patch
            ),
            micromachine_addon_recovery_patch=Path(
                args.micromachine_addon_recovery_patch
            ),
            micromachine_grounded_addon_candidate_patch=Path(
                args.micromachine_grounded_addon_candidate_patch
            ),
            micromachine_guaranteed_producer_grounding_patch=Path(
                args.micromachine_guaranteed_producer_grounding_patch
            ),
            micromachine_emergency_land_query_fallback_patch=Path(
                args.micromachine_emergency_land_query_fallback_patch
            ),
            micromachine_grounded_production_observed_targeting_patch=Path(
                args.micromachine_grounded_production_observed_targeting_patch
            ),
            micromachine_exact_composition_production_progress_patch=Path(
                args.micromachine_exact_composition_production_progress_patch
            ),
            micromachine_production_resource_operation_persistence_patch=Path(
                args.micromachine_production_resource_operation_persistence_patch
            ),
            micromachine_live_operation_unblock_patch=Path(
                args.micromachine_live_operation_unblock_patch
            ),
            micromachine_stable_flank_stage_latch_patch=Path(
                args.micromachine_stable_flank_stage_latch_patch
            ),
            micromachine_production_staging_observed_operation_patch=Path(
                args.micromachine_production_staging_observed_operation_patch
            ),
            micromachine_addon_query_footprint_validation_patch=Path(
                args.micromachine_addon_query_footprint_validation_patch
            ),
            micromachine_authoritative_addon_placement_query_patch=Path(
                args.micromachine_authoritative_addon_placement_query_patch
            ),
            micromachine_authoritative_addon_execution_patch=Path(
                args.micromachine_authoritative_addon_execution_patch
            ),
            micromachine_continuous_army_macro_patch=Path(
                args.micromachine_continuous_army_macro_patch
            ),
            micromachine_continuous_army_economy_scaling_patch=Path(
                args.micromachine_continuous_army_economy_scaling_patch
            ),
            micromachine_standing_composition_reinforcement_waves_patch=Path(
                args.micromachine_standing_composition_reinforcement_waves_patch
            ),
            micromachine_offensive_sweep_self_base_exclusion_patch=Path(
                args.micromachine_offensive_sweep_self_base_exclusion_patch
            ),
            micromachine_bounded_placement_query_cache_patch=Path(
                args.micromachine_bounded_placement_query_cache_patch
            ),
            micromachine_production_facility_stability_tank_recovery_patch=Path(
                args.micromachine_production_facility_stability_tank_recovery_patch
            ),
            micromachine_balanced_composition_wave_production_patch=Path(
                args.micromachine_balanced_composition_wave_production_patch
            ),
            micromachine_exact_composition_production_unblock_patch=Path(
                args.micromachine_exact_composition_production_unblock_patch
            ),
            micromachine_continuous_combat_production_relaunch_patch=Path(
                args.micromachine_continuous_combat_production_relaunch_patch
            ),
            micromachine_resource_throughput_expansion_backoff_patch=Path(
                args.micromachine_resource_throughput_expansion_backoff_patch
            ),
            micromachine_startup_telemetry_initialization_patch=Path(
                args.micromachine_startup_telemetry_initialization_patch
            ),
            micromachine_gas_worker_completion_cap_patch=Path(
                args.micromachine_gas_worker_completion_cap_patch
            ),
            micromachine_stable_offensive_sweep_target_patch=Path(
                args.micromachine_stable_offensive_sweep_target_patch
            ),
            micromachine_adaptive_support_composition_patch=Path(
                args.micromachine_adaptive_support_composition_patch
            ),
            micromachine_operation_scoped_adaptive_combat_closure_patch=Path(
                args.micromachine_operation_scoped_adaptive_combat_closure_patch
            ),
            micromachine_review_closure_operation_identity_full_composition_patch=Path(
                args.micromachine_review_closure_operation_identity_full_composition_patch
            ),
            micromachine_semantic_operation_production_closure_patch=Path(
                args.micromachine_semantic_operation_production_closure_patch
            ),
            micromachine_adaptive_pressure_stable_operation_key_patch=Path(
                args.micromachine_adaptive_pressure_stable_operation_key_patch
            ),
            micromachine_tactical_nuke_command_hierarchy_patch=Path(
                args.micromachine_tactical_nuke_command_hierarchy_patch
            ),
            micromachine_location_intent_target_lock_patch=Path(
                args.micromachine_location_intent_target_lock_patch
            ),
            micromachine_explicit_terran_ability_execution_patch=Path(
                args.micromachine_explicit_terran_ability_execution_patch
            ),
            micromachine_explicit_scout_command_epoch_patch=Path(
                args.micromachine_explicit_scout_command_epoch_patch
            ),
            micromachine_standing_production_continuity_closure_patch=Path(
                args.micromachine_standing_production_continuity_closure_patch
            ),
            micromachine_explicit_ability_caster_production_priority_patch=Path(
                args.micromachine_explicit_ability_caster_production_priority_patch
            ),
            micromachine_explicit_ability_observation_confirmation_patch=Path(
                args.micromachine_explicit_ability_observation_confirmation_patch
            ),
            micromachine_explicit_ability_production_isolation_patch=Path(
                args.micromachine_explicit_ability_production_isolation_patch
            ),
            micromachine_explicit_ability_attempt_lifecycle_patch=Path(
                args.micromachine_explicit_ability_attempt_lifecycle_patch
            ),
            micromachine_explicit_ability_review_closure_patch=Path(
                args.micromachine_explicit_ability_review_closure_patch
            ),
            micromachine_authoritative_addon_runtime_clearance_patch=Path(
                args.micromachine_authoritative_addon_runtime_clearance_patch
            ),
            micromachine_banshee_unit_specific_cloak_command_patch=Path(
                args.micromachine_banshee_unit_specific_cloak_command_patch
            ),
            micromachine_allied_cloak_observation_confirmation_patch=Path(
                args.micromachine_allied_cloak_observation_confirmation_patch
            ),
            micromachine_explicit_ability_caster_ownership_patch=Path(
                args.micromachine_explicit_ability_caster_ownership_patch
            ),
            micromachine_explicit_ability_staging_single_flight_patch=Path(
                args.micromachine_explicit_ability_staging_single_flight_patch
            ),
            s2client_patch=Path(args.s2client_patch),
            hook_manifest=Path(args.hook_manifest),
            map_pool=Path(args.map_pool),
            blackboard_header=Path(args.blackboard_header),
            source_attestation=(
                Path(args.source_attestation) if args.source_attestation else None
            ),
        )
    if args.initialize_source_attestation:
        write_micromachine_source_attestation(config)
        if not args.output and not args.field:
            return 0
    if args.finalize_build_attestation:
        write_micromachine_build_attestation(config)
    report = build_micromachine_build_identity(config)
    if args.output:
        write_build_identity_report(report, Path(args.output))
    if args.field == "identity":
        print(report["identity"])
    elif args.field == "ok":
        print("1" if report["ok"] else "0")
    elif not args.output:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_attestation_input_checksums(
    config: MicroMachineBuildIdentityConfig,
) -> dict[str, str | None]:
    excluded = {
        "micromachine_dir",
        "s2client_dir",
        "s2client_build_dir",
        "micromachine_build_dir",
        "source_attestation",
    }
    checksums: dict[str, str | None] = {}
    for config_field in fields(config):
        if config_field.name in excluded:
            continue
        value = getattr(config, config_field.name)
        if isinstance(value, Path):
            checksums[f"{config_field.name}_sha256"] = _sha256_file(value)
    return checksums


def _build_input_identity(checksums: Mapping[str, object]) -> str:
    build_inputs = {
        key: value
        for key, value in checksums.items()
        if key != "binary_sha256"
        and key != "source_attestation_sha256"
        and not key.endswith("_source_state_sha256")
    }
    return "sha256:" + _sha256_json(build_inputs)


def _read_source_attestation(path: Path) -> Mapping[str, object] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _source_attestation_failures(
    config: MicroMachineBuildIdentityConfig,
    source_attestation: Mapping[str, object],
    *,
    expected_input_identity: str,
    observed_micro: str | None,
    observed_s2: str | None,
    observed_micro_source_state: str | None,
    observed_s2_source_state: str | None,
    observed_s2_build_state: str | None,
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    if source_attestation.get("schema_version") != 3:
        failures.append(
            {
                "code": "invalid_source_attestation",
                "source": "schema_version",
                "path": str(config.source_attestation_path),
            }
        )
    if source_attestation.get("build_input_identity") != expected_input_identity:
        failures.append(
            {
                "code": "source_attestation_input_mismatch",
                "expected": expected_input_identity,
                "actual": source_attestation.get("build_input_identity"),
            }
        )
    for name, expected, actual in (
        ("micromachine", source_attestation.get("micromachine_commit"), observed_micro),
        ("s2client", source_attestation.get("s2client_commit"), observed_s2),
    ):
        if not isinstance(expected, str) or not expected:
            failures.append(
                {
                    "code": "invalid_source_attestation",
                    "source": f"{name}_commit",
                    "path": str(config.source_attestation_path),
                }
            )
        elif expected != actual:
            failures.append(
                {
                    "code": f"{name}_attested_commit_mismatch",
                    "expected": expected,
                    "actual": actual,
                }
            )
    for name, expected, actual in (
        (
            "micromachine",
            source_attestation.get("micromachine_source_state_sha256"),
            observed_micro_source_state,
        ),
        (
            "s2client",
            source_attestation.get("s2client_source_state_sha256"),
            observed_s2_source_state,
        ),
    ):
        if not isinstance(expected, str) or not expected:
            failures.append(
                {
                    "code": "invalid_source_attestation",
                    "source": name,
                    "path": str(config.source_attestation_path),
                }
            )
        elif expected != actual:
            failures.append(
                {
                    "code": f"{name}_source_state_mismatch",
                    "expected": expected,
                    "actual": actual,
                }
            )
    expected_s2_build_state = source_attestation.get("s2client_build_state_sha256")
    if not isinstance(expected_s2_build_state, str) or not expected_s2_build_state:
        failures.append(
            {
                "code": "invalid_source_attestation",
                "source": "s2client_build",
                "path": str(config.source_attestation_path),
            }
        )
    elif expected_s2_build_state != observed_s2_build_state:
        failures.append(
            {
                "code": "s2client_build_state_mismatch",
                "expected": expected_s2_build_state,
                "actual": observed_s2_build_state,
            }
        )
    return failures


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _git_head(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _git_source_state_sha256(
    path: Path,
    *,
    excluded_roots: Sequence[Path] = (),
    excluded_paths: Sequence[str] = (),
) -> str | None:
    if not (path / ".git").exists():
        return None
    normalized_excluded_paths = tuple(
        item.strip().strip("/")
        for item in excluded_paths
        if isinstance(item, str) and item.strip().strip("/")
    )
    diff_command = [
        "git",
        "-C",
        str(path),
        "diff",
        "--binary",
        "HEAD",
        "--",
        ".",
    ]
    diff_command.extend(
        f":(exclude){relative_path}"
        for relative_path in normalized_excluded_paths
    )
    try:
        tracked = subprocess.run(
            diff_command,
            check=True,
            capture_output=True,
        ).stdout
        untracked_output = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None

    digest = hashlib.sha256()
    digest.update(tracked)
    resolved_excluded_roots = tuple(root.resolve() for root in excluded_roots)
    for raw_path in sorted(filter(None, untracked_output.split(b"\0"))):
        relative_path = raw_path.decode("utf-8", errors="surrogateescape")
        normalized_relative_path = relative_path.strip("/")
        if any(
            normalized_relative_path == excluded
            or normalized_relative_path.startswith(excluded + "/")
            for excluded in normalized_excluded_paths
        ):
            continue
        candidate = path / relative_path
        resolved_candidate = candidate.resolve()
        if any(
            resolved_candidate == excluded
            or resolved_candidate.is_relative_to(excluded)
            for excluded in resolved_excluded_roots
        ):
            continue
        if not candidate.is_file():
            continue
        digest.update(b"\0untracked\0")
        digest.update(raw_path)
        digest.update(b"\0")
        try:
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
    return digest.hexdigest()


def _directory_state_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_dir():
        return None
    try:
        files = sorted(
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file() and not candidate.is_symlink()
        )
        digest = hashlib.sha256()
        for candidate in files:
            relative_path = candidate.relative_to(path).as_posix().encode(
                "utf-8",
                errors="surrogateescape",
            )
            digest.update(b"\0file\0")
            digest.update(relative_path)
            digest.update(b"\0")
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _read_report(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"identity": "unrecorded", "ok": False, "failures": [{"code": "missing_build_identity_report"}]}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"identity": "unrecorded", "ok": False, "failures": [{"code": "invalid_build_identity_report"}]}
    if not isinstance(payload, dict):
        return {"identity": "unrecorded", "ok": False, "failures": [{"code": "invalid_build_identity_report"}]}
    return payload


def _failure_codes(payload: Mapping[str, object]) -> list[str]:
    failures = payload.get("failures")
    if not isinstance(failures, list):
        return []
    codes: list[str] = []
    for failure in failures:
        if not isinstance(failure, Mapping):
            continue
        code = failure.get("code")
        if isinstance(code, str) and code:
            codes.append(code)
    return codes


if __name__ == "__main__":
    raise SystemExit(main())
