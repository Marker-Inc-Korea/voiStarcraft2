import unittest

from starcraft_commander.micromachine_command_execution import (
    LIVE_QA_SCENARIOS,
    classify_micromachine_command_execution,
)
from starcraft_commander.micromachine_tactical_evidence import (
    classify_micromachine_tactical_evidence,
)


def _update() -> dict[str, object]:
    return {
        "update_id": "qa-four-marine-attack",
        "issued_at_frame": 100,
        "expires_at_frame": 2_000,
        "manager_bias_domains": ["combat", "composition_requirements"],
        "vector": {
            "goal": "four marine attack",
            "tags": ["pressure"],
            "combat": {"aggression": 0.8},
            "composition_requirements": [
                {"unit_type": "TERRAN_MARINE", "count": 4, "role": "frontline"}
            ],
        },
    }


def _telemetry(
    *,
    frame: int = 1_200,
    action: bool = True,
    moved: bool = True,
) -> dict[str, object]:
    max_distance = 18.0 if moved else 3.0
    return {
        "frame": frame,
        "active_modulation_ids": ["qa-four-marine-attack"],
        "managers": {
            "GameCommander": {
                "policy_active": True,
                "update_id": "qa-four-marine-attack",
            },
            "CombatCommander": {
                "active": True,
                "bounded_intervention": True,
                "main_attack_actual_command_issued_count": 1 if action else 0,
                "main_attack_last_action_frame": 620 if action else 0,
                "main_attack_last_issued_action": (
                    "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
                    if action
                    else ""
                ),
                "main_attack_order_status": "Attack",
                "main_attack_max_home_distance": max_distance,
            },
            "CompositionTask": {
                "active": True,
                "status": "assigned",
                "task_update_id": "qa-four-marine-attack",
                "assigned_frame": 620,
                "assigned_count": 4,
            },
        },
    }


