"""Runtime backend bridge for MicroMachine policy modulation.

This module turns the issue #10 contracts into practical sidecar transports.
The filesystem backend writes canonical JSON plus a flat ``key=value`` overlay
for the C++ MicroMachine hook, while the backend protocol and in-memory backend
let LLM, replay, UI, or future neural representation providers publish the same
bounded modulation vectors without coupling callers to files.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
    validate_micromachine_blackboard_update,
)
from starcraft_commander.policy_modulation import (
    CombatModulation,
    EconomyModulation,
    EmergencyModulation,
    LifetimeModulation,
    PolicyModulationSource,
    PolicyModulationVector,
    PolicyOverrideLevel,
    PolicySafetyConstraint,
    ProductionModulation,
    ScoutingModulation,
    SquadModulation,
    StrategyModulation,
    TacticalScopeModulation,
    TacticalTaskModulation,
    TechModulation,
    WeightedBiases,
    WorkerModulation,
    reject_raw_policy_control_keys,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileResult,
    compile_policy_modulation_provider_output,
)
from starcraft_commander.policy_observability import (
    PolicyModulationBridgeStatus,
    PolicyModulationDashboardSnapshot,
    build_policy_modulation_dashboard_snapshot,
)


LATEST_UPDATE_JSON_NAME: Final[str] = "latest_modulation.json"
LATEST_UPDATE_KV_NAME: Final[str] = "latest_modulation.kv"
UPDATE_ARCHIVE_JSONL_NAME: Final[str] = "modulation_updates.jsonl"
LATEST_TELEMETRY_JSON_NAME: Final[str] = "latest_telemetry.json"
TELEMETRY_ARCHIVE_JSONL_NAME: Final[str] = "telemetry.jsonl"
MICROMACHINE_STRATEGY_PROFILE_VERSION: Final[int] = 1
MICROMACHINE_STRATEGY_PROFILE_KEYS: Final[tuple[str, ...]] = (
    "marine_rush",
    "bio_pressure",
    "tank_defensive_hold",
    "siege_contain",
    "mech_transition",
    "drop_harassment",
    "worker_line_harassment",
    "scouting_map_control",
    "expand_macro",
    "anti_air_response",
    "defensive_counterattack",
    "contain_enemy_natural",
    "defensive_hold",
    "economic_expansion",
    "aggressive_pressure",
    "tech_transition",
    "emergency_recovery",
)
MICROMACHINE_DOCTRINE_PROFILE_KEYS: Final[tuple[str, ...]] = (
    "marine_rush",
    "bio_pressure",
    "tank_defensive_hold",
    "siege_contain",
    "mech_transition",
    "drop_harassment",
    "worker_line_harassment",
    "scouting_map_control",
    "expand_macro",
    "anti_air_response",
    "defensive_counterattack",
    "contain_enemy_natural",
)
_KV_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:-]+$")


@runtime_checkable
class MicroMachineModulationBackend(Protocol):
    """Transport-independent backend for MicroMachine policy modulation."""

    def publish_vector(
        self,
        vector: PolicyModulationVector,
        *,
        current_frame: int,
        update_id: str | None = None,
        rollback_update_id: str | None = None,
    ) -> MicroMachineBlackboardUpdate:
        """Validate and publish one modulation vector."""

    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        """Validate and publish one already-built update."""

    def read_latest_update(
        self,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate | None:
        """Return the latest non-stale update, if any."""

    def ingest_telemetry(
        self,
        telemetry: MicroMachineTelemetry | Mapping[str, object],
    ) -> MicroMachineTelemetry:
        """Validate and ingest one telemetry snapshot."""

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        """Return the latest telemetry snapshot, if any."""

    def dashboard_snapshot(
        self,
        *,
        current_frame: int,
        bridge_status: PolicyModulationBridgeStatus | str = (
            PolicyModulationBridgeStatus.SIMULATED
        ),
    ) -> PolicyModulationDashboardSnapshot:
        """Build a transport-independent dashboard snapshot."""

    def write_provider_unavailable(
        self,
        *,
        current_frame: int,
        reason: str,
    ) -> MicroMachineTelemetry:
        """Record a provider-unavailable failure state."""


@dataclass(frozen=True)
class MicroMachineBackendPublishResult:
    """Result of compiling provider output and publishing through a backend."""

    compile_result: PolicyModulationCompileResult
    update: MicroMachineBlackboardUpdate | None = None

    @property
    def ok(self) -> bool:
        return self.compile_result.ok and self.update is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "compile_result": self.compile_result.to_dict(),
            "update": self.update.to_dict() if self.update else None,
        }


def build_defensive_hold_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a bounded MicroMachine profile for holding while scouting safely."""

    return PolicyModulationVector(
        goal="micromachine_defensive_hold",
        source=source,
        override_level=PolicyOverrideLevel.CONSTRAINT,
        confidence=0.85,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(posture="defensive", doctrine=""),
        combat=CombatModulation(
            aggression=-0.35,
            engage_threshold_delta=0.2,
            retreat_threshold_delta=0.2,
            defend_bias=0.85,
            preserve_army_bias=0.6,
            combat_sim_confidence_margin=0.15,
        ),
        scouting=ScoutingModulation(
            scout_priority=0.25,
            risk_tolerance=-0.35,
            require_fresh_enemy_observation=True,
        ),
        squad=SquadModulation(defense_bias=0.65, regroup_bias=0.45),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        emergency=EmergencyModulation(hold_position=True),
        constraints=(
            PolicySafetyConstraint(
                key="no_raw_unit_control",
                value=True,
                reason="Profile may bias managers but must not issue direct unit commands.",
            ),
        ),
        tags=("micromachine", "defensive_hold", "bounded_intervention"),
        rationale="Hold the army near home, preserve units, and keep scouting evidence fresh.",
    )


def build_manual_live_autonomy_profile(
    *,
    ttl_seconds: int = 900,
    source: PolicyModulationSource | str = PolicyModulationSource.SYSTEM,
) -> PolicyModulationVector:
    """Keep manual live QA neutral while retaining runtime safety guards."""

    return PolicyModulationVector(
        goal="micromachine_manual_live_autonomy",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=1.0,
        ttl_seconds=ttl_seconds,
        workers=WorkerModulation(repeat_order_guard_frames=32),
        lifetime=LifetimeModulation(
            mode="standing_order",
            completion_conditions=("cancelled_by_user",),
            completion_state="active",
            reason="neutral manual live bootstrap remains until a user command replaces it",
        ),
        constraints=(
            PolicySafetyConstraint(
                key="no_raw_unit_control",
                value=True,
                reason="Manual live QA preserves autonomous manager ownership.",
            ),
        ),
        tags=("micromachine", "manual_live_autonomy", "standing_order"),
        rationale=(
            "Do not bias combat, scouting, production, or squads before the user "
            "issues a command."
        ),
    )


