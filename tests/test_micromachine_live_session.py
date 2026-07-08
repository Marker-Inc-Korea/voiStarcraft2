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
        self.assertIn("explicit_unit_count", vector.tags)

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
        self.assertIn("flank_route", vector.tags)

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
        self.assertEqual(2, vector.tactical_task.max_units)
        self.assertIn("TERRAN_MARINE", vector.tactical_task.unit_classes)

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
        self.assertIn("live_command_reducer_applied", second.compile_result.warnings)
        self.assertIn("command_category:scouting", vector.tags)
        self.assertIn("command_action:merge_standing_orders", vector.tags)

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
        self.assertEqual(
            42 + 90 * MICROMACHINE_GAME_LOOPS_PER_SECOND,
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
