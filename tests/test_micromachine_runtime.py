"""Tests for the concrete MicroMachine filesystem runtime bridge."""

import json
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
)
from starcraft_commander.micromachine_runtime import (
    LATEST_UPDATE_JSON_NAME,
    LATEST_UPDATE_KV_NAME,
    MICROMACHINE_DOCTRINE_PROFILE_KEYS,
    MicroMachineBackendPublishResult,
    MicroMachineFilesystemBlackboard,
    MicroMachineInMemoryBlackboard,
    MicroMachineModulationBackend,
    MicroMachineRuntimePaths,
    MICROMACHINE_STRATEGY_PROFILE_KEYS,
    build_aggressive_pressure_profile,
    build_defensive_hold_profile,
    build_micromachine_strategy_profile,
    flatten_blackboard_update,
    micromachine_strategy_profile_catalog,
    publish_policy_modulation_provider_output,
)
from starcraft_commander.policy_modulation import (
    CombatModulation,
    EconomyModulation,
    EmergencyModulation,
    PolicyModulationVector,
    PolicyOverrideLevel,
    PolicyModulationSource,
    ProductionModulation,
    ScoutingModulation,
    SquadModulation,
    StrategyModulation,
    TacticalScopeModulation,
    TechModulation,
    WeightedBiases,
    WorkerModulation,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileStatus,
)


def _vector(ttl_seconds: int = 30) -> PolicyModulationVector:
    return PolicyModulationVector(
        goal="defensive_tank_hold",
        override_level=PolicyOverrideLevel.CONSTRAINT,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="defensive",
            timing_biases=WeightedBiases({"tank_timing": 0.4}),
        ),
        economy=EconomyModulation(
            expand_bias=0.7,
            gas_worker_target_bias=0.45,
            repair_priority=0.3,
        ),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        tech=TechModulation(unit_biases=WeightedBiases({"TERRAN_SIEGETANK": 0.6})),
        production=ProductionModulation(addon_biases=WeightedBiases({"TECHLAB": 0.5})),
        combat=CombatModulation(
            defend_bias=0.8,
            aggression=-0.2,
            commitment_level=0.4,
            pressure_window_frames=2400,
            attack_condition_override="earlier_if_safe",
            siege_position_bias=0.7,
            target_priority_biases=WeightedBiases({"BANELING": 0.9}),
        ),
        scouting=ScoutingModulation(scan_priority=0.35),
        squad=SquadModulation(reinforce_bias=0.4, flank_bias=0.25),
        scope=TacticalScopeModulation(
            army_group="main",
            unit_classes=("marine", "siege_tank"),
            location_intent="enemy_natural",
            min_units=6,
            require_safety_margin=0.25,
        ),
        emergency=EmergencyModulation(force_retreat=True, prioritize_repair=True),
    )


class MicroMachineRuntimePathsTest(unittest.TestCase):
    def test_paths_are_deterministic_and_json_ready(self) -> None:
        paths = MicroMachineRuntimePaths("/tmp/voi-mm")
        document = paths.to_dict()

        self.assertTrue(document["latest_update_json"].endswith(LATEST_UPDATE_JSON_NAME))
        self.assertTrue(document["latest_update_kv"].endswith(LATEST_UPDATE_KV_NAME))
        json.dumps(document)


