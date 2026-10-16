import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from starcraft_commander.live_qa_audit import audit_attempt, operation_observations


class LiveQAAuditTests(unittest.TestCase):
    scenario = {
        "id": "01-scv-production",
        "evidence": ["train_command|SCV", "unit_count_increased"],
    }

    def audit(self, rows, scenario=None):
        scenario = scenario or self.scenario
        operation_ids = {
            "21-cancel-attack": [
                ("21-cancel-attack", 1), ("21-cancel-attack", 2),
            ],
            "25-long-defense": [("25-long-defense", 1)],
            "23-reassign-tanks": [
                ("23-reassign-tanks-source", 1),
                ("23-reassign-tanks-source", 2),
            ],
            "29-heal-regroup": [("29-heal-regroup", 2)],
        }.get(scenario["id"], [])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "qa30_command_publish.json").write_text(json.dumps({
                "scenario_id": scenario["id"],
                "update_id": "command",
                "issued_at_frame": 100,
                "payload": {
                    "operations": [{
                        "operation_id": operation_id,
                        "generation": generation,
                    } for operation_id, generation in operation_ids] or (
                        [{
                            "operation_id": "20-retreat",
                            "generation": 2,
                        }]
                        if scenario["id"] == "20-retreat"
                        else []
                    ),
                },
            }))
            (root / "telemetry.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )
            return audit_attempt(root, scenario)

    def row(self, frame, workers, action="none|none", action_update="command"):
        return {
            "frame": frame,
            "runtime_instance_id": "game-a",
            "runtime_instance_id_valid": True,
            "managers": {"ProductionManager": {
                "policy_update_id": "command",
                "last_actual_production_command_update_id": action_update,
                "last_actual_production_command_frame": 110,
                "last_actual_production_command": action,
            }},
            "battlefield_overview": {"excluded_unit_tags": {"worker": workers}},
        }

    def test_population_increase_alone_is_not_command_execution(self):
        report = self.audit([self.row(100, [1]), self.row(120, [1, 2])])
        self.assertEqual("unverified", report["status"])
        self.assertEqual(2, len(report["missing_evidence"]))

    def test_matching_scv_order_and_subsequent_population_increase(self):
        first = self.row(100, [1])
        training = self.row(112, [1], "train_command|SCV")
        completed = self.row(400, [1, 2], "train_command|SCV")
        for row, units in (
            (first, [self.scv(1), self.producer([])]),
            (training, [self.scv(1), self.producer([{"ability_name": "TRAIN_SCV"}])]),
            (completed, [self.scv(1), self.scv(2), self.producer([])]),
        ):
            row["sc2_observation"] = {
                "source": "sc2_observation", "schema_version": 1,
                "frame": row["frame"], "units": units,
            }
        report = self.audit([first, training, completed])
        self.assertEqual("passed", report["status"])

    @staticmethod
    def scv(tag):
        return {"tag": tag, "type": "SCV", "alliance": 1, "build_progress": 1}

    @staticmethod
    def producer(orders):
        return {
            "tag": 20, "type": "CommandCenter", "alliance": 1,
            "build_progress": 1, "orders": orders,
        }

    def test_worker_projection_increase_cannot_prove_scv_completion(self):
        report = self.audit([
            self.row(100, [1]),
            self.row(120, [1, 2], "train_command|SCV"),
        ])
        self.assertEqual("unverified", report["status"])
        self.assertIn("unit_count_increased", report["missing_evidence"])

    def test_terran_scv_name_is_normalized_without_proving_completion(self):
        report = self.audit([self.row(120, [1], "train_command|TERRAN_SCV")])
        self.assertIn("train_command|SCV", report["proven_evidence"])
        self.assertNotIn("unit_count_increased", report["proven_evidence"])

    def test_unrelated_supply_order_cannot_prove_scv_production(self):
        report = self.audit([
            self.row(100, [1]),
            self.row(120, [1, 2], "build_command|SupplyDepot"),
        ])
        self.assertEqual("unverified", report["status"])

    def test_other_command_cannot_prove_this_command(self):
        report = self.audit([
            self.row(100, [1]),
            self.row(120, [1, 2], "train_command|SCV", "other"),
        ])
        self.assertEqual("unverified", report["status"])

    def test_train_action_alone_cannot_prove_post_expansion_continuity(self):
        report = self.audit([self.row(120, [], "train_command|Marine")], {
            "id": "19-expand-production-resume",
            "evidence": ["building_completed", "train_command|Marine"],
        })
        self.assertEqual("unverified", report["status"])
        self.assertEqual(2, len(report["missing_evidence"]))

    def test_mixed_games_cannot_prove_completion(self):
        second = self.row(120, [1, 2], "train_command|SCV")
        second["runtime_instance_id"] = "game-b"
        report = self.audit([self.row(100, [1]), second])
        self.assertEqual("unverified", report["status"])
        self.assertTrue(report["errors"])

    def test_mineral_candidate_is_not_positive_income(self):
        row = self.row(120, [1])
        row["managers"]["WorkerManager"] = {"last_trace_reason": "mineral_assignment"}
        report = self.audit([row], {
            "id": "02-mineral-saturation",
            "evidence": ["worker_assignment_observed", "positive_mineral_income"],
        })
        self.assertEqual(2, len(report["missing_evidence"]))

    def operation_record(self, **changes):
        evidence = {
            "update_id": "command", "operation_id": "attack", "generation": 1,
            "action": "move", "effect_kind": "movement",
            "submitted_frame": 110, "effect_frame": 120,
            "submitted_count": 1, "effect_count": 1,
            "submitted_unit_tags": [7], "effect_unit_tags": [7],
        }
        evidence.update(changes)
        records = {}
        operation_observations({
            "policy_update_id": "command",
            "operations": [{
                "operation_id": "attack", "generation": 1,
                "status": "MOVING", "assigned_unit_tags": [7],
                "family_evidence": [evidence],
            }],
        }, {("attack", 1)}, {"command"}, 100, 125, records)
        return records[json.dumps(["command", "attack", 1])]

    def test_operation_generations_have_separate_terminal_records(self):
        records = {}
        for generation, status in [(1, "COMPLETED"), (2, "MOVING")]:
            operation_observations({
                "policy_update_id": "command",
                "operations": [{
                    "operation_id": "attack", "generation": generation,
                    "status": status, "assigned_unit_tags": [7],
                    "family_evidence": [],
                }],
            }, {("attack", generation)}, {"command"}, 100, 125, records)
        self.assertEqual(2, len(records))
        self.assertNotIn("terminal_frame", records[json.dumps(["command", "attack", 2])])

    def test_tank_transfer_requires_observed_siege_effect_in_both_generations(self):
        scenario = {
            "id": "23-reassign-tanks",
            "evidence": [
                "transfer_admitted",
                "ability_policy_preserved",
                "terminal_state",
            ],
        }

        def row(frame, generation, status, assigned, effect_frame=None):
            operation = {
                "operation_id": "23-reassign-tanks-source",
                "generation": generation,
                "status": status,
                "assigned_unit_tags": assigned,
                "family_evidence": [],
            }
            if effect_frame is not None:
                operation["family_evidence"] = [{
                    "update_id": "command",
                    "operation_id": "23-reassign-tanks-source",
                    "generation": generation,
                    "action": "ability:MORPH_SIEGEMODE",
                    "effect_kind": "ability_state",
                    "submitted_frame": effect_frame - 1,
                    "effect_frame": effect_frame,
                    "submitted_count": 1,
                    "effect_count": 1,
                    "submitted_unit_tags": assigned,
                    "effect_unit_tags": assigned,
                }]
            data = self.row(frame, [1])
            data["managers"]["OperationDirector"] = {
                "policy_update_id": "command",
                "operations": [operation],
            }
            return data

        report = self.audit([
            row(100, 1, "MOVING", [7], 120),
            row(130, 1, "MOVING", [7], 120),
            row(150, 2, "COMPLETED", [8], 140),
        ], scenario)
        self.assertEqual("passed", report["status"])
        self.assertIn("ability_policy_preserved", report["proven_evidence"])

        report = self.audit([
            row(100, 1, "MOVING", [7], 120),
            row(130, 1, "MOVING", [7], 120),
            row(150, 2, "COMPLETED", [8]),
        ], scenario)
        self.assertEqual("unverified", report["status"])
        self.assertIn("ability_policy_preserved", report["missing_evidence"])

    def test_scoped_movement_is_not_terminal_completion(self):
        record = self.operation_record()
        self.assertEqual(120, record["effects"]["move:movement"]["effect_frame"])
        self.assertNotIn("terminal_frame", record)

    def test_unrelated_operation_effects_are_rejected(self):
        for changes in (
            {"update_id": "other"}, {"operation_id": "defense"},
            {"generation": 2}, {"effect_unit_tags": [8]},
            {"effect_frame": 109}, {"effect_frame": 126},
            {"submitted_count": 0}, {"effect_count": 0},
        ):
            with self.subTest(changes=changes):
                self.assertEqual({}, self.operation_record(**changes)["effects"])

    def test_game_end_requires_explicit_check_game_result_marker(self):
        scenario = {
            "id": "30-game-end",
            "evidence": ["OnGameEnd", "CheckGameResult", "final_telemetry"],
        }
        row = self.row(6002, [1])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "qa30_command_publish.json").write_text(json.dumps({
                "scenario_id": scenario["id"],
                "update_id": "command",
                "issued_at_frame": 100,
                "payload": {},
            }))
            (root / "telemetry.jsonl").write_text(json.dumps(row) + "\n")
            (root / "latest_telemetry.json").write_text(json.dumps(row))
            (root / "micromachine.log").write_text(
                "OnGameEnd Win\n"
            )
            report = audit_attempt(root, scenario)
        self.assertEqual("unverified", report["status"])
        self.assertIn("CheckGameResult", report["missing_evidence"])

    def test_game_end_accepts_explicit_check_game_result_marker(self):
        scenario = {
            "id": "30-game-end",
            "evidence": ["OnGameEnd", "CheckGameResult", "final_telemetry"],
        }
        row = self.row(6002, [1])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "qa30_command_publish.json").write_text(json.dumps({
                "scenario_id": scenario["id"],
                "update_id": "command",
                "issued_at_frame": 100,
                "payload": {},
            }))
            (root / "telemetry.jsonl").write_text(json.dumps(row) + "\n")
            (root / "latest_telemetry.json").write_text(json.dumps(row))
            (root / "micromachine.log").write_text(
                "CheckGameResult invoked\n"
                "OnGameEnd Win\n"
            )
            report = audit_attempt(root, scenario)
        self.assertEqual("passed", report["status"])

    def test_defend_assignment_cannot_prove_regroup(self):
        scenario = {
            "id": "29-heal-regroup",
            "evidence": [
                "operation_terminal",
                "autonomous_owner_restored",
                "regroup_action",
            ],
        }
        rows = []
        for frame, status, assigned, explicit, autonomous, family in (
            (100, "SUBMITTED", [7], 1, 0, []),
            (120, "COMPLETED", [], 0, 1, [{
                "action": "squad_order:defend",
                "assigned": 1,
                "represented": 1,
            }]),
        ):
            row = self.row(frame, [1])
            row["managers"]["OperationDirector"] = {
                "policy_update_id": "command",
                "operations": [{
                    "operation_id": "29-heal-regroup",
                    "generation": 2,
                    "status": status,
                    "assigned_unit_tags": assigned,
                    "family_evidence": family,
                }],
            }
            row["battlefield_overview"] = {
                "explicit_operation_owned_count": explicit,
                "autonomous_owned_count": autonomous,
                "autonomous_ownership": (
                    [{"owner_tags": [7]}] if autonomous else []
                ),
                "ownership_integrity": "valid",
                "excluded_unit_tags": {"worker": [1]},
            }
            rows.append(row)
        report = self.audit(rows, scenario)
        self.assertEqual("unverified", report["status"])
        self.assertIn("autonomous_owner_restored", report["proven_evidence"])
        self.assertEqual(["regroup_action"], report["missing_evidence"])

        rows[-1]["managers"]["OperationDirector"]["operations"][0][
            "family_evidence"
        ] = [{
            "update_id": "command",
            "operation_id": "29-heal-regroup",
            "generation": 2,
            "action": "squad_order:regroup",
            "effect_kind": "movement",
            "submitted_frame": 110,
            "effect_frame": 120,
            "submitted_count": 1,
            "effect_count": 1,
            "submitted_unit_tags": [7],
            "effect_unit_tags": [7],
        }]
        report = self.audit(rows, scenario)
        self.assertEqual("passed", report["status"])
        self.assertIn("regroup_action", report["proven_evidence"])

        rows[-1]["battlefield_overview"]["autonomous_ownership"] = [
            {"owner_tags": [8]}
        ]
        report = self.audit(rows, scenario)
        self.assertIn("autonomous_owner_restored", report["missing_evidence"])

    def test_regroup_order_and_sc2_effect_prove_regroup(self):
        scenario = {
            "id": "29-heal-regroup",
            "evidence": [
                "operation_terminal",
                "autonomous_owner_restored",
                "regroup_action",
            ],
        }
        rows = []
        for frame, status, assigned, explicit, autonomous in (
            (100, "SUBMITTED", [7], 1, 0),
            (120, "COMPLETED", [], 0, 1),
        ):
            row = self.row(frame, [1])
            row["managers"]["OperationDirector"] = {
                "policy_update_id": "command",
                "operations": [{
                    "operation_id": "29-heal-regroup",
                    "generation": 2,
                    "status": status,
                    "task_type": "regroup",
                    "squad_order": "regroup",
                    "assigned_unit_tags": assigned,
                    "family_evidence": [{
                        "update_id": "command",
                        "operation_id": "29-heal-regroup",
                        "generation": 2,
                        "action": "move",
                        "effect_kind": "movement",
                        "submitted_frame": 110,
                        "effect_frame": 120,
                        "submitted_count": 1,
                        "effect_count": 1,
                        "submitted_unit_tags": [7],
                        "effect_unit_tags": [7],
                    }],
                }],
            }
            row["battlefield_overview"] = {
                "explicit_operation_owned_count": explicit,
                "autonomous_owned_count": autonomous,
                "autonomous_ownership": (
                    [{"owner_tags": [7]}] if autonomous else []
                ),
                "ownership_integrity": "valid",
                "excluded_unit_tags": {"worker": [1]},
            }
            rows.append(row)
        report = self.audit(rows, scenario)
        self.assertEqual("passed", report["status"])
        self.assertIn("regroup_action", report["proven_evidence"])

    def test_cancel_lifecycle_requires_matching_release_and_purge_telemetry(self):
        scenario = {
            "id": "21-cancel-attack",
            "evidence": [
                "cancelled_by_user",
                "ownership_released",
                "queue_purge_observed",
            ],
        }
        def lifecycle(frame, operations, release=0, purge=0,
                      released_owner="none", purge_event=None):
            row = self.row(frame, [1], "none|none")
            row["managers"]["OperationDirector"] = {
                "policy_update_id": "command",
                "operations": operations,
            }
            row["managers"]["ProductionManager"].update({
                "operation_production_owner_release_count": release,
                "operation_production_queue_purge_count": purge,
                "last_operation_production_released_owner": released_owner,
                "last_operation_production_queue_purge_event": purge_event or {},
            })
            return row

        assigned = {
            "operation_id": "21-cancel-attack", "generation": 1,
            "status": "SUBMITTED", "assigned_unit_tags": [7],
            "family_evidence": [{
                "update_id": "command", "operation_id": "21-cancel-attack",
                "generation": 1, "action": "move",
                "effect_kind": "movement", "submitted_frame": 110,
                "effect_frame": 120, "submitted_count": 1,
                "effect_count": 1, "submitted_unit_tags": [7],
                "effect_unit_tags": [7],
            }],
        }
        cancelled = {
            "operation_id": "21-cancel-attack", "generation": 2,
            "status": "CANCELLED", "assigned_unit_tags": [],
            "completion_conditions": "cancelled_by_user",
            "blocked_reason": "cancelled_by_policy",
            "family_evidence": [],
        }
        report = self.audit([
            lifecycle(100, [assigned]),
            lifecycle(120, [assigned]),
            lifecycle(
                130, [cancelled], release=1,
                released_owner="21-cancel-attack#1",
                purge=1,
                purge_event={
                    "operation_id": "21-cancel-attack",
                    "generation": 1, "frame": 130, "removed_count": 2,
                },
            ),
        ], scenario)
        self.assertEqual("passed", report["status"])
        self.assertEqual([], report["missing_evidence"])

    def test_retreat_lifecycle_requires_confirmation_and_owner_release(self):
        scenario = {
            "id": "20-retreat",
            "evidence": ["retreat_confirmed", "ownership_released"],
        }

        def lifecycle(frame, operation, release=0, released_owner=""):
            row = self.row(frame, [1], "none|none")
            row["managers"]["OperationDirector"] = {
                "policy_update_id": "command",
                "operations": [operation],
            }
            row["managers"]["ProductionManager"].update({
                "operation_production_owner_release_count": release,
                "last_operation_production_released_owner": released_owner,
            })
            return row

        assigned = {
            "operation_id": "20-retreat", "generation": 2,
            "status": "BLOCKED", "assigned_unit_tags": [7],
            "completion_conditions": "retreat_confirmed",
            "retreat_confirmed": True,
            "family_evidence": [],
        }
        completed = {
            **assigned,
            "status": "COMPLETED",
            "assigned_unit_tags": [],
        }
        report = self.audit([
            lifecycle(100, assigned),
            lifecycle(
                120, completed, release=1,
                released_owner="20-retreat#2",
            ),
        ], scenario)
        self.assertEqual("passed", report["status"])
        self.assertEqual([], report["missing_evidence"])

    def test_standing_defense_requires_lease_and_actual_terminal_state(self):
        scenario = {
            "id": "25-long-defense",
            "evidence": [
                "operation_alive_after_lease",
                "defense_effect",
                "terminal_state",
            ],
        }
        operation = {
            "operation_id": "25-long-defense", "generation": 1,
            "status": "ENGAGED", "assigned_unit_tags": [7],
            "lifetime_mode": "standing_order", "duration_seconds": 300,
            "family_evidence": [{
                "update_id": "command", "operation_id": "25-long-defense",
                "generation": 1, "action": "move",
                "effect_kind": "movement", "submitted_frame": 200,
                "effect_frame": 220, "submitted_count": 1,
                "effect_count": 1, "submitted_unit_tags": [7],
                "effect_unit_tags": [7],
            }],
        }
        row = self.row(6200, [1])
        row["managers"]["OperationDirector"] = {
            "policy_update_id": "command", "operations": [operation],
        }
        row["managers"]["ProductionManager"]["policy_expires_at_frame"] = 6000
        report = self.audit([row], scenario)
        self.assertEqual("unverified", report["status"])
        self.assertEqual(["terminal_state"], report["missing_evidence"])
        terminal = json.loads(json.dumps(row))
        terminal["frame"] = 6300
        terminal["managers"]["OperationDirector"]["operations"][0].update({
            "status": "CANCELLED", "assigned_unit_tags": [],
        })
        report = self.audit([row, terminal], scenario)
        self.assertEqual("passed", report["status"])
        self.assertEqual([], report["missing_evidence"])
        row["managers"]["ProductionManager"]["policy_expires_at_frame"] = 6500
        report = self.audit([row, terminal], scenario)
        self.assertIn("operation_alive_after_lease", report["missing_evidence"])


if __name__ == "__main__":
    unittest.main()
