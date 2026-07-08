"""Provider boundary for compiling intent into policy modulation vectors.

This module is deliberately deterministic and stdlib-only. LLMs, UI controls,
replay imitators, and future neural representation models may produce bounded
semantic mappings, but this compiler is the only path into
``PolicyModulationVector``. Raw runtime control is rejected before any vector is
constructed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from starcraft_commander.policy_modulation import (
    POLICY_MODULATION_RAW_CONTROL_KEYS,
    PolicyModulationSource,
    PolicyModulationVector,
    PolicyOverrideLevel,
    reject_raw_policy_control_keys,
)


class PolicyModulationCompileStatus(str, Enum):
    """Outcome of compiling provider output into the modulation DSL."""

    COMPILED = "compiled"
    CLARIFICATION_REQUIRED = "clarification_required"
    REFUSED = "refused"


POLICY_MODULATION_PROVIDER_SOURCES: frozenset[PolicyModulationSource] = frozenset(
    {
        PolicyModulationSource.HUMAN,
        PolicyModulationSource.LLM,
        PolicyModulationSource.SMOKE_KEYWORD,
        PolicyModulationSource.UI,
        PolicyModulationSource.REPLAY_IMITATION,
        PolicyModulationSource.NEURAL_REPRESENTATION,
    }
)
"""Supported external provider roles for issue #10 modulation."""


class PolicyModulationProviderInterface(Protocol):
    """Provider seam for LLM, UI, replay, or neural modulation adapters."""

    source: PolicyModulationSource

    def propose_policy_modulation(
        self,
        request: "PolicyModulationProviderRequest",
    ) -> Mapping[str, object]:
        """Return a bounded semantic mapping, never raw SC2 runtime actions."""


@dataclass(frozen=True)
class PolicyModulationProviderRequest:
    """Context passed to a modulation provider for one user/model decision."""

    command_text: str
    source: PolicyModulationSource | str = PolicyModulationSource.LLM
    game_state: Mapping[str, object] = field(default_factory=dict)
    commander_context: Mapping[str, object] = field(default_factory=dict)
    allowed_override_levels: tuple[PolicyOverrideLevel | str, ...] = (
        PolicyOverrideLevel.BIAS,
        PolicyOverrideLevel.CONSTRAINT,
        PolicyOverrideLevel.DIRECTIVE,
        PolicyOverrideLevel.EMERGENCY,
    )
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_text",
            _require_text("command_text", self.command_text),
        )
        object.__setattr__(self, "source", _coerce_source(self.source))
        if not isinstance(self.game_state, Mapping):
            raise ValueError("game_state must be a mapping.")
        if not isinstance(self.commander_context, Mapping):
            raise ValueError("commander_context must be a mapping.")
        reject_raw_policy_control_keys(dict(self.game_state), path="game_state")
        reject_raw_policy_control_keys(
            dict(self.commander_context),
            path="commander_context",
        )
        object.__setattr__(self, "game_state", dict(self.game_state))
        object.__setattr__(self, "commander_context", dict(self.commander_context))
        object.__setattr__(
            self,
            "allowed_override_levels",
            tuple(
                _coerce_override_level(level)
                for level in self.allowed_override_levels
            ),
        )
        object.__setattr__(self, "tags", _string_tuple("tags", self.tags))

    def to_dict(self) -> dict[str, object]:
        return {
            "command_text": self.command_text,
            "source": self.source.value,
            "game_state": dict(self.game_state),
            "commander_context": dict(self.commander_context),
            "allowed_override_levels": [
                level.value for level in self.allowed_override_levels
            ],
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class PolicyModulationCompileResult:
    """Non-throwing compiler result for provider output."""

    status: PolicyModulationCompileStatus | str
    source: PolicyModulationSource | str
    vector: PolicyModulationVector | None = None
    assistant_message: str = ""
    refusal_reason: str = ""
    clarification_prompt: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        status = _coerce_status(self.status)
        source = _coerce_source(self.source)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "warnings", _string_tuple("warnings", self.warnings))
        if status is PolicyModulationCompileStatus.COMPILED and self.vector is None:
            raise ValueError("compiled modulation results require a vector.")
        if status is not PolicyModulationCompileStatus.COMPILED and self.vector is not None:
            raise ValueError("non-compiled modulation results cannot carry a vector.")
        if self.refusal_reason:
            object.__setattr__(
                self,
                "refusal_reason",
                _require_text("refusal_reason", self.refusal_reason),
            )
        if self.assistant_message:
            object.__setattr__(
                self,
                "assistant_message",
                _require_text("assistant_message", self.assistant_message),
            )
        if self.clarification_prompt:
            object.__setattr__(
                self,
                "clarification_prompt",
                _require_text("clarification_prompt", self.clarification_prompt),
            )

    @property
    def ok(self) -> bool:
        return self.status is PolicyModulationCompileStatus.COMPILED

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "source": self.source.value,
            "vector": self.vector.to_dict() if self.vector else None,
            "assistant_message": self.assistant_message,
            "refusal_reason": self.refusal_reason,
            "clarification_prompt": self.clarification_prompt,
            "warnings": list(self.warnings),
        }


def compile_policy_modulation_provider_output(
    provider_output: object,
    *,
    default_source: PolicyModulationSource | str = PolicyModulationSource.LLM,
    default_goal: str | None = None,
) -> PolicyModulationCompileResult:
    """Compile bounded provider output into a policy modulation vector.

    The function never raises for malformed provider output. Validation errors
    become explicit refusal results so callers can surface the reason to users
    or logs without letting unsafe payloads reach a bot bridge.
    """

    try:
        source = _coerce_source(default_source)
        if not isinstance(provider_output, Mapping):
            return _refused(source, "provider output must be a mapping.")
        reject_raw_policy_control_keys(provider_output)
        source = _coerce_source(provider_output.get("source", source))
        control_mapping = _extract_terminal_control_mapping(provider_output)
        source = _coerce_source(control_mapping.get("source", source))
        provider_status = _extract_provider_status(control_mapping)
        assistant_message = _extract_assistant_message(control_mapping)
        clarification = _extract_clarification(control_mapping)
        if clarification or provider_status is PolicyModulationCompileStatus.CLARIFICATION_REQUIRED:
            return PolicyModulationCompileResult(
                status=PolicyModulationCompileStatus.CLARIFICATION_REQUIRED,
                source=source,
                assistant_message=assistant_message,
                clarification_prompt=(
                    clarification
                    or "의도를 정책 조정으로 변환하려면 더 구체적인 전략 목표가 필요합니다."
                ),
            )
        refusal = _extract_refusal(control_mapping)
        vector_payload = _extract_vector_payload(provider_output)
        if refusal or provider_status is PolicyModulationCompileStatus.REFUSED:
            return _refused(
                source,
                refusal or "provider refused policy modulation.",
                assistant_message=assistant_message,
            )
        normalized, warnings = _normalize_provider_mapping(
            vector_payload if vector_payload is not None else provider_output,
            default_source=source,
            default_goal=default_goal,
        )
        vector = PolicyModulationVector.from_mapping(normalized)
        return PolicyModulationCompileResult(
            status=PolicyModulationCompileStatus.COMPILED,
            source=vector.source,
            vector=vector,
            assistant_message=assistant_message,
            warnings=warnings,
        )
    except (TypeError, ValueError) as exc:
        fallback_source = _safe_source(default_source)
        return _refused(fallback_source, str(exc))


