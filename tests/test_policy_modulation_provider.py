"""Tests for compiling bounded provider output into policy modulation."""

import unittest

from starcraft_commander.policy_modulation import (
    PolicyModulationSource,
    PolicyOverrideLevel,
)
from starcraft_commander.policy_modulation_provider import (
    POLICY_MODULATION_PROVIDER_SOURCES,
    PolicyModulationCompileStatus,
    PolicyModulationProviderRequest,
    compile_policy_modulation_from_provider,
    compile_policy_modulation_provider_output,
)


class StaticModulationProvider:
    source = PolicyModulationSource.UI

    def __init__(self, output):
        self.output = output
        self.requests = []

    def propose_policy_modulation(self, request):
        self.requests.append(request)
        return self.output


class PolicyModulationProviderCompilerTest(unittest.TestCase):
    def test_declares_provider_sources_for_llm_ui_replay_and_neural(self) -> None:
        self.assertIn(PolicyModulationSource.LLM, POLICY_MODULATION_PROVIDER_SOURCES)
        self.assertIn(PolicyModulationSource.UI, POLICY_MODULATION_PROVIDER_SOURCES)
        self.assertIn(
            PolicyModulationSource.REPLAY_IMITATION,
            POLICY_MODULATION_PROVIDER_SOURCES,
        )
        self.assertIn(
            PolicyModulationSource.NEURAL_REPRESENTATION,
            POLICY_MODULATION_PROVIDER_SOURCES,
        )

    def test_compiles_korean_llm_payload_to_deep_policy_vector(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "intent": "안정적으로 앞마당 먹고 탱크로 수비해",
                "override": "constraint",
                "confidence": 0.84,
                "ttl": 180,
                "posture": "defensive",
                "timing_biases": {"tank_push": 0.35},
                "economy": {
                    "expand_bias": 0.7,
                    "worker_production_bias": 0.4,
                    "gas_worker_target_bias": 0.5,
                },
                "tech": {"unit_biases": {"SiegeTank": 0.6}},
                "addon_biases": {"TechLab": 0.45},
                "combat": {"defend_bias": 0.8, "aggression": -0.2},
                "commitment_level": 0.35,
                "pressure_window_frames": 3300,
                "attack_condition_override": "earlier_if_safe",
                "siege_position_bias": 0.7,
                "target_priority_biases": {"Baneling": 0.9},
                "scouting": {"require_fresh_enemy_observation": True},
                "scan_priority": 0.4,
                "army_group": "main",
                "unit_classes": ["marine", "siege_tank"],
                "location_intent": "enemy_natural",
                "contain_bias": 0.3,
                "squad_flank_bias": 0.2,
                "prioritize_repair": True,
                "tags": ["korean_order", "micro_machine"],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(PolicyModulationCompileStatus.COMPILED, result.status)
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual(PolicyModulationSource.LLM, result.vector.source)
        self.assertEqual(PolicyOverrideLevel.CONSTRAINT, result.vector.override_level)
        self.assertEqual("defensive", result.vector.strategy.posture)
        self.assertEqual({"tank_push": 0.35}, result.vector.strategy.timing_biases.to_dict())
        self.assertEqual(0.7, result.vector.economy.expand_bias)
        self.assertEqual(0.5, result.vector.economy.gas_worker_target_bias)
        self.assertEqual({"SiegeTank": 0.6}, result.vector.tech.unit_biases.to_dict())
        self.assertEqual({"TechLab": 0.45}, result.vector.production.addon_biases.to_dict())
        self.assertEqual(0.8, result.vector.combat.defend_bias)
        self.assertEqual(0.35, result.vector.combat.commitment_level)
        self.assertEqual(3300, result.vector.combat.pressure_window_frames)
        self.assertEqual("earlier_if_safe", result.vector.combat.attack_condition_override)
        self.assertEqual(0.7, result.vector.combat.siege_position_bias)
        self.assertEqual({"Baneling": 0.9}, result.vector.combat.target_priority_biases.to_dict())
        self.assertEqual(0.4, result.vector.scouting.scan_priority)
        self.assertEqual(0.3, result.vector.squad.contain_bias)
        self.assertEqual(0.2, result.vector.squad.flank_bias)
        self.assertEqual("main", result.vector.scope.army_group)
        self.assertEqual(("marine", "siege_tank"), result.vector.scope.unit_classes)
        self.assertEqual("enemy_natural", result.vector.scope.location_intent)
        self.assertTrue(result.vector.emergency.prioritize_repair)

    def test_compiles_neural_representation_axes_to_same_contract(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "neural_representation",
                "goal": "two_base_defensive_tank_hold",
                "override_level": "bias",
                "confidence": 0.71,
                "ttl_seconds": 90,
                "representation": {
                    "strategy.posture": "defensive",
                    "economy.expand_bias": 0.7,
                    "economy.expansion_safety_bias": 0.5,
                    "tech.unit_biases.SiegeTank": 0.6,
                    "production.addon_biases.TechLab": 0.4,
                    "combat.defend_bias": 0.8,
                    "combat.aggression": -0.2,
                    "combat.commitment_level": 0.4,
                    "combat.target_priority_biases.Baneling": 0.7,
                    "scouting.require_fresh_enemy_observation": True,
                    "scouting.hidden_tech_scout_bias": 0.5,
                    "squad.reinforce_bias": 0.4,
                    "scope.army_group": "siege",
                    "scope.location_intent": "enemy_natural",
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        vector = result.vector
        self.assertEqual(PolicyModulationSource.NEURAL_REPRESENTATION, vector.source)
        self.assertEqual("defensive", vector.strategy.posture)
        self.assertEqual(0.7, vector.economy.expand_bias)
        self.assertEqual(0.5, vector.economy.expansion_safety_bias)
        self.assertEqual({"SiegeTank": 0.6}, vector.tech.unit_biases.to_dict())
        self.assertEqual({"TechLab": 0.4}, vector.production.addon_biases.to_dict())
        self.assertEqual(0.8, vector.combat.defend_bias)
        self.assertEqual(0.4, vector.combat.commitment_level)
        self.assertEqual({"Baneling": 0.7}, vector.combat.target_priority_biases.to_dict())
        self.assertEqual(0.5, vector.scouting.hidden_tech_scout_bias)
        self.assertEqual(0.4, vector.squad.reinforce_bias)
        self.assertEqual("siege", vector.scope.army_group)
        self.assertEqual("enemy_natural", vector.scope.location_intent)

    def test_compiles_issue_third_scope_wording(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "goal": "pressure_third",
                "location_intent": "third",
                "combat": {"attack_condition_override": "earlier_if_safe"},
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("third", result.vector.scope.location_intent)

    def test_flat_aliases_survive_later_domain_objects(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "goal": "two_base_economy",
                "gas_worker_target_bias": 0.5,
                "economy": {"expand_bias": 0.7},
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual(0.5, result.vector.economy.gas_worker_target_bias)
        self.assertEqual(0.7, result.vector.economy.expand_bias)

    def test_rejects_raw_actions_without_throwing(self) -> None:
        for payload in (
            {"goal": "unsafe", "raw_action": "attack_move"},
            {"goal": "unsafe", "rawCommand": "attack"},
            {"goal": "unsafe", "directCommand": "move"},
            {"goal": "unsafe", "directKey": "a"},
            {"goal": "unsafe", "directKeys": ["a"]},
            {"goal": "unsafe", "directSC2Command": "attack"},
            {"goal": "unsafe", "sc2Command": "attack"},
            {"goal": "unsafe", "keyboardKey": "a"},
            {"goal": "unsafe", "keyDown": "a"},
            {"goal": "unsafe", "keyUp": "a"},
            {"goal": "unsafe", "keyboardShortcut": "control+a"},
            {"goal": "unsafe", "sendKey": "a"},
            {"goal": "unsafe", "rawKey": "a"},
            {"goal": "unsafe", "attackMove": "enemy natural"},
            {"goal": "unsafe", "S2ClientAPI": "attack"},
            {"goal": "unsafe", "BotAIMethod": "do"},
            {"goal": "unsafe", "S2.Client.API": "attack"},
            {"goal": "unsafe", "S2/Client/API": "attack"},
            {"goal": "unsafe", "Bot.AI.Method": "do"},
            {"goal": "unsafe", "attack.move": "enemy natural"},
            {"goal": "unsafe", "representation": {"python_sc2.do": "attack"}},
            {"goal": "unsafe", "constraints": [{"key": "unit_tag", "value": 1}]},
        ):
            with self.subTest(payload=payload):
                result = compile_policy_modulation_provider_output(payload)
                self.assertEqual(PolicyModulationCompileStatus.REFUSED, result.status)
                self.assertIn("raw runtime control", result.refusal_reason)
                self.assertIsNone(result.vector)

    def test_surfaces_clarification_and_refusal_without_crashing(self) -> None:
        clarification = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "status": "clarification_required",
                "clarification_prompt": "어느 타이밍까지 수비할까요?",
            }
        )
        self.assertEqual(
            PolicyModulationCompileStatus.CLARIFICATION_REQUIRED,
            clarification.status,
        )
        self.assertIn("어느 타이밍", clarification.clarification_prompt)

        refusal = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "status": "refused",
                "refusal_reason": "strategy objective is missing",
                "modulation": {"goal": "ignored", "posture": "pressure"},
            }
        )
        self.assertEqual(PolicyModulationCompileStatus.REFUSED, refusal.status)
        self.assertIn("missing", refusal.refusal_reason)

    def test_surfaces_wrapped_clarification_and_refusal_without_crashing(self) -> None:
        clarification = compile_policy_modulation_provider_output(
            {
                "source": "ui",
                "modulation": {
                    "status": "clarification_required",
                    "clarification_prompt": "공격 타이밍을 더 구체화해 주세요.",
                },
            }
        )
        self.assertEqual(
            PolicyModulationCompileStatus.CLARIFICATION_REQUIRED,
            clarification.status,
        )
        self.assertEqual(PolicyModulationSource.UI, clarification.source)
        self.assertEqual(
            "공격 타이밍을 더 구체화해 주세요.",
            clarification.clarification_prompt,
        )
        self.assertIsNone(clarification.vector)

        refusal = compile_policy_modulation_provider_output(
            {
                "policy_modulation": {
                    "status": "refused",
                    "refusal_reason": "raw control request refused",
                },
            }
        )
        self.assertEqual(PolicyModulationCompileStatus.REFUSED, refusal.status)
        self.assertEqual("raw control request refused", refusal.refusal_reason)
        self.assertIsNone(refusal.vector)

    def test_wrapped_terminal_status_wins_over_outer_envelope_status(self) -> None:
        for outer_status in ("compiled", "ok"):
            with self.subTest(outer_status=outer_status):
                result = compile_policy_modulation_provider_output(
                    {
                        "status": outer_status,
                        "modulation": {
                            "status": "clarification_required",
                            "clarification_prompt": "공격 타이밍을 더 구체화해 주세요.",
                        },
                    }
                )
                self.assertEqual(
                    PolicyModulationCompileStatus.CLARIFICATION_REQUIRED,
                    result.status,
                )
                self.assertEqual(
                    "공격 타이밍을 더 구체화해 주세요.",
                    result.clarification_prompt,
                )
                self.assertIsNone(result.vector)

    def test_compiles_from_provider_interface(self) -> None:
        provider = StaticModulationProvider(
            {
                "modulation": {
                    "goal": "hold_ramp",
                    "posture": "defensive",
                    "combat": {"defend_bias": 0.5},
                }
            }
        )
        request = PolicyModulationProviderRequest(
            command_text="입구 막고 수비해",
            source=PolicyModulationSource.UI,
            allowed_override_levels=(PolicyOverrideLevel.BIAS,),
            tags=("manual",),
        )

        result = compile_policy_modulation_from_provider(provider, request)

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(1, len(provider.requests))
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual(PolicyModulationSource.UI, result.vector.source)
        self.assertEqual("hold_ramp", result.vector.goal)

    def test_provider_request_override_allowlist_is_enforced(self) -> None:
        provider = StaticModulationProvider(
            {
                "modulation": {
                    "goal": "panic",
                    "override_level": "emergency",
                    "ttl_seconds": 30,
                }
            }
        )
        request = PolicyModulationProviderRequest(
            command_text="잠깐만 공격 멈춰",
            source=PolicyModulationSource.UI,
            allowed_override_levels=(PolicyOverrideLevel.BIAS,),
        )

        result = compile_policy_modulation_from_provider(provider, request)

        self.assertEqual(PolicyModulationCompileStatus.REFUSED, result.status)
        self.assertIn("outside the allowed", result.refusal_reason)

    def test_invalid_payload_becomes_refusal_result(self) -> None:
        result = compile_policy_modulation_provider_output(
            {"source": "llm", "confidence": 2.0},
            default_goal="defend",
        )

        self.assertEqual(PolicyModulationCompileStatus.REFUSED, result.status)
        self.assertIn("confidence", result.refusal_reason)


if __name__ == "__main__":
    unittest.main()
