#!/usr/bin/env python3
"""Build one validated live-QA command from the 30-scenario manifest ID."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from starcraft_commander.policy_modulation import PolicyModulationVector


def operation(
    operation_id: str,
    task_type: str,
    location: str,
    unit_type: str = "TERRAN_MARINE",
    count: int = 2,
    completion: list[str] | None = None,
    state: str = "active",
    edit: dict[str, object] | None = None,
    ability: str = "",
    ability_policy: str = "",
    lifetime_mode: str = "until_completed",
    minimum_count: int | None = None,
    composition_count: int | None = None,
) -> dict[str, object]:
    minimum_count = count if minimum_count is None else minimum_count
    composition_count = count if composition_count is None else composition_count
    return {
        "operation_id": operation_id,
        "goal": f"live qa {operation_id}",
        "generation": 1,
        "issued_at_frame": 0,
        "command_layer": "operation",
        "tactical_task": {
            "task_type": task_type,
            "location_intent": location,
            "production_targets": [unit_type],
            "priority": 1.0,
            "min_units": minimum_count,
            "max_units": count,
            "duration_seconds": 300,
            "allow_partial": True,
            **(
                {"ability": ability}
                if ability and task_type == "execute_ability"
                else {}
            ),
        },
        "scope": {
            "army_group": "siege" if "SIEGETANK" in unit_type else "main",
            "location_intent": location,
            "min_units": minimum_count,
            "max_units": count,
            "allow_partial_scope": True,
        },
        "composition_requirements": [
            {
                "unit_type": unit_type,
                "count": composition_count,
                "role": "frontline",
            }
        ],
        "lifetime": {
            "mode": lifetime_mode,
            "completion_conditions": completion or ["target_reached"],
            "completion_state": state,
        },
        "route_intent": {"route_type": "direct", "avoid_enemy_strength": False},
        "target_intent": {"target_type": "army", "priority": 1.0},
        "operation_edit": edit or {},
        "unit_roles": [{
            "unit_type": unit_type,
            "role": "frontline",
            "priority": 1.0,
            "ability_policy": ability_policy,
        }] if ability_policy else [],
    }


def payload(scenario_id: str) -> dict[str, object]:
    common = {
        "source": "ui",
        "override_level": "directive",
        "command_layer": "macro",
        "confidence": 1.0,
        "ttl_seconds": 300,
        "tags": ["live_qa", scenario_id],
    }
    economy_scenarios = {
        "01-scv-production": {"worker_production_bias": 1.0},
        "02-mineral-saturation": {"mineral_saturation_bias": 1.0, "worker_production_bias": 1.0},
        "03-refinery-gas": {"gas_priority": 1.0, "gas_worker_target_bias": 1.0},
        "04-supply-recovery": {"supply_buffer_bias": 1.0},
    }
    if scenario_id in economy_scenarios:
        common["economy"] = economy_scenarios[scenario_id]
        targets = {
            "01-scv-production": "TERRAN_SCV",
            "02-mineral-saturation": "TERRAN_SCV",
            "03-refinery-gas": "TERRAN_REFINERY",
            "04-supply-recovery": "TERRAN_SUPPLYDEPOT",
        }
        common["production"] = {"queue_biases": {targets[scenario_id]: 1.0}}
        common["tactical_task"] = {
            "task_type": "sustain_production",
            "production_targets": [targets[scenario_id]],
            "priority": 1.0,
            "duration_seconds": 300,
            "allow_partial": True,
        }
        if scenario_id == "03-refinery-gas":
            common["production_plan"] = {
                "allow_prerequisite_buildings": True,
                "priority": 1.0,
                "targets": ["TERRAN_REFINERY"],
            }
    elif scenario_id in {"12-marine-natural-pressure", "13-marine-main-attack"}:
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "marine_rush"},
            "production": {"queue_biases": {"TERRAN_MARINE": 1.0}},
        })
        common["operations"] = [
            operation(
                scenario_id, "pressure_with_main_army",
                "enemy_main" if scenario_id == "13-marine-main-attack" else "enemy_natural",
            )
        ]
    elif scenario_id == "14-marine-scout":
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "marine_rush"},
            "production": {"queue_biases": {"TERRAN_MARINE": 1.0}},
        })
        common["operations"] = [
            operation(scenario_id, "scout_with_units", "enemy_main", count=1)
        ]
    elif scenario_id == "10-tank-ramp-defense":
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "mech_transition"},
            "production": {
                "queue_biases": {
                    "TERRAN_FACTORY": 1.0,
                    "TERRAN_SIEGETANK": 1.0,
                }
            },
        })
        common["operations"] = [
            operation(
                scenario_id,
                "defend_with_units",
                "ramp",
                "TERRAN_SIEGETANK",
                1,
                ["target_reached", "ability_cast"],
                ability="siege_mode",
                ability_policy="siege_mode",
            )
        ]
    elif scenario_id == "11-tank-siege-hold":
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "mech_transition"},
            "production": {
                "queue_biases": {
                    "TERRAN_FACTORY": 1.0,
                    "TERRAN_SIEGETANK": 1.0,
                }
            },
        })
        common["operations"] = [
            operation(
                scenario_id,
                "defend_with_units",
                "ramp",
                "TERRAN_SIEGETANK",
                1,
                ["target_reached", "ability_cast"],
                ability="siege_mode",
                ability_policy="siege_mode",
            )
        ]
    elif scenario_id == "15-parallel-attack-defense":
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "marine_rush"},
            "production": {"queue_biases": {"TERRAN_MARINE": 1.0}},
        })
        common["operations"] = [
            operation(f"{scenario_id}-attack", "pressure_with_main_army", "enemy_natural"),
            operation(f"{scenario_id}-defense", "defend_with_units", "ramp"),
        ]
    elif scenario_id == "16-defense-reinforcement":
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "marine_rush"},
            "production": {"queue_biases": {"TERRAN_MARINE": 1.0}},
        })
        common["operations"] = [
            operation(
                f"{scenario_id}-defense", "defend_with_units", "ramp",
                count=1, completion=["target_reached"],
            ),
            operation(
                f"{scenario_id}-reinforcement", "defend_with_units", "ramp",
                count=1, completion=["target_reached"],
            ),
        ]
    elif scenario_id == "25-long-defense":
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "tank_defensive_hold"},
            "production": {"queue_biases": {
                "TERRAN_FACTORY": 1.0, "TERRAN_SIEGETANK": 1.0,
            }},
            "operations": [operation(
                scenario_id, "defend_with_units", "ramp",
                "TERRAN_SIEGETANK", 1,
                completion=["ability_cast"],
                ability="siege_mode",
                ability_policy="siege_mode",
                lifetime_mode="standing_order",
            )],
        })
    elif scenario_id == "27-prerequisite-wait":
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "mech_transition"},
            "production": {"queue_biases": {
                "TERRAN_FACTORY": 1.0, "TERRAN_SIEGETANK": 1.0,
            }},
            "operations": [operation(
                scenario_id, "defend_with_units", "ramp",
                "TERRAN_SIEGETANK", 1,
                completion=["target_reached"],
            )],
        })
    elif scenario_id in {"20-retreat", "21-cancel-attack"}:
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "marine_rush"},
            "production": {"queue_biases": {"TERRAN_MARINE": 1.0}},
        })
        common["operations"] = [
            operation(
                scenario_id,
                "pressure_with_main_army",
                "enemy_natural",
                count=8 if scenario_id == "21-cancel-attack" else 2,
                completion=["cancelled_by_user"],
            )
        ]
    elif scenario_id in {"22-reassign-marines", "23-reassign-tanks"}:
        common["command_layer"] = "operation"
        unit_type = (
            "TERRAN_SIEGETANK"
            if scenario_id == "23-reassign-tanks"
            else "TERRAN_MARINE"
        )
        common.update({
            "strategy": {
                "doctrine": "mech_transition"
                if scenario_id == "23-reassign-tanks"
                else "marine_rush"
            },
            "production": {"queue_biases": {unit_type: 1.0}},
        })
        common["operations"] = [
            operation(
                f"{scenario_id}-source",
                "pressure_with_main_army",
                "enemy_natural",
                unit_type,
                2,
                ability_policy=(
                    "siege_mode"
                    if scenario_id == "23-reassign-tanks"
                    else "never"
                ),
                minimum_count=1,
                lifetime_mode="standing_order",
            ),
            operation(
                f"{scenario_id}-destination",
                "defend_with_units",
                "ramp",
                unit_type,
                1,
                ability_policy=(
                    "siege_mode"
                    if scenario_id == "23-reassign-tanks"
                    else "never"
                ),
                lifetime_mode="standing_order",
            ),
        ]
    elif scenario_id in {"24-retarget", "29-heal-regroup"}:
        common["command_layer"] = "operation"
        common.update({
            "strategy": {"doctrine": "marine_rush"},
            "production": {"queue_biases": {"TERRAN_MARINE": 1.0}},
        })
        common["operations"] = [
            operation(
                scenario_id,
                "pressure_with_main_army" if scenario_id == "24-retarget"
                else "regroup_with_units",
                "enemy_natural" if scenario_id == "24-retarget" else "home",
                completion=["target_reached"] if scenario_id == "24-retarget"
                else ["order_issued"],
            )
        ]
    elif scenario_id in {"17-natural-expansion", "18-expansion-workers", "19-expand-production-resume"}:
        common.update(
            {
                "goal": f"live qa {scenario_id}",
                "strategy": {"doctrine": "expand_macro"},
                "production": {"queue_biases": {"TERRAN_COMMANDCENTER": 1.0}},
                "tactical_task": {
                    "task_type": "expand_or_land_command_center",
                    "production_targets": ["TERRAN_COMMANDCENTER"],
                    "priority": 1.0,
                    "duration_seconds": 300,
                    "allow_partial": True,
                },
                "production_plan": {
                    "allow_prerequisite_buildings": True,
                    "priority": 1.0,
                    "targets": ["TERRAN_COMMANDCENTER"],
                },
                "tags": ["live_qa", scenario_id, "expand_macro"],
            }
        )
    elif scenario_id in {"07-factory", "08-factory-techlab", "09-siege-tank", "23-reassign-tanks", "25-long-defense", "26-long-production"}:
        common.update(
            {
                "goal": f"live qa {scenario_id}",
                "strategy": {"doctrine": "mech_transition"},
                "production": {"queue_biases": {"TERRAN_FACTORY": 1.0, "TERRAN_SIEGETANK": 1.0}},
                "tags": ["live_qa", scenario_id, "mech_transition"],
            }
        )
        if scenario_id in {"08-factory-techlab", "09-siege-tank"}:
            common["tactical_task"] = {
                "task_type": "tech_transition",
                "production_targets": [
                    "TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_SIEGETANK"
                ],
                "priority": 1.0,
                "duration_seconds": 300,
                "allow_partial": True,
            }
            common["production_plan"] = {
                "allow_prerequisite_buildings": True,
                "priority": 1.0,
                "targets": [
                    "TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_SIEGETANK"
                ],
            }
    else:
        common.update(
            {
                "goal": f"live qa {scenario_id}",
                "strategy": {"doctrine": "marine_rush"},
                "production": {"queue_biases": {"TERRAN_MARINE": 1.0}},
            }
        )
    common.setdefault("goal", f"live qa {scenario_id}")
    return common


def lifecycle_payload(scenario_id: str, phase: str) -> dict[str, object]:
    data = payload(scenario_id)
    operations = data.get("operations", [])
    if not operations:
        return data
    if phase == "initial":
        return data
    if phase in {"retreat", "cancel", "regroup"}:
        for item in operations:
            item["generation"] = 2
            item["lifetime"] = {
                "mode": "until_completed",
                "completion_conditions": (
                    ["retreat_confirmed"] if phase == "retreat"
                    else ["cancelled_by_user"] if phase == "cancel"
                    # The runtime requires the explicit regroup order plus a
                    # subsequent SC2 movement/engagement effect. Ownership
                    # restoration remains a separate assertion.
                    else ["order_issued"]
                ),
                "completion_state": (
                    "active" if phase == "retreat"
                    else "cancelled" if phase == "cancel"
                    else "active"
                ),
            }
            if phase == "retreat":
                # Returning from an enemy-side engagement can exceed the
                # normal 300-second lease; keep the operation alive until
                # SC2 observation confirms the squad is home.
                item["tactical_task"]["duration_seconds"] = 900
        # Keep the task type within the policy schema; lifecycle intent is
            # carried by the generation, lifetime state, and tags.
        if phase == "retreat":
            data["emergency"] = {"force_retreat": True}
        elif phase == "cancel":
            data["emergency"] = {"cancel_attacks": True}
        else:
            data["tags"] = [*data.get("tags", []), "autonomous_owner_restore"]
        data["command_layer"] = "emergency" if phase in {"retreat", "cancel"} else "operation"
        data["tags"] = [*data.get("tags", []), f"lifecycle:{phase}"]
        return data
    if phase == "retarget":
        for item in operations:
            item["generation"] = 2
            item["tactical_task"]["location_intent"] = "enemy_main"
            item["scope"]["location_intent"] = "enemy_main"
            item["target_intent"] = {"target_type": "army", "priority": 1.0}
            item["operation_edit"] = {
                "action": "retarget",
                "explicit_override": True,
            }
        data["tags"] = [*data.get("tags", []), "lifecycle:retarget"]
        return data
    if phase == "reassign":
        if len(operations) == 1:
            source = operations[0]
            target = dict(source)
            target["operation_id"] = f"{source['operation_id']}-defense"
            target["scope"] = dict(source["scope"])
            target["scope"]["army_group"] = "defense"
            target["generation"] = 2
            source["generation"] = 2
            unit_type = "TERRAN_MARINE"
            source["operation_edit"] = {
                "action": "transfer_out",
                "counterpart_operation_id": target["operation_id"],
                "unit_selection": [{"unit_type": unit_type, "count": 1, "role": "frontline"}],
                "before_composition": source["composition_requirements"],
                "after_composition": [],
            }
            target["operation_edit"] = {
                "action": "transfer_in",
                "counterpart_operation_id": source["operation_id"],
                "unit_selection": [{"unit_type": unit_type, "count": 1, "role": "frontline"}],
                "before_composition": [],
                "after_composition": target["composition_requirements"],
            }
            data["operations"] = [source, target]
        else:
            source, target = operations[0], operations[1]
            unit_type = (
                "TERRAN_SIEGETANK"
                if "tank" in scenario_id
                else "TERRAN_MARINE"
            )
            source_before = [
                dict(item) for item in source["composition_requirements"]
            ]
            destination_before = [
                dict(item) for item in target["composition_requirements"]
            ]
            source_before_count = int(source_before[0]["count"])
            destination_before_count = int(destination_before[0]["count"])
            source_after = [
                {**source_before[0], "count": source_before_count - 1}
            ]
            destination_after = [
                {**destination_before[0], "count": destination_before_count + 1}
            ]
            source_roles = source.get("unit_roles", [])
            role_policy = next(
                (
                    str(role.get("ability_policy", "") or "")
                    for role in source_roles
                    if role.get("unit_type") == unit_type
                    and role.get("role") == "frontline"
                ),
                "siege_mode" if "tank" in scenario_id else "never",
            )
            source["generation"] = target["generation"] = 2
            source["operation_edit"] = {
                "action": "transfer_out",
                "counterpart_operation_id": target["operation_id"],
                "unit_selection": [{
                    "unit_type": unit_type, "count": 1, "role": "frontline"
                }],
                "before_composition": source_before,
                "after_composition": source_after,
                "explicit_override": True,
                "confirmation_policy": "auto",
            }
            source["composition_requirements"] = source_after
            source["unit_roles"] = [{
                "unit_type": unit_type, "role": "frontline",
                "priority": 1.0, "ability_policy": role_policy
            }]
            source["tactical_task"]["min_units"] = source_after[0]["count"]
            source["tactical_task"]["max_units"] = source_after[0]["count"]
            source["scope"]["min_units"] = source_after[0]["count"]
            source["scope"]["max_units"] = source_after[0]["count"]
            target["operation_edit"] = {
                "action": "transfer_in",
                "counterpart_operation_id": source["operation_id"],
                "unit_selection": [{
                    "unit_type": unit_type, "count": 1, "role": "frontline"
                }],
                "before_composition": destination_before,
                "after_composition": destination_after,
                "explicit_override": True,
                "confirmation_policy": "auto",
            }
            target["composition_requirements"] = destination_after
            target["unit_roles"] = [{
                "unit_type": unit_type, "role": "frontline",
                "priority": 1.0, "ability_policy": role_policy
            }]
            target["tactical_task"]["min_units"] = destination_after[0]["count"]
            target["tactical_task"]["max_units"] = destination_after[0]["count"]
            target["scope"]["min_units"] = destination_after[0]["count"]
            target["scope"]["max_units"] = destination_after[0]["count"]
        # Keep the initial pair alive long enough to admit the atomic
        # transfer; only the edited generation should complete normally.
        for item in data["operations"]:
            item["lifetime"] = {
                "mode": "until_completed",
                "completion_conditions": ["target_reached"],
                "completion_state": "active",
            }
        data["tags"] = [*data.get("tags", []), "lifecycle:reassign"]
        return data
    raise SystemExit(f"unsupported phase: {phase}")


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: qa30_scenario_command.py SCENARIO_ID [PHASE]")
    scenario_id = sys.argv[1]
    phase = sys.argv[2] if len(sys.argv) == 3 else "initial"
    vector = PolicyModulationVector.from_mapping(
        lifecycle_payload(scenario_id, phase)
    )
    print(json.dumps(vector.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