def compile_policy_modulation_from_provider(
    provider: PolicyModulationProviderInterface,
    request: PolicyModulationProviderRequest,
) -> PolicyModulationCompileResult:
    """Ask a provider for bounded output and compile it safely."""

    try:
        provider_output = provider.propose_policy_modulation(request)
    except Exception as exc:  # pragma: no cover - defensive provider boundary.
        return _refused(request.source, f"provider raised {type(exc).__name__}: {exc}")
    result = compile_policy_modulation_provider_output(
        provider_output,
        default_source=getattr(provider, "source", request.source),
        default_goal=request.command_text,
    )
    if (
        result.vector is not None
        and result.vector.override_level not in request.allowed_override_levels
    ):
        return _refused(
            result.source,
            "provider requested override level outside the allowed request set: "
            f"{result.vector.override_level.value}",
        )
    return result


_VECTOR_WRAPPER_KEYS = (
    "modulation",
    "policy_modulation",
    "policy_modulation_vector",
    "vector",
)

_WRAPPER_METADATA_KEYS = {
    "source",
    "override_level",
    "level",
    "override",
    "confidence",
    "ttl_seconds",
    "ttl",
    "ttl_s",
    "tags",
    "rationale",
    "assistant_message",
}

_CONTROL_KEYS = {
    "status",
    "needs_clarification",
    "clarification_prompt",
    "refusal_reason",
    "assistant_message",
}

_TOP_LEVEL_ALIASES = {
    "intent": "goal",
    "goal_text": "goal",
    "user_intent": "goal",
    "level": "override_level",
    "override": "override_level",
    "ttl": "ttl_seconds",
    "ttl_s": "ttl_seconds",
}

_DOMAIN_ALIASES = {
    "posture": ("strategy", "posture"),
    "doctrine": ("strategy", "doctrine"),
    "strategy_doctrine": ("strategy", "doctrine"),
    "preferred_builds": ("strategy", "preferred_builds"),
    "avoided_builds": ("strategy", "avoided_builds"),
    "timing_biases": ("strategy", "timing_biases"),
    "strategy_timing_biases": ("strategy", "timing_biases"),
    "transition_biases": ("strategy", "transition_biases"),
    "strategy_transition_biases": ("strategy", "transition_biases"),
    "strategic_tags": ("strategy", "strategic_tags"),
    "expand_bias": ("economy", "expand_bias"),
    "worker_production_bias": ("economy", "worker_production_bias"),
    "worker_bias": ("economy", "worker_production_bias"),
    "scv_production_bias": ("economy", "worker_production_bias"),
    "scv_training_bias": ("economy", "worker_production_bias"),
    "gas_priority": ("economy", "gas_priority"),
    "gas_worker_target_bias": ("economy", "gas_worker_target_bias"),
    "gas_worker_bias": ("economy", "gas_worker_target_bias"),
    "mineral_saturation_bias": ("economy", "mineral_saturation_bias"),
    "repair_priority": ("economy", "repair_priority"),
    "repair_worker_bias": ("economy", "repair_priority"),
    "supply_buffer_bias": ("economy", "supply_buffer_bias"),
    "expansion_safety_bias": ("economy", "expansion_safety_bias"),
    "mule_priority": ("economy", "mule_priority"),
    "repeat_order_guard_frames": ("workers", "repeat_order_guard_frames"),
    "worker_repeat_order_guard_frames": ("workers", "repeat_order_guard_frames"),
    "scout_worker_bias": ("scouting", "scout_priority"),
    "worker_scout_bias": ("scouting", "scout_priority"),
    "structure_biases": ("tech", "structure_biases"),
    "unit_biases": ("tech", "unit_biases"),
    "upgrade_biases": ("tech", "upgrade_biases"),
    "tech_path_tags": ("tech", "tech_path_tags"),
    "queue_biases": ("production", "queue_biases"),
    "composition_biases": ("production", "composition_biases"),
    "addon_biases": ("production", "addon_biases"),
    "production_facility_biases": ("production", "production_facility_biases"),
    "max_tech_deviation": ("production", "max_tech_deviation"),
    "production_continuity_bias": ("production", "production_continuity_bias"),
    "tech_switch_urgency": ("production", "tech_switch_urgency"),
    "allow_build_order_rewrite": ("production", "allow_build_order_rewrite"),
    "aggression": ("combat", "aggression"),
    "combat_aggression": ("combat", "aggression"),
    "engage_threshold_delta": ("combat", "engage_threshold_delta"),
    "retreat_threshold_delta": ("combat", "retreat_threshold_delta"),
    "attack_timing_bias": ("combat", "attack_timing_bias"),
    "commitment_level": ("combat", "commitment_level"),
    "pressure_window_frames": ("combat", "pressure_window_frames"),
    "attack_condition_override": ("combat", "attack_condition_override"),
    "retreat_patience_bias": ("combat", "retreat_patience_bias"),
    "rally_before_attack_bias": ("combat", "rally_before_attack_bias"),
    "combat_harassment_bias": ("combat", "harassment_bias"),
    "defend_bias": ("combat", "defend_bias"),
    "preserve_army_bias": ("combat", "preserve_army_bias"),
    "combat_sim_confidence_margin": ("combat", "combat_sim_confidence_margin"),
    "siege_position_bias": ("combat", "siege_position_bias"),
    "kite_bias": ("combat", "kite_bias"),
    "flank_bias": ("combat", "flank_bias"),
    "target_priority_biases": ("combat", "target_priority_biases"),
    "scout_priority": ("scouting", "scout_priority"),
    "risk_tolerance": ("scouting", "risk_tolerance"),
    "scout_cadence_bias": ("scouting", "scout_cadence_bias"),
    "scan_priority": ("scouting", "scan_priority"),
    "hidden_tech_scout_bias": ("scouting", "hidden_tech_scout_bias"),
    "target_biases": ("scouting", "target_biases"),
    "require_fresh_enemy_observation": (
        "scouting",
        "require_fresh_enemy_observation",
    ),
    "main_army_bias": ("squad", "main_army_bias"),
    "squad_harassment_bias": ("squad", "harassment_bias"),
    "defense_bias": ("squad", "defense_bias"),
    "regroup_bias": ("squad", "regroup_bias"),
    "drop_bias": ("squad", "drop_bias"),
    "split_army_bias": ("squad", "split_army_bias"),
    "squad_flank_bias": ("squad", "flank_bias"),
    "reinforce_bias": ("squad", "reinforce_bias"),
    "contain_bias": ("squad", "contain_bias"),
    "proxy_pressure_bias": ("squad", "proxy_pressure_bias"),
    "squad_role_biases": ("squad", "squad_role_biases"),
    "army_group": ("scope", "army_group"),
    "unit_classes": ("scope", "unit_classes"),
    "location_intent": ("scope", "location_intent"),
    "duration_seconds": ("scope", "duration_seconds"),
    "scope_duration_seconds": ("scope", "duration_seconds"),
    "min_units": ("scope", "min_units"),
    "max_units": ("scope", "max_units"),
    "require_safety_margin": ("scope", "require_safety_margin"),
    "allow_partial_scope": ("scope", "allow_partial_scope"),
    "lifetime_mode": ("lifetime", "mode"),
    "completion_conditions": ("lifetime", "completion_conditions"),
    "completion_state": ("lifetime", "completion_state"),
    "lifetime_reason": ("lifetime", "reason"),
    "tactical_task_type": ("tactical_task", "task_type"),
    "task_type": ("tactical_task", "task_type"),
    "task_id": ("tactical_task", "task_id"),
    "task_unit_classes": ("tactical_task", "unit_classes"),
    "task_production_targets": ("tactical_task", "production_targets"),
    "production_targets": ("tactical_task", "production_targets"),
    "task_location_intent": ("tactical_task", "location_intent"),
    "task_priority": ("tactical_task", "priority"),
    "task_min_units": ("tactical_task", "min_units"),
    "task_max_units": ("tactical_task", "max_units"),
    "task_duration_seconds": ("tactical_task", "duration_seconds"),
    "task_allow_partial": ("tactical_task", "allow_partial"),
    "task_safety_margin": ("tactical_task", "safety_margin"),
    "production_targets_plan": ("production_plan", "targets"),
    "production_plan_targets": ("production_plan", "targets"),
    "allow_prerequisite_buildings": (
        "production_plan",
        "allow_prerequisite_buildings",
    ),
    "production_plan_priority": ("production_plan", "priority"),
    "route_type": ("route_intent", "route_type"),
    "avoid_enemy_strength": ("route_intent", "avoid_enemy_strength"),
    "target_type": ("target_intent", "target_type"),
    "target_intent_priority": ("target_intent", "priority"),
    "cancel_attacks": ("emergency", "cancel_attacks"),
    "pull_workers_for_defense": ("emergency", "pull_workers_for_defense"),
    "pull_workers_for_defense_bias": ("emergency", "pull_workers_for_defense"),
    "evacuate_workers": ("emergency", "evacuate_workers"),
    "force_retreat": ("emergency", "force_retreat"),
    "hold_position": ("emergency", "hold_position"),
    "prioritize_repair": ("emergency", "prioritize_repair"),
    "stop_expansion": ("emergency", "stop_expansion"),
}