def build_aggressive_pressure_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a bounded MicroMachine profile for pressure without raw commands."""

    return PolicyModulationVector(
        goal="micromachine_aggressive_pressure",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.82,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="pressure",
            doctrine="bio_pressure",
            preferred_builds=WeightedBiases({"bio_pressure": 0.55}),
        ),
        combat=CombatModulation(
            aggression=0.55,
            engage_threshold_delta=-0.15,
            retreat_threshold_delta=-0.1,
            attack_timing_bias=0.2,
            commitment_level=0.25,
            attack_condition_override="earlier_if_safe",
            retreat_patience_bias=0.15,
            rally_before_attack_bias=0.1,
            harassment_bias=0.35,
            defend_bias=0.15,
            combat_sim_confidence_margin=-0.1,
            target_priority_biases=WeightedBiases(
                {
                    "worker_line": 0.35,
                    "townhall": 0.25,
                    "army": 0.15,
                }
            ),
        ),
        production=ProductionModulation(production_continuity_bias=0.55),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        scouting=ScoutingModulation(
            scout_priority=0.7,
            risk_tolerance=0.45,
            require_fresh_enemy_observation=False,
        ),
        squad=SquadModulation(
            main_army_bias=0.45,
            harassment_bias=0.45,
            defense_bias=-0.2,
            reinforce_bias=0.25,
            contain_bias=0.25,
        ),
        scope=TacticalScopeModulation(
            army_group="main",
            location_intent="enemy_natural",
            min_units=2,
            require_safety_margin=0.1,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="pressure_with_main_army",
            task_id="profile-aggressive-pressure",
            unit_classes=("TERRAN_MARINE", "TERRAN_MARAUDER", "TERRAN_MEDIVAC"),
            location_intent="enemy_natural",
            priority=0.7,
            min_units=2,
            duration_seconds=ttl_seconds,
            allow_partial=True,
            safety_margin=0.1,
        ),
        tags=("micromachine", "aggressive_pressure", "bounded_intervention"),
        rationale="Bias MicroMachine toward pressure while leaving tactical execution autonomous.",
    )


def build_economic_expansion_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a bounded profile that biases economy without raw build commands."""

    return PolicyModulationVector(
        goal="micromachine_economic_expansion",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.78,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="economic",
            doctrine="expand_macro",
            timing_biases=WeightedBiases({"third_base_timing": 0.35}),
        ),
        economy=EconomyModulation(
            expand_bias=0.65,
            worker_production_bias=0.45,
            gas_priority=0.2,
            mineral_saturation_bias=0.55,
            supply_buffer_bias=0.35,
            expansion_safety_bias=0.25,
        ),
        production=ProductionModulation(production_continuity_bias=0.35),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        combat=CombatModulation(defend_bias=0.55, aggression=-0.15),
        scouting=ScoutingModulation(scout_priority=0.45, risk_tolerance=-0.05),
        squad=SquadModulation(defense_bias=0.35, regroup_bias=0.25),
        tags=("micromachine", "economic_expansion", "bounded_intervention"),
        rationale="Bias MicroMachine toward safer worker, supply, and expansion continuity.",
    )


def build_scouting_map_control_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a bounded profile for fresh information and map presence."""

    return PolicyModulationVector(
        goal="micromachine_scouting_map_control",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.8,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="balanced",
            doctrine="scouting_map_control",
            strategic_tags=("map_control",),
        ),
        combat=CombatModulation(aggression=0.15, defend_bias=0.35, harassment_bias=0.25),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        production=ProductionModulation(
            production_continuity_bias=0.45,
            queue_biases=WeightedBiases({"TERRAN_MARINE": 0.7}),
        ),
        scouting=ScoutingModulation(
            scout_priority=0.9,
            risk_tolerance=0.25,
            scan_priority=0.45,
            require_fresh_enemy_observation=True,
        ),
        squad=SquadModulation(main_army_bias=0.2, harassment_bias=0.25, defense_bias=0.05),
        tactical_task=TacticalTaskModulation(
            task_type="scout_with_units",
            task_id="profile-scouting-map-control",
            unit_classes=("TERRAN_MARINE",),
            production_targets=("TERRAN_MARINE",),
            location_intent="enemy_main",
            priority=0.8,
            min_units=1,
            max_units=3,
            duration_seconds=ttl_seconds,
            allow_partial=True,
            safety_margin=0.1,
        ),
        tags=("micromachine", "scouting_map_control", "bounded_intervention"),
        rationale="Bias scouting and harassment managers toward fresh enemy observations.",
    )


def build_tech_transition_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a bounded profile for higher-tech composition transition."""

    return PolicyModulationVector(
        goal="micromachine_tech_transition",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.76,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="balanced",
            doctrine="mech_transition",
            transition_biases=WeightedBiases({"bio_to_factory": 0.45}),
        ),
        economy=EconomyModulation(gas_priority=0.55, gas_worker_target_bias=0.35),
        tech=TechModulation(
            structure_biases=WeightedBiases({"TERRAN_FACTORY": 0.45, "TERRAN_STARPORT": 0.25}),
            unit_biases=WeightedBiases({"TERRAN_SIEGETANK": 0.5, "TERRAN_MEDIVAC": 0.35}),
            upgrade_biases=WeightedBiases({"STIMPACK": 0.35}),
            tech_path_tags=("factory_transition",),
        ),
        production=ProductionModulation(
            production_continuity_bias=0.55,
            addon_biases=WeightedBiases({"TECHLAB": 0.45}),
            max_tech_deviation=0.35,
            tech_switch_urgency=0.4,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="tech_transition",
            task_id="profile-tech-transition",
            production_targets=(
                "TERRAN_FACTORY",
                "FACTORY_TECHLAB",
                "TERRAN_SIEGETANK",
                "TERRAN_MEDIVAC",
            ),
            priority=0.65,
            duration_seconds=ttl_seconds,
            allow_partial=True,
        ),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        combat=CombatModulation(defend_bias=0.35, preserve_army_bias=0.35),
        tags=("micromachine", "tech_transition", "bounded_intervention"),
        rationale="Bias tech and production managers toward a safe mid-game transition.",
    )


