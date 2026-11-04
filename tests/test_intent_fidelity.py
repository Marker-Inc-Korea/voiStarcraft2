"""Regression tests for intent-fidelity defects found by the offline chain replay.

Every test in the "defect regressions" section was written red first against the
unfixed code (see docs/intent-fidelity-audit.md for the red->green log).  The last
section drives the whole fixture corpus in ``scripts/fidelity/replay_chain.py`` and
asserts that the invariant violations equal the documented handoff set exactly.
"""

from __future__ import annotations

import tempfile
import unittest

from starcraft_commander.demo_sc2 import build_dry_run_session
from starcraft_commander.micromachine_command_execution import (
    classify_micromachine_operation_executions,
)
from starcraft_commander.micromachine_live_session import (
    LiveModulationStatus,
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
)
from starcraft_commander.micromachine_runtime import MicroMachineFilesystemBlackboard
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileStatus,
    compile_policy_modulation_provider_output,
)


def _operation(operation_id, task_type, location, count, *, edit=None, lifetime=None):
    operation = {
        "operation_id": operation_id,
        "tactical_task": {
            "task_type": task_type,
            "unit_classes": ["TERRAN_MARINE"],
            "location_intent": location,
        },
        "composition_requirements": [
            {"unit_type": "TERRAN_MARINE", "count": count, "role": "frontline"}
        ],
    }
    if edit is not None:
        operation["operation_edit"] = edit
    if lifetime is not None:
        operation["lifetime"] = lifetime
    return operation


def _operation_output(*operations):
    return {"goal": "fidelity", "command_layer": "operation", "operations": list(operations)}


class _DirectHarness:
    """Real Direct path: live session + SC2RuntimeExecutor over the scripted bot."""

    def __init__(self) -> None:
        self.session, self.bot = build_dry_run_session()
        self.executor = self.session.executor
        self.lifecycle = self.executor.direct_lifecycle
        self._directory = tempfile.TemporaryDirectory()
        self.backend = MicroMachineFilesystemBlackboard(self._directory.name)
        # DemoFakeBotAI units (demo_sc2.py:155) implement move/attack but not
        # stop/hold_position, so the scripted bot refuses every emergency
        # stop as "insufficient_units" (audit handoff H-7).  The harness adds
        # the two verbs locally so lease preemption itself can be tested.
        for unit in self.bot.units:
            unit.stop = lambda *a, _u=unit, **k: _u._record("stop", None)
            unit.hold_position = lambda *a, _u=unit, **k: _u._record("hold", None)

    def close(self) -> None:
        self._directory.cleanup()

    def submit(self, text, output, *, frame, update_id):
        live = MicroMachineLiveTextSession(
            self.backend,
            StaticJsonPolicyModulationProvider(output),
            direct_executor=self.executor,
        )
        return live.submit_text(text, current_frame=frame, update_id=update_id)

    def lease_states(self):
        return {
            command_id: (lease.state.value, lease.release_reason)
            for command_id, lease in self.lifecycle._leases.items()
        }


class _DirectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _DirectHarness()
        self.addCleanup(self.harness.close)


# ---------------------------------------------------------------------------
# Defect regressions
# ---------------------------------------------------------------------------


