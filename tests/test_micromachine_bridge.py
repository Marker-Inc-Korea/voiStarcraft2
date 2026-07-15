"""Tests for the MicroMachine sidecar and blackboard protocol."""

import json
import unittest

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
    MICROMACHINE_GAME_LOOPS_PER_SECOND,
    MICROMACHINE_MANAGER_HOOKS,
    MICROMACHINE_MODULATION_UPDATE_SCHEMA,
    MICROMACHINE_TELEMETRY_SCHEMA,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeEnvelope,
    MicroMachineBridgeFailureMode,
    MicroMachineBridgeMessageType,
    MicroMachineRollbackCommand,
    MicroMachineTelemetry,
    build_micromachine_bridge_error_envelope,
    validate_micromachine_blackboard_update,
)
from starcraft_commander.policy_modulation import (
    CombatModulation,
    EconomyModulation,
    PolicyModulationSource,
    PolicyModulationVector,
    PolicyOverrideLevel,
    PolicySafetyConstraint,
    StrategyModulation,
    TacticalScopeModulation,
    TacticalTaskModulation,
    TechModulation,
    LifetimeModulation,
    WeightedBiases,
    WorkerModulation,
)


def _defensive_vector() -> PolicyModulationVector:
    return PolicyModulationVector(
        goal="two_base_defensive_tank_hold",
        source=PolicyModulationSource.LLM,
        override_level=PolicyOverrideLevel.CONSTRAINT,
        confidence=0.82,
        ttl_seconds=30,
        strategy=StrategyModulation(posture="defensive"),
        economy=EconomyModulation(expand_bias=0.7, repair_priority=0.4),
        tech=TechModulation(unit_biases=WeightedBiases({"SiegeTank": 0.6})),
        combat=CombatModulation(defend_bias=0.8, aggression=-0.2),
        constraints=(
            PolicySafetyConstraint(
                key="require_fresh_enemy_observation",
                reason="do not attack blind",
            ),
        ),
        tags=("micro_machine",),
    )


