"""Provider-agnostic policy modulation DSL for strong-bot collaboration.

The contracts in this module describe *policy bias*, not direct SC2 actions.
They are designed for MicroMachine-style strong bots where a human, LLM, UI,
replay imitator, or future neural representation model can modulate manager
decisions while the bot keeps owning tactical execution.

This module is stdlib-only and intentionally independent of python-sc2 and the
MicroMachine C++ runtime. Runtime bridges should serialize these contracts into
a sidecar/blackboard protocol; they must not treat them as raw game commands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final


class PolicyOverrideLevel(str, Enum):
    """How strongly a modulation vector may affect the autonomous bot."""

    BIAS = "bias"
    CONSTRAINT = "constraint"
    DIRECTIVE = "directive"
    EMERGENCY = "emergency"


class PolicyModulationSource(str, Enum):
    """Source category for a modulation vector."""

    HUMAN = "human"
    LLM = "llm"
    UI = "ui"
    REPLAY_IMITATION = "replay_imitation"
    NEURAL_REPRESENTATION = "neural_representation"
    SYSTEM = "system"


POLICY_OVERRIDE_LEVELS: Final[frozenset[str]] = frozenset(
    level.value for level in PolicyOverrideLevel
)
POLICY_MODULATION_SOURCES: Final[frozenset[str]] = frozenset(
    source.value for source in PolicyModulationSource
)

POLICY_MODULATION_TTL_MIN_SECONDS: Final[int] = 1
POLICY_MODULATION_TTL_MAX_SECONDS: Final[int] = 900
"""Maximum TTL is bounded so stale human/model intent cannot linger forever."""

POLICY_MODULATION_RAW_CONTROL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_call",
        "api_calls",
        "attack_move",
        "botai_method",
        "botai_methods",
        "build_structure",
        "click",
        "command",
        "commands",
        "direct_command",
        "direct_commands",
        "direct_key",
        "direct_keys",
        "direct_sc2_command",
        "direct_sc2_commands",
        "do",
        "hotkey",
        "hotkeys",
        "issue_order",
        "key_down",
        "key_press",
        "key_up",
        "keyboard",
        "keyboard_key",
        "keyboard_keys",
        "keyboard_shortcut",
        "keyboard_shortcuts",
        "keypress",
        "mouse",
        "press_key",
        "python_sc2",
        "python_sc2_call",
        "raw_action",
        "raw_actions",
        "raw_command",
        "raw_commands",
        "raw_key",
        "raw_keys",
        "s2client_api",
        "sc2_command",
        "sc2_commands",
        "send_key",
        "send_keys",
        "train_unit",
        "unit_tag",
        "unit_tags",
    }
)
"""Keys rejected from provider mappings before they can reach a bot bridge."""

POLICY_MODULATION_RAW_CONTROL_KEY_ALIASES: Final[frozenset[str]] = frozenset(
    key.replace("_", "") for key in POLICY_MODULATION_RAW_CONTROL_KEYS
)
"""Separator-insensitive raw-control key aliases for camelCase/acronyms."""


def reject_raw_policy_control_keys(mapping: Mapping[str, object], *, path: str = "") -> None:
    """Reject raw SC2/API control keys in nested provider output."""

    for key, value in mapping.items():
        if not isinstance(key, str):
            raise ValueError(f"{path or 'payload'} contains a non-string key.")
        normalized = _normalize_policy_control_key(key)
        if _is_raw_policy_control_key(normalized):
            location = f"{path}.{key}" if path else key
            raise ValueError(
                f"policy modulation payload attempted raw runtime control: {location}"
            )
        if isinstance(value, Mapping):
            next_path = f"{path}.{key}" if path else key
            reject_raw_policy_control_keys(value, path=next_path)
        elif _is_non_text_sequence(value):
            next_path = f"{path}.{key}" if path else key
            _reject_raw_policy_control_keys_in_sequence(value, path=next_path)


def _reject_raw_policy_control_keys_in_sequence(
    values: Sequence[object],
    *,
    path: str,
) -> None:
    for index, value in enumerate(values):
        next_path = f"{path}[{index}]"
        if isinstance(value, Mapping):
            reject_raw_policy_control_keys(value, path=next_path)
        elif _is_non_text_sequence(value):
            _reject_raw_policy_control_keys_in_sequence(value, path=next_path)


@dataclass(frozen=True)
class WeightedBiases:
    """Named weights in the inclusive range [-1.0, 1.0]."""

    values: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: dict[str, float] = {}
        for key, value in self.values.items():
            if type(key) is not str or not key.strip():
                raise ValueError("bias keys must be non-empty strings.")
            normalized[key.strip()] = _coerce_unit_interval(
                value, field_name=f"bias {key!r}", lower=-1.0, upper=1.0
            )
        object.__setattr__(self, "values", normalized)

    @classmethod
    def from_mapping(cls, mapping: object) -> "WeightedBiases":
        if mapping is None:
            return cls()
        if not isinstance(mapping, Mapping):
            raise ValueError("weighted biases must be a mapping.")
        return cls({str(key): _coerce_float(value, field_name=str(key)) for key, value in mapping.items()})

    def to_dict(self) -> dict[str, float]:
        return dict(self.values)

    def __bool__(self) -> bool:
        return bool(self.values)


@dataclass(frozen=True)
class StrategyModulation:
    """Build and posture preferences for `StrategyManager`-style seams."""

    posture: str = "balanced"
    preferred_builds: WeightedBiases = field(default_factory=WeightedBiases)
    avoided_builds: WeightedBiases = field(default_factory=WeightedBiases)
    timing_biases: WeightedBiases = field(default_factory=WeightedBiases)
    transition_biases: WeightedBiases = field(default_factory=WeightedBiases)
    strategic_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "posture", _require_choice(
            "posture",
            self.posture,
            {"economic", "defensive", "balanced", "pressure", "all_in"},
        ))
        object.__setattr__(self, "preferred_builds", _coerce_biases(self.preferred_builds))
        object.__setattr__(self, "avoided_builds", _coerce_biases(self.avoided_builds))
        object.__setattr__(self, "timing_biases", _coerce_biases(self.timing_biases))
        object.__setattr__(self, "transition_biases", _coerce_biases(self.transition_biases))
        object.__setattr__(
            self,
            "strategic_tags",
            _validate_string_tuple("strategic_tags", self.strategic_tags),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "posture": self.posture,
            "preferred_builds": self.preferred_builds.to_dict(),
            "avoided_builds": self.avoided_builds.to_dict(),
            "timing_biases": self.timing_biases.to_dict(),
            "transition_biases": self.transition_biases.to_dict(),
            "strategic_tags": list(self.strategic_tags),
        }


@dataclass(frozen=True)
class EconomyModulation:
    """Economy and expansion pressure for worker/production managers."""

    expand_bias: float = 0.0
    worker_production_bias: float = 0.0
    gas_priority: float = 0.0
    gas_worker_target_bias: float = 0.0
    mineral_saturation_bias: float = 0.0
    repair_priority: float = 0.0
    supply_buffer_bias: float = 0.0
    expansion_safety_bias: float = 0.0
    mule_priority: float = 0.0

    def __post_init__(self) -> None:
        _set_unit_interval_fields(
            self,
            (
                "expand_bias",
                "worker_production_bias",
                "gas_priority",
                "gas_worker_target_bias",
                "mineral_saturation_bias",
                "repair_priority",
                "supply_buffer_bias",
                "expansion_safety_bias",
                "mule_priority",
            ),
        )

    def to_dict(self) -> dict[str, float]:
        return _float_fields_to_dict(
            self,
            (
                "expand_bias",
                "worker_production_bias",
                "gas_priority",
                "gas_worker_target_bias",
                "mineral_saturation_bias",
                "repair_priority",
                "supply_buffer_bias",
                "expansion_safety_bias",
                "mule_priority",
            ),
        )


@dataclass(frozen=True)
class TechModulation:
    """Tech, upgrade, and unit-composition preferences."""

    structure_biases: WeightedBiases = field(default_factory=WeightedBiases)
    unit_biases: WeightedBiases = field(default_factory=WeightedBiases)
    upgrade_biases: WeightedBiases = field(default_factory=WeightedBiases)
    tech_path_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "structure_biases", _coerce_biases(self.structure_biases))
        object.__setattr__(self, "unit_biases", _coerce_biases(self.unit_biases))
        object.__setattr__(self, "upgrade_biases", _coerce_biases(self.upgrade_biases))
        object.__setattr__(
            self,
            "tech_path_tags",
            _validate_string_tuple("tech_path_tags", self.tech_path_tags),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "structure_biases": self.structure_biases.to_dict(),
            "unit_biases": self.unit_biases.to_dict(),
            "upgrade_biases": self.upgrade_biases.to_dict(),
            "tech_path_tags": list(self.tech_path_tags),
        }


@dataclass(frozen=True)
class ProductionModulation:
    """Build-order and production-queue modulation."""

    queue_biases: WeightedBiases = field(default_factory=WeightedBiases)
    composition_biases: WeightedBiases = field(default_factory=WeightedBiases)
    addon_biases: WeightedBiases = field(default_factory=WeightedBiases)
    production_facility_biases: WeightedBiases = field(default_factory=WeightedBiases)
    max_tech_deviation: float = 0.0
    production_continuity_bias: float = 0.0
    tech_switch_urgency: float = 0.0
    allow_build_order_rewrite: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "queue_biases", _coerce_biases(self.queue_biases))
        object.__setattr__(
            self, "composition_biases", _coerce_biases(self.composition_biases)
        )
        object.__setattr__(self, "addon_biases", _coerce_biases(self.addon_biases))
        object.__setattr__(
            self,
            "production_facility_biases",
            _coerce_biases(self.production_facility_biases),
        )
        object.__setattr__(
            self,
            "max_tech_deviation",
            _coerce_unit_interval(
                self.max_tech_deviation,
                field_name="max_tech_deviation",
                lower=0.0,
                upper=1.0,
            ),
        )
        object.__setattr__(
            self,
            "production_continuity_bias",
            _coerce_unit_interval(
                self.production_continuity_bias,
                field_name="production_continuity_bias",
                lower=-1.0,
                upper=1.0,
            ),
        )
        object.__setattr__(
            self,
            "tech_switch_urgency",
            _coerce_unit_interval(
                self.tech_switch_urgency,
                field_name="tech_switch_urgency",
                lower=-1.0,
                upper=1.0,
            ),
        )
        object.__setattr__(
            self,
            "allow_build_order_rewrite",
            _coerce_bool(self.allow_build_order_rewrite, "allow_build_order_rewrite"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "queue_biases": self.queue_biases.to_dict(),
            "composition_biases": self.composition_biases.to_dict(),
            "addon_biases": self.addon_biases.to_dict(),
            "production_facility_biases": self.production_facility_biases.to_dict(),
            "max_tech_deviation": self.max_tech_deviation,
            "production_continuity_bias": self.production_continuity_bias,
            "tech_switch_urgency": self.tech_switch_urgency,
            "allow_build_order_rewrite": self.allow_build_order_rewrite,
        }


@dataclass(frozen=True)
class CombatModulation:
    """Fight-selection and tactical-risk modulation."""

    aggression: float = 0.0
    engage_threshold_delta: float = 0.0
    retreat_threshold_delta: float = 0.0
    attack_timing_bias: float = 0.0
    commitment_level: float = 0.0
    pressure_window_frames: int = 0
    attack_condition_override: str = "normal"
    retreat_patience_bias: float = 0.0
    rally_before_attack_bias: float = 0.0
    harassment_bias: float = 0.0
    defend_bias: float = 0.0
    preserve_army_bias: float = 0.0
    combat_sim_confidence_margin: float = 0.0
    siege_position_bias: float = 0.0
    kite_bias: float = 0.0
    flank_bias: float = 0.0
    target_priority_biases: WeightedBiases = field(default_factory=WeightedBiases)

    def __post_init__(self) -> None:
        _set_unit_interval_fields(
            self,
            (
                "aggression",
                "engage_threshold_delta",
                "retreat_threshold_delta",
                "attack_timing_bias",
                "commitment_level",
                "retreat_patience_bias",
                "rally_before_attack_bias",
                "harassment_bias",
                "defend_bias",
                "preserve_army_bias",
                "combat_sim_confidence_margin",
                "siege_position_bias",
                "kite_bias",
                "flank_bias",
            ),
        )
        object.__setattr__(
            self,
            "target_priority_biases",
            _coerce_biases(self.target_priority_biases),
        )
        object.__setattr__(
            self,
            "pressure_window_frames",
            _coerce_bounded_int(
                self.pressure_window_frames,
                field_name="pressure_window_frames",
                lower=0,
                upper=120_000,
            ),
        )
        object.__setattr__(
            self,
            "attack_condition_override",
            _require_choice(
                "attack_condition_override",
                self.attack_condition_override,
                {"never", "normal", "earlier_if_safe", "force_when_threshold_met"},
            ),
        )

    def to_dict(self) -> dict[str, object]:
        payload = _float_fields_to_dict(
            self,
            (
                "aggression",
                "engage_threshold_delta",
                "retreat_threshold_delta",
                "attack_timing_bias",
                "commitment_level",
                "retreat_patience_bias",
                "rally_before_attack_bias",
                "harassment_bias",
                "defend_bias",
                "preserve_army_bias",
                "combat_sim_confidence_margin",
                "siege_position_bias",
                "kite_bias",
                "flank_bias",
            ),
        )
        payload["pressure_window_frames"] = self.pressure_window_frames
        payload["attack_condition_override"] = self.attack_condition_override
        payload["target_priority_biases"] = self.target_priority_biases.to_dict()
        return payload


@dataclass(frozen=True)
class ScoutingModulation:
    """Information-gathering and scouting-risk modulation."""

    scout_priority: float = 0.0
    risk_tolerance: float = 0.0
    scout_cadence_bias: float = 0.0
    scan_priority: float = 0.0
    hidden_tech_scout_bias: float = 0.0
    target_biases: WeightedBiases = field(default_factory=WeightedBiases)
    require_fresh_enemy_observation: bool = False

    def __post_init__(self) -> None:
        _set_unit_interval_fields(
            self,
            (
                "scout_priority",
                "risk_tolerance",
                "scout_cadence_bias",
                "scan_priority",
                "hidden_tech_scout_bias",
            ),
        )
        object.__setattr__(self, "target_biases", _coerce_biases(self.target_biases))
        object.__setattr__(
            self,
            "require_fresh_enemy_observation",
            _coerce_bool(
                self.require_fresh_enemy_observation,
                "require_fresh_enemy_observation",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scout_priority": self.scout_priority,
            "risk_tolerance": self.risk_tolerance,
            "scout_cadence_bias": self.scout_cadence_bias,
            "scan_priority": self.scan_priority,
            "hidden_tech_scout_bias": self.hidden_tech_scout_bias,
            "target_biases": self.target_biases.to_dict(),
            "require_fresh_enemy_observation": self.require_fresh_enemy_observation,
        }


@dataclass(frozen=True)
class SquadModulation:
    """Squad allocation bias across main, defense, harassment, and regrouping."""

    main_army_bias: float = 0.0
    harassment_bias: float = 0.0
    defense_bias: float = 0.0
    regroup_bias: float = 0.0
    drop_bias: float = 0.0
    split_army_bias: float = 0.0
    flank_bias: float = 0.0
    reinforce_bias: float = 0.0
    contain_bias: float = 0.0
    proxy_pressure_bias: float = 0.0
    squad_role_biases: WeightedBiases = field(default_factory=WeightedBiases)

    def __post_init__(self) -> None:
        _set_unit_interval_fields(
            self,
            (
                "main_army_bias",
                "harassment_bias",
                "defense_bias",
                "regroup_bias",
                "drop_bias",
                "split_army_bias",
                "flank_bias",
                "reinforce_bias",
                "contain_bias",
                "proxy_pressure_bias",
            ),
        )
        object.__setattr__(
            self, "squad_role_biases", _coerce_biases(self.squad_role_biases)
        )

    def to_dict(self) -> dict[str, object]:
        payload = _float_fields_to_dict(
            self,
            (
                "main_army_bias",
                "harassment_bias",
                "defense_bias",
                "regroup_bias",
                "drop_bias",
                "split_army_bias",
                "flank_bias",
                "reinforce_bias",
                "contain_bias",
                "proxy_pressure_bias",
            ),
        )
        payload["squad_role_biases"] = self.squad_role_biases.to_dict()
        return payload


@dataclass(frozen=True)
class TacticalScopeModulation:
    """Semantic unit-selection-like scope resolved by the bot, never by tags."""

    army_group: str = ""
    unit_classes: tuple[str, ...] = ()
    location_intent: str = ""
    duration_seconds: int = 0
    min_units: int = 0
    max_units: int = 0
    require_safety_margin: float = 0.0
    allow_partial_scope: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "army_group",
            _optional_choice(
                "army_group",
                self.army_group,
                {
                    "main",
                    "harass",
                    "defense",
                    "scout",
                    "air",
                    "bio",
                    "mech",
                    "siege",
                    "workers",
                },
            ),
        )
        object.__setattr__(
            self,
            "unit_classes",
            _validate_string_tuple("unit_classes", self.unit_classes),
        )
        object.__setattr__(
            self,
            "location_intent",
            _optional_choice(
                "location_intent",
                self.location_intent,
                {
                    "home",
                    "natural",
                    "enemy_main",
                    "enemy_natural",
                    "enemy_third",
                    "third",
                    "watchtower",
                    "ramp",
                    "last_seen_enemy_army",
                },
            ),
        )
        object.__setattr__(
            self,
            "duration_seconds",
            _coerce_bounded_int(
                self.duration_seconds,
                field_name="duration_seconds",
                lower=0,
                upper=POLICY_MODULATION_TTL_MAX_SECONDS,
            ),
        )
        object.__setattr__(
            self,
            "min_units",
            _coerce_bounded_int(self.min_units, field_name="min_units", lower=0, upper=200),
        )
        object.__setattr__(
            self,
            "max_units",
            _coerce_bounded_int(self.max_units, field_name="max_units", lower=0, upper=200),
        )
        if self.max_units and self.min_units and self.max_units < self.min_units:
            raise ValueError("max_units must be greater than or equal to min_units.")
        object.__setattr__(
            self,
            "require_safety_margin",
            _coerce_unit_interval(
                self.require_safety_margin,
                field_name="require_safety_margin",
                lower=0.0,
                upper=1.0,
            ),
        )
        object.__setattr__(
            self,
            "allow_partial_scope",
            _coerce_bool(self.allow_partial_scope, "allow_partial_scope"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "army_group": self.army_group,
            "unit_classes": list(self.unit_classes),
            "location_intent": self.location_intent,
            "duration_seconds": self.duration_seconds,
            "min_units": self.min_units,
            "max_units": self.max_units,
            "require_safety_margin": self.require_safety_margin,
            "allow_partial_scope": self.allow_partial_scope,
        }


@dataclass(frozen=True)
class EmergencyModulation:
    """Short-lived emergency intervention flags."""

    cancel_attacks: bool = False
    pull_workers_for_defense: bool = False
    evacuate_workers: bool = False
    force_retreat: bool = False
    hold_position: bool = False
    prioritize_repair: bool = False
    stop_expansion: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "cancel_attacks",
            "pull_workers_for_defense",
            "evacuate_workers",
            "force_retreat",
            "hold_position",
            "prioritize_repair",
            "stop_expansion",
        ):
            object.__setattr__(
                self,
                field_name,
                _coerce_bool(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, bool]:
        return {
            "cancel_attacks": self.cancel_attacks,
            "pull_workers_for_defense": self.pull_workers_for_defense,
            "evacuate_workers": self.evacuate_workers,
            "force_retreat": self.force_retreat,
            "hold_position": self.hold_position,
            "prioritize_repair": self.prioritize_repair,
            "stop_expansion": self.stop_expansion,
        }


@dataclass(frozen=True)
class PolicySafetyConstraint:
    """A bounded constraint that a bridge may enforce against bot managers."""

    key: str
    value: object = True
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _require_text("key", self.key))
        reject_raw_policy_control_keys(
            {self.key: self.value},
            path="constraint",
        )
        if self.reason:
            object.__setattr__(self, "reason", _require_text("reason", self.reason))

    def to_dict(self) -> dict[str, object]:
        return {"key": self.key, "value": self.value, "reason": self.reason}


@dataclass(frozen=True)
class PolicyModulationVector:
    """Deep commander DSL payload consumed by a strong-bot modulation bridge."""

    goal: str
    source: PolicyModulationSource | str = PolicyModulationSource.HUMAN
    override_level: PolicyOverrideLevel | str = PolicyOverrideLevel.BIAS
    confidence: float = 1.0
    ttl_seconds: int = 120
    strategy: StrategyModulation = field(default_factory=StrategyModulation)
    economy: EconomyModulation = field(default_factory=EconomyModulation)
    tech: TechModulation = field(default_factory=TechModulation)
    production: ProductionModulation = field(default_factory=ProductionModulation)
    combat: CombatModulation = field(default_factory=CombatModulation)
    scouting: ScoutingModulation = field(default_factory=ScoutingModulation)
    squad: SquadModulation = field(default_factory=SquadModulation)
    scope: TacticalScopeModulation = field(default_factory=TacticalScopeModulation)
    emergency: EmergencyModulation = field(default_factory=EmergencyModulation)
    constraints: tuple[PolicySafetyConstraint, ...] = ()
    tags: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", _require_text("goal", self.goal))
        object.__setattr__(self, "source", _coerce_source(self.source))
        object.__setattr__(self, "override_level", _coerce_override_level(self.override_level))
        object.__setattr__(
            self,
            "confidence",
            _coerce_unit_interval(
                self.confidence, field_name="confidence", lower=0.0, upper=1.0
            ),
        )
        if type(self.ttl_seconds) is bool or not isinstance(self.ttl_seconds, int):
            raise TypeError("ttl_seconds must be an integer.")
        if not (
            POLICY_MODULATION_TTL_MIN_SECONDS
            <= self.ttl_seconds
            <= POLICY_MODULATION_TTL_MAX_SECONDS
        ):
            raise ValueError(
                "ttl_seconds must be between "
                f"{POLICY_MODULATION_TTL_MIN_SECONDS} and "
                f"{POLICY_MODULATION_TTL_MAX_SECONDS}."
            )
        object.__setattr__(self, "strategy", _coerce_domain(self.strategy, StrategyModulation))
        object.__setattr__(self, "economy", _coerce_domain(self.economy, EconomyModulation))
        object.__setattr__(self, "tech", _coerce_domain(self.tech, TechModulation))
        object.__setattr__(
            self, "production", _coerce_domain(self.production, ProductionModulation)
        )
        object.__setattr__(self, "combat", _coerce_domain(self.combat, CombatModulation))
        object.__setattr__(
            self, "scouting", _coerce_domain(self.scouting, ScoutingModulation)
        )
        object.__setattr__(self, "squad", _coerce_domain(self.squad, SquadModulation))
        object.__setattr__(
            self, "scope", _coerce_domain(self.scope, TacticalScopeModulation)
        )
        object.__setattr__(
            self, "emergency", _coerce_domain(self.emergency, EmergencyModulation)
        )
        object.__setattr__(
            self,
            "constraints",
            _validate_constraints(self.constraints),
        )
        object.__setattr__(self, "tags", _validate_string_tuple("tags", self.tags))
        if self.rationale:
            object.__setattr__(self, "rationale", _require_text("rationale", self.rationale))
        if self.override_level is PolicyOverrideLevel.EMERGENCY and self.ttl_seconds > 60:
            raise ValueError("emergency modulation ttl_seconds cannot exceed 60.")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "PolicyModulationVector":
        """Build a validated vector from provider or UI JSON-like output."""

        if not isinstance(mapping, Mapping):
            raise ValueError("policy modulation vector must be built from a mapping.")
        reject_raw_policy_control_keys(mapping)
        return cls(
            goal=_text_from_mapping(mapping, "goal"),
            source=mapping.get("source", PolicyModulationSource.HUMAN.value),
            override_level=mapping.get("override_level", PolicyOverrideLevel.BIAS.value),
            confidence=mapping.get("confidence", 1.0),
            ttl_seconds=_int_from_mapping(mapping, "ttl_seconds", 120),
            strategy=_domain_from_mapping(mapping, "strategy", StrategyModulation),
            economy=_domain_from_mapping(mapping, "economy", EconomyModulation),
            tech=_domain_from_mapping(mapping, "tech", TechModulation),
            production=_domain_from_mapping(mapping, "production", ProductionModulation),
            combat=_domain_from_mapping(mapping, "combat", CombatModulation),
            scouting=_domain_from_mapping(mapping, "scouting", ScoutingModulation),
            squad=_domain_from_mapping(mapping, "squad", SquadModulation),
            scope=_domain_from_mapping(mapping, "scope", TacticalScopeModulation),
            emergency=_domain_from_mapping(mapping, "emergency", EmergencyModulation),
            constraints=_constraints_from_mapping(mapping.get("constraints", ())),
            tags=_string_tuple_from_mapping(mapping.get("tags", ()), "tags"),
            rationale=str(mapping.get("rationale", "")),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready policy modulation vector."""

        return {
            "goal": self.goal,
            "source": self.source.value,
            "override_level": self.override_level.value,
            "confidence": self.confidence,
            "ttl_seconds": self.ttl_seconds,
            "strategy": self.strategy.to_dict(),
            "economy": self.economy.to_dict(),
            "tech": self.tech.to_dict(),
            "production": self.production.to_dict(),
            "combat": self.combat.to_dict(),
            "scouting": self.scouting.to_dict(),
            "squad": self.squad.to_dict(),
            "scope": self.scope.to_dict(),
            "emergency": self.emergency.to_dict(),
            "constraints": [constraint.to_dict() for constraint in self.constraints],
            "tags": list(self.tags),
            "rationale": self.rationale,
        }