_DOMAIN_FIELD_ALIASES = {
    ("workers", "worker_production_bias"): ("economy", "worker_production_bias"),
    ("workers", "worker_bias"): ("economy", "worker_production_bias"),
    ("workers", "scv_production_bias"): ("economy", "worker_production_bias"),
    ("workers", "scv_training_bias"): ("economy", "worker_production_bias"),
    ("workers", "gas_worker_target_bias"): ("economy", "gas_worker_target_bias"),
    ("workers", "gas_worker_bias"): ("economy", "gas_worker_target_bias"),
    ("workers", "mineral_saturation_bias"): ("economy", "mineral_saturation_bias"),
    ("workers", "repair_worker_bias"): ("economy", "repair_priority"),
    ("workers", "repair_priority"): ("economy", "repair_priority"),
    ("workers", "supply_buffer_bias"): ("economy", "supply_buffer_bias"),
    ("workers", "scout_worker_bias"): ("scouting", "scout_priority"),
    ("workers", "worker_scout_bias"): ("scouting", "scout_priority"),
    ("workers", "pull_workers_for_defense_bias"): (
        "emergency",
        "pull_workers_for_defense",
    ),
    ("tactical_task", "type"): ("tactical_task", "task_type"),
    ("tactical_task", "id"): ("tactical_task", "task_id"),
    ("tactical_task", "units"): ("tactical_task", "unit_classes"),
    ("tactical_task", "unit_types"): ("tactical_task", "unit_classes"),
    ("tactical_task", "targets"): ("tactical_task", "production_targets"),
    ("tactical_task", "build_targets"): ("tactical_task", "production_targets"),
    ("tactical_task", "production_items"): ("tactical_task", "production_targets"),
    ("tactical_task", "location"): ("tactical_task", "location_intent"),
    ("tactical_task", "min_count"): ("tactical_task", "min_units"),
    ("tactical_task", "max_count"): ("tactical_task", "max_units"),
    ("tactical_task", "duration"): ("tactical_task", "duration_seconds"),
    ("tactical_task", "allow_partial_scope"): ("tactical_task", "allow_partial"),
    ("tactical_task", "require_safety_margin"): ("tactical_task", "safety_margin"),
    ("lifetime", "conditions"): ("lifetime", "completion_conditions"),
    ("lifetime", "state"): ("lifetime", "completion_state"),
    ("production_plan", "items"): ("production_plan", "targets"),
    ("production_plan", "units"): ("production_plan", "targets"),
    ("production_plan", "buildings"): ("production_plan", "targets"),
    ("production_plan", "allow_prerequisites"): (
        "production_plan",
        "allow_prerequisite_buildings",
    ),
    ("route_intent", "type"): ("route_intent", "route_type"),
    ("target_intent", "type"): ("target_intent", "target_type"),
}
"""LLM-friendly nested aliases routed to the canonical manager domains."""

_DOMAIN_KEYS = {
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
}

_POSTURE_ALIASES = {
    "aggressive": "pressure",
    "attack": "pressure",
    "attacking": "pressure",
    "offensive": "pressure",
    "pressure": "pressure",
    "pressuring": "pressure",
    "harass": "pressure",
    "harassment": "pressure",
    "contain": "pressure",
    "macro": "economic",
    "greedy": "economic",
    "economy": "economic",
    "economic": "economic",
    "defense": "defensive",
    "defensive": "defensive",
    "hold": "defensive",
    "turtle": "defensive",
    "safe": "defensive",
    "balanced": "balanced",
    "normal": "balanced",
    "allin": "all_in",
    "all_in": "all_in",
    "all-in": "all_in",
    "rush": "all_in",
}

_ATTACK_CONDITION_OVERRIDE_ALIASES = {
    "default": "normal",
    "normal": "normal",
    "none": "normal",
    "safe": "earlier_if_safe",
    "earlier": "earlier_if_safe",
    "early": "earlier_if_safe",
    "earlier_if_safe": "earlier_if_safe",
    "opportunistic": "earlier_if_safe",
    "pressure_when_safe": "earlier_if_safe",
    "attack_when_safe": "earlier_if_safe",
    "force": "force_when_threshold_met",
    "forced": "force_when_threshold_met",
    "force_when_ready": "force_when_threshold_met",
    "force_when_threshold_met": "force_when_threshold_met",
    "attack_when_ready": "force_when_threshold_met",
    "threshold": "force_when_threshold_met",
    "never": "never",
    "no_attack": "never",
    "hold": "never",
    "hold_fire": "never",
}

_TACTICAL_TASK_TYPE_ALIASES = {
    "scout": "scout_with_units",
    "scouting": "scout_with_units",
    "unit_scout": "scout_with_units",
    "unit_scouting": "scout_with_units",
    "scout_with_units": "scout_with_units",
    "marine_scout": "scout_with_units",
    "pressure": "pressure_with_main_army",
    "attack": "pressure_with_main_army",
    "main_attack": "pressure_with_main_army",
    "main_army_pressure": "pressure_with_main_army",
    "pressure_with_main_army": "pressure_with_main_army",
    "sustain": "sustain_production",
    "sustain_production": "sustain_production",
    "continuous_production": "sustain_production",
    "keep_producing": "sustain_production",
    "supply_buffer": "sustain_production",
    "tech": "tech_transition",
    "tech_transition": "tech_transition",
    "transition": "tech_transition",
    "tank_transition": "tech_transition",
    "mech_transition": "tech_transition",
    "expand": "expand_or_land_command_center",
    "land": "expand_or_land_command_center",
    "land_command_center": "expand_or_land_command_center",
    "expand_or_land_command_center": "expand_or_land_command_center",
    "command_center_landing": "expand_or_land_command_center",
}

_LOCATION_INTENT_ALIASES = {
    "enemy_base": "enemy_main",
    "enemy_start": "enemy_main",
    "enemy_main": "enemy_main",
    "enemy_natural": "enemy_natural",
    "enemy_third": "enemy_third",
    "third": "third",
    "watch_tower": "watchtower",
    "watchtower": "watchtower",
    "safe_expand": "safe_expansion",
    "safe_expansion": "safe_expansion",
    "new_base": "safe_expansion",
    "expansion": "safe_expansion",
}

