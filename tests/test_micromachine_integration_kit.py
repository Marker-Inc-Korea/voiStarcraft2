"""Tests for the MicroMachine C++ integration kit artifacts."""

import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.policy_modulation import MICROMACHINE_TACTICAL_ABILITIES


REPO_ROOT = Path(__file__).resolve().parents[1]
KIT_DIR = REPO_ROOT / "integrations" / "micromachine"
PATCH_FILE = KIT_DIR / "patches" / "0001-macos-latest-s2client-policy-blackboard.patch"
TACTICAL_PATCH_FILE = KIT_DIR / "patches" / "0002-live-tactical-operation-fixes.patch"
PRODUCTION_FIX_PATCH_FILE = (
    KIT_DIR / "patches" / "0003-production-live-qa-blockers.patch"
)
OPERATION_STATE_PATCH_FILE = (
    KIT_DIR / "patches" / "0004-live-operation-state-machine.patch"
)
ADDON_RECOVERY_PATCH_FILE = (
    KIT_DIR / "patches" / "0005-addon-relocation-recovery.patch"
)
GROUNDED_ADDON_CANDIDATE_PATCH_FILE = (
    KIT_DIR / "patches" / "0006-grounded-addon-candidate-fix.patch"
)
GUARANTEED_PRODUCER_GROUNDING_PATCH_FILE = (
    KIT_DIR / "patches" / "0007-guaranteed-producer-grounding.patch"
)
EMERGENCY_LAND_QUERY_FALLBACK_PATCH_FILE = (
    KIT_DIR / "patches" / "0008-emergency-land-query-fallback.patch"
)
GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH_FILE = (
    KIT_DIR / "patches" / "0009-grounded-production-and-observed-targeting.patch"
)
EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH_FILE = (
    KIT_DIR / "patches" / "0010-exact-composition-production-progress.patch"
)
PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH_FILE = (
    KIT_DIR / "patches" / "0011-production-resource-operation-persistence.patch"
)
LIVE_OPERATION_UNBLOCK_PATCH_FILE = (
    KIT_DIR / "patches" / "0012-live-operation-unblock.patch"
)
STABLE_FLANK_STAGE_LATCH_PATCH_FILE = (
    KIT_DIR / "patches" / "0013-stable-flank-stage-latch.patch"
)
PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH_FILE = (
    KIT_DIR / "patches" / "0014-production-staging-and-observed-operation.patch"
)
ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH_FILE = (
    KIT_DIR / "patches" / "0015-addon-query-footprint-validation.patch"
)
AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH_FILE = (
    KIT_DIR / "patches" / "0016-authoritative-addon-placement-query.patch"
)
AUTHORITATIVE_ADDON_EXECUTION_PATCH_FILE = (
    KIT_DIR / "patches" / "0017-authoritative-addon-execution.patch"
)
CONTINUOUS_ARMY_MACRO_PATCH_FILE = (
    KIT_DIR / "patches" / "0018-continuous-army-macro.patch"
)
CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH_FILE = (
    KIT_DIR / "patches" / "0019-continuous-army-economy-scaling.patch"
)
STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH_FILE = (
    KIT_DIR / "patches" / "0020-standing-composition-reinforcement-waves.patch"
)
OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH_FILE = (
    KIT_DIR / "patches" / "0021-offensive-sweep-self-base-exclusion.patch"
)
BOUNDED_PLACEMENT_QUERY_CACHE_PATCH_FILE = (
    KIT_DIR / "patches" / "0022-bounded-placement-query-cache.patch"
)
PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0023-production-facility-stability-and-tank-recovery.patch"
)
BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH_FILE = (
    KIT_DIR / "patches" / "0024-balanced-composition-wave-production.patch"
)
EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH_FILE = (
    KIT_DIR / "patches" / "0025-exact-composition-production-unblock.patch"
)
CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH_FILE = (
    KIT_DIR / "patches" / "0026-continuous-combat-production-relaunch.patch"
)
RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH_FILE = (
    KIT_DIR / "patches" / "0027-resource-throughput-and-expansion-backoff.patch"
)
STARTUP_TELEMETRY_INITIALIZATION_PATCH_FILE = (
    KIT_DIR / "patches" / "0028-startup-telemetry-initialization.patch"
)
GAS_WORKER_COMPLETION_CAP_PATCH_FILE = (
    KIT_DIR / "patches" / "0029-gas-worker-completion-and-cap.patch"
)
STABLE_OFFENSIVE_SWEEP_TARGET_PATCH_FILE = (
    KIT_DIR / "patches" / "0030-stable-offensive-sweep-target.patch"
)
ADAPTIVE_SUPPORT_COMPOSITION_PATCH_FILE = (
    KIT_DIR / "patches" / "0031-adaptive-support-composition.patch"
)
OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH_FILE = (
    KIT_DIR / "patches" / "0032-operation-scoped-adaptive-combat-closure.patch"
)
REVIEW_CLOSURE_OPERATION_IDENTITY_FULL_COMPOSITION_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0033-review-closure-operation-identity-and-full-composition.patch"
)
SEMANTIC_OPERATION_PRODUCTION_CLOSURE_PATCH_FILE = (
    KIT_DIR / "patches" / "0034-semantic-operation-production-closure.patch"
)
ADAPTIVE_PRESSURE_STABLE_OPERATION_KEY_PATCH_FILE = (
    KIT_DIR / "patches" / "0035-adaptive-pressure-stable-operation-key.patch"
)
TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH_FILE = (
    KIT_DIR / "patches" / "0036-tactical-nuke-command-hierarchy.patch"
)
LOCATION_INTENT_TARGET_LOCK_PATCH_FILE = (
    KIT_DIR / "patches" / "0037-location-intent-target-lock.patch"
)
EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH_FILE = (
    KIT_DIR / "patches" / "0038-explicit-terran-ability-execution.patch"
)
EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH_FILE = (
    KIT_DIR / "patches" / "0039-explicit-scout-command-epoch.patch"
)
STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH_FILE = (
    KIT_DIR / "patches" / "0040-standing-production-continuity-closure.patch"
)
EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0041-explicit-ability-caster-production-priority.patch"
)
EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0042-explicit-ability-observation-confirmation.patch"
)
EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0043-explicit-ability-production-isolation.patch"
)
EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0044-explicit-ability-attempt-lifecycle.patch"
)
EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0045-explicit-ability-review-closure.patch"
)
AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0046-authoritative-addon-runtime-clearance.patch"
)
BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0047-banshee-unit-specific-cloak-command.patch"
)
ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0048-allied-cloak-observation-confirmation.patch"
)
EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0049-explicit-ability-caster-ownership.patch"
)
EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0050-explicit-ability-staging-single-flight.patch"
)
ALL_TERRAN_COMBAT_SCOUTS_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0051-all-terran-combat-scouts.patch"
)
PARALLEL_OPERATIONS_INGAME_HUD_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0052-parallel-operations-ingame-hud.patch"
)
PARALLEL_OPERATION_LIFECYCLE_REVIEW_CLOSURE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0053-parallel-operation-lifecycle-review-closure.patch"
)
AUTHORITATIVE_PARALLEL_OPERATION_LIFECYCLE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0054-authoritative-parallel-operation-lifecycle.patch"
)
OPERATION_PRODUCTION_OWNERSHIP_RESTORE_PROOF_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0055-operation-production-ownership-and-restore-proof.patch"
)
EMBEDDED_BUILD_INPUT_IDENTITY_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0056-embedded-build-input-identity.patch"
)
TECH_GAS_BEFORE_SECOND_BARRACKS_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0057-tech-gas-before-second-barracks.patch"
)
OPERATION_PRODUCTION_REVIEW_CLOSURE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0058-operation-production-review-closure.patch"
)
PRODUCTION_FIFO_ZERO_OWNER_CLEANUP_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0059-production-fifo-and-zero-owner-cleanup.patch"
)
OPERATION_EDIT_OWNERSHIP_HANDOFF_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0060-operation-edit-ownership-handoff.patch"
)
OPERATION_EDIT_REVIEW_CLOSURE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0061-operation-edit-review-closure.patch"
)
ALL_TERRAN_HARASS_CAPABILITY_EVIDENCE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0068-all-terran-harass-capability-evidence.patch"
)
AUTHORITATIVE_BATTLEFIELD_OWNERSHIP_READINESS_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0069-authoritative-battlefield-ownership-readiness.patch"
)
BATTLEFIELD_PROJECTION_REVIEW_CLOSURE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0070-battlefield-projection-review-closure.patch"
)
BATTLEFIELD_IDENTITY_TRANSFER_INTEGRITY_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0071-battlefield-identity-transfer-integrity.patch"
)
ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0072-atomic-telemetry-publication.patch"
)
CONTEXTUAL_TRANSFER_CHOICE_PROJECTION_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0073-contextual-transfer-choice-projection.patch"
)
AUTONOMOUS_OWNER_COMPOSITION_EVIDENCE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0074-autonomous-owner-composition-evidence.patch"
)
BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE = (
    KIT_DIR
    / "patches"
    / "0075-battlefield-review-closure.patch"
)
S2CLIENT_PATCH_FILE = KIT_DIR / "patches" / "0001-s2client-macos-launchservices.patch"
BUILD_SCRIPT = KIT_DIR / "scripts" / "build_macos_local.sh"
PROBE_SCRIPT = KIT_DIR / "scripts" / "probe_macos_local.sh"
SMOKE_SCRIPT = KIT_DIR / "scripts" / "smoke_macos_local.sh"
SOAK_SCRIPT = KIT_DIR / "scripts" / "soak_macos_local.sh"
SOAK_MATRIX_SCRIPT = KIT_DIR / "scripts" / "soak_matrix_macos_local.sh"
STRATEGY_MATRIX_SCRIPT = KIT_DIR / "scripts" / "strategy_matrix_macos_local.sh"
LOCAL_SOAK_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "micromachine-local-soak.yml"
DEFAULT_MICROMACHINE_DIR = "/private/tmp/voi-micromachine-runtime/MicroMachine"
DEFAULT_MICROMACHINE_BUILD_DIR = f"{DEFAULT_MICROMACHINE_DIR}/build-latest-api"


def _read_patch_text(path: Path) -> str:
    return path.read_text(encoding="latin-1")


