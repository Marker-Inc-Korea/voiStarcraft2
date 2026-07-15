"""Tests for compiling bounded provider output into policy modulation."""

import unittest

from starcraft_commander.policy_modulation import (
    MICROMACHINE_TACTICAL_ABILITIES,
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
    def test_exact_composition_lowers_every_supported_terran_combat_unit_chain(
        self,
    ) -> None:
        prerequisite_chains = {
            "marine": ("TERRAN_BARRACKS", "TERRAN_MARINE"),
            "marauder": (
                "TERRAN_BARRACKS",
                "BARRACKS_TECHLAB",
                "TERRAN_MARAUDER",
            ),
            "reaper": ("TERRAN_BARRACKS", "TERRAN_REAPER"),
            "ghost": (
                "TERRAN_BARRACKS",
                "BARRACKS_TECHLAB",
                "TERRAN_GHOSTACADEMY",
                "TERRAN_GHOST",
            ),
            "hellion": ("TERRAN_FACTORY", "TERRAN_HELLION"),
            "widow_mine": ("TERRAN_FACTORY", "TERRAN_WIDOWMINE"),
            "cyclone": (
                "TERRAN_FACTORY",
                "FACTORY_TECHLAB",
                "TERRAN_CYCLONE",
            ),
            "thor": (
                "TERRAN_FACTORY",
                "FACTORY_TECHLAB",
                "TERRAN_ARMORY",
                "TERRAN_THOR",
            ),
            "tank": (
                "TERRAN_FACTORY",
                "FACTORY_TECHLAB",
                "TERRAN_SIEGETANK",
            ),
            "medivac": (
                "TERRAN_FACTORY",
                "TERRAN_STARPORT",
                "TERRAN_MEDIVAC",
            ),
            "viking": (
                "TERRAN_FACTORY",
                "TERRAN_STARPORT",
                "TERRAN_VIKINGFIGHTER",
            ),
            "liberator": (
                "TERRAN_FACTORY",
                "TERRAN_STARPORT",
                "TERRAN_LIBERATOR",
            ),
            "banshee": (
                "TERRAN_FACTORY",
                "TERRAN_STARPORT",
                "STARPORT_TECHLAB",
                "TERRAN_BANSHEE",
            ),
            "raven": (
                "TERRAN_FACTORY",
                "TERRAN_STARPORT",
                "STARPORT_TECHLAB",
                "TERRAN_RAVEN",
            ),
            "battlecruiser": (
                "TERRAN_FACTORY",
                "TERRAN_STARPORT",
                "STARPORT_TECHLAB",
                "TERRAN_FUSIONCORE",
                "TERRAN_BATTLECRUISER",
            ),
        }

        for unit_type, expected_chain in prerequisite_chains.items():
            with self.subTest(unit_type=unit_type):
                result = compile_policy_modulation_provider_output(
                    {
                        "source": "llm",
                        "goal": f"produce and field {unit_type}",
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "priority": 0.85,
                        },
                        "composition_requirements": [
                            {
                                "unit_type": unit_type,
                                "count": 1,
                                "role": "support",
                            }
                        ],
                    }
                )

                self.assertTrue(result.ok, result.to_dict())
                assert result.vector is not None
                vector = result.vector
                queue_biases = vector.production.queue_biases.to_dict()
                for target in expected_chain:
                    self.assertGreaterEqual(queue_biases[target], 0.85)
                    self.assertIn(
                        target,
                        vector.tactical_task.production_targets,
                    )
                self.assertTrue(vector.production.allow_build_order_rewrite)
                self.assertGreaterEqual(vector.economy.supply_buffer_bias, 0.55)

    def test_exact_composition_lowers_prerequisite_production_without_plan(
        self,
    ) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "밴시 한 기를 주력 공격에 합류시켜",
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "location_intent": "enemy_main",
                    "priority": 0.9,
                },
                "unit_roles": [
                    {
                        "unit_type": "banshee",
                        "role": "worker_harass",
                        "priority": 0.9,
                        "ability_policy": "if_available",
                    }
                ],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual(1, len(vector.composition_requirements))
        self.assertEqual(
            "TERRAN_BANSHEE",
            vector.composition_requirements[0].unit_type,
        )
        self.assertEqual(1, vector.composition_requirements[0].count)
        queue_biases = vector.production.queue_biases.to_dict()
        for target in (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
            "TERRAN_BANSHEE",
        ):
            with self.subTest(target=target):
                self.assertGreaterEqual(queue_biases[target], 0.9)
                self.assertIn(target, vector.tactical_task.production_targets)
        self.assertTrue(vector.production.allow_build_order_rewrite)
        self.assertGreaterEqual(vector.production.tech_switch_urgency, 0.9)

    def test_duplicate_composition_entries_merge_before_launch_floor(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 두 묶음을 합쳐 공격",
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "location_intent": "enemy_main",
                },
                "composition_requirements": [
                    {"unit_type": "marine", "count": 2, "role": "frontline"},
                    {"unit_type": "marine", "count": 2, "role": "focus_fire"},
                ],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual(1, len(vector.composition_requirements))
        self.assertEqual(4, vector.composition_requirements[0].count)
        self.assertEqual(4, vector.scope.min_units)
        self.assertEqual(4, vector.tactical_task.min_units)
        self.assertGreaterEqual(
            vector.production.queue_biases.to_dict()["TERRAN_MARINE"],
            0.8,
        )
        self.assertGreaterEqual(
            vector.production.queue_biases.to_dict()["TERRAN_BARRACKS"],
            0.8,
        )

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

    def test_preserves_primary_llm_failure_diagnostics_on_compiled_fallback(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "ui",
                "goal": "마린 4기로 공격",
                "strategy": {"posture": "pressure"},
                "combat": {"aggression": 0.7},
                "failure_kind": "contract_error",
                "llm_attempt_count": 2,
                "llm_repair_reason": "forced-tool output was missing tactical_task",
                "llm_transient_retry_reason": "provider connection timed out",
                "llm_duration_ms": 487,
                "primary_refusal_reason": (
                    "LLM policy modulation response had no forced-tool JSON."
                ),
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("contract_error", result.failure_kind)
        self.assertEqual(2, result.llm_attempt_count)
        self.assertEqual(
            "forced-tool output was missing tactical_task",
            result.llm_repair_reason,
        )
        self.assertEqual(
            "provider connection timed out",
            result.llm_transient_retry_reason,
        )
        self.assertEqual(487, result.llm_duration_ms)
        self.assertIn("no forced-tool JSON", result.primary_refusal_reason)
        self.assertEqual(
            "contract_error",
            result.to_dict()["failure_kind"],
        )

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

    def test_repairs_exact_llm_composition_to_hard_unit_floor(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 4기와 탱크 1기, 바이킹 1기로 공격",
                "scope": {
                    "army_group": "main",
                    "min_units": 1,
                    "max_units": 2,
                    "allow_partial_scope": True,
                },
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "min_units": 1,
                    "max_units": 2,
                    "allow_partial": True,
                },
                "composition_requirements": [
                    {"unit_type": "marine", "count": 4, "role": "frontline"},
                    {"unit_type": "tank", "count": 1, "role": "siege_support"},
                ],
                "unit_roles": [
                    {"unit_type": "viking", "role": "anti_air"},
                ],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual(6, vector.scope.min_units)
        self.assertEqual(6, vector.scope.max_units)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertEqual(6, vector.tactical_task.min_units)
        self.assertEqual(6, vector.tactical_task.max_units)
        self.assertFalse(vector.tactical_task.allow_partial)

    def test_continuous_exact_composition_is_a_launch_floor_not_a_hard_cap(
        self,
    ) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": (
                    "마린 6기, 탱크 2기, 바이킹 2기를 최소 편성으로 "
                    "계속 생산하고 반복 공격한다"
                ),
                "lifetime": {
                    "mode": "until_cancelled",
                    "completion_state": "active",
                },
                "production": {"production_continuity_bias": 0.9},
                "scope": {
                    "army_group": "main",
                    "min_units": 1,
                    "max_units": 10,
                },
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "min_units": 1,
                    "max_units": 10,
                },
                "composition_requirements": [
                    {"unit_type": "marine", "count": 6, "role": "frontline"},
                    {"unit_type": "tank", "count": 2, "role": "siege_support"},
                    {"unit_type": "viking", "count": 2, "role": "anti_air"},
                ],
                "tags": ["continuous_production", "standing_order"],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual(10, vector.scope.min_units)
        self.assertEqual(0, vector.scope.max_units)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertEqual(10, vector.tactical_task.min_units)
        self.assertEqual(0, vector.tactical_task.max_units)
        self.assertFalse(vector.tactical_task.allow_partial)

    def test_continuous_exact_composition_preserves_explicit_hard_cap(
        self,
    ) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 4기까지만 계속 생산해서 반복 공격한다",
                "lifetime": {
                    "mode": "until_cancelled",
                    "completion_state": "active",
                },
                "production": {"production_continuity_bias": 0.9},
                "scope": {"army_group": "main", "max_units": 4},
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "max_units": 4,
                },
                "composition_requirements": [
                    {"unit_type": "marine", "count": 4, "role": "frontline"}
                ],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        self.assertEqual(4, result.vector.scope.max_units)
        self.assertEqual(4, result.vector.tactical_task.max_units)

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
        self.assertEqual(1, vector.scope.max_units)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertEqual(1, vector.tactical_task.min_units)
        self.assertEqual(1, vector.tactical_task.max_units)
        self.assertFalse(vector.tactical_task.allow_partial)
        self.assertGreaterEqual(vector.scouting.scout_priority, 0.75)
        self.assertGreaterEqual(
            vector.squad.squad_role_biases.to_dict()["marine_scout"],
            0.75,
        )

    def test_marine_scout_composition_enforces_exact_requested_count(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 3기로 적 본진 정찰",
                "scope": {
                    "army_group": "scout",
                    "unit_classes": ["marine"],
                    "min_units": 1,
                    "max_units": 5,
                    "allow_partial_scope": True,
                },
                "tactical_task": {
                    "task_type": "scout_with_units",
                    "unit_classes": ["marine"],
                    "location_intent": "enemy_main",
                    "allow_partial": True,
                },
                "composition_requirements": [
                    {"unit_type": "marine", "count": 3, "role": "scout"}
                ],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        self.assertEqual(3, result.vector.scope.min_units)
        self.assertEqual(3, result.vector.scope.max_units)
        self.assertFalse(result.vector.scope.allow_partial_scope)
        self.assertEqual(3, result.vector.tactical_task.min_units)
        self.assertEqual(3, result.vector.tactical_task.max_units)
        self.assertFalse(result.vector.tactical_task.allow_partial)

    def test_execute_tactical_nuke_lowers_full_prerequisite_chain(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "적 본진에 전술 핵을 사용",
                "command_layer": "micro",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "tactical_nuke",
                    "location_intent": "enemy_main",
                    "priority": 0.95,
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual("micro", vector.command_layer.value)
        self.assertEqual("execute_ability", vector.tactical_task.task_type)
        self.assertEqual("tactical_nuke", vector.tactical_task.ability)
        self.assertEqual(("TERRAN_GHOST",), vector.tactical_task.unit_classes)
        self.assertEqual(0, vector.tactical_task.duration_seconds)
        roles = {role.unit_type: role for role in vector.unit_roles}
        self.assertEqual(
            {"TERRAN_MARINE", "TERRAN_MARAUDER", "TERRAN_GHOST"},
            set(roles),
        )
        self.assertEqual("scout", roles["TERRAN_MARINE"].role)
        self.assertEqual("defensive_hold", roles["TERRAN_MARAUDER"].role)
        self.assertEqual("execute_ability", roles["TERRAN_GHOST"].role)
        self.assertEqual(
            "tactical_nuke",
            roles["TERRAN_GHOST"].ability_policy,
        )
        for target in (
            "TERRAN_BARRACKS",
            "BARRACKS_TECHLAB",
            "TERRAN_GHOSTACADEMY",
            "TERRAN_GHOST",
            "TERRAN_FACTORY",
            "TERRAN_NUKE",
            "TERRAN_MARINE",
            "TERRAN_MARAUDER",
        ):
            with self.subTest(target=target):
                self.assertIn(target, vector.tactical_task.production_targets)
                self.assertGreaterEqual(
                    vector.production.queue_biases.to_dict()[target],
                    0.95,
                )
        self.assertGreaterEqual(vector.scouting.scout_priority, 0.8)
        self.assertGreaterEqual(vector.scouting.scout_cadence_bias, 0.65)
        self.assertTrue(vector.scouting.require_fresh_enemy_observation)
        self.assertEqual("scout", vector.scope.army_group)
        self.assertEqual(("TERRAN_MARINE",), vector.scope.unit_classes)
        self.assertEqual(4, vector.scope.min_units)
        self.assertEqual(4, vector.scope.max_units)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertFalse(vector.tactical_task.allow_partial)
        requirements = {
            (requirement.unit_type, requirement.role): requirement.count
            for requirement in vector.composition_requirements
        }
        self.assertEqual(4, requirements[("TERRAN_MARINE", "scout")])
        self.assertEqual(
            2,
            requirements[("TERRAN_MARAUDER", "defensive_hold")],
        )
        self.assertGreaterEqual(
            vector.squad.squad_role_biases.to_dict()["marine_scout"],
            0.8,
        )
        self.assertGreaterEqual(vector.economy.gas_priority, 0.95)
        self.assertGreaterEqual(vector.economy.gas_worker_target_bias, 0.75)
        self.assertIn("ability_cast", vector.lifetime.completion_conditions)

    def test_execute_abilities_lower_caster_and_upgrade_prerequisites(self) -> None:
        cases = (
            (
                "불곰 전투자극제를 사용해",
                "marauder_stimpack",
                "TERRAN_MARAUDER",
                {
                    "TERRAN_BARRACKS",
                    "BARRACKS_TECHLAB",
                    "TERRAN_MARAUDER",
                    "STIMPACK",
                },
            ),
            (
                "고스트를 은폐해",
                "ghost_cloak",
                "TERRAN_GHOST",
                {
                    "TERRAN_BARRACKS",
                    "BARRACKS_TECHLAB",
                    "TERRAN_GHOSTACADEMY",
                    "TERRAN_GHOST",
                    "GHOST_CLOAK",
                },
            ),
            (
                "공성 모드로 전환해",
                "siege_mode",
                "TERRAN_SIEGETANK",
                {
                    "TERRAN_FACTORY",
                    "FACTORY_TECHLAB",
                    "TERRAN_SIEGETANK",
                },
            ),
            (
                "의료선에 병력을 태워",
                "medivac_load",
                "TERRAN_MEDIVAC",
                {
                    "TERRAN_FACTORY",
                    "TERRAN_STARPORT",
                    "TERRAN_MEDIVAC",
                    "TERRAN_MARINE",
                },
            ),
            (
                "밴시 은폐해",
                "banshee_cloak",
                "TERRAN_BANSHEE",
                {
                    "TERRAN_FACTORY",
                    "TERRAN_STARPORT",
                    "STARPORT_TECHLAB",
                    "TERRAN_BANSHEE",
                    "BANSHEE_CLOAK",
                },
            ),
            (
                "야마토포 발사해",
                "yamato",
                "TERRAN_BATTLECRUISER",
                {
                    "TERRAN_FACTORY",
                    "TERRAN_STARPORT",
                    "STARPORT_TECHLAB",
                    "TERRAN_FUSIONCORE",
                    "TERRAN_BATTLECRUISER",
                    "YAMATO_CANNON",
                },
            ),
            (
                "밤까마귀 대장갑 미사일 사용해",
                "anti_armor_missile",
                "TERRAN_RAVEN",
                {
                    "TERRAN_FACTORY",
                    "TERRAN_STARPORT",
                    "STARPORT_TECHLAB",
                    "TERRAN_RAVEN",
                },
            ),
        )

        for goal, ability, caster, expected_targets in cases:
            with self.subTest(ability=ability):
                result = compile_policy_modulation_provider_output(
                    {
                        "source": "llm",
                        "goal": goal,
                        "tactical_task": {
                            "task_type": "execute_ability",
                            "ability": ability,
                        },
                    }
                )

                self.assertTrue(result.ok, result.to_dict())
                assert result.vector is not None
                vector = result.vector
                self.assertEqual("micro", vector.command_layer.value)
                self.assertEqual(ability, vector.tactical_task.ability)
                self.assertEqual((caster,), vector.tactical_task.unit_classes)
                self.assertTrue(
                    expected_targets.issubset(
                        set(vector.tactical_task.production_targets)
                    )
                )
                role = next(
                    item
                    for item in vector.unit_roles
                    if item.unit_type == caster
                )
                self.assertEqual("execute_ability", role.role)
                self.assertEqual(ability, role.ability_policy)
                self.assertEqual("until_completed", vector.lifetime.mode)
                self.assertIn(
                    "ability_cast",
                    vector.lifetime.completion_conditions,
                )

    def test_every_supported_explicit_ability_compiles_to_an_executable_contract(
        self,
    ) -> None:
        for ability in sorted(
            MICROMACHINE_TACTICAL_ABILITIES - {"", "tactical_nuke"}
        ):
            with self.subTest(ability=ability):
                result = compile_policy_modulation_provider_output(
                    {
                        "source": "llm",
                        "goal": f"execute {ability}",
                        "tactical_task": {
                            "task_type": "execute_ability",
                            "ability": ability,
                        },
                    }
                )

                self.assertTrue(result.ok, result.to_dict())
                assert result.vector is not None
                vector = result.vector
                self.assertEqual("micro", vector.command_layer.value)
                self.assertEqual(ability, vector.tactical_task.ability)
                self.assertTrue(vector.tactical_task.unit_classes)
                self.assertTrue(vector.tactical_task.production_targets)
                self.assertTrue(vector.production_plan.allow_prerequisite_buildings)
                self.assertEqual(
                    set(vector.tactical_task.unit_classes),
                    {
                        role.unit_type
                        for role in vector.unit_roles
                        if role.role == "execute_ability"
                        and role.ability_policy == ability
                    },
                )
                self.assertEqual("until_completed", vector.lifetime.mode)
                self.assertIn(
                    "ability_cast",
                    vector.lifetime.completion_conditions,
                )

    def test_execute_ability_aliases_are_canonicalized(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "탱크 공성 모드",
                "tactical_task": {
                    "task_type": "ability",
                    "ability": "공성 모드",
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        self.assertEqual("execute_ability", result.vector.tactical_task.task_type)
        self.assertEqual("siege_mode", result.vector.tactical_task.ability)

    def test_refuses_explicit_command_layer_that_conflicts_with_semantics(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "적 본진에 전술 핵을 사용",
                "command_layer": "macro",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "tactical_nuke",
                    "location_intent": "enemy_main",
                },
            }
        )

        self.assertFalse(result.ok, result.to_dict())
        self.assertIs(PolicyModulationCompileStatus.REFUSED, result.status)
        self.assertIsNone(result.vector)
        self.assertIn(
            "command_layer conflicts with semantic command content",
            result.refusal_reason,
        )

    def test_compiles_standing_production_with_home_defense_as_macro(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": (
                    "게임 내내 SCV와 보급고를 끊기지 않게 유지하고 "
                    "마린 8기와 공성전차 2기를 반복 생산하면서 본진 수비를 유지해"
                ),
                "command_layer": "macro",
                "ttl_seconds": 900,
                "production": {
                    "queue_biases": {
                        "TERRAN_SCV": 0.9,
                        "TERRAN_SUPPLYDEPOT": 0.9,
                        "TERRAN_MARINE": 0.9,
                        "TERRAN_SIEGETANK": 0.9,
                    },
                    "production_continuity_bias": 0.9,
                },
                "combat": {
                    "defend_bias": 0.8,
                    "preserve_army_bias": 0.7,
                },
                "scope": {"location_intent": "home"},
                "lifetime": {
                    "mode": "standing_order",
                    "completion_state": "active",
                },
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": [
                        "TERRAN_SCV",
                        "TERRAN_SUPPLYDEPOT",
                        "TERRAN_MARINE",
                        "TERRAN_SIEGETANK",
                    ],
                    "priority": 0.95,
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        self.assertEqual("macro", result.vector.command_layer.value)
        self.assertEqual(
            "sustain_production",
            result.vector.tactical_task.task_type,
        )
        self.assertEqual("home", result.vector.scope.location_intent)
        self.assertEqual("standing_order", result.vector.lifetime.mode)
        self.assertGreaterEqual(result.vector.combat.defend_bias, 0.8)

    def test_marine_centric_doctrine_repairs_to_persistent_macro(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 중심으로 가라",
                "command_layer": "operation",
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "unit_classes": ["marine"],
                    "location_intent": "enemy_natural",
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual("macro", vector.command_layer.value)
        self.assertEqual("sustain_production", vector.tactical_task.task_type)
        self.assertEqual(
            ("TERRAN_MARINE", "TERRAN_BARRACKS"),
            vector.tactical_task.production_targets,
        )
        self.assertEqual("standing_order", vector.lifetime.mode)
        self.assertEqual(0, vector.tactical_task.duration_seconds)
        self.assertGreaterEqual(
            vector.production.queue_biases.to_dict()["TERRAN_MARINE"],
            0.9,
        )

    def test_marine_centric_macro_preserves_explicit_secondary_composition(
        self,
    ) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 중심으로 계속 생산하고 탱크 2기도 준비해",
                "command_layer": "macro",
                "production_plan": {
                    "targets": ["SCV", "marine", "tank"],
                    "allow_prerequisite_buildings": True,
                    "priority": 0.95,
                },
                "composition_requirements": [
                    {
                        "unit_type": "tank",
                        "count": 2,
                        "role": "siege_support",
                    }
                ],
                "unit_roles": [
                    {
                        "unit_type": "tank",
                        "role": "siege_support",
                        "priority": 0.9,
                    }
                ],
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["SCV", "marine", "tank"],
                    "priority": 0.95,
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual("macro", vector.command_layer.value)
        self.assertEqual(
            (
                "TERRAN_MARINE",
                "TERRAN_SCV",
                "TERRAN_SIEGETANK",
            ),
            vector.production_plan.targets,
        )
        self.assertEqual(
            ("TERRAN_SIEGETANK",),
            tuple(
                requirement.unit_type
                for requirement in vector.composition_requirements
            ),
        )
        self.assertEqual(2, vector.composition_requirements[0].count)
        self.assertEqual("siege_support", vector.unit_roles[0].role)
        self.assertIn(
            "TERRAN_FACTORY",
            vector.production.queue_biases.to_dict(),
        )
        self.assertIn(
            "FACTORY_TECHLAB",
            vector.production.queue_biases.to_dict(),
        )
        self.assertIn(
            "TERRAN_SIEGETANK",
            vector.tactical_task.production_targets,
        )
        self.assertEqual("standing_order", vector.lifetime.mode)

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

    def test_repairs_lossless_llm_bounds_and_lifetime_aliases(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "탱크를 계속 생산해서 공격해",
                "ttl_seconds": 3600,
                "scope": {"duration_seconds": 1800},
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "duration_seconds": 1200,
                },
                "lifetime": {
                    "mode": "until_complete",
                    "completion_condition": "units_ready",
                    "completion_state": "in_progress",
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        self.assertEqual(900, result.vector.ttl_seconds)
        self.assertEqual(900, result.vector.scope.duration_seconds)
        self.assertEqual(900, result.vector.tactical_task.duration_seconds)
        self.assertEqual("until_completed", result.vector.lifetime.mode)
        self.assertEqual(
            ("unit_count_reached",),
            result.vector.lifetime.completion_conditions,
        )
        self.assertEqual("active", result.vector.lifetime.completion_state)

    def test_unknown_lifetime_condition_remains_a_validation_error(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "적이 완전히 망할 때까지 공격",
                "lifetime": {
                    "mode": "until_completed",
                    "completion_conditions": ["enemy_destroyed_forever"],
                },
            }
        )

        self.assertFalse(result.ok)
        self.assertIn("completion_conditions", result.refusal_reason)

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

    def test_compiles_rich_micromachine_intent_domains(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 4기랑 탱크 1기로 적진 공격하고 바이킹은 공중 우선",
                "production_plan": {
                    "targets": ["marine", "tank", "viking"],
                    "allow_prerequisites": True,
                    "priority": 0.8,
                },
                "composition_requirements": [
                    {"unit_type": "marine", "count": 4, "role": "frontline"},
                    {"unit_type": "tank", "count": 1, "role": "siege_support"},
                ],
                "unit_roles": [
                    {"unit_type": "viking", "role": "anti_air", "priority": 0.75},
                    {
                        "unit_type": "banshee",
                        "role": "worker_harass",
                        "priority": 0.65,
                        "ability_policy": "if_available",
                    },
                ],
                "building_tasks": [
                    {
                        "building_type": "bunker",
                        "placement_intent": "앞마당입구",
                        "anchor": "앞마당",
                        "offset_direction": "전방",
                    }
                ],
                "route_intent": {"type": "flank_left", "avoid_enemy_strength": True},
                "target_intent": {"type": "enemy_main", "priority": 0.9},
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        self.assertEqual(
            ("TERRAN_MARINE", "TERRAN_SIEGETANK", "TERRAN_VIKINGFIGHTER"),
            result.vector.production_plan.targets,
        )
        self.assertTrue(result.vector.production_plan.allow_prerequisite_buildings)
        self.assertEqual("TERRAN_MARINE", result.vector.composition_requirements[0].unit_type)
        self.assertEqual(4, result.vector.composition_requirements[0].count)
        self.assertEqual("siege_support", result.vector.composition_requirements[1].role)
        self.assertEqual("TERRAN_BANSHEE", result.vector.unit_roles[1].unit_type)
        self.assertEqual("worker_harass", result.vector.unit_roles[1].role)
        self.assertEqual("if_available", result.vector.unit_roles[1].ability_policy)
        self.assertEqual("TERRAN_BUNKER", result.vector.building_tasks[0].building_type)
        self.assertEqual("self_natural_choke", result.vector.building_tasks[0].placement_intent)
        self.assertEqual("self_natural", result.vector.building_tasks[0].anchor)
        self.assertEqual("toward_enemy", result.vector.building_tasks[0].offset_direction)
        self.assertGreaterEqual(
            result.vector.production.queue_biases.to_dict()["TERRAN_BUNKER"],
            0.85,
        )
        self.assertGreaterEqual(
            result.vector.tech.structure_biases.to_dict()["TERRAN_BUNKER"],
            0.85,
        )
        self.assertEqual("flank_left", result.vector.route_intent.route_type)
        self.assertEqual("enemy_main", result.vector.target_intent.target_type)

    def test_lowers_tank_production_plan_to_consumed_prerequisite_biases(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "탱크 생산해",
                "override_level": "directive",
                "production_plan": {
                    "targets": ["탱크"],
                    "allow_prerequisites": True,
                    "priority": 0.85,
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        vector = result.vector
        self.assertEqual(("TERRAN_SIEGETANK",), vector.production_plan.targets)
        self.assertEqual(
            (
                "TERRAN_FACTORY",
                "FACTORY_TECHLAB",
                "TERRAN_SIEGETANK",
            ),
            vector.tactical_task.production_targets,
        )
        self.assertGreaterEqual(
            vector.production.queue_biases.to_dict()["TERRAN_FACTORY"],
            0.85,
        )
        self.assertGreaterEqual(
            vector.production.addon_biases.to_dict()["FACTORY_TECHLAB"],
            0.85,
        )
        self.assertGreaterEqual(
            vector.tech.unit_biases.to_dict()["TERRAN_SIEGETANK"],
            0.85,
        )
        self.assertGreaterEqual(vector.economy.gas_priority, 0.85)
        self.assertGreaterEqual(vector.economy.gas_worker_target_bias, 0.75)
        self.assertGreaterEqual(vector.economy.supply_buffer_bias, 0.55)
        self.assertGreaterEqual(vector.production.tech_switch_urgency, 0.85)
        self.assertTrue(vector.production.allow_build_order_rewrite)
        self.assertIn("queued=TERRAN_FACTORY,FACTORY_TECHLAB,TERRAN_SIEGETANK", vector.rationale)

    def test_marine_production_plan_does_not_request_unrelated_factory_transition(
        self,
    ) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "마린 8기를 생산해",
                "override_level": "bias",
                "production_plan": {
                    "targets": ["TERRAN_MARINE"],
                    "allow_prerequisites": True,
                    "priority": 0.8,
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        vector = result.vector
        self.assertEqual(
            ("TERRAN_BARRACKS", "TERRAN_MARINE"),
            vector.tactical_task.production_targets,
        )
        self.assertGreaterEqual(
            vector.production.queue_biases.to_dict()["TERRAN_MARINE"],
            0.8,
        )
        self.assertEqual(0.0, vector.production.tech_switch_urgency)
        self.assertEqual(0.0, vector.economy.gas_priority)
        self.assertGreaterEqual(vector.economy.supply_buffer_bias, 0.55)
        self.assertNotIn(
            "TERRAN_FACTORY",
            vector.production.queue_biases.to_dict(),
        )

    def test_lowers_starport_production_plans_to_consumed_prerequisite_biases(self) -> None:
        for target, expected_chain in (
            (
                "바이킹",
                ("TERRAN_FACTORY", "TERRAN_STARPORT", "TERRAN_VIKINGFIGHTER"),
            ),
            (
                "밴시",
                (
                    "TERRAN_FACTORY",
                    "TERRAN_STARPORT",
                    "STARPORT_TECHLAB",
                    "TERRAN_BANSHEE",
                ),
            ),
            (
                "배틀크루저",
                (
                    "TERRAN_FACTORY",
                    "TERRAN_STARPORT",
                    "STARPORT_TECHLAB",
                    "TERRAN_FUSIONCORE",
                    "TERRAN_BATTLECRUISER",
                ),
            ),
        ):
            with self.subTest(target=target):
                result = compile_policy_modulation_provider_output(
                    {
                        "source": "llm",
                        "goal": f"{target} 생산해",
                        "override_level": "directive",
                        "production_plan": {
                            "targets": [target],
                            "allow_prerequisites": True,
                            "priority": 0.8,
                        },
                    }
                )

                self.assertTrue(result.ok, result.to_dict())
                self.assertIsNotNone(result.vector)
                assert result.vector is not None
                vector = result.vector
                self.assertEqual(expected_chain, vector.tactical_task.production_targets)
                self.assertEqual("sustain_production", vector.tactical_task.task_type)
                self.assertNotIn("FACTORY_TECHLAB", vector.tactical_task.production_targets)
                self.assertGreaterEqual(
                    vector.production.queue_biases.to_dict()["TERRAN_FACTORY"],
                    0.8,
                )
                self.assertGreaterEqual(
                    vector.production.queue_biases.to_dict()["TERRAN_STARPORT"],
                    0.8,
                )
                final_target = expected_chain[-1]
                self.assertGreaterEqual(
                    vector.tech.unit_biases.to_dict()[final_target],
                    0.8,
                )
                if "STARPORT_TECHLAB" in expected_chain:
                    self.assertGreaterEqual(
                        vector.production.addon_biases.to_dict()["STARPORT_TECHLAB"],
                        0.8,
                    )
                if "TERRAN_FUSIONCORE" in expected_chain:
                    self.assertGreaterEqual(
                        vector.tech.structure_biases.to_dict()["TERRAN_FUSIONCORE"],
                        0.8,
                    )

    def test_emergency_production_plan_does_not_queue_long_prerequisite_chain(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "급하면 탱크 생산해",
                "override_level": "emergency",
                "production_plan": {
                    "targets": ["탱크"],
                    "allow_prerequisites": True,
                    "priority": 0.95,
                },
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        assert result.vector is not None
        vector = result.vector
        self.assertFalse(vector.production_plan.allow_prerequisite_buildings)
        self.assertEqual(("TERRAN_SIEGETANK",), vector.tactical_task.production_targets)
        self.assertNotIn("TERRAN_FACTORY", vector.production.queue_biases.to_dict())
        self.assertFalse(vector.production.allow_build_order_rewrite)

    def test_rejects_unsafe_rich_intent_payloads(self) -> None:
        for payload in (
            {
                "goal": "bad role",
                "unit_roles": [{"unit_type": "marine", "role": "raw_action"}],
            },
            {
                "goal": "bad coordinate",
                "building_tasks": [
                    {
                        "building_type": "bunker",
                        "placement_intent": "front_door",
                        "target_position": [999, 12],
                    }
                ],
            },
            {
                "goal": "bad unit",
                "composition_requirements": [
                    {"unit_type": "UNSAFE_UNIT", "count": 1, "role": "frontline"}
                ],
            },
            {
                "goal": "bad building",
                "building_tasks": [
                    {"building_type": "DROP TABLE latest_modulation"}
                ],
            },
            {
                "goal": "too many targets",
                "production_plan": {
                    "targets": ["marine"] * 33,
                    "allow_prerequisites": True,
                },
            },
            {
                "goal": "unit as building",
                "building_tasks": [{"building_type": "marine"}],
            },
        ):
            with self.subTest(payload=payload):
                result = compile_policy_modulation_provider_output(payload)
                self.assertFalse(result.ok, result.to_dict())

    def test_normalizes_safe_unit_role_and_ability_policy_aliases(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "source": "llm",
                "goal": "우회 정찰 후 은폐",
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                },
                "composition_requirements": [
                    {"unit_type": "marine", "count": 2, "role": "flanker"},
                ],
                "unit_roles": [
                    {
                        "unit_type": "ghost",
                        "role": "caster",
                        "ability_policy": "when_available",
                    }
                ],
            }
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.vector is not None
        self.assertEqual("ambush", result.vector.composition_requirements[0].role)
        self.assertEqual("spellcaster", result.vector.unit_roles[0].role)
        self.assertEqual(
            "if_available",
            result.vector.unit_roles[0].ability_policy,
        )

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
