"""Tests for the issue #10 deep policy modulation DSL."""

import json
import unittest

from starcraft_commander.policy_modulation import (
    CombatModulation,
    EconomyModulation,
    EmergencyModulation,
    PolicyModulationSource,
    PolicyModulationVector,
    PolicyOverrideLevel,
    PolicySafetyConstraint,
    ProductionModulation,
    ScoutingModulation,
    SquadModulation,
    StrategyModulation,
    TechModulation,
    WeightedBiases,
    reject_raw_policy_control_keys,
)


class WeightedBiasesTest(unittest.TestCase):
    def test_accepts_named_weights_in_signed_unit_range(self) -> None:
        biases = WeightedBiases({"proxy_cyclone": 0.75, "one_base_111": -0.25})

        self.assertEqual(
            {"proxy_cyclone": 0.75, "one_base_111": -0.25},
            biases.to_dict(),
        )

    def test_rejects_out_of_range_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "between -1.0 and 1.0"):
            WeightedBiases({"proxy_cyclone": 2.0})


class PolicyModulationVectorTest(unittest.TestCase):
    def test_vector_is_deep_json_ready_dsl_for_micro_machine_managers(self) -> None:
        vector = PolicyModulationVector(
            goal="contain_and_expand",
            source=PolicyModulationSource.LLM,
            override_level=PolicyOverrideLevel.CONSTRAINT,
            confidence=0.82,
            ttl_seconds=180,
            strategy=StrategyModulation(
                posture="defensive",
                preferred_builds=WeightedBiases({"reaper_expand": 0.6}),
                avoided_builds=WeightedBiases({"proxy_marauder": -0.8}),
                strategic_tags=("hold_ramp", "two_base"),
            ),
            economy=EconomyModulation(
                expand_bias=0.7,
                worker_production_bias=0.5,
                repair_priority=0.3,
            ),
            tech=TechModulation(
                structure_biases=WeightedBiases({"Starport": 0.4}),
                unit_biases=WeightedBiases({"SiegeTank": 0.6, "Marine": 0.2}),
                upgrade_biases=WeightedBiases({"Stimpack": 0.3}),
                tech_path_tags=("bio_tank",),
            ),
            production=ProductionModulation(
                queue_biases=WeightedBiases({"Factory": 0.4}),
                composition_biases=WeightedBiases({"anti_air": 0.2}),
                max_tech_deviation=0.25,
            ),
            combat=CombatModulation(
                aggression=-0.2,
                engage_threshold_delta=0.15,
                retreat_threshold_delta=0.2,
                defend_bias=0.8,
                combat_sim_confidence_margin=0.1,
            ),
            scouting=ScoutingModulation(
                scout_priority=0.6,
                risk_tolerance=-0.3,
                target_biases=WeightedBiases({"enemy_natural": 0.8}),
                require_fresh_enemy_observation=True,
            ),
            squad=SquadModulation(
                main_army_bias=0.6,
                harassment_bias=-0.2,
                defense_bias=0.7,
                regroup_bias=0.5,
            ),
            emergency=EmergencyModulation(cancel_attacks=True),
            constraints=(
                PolicySafetyConstraint(
                    key="no_attack_before",
                    value="08:00",
                    reason="user wants a defensive two-base setup",
                ),
            ),
            tags=("micro_machine", "defensive_macro"),
            rationale="사용자가 안정적으로 앞마당을 먹고 방어하라고 지시했다.",
        )

        document = vector.to_dict()

        self.assertEqual("contain_and_expand", document["goal"])
        self.assertEqual("llm", document["source"])
        self.assertEqual("constraint", document["override_level"])
        self.assertEqual("defensive", document["strategy"]["posture"])
        self.assertEqual(0.15, document["combat"]["engage_threshold_delta"])
        self.assertTrue(document["emergency"]["cancel_attacks"])
        self.assertEqual("no_attack_before", document["constraints"][0]["key"])
        json.dumps(document, ensure_ascii=False)

    def test_from_mapping_compiles_provider_payload_to_same_contract(self) -> None:
        vector = PolicyModulationVector.from_mapping(
            {
                "goal": "pressure_when_safe",
                "source": "neural_representation",
                "override_level": "bias",
                "confidence": 0.66,
                "ttl_seconds": 90,
                "strategy": {
                    "posture": "pressure",
                    "preferred_builds": {"proxy_cyclone": 0.5},
                },
                "combat": {
                    "aggression": 0.4,
                    "harassment_bias": 0.25,
                },
                "squad": {
                    "squad_role_biases": {"harass": 0.5, "main_army": 0.2},
                },
                "constraints": [{"key": "require_scouting_before_attack"}],
                "tags": ["representation_modulation"],
            }
        )

        self.assertEqual(PolicyModulationSource.NEURAL_REPRESENTATION, vector.source)
        self.assertEqual(PolicyOverrideLevel.BIAS, vector.override_level)
        self.assertEqual({"proxy_cyclone": 0.5}, vector.strategy.preferred_builds.to_dict())
        self.assertEqual(0.4, vector.combat.aggression)
        self.assertEqual("require_scouting_before_attack", vector.constraints[0].key)

    def test_rejects_raw_runtime_control_keys_at_any_depth(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            PolicyModulationVector.from_mapping(
                {
                    "goal": "unsafe",
                    "strategy": {"posture": "pressure"},
                    "python_sc2": "bot.units.attack(enemy_start)",
                }
            )
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys(
                {
                    "goal": "unsafe",
                    "nested": {"botai_method": "do"},
                }
            )
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys(
                {
                    "goal": "unsafe",
                    "sequence": [{"raw_action": "attack_move"}],
                }
            )

    def test_rejects_raw_runtime_control_keys_in_constraints(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            PolicyModulationVector.from_mapping(
                {
                    "goal": "unsafe",
                    "constraints": [{"key": "raw_action", "value": "attack"}],
                }
            )
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            PolicySafetyConstraint(
                key="safe_policy",
                value=[{"s2client_api": "issue_order"}],
            )

    def test_rejects_invalid_ranges_and_emergency_ttl(self) -> None:
        with self.assertRaisesRegex(ValueError, "confidence"):
            PolicyModulationVector(goal="bad", confidence=1.5)
        with self.assertRaisesRegex(ValueError, "ttl_seconds"):
            PolicyModulationVector(goal="bad", ttl_seconds=0)
        with self.assertRaisesRegex(ValueError, "emergency"):
            PolicyModulationVector(
                goal="panic",
                override_level=PolicyOverrideLevel.EMERGENCY,
                ttl_seconds=120,
            )

    def test_rejects_unknown_enums_and_invalid_booleans(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported policy override"):
            PolicyModulationVector(goal="bad", override_level="takeover")
        with self.assertRaisesRegex(TypeError, "allow_build_order_rewrite"):
            ProductionModulation(allow_build_order_rewrite="yes")


if __name__ == "__main__":
    unittest.main()
