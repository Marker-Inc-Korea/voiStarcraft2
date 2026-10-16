"""Conservative audit of QA30 recordings, independent of launcher verdicts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from starcraft_commander.live_qa_observation_audit import ObservationAudit


def archive_lines(path: Path):
    with path.open() as stream:
        yield from enumerate(stream, 1)


def concurrent_ownership(overview, expected, update_id, frame):
    """Require distinct operations to own disjoint tags in the same snapshot."""
    if overview.get("ownership_integrity") != "valid":
        return {}
    owners = {}
    for entry in overview.get("operation_ownership", []):
        key = (entry.get("operation_id"), entry.get("generation"))
        identity = entry.get("identity", {})
        ownership = entry.get("operation_ownership", {})
        tags = ownership.get("owner_tags", [])
        if (
            key not in expected
            or identity.get("update_id") != update_id
            or identity.get("operation_id") != key[0]
            or identity.get("generation") != key[1]
            or identity.get("game_frame") != frame
            or ownership.get("integrity_status") != "valid"
            or ownership.get("owner_count") != len(tags)
            or not tags
            or len(set(tags)) != len(tags)
        ):
            continue
        if key in owners:
            return {}
        owners[key] = set(tags)
    if len({key[0] for key in owners}) < 2:
        return {}
    groups = list(owners.values())
    if any(
        not left.isdisjoint(right)
        for index, left in enumerate(groups)
        for right in groups[index + 1:]
    ):
        return {}
    return {operation_id: sorted(tags) for (operation_id, _), tags in owners.items()}


def operation_observations(director, expected, update_ids, issued, frame, records):
    """Collect scoped observations without treating movement as goal completion."""
    if director.get("policy_update_id") not in update_ids:
        return
    for operation in director.get("operations", []):
        operation_id = operation.get("operation_id")
        generation = operation.get("generation")
        if (operation_id, generation) not in expected:
            continue
        record_key = json.dumps([director["policy_update_id"], operation_id, generation])
        record = records.setdefault(record_key, {
            "operation_id": operation_id,
            "update_id": director["policy_update_id"],
            "generation": generation, "effects": {}, "last_status": None,
        })
        record["last_status"] = operation.get("status")
        record["last_frame"] = frame
        record["task_type"] = operation.get("task_type", "")
        record["squad_order"] = operation.get("squad_order", "")
        record["completion_conditions"] = operation.get("completion_conditions", "")
        if operation.get("retreat_confirmed"):
            record.setdefault("retreat_confirmed_frame", frame)
        record["blocked_reason"] = operation.get("blocked_reason", "")
        record["last_action"] = operation.get("last_action", "")
        record["assigned_count"] = operation.get("assigned_count", len(
            operation.get("assigned_unit_tags", [])
        ))
        record["last_unit_tags"] = operation.get("assigned_unit_tags", [])
        record["lifetime_mode"] = operation.get("lifetime_mode", "")
        record["duration_seconds"] = operation.get("duration_seconds", 0)
        record["issued_at_frame"] = issued
        tags = set(operation.get("assigned_unit_tags", []))
        if tags:
            record.setdefault("assigned_frame", frame)
            record.setdefault("unit_tags", sorted(tags))
        if operation.get("status") in {"COMPLETED", "CANCELLED", "EXPIRED"}:
            record.setdefault("terminal_frame", frame)
            record["terminal_status"] = operation["status"]
        for evidence in operation.get("family_evidence", []):
            if (
                evidence.get("update_id") != director["policy_update_id"]
                or evidence.get("operation_id") != operation_id
                or evidence.get("generation") != generation
            ):
                continue
            submitted = evidence.get("submitted_frame", -1)
            effect = evidence.get("effect_frame", -1)
            submitted_tags = set(evidence.get("submitted_unit_tags", []))
            effect_tags = set(evidence.get("effect_unit_tags", []))
            if issued <= submitted <= frame and submitted_tags and evidence.get(
                "submitted_count", 0
            ) > 0:
                record.setdefault("submitted_frame", submitted)
            if (
                issued <= submitted <= effect <= frame
                and evidence.get("submitted_count", 0) > 0
                and evidence.get("effect_count", 0) > 0
                and effect_tags
                and effect_tags <= submitted_tags
            ):
                key = f'{evidence.get("action")}:{evidence.get("effect_kind")}'
                record["effects"].setdefault(key, {
                    "submitted_frame": submitted,
                    "effect_frame": effect,
                    "unit_tags": sorted(effect_tags),
                })


def audit_attempt(root: Path, scenario: dict) -> dict:
    report = {
        "id": scenario["id"],
        "status": "unverified",
        "observations": {},
        "missing_evidence": list(scenario["evidence"]),
        "input_boundary": "structured_blackboard_not_natural_language_or_voice",
        "errors": [],
    }
    receipts = sorted(root.glob("qa30_command_publish*.json"))
    archive = root / "telemetry.jsonl"
    if not receipts or not archive.exists():
        report["errors"].append("missing_command_receipt_or_telemetry_archive")
        return report
    commands = [json.loads(path.read_text()) for path in receipts]
    if any(command.get("scenario_id") != scenario["id"] for command in commands):
        report["errors"].append("scenario_identity_mismatch")
        return report
    update_ids = {command["update_id"] for command in commands}
    issued = min(command["issued_at_frame"] for command in commands)
    observations = report["observations"]
    runtime = None
    previous_frame = -1
    baseline_workers = None
    action_frames = {}
    action_frame_history = {}
    commands_by_id = {command["update_id"]: command for command in commands}
    operations = {}
    production_lifecycle = []
    raw_observations = ObservationAudit()
    for number, line in archive_lines(archive):
        try:
            row = json.loads(line)
        except ValueError:
            report["errors"].append(f"invalid_telemetry_line:{number}")
            continue
        frame = row.get("frame", -1)
        if frame < issued:
            continue
        identity = row.get("runtime_instance_id")
        if not identity or not row.get("runtime_instance_id_valid"):
            report["errors"].append(f"invalid_runtime_identity:{number}")
            continue
        runtime = runtime or identity
        if identity != runtime:
            report["errors"].append(f"mixed_runtime_identity:{number}")
            continue
        if frame < previous_frame:
            report["errors"].append(f"frame_regressed:{number}")
            continue
        previous_frame = frame
        managers = row.get("managers", {})
        director = managers.get("OperationDirector", {})
        operation_command = commands_by_id.get(director.get("policy_update_id"))
        if operation_command and frame >= operation_command["issued_at_frame"]:
            expected_operations = {
                (op["operation_id"], op.get("generation", 1))
                for op in operation_command.get("payload", {}).get("operations", [])
            }
            operation_observations(
                director, expected_operations, {operation_command["update_id"]},
                operation_command["issued_at_frame"], frame, operations,
            )
        production = managers.get("ProductionManager", {})
        if production.get("policy_update_id") not in update_ids:
            continue
        if frame < commands_by_id[production["policy_update_id"]]["issued_at_frame"]:
            continue
        production_lifecycle.append({
            "frame": frame,
            "owner_release_count": production.get(
                "operation_production_owner_release_count", 0
            ),
            "queue_purge_count": production.get(
                "operation_production_queue_purge_count", 0
            ),
            "released_owner": production.get(
                "last_operation_production_released_owner", ""
            ),
            "purge_event": production.get(
                "last_operation_production_queue_purge_event", {}
            ),
        })
        if operation_command:
            lease = production.get("policy_expires_at_frame", 0)
            if lease > operation_command["issued_at_frame"] and frame > lease:
                for record in operations.values():
                    if (
                        record["update_id"] == operation_command["update_id"]
                        and record["last_frame"] == frame
                        and record.get("lifetime_mode") == "standing_order"
                        and record.get("last_status") in {"SUBMITTED", "MOVING", "ENGAGED"}
                        and record.get("last_unit_tags")
                        and record.get("effects")
                    ):
                        record.setdefault("alive_after_lease_frame", frame)
        observations.setdefault("command_consumed", frame)
        observations["last_frame"] = frame
        if production.get("last_actual_production_command_update_id") in update_ids:
            action_frame = production.get("last_actual_production_command_frame", -1)
            action_command = commands_by_id[production["last_actual_production_command_update_id"]]
            if action_command["issued_at_frame"] <= action_frame <= frame:
                action = production.get("last_actual_production_command", "")
                if action and action != "none|none":
                    action_frames.setdefault(action, action_frame)
                    action_frame_history.setdefault(action, []).append(action_frame)
        scoped_tags = set()
        for record in operations.values():
            if (
                record.get("last_frame") == frame
                and record.get("update_id") == director.get("policy_update_id")
            ):
                terminal_frame = record.get("terminal_frame")
                # The terminal snapshot can contain the first observable
                # effect (for example a morph order). Keep the operation's
                # last owned tags in scope through that exact frame, but
                # never carry them into later autonomous observations.
                if (
                    record.get("last_status") not in {
                        "COMPLETED", "CANCELLED", "EXPIRED"
                    }
                    or terminal_frame == frame
                ):
                    scoped_tags.update(
                        record.get("last_unit_tags", [])
                        or record.get("unit_tags", [])
                    )
        raw_observations.observe(
            row.get("sc2_observation"), frame, action_frames,
            scoped_unit_tags=scoped_tags,
            supply=production,
            supply_provider_frame=(
                production.get("last_supply_provider_command_frame")
                if production.get("last_supply_provider_command_update_id") in update_ids
                and production.get("last_supply_provider_command_kind") == "build_command"
                else None
            ),
        )
        overview = row.get("battlefield_overview", {})
        if scenario["id"] == "20-retreat" and operation_command:
            # A retreat normally has no operation-owned production queue.
            # Its ownership release is therefore observed in the battlefield
            # projection when the terminal operation has released all tags.
            for record in operations.values():
                if (
                    record.get("update_id") != operation_command["update_id"]
                    or record.get("terminal_status") != "COMPLETED"
                    or record.get("last_status") != "COMPLETED"
                    or not record.get("unit_tags")
                    or record.get("last_frame") != frame
                    or record.get("assigned_count") != 0
                    or record.get("last_unit_tags") != []
                    or overview.get("ownership_integrity") != "valid"
                ):
                    continue
                for ownership in overview.get("operation_ownership", []):
                    identity = ownership.get("identity", {})
                    owner = ownership.get("operation_ownership", {})
                    if (
                        ownership.get("operation_id")
                        == record.get("operation_id")
                        and ownership.get("generation")
                        == record.get("generation")
                        and identity.get("update_id") == record["update_id"]
                        and identity.get("operation_id") == record["operation_id"]
                        and identity.get("generation") == record["generation"]
                        and identity.get("game_frame") == frame
                        and owner.get("integrity_status") == "valid"
                        and owner.get("owner_count") == 0
                        and owner.get("owner_tags") == []
                    ):
                        record.setdefault("ownership_released_frame", frame)
                        break
        if operation_command:
            owners = concurrent_ownership(
                overview, expected_operations, operation_command["update_id"], frame
            )
            if owners:
                observations.setdefault("concurrent_ownership", {
                    "frame": frame, "update_id": operation_command["update_id"],
                    "owners": owners,
                })
        if scenario["id"] == "29-heal-regroup":
            autonomous_tags = {
                tag for owner in overview.get("autonomous_ownership", [])
                for tag in owner.get("owner_tags", [])
            }
            explicit_tags = {
                tag for owner in overview.get("operation_ownership", [])
                for tag in owner.get("operation_ownership", {}).get("owner_tags", [])
            }
            if overview.get("ownership_integrity") == "valid":
                for record in operations.values():
                    assigned_tags = set(record.get("unit_tags", []))
                    if (
                        record.get("terminal_status") in {
                            "COMPLETED", "CANCELLED", "EXPIRED"
                        }
                        and assigned_tags
                        and assigned_tags <= autonomous_tags
                        and assigned_tags.isdisjoint(explicit_tags)
                        and frame >= record["terminal_frame"]
                    ):
                        observations.setdefault("autonomous_owner_restored", frame)
            if any(
                (
                    str(effect_key).split(":", 1)[0] == "squad_order"
                    and str(effect_key).split(":", 2)[1:2] == ["regroup"]
                )
                or str(effect_key).rsplit(":", 1)[-1] == "regroup"
                for record in operations.values()
                for effect_key in record.get("effects", {})
            ):
                observations.setdefault("regroup_action", frame)
            elif any(
                record.get("task_type") == "regroup"
                and record.get("squad_order") == "regroup"
                and record.get("submitted_frame") is not None
                and record.get("effects")
                for record in operations.values()
            ):
                # The runtime records the regroup order on the operation and
                # the resulting SC2 movement/engagement under the unit action
                # family. Both are required here.
                observations.setdefault("regroup_action", frame)
        workers = overview.get("excluded_unit_tags", {}).get("worker")
        if isinstance(workers, list):
            tags = set(workers)
            if baseline_workers is None:
                baseline_workers = tags
                observations["baseline_worker_count"] = len(tags)
            observations["last_worker_count"] = len(tags)
            # Diagnostic only: this projection does not distinguish SCVs
            # from temporary workers such as MULEs.
            observations["max_worker_count"] = max(
                observations.get("max_worker_count", len(tags)),
                len(tags),
            )
            if tags - baseline_workers and len(tags) > len(baseline_workers):
                observations.setdefault("worker_population_increased", frame)
        worker = managers.get("WorkerManager", {})
        if worker.get("last_trace_reason") == "mineral_assignment":
            # Candidate selection is not proof of a submitted harvest order.
            observations.setdefault("mineral_assignment_candidate", frame)
        if worker.get("completed_refinery_count", 0) > 0:
            observations.setdefault("completed_refinery_observed", frame)
        if worker.get("actual_gas_workers", 0) > 0:
            observations.setdefault("gas_workers_observed", frame)
    observations["scoped_production_actions"] = action_frames
    observations["scoped_operations"] = operations
    if scenario["id"] == "28-supply-block-recovery":
        observations["supply_recovery"] = {
            key: value for key, value in raw_observations.supply_recovery.items()
            if key != "baseline_tags"
        }
    report["runtime_instance_id"] = runtime
    report["update_id"] = sorted(update_ids)
    proven = dict(action_frames)
    if scenario["id"] == "19-expand-production-resume":
        # A scoped train action before expansion completion is insufficient.
        proven.pop("train_command|Marine", None)
        observations["expansion_production_resume"] = {
            key: value for key, value in raw_observations.expansion_resume.items()
            if key != "baseline_tags"
        }
    proven.update(raw_observations.production_evidence(scenario["id"]))
    proven.update(raw_observations.evidence)
    if scenario["id"] == "08-factory-techlab":
        addon_frame = action_frames.get("addon_build_command|FactoryTechLab")
        if addon_frame is not None:
            proven["build_command|FactoryTechLab"] = addon_frame
    # Promote only scoped runtime observations into the manifest vocabulary.
    # These are backed by the same update id, operation generation, unit tags,
    # submitted frame, and subsequent SC2 effect frame collected above.
    operation_records = list(operations.values())
    assigned_records = [
        record for record in operation_records
        if record.get("assigned_frame") is not None
    ]
    movement_records = [
        record for record in operation_records
        if any(":movement" in key for key in record.get("effects", {}))
    ]
    engagement_records = [
        record for record in operation_records
        if any(":engagement" in key for key in record.get("effects", {}))
    ]
    terminal_records = [
        record for record in operation_records
        if record.get("terminal_status") in {"COMPLETED", "CANCELLED", "EXPIRED"}
    ]
    if assigned_records:
        proven["operation_assigned"] = min(
            record["assigned_frame"] for record in assigned_records
        )
    if movement_records:
        proven["movement_observed"] = min(
            effect["effect_frame"]
            for record in movement_records
            for effect in record.get("effects", {}).values()
            if effect.get("effect_frame") is not None
        )
    if engagement_records:
        proven["engagement_observed"] = min(
            effect["effect_frame"]
            for record in engagement_records
            for effect in record.get("effects", {}).values()
            if effect.get("effect_frame") is not None
        )
        # A scoped engagement is the target-side effect for attack/pressure
        # operations; it is not inferred from an order or manager status.
        proven["target_effect"] = proven["engagement_observed"]
    if "concurrent_ownership" in observations:
        ownership_frame = observations["concurrent_ownership"]["frame"]
        proven["two_operations_assigned"] = ownership_frame
        proven["exclusive_ownership"] = ownership_frame
    if terminal_records:
        proven["terminal_state"] = min(
            record["terminal_frame"] for record in terminal_records
        )
    if scenario["id"] in {
        "10-tank-ramp-defense", "16-defense-reinforcement", "25-long-defense"
    } and movement_records:
        proven["defense_effect"] = proven["movement_observed"]
    if scenario["id"] in {
        "10-tank-ramp-defense", "16-defense-reinforcement", "25-long-defense"
    } and "scoped_movement_observed" in raw_observations.evidence:
        proven["defense_effect"] = raw_observations.evidence[
            "scoped_movement_observed"
        ]
    if scenario["id"] == "16-defense-reinforcement":
        reinforcement_records = [
            record for record in operation_records
            if "reinforcement" in record.get("operation_id", "").lower()
            and record.get("assigned_frame") is not None
        ]
        if reinforcement_records:
            proven["reinforcement_assigned"] = min(
                record["assigned_frame"] for record in reinforcement_records
            )
    if scenario["id"] == "11-tank-siege-hold":
        siege_effects = [
            effect for record in operation_records
            for key, effect in record.get("effects", {}).items()
            if key.startswith("ability:MORPH_SIEGEMODE:")
            and effect.get("effect_frame") is not None
        ]
        if siege_effects:
            # The C++ effect latch is derived from SC2 Observation unit state
            # for the operation-owned tag; this is stronger than an order
            # string that may be absent between raw observation samples.
            proven["siege_command"] = min(
                effect["effect_frame"] for effect in siege_effects
            )
        if terminal_records:
            proven["position_held"] = proven["terminal_state"]
        # Do not synthesize siege_command: it requires an observed siege-mode
        # ability order or action, not merely a tank reaching a position.
    if scenario["id"] in {"22-reassign-marines", "23-reassign-tanks"}:
        if len({record.get("generation") for record in operation_records}) > 1:
            proven["transfer_admitted"] = min(
                record.get("assigned_frame")
                for record in assigned_records
                if record.get("assigned_frame") is not None
            )
        if any(record.get("operation_id", "").endswith("-destination")
               for record in operation_records):
            proven["tag_scoped_ownership"] = proven.get(
                "transfer_admitted", proven.get("operation_assigned")
            )
        if scenario["id"] == "23-reassign-tanks":
            # The tank policy must survive the ownership handoff. Require
            # observed Siege Mode effects in both generations rather than
            # trusting the command payload or a manager status string.
            generations_with_siege_effect = {
                record.get("generation")
                for record in operation_records
                if any(
                    key.startswith("ability:MORPH_SIEGEMODE:")
                    and effect.get("effect_frame") is not None
                    for key, effect in record.get("effects", {}).items()
                )
            }
            if {1, 2}.issubset(generations_with_siege_effect):
                proven["ability_policy_preserved"] = min(
                    effect["effect_frame"]
                    for record in operation_records
                    if record.get("generation") in {1, 2}
                    for key, effect in record.get("effects", {}).items()
                    if key.startswith("ability:MORPH_SIEGEMODE:")
                    and effect.get("effect_frame") is not None
                )
    if scenario["id"] == "24-retarget" and len({
        record.get("generation") for record in operation_records
    }) > 1:
        proven["generation_advanced"] = min(
            record["assigned_frame"] for record in assigned_records
            if record.get("generation") == 2
        )
        proven["target_latched"] = proven["generation_advanced"]
    if scenario["id"] == "21-cancel-attack":
        cancelled = [
            record for record in operation_records
            if record.get("terminal_status") == "CANCELLED"
            and (
                "cancelled" in str(record.get("completion_conditions", "")).lower()
                or "cancelled" in str(record.get("blocked_reason", "")).lower()
                or "cancelled" in str(record.get("last_action", "")).lower()
            )
        ]
        if cancelled:
            proven["cancelled_by_user"] = min(
                record["terminal_frame"] for record in cancelled
            )
        submitted = [
            record for record in operation_records
            if record.get("assigned_frame") is not None
            and record.get("generation") is not None
            and record.get("unit_tags")
            and record.get("submitted_frame") is not None
        ]
        for record in submitted:
            terminal = next((
                item for item in cancelled
                if item["operation_id"] == record["operation_id"]
                and item["generation"] >= record["generation"]
                and item["terminal_frame"] > record["submitted_frame"]
                and item["assigned_count"] == 0
                and not item["last_unit_tags"]
            ), None)
            if not terminal:
                continue
            baseline = next((
                event for event in reversed(production_lifecycle)
                if event["frame"] <= record["submitted_frame"]
            ), None)
            if not baseline:
                continue
            operation_key = (
                f'{record["operation_id"]}#{record["generation"]}'
            )
            release = next(
                (
                    event for event in production_lifecycle
                    if event["frame"] >= terminal["terminal_frame"]
                    and event["released_owner"] == operation_key
                    and event["owner_release_count"] > baseline["owner_release_count"]
                ),
                None,
            )
            if release:
                proven["ownership_released"] = release["frame"]
            purge = next(
                (
                    event for event in production_lifecycle
                    if event["frame"] >= terminal["terminal_frame"]
                    and event["queue_purge_count"] > baseline["queue_purge_count"]
                    and isinstance(event["purge_event"], dict)
                    and event["purge_event"].get("operation_id")
                    == record["operation_id"]
                    and event["purge_event"].get("generation")
                    == record["generation"]
                    and event["purge_event"].get("removed_count", 0) > 0
                    and max(record["submitted_frame"], terminal["issued_at_frame"])
                    < event["purge_event"].get("frame", -1) <= event["frame"]
                ),
                None,
            )
            if purge:
                proven["queue_purge_observed"] = purge["frame"]
                break
    if scenario["id"] == "20-retreat":
        retreat_records = [
            record for record in operation_records
            if record.get("retreat_confirmed_frame") is not None
            and "retreat_confirmed" in str(
                record.get("completion_conditions", "")
            )
        ]
        if retreat_records:
            proven["retreat_confirmed"] = min(
                record["retreat_confirmed_frame"]
                for record in retreat_records
            )
        completed_retreats = [
            record for record in operation_records
            if record.get("terminal_status") == "COMPLETED"
            and "retreat_confirmed" in str(
                record.get("completion_conditions", "")
            )
            and record.get("unit_tags")
            and record.get("terminal_frame") is not None
        ]
        for record in completed_retreats:
            operation_key = (
                f'{record["operation_id"]}#{record["generation"]}'
            )
            if record.get("ownership_released_frame") is not None:
                proven["ownership_released"] = record[
                    "ownership_released_frame"
                ]
                break
            release = next(
                (
                    event for event in production_lifecycle
                    if event["frame"] >= record["terminal_frame"]
                    and event["released_owner"] == operation_key
                    and event["owner_release_count"] > 0
                ),
                None,
            )
            if release:
                proven["ownership_released"] = release["frame"]
                break
    if scenario["id"] == "25-long-defense":
        for record in operation_records:
            if "alive_after_lease_frame" in record:
                proven["operation_alive_after_lease"] = record["alive_after_lease_frame"]
    if scenario["id"] == "27-prerequisite-wait":
        blocked_frames = []
        for record in operation_records:
            if record.get("blocked_reason") or any(
                item.get("blocker")
                for effect in record.get("effects", {}).values()
                for item in [effect]
            ):
                blocked_frames.append(record.get("last_frame", issued))
            for effect in record.get("effects", {}).values():
                if effect.get("blocker"):
                    blocked_frames.append(record.get("last_frame", issued))
        if blocked_frames:
            proven["blocked_reason"] = min(blocked_frames)
        completed_prerequisites = []
        for tag, building in raw_observations.building.items():
            if (
                building.get("type") in {"FACTORY", "FACTORYTECHLAB"}
                and building.get("completed") is not None
            ):
                completed_prerequisites.append(building["completed"])
        if len(completed_prerequisites) >= 2:
            prerequisite_frame = max(completed_prerequisites)
            proven["prerequisite_completed"] = prerequisite_frame
            retried = [
                frame for action, frame in action_frames.items()
                if action in {
                    "train_command|SiegeTank",
                    "train_command|TERRAN_SIEGETANK",
                } for frame in action_frame_history.get(action, [])
                if frame > prerequisite_frame
            ]
            if retried:
                proven["command_retried"] = min(retried)
    if scenario["id"] == "29-heal-regroup" and terminal_records:
        proven["operation_terminal"] = proven["terminal_state"]
        if observations.get("autonomous_owner_restored") is not None:
            proven["autonomous_owner_restored"] = observations[
                "autonomous_owner_restored"
            ]
        if observations.get("regroup_action") is not None:
            proven["regroup_action"] = observations["regroup_action"]
    observations["raw_sc2_sample_count"] = raw_observations.samples
    if scenario["id"] == "03-refinery-gas":
        if "build_command|Refinery" not in proven:
            refinery_frame = next(
                (
                    frame
                    for action, frame in action_frames.items()
                    if action in {"build_command|Refinery", "build_command|TERRAN_REFINERY"}
                ),
                None,
            )
            if refinery_frame is not None:
                proven["build_command|Refinery"] = refinery_frame
    scv_action = next(
        (
            frame
            for action, frame in action_frames.items()
            if action in {"train_command|SCV", "train_command|TERRAN_SCV"}
        ),
        None,
    )
    if (
        scenario["id"] == "01-scv-production"
        and scv_action is not None
    ):
        proven["train_command|SCV"] = scv_action
    report["proven_evidence"] = {
        key: proven[key] for key in scenario["evidence"] if key in proven
    }
    if scenario["id"] == "30-game-end":
        log_text = ""
        for log_path in (
            root / "micromachine.log",
            root / "micromachine_combined.log",
        ):
            if log_path.exists():
                log_text += log_path.read_text(errors="replace")
        final_frame = None
        latest_path = root / "latest_telemetry.json"
        if latest_path.exists():
            try:
                latest = json.loads(latest_path.read_text())
                if latest.get("runtime_instance_id") == runtime:
                    final_frame = latest.get("frame")
            except (OSError, ValueError):
                pass
        if "OnGameEnd " in log_text:
            proven["OnGameEnd"] = final_frame or observations.get(
                "last_frame", issued
            )
        # Do not infer CheckGameResult from OnGameEnd. The game-end scenario
        # requires the instrumented runtime marker from the rebuilt binary.
        if "CheckGameResult invoked" in log_text:
            proven["CheckGameResult"] = final_frame or observations.get(
                "last_frame", issued
            )
        if final_frame is not None and final_frame >= issued:
            proven["final_telemetry"] = final_frame
        report["proven_evidence"] = {
            key: proven[key] for key in scenario["evidence"] if key in proven
        }
    report["missing_evidence"] = [
        key for key in scenario["evidence"] if key not in proven
    ]
    if not report["missing_evidence"] and not report["errors"] and runtime:
        report["status"] = "passed"
    return report


def audit_run(root: Path, manifest: Path) -> dict:
    scenarios = json.loads(manifest.read_text())["scenarios"]
    reports = []
    for scenario in scenarios:
        attempts = sorted(
            (root / scenario["id"]).glob("attempt-*"),
            key=lambda p: int(p.name.removeprefix("attempt-")),
        )
        report = audit_attempt(attempts[-1] if attempts else root / scenario["id"], scenario)
        report["attempt"] = str(attempts[-1]) if attempts else None
        reports.append(report)
    return {
        "status": "passed" if all(r["status"] == "passed" for r in reports) else "incomplete",
        "scope": "recorded_SC2_runtime_evidence_only",
        "natural_language_verified": False,
        "voice_verified": False,
        "scenarios": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_run(args.run_root, args.manifest)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "status": report["status"],
        "passed": sum(r["status"] == "passed" for r in report["scenarios"]),
        "total": len(report["scenarios"]),
    }))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
