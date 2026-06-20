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
    MicroMachineFilesystemBlackboard,
    MicroMachineRuntimePaths,
    flatten_blackboard_update,
)
from starcraft_commander.policy_modulation import (
    CombatModulation,
    EconomyModulation,
    EmergencyModulation,
    PolicyModulationVector,
    PolicyOverrideLevel,
    StrategyModulation,
    TechModulation,
    WeightedBiases,
)


def _vector(ttl_seconds: int = 30) -> PolicyModulationVector:
    return PolicyModulationVector(
        goal="defensive_tank_hold",
        override_level=PolicyOverrideLevel.CONSTRAINT,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(posture="defensive"),
        economy=EconomyModulation(expand_bias=0.7, repair_priority=0.3),
        tech=TechModulation(unit_biases=WeightedBiases({"TERRAN_SIEGETANK": 0.6})),
        combat=CombatModulation(defend_bias=0.8, aggression=-0.2),
        emergency=EmergencyModulation(force_retreat=True),
    )


class MicroMachineRuntimePathsTest(unittest.TestCase):
    def test_paths_are_deterministic_and_json_ready(self) -> None:
        paths = MicroMachineRuntimePaths("/tmp/voi-mm")
        document = paths.to_dict()

        self.assertTrue(document["latest_update_json"].endswith(LATEST_UPDATE_JSON_NAME))
        self.assertTrue(document["latest_update_kv"].endswith(LATEST_UPDATE_KV_NAME))
        json.dumps(document)


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
            self.assertIn("tech.unit_biases.TERRAN_SIEGETANK=0.6", kv)
            self.assertIn("emergency.force_retreat=true", kv)
            self.assertEqual(1, len(archive.read_text().splitlines()))

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
        self.assertIn("combat.aggression=-0.2\n", text)
        self.assertIn("manager_bias_domains=strategy,economy,tech,combat,emergency\n", text)


if __name__ == "__main__":
    unittest.main()