def build_emergency_recovery_profile(
    *,
    ttl_seconds: int = 180,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a short-TTL emergency profile for recovery without raw retreat clicks."""

    ttl_seconds = min(ttl_seconds, 60)
    return PolicyModulationVector(
        goal="micromachine_emergency_recovery",
        source=source,
        override_level=PolicyOverrideLevel.EMERGENCY,
        confidence=0.88,
        ttl_seconds=ttl_seconds,
        economy=EconomyModulation(repair_priority=0.85, supply_buffer_bias=0.4),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        combat=CombatModulation(
            aggression=-0.75,
            retreat_threshold_delta=0.35,
            defend_bias=0.95,
            preserve_army_bias=0.8,
        ),
        scouting=ScoutingModulation(scout_priority=0.2, risk_tolerance=-0.55),
        squad=SquadModulation(defense_bias=0.9, regroup_bias=0.7),
        emergency=EmergencyModulation(
            force_retreat=True,
            cancel_attacks=True,
            prioritize_repair=True,
            hold_position=True,
        ),
        constraints=(
            PolicySafetyConstraint(
                key="short_ttl_emergency_only",
                value=True,
                reason="Emergency recovery must expire quickly and remain manager-bounded.",
            ),
        ),
        tags=("micromachine", "emergency_recovery", "bounded_intervention"),
        rationale="Bias MicroMachine toward survival and repair while avoiding direct unit control.",
    )


def build_marine_rush_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a doctrine profile that keeps early bio pressure Marine-focused."""

    return PolicyModulationVector(
        goal="micromachine_marine_rush",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.82,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="pressure",
            doctrine="marine_rush",
            preferred_builds=WeightedBiases({"marine_rush": 0.8}),
            timing_biases=WeightedBiases({"early_barracks_pressure": 0.7}),
        ),
        economy=EconomyModulation(worker_production_bias=0.25, supply_buffer_bias=0.35),
        tech=TechModulation(
            unit_biases=WeightedBiases({"TERRAN_MARINE": 0.85, "TERRAN_REAPER": 0.2}),
            upgrade_biases=WeightedBiases({"STIMPACK": 0.25}),
            tech_path_tags=("bio", "low_tech_pressure"),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases({"TERRAN_MARINE": 0.85, "TERRAN_BARRACKS": 0.55}),
            composition_biases=WeightedBiases({"bio": 0.75, "marine": 0.9}),
            production_facility_biases=WeightedBiases({"TERRAN_BARRACKS": 0.65}),
            production_continuity_bias=0.75,
            tech_switch_urgency=-0.35,
        ),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        combat=CombatModulation(
            aggression=0.72,
            attack_timing_bias=0.65,
            commitment_level=0.55,
            attack_condition_override="force_when_threshold_met",
            retreat_patience_bias=0.25,
            target_priority_biases=WeightedBiases({"army": 0.35, "worker_line": 0.25}),
        ),
        scouting=ScoutingModulation(scout_priority=0.65, risk_tolerance=0.35),
        squad=SquadModulation(main_army_bias=0.7, reinforce_bias=0.35),
        scope=TacticalScopeModulation(
            army_group="bio",
            unit_classes=("marine", "reaper"),
            location_intent="enemy_natural",
            min_units=2,
            require_safety_margin=0.05,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="pressure_with_main_army",
            task_id="profile-marine-rush",
            unit_classes=("TERRAN_MARINE", "TERRAN_REAPER"),
            production_targets=("TERRAN_MARINE", "TERRAN_SUPPLYDEPOT"),
            location_intent="enemy_natural",
            priority=0.8,
            min_units=2,
            duration_seconds=ttl_seconds,
            allow_partial=True,
            safety_margin=0.05,
        ),
        tags=("micromachine", "marine_rush", "bounded_intervention"),
        rationale="Bias existing MicroMachine opening toward early Marine pressure.",
    )


def build_bio_pressure_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a bio pressure profile with Marine/Marauder/Medivac support."""

    return replace(
        build_aggressive_pressure_profile(ttl_seconds=ttl_seconds, source=source),
        goal="micromachine_bio_pressure",
        strategy=StrategyModulation(
            posture="pressure",
            doctrine="bio_pressure",
            preferred_builds=WeightedBiases({"bio_pressure": 0.7}),
        ),
        tech=TechModulation(
            unit_biases=WeightedBiases(
                {
                    "TERRAN_MARINE": 0.65,
                    "TERRAN_MARAUDER": 0.45,
                    "TERRAN_MEDIVAC": 0.35,
                }
            ),
            upgrade_biases=WeightedBiases({"STIMPACK": 0.55, "COMBATSHIELD": 0.35}),
            tech_path_tags=("bio", "medivac_support"),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases(
                {
                    "TERRAN_MARINE": 0.55,
                    "TERRAN_MARAUDER": 0.35,
                    "TERRAN_MEDIVAC": 0.25,
                    "BARRACKS_TECHLAB": 0.45,
                }
            ),
            composition_biases=WeightedBiases({"bio": 0.75, "medivac_support": 0.25}),
            production_facility_biases=WeightedBiases(
                {"TERRAN_BARRACKS": 0.55, "TERRAN_STARPORT": 0.25}
            ),
            addon_biases=WeightedBiases({"BARRACKS_TECHLAB": 0.45, "BARRACKS_REACTOR": 0.35}),
            production_continuity_bias=0.55,
            tech_switch_urgency=0.1,
        ),
        tags=("micromachine", "bio_pressure", "bounded_intervention"),
    )


def build_tank_defensive_hold_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a defensive hold profile that biases Factory/TechLab/Tank paths."""

    return replace(
        build_defensive_hold_profile(ttl_seconds=ttl_seconds, source=source),
        goal="micromachine_tank_defensive_hold",
        strategy=StrategyModulation(
            posture="defensive",
            doctrine="tank_defensive_hold",
            preferred_builds=WeightedBiases({"two_base_tank_hold": 0.75}),
            transition_biases=WeightedBiases({"bio_to_factory": 0.45}),
        ),
        economy=EconomyModulation(gas_priority=0.65, repair_priority=0.35),
        tech=TechModulation(
            structure_biases=WeightedBiases({"TERRAN_FACTORY": 0.6}),
            unit_biases=WeightedBiases({"TERRAN_SIEGETANK": 0.85, "TERRAN_MARINE": 0.15}),
            upgrade_biases=WeightedBiases({"TERRANVEHICLEWEAPONSLEVEL1": 0.25}),
            tech_path_tags=("factory", "siege"),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases(
                {
                    "TERRAN_FACTORY": 0.65,
                    "FACTORY_TECHLAB": 0.65,
                    "TERRAN_SIEGETANK": 0.85,
                }
            ),
            composition_biases=WeightedBiases({"siege": 0.75, "mech": 0.55}),
            addon_biases=WeightedBiases({"FACTORY_TECHLAB": 0.7}),
            production_facility_biases=WeightedBiases({"TERRAN_FACTORY": 0.65}),
            production_continuity_bias=-0.25,
            tech_switch_urgency=0.65,
            allow_build_order_rewrite=True,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="tech_transition",
            task_id="profile-tank-defensive-hold",
            production_targets=("TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_SIEGETANK"),
            priority=0.75,
            duration_seconds=ttl_seconds,
            allow_partial=True,
        ),
        combat=CombatModulation(
            aggression=-0.25,
            defend_bias=0.85,
            preserve_army_bias=0.7,
            siege_position_bias=0.8,
            target_priority_biases=WeightedBiases({"army": 0.35}),
        ),
        tags=("micromachine", "tank_defensive_hold", "bounded_intervention"),
    )


