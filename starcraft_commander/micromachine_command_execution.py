"""End-to-end command execution state for MicroMachine live QA.

This module turns existing blackboard artifacts, telemetry, and tactical
evidence into a command-level lifecycle. It intentionally does not treat
publish alone as success; completion requires command/effect evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Final

from starcraft_commander.micromachine_tactical_evidence import (
    MicroMachineTacticalEvidence,
)


COMMAND_EXECUTION_STAGES: Final[tuple[str, ...]] = (
    "parsed",
    "reduced",
    "published",
    "consumed_by_manager",
    "queued_or_assigned",
    "order_issued",
    "action_issued",
    "effect_observed",
)

LIVE_QA_SCENARIOS: Final[tuple[str, ...]] = (
    "marine_scout",
    "four_marine_attack",
    "requested_combat_composition_attack",
    "flank_attack",
    "tank_production_prerequisite_chain",
    "bio_support_production_prerequisite_chain",
    "mech_support_production_prerequisite_chain",
    "air_support_production_prerequisite_chain",
    "capital_air_production_prerequisite_chain",
    "special_unit_role_micro",
    "bunker_placement_intent",
    "retreat_interrupt",
    "standing_production_merge",
)

ACTUAL_PRODUCTION_ITEM_ALIASES: Final[Mapping[str, str]] = {
    "TERRAN_SUPPLYDEPOT": "SupplyDepot",
    "TERRAN_BARRACKS": "Barracks",
    "TERRAN_BARRACKSTECHLAB": "BarracksTechLab",
    "TERRAN_FACTORY": "Factory",
    "TERRAN_FACTORYTECHLAB": "FactoryTechLab",
    "TERRAN_STARPORT": "Starport",
    "TERRAN_STARPORTREACTOR": "StarportReactor",
    "TERRAN_COMMANDCENTER": "CommandCenter",
    "TERRAN_ENGINEERINGBAY": "EngineeringBay",
    "TERRAN_ARMORY": "Armory",
    "TERRAN_BUNKER": "Bunker",
    "TERRAN_FUSIONCORE": "FusionCore",
    "TERRAN_MARINE": "Marine",
    "TERRAN_MARAUDER": "Marauder",
    "TERRAN_REAPER": "Reaper",
    "TERRAN_HELLION": "Hellion",
    "TERRAN_CYCLONE": "Cyclone",
    "TERRAN_THOR": "Thor",
    "TERRAN_SIEGETANK": "SiegeTank",
    "TERRAN_MEDIVAC": "Medivac",
    "TERRAN_VIKINGFIGHTER": "Viking",
    "TERRAN_BANSHEE": "Banshee",
    "TERRAN_RAVEN": "Raven",
    "TERRAN_BATTLECRUISER": "Battlecruiser",
    "BARRACKS_TECHLAB": "BarracksTechLab",
    "BARRACKS_REACTOR": "BarracksReactor",
    "FACTORY_TECHLAB": "FactoryTechLab",
    "FACTORY_REACTOR": "FactoryReactor",
    "STARPORT_TECHLAB": "StarportTechLab",
    "STARPORT_REACTOR": "StarportReactor",
}

PRODUCTION_PREREQUISITE_EFFECT_ITEMS: Final[frozenset[str]] = frozenset(
    {
        "Armory",
        "Banshee",
        "BarracksTechLab",
        "Battlecruiser",
        "Cyclone",
        "Factory",
        "FactoryReactor",
        "FactoryTechLab",
        "FusionCore",
        "Hellion",
        "Marauder",
        "Medivac",
        "Raven",
        "Reaper",
        "SiegeTank",
        "Starport",
        "StarportReactor",
        "StarportTechLab",
        "Thor",
        "Viking",
    }
)

BIO_SUPPORT_EFFECT_ITEMS: Final[frozenset[str]] = frozenset(
    {"BarracksTechLab", "BarracksReactor", "Marauder", "Reaper", "Medivac"}
)
MECH_SUPPORT_EFFECT_ITEMS: Final[frozenset[str]] = frozenset(
    {"Factory", "FactoryTechLab", "FactoryReactor", "Hellion", "Cyclone", "SiegeTank", "Thor"}
)
AIR_SUPPORT_EFFECT_ITEMS: Final[frozenset[str]] = frozenset(
    {
        "Battlecruiser",
        "Banshee",
        "Medivac",
        "Raven",
        "Starport",
        "StarportReactor",
        "StarportTechLab",
        "Viking",
    }
)
CAPITAL_AIR_EFFECT_ITEMS: Final[frozenset[str]] = frozenset(
    {"Starport", "StarportTechLab", "FusionCore", "Battlecruiser"}
)
SPECIAL_MICRO_ROLES: Final[frozenset[str]] = frozenset(
    {
        "anti_air",
        "air_superiority",
        "capital_pressure",
        "capital_ship",
        "capital_ship_focus",
        "cloak_if_available",
        "contain",
        "defensive_hold",
        "siege_support",
        "worker_harass",
        "yamato_high_value",
    }
)

TACTICAL_EFFECT_FRAME_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"([A-Za-z0-9_]*frame)['\"]?\s*:\s*(\d+)"
)


@dataclass(frozen=True)
class MicroMachineCommandStage:
    """One lifecycle stage with actionable evidence or blocker text."""

    name: str
    ok: bool
    manager: str = ""
    reason: str = ""
    frame: int = 0
    evidence: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "manager": self.manager,
            "reason": self.reason,
            "frame": self.frame,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class MicroMachineLiveQAScenarioResult:
    """One user-facing QA scenario gate."""

    name: str
    status: str
    required_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...] = ()
    details: Mapping[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "ok": self.ok,
            "required_evidence": list(self.required_evidence),
            "missing_evidence": list(self.missing_evidence),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class MicroMachineCommandExecutionReport:
    """JSON-ready command lifecycle report for telemetry/UI/QA."""

    command_id: str
    state: str
    completed: bool
    failed: bool
    expired: bool
    blocker_manager: str = ""
    blocker_reason: str = ""
    active_plan: Mapping[str, object] = field(default_factory=dict)
    stages: tuple[MicroMachineCommandStage, ...] = ()
    scenarios: tuple[MicroMachineLiveQAScenarioResult, ...] = ()

    @property
    def ok(self) -> bool:
        return self.completed and not self.failed and not self.expired

    def to_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "state": self.state,
            "ok": self.ok,
            "completed": self.completed,
            "failed": self.failed,
            "expired": self.expired,
            "blocker_manager": self.blocker_manager,
            "blocker_reason": self.blocker_reason,
            "active_plan": dict(self.active_plan),
            "stages": [stage.to_dict() for stage in self.stages],
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }


def classify_micromachine_command_execution(
    *,
    latest_update: Mapping[str, object],
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]] = (),
    tactical_evidence: MicroMachineTacticalEvidence | None = None,
    expected_tactical_effects: Sequence[str] = (),
    expected_production_items: Sequence[str] = (),
    latest_frame: int = 0,
    target_frame: int = 0,
) -> MicroMachineCommandExecutionReport:
    """Classify a live command from publish through observed game effect."""

    update = dict(latest_update)
    telemetry_entries = [
        entry for entry in (*telemetry_archive, latest_telemetry) if isinstance(entry, Mapping)
    ]
    command_id = str(update.get("update_id", "") or "")
    vector = update.get("vector") if isinstance(update.get("vector"), Mapping) else {}
    manager_domains = _string_list(update.get("manager_bias_domains"))
    active_plan = _active_plan(update)
    latest_frame = max(latest_frame, _latest_frame(telemetry_entries))
    expires_at_frame = _int_value(update.get("expires_at_frame"))
    expired = bool(command_id and expires_at_frame and latest_frame > expires_at_frame)

    stages = _build_stages(
        command_id=command_id,
        issued_at_frame=_int_value(update.get("issued_at_frame")),
        vector=vector if isinstance(vector, Mapping) else {},
        manager_domains=manager_domains,
        update=update,
        telemetry_entries=telemetry_entries,
        tactical_evidence=tactical_evidence,
        expected_tactical_effects=expected_tactical_effects,
        expected_production_items=expected_production_items,
    )
    stage_by_name = {stage.name: stage for stage in stages}
    scenarios = _build_scenario_results(
        stages=stage_by_name,
        latest_update=update,
        telemetry_entries=telemetry_entries,
        tactical_evidence=tactical_evidence,
        expected_production_items=expected_production_items,
    )
    required_scenario_names = _required_scenario_names(
        vector if isinstance(vector, Mapping) else {}
    )
    scenario_by_name = {scenario.name: scenario for scenario in scenarios}
    scenario_blocker = next(
        (
            scenario_by_name[name]
            for name in required_scenario_names
            if name in scenario_by_name and not scenario_by_name[name].ok
        ),
        None,
    )
    lifecycle_complete = all(stage.ok for stage in stages) and scenario_blocker is None
    failed = bool(target_frame and latest_frame >= target_frame and not lifecycle_complete)
    completed = lifecycle_complete and not expired
    first_blocker = next((stage for stage in stages if not stage.ok), None)
    if expired:
        blocker_manager = "GameCommander"
        blocker_reason = "Latest command expired before effect_observed."
        state = "expired"
    elif completed:
        blocker_manager = ""
        blocker_reason = ""
        state = "completed"
    elif failed and first_blocker is not None:
        blocker_manager = first_blocker.manager
        blocker_reason = first_blocker.reason
        state = "failed"
    elif failed and scenario_blocker is not None:
        blocker_manager = _scenario_blocker_manager(scenario_blocker.name)
        blocker_reason = _scenario_blocker_reason(scenario_blocker)
        state = "failed"
    elif first_blocker is not None:
        blocker_manager = first_blocker.manager
        blocker_reason = first_blocker.reason
        state = first_blocker.name
    elif scenario_blocker is not None:
        blocker_manager = _scenario_blocker_manager(scenario_blocker.name)
        blocker_reason = _scenario_blocker_reason(scenario_blocker)
        state = scenario_blocker.name
    else:
        blocker_manager = ""
        blocker_reason = ""
        state = "completed"

    return MicroMachineCommandExecutionReport(
        command_id=command_id,
        state=state,
        completed=completed,
        failed=failed,
        expired=expired,
        blocker_manager=blocker_manager,
        blocker_reason=blocker_reason,
        active_plan=active_plan,
        stages=tuple(stages),
        scenarios=tuple(scenarios),
    )


def _required_scenario_names(vector: Mapping[str, object]) -> tuple[str, ...]:
    required: list[str] = []
    task_type = _nested_string(vector, ("tactical_task", "task_type"))
    tags = set(_string_list(vector.get("tags")))
    requested_items = _requested_combat_items(vector)
    scout_unit_items = set(requested_items)
    if task_type == "scout_with_units":
        for path in (("tactical_task", "unit_classes"), ("scope", "unit_classes")):
            for unit_type in _nested_string_list(vector, path):
                item = _canonical_unit_item(unit_type)
                if item:
                    scout_unit_items.add(item)
    requested_non_marine_items = {
        item for item in requested_items if item not in {"Marine", "SCV"}
    }
    if task_type == "scout_with_units" and "Marine" in scout_unit_items:
        required.append("marine_scout")
    if (
        task_type == "pressure_with_main_army" or "pressure" in tags
    ) and requested_non_marine_items:
        required.append("requested_combat_composition_attack")
    if (
        task_type == "pressure_with_main_army" or "pressure" in tags
    ) and _requested_special_unit_items(vector):
        required.append("special_unit_role_micro")
    return tuple(required)


def _scenario_blocker_manager(name: str) -> str:
    if name == "marine_scout":
        return "CombatCommander"
    if name == "requested_combat_composition_attack":
        return "CompositionTask"
    return "Telemetry"


def _scenario_blocker_reason(
    scenario: MicroMachineLiveQAScenarioResult,
) -> str:
    missing = ", ".join(scenario.missing_evidence) or "required evidence"
    return f"Required scenario {scenario.name} is incomplete: {missing}."


def _build_stages(
    *,
    command_id: str,
    issued_at_frame: int,
    vector: Mapping[str, object],
    manager_domains: tuple[str, ...],
    update: Mapping[str, object],
    telemetry_entries: Sequence[Mapping[str, object]],
    tactical_evidence: MicroMachineTacticalEvidence | None,
    expected_tactical_effects: Sequence[str],
    expected_production_items: Sequence[str],
) -> list[MicroMachineCommandStage]:
    parsed = bool(command_id and vector)
    reduced = parsed and bool(manager_domains or _vector_domains(vector))
    published = parsed and _int_value(update.get("issued_at_frame")) >= 0
    consumed, consumed_evidence = _manager_consumption(command_id, telemetry_entries)
    queued, queued_manager, queued_evidence = _queued_or_assigned(
        command_id,
        issued_at_frame,
        telemetry_entries,
    )
    order_issued, order_manager, order_evidence = _order_issued(
        command_id,
        issued_at_frame,
        telemetry_entries,
    )
    action_issued, action_manager, action_evidence = _action_issued(
        command_id,
        issued_at_frame,
        telemetry_entries,
    )
    effect_observed, effect_manager, effect_evidence = _effect_observed(
        command_id=command_id,
        issued_at_frame=issued_at_frame,
        telemetry_entries=telemetry_entries,
        tactical_evidence=tactical_evidence,
        expected_tactical_effects=expected_tactical_effects,
        expected_production_items=expected_production_items,
    )
    latest_frame = _latest_frame(telemetry_entries)
    return [
        MicroMachineCommandStage(
            "parsed",
            parsed,
            "PolicyModulationProvider",
            "Command payload was parsed into a modulation update." if parsed else "No latest command update was parsed.",
            _int_value(update.get("issued_at_frame")),
            {"update_id": command_id, "has_vector": bool(vector)},
        ),
        MicroMachineCommandStage(
            "reduced",
            reduced,
            "PolicyModulationProvider",
            "Command was reduced into bounded manager domains." if reduced else "Parsed command has no bounded manager domains.",
            _int_value(update.get("issued_at_frame")),
            {"manager_bias_domains": list(manager_domains), "vector_domains": _vector_domains(vector)},
        ),
        MicroMachineCommandStage(
            "published",
            published,
            "MicroMachineBlackboard",
            "Command update was published to the blackboard." if published else "No blackboard publish artifact is available.",
            _int_value(update.get("issued_at_frame")),
            {"expires_at_frame": _int_value(update.get("expires_at_frame"))},
        ),
        MicroMachineCommandStage(
            "consumed_by_manager",
            consumed,
            consumed_evidence.get("manager", "GameCommander"),
            "Telemetry shows a manager consumed the update." if consumed else "Telemetry has not consumed the latest update id.",
            latest_frame,
            consumed_evidence,
        ),
        MicroMachineCommandStage(
            "queued_or_assigned",
            queued,
            queued_manager,
            "A manager queued production or assigned a squad/task." if queued else "No manager queue or squad assignment evidence.",
            latest_frame,
            queued_evidence,
        ),
        MicroMachineCommandStage(
            "order_issued",
            order_issued,
            order_manager,
            "A manager issued a concrete order." if order_issued else "No concrete manager order was issued.",
            latest_frame,
            order_evidence,
        ),
        MicroMachineCommandStage(
            "action_issued",
            action_issued,
            action_manager,
            "Telemetry reached the SC2 command/action issue path." if action_issued else "No SC2 action issue evidence.",
            latest_frame,
            action_evidence,
        ),
        MicroMachineCommandStage(
            "effect_observed",
            effect_observed,
            effect_manager,
            "Observed game effect satisfies the command gate." if effect_observed else "No observed unit movement, tactical effect, or production effect.",
            latest_frame,
            effect_evidence,
        ),
    ]


def _build_scenario_results(
    *,
    stages: Mapping[str, MicroMachineCommandStage],
    latest_update: Mapping[str, object],
    telemetry_entries: Sequence[Mapping[str, object]],
    tactical_evidence: MicroMachineTacticalEvidence | None,
    expected_production_items: Sequence[str],
) -> list[MicroMachineLiveQAScenarioResult]:
    command_id = str(latest_update.get("update_id", "") or "")
    issued_at_frame = _int_value(latest_update.get("issued_at_frame"))
    observed_effects = _current_tactical_effects(
        tactical_evidence,
        issued_at_frame,
        (),
    )
    production_items = _observed_production_items(
        telemetry_entries,
        command_id=command_id,
        issued_at_frame=issued_at_frame,
    )
    vector = latest_update.get("vector") if isinstance(latest_update.get("vector"), Mapping) else {}
    route_type = _nested_string(vector, ("route_intent", "route_type"))
    latest_combat = _latest_manager_payload_for_command(
        telemetry_entries,
        "CombatCommander",
        command_id,
        issued_at_frame,
    )
    latest_composition = _latest_manager_payload_for_command(
        telemetry_entries,
        "CompositionTask",
        command_id,
        issued_at_frame,
    )
    latest_production = _latest_manager_payload_for_command(
        telemetry_entries,
        "ProductionManager",
        command_id,
        issued_at_frame,
    )
    latest_unit_role = _latest_manager_payload_for_command(
        telemetry_entries,
        "UnitRoleTask",
        command_id,
        issued_at_frame,
    )
    requested_items = _requested_combat_items(vector)
    requested_roles = _requested_unit_roles(vector)
    requested_special_items = _requested_special_unit_items(vector)
    exact_unit_role_evidence = _exact_unit_role_evidence_by_item(
        telemetry_entries,
        command_id=command_id,
        issued_at_frame=issued_at_frame,
    )
    requested_required_count = _requested_composition_count(vector)
    requested_non_marine_items = tuple(
        item for item in requested_items if item not in {"Marine", "SCV"}
    )
    scout_marine_type = _is_marine_unit_type(
        latest_combat.get("scout_last_commanded_unit_type")
    )
    scout_marine_tag = _number(
        latest_combat.get("scout_last_commanded_unit_tag")
    ) > 0
    scout_marine_assigned = _number(
        latest_combat.get("scout_marine_assigned_count")
    ) > 0
    scout_action = (
        _number(latest_combat.get("scout_actual_command_issued_count")) > 0
        and "squad=Scout"
        in str(latest_combat.get("scout_last_issued_action", "") or "")
        and scout_marine_type
        and scout_marine_tag
    )
    scout_displaced = (
        _number(latest_combat.get("scout_marine_max_home_distance")) >= 8.0
    )
    attack_action = _number(latest_combat.get("main_attack_actual_command_issued_count")) > 0
    attack_displaced = _number(latest_combat.get("main_attack_max_home_distance")) >= 12.0
    assigned_four = _number(latest_composition.get("assigned_count")) >= 4 or _number(
        latest_combat.get("main_attack_assigned_unit_count")
    ) >= 4
    requested_composition_assigned = _requested_composition_assigned(
        latest_composition,
        requested_required_count,
    )
    special_role_requested = bool(requested_special_items)
    special_role_consumed = special_role_requested and all(
        item in exact_unit_role_evidence for item in requested_special_items
    )
    actual_production_command = _manager_has_actual_production_command(
        latest_production,
        command_id,
        issued_at_frame,
    )
    building_payload = _latest_manager_payload_for_command(
        telemetry_entries,
        "BuildingTask",
        command_id,
        issued_at_frame,
    )
    building_action = _building_task_payload_effect(building_payload)
    scenarios = [
        _scenario(
            "marine_scout",
            (
                "marine_assignment",
                "marine_scout_actual_command",
                "marine_scout_displacement",
            ),
            scout_marine_assigned
            and scout_action
            and scout_displaced
            and "scout" in observed_effects,
            {
                "observed_effects": sorted(observed_effects),
                "scout_actual_command": scout_action,
                "scout_marine_assigned": scout_marine_assigned,
                "scout_marine_unit_type": str(
                    latest_combat.get("scout_last_commanded_unit_type", "") or ""
                ),
                "scout_marine_unit_tag": _int_value(
                    latest_combat.get("scout_last_commanded_unit_tag")
                ),
                "scout_marine_displaced": scout_displaced,
            },
        ),
        _scenario(
            "four_marine_attack",
            ("main_attack_order", "main_attack_action", "main_attack_displacement"),
            assigned_four and attack_action and attack_displaced and "pressure" in observed_effects,
            {
                "observed_effects": sorted(observed_effects),
                "assigned_four": assigned_four,
                "main_attack_action": attack_action,
                "main_attack_displaced": attack_displaced,
            },
        ),
        _scenario(
            "requested_combat_composition_attack",
            (
                "requested_non_marine_composition",
                "composition_assignment",
                "main_attack_action",
                "main_attack_displacement",
            ),
            bool(requested_non_marine_items)
            and requested_composition_assigned
            and attack_action
            and attack_displaced
            and "pressure" in observed_effects,
            {
                "requested_items": list(requested_items),
                "requested_non_marine_items": list(requested_non_marine_items),
                "requested_required_count": requested_required_count,
                "composition_assignment": latest_composition,
                "observed_effects": sorted(observed_effects),
                "main_attack_action": attack_action,
                "main_attack_displaced": attack_displaced,
            },
        ),
        _scenario(
            "flank_attack",
            ("flank_route_intent", "main_attack_action", "main_attack_displacement"),
            route_type.startswith("flank_")
            and attack_action
            and attack_displaced
            and "pressure" in observed_effects,
            {
                "route_type": route_type,
                "main_attack_action": attack_action,
                "main_attack_displaced": attack_displaced,
                "observed_effects": sorted(observed_effects),
            },
        ),
        _scenario(
            "tank_production_prerequisite_chain",
            ("production_command", "factory_or_techlab_or_tank_item"),
            actual_production_command
            and bool(
                PRODUCTION_PREREQUISITE_EFFECT_ITEMS & production_items
                or {
                    _canonical_production_item(item)
                    for item in expected_production_items
                }
                & production_items
            ),
            {"observed_production_items": sorted(production_items)},
        ),
        _scenario(
            "bio_support_production_prerequisite_chain",
            ("production_command", "bio_support_item"),
            actual_production_command
            and _production_group_requested_or_observed(
                requested_items,
                production_items,
                BIO_SUPPORT_EFFECT_ITEMS,
            ),
            {
                "requested_items": list(requested_items),
                "observed_production_items": sorted(production_items),
                "required_group": sorted(BIO_SUPPORT_EFFECT_ITEMS),
            },
        ),
        _scenario(
            "mech_support_production_prerequisite_chain",
            ("production_command", "mech_support_item"),
            actual_production_command
            and _production_group_requested_or_observed(
                requested_items,
                production_items,
                MECH_SUPPORT_EFFECT_ITEMS,
            ),
            {
                "requested_items": list(requested_items),
                "observed_production_items": sorted(production_items),
                "required_group": sorted(MECH_SUPPORT_EFFECT_ITEMS),
            },
        ),
        _scenario(
            "air_support_production_prerequisite_chain",
            ("production_command", "air_support_item"),
            actual_production_command
            and _production_group_requested_or_observed(
                requested_items,
                production_items,
                AIR_SUPPORT_EFFECT_ITEMS,
            ),
            {
                "requested_items": list(requested_items),
                "observed_production_items": sorted(production_items),
                "required_group": sorted(AIR_SUPPORT_EFFECT_ITEMS),
            },
        ),
        _scenario(
            "capital_air_production_prerequisite_chain",
            ("production_command", "capital_air_item"),
            actual_production_command
            and _production_group_requested_or_observed(
                requested_items,
                production_items,
                CAPITAL_AIR_EFFECT_ITEMS,
            ),
            {
                "requested_items": list(requested_items),
                "observed_production_items": sorted(production_items),
                "required_group": sorted(CAPITAL_AIR_EFFECT_ITEMS),
            },
        ),
        _scenario(
            "special_unit_role_micro",
            ("special_unit_role_request", "unit_role_task_consumed", "unit_role_action"),
            special_role_requested and special_role_consumed,
            {
                "requested_roles": list(requested_roles),
                "requested_items": list(requested_items),
                "requested_special_items": list(requested_special_items),
                "exact_unit_role_evidence": exact_unit_role_evidence,
                "unit_role_task": latest_unit_role,
            },
        ),
        _scenario(
            "bunker_placement_intent",
            ("building_task_consumed", "building_command_or_placement"),
            building_action,
            {"building_task": building_payload},
        ),
        _scenario(
            "retreat_interrupt",
            ("hold_or_retreat_effect", "action_or_order_evidence"),
            "hold" in observed_effects and (stages["order_issued"].ok or stages["action_issued"].ok),
            {"observed_effects": sorted(observed_effects)},
        ),
        _scenario(
            "standing_production_merge",
            ("production_queue_merge", "actual_production_command"),
            stages["queued_or_assigned"].ok and actual_production_command and bool(production_items),
            {"observed_production_items": sorted(production_items)},
        ),
    ]
    return scenarios


def _requested_combat_items(vector: Mapping[str, object]) -> tuple[str, ...]:
    items: list[str] = []
    for requirement in _mapping_sequence(vector.get("composition_requirements")):
        item = _canonical_unit_item(requirement.get("unit_type"))
        if item:
            items.append(item)
    for role in _mapping_sequence(vector.get("unit_roles")):
        item = _canonical_unit_item(role.get("unit_type"))
        if item:
            items.append(item)
    tags = set(_string_list(vector.get("tags")))
    if "explicit_composition" in tags:
        for path in (("scope", "unit_classes"), ("tactical_task", "unit_classes")):
            for unit_type in _nested_string_list(vector, path):
                item = _canonical_unit_item(unit_type)
                if item:
                    items.append(item)
    return tuple(dict.fromkeys(items))


def _requested_unit_roles(vector: Mapping[str, object]) -> tuple[str, ...]:
    roles = [
        str(role.get("role", "") or "")
        for role in _mapping_sequence(vector.get("unit_roles"))
        if str(role.get("role", "") or "")
    ]
    for requirement in _mapping_sequence(vector.get("composition_requirements")):
        role = str(requirement.get("role", "") or "")
        if role:
            roles.append(role)
    return tuple(dict.fromkeys(roles))


def _requested_special_unit_items(vector: Mapping[str, object]) -> tuple[str, ...]:
    items: list[str] = []
    for role in _mapping_sequence(vector.get("unit_roles")):
        role_name = str(role.get("role", "") or "")
        item = _canonical_unit_item(role.get("unit_type"))
        if role_name in SPECIAL_MICRO_ROLES and item:
            items.append(item)
    for requirement in _mapping_sequence(vector.get("composition_requirements")):
        role_name = str(requirement.get("role", "") or "")
        item = _canonical_unit_item(requirement.get("unit_type"))
        if role_name in SPECIAL_MICRO_ROLES and item:
            items.append(item)
    return tuple(dict.fromkeys(items))


def _exact_unit_role_evidence_by_item(
    telemetry_entries: Sequence[Mapping[str, object]],
    *,
    command_id: str,
    issued_at_frame: int,
) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        payload = managers.get("UnitRoleTask")
        if not isinstance(payload, Mapping):
            continue
        if not _payload_can_belong_to_command(
            "UnitRoleTask",
            payload,
            command_id,
            issued_at_frame,
        ):
            continue
        item = _canonical_unit_item(payload.get("unit_type"))
        actor_tag = _int_value(payload.get("actor_tag"))
        issued_action = str(payload.get("issued_action", "") or "")
        role_frame = max(
            _int_value(payload.get("frame")),
            _int_value(payload.get("last_action_frame")),
        )
        if (
            not item
            or actor_tag <= 0
            or role_frame < issued_at_frame
            or str(payload.get("status", "") or "") != "executed"
            or _number(payload.get("executed_count")) <= 0
            or not issued_action
            or _number(payload.get("max_home_distance")) < 8.0
        ):
            continue
        evidence[item] = {
            "actor_tag": actor_tag,
            "unit_type": str(payload.get("unit_type", "") or ""),
            "role": str(payload.get("role", "") or ""),
            "ability": str(payload.get("ability", "") or ""),
            "issued_action": issued_action,
            "home_distance": _number(payload.get("home_distance")),
            "max_home_distance": _number(payload.get("max_home_distance")),
            "frame": role_frame,
        }
    return evidence


def _requested_composition_count(vector: Mapping[str, object]) -> int:
    count = 0
    for requirement in _mapping_sequence(vector.get("composition_requirements")):
        count += int(_number(requirement.get("count")))
    scoped_min_units = 0
    tactical_task = vector.get("tactical_task")
    if isinstance(tactical_task, Mapping):
        scoped_min_units = int(_number(tactical_task.get("min_units")))
    scope = vector.get("scope")
    if isinstance(scope, Mapping):
        scoped_min_units = max(scoped_min_units, int(_number(scope.get("min_units"))))
    return max(count, scoped_min_units)


def _requested_composition_assigned(
    composition_payload: Mapping[str, object],
    requested_required_count: int,
) -> bool:
    if not composition_payload:
        return False
    status = str(composition_payload.get("status", "") or "")
    assigned_count = _number(composition_payload.get("assigned_count"))
    required_count = max(
        _number(composition_payload.get("required_count")),
        float(requested_required_count),
    )
    if required_count > 0:
        return assigned_count >= required_count
    return status in {"assigned", "partial", "completed"} or assigned_count > 0


def _unit_role_task_consumed(
    payload: Mapping[str, object],
    *,
    requested_roles: Sequence[str],
    requested_items: Sequence[str],
) -> bool:
    if not payload:
        return False
    role = str(payload.get("role", "") or "")
    unit_type = _canonical_unit_item(payload.get("unit_type"))
    status = str(payload.get("status", "") or "")
    role_matches = not requested_roles or role in set(requested_roles)
    unit_matches = not requested_items or unit_type in set(requested_items)
    action_count = max(
        _number(payload.get("attempted_count")),
        _number(payload.get("executed_count")),
        _number(payload.get("actual_command_issued_count")),
    )
    return bool(
        role_matches
        and unit_matches
        and (
            action_count > 0
            or status in {"attempted", "executed", "command_issued", "completed"}
        )
    )


def _production_group_requested_or_observed(
    requested_items: Sequence[str],
    production_items: set[str],
    group_items: frozenset[str],
) -> bool:
    requested_group = set(requested_items) & group_items
    observed_group = production_items & group_items
    if requested_group:
        return bool(observed_group)
    return bool(observed_group)


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _nested_string_list(payload: Mapping[str, object], path: tuple[str, ...]) -> tuple[str, ...]:
    current: object = payload
    for key in path:
        if not isinstance(current, Mapping):
            return ()
        current = current.get(key)
    return _string_list(current)


def _canonical_unit_item(unit_type: object) -> str:
    item = _canonical_production_item(unit_type)
    if item.startswith("TERRAN_"):
        item = _canonical_production_item(item)
    normalized = item.strip().lower().replace("_", "").replace("-", "")
    aliases = {
        "scv": "SCV",
        "marine": "Marine",
        "marauder": "Marauder",
        "reaper": "Reaper",
        "hellion": "Hellion",
        "cyclone": "Cyclone",
        "thor": "Thor",
        "siegetank": "SiegeTank",
        "tank": "SiegeTank",
        "medivac": "Medivac",
        "viking": "Viking",
        "vikingfighter": "Viking",
        "banshee": "Banshee",
        "raven": "Raven",
        "battlecruiser": "Battlecruiser",
        "bc": "Battlecruiser",
    }
    return aliases.get(normalized, item)


def _scenario(
    name: str,
    required: tuple[str, ...],
    ok: bool,
    details: Mapping[str, object],
) -> MicroMachineLiveQAScenarioResult:
    return MicroMachineLiveQAScenarioResult(
        name=name,
        status="passed" if ok else "missing",
        required_evidence=required,
        missing_evidence=() if ok else required,
        details=details,
    )


def _active_plan(update: Mapping[str, object]) -> dict[str, object]:
    vector = update.get("vector") if isinstance(update.get("vector"), Mapping) else {}
    return {
        "goal": str(vector.get("goal", "") or "") if isinstance(vector, Mapping) else "",
        "tags": _string_list(vector.get("tags")) if isinstance(vector, Mapping) else [],
        "manager_bias_domains": _string_list(update.get("manager_bias_domains")),
        "issued_at_frame": _int_value(update.get("issued_at_frame")),
        "expires_at_frame": _int_value(update.get("expires_at_frame")),
    }


def _manager_consumption(
    command_id: str,
    telemetry_entries: Sequence[Mapping[str, object]],
) -> tuple[bool, dict[str, object]]:
    best: dict[str, object] = {"manager": "GameCommander", "expected_update_id": command_id}
    for entry in telemetry_entries:
        active_ids = _string_list(entry.get("active_modulation_ids"))
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        for manager_name, payload in managers.items():
            if not isinstance(payload, Mapping):
                continue
            manager_update_ids = _manager_update_ids(payload)
            if command_id and command_id in manager_update_ids:
                return True, {
                    "manager": str(manager_name),
                    "active_modulation_ids": active_ids,
                    "manager_update_ids": sorted(value for value in manager_update_ids if value),
                }
        best = {"manager": "GameCommander", "active_modulation_ids": active_ids}
    return False, best


def _queued_or_assigned(
    command_id: str,
    issued_at_frame: int,
    telemetry_entries: Sequence[Mapping[str, object]],
) -> tuple[bool, str, dict[str, object]]:
    for entry in reversed(tuple(telemetry_entries)):
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        for manager_name in (
            "ProductionManager",
            "CombatCommander",
            "ScoutManager",
            "TacticalTask",
            "CompositionTask",
            "UnitRoleTask",
            "BuildingTask",
        ):
            payload = managers.get(manager_name)
            if not isinstance(payload, Mapping):
                continue
            if not _payload_can_belong_to_command(
                manager_name,
                payload,
                command_id,
                issued_at_frame,
            ):
                continue
            if _manager_has_queue_or_assignment(payload):
                return True, manager_name, dict(payload)
    return False, "ProductionManager", {"expected_update_id": command_id}


def _order_issued(
    command_id: str,
    issued_at_frame: int,
    telemetry_entries: Sequence[Mapping[str, object]],
) -> tuple[bool, str, dict[str, object]]:
    for entry in reversed(tuple(telemetry_entries)):
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        for manager_name, payload in managers.items():
            if (
                isinstance(payload, Mapping)
                and _payload_can_belong_to_command(
                    str(manager_name),
                    payload,
                    command_id,
                    issued_at_frame,
                )
                and _manager_has_order(payload)
            ):
                return True, str(manager_name), dict(payload)
    return False, "CombatCommander", {"expected_update_id": command_id}


def _action_issued(
    command_id: str,
    issued_at_frame: int,
    telemetry_entries: Sequence[Mapping[str, object]],
) -> tuple[bool, str, dict[str, object]]:
    for entry in reversed(tuple(telemetry_entries)):
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        for manager_name, payload in managers.items():
            if (
                isinstance(payload, Mapping)
                and _payload_can_belong_to_command(
                    str(manager_name),
                    payload,
                    command_id,
                    issued_at_frame,
                )
                and _manager_has_action(payload)
            ):
                return True, str(manager_name), dict(payload)
    return False, "ActionDispatcher", {"expected_update_id": command_id}


def _effect_observed(
    *,
    command_id: str,
    issued_at_frame: int,
    telemetry_entries: Sequence[Mapping[str, object]],
    tactical_evidence: MicroMachineTacticalEvidence | None,
    expected_tactical_effects: Sequence[str],
    expected_production_items: Sequence[str],
) -> tuple[bool, str, dict[str, object]]:
    if _tactical_effect_observed_current(
        tactical_evidence,
        issued_at_frame,
        expected_tactical_effects,
    ):
        return True, "TacticalEvidence", tactical_evidence.to_dict()
    production_items = _observed_production_items(
        telemetry_entries,
        command_id=command_id,
        issued_at_frame=issued_at_frame,
    )
    expected_items = {
        _canonical_production_item(item)
        for item in expected_production_items
        if _canonical_production_item(item)
    }
    if expected_items and expected_items <= production_items:
        return True, "ProductionManager", {"observed_production_items": sorted(production_items)}
    if not expected_tactical_effects and not expected_items and production_items:
        return True, "ProductionManager", {"observed_production_items": sorted(production_items)}
    return False, "Telemetry", {
        "tactical_evidence": tactical_evidence.to_dict() if tactical_evidence else None,
        "observed_production_items": sorted(production_items),
    }


def _manager_has_queue_or_assignment(payload: Mapping[str, object]) -> bool:
    return any(
        _number(payload.get(key)) > 0
        for key in (
            "assigned_count",
            "assigned_unit_count",
            "scout_scope_assigned_unit_count",
            "actual_production_command_issued_count",
            "requested_count",
            "actual_command_issued_count",
            "main_attack_actual_command_issued_count",
            "scout_actual_command_issued_count",
        )
    ) or any(
        str(payload.get(key, "") or "") not in ("", "none", "None")
        for key in (
            "last_doctrine_queue_item",
            "last_doctrine_action",
            "task_type",
            "last_actual_production_command_item",
            "placement_intent",
        )
    )


def _manager_has_order(payload: Mapping[str, object]) -> bool:
    return any(
        str(payload.get(key, "") or "")
        for key in (
            "main_attack_order_status",
            "last_actual_command",
            "last_actual_production_command",
            "last_issued_action",
            "main_attack_last_issued_action",
            "scout_last_issued_action",
            "last_building_command",
        )
    )


def _manager_has_action(payload: Mapping[str, object]) -> bool:
    if str(payload.get("status", "") or "") == "executed":
        return True
    return any(
        _number(payload.get(key)) > 0
        for key in (
            "actual_command_issued_count",
            "main_attack_actual_command_issued_count",
            "scout_actual_command_issued_count",
            "executed_count",
            "actual_production_command_issued_count",
        )
    )


def _manager_has_actual_production_command(
    payload: Mapping[str, object],
    command_id: str,
    issued_at_frame: int,
) -> bool:
    if not payload:
        return False
    item = _canonical_production_item(payload.get("last_actual_production_command_item", ""))
    command = str(payload.get("last_actual_production_command", "") or "")
    count = _number(payload.get("actual_production_command_issued_count"))
    frame = _int_value(payload.get("last_actual_production_command_frame"))
    update_id = str(payload.get("last_actual_production_command_update_id", "") or "")
    update_matches = not command_id or update_id == command_id
    frame_matches = not issued_at_frame or frame >= issued_at_frame
    return bool(
        item
        and item != "none"
        and count > 0
        and command
        and command != "none|none"
        and update_matches
        and frame_matches
    )


def _observed_production_items(
    telemetry_entries: Sequence[Mapping[str, object]],
    *,
    command_id: str = "",
    issued_at_frame: int = 0,
) -> set[str]:
    items: set[str] = set()
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        production = managers.get("ProductionManager")
        if not isinstance(production, Mapping):
            continue
        if not _manager_has_actual_production_command(
            production,
            command_id,
            issued_at_frame,
        ):
            continue
        item = _canonical_production_item(
            production.get("last_actual_production_command_item", "")
        )
        if item and item != "none":
            items.add(item)
    return items


def _tactical_effect_observed_current(
    tactical_evidence: MicroMachineTacticalEvidence | None,
    issued_at_frame: int,
    expected_tactical_effects: Sequence[str],
) -> bool:
    if tactical_evidence is None or not tactical_evidence.ok:
        return False
    expected = _normalized_expected_tactical_effects(
        tactical_evidence,
        expected_tactical_effects,
    )
    observed = _current_tactical_effects(
        tactical_evidence,
        issued_at_frame,
        expected,
    )
    if expected:
        return expected <= observed
    return bool(observed)


def _current_tactical_effects(
    tactical_evidence: MicroMachineTacticalEvidence | None,
    issued_at_frame: int,
    expected_tactical_effects: Sequence[str] | set[str],
) -> set[str]:
    if tactical_evidence is None:
        return set()
    expected = {str(effect) for effect in expected_tactical_effects if str(effect)}
    effects: set[str] = set()
    for effect in tactical_evidence.effects:
        if expected and effect.tag not in expected:
            continue
        if not _tactical_effect_is_current(effect, issued_at_frame):
            continue
        effects.add(effect.tag)
    return effects


def _tactical_effect_is_current(
    effect: object,
    issued_at_frame: int,
) -> bool:
    if not issued_at_frame:
        return True
    frame = getattr(effect, "frame", None)
    if frame is None or frame < issued_at_frame:
        return False
    detail = str(getattr(effect, "detail", "") or "")
    relevant_frames = _relevant_tactical_detail_frames(
        str(getattr(effect, "tag", "") or ""),
        detail,
    )
    return not relevant_frames or all(frame >= issued_at_frame for frame in relevant_frames)


def _relevant_tactical_detail_frames(tag: str, detail: str) -> list[int]:
    frames_by_key = {
        match.group(1): int(match.group(2))
        for match in TACTICAL_EFFECT_FRAME_KEY_RE.finditer(detail)
    }
    if tag == "scout":
        keys = ("scout_last_action_frame", "last_actual_command_frame", "last_action_frame")
    elif tag in {"pressure", "contain", "harass", "target_priority"}:
        keys = (
            "main_attack_last_action_frame",
            "last_issued_action_frame",
            "last_action_frame",
        )
    else:
        keys = (
            "main_attack_last_action_frame",
            "scout_last_action_frame",
            "last_issued_action_frame",
            "last_actual_command_frame",
            "last_action_frame",
        )
    return [frames_by_key[key] for key in keys if frames_by_key.get(key, 0) > 0]


def _normalized_expected_tactical_effects(
    tactical_evidence: MicroMachineTacticalEvidence,
    expected_tactical_effects: Sequence[str],
) -> set[str]:
    normalized = {str(effect) for effect in tactical_evidence.expected_effects if str(effect)}
    if normalized:
        return normalized
    return {str(effect) for effect in expected_tactical_effects if str(effect)}


def _is_marine_unit_type(value: object) -> bool:
    normalized = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    return normalized in {
        "MARINE",
        "TERRANMARINE",
        "UNITTYPEIDTERRANMARINE",
    }


def _building_task_payload_effect(payload: Mapping[str, object]) -> bool:
    if not payload:
        return False
    status = str(payload.get("status", "") or "")
    return status in {"command_issued", "placed", "executed", "completed"} or _manager_has_action(payload)


def _latest_manager_payload(
    telemetry_entries: Sequence[Mapping[str, object]],
    manager_name: str,
) -> dict[str, object]:
    for entry in reversed(tuple(telemetry_entries)):
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        payload = managers.get(manager_name)
        if isinstance(payload, Mapping):
            return dict(payload)
    return {}


def _vector_domains(vector: Mapping[str, object]) -> list[str]:
    return sorted(
        key
        for key, value in vector.items()
        if value not in (None, "", [], {}, 0, 0.0, False)
    )


def _nested_string(payload: Mapping[str, object], path: tuple[str, ...]) -> str:
    current: object = payload
    for key in path:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key)
    return str(current or "")


def _payload_can_belong_to_command(
    manager_name: str,
    payload: Mapping[str, object],
    command_id: str,
    issued_at_frame: int,
) -> bool:
    if not command_id:
        return True
    update_ids = _manager_update_ids(payload)
    if update_ids:
        return command_id in update_ids and _payload_has_current_frame(
            payload,
            issued_at_frame,
        )
    if manager_name in {"ProductionManager", "BuildingTask"}:
        return False
    return _payload_has_current_frame(payload, issued_at_frame)


def _payload_has_current_frame(
    payload: Mapping[str, object],
    issued_at_frame: int,
) -> bool:
    if not issued_at_frame:
        return True
    positive_frames = _relevant_payload_frames(payload)
    return bool(positive_frames) and any(frame >= issued_at_frame for frame in positive_frames)


def _relevant_payload_frames(payload: Mapping[str, object]) -> list[int]:
    event_frame = _int_value(payload.get("frame"))
    if _number(payload.get("main_attack_actual_command_issued_count")) > 0 or str(
        payload.get("main_attack_last_issued_action", "") or ""
    ):
        frames = [_int_value(payload.get("main_attack_last_action_frame"))]
        if _number(payload.get("scout_actual_command_issued_count")) > 0 or str(
            payload.get("scout_last_issued_action", "") or ""
        ):
            frames.append(_int_value(payload.get("scout_last_action_frame")))
        return frames
    if _number(payload.get("scout_actual_command_issued_count")) > 0 or str(
        payload.get("scout_last_issued_action", "") or ""
    ):
        return [_int_value(payload.get("scout_last_action_frame"))]
    if _number(payload.get("actual_production_command_issued_count")) > 0 or str(
        payload.get("last_actual_production_command", "") or ""
    ) not in ("", "none", "none|none"):
        return [_int_value(payload.get("last_actual_production_command_frame"))]
    if str(payload.get("last_building_command", "") or ""):
        return [_int_value(payload.get("last_building_command_frame"))]
    if _number(payload.get("assigned_count")) > 0 or _number(
        payload.get("assigned_unit_count")
    ) > 0:
        return [
            max(
                event_frame,
                _int_value(payload.get("assigned_frame")),
                _int_value(payload.get("last_assignment_frame")),
            )
        ]
    if _number(payload.get("actual_command_issued_count")) > 0 or str(
        payload.get("last_actual_command", "") or ""
    ):
        return [
            max(
                event_frame,
                _int_value(payload.get("last_actual_command_frame")),
                _int_value(payload.get("last_action_frame")),
                _int_value(payload.get("last_issued_action_frame")),
            )
        ]
    return [
        frame
        for frame in (
            event_frame,
            _int_value(payload.get("last_doctrine_frame")),
            _int_value(payload.get("last_action_frame")),
            _int_value(payload.get("last_issued_action_frame")),
        )
        if frame > 0
    ]


def _latest_manager_payload_for_command(
    telemetry_entries: Sequence[Mapping[str, object]],
    manager_name: str,
    command_id: str,
    issued_at_frame: int,
) -> dict[str, object]:
    for entry in reversed(tuple(telemetry_entries)):
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        payload = managers.get(manager_name)
        if not isinstance(payload, Mapping):
            continue
        if _payload_can_belong_to_command(
            manager_name,
            payload,
            command_id,
            issued_at_frame,
        ):
            return dict(payload)
    return {}


def _manager_update_ids(payload: Mapping[str, object]) -> set[str]:
    return {
        str(payload.get(key, "") or "")
        for key in (
            "update_id",
            "policy_update_id",
            "last_doctrine_update_id",
            "last_actual_production_command_update_id",
            "task_update_id",
            "last_building_command_update_id",
        )
        if str(payload.get(key, "") or "")
    }


def _canonical_production_item(item: object) -> str:
    text = str(item or "")
    if not text:
        return ""
    return ACTUAL_PRODUCTION_ITEM_ALIASES.get(text.upper(), text)


def _latest_frame(telemetry_entries: Sequence[Mapping[str, object]]) -> int:
    return max((_int_value(entry.get("frame")) for entry in telemetry_entries), default=0)


def _string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item) for item in value if isinstance(item, str) and item)
    return ()


def _int_value(value: object) -> int:
    if type(value) is bool:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _number(value: object) -> float:
    if type(value) is bool:
        return 0.0
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    return 0.0
