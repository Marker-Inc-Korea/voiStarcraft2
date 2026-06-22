"""Neural/SOTA representation provider adapter for MicroMachine modulation.

This module is the concrete attachment seam for AlphaStar-like, imitation, or
other representation models. The model owns representation inference only; it
must return bounded semantic axes that still pass through the deterministic
policy modulation compiler before reaching MicroMachine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from starcraft_commander.micromachine_runtime import (
    MicroMachineBackendPublishResult,
    MicroMachineModulationBackend,
)
from starcraft_commander.policy_modulation import (
    PolicyModulationSource,
    PolicyOverrideLevel,
    reject_raw_policy_control_keys,
)
from starcraft_commander.policy_modulation_provider import (
    compile_policy_modulation_from_provider,
    PolicyModulationProviderInterface,
    PolicyModulationProviderRequest,
)


DEFAULT_NEURAL_REPRESENTATION_AXES: tuple[str, ...] = (
    "strategy.posture",
    "economy.expand_bias",
    "economy.worker_production_bias",
    "economy.gas_priority",
    "tech.unit_biases.TERRAN_MARINE",
    "tech.unit_biases.TERRAN_MARAUDER",
    "tech.unit_biases.TERRAN_SIEGETANK",
    "production.max_tech_deviation",
    "combat.aggression",
    "combat.defend_bias",
    "combat.harassment_bias",
    "combat.preserve_army_bias",
    "scouting.scout_priority",
    "scouting.risk_tolerance",
    "scouting.require_fresh_enemy_observation",
    "squad.main_army_bias",
    "squad.harassment_bias",
    "squad.defense_bias",
    "squad.regroup_bias",
)
"""Bounded semantic axes exposed to neural representation adapters by default."""


@runtime_checkable
class NeuralRepresentationModelAdapter(Protocol):
    """Adapter implemented by a SOTA/neural model runtime."""

    model_name: str

    def predict_representation(
        self,
        observation: "NeuralRepresentationObservation",
    ) -> Mapping[str, object] | "NeuralRepresentationPrediction":
        """Return bounded representation axes, never raw SC2/API actions."""


@dataclass(frozen=True)
class NeuralRepresentationObservation:
    """Model input context for one representation-modulation decision."""

    command_text: str
    game_state: Mapping[str, object] = field(default_factory=dict)
    telemetry: Mapping[str, object] = field(default_factory=dict)
    commander_context: Mapping[str, object] = field(default_factory=dict)
    candidate_axes: tuple[str, ...] = DEFAULT_NEURAL_REPRESENTATION_AXES

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_text", _require_text("command_text", self.command_text))
        for field_name in ("game_state", "telemetry", "commander_context"):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{field_name} must be a mapping.")
            reject_raw_policy_control_keys(value, path=field_name)
            object.__setattr__(self, field_name, dict(value))
        object.__setattr__(
            self,
            "candidate_axes",
            _string_tuple("candidate_axes", self.candidate_axes),
        )

    @classmethod
    def from_provider_request(
        cls,
        request: PolicyModulationProviderRequest,
        *,
        telemetry: Mapping[str, object] | None = None,
        candidate_axes: Sequence[str] = DEFAULT_NEURAL_REPRESENTATION_AXES,
    ) -> "NeuralRepresentationObservation":
        return cls(
            command_text=request.command_text,
            game_state=request.game_state,
            telemetry=telemetry or {},
            commander_context=request.commander_context,
            candidate_axes=tuple(candidate_axes),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "command_text": self.command_text,
            "game_state": dict(self.game_state),
            "telemetry": dict(self.telemetry),
            "commander_context": dict(self.commander_context),
            "candidate_axes": list(self.candidate_axes),
        }


@dataclass(frozen=True)
class NeuralRepresentationPrediction:
    """Validated neural representation output before compiler normalization."""

    goal: str
    representation_axes: Mapping[str, object]
    confidence: float = 0.65
    override_level: PolicyOverrideLevel | str = PolicyOverrideLevel.BIAS
    ttl_seconds: int = 120
    rationale: str = ""
    tags: tuple[str, ...] = ("neural_representation",)
    model_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", _require_text("goal", self.goal))
        if not isinstance(self.representation_axes, Mapping):
            raise ValueError("representation_axes must be a mapping.")
        reject_raw_policy_control_keys(self.representation_axes, path="representation_axes")
        normalized_axes: dict[str, object] = {}
        for key, value in self.representation_axes.items():
            axis = _require_text("representation_axes", key)
            normalized_axes[axis] = value
        object.__setattr__(self, "representation_axes", normalized_axes)
        object.__setattr__(
            self,
            "confidence",
            _coerce_probability("confidence", self.confidence),
        )
        object.__setattr__(self, "override_level", _coerce_override_level(self.override_level))
        if type(self.ttl_seconds) is bool or not isinstance(self.ttl_seconds, int):
            raise TypeError("ttl_seconds must be an integer.")
        if self.ttl_seconds <= 0 or self.ttl_seconds > 900:
            raise ValueError("ttl_seconds must be between 1 and 900.")
        object.__setattr__(self, "tags", _string_tuple("tags", self.tags))
        if self.rationale:
            object.__setattr__(self, "rationale", _require_text("rationale", self.rationale))
        if self.model_name:
            object.__setattr__(self, "model_name", _require_text("model_name", self.model_name))

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, object],
        *,
        default_goal: str,
        default_model_name: str = "",
    ) -> "NeuralRepresentationPrediction":
        reject_raw_policy_control_keys(mapping)
        axes = mapping.get("representation_axes", mapping.get("representation", {}))
        if not isinstance(axes, Mapping):
            raise ValueError("neural representation output requires representation_axes.")
        raw_tags = mapping.get("tags", ("neural_representation",))
        if raw_tags is None:
            raw_tags = ("neural_representation",)
        return cls(
            goal=str(mapping.get("goal") or default_goal),
            representation_axes=axes,
            confidence=mapping.get("confidence", 0.65),  # type: ignore[arg-type]
            override_level=mapping.get("override_level", PolicyOverrideLevel.BIAS),  # type: ignore[arg-type]
            ttl_seconds=mapping.get("ttl_seconds", 120),  # type: ignore[arg-type]
            rationale=str(mapping.get("rationale") or ""),
            tags=tuple(raw_tags) if not isinstance(raw_tags, str) else (raw_tags,),
            model_name=str(mapping.get("model_name") or default_model_name),
        )

    def to_provider_output(self) -> dict[str, object]:
        tags = list(dict.fromkeys((*self.tags, "neural_representation")))
        return {
            "source": PolicyModulationSource.NEURAL_REPRESENTATION.value,
            "goal": self.goal,
            "override_level": self.override_level.value,
            "confidence": self.confidence,
            "ttl_seconds": self.ttl_seconds,
            "representation_axes": dict(self.representation_axes),
            "tags": tags,
            "rationale": self.rationale,
            "model_name": self.model_name,
        }


class NeuralRepresentationProvider(PolicyModulationProviderInterface):
    """Provider wrapper that connects a neural adapter to the compiler seam."""

    source = PolicyModulationSource.NEURAL_REPRESENTATION

    def __init__(
        self,
        adapter: NeuralRepresentationModelAdapter,
        *,
        candidate_axes: Sequence[str] = DEFAULT_NEURAL_REPRESENTATION_AXES,
    ) -> None:
        self.adapter = adapter
        self.candidate_axes = _string_tuple("candidate_axes", candidate_axes)

    def propose_policy_modulation(
        self,
        request: PolicyModulationProviderRequest,
    ) -> Mapping[str, object]:
        observation = NeuralRepresentationObservation.from_provider_request(
            request,
            candidate_axes=self.candidate_axes,
        )
        raw_prediction = self.adapter.predict_representation(observation)
        if isinstance(raw_prediction, NeuralRepresentationPrediction):
            prediction = raw_prediction
        elif isinstance(raw_prediction, Mapping):
            prediction = NeuralRepresentationPrediction.from_mapping(
                raw_prediction,
                default_goal=request.command_text,
                default_model_name=getattr(self.adapter, "model_name", ""),
            )
        else:
            raise ValueError("neural adapter must return a mapping or prediction.")
        return prediction.to_provider_output()


def publish_neural_representation_modulation(
    adapter: NeuralRepresentationModelAdapter,
    request: PolicyModulationProviderRequest,
    backend: MicroMachineModulationBackend,
    *,
    current_frame: int,
    update_id: str | None = None,
    rollback_update_id: str | None = None,
    candidate_axes: Sequence[str] = DEFAULT_NEURAL_REPRESENTATION_AXES,
) -> MicroMachineBackendPublishResult:
    """Infer neural axes, compile them safely, and publish through a backend."""

    provider = NeuralRepresentationProvider(adapter, candidate_axes=candidate_axes)
    compile_result = compile_policy_modulation_from_provider(provider, request)
    if not compile_result.ok or compile_result.vector is None:
        return MicroMachineBackendPublishResult(compile_result=compile_result)
    update = backend.publish_vector(
        compile_result.vector,
        current_frame=current_frame,
        update_id=update_id,
        rollback_update_id=rollback_update_id,
    )
    return MicroMachineBackendPublishResult(compile_result=compile_result, update=update)


class StaticNeuralRepresentationAdapter:
    """Deterministic adapter for tests, dry-runs, and offline model fixtures."""

    def __init__(
        self,
        prediction: Mapping[str, object] | NeuralRepresentationPrediction,
        *,
        model_name: str = "static-neural-representation-fixture",
    ) -> None:
        self.prediction = prediction
        self.model_name = _require_text("model_name", model_name)
        self.observations: list[NeuralRepresentationObservation] = []

    def predict_representation(
        self,
        observation: NeuralRepresentationObservation,
    ) -> Mapping[str, object] | NeuralRepresentationPrediction:
        self.observations.append(observation)
        return self.prediction


def _coerce_override_level(value: PolicyOverrideLevel | str) -> PolicyOverrideLevel:
    if isinstance(value, PolicyOverrideLevel):
        return value
    if type(value) is not str:
        raise ValueError("override_level must be a string.")
    try:
        return PolicyOverrideLevel(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported override_level: {value!r}.") from exc


def _coerce_probability(name: str, value: object) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0.")
    return number


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
