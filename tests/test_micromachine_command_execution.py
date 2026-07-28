import unittest

from starcraft_commander.micromachine_command_execution import (
    LIVE_QA_SCENARIOS,
    classify_micromachine_command_execution,
    classify_micromachine_operation_executions,
)
from starcraft_commander.micromachine_tactical_evidence import (
    MicroMachineTacticalEvidence,
    MicroMachineTacticalEffect,
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
    def test_parallel_operations_are_classified_from_independent_evidence(self) -> None:
        update = {
            "update_id": "parallel-a",
            "issued_at_frame": 100,
            "expires_at_frame": 2_000,
            "vector": {
                "goal": "parallel scout and attack",
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "goal": "two marines scout",
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "min_units": 2,
                            "max_units": 2,
                        },
                    },
                    {
                        "operation_id": "assault-bravo",
                        "goal": "four marines attack",
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "min_units": 4,
                            "max_units": 4,
                        },
                    },
                ],
            },
        }
        telemetry = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "parallel-a",
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "status": "completed",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 180,
                            "assigned_unit_tags": [11, 12],
                            "assigned_count": 2,
                            "max_home_distance": 24.0,
                            "completed": True,
                            "last_action": "MoveToGoalOrder|operation=recon-alpha",
                        },
                        {
                            "operation_id": "assault-bravo",
                            "status": "assigned",
                            "received_frame": 115,
                            "assigned_frame": 125,
                            "submitted_frame": 0,
                            "assigned_unit_tags": [21, 22, 23, 24],
                            "assigned_count": 4,
                            "max_home_distance": 2.0,
                        },
                    ],
                }
            },
        }

        reports = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=500,
        )

        by_id = {report.operation_id: report for report in reports}
        self.assertTrue(by_id["recon-alpha"].ok, by_id["recon-alpha"].to_dict())
        self.assertEqual("completed", by_id["recon-alpha"].state)
        self.assertFalse(by_id["assault-bravo"].ok)
        self.assertEqual("queued_or_assigned", by_id["assault-bravo"].state)
        assault_stages = {
            stage.name: stage for stage in by_id["assault-bravo"].stages
        }
        self.assertFalse(assault_stages["action_issued"].ok)
        self.assertFalse(assault_stages["effect_observed"].ok)

    def test_duplicate_unit_ownership_blocks_parallel_operations(self) -> None:
        update = {
            "update_id": "parallel-conflict",
            "issued_at_frame": 100,
            "expires_at_frame": 2_000,
            "vector": {
                "goal": "parallel operations",
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "tactical_task": {"task_type": "scout_with_units"},
                    },
                    {
                        "operation_id": "assault-bravo",
                        "tactical_task": {
                            "task_type": "pressure_with_main_army"
                        },
                    },
                ],
            },
        }
        telemetry = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "parallel-conflict",
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "status": "moving",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "assigned_unit_tags": [11, 99],
                            "assigned_count": 2,
                            "max_home_distance": 18.0,
                        },
                        {
                            "operation_id": "assault-bravo",
                            "status": "moving",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "assigned_unit_tags": [99, 21, 22, 23],
                            "assigned_count": 4,
                            "max_home_distance": 18.0,
                        },
                    ],
                }
            },
        }

        reports = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=500,
        )

        self.assertEqual(2, len(reports))
        for report in reports:
            with self.subTest(operation_id=report.operation_id):
                self.assertTrue(report.failed)
                self.assertEqual("blocked", report.state)
                self.assertEqual("OperationDirector", report.blocker_manager)
                self.assertIn("Duplicate unit ownership", report.blocker_reason)

    def test_rejected_edit_maps_active_generation_to_requested_generation(self) -> None:
        update = {
            "update_id": "rejected-edit",
            "issued_at_frame": 200,
            "vector": {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "generation": 2,
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "duration_seconds": 120,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 240,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "rejected-edit",
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "status": "MOVING",
                            "received_frame": 100,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 140,
                            "assigned_unit_tags": [11],
                            "assigned_count": 1,
                            "max_home_distance": 20.0,
                            "engaged": True,
                            "last_action": "AttackUnitOrder",
                            "edit_requested_generation": 2,
                            "edit_rejected_update_id": "rejected-edit",
                            "edit_rejected_frame": 225,
                            "edit_resolution": "blocked",
                            "edit_blocker": "source_composition_preservation",
                        }
                    ],
                }
            },
        }

        reports = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=240,
        )

        self.assertEqual(1, len(reports))
        report = reports[0]
        self.assertEqual(2, report.operation_generation)
        self.assertTrue(report.failed)
        self.assertEqual("blocked", report.state)
        self.assertEqual(
            "source_composition_preservation",
            report.blocker_reason,
        )
        consumed = next(
            stage
            for stage in report.stages
            if stage.name == "consumed_by_manager"
        )
        self.assertTrue(consumed.ok)
        self.assertEqual(1, consumed.evidence["active_generation"])
        stages = {stage.name: stage for stage in report.stages}
        self.assertFalse(stages["queued_or_assigned"].ok)
        self.assertFalse(stages["order_issued"].ok)
        self.assertFalse(stages["action_issued"].ok)
        self.assertFalse(stages["effect_observed"].ok)

    def test_operation_evidence_requires_exact_command_identity(self) -> None:
        update = {
            "update_id": "identity-bound-operation",
            "issued_at_frame": 1_000,
            "expires_at_frame": 20_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "issued_at_frame": 1_000,
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        operation_payload = {
            "operation_id": "recon-alpha",
            "generation": 1,
            "status": "moving",
            "received_frame": 1_010,
            "assigned_frame": 1_020,
            "submitted_frame": 1_030,
            "last_action_frame": 1_040,
            "assigned_unit_tags": [11],
            "assigned_count": 1,
            "max_home_distance": 18.0,
            "last_action": "MoveToGoalOrder|operation=recon-alpha",
        }

        for policy_update_id in ("", "different-command"):
            with self.subTest(policy_update_id=policy_update_id):
                report = classify_micromachine_operation_executions(
                    latest_update=update,
                    latest_telemetry={
                        "frame": 1_200,
                        "managers": {
                            "OperationDirector": {
                                "policy_update_id": policy_update_id,
                                "operations": [operation_payload],
                            }
                        },
                    },
                    latest_frame=1_200,
                )[0]

                stages = {stage.name: stage for stage in report.stages}
                self.assertEqual("published", report.state)
                self.assertFalse(stages["consumed_by_manager"].ok)
                self.assertFalse(stages["queued_or_assigned"].ok)
                self.assertFalse(stages["order_issued"].ok)
                self.assertFalse(stages["action_issued"].ok)
                self.assertFalse(stages["effect_observed"].ok)

    def test_outer_frame_cannot_freshen_stale_operation_evidence(self) -> None:
        update = {
            "update_id": "stale-operation-evidence",
            "issued_at_frame": 1_000,
            "expires_at_frame": 20_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "issued_at_frame": 1_000,
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 1_200,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "stale-operation-evidence",
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "status": "moving",
                            "received_frame": 800,
                            "assigned_frame": 810,
                            "submitted_frame": 820,
                            "last_action_frame": 830,
                            "assigned_unit_tags": [11],
                            "assigned_count": 1,
                            "max_home_distance": 18.0,
                            "last_action": (
                                "MoveToGoalOrder|operation=recon-alpha"
                            ),
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=1_200,
        )[0]

        stages = {stage.name: stage for stage in report.stages}
        self.assertEqual("published", report.state)
        self.assertFalse(stages["consumed_by_manager"].ok)
        self.assertFalse(stages["queued_or_assigned"].ok)
        self.assertFalse(stages["order_issued"].ok)
        self.assertFalse(stages["action_issued"].ok)
        self.assertFalse(stages["effect_observed"].ok)

    def test_operation_evidence_frames_are_validated_independently(self) -> None:
        update = {
            "update_id": "independent-operation-frames",
            "issued_at_frame": 1_000,
            "expires_at_frame": 20_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "issued_at_frame": 1_000,
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        cases = (
            (
                "stale_assignment",
                {
                    "received_frame": 1_010,
                    "assigned_frame": 900,
                    "submitted_frame": 1_030,
                    "last_action_frame": 1_040,
                },
                {
                    "consumed_by_manager": True,
                    "queued_or_assigned": False,
                    "order_issued": True,
                    "action_issued": True,
                    "effect_observed": True,
                },
            ),
            (
                "stale_submission",
                {
                    "received_frame": 1_010,
                    "assigned_frame": 1_020,
                    "submitted_frame": 900,
                    "last_action_frame": 1_040,
                },
                {
                    "consumed_by_manager": True,
                    "queued_or_assigned": True,
                    "order_issued": False,
                    "action_issued": True,
                    "effect_observed": True,
                },
            ),
            (
                "stale_action",
                {
                    "received_frame": 1_010,
                    "assigned_frame": 1_020,
                    "submitted_frame": 1_030,
                    "last_action_frame": 900,
                },
                {
                    "consumed_by_manager": True,
                    "queued_or_assigned": True,
                    "order_issued": True,
                    "action_issued": False,
                    "effect_observed": False,
                },
            ),
        )

        for name, evidence_frames, expected_stages in cases:
            with self.subTest(name=name):
                report = classify_micromachine_operation_executions(
                    latest_update=update,
                    latest_telemetry={
                        "frame": 1_200,
                        "managers": {
                            "OperationDirector": {
                                "policy_update_id": (
                                    "independent-operation-frames"
                                ),
                                "operations": [
                                    {
                                        "operation_id": "recon-alpha",
                                        "generation": 1,
                                        "status": "moving",
                                        "assigned_unit_tags": [11],
                                        "assigned_count": 1,
                                        "max_home_distance": 18.0,
                                        "last_action": (
                                            "MoveToGoalOrder"
                                            "|operation=recon-alpha"
                                        ),
                                        **evidence_frames,
                                    }
                                ],
                            }
                        },
                    },
                    latest_frame=1_200,
                )[0]
                stages = {stage.name: stage for stage in report.stages}
                for stage_name, expected_ok in expected_stages.items():
                    self.assertEqual(
                        expected_ok,
                        stages[stage_name].ok,
                        report.to_dict(),
                    )

    def test_parallel_operations_use_independent_deadlines(self) -> None:
        update = {
            "update_id": "operation-deadlines",
            "issued_at_frame": 100,
            "expires_at_frame": 20_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "short-recon",
                        "issued_at_frame": 100,
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "duration_seconds": 10,
                        },
                    },
                    {
                        "operation_id": "long-assault",
                        "issued_at_frame": 100,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "duration_seconds": 300,
                        },
                    },
                ]
            },
        }
        telemetry = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "operation-deadlines",
                    "operations": [
                        {
                            "operation_id": "short-recon",
                            "generation": 1,
                            "status": "received",
                            "received_frame": 110,
                        },
                        {
                            "operation_id": "long-assault",
                            "generation": 1,
                            "status": "received",
                            "received_frame": 110,
                        },
                    ],
                }
            },
        }

        reports = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=500,
        )

        by_id = {report.operation_id: report for report in reports}
        self.assertTrue(by_id["short-recon"].expired, by_id["short-recon"].to_dict())
        self.assertEqual("expired", by_id["short-recon"].state)
        self.assertFalse(
            by_id["long-assault"].expired,
            by_id["long-assault"].to_dict(),
        )
        self.assertEqual("consumed_by_manager", by_id["long-assault"].state)

    def test_terminal_cleanup_stop_is_not_mission_action_or_effect(self) -> None:
        update = {
            "update_id": "cancel-cleanup-only",
            "issued_at_frame": 100,
            "vector": {
                "operations": [
                    {
                        "operation_id": "assault-bravo",
                        "issued_at_frame": 100,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 220,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "cancel-cleanup-only",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "CANCELLED",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 0,
                            "last_action_frame": 210,
                            "assigned_unit_tags": [21, 22, 23, 24],
                            "assigned_count": 4,
                            "max_home_distance": 25.0,
                            "engaged": True,
                            "completed": False,
                            "blocked_reason": "cancelled_by_policy",
                            "last_action": (
                                "release_stop|cancelled_by_policy"
                            ),
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=220,
        )[0]

        stages = {stage.name: stage for stage in report.stages}
        self.assertEqual("cancelled", report.state)
        self.assertFalse(report.completed)
        self.assertFalse(report.failed)
        self.assertFalse(report.expired)
        self.assertFalse(stages["order_issued"].ok)
        self.assertFalse(stages["action_issued"].ok)
        self.assertFalse(stages["effect_observed"].ok)
        self.assertEqual(
            "release_stop|cancelled_by_policy",
            stages["action_issued"].evidence["terminal_cleanup_action"],
        )
        self.assertEqual(
            210,
            stages["action_issued"].evidence[
                "terminal_cleanup_action_frame"
            ],
        )
        self.assertEqual("", stages["action_issued"].evidence["last_action"])

    def test_terminal_cleanup_without_owned_units_is_not_mission_action_or_effect(
        self,
    ) -> None:
        update = {
            "update_id": "cancel-cleanup-without-owned-units",
            "issued_at_frame": 100,
            "vector": {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "issued_at_frame": 100,
                        "tactical_task": {
                            "task_type": "scout_with_units",
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 220,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": (
                        "cancel-cleanup-without-owned-units"
                    ),
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "status": "CANCELLED",
                            "received_frame": 110,
                            "assigned_frame": 0,
                            "submitted_frame": 0,
                            "last_action_frame": 210,
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                            "max_home_distance": 0.0,
                            "engaged": False,
                            "completed": False,
                            "blocked_reason": "cancelled_by_policy",
                            "last_action": (
                                "release_no_owned_units|cancelled_by_policy"
                            ),
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=220,
        )[0]

        stages = {stage.name: stage for stage in report.stages}
        self.assertEqual("cancelled", report.state)
        self.assertFalse(report.completed)
        self.assertFalse(report.failed)
        self.assertFalse(report.expired)
        self.assertFalse(stages["order_issued"].ok)
        self.assertFalse(stages["action_issued"].ok)
        self.assertFalse(stages["effect_observed"].ok)
        self.assertEqual(
            "release_no_owned_units|cancelled_by_policy",
            stages["action_issued"].evidence["terminal_cleanup_action"],
        )
        self.assertEqual(
            210,
            stages["action_issued"].evidence[
                "terminal_cleanup_action_frame"
            ],
        )
        self.assertEqual("", stages["action_issued"].evidence["last_action"])

    def test_cancellation_preserves_prior_mission_action_and_effect(self) -> None:
        update = {
            "update_id": "cancel-after-mission-effect",
            "issued_at_frame": 100,
            "vector": {
                "operations": [
                    {
                        "operation_id": "assault-bravo",
                        "issued_at_frame": 100,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        mission_telemetry = {
            "frame": 180,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "cancel-after-mission-effect",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "ENGAGED",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 160,
                            "assigned_unit_tags": [21, 22, 23, 24],
                            "assigned_count": 4,
                            "max_home_distance": 25.0,
                            "engaged": True,
                            "completed": False,
                            "blocked_reason": "",
                            "last_action": (
                                "AttackMove|operation=assault-bravo"
                            ),
                        }
                    ],
                }
            },
        }
        cancelled_telemetry = {
            "frame": 220,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "cancel-after-mission-effect",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "CANCELLED",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 210,
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                            "max_home_distance": 25.0,
                            "engaged": True,
                            "completed": False,
                            "blocked_reason": "cancelled_by_policy",
                            "last_action": (
                                "release_stop|cancelled_by_policy"
                            ),
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            telemetry_archive=(mission_telemetry,),
            latest_telemetry=cancelled_telemetry,
            latest_frame=220,
        )[0]

        stages = {stage.name: stage for stage in report.stages}
        self.assertEqual("cancelled", report.state)
        self.assertFalse(report.failed)
        self.assertTrue(stages["order_issued"].ok)
        self.assertTrue(stages["action_issued"].ok)
        self.assertTrue(stages["effect_observed"].ok)
        self.assertEqual(160, stages["action_issued"].frame)
        self.assertEqual(
            "AttackMove|operation=assault-bravo",
            stages["action_issued"].evidence["last_action"],
        )
        self.assertEqual(
            "release_stop|cancelled_by_policy",
            stages["action_issued"].evidence["terminal_cleanup_action"],
        )
        self.assertEqual(
            210,
            stages["action_issued"].evidence[
                "terminal_cleanup_action_frame"
            ],
        )

    def test_operation_execution_ignores_stale_generation(self) -> None:
        update = {
            "update_id": "redirected-operation",
            "issued_at_frame": 500,
            "expires_at_frame": 2_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "assault-bravo",
                        "generation": 2,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 600,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "redirected-operation",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "completed",
                            "received_frame": 100,
                            "assigned_frame": 110,
                            "submitted_frame": 120,
                            "assigned_unit_tags": [11, 12, 13, 14],
                            "assigned_count": 4,
                            "max_home_distance": 30.0,
                            "completed": True,
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=600,
        )[0]

        self.assertEqual(2, report.operation_generation)
        self.assertFalse(report.ok)
        self.assertEqual("published", report.state)

    def test_preserved_generation_keeps_prior_submission_evidence(self) -> None:
        update = {
            "update_id": "parallel-addition",
            "issued_at_frame": 500,
            "expires_at_frame": 2_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "generation": 1,
                        "issued_at_frame": 100,
                        "tactical_task": {"task_type": "scout_with_units"},
                    }
                ]
            },
        }
        telemetry = {
            "frame": 600,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "parallel-addition",
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "status": "moving",
                            "received_frame": 100,
                            "assigned_frame": 110,
                            "submitted_frame": 120,
                            "last_action_frame": 120,
                            "assigned_unit_tags": [11],
                            "assigned_count": 1,
                            "max_home_distance": 18.0,
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=600,
        )[0]

        self.assertEqual("moving", report.state)
        self.assertTrue(
            {stage.name: stage.ok for stage in report.stages}["action_issued"]
        )

    def test_operation_archive_preserves_effect_after_policy_expiry(self) -> None:
        update = {
            "update_id": "archive-expiry",
            "issued_at_frame": 100,
            "expires_at_frame": 2_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "assault-bravo",
                        "generation": 1,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                        },
                    }
                ]
            },
        }
        archived = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "archive-expiry",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "engaged",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 450,
                            "last_action": "AttackTarget|operation=assault-bravo",
                            "assigned_unit_tags": [21],
                            "assigned_count": 1,
                            "max_home_distance": 112.0,
                            "engaged": True,
                        }
                    ],
                }
            },
        }
        latest = {
            "frame": 700,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "archive-expiry",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "blocked",
                            "blocked_reason": "policy_inactive_or_expired",
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=latest,
            telemetry_archive=(archived,),
            latest_frame=700,
        )[0]

        stages = {stage.name: stage for stage in report.stages}
        self.assertFalse(report.failed, report.to_dict())
        self.assertTrue(report.expired, report.to_dict())
        self.assertEqual("expired", report.state)
        self.assertTrue(stages["queued_or_assigned"].ok)
        self.assertTrue(stages["action_issued"].ok)
        self.assertTrue(stages["effect_observed"].ok)
        self.assertEqual(112.0, stages["effect_observed"].evidence["max_home_distance"])

    def test_transient_prerequisite_block_is_not_terminal_failure(self) -> None:
        update = {
            "update_id": "transient-block",
            "issued_at_frame": 100,
            "expires_at_frame": 2_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "assault-bravo",
                        "generation": 1,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "transient-block",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "blocked",
                            "blocked_reason": "composition_prerequisites_pending",
                            "received_frame": 110,
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=500,
        )[0]

        self.assertFalse(report.failed, report.to_dict())
        self.assertFalse(report.expired, report.to_dict())
        self.assertEqual("blocked", report.state)
        self.assertEqual(
            "composition_prerequisites_pending",
            report.blocker_reason,
        )

    def test_hard_operation_block_remains_terminal_failure(self) -> None:
        update = {
            "update_id": "hard-block",
            "issued_at_frame": 100,
            "expires_at_frame": 2_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "assault-bravo",
                        "generation": 1,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "hard-block",
                    "operations": [
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "blocked",
                            "blocked_reason": "invalid_target",
                            "received_frame": 110,
                        }
                    ],
                }
            },
        }

        report = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=500,
        )[0]

        self.assertTrue(report.failed, report.to_dict())
        self.assertEqual("blocked", report.state)
        self.assertEqual("invalid_target", report.blocker_reason)

    def test_historical_assignment_does_not_create_current_ownership_conflict(
        self,
    ) -> None:
        update = {
            "update_id": "tag-reassignment",
            "issued_at_frame": 100,
            "expires_at_frame": 2_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "generation": 1,
                        "tactical_task": {"task_type": "scout_with_units"},
                    },
                    {
                        "operation_id": "assault-bravo",
                        "generation": 1,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                        },
                    },
                ]
            },
        }
        archived = {
            "frame": 300,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "tag-reassignment",
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "status": "moving",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "assigned_unit_tags": [99],
                            "assigned_count": 1,
                            "max_home_distance": 18.0,
                        }
                    ],
                }
            },
        }
        latest = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "tag-reassignment",
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "status": "blocked",
                            "blocked_reason": "composition_prerequisites_pending",
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                        },
                        {
                            "operation_id": "assault-bravo",
                            "generation": 1,
                            "status": "moving",
                            "received_frame": 400,
                            "assigned_frame": 410,
                            "submitted_frame": 420,
                            "assigned_unit_tags": [99],
                            "assigned_count": 1,
                            "max_home_distance": 20.0,
                        },
                    ],
                }
            },
        }

        reports = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=latest,
            telemetry_archive=(archived,),
            latest_frame=500,
        )

        by_id = {report.operation_id: report for report in reports}
        self.assertFalse(by_id["recon-alpha"].failed, by_id["recon-alpha"].to_dict())
        self.assertFalse(
            by_id["assault-bravo"].failed,
            by_id["assault-bravo"].to_dict(),
        )

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

    def test_observed_effect_remains_completed_after_policy_ttl(self) -> None:
        update = _update()
        update["expires_at_frame"] = 700
        telemetry = _telemetry(action=True, moved=True)
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
        )

        self.assertTrue(report.ok, report.to_dict())
        self.assertTrue(report.completed)
        self.assertFalse(report.expired)
        self.assertEqual("completed", report.state)

    def test_production_command_ignores_unrelated_combat_actions(self) -> None:
        update = {
            "update_id": "qa-marine-standing",
            "issued_at_frame": 1_000,
            "expires_at_frame": 20_000,
            "manager_bias_domains": ["production", "tactical_task"],
            "vector": {
                "goal": "keep producing marines",
                "production": {
                    "queue_biases": {"TERRAN_MARINE": 1.0},
                },
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_MARINE"],
                },
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-marine-standing"],
            "managers": {
                "GameCommander": {"update_id": "qa-marine-standing"},
                "ProductionManager": {
                    "policy_update_id": "qa-marine-standing",
                    "last_doctrine_action": "marine_continuity",
                    "last_doctrine_queue_item": "Marine",
                    "last_doctrine_update_id": "qa-marine-standing",
                    "last_doctrine_frame": 1_100,
                    "actual_production_command_issued_count": 0,
                    "last_actual_production_command": "none|none",
                    "last_actual_production_command_item": "none",
                    "last_actual_production_command_update_id": "",
                    "last_actual_production_command_frame": 0,
                },
                "CombatCommander": {
                    "policy_update_id": "qa-marine-standing",
                    "main_attack_order_status": "Attack",
                    "main_attack_actual_command_issued_count": 4,
                    "main_attack_last_action_frame": 1_150,
                    "main_attack_last_issued_action": (
                        "MoveToGoalOrder|squad=MainAttack|type=2|x=90|y=90"
                    ),
                    "main_attack_max_home_distance": 20.0,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=1_200,
        )

        self.assertFalse(report.ok, report.to_dict())
        stages = {stage.name: stage for stage in report.stages}
        self.assertTrue(stages["queued_or_assigned"].ok)
        self.assertFalse(stages["order_issued"].ok)
        self.assertFalse(stages["action_issued"].ok)
        self.assertEqual("ProductionManager", stages["order_issued"].manager)

    def test_inferred_production_target_completes_on_current_command(self) -> None:
        update = {
            "update_id": "qa-marine-standing",
            "issued_at_frame": 1_000,
            "expires_at_frame": 20_000,
            "manager_bias_domains": ["production", "tactical_task"],
            "vector": {
                "goal": "keep producing marines",
                "production": {
                    "queue_biases": {"TERRAN_MARINE": 1.0},
                },
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_MARINE"],
                },
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-marine-standing"],
            "managers": {
                "GameCommander": {"update_id": "qa-marine-standing"},
                "ProductionManager": {
                    "policy_update_id": "qa-marine-standing",
                    "last_doctrine_action": "marine_continuity",
                    "last_doctrine_queue_item": "Marine",
                    "last_doctrine_update_id": "qa-marine-standing",
                    "last_doctrine_frame": 1_100,
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|Marine",
                    "last_actual_production_command_item": "Marine",
                    "last_actual_production_command_update_id": "qa-marine-standing",
                    "last_actual_production_command_frame": 1_100,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=1_200,
        )

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual("completed", report.state)

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

    def test_requested_non_marine_composition_attack_requires_assignment_and_movement(self) -> None:
        update = _update()
        update["update_id"] = "qa-mixed-attack"
        update["issued_at_frame"] = 100
        update["vector"] = {
            "goal": "tank and viking attack",
            "tags": ["pressure", "explicit_composition"],
            "combat": {"aggression": 0.8},
            "composition_requirements": [
                {"unit_type": "TERRAN_MARINE", "count": 4, "role": "frontline"},
                {"unit_type": "TERRAN_SIEGETANK", "count": 1, "role": "siege_support"},
                {"unit_type": "TERRAN_VIKINGFIGHTER", "count": 1, "role": "anti_air"},
            ],
            "unit_roles": [
                {
                    "unit_type": "TERRAN_SIEGETANK",
                    "role": "siege_support",
                    "ability_policy": "if_available",
                },
                {
                    "unit_type": "TERRAN_VIKINGFIGHTER",
                    "role": "anti_air",
                    "ability_policy": "never",
                },
            ],
        }
        telemetry = _telemetry(frame=1_200, action=True, moved=True)
        telemetry["active_modulation_ids"] = ["qa-mixed-attack"]
        managers = telemetry["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        composition = managers["CompositionTask"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        assert isinstance(composition, dict)
        game["update_id"] = "qa-mixed-attack"
        combat["policy_update_id"] = "qa-mixed-attack"
        combat["main_attack_last_action_frame"] = 620
        composition["task_update_id"] = "qa-mixed-attack"
        composition["assigned_frame"] = 620
        composition["required_count"] = 6
        composition["assigned_count"] = 6
        managers["UnitRoleTask"] = {
            "task_update_id": "qa-mixed-attack",
            "unit_type": "TERRAN_SIEGETANK",
            "actor_tag": 101,
            "role": "siege_support",
            "ability_policy": "if_available",
            "status": "executed",
            "issued_action": "VoiRoleTankSiege|squad=MainAttack|type=4",
            "max_home_distance": 18.0,
            "attempted_count": 1,
            "executed_count": 1,
            "last_action_frame": 620,
        }
        viking_telemetry = {
            "frame": 621,
            "active_modulation_ids": ["qa-mixed-attack"],
            "managers": {
                "UnitRoleTask": {
                    "task_update_id": "qa-mixed-attack",
                    "unit_type": "TERRAN_VIKINGFIGHTER",
                    "actor_tag": 202,
                    "role": "anti_air",
                    "ability_policy": "never",
                    "status": "executed",
                    "issued_action": "MoveToGoalOrder|squad=MainAttack|type=2",
                    "max_home_distance": 19.0,
                    "attempted_count": 1,
                    "executed_count": 1,
                    "last_action_frame": 621,
                }
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("pressure",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            telemetry_archive=(viking_telemetry,),
            tactical_evidence=tactical,
            expected_tactical_effects=("pressure",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertTrue(report.ok, report.to_dict())
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("passed", scenarios["requested_combat_composition_attack"].status)
        self.assertEqual("passed", scenarios["special_unit_role_micro"].status)

    def test_canonical_task_frame_scopes_composition_and_unit_role_without_update_ids(
        self,
    ) -> None:
        update = _update()
        update["update_id"] = "qa-canonical-task-frame"
        update["issued_at_frame"] = 1_000
        update["vector"] = {
            "goal": "tank and viking attack",
            "tags": ["pressure", "explicit_composition"],
            "composition_requirements": [
                {"unit_type": "TERRAN_SIEGETANK", "count": 1, "role": "siege_support"}
            ],
            "unit_roles": [
                {
                    "unit_type": "TERRAN_SIEGETANK",
                    "role": "siege_support",
                    "ability_policy": "if_available",
                }
            ],
        }
        telemetry = _telemetry(frame=1_200, action=True, moved=True)
        telemetry["active_modulation_ids"] = ["qa-canonical-task-frame"]
        managers = telemetry["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        game["update_id"] = "qa-canonical-task-frame"
        combat["policy_update_id"] = "qa-canonical-task-frame"
        combat["main_attack_last_action_frame"] = 1_100
        managers["CompositionTask"] = {
            "active": True,
            "status": "assigned",
            "required_count": 1,
            "assigned_count": 1,
            "frame": 1_100,
        }
        managers["UnitRoleTask"] = {
            "active": True,
            "unit_type": "TERRAN_SIEGETANK",
            "actor_tag": 101,
            "role": "siege_support",
            "ability_policy": "if_available",
            "status": "executed",
            "issued_action": "VoiRoleTankSiege|squad=MainAttack|type=4",
            "max_home_distance": 18.0,
            "attempted_count": 1,
            "executed_count": 1,
            "frame": 1_100,
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

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("passed", scenarios["requested_combat_composition_attack"].status)
        self.assertEqual("passed", scenarios["special_unit_role_micro"].status)

    def test_fallback_scope_defaults_are_not_explicit_composition_requests(self) -> None:
        update = _update()
        update["update_id"] = "qa-default-pressure"
        update["vector"] = {
            "goal": "pressure the enemy",
            "tags": ["pressure"],
            "scope": {
                "unit_classes": [
                    "TERRAN_MARINE",
                    "TERRAN_MARAUDER",
                    "TERRAN_MEDIVAC",
                    "TERRAN_SIEGETANK",
                ],
                "min_units": 4,
            },
            "tactical_task": {
                "task_type": "pressure_with_main_army",
                "unit_classes": [
                    "TERRAN_MARINE",
                    "TERRAN_MARAUDER",
                    "TERRAN_MEDIVAC",
                    "TERRAN_SIEGETANK",
                ],
                "min_units": 4,
            },
        }
        telemetry = _telemetry(action=True, moved=True)
        telemetry["active_modulation_ids"] = ["qa-default-pressure"]
        managers = telemetry["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        composition = managers["CompositionTask"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        assert isinstance(composition, dict)
        game["update_id"] = "qa-default-pressure"
        combat["policy_update_id"] = "qa-default-pressure"
        composition["task_update_id"] = "qa-default-pressure"
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

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["requested_combat_composition_attack"].status)

    def test_missing_explicit_composition_prevents_command_completion(self) -> None:
        update = _update()
        update["update_id"] = "qa-incomplete-mixed-attack"
        update["issued_at_frame"] = 100
        update["vector"] = {
            "goal": "attack with four marines, tank, and viking",
            "tags": ["pressure", "explicit_composition"],
            "tactical_task": {
                "task_type": "pressure_with_main_army",
                "unit_classes": [
                    "TERRAN_MARINE",
                    "TERRAN_SIEGETANK",
                    "TERRAN_VIKINGFIGHTER",
                ],
                "min_units": 6,
            },
            "composition_requirements": [
                {"unit_type": "TERRAN_MARINE", "count": 4},
                {"unit_type": "TERRAN_SIEGETANK", "count": 1},
                {"unit_type": "TERRAN_VIKINGFIGHTER", "count": 1},
            ],
        }
        telemetry = _telemetry(frame=1_200, action=True, moved=True)
        telemetry["active_modulation_ids"] = ["qa-incomplete-mixed-attack"]
        managers = telemetry["managers"]
        assert isinstance(managers, dict)
        game = managers["GameCommander"]
        combat = managers["CombatCommander"]
        composition = managers["CompositionTask"]
        assert isinstance(game, dict)
        assert isinstance(combat, dict)
        assert isinstance(composition, dict)
        game["update_id"] = "qa-incomplete-mixed-attack"
        combat["policy_update_id"] = "qa-incomplete-mixed-attack"
        composition.update(
            {
                "task_update_id": "qa-incomplete-mixed-attack",
                "status": "partial",
                "required_count": 6,
                "assigned_count": 4,
                "missing": "SiegeTank,Viking",
            }
        )
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
        self.assertTrue(report.failed)
        self.assertEqual("failed", report.state)
        self.assertEqual("CompositionTask", report.blocker_manager)
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["requested_combat_composition_attack"].status)

    def test_special_unit_role_micro_requires_unit_role_task_action(self) -> None:
        update = _update()
        update["update_id"] = "qa-banshee-role"
        update["vector"] = {
            "goal": "banshee worker harass",
            "tags": ["harass", "explicit_composition"],
            "unit_roles": [
                {
                    "unit_type": "TERRAN_BANSHEE",
                    "role": "worker_harass",
                    "ability_policy": "if_available",
                }
            ],
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-banshee-role"],
            "managers": {
                "GameCommander": {"update_id": "qa-banshee-role"},
                "UnitRoleTask": {
                    "task_update_id": "qa-banshee-role",
                    "unit_type": "TERRAN_BANSHEE",
                    "role": "worker_harass",
                    "ability_policy": "if_available",
                    "status": "pending",
                    "attempted_count": 0,
                    "executed_count": 0,
                    "last_action_frame": 620,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=1_200,
            target_frame=1_000,
        )

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["special_unit_role_micro"].status)

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

    def test_air_and_capital_production_scenarios_cover_non_marine_units(self) -> None:
        update = _update()
        update["update_id"] = "qa-battlecruiser-chain"
        update["manager_bias_domains"] = ["production", "tech", "unit_roles"]
        update["vector"] = {
            "goal": "produce battlecruiser",
            "tags": ["capital_air"],
            "production_plan": {
                "targets": ["TERRAN_STARPORT", "TERRAN_FUSIONCORE", "TERRAN_BATTLECRUISER"],
                "allow_prerequisite_buildings": True,
            },
            "unit_roles": [
                {
                    "unit_type": "TERRAN_BATTLECRUISER",
                    "role": "capital_ship",
                    "ability_policy": "high_value_target",
                }
            ],
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["qa-battlecruiser-chain"],
            "managers": {
                "GameCommander": {"update_id": "qa-battlecruiser-chain"},
                "ProductionManager": {
                    "bounded_intervention": True,
                    "policy_update_id": "qa-battlecruiser-chain",
                    "last_doctrine_action": "battlecruiser_transition",
                    "last_doctrine_queue_item": "Battlecruiser",
                    "actual_production_command_issued_count": 1,
                    "last_actual_production_command": "train_command|Battlecruiser",
                    "last_actual_production_command_item": "Battlecruiser",
                    "last_actual_production_command_update_id": "qa-battlecruiser-chain",
                    "last_actual_production_command_frame": 620,
                },
            },
        }

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            expected_production_items=("Battlecruiser",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        self.assertTrue(report.ok, report.to_dict())
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("passed", scenarios["air_support_production_prerequisite_chain"].status)
        self.assertEqual(
            "passed",
            scenarios["capital_air_production_prerequisite_chain"].status,
        )

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
                    "scout_marine_assigned_count": 1,
                    "scout_marine_home_distance": 11.0,
                    "scout_marine_max_home_distance": 14.0,
                    "scout_last_commanded_unit_tag": 4242,
                    "scout_last_commanded_unit_type": "Terran_Marine",
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

    def test_non_marine_combat_scout_does_not_satisfy_marine_scout(self) -> None:
        update = _update()
        update["update_id"] = "current"
        update["issued_at_frame"] = 1_000
        update["vector"] = {
            "goal": "scout enemy main with one marine",
            "tags": ["scouting_map_control", "single_unit_scout"],
            "tactical_task": {
                "task_type": "scout_with_units",
                "unit_classes": ["TERRAN_MARINE"],
                "location_intent": "enemy_main",
                "min_units": 1,
                "max_units": 1,
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["current"],
            "managers": {
                "GameCommander": {"update_id": "current"},
                "CombatCommander": {
                    "policy_update_id": "current",
                    "scout_actual_command_issued_count": 1,
                    "scout_last_action_frame": 1_100,
                    "scout_last_issued_action": (
                        "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
                    ),
                    "scout_scope_assigned_unit_count": 1,
                    "scout_marine_assigned_count": 0,
                    "scout_max_home_distance": 14.0,
                    "scout_marine_max_home_distance": 0.0,
                    "scout_last_commanded_unit_tag": 5151,
                    "scout_last_commanded_unit_type": "TERRAN_REAPER",
                },
            },
        }
        tactical = MicroMachineTacticalEvidence(
            status="passed",
            observed_effects=("scout",),
            missing_effects=(),
            expected_effects=("scout",),
            latest_frame=1_200,
            effects=(
                MicroMachineTacticalEffect(
                    tag="scout",
                    source="test",
                    detail="generic scout movement",
                    frame=1_100,
                    manager="CombatCommander",
                ),
            ),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("scout",),
            latest_frame=1_200,
            target_frame=1_000,
        )

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["marine_scout"].status)
        self.assertFalse(report.ok, report.to_dict())
        self.assertTrue(report.failed)
        self.assertEqual("CombatCommander", report.blocker_manager)

    def test_exact_marine_scout_rejects_partial_assignment(self) -> None:
        update = {
            "update_id": "scout-three",
            "issued_at_frame": 1_000,
            "expires_at_frame": 2_000,
            "manager_bias_domains": ["scouting", "scope", "tactical_task"],
            "vector": {
                "goal": "scout with exactly three marines",
                "tactical_task": {
                    "task_type": "scout_with_units",
                    "unit_classes": ["TERRAN_MARINE"],
                    "min_units": 3,
                    "max_units": 3,
                },
                "scope": {
                    "unit_classes": ["TERRAN_MARINE"],
                    "min_units": 3,
                    "max_units": 3,
                },
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["scout-three"],
            "managers": {
                "GameCommander": {"update_id": "scout-three", "frame": 1_100},
                "CombatCommander": {
                    "policy_update_id": "scout-three",
                    "scout_actual_command_issued_count": 1,
                    "scout_last_action_frame": 1_100,
                    "scout_last_issued_action": (
                        "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
                    ),
                    "scout_scope_requested_min_units": 3,
                    "scout_scope_requested_max_units": 3,
                    "scout_marine_assigned_count": 2,
                    "scout_marine_max_home_distance": 14.0,
                    "scout_last_commanded_unit_tag": 4242,
                    "scout_last_commanded_unit_type": "TERRAN_MARINE",
                },
            },
        }
        tactical = MicroMachineTacticalEvidence(
            status="passed",
            observed_effects=("scout",),
            missing_effects=(),
            expected_effects=("scout",),
            latest_frame=1_200,
            effects=(
                MicroMachineTacticalEffect(
                    tag="scout",
                    source="test",
                    detail="scout_last_action_frame: 1100",
                    frame=1_100,
                    manager="CombatCommander",
                ),
            ),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            expected_tactical_effects=("scout",),
            latest_frame=1_200,
            target_frame=1_100,
        )

        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["marine_scout"].status)
        self.assertEqual(
            3,
            scenarios["marine_scout"].details["requested_scout_count"],
        )
        self.assertFalse(report.ok, report.to_dict())

    def test_generic_ability_completes_only_after_current_sc2_confirmation(
        self,
    ) -> None:
        update = {
            "update_id": "ability-current",
            "issued_at_frame": 1_000,
            "expires_at_frame": 30_000,
            "manager_bias_domains": ["tactical_task", "unit_roles"],
            "vector": {
                "goal": "use stimpack",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "stimpack",
                },
                "unit_roles": [
                    {
                        "unit_type": "TERRAN_MARINE",
                        "role": "execute_ability",
                        "ability_policy": "stimpack",
                    }
                ],
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["ability-current"],
            "managers": {
                "GameCommander": {"update_id": "ability-current"},
                "AbilityTask": {
                    "update_id": "ability-current",
                    "task_type": "execute_ability",
                    "ability": "stimpack",
                    "status": "executing",
                    "submitted_count": 1,
                    "last_action": "VoiExplicitAbility:stimpack",
                    "submission_frame": 1_100,
                    "confirmation_state": "pending",
                    "confirmation_count": 0,
                    "confirmation_frame": 0,
                    "confirmation_effect": "",
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertFalse(report.ok, report.to_dict())
        stages = {stage.name: stage for stage in report.stages}
        self.assertTrue(stages["order_issued"].ok)
        self.assertTrue(stages["action_issued"].ok)
        self.assertFalse(stages["effect_observed"].ok)
        self.assertEqual("AbilityTask", stages["effect_observed"].manager)

        telemetry["managers"]["AbilityTask"] = {
            "update_id": "ability-current",
            "task_type": "execute_ability",
            "ability": "stimpack",
            "status": "completed",
            "phase": "effect_observed",
            "attempt_generation": 3,
            "submitted_attempt_generation": 3,
            "terminal_attempt_generation": 3,
            "submitted_count": 1,
            "last_actual_command": "VoiExplicitAbility:stimpack",
            "last_actual_command_frame": 1_100,
            "confirmation_state": "confirmed",
            "confirmation_count": 1,
            "confirmation_frame": 1_120,
            "confirmation_effect": "actor_buff:STIMPACK",
        }
        confirmed_tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )
        confirmed_report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=confirmed_tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertTrue(confirmed_report.ok, confirmed_report.to_dict())
        self.assertEqual("completed", confirmed_report.state)

    def test_generic_ability_accepts_observed_accepted_terminal(self) -> None:
        update = {
            "update_id": "ability-current",
            "issued_at_frame": 1_000,
            "expires_at_frame": 30_000,
            "manager_bias_domains": ["tactical_task", "unit_roles"],
            "vector": {
                "goal": "lock on",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "lock_on",
                },
                "unit_roles": [
                    {
                        "unit_type": "TERRAN_CYCLONE",
                        "role": "execute_ability",
                        "ability_policy": "lock_on",
                    }
                ],
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["ability-current"],
            "managers": {
                "GameCommander": {"update_id": "ability-current"},
                "AbilityTask": {
                    "update_id": "ability-current",
                    "task_type": "execute_ability",
                    "ability": "lock_on",
                    "status": "completed",
                    "phase": "observed_accepted",
                    "attempt_generation": 4,
                    "submitted_attempt_generation": 4,
                    "observed_accepted_attempt_generation": 4,
                    "terminal_attempt_generation": 4,
                    "submitted_count": 1,
                    "last_action": (
                        "VoiExplicitAbility:lock_on|ability=EFFECT_LOCKON"
                    ),
                    "submission_frame": 1_100,
                    "observed_accepted_frame": 1_104,
                    "observed_accepted_evidence": (
                        "actor_order:EFFECT_LOCKON"
                    ),
                    "confirmation_state": "accepted",
                    "confirmation_count": 0,
                    "confirmation_frame": 0,
                    "confirmation_effect": "actor_order:EFFECT_LOCKON",
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual("completed", report.state)

    def test_generic_ability_accepts_already_satisfied_terminal(self) -> None:
        update = {
            "update_id": "ability-current",
            "issued_at_frame": 1_000,
            "expires_at_frame": 30_000,
            "manager_bias_domains": ["tactical_task", "unit_roles"],
            "vector": {
                "goal": "fighter mode",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "viking_fighter_mode",
                },
                "unit_roles": [
                    {
                        "unit_type": "TERRAN_VIKINGFIGHTER",
                        "role": "execute_ability",
                        "ability_policy": "viking_fighter_mode",
                    }
                ],
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["ability-current"],
            "managers": {
                "GameCommander": {"update_id": "ability-current"},
                "AbilityTask": {
                    "update_id": "ability-current",
                    "task_type": "execute_ability",
                    "ability": "viking_fighter_mode",
                    "status": "completed",
                    "phase": "effect_observed",
                    "attempt_generation": 5,
                    "submitted_attempt_generation": 0,
                    "terminal_attempt_generation": 5,
                    "submitted_count": 0,
                    "last_action": "",
                    "submission_frame": 0,
                    "confirmation_state": "confirmed",
                    "confirmation_count": 1,
                    "confirmation_frame": 1_120,
                    "confirmation_effect": (
                        "already_satisfied:"
                        "unit_type:TERRAN_VIKINGFIGHTER"
                    ),
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertTrue(report.ok, report.to_dict())
        stages = {stage.name: stage for stage in report.stages}
        self.assertTrue(stages["order_issued"].ok)
        self.assertTrue(stages["action_issued"].ok)
        self.assertIn(
            "already satisfied",
            stages["action_issued"].reason,
        )

    def test_generic_ability_rejects_confirmed_stale_update(self) -> None:
        update = {
            "update_id": "ability-current",
            "issued_at_frame": 1_000,
            "expires_at_frame": 30_000,
            "manager_bias_domains": ["tactical_task"],
            "vector": {
                "goal": "use stimpack",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "stimpack",
                },
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["ability-current"],
            "managers": {
                "GameCommander": {"update_id": "ability-current"},
                "AbilityTask": {
                    "update_id": "ability-stale",
                    "task_type": "execute_ability",
                    "ability": "stimpack",
                    "status": "completed",
                    "phase": "effect_observed",
                    "attempt_generation": 3,
                    "submitted_attempt_generation": 3,
                    "terminal_attempt_generation": 3,
                    "submitted_count": 1,
                    "last_action": "VoiExplicitAbility:stimpack",
                    "submission_frame": 1_100,
                    "confirmation_state": "confirmed",
                    "confirmation_count": 1,
                    "confirmation_frame": 1_120,
                    "confirmation_effect": "actor_buff:STIMPACK",
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertFalse(tactical.ok, tactical.to_dict())
        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("AbilityTask", report.blocker_manager)

    def test_generic_ability_rejects_unrelated_confirmation_effect(self) -> None:
        update = {
            "update_id": "ability-current",
            "issued_at_frame": 1_000,
            "expires_at_frame": 30_000,
            "manager_bias_domains": ["tactical_task"],
            "vector": {
                "goal": "use stimpack",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "stimpack",
                },
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["ability-current"],
            "managers": {
                "GameCommander": {"update_id": "ability-current"},
                "AbilityTask": {
                    "update_id": "ability-current",
                    "task_type": "execute_ability",
                    "ability": "stimpack",
                    "status": "completed",
                    "submitted_count": 1,
                    "last_action": (
                        "VoiExplicitAbility:stimpack|ability=EFFECT_STIM"
                    ),
                    "submission_frame": 1_100,
                    "confirmation_state": "confirmed",
                    "confirmation_count": 1,
                    "confirmation_frame": 1_120,
                    "confirmation_effect": (
                        "unit_type:TERRAN_SIEGETANKSIEGED"
                    ),
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertFalse(tactical.ok, tactical.to_dict())
        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("AbilityTask", report.blocker_manager)

    def test_tactical_nuke_completes_only_after_current_sc2_confirmation(
        self,
    ) -> None:
        update = {
            "update_id": "nuke-current",
            "issued_at_frame": 1_000,
            "expires_at_frame": 30_000,
            "manager_bias_domains": [
                "production",
                "tactical_task",
                "unit_roles",
            ],
            "vector": {
                "goal": "launch a tactical nuke",
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "tactical_nuke",
                    "production_targets": ["TERRAN_NUKE"],
                    "duration_seconds": 0,
                },
                "unit_roles": [
                    {
                        "unit_type": "TERRAN_GHOST",
                        "role": "execute_ability",
                        "ability_policy": "tactical_nuke",
                    }
                ],
            },
        }
        telemetry = {
            "frame": 1_200,
            "active_modulation_ids": ["nuke-current"],
            "managers": {
                "AbilityTask": {
                    "update_id": "nuke-current",
                    "ability": "tactical_nuke",
                    "status": "confirming",
                    "cast_submitted_count": 1,
                    "cast_submitted_action": (
                        "VoiRoleGhostTacticalNuke|squad=|type=6|"
                        "ability=EFFECT_NUKECALLDOWN|x=80|y=80"
                    ),
                    "cast_submission_frame": 1_100,
                    "confirmation_state": "pending",
                    "confirmation_count": 0,
                    "confirmation_frame": 0,
                    "confirmation_effect": "",
                },
            },
        }
        tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )

        report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertFalse(report.ok, report.to_dict())
        self.assertEqual("AbilityTask", report.blocker_manager)
        scenarios = {item.name: item for item in report.scenarios}
        self.assertEqual("missing", scenarios["tactical_nuke_ability_cast"].status)

        telemetry["managers"]["AbilityTask"] = {
            "update_id": "nuke-current",
            "ability": "tactical_nuke",
            "status": "confirmed",
            "cast_submitted_count": 1,
            "cast_submitted_action": (
                "VoiRoleGhostTacticalNuke|squad=|type=6|"
                "ability=EFFECT_NUKECALLDOWN|x=80|y=80"
            ),
            "cast_submission_frame": 1_100,
            "confirmation_state": "confirmed",
            "confirmation_count": 1,
            "confirmation_frame": 1_120,
            "confirmation_effect": "payload_consumed:TERRAN_NUKE",
        }
        confirmed_tactical = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            expected_effects=("ability_cast",),
        )
        confirmed_report = classify_micromachine_command_execution(
            latest_update=update,
            latest_telemetry=telemetry,
            tactical_evidence=confirmed_tactical,
            latest_frame=1_200,
            target_frame=1_100,
        )

        self.assertTrue(confirmed_report.ok, confirmed_report.to_dict())
        confirmed_scenarios = {
            item.name: item for item in confirmed_report.scenarios
        }
        self.assertEqual(
            "passed",
            confirmed_scenarios["tactical_nuke_ability_cast"].status,
        )


if __name__ == "__main__":
    unittest.main()