class UnitCountDowngradeTest(unittest.TestCase):
    """D1: a count the user asked for must never be dropped or clamped silently."""

    def _scout_output(self, count):
        return {
            "goal": "마린 정찰",
            "command_layer": "operation",
            "operations": [
                {
                    "operation_id": "scout-a",
                    "tactical_task": {
                        "task_type": "scout_with_units",
                        "unit_classes": ["TERRAN_MARINE"],
                        "location_intent": "enemy_main",
                    },
                    "composition_requirements": [
                        {"unit_type": "TERRAN_MARINE", "count": count, "role": "scout"}
                    ],
                }
            ],
        }

    def test_unrepresentable_counts_are_refused_with_a_reason(self) -> None:
        for count in ("2", 0, -1, None, True, 2.5, 300):
            with self.subTest(count=count):
                result = compile_policy_modulation_provider_output(
                    self._scout_output(count)
                )
                self.assertIs(result.status, PolicyModulationCompileStatus.REFUSED)
                self.assertIsNone(result.vector)
                self.assertIn("count", result.refusal_reason)

    def test_valid_counts_still_compile_exactly(self) -> None:
        for count, expected in ((2, 2), (2.0, 2), (200, 200)):
            with self.subTest(count=count):
                result = compile_policy_modulation_provider_output(
                    self._scout_output(count)
                )
                self.assertTrue(result.ok, result.to_dict())
                task = result.vector.operations[0].tactical_task
                self.assertEqual(expected, task.min_units)
                self.assertEqual(
                    expected,
                    result.vector.operations[0].composition_requirements[0].count,
                )


class DirectOperationCancelTest(_DirectCase):
    """D2: "cancel operation X" must release X, never issue a new attack order."""

    def test_cancel_edit_releases_lease_and_issues_no_order(self) -> None:
        first = self.harness.submit(
            "마린 2기 공격",
            _operation_output(
                _operation("atk", "pressure_with_main_army", "enemy_natural", 2)
            ),
            frame=100,
            update_id="cancel-first",
        )
        self.assertIs(first.status, LiveModulationStatus.DIRECT_EXECUTED, first.to_dict())
        orders_before = len(self.harness.bot.issued_commands)
        self.assertEqual("active", self.harness.lease_states()["cancel-first"][0])

        cancel = self.harness.submit(
            "공격 취소해",
            _operation_output(
                _operation(
                    "atk",
                    "pressure_with_main_army",
                    "enemy_natural",
                    2,
                    edit={"action": "cancel"},
                    lifetime={
                        "mode": "until_completed",
                        "completion_state": "cancelled",
                        "completion_conditions": ["cancelled_by_user"],
                    },
                )
            ),
            frame=110,
            update_id="cancel-second",
        )

        self.assertEqual(
            orders_before,
            len(self.harness.bot.issued_commands),
            "cancel must not issue any SC2 order",
        )
        self.assertEqual(
            ("cancelled", "cancelled_by_user"),
            self.harness.lease_states()["cancel-first"],
        )
        self.assertIs(cancel.status, LiveModulationStatus.DIRECT_EXECUTED, cancel.to_dict())
        names = [item["name"] for item in cancel.tool_results]
        self.assertEqual(["sc2.direct.release"], names)
        self.assertEqual(
            ["cancel-first"],
            cancel.tool_results[0]["result"]["released_command_ids"],
        )

    def test_cancel_of_unknown_operation_is_refused_not_executed(self) -> None:
        cancel = self.harness.submit(
            "정찰 취소",
            _operation_output(
                _operation(
                    "ghost-op",
                    "scout_with_units",
                    "enemy_main",
                    1,
                    edit={"action": "cancel"},
                    lifetime={
                        "mode": "until_completed",
                        "completion_state": "cancelled",
                        "completion_conditions": ["cancelled_by_user"],
                    },
                )
            ),
            frame=100,
            update_id="cancel-unknown",
        )
        self.assertIs(cancel.status, LiveModulationStatus.DIRECT_FAILED)
        self.assertFalse(cancel.ok)
        self.assertEqual(0, len(self.harness.bot.issued_commands))
        self.assertEqual(
            "no_active_direct_operation",
            cancel.tool_results[0]["result"]["status"],
        )