def build_siege_contain_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a siege contain profile that transitions into controlled pressure."""

    base = build_tank_defensive_hold_profile(ttl_seconds=ttl_seconds, source=source)
    return replace(
        base,
        goal="micromachine_siege_contain",
        strategy=StrategyModulation(
            posture="pressure",
            doctrine="siege_contain",
            preferred_builds=WeightedBiases({"two_base_tank_push": 0.75}),
            timing_biases=WeightedBiases({"tank_push": 0.65}),
            transition_biases=WeightedBiases({"bio_to_factory": 0.55}),
        ),
        combat=CombatModulation(
            aggression=0.35,
            attack_timing_bias=0.45,
            commitment_level=0.5,
            attack_condition_override="earlier_if_safe",
            retreat_patience_bias=0.45,
            siege_position_bias=0.8,
            target_priority_biases=WeightedBiases({"townhall": 0.35, "production": 0.35}),
        ),
        squad=SquadModulation(main_army_bias=0.55, contain_bias=0.75, reinforce_bias=0.45),
        scope=TacticalScopeModulation(
            army_group="siege",
            unit_classes=("marine", "siege_tank"),
            location_intent="enemy_natural",
            min_units=4,
            require_safety_margin=0.15,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="pressure_with_main_army",
            task_id="profile-siege-contain",
            unit_classes=("TERRAN_MARINE", "TERRAN_SIEGETANK"),
            production_targets=("TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_SIEGETANK"),
            location_intent="enemy_natural",
            priority=0.75,
            min_units=4,
            duration_seconds=ttl_seconds,
            allow_partial=True,
            safety_margin=0.15,
        ),
        tags=("micromachine", "siege_contain", "bounded_intervention"),
    )


def build_mech_transition_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a mech transition profile that de-emphasizes Marine continuity."""

    base = build_tech_transition_profile(ttl_seconds=ttl_seconds, source=source)
    return replace(
        base,
        goal="micromachine_mech_transition",
        strategy=StrategyModulation(
            posture="balanced",
            doctrine="mech_transition",
            transition_biases=WeightedBiases({"bio_to_mech": 0.8}),
        ),
        tech=TechModulation(
            structure_biases=WeightedBiases({"TERRAN_FACTORY": 0.75, "TERRAN_ARMORY": 0.25}),
            unit_biases=WeightedBiases(
                {
                    "TERRAN_HELLION": 0.45,
                    "TERRAN_CYCLONE": 0.35,
                    "TERRAN_SIEGETANK": 0.65,
                    "TERRAN_THOR": 0.2,
                }
            ),
            upgrade_biases=WeightedBiases({"TERRANVEHICLEWEAPONSLEVEL1": 0.4}),
            tech_path_tags=("mech", "factory"),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases(
                {
                    "TERRAN_FACTORY": 0.75,
                    "FACTORY_TECHLAB": 0.55,
                    "TERRAN_HELLION": 0.35,
                    "TERRAN_SIEGETANK": 0.6,
                }
            ),
            composition_biases=WeightedBiases({"mech": 0.85, "bio": -0.45}),
            addon_biases=WeightedBiases({"FACTORY_TECHLAB": 0.55, "FACTORY_REACTOR": 0.25}),
            production_facility_biases=WeightedBiases({"TERRAN_FACTORY": 0.75}),
            production_continuity_bias=-0.55,
            tech_switch_urgency=0.85,
            allow_build_order_rewrite=True,
        ),
        combat=CombatModulation(
            defend_bias=0.35,
            preserve_army_bias=0.35,
            target_priority_biases=WeightedBiases({"army": 0.35}),
        ),
        squad=SquadModulation(
            main_army_bias=0.35,
            defense_bias=0.25,
            regroup_bias=0.3,
            reinforce_bias=0.25,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="tech_transition",
            task_id="profile-mech-transition",
            production_targets=(
                "TERRAN_FACTORY",
                "FACTORY_TECHLAB",
                "TERRAN_HELLION",
                "TERRAN_SIEGETANK",
            ),
            priority=0.8,
            duration_seconds=ttl_seconds,
            allow_partial=True,
        ),
        tags=("micromachine", "mech_transition", "bounded_intervention"),
    )


def build_drop_harassment_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a profile that biases Medivac/drop harassment support."""

    return PolicyModulationVector(
        goal="micromachine_drop_harassment",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.8,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="pressure",
            doctrine="drop_harassment",
            preferred_builds=WeightedBiases({"medivac_drop": 0.75}),
        ),
        economy=EconomyModulation(gas_priority=0.45),
        tech=TechModulation(
            structure_biases=WeightedBiases({"TERRAN_STARPORT": 0.65}),
            unit_biases=WeightedBiases({"TERRAN_MEDIVAC": 0.8, "TERRAN_MARINE": 0.45}),
            upgrade_biases=WeightedBiases({"STIMPACK": 0.35}),
            tech_path_tags=("bio", "drop"),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases(
                {
                    "TERRAN_FACTORY": 0.45,
                    "TERRAN_STARPORT": 0.65,
                    "TERRAN_MEDIVAC": 0.85,
                    "TERRAN_MARINE": 0.35,
                }
            ),
            composition_biases=WeightedBiases({"drop": 0.85, "bio": 0.45}),
            addon_biases=WeightedBiases({"STARPORT_REACTOR": 0.45}),
            production_facility_biases=WeightedBiases(
                {"TERRAN_FACTORY": 0.45, "TERRAN_STARPORT": 0.65}
            ),
            production_continuity_bias=0.15,
            tech_switch_urgency=0.55,
            allow_build_order_rewrite=True,
        ),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        combat=CombatModulation(
            aggression=0.45,
            harassment_bias=0.8,
            commitment_level=0.35,
            retreat_patience_bias=0.5,
            target_priority_biases=WeightedBiases({"worker_line": 0.8, "production": 0.25}),
        ),
        scouting=ScoutingModulation(scout_priority=0.6, risk_tolerance=0.4),
        squad=SquadModulation(harassment_bias=0.75, drop_bias=0.85, split_army_bias=0.45),
        scope=TacticalScopeModulation(
            army_group="harass",
            unit_classes=("marine", "medivac"),
            location_intent="enemy_main",
            min_units=2,
            allow_partial_scope=True,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="pressure_with_main_army",
            task_id="profile-drop-harassment",
            unit_classes=("TERRAN_MARINE", "TERRAN_MEDIVAC"),
            production_targets=("TERRAN_STARPORT", "TERRAN_MEDIVAC", "TERRAN_MARINE"),
            location_intent="enemy_main",
            priority=0.7,
            min_units=2,
            duration_seconds=ttl_seconds,
            allow_partial=True,
            safety_margin=0.1,
        ),
        tags=("micromachine", "drop_harassment", "bounded_intervention"),
        rationale="Bias MicroMachine toward drop-capable harassment without direct transport commands.",
    )


def build_worker_line_harassment_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a profile that prioritizes worker-line pressure through bot managers."""

    base = build_drop_harassment_profile(ttl_seconds=ttl_seconds, source=source)
    return replace(
        base,
        goal="micromachine_worker_line_harassment",
        strategy=StrategyModulation(
            posture="pressure",
            doctrine="worker_line_harassment",
            preferred_builds=WeightedBiases({"worker_line_harass": 0.8}),
        ),
        tech=TechModulation(
            structure_biases=WeightedBiases({"TERRAN_FACTORY": 0.35, "TERRAN_STARPORT": 0.35}),
            unit_biases=WeightedBiases(
                {"TERRAN_REAPER": 0.35, "TERRAN_HELLION": 0.55, "TERRAN_MEDIVAC": 0.35}
            ),
            tech_path_tags=("harass", "mobility"),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases(
                {"TERRAN_REAPER": 0.35, "TERRAN_HELLION": 0.55, "TERRAN_MEDIVAC": 0.35}
            ),
            composition_biases=WeightedBiases({"harass": 0.85, "worker_line": 0.85}),
            production_facility_biases=WeightedBiases(
                {"TERRAN_FACTORY": 0.35, "TERRAN_STARPORT": 0.35}
            ),
            production_continuity_bias=0.05,
            tech_switch_urgency=0.45,
        ),
        combat=CombatModulation(
            aggression=0.5,
            harassment_bias=0.85,
            retreat_patience_bias=0.55,
            target_priority_biases=WeightedBiases({"worker_line": 0.95, "townhall": 0.15}),
        ),
        squad=SquadModulation(harassment_bias=0.85, split_army_bias=0.4, drop_bias=0.35),
        tags=("micromachine", "worker_line_harassment", "bounded_intervention"),
    )


