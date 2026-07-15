"""Tests for the issue #10 deep policy modulation DSL."""

import json
import unittest

from starcraft_commander.policy_modulation import (
    BuildingTask,
    CombatModulation,
    CommandLayer,
    CompositionRequirement,
    EconomyModulation,
    EmergencyModulation,
    LifetimeModulation,
    MICROMACHINE_TACTICAL_ABILITIES,
    MICROMACHINE_DOCTRINES,
    MICROMACHINE_TACTICAL_TASK_TYPES,
    PolicyModulationSource,
    PolicyModulationVector,
    PolicyOverrideLevel,
    PolicySafetyConstraint,
    ProductionModulation,
    ScoutingModulation,
    SquadModulation,
    StrategyModulation,
    TacticalScopeModulation,
    TacticalTaskModulation,
    TargetIntentModulation,
    TechModulation,
    UnitRoleAssignment,
    ProductionPlanModulation,
    RouteIntentModulation,
    WeightedBiases,
    WorkerModulation,
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


class LifetimeModulationTest(unittest.TestCase):
    def test_lifetime_domain_round_trips_completion_semantics(self) -> None:
        vector = PolicyModulationVector(
            goal="마린으로 정찰",
            lifetime=LifetimeModulation(
                mode="until_completed",
                completion_conditions=("enemy_observed", "target_reached"),
                completion_state="active",
                reason="combat scout expires independently",
            ),
        )

        payload = vector.to_dict()
        self.assertEqual("until_completed", payload["lifetime"]["mode"])
        self.assertEqual(
            ["enemy_observed", "target_reached"],
            payload["lifetime"]["completion_conditions"],
        )
        rebuilt = PolicyModulationVector.from_mapping(payload)
        self.assertEqual(vector.lifetime, rebuilt.lifetime)

    def test_lifetime_domain_rejects_unknown_completion_conditions(self) -> None:
        with self.assertRaisesRegex(ValueError, "completion_conditions"):
            LifetimeModulation(
                mode="until_completed",
                completion_conditions=("raw_sc2_action_done",),
            )


class PolicyModulationVectorTest(unittest.TestCase):
    def test_merges_duplicate_composition_and_role_entries_by_unit_type(self) -> None:
        vector = PolicyModulationVector(
            goal="마린 요구를 하나의 작전 조합으로 병합",
            composition_requirements=(
                CompositionRequirement("marine", count=2, role="frontline"),
                CompositionRequirement("marine", count=3, role="focus_fire"),
            ),
            unit_roles=(
                UnitRoleAssignment("marine", role="frontline", priority=0.4),
                UnitRoleAssignment(
                    "marine",
                    role="focus_fire",
                    priority=0.9,
                    ability_policy="if_available",
                ),
            ),
        )

        self.assertEqual(1, len(vector.composition_requirements))
        self.assertEqual(5, vector.composition_requirements[0].count)
        self.assertEqual("focus_fire", vector.composition_requirements[0].role)
        self.assertEqual(1, len(vector.unit_roles))
        self.assertEqual("focus_fire", vector.unit_roles[0].role)
        self.assertEqual("if_available", vector.unit_roles[0].ability_policy)

    def test_command_layer_is_inferred_from_semantic_task_contract(self) -> None:
        macro = PolicyModulationVector(goal="keep making marines")
        operation = PolicyModulationVector(
            goal="scout with one marine",
            tactical_task=TacticalTaskModulation(
                task_type="scout_with_units",
                unit_classes=("marine",),
            ),
        )
        micro = PolicyModulationVector(
            goal="launch a tactical nuke",
            tactical_task=TacticalTaskModulation(
                task_type="execute_ability",
                ability="tactical_nuke",
                unit_classes=("ghost",),
                production_targets=("nuke",),
                location_intent="enemy_main",
            ),
        )
        emergency = PolicyModulationVector(
            goal="retreat now",
            override_level="emergency",
            ttl_seconds=45,
        )

        self.assertIs(CommandLayer.MACRO, macro.command_layer)
        self.assertIs(CommandLayer.OPERATION, operation.command_layer)
        self.assertIs(CommandLayer.MICRO, micro.command_layer)
        self.assertIs(CommandLayer.EMERGENCY, emergency.command_layer)

    def test_contextual_route_modifier_is_inferred_as_operation(self) -> None:
        vector = PolicyModulationVector.from_mapping(
            {
                "goal": "flank harder with that force",
                "command_layer": "operation",
                "combat": {"aggression": 0.9},
                "route_intent": {
                    "route_type": "flank_right",
                    "avoid_enemy_strength": True,
                },
            }
        )

        self.assertIs(CommandLayer.OPERATION, vector.command_layer)
        self.assertEqual("flank_right", vector.route_intent.route_type)

    def test_standing_production_with_passive_defense_context_is_macro(self) -> None:
        vector = PolicyModulationVector.from_mapping(
            {
                "goal": (
                    "게임 내내 SCV와 보급고를 유지하고 마린 8기와 "
                    "공성전차 2기를 반복 생산하면서 본진을 수비해"
                ),
                "command_layer": "macro",
                "combat": {
                    "defend_bias": 0.8,
                    "rally_before_attack_bias": 0.6,
                },
                "scope": {"location_intent": "home"},
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": [
                        "TERRAN_SCV",
                        "TERRAN_SUPPLYDEPOT",
                        "TERRAN_MARINE",
                        "TERRAN_SIEGETANK",
                    ],
                },
            }
        )

        self.assertIs(CommandLayer.MACRO, vector.command_layer)
        self.assertEqual("sustain_production", vector.tactical_task.task_type)
        self.assertEqual("home", vector.scope.location_intent)
        self.assertEqual(0.8, vector.combat.defend_bias)

    def test_explicit_command_layer_must_match_semantic_task_contract(self) -> None:
        cases = (
            {
                "goal": "launch a tactical nuke",
                "command_layer": "macro",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "tactical_nuke",
                },
            },
            {
                "goal": "keep making tanks",
                "command_layer": "micro",
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_SIEGETANK"],
                },
            },
            {
                "goal": "pressure the enemy main",
                "command_layer": "macro",
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "location_intent": "enemy_main",
                },
            },
            {
                "goal": "retreat now",
                "command_layer": "operation",
                "override_level": "emergency",
                "ttl_seconds": 45,
            },
        )

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(
                    ValueError,
                    "command_layer conflicts with semantic command content",
                ):
                    PolicyModulationVector.from_mapping(payload)

    def test_execute_ability_round_trips_inside_tactical_task(self) -> None:
        vector = PolicyModulationVector(
            goal="적 본진에 전술 핵을 사용",
            tactical_task=TacticalTaskModulation(
                task_type="execute_ability",
                ability="tactical_nuke",
                unit_classes=("ghost",),
                production_targets=("nuke",),
                location_intent="enemy_main",
                priority=0.95,
            ),
            lifetime=LifetimeModulation(
                mode="until_completed",
                completion_conditions=("ability_cast",),
                completion_state="active",
            ),
        )

        payload = vector.to_dict()
        self.assertEqual("execute_ability", payload["tactical_task"]["task_type"])
        self.assertEqual("tactical_nuke", payload["tactical_task"]["ability"])
        self.assertEqual(
            ["TERRAN_NUKE"],
            payload["tactical_task"]["production_targets"],
        )
        self.assertNotIn("ability_cast", payload)
        self.assertEqual(vector, PolicyModulationVector.from_mapping(payload))

    def test_tactical_nuke_unit_role_contract_is_allow_listed(self) -> None:
        assignment = UnitRoleAssignment(
            "ghost",
            role="execute_ability",
            priority=0.95,
            ability_policy="tactical_nuke",
        )

        self.assertEqual("TERRAN_GHOST", assignment.unit_type)
        self.assertEqual("execute_ability", assignment.role)
        self.assertEqual("tactical_nuke", assignment.ability_policy)

    def test_all_explicit_ability_policies_are_allow_listed(self) -> None:
        for ability in MICROMACHINE_TACTICAL_ABILITIES - {""}:
            with self.subTest(ability=ability):
                assignment = UnitRoleAssignment(
                    "marine",
                    role="execute_ability",
                    priority=0.9,
                    ability_policy=ability,
                )
                self.assertEqual(ability, assignment.ability_policy)

    def test_rich_micromachine_intent_round_trips(self) -> None:
        vector = PolicyModulationVector(
            goal="마린 4기랑 탱크 1기로 적진 공격",
            production_plan=ProductionPlanModulation(
                targets=("marine", "tank"),
                allow_prerequisite_buildings=True,
                priority=0.8,
            ),
            composition_requirements=(
                CompositionRequirement("marine", count=4, role="frontline"),
                CompositionRequirement("tank", count=1, role="siege_support"),
            ),
            unit_roles=(
                UnitRoleAssignment(
                    "viking",
                    role="anti_air",
                    priority=0.7,
                    ability_policy="never",
                ),
            ),
            building_tasks=(
                BuildingTask(
                    "bunker",
                    placement_intent="self_natural_choke",
                    anchor="self_natural",
                    offset_direction="toward_enemy",
                    count=1,
                ),
            ),
            route_intent=RouteIntentModulation(
                route_type="flank_left",
                avoid_enemy_strength=True,
            ),
            target_intent=TargetIntentModulation(
                target_type="enemy_main",
                priority=0.9,
            ),
        )

        payload = vector.to_dict()
        self.assertEqual(
            ["TERRAN_MARINE", "TERRAN_SIEGETANK"],
            payload["production_plan"]["targets"],
        )
        self.assertEqual(
            {"unit_type": "TERRAN_MARINE", "count": 4, "role": "frontline"},
            payload["composition_requirements"][0],
        )
        self.assertEqual(
            {
                "unit_type": "TERRAN_VIKINGFIGHTER",
                "role": "anti_air",
                "priority": 0.7,
                "ability_policy": "never",
            },
            payload["unit_roles"][0],
        )
        self.assertEqual("TERRAN_BUNKER", payload["building_tasks"][0]["building_type"])
        self.assertEqual("self_natural_choke", payload["building_tasks"][0]["placement_intent"])
        self.assertEqual("self_natural", payload["building_tasks"][0]["anchor"])
        self.assertEqual("toward_enemy", payload["building_tasks"][0]["offset_direction"])
        self.assertTrue(payload["building_tasks"][0]["allow_nearest_valid_fallback"])
        rebuilt = PolicyModulationVector.from_mapping(payload)
        self.assertEqual(vector.production_plan, rebuilt.production_plan)
        self.assertEqual(vector.composition_requirements, rebuilt.composition_requirements)
        self.assertEqual(vector.unit_roles, rebuilt.unit_roles)
        self.assertEqual(vector.building_tasks, rebuilt.building_tasks)
        self.assertEqual(vector.route_intent, rebuilt.route_intent)
        self.assertEqual(vector.target_intent, rebuilt.target_intent)

    def test_rich_intent_rejects_unknown_roles_and_out_of_bounds_coordinates(self) -> None:
        with self.assertRaisesRegex(ValueError, "role"):
            UnitRoleAssignment("marine", role="raw_attack_move")
        with self.assertRaisesRegex(ValueError, "ability_policy"):
            UnitRoleAssignment("banshee", role="worker_harass", ability_policy="raw_cast")
        with self.assertRaisesRegex(ValueError, "target_position"):
            BuildingTask("bunker", placement_intent="front_door", target_position=(300, 12))
        with self.assertRaisesRegex(ValueError, "allowed MicroMachine"):
            CompositionRequirement("UNSAFE_UNIT", count=1)
        with self.assertRaisesRegex(ValueError, "allowed MicroMachine"):
            BuildingTask("DROP TABLE latest_modulation", placement_intent="front_door")
        with self.assertRaisesRegex(ValueError, "allowed MicroMachine"):
            BuildingTask("marine", placement_intent="front_door")
        with self.assertRaisesRegex(ValueError, "allowed MicroMachine"):
            UnitRoleAssignment("bunker", role="frontline")
        with self.assertRaisesRegex(ValueError, "more than 32"):
            ProductionPlanModulation(targets=tuple("marine" for _ in range(33)))
        with self.assertRaisesRegex(ValueError, "more than 32"):
            PolicyModulationVector(
                goal="too many",
                composition_requirements=tuple(
                    {"unit_type": "marine", "count": 1} for _ in range(33)
                ),
            )

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
                timing_biases=WeightedBiases({"tank_timing": 0.45}),
                transition_biases=WeightedBiases({"bio_tank": 0.5}),
                strategic_tags=("hold_ramp", "two_base"),
            ),
            economy=EconomyModulation(
                expand_bias=0.7,
                worker_production_bias=0.5,
                gas_worker_target_bias=0.4,
                mineral_saturation_bias=0.25,
                repair_priority=0.3,
                expansion_safety_bias=0.65,
                mule_priority=0.2,
            ),
            workers=WorkerModulation(repeat_order_guard_frames=32),
            tech=TechModulation(
                structure_biases=WeightedBiases({"Starport": 0.4}),
                unit_biases=WeightedBiases({"SiegeTank": 0.6, "Marine": 0.2}),
                upgrade_biases=WeightedBiases({"Stimpack": 0.3}),
                tech_path_tags=("bio_tank",),
            ),
            production=ProductionModulation(
                queue_biases=WeightedBiases({"Factory": 0.4}),
                composition_biases=WeightedBiases({"anti_air": 0.2}),
                addon_biases=WeightedBiases({"TechLab": 0.5}),
                production_facility_biases=WeightedBiases({"Barracks": 0.3}),
                max_tech_deviation=0.25,
                production_continuity_bias=0.6,
                tech_switch_urgency=0.2,
            ),
            combat=CombatModulation(
                aggression=-0.2,
                engage_threshold_delta=0.15,
                retreat_threshold_delta=0.2,
                attack_timing_bias=-0.35,
                commitment_level=0.25,
                pressure_window_frames=4200,
                attack_condition_override="earlier_if_safe",
                retreat_patience_bias=0.3,
                rally_before_attack_bias=0.55,
                defend_bias=0.8,
                combat_sim_confidence_margin=0.1,
                siege_position_bias=0.75,
                kite_bias=0.35,
                target_priority_biases=WeightedBiases({"Baneling": 0.8}),
            ),
            scouting=ScoutingModulation(
                scout_priority=0.6,
                risk_tolerance=-0.3,
                scout_cadence_bias=0.4,
                scan_priority=0.5,
                hidden_tech_scout_bias=0.7,
                target_biases=WeightedBiases({"enemy_natural": 0.8}),
                require_fresh_enemy_observation=True,
            ),
            squad=SquadModulation(
                main_army_bias=0.6,
                harassment_bias=-0.2,
                defense_bias=0.7,
                regroup_bias=0.5,
                split_army_bias=-0.15,
                flank_bias=0.2,
                reinforce_bias=0.35,
                contain_bias=0.25,
                proxy_pressure_bias=0.1,
            ),
            scope=TacticalScopeModulation(
                army_group="main",
                unit_classes=("marine", "siege_tank"),
                location_intent="enemy_natural",
                duration_seconds=180,
                min_units=6,
                max_units=18,
                require_safety_margin=0.35,
            ),
            tactical_task=TacticalTaskModulation(
                task_type="pressure_with_main_army",
                task_id="qa-pressure-001",
                unit_classes=("marine", "siege_tank"),
                production_targets=("siege_tank", "supply_depot"),
                location_intent="enemy_natural",
                priority=0.7,
                min_units=6,
                max_units=18,
                duration_seconds=180,
                allow_partial=True,
                safety_margin=0.35,
            ),
            emergency=EmergencyModulation(
                cancel_attacks=True,
                prioritize_repair=True,
                stop_expansion=True,
            ),
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
        self.assertEqual("", document["strategy"]["doctrine"])
        self.assertEqual(0.45, document["strategy"]["timing_biases"]["tank_timing"])
        self.assertEqual(0.4, document["economy"]["gas_worker_target_bias"])
        self.assertEqual(32, document["workers"]["repeat_order_guard_frames"])
        self.assertEqual(0.5, document["production"]["addon_biases"]["TechLab"])
        self.assertEqual(0.15, document["combat"]["engage_threshold_delta"])
        self.assertEqual(4200, document["combat"]["pressure_window_frames"])
        self.assertEqual("earlier_if_safe", document["combat"]["attack_condition_override"])
        self.assertEqual(0.75, document["combat"]["siege_position_bias"])
        self.assertEqual(0.5, document["scouting"]["scan_priority"])
        self.assertEqual(0.2, document["squad"]["flank_bias"])
        self.assertEqual(0.35, document["squad"]["reinforce_bias"])
        self.assertEqual("main", document["scope"]["army_group"])
        self.assertEqual(["marine", "siege_tank"], document["scope"]["unit_classes"])
        self.assertEqual("enemy_natural", document["scope"]["location_intent"])
        self.assertEqual("pressure_with_main_army", document["tactical_task"]["task_type"])
        self.assertEqual("qa-pressure-001", document["tactical_task"]["task_id"])
        self.assertEqual(
            ["TERRAN_MARINE", "TERRAN_SIEGETANK"],
            document["tactical_task"]["unit_classes"],
        )
        self.assertEqual(
            ["TERRAN_SIEGETANK", "TERRAN_SUPPLYDEPOT"],
            document["tactical_task"]["production_targets"],
        )
        self.assertTrue(document["emergency"]["cancel_attacks"])
        self.assertTrue(document["emergency"]["prioritize_repair"])
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
                    "commitment_level": 0.55,
                    "attack_condition_override": "force_when_threshold_met",
                    "target_priority_biases": {"Baneling": 0.5},
                },
                "squad": {
                    "squad_role_biases": {"harass": 0.5, "main_army": 0.2},
                    "contain_bias": 0.4,
                },
                "workers": {"repeat_order_guard_frames": 48},
                "scope": {
                    "army_group": "harass",
                    "unit_classes": ["reaper"],
                    "location_intent": "enemy_main",
                    "min_units": 1,
                },
                "tactical_task": {
                    "task_type": "scout_with_units",
                    "task_id": "representation-scout-1",
                    "unit_classes": ["reaper"],
                    "location_intent": "enemy_main",
                    "priority": 0.6,
                    "min_units": 1,
                    "max_units": 2,
                },
                "constraints": [{"key": "require_scouting_before_attack"}],
                "tags": ["representation_modulation"],
            }
        )

        self.assertEqual(PolicyModulationSource.NEURAL_REPRESENTATION, vector.source)
        self.assertEqual(PolicyOverrideLevel.BIAS, vector.override_level)
        self.assertEqual({"proxy_cyclone": 0.5}, vector.strategy.preferred_builds.to_dict())
        self.assertEqual(0.4, vector.combat.aggression)
        self.assertEqual(0.55, vector.combat.commitment_level)
        self.assertEqual("force_when_threshold_met", vector.combat.attack_condition_override)
        self.assertEqual({"Baneling": 0.5}, vector.combat.target_priority_biases.to_dict())
        self.assertEqual(0.4, vector.squad.contain_bias)
        self.assertEqual(48, vector.workers.repeat_order_guard_frames)
        self.assertEqual("harass", vector.scope.army_group)
        self.assertEqual(("reaper",), vector.scope.unit_classes)
        self.assertEqual("scout_with_units", vector.tactical_task.task_type)
        self.assertEqual("representation-scout-1", vector.tactical_task.task_id)
        self.assertEqual(("TERRAN_REAPER",), vector.tactical_task.unit_classes)
        self.assertEqual("require_scouting_before_attack", vector.constraints[0].key)

    def test_tactical_scope_accepts_issue_third_location_wording(self) -> None:
        scope = TacticalScopeModulation(location_intent="third")

        self.assertEqual("third", scope.location_intent)

    def test_tactical_unit_classes_have_order_independent_set_semantics(self) -> None:
        scope = TacticalScopeModulation(
            unit_classes=("siege_tank", "marine", "marine")
        )
        task = TacticalTaskModulation(
            task_type="pressure_with_main_army",
            unit_classes=("siege_tank", "marine", "marine"),
        )

        self.assertEqual(
            ("marine", "siege_tank"),
            scope.unit_classes,
        )
        self.assertEqual(
            ("TERRAN_MARINE", "TERRAN_SIEGETANK"),
            task.unit_classes,
        )

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
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "rawCommand": "attack"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys(
                {"goal": "unsafe", "nested": {"directSC2Command": "move"}}
            )
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "attackMove": "enemy"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "S2ClientAPI": "attack"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "BotAIMethod": "do"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "S2.Client.API": "attack"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "Bot/AI/Method": "do"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "attack.move": "enemy"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "keyDown": "a"})
        with self.assertRaisesRegex(ValueError, "raw runtime control"):
            reject_raw_policy_control_keys({"goal": "unsafe", "keyboardShortcut": "control+a"})

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
        with self.assertRaisesRegex(ValueError, "repeat_order_guard_frames"):
            WorkerModulation(repeat_order_guard_frames=3)
        with self.assertRaisesRegex(ValueError, "repeat_order_guard_frames"):
            WorkerModulation(repeat_order_guard_frames=97)

    def test_rejects_unknown_enums_and_invalid_booleans(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported policy override"):
            PolicyModulationVector(goal="bad", override_level="takeover")
        with self.assertRaisesRegex(ValueError, "doctrine"):
            StrategyModulation(doctrine="raw_attack_move")
        with self.assertRaisesRegex(TypeError, "allow_build_order_rewrite"):
            ProductionModulation(allow_build_order_rewrite="yes")

    def test_micromachine_doctrine_labels_are_bounded_and_serialized(self) -> None:
        self.assertIn("mech_transition", MICROMACHINE_DOCTRINES)
        self.assertIn("scout_with_units", MICROMACHINE_TACTICAL_TASK_TYPES)

        vector = PolicyModulationVector(
            goal="transition_to_mech",
            strategy=StrategyModulation(
                posture="balanced",
                doctrine="mech_transition",
            ),
        )

        self.assertEqual("mech_transition", vector.strategy.doctrine)
        self.assertEqual(
            "mech_transition",
            vector.to_dict()["strategy"]["doctrine"],
        )

    def test_tactical_task_is_bounded_and_rejects_raw_like_identifiers(self) -> None:
        task = TacticalTaskModulation(
            task_type="sustain_production",
            task_id="supply-buffer-001",
            production_targets=("TERRAN_SUPPLYDEPOT", "TERRAN_SCV"),
            priority=0.9,
            duration_seconds=300,
        )

        self.assertEqual("sustain_production", task.task_type)
        self.assertEqual(("TERRAN_SUPPLYDEPOT", "TERRAN_SCV"), task.production_targets)

        with self.assertRaisesRegex(ValueError, "task_type"):
            TacticalTaskModulation(task_type="raw_attack_move")
        with self.assertRaisesRegex(ValueError, "task_id"):
            TacticalTaskModulation(task_type="scout_with_units", task_id="bad id")
        with self.assertRaisesRegex(ValueError, "task_type is required"):
            TacticalTaskModulation(production_targets=("supply_depot",))
        with self.assertRaisesRegex(ValueError, "max_units"):
            TacticalTaskModulation(
                task_type="scout_with_units",
                min_units=3,
                max_units=2,
            )


if __name__ == "__main__":
    unittest.main()