class DirectOperationRetargetTest(_DirectCase):
    """D3: retargeting an operation must not be refused by that operation's own lease."""

    def test_retarget_edit_supersedes_own_lease_then_dispatches(self) -> None:
        self.harness.submit(
            "마린 2기 공격",
            _operation_output(
                _operation("atk", "pressure_with_main_army", "enemy_natural", 2)
            ),
            frame=100,
            update_id="retarget-first",
        )
        orders_before = len(self.harness.bot.issued_commands)

        retarget = self.harness.submit(
            "본진으로 목표 변경",
            _operation_output(
                _operation(
                    "atk",
                    "pressure_with_main_army",
                    "enemy_main",
                    2,
                    edit={"action": "retarget"},
                )
            ),
            frame=120,
            update_id="retarget-second",
        )

        self.assertIs(retarget.status, LiveModulationStatus.DIRECT_EXECUTED, retarget.to_dict())
        states = self.harness.lease_states()
        self.assertEqual("cancelled", states["retarget-first"][0])
        self.assertEqual("superseded_by:retarget-second", states["retarget-first"][1])
        self.assertEqual("active", states["retarget-second"][0])
        self.assertEqual(orders_before + 2, len(self.harness.bot.issued_commands))

    def test_same_operation_id_without_edit_is_still_a_conflict(self) -> None:
        output = _operation_output(
            _operation("atk", "pressure_with_main_army", "enemy_natural", 2)
        )
        self.harness.submit("공격", output, frame=100, update_id="noedit-first")
        again = self.harness.submit("또 공격", output, frame=101, update_id="noedit-second")
        self.assertIs(again.status, LiveModulationStatus.DIRECT_FAILED)
        self.assertEqual("direct_control_conflict", again.tool_results[0]["result"]["status"])
        self.assertEqual("active", self.harness.lease_states()["noedit-first"][0])


class EmergencyPreemptionTest(_DirectCase):
    """D4: emergency retreat must preempt combat leases, and only those."""

    def test_emergency_releases_group_leases_but_not_worker_jobs(self) -> None:
        self.harness.submit(
            "마린 2기 공격",
            _operation_output(
                _operation("atk", "pressure_with_main_army", "enemy_natural", 2)
            ),
            frame=100,
            update_id="em-attack",
        )
        self.harness.lifecycle.pending(
            command_id="em-worker-job",
            issued_at_frame=100,
            ttl_seconds=120,
            owned_unit_tags=(1,),
            command_metadata={
                "actions": [
                    {
                        "action_type": "build_structure",
                        "subject": "SUPPLYDEPOT",
                        "target": "self_main",
                        "count": 1,
                        "metadata": {},
                    }
                ]
            },
        )

        emergency = self.harness.submit(
            "즉시 후퇴",
            {
                "goal": "즉시 후퇴",
                "override_level": "emergency",
                "ttl_seconds": 45,
                "emergency": {"force_retreat": True, "cancel_attacks": True},
            },
            frame=120,
            update_id="em-retreat",
        )

        self.assertIs(emergency.status, LiveModulationStatus.DIRECT_EXECUTED, emergency.to_dict())
        states = self.harness.lease_states()
        self.assertEqual(("cancelled", "emergency_preempted:em-retreat"), states["em-attack"])
        self.assertEqual("pending", states["em-worker-job"][0])
        self.assertEqual("active", states["em-retreat"][0])

    def test_emergency_with_no_conflicting_lease_is_unchanged(self) -> None:
        emergency = self.harness.submit(
            "후퇴",
            {
                "goal": "후퇴",
                "override_level": "emergency",
                "ttl_seconds": 45,
                "emergency": {"force_retreat": True},
            },
            frame=100,
            update_id="em-solo",
        )
        self.assertIs(emergency.status, LiveModulationStatus.DIRECT_EXECUTED)
        self.assertEqual({"em-solo": ("active", "")}, self.harness.lease_states())


