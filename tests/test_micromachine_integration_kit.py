"""Tests for the MicroMachine C++ integration kit artifacts."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


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

        self.assertIn(
            'ROOT_DIR="${ROOT_DIR:-/private/tmp/voi-micromachine-runtime}"',
            build_script,
        )
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
            "--max-attempts",
            "build_tank_defensive_hold_profile",
            "build_bio_pressure_profile",
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
            "SMOKE_KEEP_RUNNING_AFTER_PASS",
            "SMOKE_MANUAL_LIVE_MODE",
            "SMOKE_AUTO_AGGRESSIVE_PROFILE",
            "SMOKE_REQUIRE_BUILD_IDENTITY",
            "MICROMACHINE_BUILD_IDENTITY_REPORT",
            "verify_build_identity",
            "stale build identity",
            'SMOKE_MAX_ATTEMPTS="${SMOKE_MAX_ATTEMPTS:-1}"',
            "has_live_hold_preflight_evidence",
            "MicroMachine manual live hold preflight passed",
            "MicroMachine live hold preflight did not pass",
            "MicroMachine manual live hold active",
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
            "main_attack_scope_threshold_met",
            "main_attack_simulation_won",
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
            "expected_actual_items_by_doctrine",
            "last_actual_production_command_item",
            "actual_production_command_issued_count",
        ):
            with self.subTest(term=term):
                self.assertIn(term, smoke_script)
        self.assertNotIn(") || true", smoke_script)
        self.assertIn('payload.get("frame", 0) < min_frame', smoke_script)
        for term in (
            'MATRIX_RUN_ID="${MATRIX_RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"',
            'MATRIX_RUN_ROOT="${BLACKBOARD_ROOT}/runs/${MATRIX_RUN_ID}"',
            'summary="${MATRIX_RUN_ROOT}/strategy_matrix_summary.jsonl"',
            'run_dir="${MATRIX_RUN_ROOT}/${profile}"',
            "expected_contracts",
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
            "bio_marauder_techlab bio_marauder_support starport_transition medivac_drop_support",
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
