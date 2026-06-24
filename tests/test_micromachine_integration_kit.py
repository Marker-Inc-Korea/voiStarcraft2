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
SMOKE_SCRIPT = KIT_DIR / "scripts" / "smoke_macos_local.sh"
SOAK_SCRIPT = KIT_DIR / "scripts" / "soak_macos_local.sh"
SOAK_MATRIX_SCRIPT = KIT_DIR / "scripts" / "soak_matrix_macos_local.sh"
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
        pending_keys = manifest["python_blackboard_emitted_but_not_consumed_by_current_cpp_patch"]
        self.assertIn("production.addon_biases.*", pending_keys)
        self.assertIn("combat.target_priority_biases.*", pending_keys)
        self.assertIn("scouting.scan_priority", pending_keys)
        self.assertIn("squad.reinforce_bias", pending_keys)
        self.assertIn("emergency.prioritize_repair", pending_keys)

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
            "canTrustOpeningWallPlacement",
            "trusting valid uncontested opening wall placement.",
            "Supply provider recovery queued after supply block.",
            "m_queue.queueAsHighestPriority(supplyProviderType, false)",
            "Path to completed refinery is not safe; assigning gas worker with refinery fallback.",
            "VOI_SC2_EXTRA_ARGS",
            "ScopedVoiEnvironmentStripper",
            "VOI_MICROMACHINE_BLACKBOARD_DIR",
            "stripVoiEnvForSc2Child",
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
            'std::strncmp(*env, "VOI_", 4) == 0',
            "environment_list.data()",
            "execve(launcher_path.c_str(), &char_list[0], environment_list.data())",
            "data.size() != static_cast<size_t>(width * height)",
            "target_compile_options(civetweb-c-library PRIVATE -Wno-unknown-warning-option -Wno-error=unknown-warning-option)",
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
        soak_matrix_script = SOAK_MATRIX_SCRIPT.read_text()

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
            "submodule update --init --recursive",
            "apply --check --ignore-space-change --whitespace=nowarn",
            "cmake --build",
            "MICROMACHINE_BUILD_IDENTITY_REPORT",
            "starcraft_commander.micromachine_build_identity",
            "voi_build_identity.json",
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
            "mkdir -p \"${SC2_TEMP_DIR}\"",
            "-dataDir",
            "-tempDir",
            "*/SC2.app/Contents/MacOS/SC2",
            "latest_telemetry.json",
            "MIN_TELEMETRY_FRAME",
            "SMOKE_TIMEOUT_SECONDS",
            '"EnemyDifficulty"] = int',
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
            "clean_sc2_ports_before_launch",
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
            "except json.JSONDecodeError",
        ):
            with self.subTest(term=term):
                self.assertIn(term, smoke_script)
        self.assertNotIn(") || true", smoke_script)
        self.assertIn('payload.get("frame", 0) < min_frame', smoke_script)

        for term in (
            "VOI_MICROMACHINE_BLACKBOARD_DIR",
            "SOAK_TARGET_FRAME",
            "SOAK_ENEMY_RACE",
            "SOAK_ENEMY_DIFFICULTY",
            "SOAK_TIMEOUT_SECONDS",
            "SOAK_TELEMETRY_STALL_SECONDS",
            "SOAK_PRODUCTION_DEADLOCK_FRAME",
            "SOAK_PRODUCTION_STALL_FRAMES",
            "SOAK_INCOME_STALL_FRAMES",
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
            "max-placement-failures",
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
            "SOAK_AGGRESSIVE_MIN_FRAME",
            "SOAK_MAX_ATTEMPTS",
            "SOAK_ATTEMPT_INDEX",
            "SOAK_NON_RETRYABLE_FAILURE_CODES",
            "SC2_LAUNCH_MODE",
            "SC2_BATTLENET_EXECUTABLE",
            "SC2_ATTACH_TIMEOUT_MS",
            "SC2_USE_RUNTIME_DIR_ARGS",
            "resolve_map_file",
            "prepare_launch_contract",
            "map file not found",
            "SC2 executable is not runnable",
            "SC2_ROOT_ALIAS",
            "SC2_RUNTIME_ROOT",
            "SC2_TEMP_DIR",
            "SC2_CLEAN_PORTS_BEFORE_LAUNCH",
            "mkdir -p \"${SC2_TEMP_DIR}\"",
            "clean_sc2_ports_before_launch",
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
            self.assertEqual(
                ["bootstrap_no_start_units", "missing_map"],
                case["preflight_failure_codes"],
            )
            self.assertEqual(
                ["bootstrap_no_start_units", "missing_map"],
                case["failure_codes"],
            )

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