class DirectPartialFulfillmentTest(_DirectCase):
    """D5: a shortfall must be visible in a user-displayable field, not only in audit."""

    def test_partial_issuance_is_surfaced_in_command_queue(self) -> None:
        result = self.harness.submit(
            "마린 10기 공격",
            _operation_output(
                _operation("big", "pressure_with_main_army", "enemy_natural", 10)
            ),
            frame=100,
            update_id="partial-1",
        )
        self.assertFalse(result.ok)
        outcome = result.command_queue["direct_outcome"]
        self.assertEqual("partial", outcome["status"])
        self.assertEqual(
            [
                {
                    "action_index": 0,
                    "action_type": "attack_move",
                    "requested": 10,
                    "issued": 6,
                    "detail": "insufficient_units",
                }
            ],
            outcome["shortfalls"],
        )
        self.assertTrue(
            any("only 6 of 10" in reason for reason in outcome["reasons"]),
            outcome,
        )

    def test_successful_direct_execution_reports_executed_without_reasons(self) -> None:
        result = self.harness.submit(
            "마린 2기 공격",
            _operation_output(
                _operation("ok", "pressure_with_main_army", "enemy_natural", 2)
            ),
            frame=100,
            update_id="partial-ok",
        )
        outcome = result.command_queue["direct_outcome"]
        self.assertEqual("executed", outcome["status"])
        self.assertEqual([], outcome["shortfalls"])
        self.assertEqual([], outcome["reasons"])

    def test_refusal_reason_is_surfaced_for_runtime_not_attached(self) -> None:
        live = MicroMachineLiveTextSession(
            MicroMachineFilesystemBlackboard(tempfile.mkdtemp()),
            StaticJsonPolicyModulationProvider(
                _operation_output(
                    _operation("na", "scout_with_units", "enemy_main", 1)
                )
            ),
        )
        result = live.submit_text("정찰", current_frame=10, update_id="na-1")
        outcome = result.command_queue["direct_outcome"]
        self.assertEqual("runtime_not_attached", outcome["status"])
        self.assertTrue(outcome["reasons"])


class ObjectiveCompleteIsNotFailureTest(unittest.TestCase):
    """D6: a COMPLETED operation (objective observed) must not be reported failed."""

    def test_completed_operation_with_objective_blocker_is_not_failed(self) -> None:
        update_id = "obj-complete"
        update = {
            "update_id": update_id,
            "issued_at_frame": 100,
            "expires_at_frame": 5_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "defend-a",
                        "generation": 1,
                        "composition_requirements": [
                            {"unit_type": "TERRAN_MARINE", "count": 2, "role": "frontline"}
                        ],
                        "tactical_task": {
                            "task_type": "defend_with_units",
                            "unit_classes": ["TERRAN_MARINE"],
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 400,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": "defend-a",
                            "generation": 1,
                            "requested_task_type": "defend_with_units",
                            "task_type": "defend",
                            "squad_order": "defend",
                            "status": "COMPLETED",
                            "blocked_reason": "objective_observed_complete",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 135,
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                            "max_home_distance": 6.7,
                            "engaged": False,
                            "completed": True,
                            "family_evidence": [
                                {
                                    "update_id": update_id,
                                    "operation_id": "defend-a",
                                    "generation": 1,
                                    "family": "marine",
                                    "unit_type": "TERRAN_MARINE",
                                    "role": "frontline",
                                    "assigned": 1,
                                    "represented": 1,
                                    "action": "attack_move",
                                    "required_effect": "movement_or_engagement",
                                    "attempt_generation": 1,
                                    "attempted_count": 1,
                                    "attempted_unit_tags": [4_000_000_001],
                                    "attempted_frame": 130,
                                    "submitted_count": 1,
                                    "submitted_unit_tags": [4_000_000_001],
                                    "submitted_frame": 130,
                                    "effect_kind": "",
                                    "effect_count": 0,
                                    "effect_unit_tags": [],
                                    "effect_frame": 0,
                                    "blocker_manager": "OperationDirector",
                                    "blocker": "objective_observed_complete",
                                }
                            ],
                        }
                    ],
                }
            },
        }
        (report,) = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=400,
        )
        self.assertFalse(report.failed, report.to_dict())
        self.assertFalse(report.expired)
        self.assertFalse(report.completed, "effect evidence is incomplete; do not overclaim")
        self.assertEqual("objective_observed_complete", report.state)
        self.assertEqual("", report.blocker_reason)

    def test_genuine_family_block_still_fails(self) -> None:
        update_id = "real-block"
        update = {
            "update_id": update_id,
            "issued_at_frame": 100,
            "expires_at_frame": 5_000,
            "vector": {
                "operations": [
                    {
                        "operation_id": "defend-b",
                        "generation": 1,
                        "composition_requirements": [
                            {"unit_type": "TERRAN_MARINE", "count": 1, "role": "frontline"}
                        ],
                        "tactical_task": {
                            "task_type": "defend_with_units",
                            "unit_classes": ["TERRAN_MARINE"],
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 400,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": "defend-b",
                            "generation": 1,
                            "requested_task_type": "defend_with_units",
                            "task_type": "defend",
                            "squad_order": "defend",
                            "status": "BLOCKED",
                            "blocked_reason": "invalid_target",
                            "received_frame": 110,
                            "assigned_frame": 0,
                            "submitted_frame": 0,
                            "last_action_frame": 0,
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                            "family_evidence": [],
                        }
                    ],
                }
            },
        }
        (report,) = classify_micromachine_operation_executions(
            latest_update=update,
            latest_telemetry=telemetry,
            latest_frame=400,
        )
        self.assertTrue(report.failed)
        self.assertEqual("invalid_target", report.blocker_reason)


