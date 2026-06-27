"""Tests for MicroMachine tactical-effect evidence classification."""

import json
import unittest

from starcraft_commander.micromachine_tactical_evidence import (
    classify_micromachine_tactical_evidence,
    normalize_tactical_effect_tags,
)


class MicroMachineTacticalEvidenceTest(unittest.TestCase):
    def test_normalizes_profile_and_effect_aliases(self) -> None:
        self.assertEqual(
            ("pressure", "hold", "target_priority", "scout"),
            normalize_tactical_effect_tags(
                (
                    "aggressive_pressure",
                    "defensive-hold",
                    "worker_line",
                    "scouting_map_control",
                )
            ),
        )

    def test_passes_when_expected_effects_have_behavior_evidence(self) -> None:
        telemetry = {
            "frame": 12450,
            "managers": {
                "CombatCommander": {
                    "consumed_axes": "combat.aggression,combat.target_priority_biases.*",
                    "main_attack_order": "Attack enemy natural",
                },
                "Squad": {
                    "consumed_axes": "squad.contain_bias,scope.location_intent",
                    "contain_bias": 0.35,
                    "scope_location_intent": "enemy_natural",
                    "selected_target_class": "worker_line",
                },
            },
        }
        log_text = "\n".join(
            (
                "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                "12455: calcTargets | target worker_line selected by policy modulation",
            )
        )

        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            log_text=log_text,
            expected_effects=("aggressive_pressure", "contain", "target_priority"),
            source_paths={"bot_log": "micromachine.log"},
        )

        self.assertTrue(evidence.ok, evidence.to_dict())
        self.assertEqual("passed", evidence.status)
        self.assertEqual((), evidence.missing_effects)
        self.assertIn("pressure", evidence.observed_effects)
        self.assertIn("contain", evidence.observed_effects)
        self.assertIn("target_priority", evidence.observed_effects)
        self.assertEqual(
            ("combat.aggression", "combat.target_priority_biases.*"),
            evidence.consumed_axes_by_manager["CombatCommander"],
        )
        json.dumps(evidence.to_dict())

    def test_consumed_axes_alone_do_not_satisfy_behavior_effect(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 12000,
                "managers": {
                    "CombatCommander": {
                        "consumed_axes": "combat.aggression",
                        "bounded_intervention": True,
                    }
                },
            },
            expected_effects=("pressure",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("pressure",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)
        self.assertEqual(
            ("combat.aggression",),
            evidence.consumed_axes_by_manager["CombatCommander"],
        )

    def test_bias_only_desired_state_does_not_satisfy_behavior_effects(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "CombatCommander": {
                        "defend_bias": 0.8,
                        "force_retreat": True,
                        "hold_position": True,
                    },
                    "Squad": {
                        "contain_bias": 0.3,
                        "scope_location_intent": "enemy_natural",
                        "harassment_bias": 0.5,
                        "army_group": "harass",
                    },
                    "ScoutManager": {"scout_priority": 0.9},
                },
            },
            expected_effects=("hold", "contain", "harass", "scout"),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("hold", "contain", "harass", "scout"), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_partial_when_only_some_expected_effects_are_observed(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "Squad": {
                        "main_attack_order": "Attack enemy natural",
                    }
                },
            },
            expected_effects=("contain", "target_priority"),
        )

        self.assertEqual("partial", evidence.status)
        self.assertEqual(("target_priority",), evidence.missing_effects)
        self.assertIn("contain", evidence.observed_effects)

    def test_refused_status_takes_precedence(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={"frame": 77, "managers": {}},
            refusal_reasons=("공격 타이밍을 더 구체화해 주세요.",),
            expected_effects=("pressure",),
        )

        self.assertEqual("refused", evidence.status)
        self.assertIn("공격 타이밍", evidence.refusal_reasons[0])
        self.assertIn("refused", evidence.observed_effects)

    def test_unsupported_expected_effect_is_explicit(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={"frame": 1, "managers": {}},
            expected_effects=("raw_unit_tag_attack",),
        )

        self.assertEqual("unsupported", evidence.status)
        self.assertEqual(("raw_unit_tag_attack",), evidence.unsupported_effects)


if __name__ == "__main__":
    unittest.main()
