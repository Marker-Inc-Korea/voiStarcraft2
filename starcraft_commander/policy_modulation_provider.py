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
        provider_status = _extract_provider_status(provider_output)
        clarification = _extract_clarification(provider_output)
        if clarification or provider_status is PolicyModulationCompileStatus.CLARIFICATION_REQUIRED:
            return PolicyModulationCompileResult(
                status=PolicyModulationCompileStatus.CLARIFICATION_REQUIRED,
                source=source,
                clarification_prompt=(
                    clarification
                    or "의도를 정책 조정으로 변환하려면 더 구체적인 전략 목표가 필요합니다."
                ),
            )
        refusal = _extract_refusal(provider_output)
        vector_payload = _extract_vector_payload(provider_output)
        if refusal or provider_status is PolicyModulationCompileStatus.REFUSED:
            return _refused(source, refusal or "provider refused policy modulation.")
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
}

_CONTROL_KEYS = {
    "status",
    "needs_clarification",
    "clarification_prompt",
    "refusal_reason",
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
    "gas_priority": ("economy", "gas_priority"),
    "gas_worker_target_bias": ("economy", "gas_worker_target_bias"),
    "mineral_saturation_bias": ("economy", "mineral_saturation_bias"),
    "repair_priority": ("economy", "repair_priority"),
    "supply_buffer_bias": ("economy", "supply_buffer_bias"),
    "expansion_safety_bias": ("economy", "expansion_safety_bias"),
    "mule_priority": ("economy", "mule_priority"),
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
    "reinforce_bias": ("squad", "reinforce_bias"),
    "contain_bias": ("squad", "contain_bias"),
    "squad_role_biases": ("squad", "squad_role_biases"),
    "cancel_attacks": ("emergency", "cancel_attacks"),
    "pull_workers_for_defense": ("emergency", "pull_workers_for_defense"),
    "evacuate_workers": ("emergency", "evacuate_workers"),
    "force_retreat": ("emergency", "force_retreat"),
    "hold_position": ("emergency", "hold_position"),
    "prioritize_repair": ("emergency", "prioritize_repair"),
    "stop_expansion": ("emergency", "stop_expansion"),
}

_DOMAIN_KEYS = {
    "strategy",
    "economy",
    "tech",
    "production",
    "combat",
    "scouting",
    "squad",
    "emergency",
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
    *_DOMAIN_KEYS,
}

_REPRESENTATION_KEYS = {
    "representation",
    "representation_axes",
    "latent_axes",
    "latent_vector",
}


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
            _ensure_domain(result, domain)[field_name] = value
            continue
        if canonical_key in _DOMAIN_KEYS:
            if not isinstance(value, Mapping):
                result[canonical_key] = value
                continue
            domain = _ensure_domain(result, canonical_key)
            for field_name, field_value in value.items():
                if type(field_name) is not str or not field_name.strip():
                    raise ValueError(f"{canonical_key} field names must be strings.")
                domain[field_name] = field_value
            continue
        if canonical_key in _VECTOR_KEYS:
            result[canonical_key] = value
            continue
        warnings.append(f"ignored provider field: {key}")

    result.setdefault("source", default_source.value)
    if "goal" not in result and default_goal:
        result["goal"] = default_goal
    return result, tuple(warnings)


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


def _refused(
    source: PolicyModulationSource,
    reason: str,
) -> PolicyModulationCompileResult:
    return PolicyModulationCompileResult(
        status=PolicyModulationCompileStatus.REFUSED,
        source=source,
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