_BUILDING_PLACEMENT_INTENT_ALIASES = {
    "home": "self_main_safe_macro",
    "main": "self_main_safe_macro",
    "self_main": "self_main_safe_macro",
    "self_main_safe_macro": "self_main_safe_macro",
    "본진": "self_main_safe_macro",
    "본진안쪽": "self_main_safe_macro",
    "ramp": "self_main_ramp",
    "front_door": "self_main_ramp",
    "wall": "self_main_ramp",
    "self_main_ramp": "self_main_ramp",
    "입구": "self_main_ramp",
    "앞마당입구": "self_natural_choke",
    "natural_choke": "self_natural_choke",
    "self_natural_choke": "self_natural_choke",
    "natural": "self_natural_safe",
    "safe_expansion": "self_natural_safe",
    "self_natural_safe": "self_natural_safe",
    "앞마당": "self_natural_safe",
    "proxy": "proxy_near_enemy_natural",
    "proxy_near_enemy_natural": "proxy_near_enemy_natural",
    "전진": "proxy_near_enemy_natural",
    "적진근처": "proxy_near_enemy_natural",
    "explicit": "explicit_coordinate",
    "explicit_coordinate": "explicit_coordinate",
    "here": "explicit_coordinate",
    "여기": "explicit_coordinate",
    "near_factory": "near_factory",
    "near_barracks": "near_barracks",
    "near_starport": "near_starport",
}

_BUILDING_PLACEMENT_ANCHOR_ALIASES = {
    "main": "self_main",
    "home": "self_main",
    "self_main": "self_main",
    "본진": "self_main",
    "ramp": "self_ramp",
    "self_ramp": "self_ramp",
    "입구": "self_ramp",
    "natural": "self_natural",
    "self_natural": "self_natural",
    "앞마당": "self_natural",
    "enemy_natural": "enemy_natural",
    "적앞마당": "enemy_natural",
    "enemy_main": "enemy_main",
    "적본진": "enemy_main",
    "explicit": "explicit_coordinate",
    "explicit_coordinate": "explicit_coordinate",
    "here": "explicit_coordinate",
    "여기": "explicit_coordinate",
}

_BUILDING_PLACEMENT_DIRECTION_ALIASES = {
    "inside": "inside",
    "안쪽": "inside",
    "toward_enemy": "toward_enemy",
    "전방": "toward_enemy",
    "away_from_enemy": "away_from_enemy",
    "후방": "away_from_enemy",
    "left": "left",
    "왼쪽": "left",
    "right": "right",
    "오른쪽": "right",
    "center": "center",
    "중앙": "center",
}

_TACTICAL_SCOPE_LOCATION_INTENTS = {
    "home",
    "natural",
    "enemy_main",
    "enemy_natural",
    "enemy_third",
    "third",
    "watchtower",
    "ramp",
    "last_seen_enemy_army",
}

_VECTOR_KEYS = {
    "goal",
    "source",
    "override_level",
    "confidence",
    "ttl_seconds",
    "constraints",
    "tags",
    "rationale",
    "assistant_message",
    "composition_requirements",
    "unit_roles",
    "building_tasks",
    *_DOMAIN_KEYS,
}

_REPRESENTATION_KEYS = {
    "representation",
    "representation_axes",
    "latent_axes",
    "latent_vector",
}

_CANONICAL_MICROMACHINE_KEY_ALIASES = {
    "scv": "TERRAN_SCV",
    "worker": "TERRAN_SCV",
    "workers": "TERRAN_SCV",
    "일꾼": "TERRAN_SCV",
    "건설로봇": "TERRAN_SCV",
    "supplydepot": "TERRAN_SUPPLYDEPOT",
    "depot": "TERRAN_SUPPLYDEPOT",
    "보급고": "TERRAN_SUPPLYDEPOT",
    "commandcenter": "TERRAN_COMMANDCENTER",
    "cc": "TERRAN_COMMANDCENTER",
    "사령부": "TERRAN_COMMANDCENTER",
    "refinery": "TERRAN_REFINERY",
    "refinerygas": "TERRAN_REFINERY",
    "gas": "TERRAN_REFINERY",
    "vespene": "TERRAN_REFINERY",
    "가스": "TERRAN_REFINERY",
    "베스핀가스": "TERRAN_REFINERY",
    "barracks": "TERRAN_BARRACKS",
    "rax": "TERRAN_BARRACKS",
    "병영": "TERRAN_BARRACKS",
    "배럭": "TERRAN_BARRACKS",
    "factory": "TERRAN_FACTORY",
    "군수공장": "TERRAN_FACTORY",
    "starport": "TERRAN_STARPORT",
    "우주공항": "TERRAN_STARPORT",
    "engineeringbay": "TERRAN_ENGINEERINGBAY",
    "ebay": "TERRAN_ENGINEERINGBAY",
    "공학연구소": "TERRAN_ENGINEERINGBAY",
    "armory": "TERRAN_ARMORY",
    "무기고": "TERRAN_ARMORY",
    "bunker": "TERRAN_BUNKER",
    "벙커": "TERRAN_BUNKER",
    "marine": "TERRAN_MARINE",
    "marines": "TERRAN_MARINE",
    "해병": "TERRAN_MARINE",
    "마린": "TERRAN_MARINE",
    "marauder": "TERRAN_MARAUDER",
    "marauders": "TERRAN_MARAUDER",
    "불곰": "TERRAN_MARAUDER",
    "reaper": "TERRAN_REAPER",
    "reapers": "TERRAN_REAPER",
    "사신": "TERRAN_REAPER",
    "ghost": "TERRAN_GHOST",
    "ghosts": "TERRAN_GHOST",
    "유령": "TERRAN_GHOST",
    "hellion": "TERRAN_HELLION",
    "hellions": "TERRAN_HELLION",
    "화염차": "TERRAN_HELLION",
    "cyclone": "TERRAN_CYCLONE",
    "cyclones": "TERRAN_CYCLONE",
    "사이클론": "TERRAN_CYCLONE",
    "thor": "TERRAN_THOR",
    "thors": "TERRAN_THOR",
    "토르": "TERRAN_THOR",
    "siegetank": "TERRAN_SIEGETANK",
    "tank": "TERRAN_SIEGETANK",
    "tanks": "TERRAN_SIEGETANK",
    "탱크": "TERRAN_SIEGETANK",
    "공성전차": "TERRAN_SIEGETANK",
    "medivac": "TERRAN_MEDIVAC",
    "medivacs": "TERRAN_MEDIVAC",
    "의료선": "TERRAN_MEDIVAC",
    "viking": "TERRAN_VIKINGFIGHTER",
    "vikings": "TERRAN_VIKINGFIGHTER",
    "바이킹": "TERRAN_VIKINGFIGHTER",
    "banshee": "TERRAN_BANSHEE",
    "banshees": "TERRAN_BANSHEE",
    "밴시": "TERRAN_BANSHEE",
    "raven": "TERRAN_RAVEN",
    "ravens": "TERRAN_RAVEN",
    "밤까마귀": "TERRAN_RAVEN",
    "battlecruiser": "TERRAN_BATTLECRUISER",
    "battlecruisers": "TERRAN_BATTLECRUISER",
    "bc": "TERRAN_BATTLECRUISER",
    "배틀크루저": "TERRAN_BATTLECRUISER",
    "전투순양함": "TERRAN_BATTLECRUISER",
    "fusioncore": "TERRAN_FUSIONCORE",
    "fusion": "TERRAN_FUSIONCORE",
    "융합로": "TERRAN_FUSIONCORE",
    "barrackstechlab": "BARRACKS_TECHLAB",
    "raxtechlab": "BARRACKS_TECHLAB",
    "병영기술실": "BARRACKS_TECHLAB",
    "배럭기술실": "BARRACKS_TECHLAB",
    "barracksreactor": "BARRACKS_REACTOR",
    "raxreactor": "BARRACKS_REACTOR",
    "병영반응로": "BARRACKS_REACTOR",
    "배럭반응로": "BARRACKS_REACTOR",
    "factorytechlab": "FACTORY_TECHLAB",
    "군수공장기술실": "FACTORY_TECHLAB",
    "factoryreactor": "FACTORY_REACTOR",
    "군수공장반응로": "FACTORY_REACTOR",
    "starporttechlab": "STARPORT_TECHLAB",
    "우주공항기술실": "STARPORT_TECHLAB",
    "starportreactor": "STARPORT_REACTOR",
    "우주공항반응로": "STARPORT_REACTOR",
    "techlab": "BARRACKS_TECHLAB",
    "기술실": "BARRACKS_TECHLAB",
    "reactor": "BARRACKS_REACTOR",
    "반응로": "BARRACKS_REACTOR",
    "stimpack": "STIMPACK",
    "stim": "STIMPACK",
    "전투자극제": "STIMPACK",
    "combatshield": "COMBATSHIELD",
    "방패업": "COMBATSHIELD",
}

