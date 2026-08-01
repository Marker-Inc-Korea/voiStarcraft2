"""Tests for authoritative MicroMachine battlefield projection validation."""

from __future__ import annotations

from copy import deepcopy
import json
import unittest

from starcraft_commander.micromachine_battlefield_projection import (
    BattlefieldProjectionIdentity,
    battlefield_overview_fingerprint,
    select_latest_battlefield_projection,
    validate_battlefield_overview,
)


def _identity(
    *,
    update_id: str = "voi-mm-current",
    scope: str = "battlefield",
    session_epoch: int = 1700000000000,
    generation: int = 7,
    stage: str = "observed",
    game_frame: int = 320,
    operation_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "update_id": update_id,
        "scope": scope,
        "session_epoch": session_epoch,
        "generation": generation,
        "stage": stage,
        "game_frame": game_frame,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    return payload


def _empty_transfer_selection_identity() -> dict[str, object]:
    return {
        "update_id": "",
        "source_owner_id": "",
        "counterpart_operation_id": "",
        "source_action": "",
        "counterpart_action": "",
        "source_generation": 0,
        "counterpart_generation": 0,
        "requested_source_generation": 0,
        "requested_counterpart_generation": 0,
        "requested_count": 0,
        "selected_unit_tags": [],
    }


def _transfer_selection_identity() -> dict[str, object]:
    return {
        "update_id": "voi-mm-operation",
        "source_owner_id": "flank-alpha",
        "counterpart_operation_id": "assault-bravo",
        "source_action": "transfer_out",
        "counterpart_action": "transfer_in",
        "source_generation": 3,
        "counterpart_generation": 5,
        "requested_source_generation": 4,
        "requested_counterpart_generation": 6,
        "requested_count": 2,
        "selected_unit_tags": [103, 104],
    }


def _transfer_selection_write_identity(
    *,
    operation_id: str = "",
    operation_generation: int = 0,
    game_frame: int = 0,
    present: bool = False,
) -> dict[str, object]:
    return {
        "update_id": "voi-mm-operation" if present else "",
        "operation_id": operation_id,
        "operation_generation": operation_generation,
        "stage": "effect_observed" if present else "",
        "game_frame": game_frame,
        "selection_identity": (
            _transfer_selection_identity()
            if present
            else _empty_transfer_selection_identity()
        ),
    }


def _operation_transfer_selection(
    *,
    operation_id: str = "",
    operation_generation: int = 0,
    game_frame: int = 0,
    present: bool = False,
) -> dict[str, object]:
    return {
        "present": present,
        "edit_resolution": "pending" if present else "",
        "identity_valid": present,
        "blocker": "",
        "identity": (
            _transfer_selection_identity()
            if present
            else _empty_transfer_selection_identity()
        ),
        "write_identity": _transfer_selection_write_identity(
            operation_id=operation_id,
            operation_generation=operation_generation,
            game_frame=game_frame,
            present=present,
        ),
        "successful_write_acknowledgement": {
            "acknowledged": False,
            "acknowledged_frame": 0,
            "identity": _transfer_selection_write_identity(),
        },
    }


def _operation(
    *,
    update_id: str = "voi-mm-operation",
    operation_id: str = "flank-alpha",
    generation: int = 3,
    game_frame: int = 320,
    owner_tags: tuple[int, ...] = (101, 102, 103, 104),
    standing: bool = False,
    state: str = "active",
    terminal: bool = False,
    reason: str = "",
    completion_frame: int = 0,
) -> dict[str, object]:
    launch_count = len(owner_tags)
    return {
        "identity": _identity(
            update_id=update_id,
            scope=f"operation:{operation_id}",
            generation=generation,
            stage="effect_observed",
            game_frame=game_frame,
            operation_id=operation_id,
        ),
        "operation_id": operation_id,
        "generation": generation,
        "operation_route": {
            "requested_route_type": "flank_right",
            "applied_route_type": "flank_right",
            "location_intent": "enemy_natural",
            "target_type": "enemy_expansion",
            "resolved_target_label": "enemy natural",
            "target_x": 120.0,
            "target_y": 44.0,
            "target_evidence": "observed_enemy_structure",
        },
        "operation_lifetime": {
            "mode": "until_completed",
            "completion_state": state,
            "completion_conditions": [
                "target_reached",
                "cancelled_by_user",
            ],
            "duration_seconds": 300,
            "issued_at_frame": 200,
            "deadline_frame": 4700,
            "standing": standing,
            "completed": terminal,
            "completion_reason": reason,
            "completed_frame": completion_frame,
        },
        "operation_ownership": {
            "owner_count": len(owner_tags),
            "owner_tags": list(owner_tags),
            "integrity_status": "valid",
        },
        "operation_launch_policy": {
            "min_units": 4,
            "max_units": 4,
            "allow_partial_requested": True,
            "strict_scope": False,
            "partial_launch_allowed": True,
            "partial_launch_safe": launch_count > 0,
            "launch_count": launch_count,
            "missing_count": max(0, 4 - launch_count),
            "decision": "launch" if launch_count > 0 else "wait",
            "blocker": "",
            "recommended_choices": [],
            "safety_evidence": {
                "evaluated_at_frame": game_frame,
                "protected_defense_minimum_respected": True,
                "source_operation_minimum_respected": True,
                "transfer_admission": "accepted",
                "emergency_preemption": "none",
            },
        },
        "operation_completion": {
            "movement_observed": True,
            "engagement_observed": False,
            "target_reached": False,
            "terminal": terminal,
            "state": state,
            "reason": reason,
            "frame": completion_frame,
            "generation": generation,
        },
        "operation_transfer_selection": _operation_transfer_selection(),
    }


def _telemetry(
    *,
    update_id: str = "voi-mm-current",
    scope: str = "battlefield",
    generation: int = 7,
    game_frame: int = 320,
) -> dict[str, object]:
    operation = _operation(game_frame=game_frame)
    counterpart = _operation(
        update_id="voi-mm-operation",
        operation_id="assault-bravo",
        generation=5,
        game_frame=game_frame,
        owner_tags=(),
    )
    operation["operation_transfer_selection"] = _operation_transfer_selection(
        operation_id="flank-alpha",
        operation_generation=3,
        game_frame=game_frame,
        present=True,
    )
    counterpart["operation_transfer_selection"] = (
        _operation_transfer_selection(
            operation_id="assault-bravo",
            operation_generation=5,
            game_frame=game_frame,
            present=True,
        )
    )
    return {
        "frame": game_frame,
        "battlefield_overview": {
            "schema_version": 2,
            "authority": "micromachine_cpp",
            "identity": _identity(
                update_id=update_id,
                scope=scope,
                generation=generation,
                game_frame=game_frame,
            ),
            "eligible_combat_count": 8,
            "explicit_operation_owned_count": 4,
            "autonomous_owned_count": 2,
            "unassigned_count": 2,
            "duplicate_owner_count": 0,
            "operation_ownership": [operation, counterpart],
            "autonomous_ownership": [
                {
                    "owner_id": "BaseDefense:main",
                    "owner_count": 2,
                    "owner_tags": [201, 202],
                    "integrity_status": "valid",
                }
            ],
            "unassigned_unit_tags": [301, 302],
            "bases": [
                {
                    "base_id": "main",
                    "semantic_anchor": "self_main",
                    "base_readiness": {
                        "readiness_state": "ready",
                        "reason": "protected_minimum_satisfied",
                        "ground_threat": 2.0,
                        "air_threat": 0.0,
                        "observed_enemy_strength": 2.0,
                        "last_evidence_frame": game_frame - 2,
                        "evidence_class": "observed_enemy_units",
                        "assigned_defender_count": 2,
                        "ground_capable_defender_count": 2,
                        "air_capable_defender_count": 2,
                        "required_defender_count": 2,
                        "required_ground_defender_count": 2,
                        "required_air_defender_count": 0,
                        "protected_minimum": [
                            {
                                "family": "marine",
                                "role": "defender",
                                "count": 2,
                            }
                        ],
                    },
                }
            ],
            "transfer_availability": {
                "evaluated_at_frame": game_frame,
                "atomic_revalidation_required": True,
                "entries": [
                    {
                        "source_owner_id": "flank-alpha",
                        "source_owner_count": 4,
                        "protected_minimum": 2,
                        "transferable_count": 2,
                        "transferable_unit_tags": [103, 104],
                        "transfer_safe": True,
                        "atomic_runtime_blocker": "",
                        "recommended_resolution_choices": [],
                        "safety_evidence": {
                            "evaluated_at_frame": game_frame,
                            "protected_minimum_respected": True,
                            "atomic_revalidation_required": True,
                        },
                        "atomic_revalidation_inputs": {
                            "requested": True,
                            "selected_unit_tags": [103, 104],
                            "requested_count": 2,
                            "source_owner_id": "flank-alpha",
                            "action": "transfer_out",
                            "requested_generation": 3,
                            "counterpart_operation_id": "assault-bravo",
                            "counterpart_action": "transfer_in",
                            "counterpart_generation": 5,
                            "requested_source_generation": 4,
                            "requested_counterpart_generation": 6,
                            "edit_resolution": "pending",
                            "counterpart_present": True,
                            "counterpart_pending": True,
                            "reciprocal_action": True,
                            "reciprocal_counterpart": True,
                            "reciprocal_generation": True,
                            "reciprocal_count": True,
                            "source_active": True,
                            "destination_active": True,
                            "ownership_integrity": True,
                            "operation_assignments_match": True,
                            "squad_assignments_match": True,
                            "action_assignments_match": True,
                            "role_assignments_match": True,
                            "atomic_revalidation_ready": True,
                        },
                    }
                ],
            },
        },
    }


def _blocker_codes(result: object) -> set[str]:
    return {blocker.code for blocker in result.blockers}


class BattlefieldProjectionValidationTest(unittest.TestCase):
    def test_valid_projection_is_exact_deep_copy_passthrough(self) -> None:
        telemetry = _telemetry()
        original = deepcopy(telemetry["battlefield_overview"])

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(original, result.battlefield_overview)
        self.assertIsNot(
            telemetry["battlefield_overview"],
            result.battlefield_overview,
        )
        self.assertEqual("valid", result.integrity["status"])
        self.assertTrue(result.integrity["ownership_partition_valid"])
        self.assertTrue(result.integrity["launch_safety_valid"])
        self.assertTrue(result.integrity["transfer_safety_valid"])
        self.assertEqual(telemetry["battlefield_overview"], original)
        json.dumps(result.to_dict())

    def test_active_standing_operation_does_not_complete_from_movement(self) -> None:
        telemetry = _telemetry()
        overview = telemetry["battlefield_overview"]
        operation = overview["operation_ownership"][0]
        operation["operation_lifetime"]["standing"] = True
        operation["operation_completion"]["movement_observed"] = True
        operation["operation_completion"]["engagement_observed"] = True

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertFalse(
            result.battlefield_overview["operation_ownership"][0][
                "operation_completion"
            ]["terminal"]
        )

    def test_terminal_completion_preserves_reason_frame_and_generation(self) -> None:
        telemetry = _telemetry(game_frame=500)
        overview = telemetry["battlefield_overview"]
        operation = _operation(
            game_frame=500,
            state="completed",
            terminal=True,
            reason="target_reached",
            completion_frame=488,
        )
        operation["operation_transfer_selection"] = deepcopy(
            overview["operation_ownership"][0]["operation_transfer_selection"]
        )
        overview["operation_ownership"][0] = operation

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        completion = result.battlefield_overview["operation_ownership"][0][
            "operation_completion"
        ]
        self.assertEqual("target_reached", completion["reason"])
        self.assertEqual(488, completion["frame"])
        self.assertEqual(3, completion["generation"])

    def test_missing_or_malformed_top_level_identity_fails_closed(self) -> None:
        cases: list[tuple[str, object]] = [
            ("missing", None),
            ("empty_update", {**_identity(), "update_id": ""}),
            ("bool_generation", {**_identity(), "generation": True}),
            ("negative_frame", {**_identity(), "game_frame": -1}),
        ]
        for label, identity in cases:
            with self.subTest(label=label):
                telemetry = _telemetry()
                if identity is None:
                    del telemetry["battlefield_overview"]["identity"]
                else:
                    telemetry["battlefield_overview"]["identity"] = identity

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIsNone(result.battlefield_overview)
                self.assertTrue(
                    {"missing_identity", "invalid_identity"}
                    & _blocker_codes(result)
                )

    def test_authority_and_schema_are_not_inferred(self) -> None:
        for field_name in ("authority", "schema_version"):
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                del telemetry["battlefield_overview"][field_name]

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(
                    (
                        "invalid_authority"
                        if field_name == "authority"
                        else "unsupported_schema_version"
                    ),
                    _blocker_codes(result),
                )

    def test_scope_mismatch_fails_closed(self) -> None:
        result = validate_battlefield_overview(
            _telemetry(scope="match:other"),
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("scope_mismatch", _blocker_codes(result))

    def test_enclosing_telemetry_frame_must_match_projection_frame(self) -> None:
        telemetry = _telemetry()
        telemetry["frame"] = 319

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("telemetry_frame_mismatch", _blocker_codes(result))

    def test_duplicated_identity_fields_must_match(self) -> None:
        telemetry = _telemetry()
        telemetry["battlefield_overview"]["generation"] = 99

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("identity_field_mismatch", _blocker_codes(result))

    def test_operation_owner_count_mismatch_fails_closed(self) -> None:
        telemetry = _telemetry()
        ownership = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_ownership"
        ]
        ownership["owner_count"] = 5

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("owner_count_mismatch", _blocker_codes(result))
        self.assertFalse(result.integrity["owner_counts_valid"])

    def test_owner_integrity_status_must_be_valid(self) -> None:
        telemetry = _telemetry()
        ownership = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_ownership"
        ]
        ownership["integrity_status"] = "mismatch"

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("owner_integrity_invalid", _blocker_codes(result))

    def test_owner_ids_must_be_unique_across_runtime_owners(self) -> None:
        telemetry = _telemetry()
        telemetry["battlefield_overview"]["autonomous_ownership"][0][
            "owner_id"
        ] = "flank-alpha"

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("duplicate_owner_id", _blocker_codes(result))

    def test_duplicate_owner_across_partitions_fails_closed(self) -> None:
        telemetry = _telemetry()
        overview = telemetry["battlefield_overview"]
        overview["autonomous_ownership"][0]["owner_tags"] = [104, 202]
        overview["duplicate_owner_count"] = 1

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("duplicate_owners", _blocker_codes(result))
        self.assertFalse(result.integrity["duplicate_owners_valid"])

    def test_duplicate_owner_report_must_match_tag_evidence(self) -> None:
        telemetry = _telemetry()
        telemetry["battlefield_overview"]["duplicate_owner_count"] = 1

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "duplicate_owner_evidence_mismatch",
            _blocker_codes(result),
        )

    def test_ownership_partition_equation_mismatch_fails_closed(self) -> None:
        telemetry = _telemetry()
        telemetry["battlefield_overview"]["eligible_combat_count"] = 9

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("ownership_equation_mismatch", _blocker_codes(result))
        self.assertFalse(result.integrity["ownership_partition_valid"])

    def test_summary_counts_must_match_owner_rows_and_tags(self) -> None:
        cases = (
            ("explicit_operation_owned_count", 5, "explicit_owner_count_mismatch"),
            ("autonomous_owned_count", 3, "autonomous_owner_count_mismatch"),
            ("unassigned_count", 3, "unassigned_owner_count_mismatch"),
        )
        for field_name, value, code in cases:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                overview = telemetry["battlefield_overview"]
                overview[field_name] = value
                overview["eligible_combat_count"] += 1

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_missing_unassigned_tags_are_not_reconstructed(self) -> None:
        telemetry = _telemetry()
        del telemetry["battlefield_overview"]["unassigned_unit_tags"]

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("missing_owner_tags", _blocker_codes(result))

    def test_all_operation_blocks_are_required_and_preserved(self) -> None:
        block_names = (
            "operation_route",
            "operation_lifetime",
            "operation_ownership",
            "operation_launch_policy",
            "operation_completion",
            "operation_transfer_selection",
        )
        for block_name in block_names:
            with self.subTest(block_name=block_name):
                telemetry = _telemetry()
                del telemetry["battlefield_overview"]["operation_ownership"][0][
                    block_name
                ]

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(
                    "missing_safety_or_state_block",
                    _blocker_codes(result),
                )

    def test_transfer_selection_block_requires_complete_typed_identities(
        self,
    ) -> None:
        cases = (
            ("present", "yes"),
            ("identity_valid", 1),
            ("identity.update_id", None),
            ("identity.source_generation", True),
            ("write_identity.stage", None),
            (
                "successful_write_acknowledgement.acknowledged",
                "false",
            ),
            (
                "successful_write_acknowledgement.acknowledged_frame",
                1.5,
            ),
        )
        for field_path, value in cases:
            with self.subTest(field_path=field_path):
                telemetry = _telemetry()
                selection = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]["operation_transfer_selection"]
                target = selection
                parts = field_path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIsNone(result.battlefield_overview)

    def test_transfer_selection_identity_binds_both_operations(self) -> None:
        cases = (
            (
                "identity.update_id",
                "other-update",
                "transfer_selection_update_mismatch",
            ),
            (
                "identity.source_owner_id",
                "missing-operation",
                "unknown_transfer_selection_operation",
            ),
            (
                "identity.requested_source_generation",
                3,
                "invalid_transfer_selection_generations",
            ),
            (
                "write_identity.update_id",
                "other-update",
                "transfer_selection_write_identity_mismatch",
            ),
            (
                "write_identity.operation_id",
                "assault-bravo",
                "transfer_selection_write_identity_mismatch",
            ),
            (
                "write_identity.operation_generation",
                4,
                "transfer_selection_write_identity_mismatch",
            ),
            (
                "write_identity.stage",
                "submitted",
                "transfer_selection_write_identity_mismatch",
            ),
            (
                "write_identity.game_frame",
                319,
                "transfer_selection_write_identity_mismatch",
            ),
        )
        for field_path, value, code in cases:
            with self.subTest(field_path=field_path):
                telemetry = _telemetry()
                selection = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]["operation_transfer_selection"]
                target = selection
                parts = field_path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_transfer_selection_endpoints_must_publish_same_exact_tags(
        self,
    ) -> None:
        telemetry = _telemetry()
        counterpart_selection = telemetry["battlefield_overview"][
            "operation_ownership"
        ][1]["operation_transfer_selection"]
        counterpart_selection["identity"]["selected_unit_tags"] = [101, 102]
        counterpart_selection["write_identity"]["selection_identity"][
            "selected_unit_tags"
        ] = [101, 102]

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "reciprocal_transfer_selection_mismatch",
            _blocker_codes(result),
        )

    def test_transfer_selection_must_match_atomic_availability_selection(
        self,
    ) -> None:
        telemetry = _telemetry()
        for operation in telemetry["battlefield_overview"][
            "operation_ownership"
        ]:
            selection = operation["operation_transfer_selection"]
            selection["identity"]["selected_unit_tags"] = [999, 1000]
            selection["write_identity"]["selection_identity"][
                "selected_unit_tags"
            ] = [999, 1000]

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "atomic_transfer_selection_evidence_mismatch",
            _blocker_codes(result),
        )

    def test_transfer_selection_resolution_binds_current_generation(self) -> None:
        telemetry = _telemetry()
        operation = telemetry["battlefield_overview"]["operation_ownership"][0]
        selection = operation["operation_transfer_selection"]
        selection["edit_resolution"] = "applied"
        operation["identity"]["generation"] = 4
        operation["generation"] = 4
        operation["operation_completion"]["generation"] = 4
        selection["write_identity"]["operation_generation"] = 4
        atomic_inputs = telemetry["battlefield_overview"][
            "transfer_availability"
        ]["entries"][0]["atomic_revalidation_inputs"]
        atomic_inputs["requested_generation"] = 4
        atomic_inputs["edit_resolution"] = "applied"

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())

        selection["edit_resolution"] = "pending"
        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )
        self.assertFalse(result.ok)
        self.assertIn(
            "transfer_selection_generation_mismatch",
            _blocker_codes(result),
        )

    def test_transfer_selection_rejects_impossible_state_combinations(
        self,
    ) -> None:
        mutations = (
            {"present": False},
            {"identity_valid": False, "blocker": ""},
            {"identity_valid": True, "blocker": "runtime_mismatch"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                telemetry = _telemetry()
                selection = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]["operation_transfer_selection"]
                selection.update(mutation)

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertTrue(
                    {
                        "impossible_transfer_selection_state",
                        "contradictory_transfer_selection_identity",
                        "absent_transfer_selection_identity",
                    }
                    & _blocker_codes(result)
                )

    def test_unacknowledged_transfer_selection_requires_zero_frame_and_identity(
        self,
    ) -> None:
        cases = (
            (
                12,
                _transfer_selection_write_identity(),
                "unacknowledged_transfer_selection_frame",
            ),
            (
                999,
                _transfer_selection_write_identity(),
                "future_transfer_selection_acknowledgement",
            ),
            (
                0,
                _transfer_selection_write_identity(
                    operation_id="flank-alpha",
                    operation_generation=3,
                    game_frame=300,
                    present=True,
                ),
                "unacknowledged_transfer_selection_identity",
            ),
        )
        for frame, identity, code in cases:
            with self.subTest(frame=frame, code=code):
                telemetry = _telemetry()
                acknowledgement = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]["operation_transfer_selection"][
                    "successful_write_acknowledgement"
                ]
                acknowledgement["acknowledged_frame"] = frame
                acknowledgement["identity"] = identity

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_successful_transfer_selection_acknowledgement_is_strictly_bound(
        self,
    ) -> None:
        telemetry = _telemetry()
        acknowledgement = telemetry["battlefield_overview"][
            "operation_ownership"
        ][0]["operation_transfer_selection"][
            "successful_write_acknowledgement"
        ]
        acknowledgement.update(
            {
                "acknowledged": True,
                "acknowledged_frame": 300,
                "identity": _transfer_selection_write_identity(
                    operation_id="flank-alpha",
                    operation_generation=3,
                    game_frame=300,
                    present=True,
                ),
            }
        )

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        preserved = result.battlefield_overview["operation_ownership"][0][
            "operation_transfer_selection"
        ]
        self.assertEqual(acknowledgement, preserved[
            "successful_write_acknowledgement"
        ])

        malformed_cases = (
            ("zero_frame", "acknowledged_frame", 0),
            ("future_frame", "acknowledged_frame", 321),
            ("wrong_update", "identity.update_id", "other-update"),
            ("wrong_operation", "identity.operation_id", "assault-bravo"),
            ("wrong_generation", "identity.operation_generation", 4),
            ("wrong_stage", "identity.stage", "submitted"),
            ("wrong_frame", "identity.game_frame", 299),
        )
        for label, field_path, value in malformed_cases:
            with self.subTest(label=label):
                malformed = deepcopy(telemetry)
                candidate = malformed["battlefield_overview"][
                    "operation_ownership"
                ][0]["operation_transfer_selection"][
                    "successful_write_acknowledgement"
                ]
                target = candidate
                parts = field_path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value

                malformed_result = validate_battlefield_overview(
                    malformed,
                    expected_scope="battlefield",
                )

                self.assertFalse(malformed_result.ok)
                self.assertIn(
                    "malformed_successful_transfer_selection_acknowledgement",
                    _blocker_codes(malformed_result),
                )

    def test_invalid_transfer_selection_cannot_claim_successful_ack(self) -> None:
        telemetry = _telemetry()
        selection = telemetry["battlefield_overview"][
            "operation_ownership"
        ][0]["operation_transfer_selection"]
        selection["identity_valid"] = False
        selection["blocker"] = "transfer_selection_runtime_ownership_mismatch"
        selection["successful_write_acknowledgement"].update(
            {
                "acknowledged": True,
                "acknowledged_frame": 300,
                "identity": _transfer_selection_write_identity(
                    operation_id="flank-alpha",
                    operation_generation=3,
                    game_frame=300,
                    present=True,
                ),
            }
        )

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "impossible_transfer_selection_acknowledgement",
            _blocker_codes(result),
        )

    def test_route_and_lifetime_evidence_are_not_inferred(self) -> None:
        cases = (
            ("operation_route", "target_evidence"),
            ("operation_lifetime", "duration_seconds"),
            ("operation_lifetime", "deadline_frame"),
        )
        for block_name, field_name in cases:
            with self.subTest(block_name=block_name, field_name=field_name):
                telemetry = _telemetry()
                operation = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]
                del operation[block_name][field_name]

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)

    def test_operation_identity_must_match_generation(self) -> None:
        telemetry = _telemetry()
        operation = telemetry["battlefield_overview"]["operation_ownership"][0]
        operation["identity"]["generation"] = 4

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("identity_field_mismatch", _blocker_codes(result))

    def test_operation_identity_is_bound_to_parent_session_and_scope(self) -> None:
        cases = (
            ("session_epoch", 1600000000000, "operation_session_epoch_mismatch"),
            ("scope", "operation:other", "operation_scope_mismatch"),
        )
        for field_name, value, blocker in cases:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                operation = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]
                operation["identity"][field_name] = value

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(blocker, _blocker_codes(result))

    def test_operation_direct_identity_fields_are_required(self) -> None:
        for field_name, code in (
            ("operation_id", "missing_operation_id"),
            ("generation", "missing_operation_generation"),
        ):
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                operation = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]
                del operation[field_name]

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_missing_launch_safety_evidence_is_not_inferred(self) -> None:
        telemetry = _telemetry()
        launch = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_launch_policy"
        ]
        del launch["safety_evidence"]

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "missing_launch_safety_evidence",
            _blocker_codes(result),
        )
        self.assertFalse(result.integrity["launch_safety_valid"])

    def test_launch_safety_cannot_contradict_runtime_evidence(self) -> None:
        telemetry = _telemetry()
        launch = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_launch_policy"
        ]
        launch["safety_evidence"][
            "protected_defense_minimum_respected"
        ] = False

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("contradictory_launch_safety", _blocker_codes(result))

    def test_full_launch_does_not_require_partial_launch_safe(self) -> None:
        telemetry = _telemetry()
        launch = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_launch_policy"
        ]
        launch["allow_partial_requested"] = False
        launch["partial_launch_allowed"] = False
        launch["partial_launch_safe"] = False

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())

    def test_partial_launch_requires_requested_allowed_and_safe_evidence(
        self,
    ) -> None:
        for field_name in (
            "allow_partial_requested",
            "partial_launch_allowed",
            "partial_launch_safe",
        ):
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                launch = telemetry["battlefield_overview"][
                    "operation_ownership"
                ][0]["operation_launch_policy"]
                launch["launch_count"] = 3
                launch["missing_count"] = 1
                launch[field_name] = False

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn("unsafe_launch_decision", _blocker_codes(result))

    def test_launch_safety_evidence_must_match_projection_frame(self) -> None:
        telemetry = _telemetry()
        launch = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_launch_policy"
        ]
        launch["safety_evidence"]["evaluated_at_frame"] = 319

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("stale_launch_safety_evidence", _blocker_codes(result))

    def test_launch_count_cannot_exceed_bounds_or_authoritative_owners(self) -> None:
        telemetry = _telemetry()
        launch = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_launch_policy"
        ]
        launch["launch_count"] = 999

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("launch_count_exceeds_maximum", _blocker_codes(result))
        self.assertIn("launch_count_exceeds_owner_count", _blocker_codes(result))

    def test_launch_decision_requires_nonzero_force(self) -> None:
        telemetry = _telemetry()
        launch = telemetry["battlefield_overview"]["operation_ownership"][0][
            "operation_launch_policy"
        ]
        launch["launch_count"] = 0
        launch["missing_count"] = 4
        launch["partial_launch_safe"] = True
        launch["decision"] = "launch"

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("empty_launch_decision", _blocker_codes(result))

    def test_base_readiness_requires_protected_minimum_and_threat_evidence(self) -> None:
        field_paths = (
            "protected_minimum",
            "last_evidence_frame",
            "evidence_class",
            "required_defender_count",
        )
        for field_name in field_paths:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                readiness = telemetry["battlefield_overview"]["bases"][0][
                    "base_readiness"
                ]
                del readiness[field_name]

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertFalse(result.integrity["base_readiness_valid"])

    def test_transfer_safety_is_required_and_not_derived(self) -> None:
        telemetry = _telemetry()
        entry = telemetry["battlefield_overview"]["transfer_availability"][
            "entries"
        ][0]
        del entry["safety_evidence"]

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "missing_transfer_safety_evidence",
            _blocker_codes(result),
        )
        self.assertFalse(result.integrity["transfer_safety_valid"])

    def test_transfer_cannot_cross_protected_minimum(self) -> None:
        telemetry = _telemetry()
        entry = telemetry["battlefield_overview"]["transfer_availability"][
            "entries"
        ][0]
        entry["protected_minimum"] = 3

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "transfer_protected_minimum_violation",
            _blocker_codes(result),
        )

    def test_transfer_source_count_and_tags_must_match_named_owner(self) -> None:
        cases = (
            (
                "source_owner_count",
                3,
                "transfer_source_owner_count_mismatch",
            ),
            (
                "transferable_unit_tags",
                [201, 202],
                "transfer_source_owner_mismatch",
            ),
            (
                "source_owner_id",
                "missing-owner",
                "unknown_transfer_source_owner",
            ),
        )
        for field_name, value, code in cases:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                entry = telemetry["battlefield_overview"][
                    "transfer_availability"
                ]["entries"][0]
                entry[field_name] = value

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_atomic_revalidation_accepts_exact_requested_selection(self) -> None:
        telemetry = _telemetry()

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        inputs = result.battlefield_overview["transfer_availability"][
            "entries"
        ][0]["atomic_revalidation_inputs"]
        self.assertEqual([103, 104], inputs["selected_unit_tags"])
        self.assertEqual(2, inputs["requested_count"])
        self.assertEqual("flank-alpha", inputs["source_owner_id"])
        self.assertEqual("transfer_out", inputs["action"])
        self.assertEqual(3, inputs["requested_generation"])
        self.assertEqual("assault-bravo", inputs["counterpart_operation_id"])
        self.assertEqual("transfer_in", inputs["counterpart_action"])
        self.assertEqual(5, inputs["counterpart_generation"])

    def test_availability_preserves_multiple_destination_endpoints(self) -> None:
        telemetry = _telemetry()
        overview = telemetry["battlefield_overview"]
        reserve = _operation(
            operation_id="reserve-charlie",
            generation=7,
            owner_tags=(),
        )
        overview["operation_ownership"].append(reserve)
        entry = overview["transfer_availability"]["entries"][0]
        entry["recommended_resolution_choices"] = [
            "transfer_available_units",
            "transfer_two_units",
        ]
        inputs = entry["atomic_revalidation_inputs"]
        inputs.update(
            {
                "requested": False,
                "requested_count": 0,
                "action": "availability",
                "edit_resolution": "none",
                "counterpart_action": "",
                "requested_source_generation": 3,
                "requested_counterpart_generation": 5,
                "counterpart_pending": False,
            }
        )
        second = deepcopy(entry)
        second_inputs = second["atomic_revalidation_inputs"]
        second_inputs["counterpart_operation_id"] = "reserve-charlie"
        second_inputs["counterpart_generation"] = 7
        second_inputs["requested_counterpart_generation"] = 7
        overview["transfer_availability"]["entries"].append(second)

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        endpoints = {
            (
                item["source_owner_id"],
                item["atomic_revalidation_inputs"]["counterpart_operation_id"],
            )
            for item in result.battlefield_overview[
                "transfer_availability"
            ]["entries"]
        }
        self.assertEqual(
            {
                ("flank-alpha", "assault-bravo"),
                ("flank-alpha", "reserve-charlie"),
            },
            endpoints,
        )

    def test_availability_rejects_duplicate_endpoint_and_bad_recommendation(
        self,
    ) -> None:
        telemetry = _telemetry()
        overview = telemetry["battlefield_overview"]
        entry = overview["transfer_availability"]["entries"][0]
        entry["recommended_resolution_choices"] = ["transfer_two_units"]
        inputs = entry["atomic_revalidation_inputs"]
        inputs.update(
            {
                "requested": False,
                "requested_count": 0,
                "action": "availability",
                "edit_resolution": "none",
                "counterpart_action": "",
                "requested_source_generation": 3,
                "requested_counterpart_generation": 5,
                "counterpart_pending": False,
            }
        )
        overview["transfer_availability"]["entries"].append(deepcopy(entry))

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("duplicate_transfer_endpoint", _blocker_codes(result))
        self.assertIn("transfer_recommendation_mismatch", _blocker_codes(result))

    def test_availability_rejects_stale_destination_generation(self) -> None:
        telemetry = _telemetry()
        entry = telemetry["battlefield_overview"]["transfer_availability"][
            "entries"
        ][0]
        entry["recommended_resolution_choices"] = [
            "transfer_available_units",
            "transfer_two_units",
        ]
        inputs = entry["atomic_revalidation_inputs"]
        inputs.update(
            {
                "requested": False,
                "requested_count": 0,
                "action": "availability",
                "edit_resolution": "none",
                "counterpart_action": "",
                "counterpart_generation": 6,
                "requested_source_generation": 3,
                "requested_counterpart_generation": 6,
                "counterpart_pending": False,
            }
        )

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "atomic_counterpart_generation_mismatch",
            _blocker_codes(result),
        )

    def test_atomic_revalidation_inputs_and_exact_fields_are_required(
        self,
    ) -> None:
        telemetry = _telemetry()
        entry = telemetry["battlefield_overview"]["transfer_availability"][
            "entries"
        ][0]
        del entry["atomic_revalidation_inputs"]

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "missing_atomic_revalidation_inputs",
            _blocker_codes(result),
        )

        required_fields = (
            "requested",
            "selected_unit_tags",
            "requested_count",
            "source_owner_id",
            "action",
            "requested_generation",
            "counterpart_operation_id",
            "counterpart_action",
            "counterpart_generation",
            "requested_source_generation",
            "requested_counterpart_generation",
            "edit_resolution",
            "counterpart_present",
            "counterpart_pending",
            "reciprocal_action",
            "reciprocal_counterpart",
            "reciprocal_generation",
            "reciprocal_count",
            "source_active",
            "destination_active",
            "ownership_integrity",
            "operation_assignments_match",
            "squad_assignments_match",
            "action_assignments_match",
            "role_assignments_match",
            "atomic_revalidation_ready",
        )
        for field_name in required_fields:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                inputs = telemetry["battlefield_overview"][
                    "transfer_availability"
                ]["entries"][0]["atomic_revalidation_inputs"]
                del inputs[field_name]

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(
                    "missing_atomic_revalidation_input",
                    _blocker_codes(result),
                )

    def test_atomic_revalidation_rejects_mismatched_values(self) -> None:
        cases = (
            (
                "selected_unit_tags",
                [104, 103],
                "atomic_selected_tags_mismatch",
            ),
            (
                "requested_count",
                1,
                "atomic_requested_count_mismatch",
            ),
            (
                "source_owner_id",
                "assault-bravo",
                "atomic_source_owner_mismatch",
            ),
            (
                "action",
                "transfer_in",
                "atomic_source_action_mismatch",
            ),
            (
                "requested_generation",
                4,
                "atomic_source_generation_mismatch",
            ),
            (
                "counterpart_operation_id",
                "missing-operation",
                "atomic_counterpart_operation_mismatch",
            ),
            (
                "counterpart_action",
                "transfer_out",
                "atomic_counterpart_action_mismatch",
            ),
            (
                "counterpart_generation",
                6,
                "atomic_counterpart_generation_mismatch",
            ),
            (
                "requested_source_generation",
                5,
                "atomic_transfer_selection_evidence_mismatch",
            ),
            (
                "requested_counterpart_generation",
                7,
                "atomic_transfer_selection_evidence_mismatch",
            ),
            (
                "edit_resolution",
                "blocked",
                "atomic_edit_resolution_mismatch",
            ),
        )
        for field_name, value, code in cases:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                inputs = telemetry["battlefield_overview"][
                    "transfer_availability"
                ]["entries"][0]["atomic_revalidation_inputs"]
                inputs[field_name] = value

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_atomic_revalidation_requires_every_runtime_readiness_flag(
        self,
    ) -> None:
        readiness_fields = (
            "counterpart_present",
            "counterpart_pending",
            "reciprocal_action",
            "reciprocal_counterpart",
            "reciprocal_generation",
            "reciprocal_count",
            "source_active",
            "destination_active",
            "ownership_integrity",
            "operation_assignments_match",
            "squad_assignments_match",
            "action_assignments_match",
            "role_assignments_match",
            "atomic_revalidation_ready",
        )
        for field_name in readiness_fields:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry()
                inputs = telemetry["battlefield_overview"][
                    "transfer_availability"
                ]["entries"][0]["atomic_revalidation_inputs"]
                inputs[field_name] = False

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(
                    "atomic_revalidation_not_ready",
                    _blocker_codes(result),
                )

    def test_atomic_selected_tags_must_belong_to_source_and_transferable_set(
        self,
    ) -> None:
        telemetry = _telemetry()
        entry = telemetry["battlefield_overview"]["transfer_availability"][
            "entries"
        ][0]
        entry["transferable_unit_tags"] = [201, 202]
        inputs = entry["atomic_revalidation_inputs"]
        inputs["selected_unit_tags"] = [201, 202]

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("transfer_source_owner_mismatch", _blocker_codes(result))
        self.assertIn(
            "atomic_selected_source_ownership_mismatch",
            _blocker_codes(result),
        )

    def test_transfer_requires_atomic_revalidation(self) -> None:
        telemetry = _telemetry()
        transfer = telemetry["battlefield_overview"]["transfer_availability"]
        transfer["atomic_revalidation_required"] = False

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "atomic_transfer_revalidation_disabled",
            _blocker_codes(result),
        )

    def test_terminal_completion_requires_reason_frame_and_matching_generation(
        self,
    ) -> None:
        cases = (
            ("reason", "", "missing_terminal_completion_identity"),
            ("frame", 0, "missing_terminal_completion_identity"),
            ("generation", 99, "completion_generation_mismatch"),
        )
        for field_name, value, code in cases:
            with self.subTest(field_name=field_name):
                telemetry = _telemetry(game_frame=500)
                operation = _operation(
                    game_frame=500,
                    state="completed",
                    terminal=True,
                    reason="target_reached",
                    completion_frame=488,
                )
                operation["operation_completion"][field_name] = value
                if field_name == "reason":
                    operation["operation_lifetime"]["completion_reason"] = value
                elif field_name == "frame":
                    operation["operation_lifetime"]["completed_frame"] = value
                operation["operation_transfer_selection"] = deepcopy(
                    telemetry["battlefield_overview"]["operation_ownership"][0][
                        "operation_transfer_selection"
                    ]
                )
                telemetry["battlefield_overview"]["operation_ownership"][0] = (
                    operation
                )

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_terminal_completion_cannot_predate_operation_issue(self) -> None:
        telemetry = _telemetry(game_frame=500)
        operation = _operation(
            game_frame=500,
            state="completed",
            terminal=True,
            reason="target_reached",
            completion_frame=100,
        )
        operation["operation_transfer_selection"] = deepcopy(
            telemetry["battlefield_overview"]["operation_ownership"][0][
                "operation_transfer_selection"
            ]
        )
        telemetry["battlefield_overview"]["operation_ownership"][0] = operation

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "completion_before_operation_issue",
            _blocker_codes(result),
        )

    def test_previous_identity_rejects_stale_generation_or_frame(self) -> None:
        previous = BattlefieldProjectionIdentity(
            update_id="previous",
            scope="battlefield",
            session_epoch=1700000000000,
            generation=8,
            stage="observed",
            game_frame=400,
        )
        cases = (
            (_telemetry(generation=7, game_frame=410), "stale_generation"),
            (_telemetry(generation=8, game_frame=399), "stale_game_frame"),
        )
        for telemetry, code in cases:
            with self.subTest(code=code):
                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                    previous_identity=previous,
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))
                self.assertFalse(result.integrity["monotonic"])

    def test_newer_session_epoch_allows_safe_game_frame_reset(self) -> None:
        previous = BattlefieldProjectionIdentity(
            update_id="previous",
            scope="battlefield",
            session_epoch=1700000000000,
            generation=800,
            stage="observed",
            game_frame=5000,
        )
        telemetry = _telemetry(generation=1, game_frame=320)
        telemetry["battlefield_overview"]["identity"]["session_epoch"] = (
            1700000000100
        )
        for operation in telemetry["battlefield_overview"]["operation_ownership"]:
            operation["identity"]["session_epoch"] = 1700000000100

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
            previous_identity=previous,
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertTrue(result.integrity["monotonic"])

    def test_retired_session_epoch_cannot_replace_current_game(self) -> None:
        previous = BattlefieldProjectionIdentity(
            update_id="current",
            scope="battlefield",
            session_epoch=1700000000200,
            generation=4,
            stage="observed",
            game_frame=40,
        )

        result = validate_battlefield_overview(
            _telemetry(generation=900, game_frame=9000),
            expected_scope="battlefield",
            previous_identity=previous,
        )

        self.assertFalse(result.ok)
        self.assertIn("stale_session_epoch", _blocker_codes(result))

    def test_ready_base_requires_compatible_ground_and_air_defenders(self) -> None:
        cases = (
            (
                {
                    "ground_threat": 0.0,
                    "air_threat": 1.0,
                    "observed_enemy_strength": 1.0,
                    "ground_capable_defender_count": 2,
                    "air_capable_defender_count": 0,
                    "required_defender_count": 1,
                    "required_ground_defender_count": 0,
                    "required_air_defender_count": 1,
                },
                "incompatible_defender_readiness",
            ),
            (
                {
                    "ground_threat": 1.0,
                    "air_threat": 0.0,
                    "observed_enemy_strength": 1.0,
                    "ground_capable_defender_count": 0,
                    "air_capable_defender_count": 2,
                    "required_defender_count": 1,
                    "required_ground_defender_count": 1,
                    "required_air_defender_count": 0,
                },
                "incompatible_defender_readiness",
            ),
        )
        for updates, code in cases:
            with self.subTest(updates=updates):
                telemetry = _telemetry()
                readiness = telemetry["battlefield_overview"]["bases"][0][
                    "base_readiness"
                ]
                readiness.update(updates)

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(code, _blocker_codes(result))

    def test_ready_base_whitespace_cannot_bypass_capability_check(self) -> None:
        telemetry = _telemetry()
        readiness = telemetry["battlefield_overview"]["bases"][0][
            "base_readiness"
        ]
        readiness.update(
            {
                "readiness_state": "ready ",
                "ground_threat": 0.0,
                "air_threat": 1.0,
                "observed_enemy_strength": 1.0,
                "ground_capable_defender_count": 2,
                "air_capable_defender_count": 0,
                "required_defender_count": 1,
                "required_ground_defender_count": 0,
                "required_air_defender_count": 1,
            }
        )

        result = validate_battlefield_overview(
            telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn("incompatible_defender_readiness", _blocker_codes(result))

    def test_non_finite_route_and_threat_values_fail_closed(self) -> None:
        cases = (
            ("route_nan", "target_x", float("nan"), "invalid_state_value"),
            (
                "route_infinity",
                "target_y",
                float("inf"),
                "invalid_state_value",
            ),
            (
                "threat_nan",
                "ground_threat",
                float("nan"),
                "invalid_safety_evidence_value",
            ),
            (
                "threat_infinity",
                "air_threat",
                float("inf"),
                "invalid_safety_evidence_value",
            ),
        )
        for label, field_name, value, blocker in cases:
            with self.subTest(label=label):
                telemetry = _telemetry()
                if label.startswith("route"):
                    telemetry["battlefield_overview"]["operation_ownership"][0][
                        "operation_route"
                    ][field_name] = value
                else:
                    telemetry["battlefield_overview"]["bases"][0][
                        "base_readiness"
                    ][field_name] = value

                result = validate_battlefield_overview(
                    telemetry,
                    expected_scope="battlefield",
                )

                self.assertFalse(result.ok)
                self.assertIn(blocker, _blocker_codes(result))

    def test_non_finite_unknown_field_cannot_crash_fingerprint(self) -> None:
        telemetry = _telemetry()
        telemetry["battlefield_overview"]["unknown_runtime_state"] = {
            "unsafe": float("nan"),
        }

        result = select_latest_battlefield_projection(
            latest_telemetry=telemetry,
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "non_finite_projection_value",
            _blocker_codes(result),
        )


class BattlefieldProjectionSelectionTest(unittest.TestCase):
    def test_selects_latest_monotonic_same_scope_projection(self) -> None:
        archive = (
            _telemetry(update_id="one", generation=5, game_frame=200),
            _telemetry(
                update_id="other",
                scope="match:other",
                generation=99,
                game_frame=999,
            ),
            _telemetry(update_id="two", generation=6, game_frame=250),
        )
        latest = _telemetry(
            update_id="three",
            generation=7,
            game_frame=320,
        )

        result = select_latest_battlefield_projection(
            latest_telemetry=latest,
            telemetry_archive=archive,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("three", result.identity.update_id)
        self.assertEqual(320, result.identity.game_frame)
        self.assertEqual("latest", result.source)
        self.assertEqual((), result.rejected_candidates)

    def test_archive_stale_candidate_is_rejected_without_regression(self) -> None:
        archive = (
            _telemetry(update_id="one", generation=6, game_frame=250),
            _telemetry(update_id="stale", generation=5, game_frame=260),
            _telemetry(update_id="two", generation=7, game_frame=300),
        )

        result = select_latest_battlefield_projection(
            telemetry_archive=archive,
            expected_scope="battlefield",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual("two", result.identity.update_id)
        self.assertEqual("valid_with_rejections", result.integrity["status"])
        self.assertEqual(1, len(result.rejected_candidates))
        self.assertIn(
            "stale_generation",
            {
                blocker.code
                for blocker in result.rejected_candidates[0].blockers
            },
        )

    def test_malformed_latest_blocks_instead_of_falling_back(self) -> None:
        latest = _telemetry(update_id="latest", generation=8, game_frame=400)
        del latest["battlefield_overview"]["transfer_availability"]

        result = select_latest_battlefield_projection(
            latest_telemetry=latest,
            telemetry_archive=(
                _telemetry(update_id="archive", generation=7, game_frame=350),
            ),
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIsNone(result.battlefield_overview)
        self.assertEqual("latest", result.source)
        self.assertIn(
            "missing_transfer_safety_evidence",
            _blocker_codes(result),
        )

    def test_stale_latest_blocks_instead_of_falling_back(self) -> None:
        result = select_latest_battlefield_projection(
            latest_telemetry=_telemetry(
                update_id="stale",
                generation=6,
                game_frame=360,
            ),
            telemetry_archive=(
                _telemetry(
                    update_id="archive",
                    generation=7,
                    game_frame=350,
                ),
            ),
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIsNone(result.battlefield_overview)
        self.assertIn("stale_generation", _blocker_codes(result))

    def test_exact_replay_is_accepted_but_identity_collision_is_blocked(
        self,
    ) -> None:
        archive = _telemetry(update_id="same", generation=7, game_frame=320)
        replay = deepcopy(archive)
        replay_result = select_latest_battlefield_projection(
            latest_telemetry=replay,
            telemetry_archive=(archive,),
            expected_scope="battlefield",
        )
        self.assertTrue(replay_result.ok, replay_result.to_dict())

        collision = deepcopy(replay)
        collision["battlefield_overview"]["bases"][0]["base_readiness"][
            "reason"
        ] = "changed_without_identity_advance"
        collision_result = select_latest_battlefield_projection(
            latest_telemetry=collision,
            telemetry_archive=(archive,),
            expected_scope="battlefield",
        )
        self.assertFalse(collision_result.ok)
        self.assertIn("identity_collision", _blocker_codes(collision_result))

    def test_previous_poll_fingerprint_rejects_identity_payload_mutation(
        self,
    ) -> None:
        accepted = validate_battlefield_overview(
            _telemetry(update_id="same", generation=7, game_frame=320),
            expected_scope="battlefield",
        )
        assert accepted.identity is not None
        assert accepted.battlefield_overview is not None
        mutated = _telemetry(update_id="same", generation=7, game_frame=320)
        mutated["battlefield_overview"]["bases"][0]["base_readiness"][
            "reason"
        ] = "changed_on_later_poll"

        result = select_latest_battlefield_projection(
            latest_telemetry=mutated,
            expected_scope="battlefield",
            previous_identity=accepted.identity,
            previous_payload_fingerprint=battlefield_overview_fingerprint(
                accepted.battlefield_overview
            ),
        )

        self.assertFalse(result.ok)
        self.assertIn("identity_collision", _blocker_codes(result))

    def test_returns_explicit_no_projection_blocker(self) -> None:
        result = select_latest_battlefield_projection(
            telemetry_archive=(),
            expected_scope="battlefield",
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "no_valid_battlefield_projection",
            _blocker_codes(result),
        )
        self.assertEqual("blocked", result.integrity["status"])


if __name__ == "__main__":
    unittest.main()