class MicroMachineInterventionProfileTest(unittest.TestCase):
    def test_defensive_and_aggressive_profiles_bias_managers_without_raw_control(self) -> None:
        defensive = build_defensive_hold_profile()
        aggressive = build_aggressive_pressure_profile()

        self.assertEqual("micromachine_defensive_hold", defensive.goal)
        self.assertEqual("", defensive.strategy.doctrine)
        self.assertLess(defensive.combat.aggression, 0)
        self.assertGreater(defensive.combat.defend_bias, 0.8)
        self.assertGreater(defensive.scouting.scout_priority, 0)
        self.assertLess(defensive.scouting.risk_tolerance, 0)
        self.assertTrue(defensive.scouting.require_fresh_enemy_observation)
        self.assertEqual(32, defensive.workers.repeat_order_guard_frames)
        self.assertIn("bounded_intervention", defensive.tags)

        self.assertEqual("micromachine_aggressive_pressure", aggressive.goal)
        self.assertGreater(aggressive.combat.aggression, 0.5)
        self.assertLess(aggressive.combat.defend_bias, defensive.combat.defend_bias)
        self.assertGreater(aggressive.combat.attack_timing_bias, 0)
        self.assertGreater(aggressive.combat.commitment_level, 0)
        self.assertEqual("earlier_if_safe", aggressive.combat.attack_condition_override)
        self.assertGreater(aggressive.combat.retreat_patience_bias, 0)
        self.assertGreater(aggressive.combat.rally_before_attack_bias, 0)
        self.assertGreater(
            aggressive.combat.target_priority_biases.to_dict()["worker_line"],
            0,
        )
        self.assertGreater(aggressive.scouting.risk_tolerance, 0)
        self.assertFalse(aggressive.scouting.require_fresh_enemy_observation)
        self.assertGreater(aggressive.squad.contain_bias, 0)
        self.assertGreater(aggressive.squad.reinforce_bias, 0)
        self.assertEqual("main", aggressive.scope.army_group)
        self.assertEqual("enemy_natural", aggressive.scope.location_intent)
        self.assertEqual(32, aggressive.workers.repeat_order_guard_frames)
        self.assertGreaterEqual(aggressive.scope.min_units, 1)
        self.assertGreater(aggressive.scope.require_safety_margin, 0)
        self.assertIn("bounded_intervention", aggressive.tags)

        for profile in (defensive, aggressive):
            with self.subTest(profile=profile.goal):
                payload = profile.to_dict()
                json.dumps(payload)
                self.assertNotIn("raw_action", json.dumps(payload))
                self.assertNotIn("s2client_api", json.dumps(payload))

    def test_named_strategy_profile_catalog_is_versioned_and_bounded(self) -> None:
        catalog = micromachine_strategy_profile_catalog()

        self.assertEqual(1, catalog["schema_version"])
        self.assertEqual(
            set(MICROMACHINE_STRATEGY_PROFILE_KEYS),
            set(catalog["profiles"]),
        )
        for key in MICROMACHINE_STRATEGY_PROFILE_KEYS:
            with self.subTest(profile=key):
                vector = build_micromachine_strategy_profile(key)
                payload = vector.to_dict()
                self.assertIn(key, payload["tags"])
                self.assertIn("bounded_intervention", payload["tags"])
                self.assertNotIn("raw_action", json.dumps(payload))
                self.assertNotIn("unit_tag", json.dumps(payload))
                self.assertTrue(catalog["profiles"][key]["managers"])

        emergency = build_micromachine_strategy_profile("emergency_recovery", ttl_seconds=600)
        self.assertLessEqual(emergency.ttl_seconds, 60)

    def test_doctrine_profiles_have_distinct_production_vectors(self) -> None:
        doctrine_vectors = {
            key: build_micromachine_strategy_profile(key)
            for key in MICROMACHINE_DOCTRINE_PROFILE_KEYS
        }

        for key, vector in doctrine_vectors.items():
            with self.subTest(profile=key):
                self.assertEqual(key, vector.strategy.doctrine)
                self.assertIn(key, vector.tags)
                self.assertIn("bounded_intervention", vector.tags)

        marine = doctrine_vectors["marine_rush"]
        mech = doctrine_vectors["mech_transition"]
        drop = doctrine_vectors["drop_harassment"]
        macro = doctrine_vectors["expand_macro"]
        anti_air = doctrine_vectors["anti_air_response"]

        self.assertGreater(
            marine.production.queue_biases.to_dict()["TERRAN_MARINE"],
            mech.production.queue_biases.to_dict().get("TERRAN_MARINE", -1.0),
        )
        self.assertGreater(
            mech.production.queue_biases.to_dict()["TERRAN_FACTORY"],
            marine.production.queue_biases.to_dict().get("TERRAN_FACTORY", -1.0),
        )
        self.assertGreater(
            mech.tech.unit_biases.to_dict()["TERRAN_SIEGETANK"],
            marine.tech.unit_biases.to_dict().get("TERRAN_SIEGETANK", -1.0),
        )
        self.assertGreater(mech.squad.reinforce_bias, 0)
        self.assertGreater(mech.combat.target_priority_biases.to_dict()["army"], 0)
        self.assertGreater(
            drop.production.queue_biases.to_dict()["TERRAN_STARPORT"],
            marine.production.queue_biases.to_dict().get("TERRAN_STARPORT", -1.0),
        )
        self.assertGreater(drop.production.queue_biases.to_dict()["TERRAN_FACTORY"], 0)
        self.assertGreater(
            drop.production.queue_biases.to_dict()["TERRAN_MEDIVAC"],
            marine.production.queue_biases.to_dict().get("TERRAN_MEDIVAC", -1.0),
        )
        self.assertGreater(
            macro.production.queue_biases.to_dict()["TERRAN_COMMANDCENTER"],
            marine.production.queue_biases.to_dict().get("TERRAN_COMMANDCENTER", -1.0),
        )
        self.assertGreater(
            anti_air.production.queue_biases.to_dict()["TERRAN_VIKINGFIGHTER"],
            marine.production.queue_biases.to_dict().get("TERRAN_VIKINGFIGHTER", -1.0),
        )
        self.assertLess(
            mech.production.production_continuity_bias,
            marine.production.production_continuity_bias,
        )

    def test_unknown_strategy_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown MicroMachine strategy profile"):
            build_micromachine_strategy_profile("raw_action")


