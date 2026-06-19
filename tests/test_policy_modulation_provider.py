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
                "economy": {"expand_bias": 0.7, "worker_production_bias": 0.4},
                "tech": {"unit_biases": {"SiegeTank": 0.6}},
                "combat": {"defend_bias": 0.8, "aggression": -0.2},
                "scouting": {"require_fresh_enemy_observation": True},
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
        self.assertEqual(0.7, result.vector.economy.expand_bias)
        self.assertEqual({"SiegeTank": 0.6}, result.vector.tech.unit_biases.to_dict())
        self.assertEqual(0.8, result.vector.combat.defend_bias)

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
                    "tech.unit_biases.SiegeTank": 0.6,
                    "combat.defend_bias": 0.8,
                    "combat.aggression": -0.2,
                    "scouting.require_fresh_enemy_observation": True,
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
        self.assertEqual({"SiegeTank": 0.6}, vector.tech.unit_biases.to_dict())
        self.assertEqual(0.8, vector.combat.defend_bias)

    def test_rejects_raw_actions_without_throwing(self) -> None:
        for payload in (
            {"goal": "unsafe", "raw_action": "attack_move"},
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
