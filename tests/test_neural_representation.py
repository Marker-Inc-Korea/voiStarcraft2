"""Tests for neural/SOTA representation provider attachment."""

import unittest

from starcraft_commander.micromachine_runtime import MicroMachineInMemoryBlackboard
from starcraft_commander.neural_representation import (
    DEFAULT_NEURAL_REPRESENTATION_AXES,
    NeuralRepresentationObservation,
    NeuralRepresentationPrediction,
    NeuralRepresentationProvider,
    StaticNeuralRepresentationAdapter,
    publish_neural_representation_modulation,
)
from starcraft_commander.policy_modulation import (
    PolicyModulationSource,
    PolicyOverrideLevel,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileStatus,
    PolicyModulationProviderRequest,
    compile_policy_modulation_from_provider,
)


class NeuralRepresentationProviderTest(unittest.TestCase):
    def test_provider_passes_candidate_axes_and_compiles_model_axes(self) -> None:
        adapter = StaticNeuralRepresentationAdapter(
            {
                "goal": "two_base_tank_hold",
                "confidence": 0.73,
                "override_level": "bias",
                "ttl_seconds": 180,
                "representation_axes": {
                    "strategy.posture": "defensive",
                    "economy.expand_bias": 0.55,
                    "tech.unit_biases.TERRAN_SIEGETANK": 0.8,
                    "combat.defend_bias": 0.75,
                    "scouting.require_fresh_enemy_observation": True,
                },
                "tags": ["alphastar_like"],
            },
            model_name="sota-fixture",
        )
        provider = NeuralRepresentationProvider(adapter)
        request = PolicyModulationProviderRequest(
            command_text="탱크 중심으로 안전하게 버텨",
            source=PolicyModulationSource.NEURAL_REPRESENTATION,
            game_state={"frame": 5200},
            allowed_override_levels=(PolicyOverrideLevel.BIAS,),
        )

        result = compile_policy_modulation_from_provider(provider, request)

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(1, len(adapter.observations))
        self.assertEqual(DEFAULT_NEURAL_REPRESENTATION_AXES, adapter.observations[0].candidate_axes)
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual(PolicyModulationSource.NEURAL_REPRESENTATION, result.vector.source)
        self.assertEqual("two_base_tank_hold", result.vector.goal)
        self.assertEqual("defensive", result.vector.strategy.posture)
        self.assertEqual({"TERRAN_SIEGETANK": 0.8}, result.vector.tech.unit_biases.to_dict())
        self.assertIn("alphastar_like", result.vector.tags)

    def test_publish_neural_representation_modulation_writes_backend_update(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        adapter = StaticNeuralRepresentationAdapter(
            NeuralRepresentationPrediction(
                goal="marine_pressure",
                confidence=0.66,
                representation_axes={
                    "strategy.posture": "pressure",
                    "combat.aggression": 0.6,
                    "squad.main_army_bias": 0.45,
                },
                model_name="sota-fixture",
            )
        )
        request = PolicyModulationProviderRequest(
            command_text="해병으로 압박해",
            source=PolicyModulationSource.NEURAL_REPRESENTATION,
        )

        result = publish_neural_representation_modulation(
            adapter,
            request,
            backend,
            current_frame=6400,
            update_id="neural-pressure-6400",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual("neural-pressure-6400", result.update.update_id)
        latest = backend.read_latest_update(current_frame=6401)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual("pressure", latest.vector.strategy.posture)
        self.assertEqual(0.6, latest.vector.combat.aggression)

    def test_raw_runtime_control_from_neural_adapter_is_refused_before_publish(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        adapter = StaticNeuralRepresentationAdapter(
            {
                "goal": "unsafe",
                "representation_axes": {
                    "combat.aggression": 0.5,
                    "raw_action": "attack_move",
                },
            }
        )
        request = PolicyModulationProviderRequest(
            command_text="공격해",
            source=PolicyModulationSource.NEURAL_REPRESENTATION,
        )

        result = publish_neural_representation_modulation(
            adapter,
            request,
            backend,
            current_frame=1,
        )

        self.assertFalse(result.ok)
        self.assertEqual(PolicyModulationCompileStatus.REFUSED, result.compile_result.status)
        self.assertIn("raw runtime control", result.compile_result.refusal_reason)
        self.assertIsNone(backend.read_latest_update(current_frame=1))

    def test_observation_rejects_raw_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            NeuralRepresentationObservation(
                command_text="unsafe",
                game_state={"python_sc2": {"do": "attack"}},
            )

    def test_invalid_axis_becomes_compile_refusal(self) -> None:
        adapter = StaticNeuralRepresentationAdapter(
            {
                "goal": "invalid_axis",
                "representation_axes": {"unknown.axis": 0.2},
            }
        )
        provider = NeuralRepresentationProvider(adapter)
        request = PolicyModulationProviderRequest(
            command_text="invalid",
            source=PolicyModulationSource.NEURAL_REPRESENTATION,
        )

        result = compile_policy_modulation_from_provider(provider, request)

        self.assertEqual(PolicyModulationCompileStatus.REFUSED, result.status)
        self.assertIn("unsupported representation axis", result.refusal_reason)


if __name__ == "__main__":
    unittest.main()