class MicroMachineFilesystemBlackboardTest(unittest.TestCase):
    def test_publish_vector_writes_atomic_json_kv_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blackboard = MicroMachineFilesystemBlackboard(directory)

            update = blackboard.publish_vector(
                _vector(),
                current_frame=100,
                update_id="mod-1",
            )

            self.assertEqual("mod-1", update.update_id)
            latest_json = Path(directory) / LATEST_UPDATE_JSON_NAME
            latest_kv = Path(directory) / LATEST_UPDATE_KV_NAME
            archive = Path(directory) / "modulation_updates.jsonl"
            self.assertTrue(latest_json.exists())
            self.assertTrue(latest_kv.exists())
            self.assertTrue(archive.exists())

            document = json.loads(latest_json.read_text())
            self.assertEqual(MICROMACHINE_BRIDGE_PROTOCOL_VERSION, document["protocol_version"])
            self.assertEqual("defensive_tank_hold", document["vector"]["goal"])
            kv = latest_kv.read_text()
            self.assertIn("combat.defend_bias=0.8", kv)
            self.assertIn("strategy.doctrine=", kv)
            self.assertIn("combat.commitment_level=0.4", kv)
            self.assertIn("combat.pressure_window_frames=2400", kv)
            self.assertIn("combat.attack_condition_override=earlier_if_safe", kv)
            self.assertIn("combat.siege_position_bias=0.7", kv)
            self.assertIn("combat.target_priority_biases.BANELING=0.9", kv)
            self.assertIn("tech.unit_biases.TERRAN_SIEGETANK=0.6", kv)
            self.assertIn("production.addon_biases.TECHLAB=0.5", kv)
            self.assertIn("scouting.scan_priority=0.35", kv)
            self.assertIn("squad.reinforce_bias=0.4", kv)
            self.assertIn("squad.flank_bias=0.25", kv)
            self.assertIn("scope.army_group=main", kv)
            self.assertIn("scope.unit_classes=marine,siege_tank", kv)
            self.assertIn("scope.location_intent=enemy_natural", kv)
            self.assertIn("scope.require_safety_margin=0.25", kv)
            self.assertIn("emergency.force_retreat=true", kv)
            self.assertIn("emergency.prioritize_repair=true", kv)
            self.assertEqual(1, len(archive.read_text().splitlines()))

    def test_publish_rejects_unsafe_kv_key_without_partial_latest_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blackboard = MicroMachineFilesystemBlackboard(directory)

            with self.assertRaisesRegex(ValueError, "unsafe characters"):
                blackboard.publish_vector(
                    PolicyModulationVector(
                        goal="hold",
                        combat=CombatModulation(
                            target_priority_biases=WeightedBiases(
                                {"BANELING\ncombat.aggression": 0.9}
                            ),
                        ),
                    ),
                    current_frame=100,
                    update_id="unsafe-key",
                )

            self.assertFalse((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())
            self.assertFalse((Path(directory) / LATEST_UPDATE_KV_NAME).exists())
            self.assertFalse((Path(directory) / "modulation_updates.jsonl").exists())
            self.assertIsNone(blackboard.read_latest_update(current_frame=100))

    def test_read_latest_update_rejects_stale_and_raw_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blackboard = MicroMachineFilesystemBlackboard(directory)
            update = MicroMachineBlackboardUpdate(
                update_id="stale",
                vector=_vector(ttl_seconds=1),
                issued_at_frame=0,
            )
            blackboard.publish_update(update, current_frame=0)

            with self.assertRaisesRegex(ValueError, "stale"):
                blackboard.read_latest_update(current_frame=10_000)

            raw_payload = update.to_dict()
            raw_payload["vector"]["raw_action"] = "attack_move"
            (Path(directory) / LATEST_UPDATE_JSON_NAME).write_text(json.dumps(raw_payload))
            with self.assertRaisesRegex(ValueError, "raw runtime control"):
                blackboard.read_latest_update(current_frame=1)

    def test_ingests_telemetry_and_builds_dashboard_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blackboard = MicroMachineFilesystemBlackboard(directory)
            blackboard.publish_vector(_vector(), current_frame=10, update_id="active")
            telemetry = blackboard.ingest_telemetry(
                MicroMachineTelemetry(
                    frame=12,
                    managers={"CombatCommander": {"posture": "hold"}},
                    active_modulation_ids=("active",),
                )
            )

            self.assertEqual(12, telemetry.frame)
            restored = blackboard.read_latest_telemetry()
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(("active",), restored.active_modulation_ids)

            snapshot = blackboard.dashboard_snapshot(current_frame=12).to_dict()
            self.assertEqual(1, snapshot["active_modulation_count"])
            self.assertEqual("MicroMachine", snapshot["telemetry"]["bot_name"])

    def test_provider_unavailable_state_surfaces_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blackboard = MicroMachineFilesystemBlackboard(directory)
            blackboard.write_provider_unavailable(
                current_frame=77,
                reason="local LLM key missing",
            )

            snapshot = blackboard.dashboard_snapshot(current_frame=77).to_dict()

            self.assertEqual(
                MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE.value,
                snapshot["last_failure"],
            )
            telemetry = snapshot["telemetry"]
            self.assertIsInstance(telemetry, dict)
            assert isinstance(telemetry, dict)
            provider = telemetry["managers"]["Provider"]
            self.assertEqual("unavailable", provider["status"])
            self.assertEqual("local LLM key missing", provider["unavailable_reason"])


class MicroMachineBackendAbstractionTest(unittest.TestCase):
    def test_filesystem_and_memory_backends_share_publish_contract(self) -> None:
        backends: list[MicroMachineModulationBackend] = []
        with tempfile.TemporaryDirectory() as directory:
            backends.append(MicroMachineFilesystemBlackboard(directory))
            backends.append(MicroMachineInMemoryBlackboard())

            for backend in backends:
                with self.subTest(backend=type(backend).__name__):
                    update = backend.publish_vector(
                        _vector(),
                        current_frame=100,
                        update_id=f"{type(backend).__name__}-update",
                    )
                    telemetry = backend.ingest_telemetry(
                        {
                            "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                            "frame": 104,
                            "bot_name": "MicroMachine",
                            "race": "Terran",
                            "managers": {"GameCommander": {"policy_active": True}},
                            "active_modulation_ids": [update.update_id],
                            "last_failure": None,
                        }
                    )
                    latest = backend.read_latest_update(current_frame=104)
                    snapshot = backend.dashboard_snapshot(current_frame=104).to_dict()

                    self.assertIsNotNone(latest)
                    assert latest is not None
                    self.assertEqual(update.update_id, latest.update_id)
                    self.assertEqual(104, telemetry.frame)
                    self.assertEqual(1, snapshot["active_modulation_count"])
                    self.assertIsInstance(backend, MicroMachineModulationBackend)

    def test_memory_backend_rejects_raw_payloads_at_telemetry_boundary(self) -> None:
        backend = MicroMachineInMemoryBlackboard()

        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            backend.ingest_telemetry(
                {
                    "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                    "frame": 1,
                    "bot_name": "MicroMachine",
                    "race": "Terran",
                    "managers": {"GameCommander": {"raw_action": "attack_move"}},
                    "active_modulation_ids": [],
                    "last_failure": None,
                }
            )

    def test_backends_reject_raw_control_in_telemetry_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backends: list[MicroMachineModulationBackend] = [
                MicroMachineFilesystemBlackboard(directory),
                MicroMachineInMemoryBlackboard(),
            ]

            for backend in backends:
                with self.subTest(backend=type(backend).__name__):
                    with self.assertRaisesRegex(ValueError, "raw runtime control"):
                        backend.ingest_telemetry(
                            MicroMachineTelemetry(
                                frame=1,
                                managers={
                                    "GameCommander": {"raw_action": "attack_move"}
                                },
                            )
                        )

    def test_memory_backend_defensively_copies_telemetry_boundaries(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        source = MicroMachineTelemetry(
            frame=1,
            managers={
                "GameCommander": {
                    "policy": {"active": True},
                }
            },
        )

        ingested = backend.ingest_telemetry(source)
        source.managers["GameCommander"]["policy"]["raw_action"] = "attack_move"
        ingested.managers["GameCommander"]["raw_action"] = "attack_move"
        latest = backend.read_latest_telemetry()

        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertNotIn("raw_action", latest.managers["GameCommander"])
        self.assertNotIn(
            "raw_action",
            latest.managers["GameCommander"]["policy"],
        )

        latest.managers["GameCommander"]["raw_action"] = "attack_move"
        snapshot = backend.dashboard_snapshot(current_frame=1).to_dict()

        self.assertNotIn(
            "raw_action",
            snapshot["telemetry"]["managers"]["GameCommander"],
        )

    def test_neural_representation_provider_publishes_through_backend(self) -> None:
        backend = MicroMachineInMemoryBlackboard()

        result = publish_policy_modulation_provider_output(
            {
                "source": "neural_representation",
                "goal": "two_base_tank_hold",
                "representation_axes": {
                    "strategy.posture": "defensive",
                    "economy.expand_bias": 0.6,
                    "tech.unit_biases.TERRAN_SIEGETANK": 0.8,
                    "combat.defend_bias": 0.7,
                },
            },
            backend,
            current_frame=44,
            update_id="neural-001",
        )

        self.assertIsInstance(result, MicroMachineBackendPublishResult)
        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual("neural-001", result.update.update_id)
        self.assertEqual(
            PolicyModulationSource.NEURAL_REPRESENTATION,
            result.update.vector.source,
        )
        self.assertEqual("defensive", result.update.vector.strategy.posture)
        self.assertEqual(
            {"TERRAN_SIEGETANK": 0.8},
            result.update.vector.tech.unit_biases.to_dict(),
        )
        latest = backend.read_latest_update(current_frame=45)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(result.update.update_id, latest.update_id)

    def test_provider_compile_refusal_does_not_publish_update(self) -> None:
        backend = MicroMachineInMemoryBlackboard()

        result = publish_policy_modulation_provider_output(
            {"goal": "unsafe", "representation": {"raw_action": "attack"}},
            backend,
            current_frame=1,
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            PolicyModulationCompileStatus.REFUSED,
            result.compile_result.status,
        )
        self.assertIsNone(result.update)
        self.assertIsNone(backend.read_latest_update(current_frame=1))


class FlatBlackboardUpdateTest(unittest.TestCase):
    def test_flatten_update_outputs_cpp_readable_key_values(self) -> None:
        update = MicroMachineBlackboardUpdate(
            update_id="flat",
            vector=_vector(),
            issued_at_frame=22,
        )

        text = flatten_blackboard_update(update)

        self.assertIn("protocol_version=voi-mm-bridge/v1\n", text)
        self.assertIn("source=human\n", text)
        self.assertIn("override_level=constraint\n", text)
        self.assertIn("strategy.posture=defensive\n", text)
        self.assertIn("strategy.timing_biases.tank_timing=0.4\n", text)
        self.assertIn("economy.gas_worker_target_bias=0.45\n", text)
        self.assertIn("workers.repeat_order_guard_frames=32\n", text)
        self.assertIn("combat.aggression=-0.2\n", text)
        self.assertIn("combat.target_priority_biases.BANELING=0.9\n", text)
        self.assertIn(
            "manager_bias_domains=strategy,economy,workers,tech,production,combat,scouting,squad,scope,emergency\n",
            text,
        )

    def test_flatten_update_rejects_injected_kv_keys(self) -> None:
        update = MicroMachineBlackboardUpdate(
            update_id="unsafe-key",
            vector=PolicyModulationVector(
                goal="hold",
                combat=CombatModulation(
                    target_priority_biases=WeightedBiases(
                        {"BANELING\ncombat.aggression": 0.9}
                    ),
                ),
            ),
            issued_at_frame=22,
        )

        with self.assertRaisesRegex(ValueError, "unsafe characters"):
            flatten_blackboard_update(update)


if __name__ == "__main__":
    unittest.main()