def _coerce_source(value: object) -> PolicyModulationSource:
    if isinstance(value, PolicyModulationSource):
        return value
    if type(value) is not str:
        raise ValueError("source must be a string.")
    normalized = value.strip().lower()
    try:
        return PolicyModulationSource(normalized)
    except ValueError as exc:
        raise ValueError(
            "unsupported policy modulation source: "
            f"{value!r}. Supported: {', '.join(sorted(POLICY_MODULATION_SOURCES))}."
        ) from exc


def _coerce_override_level(value: object) -> PolicyOverrideLevel:
    if isinstance(value, PolicyOverrideLevel):
        return value
    if type(value) is not str:
        raise ValueError("override_level must be a string.")
    normalized = value.strip().lower()
    try:
        return PolicyOverrideLevel(normalized)
    except ValueError as exc:
        raise ValueError(
            "unsupported policy override level: "
            f"{value!r}. Supported: {', '.join(sorted(POLICY_OVERRIDE_LEVELS))}."
        ) from exc


def _coerce_biases(value: object) -> WeightedBiases:
    if isinstance(value, WeightedBiases):
        return value
    return WeightedBiases.from_mapping(value)


def _coerce_domain(value: object, domain_type: type) -> object:
    if isinstance(value, domain_type):
        return value
    if isinstance(value, Mapping):
        return domain_type(**value)
    raise ValueError(f"{domain_type.__name__} must be an instance or mapping.")