def build_expand_macro_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return an expansion-first macro profile."""

    return replace(
        build_economic_expansion_profile(ttl_seconds=ttl_seconds, source=source),
        goal="micromachine_expand_macro",
        strategy=StrategyModulation(
            posture="economic",
            doctrine="expand_macro",
            preferred_builds=WeightedBiases({"safe_expand": 0.75}),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases({"TERRAN_COMMANDCENTER": 0.75, "TERRAN_SCV": 0.55}),
            composition_biases=WeightedBiases({"macro": 0.85}),
            production_continuity_bias=0.2,
            tech_switch_urgency=-0.2,
        ),
        tactical_task=TacticalTaskModulation(
            task_type="expand_or_land_command_center",
            task_id="profile-expand-macro",
            production_targets=("TERRAN_COMMANDCENTER", "TERRAN_SCV", "TERRAN_SUPPLYDEPOT"),
            location_intent="safe_expansion",
            priority=0.75,
            duration_seconds=ttl_seconds,
            allow_partial=True,
            safety_margin=0.2,
        ),
        tags=("micromachine", "expand_macro", "bounded_intervention"),
    )


def build_anti_air_response_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return an anti-air response profile using MicroMachine's Viking/turret paths."""

    return PolicyModulationVector(
        goal="micromachine_anti_air_response",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.78,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="defensive",
            doctrine="anti_air_response",
            preferred_builds=WeightedBiases({"anti_air_response": 0.75}),
        ),
        economy=EconomyModulation(gas_priority=0.55, repair_priority=0.25),
        tech=TechModulation(
            structure_biases=WeightedBiases(
                {"TERRAN_STARPORT": 0.55, "TERRAN_ENGINEERINGBAY": 0.35}
            ),
            unit_biases=WeightedBiases({"TERRAN_VIKINGFIGHTER": 0.85, "TERRAN_MARINE": 0.25}),
            upgrade_biases=WeightedBiases({"HISECAUTOTRACKING": 0.4}),
            tech_path_tags=("anti_air",),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases(
                {
                    "TERRAN_STARPORT": 0.55,
                    "TERRAN_VIKINGFIGHTER": 0.85,
                    "TERRAN_MISSILETURRET": 0.35,
                }
            ),
            composition_biases=WeightedBiases({"anti_air": 0.9}),
            production_facility_biases=WeightedBiases({"TERRAN_STARPORT": 0.55}),
            production_continuity_bias=-0.1,
            tech_switch_urgency=0.65,
            allow_build_order_rewrite=True,
        ),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        combat=CombatModulation(
            defend_bias=0.65,
            preserve_army_bias=0.45,
            target_priority_biases=WeightedBiases({"air_threat": 0.95, "detector": 0.3}),
        ),
        scouting=ScoutingModulation(scan_priority=0.55, hidden_tech_scout_bias=0.55),
        squad=SquadModulation(defense_bias=0.45, main_army_bias=0.25),
        scope=TacticalScopeModulation(army_group="air", unit_classes=("viking", "marine")),
        tags=("micromachine", "anti_air_response", "bounded_intervention"),
        rationale="Bias MicroMachine toward anti-air production and target priority.",
    )


def build_defensive_counterattack_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a profile that holds first and counterattacks when safe."""

    return PolicyModulationVector(
        goal="micromachine_defensive_counterattack",
        source=source,
        override_level=PolicyOverrideLevel.BIAS,
        confidence=0.8,
        ttl_seconds=ttl_seconds,
        strategy=StrategyModulation(
            posture="defensive",
            doctrine="defensive_counterattack",
            timing_biases=WeightedBiases({"counterattack_after_hold": 0.7}),
        ),
        economy=EconomyModulation(repair_priority=0.4, supply_buffer_bias=0.25),
        tech=TechModulation(
            unit_biases=WeightedBiases({"TERRAN_MARINE": 0.35, "TERRAN_SIEGETANK": 0.35}),
            tech_path_tags=("counterattack",),
        ),
        production=ProductionModulation(
            queue_biases=WeightedBiases({"TERRAN_MARINE": 0.35, "TERRAN_SIEGETANK": 0.35}),
            composition_biases=WeightedBiases({"defense": 0.6, "counterattack": 0.55}),
            production_continuity_bias=0.25,
            tech_switch_urgency=0.25,
        ),
        workers=WorkerModulation(repeat_order_guard_frames=32),
        combat=CombatModulation(
            aggression=0.25,
            defend_bias=0.65,
            preserve_army_bias=0.55,
            attack_timing_bias=0.35,
            attack_condition_override="earlier_if_safe",
            commitment_level=0.3,
        ),
        scouting=ScoutingModulation(require_fresh_enemy_observation=True, scout_priority=0.45),
        squad=SquadModulation(defense_bias=0.55, regroup_bias=0.45, main_army_bias=0.35),
        tags=("micromachine", "defensive_counterattack", "bounded_intervention"),
        rationale="Bias MicroMachine to defend first and pressure only after safe evidence.",
    )


def build_contain_enemy_natural_profile(
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Return a profile that biases contain pressure at enemy natural."""

    return replace(
        build_siege_contain_profile(ttl_seconds=ttl_seconds, source=source),
        goal="micromachine_contain_enemy_natural",
        strategy=StrategyModulation(
            posture="pressure",
            doctrine="contain_enemy_natural",
            preferred_builds=WeightedBiases({"enemy_natural_contain": 0.85}),
        ),
        squad=SquadModulation(main_army_bias=0.55, contain_bias=0.85, reinforce_bias=0.55),
        scope=TacticalScopeModulation(
            army_group="main",
            unit_classes=("marine", "marauder", "siege_tank"),
            location_intent="enemy_natural",
            min_units=4,
            require_safety_margin=0.12,
        ),
        tags=("micromachine", "contain_enemy_natural", "bounded_intervention"),
    )


