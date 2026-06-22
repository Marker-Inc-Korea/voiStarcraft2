"""Tests for the MicroMachine C++ integration kit artifacts."""

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
KIT_DIR = REPO_ROOT / "integrations" / "micromachine"
PATCH_FILE = KIT_DIR / "patches" / "0001-macos-latest-s2client-policy-blackboard.patch"
S2CLIENT_PATCH_FILE = KIT_DIR / "patches" / "0001-s2client-macos-launchservices.patch"
BUILD_SCRIPT = KIT_DIR / "scripts" / "build_macos_local.sh"
SMOKE_SCRIPT = KIT_DIR / "scripts" / "smoke_macos_local.sh"
SOAK_SCRIPT = KIT_DIR / "scripts" / "soak_macos_local.sh"


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
            {"production", "combat", "scouting", "economy", "combat_analysis", "squad"},
            domains,
        )
        required_sources = {
            "src/ProductionManager.cpp",
            "src/CombatCommander.cpp",
            "src/ScoutManager.cpp",
            "src/WorkerManager.cpp",
            "src/CombatAnalyzer.cpp",
            "src/Squad.cpp",
        }
        self.assertEqual(required_sources, {hook["source_path"] for hook in hooks})
        for hook in hooks:
            with self.subTest(hook=hook["domain"]):
                self.assertTrue(hook["keys"])
                self.assertTrue(hook["function"])
                self.assertTrue(hook["intended_effect"])

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
            "bool CCBot::isInitialObservationReady() const",
            "void CCBot::initializeManagers()",
            "m_managersInitialized",
            "getVoiPolicyBool(\"emergency.force_retreat\", false)",
            "getVoiPolicyBool(\"emergency.cancel_attacks\", false)",
            "voiEngageMarginDelta",
            "BaseLocation * closestStartBase = nullptr",
            "!building.type.isRefinery() && !building.type.isAddon()",
            "m_bot.GetCurrentFrame() < 5000",
            "Path to completed refinery is not safe; assigning gas worker with refinery fallback.",
            "VOI_SC2_EXTRA_ARGS",
            "PROTOSS_OBSERVERSIEGEMODE",
            "coordinator.SetRawAffectsSelection",
            "diff --git a/src/voi_policy_blackboard.hpp",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, patch)
        self.assertNotIn("-\t\t\t\t\t\t\t++neighborsBaseLocation[bl];", patch)
        for term in (
            "extern char **environ",
            "execve(launcher_path.c_str(), &char_list[0], environ)",
            "data.size() != static_cast<size_t>(width * height)",
            "options->set_show_cloaked(true)",
            "options->set_raw_affects_selection(true)",
            "setup.type == PlayerType::Computer",
        ):
            with self.subTest(term=term):
                self.assertIn(term, s2client_patch)

    def test_macos_scripts_document_reproducible_build_smoke_and_soak(self) -> None:
        build_script = BUILD_SCRIPT.read_text()
        smoke_script = SMOKE_SCRIPT.read_text()
        soak_script = SOAK_SCRIPT.read_text()

        for term in (
            "https://github.com/Blizzard/s2client-api",
            "https://github.com/RaphaelRoyerRivard/MicroMachine",
            "0001-macos-latest-s2client-policy-blackboard.patch",
            "0001-s2client-macos-launchservices.patch",
            "DSC2Api_SC2API_LIB",
            "reset --hard",
            "apply --check --ignore-space-change --whitespace=nowarn",
            "cmake --build",
        ):
            with self.subTest(term=term):
                self.assertIn(term, build_script)

        for term in (
            "VOI_MICROMACHINE_BLACKBOARD_DIR",
            "build_defensive_hold_profile",
            "build_aggressive_pressure_profile",
            "latest_modulation.kv",
            "telemetry.jsonl",
            "AcropolisLE.SC2Map",
            "Versions/Base96883/SC2.app/Contents/MacOS/SC2",
            "latest_telemetry.json",
            "MIN_TELEMETRY_FRAME",
            "SMOKE_TIMEOUT_SECONDS",
            '"EnemyDifficulty"] = 1',
            '"EnemyRace"] = "Zerg"',
            '"StepSize"] = 1',
            '"SC2API Strategy"]["Terran"] = "Terran_MarineRush"',
            "policy_active",
            "CombatCommander",
            "ScoutManager",
            "bounded_intervention",
            "aggressive_update_id = sys.argv[3]",
            "defensive_update_id = sys.argv[4]",
            "smoke-defensive-hold",
            "smoke-aggressive-pressure",
            "cleanup_runtime",
            "pgrep -f",
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
            "marine_rush.insert(first_barracks + 1, \"Marine\")",
            "FORBIDDEN_MACRO_FAILURES",
            "Failed to place Barracks",
            "Cancel building TERRAN_SUPPLYDEPOT :",
            "MicroMachine reached SC2 API but did not execute the required macro opening",
            "except json.JSONDecodeError",
        ):
            with self.subTest(term=term):
                self.assertIn(term, smoke_script)
        self.assertNotIn(") || true", smoke_script)
        self.assertIn('payload.get("frame", 0) < min_frame', smoke_script)

        for term in (
            "VOI_MICROMACHINE_BLACKBOARD_DIR",
            "SOAK_TARGET_FRAME",
            "SOAK_TIMEOUT_SECONDS",
            "SOAK_TELEMETRY_STALL_SECONDS",
            "SOAK_PRODUCTION_DEADLOCK_FRAME",
            "SOAK_PRODUCTION_STALL_FRAMES",
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
            "max-placement-failures",
            "modulation-consumption-grace-frames",
            "termination-reason",
            "target_frame_reached_cleanup",
            "live_classifier_failure",
            "fail_from_live_classifier",
            "if ! classify_soak \"live\"",
            "build_defensive_hold_profile",
            "build_aggressive_pressure_profile",
            "CombatCommander",
            "ScoutManager",
            "bounded_intervention",
            "REQUIRED_MACRO_EVIDENCE",
            "TERRAN_BARRACKS UnderConstruction",
            "create unit item=Marine result=1",
            "Gas income:",
            "cleanup_runtime",
            "SOAK_PROFILE_REFRESH_FRAMES",
            "SOAK_MAX_ATTEMPTS",
            "SOAK_ATTEMPT_INDEX",
            "SOAK_NON_RETRYABLE_FAILURE_CODES",
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
            "telemetry_stall",
            "repeated_placement_failures",
            "no_production_deadlock",
            "stale_modulation",
        ):
            with self.subTest(term=term):
                self.assertIn(term, (REPO_ROOT / "starcraft_commander" / "micromachine_soak.py").read_text())


if __name__ == "__main__":
    unittest.main()