def _validate_constraints(values: object) -> tuple[PolicySafetyConstraint, ...]:
    if not _is_non_text_sequence(values):
        raise ValueError("constraints must be a sequence.")
    result: list[PolicySafetyConstraint] = []
    for value in values:
        if isinstance(value, PolicySafetyConstraint):
            result.append(value)
        elif isinstance(value, Mapping):
            result.append(PolicySafetyConstraint(**value))
        else:
            raise ValueError("constraints must contain mappings or constraints.")
    return tuple(result)


def _constraints_from_mapping(values: object) -> tuple[PolicySafetyConstraint, ...]:
    return _validate_constraints(values)


def _domain_from_mapping(mapping: Mapping[str, object], key: str, domain_type: type) -> object:
    value = mapping.get(key, {})
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping.")
    converted = _convert_bias_fields(value)
    return domain_type(**converted)


def _convert_bias_fields(mapping: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in mapping.items():
        if key.endswith("_biases") or key in {
            "preferred_builds",
            "avoided_builds",
            "target_biases",
            "queue_biases",
            "composition_biases",
            "squad_role_biases",
        }:
            result[key] = WeightedBiases.from_mapping(value)
        else:
            result[key] = value
    return result


def _set_unit_interval_fields(instance: object, field_names: Sequence[str]) -> None:
    for field_name in field_names:
        object.__setattr__(
            instance,
            field_name,
            _coerce_unit_interval(
                getattr(instance, field_name),
                field_name=field_name,
                lower=-1.0,
                upper=1.0,
            ),
        )


def _float_fields_to_dict(instance: object, field_names: Sequence[str]) -> dict[str, float]:
    return {field_name: float(getattr(instance, field_name)) for field_name in field_names}


def _coerce_unit_interval(
    value: object,
    *,
    field_name: str,
    lower: float,
    upper: float,
) -> float:
    number = _coerce_float(value, field_name=field_name)
    if not lower <= number <= upper:
        raise ValueError(f"{field_name} must be between {lower} and {upper}.")
    return number


def _coerce_float(value: object, *, field_name: str) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number.")
    return float(value)


def _coerce_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be a bool.")
    return value


def _coerce_bounded_int(
    value: object,
    *,
    field_name: str,
    lower: int,
    upper: int,
) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer.")
    if not lower <= value <= upper:
        raise ValueError(f"{field_name} must be between {lower} and {upper}.")
    return value


def _require_text(field_name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _require_choice(field_name: str, value: object, choices: set[str]) -> str:
    text = _require_text(field_name, value).lower()
    if text not in choices:
        raise ValueError(
            f"{field_name} must be one of: {', '.join(sorted(choices))}."
        )
    return text


def _optional_choice(field_name: str, value: object, choices: set[str]) -> str:
    if value in (None, ""):
        return ""
    return _require_choice(field_name, value, choices)


def _validate_string_tuple(name: str, values: object) -> tuple[str, ...]:
    if values is None:
        return ()
    if not _is_non_text_sequence(values):
        raise ValueError(f"{name} must be a sequence of strings.")
    result = tuple(values)
    for value in result:
        _require_text(name, value)
    return tuple(str(value).strip() for value in result)


def _string_tuple_from_mapping(values: object, name: str) -> tuple[str, ...]:
    return _validate_string_tuple(name, values)


def _text_from_mapping(mapping: Mapping[str, object], key: str) -> str:
    if key not in mapping:
        raise ValueError(f"{key} is required.")
    return _require_text(key, mapping[key])


def _int_from_mapping(mapping: Mapping[str, object], key: str, default: int) -> int:
    value = mapping.get(key, default)
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer.")
    return value


def _is_non_text_sequence(value: object) -> bool:
    return not isinstance(value, (str, bytes)) and isinstance(value, Sequence)


def _normalize_policy_control_key(value: str) -> str:
    """Canonicalize key spellings so camelCase cannot bypass raw-control checks."""

    result: list[str] = []
    previous = ""
    for character in value.strip():
        if character in {"-", " ", "\t", "\n", "\r"}:
            if result and result[-1] != "_":
                result.append("_")
        elif character.isupper():
            if (
                result
                and result[-1] != "_"
                and (previous.islower() or previous.isdigit())
            ):
                result.append("_")
            result.append(character.lower())
        else:
            result.append(character.lower())
        previous = character
    return "".join(result).strip("_")


def _is_raw_policy_control_key(normalized_key: str) -> bool:
    if normalized_key in POLICY_MODULATION_RAW_CONTROL_KEYS:
        return True
    compact = _compact_policy_control_key(normalized_key)
    if compact in POLICY_MODULATION_RAW_CONTROL_KEY_ALIASES:
        return True
    if _is_keyboard_control_alias(compact):
        return True
    for separator in (".", "/", ":"):
        if separator not in normalized_key:
            continue
        if any(
            part in POLICY_MODULATION_RAW_CONTROL_KEYS
            or _compact_policy_control_key(part) in POLICY_MODULATION_RAW_CONTROL_KEY_ALIASES
            or _is_keyboard_control_alias(_compact_policy_control_key(part))
            for part in normalized_key.split(separator)
        ):
            return True
    return False


def _compact_policy_control_key(value: str) -> str:
    return "".join(character for character in value if character.isalnum())


def _is_keyboard_control_alias(compact_key: str) -> bool:
    has_key_token = "key" in compact_key or "hotkey" in compact_key
    if not has_key_token:
        return False
    return any(
        marker in compact_key
        for marker in (
            "direct",
            "down",
            "keyboard",
            "press",
            "raw",
            "send",
            "shortcut",
            "up",
        )
    )