def build_micromachine_strategy_profile(
    profile_key: str,
    *,
    ttl_seconds: int = 600,
    source: PolicyModulationSource | str = PolicyModulationSource.LLM,
) -> PolicyModulationVector:
    """Build one named MicroMachine strategy profile."""

    builders = {
        "marine_rush": build_marine_rush_profile,
        "bio_pressure": build_bio_pressure_profile,
        "tank_defensive_hold": build_tank_defensive_hold_profile,
        "siege_contain": build_siege_contain_profile,
        "mech_transition": build_mech_transition_profile,
        "drop_harassment": build_drop_harassment_profile,
        "worker_line_harassment": build_worker_line_harassment_profile,
        "scouting_map_control": build_scouting_map_control_profile,
        "expand_macro": build_expand_macro_profile,
        "anti_air_response": build_anti_air_response_profile,
        "defensive_counterattack": build_defensive_counterattack_profile,
        "contain_enemy_natural": build_contain_enemy_natural_profile,
        "defensive_hold": build_defensive_hold_profile,
        "economic_expansion": build_economic_expansion_profile,
        "aggressive_pressure": build_aggressive_pressure_profile,
        "tech_transition": build_tech_transition_profile,
        "emergency_recovery": build_emergency_recovery_profile,
    }
    try:
        builder = builders[profile_key]
    except KeyError as exc:
        raise ValueError(f"unknown MicroMachine strategy profile: {profile_key}") from exc
    return builder(ttl_seconds=ttl_seconds, source=source)


def micromachine_strategy_profile_catalog() -> dict[str, dict[str, object]]:
    """Return versioned, JSON-ready profile metadata for docs and tests."""

    return {
        "schema_version": MICROMACHINE_STRATEGY_PROFILE_VERSION,
        "profiles": {
            "marine_rush": {
                "managers": ["StrategyManager", "ProductionManager", "CombatCommander"],
                "expected_tags": ["marine_rush", "bounded_intervention"],
            },
            "bio_pressure": {
                "managers": ["StrategyManager", "ProductionManager", "CombatCommander", "Squad"],
                "expected_tags": ["bio_pressure", "bounded_intervention"],
            },
            "tank_defensive_hold": {
                "managers": ["ProductionManager", "CombatCommander", "Squad"],
                "expected_tags": ["tank_defensive_hold", "bounded_intervention"],
            },
            "siege_contain": {
                "managers": ["ProductionManager", "CombatCommander", "Squad"],
                "expected_tags": ["siege_contain", "bounded_intervention"],
            },
            "mech_transition": {
                "managers": ["ProductionManager", "StrategyManager", "CombatCommander"],
                "expected_tags": ["mech_transition", "bounded_intervention"],
            },
            "drop_harassment": {
                "managers": ["ProductionManager", "ScoutManager", "Squad"],
                "expected_tags": ["drop_harassment", "bounded_intervention"],
            },
            "worker_line_harassment": {
                "managers": ["ProductionManager", "ScoutManager", "Squad"],
                "expected_tags": ["worker_line_harassment", "bounded_intervention"],
            },
            "scouting_map_control": {
                "managers": ["ScoutManager", "CombatCommander", "Squad"],
                "expected_tags": ["scouting_map_control", "bounded_intervention"],
            },
            "expand_macro": {
                "managers": ["WorkerManager", "ProductionManager", "CombatCommander"],
                "expected_tags": ["expand_macro", "bounded_intervention"],
            },
            "anti_air_response": {
                "managers": ["ProductionManager", "CombatCommander", "ScoutManager"],
                "expected_tags": ["anti_air_response", "bounded_intervention"],
            },
            "defensive_counterattack": {
                "managers": ["CombatCommander", "Squad", "ScoutManager"],
                "expected_tags": ["defensive_counterattack", "bounded_intervention"],
            },
            "contain_enemy_natural": {
                "managers": ["CombatCommander", "Squad", "ProductionManager"],
                "expected_tags": ["contain_enemy_natural", "bounded_intervention"],
            },
            "defensive_hold": {
                "managers": ["CombatCommander", "ScoutManager", "Squad"],
                "expected_tags": ["defensive_hold", "bounded_intervention"],
            },
            "economic_expansion": {
                "managers": ["WorkerManager", "ProductionManager", "CombatCommander"],
                "expected_tags": ["economic_expansion", "bounded_intervention"],
            },
            "aggressive_pressure": {
                "managers": ["CombatCommander", "ScoutManager", "Squad"],
                "expected_tags": ["aggressive_pressure", "bounded_intervention"],
            },
            "tech_transition": {
                "managers": ["ProductionManager", "WorkerManager", "CombatCommander"],
                "expected_tags": ["tech_transition", "bounded_intervention"],
            },
            "emergency_recovery": {
                "managers": ["CombatCommander", "WorkerManager", "Squad"],
                "expected_tags": ["emergency_recovery", "bounded_intervention"],
            },
        },
    }


@dataclass(frozen=True)
class MicroMachineRuntimePaths:
    """Filesystem locations shared by the Python sidecar and C++ bot."""

    root: Path | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))

    @property
    def latest_update_json(self) -> Path:
        return self.root / LATEST_UPDATE_JSON_NAME

    @property
    def latest_update_kv(self) -> Path:
        return self.root / LATEST_UPDATE_KV_NAME

    @property
    def update_archive_jsonl(self) -> Path:
        return self.root / UPDATE_ARCHIVE_JSONL_NAME

    @property
    def latest_telemetry_json(self) -> Path:
        return self.root / LATEST_TELEMETRY_JSON_NAME

    @property
    def telemetry_archive_jsonl(self) -> Path:
        return self.root / TELEMETRY_ARCHIVE_JSONL_NAME

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "latest_update_json": str(self.latest_update_json),
            "latest_update_kv": str(self.latest_update_kv),
            "update_archive_jsonl": str(self.update_archive_jsonl),
            "latest_telemetry_json": str(self.latest_telemetry_json),
            "telemetry_archive_jsonl": str(self.telemetry_archive_jsonl),
        }