_CANONICAL_BIAS_FIELDS = {
    ("tech", "structure_biases"),
    ("tech", "unit_biases"),
    ("production", "queue_biases"),
    ("production", "addon_biases"),
    ("production", "production_facility_biases"),
}

_PRODUCTION_PLAN_PREREQUISITE_CHAINS: dict[str, tuple[str, ...]] = {
    "TERRAN_MARINE": ("TERRAN_BARRACKS", "TERRAN_MARINE"),
    "TERRAN_MARAUDER": (
        "TERRAN_BARRACKS",
        "BARRACKS_TECHLAB",
        "TERRAN_MARAUDER",
    ),
    "TERRAN_REAPER": ("TERRAN_BARRACKS", "TERRAN_REAPER"),
    "TERRAN_HELLION": ("TERRAN_FACTORY", "TERRAN_HELLION"),
    "TERRAN_CYCLONE": ("TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_CYCLONE"),
    "TERRAN_THOR": (
        "TERRAN_FACTORY",
        "FACTORY_TECHLAB",
        "TERRAN_ARMORY",
        "TERRAN_THOR",
    ),
    "TERRAN_SIEGETANK": (
        "TERRAN_FACTORY",
        "FACTORY_TECHLAB",
        "TERRAN_SIEGETANK",
    ),
    "TERRAN_MEDIVAC": ("TERRAN_STARPORT", "TERRAN_MEDIVAC"),
    "TERRAN_VIKINGFIGHTER": ("TERRAN_STARPORT", "TERRAN_VIKINGFIGHTER"),
    "TERRAN_BANSHEE": (
        "TERRAN_STARPORT",
        "STARPORT_TECHLAB",
        "TERRAN_BANSHEE",
    ),
    "TERRAN_RAVEN": ("TERRAN_STARPORT", "STARPORT_TECHLAB", "TERRAN_RAVEN"),
    "TERRAN_BATTLECRUISER": (
        "TERRAN_STARPORT",
        "STARPORT_TECHLAB",
        "TERRAN_FUSIONCORE",
        "TERRAN_BATTLECRUISER",
    ),
}

_PRODUCTION_PLAN_UNIT_TARGETS = frozenset(
    {
        "TERRAN_MARINE",
        "TERRAN_MARAUDER",
        "TERRAN_REAPER",
        "TERRAN_HELLION",
        "TERRAN_CYCLONE",
        "TERRAN_THOR",
        "TERRAN_SIEGETANK",
        "TERRAN_MEDIVAC",
        "TERRAN_VIKINGFIGHTER",
        "TERRAN_BANSHEE",
        "TERRAN_RAVEN",
        "TERRAN_BATTLECRUISER",
    }
)

_PRODUCTION_PLAN_STRUCTURE_TARGETS = frozenset(
    {
        "TERRAN_SUPPLYDEPOT",
        "TERRAN_COMMANDCENTER",
        "TERRAN_REFINERY",
        "TERRAN_BARRACKS",
        "TERRAN_FACTORY",
        "TERRAN_STARPORT",
        "TERRAN_ENGINEERINGBAY",
        "TERRAN_ARMORY",
        "TERRAN_FUSIONCORE",
        "TERRAN_BUNKER",
    }
)

_PRODUCTION_PLAN_ADDON_TARGETS = frozenset(
    {
        "BARRACKS_TECHLAB",
        "BARRACKS_REACTOR",
        "FACTORY_TECHLAB",
        "FACTORY_REACTOR",
        "STARPORT_TECHLAB",
        "STARPORT_REACTOR",
    }
)


def _extract_vector_payload(mapping: Mapping[str, object]) -> Mapping[str, object] | None:
    for key in _VECTOR_WRAPPER_KEYS:
        if key not in mapping:
            continue
        value = mapping[key]
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be a mapping.")
        metadata = {
            canonical: mapping[raw]
            for raw in _WRAPPER_METADATA_KEYS
            if raw in mapping
            for canonical in (_TOP_LEVEL_ALIASES.get(raw, raw),)
        }
        return {**metadata, **value}
    return None


def _extract_terminal_control_mapping(mapping: Mapping[str, object]) -> Mapping[str, object]:
    for key in _VECTOR_WRAPPER_KEYS:
        value = mapping.get(key)
        if not isinstance(value, Mapping):
            continue
        status = str(value.get("status", "") or "").strip().lower()
        has_terminal_signal = (
            status
            in {
                PolicyModulationCompileStatus.CLARIFICATION_REQUIRED.value,
                PolicyModulationCompileStatus.REFUSED.value,
            }
            or bool(value.get("clarification_prompt"))
            or bool(value.get("needs_clarification"))
            or bool(value.get("refusal_reason"))
        )
        if has_terminal_signal:
            metadata = {
                canonical: mapping[raw]
                for raw in _WRAPPER_METADATA_KEYS
                if raw in mapping
                for canonical in (_TOP_LEVEL_ALIASES.get(raw, raw),)
            }
            return {**metadata, **value}
    if any(key in mapping for key in _CONTROL_KEYS):
        return mapping
    return mapping