class AssignedShortfallIsSurfacedTest(unittest.TestCase):
    """D7: asking for N units and getting fewer must be visible on the report."""

    def _classify(self, *, requested, assigned_tags, allow_partial):
        update = {
            "update_id": "short-1",
            "issued_at_frame": 100,
            "expires_at_frame": 9_999,
            "vector": {
                "operations": [
                    {
                        "operation_id": "atk",
                        "generation": 1,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "min_units": requested,
                            "max_units": requested,
                            "allow_partial": allow_partial,
                            "duration_seconds": 300,
                        },
                        "scope": {
                            "min_units": requested,
                            "max_units": requested,
                            "allow_partial_scope": allow_partial,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 500,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "short-1",
                    "operations": [
                        {
                            "operation_id": "atk",
                            "generation": 1,
                            "requested_task_type": "pressure_with_main_army",
                            "task_type": "attack",
                            "squad_order": "attack",
                            "status": "MOVING",
                            "received_frame": 110,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 140,
                            "assigned_unit_tags": list(assigned_tags),
                            "assigned_count": len(assigned_tags),
                            "max_home_distance": 30.0,
                        }
                    ],
                }
            },
        }
        (report,) = classify_micromachine_operation_executions(
            latest_update=update, latest_telemetry=telemetry, latest_frame=500
        )
        return report

    def test_four_requested_one_assigned_reports_a_shortfall(self) -> None:
        report = self._classify(requested=4, assigned_tags=(11,), allow_partial=False)
        shortfall = report.to_dict()["shortfall"]
        self.assertEqual(4, shortfall["requested"])
        self.assertEqual(1, shortfall["assigned"])
        self.assertEqual(3, shortfall["missing"])
        self.assertFalse(shortfall["allow_partial"])
        self.assertIn("1 of 4", shortfall["reason"])

    def test_full_assignment_reports_no_shortfall(self) -> None:
        report = self._classify(
            requested=2, assigned_tags=(11, 12), allow_partial=False
        )
        self.assertEqual({}, report.to_dict()["shortfall"])

    def test_allowed_partial_still_surfaces_the_numbers(self) -> None:
        report = self._classify(requested=4, assigned_tags=(11, 12), allow_partial=True)
        shortfall = report.to_dict()["shortfall"]
        self.assertEqual((4, 2, 2), (shortfall["requested"], shortfall["assigned"], shortfall["missing"]))
        self.assertTrue(shortfall["allow_partial"])


class WaitingOperationIsNotARoutingFailureTest(unittest.TestCase):
    """D8: a unit-starved op waiting with squad_order=idle is waiting, not failed.

    Recorded QA30 scenarios 16-defense-reinforcement and 27-prerequisite-wait
    carry exactly this telemetry (status WAITING_FOR_UNITS, squad_order idle).
    """

    def _report(self, *, status, blocked_reason, squad_order, assigned_frame=0):
        update_id = "wait-1"
        update = {
            "update_id": update_id,
            "issued_at_frame": 100,
            "expires_at_frame": 9_999,
            "vector": {
                "operations": [
                    {
                        "operation_id": "reinforce",
                        "generation": 1,
                        "tactical_task": {
                            "task_type": "defend_with_units",
                            "min_units": 1,
                            "max_units": 1,
                            "duration_seconds": 300,
                        },
                    }
                ]
            },
        }
        telemetry = {
            "frame": 400,
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": "reinforce",
                            "generation": 1,
                            "requested_task_type": "defend_with_units",
                            "task_type": "defend",
                            "squad_order": squad_order,
                            "status": status,
                            "blocked_reason": blocked_reason,
                            "received_frame": 110,
                            "assigned_frame": assigned_frame,
                            "submitted_frame": 0,
                            "last_action_frame": 0,
                            "assigned_unit_tags": [],
                            "assigned_count": 0,
                        }
                    ],
                }
            },
        }
        (report,) = classify_micromachine_operation_executions(
            latest_update=update, latest_telemetry=telemetry, latest_frame=400
        )
        return report

    def test_waiting_for_units_with_idle_squad_is_blocked_not_failed(self) -> None:
        for reason in ("composition_assignment_pending", "composition_prerequisites_pending"):
            with self.subTest(reason=reason):
                report = self._report(
                    status="WAITING_FOR_UNITS", blocked_reason=reason, squad_order="idle"
                )
                self.assertFalse(report.failed, report.to_dict())
                self.assertEqual("blocked", report.state)
                self.assertNotIn("routing mismatch", report.blocker_reason.lower())

    def test_wrong_squad_order_after_assignment_is_still_a_mismatch(self) -> None:
        report = self._report(
            status="MOVING",
            blocked_reason="",
            squad_order="attack",
            assigned_frame=120,
        )
        self.assertTrue(report.failed)
        self.assertIn("routing mismatch", report.blocker_reason.lower())