class MicroMachineFilesystemBlackboard:
    """Atomic filesystem blackboard for a local MicroMachine sidecar."""

    def __init__(self, root: Path | str) -> None:
        self.paths = MicroMachineRuntimePaths(root)
        self.paths.ensure()

    def publish_vector(
        self,
        vector: PolicyModulationVector,
        *,
        current_frame: int,
        update_id: str | None = None,
        rollback_update_id: str | None = None,
    ) -> MicroMachineBlackboardUpdate:
        """Create and persist one validated modulation update."""

        update = MicroMachineBlackboardUpdate(
            update_id=update_id or _new_update_id(),
            vector=vector,
            issued_at_frame=_non_negative_int("current_frame", current_frame),
            rollback_update_id=rollback_update_id,
        )
        return self.publish_update(update, current_frame=current_frame)

    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        """Persist an update after stale/invalid validation."""

        result = validate_micromachine_blackboard_update(
            update.to_dict(),
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            reason = result.reason or "blackboard update was rejected."
            raise ValueError(reason)
        accepted = result.update
        if accepted is None:
            raise ValueError("blackboard update validation did not return an update.")
        document = accepted.to_dict()
        json_text = json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n"
        kv_text = flatten_blackboard_update(accepted)
        _append_jsonl(self.paths.update_archive_jsonl, document)
        _write_latest_update_transactionally(
            self.paths.latest_update_json,
            json_text,
            self.paths.latest_update_kv,
            kv_text,
        )
        return accepted

    def read_latest_update(
        self,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate | None:
        """Read and validate the latest update, returning ``None`` if absent."""

        if not self.paths.latest_update_json.exists():
            return None
        payload = _read_json_mapping(self.paths.latest_update_json)
        result = validate_micromachine_blackboard_update(
            payload,
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            raise ValueError(result.reason)
        return result.update

    def ingest_telemetry(
        self,
        telemetry: MicroMachineTelemetry | Mapping[str, object],
    ) -> MicroMachineTelemetry:
        """Validate and persist telemetry emitted by MicroMachine."""

        parsed = _coerce_telemetry(telemetry)
        document = parsed.to_dict()
        _atomic_write_text(
            self.paths.latest_telemetry_json,
            json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
        )
        _append_jsonl(self.paths.telemetry_archive_jsonl, document)
        return parsed

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        if not self.paths.latest_telemetry_json.exists():
            return None
        return MicroMachineTelemetry.from_mapping(
            _read_json_mapping(self.paths.latest_telemetry_json)
        )

    def dashboard_snapshot(
        self,
        *,
        current_frame: int,
        bridge_status: PolicyModulationBridgeStatus | str = PolicyModulationBridgeStatus.SIMULATED,
    ) -> PolicyModulationDashboardSnapshot:
        """Build a dashboard snapshot from latest files only."""

        updates: tuple[MicroMachineBlackboardUpdate, ...] = ()
        failure: MicroMachineBridgeFailureMode | None = None
        try:
            update = self.read_latest_update(current_frame=current_frame)
            if update is not None:
                updates = (update,)
        except (OSError, TypeError, ValueError):
            failure = MicroMachineBridgeFailureMode.INVALID_PAYLOAD
        try:
            telemetry = self.read_latest_telemetry()
            if telemetry is not None and telemetry.last_failure is not None:
                failure = telemetry.last_failure
        except (OSError, TypeError, ValueError):
            telemetry = None
            failure = MicroMachineBridgeFailureMode.INVALID_PAYLOAD
        return build_policy_modulation_dashboard_snapshot(
            updates,
            current_frame=current_frame,
            bridge_status=bridge_status,
            telemetry=telemetry,
            last_failure=failure,
        )

    def write_provider_unavailable(
        self,
        *,
        current_frame: int,
        reason: str,
    ) -> MicroMachineTelemetry:
        """Persist a telemetry failure state when the provider cannot produce intent."""

        return self.ingest_telemetry(
            MicroMachineTelemetry(
                frame=_non_negative_int("current_frame", current_frame),
                managers={
                    "Provider": {
                        "status": "unavailable",
                        "unavailable_reason": _require_text("reason", reason),
                    }
                },
                active_modulation_ids=(),
                last_failure=MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE,
            )
        )


class MicroMachineInMemoryBlackboard:
    """In-memory backend for tests and future live model-loop orchestration."""

    def __init__(self) -> None:
        self.latest_update: MicroMachineBlackboardUpdate | None = None
        self.update_archive: list[MicroMachineBlackboardUpdate] = []
        self.latest_telemetry: MicroMachineTelemetry | None = None
        self.telemetry_archive: list[MicroMachineTelemetry] = []

    def publish_vector(
        self,
        vector: PolicyModulationVector,
        *,
        current_frame: int,
        update_id: str | None = None,
        rollback_update_id: str | None = None,
    ) -> MicroMachineBlackboardUpdate:
        update = MicroMachineBlackboardUpdate(
            update_id=update_id or _new_update_id(),
            vector=vector,
            issued_at_frame=_non_negative_int("current_frame", current_frame),
            rollback_update_id=rollback_update_id,
        )
        return self.publish_update(update, current_frame=current_frame)

    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        result = validate_micromachine_blackboard_update(
            update.to_dict(),
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            raise ValueError(result.reason or "blackboard update was rejected.")
        accepted = result.update
        if accepted is None:
            raise ValueError("blackboard update validation did not return an update.")
        self.latest_update = accepted
        self.update_archive.append(accepted)
        return accepted

    def read_latest_update(
        self,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate | None:
        if self.latest_update is None:
            return None
        result = validate_micromachine_blackboard_update(
            self.latest_update.to_dict(),
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            raise ValueError(result.reason)
        return result.update

    def ingest_telemetry(
        self,
        telemetry: MicroMachineTelemetry | Mapping[str, object],
    ) -> MicroMachineTelemetry:
        parsed = _coerce_telemetry(telemetry)
        stored = _clone_telemetry(parsed)
        self.latest_telemetry = stored
        self.telemetry_archive.append(stored)
        return _clone_telemetry(stored)

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        if self.latest_telemetry is None:
            return None
        return _clone_telemetry(self.latest_telemetry)

    def dashboard_snapshot(
        self,
        *,
        current_frame: int,
        bridge_status: PolicyModulationBridgeStatus | str = (
            PolicyModulationBridgeStatus.SIMULATED
        ),
    ) -> PolicyModulationDashboardSnapshot:
        updates: tuple[MicroMachineBlackboardUpdate, ...] = ()
        failure: MicroMachineBridgeFailureMode | None = None
        try:
            update = self.read_latest_update(current_frame=current_frame)
            if update is not None:
                updates = (update,)
        except ValueError:
            failure = MicroMachineBridgeFailureMode.INVALID_PAYLOAD
        telemetry = self.read_latest_telemetry()
        if telemetry is not None and telemetry.last_failure is not None:
            failure = telemetry.last_failure
        return build_policy_modulation_dashboard_snapshot(
            updates,
            current_frame=current_frame,
            bridge_status=bridge_status,
            telemetry=telemetry,
            last_failure=failure,
        )

    def write_provider_unavailable(
        self,
        *,
        current_frame: int,
        reason: str,
    ) -> MicroMachineTelemetry:
        return self.ingest_telemetry(
            MicroMachineTelemetry(
                frame=_non_negative_int("current_frame", current_frame),
                managers={
                    "Provider": {
                        "status": "unavailable",
                        "unavailable_reason": _require_text("reason", reason),
                    }
                },
                active_modulation_ids=(),
                last_failure=MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE,
            )
        )


def publish_policy_modulation_provider_output(
    provider_output: object,
    backend: MicroMachineModulationBackend,
    *,
    current_frame: int,
    default_source: PolicyModulationSource | str = (
        PolicyModulationSource.NEURAL_REPRESENTATION
    ),
    default_goal: str | None = None,
    update_id: str | None = None,
    rollback_update_id: str | None = None,
) -> MicroMachineBackendPublishResult:
    """Compile provider output and publish it through any modulation backend."""

    compile_result = compile_policy_modulation_provider_output(
        provider_output,
        default_source=default_source,
        default_goal=default_goal,
    )
    if not compile_result.ok or compile_result.vector is None:
        return MicroMachineBackendPublishResult(compile_result=compile_result)
    update = backend.publish_vector(
        compile_result.vector,
        current_frame=current_frame,
        update_id=update_id,
        rollback_update_id=rollback_update_id,
    )
    return MicroMachineBackendPublishResult(
        compile_result=compile_result,
        update=update,
    )


def flatten_blackboard_update(update: MicroMachineBlackboardUpdate) -> str:
    """Return a C++-friendly ``key=value`` representation of an update."""

    document = update.to_dict()
    vector = document["vector"]
    if not isinstance(vector, Mapping):
        raise ValueError("update vector must be a mapping.")
    rows: list[tuple[str, object]] = [
        ("protocol_version", MICROMACHINE_BRIDGE_PROTOCOL_VERSION),
        ("update_id", update.update_id),
        ("issued_at_frame", update.issued_at_frame),
        ("expires_at_frame", update.expires_at_frame),
        ("rollback_update_id", update.rollback_update_id or ""),
        ("goal", vector["goal"]),
        ("source", vector["source"]),
        ("override_level", vector["override_level"]),
        ("confidence", vector["confidence"]),
        ("ttl_seconds", vector["ttl_seconds"]),
        ("manager_bias_domains", ",".join(update.manager_bias_domains)),
    ]
    for domain in (
        "strategy",
        "economy",
        "workers",
        "tech",
        "production",
        "combat",
        "scouting",
        "squad",
        "scope",
        "lifetime",
        "tactical_task",
        "emergency",
        "production_plan",
        "route_intent",
        "target_intent",
    ):
        value = vector.get(domain, {})
        if isinstance(value, Mapping):
            _flatten_mapping(rows, domain, value)
    for domain in ("composition_requirements", "unit_roles", "building_tasks"):
        value = vector.get(domain, ())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    _flatten_mapping(rows, f"{domain}.{index}", item)
    constraints = vector.get("constraints", ())
    if isinstance(constraints, list):
        rows.append(("constraints.count", len(constraints)))
        for index, constraint in enumerate(constraints):
            if isinstance(constraint, Mapping):
                for key, value in constraint.items():
                    rows.append((f"constraints.{index}.{key}", value))
    text = "".join(
        f"{_format_kv_key(key)}={_format_kv_value(value)}\n" for key, value in rows
    )
    return text


def _flatten_mapping(
    rows: list[tuple[str, object]],
    prefix: str,
    mapping: Mapping[str, object],
) -> None:
    for key, value in mapping.items():
        if type(key) is not str or not key.strip():
            raise ValueError("blackboard kv keys must be non-empty strings.")
        flat_key = f"{prefix}.{key}"
        if isinstance(value, Mapping):
            _flatten_mapping(rows, flat_key, value)
        elif isinstance(value, list):
            rows.append((flat_key, ",".join(str(item) for item in value)))
        else:
            rows.append((flat_key, value))


def _format_kv_key(key: str) -> str:
    normalized = key.strip()
    if not _KV_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(f"blackboard kv key contains unsafe characters: {key!r}.")
    return normalized


def _format_kv_value(value: object) -> str:
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    return str(value).replace("\n", " ").replace("\r", " ").strip()


def _read_json_mapping(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object.")
    reject_raw_policy_control_keys(payload)
    return payload


def _coerce_telemetry(
    telemetry: MicroMachineTelemetry | Mapping[str, object],
) -> MicroMachineTelemetry:
    if isinstance(telemetry, MicroMachineTelemetry):
        document = deepcopy(telemetry.to_dict())
        reject_raw_policy_control_keys(document)
        return MicroMachineTelemetry.from_mapping(document)
    if isinstance(telemetry, Mapping):
        document = deepcopy(dict(telemetry))
        reject_raw_policy_control_keys(document)
        return MicroMachineTelemetry.from_mapping(document)
    raise ValueError("telemetry must be a MicroMachineTelemetry or mapping.")


def _clone_telemetry(telemetry: MicroMachineTelemetry) -> MicroMachineTelemetry:
    return _coerce_telemetry(telemetry)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _write_latest_update_transactionally(
    json_path: Path,
    json_text: str,
    kv_path: Path,
    kv_text: str,
) -> None:
    old_json = _read_existing_text(json_path)
    old_kv = _read_existing_text(kv_path)
    try:
        _atomic_write_text(json_path, json_text)
        _atomic_write_text(kv_path, kv_text)
    except Exception:
        _restore_text_path(json_path, old_json)
        _restore_text_path(kv_path, old_kv)
        raise


def _read_existing_text(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return (False, "")
    return (True, path.read_text())


def _restore_text_path(path: Path, snapshot: tuple[bool, str]) -> None:
    existed, text = snapshot
    if existed:
        _atomic_write_text(path, text)
    elif path.exists():
        path.unlink()


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _new_update_id() -> str:
    return f"voi-mm-{uuid.uuid4().hex}"


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer.")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return value


def _require_text(field_name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()