class MicroMachineBridgeContractsTest(unittest.TestCase):
    def test_json_schemas_are_versioned_and_json_ready(self) -> None:
        self.assertEqual(
            MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
            MICROMACHINE_TELEMETRY_SCHEMA["properties"]["protocol_version"]["const"],
        )
        self.assertIn("vector", MICROMACHINE_MODULATION_UPDATE_SCHEMA["required"])
        json.dumps(MICROMACHINE_TELEMETRY_SCHEMA)
        json.dumps(MICROMACHINE_MODULATION_UPDATE_SCHEMA)

    def test_manager_hook_mapping_covers_required_micromachine_surfaces(self) -> None:
        managers = {hook.manager for hook in MICROMACHINE_MANAGER_HOOKS}
        expected_fragments = (
            "StrategyManager",
            "ProductionManager",
            "BuildOrderQueue",
            "CombatCommander",
            "CombatAnalyzer",
            "Squad",
            "SquadOrder",
            "ScoutManager",
            "WorkerManager",
            "libvoxelbot",
        )

        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertTrue(any(fragment in manager for manager in managers))

        domains = {hook.domain for hook in MICROMACHINE_MANAGER_HOOKS}
        self.assertEqual(
            {
                "strategy",
                "production",
                "combat",
                "squad",
                "scope",
                "tactical_task",
                "scouting",
                "economy",
                "workers",
            },
            domains,
        )

    def test_blackboard_update_serializes_ttl_constraints_and_domains(self) -> None:
        update = MicroMachineBlackboardUpdate(
            update_id="mod-001",
            vector=_defensive_vector(),
            issued_at_frame=1000,
        )
        document = update.to_dict()

        self.assertEqual(MICROMACHINE_BRIDGE_PROTOCOL_VERSION, document["protocol_version"])
        self.assertEqual(1000 + 30 * MICROMACHINE_GAME_LOOPS_PER_SECOND, update.expires_at_frame)
        self.assertIn("strategy", document["manager_bias_domains"])
        self.assertIn("economy", document["manager_bias_domains"])
        self.assertIn("combat", document["manager_bias_domains"])
        self.assertEqual(
            "require_fresh_enemy_observation",
            document["active_constraints"][0]["key"],
        )
        json.dumps(document, ensure_ascii=False)

    def test_semantic_lifetimes_survive_transport_ttl(self) -> None:
        standing = MicroMachineBlackboardUpdate(
            update_id="standing",
            issued_at_frame=10,
            expires_at_frame=20,
            vector=PolicyModulationVector(
                goal="keep producing",
                lifetime=LifetimeModulation(
                    mode="standing_order",
                    completion_state="active",
                ),
            ),
        )
        transient = MicroMachineBlackboardUpdate(
            update_id="transient",
            issued_at_frame=10,
            expires_at_frame=20,
            vector=PolicyModulationVector(
                goal="attack until complete",
                tactical_task=TacticalTaskModulation(
                    task_type="pressure_with_main_army",
                ),
                lifetime=LifetimeModulation(
                    mode="until_completed",
                    completion_state="active",
                ),
            ),
        )

        self.assertFalse(standing.is_stale(21))
        self.assertFalse(transient.is_stale(21))

    def test_manager_bias_domains_ignore_neutral_defaults(self) -> None:
        neutral = MicroMachineBlackboardUpdate(
            update_id="mod-neutral",
            vector=PolicyModulationVector(goal="observe"),
            issued_at_frame=1000,
        )

        self.assertEqual((), neutral.manager_bias_domains)
        self.assertEqual([], neutral.to_dict()["manager_bias_domains"])

        tactical = MicroMachineBlackboardUpdate(
            update_id="mod-tactical",
            vector=PolicyModulationVector(
                goal="pressure_third",
                combat=CombatModulation(attack_condition_override="earlier_if_safe"),
                scope=TacticalScopeModulation(location_intent="third"),
                tactical_task=TacticalTaskModulation(
                    task_type="pressure_with_main_army",
                    task_id="pressure-third",
                    location_intent="third",
                    priority=0.6,
                ),
                workers=WorkerModulation(repeat_order_guard_frames=32),
            ),
            issued_at_frame=1000,
        )

        self.assertEqual(
            ("workers", "combat", "scope", "tactical_task"),
            tactical.manager_bias_domains,
        )

    def test_blackboard_update_rejects_json_unsafe_update_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "update_id"):
            MicroMachineBlackboardUpdate(
                update_id='bad"id',
                vector=_defensive_vector(),
                issued_at_frame=1000,
            )

    def test_blackboard_update_round_trips_from_mapping(self) -> None:
        update = MicroMachineBlackboardUpdate(
            update_id="mod-002",
            vector=_defensive_vector(),
            issued_at_frame=10,
            rollback_update_id="mod-001",
        )

        restored = MicroMachineBlackboardUpdate.from_mapping(update.to_dict())

        self.assertEqual(update.update_id, restored.update_id)
        self.assertEqual(update.rollback_update_id, restored.rollback_update_id)
        self.assertEqual(update.vector.goal, restored.vector.goal)

    def test_stale_and_invalid_modulation_are_rejected_without_throwing(self) -> None:
        update = MicroMachineBlackboardUpdate(
            update_id="mod-stale",
            vector=_defensive_vector(),
            issued_at_frame=0,
        )

        stale = validate_micromachine_blackboard_update(
            update.to_dict(),
            current_frame=update.expires_at_frame + 1,
        )
        self.assertFalse(stale.accepted)
        self.assertEqual(
            MicroMachineBridgeFailureMode.STALE_MODULATION,
            stale.failure_mode,
        )

        semantic_update = MicroMachineBlackboardUpdate(
            update_id="mod-semantic",
            vector=PolicyModulationVector(
                goal="complete the operation",
                ttl_seconds=1,
                lifetime=LifetimeModulation(
                    mode="until_completed",
                    completion_conditions=("target_reached",),
                ),
            ),
            issued_at_frame=0,
        )
        semantic = validate_micromachine_blackboard_update(
            semantic_update.to_dict(),
            current_frame=semantic_update.expires_at_frame + 10_000,
        )
        self.assertTrue(semantic.accepted, semantic.to_dict())

        invalid = validate_micromachine_blackboard_update(
            {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "update_id": "bad",
                "issued_at_frame": 0,
                "vector": {"goal": "unsafe", "raw_action": "attack_move"},
            },
            current_frame=1,
        )
        self.assertFalse(invalid.accepted)
        self.assertEqual(
            MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
            invalid.failure_mode,
        )
        self.assertIn("raw runtime control", invalid.reason)

    def test_telemetry_envelope_and_rollback_are_json_ready(self) -> None:
        telemetry = MicroMachineTelemetry(
            frame=512,
            managers={
                "StrategyManager": {"active_build": "reaper_expand"},
                "CombatCommander": {"posture": "hold"},
            },
            active_modulation_ids=("mod-001",),
        )
        telemetry_envelope = MicroMachineBridgeEnvelope(
            message_type=MicroMachineBridgeMessageType.TELEMETRY,
            sequence=3,
            frame=512,
            payload=telemetry.to_dict(),
        )
        rollback = MicroMachineRollbackCommand(
            rollback_update_id="mod-001",
            requested_at_frame=520,
            reason="emergency user cancellation",
        )
        rollback_envelope = MicroMachineBridgeEnvelope(
            message_type=MicroMachineBridgeMessageType.ROLLBACK,
            sequence=4,
            frame=520,
            payload=rollback.to_dict(),
        )

        self.assertEqual("telemetry", telemetry_envelope.to_dict()["message_type"])
        self.assertEqual("rollback", rollback_envelope.to_dict()["message_type"])
        self.assertEqual(
            "emergency_rollback",
            rollback.to_dict()["failure_mode"],
        )
        json.dumps(telemetry_envelope.to_dict())
        json.dumps(rollback_envelope.to_dict())

    def test_bridge_error_envelope_covers_disconnected_and_provider_failures(self) -> None:
        for mode in (
            MicroMachineBridgeFailureMode.BRIDGE_DISCONNECTED,
            MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE,
            MicroMachineBridgeFailureMode.EMERGENCY_ROLLBACK,
        ):
            with self.subTest(mode=mode):
                envelope = build_micromachine_bridge_error_envelope(
                    failure_mode=mode,
                    reason=f"{mode.value} occurred",
                    sequence=9,
                    frame=900,
                )
                document = envelope.to_dict()
                self.assertEqual("error", document["message_type"])
                self.assertEqual(mode.value, document["payload"]["failure_mode"])


if __name__ == "__main__":
    unittest.main()