class PublishFailureReasonTest(unittest.TestCase):
    """D9: publish_failed must carry its cause in a user-displayable field."""

    def test_publish_failed_exposes_error_in_command_queue(self) -> None:
        from starcraft_commander.micromachine_runtime import MicroMachineInMemoryBlackboard

        class DiskFull(MicroMachineInMemoryBlackboard):
            def publish_vector(self, *args, **kwargs):
                raise OSError("disk full")

        live = MicroMachineLiveTextSession(
            DiskFull(),
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "마린 생산",
                    "command_layer": "macro",
                    "production": {"queue_biases": {"TERRAN_MARINE": 0.9}},
                    "tactical_task": {
                        "task_type": "sustain_production",
                        "production_targets": ["TERRAN_MARINE"],
                    },
                }
            ),
        )
        result = live.submit_text("마린 생산", current_frame=10, update_id="pf-1")
        self.assertIs(result.status, LiveModulationStatus.PUBLISH_FAILED)
        self.assertIsNone(result.update)
        failure = result.command_queue["failure"]
        self.assertEqual("publish", failure["stage"])
        self.assertIn("disk full", failure["reason"])


class AchievedStageTest(unittest.TestCase):
    """D10: ``state`` of the legacy classifier names the first MISSING stage.

    A UI that renders ``state`` verbatim would say "consumed_by_manager" for a
    command whose consumption was never observed.  ``achieved_stage`` is the
    last stage reached with every earlier stage proven.
    """

    def _legacy(self, telemetry):
        from starcraft_commander.micromachine_command_execution import (
            classify_micromachine_command_execution,
        )

        update = {
            "update_id": "ach-1",
            "issued_at_frame": 100,
            "expires_at_frame": 99_999,
            "manager_bias_domains": ["strategy", "production"],
            "vector": {
                "goal": "g",
                "production": {"queue_biases": {"TERRAN_MARINE": 0.9}},
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_MARINE"],
                },
            },
        }
        return classify_micromachine_command_execution(
            latest_update=update, latest_telemetry=telemetry
        ).to_dict()

    def test_unconsumed_command_has_published_as_achieved_stage(self) -> None:
        document = self._legacy({"frame": 50, "managers": {}})
        self.assertEqual("consumed_by_manager", document["state"])  # legacy name
        self.assertEqual("published", document["achieved_stage"])

    def test_consumed_command_advances_achieved_stage(self) -> None:
        document = self._legacy(
            {
                "frame": 200,
                "managers": {
                    "GameCommander": {"update_id": "ach-1", "policy_active": True}
                },
            }
        )
        self.assertEqual("consumed_by_manager", document["achieved_stage"])

    def test_operation_report_carries_the_same_field(self) -> None:
        report = AssignedShortfallIsSurfacedTest()._classify(
            requested=2, assigned_tags=(11, 12), allow_partial=False
        )
        self.assertEqual("effect_observed", report.to_dict()["achieved_stage"])