class MicroMachineIntegrationKitTest(unittest.TestCase):
    def test_parallel_operations_close_operation_scoped_production_prerequisites(
        self,
    ) -> None:
        patch = _read_patch_text(PARALLEL_OPERATIONS_INGAME_HUD_PATCH_FILE)

        for term in (
            "int voiParallelOperationCount(CCBot & bot)",
            "bool voiParallelOperationTaskListContains(",
            "float voiParallelOperationTaskPriority(CCBot & bot)",
            'prefix + ".lifetime.completion_state"',
            '".tactical_task."',
            '"production_targets"',
            '"unit_classes"',
            '".tactical_task.priority"',
            "parallelOperationProduction",
            'taskTargets("TERRAN_FACTORY")',
            'taskTargets("FACTORY_TECHLAB")',
            'taskTargets("TERRAN_SIEGETANK")',
            'taskTargets("TERRAN_STARPORT")',
            'taskTargets("STARPORT_TECHLAB")',
            'taskTargets("TERRAN_FUSIONCORE")',
            'taskTargets("TERRAN_BATTLECRUISER")',
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

    def test_parallel_operations_override_strategy_vetoes_and_force_a_command_epoch(
        self,
    ) -> None:
        patch = _read_patch_text(PARALLEL_OPERATIONS_INGAME_HUD_PATCH_FILE)

        for term in (
            "bool voiExplicitTacticalPrerequisiteRequested(",
            '\"operations.\"',
            '\"production_targets\"',
            '\"unit_classes\"',
            "type == MetaTypeEnum::Barracks",
            "type == MetaTypeEnum::BarracksTechLab",
            "type == MetaTypeEnum::GhostAcademy",
            "type == MetaTypeEnum::Factory",
            "type == MetaTypeEnum::FactoryTechLab",
            "type == MetaTypeEnum::FactoryReactor",
            "type == MetaTypeEnum::Armory",
            "type == MetaTypeEnum::Starport",
            "type == MetaTypeEnum::StarportTechLab",
            "type == MetaTypeEnum::StarportReactor",
            "type == MetaTypeEnum::FusionCore",
            "VOI explicit tech transition bypassed opening strategy veto",
            "const VoiOperationState * commandEpochOperation",
            "const bool forceOperationCommandEpoch",
            "commandEpochOperation->submittedPositions.find(",
            "rangedUnit->tag",
            "== commandEpochOperation->submittedPositions.end()",
            "if (!forceOperationCommandEpoch",
            "recordVoiOperationSubmission(",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

    def test_parallel_operations_use_authoritative_home_and_truthful_remote_targets(
        self,
    ) -> None:
        patch = _read_patch_text(PARALLEL_OPERATIONS_INGAME_HUD_PATCH_FILE)

        for term in (
            "const CCPosition observedStart = bot.GetStartLocation();",
            "return observedStart;",
            "bot.Bases().getStartingBaseLocations()",
            "bot.Bases().getBaseLocations()",
            "const CCPosition playableCenter",
            "return CCPosition();",
            "voiResolveOperationTargetEvidence(",
            '"search_candidate"',
            'routeType == "safe_path"',
            "std::string targetEvidence;",
            '\\"target_evidence\\"',
            "operation.locationIntent",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

    def test_parallel_operation_lifecycle_review_closure_is_fail_closed(
        self,
    ) -> None:
        patch = _read_patch_text(
            PARALLEL_OPERATION_LIFECYCLE_REVIEW_CLOSURE_PATCH_FILE
        )

        for term in (
            "bool voiParallelOperationIsActive(CCBot & bot, int operationIndex)",
            "bool voiParallelOperationTerminalState(",
            'taskType != "scout_with_units"',
            'taskType != "pressure_with_main_army"',
            'taskType != "defend_with_units"',
            'prefix + ".lifetime.completion_state"',
            "voiActiveParallelOperationCount(m_bot) > 0",
            'prefix + ".scope.allow_partial_scope"',
            'prefix + ".lifetime.mode"',
            '".lifetime.completion_conditions"',
            "requested.deadlineFrame",
            'operation.status = "EXPIRED";',
            'requested.status = "SUPERSEDED";',
            '"superseded_by_policy"',
            "submissionObserved",
            '\\"submission_observed\\"',
            '\\"owned_unit_count\\"',
            'm_voiPolicyBlackboard.getString("update_id", "")',
            "updateId.empty()",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertIn(
            '-\t\t\t\t\tprefix + ".scope.allow_partial",',
            patch,
        )

    def test_authoritative_parallel_operation_lifecycle_closes_review_blockers(
        self,
    ) -> None:
        patch = _read_patch_text(
            AUTHORITATIVE_PARALLEL_OPERATION_LIFECYCLE_PATCH_FILE
        )

        for term in (
            "bool CombatCommander::hasVoiOperationState(",
            "bool CombatCommander::isVoiOperationActive(",
            "bot.Commander().Combat().hasVoiOperationState(",
            "bot.Commander().Combat().isVoiOperationActive(",
            'prefix + ".generation"',
            'prefix + ".scope.allow_partial_scope"',
            'prefix + ".tactical_task.allow_partial"',
            'prefix + ".allow_partial"',
            "operation->deadlineFrame",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn(
            "+\t\texisting->deadlineFrame = requested.deadlineFrame;",
            patch,
        )
        self.assertIn(
            "-\t\texisting->deadlineFrame = requested.deadlineFrame;",
            patch,
        )

    def test_operation_edit_patch_preserves_atomic_ownership_handoff(
        self,
    ) -> None:
        patch = _read_patch_text(
            OPERATION_EDIT_OWNERSHIP_HANDOFF_PATCH_FILE
        )

        required_terms = (
            'prefix + ".operation_edit"',
            'editPrefix + ".action"',
            'requested.editCounterpartOperationId =',
            '+ ".counterpart_operation_id"',
            'editPrefix + ".unit_selection"',
            'editPrefix + ".before_composition"',
            'editPrefix + ".after_composition"',
            '"operation_edit_confirmation_required"',
            '"invalid_operation_edit_confirmation_policy"',
            '"operation_edit_requires_generation_increment"',
            '"operation_generation_handoff|"',
            '"transfer_counterpart_contract_mismatch"',
            '"transfer_capacity_or_source_minimum"',
            '"source_minimum_preservation"',
            '"source_exact_composition_protected"',
            '"source_composition_preservation"',
            '"destination_priority_not_higher"',
            '"transfer_preflight_plan_missing"',
            '"explicit_ability_owner_protected"',
            "m_voiExplicitAbilityStagingActorTag",
            "m_lastVoiExplicitAbilityActorTag",
            "voiTacticalNukeTaskRequested(m_bot)",
            "sourceSquad.removeUnit(unit);",
            "unitActions.erase(action);",
            "m_voiOperationUnitOwners[unit.getTag()] =",
            "destination.operationId,",
            "destination.generation);",
            '"operation_edit_transfer_out|counterpart="',
            '"operation_edit_transfer_in|counterpart="',
            "stale_operation_generation_preserved",
            "higher_operation_generation_pending_handoff",
            "transferBlockerBeforeHandoff",
            "transferBlockers.find(",
            "transferSelections",
            "voiOperationUnitMatchesRequirement(",
            "destination.priority\n+\t\t\t\t\t< sourceExisting->priority",
            "editRequestedGeneration",
            "editRejectedUpdateId",
            "editRejectedFrame",
            "editSupersessionMode",
            "isCurrentVoiOperationAction(",
            '\\"edit_before_composition\\":',
            '\\"edit_after_composition\\":',
            '\\"transferred_in_count\\":',
            '\\"transferred_out_count\\":',
            '\\"edit_resolution\\":\\"',
            '\\"edit_blocker\\":\\"',
            '\\"edit_requested_generation\\":',
            '\\"edit_rejected_update_id\\":\\"',
            '\\"edit_rejected_frame\\":',
            '\\"edit_supersession_mode\\":\\"',
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        handoff_start = patch.index("auto handoffGeneration =")
        handoff_end = patch.index(
            "std::set<std::string> consumedRequestedIds;",
            handoff_start,
        )
        self.assertNotIn(
            "SmartStop",
            patch[handoff_start:handoff_end],
        )
        self.assertNotIn("sourceCompositionPreserved", patch)
        self.assertLess(
            patch.index('"source_composition_preservation"'),
            handoff_start,
        )
        self.assertLess(
            patch.index('"destination_priority_not_higher"'),
            handoff_start,
        )
        self.assertLess(
            patch.index('"explicit_ability_owner_protected"'),
            handoff_start,
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.cpp "
                "b/src/CombatCommander.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.h "
                "b/src/CombatCommander.h"
            ),
        )

        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(OPERATION_EDIT_OWNERSHIP_HANDOFF_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_operation_edit_review_closure_enforces_roles_and_atomic_rejection(
        self,
    ) -> None:
        patch = _read_patch_text(OPERATION_EDIT_REVIEW_CLOSURE_PATCH_FILE)
        combined_patch = (
            _read_patch_text(OPERATION_EDIT_OWNERSHIP_HANDOFF_PATCH_FILE)
            + patch
        )

        required_terms = (
            "assignedRoleByTag",
            "operation.assignedRoleByTag[unit.getTag()] =",
            "requirement.role",
            "operationRole.role == role",
            "sourceExisting",
            "destination.blockedReason",
            "sourceRequested->blockedReason",
            'destination.editResolution == "blocked"',
            'source->editResolution == "blocked"',
            "replacement.assignedRoleByTag =",
            "requiredRole",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        for term in (
            "transferBlockers",
            "requested.editCounterpartOperationId",
            "transferBlockers[requested.operationId]",
        ):
            with self.subTest(combined_term=term):
                self.assertIn(term, combined_patch)
        self.assertIn("+\t\t\t\t\t\t*ownerOperation,", patch)
        self.assertIn("+\t\t\t\t\t\trequirement.unitType,", patch)
        self.assertIn("+\t\t\t\t\t\trequirement.role)", patch)
        self.assertIn("+\t\t\t\t\t\t\t*sourceExisting,", patch)
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(OPERATION_EDIT_REVIEW_CLOSURE_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_operation_transfer_atomic_admission_has_executable_cpp_contract(
        self,
    ) -> None:
        patch = _read_patch_text(
            KIT_DIR
            / "patches"
            / "0062-operation-transfer-atomic-admission.patch"
        )

        required_terms = (
            "voiDecideOperationTransferAdmission",
            '"transfer_pair_requires_generation_increment"',
            '"transfer_pair_not_active"',
            "voiPreserveAssignedRolesForUnits",
            "voiSelectRoleAbilityPolicy",
            "evaluatedTransferOperations",
            "transferBlockers.find(",
            '"VOI rejected new transfer operation id="',
            "include(CTest)",
            "add_test(",
            "voi_operation_transfer_admission_test",
            "if (MSVC)",
            "partialGeneration",
            "newCounterpartDecision",
            "atomicPair",
            '"scout"',
            '"frontline"',
            '"avoid_combat"',
            '"stim_focus_fire"',
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        blocker_index = patch.index(
            "const auto transferBlocker ="
        )
        generation_index = patch.index(
            "if (requested->generation < existing.generation)",
            blocker_index,
        )
        self.assertLess(blocker_index, generation_index)
        append_index = patch.index(
            "m_voiOperations.push_back(requested);"
        )
        self.assertLess(
            patch.index(
                '"VOI rejected new transfer operation id="'
            ),
            append_index,
        )

        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(
                    KIT_DIR
                    / "patches"
                    / "0062-operation-transfer-atomic-admission.patch"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_operation_transfer_runtime_preservation_closes_review_findings(
        self,
    ) -> None:
        patch_path = (
            KIT_DIR
            / "patches"
            / "0063-operation-transfer-runtime-preservation.patch"
        )
        patch = _read_patch_text(patch_path)

        required_terms = (
            "voiPreserveOperationForRejectedTransfer",
            "annotatePreservedTransferEndpoint",
            "voiShouldReconcileExistingOperation",
            "voiExistingTransferEndpointActive",
            '"transfer_endpoint_terminal"',
            "voiTransferOwnershipAtomically",
            '"transfer_ownership_changed_after_preflight"',
            "ownersBeforeRejectedTransfer",
            "selectedUnits.size()",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        missing_request_branch = patch.index("if (requested == nullptr)")
        self.assertLess(
            patch.index(
                "voiPreserveOperationForRejectedTransfer",
                missing_request_branch,
            ),
            patch.index("releaseVoiOperation(", missing_request_branch),
        )
        parse_result = subprocess.run(
            ["git", "apply", "--numstat", str(patch_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_operation_transfer_transactional_closure_uses_runtime_snapshot(
        self,
    ) -> None:
        patch_path = (
            KIT_DIR
            / "patches"
            / "0064-operation-transfer-transactional-closure.patch"
        )
        patch = _read_patch_text(patch_path)

        required_terms = (
            "voiPlanOperationTransferTransaction",
            "operationAssignments",
            "squadAssignments",
            "actionAssignments",
            '"transfer_owner_mismatch"',
            '"transfer_operation_squad_mismatch"',
            '"transfer_action_owner_mismatch"',
            "prepareGenerationReplacement",
            "transactionPlan.replacementOwners",
            "requested.completed",
            "requestedTerminalDecision",
            "fullForcePlan.sourceTags.empty()",
            "|terminal=transferred_all_units",
            "voiOperationTransferCompositionDeltaMatches",
            "voiOperationTransferRolePoliciesMatch",
            '"source_transfer_delta_mismatch"',
            '"destination_transfer_delta_mismatch"',
            '"transfer_role_policy_mismatch"',
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        deferred_handoff = patch.index(
            "Transfer generations commit together"
        )
        transaction_plan = patch.index(
            "voiPlanOperationTransferTransaction(",
            deferred_handoff,
        )
        first_squad_mutation = patch.index(
            "removeUnit(unit);",
            transaction_plan,
        )
        self.assertNotIn(
            '+\t\t\texisting.status = "WAITING_FOR_UNITS";',
            patch[:deferred_handoff],
        )
        self.assertNotIn("source->editAfterComposition.empty()", patch)
        terminalization = patch.index(
            "if (transactionPlan.sourceTags.empty())",
            transaction_plan,
        )
        self.assertLess(
            terminalization,
            patch.index(
                "|terminal=transferred_all_units",
                terminalization,
            ),
        )
        self.assertLess(transaction_plan, first_squad_mutation)

        parse_result = subprocess.run(
            ["git", "apply", "--numstat", str(patch_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_operation_transfer_final_review_closure_is_runtime_visible(
        self,
    ) -> None:
        patch_path = (
            KIT_DIR
            / "patches"
            / "0065-operation-transfer-final-review-closure.patch"
        )
        patch = _read_patch_text(patch_path)

        required_terms = (
            "voiOperationReferencedByPendingTransfer",
            '"pending_transfer_counterpart_admission"',
            "source == sourceRoles.end()",
            "replacementSourceRoles",
            "replacementDestinationRoles",
            "actionCleanupTags",
            '"transfer_selected_role_missing"',
            "voiOperationTransferHudSuffix",
            '" edit=BLOCKED edit_gen="',
            '" edit_reason="',
            "oneSidedTransferRequest",
            "transactionPlan.replacementDestinationRoles.at(11)",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        lifecycle = patch.index(
            "pending_transfer_counterpart_admission"
        )
        self.assertLess(
            patch.rfind(
                "voiOperationReferencedByPendingTransfer",
                0,
                lifecycle,
            ),
            lifecycle,
        )
        transaction = patch.index(
            "voiPlanOperationTransferTransaction("
        )
        self.assertLess(
            transaction,
            patch.index(
                "transactionPlan.replacementSourceRoles",
                transaction,
            ),
        )
        self.assertLess(
            patch.index("voiOperationTransferHudSuffix"),
            patch.index("oneSidedTransferRequest"),
        )

        parse_result = subprocess.run(
            ["git", "apply", "--numstat", str(patch_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_operation_transfer_idempotence_is_runtime_visible(self) -> None:
        patch_path = (
            KIT_DIR
            / "patches"
            / "0066-operation-transfer-idempotence-and-active-evidence.patch"
        )
        patch = _read_patch_text(patch_path)

        required_terms = (
            "voiOperationTransferAlreadyApplied",
            "voiApplyOperationTransferMutationOnce",
            "voiApplyRejectedOperationEditMetadata",
            "existingCounterpartOperationId",
            "existingEditResolution",
            "alreadyAppliedTransferOperations",
            "sourceMutationEndpoint",
            "destinationMutationEndpoint",
            "transferMutationApplied",
            'existingEditResolution = "applied"',
            "alreadyAppliedDestination.requestedGeneration = 9",
            "ReplayRuntimeState",
            "mutationInvocationCount",
            "replayState.owners",
            "replayState.sourceSquad",
            "replayState.destinationSquad",
            "replayState.actions",
            "transferredOutCount",
            "transferredInCount",
            "secondReplayMutation",
            "RejectedEditRuntimeState",
            '"AttackUnitOrder"',
            "rejectedEditState.lastActionFrame == 140",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertLess(
            patch.index("voiOperationTransferAlreadyApplied("),
            patch.index("voiDecideOperationTransferAdmission("),
        )
        first_runtime_annotation = patch.index(
            "+\t\t\tvoiApplyRejectedOperationEditMetadata("
        )
        preserved_endpoint_marker = patch.index(
            "auto annotatePreservedTransferEndpoint"
        )
        second_runtime_annotation = patch.index(
            "+\t\t\tvoiApplyRejectedOperationEditMetadata(",
            first_runtime_annotation + 1,
        )
        self.assertLess(
            first_runtime_annotation,
            preserved_endpoint_marker,
        )
        self.assertGreater(
            second_runtime_annotation,
            preserved_endpoint_marker,
        )
        self.assertLess(
            first_runtime_annotation,
            patch.index(
                "existing.policyUpdateId = policyUpdateId",
                first_runtime_annotation,
            ),
        )
        self.assertLess(
            second_runtime_annotation,
            patch.index(
                "existing.policyUpdateId = policyUpdateId",
                second_runtime_annotation,
            ),
        )
        rejected_state_test = patch.index("RejectedEditRuntimeState")
        rejected_action_overwrite = patch.index(
            "rejectedEditState.lastAction =",
            rejected_state_test,
        )
        self.assertIn(
            '"operation_edit_blocked"',
            patch[rejected_action_overwrite:],
        )
        self.assertLess(
            rejected_action_overwrite,
            patch.index(
                'rejectedEditState.lastAction == "AttackUnitOrder"',
                rejected_state_test,
            ),
        )
        self.assertLess(
            patch.index("alreadyAppliedTransferOperations.find("),
            patch.index("transferBlockers.find("),
        )
        runtime_mutation = patch.index(
            "const bool transferMutationApplied"
        )
        self.assertLess(
            runtime_mutation,
            patch.index(
                "VoiOperationState sourceReplacement",
                runtime_mutation,
            ),
        )
        self.assertLess(
            patch.index(
                "VoiOperationState sourceReplacement",
                runtime_mutation,
            ),
            patch.index(
                "if (!transferMutationApplied)",
                runtime_mutation,
            ),
        )
        replay_test = patch.index("ReplayRuntimeState")
        second_replay = patch.index(
            "const bool secondReplayMutation",
            replay_test,
        )
        self.assertLess(
            patch.index(
                "const bool firstReplayMutation",
                replay_test,
            ),
            second_replay,
        )
        for invariant in (
            "replayState.owners == replayOwnersAfterFirstMutation",
            "replayState.sourceSquad == replaySourceSquadAfterFirstMutation",
            "replayState.destinationSquad",
            "replayState.actions == replayActionsAfterFirstMutation",
            "replayState.transferredOutCount == 1",
            "replayState.transferredInCount == 1",
            "replayState.mutationInvocationCount == 1",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(invariant, patch[second_replay:])
        parse_result = subprocess.run(
            ["git", "apply", "--numstat", str(patch_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_patch_bundle_is_contiguous_present_and_matches_build_apply_order(
        self,
    ) -> None:
        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        bundle = manifest["patch_bundle"]
        manifest_paths = [patch["path"] for patch in bundle]

        self.assertEqual(
            [patch["order"] for patch in bundle],
            list(range(1, 76)),
        )
        self.assertEqual(len(set(manifest_paths)), 75)
        self.assertEqual(
            manifest_paths[-1],
            "patches/0075-battlefield-review-closure.patch",
        )
        self.assertTrue(
            all((KIT_DIR / path).is_file() for path in manifest_paths)
        )

        build_script = (
            KIT_DIR / "scripts" / "build_macos_local.sh"
        ).read_text()
        variable_paths = dict(
            re.findall(
                (
                    r'^([A-Z0-9_]*PATCH_FILE)="'
                    r'\$\{REPO_ROOT\}/integrations/micromachine/'
                    r'(patches/[^"]+)"$'
                ),
                build_script,
                flags=re.MULTILINE,
            )
        )
        extracted_build_apply_paths: list[str] = []
        for line in build_script.splitlines():
            if (
                not line.startswith(
                    'git -C "${MICROMACHINE_DIR}" apply '
                )
                or "--check" in line
            ):
                continue
            match = re.search(
                r'"\$\{([A-Z0-9_]*PATCH_FILE)\}"$',
                line,
            )
            self.assertIsNotNone(match, line)
            assert match is not None
            variable = match.group(1)
            self.assertIn(variable, variable_paths)
            extracted_build_apply_paths.append(variable_paths[variable])

        self.assertEqual(manifest_paths, extracted_build_apply_paths)

    def test_all_terran_harass_patch_has_cpp_execution_and_evidence_contract(
        self,
    ) -> None:
        patch = _read_patch_text(
            ALL_TERRAN_HARASS_CAPABILITY_EVIDENCE_PATCH_FILE
        )

        for source_path in (
            "--- a/src/CombatCommander.cpp",
            "--- a/src/GameCommander.cpp",
            "--- a/src/ProductionManager.cpp",
            "--- /dev/null",
            "+++ b/src/voi_family_effect_lifecycle.hpp",
            "+++ b/tests/family_effect_lifecycle_test.cpp",
        ):
            with self.subTest(source_path=source_path):
                self.assertIn(source_path, patch)
        for contract in (
            "harass_with_units",
            "voiCanonicalOperationTaskType",
            'if (taskType == "harass_with_units") return "harass"',
            "SquadOrderTypes::Harass",
            "HarassOrderRadius",
            'taskType == "harass_with_units"',
            "voiTerranOperationFamily",
            "voiMergeAttemptGenerationWatermarks",
            "replacement.familyAttemptGenerationByKey",
            "existing.familyAttemptGenerationByKey",
            "requested.familyAttemptGenerationByKey",
            "key.unitType = delivery.unitType",
            "key.requiredEffect = delivery.snapshot.requiredEffect",
            "left.unitType == right.unitType",
            "left.snapshot.requiredEffect",
            "voiCanonicalTerranFamilyForUnitType(",
            "voiSquadOrderName",
            "attemptedUnitTags",
            "voiRemoveUnitFromOtherFamilyEvidence",
            "voiFindSubmittedOperationFamilyEvidence",
            "voiFamilyEffectTelemetryLifecycleIsValid",
            'return unitType + "|" + role + "|" + action;',
            "matchingEvidence",
            '\\"squad_order\\"',
            '\\"family_evidence\\"',
            '\\"update_id\\"',
            '\\"operation_id\\"',
            '\\"generation\\"',
            '\\"action\\"',
            '\\"required_effect\\"',
            '\\"attempt_generation\\"',
            '\\"attempted_count\\"',
            '\\"attempted_unit_tags\\"',
            '\\"attempted_frame\\"',
            '\\"submitted_count\\"',
            '\\"submitted_unit_tags\\"',
            '\\"submitted_frame\\"',
            '\\"effect_kind\\"',
            '\\"effect_count\\"',
            '\\"effect_unit_tags\\"',
            '\\"effect_frame\\"',
            '\\"coalesced_observation_count\\"',
            '\\"latest_effect_frame\\"',
            '\\"maximum_effect_count\\"',
            '\\"blocker_manager\\"',
            '\\"blocker\\"',
            '<< " order="',
            '<< " action="',
            '<< " effect="',
            'card << " force="',
            "VoiFamilyEffectTelemetryLatch",
            "VoiFamilyEffectTelemetryDeliveryQueue",
            "VoiFamilyEffectTelemetryAttemptKey",
            "acknowledgedAttemptWatermarks",
            "acknowledgedPolicyUpdateId",
            "voiEnqueueFamilyEffectTelemetryDelivery",
            "voiLatchFamilyEffectForTelemetry",
            "voiSelectFamilyEffectForTelemetry",
            "voiFamilyEffectHasMatchingSubmission",
            "voiFamilyEffectHasMatchingObservation",
            "voiFamilyAbilityEffectHasMatchingObservation",
            "voiFamilyAbilityEffectHasMatchingUnitObservation",
            "voiCanonicalTerranFamilyForUnitType",
            "voiNextMonotonicAttemptGeneration",
            "voiAcknowledgeFamilyEffectTelemetry",
            "voiAcknowledgeFamilyEffectTelemetryDeliveries",
            "voiWriteAuthoritativeTelemetryFile",
            "acknowledgeVoiFamilyEffects",
            '\\"pending_family_effects\\"',
            "familyEvidence.policyUpdateId",
            "familyEvidence.submittedPositions[unit->tag]",
            "delivery.family = evidence.family",
            "left.family == right.family",
            "familyAttemptGenerationByKey",
            "operation.requirementSnapshots",
            "serializedSnapshot.policyUpdateId",
            "authoritativeTelemetryWritten",
            "telemetry.close()",
            "observe A -> Plan B -> submit B -> telemetry still emits A",
            "A persistent state cannot relatch",
            "local latch keeps the oldest unacknowledged",
            "newer policy cannot inherit or acknowledge",
            "Submission identity must precede effect observation",
            "Same-family unit B cannot claim unit A",
            "Same-family unit B already in the ability state cannot prove unit A",
            "Persistent state observations coalesce",
            "raw morph type stays distinct",
            "Bounded diagnostic history cannot reopen",
            "submitted\n+\t\t\t\t\t!= evidence->submittedPositions.end()",
            "immutable delivery queue preserves effects",
            "Retarget can discard family state",
            "Failed writes retain both local and immutable",
            "successful authoritative write acknowledges serialized",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)

        family_aliases = {
            "marine": ("TERRAN_MARINE",),
            "marauder": ("TERRAN_MARAUDER",),
            "reaper": ("TERRAN_REAPER",),
            "ghost": ("TERRAN_GHOST",),
            "hellion_hellbat": (
                "TERRAN_HELLION",
                "TERRAN_HELLIONTANK",
            ),
            "widow_mine": (
                "TERRAN_WIDOWMINE",
                "TERRAN_WIDOWMINEBURROWED",
            ),
            "cyclone": ("TERRAN_CYCLONE",),
            "siege_tank": (
                "TERRAN_SIEGETANK",
                "TERRAN_SIEGETANKSIEGED",
            ),
            "thor": ("TERRAN_THOR", "TERRAN_THORAP"),
            "medivac": ("TERRAN_MEDIVAC",),
            "raven": ("TERRAN_RAVEN",),
            "viking": (
                "TERRAN_VIKINGFIGHTER",
                "TERRAN_VIKINGASSAULT",
            ),
            "banshee": ("TERRAN_BANSHEE",),
            "liberator": (
                "TERRAN_LIBERATOR",
                "TERRAN_LIBERATORAG",
            ),
            "battlecruiser": ("TERRAN_BATTLECRUISER",),
        }
        for family, unit_types in family_aliases.items():
            with self.subTest(family=family):
                self.assertIn(f'return "{family}"', patch)
                for unit_type in unit_types:
                    self.assertIn(f'unitType == "{unit_type}"', patch)

        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        self.assertEqual(
            {
                "path": (
                    "patches/"
                    "0068-all-terran-harass-capability-evidence.patch"
                ),
                "order": 68,
            },
            {
                "path": manifest["patch_bundle"][67]["path"],
                "order": manifest["patch_bundle"][67]["order"],
            },
        )
        build_script = BUILD_SCRIPT.read_text()
        patch_variable = (
            "ALL_TERRAN_HARASS_CAPABILITY_EVIDENCE_PATCH_FILE"
        )
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0068-all-terran-harass-capability-evidence.patch"',
            build_script,
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${'
            f'{patch_variable}'
            '}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            f'"${{{patch_variable}}}"'
        )
        self.assertLess(patch_check, patch_apply)
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(ALL_TERRAN_HARASS_CAPABILITY_EVIDENCE_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_battlefield_projection_patch_has_authoritative_runtime_contract(
        self,
    ) -> None:
        patch = _read_patch_text(
            AUTHORITATIVE_BATTLEFIELD_OWNERSHIP_READINESS_PATCH_FILE
        )

        for source_path in (
            "--- a/src/CombatCommander.cpp",
            "--- a/src/CombatCommander.h",
            "--- a/src/GameCommander.cpp",
            "+++ b/src/voi_battlefield_projection.hpp",
            "+++ b/tests/battlefield_projection_test.cpp",
            "--- a/tests/CMakeLists.txt",
        ):
            with self.subTest(source_path=source_path):
                self.assertIn(source_path, patch)
        for contract in (
            "voiMakeBattlefieldProjection",
            "voiProjectBattlefieldTransfer",
            "battlefield_overview",
            "eligibleCombatCount",
            "duplicateOwnershipCount",
            '\\"operation_ownership\\"',
            '\\"autonomous_ownership\\"',
            "unassignedTags",
            "protectedMinimum",
            "atomicRevalidationReady",
            "atomic_revalidation_inputs",
            "selected_unit_tags",
            "source_owner_id",
            "counterpart_action",
            "targetReached",
            "completionReason",
            "completionFrame",
            'status == "FAILED"',
            'return "failed"',
            "voi_battlefield_projection_ndebug_test",
            "NDEBUG",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)

        build_script = BUILD_SCRIPT.read_text()
        patch_variable = (
            "AUTHORITATIVE_BATTLEFIELD_OWNERSHIP_READINESS_PATCH_FILE"
        )
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0069-authoritative-battlefield-ownership-readiness.patch"',
            build_script,
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${'
            f'{patch_variable}'
            '}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            f'"${{{patch_variable}}}"'
        )
        self.assertLess(patch_check, patch_apply)
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(AUTHORITATIVE_BATTLEFIELD_OWNERSHIP_READINESS_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_battlefield_projection_review_closure_patch_is_required(
        self,
    ) -> None:
        patch = _read_patch_text(
            BATTLEFIELD_PROJECTION_REVIEW_CLOSURE_PATCH_FILE
        )
        for contract in (
            "#include <chrono>",
            "voiCurrentBattlefieldSessionEpoch",
            "std::chrono::nanoseconds",
            '<< ",\\"generation\\":"',
            "nlohmann::json::parse",
            "1700000000000ULL",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)

        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        self.assertEqual(
            {
                "path": (
                    "patches/"
                    "0070-battlefield-projection-review-closure.patch"
                ),
                "order": 70,
            },
            {
                "path": manifest["patch_bundle"][69]["path"],
                "order": manifest["patch_bundle"][69]["order"],
            },
        )
        build_script = BUILD_SCRIPT.read_text()
        patch_variable = "BATTLEFIELD_PROJECTION_REVIEW_CLOSURE_PATCH_FILE"
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0070-battlefield-projection-review-closure.patch"',
            build_script,
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${'
            f'{patch_variable}'
            '}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            f'"${{{patch_variable}}}"'
        )
        self.assertLess(patch_check, patch_apply)
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(BATTLEFIELD_PROJECTION_REVIEW_CLOSURE_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_battlefield_identity_transfer_integrity_patch_is_required(
        self,
    ) -> None:
        patch = _read_patch_text(
            BATTLEFIELD_IDENTITY_TRANSFER_INTEGRITY_PATCH_FILE
        )
        for contract in (
            "VOI_MICROMACHINE_RUNTIME_INSTANCE_ID",
            "runtime_instance_id",
            "ownership_integrity_fail_closed",
            "voiValidateBattlefieldTransferMutation",
            "thirdOperationConflict",
            "thirdAutonomousConflict",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)

        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        self.assertEqual(
            {
                "path": (
                    "patches/"
                    "0071-battlefield-identity-transfer-integrity.patch"
                ),
                "order": 71,
            },
            {
                "path": manifest["patch_bundle"][70]["path"],
                "order": manifest["patch_bundle"][70]["order"],
            },
        )
        build_script = BUILD_SCRIPT.read_text()
        patch_variable = "BATTLEFIELD_IDENTITY_TRANSFER_INTEGRITY_PATCH_FILE"
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0071-battlefield-identity-transfer-integrity.patch"',
            build_script,
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${'
            f'{patch_variable}'
            '}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            f'"${{{patch_variable}}}"'
        )
        self.assertLess(patch_check, patch_apply)
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(BATTLEFIELD_IDENTITY_TRANSFER_INTEGRITY_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_atomic_telemetry_publication_patch_is_required(self) -> None:
        patch = _read_patch_text(ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE)
        for contract in (
            "voiWriteTelemetrySnapshotAtomically",
            "voiWriteTelemetrySnapshotAtomicallyWithOperations",
            "O_EXCL | O_NOFOLLOW | O_CLOEXEC",
            "O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC",
            "::openat",
            "::renameat",
            "::unlinkat",
            "voiWriteAuthoritativeTelemetryFile",
            "acknowledge();",
            "voi_atomic_telemetry_test",
            "std::string(1024 * 512",
            "NDEBUG",
            "#define REQUIRE(expression)",
            "readerReady",
            "startReader",
            "observations.load() > 0",
            "InterruptingShortWriteOperations",
            "WriteFailureOperations",
            "CloseFailureOperations",
            "RenameFailureOperations",
            "::symlink",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)
        self.assertNotIn("#include <cassert>", patch)
        self.assertNotIn("assert(", patch)

        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        self.assertEqual(
            {
                "path": "patches/0072-atomic-telemetry-publication.patch",
                "order": 72,
            },
            {
                "path": manifest["patch_bundle"][71]["path"],
                "order": manifest["patch_bundle"][71]["order"],
            },
        )
        build_script = BUILD_SCRIPT.read_text()
        patch_variable = "ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE"
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0072-atomic-telemetry-publication.patch"',
            build_script,
        )
        prior_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            '"${BATTLEFIELD_IDENTITY_TRANSFER_INTEGRITY_PATCH_FILE}"'
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            '"${ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE}"'
        )
        self.assertLess(prior_apply, patch_check)
        self.assertLess(patch_check, patch_apply)
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-atomic-telemetry-publication-patch "
                '"${ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE}"'
            ),
        )
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_contextual_transfer_choice_projection_patch_is_required(self) -> None:
        patch = _read_patch_text(
            CONTEXTUAL_TRANSFER_CHOICE_PROJECTION_PATCH_FILE
        )
        for contract in (
            "for (const auto & destination : m_voiOperations)",
            "counterpartOperationId",
            "requestedCounterpartGeneration",
            "destinationMaximum",
            "VoiBattlefieldTransferEndpoint",
            "transferByEndpoint",
            "recommendedResolutionChoices",
            "transfer_available_units",
            "transfer_two_units",
            "multiDestinationProjection",
            "reserve-charlie",
            "no_transferable_units",
            "kVoiBattlefieldMaximumJsonSafeInteger",
            "(1ULL << 53) - 1ULL",
            "value &= kVoiBattlefieldMaximumJsonSafeInteger",
            "<= kVoiBattlefieldMaximumJsonSafeInteger",
            "if (processEpoch > previous",
            "wrappedMaximumSafeEpoch",
            "rejectedUnsafeProcessEpoch",
            "kVoiBattlefieldMaximumJsonSafeInteger + 1",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)

        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        self.assertEqual(
            {
                "path": (
                    "patches/"
                    "0073-contextual-transfer-choice-projection.patch"
                ),
                "order": 73,
            },
            {
                "path": manifest["patch_bundle"][72]["path"],
                "order": manifest["patch_bundle"][72]["order"],
            },
        )
        build_script = BUILD_SCRIPT.read_text()
        patch_variable = "CONTEXTUAL_TRANSFER_CHOICE_PROJECTION_PATCH_FILE"
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0073-contextual-transfer-choice-projection.patch"',
            build_script,
        )
        prior_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            '"${ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE}"'
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${'
            f'{patch_variable}'
            '}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            f'"${{{patch_variable}}}"'
        )
        self.assertLess(prior_apply, patch_check)
        self.assertLess(patch_check, patch_apply)
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-contextual-transfer-choice-projection-patch "
                '"${CONTEXTUAL_TRANSFER_CHOICE_PROJECTION_PATCH_FILE}"'
            ),
        )
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(CONTEXTUAL_TRANSFER_CHOICE_PROJECTION_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_autonomous_owner_composition_evidence_patch_is_required(
        self,
    ) -> None:
        patch = _read_patch_text(
            AUTONOMOUS_OWNER_COMPOSITION_EVIDENCE_PATCH_FILE
        )
        for contract in (
            "projected.family = voiTerranOperationFamily",
            "autonomousRoleByOwner",
            "base_defender",
            "autonomous_squad_member",
            "VoiBattlefieldAutonomousOwnerProjection::Composition",
            "compositionByFamilyRole",
            "groundCapableCount",
            "airCapableCount",
            '\\"composition\\"',
            "missingAutonomousRoleProjection",
            "mixedAutonomousProjection",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)

        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        self.assertEqual(
            {
                "path": (
                    "patches/"
                    "0074-autonomous-owner-composition-evidence.patch"
                ),
                "order": 74,
            },
            {
                "path": manifest["patch_bundle"][73]["path"],
                "order": manifest["patch_bundle"][73]["order"],
            },
        )
        build_script = BUILD_SCRIPT.read_text()
        patch_variable = "AUTONOMOUS_OWNER_COMPOSITION_EVIDENCE_PATCH_FILE"
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0074-autonomous-owner-composition-evidence.patch"',
            build_script,
        )
        prior_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            '"${CONTEXTUAL_TRANSFER_CHOICE_PROJECTION_PATCH_FILE}"'
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${'
            f'{patch_variable}'
            '}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            f'"${{{patch_variable}}}"'
        )
        self.assertLess(prior_apply, patch_check)
        self.assertLess(patch_check, patch_apply)
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-autonomous-owner-composition-evidence-patch "
                '"${AUTONOMOUS_OWNER_COMPOSITION_EVIDENCE_PATCH_FILE}"'
            ),
        )
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(AUTONOMOUS_OWNER_COMPOSITION_EVIDENCE_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_battlefield_review_closure_patch_is_required(self) -> None:
        patch = _read_patch_text(BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE)
        for contract in (
            "VoiBattlefieldDefenseOperationInput",
            "voiBattlefieldCollectBaseDefenders",
            "targetMatchesBase",
            "squadEvidencePresent",
            "getBaseContainingPosition",
            "projectedBase.readinessEvidencePresent",
            '\\"session_epoch\\":\\"',
            "18446744073709551615",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, patch)

        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())
        self.assertEqual(
            {
                "path": "patches/0075-battlefield-review-closure.patch",
                "order": 75,
            },
            {
                "path": manifest["patch_bundle"][74]["path"],
                "order": manifest["patch_bundle"][74]["order"],
            },
        )
        build_script = BUILD_SCRIPT.read_text()
        patch_variable = "BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE"
        self.assertIn(
            f'{patch_variable}="${{REPO_ROOT}}/integrations/'
            "micromachine/patches/"
            '0075-battlefield-review-closure.patch"',
            build_script,
        )
        prior_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            '"${AUTONOMOUS_OWNER_COMPOSITION_EVIDENCE_PATCH_FILE}"'
        )
        patch_check = build_script.index(
            "apply --recount --check --ignore-space-change "
            '--whitespace=nowarn "${BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE}"'
        )
        patch_apply = build_script.index(
            "apply --recount --ignore-space-change --whitespace=nowarn "
            '"${BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE}"'
        )
        self.assertLess(prior_apply, patch_check)
        self.assertLess(patch_check, patch_apply)
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-battlefield-review-closure-patch "
                '"${BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE}"'
            ),
        )
        parse_result = subprocess.run(
            [
                "git",
                "apply",
                "--numstat",
                str(BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, parse_result.returncode, parse_result.stderr)

    def test_operation_production_ownership_restore_proof_closes_runtime_gap(
        self,
    ) -> None:
        patch = _read_patch_text(
            OPERATION_PRODUCTION_OWNERSHIP_RESTORE_PROOF_PATCH_FILE
        )

        for term in (
            "VoiBuildOrderClaim",
            "VoiBuildOrderOwnerMetadata",
            "voiOperationOwners",
            "voiOperationOwnerMetadata",
            "preserveWithoutVoiOperationOwners",
            "voiClaimSource",
            "voiPreservedClaimSource",
            "voiTaskPriority",
            "insertionSequence",
            "normalizedOwnerMetadata(",
            "refreshVoiAggregateMetadata(",
            "void BuildOrderQueue::addVoiOperationOwnersToType(",
            "void BuildOrderQueue::reconcileTypeVoiClaims(",
            "void BuildOrderQueue::promoteTypeToHighestPriority(",
            "void BuildOrderQueue::resizeTypePreservingVoiClaims(",
            "int BuildOrderQueue::removeVoiOperationOwner(",
            "for (auto item = m_queue.rbegin();",
            "sameIdentity || sameClaim",
            "claims.end() - targetCount",
            "void ProductionManager::reconcileVoiParallelOperationProductionQueue()",
            "bool ProductionManager::reconcileVoiProductionClaimsForType(",
            "voiParallelOperationOwnersForType(",
            "voiParallelOperationOwnerMetadataForType(",
            "voiParallelOperationOwnerIsActive(",
            "voiActiveParallelOperationCount(m_bot) > 0;",
            "voiTopLevelRequestedCompositionCount(",
            "voiParallelOperationRequestedCompositionCount(",
            "voiTopLevelPolicyRequiresType(",
            "requestedByOwner",
            "macroOutstanding",
            "getVoiOperationOwnedUnitCount(",
            "getVoiAssignableUnownedUnitCount(",
            "representedForOwner",
            "assignableCapacity",
            "requested.owner.first",
            "requestedByOwner.begin()",
            "return left.taskPriority",
            "> right.taskPriority;",
            "requestedOperations.begin()",
            "left.priority > right.priority",
            '"shared_prerequisite"',
            'existing.source == "legacy"',
            "Shrink-only reconciliation must never relabel an existing owner",
            '"represented_satisfied"',
            "m_voiTrackedOperationProductionOwners",
            "if (removedItems > 0)",
            "item.voiOperationOwners",
            "item.voiOperationOwnerMetadata",
            "item.preserveWithoutVoiOperationOwners",
            'prefix + ".issued_at_frame"',
            '"missing_operation_issued_at_frame"',
            "synchronizeVoiOperationLifecycleForProduction()",
            "removed_from_policy_before_production",
            "metadataToInherit.blocking",
            "dependentPriority + 1",
            "promoted->voiPreservedBlocking || blocking",
            "promotedOwner->second.blocking || blocking",
            "independentLegacyDemand",
            'item.voiClaimSource == "legacy"',
            'item.voiPreservedClaimSource == "legacy"',
            "#include <tuple>",
            "std::tie(",
            "getVoiOperationOwnedItemsTelemetryJson",
            "operation_owned_queue_items",
            '"WAITING_FOR_UNITS"',
            "mayPreemptOwnedUnit",
            "ownerOperation->minUnits",
            "previousOperation->assignedUnitTags.erase(",
            '\\"operation_owned_queue_item_count\\"',
            '\\"operation_production_owner_release_count\\"',
            '\\"operation_production_queue_purge_count\\"',
            '\\"last_operation_production_queue_purge_frame\\"',
            '\\"last_operation_production_released_owner\\"',
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertIn(
            "+\treconcileVoiParallelOperationProductionQueue();",
            patch,
        )
        self.assertIn(
            "+\tif (!reconcileVoiProductionClaimsForType(",
            patch,
        )
        self.assertIn(
            '+\t\t\t== "missing_operation_issued_at_frame"',
            patch,
        )
        self.assertIn(
            "+\t\t\titem,",
            patch,
        )
        comparator_start = patch.index(
            "bool MM::BuildOrderItem::operator <"
        )
        comparator_end = patch.index(
            "bool MM::BuildOrderItem::operator ==",
            comparator_start,
        )
        comparator = patch[comparator_start:comparator_end]
        self.assertNotIn("operationOwnedSameType", comparator)
        self.assertLess(
            comparator.index("+\t\t\tvoiTaskPriority"),
            comparator.index("+\t\t\tpriority"),
        )
        self.assertLess(
            comparator.index("+\t\t\tpriority"),
            comparator.index("+\t\t\tinsertionSequence"),
        )
        self.assertIn(
            "+\t\t|| operation->status == \"BLOCKED\"",
            patch,
        )
        self.assertIn(
            "+\t\t|| operation->durationSeconds == 0)",
            patch,
        )
        self.assertIn(
            '+\t\t\t== "missing_operation_issued_at_frame"',
            patch,
        )
        self.assertNotIn(
            "+\t\texisting->issuedAtFrame =",
            patch,
        )
        self.assertNotIn(
            "+\t\texisting->deadlineFrame =",
            patch,
        )
        self.assertIn(
            "+\treturn false;\n }\n \n int voiActiveParallelOperationCount",
            patch,
        )

        self.assertIn(
            "-\tif (bot.Commander().Combat().hasVoiOperationState(",
            patch,
        )
        self.assertIn(
            "+\tif (bot.Commander().Combat().hasVoiOperationState(",
            patch,
        )
        self.assertIn(
            "+\t\t\t\t|| metadataToInherit.blocking;",
            patch,
        )
        self.assertIn(
            "+\t\t\t\t\t|| dependent.voiPreservedBlocking;",
            patch,
        )
        self.assertIn(
            "+\t\tstd::sort(m_queue.begin(), m_queue.end());",
            patch,
        )
        self.assertIn(
            "+\tstd::sort(m_queue.begin(), m_queue.end());",
            patch,
        )
        self.assertIn(
            "+\treturn macroRequested * std::max(1, waveMultiplier)",
            patch,
        )
        self.assertIn(
            "+\t\t\t\t\t- operationOwnedCount);",
            patch,
        )
        self.assertIn(
            "+\t\t\t\t\t< ownerOperation->minUnits)",
            patch,
        )
        self.assertIn(
            "+\t\t\t\t\t\t< requirement.count)",
            patch,
        )
        self.assertIn(
            "+\t\t\t\t\trequestedCount,",
            patch,
        )
        self.assertIn(
            "+\t\t\t\t\t!allowPartial,",
            patch,
        )
        self.assertNotIn(
            "+\t\titem.blocking = item.blocking || blocking;",
            patch,
        )
        self.assertNotIn(
            "+\t\t\t\t\trequestedCount * waveMultiplier,",
            patch,
        )
        self.assertNotIn(
            "+\t\t\t\t\tblocking && !allowPartial,",
            patch,
        )
        self.assertNotIn(
            "representedRemaining -= representedForOwner;",
            patch,
        )
        self.assertNotIn(
            "+\t\t&& voiExactCompositionActive(m_bot);",
            patch,
        )

    def test_operation_production_review_closure_allocates_growth_once(
        self,
    ) -> None:
        patch = _read_patch_text(
            OPERATION_PRODUCTION_REVIEW_CLOSURE_PATCH_FILE
        )

        for term in (
            "attributedRepresentedGrowth",
            "productionTarget",
            "m_voiOperationProductionLastRepresentedCount",
            "representedGrowthAvailable",
            "representedLoss",
            "lastOwnedCount",
            "ownerSpecificLoss",
            "initialReservedAssignable",
            "m_voiOperationProductionUnattributedLossDebt",
            "growthConsumedByLossDebt",
            "productionStates.rbegin()",
            "representedGrowthAvailable -=",
            "releasedProductionTypeKeys",
            "hasRemainingOwner",
            '\\"last_operation_production_queue_purge_event\\"',
            '\\"operation_production_queue_purge_events\\"',
            "VoiOperationProductionQueuePurgeEvent",
            "maxPurgeEventHistory = 32",
            "getLastVoiOperationProductionQueuePurgeOperationId()",
            "getLastVoiOperationProductionQueuePurgeGeneration()",
            "getLastVoiOperationProductionQueuePurgeRemovedCount()",
            "getVoiOperationProductionQueuePurgeEventsTelemetryJson()",
            "ownedProductionGrowth",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        targets = [2, 2]
        credits = [0, 0]

        def attribute_growth(growth: int) -> int:
            for index, target in enumerate(targets):
                attributable = min(growth, max(0, target - credits[index]))
                credits[index] += attributable
                growth -= attributable
            return growth

        self.assertEqual(0, attribute_growth(1))
        self.assertEqual([1, 0], credits)
        self.assertEqual(0, attribute_growth(1))
        self.assertEqual([2, 0], credits)
        self.assertEqual(0, attribute_growth(1))
        self.assertEqual([2, 1], credits)
        self.assertEqual(3, sum(credits))

        last_owned = [2, 1]

        def reconcile_loss(
            *,
            represented_loss: int,
            current_owned: list[int],
        ) -> int:
            nonlocal last_owned
            for index in range(len(credits)):
                owner_specific_loss = max(
                    0,
                    last_owned[index] - current_owned[index],
                )
                retracted = min(owner_specific_loss, credits[index])
                credits[index] -= retracted
                represented_loss = max(
                    0,
                    represented_loss - owner_specific_loss,
                )
            last_owned = current_owned
            for index in range(len(credits) - 1, -1, -1):
                retracted = min(represented_loss, credits[index])
                credits[index] -= retracted
                represented_loss -= retracted
            return represented_loss

        self.assertEqual(
            0,
            reconcile_loss(
                represented_loss=1,
                current_owned=[1, 1],
            ),
        )
        self.assertEqual([1, 1], credits)
        self.assertEqual(0, attribute_growth(1))
        self.assertEqual([2, 1], credits)

        del credits[0]
        del targets[0]
        del last_owned[0]
        self.assertEqual([1], credits)
        self.assertEqual([2], targets)

        represented_by_type: dict[str, int] = {}
        owner_types: dict[str, set[str]] = {}
        owner_credits: dict[str, int] = {}

        def observe(owner: str, unit_type: str, represented: int) -> int:
            owner_types.setdefault(owner, set()).add(unit_type)
            previous = represented_by_type.get(unit_type)
            represented_by_type[unit_type] = represented
            growth = 0 if previous is None else max(0, represented - previous)
            owner_credits[owner] = owner_credits.get(owner, 0) + growth
            return growth

        def release(owner: str) -> None:
            released_types = owner_types.pop(owner)
            remaining_types = (
                set().union(*owner_types.values()) if owner_types else set()
            )
            for unit_type in released_types - remaining_types:
                represented_by_type.pop(unit_type, None)

        self.assertEqual(0, observe("owner-a", "Marine", 2))
        self.assertEqual(1, observe("owner-a", "Marine", 3))
        release("owner-a")
        self.assertNotIn("Marine", represented_by_type)
        self.assertEqual(0, observe("owner-b", "Marine", 4))
        self.assertEqual(0, owner_credits["owner-b"])
        self.assertEqual(1, observe("owner-b", "Marine", 5))

        loss_debt = 0
        previous_represented = 10
        zero_credit = 0

        def reconcile_unowned_count(represented: int) -> None:
            nonlocal loss_debt, previous_represented, zero_credit
            growth = max(0, represented - previous_represented)
            loss = max(0, previous_represented - represented)
            previous_represented = represented
            loss_debt += loss
            consumed = min(growth, loss_debt)
            loss_debt -= consumed
            zero_credit += growth - consumed

        reconcile_unowned_count(9)
        self.assertEqual(1, loss_debt)
        reconcile_unowned_count(10)
        self.assertEqual(0, loss_debt)
        self.assertEqual(0, zero_credit)
        reconcile_unowned_count(11)
        self.assertEqual(1, zero_credit)

        initial_reserved_assignable = 1
        initial_owned_count = 0
        guaranteed_target = 2

        def queue_claims(
            *,
            attributed_growth: int,
            deployable_remaining: int,
            reserved_assignable: int,
            owned_for_owner: int,
        ) -> int:
            newly_reserved_assignable = max(
                0,
                reserved_assignable - initial_reserved_assignable,
            )
            owned_production_growth = max(
                0,
                owned_for_owner
                - initial_owned_count
                - initial_reserved_assignable,
            )
            growth_used_for_deployable = min(
                attributed_growth,
                owned_production_growth + newly_reserved_assignable,
            )
            guaranteed_completed = max(
                0,
                attributed_growth - growth_used_for_deployable,
            )
            return (
                deployable_remaining - reserved_assignable
            ) + max(0, guaranteed_target - guaranteed_completed)

        self.assertEqual(
            3,
            queue_claims(
                attributed_growth=0,
                deployable_remaining=2,
                reserved_assignable=1,
                owned_for_owner=0,
            ),
        )
        self.assertEqual(
            3,
            queue_claims(
                attributed_growth=0,
                deployable_remaining=1,
                reserved_assignable=0,
                owned_for_owner=1,
            ),
        )
        self.assertEqual(
            2,
            queue_claims(
                attributed_growth=1,
                deployable_remaining=0,
                reserved_assignable=0,
                owned_for_owner=2,
            ),
        )
        self.assertEqual(
            1,
            queue_claims(
                attributed_growth=2,
                deployable_remaining=0,
                reserved_assignable=0,
                owned_for_owner=2,
            ),
        )
        self.assertEqual(
            0,
            queue_claims(
                attributed_growth=3,
                deployable_remaining=0,
                reserved_assignable=0,
                owned_for_owner=2,
            ),
        )

    def test_tech_gas_patch_prioritizes_refinery_before_second_barracks(
        self,
    ) -> None:
        patch = _read_patch_text(TECH_GAS_BEFORE_SECOND_BARRACKS_PATCH_FILE)

        for term in (
            "m_voiRequiresTechGasBeforeSecondBarracks",
            "effectiveMarauderBias > 0.25f",
            "effectiveTankBias > 0.25f",
            "effectiveLiberatorBias > 0.25f",
            "|| refineryCount > 0",
            "firstBarracksRepresented",
            "&& !barracksTaskActive",
            "currentItem.type == MetaTypeEnum::Barracks",
            "|| refineryTaskActive",
            (
                '"VOI held a follow-up Barracks until the tech-gas '
                'Refinery was observed."'
            ),
            "techGasQueuedOrBuilding",
            "m_queue.contains(MetaTypeEnum::Refinery)",
            "|| barracksTaskActive",
            "m_queue.promoteTypeToHighestPriority(",
            '"queued_existing_promoted"',
            '"building_existing"',
            "if (!techGasQueuedOrBuilding",
            "needsTechBootstrapRefinery",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)
        self.assertLess(
            patch.index("m_voiRequiresTechGasBeforeSecondBarracks"),
            patch.index("needsTechBootstrapRefinery"),
        )
        self.assertLess(
            patch.index("m_queue.promoteTypeToHighestPriority("),
            patch.index("if (!techGasQueuedOrBuilding"),
        )

    def test_tech_gas_queue_hold_does_not_block_pure_marine_policy(
        self,
    ) -> None:
        patch = _read_patch_text(TECH_GAS_BEFORE_SECOND_BARRACKS_PATCH_FILE)
        queue_hold_start = patch.index(
            "+\tif (currentItem.type == MetaTypeEnum::Barracks"
        )
        queue_hold_end = patch.index(
            "\n \tconst int starportCount",
            queue_hold_start,
        )
        queue_hold = patch[queue_hold_start:queue_hold_end]
        requirement_start = patch.index(
            "+\tm_voiRequiresTechGasBeforeSecondBarracks ="
        )
        requirement_end = patch.index(
            "+\tconst bool techGasQueuedOrBuilding",
            requirement_start,
        )
        requirement = patch[requirement_start:requirement_end]

        self.assertIn(
            "&& m_voiRequiresTechGasBeforeSecondBarracks",
            queue_hold,
        )
        self.assertIn("effectiveMarauderBias > 0.25f", requirement)
        self.assertIn("effectiveTankBias > 0.25f", requirement)
        self.assertNotIn("effectiveMarineBias", requirement)

    def test_build_order_queue_priority_key_is_strict_weak_order(self) -> None:
        ownership_patch = _read_patch_text(
            OPERATION_PRODUCTION_OWNERSHIP_RESTORE_PROOF_PATCH_FILE
        )
        closure_patch = _read_patch_text(
            PRODUCTION_FIFO_ZERO_OWNER_CLEANUP_PATCH_FILE
        )
        comparator_start = ownership_patch.index(
            "bool MM::BuildOrderItem::operator <"
        )
        comparator_end = ownership_patch.index(
            "bool MM::BuildOrderItem::operator ==",
            comparator_start,
        )
        comparator = ownership_patch[comparator_start:comparator_end]
        self.assertIn(
            "std::tie(\n+\t\t\tvoiTaskPriority,\n"
            "+\t\t\tpriority,\n+\t\t\tinsertionSequence)",
            comparator,
        )
        self.assertIn(
            "-\t\t\tinsertionSequence)\n"
            "+\t\t\tx.insertionSequence)",
            closure_patch,
        )
        self.assertIn(
            "-\t\t\tx.insertionSequence);\n"
            "+\t\t\tinsertionSequence);",
            closure_patch,
        )
        records = [
            ("Marine-operation", 100.0, 10, 4),
            ("Tank-operation", 0.0, 20, 2),
            ("Marine-legacy", 0.0, 15, 1),
            ("Tank-legacy", 0.0, 15, 3),
        ]

        def less(left: tuple, right: tuple) -> bool:
            left_key = (left[1], left[2], -left[3])
            right_key = (right[1], right[2], -right[3])
            return left_key < right_key

        for record in records:
            self.assertFalse(less(record, record))
        for left, right in itertools.permutations(records, 2):
            self.assertFalse(less(left, right) and less(right, left))
        for left, middle, right in itertools.permutations(records, 3):
            if less(left, middle) and less(middle, right):
                self.assertTrue(less(left, right))

        expected = [
            "Tank-legacy",
            "Marine-legacy",
            "Tank-operation",
            "Marine-operation",
        ]
        for permutation in itertools.permutations(records):
            ordered = sorted(
                permutation,
                key=lambda item: (item[1], item[2], -item[3]),
            )
            self.assertEqual(expected, [item[0] for item in ordered])

        equal_priority = [
            ("Marine", 0.0, 10, 1),
            ("Medivac", 0.0, 10, 2),
        ]
        queue = sorted(
            equal_priority,
            key=lambda item: (item[1], item[2], -item[3]),
        )
        execution_order = [queue.pop()[0], queue.pop()[0]]
        self.assertEqual(["Marine", "Medivac"], execution_order)

    def test_operation_release_reports_cleanup_without_owned_units(
        self,
    ) -> None:
        patch = _read_patch_text(
            PRODUCTION_FIFO_ZERO_OWNER_CLEANUP_PATCH_FILE
        )
        release_start = patch.index(
            "void CombatCommander::releaseVoiOperation"
        )
        release = patch[release_start:]

        self.assertIn(
            "+\tconst bool cleanupAlreadyRecorded =",
            release,
        )
        self.assertIn(
            '+\t\t&& (operation.lastAction.rfind(\n'
            '+\t\t\t\t"release_stop|",',
            release,
        )
        self.assertIn(
            '+\t\t\t|| operation.lastAction.rfind(\n'
            '+\t\t\t\t"release_no_owned_units|",',
            release,
        )
        self.assertNotIn("+\t\treturn;", release)
        self.assertLess(
            release.index("+\tconst bool cleanupAlreadyRecorded ="),
            release.index(" \tstd::vector<Unit> unitsToRestore;"),
        )
        self.assertIn(
            "+\tbool issuedReleaseStop = false;",
            release,
        )
        self.assertIn(
            '? "release_stop|"',
            release,
        )
        self.assertIn(
            ': "release_no_owned_units|"',
            release,
        )
        self.assertIn(
            "+\tif (!cleanupAlreadyRecorded)\n"
            "+\t{\n"
            "+\t\toperation.lastAction =",
            release,
        )
        self.assertLess(
            release.index("+\tif (!cleanupAlreadyRecorded)"),
            release.index(" \tfor (const auto & unit : unitsToRestore)"),
        )
        self.assertIn(
            ' \t\t\texisting->status = "RECEIVED";\n'
            " \t\t\texisting->blockedReason.clear();\n"
            "+\t\t\texisting->lastAction.clear();\n"
            "+\t\t\texisting->lastActionFrame = 0;",
            release,
        )

        def release_once(
            last_action: str,
            last_action_frame: int,
            *,
            has_live_owned_units: bool,
            frame: int,
            recovered: bool = False,
        ) -> tuple[str, int, int, bool, int]:
            if recovered:
                last_action = ""
                last_action_frame = 0
            cleanup_recorded = last_action_frame > 0 and last_action.startswith(
                ("release_stop|", "release_no_owned_units|")
            )
            if not cleanup_recorded:
                prefix = (
                    "release_stop|"
                    if has_live_owned_units
                    else "release_no_owned_units|"
                )
                last_action = f"{prefix}cancelled_by_policy"
                last_action_frame = frame
            return last_action, last_action_frame, 0, False, 0

        first_release = release_once(
            "",
            0,
            has_live_owned_units=True,
            frame=410,
        )
        repeated_release = release_once(
            first_release[0],
            first_release[1],
            has_live_owned_units=False,
            frame=411,
        )
        self.assertEqual(
            (
                "release_stop|cancelled_by_policy",
                410,
                0,
                False,
                0,
            ),
            first_release,
        )
        self.assertEqual(first_release, repeated_release)

        recovered_then_cancelled = release_once(
            "release_no_owned_units|invalid_target",
            300,
            has_live_owned_units=True,
            frame=420,
            recovered=True,
        )
        self.assertEqual(
            (
                "release_stop|cancelled_by_policy",
                420,
                0,
                False,
                0,
            ),
            recovered_then_cancelled,
        )

    def test_operation_production_requires_authoritative_runtime_state(
        self,
    ) -> None:
        patch = _read_patch_text(
            OPERATION_PRODUCTION_OWNERSHIP_RESTORE_PROOF_PATCH_FILE
        )
        production_start = patch.index(
            "bool voiParallelOperationIsActive"
        )
        production_end = patch.index(
            "int voiActiveParallelOperationCount",
            production_start,
        )
        production_active = patch[production_start:production_end]
        self.assertIn(
            "+\tif (bot.Commander().Combat().hasVoiOperationState(",
            production_active,
        )
        self.assertIn(
            "+\treturn false;",
            production_active,
        )
        self.assertNotIn(
            "+\tconst std::string lifetimeMode",
            production_active,
        )
        self.assertNotIn(
            "+\tconst uint64_t deadlineFrame",
            production_active,
        )

        combat_active_start = patch.index(
            "bool CombatCommander::isVoiOperationActive"
        )
        combat_active_end = patch.index(
            "int CombatCommander::getVoiOperationOwnedUnitCount",
            combat_active_start,
        )
        combat_active = patch[combat_active_start:combat_active_end]
        self.assertIn(
            '+\t\t|| operation->status == "BLOCKED"',
            combat_active,
        )
        self.assertIn(
            "+\t\t|| operation->durationSeconds == 0)",
            combat_active,
        )
        self.assertIn(
            "+\treturn operation->deadlineFrame > 0",
            combat_active,
        )

        self.assertIn(
            '+\t\toperation.status = "WAITING_FOR_UNITS";',
            patch,
        )
        self.assertIn(
            '+\t\t|| operation.status == "WAITING_FOR_UNITS")',
            patch,
        )
        self.assertIn(
            "+\t\tif (missingIssuedAtFrame\n"
            "+\t\t\t|| futureIssuedAtFrame\n"
            "+\t\t\t|| expiredAtAdmission)",
            patch,
        )
        self.assertIn(
            "+\t\t\tcontinue;",
            patch,
        )
        self.assertNotIn(
            'existing->blockedReason\n'
            '+\t\t\t\t== "missing_operation_issued_at_frame"',
            patch,
        )

    def test_live_tactical_patch_locks_runtime_operation_invariants(self) -> None:
        patch = _read_patch_text(TACTICAL_PATCH_FILE)

        required_terms = (
            "bool BuildingManager::handleVoiAddonTask(Building & b)",
            "m_addonRelocations.find(unit.getTag())",
            "m_addonRelocations.find(b.builderUnit.getTag())",
            "order.ability_id == sc2::ABILITY_ID::LAND_BARRACKS",
            'const bool scopeTargetsScout = scopeArmyGroup == "scout";',
            "if (totalAssigned < totalRequired)",
            "mainAttackSquad.setPriority(exactCompositionPressureTask ? BaseDefensePriority : AttackPriority);",
            'm_lastVoiOperationPhase = exactCompositionPressureTask ? "Producing" : "Idle";',
            "const bool voiOperationRallying",
            "const bool voiBoundedForceAdvance",
            "const bool siegeWindowOpen",
            "const float flankSign = voiRouteIntent == \"flank_left\" ? 1.0f : -1.0f;",
            "const CCPosition lateral(-forward.y * flankSign, forward.x * flankSign);",
            'm_squad->getName() == "MainAttack"',
            'vikingRole == "support" || vikingRole == "escort"',
            '\\"operation_phase\\":\\"',
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertIn(
            "currentFrame - state.lastCommandFrame < commandCooldown",
            patch,
        )
        self.assertIn("state.landAttempts >= 4", patch)
        self.assertIn("scoutSquad.getUnits().size() > static_cast<size_t>(desiredScoutUnits)", patch)

    def test_production_fix_patch_closes_live_qa_blockers(self) -> None:
        patch = _read_patch_text(PRODUCTION_FIX_PATCH_FILE)

        required_terms = (
            "const size_t ExplicitOperationPriority = 6;",
            "mainAttackSquad.setPriority(exactCompositionPressureTask ? ExplicitOperationPriority : AttackPriority);",
            "Requested composition atomically assigned to MainAttack",
            "exactCompositionReady",
            "partialOperationUnits",
            "isSafeMainAttackSource",
            "isSafeScoutSource",
            'currentName == "MainAttack"',
            "currentSquad->getPriority() < ExplicitOperationPriority",
            "CCPosition voiForwardRallyPosition",
            "m_voiScoutPreviousSquadByTag",
            "Exact combat scout composition is not available yet",
            'addScoutType("TERRAN_VIKINGFIGHTER"',
            "const bool scoutViking",
            "const bool preserveScoutOrder",
            "Explicit MainAttack operation owns available combat units",
            'currentName.find("Base Defense ") == 0',
            "ScoutVikingFighterMode",
            "voiOffensiveTankSiegeBlocked",
            "const bool explicitOperationTank",
            "VoiRoleTankSiegeApproved",
            "RangedManager approved siege after operation and home-safety checks",
            "m_voiFocusTarget",
            'voiPolicyRoleForUnit(m_bot, rangedUnit) == "focus_fire"',
            'getVoiPolicyFloat("combat.kite_bias", 0.0f)',
            "VoiKiteMove",
            "voiLandAbilityForProducer",
            "voiIsLandOrderForTarget",
            "voiIsBuildingMobilityOrder",
            "tryIssueVoiBuildingMobilityCommand",
            "releaseVoiBuildingMobilityOwnershipIfSettled",
            "m_voiMobilityOwner",
            "m_voiMobilityLastCommandFrame",
            "isVoiBuildingMobilityOwned",
            "isVoiBuildingMobilityOwned(barracks.getTag())",
            'm_bot.Query()->Placement(ability, target, unit.getUnitPtr())',
            '"addon_relocation"',
            '"legacy_addon"',
            '"proxy_cyclones"',
            '"damaged_building"',
            "currentFrame - state.lastCommandFrame < commandCooldown",
            "const uint32_t groundedRetryDelay = 22u * 30u;",
            "state.liftAttempts >= 2",
            "m_liftedBuildingPositions.find",
            "Refusing legacy addon LAND",
            "Marine continuity ability query failed; deferring direct train command.",
            "bool voiIsMorphAbility(sc2::AbilityID ability)",
            "currentAction.executed && currentAction.finished && action.prioritized",
            "const bool preserveScoutObjective = action.squad == \"Scout\";",
            "&& !preserveScoutObjective",
            "bool voiExplicitMainAttackUnitRequested",
            'voiExplicitMainAttackUnitRequested(m_bot, "TERRAN_SIEGETANK")',
            "-\t\t\t\t\tMicro::SmartHold(worker.getUnitPtr(), true, m_bot);",
            "mineral_return_depot_direct_no_queue",
            "mineral_distance_optimization_direct_no_queue",
            "-\t\t\t\t\t\t\t\t\tworker.shiftRightClick(depot);",
            "-\t\t\t\t\t\t\t\tworker.shiftRightClick(mineralTarget);",
            "-\tconst float techSwitchUrgency = m_bot.Commander().getVoiPolicyFloat(\"production.tech_switch_urgency\", 0.0f);",
            "const bool wantsFactory = taskTechTransition || wantsFactoryDoctrine || effectiveFactoryBias > 0.25f || effectiveTankBias > 0.25f || effectiveHellionBias > 0.25f || effectiveCycloneBias > 0.25f;",
            "-\treturn techSwitchUrgency > 0.55f && (hasPendingFactoryTransition || hasPendingStarportTransition);",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)
        self.assertNotIn("+\t\t\t\t\tMicro::SmartHold(worker.getUnitPtr(), true, m_bot);", patch)
        self.assertNotIn("+\t\t\t\t\t\t\t\t\tworker.shiftRightClick(depot);", patch)
        self.assertNotIn("+\t\t\t\t\t\t\t\tworker.shiftRightClick(mineralTarget);", patch)
        self.assertNotIn(
            "+\tconst bool wantsFactory = taskTechTransition || wantsFactoryDoctrine || effectiveFactoryBias > 0.25f || effectiveTankBias > 0.25f || effectiveHellionBias > 0.25f || effectiveCycloneBias > 0.25f || techSwitchUrgency > 0.55f;",
            patch,
        )
        self.assertIn(
            "-\t\t\t\taction.abilityID = sc2::ABILITY_ID::MORPH_SIEGEMODE;",
            patch,
        )
        for source_path in (
            "src/BuildingManager.cpp",
            "src/BuildingManager.h",
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/GameCommander.cpp",
            "src/ProductionManager.cpp",
            "src/RangedManager.cpp",
            "src/RangedManager.h",
            "src/WorkerManager.cpp",
        ):
            with self.subTest(single_diff=source_path):
                self.assertEqual(
                    1,
                patch.count(f"diff --git a/{source_path} b/{source_path}"),
                )

    def test_operation_state_patch_closes_live_state_machine_blockers(self) -> None:
        patch = _read_patch_text(OPERATION_STATE_PATCH_FILE)

        required_terms = (
            "m_voiRallyLatchOperationKey",
            "invalid_zero_position",
            "m_lastVoiSkippedAction = \"morph_unavailable|\" + voiActionEvidence(action);",
            "isVoiProducerCommandOwned",
            "voiIsLandAbility",
            "VOI kept the proxy Barracks grounded",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        for source_path in (
            "src/BuildingManager.cpp",
            "src/BuildingManager.h",
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/ProductionManager.cpp",
            "src/RangedManager.cpp",
            "src/Squad.cpp",
        ):
            with self.subTest(single_diff=source_path):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source_path} b/{source_path}"),
                )

    def test_addon_recovery_patch_closes_live_relocation_deadlock(self) -> None:
        patch = _read_patch_text(ADDON_RECOVERY_PATCH_FILE)

        required_terms = (
            "isVoiProducerFootprintClear",
            "if (!b.builderUnit.isFlying())",
            "m_bot.Query()->Placement(landAbility, producerPosition, b.builderUnit.getUnitPtr())",
            "const size_t queryBudget = 8;",
            "AddonPlacementRetryState",
            "state.nextRetryFrame",
            "state.abortAfterLanding = true;",
            "findVoiProducerLandingSite",
            "VOI addon producer grounded safely; released addon ownership for producer replanning.",
            "m_addonProducerCooldownUntil[producerTag] = currentFrame + 22u * 15u;",
            "m_voiMobilityOwner.erase(producerTag);",
            "b.unassign();",
            "isVoiAddonProducerEligible",
            "if (isTypeAddon && !m_bot.Buildings().isVoiAddonProducerEligible(unit.getTag()))",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn(
            "+\tconst sc2::Unit * queryUnit = b.builderUnit.isFlying() ? b.builderUnit.getUnitPtr() : nullptr;",
            patch,
        )
        for source_path in (
            "src/BuildingManager.cpp",
            "src/BuildingManager.h",
            "src/ProductionManager.cpp",
        ):
            with self.subTest(single_diff=source_path):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source_path} b/{source_path}"),
                )

    def test_grounded_addon_candidate_patch_removes_second_live_blocker(self) -> None:
        patch = _read_patch_text(GROUNDED_ADDON_CANDIDATE_PATCH_FILE)

        for term in (
            "if (!b.builderUnit.isFlying())",
            "getBuildingPlacer().getBuildLocationNear(",
            "false,\n+\t\t\tfalse,\n+\t\t\ttrue,\n+\t\t\ttrue);",
            "BuildingPlacer already validated the producer plus addon footprint.",
            "Dynamic unit occupancy is checked by the LAND query after lift.",
            "return Util::GetPosition(nearbyTile);",
            "return CCPosition();",
            "const size_t queryBudget = 8;",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn(
            "+\t\t\tcandidates.push_back(Util::GetPosition(nearbyTile));",
            patch,
        )
        self.assertEqual(
            1,
            patch.count("diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp"),
        )

    def test_guaranteed_producer_grounding_patch_prevents_airborne_deadlock(self) -> None:
        patch = _read_patch_text(GUARANTEED_PRODUCER_GROUNDING_PATCH_FILE)

        for term in (
            "originalPosition",
            "liftFrame",
            "producerOnlyRecovery",
            "isVoiProducerLandingSiteValid",
            "TERRAN_FACTORYFLYING",
            "const UnitType groundedProducerType",
            "voiLandAbilityCandidatesForProducer",
            "voiAvailableLandAbility",
            "voiResolveLandAbility",
            "return {specializedAbility, sc2::ABILITY_ID::LAND};",
            "resolvedAbility = voiResolveLandAbility",
            'owner == "addon_relocation"',
            "false-negative SC2 placement query",
            "Micro::SmartAbility(unit.getUnitPtr(), resolvedAbility, target, m_bot);",
            "getBuildingPlacer().getBuildLocationNear(",
            "const size_t queryBudget = 16;",
            "locallyValidatedFallbacks",
            "rotating locally valid LAND fallback",
            "maximumAddonFlightFrames",
            "findVoiProducerLandingSite(b, state.originalPosition)",
            "emergency grounding search has no valid LAND target yet",
            "grounding the producer without an addon footprint",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn(
            "+\treturn m_bot.Query()->Placement(landAbility, producerPosition, b.builderUnit.getUnitPtr());",
            patch,
        )
        self.assertNotIn(
            "+\t\treturn m_bot.Query()->Placement(landAbility, candidate, b.builderUnit.getUnitPtr());",
            patch,
        )
        self.assertEqual(
            1,
            patch.count("diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp"),
        )
        self.assertEqual(
            1,
            patch.count("diff --git a/src/BuildingManager.h b/src/BuildingManager.h"),
        )

    def test_emergency_land_query_fallback_removes_live_false_negatives(self) -> None:
        patch = _read_patch_text(EMERGENCY_LAND_QUERY_FALLBACK_PATCH_FILE)

        for term in (
            "producerHalfExtent",
            "m_bot.Map().isBuildable(tileX, tileY)",
            "m_bot.Observation()->HasCreep",
            "std::abs(delta.x) < collisionExtent",
            "fallbackAbilities",
            "(currentFrame / 64u) % fallbackAbilities.size()",
            "locally validated nonqueued LAND",
            "placement or availability queries",
            "locallyValidatedFallbacks.push_back(candidate)",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn(
            "+\t\t\tif (!getBuildingPlacer().buildable(",
            patch,
        )
        self.assertEqual(
            1,
            patch.count("diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp"),
        )

    def test_grounded_production_and_observed_targeting_closes_live_blockers(
        self,
    ) -> None:
        patch = _read_patch_text(GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH_FILE)

        for term in (
            "return {specializedAbility};",
            "producer-specific nonqueued LAND",
            "clearing the reserved addon tiles without lifting the producer",
            "released the addon task for producer replanning without lifting",
            "m_lastVoiDoctrineFrame < 48",
            "queued_supply_and_continuing_plan",
            "queued_worker_and_continuing_plan",
            "voiObservedEnemyCombatTarget",
            "voiHasObservedEnemyLocationEvidence",
            "recentObservationWindowFrames",
            "scouting.require_fresh_enemy_observation",
            "Combat scout is searching base candidates before a fresh-observation attack",
            "Waiting for combat scout enemy-location evidence",
            "launchedExactOperation",
            "Launched operation continuing with survivors",
            "voiSweepingAfterLostContact",
            "Continuing launched operation through base candidates",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn(
            "+\treturn {specializedAbility, sc2::ABILITY_ID::LAND};",
            patch,
        )
        for source_path in (
            "src/BuildingManager.cpp",
            "src/ProductionManager.cpp",
            "src/CombatCommander.cpp",
        ):
            with self.subTest(single_diff=source_path):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source_path} b/{source_path}"),
                )

    def test_exact_composition_production_progress_closes_remaining_live_stalls(
        self,
    ) -> None:
        patch = _read_patch_text(EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH_FILE)

        for term in (
            "voiRequestedCompositionCount",
            "voiRepresentedUnitCount",
            "wantsStarport",
            "combat_scout_bootstrap",
            "marineScoutBootstrapTarget",
            "exactMarineCompositionPending",
            "requestedMarineCount > 0 && !exactMarineCompositionPending",
            "requestedMarineCount > representedMarineCount",
            "voiCountMobileAttackUnits(m_combatUnits) >= 1",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertIn(
            "wantsFactory = taskTechTransition || wantsFactoryDoctrine || wantsStarport",
            patch,
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.cpp b/src/CombatCommander.cpp"
            ),
        )

    def test_production_resource_operation_patch_closes_live_control_gaps(
        self,
    ) -> None:
        patch = _read_patch_text(
            PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH_FILE
        )

        for term in (
            "production.allow_building_relocation",
            "VOI refused nonessential production-building LIFT",
            "economy.gas_priority",
            "economy.gas_worker_target_bias",
            "voiCompletedProducersWithAttachedAddon",
            "voiExactCompositionTypes",
            "standingSustainProduction",
            'lifetimeMode == "until_cancelled"',
            "TERRAN_GHOST",
            "TERRAN_WIDOWMINE",
            "TERRAN_LIBERATOR",
            "recentCombatObservationWindow",
            "VOI tactical operation regroup completed",
            "Regrouping surviving units for tactical relaunch",
            "MORPH_LIBERATORAGMODE",
            "BURROWDOWN_WIDOWMINE",
            "EFFECT_GHOSTSNIPE",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        for source_path in (
            "src/BuildingManager.cpp",
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/ProductionManager.cpp",
            "src/ProductionManager.h",
            "src/RangedManager.cpp",
            "src/WorkerManager.cpp",
        ):
            with self.subTest(single_diff=source_path):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source_path} b/{source_path}"),
                )

    def test_live_operation_unblock_patch_closes_live_stalls(self) -> None:
        patch = _read_patch_text(LIVE_OPERATION_UNBLOCK_PATCH_FILE)

        for term in (
            "countVoiEligibleAddonProducers",
            "hasVoiBlockedAddonProducer",
            "const uint32_t producerCooldown = 22u * 60u * 5u;",
            "GetNextEnemyStartCandidateToScout",
            "currentFrame - m_voiScoutLastProgressFrame >= 22u * 12u",
            "m_voiStalledScoutTargets",
            "enemy-start candidate",
            "m_voiFactoryAddonReplacementQueued",
            "factory_addon_replacement",
            "factoryTechLabTaskActive",
            "eligibleFactoryAddonProducers",
            "m_queue.removeAllOfType(MetaTypeEnum::FactoryTechLab)",
            "target_evidence",
            "observed_enemy",
            "enemy_start_candidate_scouting",
            "explicit_blind_candidate",
            "observed_contact_lost_sweep",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        for source_path in (
            "src/BuildingManager.cpp",
            "src/BuildingManager.h",
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/GameCommander.cpp",
            "src/ProductionManager.cpp",
            "src/ProductionManager.h",
        ):
            with self.subTest(single_diff=source_path):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source_path} b/{source_path}"),
                )

    def test_stable_flank_stage_patch_freezes_operation_waypoint(self) -> None:
        patch = _read_patch_text(STABLE_FLANK_STAGE_LATCH_PATCH_FILE)

        for term in (
            "if (m_voiFlankStagePosition == CCPosition())",
            "CCPosition candidateStage;",
            "candidateStage = home + forward * forwardDistance + lateral * lateralDistance;",
            "candidateStage = orderPosition * (1.0f - flankStageWeight) + m_bot.Map().center() * flankStageWeight;",
            "if (m_voiFlankStagePosition != CCPosition())",
            "static_cast<float>(mainAttackSquad.getUnits().size()) * 0.60f",
            "orderPosition = m_voiFlankStagePosition;",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.cpp b/src/CombatCommander.cpp"
            ),
        )

    def test_production_staging_patch_closes_live_operation_leaks(self) -> None:
        patch = _read_patch_text(PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH_FILE)

        for term in (
            "voiProductionStagingPosition",
            "voiProductionAddonAreaHasStaticBlocker",
            'const bool addonRelocation = owner == "legacy_addon" || owner == "addon_relocation";',
            "VOI queued an addon-clear replacement Factory because every completed producer is quarantined.",
            "autonomousNeedsEnemyEvidence",
            "m_voiOperationObservedEnemy",
            "No recently observed local combat threat is a safe advance",
            "semanticLifetimeActive",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        semantic_lifetime_block = patch[
            patch.index("const bool semanticLifetimeActive") :
            patch.index(
                "if (!m_voiPolicyBlackboard.isProtocolCompatible()",
                patch.index("const bool semanticLifetimeActive"),
            )
        ]
        self.assertNotIn('lifetimeMode == "until_completed"', semantic_lifetime_block)

        for source_path in (
            "src/BuildingManager.cpp",
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/GameCommander.cpp",
            "src/ProductionManager.cpp",
            "src/ProductionManager.h",
        ):
            with self.subTest(source_path=source_path):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source_path} b/{source_path}"),
                )

    def test_addon_query_footprint_patch_rejects_body_only_placements(self) -> None:
        patch = _read_patch_text(ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH_FILE)

        for term in (
            "voiProductionAddonTiles",
            "CCTilePosition(position.x + 2, position.y - 1)",
            "CCTilePosition(position.x + 2, position.y)",
            "CCTilePosition(position.x + 3, position.y - 1)",
            "CCTilePosition(position.x + 3, position.y)",
            "voiStaticUnitOccupiesTile",
            "unit.getBuildingLimits(bottomLeft, topRight)",
            "voiProductionAddonFootprintIsBuildable",
            "!bot.Map().isBuildable(tile.x, tile.y)",
            "bot.Observation()->HasCreep",
            "unit.getType().isBuilding()",
            "unit.getType().isMineral()",
            "unit.getType().isGeyser()",
            "includeAddonTiles && !voiProductionAddonFootprintIsBuildable",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn("unit.getType().tileWidth()", patch)
        self.assertNotIn("unit.getType().tileHeight()", patch)
        self.assertEqual(
            1,
            patch.count("diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp"),
        )

    def test_authoritative_addon_query_patch_uses_batched_sc2_placement(self) -> None:
        patch = _read_patch_text(AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH_FILE)

        for term in (
            "isVoiProductionBuildingWithAddonFootprint",
            "voiProductionAddonProbePosition",
            "producerPosition.x + 3",
            "queryCanPlaceVoiProductionAddonFootprint",
            "TERRAN_SUPPLYDEPOT",
            "std::vector<sc2::QueryInterface::PlacementQuery> addonQueries",
            "addonQueries.emplace_back",
            "addonResults = bot.Query()->Placement(addonQueries)",
            "bodyAcceptedCount",
            "addonAcceptedCount",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("bot.Map().isBuildable", added_lines)
        self.assertNotIn("voiStaticUnitOccupiesTile", added_lines)
        self.assertEqual(
            1,
            patch.count("diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp"),
        )

    def test_authoritative_addon_execution_patch_closes_runtime_split_brain(
        self,
    ) -> None:
        patch = _read_patch_text(AUTHORITATIVE_ADDON_EXECUTION_PATCH_FILE)

        for term in (
            "Util::GetTilePosition(producerPosition)",
            "queryCanPlaceVoiProductionAddonFootprint(m_bot, producerTile)",
            "const uint32_t producerCooldown = 22u * 15u;",
            "&& factoryCount < 2",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("getBuildingPlacer().buildable", added_lines)
        self.assertNotIn("22u * 60u * 5u", added_lines)
        self.assertEqual(
            1,
            patch.count("diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp"),
        )

    def test_continuous_army_macro_patch_spends_resources_after_minimum_composition(
        self,
    ) -> None:
        patch = _read_patch_text(CONTINUOUS_ARMY_MACRO_PATCH_FILE)

        required_terms = (
            "voiContinuousCompositionProductionActive",
            "voiCompositionProductionWaveMultiplier",
            "voiCompositionProductionTargetCount",
            'tacticalTaskType == "pressure_with_main_army"',
            '"production.production_continuity_bias"',
            "bot.GetMaxSupply() < 200 || bot.GetCurrentSupply() < 196",
            '"army_macro_worker_continuity"',
            "macroWorkerTarget",
            '"army_macro_barracks"',
            '"army_macro_factory"',
            '"army_macro_starport"',
            "std::min(3, desiredBarracksCount)",
            "std::min(2, desiredFactoryCount)",
            "std::min(2, desiredStarportCount)",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertEqual(
            1,
            patch.count("diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"),
        )

    def test_continuous_army_economy_scaling_patch_closes_gas_and_base_bottlenecks(
        self,
    ) -> None:
        patch = _read_patch_text(CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH_FILE)

        required_terms = (
            "requestedCompositionGasPerWave",
            "continuousGasComposition",
            "desiredMacroRefineryCount",
            "macroRefineryWorkerFloor",
            "macroGasStarved",
            '"army_macro_refinery"',
            "macroExpansionWorkerFloor",
            "macroExpansionMineralThreshold",
            "getFreeBaseLocationCount() > 0",
            '"army_macro_command_center"',
            "totalTownHallCount < 3",
            "refineryCount >= 2",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("m_bot.GetFreeGas() >= 500", added_lines)
        self.assertNotIn("m_bot.GetFreeGas() >= 600", added_lines)
        self.assertEqual(
            1,
            patch.count("diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"),
        )

    def test_standing_composition_patch_joins_complete_uncapped_reinforcement_waves(
        self,
    ) -> None:
        patch = _read_patch_text(STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH_FILE)

        required_terms = (
            "standingContinuousExactOperation",
            '"production.production_continuity_bias"',
            'voiOperationLifetimeMode == "until_cancelled"',
            'voiOperationLifetimeMode == "standing_order"',
            '"scope.max_units", 0) == 0',
            '"tactical_task.max_units"',
            "Preserve the launched army",
            "Every standing reinforcement is an independent complete wave.",
            "const int requestedForPass = requirement.second;",
            "<= 32.0f * 32.0f",
            "Complete reinforcement composition atomically joined MainAttack",
            "&& !exactCompositionPressureTask",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn("selectedForType", patch)
        self.assertEqual(
            1,
            patch.count("diff --git a/src/CombatCommander.cpp b/src/CombatCommander.cpp"),
        )

    def test_offensive_sweep_patch_excludes_self_and_home_bases(self) -> None:
        patch = _read_patch_text(OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH_FILE)

        required_terms = (
            "baseLocation == selfStartingBase",
            "baseLocation->isOccupiedByPlayer(Players::Self)",
            "getOccupiedBaseLocations(Players::Self)",
            "voiIsRemoteCombatTarget(m_bot, candidatePosition, 32.0f)",
            "< 18.0f * 18.0f",
            "addCandidates(true, false)",
            "addCandidates(false, true)",
            "addCandidates(false, false)",
            "m_currentBaseExplorationIndex %= offensiveCandidates.size()",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn(
            "getBasePosition(Players::Enemy, m_currentBaseExplorationIndex)",
            added_lines,
        )

    def test_bounded_placement_patch_caps_queries_and_removes_duplicate_search(
        self,
    ) -> None:
        patch = _read_patch_text(BOUNDED_PLACEMENT_QUERY_CACHE_PATCH_FILE)

        required_terms = (
            "voiMacroPlacementCache",
            "VOI SC2 Query macro placement reused validated cache",
            "anchors.size() >= 8",
            "supplyRecovery ? 192 : 96",
            "supplyRecovery ? 640 : 320",
            "radius <= 32",
            "usedVoiQueryPlacement",
            "m_bot.Commander().isVoiPolicyActive() ? 24 : 60",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)
        self.assertEqual(
            1,
            patch.count(
                '+\t\t\t\tbuildingLocation = m_buildingPlacer.getBuildLocationNear('
            ),
        )

    def test_production_facility_stability_and_tank_recovery_patch(self) -> None:
        patch = _read_patch_text(
            PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH_FILE
        )

        for term in (
            "groundedProductionFacility",
            "production.allow_building_relocation=true",
            "factoryAddonRecoveryCap",
            "continuousCompositionProduction && taskTargetsSiegeTank ? 3 : 2",
            "reserveGasForTankTech",
            "VOI deferred queued Vikings until Factory Tech Lab recovery completes.",
            "std::max(0.55f, productionContinuityBias)",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

    def test_balanced_composition_wave_patch_prevents_cross_type_inflation(
        self,
    ) -> None:
        patch = _read_patch_text(BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH_FILE)

        for term in (
            "voiContinuousCompositionProductionConfigured",
            "completedWaves",
            "completedForType",
            "std::min(completedWaves, completedForType)",
            "A surplus of one unit type must never inflate another type's target.",
            "m_voiCompositionProductionOperationKey",
            "m_voiCompositionProductionWaveMultiplier",
            "refreshVoiCompositionProductionWaveMultiplier",
            "The latch is monotonic for the operation.",
            "VOI advanced balanced composition production wave multiplier=",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("requestedTotal", added_lines)
        self.assertNotIn("representedTotal", added_lines)
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.h b/src/ProductionManager.h"
            ),
        )

    def test_exact_composition_production_unblock_patch_closes_live_queue_stall(
        self,
    ) -> None:
        patch = _read_patch_text(EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH_FILE)

        for term in (
            "voiExactCompositionFirstWaveComplete",
            "return exactCompositionActive;",
            "an exact composition must never queue an unrequested unit",
            "exactCompositionFirstWaveIncomplete",
            "!standingProductionNeedsRoom && !exactCompositionFirstWaveIncomplete",
            "exact_composition_first_wave_incomplete=",
            "m_voiMobilityRefusalLogFrame",
            "currentFrame - lastLogFrame >= 224",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/BuildingManager.h b/src/BuildingManager.h"
            ),
        )

    def test_continuous_combat_production_relaunch_patch_closes_post_launch_stall(
        self,
    ) -> None:
        patch = _read_patch_text(CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH_FILE)

        for term in (
            "voiPostLaunchScopeThresholdMet",
            "voiEffectiveScopeThresholdMet",
            "VOI post-launch survivors cleared bounded relaunch gate",
            "The VOI doctrine path owns combat-unit selection",
            "if (voiExactCompositionActive(m_bot))",
            "&& !voiExactCompositionActive(m_bot)",
            "m_lastVoiExactCompositionReconciliationState",
            "m_lastVoiExactCompositionReconciliationLogFrame",
            ">= 224",
            "taskTargetsArmory",
            "production.queue_biases.TERRAN_ARMORY",
            "effectiveArmoryBias",
            "wantsArmory",
            "thor_armory_transition",
            "completedArmoryCount > 0",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.cpp b/src/CombatCommander.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.h b/src/ProductionManager.h"
            ),
        )
        self.assertNotIn(
            "diff --git a/src/BuildingManager.cpp b/src/BuildingManager.cpp",
            patch,
        )

    def test_resource_throughput_patch_sustains_mixed_army_macro(self) -> None:
        patch = _read_patch_text(RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH_FILE)

        for term in (
            "voiCompositionProductionTargetCount(",
            "m_voiCompositionProductionWaveMultiplier",
            "m_queue.getCountOfType(MetaTypeEnum::Marine)",
            "int BuildOrderQueue::getCountOfType(const MetaType & type) const",
            "while (numAssigned < gasWorkersTarget)",
            "getCompletedRefineryCount() * gasWorkersTarget",
            "retryCount <= 2 ? 22 * 5 : 22 * 60",
            "retryCount <= 2 ? 22 * 5 : 22 * 10",
            "blocked_passive_expand_quarantine",
            "m_queue.removeAllOfType(MetaTypeEnum::CommandCenter)",
            '\\"free_minerals\\"',
            '\\"free_gas\\"',
            '\\"current_supply\\"',
            '\\"max_supply\\"',
            '\\"actual_gas_workers\\"',
            '\\"total_gas_worker_target\\"',
            '\\"command_center_placement_quarantined\\"',
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertEqual(
            2,
            patch.count("+\t\t\twhile (numAssigned < gasWorkersTarget)"),
        )
        self.assertEqual(
            2,
            patch.count(
                "-\t\t\twhile (numAssigned < gasWorkersTarget && "
                "m_workerData.getWorkerJobCount(WorkerJobs::Gas) < gasWorkersTarget)"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count("diff --git a/src/WorkerManager.cpp b/src/WorkerManager.cpp"),
        )

    def test_startup_telemetry_patch_respects_manager_initialization(self) -> None:
        patch = _read_patch_text(STARTUP_TELEMETRY_INITIALIZATION_PATCH_FILE)

        for term in (
            "void UnitInfoManager::onStart()",
            "updateUnitInfo();",
            "Publish only after every subordinate manager has initialized.",
            "m_bot.GetAllyGeyserUnits()",
            "-    writeVoiTelemetry();",
            "+    writeVoiTelemetry();",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertLess(
            patch.index("m_combatCommander.onStart();"),
            patch.index("+    writeVoiTelemetry();"),
        )

    def test_gas_worker_patch_requires_completion_and_caps_each_refinery(
        self,
    ) -> None:
        patch = _read_patch_text(GAS_WORKER_COMPLETION_CAP_PATCH_FILE)

        for term in (
            "if (numAssigned > gasWorkersTarget)",
            "while (numAssigned > gasWorkersTarget)",
            "capGasWorkersBeforeBaseLookup",
            "m_workerData.setWorkerJob(gasWorker, WorkerJobs::Idle)",
            "Trimmed excess gas workers before refinery base/depot lookup.",
            "|| !refinery.isCompleted()",
            "std::set<sc2::Tag> handledCompletedRefineryTags",
            "!handledCompletedRefineryTags.insert(refinery.getTag()).second",
            "Re-evaluate next frame so completion-time assignments cannot "
            "oversubscribe gas.",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertLess(
            patch.index("if (numAssigned > gasWorkersTarget)"),
            patch.index("getBaseContainingPosition"),
        )
        self.assertLess(
            patch.index(
                "if (base == nullptr || !base->getResourceDepot().isValid() "
                "|| !base->getResourceDepot().isCompleted())"
            ),
            patch.index(
                "if (!handledCompletedRefineryTags.insert("
                "refinery.getTag()).second)"
            ),
        )
        self.assertEqual(
            1,
            patch.count("diff --git a/src/WorkerManager.cpp b/src/WorkerManager.cpp"),
        )
        self.assertEqual(
            2,
            patch.count(
                "Re-evaluate next frame so completion-time assignments cannot "
                "oversubscribe gas."
            ),
        )
        self.assertNotIn("+\t\tif (ownCompletedRefinery)", patch)
        self.assertNotIn(
            "+\tfor (const auto & unit : "
            "m_bot.UnitInfo().getUnits(Players::Self))",
            patch,
        )

    def test_offensive_sweep_patch_latches_target_until_arrival_or_stall(
        self,
    ) -> None:
        patch = _read_patch_text(STABLE_OFFENSIVE_SWEEP_TARGET_PATCH_FILE)

        for term in (
            'm_voiSweepOperationKey = "";',
            "m_voiSweepTarget = CCPosition();",
            "m_voiSweepBestTargetDistance = 0.0f;",
            "m_voiSweepLastProgressFrame = 0;",
            'voiPressureOperationLatchKey + "|lost_contact_sweep"',
            "static_cast<float>(mainAttackSquad.getUnits().size())",
            "* 0.60f",
            "m_voiSweepTarget,",
            "8.0f) >= sweepRequiredUnits",
            "sweepFrame - m_voiSweepLastProgressFrame >= 22u * 15u",
            "if (sweepTargetReached || sweepRouteStalled)",
            "++m_currentBaseExplorationIndex;",
            "m_voiSweepTarget = exploreMap();",
            "orderPosition = m_voiSweepTarget;",
            "VOI offensive sweep objective reached by the squad majority",
            "VOI offensive sweep route made no progress for 15 seconds",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn("+\t\torderPosition = exploreMap();", patch)
        self.assertLess(
            patch.index("if (sweepTargetReached || sweepRouteStalled)"),
            patch.index("++m_currentBaseExplorationIndex;"),
        )
        self.assertLess(
            patch.index("++m_currentBaseExplorationIndex;"),
            patch.index("selectNewSweepTarget = true;"),
        )
        self.assertNotIn(
            "if (sweepRouteStalled && !sweepTargetReached)",
            patch,
        )
        self.assertLess(
            patch.index("if (selectNewSweepTarget)"),
            patch.index("m_voiSweepTarget = exploreMap();"),
        )
        self.assertLess(
            patch.index("m_voiSweepTarget = exploreMap();"),
            patch.index("orderPosition = m_voiSweepTarget;"),
        )

    def test_adaptive_support_patch_selects_and_latches_counter_units(
        self,
    ) -> None:
        patch = _read_patch_text(ADAPTIVE_SUPPORT_COMPOSITION_PATCH_FILE)

        for term in (
            "voiObservedAdaptiveSupportTargetCount",
            "m_voiAdaptiveFirstWaveComplete",
            "m_voiAdaptiveSupportTargets.clear();",
            "observedTarget <= latchedTarget",
            "VOI exact first wave completed; adaptive support selection enabled",
            "VOI selected adaptive support unit=",
            "getVoiAdaptiveSupportTargetCount",
            "TERRAN_MARAUDER",
            "TERRAN_HELLION",
            "TERRAN_WIDOWMINE",
            "TERRAN_CYCLONE",
            "TERRAN_THOR",
            "TERRAN_MEDIVAC",
            "TERRAN_VIKINGFIGHTER",
            "TERRAN_LIBERATOR",
            "TERRAN_BANSHEE",
            "TERRAN_RAVEN",
            "TERRAN_BATTLECRUISER",
            "adaptiveSupportTargetCount",
            "std::max(productionTargetCount, adaptiveTargetCount)",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.h b/src/ProductionManager.h"
            ),
        )

    def test_operation_scoped_adaptive_combat_closure_enforces_runtime_invariants(
        self,
    ) -> None:
        patch = _read_patch_text(
            OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for term in (
            "voiCompletedUnitCount",
            "Start from completed units, then count every order once",
            "voiPressureOperationKey",
            'getVoiPolicyString("tactical_task.task_id", "")',
            "m_voiAdaptiveObservedTargets",
            "m_voiAdaptiveLastObservedFrame",
            "targetHoldFrames = 22u * 90u",
            "supportSupplyBudget = 40",
            "supportMineralBudget = 2400",
            "supportGasBudget = 1600",
            "std::max(0, productionTargetCount) + adaptiveTargetCount",
            "barracks_addon_replacement",
            "eligibleFactoryAddonProducers == 0",
            "starport_addon_replacement",
            "getVoiAdaptiveSupportTargets",
            "desiredMainAttackCount",
            "retainedAdaptiveUnits",
            "m_squadData.assignUnitToSquad",
            "TERRAN_GHOST",
            "TERRAN_REAPER",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)

        self.assertNotIn(
            "getUnitTypeCount(Players::Self, type.getUnitType(), false, true, true)",
            added_lines,
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.cpp b/src/CombatCommander.cpp"
            ),
        )

    def test_review_closure_patch_preserves_explicit_operations_and_32_entries(
        self,
    ) -> None:
        patch = _read_patch_text(
            REVIEW_CLOSURE_OPERATION_IDENTITY_FULL_COMPOSITION_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn('currentName.find("Harass") == 0', added_lines)
        self.assertEqual(13, added_lines.count("for (int i = 0; i < 32; ++i)"))
        self.assertNotIn("for (int i = 0; i < 8; ++i)", added_lines)
        for source in (
            "src/ProductionManager.cpp",
            "src/CombatCommander.cpp",
            "src/RangedManager.cpp",
        ):
            with self.subTest(source=source):
                self.assertIn(f"diff --git a/{source} b/{source}", patch)

    def test_all_terran_combat_scouts_patch_closes_assignment_and_order_ownership(
        self,
    ) -> None:
        patch = _read_patch_text(ALL_TERRAN_COMBAT_SCOUTS_PATCH_FILE)
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertEqual(
            2,
            added_lines.count(
                'token == "TERRAN_HELLION" && '
                "(type == sc2::UNIT_TYPEID::TERRAN_HELLION || "
                "type == sc2::UNIT_TYPEID::TERRAN_HELLIONTANK)"
            ),
        )
        self.assertIn(
            'addScoutType("TERRAN_HELLION", '
            "sc2::UNIT_TYPEID::TERRAN_HELLIONTANK);",
            added_lines,
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.cpp b/src/CombatCommander.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/RangedManager.cpp b/src/RangedManager.cpp"
            ),
        )
        self.assertIn(
            "if (!preserveScoutOrder\n"
            "\t\t\t&& !isCycloneHelper\n"
            "\t\t\t&& (isMedivac || isRaven",
            added_lines,
        )
        self.assertIn(
            "\tconst Squad * owningSquad = "
            "m_bot.Commander().Combat().getSquadData().getUnitSquad(\n"
            "\t\tUnit(medivac, m_bot));\n"
            '\tif (owningSquad != nullptr && owningSquad->getName() == "Scout")\n'
            "\t\treturn false;",
            added_lines,
        )
        self.assertIn(
            " \tsc2::Units otherSquadsUnits;\n"
            "+\tauto & squadData = "
            "m_bot.Commander().Combat().getSquadData();\n"
            " \tfor (auto & unit : "
            "m_bot.Commander().Combat().GetCombatUnits())\n"
            " \t{\n"
            "+\t\tconst Squad * owningSquad = squadData.getUnitSquad(unit);\n"
            '+\t\tif (owningSquad != nullptr && owningSquad->getName() == "Scout")',
            patch,
        )

    def test_semantic_operation_patch_closes_identity_and_bio_production_gaps(
        self,
    ) -> None:
        patch = _read_patch_text(
            SEMANTIC_OPERATION_PRODUCTION_CLOSURE_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn(
            '"task=" + bot.Commander().getVoiPolicyString("tactical_task.task_type", "")',
            added_lines,
        )
        self.assertNotIn(
            'getVoiPolicyString("tactical_task.task_id"',
            added_lines,
        )
        self.assertNotIn('getVoiPolicyString("update_id"', added_lines)
        for semantic_axis in (
            '"|role="',
            '"|task_min="',
            '"|task_max="',
            '"|army="',
            '"|scope_units="',
            '"|task_units="',
            '"|partial="',
            '"|task_partial="',
            '"|scope_location="',
            '"|target="',
        ):
            with self.subTest(semantic_axis=semantic_axis):
                self.assertIn(semantic_axis, added_lines)
        self.assertIn('"|route="', patch)
        self.assertIn('"|location="', patch)
        self.assertIn("requestedCount +=", added_lines)
        self.assertIn("effectiveMarineBias > 0.25f", added_lines)
        self.assertIn("effectiveMarauderBias > 0.25f", added_lines)
        self.assertIn("effectiveReaperBias > 0.25f", added_lines)
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/CombatCommander.cpp b/src/CombatCommander.cpp"
            ),
        )
        self.assertEqual(
            1,
            patch.count(
                "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp"
            ),
        )

    def test_adaptive_pressure_patch_covers_one_shot_first_wave_and_stable_keys(
        self,
    ) -> None:
        patch = _read_patch_text(
            ADAPTIVE_PRESSURE_STABLE_OPERATION_KEY_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn("voiExactCompositionActive(bot)", added_lines)
        self.assertIn('"pressure_with_main_army"', added_lines)
        self.assertIn(
            "entry.second.getUnitType().supplyRequired()",
            added_lines,
        )
        self.assertIn(
            "bot.GetMaxSupply() - bot.GetCurrentSupply()",
            added_lines,
        )
        self.assertNotIn("bot.GetCurrentSupply() < 196", added_lines)
        self.assertEqual(
            5,
            added_lines.count("voiAdaptiveSupportProductionActive("),
        )
        self.assertIn(
            "voiAdaptiveSupportProductionActive(m_bot, entry.first)\n"
            "\t\t\t\t? getVoiAdaptiveSupportTargetCount(entry.first)",
            added_lines,
        )
        self.assertIn(
            "voiAdaptiveSupportProductionActive(m_bot, policyUnitType)\n"
            "\t\t\t\t? getVoiAdaptiveSupportTargetCount(policyUnitType)",
            added_lines,
        )
        self.assertIn(
            "if (voiContinuousCompositionProductionConfigured(m_bot))",
            added_lines,
        )
        self.assertNotIn(
            "if (!m_voiAdaptiveFirstWaveComplete)\n\t{\n\t\treturn;",
            added_lines,
        )
        self.assertEqual(
            2,
            added_lines.count(
                "std::sort(operationLabels.begin(), operationLabels.end());"
            ),
        )
        self.assertEqual(2, added_lines.count("const auto appendUnitClasses"))
        self.assertEqual(2, added_lines.count("std::unique("))
        self.assertNotIn('"|scope_units="', added_lines)
        self.assertNotIn('"|task_units="', added_lines)
        for semantic_axis in (
            '"|route_avoid="',
            '"|lifetime="',
            '"|continuity="',
        ):
            with self.subTest(semantic_axis=semantic_axis):
                self.assertEqual(2, added_lines.count(semantic_axis))
        for source in ("src/ProductionManager.cpp", "src/CombatCommander.cpp"):
            with self.subTest(source=source):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source} b/{source}"),
                )

    def test_tactical_nuke_patch_covers_production_execution_and_telemetry(
        self,
    ) -> None:
        patch = _read_patch_text(TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH_FILE)
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for source in (
            "src/BuildingManager.cpp",
            "src/MetaTypeEnum.h",
            "src/MetaTypeEnum.cpp",
            "src/ProductionManager.cpp",
            "src/ProductionManager.h",
            "src/CombatCommander.h",
            "src/CombatCommander.cpp",
            "src/GameCommander.cpp",
            "src/TechTree.cpp",
        ):
            with self.subTest(source=source):
                self.assertIn(f"diff --git a/{source} b/{source}", patch)
        for production_contract in (
            "MetaType Nuke",
            "MetaTypeEnum::Nuke",
            "TERRAN_NUKE",
            "BUILD_NUKE",
            "TERRAN_GHOSTACADEMY",
            "TERRAN_GHOST",
            "TERRAN_FACTORY",
        ):
            with self.subTest(production_contract=production_contract):
                self.assertIn(production_contract, added_lines)
        for placement_recovery_contract in (
            "isVoiExplicitTacticalPrerequisiteBuilding",
            "tactical_prerequisite=",
            "retryCount <= 2 ? 22 * 5 : 22 * 10",
            "TERRAN_GHOSTACADEMY",
            "TERRAN_ARMORY",
            "TERRAN_FUSIONCORE",
        ):
            with self.subTest(
                placement_recovery_contract=placement_recovery_contract
            ):
                self.assertIn(placement_recovery_contract, added_lines)
        self.assertIn(
            "voiDoctrineRequestsTechTransition(m_bot, type) || "
            "isVoiTacticalPrerequisite",
            added_lines,
        )
        for execution_contract in (
            '"execute_ability"',
            '"tactical_nuke"',
            '"tactical_task.ability"',
            "EFFECT_NUKECALLDOWN",
            "last_seen_game_loop",
            "GetEffects()",
            "NukePersistentEffectId",
            "voiGhostHasTacticalNukeOrder",
            "order.ability_id",
            "voiTacticalNukePayloadCount",
            "TacticalNukeConfirmationRetryFrames",
            "AbilityPosition",
            "VoiTacticalNukeStage",
            "minimumCandidateRadius",
            "preferredStagingRadius",
            "angleIndex < 32",
            "isConnected(ghost->pos, candidate)",
            "No terrain-connected safe cast staging position",
            '"waiting_target"',
            '"confirming"',
            '"retryable"',
            '"ghost_order:EFFECT_NUKECALLDOWN"',
            '"persistent_effect:NUKEPERSISTENT"',
            '"payload_consumed:TERRAN_NUKE"',
            "Waiting for a safe recently observed high-value or clustered enemy target",
            "tacticalNukeNeedsTargetObservation",
            "enemyEvidenceScoutRequested",
            "enemyEvidenceScoutRequested && !explicitScoutScope",
            "Tactical nuke is waiting for a recently observed enemy target",
            "out-of-range target; staging must complete first",
            "TacticalAbilityPriority",
            '"NukeOps"',
            "updateVoiTacticalNukeSquad",
            '"clearing_blast_radius"',
            "voiTacticalNukeContainmentPosition",
            '"NukeContainment"',
            "waiting for MainAttack and other mobile allies to clear the blast radius",
        ):
            with self.subTest(execution_contract=execution_contract):
                self.assertIn(execution_contract, added_lines)
        for telemetry_contract in (
            "AbilityTask",
            "staging_command_issued_count",
            "cast_submitted_count",
            "cast_submission_frame",
            "cast_submitted_action",
            "payload_count_at_submission",
            "confirmation_state",
            "confirmation_count",
            "confirmation_frame",
            "confirmation_effect",
            "unit_roles.*.role",
            "unit_roles.*.ability_policy",
        ):
            with self.subTest(telemetry_contract=telemetry_contract):
                self.assertIn(telemetry_contract, added_lines)
        for ability_action_type in (
            "action.microActionType == MicroActionType::Ability",
            "action.microActionType == MicroActionType::AbilityPosition",
            "action.microActionType == MicroActionType::AbilityTarget",
            "action.microActionType == MicroActionType::ToggleAbility",
        ):
            with self.subTest(ability_action_type=ability_action_type):
                self.assertIn(ability_action_type, added_lines)
        self.assertIn(
            'ss << "|ability=" << sc2::AbilityTypeToName(action.abilityID);',
            added_lines,
        )
        self.assertRegex(
            added_lines,
            r"getVoi[A-Za-z0-9_]*Nuke[A-Za-z0-9_]*\(\) const",
        )
        self.assertIn("m_queue.contains(MetaTypeEnum::Nuke)", added_lines)
        for payload_state_contract in (
            "nukeReadyGhostCount",
            "GetAbilitiesForUnits(completedGhosts)",
            "payload_ready:EFFECT_NUKECALLDOWN",
            "payload_building:BUILD_NUKE",
            "m_queue.removeAllOfType(MetaTypeEnum::Nuke)",
            "VOI tactical nuke payload command submitted",
            "build_nuke_accepted",
            "payload_command_rejected",
            "blocked_payload_retry_cooldown",
            "canonicalQueueItemName",
            "completedGroundedAcademy",
            "academyIdle",
            "resourcesAvailable",
            "payloadBuildInProgress",
            "payloadReady",
            "submissionPending",
            "retryCoolingDown",
            "completedFactoryAvailable",
            "completed_factory_available",
            "tactical_nuke_factory_prerequisite",
            "waiting_for_completed_grounded_factory",
            "directSubmissionEligible",
            "ability query omission tolerated",
            "state/resource gate permits direct BUILD_NUKE submission",
        ):
            with self.subTest(payload_state_contract=payload_state_contract):
                self.assertIn(payload_state_contract, added_lines)
        self.assertIn("if (!directSubmissionEligible)", added_lines)
        self.assertNotIn(
            "getGroundDistance(ghost->pos, candidate)",
            added_lines,
        )
        self.assertIn("if (!normalAllowsNuke && shouldLogAvailability)", added_lines)
        self.assertIn("command not submitted.", added_lines)
        self.assertIn("availabilityChanged", added_lines)
        self.assertIn("shouldLogAvailability", added_lines)
        self.assertIn("m_lastVoiTacticalNukeAvailabilityLogFrame", added_lines)
        self.assertIn(">= 22u * 5u", added_lines)
        self.assertIn(
            "m_bot.GetFreeMinerals() >= m_bot.Data(type).mineralCost",
            added_lines,
        )
        self.assertIn(
            "m_bot.GetFreeGas() >= m_bot.Data(type).gasCost",
            added_lines,
        )
        self.assertNotIn(
            "VOI tactical nuke is not exposed by the Ghost Academy ability "
            "query; command not submitted.",
            added_lines,
        )
        self.assertNotIn("fallbackAbilities", added_lines)
        self.assertNotIn(
            "getUnitTypeCount(\n"
            "\t\tPlayers::Self,\n"
            "\t\tMetaTypeEnum::Nuke.getUnitType()",
            added_lines,
        )
        self.assertIn("const bool hasExplicitGhostRole", added_lines)
        self.assertNotIn("if (!voiHasTacticalNukeGhostRole", added_lines)
        self.assertIn(
            "const bool autonomousNeedsEnemyEvidence =\n"
            "\t\ttacticalNukeNeedsTargetObservation\n"
            "\t\t||",
            added_lines,
        )
        self.assertIn(
            "CCPosition scoutTarget = enemyEvidenceScoutRequested",
            added_lines,
        )
        self.assertIn("? GetNextEnemyStartCandidateToScout()", patch)
        self.assertGreaterEqual(added_lines.count("EFFECT_NUKECALLDOWN"), 2)
        self.assertNotIn("cast_executed_count", added_lines)
        self.assertNotIn("m_lastVoiTacticalNukeCastExecutedCount", added_lines)
        self.assertNotIn('"ability_cast.', added_lines)
        self.assertNotIn('tacticalTaskType == "tactical_nuke"', added_lines)

    def test_location_intent_target_lock_patch_prevents_home_fallback(self) -> None:
        patch = _read_patch_text(LOCATION_INTENT_TARGET_LOCK_PATCH_FILE)
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for contract in (
            "voiEffectiveTacticalNukeLocationIntent",
            "voiResolveTacticalNukeTargetAnchor",
            "TacticalNukeEnemyMainIntentRadius",
            "observedTarget != CCPosition()",
            "Util::Dist(anchor, observedTarget) <= radius",
            "anchor = observedTarget",
            "targetAnchorDistance > targetAnchorRadius",
            "target_location_match",
            "target_anchor_distance",
            "scout_target_enemy_main_distance",
            "retaining the requested location anchor",
            "singleEnemyStartCandidate",
            "voiIsRemoteCombatTarget(m_bot, fallbackTarget)",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertIn(
            "selectedTarget.anchor != CCPosition()",
            added_lines,
        )
        self.assertIn(
            "m_lastVoiTacticalNukeTargetLocationMatch = false",
            added_lines,
        )

    def test_explicit_terran_ability_patch_reaches_sc2_command_issue_path(
        self,
    ) -> None:
        patch = _read_patch_text(EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH_FILE)
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for source in (
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/GameCommander.cpp",
            "src/MetaTypeEnum.cpp",
            "src/MetaTypeEnum.h",
            "src/ProductionManager.cpp",
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    1,
                    patch.count(f"diff --git a/{source} b/{source}"),
                )
        explicit_abilities = {
            "stimpack",
            "marine_stimpack",
            "marauder_stimpack",
            "kd8_charge",
            "emp",
            "snipe",
            "ghost_cloak",
            "ghost_decloak",
            "widow_mine_burrow",
            "widow_mine_unburrow",
            "lock_on",
            "siege_mode",
            "unsiege",
            "hellbat_mode",
            "hellion_mode",
            "thor_high_impact_mode",
            "thor_explosive_mode",
            "medivac_afterburners",
            "medivac_heal",
            "medivac_load",
            "medivac_unload_all",
            "viking_fighter_mode",
            "viking_assault_mode",
            "liberator_defender_mode",
            "liberator_fighter_mode",
            "banshee_cloak",
            "banshee_decloak",
            "auto_turret",
            "interference_matrix",
            "anti_armor_missile",
            "yamato",
            "tactical_jump",
        }
        self.assertEqual(
            MICROMACHINE_TACTICAL_ABILITIES - {"", "tactical_nuke"},
            explicit_abilities,
        )
        for ability in sorted(explicit_abilities):
            with self.subTest(ability=ability):
                self.assertIn(f'"{ability}"', added_lines)
        for execution_contract in (
            "updateVoiExplicitAbilityAction();",
            "VoiExplicitAbility:",
            "Util::IsAbilityAvailable",
            "m_bot.Query()->Placement(",
            "bestLoadTarget",
            "LOAD_MEDIVAC",
            "UNLOADALLAT_MEDIVAC",
            "BEHAVIOR_CLOAKON_GHOST",
            "EFFECT_STIM_MARAUDER",
            "Explicit position ability target became invalid before command submission",
            "Explicit target ability target became invalid before command submission",
            "Explicit target ability target moved outside the verified cast range",
            "m_lastVoiExplicitAbilitySubmittedCount++",
            "Explicit ability reached the SC2 command issue path",
            "Protect reserved tactical nuke Ghost at home",
        ):
            with self.subTest(execution_contract=execution_contract):
                self.assertIn(execution_contract, added_lines)
        for telemetry_contract in (
            "actor_type",
            "target_tag",
            "available_caster_count",
            "planned_count",
            "submitted_count",
            "last_action",
            "getVoiExplicitAbilityActorTag",
            "getVoiExplicitAbilityTargetPosition",
        ):
            with self.subTest(telemetry_contract=telemetry_contract):
                self.assertIn(telemetry_contract, added_lines)
        for production_contract in (
            "taskTechOrAbilityProduction",
            "taskTargetsBarracks",
            "taskTargetsFactoryTechLab",
            "taskTargetsStarportTechLab",
            "MetaTypeEnum::Stimpack",
            "MetaTypeEnum::PersonalCloaking",
            "MetaTypeEnum::BansheeCloak",
            "MetaTypeEnum::YamatoCannon",
            "explicit_ability_stimpack_upgrade",
            "explicit_ability_ghost_cloak_upgrade",
            "explicit_ability_banshee_cloak_upgrade",
            "explicit_ability_yamato_upgrade",
        ):
            with self.subTest(production_contract=production_contract):
                self.assertIn(production_contract, added_lines)
        self.assertNotIn(
            "execute_ability currently supports tactical_task.ability=tactical_nuke",
            added_lines,
        )

    def test_explicit_scout_command_epoch_forces_one_fresh_sc2_move(
        self,
    ) -> None:
        patch = _read_patch_text(EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH_FILE)
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for source in (
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/GameCommander.cpp",
        ):
            with self.subTest(source=source):
                self.assertIn(f"diff --git a/{source} b/{source}", patch)
        for contract in (
            "explicitScoutUpdateId != m_voiExplicitScoutUpdateId",
            "m_voiExplicitScoutReissuePending = true",
            "? !aAlreadyScout",
            "unitActions.erase(unit)",
            "nextCommandFrameForUnit.erase(unit)",
            "VoiExplicitScoutOrder:",
            "MicroActionType::Move",
            "explicitScoutOrderAction",
            "!explicitScoutOrderAction",
            'm_lastVoiScoutExplicitReissueStatus = "submitted"',
            'action.description = "MoveToGoalOrder"',
            "scout_explicit_reissue_status",
            "scout_explicit_reissue_frame",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)

    def test_standing_production_continuity_survives_operation_overlays(
        self,
    ) -> None:
        patch = _read_patch_text(
            STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn(
            "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp",
            patch,
        )
        for contract in (
            "standingProductionOrder",
            "isStandingProductionTarget",
            "standingMarineProduction",
            '"TERRAN_MARINE");',
            "requestedCompositionGasPerWave",
            "continuousCompositionProduction",
            "isStandingProductionTarget(policyUnitType)",
            '!isStandingProductionTarget("TERRAN_MARINE")',
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertNotIn(
            "compositionSiegeBias,\n\t\tadaptiveSupportBias(\"TERRAN_LIBERATOR\")",
            added_lines,
        )

    def test_explicit_ability_caster_preempts_unrelated_production_until_represented(
        self,
    ) -> None:
        patch = _read_patch_text(
            EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn(
            "diff --git a/src/ProductionManager.cpp b/src/ProductionManager.cpp",
            patch,
        )
        for contract in (
            "tactical_task.unit_classes",
            "explicitAbilityCasterPending",
            "explicitAbilityNeedsFactoryTechLab",
            "explicitAbilityNeedsStarportTechLab",
            "explicit_ability_caster",
            "completedFactoryCount > 0",
            "!explicitAbilityCasterPending",
            "voiRepresentedUnitCount(type) > 0",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        for caster in (
            "Marine",
            "Marauder",
            "Reaper",
            "Ghost",
            "Hellion",
            "WidowMine",
            "Cyclone",
            "Thor",
            "SiegeTank",
            "Medivac",
            "Viking",
            "Liberator",
            "Banshee",
            "Raven",
            "Battlecruiser",
        ):
            with self.subTest(caster=caster):
                self.assertIn(f"MetaTypeEnum::{caster}", added_lines)
        self.assertIn(
            'queueVoiDoctrineItem(MetaTypeEnum::Starport, "starport_transition", true)',
            added_lines,
        )
        self.assertNotIn(
            "wantsStarport && factoryCount > 0 && starportCount == 0",
            added_lines,
        )

    def test_explicit_ability_completion_requires_subsequent_sc2_observation(
        self,
    ) -> None:
        patch = _read_patch_text(
            EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for source_path in (
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/GameCommander.cpp",
        ):
            with self.subTest(source_path=source_path):
                self.assertIn(
                    f"--- a/{source_path}",
                    patch,
                )
                self.assertIn(
                    f"+++ b/{source_path}",
                    patch,
                )
        for contract in (
            "m_voiExplicitAbilityAwaitingConfirmation",
            "m_lastVoiExplicitAbilitySubmissionFrame",
            "m_lastVoiExplicitAbilityConfirmationState",
            "m_lastVoiExplicitAbilityConfirmationCount",
            "m_lastVoiExplicitAbilityConfirmationFrame",
            "m_lastVoiExplicitAbilityConfirmationEffect",
            "confirmationTimeoutFrames = 224",
            '"pending_confirmation"',
            '"confirmation_timeout"',
            '"actor_order:"',
            '"actor_energy:decreased"',
            '"ability_availability:consumed"',
            "explicitAbilityConfirmationFresh",
            "rawTacticalTaskAbility == \"tactical_nuke\"",
            "Explicit ability effect was observed in subsequent SC2 state",
            "waiting for SC2 observation confirmation",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertIn(
            '-\t\t\t\tm_lastVoiExplicitAbilityStatus = "completed";',
            patch,
        )
        self.assertIn(
            '-                "CombatCommander submitted the explicit ability through the SC2 command issue path";',
            patch,
        )

    def test_explicit_ability_caster_production_isolated_from_legacy_strategy(
        self,
    ) -> None:
        patch = _read_patch_text(
            EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for source_path in (
            "src/ProductionManager.cpp",
            "src/BuildingManager.cpp",
        ):
            with self.subTest(source_path=source_path):
                self.assertIn(f"--- a/{source_path}", patch)
                self.assertIn(f"+++ b/{source_path}", patch)
        for contract in (
            "voiExplicitAbilityCasterPending",
            "queueInspectionBudget = m_queue.size()",
            "removeIfUnrequested",
            "casterNeedsStarport",
            "casterNeedsStarportTechLab",
            "requestedUnitClasses",
            "requiredByCaster",
            "MetaTypeEnum::Starport",
            "MetaTypeEnum::StarportTechLab",
            "MetaTypeEnum::StarportReactor",
            "MetaTypeEnum::Banshee",
            "MetaTypeEnum::BansheeCloak",
            "MetaTypeEnum::HyperflightRotors",
            "trustedVoiMacroPlacement",
            "canTrustSafeMacroPlacement",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        for caster in (
            "TERRAN_MEDIVAC",
            "TERRAN_VIKINGFIGHTER",
            "TERRAN_LIBERATOR",
            "TERRAN_BANSHEE",
            "TERRAN_RAVEN",
            "TERRAN_BATTLECRUISER",
        ):
            with self.subTest(caster_prerequisite_preserved=caster):
                self.assertIn(caster, added_lines)

    def test_explicit_ability_attempt_lifecycle_is_exact_and_observation_bound(
        self,
    ) -> None:
        patch = _read_patch_text(
            EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        for source_path in (
            "src/CombatCommander.cpp",
            "src/CombatCommander.h",
            "src/GameCommander.cpp",
        ):
            with self.subTest(source_path=source_path):
                self.assertIn(f"--- a/{source_path}", patch)
                self.assertIn(f"+++ b/{source_path}", patch)
        for contract in (
            "explicitAbilityAttemptGeneration",
            "explicitAbilityUpdateId",
            "explicitAbilityTaskId",
            "explicitAbilityName",
            "bindCurrentVoiExplicitAbilityAttempt",
            "isCurrentVoiExplicitAbilityAttempt",
            "discardVoiExplicitAbilityActions",
            "advanceVoiExplicitAbilityAttempt",
            "voiExplicitAbilityDesiredStateSatisfied",
            "voiExplicitAbilityIsIrreversible",
            "voiExplicitAbilityAcceptanceIsTerminal",
            "m_lastVoiExplicitAbilityPhase",
            "m_lastVoiExplicitAbilitySubmittedAttemptGeneration",
            "m_lastVoiExplicitAbilityObservedAcceptedAttemptGeneration",
            "m_lastVoiExplicitAbilityTerminalAttemptGeneration",
            "m_lastVoiExplicitAbilityActorPassengerTags",
            "m_lastVoiExplicitAbilitySpawnedUnitTags",
            "STIMPACKMARAUDER",
            '\\"phase\\":\\"',
            '\\"attempt_generation\\":',
            '\\"submitted_attempt_generation\\":',
            '\\"observed_accepted_frame\\":',
            '\\"observed_accepted_attempt_generation\\":',
            '\\"observed_accepted_evidence\\":\\"',
            '\\"terminal_attempt_generation\\":',
            "explicitAbilityTelemetryMatchesAttempt",
            "exact update, task, ability, and attempt",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertNotIn(
            "VoiRavenInterferenceMatrixBuff",
            added_lines,
        )
        self.assertNotIn(
            "VoiRavenAntiArmorMissileBuff",
            added_lines,
        )

    def test_explicit_ability_review_closure_matches_sc2_capabilities_and_scope(
        self,
    ) -> None:
        patch = _read_patch_text(
            EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn("--- a/src/CombatCommander.cpp", patch)
        self.assertIn("+++ b/src/CombatCommander.cpp", patch)
        self.assertGreaterEqual(
            added_lines.count("sc2::ABILITY_ID::EFFECT_STIM"),
            2,
        )
        self.assertNotIn("EFFECT_STIM_MARINE", added_lines)
        self.assertNotIn("EFFECT_STIM_MARAUDER", added_lines)
        for contract in (
            "alreadySatisfiedCaster",
            "allMatchingCastersSatisfied",
            'abilityName == "medivac_unload_all"',
            "completeAlreadySatisfied",
            "voiExplicitAbilityBlocksAutonomousAction",
            "explicit_state_ownership",
            "voiTacticalTaskWithinDuration",
            "MORPH_UNSIEGE",
            "MORPH_SIEGEMODE",
            "BURROWUP_WIDOWMINE",
            "BURROWDOWN_WIDOWMINE",
            "BEHAVIOR_CLOAKOFF_GHOST",
            "BEHAVIOR_CLOAKON_GHOST",
            "MORPH_HELLION",
            "MORPH_HELLBAT",
            "MORPH_THOREXPLOSIVEMODE",
            "MORPH_THORHIGHIMPACTMODE",
            "MORPH_VIKINGASSAULTMODE",
            "MORPH_VIKINGFIGHTERMODE",
            "MORPH_LIBERATORAAMODE",
            "MORPH_LIBERATORAGMODE",
            "BEHAVIOR_CLOAKOFF",
            "BEHAVIOR_CLOAKON",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)

    def test_authoritative_addon_runtime_clearance_uses_exact_sc2_footprint(
        self,
    ) -> None:
        patch = _read_patch_text(
            AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn("--- a/src/BuildingManager.cpp", patch)
        self.assertIn("+++ b/src/BuildingManager.cpp", patch)
        for contract in (
            "queryCanPlaceVoiProductionAddonFootprint",
            "producerTile != CCTilePosition()",
            "const CCPosition delta = addonCenter - unit.getPosition()",
            "const float collisionExtent = 1.0f + unit.getUnitPtr()->radius",
            "std::abs(delta.x) >= collisionExtent",
            "std::abs(delta.y) >= collisionExtent",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertNotIn("Util::Dist(addonCenter", added_lines)

    def test_banshee_cloak_uses_unit_specific_sc2_command_ids(self) -> None:
        patch = _read_patch_text(
            BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )

        for source in ("src/CombatCommander.cpp", "src/RangedManager.cpp"):
            with self.subTest(source=source):
                self.assertIn(f"--- a/{source}", patch)
                self.assertIn(f"+++ b/{source}", patch)
        for contract in (
            "BEHAVIOR_CLOAKON_BANSHEE",
            "BEHAVIOR_CLOAKOFF_BANSHEE",
            "voiIsAbilityAvailableForCommand",
            "SC2 ability queries may expose the generic remap",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertIn("VoiRoleBansheeCloak", patch)
        self.assertIn(
            "spec = { sc2::ABILITY_ID::BEHAVIOR_CLOAKON,",
            removed_lines,
        )
        self.assertIn(
            "spec = { sc2::ABILITY_ID::BEHAVIOR_CLOAKOFF,",
            removed_lines,
        )
        self.assertNotIn(
            "action.abilityID = sc2::ABILITY_ID::BEHAVIOR_CLOAKON;",
            added_lines,
        )

    def test_allied_cloak_observation_reaches_banshee_runtime_logic(self) -> None:
        patch = _read_patch_text(
            ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH_FILE
        )
        s2client_patch = _read_patch_text(S2CLIENT_PATCH_FILE)

        for source in ("src/RangedManager.cpp", "src/Util.cpp"):
            with self.subTest(source=source):
                self.assertIn(f"--- a/{source}", patch)
                self.assertIn(f"+++ b/{source}", patch)
        self.assertGreaterEqual(patch.count("sc2::Unit::CloakedAllied"), 3)
        for contract in (
            "SC2APIProtocol::CloakState::CloakedUnknown",
            "CloakedAllied = 4",
            "Unknown = 5",
            "SC2APIProtocol::CloakState::CloakedAllied",
            "Unit::CloakedAllied",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, s2client_patch)

    def test_explicit_ability_caster_ownership_blocks_competing_micro(self) -> None:
        patch = _read_patch_text(EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH_FILE)
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn("--- a/src/CombatCommander.cpp", patch)
        self.assertIn("bool CombatCommander::PlanAction", patch)
        for contract in (
            "explicit_caster_ownership",
            "m_voiExplicitAbilityStagingActorTag",
            "m_lastVoiExplicitAbilityActorTag",
            "m_lastVoiExplicitAbilityTerminalAttemptGeneration",
            "m_voiExplicitAbilityAttemptGeneration",
            'activeExplicitAbility != "tactical_nuke"',
            "!directExplicitAction",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertLess(
            added_lines.index(
                "m_lastVoiExplicitAbilityTerminalAttemptGeneration"
            ),
            added_lines.index("explicit_caster_ownership"),
        )

    def test_explicit_ability_staging_move_is_single_flight(self) -> None:
        patch = _read_patch_text(
            EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH_FILE
        )
        added_lines = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

        self.assertIn("--- a/src/CombatCommander.cpp", patch)
        self.assertIn("--- a/src/CombatCommander.h", patch)
        for contract in (
            "m_voiExplicitAbilityStagingCommandActorTag",
            "m_voiExplicitAbilityStagingCommandPosition",
            "stagingCommandOwnsRoute",
            "already in flight",
            "stalled-route observation own bounded recovery",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, added_lines)
        self.assertLess(
            patch.index("stagingCommandOwnsRoute"),
            patch.index("UnitAction stagingAction"),
        )

    def test_hook_manifest_covers_verified_upstream_manager_hooks(self) -> None:
        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())

        self.assertEqual(
            "eb893161371dab975a0a7e600f9e250ac03ec1ef",
            manifest["verified_upstream_commit"],
        )
        self.assertEqual("src/GameCommander.cpp", manifest["central_polling_hook"]["source_path"])
        self.assertIn("GameCommander::onFrame", manifest["central_polling_hook"]["function"])
        self.assertIn(
            "patches/0004-live-operation-state-machine.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0005-addon-relocation-recovery.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0006-grounded-addon-candidate-fix.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0007-guaranteed-producer-grounding.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0008-emergency-land-query-fallback.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0009-grounded-production-and-observed-targeting.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0010-exact-composition-production-progress.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0011-production-resource-operation-persistence.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0012-live-operation-unblock.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0013-stable-flank-stage-latch.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0014-production-staging-and-observed-operation.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0015-addon-query-footprint-validation.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0016-authoritative-addon-placement-query.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0017-authoritative-addon-execution.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0018-continuous-army-macro.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0019-continuous-army-economy-scaling.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0020-standing-composition-reinforcement-waves.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0021-offensive-sweep-self-base-exclusion.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0022-bounded-placement-query-cache.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0023-production-facility-stability-and-tank-recovery.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0026-continuous-combat-production-relaunch.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0027-resource-throughput-and-expansion-backoff.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0028-startup-telemetry-initialization.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0029-gas-worker-completion-and-cap.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0030-stable-offensive-sweep-target.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0031-adaptive-support-composition.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0032-operation-scoped-adaptive-combat-closure.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0033-review-closure-operation-identity-and-full-composition.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0034-semantic-operation-production-closure.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            "patches/0035-adaptive-pressure-stable-operation-key.patch",
            {patch["path"] for patch in manifest["patch_bundle"]},
        )
        self.assertIn(
            {
                "path": "patches/0036-tactical-nuke-command-hierarchy.patch",
                "order": 36,
                "scope": (
                    "tactical nuke MetaType and full Factory/Ghost prerequisite "
                    "production, four-Marine target-acquisition scope with separate "
                    "defensive escort production, authoritative Ghost "
                    "Academy/Factory/Armory/Fusion Core placement "
                    "with bounded tactical-prerequisite retry, "
                    "completed-Factory/state/resource-gated direct BUILD_NUKE "
                    "submission with "
                    "SC2-observed accepted/rejected state, duplicate-payload "
                    "protection, retry cooldown, and rate-limited ability "
                    "diagnostics, "
                    "stale Nuke queue removal, "
                    "dedicated NukeOps squad ownership, blast-radius MainAttack "
                    "containment, safe recently observed enemy targeting, dense "
                    "terrain-connected staging selection, retry-safe "
                    "EFFECT_NUKECALLDOWN submission, SC2-observed cast/effect "
                    "confirmation, and dedicated production/AbilityTask telemetry"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0045-explicit-ability-review-closure.patch"
                ),
                "order": 45,
                "scope": (
                    "resolve Marine and Marauder stim through the SC2 "
                    "capability ID actually exposed by unit queries, "
                    "require every scoped Medivac to be empty before treating "
                    "unload-all as already satisfied, and preserve active "
                    "explicit siege, burrow, cloak, vehicle, Viking, and "
                    "Liberator state commands by rejecting autonomous inverse "
                    "mode changes for their full policy TTL"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0055-operation-production-ownership-and-restore-proof.patch"
                ),
                "order": 55,
                "scope": (
                    "attach operation_id and generation ownership to "
                    "operation-created production queue items and inherited "
                    "prerequisites, purge only exclusively operation-owned "
                    "work when its runtime or policy lifecycle becomes "
                    "terminal, preserve shared macro work, consume "
                    "operation-specific issued_at_frame deadlines, expose "
                    "production-owner release telemetry, and require "
                    "post-cancel MainAttack command and movement restoration "
                    "evidence"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0056-embedded-build-input-identity.patch",
                "order": 56,
                "scope": (
                    "compile the deterministic VOI build-input identity into "
                    "the MicroMachine executable and expose a side-effect-free "
                    "command-line query used by build finalization and live "
                    "smoke provenance checks"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0057-tech-gas-before-second-barracks.patch",
                "order": 57,
                "scope": (
                    "persist whether the active VOI policy actually requires "
                    "tech gas to the queue-consumption boundary, treat the first "
                    "Barracks building task as represented, hold follow-up "
                    "Barracks only for gas-dependent bio, mech, air, Ghost, and "
                    "explicit ability lanes until a Refinery is observed, leave "
                    "pure Marine production unblocked when an unrelated Refinery "
                    "is queued, promote an already queued required Refinery above "
                    "blocking opening production, suppress duplicate bootstrap "
                    "while one is queued or building, and preserve first-Barracks "
                    "Marine bootstrap"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0058-operation-production-review-closure.patch",
                "order": 58,
                "scope": (
                    "attribute each represented production increase to at "
                    "most one sorted operation owner, exclude initial "
                    "reservations when existing units transition into operation "
                    "ownership, retract credits from the owner whose owned count "
                    "fell, carry unattributed represented-count loss debt across "
                    "frames before crediting replacement growth, reset count and "
                    "debt epochs when the last owner leaves, and retain a bounded "
                    "owner-keyed queue-purge history containing operation_id, "
                    "generation, frame, and removed_count so same-frame evidence "
                    "cannot be overwritten"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0059-production-fifo-and-zero-owner-cleanup.patch",
                "order": 59,
                "scope": (
                    "preserve FIFO execution for equal-priority production "
                    "queue items even though the queue executes from its back, "
                    "emit positive-frame operation cleanup evidence when "
                    "cancellation releases either stopped owned units or an "
                    "operation with no remaining owned units, and preserve the "
                    "first terminal cleanup action and frame idempotently across "
                    "later release passes"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0054-authoritative-parallel-operation-lifecycle.patch"
                ),
                "order": 54,
                "scope": (
                    "make task-specific operation completion defaults exclude "
                    "command submission, keep finite deadlines immutable for an "
                    "existing operation_id and generation, make any explicit "
                    "partial-scope false fail closed, and share CombatCommander "
                    "runtime terminal state with ProductionManager as the "
                    "authoritative operation lifecycle"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0049-explicit-ability-caster-ownership.patch"
                ),
                "order": 49,
                "scope": (
                    "reserve the selected explicit-ability caster from Squad "
                    "and unit-manager autonomous actions while staging or "
                    "awaiting confirmation, keep direct explicit actions and "
                    "tactical-nuke ownership unchanged, and release the caster "
                    "immediately when the exact attempt becomes terminal"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0050-explicit-ability-staging-single-flight.patch"
                ),
                "order": 50,
                "scope": (
                    "bind an issued explicit-ability staging Move to its exact "
                    "caster and staging target for the full route lifetime, "
                    "suppress duplicate submissions while position "
                    "observations show progress, and release that ownership "
                    "only when the existing stalled-route detector rejects "
                    "and re-resolves the route"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0051-all-terran-combat-scouts.patch",
                "order": 51,
                "scope": (
                    "treat Hellion and transformed Hellbat forms as the same "
                    "TERRAN_HELLION composition and role token, include "
                    "Hellbats in explicit Scout squad candidate selection, "
                    "preserve Scout order ownership over Hellion, Medivac, and "
                    "Raven support fallbacks, and exclude Scout-owned units from "
                    "cross-squad threat or healing control so every standard "
                    "Terran combat-unit family can follow its bounded scouting "
                    "target"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0052-parallel-operations-ingame-hud.patch",
                "order": 52,
                "scope": (
                    "consume indexed parallel operations, aggregate every "
                    "operation-scoped tactical production target, unit class, "
                    "priority, and exact composition into prerequisite closure "
                    "for Terran bio, Factory, Starport, Ghost, Armory, and "
                    "Fusion Core lanes, maintain operation_id and generation "
                    "scoped dynamic squads with exclusive unit-tag ownership, "
                    "protect operation units from autonomous reassignment, "
                    "execute scout, attack, and defend tasks through existing "
                    "Squad micro, emit operation-scoped observed telemetry, "
                    "and draw truthful in-game HUD lifecycle, route, and target "
                    "state"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0053-parallel-operation-lifecycle-review-closure.patch"
                ),
                "order": 53,
                "scope": (
                    "fail closed on empty policy update identity, exclude "
                    "terminal, expired, and unsupported parallel operations "
                    "from production, consume operation-scoped lifetime, "
                    "deadline, completion, and strict partial-scope semantics, "
                    "distinguish frame-zero command submission from missing "
                    "evidence, release terminal ownership, and expose lifecycle "
                    "and owned-unit telemetry for live verification"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0047-banshee-unit-specific-cloak-command.patch"
                ),
                "order": 47,
                "scope": (
                    "submit Banshee cloak and decloak through the "
                    "unit-specific SC2 executable ability IDs while accepting "
                    "generic capability-query remaps, and use the corrected "
                    "IDs in explicit DSL execution, unit-role micro, "
                    "inverse-action ownership, and autonomous RangedManager "
                    "cloak logic"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0048-allied-cloak-observation-confirmation.patch"
                ),
                "order": 48,
                "scope": (
                    "preserve protocol CloakedUnknown and CloakedAllied "
                    "observations through s2client-api without per-frame "
                    "unsupported-state log flooding, recognize allied Banshee "
                    "cloak in autonomous uncloak and cloaked-combat safety "
                    "decisions, and allow explicit cloak tasks to reach "
                    "effect_observed confirmation from the actual SC2 unit "
                    "state"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0046-authoritative-addon-runtime-clearance.patch"
                ),
                "order": 46,
                "scope": (
                    "trust the authoritative SC2 placement query for a "
                    "grounded producer's exact 2x2 addon footprint instead "
                    "of rejecting valid adjacent structures through a broad "
                    "radius heuristic, and clear only friendly mobile units "
                    "whose collision boxes actually overlap the addon tiles"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0037-location-intent-target-lock.patch",
                "order": 37,
                "scope": (
                    "requested enemy-location anchor locking for combat scouts "
                    "and tactical-nuke target selection, observed-enemy anchor "
                    "promotion inside the enemy-main area, home-adjacent "
                    "fallback rejection, route-stall retention, and "
                    "target-anchor match telemetry"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0038-explicit-terran-ability-execution.patch",
                "order": 38,
                "scope": (
                    "SC2-availability-checked execution for explicit Terran "
                    "abilities and mode changes, including Marine/Marauder-specific "
                    "stim, Ghost cloak/decloak, Medivac load/unload, caster and "
                    "target resolution from tactical_task unit classes and "
                    "location intent, target liveness and cast-range submission "
                    "guards, generic AbilityTask actor/action telemetry, "
                    "authoritative production prerequisites and upgrades including "
                    "Personal Cloaking, and protected home defense ownership for "
                    "the reserved tactical-nuke Ghost"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0039-explicit-scout-command-epoch.patch",
                "order": 39,
                "scope": (
                    "update-id-scoped explicit combat-scout command epochs that "
                    "prefer a fresh matching unit, invalidate stale planned "
                    "actions and command cooldowns, submit one prioritized MOVE "
                    "even when the same SC2 order already exists, then restore "
                    "ordinary duplicate suppression with planned/submitted telemetry"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": "patches/0040-standing-production-continuity-closure.patch",
                "order": 40,
                "scope": (
                    "standing production targets remain authoritative under "
                    "operation-layer overlays, Marine continuity runs alongside "
                    "requested Factory and Tank tech, standing gas and facility "
                    "scaling stays active without exact composition entries, and "
                    "ground siege bias no longer creates an unrequested Liberator "
                    "and Starport transition"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0041-explicit-ability-caster-production-priority.patch"
                ),
                "order": 41,
                "scope": (
                    "explicit execute_ability unit classes become authoritative "
                    "caster production requests for all supported Terran bio, "
                    "mech, air, and capital units; required add-ons and tech "
                    "structures are reconstructed in ProductionManager, unrelated "
                    "doctrine and expansion work is deferred only while no caster "
                    "is represented, Starport transition waits for a completed "
                    "Factory, and standing macro resumes after caster production "
                    "is queued, training, or complete"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0044-explicit-ability-attempt-lifecycle.patch"
                ),
                "order": 44,
                "scope": (
                    "bind each explicit ability action and telemetry record to "
                    "the exact update, task, ability, and attempt generation; "
                    "separate planned, submitted, observed-accepted, and "
                    "effect-observed phases; suppress stale or irreversible "
                    "duplicate submissions; recognize already-satisfied unit "
                    "states; and confirm effects from actor type, cloak, buff, "
                    "cargo, spawned-unit, destination, order, energy, and "
                    "ability-availability observations"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0042-explicit-ability-observation-confirmation.patch"
                ),
                "order": 42,
                "scope": (
                    "non-nuke explicit ability submissions remain executing "
                    "until subsequent SC2 observations confirm ability-specific "
                    "unit-type, cloak, buff, cargo, target-state, destination, or "
                    "spawned-unit effects; SC2 pathing queries are bounded into "
                    "API-safe batches, stalled staging routes are rejected and "
                    "re-resolved, and bounded confirmation timeout reopens safe "
                    "retry, while location-derived target staging and AbilityTask "
                    "submission/confirmation telemetry distinguish planned, "
                    "submitted, pending, and observed completion"
                ),
            },
            manifest["patch_bundle"],
        )
        self.assertIn(
            {
                "path": (
                    "patches/"
                    "0043-explicit-ability-production-isolation.patch"
                ),
                "order": 43,
                "scope": (
                    "while an explicit non-nuke ability caster is absent, "
                    "isolate its prerequisite production lane from unrelated "
                    "legacy Starport/Banshee tech, bound build-queue inspection "
                    "to the queue snapshot, and trust already SC2-validated VOI "
                    "macro placements instead of entering an additional "
                    "unbounded path-safety search"
                ),
            },
            manifest["patch_bundle"],
        )

        hooks = manifest["manager_hooks"]
        domains = {hook["domain"] for hook in hooks}
        self.assertEqual(
            {
                "production",
                "combat",
                "scouting",
                "economy",
                "combat_analysis",
                "composition",
                "squad",
                "scope",
                "tactical_task",
                "building_tasks",
                "workers",
            },
            domains,
        )
        required_sources = {
            "src/ProductionManager.cpp",
            "src/CombatCommander.cpp",
            "src/ScoutManager.cpp",
            "src/WorkerManager.cpp",
            "src/CombatAnalyzer.cpp",
            "src/Squad.cpp",
            "src/GameCommander.cpp",
            "src/BuildingManager.cpp",
        }
        self.assertEqual(required_sources, {hook["source_path"] for hook in hooks})
        for hook in hooks:
            with self.subTest(hook=hook["domain"]):
                self.assertTrue(hook["keys"])
                self.assertTrue(hook["function"])
                self.assertTrue(hook["intended_effect"])
        hooks_by_domain = {hook["domain"]: hook for hook in hooks}
        production_hook = hooks_by_domain["production"]
        self.assertIn("tactical_task.ability", production_hook["keys"])
        self.assertIn("tactical_task.unit_classes", production_hook["keys"])
        self.assertIn("tactical_task.duration_seconds", production_hook["keys"])
        self.assertEqual(
            (
                "ProductionManager::refreshVoiDoctrinePolicyBaseline() / "
                "ProductionManager::applyVoiDoctrineProductionBias() / "
                "ProductionManager::putImportantBuildOrderItemsInQueue() / "
                "ProductionManager::manageBuildOrderQueue() / "
                "ProductionManager::queueVoiDoctrineItem()"
            ),
            production_hook["secondary_function"],
        )
        for telemetry_field in (
            "tactical_nuke_payload_status",
            "tactical_nuke_payload_update_id",
            "tactical_nuke_payload_producer_tag",
            "tactical_nuke_payload_submission_count",
            "tactical_nuke_payload_accepted_count",
            "tactical_nuke_payload_rejected_count",
            "tactical_nuke_payload_submission_frame",
            "tactical_nuke_payload_accepted_frame",
            "tactical_nuke_payload_next_retry_frame",
        ):
            with self.subTest(telemetry_field=telemetry_field):
                self.assertIn(telemetry_field, production_hook["telemetry_fields"])
        required_production_fields = {
            "last_doctrine_required_count",
            "last_doctrine_represented_count",
            "last_doctrine_prerequisites_satisfied",
            "operation_owned_queue_item_count",
            "operation_owned_queue_items",
            "operation_production_owner_release_count",
            "operation_production_queue_purge_count",
            "last_operation_production_queue_purge_frame",
            "last_operation_production_released_owner",
            "last_operation_production_queue_purge_event",
            "operation_production_queue_purge_events",
        }
        self.assertTrue(
            required_production_fields
            <= set(production_hook["telemetry_fields"])
        )
        combat_hook = hooks_by_domain["combat"]
        self.assertIn(
            "main_attack_unit_samples",
            combat_hook["telemetry_fields"],
        )
        self.assertIn(
            "becomes an actual production command only after",
            production_hook["intended_effect"],
        )
        self.assertIn(
            "isolate its prerequisite lane from unrelated legacy Starport/Banshee work",
            production_hook["intended_effect"],
        )
        self.assertIn(
            "initial queue snapshot",
            production_hook["intended_effect"],
        )
        building_tasks_hook = hooks_by_domain["building_tasks"]
        self.assertIn(
            "BuildingManager::assignWorkerToUnassignedBuilding(Building &, bool)",
            building_tasks_hook["secondary_function"],
        )
        self.assertIn(
            "bypass the redundant unbounded path-safety search",
            building_tasks_hook["intended_effect"],
        )
        tactical_task_hook = hooks_by_domain["tactical_task"]
        self.assertEqual(
            "verified_consumed_with_observation_confirmed_ability_staging_telemetry",
            tactical_task_hook["status"],
        )
        self.assertIn("tactical_task.ability", tactical_task_hook["keys"])
        for role_key in (
            "unit_roles.*.unit_type",
            "unit_roles.*.role",
            "unit_roles.*.ability_policy",
        ):
            with self.subTest(role_key=role_key):
                self.assertIn(role_key, tactical_task_hook["keys"])
        self.assertIn(
            "CombatCommander::updateVoiTacticalNukeAction()",
            tactical_task_hook["secondary_function"],
        )
        self.assertIn(
            "CombatCommander::updateVoiExplicitAbilityAction()",
            tactical_task_hook["secondary_function"],
        )
        self.assertIn(
            "CombatCommander::ExecuteActions()",
            tactical_task_hook["secondary_function"],
        )
        for telemetry_field in (
            "TacticalTask.ability",
            "AbilityTask.active",
            "AbilityTask.task_id",
            "AbilityTask.ability",
            "AbilityTask.ability_policy",
            "AbilityTask.status",
            "AbilityTask.reason",
            "AbilityTask.phase",
            "AbilityTask.update_id",
            "AbilityTask.attempt_generation",
            "AbilityTask.submitted_attempt_generation",
            "AbilityTask.observed_accepted_frame",
            "AbilityTask.observed_accepted_attempt_generation",
            "AbilityTask.observed_accepted_evidence",
            "AbilityTask.terminal_attempt_generation",
            "AbilityTask.actor_tag",
            "AbilityTask.actor_type",
            "AbilityTask.target_tag",
            "AbilityTask.location_intent",
            "AbilityTask.target_x",
            "AbilityTask.target_y",
            "AbilityTask.target_anchor_x",
            "AbilityTask.target_anchor_y",
            "AbilityTask.target_anchor_distance",
            "AbilityTask.target_location_match",
            "AbilityTask.target_observation_frame",
            "AbilityTask.staging_x",
            "AbilityTask.staging_y",
            "AbilityTask.available_ghost_count",
            "AbilityTask.available_caster_count",
            "AbilityTask.planned_count",
            "AbilityTask.staging_command_issued_count",
            "AbilityTask.staging_command_frame",
            "AbilityTask.staging_arrival_frame",
            "AbilityTask.staging_issued_action",
            "AbilityTask.actor_home_distance",
            "AbilityTask.actor_max_home_distance",
            "AbilityTask.cast_attempted_count",
            "AbilityTask.cast_submitted_count",
            "AbilityTask.cast_submission_frame",
            "AbilityTask.cast_submitted_action",
            "AbilityTask.submitted_count",
            "AbilityTask.last_action",
            "AbilityTask.payload_count_at_submission",
            "AbilityTask.confirmation_state",
            "AbilityTask.confirmation_count",
            "AbilityTask.confirmation_frame",
            "AbilityTask.confirmation_effect",
            "AbilityTask.consumed_role_axis",
            "AbilityTask.consumed_ability_policy_axis",
            "AbilityTask.consumed_axes",
        ):
            with self.subTest(telemetry_field=telemetry_field):
                self.assertIn(
                    telemetry_field,
                    tactical_task_hook["telemetry_fields"],
                )
        self.assertIn(
            "exact update, task, ability, and attempt generation",
            tactical_task_hook["intended_effect"],
        )
        self.assertIn(
            "suppresses stale and irreversible duplicate submissions",
            tactical_task_hook["intended_effect"],
        )
        self.assertIn(
            "SC2 submission, observed acceptance, and concrete effect confirmation",
            tactical_task_hook["intended_effect"],
        )
        self.assertIn(
            "location target/staging coordinates and staging-command telemetry",
            tactical_task_hook["intended_effect"],
        )
        pending_keys = manifest["python_blackboard_emitted_but_not_consumed_by_current_cpp_patch"]
        self.assertIn("combat.pressure_window_frames", pending_keys)
        self.assertIn("squad.flank_bias", pending_keys)
        self.assertIn("emergency.prioritize_repair", pending_keys)
        self.assertNotIn("combat.kite_bias", pending_keys)
        self.assertNotIn("strategy.doctrine", pending_keys)
        self.assertNotIn("production.queue_biases.*", pending_keys)
        self.assertNotIn("production.addon_biases.*", pending_keys)
        self.assertNotIn("production.production_facility_biases.*", pending_keys)
        self.assertNotIn("production.tech_switch_urgency", pending_keys)
        self.assertNotIn("combat.target_priority_biases.*", pending_keys)
        self.assertNotIn("scope.army_group", pending_keys)
        self.assertNotIn("scope.unit_classes", pending_keys)
        self.assertNotIn("scope.max_units", pending_keys)
        self.assertNotIn("tactical_task.task_type", pending_keys)
        self.assertNotIn("tactical_task.ability", pending_keys)
        self.assertNotIn("tactical_task.production_targets", pending_keys)
        self.assertNotIn("building_tasks.*", pending_keys)
        self.assertNotIn("composition_requirements.*", pending_keys)
        self.assertNotIn("unit_roles.*", pending_keys)
        self.assertNotIn("route_intent.route_type", pending_keys)
        self.assertNotIn("target_intent.target_type", pending_keys)
        self.assertNotIn("scouting.scan_priority", pending_keys)
        self.assertNotIn("squad.reinforce_bias", pending_keys)
        self.assertNotIn("lifetime.mode", pending_keys)
        self.assertNotIn("lifetime.completion_state", pending_keys)

    def test_cpp_blackboard_header_is_header_only_and_uses_stdlib(self) -> None:
        header = (KIT_DIR / "voi_policy_blackboard.hpp").read_text()

        required_terms = (
            "#pragma once",
            "class PolicyBlackboard",
            "loadFromFile",
            "getFloat",
            "getBool",
            "isExpired",
            "isProtocolCompatible",
            'lifetimeMode == "until_cancelled"',
            'lifetimeMode == "standing_order"',
            "static_cast<std::uint32_t>(expiresAt)",
            "std::unordered_map",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, header)

        forbidden_terms = (
            "python_sc2",
            "s2client_api",
            "raw_action",
            "nlohmann",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, header)

    def test_readme_documents_runtime_wiring_and_local_smoke_boundary(self) -> None:
        readme = (KIT_DIR / "README.md").read_text()

        required_terms = (
            "GameCommander::onStart",
            "GameCommander::onFrame",
            "latest_modulation.kv",
            "combat.defend_bias",
            "emergency.force_retreat",
            "emergency.cancel_attacks",
            "combat.aggression",
            "MicroMachine managers",
            "local StarCraft II installation",
            "MIN_TELEMETRY_FRAME",
            "Connected to 127.0.0.1:8167",
            "WaitJoinGame finished successfully.",
            "create unit item=Marine result=1",
            "TERRAN_BARRACKS UnderConstruction",
            "Gas income:       67",
            "\"policy_active\":true",
            "pins the Terran strategy to `Terran_MarineRush`",
            "Invalid setup detected. | 0x0000000",
            "authoritative SC2 placement query",
            "gas-worker path-safety fallback",
            "environment-preserving `execve`",
            "outside Codex filesystem/network sandboxing",
            "VOI_SC2_CREATEGAME_MAP_DATA=1",
            "local_map.map_data",
            "ProductionManager::putImportantBuildOrderItemsInQueue()",
            "BuildingManager::assignWorkerToUnassignedBuilding(Building &, bool)",
            "patches/0068-all-terran-harass-capability-evidence.patch",
            "patches/0069-authoritative-battlefield-ownership-readiness.patch",
            "patches/0070-battlefield-projection-review-closure.patch",
            "patches/0071-battlefield-identity-transfer-integrity.patch",
            "patches/0072-atomic-telemetry-publication.patch",
            "patches/0073-contextual-transfer-choice-projection.patch",
            "patches/0074-autonomous-owner-composition-evidence.patch",
            "patches/0075-battlefield-review-closure.patch",
            "through `0075`",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, readme)

    def test_patch_bundle_contains_build_bridge_and_smoke_hardening(self) -> None:
        patch = _read_patch_text(PATCH_FILE)
        s2client_patch = _read_patch_text(S2CLIENT_PATCH_FILE)

        required_terms = (
            "target_link_libraries(MicroMachine ${SC2Api_LIBRARIES})",
            "file(GLOB_RECURSE LIBVOXELBOT_SOURCES",
            "#include \"voi_policy_blackboard.hpp\"",
            "void GameCommander::updateVoiPolicyBlackboard()",
            "void GameCommander::writeVoiTelemetry() const",
            "bool GameCommander::shouldSuppressRepeatedWorkerCommand",
            "bool CCBot::isInitialObservationReady() const",
            "void CCBot::initializeManagers()",
            "m_managersInitialized",
            "getVoiPolicyBool(\"emergency.force_retreat\", false)",
            "getVoiPolicyBool(\"emergency.cancel_attacks\", false)",
            "getVoiPolicyFloat(\"combat.commitment_level\", 0.0f)",
            "getVoiPolicyString(\"combat.attack_condition_override\", \"normal\")",
            "getVoiPolicyFloat(\"combat.retreat_patience_bias\", 0.0f)",
            "voiRetreatPatienceBias * 0.20f",
            "getVoiPolicyFloat(\"squad.contain_bias\", 0.0f)",
            "getVoiPolicyFloat(\"squad.reinforce_bias\", 0.0f)",
            "squadScoutIntervention",
            "getVoiScoutScopeStatus() == \"Consumed\"",
            "scouting.scout_priority,squad.squad_role_biases.marine_scout",
            "getVoiPolicyString(\"strategy.doctrine\", \"\")",
            "applyVoiDoctrineProductionBias",
            "queueVoiDoctrineItem",
            "production.queue_biases.TERRAN_FACTORY",
            "production.queue_biases.TERRAN_STARPORT",
            "production.queue_biases.TERRAN_SIEGETANK",
            "production.queue_biases.STARPORT_TECHLAB",
            "production.queue_biases.TERRAN_BANSHEE",
            "production.queue_biases.TERRAN_RAVEN",
            "production.queue_biases.TERRAN_BATTLECRUISER",
            "production.queue_biases.TERRAN_FUSIONCORE",
            "production.composition_biases.bio",
            "production.composition_biases.mech",
            "production.composition_biases.siege",
            "production.composition_biases.drop",
            "production.composition_biases.anti_air",
            "production.production_facility_biases.TERRAN_FACTORY",
            "production.production_facility_biases.TERRAN_FUSIONCORE",
            "tech.unit_biases.TERRAN_SIEGETANK",
            "tech.unit_biases.TERRAN_BANSHEE",
            "tech.unit_biases.TERRAN_RAVEN",
            "tech.unit_biases.TERRAN_BATTLECRUISER",
            "tech.structure_biases.TERRAN_FACTORY",
            "tech.structure_biases.TERRAN_FUSIONCORE",
            "voi doctrine action=",
            "last_doctrine_action",
            "last_doctrine_queue_item",
            "last_doctrine_evidence",
            "last_doctrine_requested_targets",
            "last_doctrine_blocked_reason",
            "last_doctrine_missing_prerequisites",
            "blocked_missing_prerequisite_or_producer",
            "queue_bias_starport_techlab",
            "queue_bias_banshee",
            "queue_bias_raven",
            "queue_bias_battlecruiser",
            "last_doctrine_update_id",
            "last_doctrine_fresh",
            "recordVoiActualProductionCommand",
            "actual_production_command_issued_count",
            "last_actual_production_command",
            "last_actual_production_command_item",
            "last_actual_production_command_update_id",
            "last_actual_production_command_frame",
            "recordVoiScoutCommand",
            "recordVoiScoutProgress",
            "getVoiScoutCommandIssuedCount",
            "getLastVoiScoutCommand",
            "last_actual_command",
            "last_actual_command_frame",
            "last_target_distance",
            "max_home_distance",
            "min_enemy_base_distance",
            "deep_scout_frame_count",
            "scout_enemy_base_deep_entry_move",
            "scout_unknown_far_start_location_move",
            "explicitVoiScout",
            "occupiedByEnemy ? 100000000.0f",
            "recordVoiDoctrineConsumptionIfRepresented",
            "Only queueVoiDoctrineItem() records consumption",
            "VOI doctrine bypassed pre-expand production cap",
            "policy_update_id",
            "strategy.doctrine",
            "production.queue_biases.*",
            "production.queue_biases.TERRAN_SUPPLYDEPOT",
            "economy.supply_buffer_bias",
            "production.composition_biases.*",
            "queue_bias_supply_depot",
            "economy_supply_buffer_bias",
            "supply_buffer",
            "getVoiPolicyInt(\"scope.min_units\", 0)",
            "getVoiPolicyInt(\"scope.max_units\", 0)",
            "getVoiPolicyString(\"scope.army_group\", \"\")",
            "getVoiPolicyString(\"scope.unit_classes\", \"\")",
            "getVoiPolicyString(\"scope.location_intent\", \"\")",
            "getVoiPolicyString(\"tactical_task.task_type\", \"\")",
            "getVoiPolicyString(\"tactical_task.unit_classes\", \"\")",
            "getVoiPolicyString(\"tactical_task.production_targets\", \"\")",
            "getVoiPolicyString(\"tactical_task.location_intent\", \"\")",
            "getVoiPolicyFloat(\"tactical_task.priority\", 0.0f)",
            "getVoiPolicyInt(\"tactical_task.min_units\", 0)",
            "getVoiPolicyInt(\"tactical_task.max_units\", 0)",
            "resolveVoiBuildingTaskPlacement",
            "offsetVoiBuildingTaskAnchor",
            "resolveVoiBuildingTaskAnchor",
            "recordVoiBuildingTaskPlacement",
            "building_tasks.0.building_type",
            "building_tasks.0.placement_intent",
            "building_tasks.0.anchor",
            "building_tasks.0.offset_direction",
            "building_tasks.0.target_position",
            "building_tasks.0.allow_nearest_valid_fallback",
            "getVoiPolicyBool(\"building_tasks.0.allow_nearest_valid_fallback\", true)",
            "voiBuildingTaskBlocksFallback",
            "buildingLocation = CCTilePosition();",
            "BuildingManager must place exactly at target_position or reject without nearest fallback",
            "exact placement invalid and nearest fallback disabled",
            "\\\"resolved_position\\\":\\\"",
            "\\\"TacticalTask\\\"",
            "\\\"BuildingTask\\\"",
            "\\\"status\\\":\\\"",
            "\\\"consumed_by\\\":\\\"",
            "VOI building_task placement anchor selected",
            "tacticalTaskStatus",
            "tacticalTaskConsumedBy",
            "scout_with_units",
            "pressure_with_main_army",
            "sustain_production",
            "tech_transition",
            "expand_or_land_command_center",
            "getVoiPolicyFloat(\"scouting.scout_priority\", 0.0f)",
            "getVoiPolicyFloat(\"squad.squad_role_biases.marine_scout\", 0.0f)",
            "voiUnitMatchesCompositionToken",
            "composition_requirements.",
            "unit_roles.",
            "unit_roles.*",
            "unit_roles.*.ability_policy",
            "const std::string roleName = m_bot.Commander().getVoiPolicyString(\"unit_roles.\"",
            "voiRoleForUnit",
            "voiAbilityPolicyForUnit",
            "voiCanTargetYamato",
            "DisplayType::Visible",
            "VoiRoleTankSiege",
            "VoiRoleVikingAirPriority",
            "VoiRoleBansheeCloak",
            "VoiRoleBattlecruiserYamato",
            "Role-biased action accepted into action plan",
            "\\\"UnitRoleTask\\\"",
            "\\\"requested_count\\\":",
            "\\\"available_count\\\":",
            "\\\"attempted_count\\\":",
            "\\\"executed_count\\\":",
            "Role-assigned unit action reached SC2 command issue path",
            "alreadyRequired",
            "route_intent.route_type",
            "target_intent.target_type",
            "\\\"CompositionTask\\\"",
            "Missing composition units",
            "Requested composition assigned to MainAttack",
            "voiPartialCompositionReady",
            "keptForType",
            "tacticalPressureTask && !exactCompositionPressureTask && voiScopeMaxUnits",
            "composition_requirements.*,unit_roles.*,unit_roles.*.ability_policy,route_intent.route_type,target_intent.target_type",
            "scope.unit_classes",
            "squad.squad_role_biases.marine_scout",
            "scout_scope_status",
            "scout_scope_reason",
            "scout_scope_assigned_unit_count",
            "action_plan_count",
            "actual_command_issued_count",
            "action_skipped_count",
            "last_planned_action",
            "last_issued_action",
            "voiEngageMarginDelta",
            "but it must not bypass the combat simulation safety gate.",
            "voiRelaunchMargin",
            "voiTargetPriorityScore",
            "combat.target_priority_biases.worker_line",
            "consumed_axes",
            "BaseLocation * closestStartBase = nullptr",
            "adoptAsPlayerStartLocation(Players::Self, selfDepot)",
            "closeToResourceCenter",
            "closeToMineralCenter",
            "isVoiDepotFlowProtectedPlacement",
            "getVoiNearbyResourceCenter",
            "voiDistanceSqToSegment",
            "const bool forceVoiScout = getVoiPolicyFloat(\"scouting.scout_priority\", 0.0f) >= 0.35f",
            "NoScoutOn2PlayersMap && enemyBaseLocation != nullptr && !forceVoiScout",
            "workerSplitBase = m_bot.Bases().getPlayerStartingBaseLocation(Players::Self)",
            "Skipping frame1 worker split: no occupied or starting self base location.",
            "Skipping frame1 worker split: no valid resource depot.",
            "Unit depot = ressourceDepot",
            "ownCompletedRefinery",
            "geyser.getPlayer() == Players::Self",
            "!building.type.isRefinery() && !building.type.isAddon()",
            "m_bot.GetCurrentFrame() < 5000",
            "canTrustOpeningWallPlacement",
            "trusting valid uncontested buildable placement.",
            "Root fix for repeated addon cancellation",
            "addon placement is validated by producer state and relocation logic",
            "building.type.isAddon()",
            "shouldUseVoiDirectAddonCommand",
            "VOI addon command issued once with BuildingManager task retained for SC2 construction feedback",
            "addonFootprintOccupied",
            "!addonFootprintOccupied && !b.buildCommandGiven",
            "falling through to lift and relocation logic",
            "recordVoiActualProductionCommand(b.type, \"addon_build_command\")",
            "voiRequestedCompositionCount",
            "voiRepresentedUnitCount",
            "voiCompositionRequirementSatisfied",
            "producer.getUnitPtr()->orders",
            "!tankCompositionSatisfied",
            "!vikingCompositionSatisfied",
            "BUILD_TECHLAB_FACTORY",
            "BUILD_TECHLAB_STARPORT",
            "BUILD_REACTOR_FACTORY",
            "BUILD_REACTOR_STARPORT",
            "const float effectiveRavenBias = ravenBias;",
            "Supply provider recovery queued after supply block.",
            "m_queue.queueAsHighestPriority(supplyProviderType, false)",
            "Path to completed refinery is not safe; assigning gas worker with refinery fallback.",
            "handleNonBunkerGasWorkers",
            "alreadyReturningToDepot",
            "alreadyGasHarvestOrder",
            "isInsideGeyser(worker)",
            "worker.rightClick(geyser)",
            "\\\"WorkerManager\\\":{",
            "repeat_order_guard_active",
            "repeat_order_suppressed_count",
            "self_position_command_block_count",
            "root_cause_status",
            "root_cause_reason",
            "setVoiWorkerCommandReason",
            "consumeVoiWorkerCommandReason",
            "voiHasSamePositionOrder",
            "voiHasSamePositionOrderTarget",
            "redundant_existing_position_order",
            "Root fix for repeated SCV mineral commands",
            "alreadyInMineralCycle",
            "currentJob->second == WorkerJobs::Minerals",
            "currentDepot->second.getTag() == jobUnit.getTag()",
            "const bool idleSpotIsUseful = Util::DistSq(worker.getPosition(), idlePos) > 1.0f",
            "const bool depotFallbackIsUseful = Util::DistSq(worker.getPosition(), base->getDepotPosition()) > 1.0f",
            "m_lastVoiScoutMoveFrame",
            "m_lastVoiScoutMoveTarget",
            "const float targetReachedDistanceSq = 1.0f",
            "already ordered",
            "voiIsMobileAttackUnit",
            "autonomousCombatScoutScope",
            "Autonomous combat scout assigned because enemy start is unexplored",
            "autonomousAttackReady",
            "Autonomous combat threshold met",
            "main_attack_max_home_distance",
            "scout_max_home_distance",
            "workers.repeat_order_guard_frames",
            "m_bot->Commander().shouldSuppressRepeatedWorkerCommand(m_unit, sc2::ABILITY_ID::SMART",
            "bot.Commander().shouldSuppressRepeatedWorkerCommand(unit, sc2::ABILITY_ID::MOVE",
            "VOI_SC2_EXTRA_ARGS",
            "ScopedVoiEnvironmentStripper",
            "VOI_MICROMACHINE_BLACKBOARD_DIR",
            "stripVoiEnvForSc2Child",
            "PROTOSS_OBSERVERSIEGEMODE",
            "coordinator.SetRawAffectsSelection",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)
        self.assertNotIn("bool commandCooldownElapsed", patch)
        self.assertNotIn("bool addonFootprintBuildable", patch)
        self.assertTrue((KIT_DIR / "voi_policy_blackboard.hpp").is_file())
        self.assertNotIn("-\t\t\t\t\t\t\t++neighborsBaseLocation[bl];", patch)
        self.assertIn(
            "return lhs.x < rhs.x || (lhs.x == rhs.x && lhs.y < rhs.y);",
            patch,
        )
        self.assertIn(
            "-\t\treturn lhs.x < rhs.x || lhs.x == rhs.y && lhs.y < rhs.y;",
            patch,
        )
        self.assertNotIn(
            " \t\treturn lhs.x < rhs.x || lhs.x == rhs.y && lhs.y < rhs.y;",
            patch,
        )
        self.assertNotIn(
            "+\t\treturn lhs.x < rhs.x || lhs.x == rhs.y && lhs.y < rhs.y;",
            patch,
        )
        self.assertNotIn("VOI addon direct command bypassed exploration gate", patch)
        self.assertNotIn(
            "VOI addon direct command bypassed conservative placement precheck",
            patch,
        )
        self.assertNotIn(
            "const float effectiveRavenBias = std::max(ravenBias, compositionAntiAirBias);",
            patch,
        )
        self.assertNotIn(
            "m_lastVoiScoutMoveReason == reason",
            patch,
            "Scout duplicate prevention must be target-based, not reason-dependent.",
        )
        for term in (
            "extern char **environ",
            "#include <sys/wait.h>",
            "FindProcessByPathAndPort",
            "waitpid(p, &status, 0)",
            'std::strncmp(*env, "VOI_", 4) == 0',
            "environment_list.data()",
            "execve(launcher_path.c_str(), &char_list[0], environment_list.data())",
            "data.size() != static_cast<size_t>(width * height)",
            "target_compile_options(civetweb-c-library PRIVATE -Wno-unknown-warning-option -Wno-error=unknown-warning-option)",
            "add_executable(voi_bootstrap_probe src/voi_bootstrap_probe.cc)",
            "target_link_libraries(voi_bootstrap_probe sc2api sc2lib sc2utils)",
            "voi-s2client-bootstrap-probe/v1",
            "bootstrap_no_start_units",
            "resource_depot_count",
            "self_worker_count",
            "options->set_show_cloaked(true)",
            "options->set_raw_affects_selection(true)",
            "setup.type == PlayerType::Participant || setup.type == PlayerType::Computer",
            "setup.type == PlayerType::Computer",
            "VOI_SC2_CREATEGAME_MAP_DATA",
            "VoiAttachCreateGameMapData",
            "local_map->set_map_data(data)",
            "raw_observation_present",
            "raw_self_worker_count",
            "raw_resource_depot_count",
            "obs->GetRawObservation()",
            "available_index_ = {0, 0}",
            "Skipping unit with unsupported display type",
            "Skipping unit with unsupported alliance",
            "Coercing unsupported cloak state to Unknown",
        ):
            with self.subTest(term=term):
                self.assertIn(term, s2client_patch)

    def test_embedded_build_identity_patch_exposes_side_effect_free_query(
        self,
    ) -> None:
        patch = _read_patch_text(EMBEDDED_BUILD_INPUT_IDENTITY_PATCH_FILE)

        self.assertIn('#include "voi_build_identity.hpp"', patch)
        self.assertIn("--voi-build-input-identity", patch)
        self.assertIn("VOI_BUILD_INPUT_IDENTITY", patch)
        self.assertIn("return 0;", patch)

    def test_patch_records_requeued_doctrine_items_as_existing_queue_evidence(self) -> None:
        patch = _read_patch_text(PATCH_FILE)

        self.assertNotIn("requeued_highest", patch)
        self.assertNotIn("requeued_blocking", patch)
        self.assertIn('recordVoiDoctrineConsumption(type, action, "queued_existing");', patch)

    def test_addon_relocation_bypasses_generic_build_position_exploration(self) -> None:
        patch = _read_patch_text(PATCH_FILE)

        self.assertIn(
            "if (!b.type.isAddon() && !isBuildingPositionExplored(b))",
            patch,
        )
        self.assertNotIn(
            "\n             if (!isBuildingPositionExplored(b))",
            patch,
        )

    def test_addon_relocation_does_not_claim_build_command_before_ability(self) -> None:
        patch = _read_patch_text(PATCH_FILE)

        self.assertIn(
            "if (b.type.isAddon())\n \t\t\t\t\t{\n+\t\t\t\t\t\tsetCommandGiven = false;",
            patch,
        )
        self.assertIn(
            'recordVoiActualProductionCommand(b.type, "addon_build_command");'
            "\n+\t\t\t\t\t\t\t\t\t\tsetCommandGiven = true;"
            "\n+\t\t\t\t\t\t\t\t\t\tb.lastOrderFrame = m_bot.GetCurrentFrame();",
            patch,
        )

    def test_explicit_tech_bias_bypasses_pre_expand_production_gate(self) -> None:
        patch = _read_patch_text(PATCH_FILE)

        for signal in (
            'production.queue_biases.TERRAN_FACTORY',
            'production.queue_biases.FACTORY_TECHLAB',
            'production.queue_biases.TERRAN_SIEGETANK',
            'production.queue_biases.TERRAN_STARPORT',
            'production.queue_biases.TERRAN_VIKINGFIGHTER',
            'production.composition_biases.siege',
            'production.composition_biases.anti_air',
        ):
            with self.subTest(signal=signal):
                self.assertIn(signal, patch)
        self.assertIn(
            "voiDoctrineRequestsTechTransition(m_bot, MetaTypeEnum::Factory)",
            patch,
        )
        self.assertIn(
            "voiDoctrineRequestsTechTransition(m_bot, MetaTypeEnum::Starport)",
            patch,
        )
        self.assertIn(
            "currentItem.type == MetaTypeEnum::Factory && voiFactoryTransitionRequested",
            patch,
        )
        self.assertIn(
            "currentItem.type == MetaTypeEnum::Starport && voiStarportTransitionRequested",
            patch,
        )
        self.assertIn(
            "taskTechTransition && (taskTargetsFactoryTechLab || taskTargetsSiegeTank)",
            patch,
        )
        self.assertNotIn(
            "const bool wantsFactoryTechLab = taskTechTransition ||",
            patch,
        )

    def test_patch_keeps_real_build_and_continuity_commands_from_live_blockers(self) -> None:
        patch = _read_patch_text(PATCH_FILE)

        self.assertIn("&& !buildPositionCommand", patch)
        self.assertIn("explicitVoiSupplyRequest", patch)
        self.assertIn("criticalSupplyNeed", patch)
        self.assertIn("worker_continuity", patch)
        self.assertIn("standingProductionNeedsRoom", patch)
        self.assertIn(
            "queueVoiDoctrineItem(MetaTypeEnum::CommandCenter, \"expand_macro\", true, !(explicitSupplyBufferNeeded || workerProductionBias > 0.25f || effectiveMarineBias > 0.25f))",
            patch,
        )

    def test_completed_expansion_command_center_guard_does_not_require_placement_query(self) -> None:
        patch = _read_patch_text(PATCH_FILE)
        body = patch.split("+bool canTrustAssignedVoiExpansionDepot", 1)[1].split(
            "+}\n+}\n+\n BuildingManager::BuildingManager",
            1,
        )[0]

        self.assertNotIn("findVoiExpansionPlacementCommandPosition", body)
        self.assertIn("building.buildingUnit.isValid()", body)
        self.assertIn("building.buildingUnit.isCompleted()", body)
        self.assertIn("building.buildingUnit.isFlying()", body)
        self.assertIn("building.buildingUnit.getAPIUnitType()", body)
        self.assertIn("A completed CommandCenter occupies its own footprint", body)
        self.assertIn("GetCombatInfluenceOnTile(building.finalPosition", body)
        self.assertEqual(
            1,
            patch.count("canTrustAssignedVoiExpansionDepot(m_bot, b)"),
            "completed CommandCenter trust must not be used by pre-build placement paths",
        )
        self.assertGreaterEqual(
            patch.count("canTrustVoiExpansionDepotPlacement(m_bot, b.type, b.finalPosition)"),
            4,
            "pre-build expansion paths must keep using placement validation",
        )

    def test_macos_scripts_document_reproducible_build_smoke_and_soak(self) -> None:
        build_script = BUILD_SCRIPT.read_text()
        probe_script = PROBE_SCRIPT.read_text()
        smoke_script = SMOKE_SCRIPT.read_text()
        soak_script = SOAK_SCRIPT.read_text()
        soak_matrix_script = SOAK_MATRIX_SCRIPT.read_text()
        strategy_matrix_script = STRATEGY_MATRIX_SCRIPT.read_text()
        local_soak_workflow = (
            REPO_ROOT
            / ".github"
            / "workflows"
            / "micromachine-local-soak.yml"
        ).read_text()
        smoke_guide = (
            REPO_ROOT / "docs" / "sc2-smoke-test.md"
        ).read_text()
        production_evidence_module = (
            REPO_ROOT
            / "starcraft_commander"
            / "micromachine_production_evidence.py"
        ).read_text()
        soak_classifier_module = (
            REPO_ROOT / "starcraft_commander" / "micromachine_soak.py"
        ).read_text()

        self.assertIn(
            'ROOT_DIR="${ROOT_DIR:-/private/tmp/voi-micromachine-runtime}"',
            build_script,
        )
        self.assertIn(
            'OPERATION_EDIT_OWNERSHIP_HANDOFF_PATCH_FILE="${REPO_ROOT}/'
            'integrations/micromachine/patches/'
            '0060-operation-edit-ownership-handoff.patch"',
            build_script,
        )
        self.assertIn(
            'OPERATION_EDIT_REVIEW_CLOSURE_PATCH_FILE="${REPO_ROOT}/'
            'integrations/micromachine/patches/'
            '0061-operation-edit-review-closure.patch"',
            build_script,
        )
        self.assertIn(
            'OPERATION_TRANSFER_ATOMIC_ADMISSION_PATCH_FILE="${REPO_ROOT}/'
            'integrations/micromachine/patches/'
            '0062-operation-transfer-atomic-admission.patch"',
            build_script,
        )
        self.assertIn(
            'OPERATION_TRANSFER_RUNTIME_PRESERVATION_PATCH_FILE="${REPO_ROOT}/'
            'integrations/micromachine/patches/'
            '0063-operation-transfer-runtime-preservation.patch"',
            build_script,
        )
        self.assertIn(
            'OPERATION_TRANSFER_TRANSACTIONAL_CLOSURE_PATCH_FILE="${REPO_ROOT}/'
            'integrations/micromachine/patches/'
            '0064-operation-transfer-transactional-closure.patch"',
            build_script,
        )
        self.assertIn(
            'OPERATION_TRANSFER_FINAL_REVIEW_CLOSURE_PATCH_FILE="${REPO_ROOT}/'
            'integrations/micromachine/patches/'
            '0065-operation-transfer-final-review-closure.patch"',
            build_script,
        )
        self.assertIn(
            'OPERATION_TRANSFER_IDEMPOTENCE_ACTIVE_EVIDENCE_PATCH_FILE="${REPO_ROOT}/'
            'integrations/micromachine/patches/'
            '0066-operation-transfer-idempotence-and-active-evidence.patch"',
            build_script,
        )
        self.assertIn(
            'RUNTIME_CONVERGENCE_DEFENSE_PLACEMENT_INFORMATION_PATCH_FILE='
            '"${REPO_ROOT}/integrations/micromachine/patches/'
            '0067-runtime-convergence-defense-placement-information.patch"',
            build_script,
        )
        transfer_patch_apply = build_script.index(
            'apply --recount --ignore-space-change --whitespace=nowarn '
            '"${OPERATION_TRANSFER_IDEMPOTENCE_ACTIVE_EVIDENCE_PATCH_FILE}"'
        )
        convergence_patch_check = build_script.index(
            'apply --recount --check --ignore-space-change '
            '--whitespace=nowarn '
            '"${RUNTIME_CONVERGENCE_DEFENSE_PLACEMENT_INFORMATION_PATCH_FILE}"'
        )
        convergence_patch_apply = build_script.index(
            'apply --recount --ignore-space-change --whitespace=nowarn '
            '"${RUNTIME_CONVERGENCE_DEFENSE_PLACEMENT_INFORMATION_PATCH_FILE}"'
        )
        self.assertLess(transfer_patch_apply, convergence_patch_check)
        self.assertLess(convergence_patch_check, convergence_patch_apply)
        self.assertIn(
            'apply --recount --check --ignore-space-change '
            '--whitespace=nowarn '
            '"${OPERATION_EDIT_OWNERSHIP_HANDOFF_PATCH_FILE}"',
            build_script,
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-operation-edit-ownership-handoff-patch"
            ),
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-operation-edit-review-closure-patch"
            ),
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-operation-transfer-atomic-admission-patch"
            ),
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-operation-transfer-runtime-preservation-patch"
            ),
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-operation-transfer-transactional-closure-patch"
            ),
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-operation-transfer-final-review-closure-patch"
            ),
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-operation-transfer-idempotence-active-evidence-patch"
            ),
        )
        self.assertEqual(
            2,
            build_script.count(
                "--micromachine-runtime-convergence-defense-placement-information-patch"
            ),
        )
        self.assertIn(
            '"${CTEST_COMMAND}" --test-dir "${MICROMACHINE_BUILD_DIR}" '
            "--output-on-failure",
            build_script,
        )
        for script_name, script in (
            ("probe", probe_script),
            ("smoke", smoke_script),
            ("soak", soak_script),
        ):
            with self.subTest(script=script_name, contract="portable SC2 discovery"):
                self.assertIn("discover_sc2_root()", script)
                self.assertIn(
                    'local configured="${SC2_ROOT:-${SC2PATH:-}}"',
                    script,
                )
                self.assertIn(
                    '"${HOME}/Desktop/StarCraft2/StarCraft II"',
                    script,
                )
                self.assertIn('"/Applications/StarCraft II"', script)
                self.assertIn(
                    '"${HOME}/Applications/StarCraft II"',
                    script,
                )
                self.assertNotIn("/Users/", script)
        self.assertIn(
            'sc2_root="${SC2_ROOT:-${SC2PATH:-}}"',
            local_soak_workflow,
        )
        self.assertIn(
            "/private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api",
            local_soak_workflow,
        )
        self.assertNotIn("/Users/", local_soak_workflow)
        self.assertNotIn("Base96883", local_soak_workflow)
        self.assertIn("SC2_ROOT", smoke_guide)
        self.assertIn("SC2PATH", smoke_guide)
        self.assertNotIn("/Users/", smoke_guide)
        for script_name, script in (
            ("smoke", smoke_script),
            ("soak", soak_script),
        ):
            with self.subTest(script=script_name, contract="default patched MicroMachine root"):
                self.assertIn(
                    f'MICROMACHINE_DIR="${{MICROMACHINE_DIR:-{DEFAULT_MICROMACHINE_DIR}}}"',
                    script,
                )
                self.assertIn(
                    'MICROMACHINE_BUILD_DIR="${MICROMACHINE_BUILD_DIR:-${MICROMACHINE_DIR}/build-latest-api}"',
                    script,
                )
                self.assertIn(
                    '[[ "${SC2_EXECUTABLE}" != "${SC2_BATTLENET_EXECUTABLE}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]',
                    script,
                )
                self.assertIn(
                    'elif [[ -z "${VOI_SC2_EXTRA_ARGS:-}" && "${SC2_USE_RUNTIME_DIR_ARGS}" == "1" ]]; then',
                    script,
                )
        for term in (
            "--fresh-live-session",
            'SMOKE_FRESH_LIVE_SESSION="${SMOKE_FRESH_LIVE_SESSION:-0}"',
            '"${BLACKBOARD_DIR}/latest_modulation.json"',
            '"${BLACKBOARD_DIR}/latest_modulation.kv"',
            '"${BLACKBOARD_DIR}/latest_modulation_compile_result.json"',
            "fresh live session cleared detached tactical command state",
        ):
            with self.subTest(smoke_fresh_session_term=term):
                self.assertIn(term, smoke_script)
        for term in (
            "def first_modulation_issued_at_frame(update_id):",
            "return min(int(entry.get(\"issued_at_frame\", 0) or 0) for entry in candidates)",
            "aggressive_first_issued_at_frame",
        ):
            with self.subTest(smoke_republished_profile_epoch_term=term):
                self.assertIn(term, smoke_script)
        self.assertNotIn(
            "return max(int(entry.get(\"issued_at_frame\", 0) or 0) for entry in candidates)",
            smoke_script,
        )
        for term in (
            "def operation(operation_id, task_type, location, route_type, target_type):",
            '"issued_at_frame": frame',
            "has_expected_strategy_profile_evidence()",
            "find_causal_production_evidence",
            "expected_production_pairs",
            "operation_restore_ready()",
            "operation_production_baseline()",
            'phase == "cancelled_baseline"',
            "if combat is None:",
            "SMOKE_RESTORE_UPDATE_ID",
            "SMOKE_STRATEGY_EVIDENCE_UPDATE_ID",
            "OPERATION_RESTORE_ISSUED_FRAME",
            "OPERATION_CANCEL_MAIN_ATTACK_COMMAND_BASELINE",
            "OPERATION_CANCEL_MAIN_ATTACK_ACTION_FRAME_BASELINE",
            "OPERATION_CANCEL_OWNER_RELEASE_COUNT_BASELINE",
            "OPERATION_CANCEL_QUEUE_PURGE_COUNT_BASELINE",
            "OPERATION_CANCEL_QUEUE_PURGE_FRAME_BASELINE",
            'OPERATION_RESTORE_ISSUED_FRAME="${OPERATION_CANCEL_TELEMETRY_FRAME}"',
            "latest_restore_frame > OPERATION_RESTORE_ISSUED_FRAME",
            "first_restore_consumed_frame",
            "command_count > command_baseline",
            "command_frame > action_frame_baseline",
            "command_frame >= first_restore_consumed_frame",
            "current_home_distance >= minimum_home_distance",
            "restore_initial_positions",
            "reference_positions",
            '"production_targets": (',
            '["TERRAN_MARINE", "TERRAN_SIEGETANK"]',
            '"unit_type": "TERRAN_MARINE"',
            "operation_owned_queue_item_count",
            "operation_production_owner_release_count",
            "owner_release_count <= owner_release_count_baseline",
            "queue_purge_count <= queue_purge_count_baseline",
            "operation_production_queue_purge_events",
            "matching_purge_event is None",
            "purge_event_generation == expected_generation",
            "purge_event_frame > queue_purge_frame_baseline",
            "purge_event_removed_count > 0",
            "type(operation_owned_queue_item_count) is not int",
            "post_cancel_displacement",
            '"represented_satisfied"',
            "production_evidence_update_id",
            "latest_cancel_frame",
            "post-cancel MainAttack command and movement evidence",
        ):
            with self.subTest(operation_restore_proof_term=term):
                self.assertIn(term, smoke_script)
        self.assertNotIn(
            "restore_operational_without_production",
            smoke_script,
        )
        self.assertNotIn(
            "production_already_satisfied",
            smoke_script,
        )
        self.assertNotIn(
            "already_fulfilled_doctrine_seen",
            smoke_script,
        )
        self.assertNotIn(
            'SMOKE_STRATEGY_EVIDENCE_UPDATE_ID="${SMOKE_RESTORE_UPDATE_ID}"',
            smoke_script,
        )
        self.assertNotIn(
            'cancelled_production.get("operation_owned_queue_item_count", -1) or -1',
            smoke_script,
        )
        for term in (
            "for _ in range(8):",
            "except (json.JSONDecodeError, OSError, TypeError, ValueError):",
            'if [[ -z "${current_telemetry_frame}" ]]; then',
        ):
            with self.subTest(smoke_stable_telemetry_frame_term=term):
                self.assertIn(term, smoke_script)
        for term in (
            "type(last_trace_frame_value) is not int",
            "last_trace_frame < 0",
            "worker_trace_frame < 0",
            "latest_payload_frame - last_trace_frame > 4096",
        ):
            with self.subTest(worker_trace_frame_contract=term):
                self.assertIn(term, smoke_script)
        self.assertIn(
            f'SOAK_MATRIX_DEFAULT_BUILD_DIR="${{MICROMACHINE_BUILD_DIR:-{DEFAULT_MICROMACHINE_BUILD_DIR}}}"',
            soak_matrix_script,
        )

        for term in (
            "https://github.com/Blizzard/s2client-api",
            "https://github.com/RaphaelRoyerRivard/MicroMachine",
            "0001-macos-latest-s2client-policy-blackboard.patch",
            "0002-live-tactical-operation-fixes.patch",
            "0003-production-live-qa-blockers.patch",
            "0004-live-operation-state-machine.patch",
            "0005-addon-relocation-recovery.patch",
            "0006-grounded-addon-candidate-fix.patch",
            "0007-guaranteed-producer-grounding.patch",
            "0008-emergency-land-query-fallback.patch",
            "0009-grounded-production-and-observed-targeting.patch",
            "0010-exact-composition-production-progress.patch",
            "0011-production-resource-operation-persistence.patch",
            "0012-live-operation-unblock.patch",
            "0013-stable-flank-stage-latch.patch",
            "0014-production-staging-and-observed-operation.patch",
            "0015-addon-query-footprint-validation.patch",
            "0016-authoritative-addon-placement-query.patch",
            "0017-authoritative-addon-execution.patch",
            "0018-continuous-army-macro.patch",
            "0019-continuous-army-economy-scaling.patch",
            "0020-standing-composition-reinforcement-waves.patch",
            "0021-offensive-sweep-self-base-exclusion.patch",
            "0022-bounded-placement-query-cache.patch",
            "0023-production-facility-stability-and-tank-recovery.patch",
            "0024-balanced-composition-wave-production.patch",
            "0025-exact-composition-production-unblock.patch",
            "0026-continuous-combat-production-relaunch.patch",
            "0027-resource-throughput-and-expansion-backoff.patch",
            "0028-startup-telemetry-initialization.patch",
            "0029-gas-worker-completion-and-cap.patch",
            "0030-stable-offensive-sweep-target.patch",
            "0031-adaptive-support-composition.patch",
            "0032-operation-scoped-adaptive-combat-closure.patch",
            "0033-review-closure-operation-identity-and-full-composition.patch",
            "0034-semantic-operation-production-closure.patch",
            "0035-adaptive-pressure-stable-operation-key.patch",
            "0036-tactical-nuke-command-hierarchy.patch",
            "0037-location-intent-target-lock.patch",
            "0038-explicit-terran-ability-execution.patch",
            "0039-explicit-scout-command-epoch.patch",
            "0040-standing-production-continuity-closure.patch",
            "0041-explicit-ability-caster-production-priority.patch",
            "0042-explicit-ability-observation-confirmation.patch",
            "0043-explicit-ability-production-isolation.patch",
            "0044-explicit-ability-attempt-lifecycle.patch",
            "0045-explicit-ability-review-closure.patch",
            "0046-authoritative-addon-runtime-clearance.patch",
            "0047-banshee-unit-specific-cloak-command.patch",
            "0048-allied-cloak-observation-confirmation.patch",
            "0049-explicit-ability-caster-ownership.patch",
            "0050-explicit-ability-staging-single-flight.patch",
            "0051-all-terran-combat-scouts.patch",
            "0052-parallel-operations-ingame-hud.patch",
            "0053-parallel-operation-lifecycle-review-closure.patch",
            "0054-authoritative-parallel-operation-lifecycle.patch",
            "0055-operation-production-ownership-and-restore-proof.patch",
            "0056-embedded-build-input-identity.patch",
            "0072-atomic-telemetry-publication.patch",
            "0073-contextual-transfer-choice-projection.patch",
            "0074-autonomous-owner-composition-evidence.patch",
            "0075-battlefield-review-closure.patch",
            "0001-s2client-macos-launchservices.patch",
            "OPERATION_STATE_PATCH_FILE",
            "ADDON_RECOVERY_PATCH_FILE",
            "GROUNDED_ADDON_CANDIDATE_PATCH_FILE",
            "GUARANTEED_PRODUCER_GROUNDING_PATCH_FILE",
            "EMERGENCY_LAND_QUERY_FALLBACK_PATCH_FILE",
            "GROUNDED_PRODUCTION_OBSERVED_TARGETING_PATCH_FILE",
            "EXACT_COMPOSITION_PRODUCTION_PROGRESS_PATCH_FILE",
            "PRODUCTION_RESOURCE_OPERATION_PERSISTENCE_PATCH_FILE",
            "LIVE_OPERATION_UNBLOCK_PATCH_FILE",
            "STABLE_FLANK_STAGE_LATCH_PATCH_FILE",
            "PRODUCTION_STAGING_OBSERVED_OPERATION_PATCH_FILE",
            "ADDON_QUERY_FOOTPRINT_VALIDATION_PATCH_FILE",
            "AUTHORITATIVE_ADDON_PLACEMENT_QUERY_PATCH_FILE",
            "AUTHORITATIVE_ADDON_EXECUTION_PATCH_FILE",
            "CONTINUOUS_ARMY_MACRO_PATCH_FILE",
            "CONTINUOUS_ARMY_ECONOMY_SCALING_PATCH_FILE",
            "STANDING_COMPOSITION_REINFORCEMENT_WAVES_PATCH_FILE",
            "OFFENSIVE_SWEEP_SELF_BASE_EXCLUSION_PATCH_FILE",
            "BOUNDED_PLACEMENT_QUERY_CACHE_PATCH_FILE",
            "PRODUCTION_FACILITY_STABILITY_TANK_RECOVERY_PATCH_FILE",
            "BALANCED_COMPOSITION_WAVE_PRODUCTION_PATCH_FILE",
            "EXACT_COMPOSITION_PRODUCTION_UNBLOCK_PATCH_FILE",
            "CONTINUOUS_COMBAT_PRODUCTION_RELAUNCH_PATCH_FILE",
            "RESOURCE_THROUGHPUT_EXPANSION_BACKOFF_PATCH_FILE",
            "STARTUP_TELEMETRY_INITIALIZATION_PATCH_FILE",
            "GAS_WORKER_COMPLETION_CAP_PATCH_FILE",
            "STABLE_OFFENSIVE_SWEEP_TARGET_PATCH_FILE",
            "ADAPTIVE_SUPPORT_COMPOSITION_PATCH_FILE",
            "OPERATION_SCOPED_ADAPTIVE_COMBAT_CLOSURE_PATCH_FILE",
            "REVIEW_CLOSURE_OPERATION_IDENTITY_FULL_COMPOSITION_PATCH_FILE",
            "SEMANTIC_OPERATION_PRODUCTION_CLOSURE_PATCH_FILE",
            "ADAPTIVE_PRESSURE_STABLE_OPERATION_KEY_PATCH_FILE",
            "TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH_FILE",
            "LOCATION_INTENT_TARGET_LOCK_PATCH_FILE",
            "EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH_FILE",
            "EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH_FILE",
            "STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH_FILE",
            "EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH_FILE",
            "EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH_FILE",
            "EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH_FILE",
            "EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH_FILE",
            "EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH_FILE",
            "AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH_FILE",
            "BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH_FILE",
            "ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH_FILE",
            "EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH_FILE",
            "EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH_FILE",
            "ALL_TERRAN_COMBAT_SCOUTS_PATCH_FILE",
            "PARALLEL_OPERATIONS_INGAME_HUD_PATCH_FILE",
            "PARALLEL_OPERATION_LIFECYCLE_REVIEW_CLOSURE_PATCH_FILE",
            "AUTHORITATIVE_PARALLEL_OPERATION_LIFECYCLE_PATCH_FILE",
            "OPERATION_PRODUCTION_OWNERSHIP_RESTORE_PROOF_PATCH_FILE",
            "EMBEDDED_BUILD_INPUT_IDENTITY_PATCH_FILE",
            "ATOMIC_TELEMETRY_PUBLICATION_PATCH_FILE",
            "CONTEXTUAL_TRANSFER_CHOICE_PROJECTION_PATCH_FILE",
            "AUTONOMOUS_OWNER_COMPOSITION_EVIDENCE_PATCH_FILE",
            "BATTLEFIELD_REVIEW_CLOSURE_PATCH_FILE",
            "--micromachine-explicit-ability-production-isolation-patch",
            "--micromachine-explicit-ability-attempt-lifecycle-patch",
            "--micromachine-explicit-ability-review-closure-patch",
            "--micromachine-authoritative-addon-runtime-clearance-patch",
            "--micromachine-banshee-unit-specific-cloak-command-patch",
            "--micromachine-allied-cloak-observation-confirmation-patch",
            "--micromachine-explicit-ability-caster-ownership-patch",
            "--micromachine-explicit-ability-staging-single-flight-patch",
            "--micromachine-parallel-operations-ingame-hud-patch",
            "--micromachine-parallel-operation-lifecycle-review-closure-patch",
            "--micromachine-authoritative-parallel-operation-lifecycle-patch",
            "--micromachine-operation-production-ownership-restore-proof-patch",
            "--micromachine-embedded-build-input-identity-patch",
            "--micromachine-atomic-telemetry-publication-patch",
            "--micromachine-contextual-transfer-choice-projection-patch",
            "--micromachine-autonomous-owner-composition-evidence-patch",
            "--micromachine-battlefield-review-closure-patch",
            "--write-embedded-identity-header",
            "DSC2Api_SC2API_LIB",
            "reset --hard",
            "clean -fdx",
            "canonical_checkout_path",
            "pwd -P",
            "require_disposable_checkout_mutation",
            "safe_clean_git_checkout",
            "MICROMACHINE_ALLOW_DESTRUCTIVE_CLEAN",
            "Refusing to ${action} override checkout outside",
            "is_valid_git_checkout",
            "prepare_git_checkout",
            ".invalid.$(date +%Y%m%d%H%M%S).$$",
            "Invalid ${repo_name} git checkout; moving aside",
            "submodule update --init --recursive",
            "apply --check --ignore-space-change --whitespace=nowarn",
            "cmake --build",
            "MICROMACHINE_BUILD_IDENTITY_REPORT",
            "starcraft_commander.micromachine_build_identity",
            "--s2client-build-dir",
            "--initialize-source-attestation",
            "--finalize-build-attestation",
            "--micromachine-operation-state-patch",
            "--micromachine-addon-recovery-patch",
            "--micromachine-grounded-addon-candidate-patch",
            "--micromachine-guaranteed-producer-grounding-patch",
            "--micromachine-emergency-land-query-fallback-patch",
            "--micromachine-grounded-production-observed-targeting-patch",
            "--micromachine-live-operation-unblock-patch",
            "--micromachine-stable-flank-stage-latch-patch",
            "--micromachine-production-staging-observed-operation-patch",
            "--micromachine-addon-query-footprint-validation-patch",
            "--micromachine-authoritative-addon-execution-patch",
            "--micromachine-continuous-army-macro-patch",
            "--micromachine-continuous-army-economy-scaling-patch",
            "--micromachine-production-facility-stability-tank-recovery-patch",
            "--micromachine-balanced-composition-wave-production-patch",
            "--micromachine-exact-composition-production-unblock-patch",
            "--micromachine-continuous-combat-production-relaunch-patch",
            "--micromachine-resource-throughput-expansion-backoff-patch",
            "--micromachine-gas-worker-completion-cap-patch",
            "--micromachine-tactical-nuke-command-hierarchy-patch",
            "--micromachine-explicit-terran-ability-execution-patch",
            "--micromachine-explicit-scout-command-epoch-patch",
            "--micromachine-standing-production-continuity-closure-patch",
            "--micromachine-explicit-ability-caster-production-priority-patch",
            "--micromachine-explicit-ability-observation-confirmation-patch",
            "voi_build_identity.json",
            "BLACKBOARD_HEADER_FILE",
            "voi_policy_blackboard.hpp",
            'cp "${BLACKBOARD_HEADER_FILE}" "${MICROMACHINE_DIR}/src/voi_policy_blackboard.hpp"',
            '"${MICROMACHINE_BUILD_DIR}/bin/MicroMachine"',
        ):
            with self.subTest(term=term):
                self.assertIn(term, build_script)
        self.assertLess(
            build_script.index("--initialize-source-attestation"),
            build_script.index('cmake -S "${MICROMACHINE_DIR}"'),
        )
        self.assertLess(
            build_script.index('"${MICROMACHINE_BUILD_DIR}/bin/MicroMachine"'),
            build_script.index("--initialize-source-attestation"),
        )
        self.assertLess(
            build_script.index('cmake --build "${MICROMACHINE_BUILD_DIR}"'),
            build_script.index("--finalize-build-attestation"),
        )
        adaptive_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${ADAPTIVE_PRESSURE_STABLE_OPERATION_KEY_PATCH_FILE}"'
        )
        tactical_nuke_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH_FILE}"'
        )
        tactical_nuke_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${TACTICAL_NUKE_COMMAND_HIERARCHY_PATCH_FILE}"'
        )
        location_lock_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${LOCATION_INTENT_TARGET_LOCK_PATCH_FILE}"'
        )
        location_lock_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${LOCATION_INTENT_TARGET_LOCK_PATCH_FILE}"'
        )
        explicit_ability_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH_FILE}"'
        )
        explicit_ability_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_TERRAN_ABILITY_EXECUTION_PATCH_FILE}"'
        )
        explicit_scout_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH_FILE}"'
        )
        explicit_scout_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_SCOUT_COMMAND_EPOCH_PATCH_FILE}"'
        )
        standing_production_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH_FILE}"'
        )
        standing_production_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${STANDING_PRODUCTION_CONTINUITY_CLOSURE_PATCH_FILE}"'
        )
        explicit_caster_priority_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH_FILE}"'
        )
        explicit_caster_priority_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_CASTER_PRODUCTION_PRIORITY_PATCH_FILE}"'
        )
        explicit_observation_confirmation_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH_FILE}"'
        )
        explicit_observation_confirmation_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_OBSERVATION_CONFIRMATION_PATCH_FILE}"'
        )
        explicit_production_isolation_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH_FILE}"'
        )
        explicit_production_isolation_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_PRODUCTION_ISOLATION_PATCH_FILE}"'
        )
        explicit_attempt_lifecycle_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH_FILE}"'
        )
        explicit_attempt_lifecycle_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_ATTEMPT_LIFECYCLE_PATCH_FILE}"'
        )
        explicit_review_closure_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH_FILE}"'
        )
        explicit_review_closure_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_REVIEW_CLOSURE_PATCH_FILE}"'
        )
        authoritative_addon_runtime_clearance_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH_FILE}"'
        )
        authoritative_addon_runtime_clearance_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${AUTHORITATIVE_ADDON_RUNTIME_CLEARANCE_PATCH_FILE}"'
        )
        banshee_unit_specific_cloak_command_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH_FILE}"'
        )
        banshee_unit_specific_cloak_command_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${BANSHEE_UNIT_SPECIFIC_CLOAK_COMMAND_PATCH_FILE}"'
        )
        allied_cloak_observation_confirmation_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH_FILE}"'
        )
        allied_cloak_observation_confirmation_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${ALLIED_CLOAK_OBSERVATION_CONFIRMATION_PATCH_FILE}"'
        )
        explicit_ability_caster_ownership_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH_FILE}"'
        )
        explicit_ability_caster_ownership_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_CASTER_OWNERSHIP_PATCH_FILE}"'
        )
        explicit_ability_staging_single_flight_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH_FILE}"'
        )
        explicit_ability_staging_single_flight_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${EXPLICIT_ABILITY_STAGING_SINGLE_FLIGHT_PATCH_FILE}"'
        )
        all_terran_combat_scouts_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${ALL_TERRAN_COMBAT_SCOUTS_PATCH_FILE}"'
        )
        all_terran_combat_scouts_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${ALL_TERRAN_COMBAT_SCOUTS_PATCH_FILE}"'
        )
        parallel_operations_ingame_hud_check = (
            'git -C "${MICROMACHINE_DIR}" apply --recount --check '
            '--ignore-space-change --whitespace=nowarn '
            '"${PARALLEL_OPERATIONS_INGAME_HUD_PATCH_FILE}"'
        )
        parallel_operations_ingame_hud_apply = (
            'git -C "${MICROMACHINE_DIR}" apply --recount '
            '--ignore-space-change --whitespace=nowarn '
            '"${PARALLEL_OPERATIONS_INGAME_HUD_PATCH_FILE}"'
        )
        blackboard_copy = (
            'cp "${BLACKBOARD_HEADER_FILE}" '
            '"${MICROMACHINE_DIR}/src/voi_policy_blackboard.hpp"'
        )
        self.assertLess(
            build_script.index(adaptive_apply),
            build_script.index(tactical_nuke_check),
        )
        self.assertLess(
            build_script.index(tactical_nuke_check),
            build_script.index(tactical_nuke_apply),
        )
        self.assertLess(
            build_script.index(tactical_nuke_apply),
            build_script.index(location_lock_check),
        )
        self.assertLess(
            build_script.index(location_lock_check),
            build_script.index(location_lock_apply),
        )
        self.assertLess(
            build_script.index(location_lock_apply),
            build_script.index(explicit_ability_check),
        )
        self.assertLess(
            build_script.index(explicit_ability_check),
            build_script.index(explicit_ability_apply),
        )
        self.assertLess(
            build_script.index(explicit_ability_apply),
            build_script.index(explicit_scout_check),
        )
        self.assertLess(
            build_script.index(explicit_scout_check),
            build_script.index(explicit_scout_apply),
        )
        self.assertLess(
            build_script.index(explicit_scout_apply),
            build_script.index(standing_production_check),
        )
        self.assertLess(
            build_script.index(standing_production_check),
            build_script.index(standing_production_apply),
        )
        self.assertLess(
            build_script.index(standing_production_apply),
            build_script.index(explicit_caster_priority_check),
        )
        self.assertLess(
            build_script.index(explicit_caster_priority_check),
            build_script.index(explicit_caster_priority_apply),
        )
        self.assertLess(
            build_script.index(explicit_caster_priority_apply),
            build_script.index(explicit_observation_confirmation_check),
        )
        self.assertLess(
            build_script.index(explicit_observation_confirmation_check),
            build_script.index(explicit_observation_confirmation_apply),
        )
        self.assertLess(
            build_script.index(explicit_observation_confirmation_apply),
            build_script.index(explicit_production_isolation_check),
        )
        self.assertLess(
            build_script.index(explicit_production_isolation_check),
            build_script.index(explicit_production_isolation_apply),
        )
        self.assertLess(
            build_script.index(explicit_production_isolation_apply),
            build_script.index(explicit_attempt_lifecycle_check),
        )
        self.assertLess(
            build_script.index(explicit_attempt_lifecycle_check),
            build_script.index(explicit_attempt_lifecycle_apply),
        )
        self.assertLess(
            build_script.index(explicit_attempt_lifecycle_apply),
            build_script.index(explicit_review_closure_check),
        )
        self.assertLess(
            build_script.index(explicit_review_closure_check),
            build_script.index(explicit_review_closure_apply),
        )
        self.assertLess(
            build_script.index(explicit_review_closure_apply),
            build_script.index(authoritative_addon_runtime_clearance_check),
        )
        self.assertLess(
            build_script.index(authoritative_addon_runtime_clearance_check),
            build_script.index(authoritative_addon_runtime_clearance_apply),
        )
        self.assertLess(
            build_script.index(authoritative_addon_runtime_clearance_apply),
            build_script.index(banshee_unit_specific_cloak_command_check),
        )
        self.assertLess(
            build_script.index(banshee_unit_specific_cloak_command_check),
            build_script.index(banshee_unit_specific_cloak_command_apply),
        )
        self.assertLess(
            build_script.index(banshee_unit_specific_cloak_command_apply),
            build_script.index(allied_cloak_observation_confirmation_check),
        )
        self.assertLess(
            build_script.index(allied_cloak_observation_confirmation_check),
            build_script.index(allied_cloak_observation_confirmation_apply),
        )
        self.assertLess(
            build_script.index(allied_cloak_observation_confirmation_apply),
            build_script.index(explicit_ability_caster_ownership_check),
        )
        self.assertLess(
            build_script.index(explicit_ability_caster_ownership_check),
            build_script.index(explicit_ability_caster_ownership_apply),
        )
        self.assertLess(
            build_script.index(explicit_ability_caster_ownership_apply),
            build_script.index(explicit_ability_staging_single_flight_check),
        )
        self.assertLess(
            build_script.index(explicit_ability_staging_single_flight_check),
            build_script.index(explicit_ability_staging_single_flight_apply),
        )
        self.assertLess(
            build_script.index(explicit_ability_staging_single_flight_apply),
            build_script.index(all_terran_combat_scouts_check),
        )
        self.assertLess(
            build_script.index(all_terran_combat_scouts_check),
            build_script.index(all_terran_combat_scouts_apply),
        )
        self.assertLess(
            build_script.index(all_terran_combat_scouts_apply),
            build_script.index(parallel_operations_ingame_hud_check),
        )
        self.assertLess(
            build_script.index(parallel_operations_ingame_hud_check),
            build_script.index(parallel_operations_ingame_hud_apply),
        )
        self.assertLess(
            build_script.index(parallel_operations_ingame_hud_apply),
            build_script.index(blackboard_copy),
        )
        self.assertLess(
            build_script.index('git -C "${MICROMACHINE_DIR}" reset --hard\n'),
            build_script.index(
                'git -C "${MICROMACHINE_DIR}" checkout "${MICROMACHINE_COMMIT}"'
            ),
        )
        self.assertLess(
            build_script.index('git -C "${S2CLIENT_DIR}" reset --hard\n'),
            build_script.index(
                'git -C "${S2CLIENT_DIR}" checkout "${S2CLIENT_COMMIT}"'
            ),
        )

        for term in (
            "voi_bootstrap_probe",
            "PROBE_OUTPUT",
            "PROBE_MAX_FRAME",
            "PROBE_STEP_SIZE",
            "PROBE_ENEMY_RACE",
            "PROBE_ENEMY_DIFFICULTY",
            "SC2_PORT_START",
            "SC2_MAP_AS_PROVIDED",
            "SC2_USE_RUNTIME_DIR_ARGS",
            "VOI_SC2_CREATEGAME_MAP_DATA",
            "SC2_CLEAN_PORTS_BEFORE_LAUNCH",
            "SC2_POST_CLEAN_SETTLE_SECONDS",
            "clean_sc2_ports_before_launch",
            "settle_after_sc2_port_cleanup",
            "MicroMachine bootstrap probe failed",
            "bootstrap probe false pass",
            "self_worker_count",
            "resource_depot_count",
            "Run integrations/micromachine/scripts/build_macos_local.sh",
        ):
            with self.subTest(term=term):
                self.assertIn(term, probe_script)

        for term in (
            "VOI_MICROMACHINE_BLACKBOARD_DIR",
            "--live-hold",
            "--blackboard-dir",
            "--enemy-difficulty",
            "--max-attempts",
            "build_tank_defensive_hold_profile",
            "build_bio_pressure_profile",
            "build_manual_live_autonomy_profile",
            'publish_profile "manual_live_autonomy"',
            "latest_modulation.kv",
            "preserve_existing_live_modulation",
            "manual live mode preserved existing tactical blackboard command",
            'update_id.startswith(("smoke-", "soak-"))',
            "PolicyModulationVector.from_mapping",
            "MicroMachineBlackboardUpdate(",
            "AGGRESSIVE_PROFILE_PUBLISHED=1",
            "telemetry.jsonl",
            "AcropolisLE.SC2Map",
            "resolve_sc2_executable",
            "resolve_map_file",
            "prepare_launch_contract",
            "map file not found",
            "SC2 executable is not runnable",
            "SC2_LAUNCH_MODE",
            "SC2_BATTLENET_EXECUTABLE",
            "SC2_ATTACH_TIMEOUT_MS",
            "SC2_USE_RUNTIME_DIR_ARGS",
            "SC2_ROOT_ALIAS",
            "SC2_RUNTIME_ROOT",
            "SC2_TEMP_DIR",
            "SC2_CLEAN_PORTS_BEFORE_LAUNCH",
            "SC2_POST_CLEAN_SETTLE_SECONDS",
            "VOI_SC2_CREATEGAME_MAP_DATA",
            "mkdir -p \"${SC2_TEMP_DIR}\"",
            "-dataDir",
            "-tempDir",
            "*/SC2.app/Contents/MacOS/SC2",
            "latest_telemetry.json",
            "MIN_TELEMETRY_FRAME",
            "SMOKE_TIMEOUT_SECONDS",
            "SMOKE_MAX_ATTEMPTS",
            "SMOKE_RETRY_SETTLE_SECONDS",
            "SMOKE_ATTEMPT_INDEX",
            "SMOKE_RUN_ID",
            "SMOKE_RUN_ROOT",
            "SMOKE_REPO_HEAD_SHA",
            "reset_promoted_smoke_artifacts",
            "write_smoke_attempt_status",
            "write_smoke_attempt_summary",
            "repo_head_sha",
            "build_identity",
            "micromachine_source_state_sha256",
            "selected_attempt_dir",
            "SMOKE_KEEP_RUNNING_AFTER_PASS",
            "SMOKE_MANUAL_LIVE_MODE",
            "SMOKE_AUTO_AGGRESSIVE_PROFILE",
            "SMOKE_REQUIRE_BUILD_IDENTITY",
            "MICROMACHINE_BUILD_IDENTITY_REPORT",
            "verify_build_identity",
            "snapshot_runtime_identity",
            "verify_runtime_identity_snapshot",
            "runtime_identity.snapshot.json",
            "build_identity_report_sha256",
            "binary_sha256",
            "stale build identity",
            "SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE",
            "SMOKE_OPERATION_UPDATE_ID",
            "publish_parallel_operations",
            "operation_lifecycle_ready",
            "snapshot_latest_telemetry_for_cleanup",
            "restore_latest_telemetry_after_cleanup",
            "latest_telemetry.pre_cleanup.json",
            "exclusive_assignment_observed",
            "live_production_owner_observed",
            "operation_owned_queue_items",
            "operation_owner_present",
            "difficulty_smoke_selective_attack_cancel",
            "selective attack cancellation",
            '"owned_unit_count"',
            '"submission_observed"',
            '"assigned_unit_tags"',
            '"CANCELLED"',
            "parallel operation assignment/submission/movement",
            "CANCELLED terminal ownership-release evidence",
            'SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-1}"',
            'SMOKE_ENEMY_DIFFICULTY="${SMOKE_ENEMY_DIFFICULTY:-7}"',
            'SMOKE_ENEMY_DIFFICULTY="${SMOKE_ENEMY_DIFFICULTY:-1}"',
            "SMOKE_ENEMY_DIFFICULTY must be an integer from 1 to 10",
            "has_live_hold_preflight_evidence",
            "MicroMachine manual live hold preflight passed",
            "MicroMachine live hold preflight did not pass",
            "MicroMachine manual live autonomy active",
            "automatic aggressive smoke profile is disabled",
            "smoke_attempts.json",
            "MicroMachine smoke retrying after retryable frame-0 startup failure",
            "startup_frame_threshold",
            "latest_frame >= startup_frame_threshold",
            "macro_terms",
            "non_retryable_terms",
            "retryable_startup_failure",
            "selected_attempt",
            "micromachine_combined.log",
            "RUNTIME_LOG_BASELINE",
            "runtime_log_baseline.tsv",
            "record_runtime_log_baseline",
            "stream_current_run_log",
            "runtime_log_start_offset",
            '"EnemyDifficulty"] = int',
            '"ForceStepMode"] = bool(int(os.environ.get("SMOKE_FORCE_STEP_MODE", "0")))',
            '"EnemyRace"] = "Zerg"',
            '"StepSize"] = 1',
            'profile = os.environ.get("SMOKE_STRATEGY_PROFILE_NAME", "bio_pressure")',
            "strategy_by_profile = {",
            '"tank_defensive_hold": "Terran_Hellion"',
            '"expand_macro": "Terran_FastExpand"',
            'config["SC2API Strategy"]["Terran"] = selected_strategy',
            "policy_active",
            "CombatCommander",
            "ScoutManager",
            "Squad",
            "WorkerManager",
            "bounded_intervention",
            "repeat_order_guard_active",
            "repeat_order_suppressed_count",
            "self_position_command_block_count",
            "root_cause_status",
            "root_cause_reason",
            "workers.repeat_order_guard_frames",
            "combat.attack_timing_bias",
            "combat.commitment_level",
            "combat.attack_condition_override",
            "main_attack_order_status",
            "main_attack_unit_count",
            "main_attack_scope_min_units",
            "main_attack_scope_threshold_met",
            "main_attack_simulation_won",
            "and unit_count >= min_units",
            "SMOKE_MIN_MAIN_ATTACK_HOME_DISTANCE",
            "SMOKE_MIN_COMBAT_SCOUT_HOME_DISTANCE",
            "MainAttack command did not produce live movement away from home",
            "Combat scout squad was assigned but did not produce live movement away from home",
            "combat.retreat_patience_bias",
            "combat.rally_before_attack_bias",
            "squad.contain_bias",
            "squad.reinforce_bias",
            "scope.location_intent",
            "combat.target_priority_biases.*",
            "target_worker_line_bias",
            "target_townhall_bias",
            "target_army_bias",
            "missing deep CombatCommander consumed axis",
            "missing deep Squad consumed axis",
            "worker self-position command root-cause blocks were observed",
            "worker repeat-order safety guard had to suppress commands; root cause remains active",
            "aggressive_update_id = sys.argv[3]",
            "defensive_update_id = sys.argv[4]",
            "smoke-defensive-hold",
            "smoke-aggressive-pressure",
            "cleanup_runtime",
            "clean_sc2_ports_before_launch",
            "settle_after_sc2_port_cleanup",
            "lsof -nP -tiTCP",
            "capture_preexisting_sc2_port_pids",
            "PREEXISTING_SC2_PORT_PIDS",
            "did not initialize GameCommander",
            "REQUIRED_MACRO_EVIDENCE",
            "build command type=TERRAN_SUPPLYDEPOT",
            "TERRAN_SUPPLYDEPOT UnderConstruction",
            "build command type=TERRAN_BARRACKS",
            "TERRAN_BARRACKS UnderConstruction",
            "build command type=TERRAN_REFINERY",
            "POST_BARRACKS_UNIT_EVIDENCE",
            "create unit item=Marine result=1",
            "has_positive_gas_income",
            "has_positive_mineral_income",
            "marine_rush.insert(first_barracks + 1, \"Marine\")",
            "FORBIDDEN_MACRO_FAILURES",
            "Failed to place Barracks",
            "Cancel building TERRAN_SUPPLYDEPOT :",
            "MicroMachine reached SC2 API but did not execute the required macro opening",
            "bootstrap_no_start_units",
            "NO_START_UNITS_FRAME",
            "missing worker root-cause status telemetry",
            "missing_worker_root_cause_reason",
            "worker root-cause archive violation",
            "archived_scout_duplicate_worker_move",
            "except json.JSONDecodeError",
        ):
            with self.subTest(term=term):
                self.assertIn(term, smoke_script)
        for term in (
            "EXPECTED_ACTUAL_PRODUCTION_ITEM_BY_PAIR",
            '("bio_facility", "Barracks")',
            "actual_frame < doctrine_frame",
            "last_doctrine_required_count",
            "last_doctrine_represented_count",
            "last_doctrine_prerequisites_satisfied",
            "last_actual_production_command_item",
            "actual_production_command_issued_count",
        ):
            with self.subTest(production_evidence_contract=term):
                self.assertIn(term, production_evidence_module)
        self.assertIn(
            "exact same-update SC2 command",
            soak_classifier_module,
        )
        self.assertNotIn(
            'int(combat.get("main_attack_unit_count", 0)) < '
            'int(combat.get("main_attack_scope_min_units", 1))',
            smoke_script,
        )
        self.assertNotIn(
            'if combat.get("main_attack_order_status") != "Attack":',
            smoke_script,
        )
        self.assertNotIn(
            "ready_with_live_production_owner",
            smoke_script,
        )
        self.assertNotIn(") || true", smoke_script)
        self.assertIn('payload.get("frame", 0) < min_frame', smoke_script)
        for term in (
            'MATRIX_RUN_ID="${MATRIX_RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"',
            'MATRIX_RUN_ROOT="${BLACKBOARD_ROOT}/runs/${MATRIX_RUN_ID}"',
            'summary="${MATRIX_RUN_ROOT}/strategy_matrix_summary.jsonl"',
            'run_dir="${MATRIX_RUN_ROOT}/${profile}"',
            "find_causal_production_evidence",
            "expected_production_pairs",
            "load_latest_or_archive",
            "summary_evidence_source",
            "latest_doctrine_action",
            "last_doctrine_evidence",
            "actual_production_command_issued_count",
            "last_actual_production_command",
            "last_actual_production_command_frame",
            "worker_trace_status",
            "worker_repeat_order_suppressions",
            "worker_root_cause_status",
            "worker_root_cause_reason",
        ):
            with self.subTest(strategy_matrix_contract=term):
                self.assertIn(term, strategy_matrix_script)

        for term in (
            "VOI_MICROMACHINE_BLACKBOARD_DIR",
            "SOAK_TARGET_FRAME",
            "SOAK_ENEMY_RACE",
            "SOAK_ENEMY_DIFFICULTY",
            '"ForceStepMode"] = bool(int(os.environ.get("SOAK_FORCE_STEP_MODE", "0")))',
            "SOAK_TIMEOUT_SECONDS",
            "SOAK_TELEMETRY_STALL_SECONDS",
            "SOAK_PRODUCTION_DEADLOCK_FRAME",
            "SOAK_PRODUCTION_STALL_FRAMES",
            "SOAK_INCOME_STALL_FRAMES",
            "SOAK_BOOTSTRAP_NO_START_UNITS_FRAME",
            "SOAK_MAX_PLACEMENT_FAILURES",
            "SOAK_MODULATION_CONSUMPTION_GRACE_FRAMES",
            "SOAK_ARTIFACT_ROOT",
            "SOAK_RUN_DIR",
            "soak_report.json",
            "soak_live_report.json",
            "starcraft_commander.micromachine_soak",
            "--allow-incomplete",
            "telemetry-stall-seconds",
            "production-deadlock-frame",
            "income-stall-frames",
            "bootstrap-no-start-units-frame",
            "max-placement-failures",
            "max-worker-repeat-order-suppressions",
            "modulation-consumption-grace-frames",
            "termination-reason",
            "target_frame_reached_cleanup",
            "live_classifier_failure",
            "fail_from_live_classifier",
            "if ! classify_soak \"live\"",
            "CombatCommander",
            "ScoutManager",
            "bounded_intervention",
            "REQUIRED_MACRO_EVIDENCE",
            "TERRAN_BARRACKS UnderConstruction",
            "create unit item=Marine result=1",
            "Gas income:",
            "Mineral income:",
            "cleanup_runtime",
            "SOAK_PROFILE_REFRESH_FRAMES",
            "SOAK_PROFILE_SEQUENCE",
            "default_defensive_to_aggressive",
            "build_micromachine_strategy_profile",
            "MICROMACHINE_STRATEGY_PROFILE_KEYS",
            "PROFILE_SCHEDULE_FRAMES[$index] <= SOAK_TARGET_FRAME",
            "unknown SOAK_PROFILE_SEQUENCE profile",
            "strategy_profile_missing",
            "--expected-profile-tags",
            "SOAK_EXPECTED_TACTICAL_EFFECTS",
            "--expected-tactical-effects",
            "tactical_effect_missing",
            "SOAK_EXPECTED_STRATEGY_DOCTRINE",
            "SOAK_EXPECTED_PRODUCTION_ACTIONS",
            "SOAK_EXPECTED_PRODUCTION_ITEMS",
            "expected_strategy_contract",
            "--expected-strategy-doctrine",
            "--expected-production-actions",
            "--expected-production-items",
            "strategy_actual_command_missing",
            "tactical_actual_command_missing",
            "worker_repeat_order_suppression",
            "scouting_map_control",
            "expected_actual_production_items",
            "bio_facility bio_marauder_techlab bio_ghost_techlab bio_marauder_support starport_transition medivac_drop_support",
            "Barracks BarracksTechLab Marauder Starport Medivac",
            'SOAK_AGGRESSIVE_MIN_FRAME="${SOAK_AGGRESSIVE_MIN_FRAME:-6000}"',
            "SOAK_MAX_ATTEMPTS",
            "SOAK_RETRY_SETTLE_SECONDS",
            "MicroMachine soak retrying after retryable startup failure",
            "SOAK_ATTEMPT_INDEX",
            "SOAK_NON_RETRYABLE_FAILURE_CODES",
            "bootstrap_no_start_units repeated_placement_failures no_production_deadlock production_stall income_stall manager_intervention_missing stale_modulation strategy_profile_missing tactical_effect_missing tactical_actual_command_missing strategy_actual_command_missing worker_repeat_order_suppression",
            "retryable_startup_codes",
            "\"telemetry_missing\"",
            "latest_frame == 0 and codes and codes <= retryable_startup_codes",
            "payload[\"attempts\"] = attempts",
            "CLASSIFIER_BOT_LOG",
            "micromachine_combined.log",
            "latest_runtime_log",
            "RUNTIME_LOG_BASELINE",
            "runtime_log_baseline.tsv",
            "record_runtime_log_baseline",
            "stream_current_run_log",
            "runtime_log_start_offset",
            "refresh_classifier_log",
            "runtime_log_start.marker",
            "SC2_LAUNCH_MODE",
            "SC2_BATTLENET_EXECUTABLE",
            "SC2_ATTACH_TIMEOUT_MS",
            "SC2_USE_RUNTIME_DIR_ARGS",
            "resolve_map_file",
            "prepare_launch_contract",
            "SOAK_REQUIRE_BUILD_IDENTITY",
            "MICROMACHINE_BUILD_IDENTITY_REPORT",
            "verify_build_identity",
            "MicroMachine soak rejected: missing build identity report",
            "MicroMachine soak rejected: stale build identity",
            "Run integrations/micromachine/scripts/build_macos_local.sh",
            "map file not found",
            "SC2 executable is not runnable",
            "SC2_ROOT_ALIAS",
            "SC2_RUNTIME_ROOT",
            "SC2_TEMP_DIR",
            "SC2_CLEAN_PORTS_BEFORE_LAUNCH",
            "SC2_POST_CLEAN_SETTLE_SECONDS",
            "VOI_SC2_CREATEGAME_MAP_DATA",
            "mkdir -p \"${SC2_TEMP_DIR}\"",
            "clean_sc2_ports_before_launch",
            "settle_after_sc2_port_cleanup",
            "lsof -nP -tiTCP",
            "VOI_SC2_EXTRA_ARGS",
            "-t \"${SC2_ATTACH_TIMEOUT_MS}\"",
            "non_retryable_failure",
            "attempt_summary",
            "selected_attempt",
            "artifact_manifest",
            "relative_to(root)",
            "MicroMachine soak passed",
            "deterministic",
            "capture_preexisting_sc2_port_pids",
            "PREEXISTING_SC2_PORT_PIDS",
            "BOT_TERMINATION_REASON=\"timeout\"",
        ):
            with self.subTest(term=term):
                self.assertIn(term, soak_script)

        for term in (
            "SOAK_MATRIX_MAP_FILES",
            "SOAK_MATRIX_MAP_ROOTS",
            "IFS=':' read -r -a map_roots",
            "SOAK_MATRIX_ENEMY_RACES",
            "SOAK_MATRIX_ENEMY_DIFFICULTIES",
            "SOAK_MATRIX_QUALIFICATION_TIER",
            "MICROMACHINE_MAP_POOL.json",
            "starcraft_commander.micromachine_map_pool",
            "starcraft_commander.micromachine_preflight",
            "preflight_report.json",
            "SOAK_MATRIX_REPORT",
            "matrix_report.json",
            "SOAK_MATRIX_TRIAGE_JSON",
            "SOAK_MATRIX_TRIAGE_MD",
            "starcraft_commander.micromachine_triage",
            "triage_report.json",
            "triage_report.md",
            'SOAK_QUALIFICATION_TIER="${SOAK_MATRIX_QUALIFICATION_TIER}"',
            "SOAK_MATRIX_BUILD_IDENTITY_REPORT",
            "SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY",
            "starcraft_commander.micromachine_build_identity",
            "SOAK_MATRIX_ALLOW_FAILURES",
            "SOAK_MATRIX_STOP_ON_FAILURE",
            "SOAK_MATRIX_AGGREGATE_ONLY",
            "SOAK_MATRIX_MIN_PASSES",
            "case_count",
            "passed",
            "failed",
            "failure_codes",
            "attempts",
            "artifact_manifest",
            "MicroMachine matrix completed",
        ):
            with self.subTest(term=term):
                self.assertIn(term, soak_matrix_script)

        for term in (
            "telemetry_stall",
            "bootstrap_no_start_units",
            "bootstrap_no_start_units_frame",
            "repeated_placement_failures",
            "no_production_deadlock",
            "income_stall",
            "stale_modulation",
        ):
            with self.subTest(term=term):
                self.assertIn(term, (REPO_ROOT / "starcraft_commander" / "micromachine_soak.py").read_text())

    def test_sc2_direct_resolvers_choose_highest_numeric_base(self) -> None:
        for script_path in (PROBE_SCRIPT, SMOKE_SCRIPT, SOAK_SCRIPT):
            script = script_path.read_text()
            function_start = script.index(
                "resolve_latest_direct_sc2_executable() {"
            )
            function_end = script.index(
                "\n}\n\nresolve_sc2_executable()",
                function_start,
            ) + 2
            resolver = script[function_start:function_end]
            with self.subTest(script=script_path.name):
                self.assertNotIn("sort -r", resolver)
                self.assertIn("substr($part, 5) + 0", resolver)
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory) / "StarCraft II"
                    expected = (
                        root
                        / "Versions"
                        / "Base100000"
                        / "SC2.app"
                        / "Contents"
                        / "MacOS"
                        / "SC2"
                    )
                    older = (
                        root
                        / "Versions"
                        / "Base99999"
                        / "SC2.app"
                        / "Contents"
                        / "MacOS"
                        / "SC2"
                    )
                    for executable in (older, expected):
                        executable.parent.mkdir(parents=True)
                        executable.write_text("#!/usr/bin/env bash\nexit 0\n")
                        executable.chmod(0o755)
                    completed = subprocess.run(
                        [
                            "bash",
                            "-c",
                            (
                                "set -euo pipefail\n"
                                f"{resolver}\n"
                                'SC2_ROOT="$1"\n'
                                "resolve_latest_direct_sc2_executable\n"
                            ),
                            "resolver",
                            str(root),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        0,
                        completed.returncode,
                        completed.stdout + completed.stderr,
                    )
                    self.assertEqual(
                        str(expected),
                        completed.stdout.strip(),
                    )

    def test_production_soak_launcher_fails_without_direct_sc2(self) -> None:
        workflow = LOCAL_SOAK_WORKFLOW.read_text()
        step_start = workflow.index(
            "      - name: Verify local MicroMachine and SC2 inputs"
        )
        run_start = workflow.index("        run: |\n", step_start)
        step_end = workflow.index(
            "      - name: Run or disable real SC2 MicroMachine soak matrix",
            run_start,
        )
        shell = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in workflow[run_start + len("        run: |\n") : step_end]
            .rstrip()
            .splitlines()
        )

        self.assertNotIn("sort -r", shell)
        self.assertIn("Battle.net launch is diagnostic-only", shell)
        self.assertIn("resolved SC2 executable must be a direct", shell)
        self.assertIn("no runnable executable selected", shell)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            micromachine = root / "build" / "bin" / "MicroMachine"
            micromachine.parent.mkdir(parents=True)
            micromachine.write_text("#!/usr/bin/env bash\nexit 0\n")
            micromachine.chmod(0o755)
            github_env = root / "github.env"
            base_env = {
                **os.environ,
                "MICROMACHINE_BUILD_DIR": str(root / "build"),
                "SC2_ROOT": str(root / "missing-sc2"),
                "GITHUB_ENV": str(github_env),
            }

            missing_direct = subprocess.run(
                ["bash", "-c", shell],
                env={
                    **base_env,
                    "INPUT_QUALIFICATION_TIER": "production",
                    "SC2_LAUNCH_MODE": "auto",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, missing_direct.returncode)
            self.assertIn(
                "no runnable executable selected",
                missing_direct.stderr,
            )

            battlenet = root / "Battle.net"
            battlenet.write_text("#!/usr/bin/env bash\nexit 0\n")
            battlenet.chmod(0o755)
            production_battlenet = subprocess.run(
                ["bash", "-c", shell],
                env={
                    **base_env,
                    "INPUT_QUALIFICATION_TIER": "production",
                    "SC2_LAUNCH_MODE": "battlenet",
                    "SC2_BATTLENET_EXECUTABLE": str(battlenet),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, production_battlenet.returncode)
            self.assertIn(
                "Battle.net launch is diagnostic-only",
                production_battlenet.stderr,
            )

            production_auto_explicit_battlenet = subprocess.run(
                ["bash", "-c", shell],
                env={
                    **base_env,
                    "INPUT_QUALIFICATION_TIER": "production",
                    "SC2_LAUNCH_MODE": "auto",
                    "SC2_EXECUTABLE": str(battlenet),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(
                0,
                production_auto_explicit_battlenet.returncode,
            )
            self.assertIn(
                "resolved SC2 executable must be a direct",
                production_auto_explicit_battlenet.stderr,
            )

            diagnostic_battlenet = subprocess.run(
                ["bash", "-c", shell],
                env={
                    **base_env,
                    "INPUT_QUALIFICATION_TIER": "diagnostic",
                    "SC2_LAUNCH_MODE": "battlenet",
                    "SC2_BATTLENET_EXECUTABLE": str(battlenet),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                diagnostic_battlenet.returncode,
                diagnostic_battlenet.stdout + diagnostic_battlenet.stderr,
            )

    def test_soak_runtime_rejects_explicit_wrapper_for_production(self) -> None:
        script = SOAK_SCRIPT.read_text()
        function_start = script.index("resolve_map_file() {")
        function_end = script.index(
            "\n}\n\nverify_build_identity()",
            function_start,
        ) + 3
        launch_contract = script[function_start:function_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            map_file = root / "AcropolisLE.SC2Map"
            map_file.write_text("map")
            wrapper = root / "Battle.net"
            wrapper.write_text("#!/usr/bin/env bash\nexit 0\n")
            wrapper.chmod(0o755)
            direct = (
                root
                / "StarCraft II"
                / "Versions"
                / "Base100000"
                / "SC2.app"
                / "Contents"
                / "MacOS"
                / "SC2"
            )
            direct.parent.mkdir(parents=True)
            direct.write_text("#!/usr/bin/env bash\nexit 0\n")
            direct.chmod(0o755)
            base_env = {
                **os.environ,
                "MAP_FILE": str(map_file),
                "PYTHONPATH": str(REPO_ROOT),
                "REPO_ROOT": str(REPO_ROOT),
                "SC2_BATTLENET_EXECUTABLE": str(wrapper),
                "SC2_LAUNCH_MODE": "auto",
                "SC2_ROOT": str(root / "StarCraft II"),
                "SC2_TEMP_DIR": str(root / "temp"),
                "SC2_USE_RUNTIME_DIR_ARGS": "0",
                "SOAK_MAP_POOL_MANIFEST": str(
                    KIT_DIR / "MICROMACHINE_MAP_POOL.json"
                ),
                "SOAK_QUALIFICATION_TIER": "production",
            }
            command = (
                "set -euo pipefail\n"
                f"{launch_contract}\n"
                "prepare_launch_contract\n"
            )
            rejected = subprocess.run(
                ["bash", "-c", command],
                env={**base_env, "SC2_EXECUTABLE": str(wrapper)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, rejected.returncode)
            self.assertIn(
                "resolved SC2 executable must be a direct",
                rejected.stderr,
            )

            accepted = subprocess.run(
                ["bash", "-c", command],
                env={**base_env, "SC2_EXECUTABLE": str(direct)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                accepted.returncode,
                accepted.stdout + accepted.stderr,
            )

            typo_tier = subprocess.run(
                ["bash", "-c", command],
                env={
                    **base_env,
                    "SC2_EXECUTABLE": str(wrapper),
                    "SOAK_QUALIFICATION_TIER": "prodution",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, typo_tier.returncode)
            self.assertIn(
                "unknown SOAK_QUALIFICATION_TIER=prodution",
                typo_tier.stderr,
            )

            smoke_wrapper = subprocess.run(
                ["bash", "-c", command],
                env={
                    **base_env,
                    "SC2_EXECUTABLE": str(wrapper),
                    "SOAK_QUALIFICATION_TIER": "smoke",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, smoke_wrapper.returncode)
            self.assertIn(
                "resolved SC2 executable must be a direct",
                smoke_wrapper.stderr,
            )

            diagnostic_wrapper = subprocess.run(
                ["bash", "-c", command],
                env={
                    **base_env,
                    "SC2_EXECUTABLE": str(wrapper),
                    "SOAK_QUALIFICATION_TIER": "diagnostic",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                diagnostic_wrapper.returncode,
                diagnostic_wrapper.stdout + diagnostic_wrapper.stderr,
            )

    def test_soak_matrix_forwards_production_tier_to_direct_soak_call(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_soak = root / "fake-soak.sh"
            fake_soak.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'if [[ "${SOAK_QUALIFICATION_TIER:-}" != "production" ]]; then\n'
                '  echo "missing production qualification tier" >&2\n'
                "  exit 42\n"
                "fi\n"
                'mkdir -p "${SOAK_RUN_DIR}"\n'
                "printf '%s\\n' "
                '\'{"status":"passed","ok":true,"latest_frame":1,'
                '"target_reached":true,"macro_evidence_ok":true,'
                '"manager_intervention_ok":true}\' '
                '> "${SOAK_RUN_DIR}/soak_report.json"\n'
            )
            fake_soak.chmod(0o755)
            run_dir = root / "matrix"
            completed = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env={
                    **os.environ,
                    "SOAK_SCRIPT": str(fake_soak),
                    "SOAK_MATRIX_ARTIFACT_ROOT": str(root),
                    "SOAK_MATRIX_RUN_DIR": str(run_dir),
                    "SOAK_MATRIX_REPORT": str(run_dir / "matrix_report.json"),
                    "SOAK_MATRIX_HISTORY_JSON": str(run_dir / "history.json"),
                    "SOAK_MATRIX_HISTORY_MD": str(run_dir / "history.md"),
                    "SOAK_MATRIX_TRIAGE_JSON": str(run_dir / "triage.json"),
                    "SOAK_MATRIX_TRIAGE_MD": str(run_dir / "triage.md"),
                    "SOAK_MATRIX_QUALIFICATION_TIER": "production",
                    "SOAK_MATRIX_MAP_FILES": "AcropolisLE.SC2Map",
                    "SOAK_MATRIX_ENEMY_RACES": "Zerg",
                    "SOAK_MATRIX_ENEMY_DIFFICULTIES": "1",
                    "SOAK_MATRIX_TARGET_FRAME": "1",
                    "SOAK_MATRIX_TIMEOUT_SECONDS": "1",
                    "SOAK_MATRIX_STRATEGY_PROFILES": (
                        "default_defensive_to_aggressive"
                    ),
                    "SOAK_MATRIX_BUILD_IDENTITY": "sha256:test",
                    "SOAK_MATRIX_BUILD_IDENTITY_OK": "1",
                    "SOAK_MATRIX_BUILD_IDENTITY_FAILURE_CODES": "none",
                    "SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY": (
                        "sha256:test"
                    ),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                completed.returncode,
                completed.stdout + completed.stderr,
            )
            report = json.loads((run_dir / "matrix_report.json").read_text())
            self.assertTrue(report["ok"])
            self.assertEqual("production", report["qualification_tier"])

    def test_smoke_wrapper_removes_stale_promoted_artifacts(self) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index(
            "reset_promoted_smoke_artifacts() {"
        )
        function_end = script.index(
            "\n}\n\nreset_current_smoke_artifacts()",
            function_start,
        ) + 3
        reset_function = script[function_start:function_end]
        promoted_artifacts = (
            "latest_telemetry.json",
            "telemetry.jsonl",
            "micromachine_combined.log",
            "micromachine.log",
            "smoke_attempts.json",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for artifact in promoted_artifacts:
                (root / artifact).write_text("stale-success\n")
            preserved = root / "runs" / "historical-run" / "attempt-1"
            preserved.mkdir(parents=True)
            (preserved / "latest_telemetry.json").write_text("history\n")
            unrelated = root / "operator-note.txt"
            unrelated.write_text("keep\n")

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        f"{reset_function}\n"
                        'BLACKBOARD_DIR="$1"\n'
                        "reset_promoted_smoke_artifacts\n"
                    ),
                    "smoke-reset-test",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                result.returncode,
                result.stdout + result.stderr,
            )
            for artifact in promoted_artifacts:
                self.assertFalse((root / artifact).exists(), artifact)
            self.assertTrue(
                (preserved / "latest_telemetry.json").exists()
            )
            self.assertEqual("keep\n", unrelated.read_text())

    def test_smoke_wrapper_clears_current_preflight_artifacts_only(self) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("reset_current_smoke_artifacts() {")
        function_end = script.index(
            "\n}\n\nwrite_smoke_attempt_status()",
            function_start,
        ) + 3
        reset_function = script[function_start:function_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current_artifacts = {
                "latest_telemetry.json",
                "latest_telemetry.pre_cleanup.json",
                "telemetry.jsonl",
                "micromachine.log",
                "micromachine_classifier.log",
                "runtime_log_baseline.tsv",
                "runtime_identity.snapshot.json",
                "smoke_attempts.json",
                "latest_modulation.json",
                "latest_modulation.kv",
                "latest_modulation_compile_result.json",
                "modulation_updates.jsonl",
            }
            for artifact in current_artifacts:
                (root / artifact).write_text("stale-current-run\n")

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        f"{reset_function}\n"
                        'BLACKBOARD_DIR="$1"\n'
                        'TELEMETRY_CLEANUP_SNAPSHOT="$1/'
                        'latest_telemetry.pre_cleanup.json"\n'
                        'BOT_LOG="$1/micromachine.log"\n'
                        'CLASSIFIER_BOT_LOG="$1/'
                        'micromachine_classifier.log"\n'
                        'RUNTIME_LOG_BASELINE="$1/'
                        'runtime_log_baseline.tsv"\n'
                        'RUNTIME_IDENTITY_SNAPSHOT="$1/'
                        'runtime_identity.snapshot.json"\n'
                        "SMOKE_MANUAL_LIVE_MODE=0\n"
                        "reset_current_smoke_artifacts\n"
                    ),
                    "smoke-current-reset-test",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                result.returncode,
                result.stdout + result.stderr,
            )
            for artifact in current_artifacts:
                self.assertFalse((root / artifact).exists(), artifact)

        reset_call = script.index(
            "\nreset_current_smoke_artifacts\nprepare_launch_contract\n"
        )
        identity_check = script.index("\nverify_build_identity\n", reset_call)
        self.assertLess(reset_call, identity_check)

    def test_smoke_wrapper_preserves_manual_live_modulation_only(self) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("reset_current_smoke_artifacts() {")
        function_end = script.index(
            "\n}\n\nwrite_smoke_attempt_status()",
            function_start,
        ) + 3
        reset_function = script[function_start:function_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "latest_telemetry.json").write_text("stale\n")
            for artifact in (
                "latest_modulation.json",
                "latest_modulation.kv",
                "latest_modulation_compile_result.json",
                "modulation_updates.jsonl",
            ):
                (root / artifact).write_text("active-command\n")

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        f"{reset_function}\n"
                        'BLACKBOARD_DIR="$1"\n'
                        'TELEMETRY_CLEANUP_SNAPSHOT="$1/'
                        'latest_telemetry.pre_cleanup.json"\n'
                        'BOT_LOG="$1/micromachine.log"\n'
                        'CLASSIFIER_BOT_LOG="$1/'
                        'micromachine_classifier.log"\n'
                        'RUNTIME_LOG_BASELINE="$1/'
                        'runtime_log_baseline.tsv"\n'
                        'RUNTIME_IDENTITY_SNAPSHOT="$1/'
                        'runtime_identity.snapshot.json"\n'
                        "SMOKE_MANUAL_LIVE_MODE=1\n"
                        "reset_current_smoke_artifacts\n"
                    ),
                    "smoke-manual-current-reset-test",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                result.returncode,
                result.stdout + result.stderr,
            )
            self.assertFalse((root / "latest_telemetry.json").exists())
            for artifact in (
                "latest_modulation.json",
                "latest_modulation.kv",
                "latest_modulation_compile_result.json",
                "modulation_updates.jsonl",
            ):
                self.assertEqual(
                    "active-command\n",
                    (root / artifact).read_text(),
                    artifact,
                )

    def test_smoke_modulation_evidence_ignores_pre_run_matching_id(self) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index(
            "def first_modulation_issued_at_frame(update_id):"
        )
        function_end = script.index(
            "\naggressive_first_issued_at_frame =",
            function_start,
        )
        function_code = script[function_start:function_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            telemetry = root / "latest_telemetry.json"
            telemetry.write_text("{}\n")
            archive = root / "modulation_updates.jsonl"
            stale = {
                "update_id": "smoke-bio_pressure-0",
                "issued_at_frame": 0,
            }
            archive.write_text(json.dumps(stale) + "\n")
            start_offset = archive.stat().st_size
            with archive.open("a") as handle:
                handle.write(
                    json.dumps(
                        {
                            "update_id": "fresh-different-update",
                            "issued_at_frame": 100,
                        }
                    )
                    + "\n"
                )

            check_code = (
                "import json\n"
                "from pathlib import Path\n"
                "import sys\n"
                "telemetry = Path(sys.argv[1])\n"
                "modulation_archive_start_offset = int(sys.argv[2])\n"
                f"{function_code}\n"
                "print(first_modulation_issued_at_frame(sys.argv[3]))\n"
            )
            stale_result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    check_code,
                    str(telemetry),
                    str(start_offset),
                    stale["update_id"],
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                stale_result.returncode,
                stale_result.stdout + stale_result.stderr,
            )
            self.assertEqual("None", stale_result.stdout.strip())

            with archive.open("a") as handle:
                handle.write(
                    json.dumps(
                        {
                            "update_id": stale["update_id"],
                            "issued_at_frame": 200,
                        }
                    )
                    + "\n"
                )
            fresh_result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    check_code,
                    str(telemetry),
                    str(start_offset),
                    stale["update_id"],
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                fresh_result.returncode,
                fresh_result.stdout + fresh_result.stderr,
            )
            self.assertEqual("200", fresh_result.stdout.strip())

    def test_root_readme_separates_runtime_support_from_live_qualification(
        self,
    ) -> None:
        readme = (REPO_ROOT / "README.md").read_text()
        for term in (
            "runtime support statement",
            "not a claim that every family has passed",
            "Comprehensive all-Terran live qualification",
            "Pending family-by-family evidence",
            "production -> exclusive assignment -> SC2 action",
        ):
            with self.subTest(term=term):
                self.assertIn(term, readme)
        self.assertNotIn("| All-unit operation model |", readme)

    def test_runtime_convergence_patch_exposes_actionable_force_progress(
        self,
    ) -> None:
        patch = (
            KIT_DIR
            / "patches"
            / "0067-runtime-convergence-defense-placement-information.patch"
        ).read_text()
        for term in (
            "getVoiOperationOwnedItemCount(",
            "getVoiOperationProductionProgress(",
            "voiAllocateOperationProductionProgress(",
            '\\"completed_count\\"',
            '\\"in_progress_count\\"',
            '\\"queued_count\\"',
            '\\"production_blocker\\"',
            '\\"missing_prerequisites\\"',
            "producer_busy",
            "voiIsSelfRampLocationIntent",
            "voiShouldReplaceOperationTarget",
            "voiAutonomousDefenseMayClaim",
            "voiSelectSemanticPlacementCandidate",
        ):
            with self.subTest(term=term):
                self.assertIn(term, patch)
        self.assertEqual(
            1,
            patch.count("COMMAND voi_runtime_convergence_test"),
        )

    def test_operation_lifecycle_accepts_temporally_separated_live_evidence(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("operation_lifecycle_ready() {")
        code_start = script.index(
            "import json\nimport sys\nfrom pathlib import Path\n",
            function_start,
        )
        code_end = script.index("\nPY\n}", code_start)
        lifecycle_check = script[code_start:code_end]

        def operation(
            operation_id: str,
            tag: int | None,
            max_home_distance: float,
        ) -> dict[str, object]:
            tags = [] if tag is None else [tag]
            return {
                "operation_id": operation_id,
                "generation": 1,
                "assigned_unit_tags": tags,
                "assigned_count": len(tags),
                "assigned_frame": 100 if tags else 0,
                "submission_observed": True,
                "submitted_frame": 101,
                "max_home_distance": max_home_distance,
                "engaged": False,
                "last_action_frame": 102,
            }

        def telemetry_entry(
            frame: int,
            scout_tag: int | None,
            attack_tag: int | None,
            scout_distance: float,
            attack_distance: float,
            owned_unit_count: int,
            owned_queue_count: int,
        ) -> dict[str, object]:
            return {
                "frame": frame,
                "managers": {
                    "OperationDirector": {
                        "policy_update_id": "smoke-parallel-operations",
                        "owned_unit_count": owned_unit_count,
                        "operations": [
                            operation(
                                "smoke-scout-alpha",
                                scout_tag,
                                scout_distance,
                            ),
                            operation(
                                "smoke-attack-bravo",
                                attack_tag,
                                attack_distance,
                            ),
                        ],
                    },
                    "ProductionManager": {
                        "policy_update_id": "smoke-parallel-operations",
                        "operation_owned_queue_item_count": owned_queue_count,
                        "operation_owned_queue_items": (
                            [
                                {
                                    "item": "Factory",
                                    "claim_source": "operation",
                                    "exclusive_operation_owned": True,
                                    "preserve_without_operation_owners": False,
                                    "owners": [
                                        {
                                            "operation_id": (
                                                "smoke-attack-bravo"
                                            ),
                                            "generation": 1,
                                        }
                                    ],
                                }
                            ]
                            if owned_queue_count
                            else []
                        ),
                    },
                },
            }

        assignment = telemetry_entry(100, 101, 202, 0.0, 0.0, 2, 0)
        movement = telemetry_entry(110, None, None, 9.0, 13.0, 0, 0)
        production = telemetry_entry(120, None, None, 9.0, 13.0, 0, 1)

        def run_check(
            root: Path,
            entries: list[dict[str, object]],
            latest: dict[str, object],
        ) -> subprocess.CompletedProcess[str]:
            telemetry = root / "latest_telemetry.json"
            telemetry.write_text(json.dumps(latest))
            telemetry.with_name("telemetry.jsonl").write_text(
                "\n".join(json.dumps(entry) for entry in entries) + "\n"
            )
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    lifecycle_check,
                    str(telemetry),
                    "smoke-parallel-operations",
                    "smoke-scout-alpha",
                    "smoke-attack-bravo",
                    "active",
                    "0",
                    "0",
                    "0",
                    "0",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            separated = run_check(
                root,
                [assignment, movement, production],
                production,
            )
            self.assertEqual(
                0,
                separated.returncode,
                separated.stdout + separated.stderr,
            )

            (root / "latest_telemetry.json").write_text("")
            archived_only = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    lifecycle_check,
                    str(root / "latest_telemetry.json"),
                    "smoke-parallel-operations",
                    "smoke-scout-alpha",
                    "smoke-attack-bravo",
                    "active",
                    "0",
                    "0",
                    "0",
                    "0",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                archived_only.returncode,
                archived_only.stdout + archived_only.stderr,
            )

            missing_production = run_check(
                root,
                [assignment],
                movement,
            )
            self.assertNotEqual(0, missing_production.returncode)

            overlapping_assignment = telemetry_entry(
                100,
                101,
                101,
                0.0,
                0.0,
                1,
                0,
            )
            overlapping = run_check(
                root,
                [overlapping_assignment, movement],
                production,
            )
            self.assertNotEqual(0, overlapping.returncode)

            preserved_production = telemetry_entry(
                120,
                None,
                None,
                9.0,
                13.0,
                0,
                1,
            )
            preserved_queue = preserved_production["managers"][
                "ProductionManager"
            ]["operation_owned_queue_items"][0]
            preserved_queue["exclusive_operation_owned"] = False
            preserved_queue["preserve_without_operation_owners"] = True
            preserved = run_check(
                root,
                [assignment, movement],
                preserved_production,
            )
            self.assertNotEqual(0, preserved.returncode)

    def test_operation_production_baseline_accepts_purgeable_prerequisite(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("operation_production_baseline() {")
        code_start = script.index(
            "import json\nimport sys\nfrom pathlib import Path\n",
            function_start,
        )
        code_end = script.index("\nPY\n}", code_start)
        baseline_check = script[code_start:code_end]

        def run_check(
            root: Path,
            *,
            exclusive: bool,
            preserve: bool,
        ) -> subprocess.CompletedProcess[str]:
            telemetry = root / "latest_telemetry.json"
            telemetry.write_text(
                json.dumps(
                    {
                        "managers": {
                            "ProductionManager": {
                                "operation_owned_queue_items": [
                                    {
                                        "item": "Factory",
                                        "claim_source": "operation",
                                        "exclusive_operation_owned": exclusive,
                                        "preserve_without_operation_owners": (
                                            preserve
                                        ),
                                        "owners": [
                                            {
                                                "operation_id": (
                                                    "smoke-attack-bravo"
                                                ),
                                                "generation": 1,
                                            }
                                        ],
                                    }
                                ],
                                "operation_production_owner_release_count": 2,
                                "operation_production_queue_purge_count": 3,
                                "last_operation_production_queue_purge_frame": 4,
                            }
                        }
                    }
                )
            )
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    baseline_check,
                    str(telemetry),
                    "smoke-attack-bravo",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prerequisite = run_check(
                root,
                exclusive=True,
                preserve=False,
            )
            self.assertEqual(
                0,
                prerequisite.returncode,
                prerequisite.stdout + prerequisite.stderr,
            )
            self.assertEqual("2\t3\t4\n", prerequisite.stdout)

            preserved = run_check(
                root,
                exclusive=False,
                preserve=True,
            )
            self.assertNotEqual(0, preserved.returncode)

    def test_operation_lifecycle_waits_for_async_cancel_reconciliation(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("operation_lifecycle_ready() {")
        code_start = script.index(
            "import json\nimport sys\nfrom pathlib import Path\n",
            function_start,
        )
        code_end = script.index("\nPY\n}", code_start)
        lifecycle_check = script[code_start:code_end]

        def operation_state(
            operation_id: str,
            *,
            cancelled: bool,
        ) -> dict[str, object]:
            tags = [] if cancelled else [101]
            return {
                "operation_id": operation_id,
                "generation": 1,
                "status": "CANCELLED" if cancelled else "MOVING",
                "completed": cancelled,
                "assigned_unit_tags": tags,
                "assigned_count": len(tags),
            }

        def cancelled_entry(
            frame: int,
            *,
            owned_queue_count: int,
            release_count: int,
            purge_count: int,
            purge_frame: int,
            purge_operation_id: str = "smoke-attack-bravo",
            purge_generation: int = 1,
            purge_removed_count: int = 1,
            purge_events: list[dict[str, object]] | None = None,
        ) -> dict[str, object]:
            if purge_events is None:
                purge_events = [
                    {
                        "operation_id": purge_operation_id,
                        "generation": purge_generation,
                        "frame": purge_frame,
                        "removed_count": purge_removed_count,
                    }
                ]
            return {
                "frame": frame,
                "managers": {
                    "OperationDirector": {
                        "policy_update_id": "smoke-parallel-operations",
                        "owned_unit_count": 1,
                        "operations": [
                            operation_state(
                                "smoke-scout-alpha",
                                cancelled=False,
                            ),
                            operation_state(
                                "smoke-attack-bravo",
                                cancelled=True,
                            ),
                        ],
                    },
                    "ProductionManager": {
                        "operation_owned_queue_item_count": owned_queue_count,
                        "operation_owned_queue_items": (
                            [
                                {
                                    "item": "SiegeTank",
                                    "claim_source": "operation",
                                    "preserve_without_operation_owners": False,
                                    "owners": [
                                        {
                                            "operation_id": (
                                                "smoke-attack-bravo"
                                            ),
                                            "generation": 1,
                                        }
                                    ],
                                }
                            ]
                            if owned_queue_count
                            else []
                        ),
                        "operation_production_owner_release_count": release_count,
                        "operation_production_queue_purge_count": purge_count,
                        "last_operation_production_queue_purge_frame": purge_frame,
                        "last_operation_production_released_owner": (
                            "smoke-attack-bravo#1"
                        ),
                        "last_operation_production_queue_purge_event": {
                            "operation_id": purge_operation_id,
                            "generation": purge_generation,
                            "frame": purge_frame,
                            "removed_count": purge_removed_count,
                        },
                        "operation_production_queue_purge_events": (
                            purge_events
                        ),
                    },
                    "CombatCommander": {
                        "main_attack_actual_command_issued_count": 7,
                        "main_attack_last_action_frame": 500,
                        "main_attack_home_distance": 18.0,
                        "main_attack_unit_samples": [],
                    },
                },
            }

        def run_check(
            root: Path,
            entries: list[dict[str, object]],
            latest: dict[str, object],
        ) -> subprocess.CompletedProcess[str]:
            telemetry = root / "latest_telemetry.json"
            telemetry.write_text(json.dumps(latest))
            telemetry.with_name("telemetry.jsonl").write_text(
                "\n".join(json.dumps(entry) for entry in entries) + "\n"
            )
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    lifecycle_check,
                    str(telemetry),
                    "smoke-parallel-operations",
                    "smoke-scout-alpha",
                    "smoke-attack-bravo",
                    "cancelled_baseline",
                    "400",
                    "5",
                    "9",
                    "300",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        cancelled_before_production_reconcile = cancelled_entry(
            500,
            owned_queue_count=1,
            release_count=5,
            purge_count=9,
            purge_frame=300,
        )
        reconciled_after_manager_tick = cancelled_entry(
            520,
            owned_queue_count=0,
            release_count=6,
            purge_count=10,
            purge_frame=510,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reconciled = run_check(
                root,
                [cancelled_before_production_reconcile],
                reconciled_after_manager_tick,
            )
            self.assertEqual(
                0,
                reconciled.returncode,
                reconciled.stdout + reconciled.stderr,
            )
            self.assertTrue(
                reconciled.stdout.startswith("520\t"),
                reconciled.stdout,
            )
            self.assertTrue(
                reconciled.stdout.rstrip().endswith("\t[]"),
                reconciled.stdout,
            )

            same_frame_purge_history = cancelled_entry(
                520,
                owned_queue_count=0,
                release_count=7,
                purge_count=11,
                purge_frame=510,
                purge_operation_id="smoke-scout-alpha",
                purge_events=[
                    {
                        "operation_id": "smoke-attack-bravo",
                        "generation": 1,
                        "frame": 510,
                        "removed_count": 1,
                    },
                    {
                        "operation_id": "smoke-scout-alpha",
                        "generation": 1,
                        "frame": 510,
                        "removed_count": 1,
                    },
                ],
            )
            same_frame_reconciled = run_check(
                root,
                [cancelled_before_production_reconcile],
                same_frame_purge_history,
            )
            self.assertEqual(
                0,
                same_frame_reconciled.returncode,
                same_frame_reconciled.stdout
                + same_frame_reconciled.stderr,
            )

            unreconciled = run_check(
                root,
                [],
                cancelled_before_production_reconcile,
            )
            self.assertNotEqual(0, unreconciled.returncode)

            wrong_owner_purge = cancelled_entry(
                520,
                owned_queue_count=0,
                release_count=6,
                purge_count=10,
                purge_frame=510,
                purge_operation_id="smoke-scout-alpha",
            )
            cross_owner = run_check(
                root,
                [cancelled_before_production_reconcile],
                wrong_owner_purge,
            )
            self.assertNotEqual(0, cross_owner.returncode)

            zero_item_release = cancelled_entry(
                520,
                owned_queue_count=0,
                release_count=6,
                purge_count=10,
                purge_frame=510,
                purge_removed_count=0,
            )
            no_positive_purge = run_check(
                root,
                [cancelled_before_production_reconcile],
                zero_item_release,
            )
            self.assertNotEqual(0, no_positive_purge.returncode)

    def test_operation_restore_proof_tracks_post_restore_unit_displacement(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("operation_restore_ready() {")
        code_start = script.index(
            "import json\nimport os\nimport sys\nfrom pathlib import Path\n",
            function_start,
        )
        code_end = script.index("\nPY\n}", code_start)
        restore_check = script[code_start:code_end]

        def entry(
            frame: int,
            *,
            command_count: int,
            command_frame: int,
            status: str,
            command: str,
            home_distance: float,
            x: float,
        ) -> dict[str, object]:
            return {
                "frame": frame,
                "managers": {
                    "GameCommander": {
                        "policy_active": True,
                        "update_id": "smoke-restore",
                    },
                    "CombatCommander": {
                        "main_attack_actual_command_issued_count": command_count,
                        "main_attack_last_action_frame": command_frame,
                        "main_attack_last_issued_action": command,
                        "main_attack_order_status": status,
                        "main_attack_home_distance": home_distance,
                        "main_attack_unit_count": 1,
                        "main_attack_scope_min_units": 1,
                        "main_attack_unit_samples": [
                            {"tag": 303, "x": x, "y": 10.0}
                        ],
                    },
                },
            }

        def run_check(
            root: Path,
            entries: list[dict[str, object]],
        ) -> subprocess.CompletedProcess[str]:
            telemetry = root / "latest_telemetry.json"
            telemetry.write_text(json.dumps(entries[-1]))
            telemetry.with_name("telemetry.jsonl").write_text(
                "\n".join(json.dumps(item) for item in entries) + "\n"
            )
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    restore_check,
                    str(telemetry),
                    "smoke-restore",
                    "600",
                    "7",
                    "500",
                    "0.0",
                    "[]",
                    "12.0",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        first_observation = entry(
            600,
            command_count=7,
            command_frame=500,
            status="Waiting",
            command="",
            home_distance=0.0,
            x=10.0,
        )
        moved_under_restore = entry(
            620,
            command_count=8,
            command_frame=610,
            status="Attack",
            command="AttackMove|squad=MainAttack|x=40|y=50",
            home_distance=13.0,
            x=15.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            restored = run_check(
                root,
                [first_observation, moved_under_restore],
            )
            self.assertEqual(
                0,
                restored.returncode,
                restored.stdout + restored.stderr,
            )

            no_displacement = run_check(
                root,
                [moved_under_restore],
            )
            self.assertNotEqual(0, no_displacement.returncode)

    def test_final_smoke_validator_derives_restore_epoch_position_baseline(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        validator_start = script.index(
            "def profile_main_attack_command_seen():"
        )
        validator_end = script.index(
            "\nif payload.get(\"protocol_version\")",
            validator_start,
        )
        validator_code = script[validator_start:validator_end]
        entries = [
            {
                "frame": 620,
                "managers": {
                    "GameCommander": {"update_id": "smoke-restore"},
                    "CombatCommander": {
                        "main_attack_actual_command_issued_count": 8,
                        "main_attack_last_action_frame": 610,
                        "main_attack_last_issued_action": (
                            "AttackMove|squad=MainAttack|x=40|y=50"
                        ),
                        "main_attack_order_status": "Attack",
                        "main_attack_unit_count": 1,
                        "main_attack_scope_min_units": 1,
                        "main_attack_scope_threshold_met": True,
                        "main_attack_simulation_won": True,
                        "main_attack_max_home_distance": 15.0,
                        "main_attack_home_distance": 13.0,
                        "main_attack_unit_samples": [
                            {"tag": 303, "x": 15.0, "y": 10.0}
                        ],
                    },
                },
            },
            {
                "frame": 600,
                "managers": {
                    "GameCommander": {"update_id": "smoke-restore"},
                    "CombatCommander": {
                        "main_attack_actual_command_issued_count": 7,
                        "main_attack_last_action_frame": 500,
                        "main_attack_last_issued_action": "",
                        "main_attack_order_status": "Waiting",
                        "main_attack_unit_count": 1,
                        "main_attack_scope_min_units": 1,
                        "main_attack_scope_threshold_met": True,
                        "main_attack_simulation_won": True,
                        "main_attack_max_home_distance": 0.0,
                        "main_attack_home_distance": 0.0,
                        "main_attack_unit_samples": [
                            {"tag": 303, "x": 10.0, "y": 10.0}
                        ],
                    },
                },
            },
        ]
        namespace = {
            "aggressive_update_id": "smoke-restore",
            "aggressive_first_consumed_frame": 600,
            "aggressive_first_issued_at_frame": 600,
            "iter_telemetry_entries": lambda: iter(entries),
            "min_main_attack_home_distance": 12.0,
            "min_post_cancel_main_attack_displacement": 4.0,
            "post_cancel_action_frame_baseline": 500,
            "post_cancel_command_baseline": 7,
            "post_cancel_unit_positions": {},
            "require_operation_lifecycle": True,
            "restore_issued_at_frame": 600,
            "unit_positions": lambda samples: {
                int(sample["tag"]): (
                    float(sample["x"]),
                    float(sample["y"]),
                )
                for sample in samples
            },
        }
        exec(validator_code, namespace)
        seen, evidence = namespace[
            "profile_main_attack_command_seen"
        ]()
        self.assertTrue(seen, evidence)
        self.assertEqual([303], evidence["post_cancel_matching_unit_tags"])
        self.assertEqual(
            5.0,
            evidence["post_cancel_main_attack_displacement"],
        )

    def test_final_smoke_validator_enforces_single_tech_gas_bootstrap_order(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        validator_start = script.index(
            "def verify_tech_gas_before_second_barracks():"
        )
        validator_end = script.index(
            "\nverify_tech_gas_before_second_barracks()",
            validator_start,
        )
        validator_code = script[validator_start:validator_end]
        telemetry_entries = [
            {
                "frame": 400,
                "managers": {
                    "WorkerManager": {
                        "completed_refinery_count": 1,
                    }
                },
            }
        ]

        def run_check(log_text: str) -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_path = Path(tmpdir) / "micromachine.log"
                log_path.write_text(log_text)
                namespace = {
                    "bot_log": log_path,
                    "expected_strategy_doctrine": "bio_pressure",
                    "iter_telemetry_entries": (
                        lambda: iter(telemetry_entries)
                    ),
                    "re": re,
                }
                exec(validator_code, namespace)
                namespace[
                    "verify_tech_gas_before_second_barracks"
                ]()

        run_check(
            "100: recordVoiActualProductionCommand | "
            "voi actual production command kind=build_command item=Barracks "
            "update_id=smoke\n"
            "200: constructAssignedBuildings | "
            "build command type=TERRAN_REFINERY\n"
            "300: recordVoiActualProductionCommand | "
            "voi actual production command kind=build_command item=Barracks "
            "update_id=smoke\n"
        )

        with self.assertRaises(SystemExit):
            run_check(
                "100: recordVoiActualProductionCommand | "
                "voi actual production command kind=build_command item=Barracks "
                "update_id=smoke\n"
                "200: constructAssignedBuildings | "
                "build command type=TERRAN_REFINERY\n"
                "250: constructAssignedBuildings | "
                "build command type=TERRAN_REFINERY\n"
            )

        with self.assertRaises(SystemExit):
            run_check(
                "100: recordVoiActualProductionCommand | "
                "voi actual production command kind=build_command item=Barracks "
                "update_id=smoke\n"
                "150: recordVoiActualProductionCommand | "
                "voi actual production command kind=build_command item=Barracks "
                "update_id=smoke\n"
                "200: constructAssignedBuildings | "
                "build command type=TERRAN_REFINERY\n"
            )

    def test_smoke_cleanup_preserves_last_valid_telemetry_atomically(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        snapshot_start = script.index(
            "snapshot_latest_telemetry_for_cleanup() {"
        )
        snapshot_code_start = script.index(
            "import json\nimport os\nimport sys\nimport time\nfrom pathlib import Path\n",
            snapshot_start,
        )
        snapshot_code_end = script.index(
            "\nPY\n}",
            snapshot_code_start,
        )
        snapshot_code = script[snapshot_code_start:snapshot_code_end]

        restore_start = script.index(
            "restore_latest_telemetry_after_cleanup() {"
        )
        restore_code_start = script.index(
            "import os\nimport sys\nfrom pathlib import Path\n",
            restore_start,
        )
        restore_code_end = script.index(
            "\nPY\n}",
            restore_code_start,
        )
        restore_code = script[restore_code_start:restore_code_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            latest = root / "latest_telemetry.json"
            archive = root / "telemetry.jsonl"
            snapshot = root / "latest_telemetry.pre_cleanup.json"
            latest.write_text("")
            archive.write_text(
                json.dumps({"frame": 11})
                + "\n"
                + "{partial\n"
                + json.dumps({"frame": 22})
                + "\n"
            )

            archived_fallback = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    snapshot_code,
                    str(latest),
                    str(archive),
                    str(snapshot),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                archived_fallback.returncode,
                archived_fallback.stdout + archived_fallback.stderr,
            )
            self.assertEqual(22, json.loads(snapshot.read_text())["frame"])

            restored = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    restore_code,
                    str(snapshot),
                    str(latest),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                restored.returncode,
                restored.stdout + restored.stderr,
            )
            self.assertEqual(22, json.loads(latest.read_text())["frame"])
            self.assertFalse(snapshot.exists())

            latest.write_text(json.dumps({"frame": 33}))
            latest_preferred = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    snapshot_code,
                    str(latest),
                    str(archive),
                    str(snapshot),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                latest_preferred.returncode,
                latest_preferred.stdout + latest_preferred.stderr,
            )
            self.assertEqual(33, json.loads(snapshot.read_text())["frame"])

    def test_smoke_runtime_identity_snapshot_rejects_report_or_binary_change(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        snapshot_start = script.index("snapshot_runtime_identity() {")
        snapshot_code_start = script.index(
            "import hashlib\nimport json\nimport os\nimport subprocess\nimport sys\nfrom pathlib import Path\n",
            snapshot_start,
        )
        snapshot_code_end = script.index(
            "\nPY\n}",
            snapshot_code_start,
        )
        snapshot_code = script[snapshot_code_start:snapshot_code_end]

        verify_start = script.index("verify_runtime_identity_snapshot() {")
        verify_code_start = script.index(
            "import hashlib\nimport json\nimport subprocess\nimport sys\nfrom pathlib import Path\n",
            verify_start,
        )
        verify_code_end = script.index(
            "\nPY\n}",
            verify_code_start,
        )
        verify_code = script[verify_code_start:verify_code_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "voi_build_identity.json"
            binary = root / "MicroMachine"
            snapshot = root / "runtime_identity.snapshot.json"
            smoke_script = root / "smoke_macos_local.sh"
            smoke_script.write_text("#!/usr/bin/env bash\nexit 0\n")
            runtime_package = root / "starcraft_commander"
            runtime_package.mkdir()
            runtime_source = runtime_package / "runtime.py"
            runtime_source.write_text("VALUE = 1\n")
            (root / "pyproject.toml").write_text(
                "[project]\nname='runtime-fixture'\n"
            )
            subprocess.run(
                ["git", "-C", str(root), "init", "-q"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Test User"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "config",
                    "user.email",
                    "test@example.com",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "add", "."],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-q", "-m", "fixture"],
                check=True,
            )
            head_sha = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            binary.write_bytes(b"compiled-binary-v1")
            binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
            report.write_text(
                json.dumps(
                    {
                        "schema_version": 54,
                        "identity": "sha256:fixture",
                        "ok": True,
                        "checksums": {
                            "binary_sha256": binary_sha256,
                        },
                        "observed": {
                            "micromachine_source_state_sha256": (
                                "source-fixture"
                            ),
                        },
                    }
                )
            )

            captured = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    snapshot_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                captured.returncode,
                captured.stdout + captured.stderr,
            )
            payload = json.loads(snapshot.read_text())
            self.assertEqual("sha256:fixture", payload["build_identity"])
            self.assertEqual(binary_sha256, payload["binary_sha256"])
            self.assertEqual("run-fixture", payload["run_id"])
            self.assertEqual(head_sha, payload["repo_head_sha"])
            self.assertEqual(
                str(smoke_script.resolve()),
                payload["smoke_script"],
            )
            self.assertEqual(
                hashlib.sha256(smoke_script.read_bytes()).hexdigest(),
                payload["smoke_script_sha256"],
            )
            self.assertTrue(
                str(payload["python_runtime_source_identity"]).startswith(
                    "sha256:"
                )
            )
            self.assertEqual(
                [
                    "pyproject.toml",
                    "starcraft_commander/runtime.py",
                ],
                [
                    entry["path"]
                    for entry in payload["python_runtime_source_files"]
                ],
            )

            unchanged = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                unchanged.returncode,
                unchanged.stdout + unchanged.stderr,
            )

            runtime_source.write_text("VALUE = 2\n")
            changed_runtime_source = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, changed_runtime_source.returncode)
            runtime_source.write_text("VALUE = 1\n")

            report.write_text(report.read_text() + "\n")
            changed_report = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, changed_report.returncode)

            report.write_text(report.read_text().rstrip() + "\n")
            recaptured = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    snapshot_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                recaptured.returncode,
                recaptured.stdout + recaptured.stderr,
            )
            binary.write_bytes(b"compiled-binary-v2")
            changed_binary = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, changed_binary.returncode)

            binary.write_bytes(b"compiled-binary-v1")
            recaptured = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    snapshot_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                recaptured.returncode,
                recaptured.stdout + recaptured.stderr,
            )
            changed_run = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "other-run",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, changed_run.returncode)
            changed_head = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    "other-head",
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, changed_head.returncode)
            smoke_script.write_text("#!/usr/bin/env bash\nexit 1\n")
            changed_script = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, changed_script.returncode)

            smoke_script.write_text("#!/usr/bin/env bash\nexit 0\n")
            (root / "head-marker.txt").write_text("new head\n")
            subprocess.run(
                ["git", "-C", str(root), "add", "head-marker.txt"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "commit",
                    "-q",
                    "-m",
                    "change head",
                ],
                check=True,
            )
            changed_checkout_head = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    verify_code,
                    str(report),
                    str(binary),
                    str(snapshot),
                    "run-fixture",
                    head_sha,
                    str(smoke_script),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, changed_checkout_head.returncode)
            self.assertIn(
                "repository HEAD changed during live run",
                changed_checkout_head.stdout + changed_checkout_head.stderr,
            )

    def test_smoke_parallel_operations_persist_until_selective_cancel(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("publish_parallel_operations() {")
        code_start = script.index(
            "import sys\nfrom pathlib import Path\n",
            function_start,
        )
        code_end = script.index("\nPY\n}", code_start)
        publish_code = script[code_start:code_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = {
                **os.environ,
                "PYTHONPATH": (
                    f"{REPO_ROOT}:{os.environ.get('PYTHONPATH', '')}"
                ),
            }

            def publish(state: str, frame: int) -> dict[str, object]:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        publish_code,
                        str(root),
                        "smoke-parallel-operations",
                        "smoke-scout-alpha",
                        "smoke-attack-bravo",
                        str(frame),
                        state,
                    ],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    0,
                    result.returncode,
                    result.stdout + result.stderr,
                )
                return json.loads(
                    (root / "latest_modulation.json").read_text()
                )

            active = publish("active", 500)
            active_operations = {
                operation["operation_id"]: operation
                for operation in active["vector"]["operations"]
            }
            for operation_id in (
                "smoke-scout-alpha",
                "smoke-attack-bravo",
            ):
                lifetime = active_operations[operation_id]["lifetime"]
                self.assertEqual("until_cancelled", lifetime["mode"])
                self.assertEqual(
                    ["cancelled_by_user"],
                    lifetime["completion_conditions"],
                )
                self.assertEqual("active", lifetime["completion_state"])

            cancelled = publish("cancelled", 600)
            cancelled_operations = {
                operation["operation_id"]: operation
                for operation in cancelled["vector"]["operations"]
            }
            self.assertEqual(
                "cancelled",
                cancelled_operations["smoke-attack-bravo"]["lifetime"][
                    "completion_state"
                ],
            )
            self.assertEqual(
                "active",
                cancelled_operations["smoke-scout-alpha"]["lifetime"][
                    "completion_state"
                ],
            )
            attack = active_operations["smoke-attack-bravo"]
            self.assertTrue(attack["tactical_task"]["allow_partial"])
            self.assertTrue(attack["scope"]["allow_partial_scope"])
            self.assertEqual(
                [
                    {
                        "unit_type": "TERRAN_MARINE",
                        "count": 4,
                        "role": "frontline",
                    },
                    {
                        "unit_type": "TERRAN_SIEGETANK",
                        "count": 4,
                        "role": "siege_support",
                    }
                ],
                attack["composition_requirements"],
            )
            scout = active_operations["smoke-scout-alpha"]
            self.assertFalse(scout["tactical_task"]["allow_partial"])
            self.assertFalse(scout["scope"]["allow_partial_scope"])
            self.assertEqual(
                [
                    {
                        "unit_type": "TERRAN_MARINE",
                        "count": 3,
                        "role": "scout",
                    }
                ],
                scout["composition_requirements"],
            )

    def test_smoke_publishes_operations_when_aggressive_profile_is_ready(
        self,
    ) -> None:
        script = SMOKE_SCRIPT.read_text()
        function_start = script.index("operation_publish_ready() {")
        code_start = script.index("import json\n", function_start)
        code_end = script.index("\nPY\n}", code_start)
        readiness_code = script[code_start:code_end]

        with tempfile.TemporaryDirectory() as tmpdir:
            telemetry = Path(tmpdir) / "latest_telemetry.json"

            def ready(
                *,
                frame: object = 2600,
                update_id: object = "smoke-aggressive-pressure",
                policy_active: object = True,
                combat_unit_count: object = 2,
                expected_update_id: str = "smoke-aggressive-pressure",
                minimum_frame: int = 2600,
            ) -> bool:
                telemetry.write_text(
                    json.dumps(
                        {
                            "frame": frame,
                            "managers": {
                                "GameCommander": {
                                    "policy_active": policy_active,
                                    "update_id": update_id,
                                },
                                "CombatCommander": {
                                    "combat_unit_count": combat_unit_count,
                                },
                            },
                        }
                    )
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        readiness_code,
                        str(telemetry),
                        expected_update_id,
                        str(minimum_frame),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                return result.returncode == 0

            self.assertTrue(ready())
            self.assertFalse(ready(frame=2599))
            self.assertFalse(ready(policy_active=False))
            self.assertFalse(ready(update_id="stale-policy"))
            self.assertFalse(ready(combat_unit_count=1))
            self.assertFalse(ready(combat_unit_count={"invalid": True}))

        publish_block = (
            'if [[ "${SMOKE_REQUIRE_OPERATION_DIRECTOR_LIFECYCLE}" == "1" '
            '&& "${OPERATION_LIFECYCLE_PHASE}" == "pending" '
            '&& "${AGGRESSIVE_PROFILE_PUBLISHED}" -eq 1 ]] '
            "&& has_required_macro_evidence "
            '&& operation_publish_ready "${SMOKE_STRATEGY_EVIDENCE_UPDATE_ID}" '
            '"${AGGRESSIVE_PROFILE_FRAME}"; then'
        )
        self.assertIn(publish_block, script)
        self.assertLess(
            script.index(publish_block),
            script.index(
                'if python3 - "${BLACKBOARD_DIR}/latest_telemetry.json" '
                '"${MIN_TELEMETRY_FRAME}"'
            ),
        )
        self.assertEqual(
            1,
            script.count(
                'publish_parallel_operations "${current_telemetry_frame}" "active"'
            ),
        )

    def test_production_soak_defaults_exclude_known_blocker_maps(self) -> None:
        soak_matrix_script = SOAK_MATRIX_SCRIPT.read_text()
        workflow = LOCAL_SOAK_WORKFLOW.read_text()
        production_ops = (REPO_ROOT / "docs" / "micromachine-production-ops.md").read_text()
        readme = (KIT_DIR / "README.md").read_text()
        map_pool = json.loads((KIT_DIR / "MICROMACHINE_MAP_POOL.json").read_text())

        self.assertIn(
            'SOAK_MATRIX_QUALIFICATION_TIER="${SOAK_MATRIX_QUALIFICATION_TIER:-production}"',
            soak_matrix_script,
        )
        self.assertIn("starcraft_commander.micromachine_map_pool", soak_matrix_script)
        self.assertIn("qualification_tier:", workflow)
        self.assertIn('default: "production"', workflow)
        self.assertIn('default: ""', workflow)
        self.assertIn("INPUT_MAPS:", workflow)
        self.assertIn("validate_required qualification_tier", workflow)
        self.assertIn("validate_optional maps", workflow)
        self.assertNotIn('[[ -n "${{ inputs.maps }}" ]]', workflow)
        self.assertIn(
            "Empty uses map-pool tier default.",
            workflow,
        )
        self.assertIn(
            'allow_failures:\n'
            '        description: "Optional override. Set to 1 only for diagnostic or negative-control runs, never production sign-off. Empty uses map-pool tier default."\n'
            "        required: false\n"
            '        default: ""',
            workflow,
        )
        self.assertNotIn(
            'SOAK_MATRIX_MAP_FILES="${SOAK_MATRIX_MAP_FILES:-AcropolisLE.SC2Map Ladder2019Season3/ThunderbirdLE.SC2Map}"',
            soak_matrix_script,
        )
        self.assertIn("MICROMACHINE_MAP_POOL.json", production_ops)
        self.assertIn("SOAK_MATRIX_QUALIFICATION_TIER=production", production_ops)
        self.assertIn("MICROMACHINE_MAP_POOL.json", readme)
        self.assertIn("SOAK_MATRIX_QUALIFICATION_TIER=production", readme)
        self.assertIn("SOAK_PROFILE_SEQUENCE", readme)
        self.assertIn("python3 -m starcraft_commander.micromachine_release_gate", readme)
        self.assertIn("release_gate.json", readme)
        self.assertIn("release_gate.md", readme)
        self.assertIn("map_pool_runtime_risk", (REPO_ROOT / "starcraft_commander" / "micromachine_release_gate.py").read_text())
        self.assertIn("economic_expansion@6000", readme)
        self.assertIn("strategy_profile_missing", readme)
        self.assertIn("emergency_recovery", production_ops)
        self.assertIn("thunderbird_walloff_geometry_no_production_deadlock", production_ops)
        self.assertIn("thunderbird_walloff_geometry_no_production_deadlock", readme)
        self.assertIn("docs/micromachine-thunderbird-blocker.md", production_ops)
        self.assertIn("docs/micromachine-thunderbird-blocker.md", readme)
        self.assertNotIn('SOAK_MATRIX_ENEMY_DIFFICULTIES="1 2"', production_ops)
        self.assertNotIn('SOAK_MATRIX_ENEMY_DIFFICULTIES="1 2"', readme)
        self.assertIn(
            'SOAK_MATRIX_MAP_FILES="Ladder2019Season3/ThunderbirdLE.SC2Map"',
            production_ops,
        )
        self.assertIn("SOAK_MATRIX_QUALIFICATION_TIER=diagnostic", production_ops)
        self.assertIn(
            'SOAK_MATRIX_MAP_FILES="Ladder2019Season3/ThunderbirdLE.SC2Map"',
            readme,
        )
        self.assertIn("SOAK_MATRIX_QUALIFICATION_TIER=diagnostic", readme)
        required_maps = [
            item["map_file"]
            for item in map_pool["maps"]
            if item["classification"] == "required"
        ]
        diagnostic_maps = [
            item["map_file"]
            for item in map_pool["maps"]
            if item["classification"] == "diagnostic"
        ]
        self.assertEqual(["AcropolisLE.SC2Map"], required_maps)
        self.assertEqual(["Ladder2019Season3/ThunderbirdLE.SC2Map"], diagnostic_maps)
        self.assertFalse(map_pool["contract"]["production_allows_failures"])
        self.assertEqual([1, 2], map_pool["tiers"]["extended"]["enemy_difficulties"])
        self.assertEqual(["required"], map_pool["tiers"]["extended"]["map_classifications"])
        diagnostic_entry = next(
            item for item in map_pool["maps"] if item["classification"] == "diagnostic"
        )
        self.assertEqual(
            "thunderbird_walloff_geometry_no_production_deadlock",
            diagnostic_entry["blocker"]["code"],
        )
        self.assertEqual(
            "no_production_deadlock",
            diagnostic_entry["blocker"]["runtime_failure_code"],
        )
        excluded_maps = [
            item["map_file"]
            for item in map_pool["maps"]
            if item["classification"] == "excluded"
        ]
        self.assertEqual(["Custom/UnknownOrUnvetted.SC2Map"], excluded_maps)

    def test_soak_matrix_aggregate_preserves_nested_attempt_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            passed_case = root / "01-AcropolisLE-SC2Map-Protoss-d1"
            failed_case = root / "02-ThunderbirdLE-SC2Map-Zerg-d1"
            passed_case.mkdir()
            failed_case.mkdir()
            (passed_case / "soak_report.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "ok": True,
                        "latest_frame": 12042,
                        "macro_evidence_ok": True,
                        "manager_intervention_ok": True,
                        "selected_attempt": 1,
                        "artifact_manifest": {"telemetry_archive": "attempt-1/telemetry.jsonl"},
                    }
                )
            )
            (failed_case / "soak_report.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "ok": False,
                        "attempts": [
                            {
                                "attempt": 1,
                                "status": "failed",
                                "latest_frame": 7074,
                                "failures": [
                                    {
                                        "code": "no_production_deadlock",
                                        "message": "Opening production evidence did not appear.",
                                        "severity": "terminal",
                                    }
                                ],
                            },
                            {"attempt": 2, "status": "not_run"},
                        ],
                    }
                )
            )

            report = root / "matrix_report.json"
            env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(root),
                "SOAK_MATRIX_REPORT": str(report),
                "SOAK_MATRIX_TARGET_FRAME": "12000",
                "SOAK_MATRIX_TIMEOUT_SECONDS": "1200",
                "SOAK_MATRIX_AGGREGATE_ONLY": "1",
                "SOAK_MATRIX_QUALIFICATION_TIER": "diagnostic",
                "SOAK_MATRIX_ALLOW_FAILURES": "1",
            }

            subprocess.run([str(SOAK_MATRIX_SCRIPT)], check=True, env=env)

            payload = json.loads(report.read_text())
            self.assertFalse(payload["ok"])
            self.assertEqual(1, payload["passed"])
            self.assertEqual(1, payload["failed"])
            failed_payload = payload["cases"][1]
            self.assertEqual(["no_production_deadlock"], failed_payload["failure_codes"])
            self.assertEqual("no_production_deadlock", failed_payload["failures"][0]["code"])
            self.assertEqual(1, failed_payload["failures"][0]["attempt"])

    def test_soak_matrix_allow_failures_rejects_zero_pass_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            failed_case = root / "01-ThunderbirdLE-SC2Map-Zerg-d1"
            failed_case.mkdir()
            (failed_case / "soak_report.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "ok": False,
                        "failures": [{"code": "no_production_deadlock"}],
                    }
                )
            )
            report = root / "matrix_report.json"
            env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(root),
                "SOAK_MATRIX_REPORT": str(report),
                "SOAK_MATRIX_AGGREGATE_ONLY": "1",
                "SOAK_MATRIX_QUALIFICATION_TIER": "diagnostic",
                "SOAK_MATRIX_ALLOW_FAILURES": "1",
            }

            completed = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("still requires at least 1 passing case", completed.stdout)

    def test_soak_matrix_uses_manifest_allow_failures_only_for_diagnostic_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            failed_case = root / "01-ThunderbirdLE-SC2Map-Zerg-d1"
            failed_case.mkdir()
            (failed_case / "soak_report.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "ok": False,
                        "failures": [{"code": "no_production_deadlock"}],
                    }
                )
            )
            common_env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(root),
                "SOAK_MATRIX_REPORT": str(root / "matrix_report.json"),
                "SOAK_MATRIX_AGGREGATE_ONLY": "1",
                "SOAK_MATRIX_MIN_PASSES": "0",
            }

            production = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env={**common_env, "SOAK_MATRIX_QUALIFICATION_TIER": "production"},
                text=True,
                capture_output=True,
                check=False,
            )
            diagnostic = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env={**common_env, "SOAK_MATRIX_QUALIFICATION_TIER": "diagnostic"},
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, production.returncode)
            self.assertEqual(0, diagnostic.returncode, diagnostic.stdout + diagnostic.stderr)

    def test_soak_matrix_rejects_allow_failures_for_production_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "matrix_report.json"
            env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(root),
                "SOAK_MATRIX_REPORT": str(report),
                "SOAK_MATRIX_AGGREGATE_ONLY": "1",
                "SOAK_MATRIX_QUALIFICATION_TIER": "production",
                "SOAK_MATRIX_ALLOW_FAILURES": "1",
            }

            completed = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(2, completed.returncode)
            self.assertIn("production tier cannot set SOAK_MATRIX_ALLOW_FAILURES=1", completed.stderr)

    def test_soak_matrix_rejects_allow_failures_for_extended_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "matrix_report.json"
            env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(root),
                "SOAK_MATRIX_REPORT": str(report),
                "SOAK_MATRIX_AGGREGATE_ONLY": "1",
                "SOAK_MATRIX_QUALIFICATION_TIER": "extended",
                "SOAK_MATRIX_ALLOW_FAILURES": "1",
            }

            completed = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(2, completed.returncode)
            self.assertIn("extended tier cannot set SOAK_MATRIX_ALLOW_FAILURES=1", completed.stderr)

    def test_soak_script_rejects_unknown_future_profile_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            completed = subprocess.run(
                [str(SOAK_SCRIPT)],
                env={
                    **os.environ,
                    "SOAK_MAX_ATTEMPTS": "1",
                    "SOAK_RUN_DIR": str(root / "run"),
                    "BLACKBOARD_DIR": str(root / "run"),
                    "SOAK_PROFILE_SEQUENCE": "raw_action@13000",
                    "SOAK_TARGET_FRAME": "12000",
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("unknown SOAK_PROFILE_SEQUENCE profile", completed.stderr)

    def test_soak_matrix_rejects_invalid_local_failure_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            completed = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env={
                    **os.environ,
                    "SOAK_MATRIX_RUN_DIR": str(root),
                    "SOAK_MATRIX_REPORT": str(root / "matrix_report.json"),
                    "SOAK_MATRIX_AGGREGATE_ONLY": "1",
                    "SOAK_MATRIX_ALLOW_FAILURES": "maybe",
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(2, completed.returncode)
            self.assertIn("SOAK_MATRIX_ALLOW_FAILURES must be 0 or 1", completed.stderr)

    def test_soak_matrix_preflight_skips_known_diagnostic_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "matrix_report.json"
            env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(root),
                "SOAK_MATRIX_REPORT": str(report),
                "SOAK_MATRIX_AGGREGATE_ONLY": "0",
                "SOAK_MATRIX_QUALIFICATION_TIER": "diagnostic",
                "SOAK_MATRIX_MIN_PASSES": "0",
            }

            subprocess.run([str(SOAK_MATRIX_SCRIPT)], check=True, env=env)

            payload = json.loads(report.read_text())
            self.assertEqual(1, payload["case_count"])
            case = payload["cases"][0]
            self.assertEqual("failed", case["preflight_status"])
            self.assertEqual(["geometry_risk", "placement_risk"], case["preflight_failure_codes"])
            self.assertEqual(["geometry_risk", "placement_risk"], case["failure_codes"])

    def test_soak_matrix_preflight_blocks_required_missing_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing_maps = root / "StarCraft II Maps"
            missing_maps.mkdir()
            run_dir = root / "run"
            report = run_dir / "matrix_report.json"
            env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(run_dir),
                "SOAK_MATRIX_REPORT": str(report),
                "SOAK_MATRIX_AGGREGATE_ONLY": "0",
                "SOAK_MATRIX_QUALIFICATION_TIER": "production",
                "SOAK_MATRIX_MAP_FILES": "AcropolisLE.SC2Map",
                "SOAK_MATRIX_ENEMY_RACES": "Zerg",
                "SOAK_MATRIX_MAP_ROOTS": str(missing_maps),
            }

            completed = subprocess.run(
                [str(SOAK_MATRIX_SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, completed.returncode)
            payload = json.loads(report.read_text())
            self.assertEqual(1, payload["failed"])
            case = payload["cases"][0]
            self.assertEqual("preflight_failure", case["failure_phase"])
            self.assertEqual(["missing_map"], case["preflight_failure_codes"])
            self.assertEqual(["missing_map"], case["failure_codes"])

    def test_soak_matrix_report_records_qualification_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            passed_case = root / "01-AcropolisLE-SC2Map-Zerg-d1"
            passed_case.mkdir()
            (passed_case / "soak_report.json").write_text(
                json.dumps({"status": "passed", "ok": True, "latest_frame": 12042})
            )
            report = root / "matrix_report.json"
            env = {
                **os.environ,
                "SOAK_MATRIX_RUN_DIR": str(root),
                "SOAK_MATRIX_REPORT": str(report),
                "SOAK_MATRIX_AGGREGATE_ONLY": "1",
                "SOAK_MATRIX_QUALIFICATION_TIER": "diagnostic",
            }

            subprocess.run([str(SOAK_MATRIX_SCRIPT)], check=True, env=env)

            payload = json.loads(report.read_text())
            self.assertEqual("diagnostic", payload["qualification_tier"])
            self.assertTrue(payload["allow_failures"])


if __name__ == "__main__":
    unittest.main()
