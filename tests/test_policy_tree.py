"""Tests for the issue #10 human-interruptible policy tree seam."""

import json
import unittest

from starcraft_commander.policy_tree import (
    DEFAULT_PROFILE_KEY,
    MANUAL_PROFILE_KEY,
    CommanderPolicyDecision,
    CommanderPolicyTree,
    CommanderPolicyTreeInterface,
    CommanderStrategyProfile,
)
from starcraft_commander.standing_orders import StandingOrderController


class CommanderStrategyProfileTest(unittest.TestCase):
    def test_profile_validates_standing_order_leaves(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported kinds"):
            CommanderStrategyProfile(
                key="unsafe",
                korean_label="위험",
                description="bad leaf",
                standing_order_kinds=("call_python_sc2",),
            )

    def test_profile_to_dict_is_json_ready(self) -> None:
        profile = CommanderStrategyProfile(
            key="safe",
            korean_label="안전",
            description="safe macro profile",
            standing_order_kinds=("keep_worker_production",),
            recommended_utterances=("정찰 보내",),
            risk_notes=("pressure delayed",),
        )

        document = profile.to_dict()

        self.assertEqual("safe", document["key"])
        self.assertEqual(["keep_worker_production"], document["standing_order_kinds"])
        self.assertIn("keep_worker_production", document["standing_order_labels"])
        json.dumps(document, ensure_ascii=False)


class CommanderPolicyDecisionTest(unittest.TestCase):
    def test_rejected_decision_requires_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "needs a reason"):
            CommanderPolicyDecision(profile_key="safe_macro", accepted=False)

    def test_decision_to_dict_is_json_ready(self) -> None:
        decision = CommanderPolicyDecision(
            profile_key="safe_macro",
            accepted=True,
            standing_order_kinds=(
                "keep_worker_production",
                "prevent_supply_block",
            ),
            recommended_utterances=("정찰 보내",),
        )

        document = decision.to_dict()

        self.assertTrue(document["accepted"])
        self.assertEqual(
            ["keep_worker_production", "prevent_supply_block"],
            document["standing_order_kinds"],
        )
        json.dumps(document, ensure_ascii=False)


class CommanderPolicyTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = CommanderPolicyTree()

    def test_satisfies_runtime_checkable_interface(self) -> None:
        self.assertIsInstance(self.tree, CommanderPolicyTreeInterface)

    def test_default_profile_is_safe_macro_with_bounded_policy_leaves(self) -> None:
        decision = self.tree.decide()

        self.assertEqual(DEFAULT_PROFILE_KEY, decision.profile_key)
        self.assertTrue(decision.accepted)
        self.assertEqual(
            ("keep_worker_production", "prevent_supply_block"),
            decision.standing_order_kinds,
        )
        self.assertIn("정찰 보내", decision.recommended_utterances)

    def test_human_manual_override_disables_autonomous_policy_leaves(self) -> None:
        decision = self.tree.decide("pressure_when_safe", human_override="pause")

        self.assertTrue(decision.accepted)
        self.assertEqual(MANUAL_PROFILE_KEY, decision.profile_key)
        self.assertEqual((), decision.standing_order_kinds)
        self.assertEqual("pause", decision.human_override)
        self.assertIn("human override active: pause", decision.warnings)

    def test_human_can_disable_autonomy_without_selecting_profile(self) -> None:
        decision = self.tree.decide(allow_autonomy=False)

        self.assertTrue(decision.accepted)
        self.assertEqual(MANUAL_PROFILE_KEY, decision.profile_key)
        self.assertEqual("autonomy_disabled", decision.human_override)
        self.assertEqual((), decision.standing_order_kinds)

    def test_unknown_profile_is_rejected_without_policy_leaves(self) -> None:
        decision = self.tree.decide("alpha_star_takeover")

        self.assertFalse(decision.accepted)
        self.assertEqual((), decision.standing_order_kinds)
        self.assertIn("unknown policy profile", decision.rejection_reason)
        self.assertEqual(decision, self.tree.last_decision())

    def test_model_output_can_only_select_bounded_profiles(self) -> None:
        decision = self.tree.decide_from_model_output(
            {"strategy_profile": "information_first"}
        )

        self.assertTrue(decision.accepted)
        self.assertEqual("information_first", decision.profile_key)
        self.assertEqual(
            ("keep_worker_production", "prevent_supply_block"),
            decision.standing_order_kinds,
        )
        self.assertIn("적 본진 정찰 보내", decision.recommended_utterances)

    def test_model_output_attempting_raw_sc2_control_is_rejected(self) -> None:
        decision = self.tree.decide_from_model_output(
            {
                "strategy_profile": "pressure_when_safe",
                "python_sc2_call": "bot.units.attack(enemy_start)",
            }
        )

        self.assertFalse(decision.accepted)
        self.assertEqual((), decision.standing_order_kinds)
        self.assertIn("raw runtime control", decision.rejection_reason)

    def test_apply_to_standing_orders_routes_through_existing_controller(self) -> None:
        standing_orders = StandingOrderController()
        decision = self.tree.decide("safe_macro")

        first = self.tree.apply_to_standing_orders(decision, standing_orders)
        second = self.tree.apply_to_standing_orders(decision, standing_orders)

        self.assertEqual(
            ("keep_worker_production", "prevent_supply_block"),
            first,
        )
        self.assertEqual((), second)
        self.assertEqual(
            ("keep_worker_production", "prevent_supply_block"),
            standing_orders.active_kinds(),
        )

    def test_rejected_decision_applies_no_standing_orders(self) -> None:
        standing_orders = StandingOrderController()
        decision = self.tree.decide("unknown")

        self.assertEqual((), self.tree.apply_to_standing_orders(decision, standing_orders))
        self.assertEqual((), standing_orders.active_kinds())

    def test_tree_snapshot_is_json_ready_for_dashboard(self) -> None:
        self.tree.decide("defensive_hold")

        document = self.tree.to_dict()

        self.assertIn("profiles", document)
        self.assertEqual("defensive_hold", document["last_decision"]["profile_key"])
        json.dumps(document, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()

