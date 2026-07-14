"""Tests for live text-to-MicroMachine modulation sessions."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_GAME_LOOPS_PER_SECOND,
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
)
from starcraft_commander.micromachine_live_session import (
    KeywordPolicyModulationProvider,
    LiveModulationConsumptionStatus,
    LiveModulationStatus,
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
    main,
)
from starcraft_commander.micromachine_runtime import (
    LATEST_TELEMETRY_JSON_NAME,
    LATEST_UPDATE_JSON_NAME,
    LATEST_UPDATE_KV_NAME,
    MicroMachineFilesystemBlackboard,
    MicroMachineInMemoryBlackboard,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileStatus,
)


class AutoConsumingBlackboard(MicroMachineInMemoryBlackboard):
    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        accepted = super().publish_update(update, current_frame=current_frame)
        self.ingest_telemetry(
            MicroMachineTelemetry(
                frame=accepted.issued_at_frame + 1,
                active_modulation_ids=(accepted.update_id,),
            )
        )
        return accepted


class FailingPublishBlackboard(MicroMachineInMemoryBlackboard):
    def publish_vector(self, *args, **kwargs):
        raise OSError("blackboard directory unavailable")


class EventuallyReadableTelemetryBlackboard(MicroMachineInMemoryBlackboard):
    def __init__(self) -> None:
        super().__init__()
        self.read_count = 0

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        self.read_count += 1
        if self.read_count == 1:
            return None
        return MicroMachineTelemetry(frame=321)


class MicroMachineLiveTextSessionTest(unittest.TestCase):
    def test_keyword_provider_does_not_publish_plain_greeting(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text("안녕", current_frame=7, update_id="hello-noop")

        self.assertFalse(result.ok, result.to_dict())
        self.assertEqual(LiveModulationStatus.CLARIFICATION_REQUIRED, result.status)
        self.assertIsNone(result.update)
        self.assertIsNone(backend.latest_update)
        self.assertEqual(
            LiveModulationConsumptionStatus.NOT_PUBLISHED,
            result.consumption_status,
        )
        self.assertEqual(
            PolicyModulationCompileStatus.CLARIFICATION_REQUIRED,
            result.compile_result.status,
        )
        self.assertEqual("smoke_keyword", result.compile_result.source.value)
        self.assertEqual("smoke_keyword", result.to_dict()["provider_source"])
        self.assertIn("전술 의도", result.compile_result.clarification_prompt)

    def test_keyword_provider_maps_attack_intent_to_offensive_gate_biases(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "공격적으로 마린 탐색해서 적발견시 바로 공격해",
            current_frame=100,
            update_id="attack-now",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual("smoke_keyword", result.update.vector.source.value)
        self.assertEqual("smoke_keyword", result.to_dict()["provider_source"])
        vector = result.update.vector
        self.assertEqual("force_when_threshold_met", vector.combat.attack_condition_override)
        self.assertGreaterEqual(vector.combat.attack_timing_bias, 0.6)
        self.assertGreaterEqual(vector.combat.commitment_level, 0.5)
        self.assertGreaterEqual(vector.combat.retreat_patience_bias, 0.4)
        self.assertGreaterEqual(vector.squad.main_army_bias, 0.5)
        self.assertGreaterEqual(vector.squad.contain_bias, 0.3)
        self.assertEqual("main", vector.scope.army_group)
        self.assertEqual("enemy_natural", vector.scope.location_intent)
        self.assertEqual(1, vector.scope.min_units)
        self.assertGreaterEqual(vector.ttl_seconds, 600)
        self.assertEqual(
            "pressure_with_main_army",
            vector.tactical_task.task_type,
        )
        self.assertEqual("enemy_natural", vector.tactical_task.location_intent)
        self.assertEqual(1, vector.tactical_task.min_units)
        self.assertIn("TERRAN_MARINE", vector.tactical_task.unit_classes)
        self.assertGreaterEqual(vector.production.production_continuity_bias, 0.6)
        self.assertEqual(32, vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertIn("tactical_task", result.update.manager_bias_domains)
        self.assertIn("worker_line", vector.combat.target_priority_biases.to_dict())
        self.assertIn("aggressive_pressure", vector.tags)

    def test_keyword_provider_maps_enemy_base_attack_to_enemy_main(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "마린으로 적진 공격해",
            current_frame=100,
            update_id="attack-enemy-main",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("main", vector.scope.army_group)
        self.assertEqual("enemy_main", vector.scope.location_intent)
        self.assertEqual("pressure_with_main_army", vector.tactical_task.task_type)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertIn("TERRAN_MARINE", vector.tactical_task.unit_classes)
        self.assertTrue(vector.scouting.require_fresh_enemy_observation)
        self.assertIn("search_before_attack", vector.tags)

    def test_keyword_provider_allows_explicit_blind_enemy_main_attack(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "정찰 없이 마린으로 적진을 바로 공격해",
            current_frame=100,
            update_id="blind-attack-enemy-main",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertFalse(vector.scouting.require_fresh_enemy_observation)
        self.assertIn("explicit_blind_attack", vector.tags)

    def test_keyword_provider_maps_four_marine_attack_to_unit_scope(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "4마린으로 적진 공격해",
            current_frame=100,
            update_id="attack-four-marines",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("main", vector.scope.army_group)
        self.assertEqual("enemy_main", vector.scope.location_intent)
        self.assertEqual(4, vector.scope.min_units)
        self.assertEqual(4, vector.scope.max_units)
        self.assertEqual("pressure_with_main_army", vector.tactical_task.task_type)
        self.assertEqual(4, vector.tactical_task.min_units)
        self.assertEqual(4, vector.tactical_task.max_units)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertFalse(vector.tactical_task.allow_partial)
        self.assertEqual(0.0, vector.scouting.scout_priority)
        self.assertIn("explicit_unit_count", vector.tags)

    def test_keyword_provider_treats_continuous_composition_as_uncapped_standing_order(
        self,
    ) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "마린 6기, 탱크 2기, 바이킹 2기를 최소 편성으로 계속 생산하고 반복 공격해",
            current_frame=100,
            update_id="continuous-composition",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual(10, vector.scope.min_units)
        self.assertEqual(0, vector.scope.max_units)
        self.assertEqual(10, vector.tactical_task.min_units)
        self.assertEqual(0, vector.tactical_task.max_units)
        self.assertEqual("until_cancelled", vector.lifetime.mode)
        self.assertIn("continuous_production", vector.tags)
        self.assertIn("standing_order", vector.tags)

    def test_keyword_provider_maps_marine_tank_attack_to_composition_requirements(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "4마린 1탱크로 적진 공격해",
            current_frame=100,
            update_id="attack-marine-tank",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual(5, vector.scope.min_units)
        self.assertEqual(5, vector.tactical_task.min_units)
        self.assertEqual("pressure_with_main_army", vector.tactical_task.task_type)
        self.assertEqual("enemy_main", vector.target_intent.target_type)
        self.assertEqual("TERRAN_MARINE", vector.composition_requirements[0].unit_type)
        self.assertEqual(4, vector.composition_requirements[0].count)
        self.assertEqual("TERRAN_SIEGETANK", vector.composition_requirements[1].unit_type)
        self.assertEqual(1, vector.composition_requirements[1].count)
        self.assertEqual("siege_support", vector.composition_requirements[1].role)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertFalse(vector.tactical_task.allow_partial)
        self.assertEqual(0.0, vector.scouting.scout_priority)
        self.assertIn("explicit_composition", vector.tags)

    def test_keyword_provider_keeps_scout_priority_for_actual_combat_scout(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "마린 2기로 적 본진 정찰해",
            current_frame=100,
            update_id="scout-two-marines",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("scout", vector.scope.army_group)
        self.assertEqual("scout_with_units", vector.tactical_task.task_type)
        self.assertGreaterEqual(vector.scouting.scout_priority, 0.7)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertFalse(vector.tactical_task.allow_partial)

    def test_keyword_provider_defaults_named_viking_scout_to_one_exact_unit(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "바이킹으로 적 본진 정찰해",
            current_frame=100,
            update_id="scout-one-viking",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("scout_with_units", vector.tactical_task.task_type)
        self.assertEqual(("TERRAN_VIKINGFIGHTER",), vector.tactical_task.unit_classes)
        self.assertEqual(1, vector.scope.min_units)
        self.assertEqual(1, vector.scope.max_units)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertFalse(vector.tactical_task.allow_partial)
        self.assertEqual(1, len(vector.composition_requirements))
        self.assertEqual(
            "TERRAN_VIKINGFIGHTER",
            vector.composition_requirements[0].unit_type,
        )
        self.assertEqual(1, vector.composition_requirements[0].count)
        self.assertIn("TERRAN_FACTORY", vector.tactical_task.production_targets)
        self.assertIn("TERRAN_STARPORT", vector.tactical_task.production_targets)

    def test_keyword_provider_preserves_tactics_in_produce_then_attack_command(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            (
                "필요한 건물을 먼저 지어서 공성전차 1기와 바이킹 1기를 생산해. "
                "공성전차는 공성 모드로 적진을 압박하고 바이킹은 함께 공격해."
            ),
            current_frame=100,
            update_id="produce-then-attack-tank-viking",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("pressure_with_main_army", vector.tactical_task.task_type)
        self.assertEqual("main", vector.scope.army_group)
        self.assertEqual(2, vector.scope.min_units)
        self.assertEqual(2, vector.scope.max_units)
        self.assertIn("TERRAN_FACTORY", vector.production_plan.targets)
        self.assertIn("FACTORY_TECHLAB", vector.production_plan.targets)
        self.assertIn("TERRAN_STARPORT", vector.production_plan.targets)
        self.assertEqual(600, vector.ttl_seconds)
        self.assertEqual(0, vector.tactical_task.duration_seconds)
        roles = {item.unit_type: item.role for item in vector.unit_roles}
        self.assertEqual("siege_support", roles["TERRAN_SIEGETANK"])
        self.assertEqual("anti_air", roles["TERRAN_VIKINGFIGHTER"])
        self.assertIn("command_category:tactical", vector.tags)

    def test_keyword_provider_maps_non_marine_units_to_scope_roles_and_production_plan(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "화염차 2기 바이킹 1기 밴시 1기 배틀크루저 1기로 다른 길 공격해",
            current_frame=100,
            update_id="attack-mixed-tech",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        unit_types = tuple(item.unit_type for item in vector.composition_requirements)
        self.assertEqual(
            (
                "TERRAN_HELLION",
                "TERRAN_VIKINGFIGHTER",
                "TERRAN_BANSHEE",
                "TERRAN_BATTLECRUISER",
            ),
            unit_types,
        )
        self.assertEqual(set(unit_types), set(vector.scope.unit_classes))
        self.assertEqual(set(unit_types), set(vector.tactical_task.unit_classes))
        self.assertTrue(set(unit_types) <= set(vector.tactical_task.production_targets))
        self.assertIn("TERRAN_FACTORY", vector.tactical_task.production_targets)
        self.assertIn("TERRAN_STARPORT", vector.tactical_task.production_targets)
        self.assertIn("STARPORT_TECHLAB", vector.tactical_task.production_targets)
        self.assertIn("TERRAN_FUSIONCORE", vector.tactical_task.production_targets)
        self.assertTrue(set(unit_types) <= set(vector.production_plan.targets))
        self.assertIn("TERRAN_FACTORY", vector.production_plan.targets)
        self.assertIn("TERRAN_STARPORT", vector.production_plan.targets)
        self.assertIn("STARPORT_TECHLAB", vector.production_plan.targets)
        self.assertIn("TERRAN_FUSIONCORE", vector.production_plan.targets)
        self.assertTrue(vector.production_plan.allow_prerequisite_buildings)
        roles = {item.unit_type: item.role for item in vector.unit_roles}
        self.assertEqual("worker_harass", roles["TERRAN_HELLION"])
        self.assertEqual("anti_air", roles["TERRAN_VIKINGFIGHTER"])
        self.assertEqual("worker_harass", roles["TERRAN_BANSHEE"])
        self.assertEqual("capital_ship", roles["TERRAN_BATTLECRUISER"])
        self.assertEqual("flank_left", vector.route_intent.route_type)
        self.assertGreaterEqual(vector.economy.gas_priority, 0.8)
        self.assertGreaterEqual(vector.economy.gas_worker_target_bias, 0.75)
        self.assertGreaterEqual(vector.economy.supply_buffer_bias, 0.55)
        self.assertIn("explicit_composition", vector.tags)

    def test_keyword_provider_accepts_ghost_widow_mine_liberator_attack(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "유령 1명, 땅거미지뢰 1기, 해방선 1기로 공격해",
            current_frame=100,
            update_id="attack-ghost-mine-liberator",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual(
            ("TERRAN_GHOST", "TERRAN_WIDOWMINE", "TERRAN_LIBERATOR"),
            tuple(item.unit_type for item in vector.composition_requirements),
        )
        self.assertIn("TERRAN_GHOSTACADEMY", vector.production_plan.targets)
        self.assertIn("TERRAN_FACTORY", vector.production_plan.targets)
        self.assertIn("TERRAN_STARPORT", vector.production_plan.targets)

    def test_keyword_provider_accepts_standing_ghost_production(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "유령 계속 생산해",
            current_frame=100,
            update_id="standing-ghost-production",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("until_cancelled", vector.lifetime.mode)
        self.assertIn("TERRAN_GHOSTACADEMY", vector.production_plan.targets)
        self.assertIn("TERRAN_GHOST", vector.production_plan.targets)

    def test_keyword_provider_accepts_liberator_scout(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "해방선으로 정찰해",
            current_frame=100,
            update_id="scout-one-liberator",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("scout_with_units", vector.tactical_task.task_type)
        self.assertEqual(("TERRAN_LIBERATOR",), vector.tactical_task.unit_classes)
        self.assertEqual(1, vector.scope.min_units)
        self.assertEqual(1, vector.scope.max_units)

    def test_keyword_provider_maps_flank_route_attack_to_flank_bias(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "마린 4기로 다른 길로 우회해서 적진 공격해",
            current_frame=100,
            update_id="attack-flank-route",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("enemy_main", vector.scope.location_intent)
        self.assertEqual(4, vector.scope.min_units)
        self.assertEqual(4, vector.scope.max_units)
        self.assertGreaterEqual(vector.squad.flank_bias, 0.7)
        self.assertLessEqual(vector.squad.contain_bias, 0.15)
        self.assertGreaterEqual(vector.combat.flank_bias, 0.6)
        self.assertEqual("flank_left", vector.route_intent.route_type)
        self.assertIn("flank_route", vector.tags)

    def test_keyword_provider_preserves_explicit_right_flank_route(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "마린 4기로 오른쪽 우회해서 적진 공격해",
            current_frame=100,
            update_id="attack-right-flank-route",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("flank_right", vector.route_intent.route_type)
        self.assertEqual(4, vector.scope.min_units)
        self.assertEqual(4, vector.scope.max_units)
        self.assertFalse(vector.scope.allow_partial_scope)
        self.assertFalse(vector.tactical_task.allow_partial)

    def test_keyword_provider_maps_focus_fire_and_kite_to_consumable_tactics(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            (
                "마린 4기를 모은 뒤 왼쪽으로 우회해서 적 본진을 공격해. "
                "적을 만나면 집중사격하고 위험하면 kite해."
            ),
            current_frame=100,
            update_id="marine-focus-kite",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("flank_left", vector.route_intent.route_type)
        self.assertGreaterEqual(vector.combat.kite_bias, 0.75)
        self.assertIn("focus_fire", vector.tags)
        self.assertIn("kite", vector.tags)
        self.assertEqual(1, len(vector.unit_roles))
        self.assertEqual("TERRAN_MARINE", vector.unit_roles[0].unit_type)
        self.assertEqual("focus_fire", vector.unit_roles[0].role)
        self.assertEqual("focus_fire", vector.composition_requirements[0].role)

    def test_keyword_provider_keeps_conditional_retreat_inside_mixed_attack(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            (
                "마린 4기, 공성전차 1대, 바이킹 1기를 생산해서 좌측 우회로 "
                "적 본진을 공격해. 탱크는 접촉 직전에 공성 모드, 바이킹은 "
                "대공 우선, 마린은 집중사격하고 카이팅해. 위험하면 후퇴 후 "
                "재집결해서 다시 공격해."
            ),
            current_frame=100,
            update_id="mixed-conditional-retreat",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("tactical", result.command_queue["category"])
        self.assertNotEqual("overwrite_emergency", result.command_queue["action"])
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("bias", vector.override_level.value)
        self.assertEqual("pressure_with_main_army", vector.tactical_task.task_type)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertEqual("flank_left", vector.route_intent.route_type)
        self.assertEqual(6, vector.scope.min_units)
        self.assertEqual(6, vector.scope.max_units)
        self.assertEqual(
            ("TERRAN_MARINE", "TERRAN_SIEGETANK", "TERRAN_VIKINGFIGHTER"),
            tuple(item.unit_type for item in vector.composition_requirements),
        )
        self.assertEqual(
            (4, 1, 1),
            tuple(item.count for item in vector.composition_requirements),
        )
        self.assertGreaterEqual(vector.combat.preserve_army_bias, 0.6)
        self.assertGreaterEqual(vector.squad.regroup_bias, 0.7)
        self.assertIn("conditional_retreat_regroup", vector.tags)
        self.assertIn("focus_fire", vector.tags)
        self.assertIn("kite", vector.tags)

    def test_keyword_provider_preserves_proactive_supply_inside_compound_attack(
        self,
    ) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            (
                "마린 4기, 공성전차 1대, 바이킹 1기를 생산해서 적 본진을 공격해. "
                "보급이 부족해지기 전에 보급고를 지어서 생산을 계속해."
            ),
            current_frame=100,
            update_id="compound-attack-with-supply",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertIn("TERRAN_SUPPLYDEPOT", vector.tactical_task.production_targets)
        self.assertGreaterEqual(
            vector.production.queue_biases.to_dict()["TERRAN_SUPPLYDEPOT"],
            0.8,
        )
        self.assertGreaterEqual(vector.economy.supply_buffer_bias, 0.8)
        self.assertIn("proactive_supply", vector.tags)

    def test_keyword_provider_maps_korean_kiting_without_focus_role(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "마린 3기로 적진을 치고 빠져",
            current_frame=100,
            update_id="marine-kite-only",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertGreaterEqual(vector.combat.kite_bias, 0.75)
        self.assertEqual("kite", vector.unit_roles[0].role)
        self.assertEqual("kite", vector.composition_requirements[0].role)

    def test_keyword_provider_prioritizes_defense_over_enemy_rush_words(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "defend enemy rush",
            current_frame=100,
            update_id="defend-enemy-rush",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertLess(vector.combat.aggression, 0.0)
        self.assertGreaterEqual(vector.combat.defend_bias, 0.6)
        self.assertGreaterEqual(vector.combat.preserve_army_bias, 0.3)
        self.assertGreaterEqual(vector.squad.defense_bias, 0.4)
        self.assertEqual("", vector.tactical_task.task_type)

    def test_keyword_provider_prioritizes_korean_defense_over_rush_words(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "적 러쉬 오니까 수비해",
            current_frame=100,
            update_id="defend-korean-rush",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertLess(vector.combat.aggression, 0.0)
        self.assertGreaterEqual(vector.combat.defend_bias, 0.6)
        self.assertGreaterEqual(vector.squad.defense_bias, 0.4)
        self.assertNotEqual("pressure_with_main_army", vector.tactical_task.task_type)

    def test_keyword_provider_maps_marine_scout_to_scout_task(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "마린으로 적 본진 정찰해",
            current_frame=100,
            update_id="marine-scout",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("scout", vector.scope.army_group)
        self.assertEqual("enemy_main", vector.scope.location_intent)
        self.assertEqual("scout_with_units", vector.tactical_task.task_type)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertEqual(1, vector.tactical_task.min_units)
        self.assertEqual(1, vector.tactical_task.max_units)
        self.assertIn("TERRAN_MARINE", vector.tactical_task.unit_classes)
        self.assertEqual(1, len(vector.composition_requirements))
        self.assertEqual("TERRAN_MARINE", vector.composition_requirements[0].unit_type)
        self.assertEqual(1, vector.composition_requirements[0].count)
        self.assertEqual(180, vector.ttl_seconds)
        self.assertEqual("until_completed", vector.lifetime.mode)
        self.assertEqual(
            ("enemy_observed", "target_reached"),
            vector.lifetime.completion_conditions,
        )
        self.assertEqual(0, vector.tactical_task.duration_seconds)
        self.assertEqual(180, result.command_queue["ttl_seconds"])
        self.assertEqual("until_completed", result.command_queue["lifetime_mode"])

    def test_explicit_tactical_task_drops_stale_defensive_tactical_standing_order(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        defensive_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "방어하면서 마린 생산은 유지",
                    "strategy": {"posture": "defensive", "doctrine": "marine_rush"},
                    "production": {"queue_biases": {"TERRAN_MARINE": 0.8}},
                    "combat": {"defend_bias": 0.75, "aggression": -0.25},
                    "squad": {"defense_bias": 0.8, "main_army_bias": -0.4},
                    "tags": ["defensive_hold"],
                    "rationale": "Hold the army near home.",
                }
            ),
        )
        self.assertTrue(defensive_session.submit_text("방어 유지", current_frame=100).ok)

        pressure_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
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
                    "tags": ["aggressive_pressure"],
                    "rationale": "Attack with the main army.",
                }
            ),
        )

        result = pressure_session.submit_text("마린 러쉬 진행해", current_frame=200)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("marine_rush", vector.strategy.doctrine)
        self.assertEqual(0.8, vector.production.queue_biases.to_dict()["TERRAN_MARINE"])
        self.assertEqual("enemy_natural", vector.scope.location_intent)
        self.assertEqual("enemy_natural", vector.tactical_task.location_intent)
        self.assertEqual("force_when_threshold_met", vector.combat.attack_condition_override)
        self.assertLess(vector.combat.defend_bias, 0.45)
        self.assertEqual(0.0, vector.squad.defense_bias)
        self.assertGreaterEqual(vector.squad.main_army_bias, 0.6)
        self.assertEqual("마린 러쉬 진행해", vector.goal)
        self.assertIn("aggressive_pressure", vector.tags)
        self.assertNotIn("defensive_hold", vector.tags)
        self.assertEqual("Attack with the main army.", vector.rationale)

    def test_explicit_tactical_task_drops_defensive_marker_from_contaminated_standing_order(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        contaminated_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린 러쉬 진행해 | standing: micromachine_defensive_hold",
                    "strategy": {"posture": "pressure", "doctrine": "marine_rush"},
                    "production": {"queue_biases": {"TERRAN_MARINE": 0.8}},
                    "combat": {"defend_bias": -0.25, "aggression": 0.7},
                    "tags": ["aggressive_pressure", "defensive_hold"],
                    "rationale": "Hold the army near home.",
                }
            ),
        )
        self.assertTrue(contaminated_session.submit_text("이전 오염 상태", current_frame=100).ok)

        pressure_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린으로 적진 공격해",
                    "scope": {
                        "army_group": "main",
                        "unit_classes": ["marine"],
                        "location_intent": "enemy_main",
                    },
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "unit_classes": ["marine"],
                        "location_intent": "enemy_main",
                    },
                    "tags": ["aggressive_pressure"],
                    "rationale": "Attack the enemy main.",
                }
            ),
        )

        result = pressure_session.submit_text("마린으로 적진 공격해", current_frame=200)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("마린으로 적진 공격해", vector.goal)
        self.assertEqual("enemy_main", vector.scope.location_intent)
        self.assertNotIn("defensive_hold", vector.tags)
        self.assertNotIn("micromachine_defensive_hold", vector.goal)
        self.assertEqual("Attack the enemy main.", vector.rationale)

    def test_live_provider_cannot_weaken_worker_repeat_order_guard(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "공격적으로 압박해",
                    "combat": {"aggression": 0.5},
                    "workers": {"repeat_order_guard_frames": 4},
                }
            ),
        )

        result = session.submit_text("공격적으로 압박해", current_frame=100)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual(32, result.update.vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertIn(
            "live_worker_repeat_order_guard_frames_clamped=4->32",
            result.compile_result.warnings,
        )

    def test_live_provider_can_strengthen_worker_repeat_order_guard(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "수비적으로 버텨",
                    "combat": {"defend_bias": 0.5},
                    "workers": {"repeat_order_guard_frames": 48},
                }
            ),
        )

        result = session.submit_text("수비적으로 버텨", current_frame=100)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual(48, result.update.vector.workers.repeat_order_guard_frames)

    def test_live_commands_preserve_standing_production_when_scout_task_arrives(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        production_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린 러쉬와 보급고, SCV 생산을 계속 유지한다.",
                    "strategy": {"posture": "pressure", "doctrine": "marine_rush"},
                    "economy": {
                        "worker_production_bias": 0.75,
                        "supply_buffer_bias": 0.75,
                        "expand_bias": 0.55,
                    },
                    "production": {
                        "queue_biases": {
                            "TERRAN_SUPPLYDEPOT": 0.8,
                            "TERRAN_MARINE": 0.8,
                            "TERRAN_COMMANDCENTER": 0.55,
                        },
                        "production_continuity_bias": 0.75,
                    },
                    "tactical_task": {
                        "task_type": "sustain_production",
                        "production_targets": [
                            "TERRAN_SCV",
                            "TERRAN_SUPPLYDEPOT",
                            "TERRAN_MARINE",
                            "TERRAN_COMMANDCENTER",
                        ],
                        "priority": 0.8,
                        "duration_seconds": 600,
                    },
                    "tags": ["live_text"],
                }
            ),
        )
        first = production_session.submit_text("보급고와 마린, SCV 계속", current_frame=100)
        self.assertTrue(first.ok, first.to_dict())

        scout_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린 3기로 적 위치를 정찰한다.",
                    "strategy": {
                        "posture": "balanced",
                        "doctrine": "scouting_map_control",
                    },
                    "scouting": {
                        "scout_priority": 0.9,
                        "risk_tolerance": 0.25,
                    },
                    "scope": {
                        "army_group": "scout",
                        "unit_classes": ["marine"],
                        "location_intent": "enemy_main",
                        "min_units": 3,
                        "max_units": 3,
                    },
                    "tactical_task": {
                        "task_type": "scout_with_units",
                        "unit_classes": ["TERRAN_MARINE"],
                        "location_intent": "enemy_main",
                        "min_units": 3,
                        "max_units": 3,
                        "priority": 0.85,
                        "duration_seconds": 180,
                    },
                    "tags": ["live_text", "scout"],
                }
            ),
        )
        second = scout_session.submit_text("마린 3마리로 정찰해", current_frame=160)

        self.assertTrue(second.ok, second.to_dict())
        self.assertIsNotNone(second.update)
        assert second.update is not None
        vector = second.update.vector
        self.assertEqual("scout_with_units", vector.tactical_task.task_type)
        self.assertEqual("marine_rush", vector.strategy.doctrine)
        queue_biases = vector.production.queue_biases.to_dict()
        self.assertGreaterEqual(queue_biases["TERRAN_SUPPLYDEPOT"], 0.75)
        self.assertGreaterEqual(queue_biases["TERRAN_MARINE"], 0.75)
        self.assertGreaterEqual(queue_biases["TERRAN_COMMANDCENTER"], 0.5)
        self.assertGreaterEqual(vector.economy.worker_production_bias, 0.7)
        self.assertGreaterEqual(vector.economy.supply_buffer_bias, 0.7)
        self.assertIn(
            "live_standing_orders_merged",
            second.compile_result.warnings,
        )
        self.assertEqual("scouting", second.command_queue["category"])
        self.assertEqual("merge_standing_orders", second.command_queue["action"])
        self.assertEqual([first.update.update_id], second.command_queue["parent_command_ids"])
        self.assertTrue(second.command_queue["standing_order_preserved"])
        self.assertEqual("until_completed", second.command_queue["lifetime_mode"])
        self.assertEqual(180, second.command_queue["ttl_seconds"])
        self.assertEqual("until_cancelled", second.command_queue["update_lifetime_mode"])
        self.assertEqual(900, second.command_queue["update_ttl_seconds"])
        self.assertEqual("until_cancelled", vector.lifetime.mode)
        self.assertEqual(900, vector.ttl_seconds)
        self.assertEqual(0, vector.tactical_task.duration_seconds)
        self.assertIn("live_command_reducer_applied", second.compile_result.warnings)
        self.assertIn("command_category:scouting", vector.tags)
        self.assertIn("command_action:merge_standing_orders", vector.tags)

    def test_production_standing_order_preserves_active_tactical_operation(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        tactical_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린과 탱크로 좌측 우회 공격",
                    "combat": {"aggression": 0.8},
                    "scouting": {
                        "scout_priority": 0.8,
                        "require_fresh_enemy_observation": True,
                    },
                    "scope": {
                        "army_group": "main",
                        "unit_classes": ["marine", "tank"],
                        "location_intent": "enemy_main",
                        "min_units": 5,
                    },
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "task_id": "active-left-flank",
                        "unit_classes": ["marine", "tank"],
                        "production_targets": ["marine", "tank"],
                        "location_intent": "enemy_main",
                        "min_units": 5,
                        "priority": 0.9,
                    },
                    "composition_requirements": [
                        {"unit_type": "marine", "count": 4, "role": "focus_fire"},
                        {"unit_type": "tank", "count": 1, "role": "siege_support"},
                    ],
                    "unit_roles": [
                        {
                            "unit_type": "tank",
                            "role": "siege_support",
                            "ability_policy": "if_available",
                        }
                    ],
                    "route_intent": {
                        "type": "flank_left",
                        "avoid_enemy_strength": True,
                    },
                    "target_intent": {"type": "enemy_main", "priority": 0.9},
                }
            ),
        )
        first = tactical_session.submit_text(
            "마린과 탱크로 좌측 우회 공격해",
            current_frame=100,
            update_id="active-left-flank",
        )
        self.assertTrue(first.ok, first.to_dict())

        production_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "탱크와 바이킹을 계속 보충",
                    "production": {
                        "production_continuity_bias": 0.9,
                        "queue_biases": {
                            "TERRAN_SIEGETANK": 0.9,
                            "TERRAN_VIKINGFIGHTER": 0.9,
                        },
                    },
                    "scouting": {
                        "require_fresh_enemy_observation": False,
                    },
                    "tactical_task": {
                        "task_type": "sustain_production",
                        "task_id": "standing-mixed-production",
                        "production_targets": ["tank", "viking"],
                        "priority": 0.9,
                    },
                    "production_plan": {
                        "targets": ["tank", "viking"],
                        "allow_prerequisite_buildings": True,
                        "priority": 0.9,
                    },
                }
            ),
        )
        second = production_session.submit_text(
            "탱크와 바이킹을 계속 보충해",
            current_frame=160,
            update_id="standing-mixed-production",
        )

        self.assertTrue(second.ok, second.to_dict())
        self.assertEqual("production", second.command_queue["category"])
        self.assertEqual("merge_standing_orders", second.command_queue["action"])
        self.assertTrue(second.command_queue["standing_order_preserved"])
        assert second.update is not None
        vector = second.update.vector
        self.assertEqual("pressure_with_main_army", vector.tactical_task.task_type)
        self.assertEqual("active-left-flank", vector.tactical_task.task_id)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertEqual("flank_left", vector.route_intent.route_type)
        self.assertEqual("enemy_main", vector.target_intent.target_type)
        self.assertTrue(vector.scouting.require_fresh_enemy_observation)
        self.assertEqual("siege_support", vector.unit_roles[0].role)
        self.assertEqual(4, vector.composition_requirements[0].count)
        self.assertIn(
            "TERRAN_VIKINGFIGHTER",
            vector.tactical_task.production_targets,
        )
        self.assertGreaterEqual(
            vector.production.queue_biases.to_dict()["TERRAN_VIKINGFIGHTER"],
            0.9,
        )
        self.assertEqual("until_cancelled", vector.lifetime.mode)
        self.assertEqual(
            1,
            vector.goal.count("standing: 마린과 탱크로 좌측 우회 공격"),
        )

    def test_standing_merge_does_not_repurpose_optional_correlation_task_id(
        self,
    ) -> None:
        backend = MicroMachineInMemoryBlackboard()
        tactical_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린과 탱크로 적진을 계속 압박",
                    "combat": {"aggression": 0.8},
                    "scope": {
                        "army_group": "main",
                        "location_intent": "enemy_main",
                        "min_units": 5,
                    },
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "unit_classes": ["marine", "tank"],
                        "location_intent": "enemy_main",
                        "min_units": 5,
                        "priority": 0.9,
                    },
                    "composition_requirements": [
                        {"unit_type": "marine", "count": 4, "role": "frontline"},
                        {"unit_type": "tank", "count": 1, "role": "siege_support"},
                    ],
                }
            ),
        )
        first = tactical_session.submit_text(
            "마린과 탱크로 적진을 계속 압박해",
            current_frame=100,
            update_id="publication-a",
        )

        self.assertTrue(first.ok, first.to_dict())
        assert first.update is not None
        self.assertEqual("", first.update.vector.tactical_task.task_id)

        production_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "탱크와 바이킹을 계속 보충",
                    "production": {
                        "queue_biases": {
                            "TERRAN_SIEGETANK": 0.9,
                            "TERRAN_VIKINGFIGHTER": 0.9,
                        }
                    },
                    "tactical_task": {
                        "task_type": "sustain_production",
                        "production_targets": ["tank", "viking"],
                        "priority": 0.9,
                    },
                }
            ),
        )
        second = production_session.submit_text(
            "탱크와 바이킹을 계속 보충해",
            current_frame=160,
            update_id="publication-b",
        )

        self.assertTrue(second.ok, second.to_dict())
        self.assertEqual("merge_standing_orders", second.command_queue["action"])
        assert second.update is not None
        self.assertEqual(
            "pressure_with_main_army",
            second.update.vector.tactical_task.task_type,
        )
        self.assertEqual("", second.update.vector.tactical_task.task_id)

    def test_emergency_command_overwrites_active_tactical_command(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        tactical_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린으로 적진 공격",
                    "combat": {"aggression": 0.7},
                    "scope": {"army_group": "main", "location_intent": "enemy_main"},
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "location_intent": "enemy_main",
                    },
                }
            ),
        )
        first = tactical_session.submit_text(
            "마린으로 적진 공격해",
            current_frame=100,
            update_id="attack-before-retreat",
        )
        self.assertTrue(first.ok, first.to_dict())

        emergency_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "후퇴해서 병력 살려",
                    "override_level": "emergency",
                    "ttl_seconds": 45,
                    "combat": {"aggression": -0.8, "preserve_army_bias": 0.9},
                    "squad": {"regroup_bias": 0.9, "defense_bias": 0.8},
                    "emergency": {"force_retreat": True},
                }
            ),
        )

        result = emergency_session.submit_text(
            "아니 후퇴해",
            current_frame=140,
            update_id="retreat-now",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("emergency", result.command_queue["category"])
        self.assertEqual("overwrite_emergency", result.command_queue["action"])
        self.assertEqual(["attack-before-retreat"], result.command_queue["parent_command_ids"])
        self.assertTrue(result.command_queue["superseded_previous"])
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("후퇴해서 병력 살려", vector.goal)
        self.assertEqual("emergency", vector.override_level.value)
        self.assertTrue(vector.emergency.force_retreat)
        self.assertIn("command_action:overwrite_emergency", vector.tags)

    def test_emergency_without_override_still_overwrites_without_stale_tags(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        first_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린 러쉬",
                    "strategy": {"doctrine": "marine_rush"},
                    "combat": {"aggression": 0.7},
                    "tactical_task": {"task_type": "pressure_with_main_army"},
                    "tags": ["aggressive_pressure"],
                }
            ),
        )
        self.assertTrue(
            first_session.submit_text(
                "마린 러쉬",
                current_frame=100,
                update_id="old-rush",
            ).ok
        )
        cancel_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "공격 취소",
                    "combat": {"aggression": -0.7, "preserve_army_bias": 0.8},
                    "squad": {"regroup_bias": 0.8},
                    "emergency": {"force_retreat": True},
                    "tags": ["cancel_attack"],
                }
            ),
        )

        result = cancel_session.submit_text(
            "공격 취소해",
            current_frame=150,
            update_id="cancel-rush",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("emergency", result.command_queue["category"])
        self.assertEqual("overwrite_emergency", result.command_queue["action"])
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("emergency", vector.override_level.value)
        self.assertEqual(45, vector.ttl_seconds)
        self.assertEqual("emergency_window", vector.lifetime.mode)
        self.assertEqual("", vector.strategy.doctrine)
        self.assertEqual("공격 취소", vector.goal)
        self.assertIn("cancel_attack", vector.tags)
        self.assertIn("command_category:emergency", vector.tags)
        self.assertNotIn("command_category:tactical", vector.tags)
        self.assertNotIn("aggressive_pressure", vector.tags)

    def test_keyword_provider_maps_cancel_attack_to_emergency_not_attack(self) -> None:
        for command in ("공격 취소해", "cancel attack", "중지"):
            with self.subTest(command=command):
                backend = MicroMachineInMemoryBlackboard()
                session = MicroMachineLiveTextSession(
                    backend,
                    KeywordPolicyModulationProvider(),
                )

                result = session.submit_text(
                    command,
                    current_frame=100,
                    update_id=f"cancel-{len(command)}",
                )

                self.assertTrue(result.ok, result.to_dict())
                self.assertEqual("emergency", result.command_queue["category"])
                self.assertEqual("overwrite_emergency", result.command_queue["action"])
                assert result.update is not None
                vector = result.update.vector
                self.assertTrue(vector.emergency.cancel_attacks)
                self.assertTrue(vector.emergency.force_retreat)
                self.assertLess(vector.combat.aggression, 0.0)
                self.assertNotEqual(
                    "pressure_with_main_army",
                    vector.tactical_task.task_type,
                )
                self.assertIn("cancel_attack", vector.tags)

    def test_keyword_provider_does_not_invert_negated_cancel_into_emergency(self) -> None:
        for command in (
            "공격을 취소하지 말고 계속 공격해",
            "공격 중지하지 마. 마린으로 압박해",
            "do not cancel the attack; keep attacking",
        ):
            with self.subTest(command=command):
                backend = MicroMachineInMemoryBlackboard()
                session = MicroMachineLiveTextSession(
                    backend,
                    KeywordPolicyModulationProvider(),
                )

                result = session.submit_text(
                    command,
                    current_frame=100,
                    update_id=f"negated-cancel-{len(command)}",
                )

                self.assertTrue(result.ok, result.to_dict())
                self.assertEqual("tactical", result.command_queue["category"])
                assert result.update is not None
                vector = result.update.vector
                self.assertFalse(vector.emergency.cancel_attacks)
                self.assertFalse(vector.emergency.force_retreat)
                self.assertEqual(
                    "pressure_with_main_army",
                    vector.tactical_task.task_type,
                )
                self.assertNotIn("cancel_attack", vector.tags)

    def test_keyword_provider_cancel_attack_uses_short_emergency_lifetime(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "공격 취소해",
            current_frame=100,
            update_id="cancel-short-window",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual(45, vector.ttl_seconds)
        self.assertEqual("emergency_window", vector.lifetime.mode)
        self.assertEqual(
            ("cancelled_by_user", "retreat_confirmed", "ttl_expired"),
            vector.lifetime.completion_conditions,
        )
        self.assertEqual(
            100 + 45 * MICROMACHINE_GAME_LOOPS_PER_SECOND,
            result.update.expires_at_frame,
        )

    def test_production_command_gets_until_cancelled_lifetime(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린 계속 뽑아",
                    "tactical_task": {
                        "task_type": "sustain_production",
                        "production_targets": ["TERRAN_MARINE"],
                        "priority": 0.8,
                    },
                }
            ),
        )

        result = session.submit_text(
            "마린 계속 뽑아",
            current_frame=10,
            update_id="marine-standing-production",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual(900, vector.ttl_seconds)
        self.assertEqual("until_cancelled", vector.lifetime.mode)
        self.assertEqual(
            ("unit_count_reached", "cancelled_by_user"),
            vector.lifetime.completion_conditions,
        )
        self.assertEqual("production", result.command_queue["category"])
        self.assertEqual(900, result.command_queue["ttl_seconds"])

    def test_keyword_provider_maps_standing_non_marine_production_to_prerequisite_biases(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "배틀크루저 계속 생산해",
            current_frame=10,
            update_id="standing-bc-production",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual(900, vector.ttl_seconds)
        self.assertEqual("until_cancelled", vector.lifetime.mode)
        self.assertEqual("sustain_production", vector.tactical_task.task_type)
        self.assertIn("TERRAN_BATTLECRUISER", vector.production_plan.targets)
        self.assertIn("TERRAN_STARPORT", vector.production_plan.targets)
        self.assertIn("STARPORT_TECHLAB", vector.production_plan.targets)
        self.assertIn("TERRAN_FUSIONCORE", vector.production_plan.targets)
        queue_biases = vector.production.queue_biases.to_dict()
        self.assertGreaterEqual(queue_biases["TERRAN_STARPORT"], 0.8)
        self.assertGreaterEqual(queue_biases["STARPORT_TECHLAB"], 0.8)
        self.assertGreaterEqual(queue_biases["TERRAN_FUSIONCORE"], 0.8)
        self.assertGreaterEqual(queue_biases["TERRAN_BATTLECRUISER"], 0.8)
        self.assertEqual("production", result.command_queue["category"])
        self.assertEqual("until_cancelled", result.command_queue["lifetime_mode"])

    def test_standing_scout_uses_until_cancelled_lifetime_without_infinite_ttl(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "바이킹으로 계속 정찰 유지해",
            current_frame=10,
            update_id="standing-viking-scout",
        )

        self.assertTrue(result.ok, result.to_dict())
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual(900, vector.ttl_seconds)
        self.assertEqual("until_cancelled", vector.lifetime.mode)
        self.assertIn("cancelled_by_user", vector.lifetime.completion_conditions)
        self.assertEqual(0, vector.tactical_task.duration_seconds)
        self.assertEqual(0, vector.scope.duration_seconds)
        self.assertEqual(900, result.command_queue["ttl_seconds"])
        self.assertEqual("until_cancelled", result.command_queue["lifetime_mode"])

    def test_new_tactical_command_supersedes_stale_tactical_command(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        first_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린으로 적 앞마당 압박",
                    "scope": {"army_group": "main", "location_intent": "enemy_natural"},
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "location_intent": "enemy_natural",
                    },
                }
            ),
        )
        self.assertTrue(
            first_session.submit_text(
                "앞마당 압박해",
                current_frame=100,
                update_id="pressure-natural",
            ).ok
        )
        second_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린 4기로 적 본진 우회 공격",
                    "scope": {
                        "army_group": "main",
                        "location_intent": "enemy_main",
                        "min_units": 4,
                        "max_units": 4,
                    },
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "location_intent": "enemy_main",
                        "min_units": 4,
                        "max_units": 4,
                    },
                    "squad": {"flank_bias": 0.8},
                }
            ),
        )

        result = second_session.submit_text(
            "마린 4기로 다른 길로 적 본진 공격해",
            current_frame=160,
            update_id="pressure-main-flank",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("tactical", result.command_queue["category"])
        self.assertEqual("supersede_tactical", result.command_queue["action"])
        self.assertTrue(result.command_queue["superseded_previous"])
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("마린 4기로 적 본진 우회 공격", vector.goal)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertEqual(4, vector.tactical_task.min_units)
        self.assertIn("command_action:supersede_tactical", vector.tags)

    def test_new_tactical_command_supersedes_prior_tactical_doctrine(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        first_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "앞마당 contain",
                    "strategy": {"doctrine": "contain_enemy_natural"},
                    "combat": {"aggression": 0.55},
                    "scope": {"location_intent": "enemy_natural"},
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "location_intent": "enemy_natural",
                    },
                    "tags": ["old_contain"],
                }
            ),
        )
        self.assertTrue(
            first_session.submit_text(
                "앞마당 contain",
                current_frame=100,
                update_id="old-contain",
            ).ok
        )
        second_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "적 본진 공격",
                    "combat": {"aggression": 0.75},
                    "scope": {"location_intent": "enemy_main"},
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "location_intent": "enemy_main",
                    },
                    "tags": ["new_main_attack"],
                }
            ),
        )

        result = second_session.submit_text(
            "이제 적 본진 공격해",
            current_frame=140,
            update_id="new-main-attack",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("supersede_tactical", result.command_queue["action"])
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("", vector.strategy.doctrine)
        self.assertEqual("enemy_main", vector.tactical_task.location_intent)
        self.assertIn("new_main_attack", vector.tags)
        self.assertNotIn("old_contain", vector.tags)
        self.assertNotIn("command_action:merge_standing_orders", vector.tags)

    def test_live_stop_expansion_command_drops_prior_command_center_bias(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        expand_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "사령부 확장과 SCV 생산을 유지한다.",
                    "strategy": {"posture": "economic", "doctrine": "expand_macro"},
                    "economy": {
                        "expand_bias": 0.8,
                        "worker_production_bias": 0.65,
                        "supply_buffer_bias": 0.5,
                    },
                    "production": {
                        "queue_biases": {
                            "TERRAN_COMMANDCENTER": 0.8,
                            "TERRAN_SUPPLYDEPOT": 0.5,
                        },
                        "composition_biases": {"macro": 0.8},
                    },
                    "tactical_task": {
                        "task_type": "expand_or_land_command_center",
                        "production_targets": [
                            "TERRAN_COMMANDCENTER",
                            "TERRAN_SCV",
                            "TERRAN_SUPPLYDEPOT",
                        ],
                        "priority": 0.8,
                    },
                }
            ),
        )
        self.assertTrue(expand_session.submit_text("사령부 하나 더", current_frame=10).ok)

        stop_session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "확장은 멈추고 방어에 집중한다.",
                    "strategy": {"posture": "defensive"},
                    "combat": {"defend_bias": 0.75, "aggression": -0.35},
                    "emergency": {"stop_expansion": True},
                }
            ),
        )
        result = stop_session.submit_text("확장 멈추고 수비해", current_frame=20)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        vector = result.update.vector
        self.assertEqual("", vector.strategy.doctrine)
        self.assertLessEqual(vector.economy.expand_bias, 0.0)
        self.assertNotIn(
            "TERRAN_COMMANDCENTER",
            vector.production.queue_biases.to_dict(),
        )
        self.assertGreaterEqual(vector.economy.worker_production_bias, 0.6)
        self.assertGreaterEqual(vector.economy.supply_buffer_bias, 0.5)

    def test_retries_transient_telemetry_read_before_issuing_live_update(self) -> None:
        backend = EventuallyReadableTelemetryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "공격적으로 압박해", "combat": {"aggression": 0.5}}
            ),
        )

        result = session.submit_text("공격적으로 압박해", update_id="frame-race")

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(321, result.current_frame)
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual(321, result.update.issued_at_frame)
        self.assertGreaterEqual(backend.read_count, 2)

    def test_text_provider_output_publishes_modulation_update(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        backend.ingest_telemetry(MicroMachineTelemetry(frame=42))
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "탱크 중심으로 안전하게 버텨",
                    "override_level": "constraint",
                    "confidence": 0.8,
                    "ttl_seconds": 90,
                    "posture": "defensive",
                    "combat": {"defend_bias": 0.7, "aggression": -0.2},
                    "tags": ["live_text"],
                }
            ),
        )

        result = session.submit_text(
            "탱크 중심으로 안전하게 버텨",
            update_id="live-42",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(LiveModulationStatus.PUBLISHED, result.status)
        self.assertEqual(42, result.current_frame)
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual("live-42", result.update.update_id)
        self.assertEqual(42, result.update.issued_at_frame)
        self.assertEqual(32, result.update.vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertEqual("standing_order", result.update.vector.lifetime.mode)
        self.assertEqual(
            42 + 900 * MICROMACHINE_GAME_LOOPS_PER_SECOND,
            result.update.expires_at_frame,
        )
        self.assertEqual(
            LiveModulationConsumptionStatus.PENDING_CONSUMPTION,
            result.consumption_status,
        )
        latest = backend.read_latest_update(current_frame=42)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual("live-42", latest.update_id)

    def test_does_not_report_consumed_from_pre_publish_telemetry(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        backend.ingest_telemetry(
            MicroMachineTelemetry(
                frame=100,
                active_modulation_ids=("known-live-id",),
            )
        )
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "공격적으로 압박해", "combat": {"aggression": 0.5}}
            ),
        )

        result = session.submit_text("공격적으로 압박해", update_id="known-live-id")

        self.assertTrue(result.ok, result.to_dict())
        self.assertFalse(result.consumed)
        self.assertEqual(
            LiveModulationConsumptionStatus.PENDING_CONSUMPTION,
            result.consumption_status,
        )

    def test_reports_consumed_only_from_post_publish_telemetry(self) -> None:
        backend = AutoConsumingBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "공격적으로 압박해", "combat": {"aggression": 0.5}}
            ),
        )

        consumed = session.submit_text(
            "공격적으로 압박해",
            current_frame=100,
            update_id="known-live-id",
        )

        self.assertTrue(consumed.ok, consumed.to_dict())
        self.assertTrue(consumed.consumed)
        self.assertEqual(
            LiveModulationConsumptionStatus.CONSUMED,
            consumed.consumption_status,
        )

    def test_oserror_publish_failure_returns_structured_result(self) -> None:
        backend = FailingPublishBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "hold", "combat": {"defend_bias": 0.2}}
            ),
        )

        result = session.submit_text("hold", current_frame=3)

        self.assertFalse(result.ok)
        self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
        self.assertTrue(result.provider_failure_recorded)
        telemetry = backend.read_latest_telemetry()
        self.assertIsNotNone(telemetry)
        assert telemetry is not None
        self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_refused_provider_output_does_not_publish_and_records_failure(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "status": "refused",
                    "refusal_reason": "strategy objective is unsafe",
                }
            ),
        )

        result = session.submit_text("unsafe", current_frame=77)

        self.assertFalse(result.ok)
        self.assertEqual(LiveModulationStatus.REFUSED, result.status)
        self.assertEqual(PolicyModulationCompileStatus.REFUSED, result.compile_result.status)
        self.assertIsNone(result.update)
        self.assertTrue(result.provider_failure_recorded)
        self.assertIsNone(backend.read_latest_update(current_frame=77))
        telemetry = backend.read_latest_telemetry()
        self.assertIsNotNone(telemetry)
        assert telemetry is not None
        self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_clarification_required_does_not_publish_or_mark_provider_down(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "status": "clarification_required",
                    "clarification_prompt": "어느 타이밍까지 수비할까요?",
                }
            ),
        )

        result = session.submit_text("수비?", current_frame=11)

        self.assertFalse(result.ok)
        self.assertEqual(LiveModulationStatus.CLARIFICATION_REQUIRED, result.status)
        self.assertFalse(result.provider_failure_recorded)
        self.assertIsNone(result.update)
        self.assertIsNone(backend.read_latest_update(current_frame=11))
        self.assertIsNone(backend.read_latest_telemetry())

    def test_publish_failure_returns_failure_result_without_latest_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "unsafe dynamic key",
                        "combat": {
                            "target_priority_biases": {
                                "BANELING\ncombat.aggression": 0.9,
                            }
                        },
                    }
                ),
            )

            result = session.submit_text("unsafe dynamic key", current_frame=17)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
            self.assertTrue(result.provider_failure_recorded)
            self.assertIsNone(result.update)
            self.assertFalse((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())
            self.assertFalse((Path(directory) / LATEST_UPDATE_KV_NAME).exists())
            telemetry = backend.read_latest_telemetry()
            self.assertIsNotNone(telemetry)
            assert telemetry is not None
            self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_archive_failure_does_not_leave_latest_files_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "modulation_updates.jsonl").mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "archive blocked",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("archive blocked", current_frame=19)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
            self.assertFalse((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())
            self.assertFalse((Path(directory) / LATEST_UPDATE_KV_NAME).exists())
            telemetry = backend.read_latest_telemetry()
            self.assertIsNotNone(telemetry)
            assert telemetry is not None
            self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_broken_latest_json_path_still_returns_publish_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_UPDATE_JSON_NAME).mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "broken latest json",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("broken latest json", current_frame=21)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
            self.assertTrue(result.provider_failure_recorded)
            self.assertEqual(
                MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE,
                result.dashboard.last_failure,
            )
            self.assertFalse((Path(directory) / LATEST_UPDATE_KV_NAME).exists())

    def test_broken_latest_telemetry_path_does_not_escape_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_TELEMETRY_JSON_NAME).mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "ignore broken telemetry",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("ignore broken telemetry", update_id="broken-telemetry")

            self.assertTrue(result.ok, result.to_dict())
            self.assertEqual(0, result.current_frame)
            self.assertEqual(
                LiveModulationConsumptionStatus.PENDING_TELEMETRY,
                result.consumption_status,
            )
            self.assertEqual(
                MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
                result.dashboard.last_failure,
            )

    def test_refusal_with_broken_telemetry_path_stays_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_TELEMETRY_JSON_NAME).mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "status": "refused",
                        "refusal_reason": "provider refused",
                    }
                ),
            )

            result = session.submit_text("refused", current_frame=5)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.REFUSED, result.status)
            self.assertFalse(result.provider_failure_recorded)
            self.assertEqual(
                MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
                result.dashboard.last_failure,
            )

    def test_malformed_latest_telemetry_type_does_not_escape_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_TELEMETRY_JSON_NAME).write_text(
                json.dumps(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": "bad",
                        "bot_name": "MicroMachine",
                        "race": "Terran",
                        "managers": {},
                        "active_modulation_ids": [],
                        "last_failure": None,
                    }
                )
            )
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "ignore malformed telemetry",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("ignore malformed telemetry", current_frame=1)

            self.assertTrue(result.ok, result.to_dict())
            self.assertEqual(
                LiveModulationConsumptionStatus.PENDING_TELEMETRY,
                result.consumption_status,
            )
            self.assertEqual(
                MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
                result.dashboard.last_failure,
            )

    def test_ttl_expiry_is_enforced_by_backend_after_live_publish(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "short hold", "ttl_seconds": 1, "combat": {"defend_bias": 0.2}}
            ),
        )

        result = session.submit_text("short hold", current_frame=5, update_id="short")

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(backend.read_latest_update(current_frame=27))
        with self.assertRaisesRegex(ValueError, "stale"):
            backend.read_latest_update(current_frame=28)

    def test_filesystem_session_writes_json_kv_and_telemetry_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = MicroMachineFilesystemBlackboard(directory)
            backend.ingest_telemetry(MicroMachineTelemetry(frame=640))
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "hold natural",
                        "override_level": "constraint",
                        "combat": {"defend_bias": 0.6},
                    }
                ),
            )

            result = session.submit_text("hold natural", update_id="fs-live")

            self.assertTrue(result.ok, result.to_dict())
            root = Path(directory)
            latest_json = root / LATEST_UPDATE_JSON_NAME
            latest_kv = root / LATEST_UPDATE_KV_NAME
            latest_telemetry = root / LATEST_TELEMETRY_JSON_NAME
            self.assertTrue(latest_json.exists())
            self.assertTrue(latest_kv.exists())
            self.assertTrue(latest_telemetry.exists())
            self.assertEqual("fs-live", json.loads(latest_json.read_text())["update_id"])
            self.assertIn("combat.defend_bias=0.6", latest_kv.read_text())

    def test_keyword_provider_allows_no_sdk_text_publish(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text("탱크로 수비하면서 버텨", current_frame=9)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertLess(result.update.vector.combat.aggression, 0)
        self.assertGreater(result.update.vector.combat.defend_bias, 0)
        self.assertEqual(32, result.update.vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertEqual("smoke_keyword", result.update.vector.source.value)

    def test_cli_without_provider_output_fails_closed_instead_of_keyword_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--blackboard-dir",
                        directory,
                        "--command",
                        "탱크로 수비하면서 버텨",
                        "--current-frame",
                        "9",
                    ]
                )

            self.assertEqual(2, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["ok"], result)
            self.assertEqual("refused", result["compile_result"]["status"])
            self.assertEqual("llm", result["provider_source"])
            self.assertIsNone(result["update"])
            self.assertFalse((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())

    def test_cli_allows_keyword_provider_only_with_explicit_smoke_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--blackboard-dir",
                        directory,
                        "--command",
                        "탱크로 수비하면서 버텨",
                        "--current-frame",
                        "9",
                        "--allow-smoke-keyword-provider",
                    ]
                )

            self.assertEqual(0, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ok"], result)
            self.assertEqual("smoke_keyword", result["provider_source"])
            self.assertTrue((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())

    def test_cli_writes_result_and_filesystem_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            payload = json.dumps(
                {
                    "goal": "cli hold",
                    "override_level": "constraint",
                    "combat": {"defend_bias": 0.55},
                }
            )

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--blackboard-dir",
                        directory,
                        "--command",
                        "cli hold",
                        "--current-frame",
                        "13",
                        "--update-id",
                        "cli-live",
                        "--provider-output-json",
                        payload,
                    ]
                )

            self.assertEqual(0, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ok"], result)
            self.assertEqual("cli-live", result["update"]["update_id"])
            self.assertTrue((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())
            self.assertTrue((Path(directory) / LATEST_UPDATE_KV_NAME).exists())


if __name__ == "__main__":
    unittest.main()
