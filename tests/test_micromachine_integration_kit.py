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


class MicroMachineIntegrationKitTest(unittest.TestCase):
    def test_hook_manifest_covers_verified_upstream_manager_hooks(self) -> None:
        manifest = json.loads((KIT_DIR / "HOOK_MANIFEST.json").read_text())

        self.assertEqual(
            "eb893161371dab975a0a7e600f9e250ac03ec1ef",
            manifest["verified_upstream_commit"],
        )
        self.assertEqual("src/GameCommander.cpp", manifest["central_polling_hook"]["source_path"])
        self.assertIn("GameCommander::onFrame", manifest["central_polling_hook"]["function"])

        hooks = manifest["manager_hooks"]
        domains = {hook["domain"] for hook in hooks}
        self.assertEqual(
            {
                "production",
                "combat",
                "scouting",
                "economy",
                "combat_analysis",
                "squad",
                "scope",
                "tactical_task",
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
        self.assertNotIn("scouting.scan_priority", pending_keys)
        self.assertNotIn("squad.reinforce_bias", pending_keys)

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
        patch = PATCH_FILE.read_text()
        s2client_patch = S2CLIENT_PATCH_FILE.read_text()

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
            "production.composition_biases.bio",
            "production.composition_biases.mech",
            "production.composition_biases.siege",
            "production.composition_biases.drop",
            "production.composition_biases.anti_air",
            "production.production_facility_biases.TERRAN_FACTORY",
            "tech.unit_biases.TERRAN_SIEGETANK",
            "tech.structure_biases.TERRAN_FACTORY",
            "voi doctrine action=",
            "last_doctrine_action",
            "last_doctrine_queue_item",
            "last_doctrine_evidence",
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
            "\\\"TacticalTask\\\"",
            "\\\"status\\\":\\\"",
            "\\\"consumed_by\\\":\\\"",
            "tacticalTaskStatus",
            "tacticalTaskConsumedBy",
            "scout_with_units",
            "pressure_with_main_army",
            "sustain_production",
            "tech_transition",
            "expand_or_land_command_center",
            "getVoiPolicyFloat(\"scouting.scout_priority\", 0.0f)",
            "getVoiPolicyFloat(\"squad.squad_role_biases.marine_scout\", 0.0f)",
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
            "VOI addon direct command bypassed exploration gate",
            "voiAddonCommandCooldownElapsed",
            "VOI addon direct command bypassed conservative placement precheck",
            "recordVoiActualProductionCommand(b.type, \"addon_build_command\")",
            "continue;",
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
        patch = PATCH_FILE.read_text()

        self.assertNotIn("requeued_highest", patch)
        self.assertNotIn("requeued_blocking", patch)
        self.assertIn('recordVoiDoctrineConsumption(type, action, "queued_existing");', patch)

    def test_completed_expansion_command_center_guard_does_not_require_placement_query(self) -> None:
        patch = PATCH_FILE.read_text()
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
        self.assertIn(
            f'SOAK_MATRIX_DEFAULT_BUILD_DIR="${{MICROMACHINE_BUILD_DIR:-{DEFAULT_MICROMACHINE_BUILD_DIR}}}"',
            soak_matrix_script,
        )

        for term in (
            "https://github.com/Blizzard/s2client-api",
            "https://github.com/RaphaelRoyerRivard/MicroMachine",
            "0001-macos-latest-s2client-policy-blackboard.patch",
            "0001-s2client-macos-launchservices.patch",
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
            "voi_build_identity.json",
            "BLACKBOARD_HEADER_FILE",
            "voi_policy_blackboard.hpp",
            'cp "${BLACKBOARD_HEADER_FILE}" "${MICROMACHINE_DIR}/src/voi_policy_blackboard.hpp"',
        ):
            with self.subTest(term=term):
                self.assertIn(term, build_script)

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
            '"ForceStepMode"] = bool(int(__import__("os").environ.get("SMOKE_FORCE_STEP_MODE", "0")))',
            '"EnemyRace"] = "Zerg"',
            '"StepSize"] = 1',
            '"SC2API Strategy"]["Terran"] = "Terran_MarineRush"',
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
