"""Tests for MicroMachine tactical-effect evidence classification."""

import json
import unittest

from starcraft_commander.micromachine_tactical_evidence import (
    classify_micromachine_tactical_evidence,
    normalize_tactical_effect_tags,
)


class MicroMachineTacticalEvidenceTest(unittest.TestCase):
    def test_normalizes_profile_and_effect_aliases(self) -> None:
        self.assertEqual(
            ("pressure", "hold", "target_priority", "scout"),
            normalize_tactical_effect_tags(
                (
                    "aggressive_pressure",
                    "defensive-hold",
                    "worker_line",
                    "scouting_map_control",
                )
            ),
        )

    def test_passes_when_expected_effects_have_behavior_evidence(self) -> None:
        telemetry = {
            "frame": 12450,
            "managers": {
                "CombatCommander": {
                    "consumed_axes": "combat.aggression,combat.target_priority_biases.*",
                    "main_attack_order": "Attack enemy natural",
                },
                "Squad": {
                    "consumed_axes": "squad.contain_bias,scope.location_intent",
                    "contain_bias": 0.35,
                    "scope_location_intent": "enemy_natural",
                    "selected_target_class": "worker_line",
                },
            },
        }
        log_text = "\n".join(
            (
                "12450: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
                "12455: calcTargets | target worker_line selected by policy modulation",
            )
        )

        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry=telemetry,
            log_text=log_text,
            expected_effects=("aggressive_pressure", "contain", "target_priority"),
            source_paths={"bot_log": "micromachine.log"},
        )

        self.assertTrue(evidence.ok, evidence.to_dict())
        self.assertEqual("passed", evidence.status)
        self.assertEqual((), evidence.missing_effects)
        self.assertIn("pressure", evidence.observed_effects)
        self.assertIn("contain", evidence.observed_effects)
        self.assertIn("target_priority", evidence.observed_effects)
        self.assertEqual(
            ("combat.aggression", "combat.target_priority_biases.*"),
            evidence.consumed_axes_by_manager["CombatCommander"],
        )
        json.dumps(evidence.to_dict())

    def test_consumed_axes_alone_do_not_satisfy_behavior_effect(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 12000,
                "managers": {
                    "CombatCommander": {
                        "consumed_axes": "combat.aggression",
                        "bounded_intervention": True,
                    }
                },
            },
            expected_effects=("pressure",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("pressure",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)
        self.assertEqual(
            ("combat.aggression",),
            evidence.consumed_axes_by_manager["CombatCommander"],
        )

    def test_bias_only_desired_state_does_not_satisfy_behavior_effects(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "CombatCommander": {
                        "defend_bias": 0.8,
                        "force_retreat": True,
                        "hold_position": True,
                    },
                    "Squad": {
                        "contain_bias": 0.3,
                        "scope_location_intent": "enemy_natural",
                        "harassment_bias": 0.5,
                        "army_group": "harass",
                    },
                    "ScoutManager": {"scout_priority": 0.9},
                },
            },
            expected_effects=("hold", "contain", "harass", "scout"),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("hold", "contain", "harass", "scout"), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_scout_with_units_requires_actual_scout_squad_command(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "scout_with_units",
                        "status": "executing",
                        "actual_command_issued_count": 3,
                        "last_actual_command": "ScoutSquadOrder|assigned_units=3",
                    },
                    "CombatCommander": {
                        "scout_scope_status": "Consumed",
                        "scout_scope_assigned_unit_count": 3,
                    },
                },
            },
            expected_effects=("scout",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("scout",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_scout_with_units_ignores_generic_scout_manager_goal(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "scout_with_units",
                        "status": "executing",
                        "actual_command_issued_count": 3,
                        "last_actual_command": "ScoutSquadOrder|assigned_units=3",
                    },
                    "ScoutManager": {
                        "current_scout_goal": "enemy_start_location",
                        "status": "policy scout target selected",
                    },
                },
            },
            log_text="13000: Scout policy target selected",
            expected_effects=("scout",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("scout",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_scout_with_units_accepts_actual_scout_squad_command(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "scout_with_units",
                        "status": "executing",
                        "actual_command_issued_count": 1,
                        "last_actual_command": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                    },
                    "CombatCommander": {
                        "scout_actual_command_issued_count": 1,
                        "scout_last_action_frame": 13000,
                        "scout_last_issued_action": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                        "scout_home_distance": 10.0,
                        "scout_max_home_distance": 16.0,
                        "scout_marine_assigned_count": 1,
                        "scout_marine_home_distance": 10.0,
                        "scout_marine_max_home_distance": 16.0,
                        "scout_last_commanded_unit_tag": 4242,
                        "scout_last_commanded_unit_type": "TERRAN_MARINE",
                    },
                },
            },
            expected_effects=("scout",),
        )

        self.assertTrue(evidence.ok, evidence.to_dict())
        self.assertIn("scout", evidence.observed_effects)

    def test_scout_with_units_rejects_command_without_live_movement(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "scout_with_units",
                        "status": "executing",
                        "actual_command_issued_count": 1,
                        "last_actual_command": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                    },
                    "CombatCommander": {
                        "scout_actual_command_issued_count": 1,
                        "scout_last_action_frame": 13000,
                        "scout_last_issued_action": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                        "scout_home_distance": 2.0,
                        "scout_max_home_distance": 3.0,
                        "scout_marine_assigned_count": 1,
                        "scout_marine_home_distance": 2.0,
                        "scout_marine_max_home_distance": 3.0,
                        "scout_last_commanded_unit_tag": 4242,
                        "scout_last_commanded_unit_type": "Marine",
                    },
                },
            },
            expected_effects=("scout",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("scout",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_scout_with_units_rejects_non_marine_scout_movement(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "scout_with_units",
                        "status": "executing",
                        "actual_command_issued_count": 1,
                        "last_actual_command": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                    },
                    "CombatCommander": {
                        "scout_actual_command_issued_count": 1,
                        "scout_last_action_frame": 13000,
                        "scout_last_issued_action": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                        "scout_scope_assigned_unit_count": 1,
                        "scout_marine_assigned_count": 0,
                        "scout_max_home_distance": 16.0,
                        "scout_marine_max_home_distance": 0.0,
                        "scout_last_commanded_unit_tag": 5151,
                        "scout_last_commanded_unit_type": "TERRAN_REAPER",
                    },
                },
            },
            expected_effects=("scout",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("scout",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_marine_scout_requires_exact_requested_assignment_count(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "CombatCommander": {
                        "scout_actual_command_issued_count": 1,
                        "scout_last_action_frame": 13000,
                        "scout_last_issued_action": (
                            "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5"
                        ),
                        "scout_scope_requested_min_units": 3,
                        "scout_scope_requested_max_units": 3,
                        "scout_marine_assigned_count": 2,
                        "scout_marine_max_home_distance": 16.0,
                        "scout_last_commanded_unit_tag": 5151,
                        "scout_last_commanded_unit_type": "TERRAN_MARINE",
                    },
                },
            },
            expected_effects=("scout",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("scout",), evidence.missing_effects)

    def test_generic_ability_submission_without_confirmation_is_missing(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
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
                        "submission_frame": 12990,
                        "confirmation_state": "pending",
                        "confirmation_count": 0,
                        "confirmation_frame": 0,
                        "confirmation_effect": "",
                    },
                },
            },
            expected_effects=("ability_cast",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("ability_cast",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_generic_ability_requires_complete_current_confirmation(self) -> None:
        confirmed_payload = {
            "update_id": "ability-current",
            "task_type": "execute_ability",
            "ability": "stimpack",
            "status": "completed",
            "phase": "effect_observed",
            "attempt_generation": 3,
            "submitted_attempt_generation": 3,
            "terminal_attempt_generation": 3,
            "submitted_count": 1,
            "last_action": "VoiExplicitAbility:stimpack",
            "submission_frame": 12990,
            "confirmation_state": "confirmed",
            "confirmation_count": 1,
            "confirmation_frame": 13000,
            "confirmation_effect": "actor_buff:STIMPACK",
        }
        invalid_fields = {
            "submitted_count": 0,
            "last_action": "",
            "submission_frame": 0,
            "confirmation_state": "pending",
            "confirmation_count": 0,
            "confirmation_frame": 12989,
            "confirmation_effect": "",
            "update_id": "ability-stale",
        }

        for field, invalid_value in invalid_fields.items():
            with self.subTest(field=field):
                payload = {**confirmed_payload, field: invalid_value}
                evidence = classify_micromachine_tactical_evidence(
                    latest_telemetry={
                        "frame": 13000,
                        "active_modulation_ids": ["ability-current"],
                        "managers": {
                            "GameCommander": {"update_id": "ability-current"},
                            "AbilityTask": payload,
                        },
                    },
                    expected_effects=("ability_cast",),
                )

                self.assertEqual("missing", evidence.status)
                self.assertEqual(("ability_cast",), evidence.missing_effects)

    def test_generic_ability_accepts_last_actual_command_frame_fallback(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "active_modulation_ids": ["ability-current"],
                "managers": {
                    "GameCommander": {"update_id": "ability-current"},
                    "AbilityTask": {
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
                        "last_actual_command_frame": 12990,
                        "confirmation_state": "confirmed",
                        "confirmation_count": 1,
                        "confirmation_frame": 13000,
                        "confirmation_effect": "actor_buff:STIMPACK",
                    },
                },
            },
            expected_effects=("ability_cast",),
        )

        self.assertTrue(evidence.ok, evidence.to_dict())
        self.assertEqual(("ability_cast",), evidence.observed_effects)
        self.assertEqual(13000, evidence.effects[0].frame)

    def test_generic_ability_accepts_exact_observed_accepted_terminal(
        self,
    ) -> None:
        ability_task = {
            "update_id": "ability-current",
            "task_type": "execute_ability",
            "ability": "lock_on",
            "status": "completed",
            "phase": "observed_accepted",
            "attempt_generation": 7,
            "submitted_attempt_generation": 7,
            "observed_accepted_attempt_generation": 7,
            "terminal_attempt_generation": 7,
            "submitted_count": 1,
            "last_action": "VoiExplicitAbility:lock_on|ability=EFFECT_LOCKON",
            "submission_frame": 12990,
            "observed_accepted_frame": 12994,
            "observed_accepted_evidence": "actor_order:EFFECT_LOCKON",
            "confirmation_state": "accepted",
            "confirmation_count": 0,
            "confirmation_frame": 0,
            "confirmation_effect": "actor_order:EFFECT_LOCKON",
        }

        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "active_modulation_ids": ["ability-current"],
                "managers": {
                    "GameCommander": {"update_id": "ability-current"},
                    "AbilityTask": ability_task,
                },
            },
            expected_effects=("ability_cast",),
        )

        self.assertTrue(evidence.ok, evidence.to_dict())
        for field in (
            "submitted_attempt_generation",
            "observed_accepted_attempt_generation",
            "terminal_attempt_generation",
        ):
            with self.subTest(stale_generation=field):
                stale_task = {**ability_task, field: 6}
                stale = classify_micromachine_tactical_evidence(
                    latest_telemetry={
                        "frame": 13000,
                        "active_modulation_ids": ["ability-current"],
                        "managers": {
                            "GameCommander": {
                                "update_id": "ability-current"
                            },
                            "AbilityTask": stale_task,
                        },
                    },
                    expected_effects=("ability_cast",),
                )
                self.assertFalse(stale.ok, stale.to_dict())

    def test_generic_ability_accepts_exact_already_satisfied_terminal(
        self,
    ) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "active_modulation_ids": ["ability-current"],
                "managers": {
                    "GameCommander": {"update_id": "ability-current"},
                    "AbilityTask": {
                        "update_id": "ability-current",
                        "task_type": "execute_ability",
                        "ability": "viking_fighter_mode",
                        "status": "completed",
                        "phase": "effect_observed",
                        "attempt_generation": 8,
                        "submitted_attempt_generation": 0,
                        "terminal_attempt_generation": 8,
                        "submitted_count": 0,
                        "last_action": "",
                        "submission_frame": 0,
                        "confirmation_state": "confirmed",
                        "confirmation_count": 1,
                        "confirmation_frame": 12992,
                        "confirmation_effect": (
                            "already_satisfied:"
                            "unit_type:TERRAN_VIKINGFIGHTER"
                        ),
                    },
                },
            },
            expected_effects=("ability_cast",),
        )

        self.assertTrue(evidence.ok, evidence.to_dict())

    def test_generic_ability_accepts_cpp_effect_tag_contracts(self) -> None:
        cases = (
            ("stimpack", "actor_buff:STIMPACK"),
            ("marauder_stimpack", "actor_buff:STIMPACKMARAUDER"),
            ("medivac_afterburners", "actor_buff:MEDIVACSPEEDBOOST"),
            ("medivac_load", "cargo:passenger_loaded"),
        )
        for ability, confirmation_effect in cases:
            with self.subTest(ability=ability):
                evidence = classify_micromachine_tactical_evidence(
                    latest_telemetry={
                        "frame": 13000,
                        "active_modulation_ids": ["ability-current"],
                        "managers": {
                            "GameCommander": {
                                "update_id": "ability-current"
                            },
                            "AbilityTask": {
                                "update_id": "ability-current",
                                "task_type": "execute_ability",
                                "ability": ability,
                                "status": "completed",
                                "phase": "effect_observed",
                                "attempt_generation": 9,
                                "submitted_attempt_generation": 9,
                                "terminal_attempt_generation": 9,
                                "submitted_count": 1,
                                "last_action": (
                                    f"VoiExplicitAbility:{ability}"
                                ),
                                "submission_frame": 12990,
                                "confirmation_state": "confirmed",
                                "confirmation_count": 1,
                                "confirmation_frame": 13000,
                                "confirmation_effect": confirmation_effect,
                            },
                        },
                    },
                    expected_effects=("ability_cast",),
                )
                self.assertTrue(evidence.ok, evidence.to_dict())

    def test_generic_ability_rejects_mismatched_action_or_effect(self) -> None:
        confirmed_payload = {
            "update_id": "ability-current",
            "task_type": "execute_ability",
            "ability": "stimpack",
            "status": "completed",
            "phase": "effect_observed",
            "attempt_generation": 3,
            "submitted_attempt_generation": 3,
            "terminal_attempt_generation": 3,
            "submitted_count": 1,
            "last_action": "VoiExplicitAbility:stimpack|ability=EFFECT_STIM",
            "submission_frame": 12990,
            "confirmation_state": "confirmed",
            "confirmation_count": 1,
            "confirmation_frame": 13000,
            "confirmation_effect": "actor_buff:STIMPACK",
        }
        invalid_overrides = (
            {
                "last_action": (
                    "VoiExplicitAbility:siege_mode|ability=MORPH_SIEGEMODE"
                )
            },
            {"confirmation_effect": "unit_type:TERRAN_SIEGETANKSIEGED"},
            {"confirmation_effect": "actor_order:EFFECT_STIM"},
        )

        for override in invalid_overrides:
            with self.subTest(override=override):
                evidence = classify_micromachine_tactical_evidence(
                    latest_telemetry={
                        "frame": 13000,
                        "active_modulation_ids": ["ability-current"],
                        "managers": {
                            "GameCommander": {"update_id": "ability-current"},
                            "AbilityTask": {
                                **confirmed_payload,
                                **override,
                            },
                        },
                    },
                    expected_effects=("ability_cast",),
                )

                self.assertEqual("missing", evidence.status)
                self.assertEqual(("ability_cast",), evidence.missing_effects)

    def test_tactical_nuke_submission_without_confirmation_is_missing(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
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
                        "cast_submission_frame": 12990,
                        "confirmation_state": "pending",
                        "confirmation_count": 0,
                        "confirmation_frame": 0,
                        "confirmation_effect": "",
                    },
                },
            },
            expected_effects=("ability_cast", "tactical_nuke"),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(
            ("ability_cast", "tactical_nuke"),
            evidence.missing_effects,
        )

    def test_tactical_nuke_rejects_legacy_cast_executed_submission(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "AbilityTask": {
                        "update_id": "nuke-current",
                        "ability": "tactical_nuke",
                        "status": "cast_issued",
                        "cast_executed_count": 1,
                        "cast_issued_action": (
                            "VoiRoleGhostTacticalNuke|squad=|type=6|"
                            "ability=EFFECT_NUKECALLDOWN|x=80|y=80"
                        ),
                        "cast_frame": 13000,
                    },
                },
            },
            expected_effects=("ability_cast", "tactical_nuke"),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual((), evidence.observed_effects)

    def test_tactical_nuke_accepts_sc2_observed_ghost_order(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "AbilityTask": {
                        "update_id": "nuke-current",
                        "ability": "tactical_nuke",
                        "location_intent": "enemy_main",
                        "target_location_match": True,
                        "status": "confirmed",
                        "cast_submitted_count": 1,
                        "cast_submitted_action": (
                            "VoiRoleGhostTacticalNuke|squad=|type=6|"
                            "ability=EFFECT_NUKECALLDOWN|x=80|y=80"
                        ),
                        "cast_submission_frame": 12990,
                        "confirmation_state": "confirmed",
                        "confirmation_count": 1,
                        "confirmation_frame": 13000,
                        "confirmation_effect": (
                            "ghost_order:EFFECT_NUKECALLDOWN"
                        ),
                    },
                },
            },
            expected_effects=("ability_cast", "tactical_nuke"),
        )

        self.assertTrue(evidence.ok, evidence.to_dict())
        self.assertIn("ability_cast", evidence.observed_effects)
        self.assertIn("tactical_nuke", evidence.observed_effects)

    def test_tactical_nuke_accepts_persistent_effect_confirmation(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13002,
                "managers": {
                    "AbilityTask": {
                        "update_id": "nuke-current",
                        "ability": "tactical_nuke",
                        "location_intent": "enemy_main",
                        "target_location_match": True,
                        "status": "confirmed",
                        "cast_submitted_count": 1,
                        "cast_submitted_action": (
                            "VoiRoleGhostTacticalNuke|squad=|type=6|"
                            "ability=EFFECT_NUKECALLDOWN|x=80|y=80"
                        ),
                        "cast_submission_frame": 12990,
                        "confirmation_state": "confirmed",
                        "confirmation_count": 1,
                        "confirmation_frame": 13002,
                        "confirmation_effect": (
                            "persistent_effect:NUKEPERSISTENT"
                        ),
                    },
                },
            },
            expected_effects=("ability_cast", "tactical_nuke"),
        )

        self.assertTrue(evidence.ok, evidence.to_dict())

    def test_tactical_nuke_rejects_submission_echo_as_confirmation(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "AbilityTask": {
                        "update_id": "nuke-current",
                        "ability": "tactical_nuke",
                        "status": "confirmed",
                        "cast_submitted_count": 1,
                        "cast_submitted_action": (
                            "VoiRoleGhostTacticalNuke|squad=|type=6|"
                            "ability=EFFECT_NUKECALLDOWN|x=80|y=80"
                        ),
                        "cast_submission_frame": 12990,
                        "confirmation_state": "confirmed",
                        "confirmation_count": 1,
                        "confirmation_frame": 13000,
                        "confirmation_effect": (
                            "command_submission:EFFECT_NUKECALLDOWN"
                        ),
                    },
                },
            },
            expected_effects=("ability_cast", "tactical_nuke"),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(
            ("ability_cast", "tactical_nuke"),
            evidence.missing_effects,
        )

    def test_tactical_nuke_rejects_location_mismatch(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "AbilityTask": {
                        "update_id": "nuke-current",
                        "ability": "tactical_nuke",
                        "location_intent": "enemy_main",
                        "target_location_match": False,
                        "status": "confirmed",
                        "cast_submitted_count": 1,
                        "cast_submitted_action": (
                            "VoiRoleGhostTacticalNuke|squad=|type=6|"
                            "ability=EFFECT_NUKECALLDOWN|x=80|y=80"
                        ),
                        "cast_submission_frame": 12990,
                        "confirmation_state": "confirmed",
                        "confirmation_count": 1,
                        "confirmation_frame": 13000,
                        "confirmation_effect": (
                            "ghost_order:EFFECT_NUKECALLDOWN"
                        ),
                    },
                },
            },
            expected_effects=("ability_cast", "tactical_nuke"),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(
            ("ability_cast", "tactical_nuke"),
            evidence.missing_effects,
        )

    def test_scout_with_units_ignores_stale_combat_scout_command(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "scout_with_units",
                        "status": "accepted",
                        "actual_command_issued_count": 0,
                        "last_actual_command": "",
                    },
                    "CombatCommander": {
                        "scout_actual_command_issued_count": 31,
                        "scout_last_action_frame": 5256,
                        "scout_last_issued_action": "MoveToGoalOrder|squad=Scout|type=2|x=33.5|y=138.5",
                    },
                },
            },
            expected_effects=("scout",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("scout",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_pressure_task_ignores_attack_order_without_main_attack_command(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "pressure_with_main_army",
                        "status": "accepted",
                        "actual_command_issued_count": 0,
                        "last_actual_command": "",
                    },
                    "CombatCommander": {
                        "main_attack_order": "Attack enemy natural",
                        "main_attack_order_status": "Attack",
                        "main_attack_actual_command_issued_count": 0,
                        "main_attack_last_action_frame": 0,
                        "main_attack_last_issued_action": "",
                    },
                },
            },
            log_text="13000: updateAttackSquads | MainAttackSquad new order = Attack enemy natural",
            expected_effects=("pressure",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("pressure",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_pressure_task_ignores_stale_main_attack_command(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "pressure_with_main_army",
                        "status": "accepted",
                        "actual_command_issued_count": 0,
                        "last_actual_command": "",
                    },
                    "CombatCommander": {
                        "main_attack_order_status": "Attack",
                        "main_attack_actual_command_issued_count": 55,
                        "main_attack_last_action_frame": 5277,
                        "main_attack_last_issued_action": "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5",
                    },
                },
            },
            expected_effects=("pressure",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("pressure",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_pressure_task_accepts_actual_main_attack_command(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "pressure_with_main_army",
                        "status": "executing",
                        "actual_command_issued_count": 1,
                        "last_actual_command": "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5",
                    },
                    "CombatCommander": {
                        "main_attack_order_status": "Attack",
                        "main_attack_actual_command_issued_count": 1,
                        "main_attack_last_action_frame": 13000,
                        "main_attack_last_issued_action": "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5",
                        "main_attack_home_distance": 18.0,
                        "main_attack_max_home_distance": 28.0,
                    },
                },
            },
            expected_effects=("pressure",),
        )

        self.assertTrue(evidence.ok, evidence.to_dict())
        self.assertIn("pressure", evidence.observed_effects)

    def test_pressure_task_rejects_command_without_live_movement(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "TacticalTask": {
                        "task_type": "pressure_with_main_army",
                        "status": "executing",
                        "actual_command_issued_count": 1,
                        "last_actual_command": "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5",
                    },
                    "CombatCommander": {
                        "main_attack_order_status": "Attack",
                        "main_attack_actual_command_issued_count": 1,
                        "main_attack_last_action_frame": 13000,
                        "main_attack_last_issued_action": "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5",
                        "main_attack_home_distance": 3.0,
                        "main_attack_max_home_distance": 4.0,
                    },
                },
            },
            expected_effects=("pressure",),
        )

        self.assertEqual("missing", evidence.status)
        self.assertEqual(("pressure",), evidence.missing_effects)
        self.assertEqual((), evidence.observed_effects)

    def test_partial_when_only_some_expected_effects_are_observed(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={
                "frame": 13000,
                "managers": {
                    "Squad": {
                        "main_attack_order": "Attack enemy natural",
                    }
                },
            },
            expected_effects=("contain", "target_priority"),
        )

        self.assertEqual("partial", evidence.status)
        self.assertEqual(("target_priority",), evidence.missing_effects)
        self.assertIn("contain", evidence.observed_effects)

    def test_refused_status_takes_precedence(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={"frame": 77, "managers": {}},
            refusal_reasons=("공격 타이밍을 더 구체화해 주세요.",),
            expected_effects=("pressure",),
        )

        self.assertEqual("refused", evidence.status)
        self.assertIn("공격 타이밍", evidence.refusal_reasons[0])
        self.assertIn("refused", evidence.observed_effects)

    def test_unsupported_expected_effect_is_explicit(self) -> None:
        evidence = classify_micromachine_tactical_evidence(
            latest_telemetry={"frame": 1, "managers": {}},
            expected_effects=("raw_unit_tag_attack",),
        )

        self.assertEqual("unsupported", evidence.status)
        self.assertEqual(("raw_unit_tag_attack",), evidence.unsupported_effects)


if __name__ == "__main__":
    unittest.main()
