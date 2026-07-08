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
        self.assertIn(
            PolicyModulationSource.SMOKE_KEYWORD,
            POLICY_MODULATION_PROVIDER_SOURCES,
        )
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
                "doctrine": "tank_defensive_hold",
                "timing_biases": {"tank_push": 0.35},
                "economy": {
                    "expand_bias": 0.7,
                    "worker_production_bias": 0.4,
                    "gas_worker_target_bias": 0.5,
                },
                "repeat_order_guard_frames": 32,
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
        self.assertEqual("tank_defensive_hold", result.vector.strategy.doctrine)
        self.assertEqual({"tank_push": 0.35}, result.vector.strategy.timing_biases.to_dict())
        self.assertEqual(0.7, result.vector.economy.expand_bias)
        self.assertEqual(0.5, result.vector.economy.gas_worker_target_bias)
        self.assertEqual(32, result.vector.workers.repeat_order_guard_frames)
        self.assertEqual({"TERRAN_SIEGETANK": 0.6}, result.vector.tech.unit_biases.to_dict())
        self.assertEqual({"BARRACKS_TECHLAB": 0.45}, result.vector.production.addon_biases.to_dict())
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
        self.assertEqual(("TERRAN_MARINE", "TERRAN_SIEGETANK"), result.vector.scope.unit_classes)
        self.assertEqual("enemy_natural", result.vector.scope.location_intent)
        self.assertTrue(result.vector.emergency.prioritize_repair)

    def test_canonicalizes_llm_friendly_tactical_enum_aliases(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "공격적으로 안전할 때 압박",
                "strategy": {"posture": "aggressive"},
                "combat": {"attack_condition_override": "opportunistic"},
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("pressure", result.vector.strategy.posture)
        self.assertEqual(
            "earlier_if_safe",
            result.vector.combat.attack_condition_override,
        )

    def test_repairs_empty_combat_tactical_location_before_publish(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 러쉬 진행해",
                "scope": {
                    "army_group": "main",
                    "unit_classes": ["marine"],
                    "location_intent": "",
                },
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "unit_classes": ["marine"],
                    "location_intent": "",
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        vector = result.vector
        self.assertEqual("enemy_natural", vector.scope.location_intent)
        self.assertEqual("enemy_natural", vector.tactical_task.location_intent)
        self.assertEqual("force_when_threshold_met", vector.combat.attack_condition_override)
        self.assertGreaterEqual(vector.combat.aggression, 0.65)
        self.assertGreaterEqual(vector.combat.attack_timing_bias, 0.65)
        self.assertGreaterEqual(vector.combat.commitment_level, 0.55)
        self.assertGreaterEqual(vector.squad.main_army_bias, 0.6)

    def test_repairs_empty_marine_scout_location_before_publish(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린으로 정찰해",
                "scope": {
                    "army_group": "scout",
                    "unit_classes": ["marine"],
                    "location_intent": "",
                },
                "tactical_task": {
                    "task_type": "scout_with_units",
                    "unit_classes": ["marine"],
                    "location_intent": "",
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        vector = result.vector
        self.assertEqual("enemy_main", vector.scope.location_intent)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertEqual("scout", vector.scope.army_group)
        self.assertEqual(1, vector.scope.min_units)
        self.assertEqual(2, vector.scope.max_units)
        self.assertGreaterEqual(vector.scouting.scout_priority, 0.75)
        self.assertGreaterEqual(
            vector.squad.squad_role_biases.to_dict()["marine_scout"],
            0.75,
        )

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
                    "workers.repeat_order_guard_frames": 48,
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
        self.assertEqual(48, vector.workers.repeat_order_guard_frames)
        self.assertEqual({"TERRAN_SIEGETANK": 0.6}, vector.tech.unit_biases.to_dict())
        self.assertEqual({"BARRACKS_TECHLAB": 0.4}, vector.production.addon_biases.to_dict())
        self.assertEqual(0.8, vector.combat.defend_bias)
        self.assertEqual(0.4, vector.combat.commitment_level)
        self.assertEqual({"Baneling": 0.7}, vector.combat.target_priority_biases.to_dict())
        self.assertEqual(0.5, vector.scouting.hidden_tech_scout_bias)
        self.assertEqual(0.4, vector.squad.reinforce_bias)
        self.assertEqual("siege", vector.scope.army_group)
        self.assertEqual("enemy_natural", vector.scope.location_intent)

    def test_compiles_wrapped_doctrine_alias_to_strategy_domain(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "policy_modulation": {
                    "goal": "switch_to_drop_play",
                    "strategy_doctrine": "drop_harassment",
                    "posture": "pressure",
                    "production": {
                        "queue_biases": {
                            "TERRAN_STARPORT": 0.65,
                            "TERRAN_MEDIVAC": 0.8,
                        },
                        "production_facility_biases": {"TERRAN_STARPORT": 0.65},
                    },
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("drop_harassment", result.vector.strategy.doctrine)
        self.assertEqual("pressure", result.vector.strategy.posture)
        self.assertEqual(
            {"TERRAN_STARPORT": 0.65, "TERRAN_MEDIVAC": 0.8},
            result.vector.production.queue_biases.to_dict(),
        )
        self.assertEqual(
            {"TERRAN_STARPORT": 0.65},
            result.vector.production.production_facility_biases.to_dict(),
        )

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

    def test_compiles_llm_worker_aliases_from_nested_workers_domain(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "marine scout pressure",
                "workers": {
                    "scout_worker_bias": 0.7,
                    "repair_worker_bias": 0.4,
                    "pull_workers_for_defense_bias": 0.6,
                    "scv_production_bias": 0.5,
                },
                "combat": {"aggression": 0.4},
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual(0.7, result.vector.scouting.scout_priority)
        self.assertEqual(0.4, result.vector.economy.repair_priority)
        self.assertEqual(0.5, result.vector.economy.worker_production_bias)
        self.assertTrue(result.vector.emergency.pull_workers_for_defense)
        self.assertIn(
            "mapped provider field: workers.scout_worker_bias->scouting.scout_priority",
            result.warnings,
        )

    def test_compiles_marine_scouting_policy_without_raw_commands(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "status": "compiled",
                "modulation": {
                    "goal": "마린 5마리로 안전하게 정찰",
                    "source": "llm",
                    "override_level": "directive",
                    "strategy": {
                        "posture": "balanced",
                        "doctrine": "scouting_map_control",
                    },
                    "scouting": {
                        "scout_priority": 0.75,
                        "scout_cadence_bias": 0.45,
                        "risk_tolerance": 0.15,
                        "require_fresh_enemy_observation": True,
                        "target_biases": {
                            "enemy_natural": 0.3,
                            "watchtower": 0.35,
                        },
                    },
                    "squad": {
                        "split_army_bias": 0.45,
                        "squad_role_biases": {"marine_scout": 0.8},
                    },
                    "combat": {
                        "aggression": -0.15,
                        "preserve_army_bias": 0.45,
                    },
                    "scope": {
                        "army_group": "scout",
                        "unit_classes": ["marine"],
                        "min_units": 5,
                        "max_units": 5,
                        "duration_seconds": 120,
                        "require_safety_margin": 0.35,
                        "allow_partial_scope": True,
                    },
                    "tactical_task": {
                        "task_type": "scout",
                        "task_id": "marine-scout-5",
                        "unit_classes": ["marine"],
                        "location_intent": "enemy_base",
                        "priority": 0.75,
                        "min_units": 5,
                        "max_units": 5,
                        "duration_seconds": 120,
                        "allow_partial": True,
                        "safety_margin": 0.35,
                    },
                    "tags": ["scouting", "marine_scout"],
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        vector = result.vector
        self.assertEqual("scouting_map_control", vector.strategy.doctrine)
        self.assertEqual(0.75, vector.scouting.scout_priority)
        self.assertEqual(0.45, vector.scouting.scout_cadence_bias)
        self.assertEqual("scout", vector.scope.army_group)
        self.assertEqual(("TERRAN_MARINE",), vector.scope.unit_classes)
        self.assertEqual(5, vector.scope.min_units)
        self.assertEqual(5, vector.scope.max_units)
        self.assertEqual("scout_with_units", vector.tactical_task.task_type)
        self.assertEqual("marine-scout-5", vector.tactical_task.task_id)
        self.assertEqual(("TERRAN_MARINE",), vector.tactical_task.unit_classes)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertEqual(0.8, vector.squad.squad_role_biases.to_dict()["marine_scout"])

    def test_canonicalizes_korean_compound_unit_and_structure_biases(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "assistant_message": "마린 정찰과 보급고 보강 의도로 해석했습니다.",
                "modulation": {
                    "goal": "마린 5마리 정찰하고 보급고 계속 지어",
                    "override_level": "directive",
                    "production": {
                        "queue_biases": {"마린": 0.8, "보급고": 0.7, "탱크": 0.25},
                        "production_facility_biases": {"배럭": 0.5},
                    },
                    "tech": {"unit_biases": {"공성전차": 0.4}},
                    "tactical_task": {
                        "type": "continuous_production",
                        "id": "sustain-supply-and-marine",
                        "production_items": ["마린", "보급고", "탱크"],
                        "priority": 0.8,
                    },
                    "scope": {
                        "army_group": "scout",
                        "unit_classes": ["마린"],
                        "min_units": 5,
                        "max_units": 5,
                    },
                    "scouting": {"scout_priority": 0.8},
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual(
            {
                "TERRAN_MARINE": 0.8,
                "TERRAN_SUPPLYDEPOT": 0.7,
                "TERRAN_SIEGETANK": 0.25,
            },
            result.vector.production.queue_biases.to_dict(),
        )
        self.assertEqual(
            {"TERRAN_BARRACKS": 0.5},
            result.vector.production.production_facility_biases.to_dict(),
        )
        self.assertEqual(
            {"TERRAN_SIEGETANK": 0.4},
            result.vector.tech.unit_biases.to_dict(),
        )
        self.assertEqual("sustain_production", result.vector.tactical_task.task_type)
        self.assertEqual(
            ("TERRAN_MARINE", "TERRAN_SUPPLYDEPOT", "TERRAN_SIEGETANK"),
            result.vector.tactical_task.production_targets,
        )
        self.assertEqual(("TERRAN_MARINE",), result.vector.scope.unit_classes)

    def test_compiles_flat_tactical_task_aliases_for_bounded_tasks(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "intent": "탱크 체제로 전환하고 보급고 계속 지어",
                "task_type": "tank_transition",
                "task_id": "tank-transition-1",
                "production_targets": ["factory", "factorytechlab", "siegetank", "보급고"],
                "task_priority": 0.85,
                "task_duration_seconds": 300,
                "production": {
                    "queue_biases": {"factory": 0.7, "siegetank": 0.9, "보급고": 0.5},
                    "tech_switch_urgency": 0.7,
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("tech_transition", result.vector.tactical_task.task_type)
        self.assertEqual("tank-transition-1", result.vector.tactical_task.task_id)
        self.assertEqual(
            (
                "TERRAN_FACTORY",
                "FACTORY_TECHLAB",
                "TERRAN_SIEGETANK",
                "TERRAN_SUPPLYDEPOT",
            ),
            result.vector.tactical_task.production_targets,
        )
        self.assertEqual(0.85, result.vector.tactical_task.priority)

    def test_compiles_lifetime_aliases(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "intent": "마린으로 정찰해",
                "lifetime_mode": "until_completed",
                "completion_conditions": ["enemy_observed", "target_reached"],
                "completion_state": "active",
                "lifetime_reason": "scout lifecycle",
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("until_completed", result.vector.lifetime.mode)
        self.assertEqual(
            ("enemy_observed", "target_reached"),
            result.vector.lifetime.completion_conditions,
        )
        self.assertEqual("scout lifecycle", result.vector.lifetime.reason)

    def test_emergency_payload_without_ttl_gets_safe_default(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "후퇴해",
                "override_level": "emergency",
                "combat": {"aggression": -0.8},
                "emergency": {"force_retreat": True},
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("emergency", result.vector.override_level.value)
        self.assertEqual(60, result.vector.ttl_seconds)

    def test_emergency_override_without_flags_gets_safe_default_ttl(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "후퇴해",
                "override_level": "emergency",
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("emergency", result.vector.override_level.value)
        self.assertEqual(60, result.vector.ttl_seconds)

    def test_emergency_flags_upgrade_override_level(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "병력 살려",
                "combat": {"preserve_army_bias": 0.8},
                "emergency": {"force_retreat": True},
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual("emergency", result.vector.override_level.value)
        self.assertEqual(60, result.vector.ttl_seconds)

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
                "assistant_message": "입구 방어 의도로 해석해서 수비 성향을 높였습니다.",
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
        self.assertEqual(
            "입구 방어 의도로 해석해서 수비 성향을 높였습니다.",
            result.assistant_message,
        )
        self.assertEqual(
            "입구 방어 의도로 해석해서 수비 성향을 높였습니다.",
            result.to_dict()["assistant_message"],
        )
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