def _normalize_provider_mapping(
    mapping: Mapping[str, object],
    *,
    default_source: PolicyModulationSource,
    default_goal: str | None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    reject_raw_policy_control_keys(mapping)
    result: dict[str, object] = {}
    warnings: list[str] = []
    for key, value in mapping.items():
        canonical_key = _TOP_LEVEL_ALIASES.get(key, key)
        if canonical_key in _CONTROL_KEYS:
            continue
        if canonical_key in _REPRESENTATION_KEYS:
            _apply_representation_axes(result, value)
            continue
        if canonical_key in _DOMAIN_ALIASES:
            domain, field_name = _DOMAIN_ALIASES[canonical_key]
            if (domain, field_name) == ("emergency", "pull_workers_for_defense"):
                value = _coerce_bias_to_bool(
                    value,
                    field_name=canonical_key,
                )
            _ensure_domain(result, domain)[field_name] = value
            continue
        if canonical_key in _DOMAIN_KEYS:
            if not isinstance(value, Mapping):
                result[canonical_key] = value
                continue
            for field_name, field_value in value.items():
                if type(field_name) is not str or not field_name.strip():
                    raise ValueError(f"{canonical_key} field names must be strings.")
                target_domain, target_field, target_value = _normalize_domain_field(
                    canonical_key,
                    field_name,
                    field_value,
                )
                _ensure_domain(result, target_domain)[target_field] = target_value
                if target_domain != canonical_key or target_field != field_name:
                    warnings.append(
                        "mapped provider field: "
                        f"{canonical_key}.{field_name}->{target_domain}.{target_field}"
                    )
            continue
        if canonical_key in _VECTOR_KEYS:
            result[canonical_key] = value
            continue
        warnings.append(f"ignored provider field: {key}")

    result.setdefault("source", default_source.value)
    if "goal" not in result and default_goal:
        result["goal"] = default_goal
    _canonicalize_micromachine_payload(result)
    return result, tuple(warnings)


def _normalize_domain_field(
    domain: str,
    field_name: str,
    field_value: object,
) -> tuple[str, str, object]:
    canonical_field = field_name.strip()
    alias = _DOMAIN_FIELD_ALIASES.get((domain, canonical_field))
    if alias is None:
        return domain, canonical_field, field_value
    target_domain, target_field = alias
    if (target_domain, target_field) == ("emergency", "pull_workers_for_defense"):
        return target_domain, target_field, _coerce_bias_to_bool(
            field_value,
            field_name=f"{domain}.{canonical_field}",
        )
    return target_domain, target_field, field_value


def _coerce_bias_to_bool(value: object, *, field_name: str) -> bool:
    if type(value) is bool:
        return value
    if type(value) in (int, float):
        return float(value) > 0.0
    raise ValueError(f"{field_name} must be a bool or numeric bias.")


def _apply_representation_axes(result: dict[str, object], value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("representation axes must be a mapping.")
    reject_raw_policy_control_keys(value, path="representation")
    for axis, axis_value in value.items():
        if type(axis) is not str or not axis.strip():
            raise ValueError("representation axis names must be non-empty strings.")
        parts = tuple(part.strip() for part in axis.split(".") if part.strip())
        if len(parts) < 2 or parts[0] not in _DOMAIN_KEYS:
            raise ValueError(f"unsupported representation axis: {axis}.")
        if any(part.lower() in POLICY_MODULATION_RAW_CONTROL_KEYS for part in parts):
            raise ValueError(f"raw runtime control is not a representation axis: {axis}.")
        domain = _ensure_domain(result, parts[0])
        if len(parts) == 2:
            domain[parts[1]] = axis_value
            continue
        nested = domain.setdefault(parts[1], {})
        if not isinstance(nested, dict):
            raise ValueError(f"representation axis conflicts with scalar field: {axis}.")
        nested[".".join(parts[2:])] = axis_value


def _canonicalize_micromachine_payload(payload: dict[str, object]) -> None:
    """Normalize LLM-friendly unit/building names to C++ blackboard keys."""

    for domain_name, domain_value in list(payload.items()):
        if domain_name not in _DOMAIN_KEYS or not isinstance(domain_value, dict):
            continue
        if domain_name == "strategy" and "posture" in domain_value:
            domain_value["posture"] = _canonicalize_enum_alias(
                domain_value["posture"],
                aliases=_POSTURE_ALIASES,
            )
        if domain_name == "combat" and "attack_condition_override" in domain_value:
            domain_value["attack_condition_override"] = _canonicalize_enum_alias(
                domain_value["attack_condition_override"],
                aliases=_ATTACK_CONDITION_OVERRIDE_ALIASES,
            )
        if domain_name == "tactical_task" and "task_type" in domain_value:
            domain_value["task_type"] = _canonicalize_enum_alias(
                domain_value["task_type"],
                aliases=_TACTICAL_TASK_TYPE_ALIASES,
            )
        if domain_name in {"scope", "tactical_task"} and "location_intent" in domain_value:
            domain_value["location_intent"] = _canonicalize_enum_alias(
                domain_value["location_intent"],
                aliases=_LOCATION_INTENT_ALIASES,
            )
        for field_name, field_value in list(domain_value.items()):
            if (domain_name, field_name) in _CANONICAL_BIAS_FIELDS:
                domain_value[field_name] = _canonicalize_bias_mapping(field_value)
        if domain_name in {"scope", "tactical_task"}:
            classes = domain_value.get("unit_classes")
            if isinstance(classes, tuple):
                domain_value["unit_classes"] = tuple(
                    _canonicalize_micromachine_key(item) for item in classes
                )
            elif _is_non_text_sequence(classes):
                domain_value["unit_classes"] = [
                    _canonicalize_micromachine_key(item) for item in classes
                ]
        if domain_name == "tactical_task":
            targets = domain_value.get("production_targets")
            if isinstance(targets, tuple):
                domain_value["production_targets"] = tuple(
                    _canonicalize_micromachine_key(item) for item in targets
                )
            elif _is_non_text_sequence(targets):
                domain_value["production_targets"] = [
                    _canonicalize_micromachine_key(item) for item in targets
                ]
        if domain_name == "production_plan":
            targets = domain_value.get("targets")
            if isinstance(targets, tuple):
                domain_value["targets"] = tuple(
                    _canonicalize_micromachine_key(item) for item in targets
                )
            elif _is_non_text_sequence(targets):
                domain_value["targets"] = [
                    _canonicalize_micromachine_key(item) for item in targets
                ]
    _canonicalize_rich_intent_sequences(payload)
    _repair_micromachine_emergency_defaults(payload)
    _lower_micromachine_production_plan(payload)
    _lower_micromachine_building_tasks(payload)
    _repair_micromachine_tactical_task_defaults(payload)


def _canonicalize_rich_intent_sequences(payload: dict[str, object]) -> None:
    for key, field_name in (
        ("composition_requirements", "unit_type"),
        ("unit_roles", "unit_type"),
        ("building_tasks", "building_type"),
    ):
        values = payload.get(key)
        if not _is_non_text_sequence(values):
            continue
        normalized_items: list[object] = []
        for item in values:
            if not isinstance(item, Mapping):
                normalized_items.append(item)
                continue
            normalized = dict(item)
            if field_name in normalized:
                normalized[field_name] = _canonicalize_micromachine_key(
                    normalized[field_name]
                )
            if key == "building_tasks":
                if "placement_intent" in normalized:
                    normalized["placement_intent"] = _canonicalize_enum_alias(
                        normalized["placement_intent"],
                        aliases=_BUILDING_PLACEMENT_INTENT_ALIASES,
                    )
                if "anchor" in normalized:
                    normalized["anchor"] = _canonicalize_enum_alias(
                        normalized["anchor"],
                        aliases=_BUILDING_PLACEMENT_ANCHOR_ALIASES,
                    )
                if "offset_direction" in normalized:
                    normalized["offset_direction"] = _canonicalize_enum_alias(
                        normalized["offset_direction"],
                        aliases=_BUILDING_PLACEMENT_DIRECTION_ALIASES,
                    )
            normalized_items.append(normalized)
        payload[key] = normalized_items


def _repair_micromachine_emergency_defaults(payload: dict[str, object]) -> None:
    """Make emergency intent valid even when the provider omits emergency TTL."""

    emergency = payload.get("emergency")
    has_emergency_flags = isinstance(emergency, dict) and any(
        emergency.get(key) is True
        for key in (
            "cancel_attacks",
            "pull_workers_for_defense",
            "evacuate_workers",
            "force_retreat",
            "hold_position",
            "stop_expansion",
        )
    )
    override_level = str(payload.get("override_level", "") or "").strip().lower()
    if not has_emergency_flags and override_level != "emergency":
        return
    payload["override_level"] = "emergency"
    ttl_seconds = payload.get("ttl_seconds")
    if ttl_seconds is None:
        payload["ttl_seconds"] = 60
        return
    if isinstance(ttl_seconds, int) and not isinstance(ttl_seconds, bool):
        payload["ttl_seconds"] = min(ttl_seconds, 60)


def _lower_micromachine_production_plan(payload: dict[str, object]) -> None:
    """Translate rich production plans into fields consumed by MicroMachine C++."""

    production_plan = payload.get("production_plan")
    if not isinstance(production_plan, dict):
        return
    raw_targets = production_plan.get("targets")
    if not _is_non_text_sequence(raw_targets):
        return
    targets = tuple(_canonicalize_micromachine_key(target) for target in raw_targets)
    if not targets:
        return

    override_level = str(payload.get("override_level", "") or "").strip().lower()
    allow_prerequisites = bool(production_plan.get("allow_prerequisite_buildings"))
    if override_level == PolicyOverrideLevel.EMERGENCY.value and allow_prerequisites:
        allow_prerequisites = False
        production_plan["allow_prerequisite_buildings"] = False

    priority = _production_plan_priority(production_plan.get("priority"))
    production = _ensure_micromachine_domain_dict(payload, "production")
    tech = _ensure_micromachine_domain_dict(payload, "tech")
    tactical_task = _ensure_micromachine_domain_dict(payload, "tactical_task")

    queue_items: list[str] = []
    final_targets: list[str] = []
    for target in targets:
        final_targets.append(target)
        chain = _production_plan_chain_for_target(
            target,
            allow_prerequisites=allow_prerequisites,
        )
        for item in chain:
            if item not in queue_items:
                queue_items.append(item)

    for item in queue_items:
        _set_production_plan_bias(production, tech, item, priority)

    existing_targets = tactical_task.get("production_targets")
    merged_targets = _merge_ordered_tokens(
        existing_targets if _is_non_text_sequence(existing_targets) else (),
        queue_items,
    )
    if merged_targets:
        tactical_task["production_targets"] = list(merged_targets[:32])
    _set_if_empty(tactical_task, "task_type", "sustain_production")
    _set_float_at_least(tactical_task, "priority", priority)
    if allow_prerequisites:
        _set_float_at_least(production, "tech_switch_urgency", min(1.0, priority))
        if priority >= 0.75 and override_level in {
            PolicyOverrideLevel.DIRECTIVE.value,
            PolicyOverrideLevel.CONSTRAINT.value,
            PolicyOverrideLevel.BIAS.value,
            "",
        }:
            production["allow_build_order_rewrite"] = True

    lowered_tags = tuple(f"production_plan:{target}" for target in final_targets)
    payload["tags"] = list(_merge_ordered_tokens(payload.get("tags", ()), lowered_tags))
    evidence = (
        "production_plan lowered to consumed production/tech/tactical_task fields; "
        f"targets={','.join(final_targets)}; queued={','.join(queue_items)}"
    )
    payload["rationale"] = _append_rationale(payload.get("rationale"), evidence)


def _lower_micromachine_building_tasks(payload: dict[str, object]) -> None:
    """Translate semantic building placement tasks into consumed build biases."""

    building_tasks = payload.get("building_tasks")
    if not _is_non_text_sequence(building_tasks):
        return
    production = _ensure_micromachine_domain_dict(payload, "production")
    tech = _ensure_micromachine_domain_dict(payload, "tech")
    lowered_buildings: list[str] = []
    for item in building_tasks:
        if not isinstance(item, Mapping):
            continue
        building_type = item.get("building_type")
        if not isinstance(building_type, str) or not building_type.strip():
            continue
        token = _canonicalize_micromachine_key(building_type)
        if token not in _PRODUCTION_PLAN_STRUCTURE_TARGETS:
            continue
        lowered_buildings.append(token)
        _set_production_plan_bias(production, tech, token, 0.85)
    if not lowered_buildings:
        return
    payload["tags"] = list(
        _merge_ordered_tokens(
            payload.get("tags", ()),
            tuple(f"building_task:{token}" for token in lowered_buildings),
        )
    )
    evidence = (
        "building_tasks lowered to consumed production/tech fields; "
        f"buildings={','.join(lowered_buildings)}"
    )
    payload["rationale"] = _append_rationale(payload.get("rationale"), evidence)


def _production_plan_chain_for_target(
    target: str,
    *,
    allow_prerequisites: bool,
) -> tuple[str, ...]:
    if allow_prerequisites:
        return _PRODUCTION_PLAN_PREREQUISITE_CHAINS.get(target, (target,))
    return (target,)


def _production_plan_priority(value: object) -> float:
    if isinstance(value, (int, float)) and type(value) is not bool:
        return max(0.1, min(1.0, float(value)))
    return 0.8


def _set_production_plan_bias(
    production: dict[str, object],
    tech: dict[str, object],
    item: str,
    priority: float,
) -> None:
    _set_nested_float_at_least(production, ("queue_biases", item), priority)
    if item in _PRODUCTION_PLAN_ADDON_TARGETS:
        _set_nested_float_at_least(production, ("addon_biases", item), priority)
        return
    if item in _PRODUCTION_PLAN_STRUCTURE_TARGETS:
        _set_nested_float_at_least(
            production,
            ("production_facility_biases", item),
            priority,
        )
        _set_nested_float_at_least(tech, ("structure_biases", item), priority)
        return
    if item in _PRODUCTION_PLAN_UNIT_TARGETS:
        _set_nested_float_at_least(tech, ("unit_biases", item), priority)


def _merge_ordered_tokens(*sequences: object) -> tuple[str, ...]:
    result: list[str] = []
    for sequence in sequences:
        if isinstance(sequence, str):
            candidates = (sequence,)
        elif _is_non_text_sequence(sequence):
            candidates = sequence
        else:
            continue
        for item in candidates:
            if type(item) is not str:
                continue
            normalized = item.strip()
            if normalized and normalized not in result:
                result.append(normalized)
    return tuple(result)


def _append_rationale(existing: object, addition: str) -> str:
    if isinstance(existing, str) and existing.strip():
        return f"{existing.strip()} {addition}"
    return addition


def _repair_micromachine_tactical_task_defaults(payload: dict[str, object]) -> None:
    """Make concrete MicroMachine tactical tasks executable, not just descriptive."""

    tactical_task = payload.get("tactical_task")
    if not isinstance(tactical_task, dict):
        return
    task_type = tactical_task.get("task_type")
    if not isinstance(task_type, str) or not task_type.strip():
        return
    task_type = task_type.strip()

    if task_type == "scout_with_units":
        scope = _ensure_micromachine_domain_dict(payload, "scope")
        location = _first_non_empty_text(
            tactical_task.get("location_intent"),
            scope.get("location_intent"),
            "enemy_main",
        )
        tactical_task["location_intent"] = location
        _set_if_empty(scope, "location_intent", location)
        _set_if_empty(scope, "army_group", "scout")
        _copy_or_default_unit_classes(
            scope,
            tactical_task,
            default=("TERRAN_MARINE",),
        )
        _set_if_zero_or_empty(scope, "min_units", 1)
        _set_if_zero_or_empty(scope, "max_units", 2)
        _set_if_zero_or_empty(tactical_task, "min_units", 1)
        _set_if_zero_or_empty(tactical_task, "max_units", 2)
        _set_if_zero_or_empty(tactical_task, "duration_seconds", 180)
        _set_if_zero_or_empty(tactical_task, "priority", 0.85)
        scouting = _ensure_micromachine_domain_dict(payload, "scouting")
        squad = _ensure_micromachine_domain_dict(payload, "squad")
        _set_float_at_least(scouting, "scout_priority", 0.75)
        _set_nested_float_at_least(squad, ("squad_role_biases", "marine_scout"), 0.75)
        return

    if task_type == "pressure_with_main_army":
        scope = _ensure_micromachine_domain_dict(payload, "scope")
        location = _first_non_empty_text(
            tactical_task.get("location_intent"),
            scope.get("location_intent"),
            "enemy_natural",
        )
        tactical_task["location_intent"] = location
        _set_if_empty(scope, "location_intent", location)
        _set_if_empty(scope, "army_group", "main")
        _copy_or_default_unit_classes(
            scope,
            tactical_task,
            default=("TERRAN_MARINE", "TERRAN_MARAUDER", "TERRAN_MEDIVAC", "TERRAN_SIEGETANK"),
        )
        _set_if_zero_or_empty(scope, "min_units", 1)
        _set_if_zero_or_empty(tactical_task, "min_units", 1)
        _set_if_zero_or_empty(tactical_task, "duration_seconds", 300)
        _set_if_zero_or_empty(tactical_task, "priority", 0.9)
        combat = _ensure_micromachine_domain_dict(payload, "combat")
        squad = _ensure_micromachine_domain_dict(payload, "squad")
        attack_override = str(combat.get("attack_condition_override", "") or "").strip()
        if not attack_override or attack_override == "normal":
            combat["attack_condition_override"] = "force_when_threshold_met"
        _set_float_at_least(combat, "aggression", 0.65)
        _set_float_at_least(combat, "attack_timing_bias", 0.65)
        _set_float_at_least(combat, "commitment_level", 0.55)
        _set_float_at_least(combat, "retreat_patience_bias", 0.35)
        _set_float_at_least(squad, "main_army_bias", 0.6)
        _set_float_at_least(squad, "reinforce_bias", 0.25)
        if location == "enemy_natural":
            _set_float_at_least(squad, "contain_bias", 0.35)
        return

    if task_type == "expand_or_land_command_center":
        _set_if_empty(tactical_task, "location_intent", "safe_expansion")
        _set_if_zero_or_empty(tactical_task, "priority", 0.75)


def _ensure_micromachine_domain_dict(
    payload: dict[str, object],
    domain_name: str,
) -> dict[str, object]:
    value = payload.get(domain_name)
    if isinstance(value, dict):
        return value
    domain: dict[str, object] = {}
    payload[domain_name] = domain
    return domain


def _first_non_empty_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_empty_tactical_value(value: object) -> bool:
    if value in (None, ""):
        return True
    if isinstance(value, (list, tuple)) and not value:
        return True
    return False


def _set_if_empty(mapping: dict[str, object], key: str, value: object) -> None:
    if _is_empty_tactical_value(mapping.get(key)):
        mapping[key] = value


def _set_if_zero_or_empty(mapping: dict[str, object], key: str, value: object) -> None:
    current = mapping.get(key)
    if _is_empty_tactical_value(current):
        mapping[key] = value
        return
    if isinstance(current, (int, float)) and type(current) is not bool and float(current) == 0.0:
        mapping[key] = value


def _set_float_at_least(mapping: dict[str, object], key: str, minimum: float) -> None:
    current = mapping.get(key)
    if isinstance(current, (int, float)) and type(current) is not bool:
        mapping[key] = max(float(current), minimum)
        return
    if _is_empty_tactical_value(current):
        mapping[key] = minimum


def _set_nested_float_at_least(
    mapping: dict[str, object],
    path: tuple[str, str],
    minimum: float,
) -> None:
    parent_key, child_key = path
    parent = mapping.get(parent_key)
    if not isinstance(parent, dict):
        parent = {}
        mapping[parent_key] = parent
    _set_float_at_least(parent, child_key, minimum)


def _copy_or_default_unit_classes(
    scope: dict[str, object],
    tactical_task: dict[str, object],
    *,
    default: tuple[str, ...],
) -> None:
    task_classes = tactical_task.get("unit_classes")
    scope_classes = scope.get("unit_classes")
    if _is_empty_tactical_value(task_classes) and not _is_empty_tactical_value(scope_classes):
        tactical_task["unit_classes"] = scope_classes
        task_classes = scope_classes
    if _is_empty_tactical_value(task_classes):
        tactical_task["unit_classes"] = list(default)
        task_classes = tactical_task["unit_classes"]
    if _is_empty_tactical_value(scope_classes):
        scope["unit_classes"] = task_classes


def _canonicalize_bias_mapping(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return {
        _canonicalize_micromachine_key(key): bias_value
        for key, bias_value in value.items()
    }


def _canonicalize_enum_alias(
    value: object,
    *,
    aliases: Mapping[str, str],
) -> object:
    if type(value) is not str:
        return value
    normalized = value.strip().lower().replace(" ", "_")
    return aliases.get(normalized, value)


def _canonicalize_micromachine_key(value: object) -> str:
    if type(value) is not str:
        return str(value)
    key = value.strip()
    if not key:
        return key
    upper = key.upper()
    if upper.startswith("TERRAN_") or upper in {
        "BARRACKS_TECHLAB",
        "BARRACKS_REACTOR",
        "FACTORY_TECHLAB",
        "FACTORY_REACTOR",
        "STARPORT_TECHLAB",
        "STARPORT_REACTOR",
        "STIMPACK",
        "COMBATSHIELD",
    }:
        return upper
    return _CANONICAL_MICROMACHINE_KEY_ALIASES.get(
        _canonical_key_token(key),
        key,
    )


def _canonical_key_token(value: str) -> str:
    return "".join(
        char
        for char in value.strip().lower()
        if char.isalnum() or "\uac00" <= char <= "\ud7a3"
    )


def _is_non_text_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _ensure_domain(result: dict[str, object], domain: str) -> dict[str, object]:
    existing = result.setdefault(domain, {})
    if not isinstance(existing, dict):
        raise ValueError(f"{domain} must be a mapping.")
    return existing


def _extract_clarification(mapping: Mapping[str, object]) -> str:
    prompt = mapping.get("clarification_prompt")
    needs_clarification = mapping.get("needs_clarification", False)
    if type(needs_clarification) is not bool:
        raise ValueError("needs_clarification must be a bool.")
    if prompt:
        return _require_text("clarification_prompt", prompt)
    if needs_clarification:
        return "의도를 정책 조정으로 변환하려면 더 구체적인 전략 목표가 필요합니다."
    return ""


def _extract_provider_status(
    mapping: Mapping[str, object],
) -> PolicyModulationCompileStatus | None:
    status = mapping.get("status")
    if not status:
        return None
    return _coerce_status(status)


def _extract_refusal(mapping: Mapping[str, object]) -> str:
    refusal = mapping.get("refusal_reason", "")
    return _require_text("refusal_reason", refusal) if refusal else ""


def _extract_assistant_message(mapping: Mapping[str, object]) -> str:
    message = mapping.get("assistant_message", "")
    return _require_text("assistant_message", message) if message else ""


def _refused(
    source: PolicyModulationSource,
    reason: str,
    *,
    assistant_message: str = "",
) -> PolicyModulationCompileResult:
    return PolicyModulationCompileResult(
        status=PolicyModulationCompileStatus.REFUSED,
        source=source,
        assistant_message=assistant_message,
        refusal_reason=reason,
    )


def _coerce_status(value: PolicyModulationCompileStatus | str) -> PolicyModulationCompileStatus:
    if isinstance(value, PolicyModulationCompileStatus):
        return value
    if type(value) is not str:
        raise ValueError("status must be a string.")
    try:
        return PolicyModulationCompileStatus(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported compile status: {value!r}.") from exc


def _coerce_source(value: PolicyModulationSource | str) -> PolicyModulationSource:
    if isinstance(value, PolicyModulationSource):
        return value
    if type(value) is not str:
        raise ValueError("source must be a string.")
    try:
        source = PolicyModulationSource(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported policy modulation source: {value!r}.") from exc
    if source is PolicyModulationSource.SYSTEM:
        return source
    if source not in POLICY_MODULATION_PROVIDER_SOURCES:
        raise ValueError(f"unsupported provider source: {value!r}.")
    return source


def _safe_source(value: object) -> PolicyModulationSource:
    try:
        return _coerce_source(value)  # type: ignore[arg-type]
    except ValueError:
        return PolicyModulationSource.LLM


def _coerce_override_level(value: PolicyOverrideLevel | str) -> PolicyOverrideLevel:
    if isinstance(value, PolicyOverrideLevel):
        return value
    if type(value) is not str:
        raise ValueError("override level must be a string.")
    try:
        return PolicyOverrideLevel(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported override level: {value!r}.") from exc


def _require_text(field_name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _string_tuple(name: str, values: object) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence of strings.")
    return tuple(_require_text(name, value) for value in values)
