"""Tests for issue #10 modulation observability and evaluation contracts."""

import json
import unittest

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_GAME_LOOPS_PER_SECOND,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
)
from starcraft_commander.policy_modulation import (
    CombatModulation,
    PolicyModulationVector,
    PolicyOverrideLevel,
    StrategyModulation,
)
from starcraft_commander.policy_observability import (
    REQUIRED_EVALUATION_METRICS,
    MicroMachineModulationEvaluationPlan,
    ModulationEvaluationMetric,
    PolicyModulationBridgeStatus,
    build_issue10_evaluation_plan,
    build_policy_modulation_dashboard_snapshot,
    validate_dashboard_snapshot_payload,
)
from starcraft_commander.policy_tree import CommanderPolicyTree


def _vector(ttl_seconds: int = 10) -> PolicyModulationVector:
    return PolicyModulationVector(
        goal="defensive_hold",
        override_level=PolicyOverrideLevel.CONSTRAINT,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(posture="defensive"),
        combat=CombatModulation(defend_bias=0.6),
    )


class PolicyModulationObservabilityTest(unittest.TestCase):
    def test_dashboard_snapshot_filters_stale_updates_and_is_json_ready(self) -> None:
        active = MicroMachineBlackboardUpdate(
            update_id="active",
            vector=_vector(ttl_seconds=10),
            issued_at_frame=100,
        )
        stale = MicroMachineBlackboardUpdate(
            update_id="stale",
            vector=_vector(ttl_seconds=1),
            issued_at_frame=0,
        )
        telemetry = MicroMachineTelemetry(
            frame=150,
            managers={"StrategyManager": {"active_build": "safe_macro"}},
            active_modulation_ids=("active",),
        )

        snapshot = build_policy_modulation_dashboard_snapshot(
            (active, stale),
            current_frame=MICROMACHINE_GAME_LOOPS_PER_SECOND + 1,
            bridge_status=PolicyModulationBridgeStatus.CONNECTED,
            telemetry=telemetry,
            notes=("dashboard safe",),
        )
        document = snapshot.to_dict()

        self.assertEqual("connected", document["bridge_status"])
        self.assertEqual(1, document["active_modulation_count"])
        self.assertEqual("active", document["active_updates"][0]["update_id"])
        self.assertEqual(["stale"], document["stale_update_ids"])
        self.assertEqual("MicroMachine", document["telemetry"]["bot_name"])
        json.dumps(document, ensure_ascii=False)
        validate_dashboard_snapshot_payload(document)

    def test_policy_tree_dashboard_snapshot_can_include_modulation_state(self) -> None:
        tree = CommanderPolicyTree()
        tree.decide("defensive_hold")
        update = MicroMachineBlackboardUpdate(
            update_id="mod-1",
            vector=_vector(),
            issued_at_frame=0,
        )
        modulation = build_policy_modulation_dashboard_snapshot(
            (update,),
            current_frame=1,
            bridge_status="simulated",
        ).to_dict()

        document = tree.to_dict(modulation_snapshot=modulation)

        self.assertEqual("defensive_hold", document["last_decision"]["profile_key"])
        self.assertEqual(1, document["policy_modulation"]["active_modulation_count"])
        json.dumps(document, ensure_ascii=False)

    def test_policy_tree_rejects_unsafe_modulation_dashboard_payload(self) -> None:
        tree = CommanderPolicyTree()

        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            tree.to_dict(
                modulation_snapshot={
                    "bridge_status": "connected",
                    "nested": [{"python_sc2": "bot.do(action)"}],
                }
            )

    def test_evaluation_plan_contains_required_baseline_metrics(self) -> None:
        plan = build_issue10_evaluation_plan()
        document = plan.to_dict()
        metric_keys = {metric["key"] for metric in document["metrics"]}

        self.assertEqual(REQUIRED_EVALUATION_METRICS, metric_keys)
        self.assertEqual("MicroMachine baseline", document["baseline_bot"])
        self.assertIn("MicroMachine + voi", document["modulated_bot"])
        self.assertIn("emergency rollback remains available", document["safety_gates"])
        json.dumps(document)

    def test_evaluation_plan_rejects_missing_required_metric(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required metrics"):
            MicroMachineModulationEvaluationPlan(
                metrics=(
                    ModulationEvaluationMetric(
                        key="win_loss",
                        description="wins",
                        aggregation="rate",
                        desired_direction="higher",
                    ),
                )
            )

    def test_dashboard_snapshot_surfaces_bridge_failure_mode(self) -> None:
        snapshot = build_policy_modulation_dashboard_snapshot(
            (),
            current_frame=0,
            bridge_status=PolicyModulationBridgeStatus.DISCONNECTED,
            last_failure=MicroMachineBridgeFailureMode.BRIDGE_DISCONNECTED,
        )

        document = snapshot.to_dict()

        self.assertEqual("disconnected", document["bridge_status"])
        self.assertEqual("bridge_disconnected", document["last_failure"])


if __name__ == "__main__":
    unittest.main()