class MicroMachineCommandExecutionTest(unittest.TestCase):
    def test_publish_only_cannot_complete_command(self) -> None:
        report = classify_micromachine_command_execution(
            latest_update=_update(),
            latest_telemetry={
                "frame": 1_200,
                "active_modulation_ids": [],
                "managers": {},
            },
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok)
        self.assertTrue(report.failed)
        self.assertEqual("failed", report.state)
        self.assertEqual("GameCommander", report.blocker_manager)
        self.assertEqual(
            "consumed_by_manager",
            [stage.name for stage in report.stages if not stage.ok][0],
        )

    def test_main_attack_requires_action_and_displacement_for_completion(self) -> None:
        telemetry = _telemetry(action=True, moved=True)
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=_update(),
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual("completed", report.state)
        self.assertTrue(report.to_dict()["stages"][-1]["ok"])
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual(tuple(LIVE_QA_SCENARIOS), tuple(scenarios))
        self.assertEqual("passed", scenarios["four_marine_attack"].status)

    def test_tactical_alias_uses_normalized_effect_for_completion(self) -> None:
        telemetry = _telemetry(action=True, moved=True)
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("aggressive_pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=_update(),
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("aggressive_pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertTrue(report.ok, report.to_dict())
        self.assertIn("pressure", tactical.observed_effects)

    def test_action_without_displacement_is_not_effect_observed(self) -> None:
        telemetry = _telemetry(action=True, moved=False)
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=_update(),
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok)
        self.assertEqual("failed", report.state)
        self.assertEqual("Telemetry", report.blocker_manager)
        self.assertIn("No observed", report.blocker_reason)

    def test_previous_update_production_effect_cannot_complete_current_command(self) -> None:
        update = _update()
        update["update_id"] = "current"
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["current"],
            "managers": {
                "GameCommander": {"update_id": "current"},
                "ProductionManager": {
                    "bounded_intervention": True,
                    "policy_update_id": "previous",
                    "last_doctrine_queue_item": "SiegeTank",
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|SiegeTank",
                    "last_actual_production_command_item": "SiegeTank",
                    "last_actual_production_command_update_id": "previous",
                    "last_actual_production_command_frame": 900,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            expected_production_items=("SiegeTank",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("failed", report.state)
        self.assertEqual("ProductionManager", report.blocker_manager)
        self.assertFalse(report.to_dict()["stages"][-1]["ok"])

    def test_queue_intent_without_actual_production_command_cannot_complete(self) -> None:
        update = _update()
        update["update_id"] = "qa-tank-chain"
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-tank-chain"],
            "managers": {
                "GameCommander": {"update_id": "qa-tank-chain"},
                "ProductionManager": {
                    "bounded_intervention": True,
                    "policy_update_id": "qa-tank-chain",
                    "last_doctrine_action": "tank_defensive_hold",
                    "last_doctrine_queue_item": "SiegeTank",
                    "last_doctrine_update_id": "qa-tank-chain",
                    "actual_production_command_issued_count": 0,
                    "last_actual_production_command": "none|none",
                    "last_actual_production_command_item": "none",
                    "last_actual_production_command_update_id": "",
                    "last_actual_production_command_frame": 0,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            expected_production_items=("SiegeTank",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("failed", report.state)
        self.assertFalse(report.to_dict()["stages"][-1]["ok"])

    def test_wrong_current_production_item_does_not_satisfy_expected_item(self) -> None:
        update = _update()
        update["update_id"] = "qa-tank-chain"
        update["issued_at_frame"] = 100
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-tank-chain"],
            "managers": {
                "GameCommander": {"update_id": "qa-tank-chain"},
                "ProductionManager": {
                    "bounded_intervention": True,
                    "policy_update_id": "qa-tank-chain",
                    "last_doctrine_action": "marine_rush",
                    "last_doctrine_queue_item": "Marine",
                    "last_doctrine_update_id": "qa-tank-chain",
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|Marine",
                    "last_actual_production_command_item": "Marine",
                    "last_actual_production_command_update_id": "qa-tank-chain",
                    "last_actual_production_command_frame": 620,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            expected_production_items=("SiegeTank",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("failed", report.state)
        self.assertFalse(report.to_dict()["stages"][-1]["ok"])

    def test_stale_archive_item_does_not_satisfy_tank_production_scenario(self) -> None:
        update = _update()
        update["update_id"] = "qa-tank-chain"
        update["issued_at_frame"] = 1_000
        stale_archive = {
            "frame": 900,
            "managers": {
                "ProductionManager": {
                    "policy_update_id": "previous",
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|SiegeTank",
                    "last_actual_production_command_item": "SiegeTank",
                    "last_actual_production_command_update_id": "previous",
                    "last_actual_production_command_frame": 900,
                },
            },
        }
        latest = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-tank-chain"],
            "managers": {
                "GameCommander": {"update_id": "qa-tank-chain"},
                "ProductionManager": {
                    "bounded_intervention": True,
                    "policy_update_id": "qa-tank-chain",
                    "last_doctrine_action": "marine_rush",
                    "last_doctrine_queue_item": "Marine",
                    "last_doctrine_update_id": "qa-tank-chain",
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|Marine",
                    "last_actual_production_command_item": "Marine",
                    "last_actual_production_command_update_id": "qa-tank-chain",
                    "last_actual_production_command_frame": 1_100,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=latest,
            telemetry_archive=(stale_archive,),
            expected_production_items=("SiegeTank",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["tank_production_prerequisite_chain"].status)

    def test_stale_archived_combat_action_cannot_complete_current_attack(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        archived = _telemetry(frame=900, action=True, moved=True)
        managers = archived["managers"]
        assert isinstance(managers, dict)
        combat = managers["CombatCommander"]
        assert isinstance(combat, dict)
        combat["main_attack_last_action_frame"] = 800
        latest = {
            "frame": 1_200,
            "active_modulation_ids": ["current"],
            "managers": {
                "GameCommander": {"update_id": "current"},
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=latest,
            telemetry_archive=(archived,),
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=latest,
            telemetry_archive=(archived,),
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("failed", report.state)
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["four_marine_attack"].status)

    def test_stale_archived_movement_cannot_complete_current_action(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        archived = _telemetry(frame=900, action=True, moved=True)
        latest = _telemetry(frame=1_200, action=True, moved=False)
        latest["active_modulation_ids"] = ["current"]
        managers = latest["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        composition = managers["CompositionTask"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        assert isinstance(composition, dict)
        game["update_id"] = "current"
        combat["main_attack_last_action_frame"] = 1_100
        composition["task_update_id"] = "current"
        composition["assigned_frame"] = 1_100
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=latest,
            telemetry_archive=(archived,),
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=latest,
            telemetry_archive=(archived,),
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("failed", report.state)
        self.assertEqual("Telemetry", report.blocker_manager)

    def test_matching_update_id_with_stale_action_frame_cannot_complete(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        telemetry = _telemetry(frame=1_200, action=True, moved=True)
        telemetry["active_modulation_ids"] = ["current"]
        managers = telemetry["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        composition = managers["CompositionTask"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        assert isinstance(composition, dict)
        game["update_id"] = "current"
        combat["policy_update_id"] = "current"
        combat["main_attack_last_action_frame"] = 800
        composition["task_update_id"] = "current"
        composition["assigned_frame"] = 1_100
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("failed", report.state)
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["four_marine_attack"].status)

    def test_unrelated_fresh_frame_cannot_mask_stale_main_attack_frame(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        telemetry = _telemetry(frame=1_200, action=True, moved=True)
        telemetry["active_modulation_ids"] = ["current"]
        managers = telemetry["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        composition = managers["CompositionTask"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        assert isinstance(composition, dict)
        game["update_id"] = "current"
        combat["policy_update_id"] = "current"
        combat["main_attack_last_action_frame"] = 800
        combat["last_actual_command_frame"] = 1_100
        composition["task_update_id"] = "current"
        composition["assigned_frame"] = 1_100
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["four_marine_attack"].status)

    def test_unrelated_stale_scout_frame_does_not_block_current_main_attack(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        telemetry = _telemetry(frame=1_200, action=True, moved=True)
        telemetry["active_modulation_ids"] = ["current"]
        managers = telemetry["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        composition = managers["CompositionTask"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        assert isinstance(composition, dict)
        game["update_id"] = "current"
        combat["policy_update_id"] = "current"
        combat["main_attack_last_action_frame"] = 1_100
        combat["scout_actual_command_issued_count"] = 1
        combat["scout_last_action_frame"] = 800
        composition["task_update_id"] = "current"
        composition["assigned_frame"] = 1_100
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertTrue(report.ok, report.to_dict())
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("passed", scenarios["four_marine_attack"].status)

    def test_stale_building_task_cannot_pass_bunker_placement_scenario(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        archived = {
            "frame": 900,
            "managers": {
                "BuildingTask": {
                    "task_update_id": "previous",
                    "status": "command_issued",
                    "last_building_command_frame": 900,
                    "last_building_command": "build_command|Bunker",
                },
            },
        }
        latest = {
            "frame": 1_200,
            "active_modulation_ids": ["current"],
            "managers": {
                "GameCommander": {"update_id": "current"},
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=latest,
            telemetry_archive=(archived,),
            latest_frame=1_200,
            target_frame=1_000,
        )

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["bunker_placement_intent"].status)

    def test_stale_combat_effect_cannot_complete_via_current_production_action(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["current"],
            "managers": {
                "GameCommander": {"update_id": "current"},
                "ProductionManager": {
                    "policy_update_id": "current",
                    "last_doctrine_action": "marine_rush",
                    "last_doctrine_queue_item": "Marine",
                    "last_doctrine_update_id": "current",
                    "last_doctrine_frame": 1_100,
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|Marine",
                    "last_actual_production_command_item": "Marine",
                    "last_actual_production_command_update_id": "current",
                    "last_actual_production_command_frame": 1_100,
                },
                "CombatCommander": {
                    "policy_update_id": "current",
                    "main_attack_actual_command_issued_count": 1,
                    "main_attack_last_action_frame": 800,
                    "main_attack_last_issued_action": (
                        "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
                    ),
                    "main_attack_max_home_distance": 18.0,
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("failed", report.state)
        self.assertFalse(report.to_dict()["stages"][-1]["ok"])

    def test_production_command_can_complete_production_focused_command(self) -> None:
        update = _update()
        update["update_id"] = "qa-tank-chain"
        update["manager_bias_domains"] = ["production", "tech"]
        update["vector"] = {
            "goal": "produce tanks",
            "tags": ["tank_defensive_hold"],
            "production": {"queue_biases": {"tank": 0.8}},
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-tank-chain"],
            "managers": {
                "GameCommander": {"update_id": "qa-tank-chain"},
                "ProductionManager": {
                    "bounded_intervention": True,
                    "policy_update_id": "qa-tank-chain",
                    "last_doctrine_action": "tank_defensive_hold",
                    "last_doctrine_queue_item": "SiegeTank",
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|SiegeTank",
                    "last_actual_production_command_item": "SiegeTank",
                    "last_actual_production_command_update_id": "qa-tank-chain",
                    "last_actual_production_command_frame": 620,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            expected_production_items=("SiegeTank",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertTrue(report.ok, report.to_dict())
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("passed", scenarios["tank_production_prerequisite_chain"].status)

    def test_worker_style_scout_does_not_satisfy_marine_scout_scenario(self) -> None:
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-four-marine-attack"],
            "managers": {
                "GameCommander": {"update_id": "qa-four-marine-attack"},
                "ScoutManager": {
                    "actual_command_issued_count": 1,
                    "last_actual_command": "move|worker_scout|x=33.5|y=138.5",
                    "max_home_distance": 34.0,
                },
                "CombatCommander": {},
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("scout",),
            log_text="620: Scout policy target selected",
        )

        report = classify_micromachine_command_execution(
            latest_update=_update(),
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("scout",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["marine_scout"].status)

    def test_current_combat_scout_is_not_blocked_by_stale_main_attack_frame(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["current"],
            "managers": {
                "GameCommander": {"update_id": "current"},
                "CombatCommander": {
                    "policy_update_id": "current",
                    "main_attack_actual_command_issued_count": 1,
                    "main_attack_last_action_frame": 800,
                    "main_attack_last_issued_action": (
                        "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
                    ),
                    "scout_actual_command_issued_count": 1,
                    "scout_last_action_frame": 1_100,
                    "scout_last_issued_action": (
                        "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
                    ),
                    "scout_max_home_distance": 14.0,
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("scout",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("scout",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertTrue(report.ok, report.to_dict())
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("passed", scenarios["marine_scout"].status)


if __name__ == "__main__":
    unittest.main()