class SilentDurationClampTest(unittest.TestCase):
    """D11: ttl/duration clamped by the compiler must produce a warning."""

    def test_ttl_clamp_is_reported(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "goal": "계속 생산",
                "ttl_seconds": 5000,
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_MARINE"],
                },
            }
        )
        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(900, result.vector.ttl_seconds)
        self.assertTrue(
            any("ttl_seconds" in warning and "5000" in warning for warning in result.warnings),
            result.warnings,
        )

    def test_emergency_ttl_cap_is_reported(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "goal": "후퇴",
                "override_level": "emergency",
                "ttl_seconds": 600,
                "emergency": {"force_retreat": True},
            }
        )
        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(60, result.vector.ttl_seconds)
        self.assertTrue(any("ttl_seconds" in warning for warning in result.warnings))

    def test_in_range_ttl_has_no_clamp_warning(self) -> None:
        result = compile_policy_modulation_provider_output(
            {
                "goal": "계속 생산",
                "ttl_seconds": 300,
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_MARINE"],
                },
            }
        )
        self.assertFalse(any("ttl_seconds" in warning for warning in result.warnings))


# ---------------------------------------------------------------------------
# Whole-corpus invariants (scripts/fidelity/replay_chain.py)
# ---------------------------------------------------------------------------


class ChainReplayInvariantsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from scripts.fidelity import replay_chain

        cls.replay = replay_chain
        cls.report = replay_chain.run_all()

    def test_corpus_has_at_least_forty_cases(self) -> None:
        self.assertGreaterEqual(len(self.report["cases"]), 40)

    def test_every_case_replayed_without_harness_error(self) -> None:
        errors = {
            case["id"]: case["error"] for case in self.report["cases"] if case.get("error")
        }
        self.assertEqual({}, errors)

    def test_violations_equal_documented_handoff_set(self) -> None:
        actual = sorted(
            f"{violation['case']}::{violation['invariant']}"
            for violation in self.report["violations"]
        )
        self.assertEqual(sorted(self.replay.EXPECTED_OPEN_VIOLATIONS), actual)

    def test_ui_contract_never_claims_effect_without_effect_stage(self) -> None:
        for case in self.report["cases"]:
            for step in case["steps"]:
                for mode in ("projection", "production"):
                    claim = step.get(mode, {}).get("ui")
                    if not claim:
                        continue
                    with self.subTest(case=case["id"], mode=mode):
                        if claim["effect_observed"]:
                            self.assertTrue(claim["ordered"], claim)
                            self.assertTrue(claim["assigned"], claim)


if __name__ == "__main__":
    unittest.main()
