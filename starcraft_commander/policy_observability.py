"""Observability and evaluation contracts for issue #10 policy modulation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from starcraft_commander.micromachine_bridge import (
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
)
from starcraft_commander.policy_modulation import reject_raw_policy_control_keys


class PolicyModulationBridgeStatus(str, Enum):
    """Dashboard-safe bridge availability states."""

    SIMULATED = "simulated"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class ModulationEvaluationMetricKey(str, Enum):
    """Required baseline-vs-modulated evaluation metrics for issue #10."""

    WIN_LOSS = "win_loss"
    CRASH_RATE = "crash_rate"
    INTENT_COMPLIANCE = "intent_compliance"
    INTERVENTION_LATENCY_MS = "intervention_latency_ms"


REQUIRED_EVALUATION_METRICS: Final[frozenset[str]] = frozenset(
    metric.value for metric in ModulationEvaluationMetricKey
)


@dataclass(frozen=True)
class PolicyModulationDashboardSnapshot:
    """JSON-ready dashboard snapshot for active policy modulation state."""

    generated_at_frame: int
    bridge_status: PolicyModulationBridgeStatus | str = (
        PolicyModulationBridgeStatus.SIMULATED
    )
    active_updates: tuple[MicroMachineBlackboardUpdate, ...] = ()
    stale_update_ids: tuple[str, ...] = ()
    telemetry: MicroMachineTelemetry | None = None
    last_failure: MicroMachineBridgeFailureMode | str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "generated_at_frame",
            _non_negative_int("generated_at_frame", self.generated_at_frame),
        )
        object.__setattr__(
            self,
            "bridge_status",
            _coerce_bridge_status(self.bridge_status),
        )
        updates = tuple(self.active_updates)
        for update in updates:
            if not isinstance(update, MicroMachineBlackboardUpdate):
                raise ValueError("active_updates must contain blackboard updates.")
        object.__setattr__(self, "active_updates", updates)
        object.__setattr__(
            self,
            "stale_update_ids",
            _string_tuple("stale_update_ids", self.stale_update_ids),
        )
        if self.telemetry is not None and not isinstance(self.telemetry, MicroMachineTelemetry):
            raise ValueError("telemetry must be a MicroMachineTelemetry or None.")
        failure = self.last_failure
        if failure is not None:
            failure = _coerce_failure_mode(failure)
        object.__setattr__(self, "last_failure", failure)
        object.__setattr__(self, "notes", _string_tuple("notes", self.notes))

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at_frame": self.generated_at_frame,
            "bridge_status": self.bridge_status.value,
            "active_modulation_count": len(self.active_updates),
            "active_updates": [update.to_dict() for update in self.active_updates],
            "stale_update_ids": list(self.stale_update_ids),
            "telemetry": self.telemetry.to_dict() if self.telemetry else None,
            "last_failure": self.last_failure.value if self.last_failure else None,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ModulationEvaluationMetric:
    """One measurable comparison metric for baseline vs modulated bots."""

    key: ModulationEvaluationMetricKey | str
    description: str
    aggregation: str
    desired_direction: str
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _coerce_metric_key(self.key))
        for field_name in ("description", "aggregation", "desired_direction"):
            object.__setattr__(
                self,
                field_name,
                _require_text(field_name, getattr(self, field_name)),
            )
        object.__setattr__(self, "required", bool(self.required))

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key.value,
            "description": self.description,
            "aggregation": self.aggregation,
            "desired_direction": self.desired_direction,
            "required": self.required,
        }


@dataclass(frozen=True)
class MicroMachineModulationEvaluationPlan:
    """Baseline-vs-modulated evaluation contract for issue #10."""

    baseline_bot: str = "MicroMachine baseline"
    modulated_bot: str = "MicroMachine + voi policy modulation"
    minimum_games_per_matchup: int = 30
    metrics: tuple[ModulationEvaluationMetric, ...] = field(default_factory=tuple)
    safety_gates: tuple[str, ...] = (
        "no raw SC2 API actions from model/provider output",
        "no bridge crash on invalid provider payload",
        "emergency rollback remains available",
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "baseline_bot",
            _require_text("baseline_bot", self.baseline_bot),
        )
        object.__setattr__(
            self,
            "modulated_bot",
            _require_text("modulated_bot", self.modulated_bot),
        )
        object.__setattr__(
            self,
            "minimum_games_per_matchup",
            _positive_int("minimum_games_per_matchup", self.minimum_games_per_matchup),
        )
        metrics = tuple(self.metrics) or default_modulation_evaluation_metrics()
        observed = {metric.key.value for metric in metrics}
        missing = REQUIRED_EVALUATION_METRICS - observed
        if missing:
            raise ValueError(
                "evaluation plan missing required metrics: "
                + ", ".join(sorted(missing))
            )
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(
            self,
            "safety_gates",
            _string_tuple("safety_gates", self.safety_gates),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_bot": self.baseline_bot,
            "modulated_bot": self.modulated_bot,
            "minimum_games_per_matchup": self.minimum_games_per_matchup,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "safety_gates": list(self.safety_gates),
        }


def build_policy_modulation_dashboard_snapshot(
    updates: Sequence[MicroMachineBlackboardUpdate] = (),
    *,
    current_frame: int,
    bridge_status: PolicyModulationBridgeStatus | str = PolicyModulationBridgeStatus.SIMULATED,
    telemetry: MicroMachineTelemetry | None = None,
    last_failure: MicroMachineBridgeFailureMode | str | None = None,
    notes: Sequence[str] = (),
) -> PolicyModulationDashboardSnapshot:
    """Build a dashboard snapshot and separate active updates from stale ones."""

    frame = _non_negative_int("current_frame", current_frame)
    active_updates: list[MicroMachineBlackboardUpdate] = []
    stale_update_ids: list[str] = []
    for update in updates:
        if not isinstance(update, MicroMachineBlackboardUpdate):
            raise ValueError("updates must contain MicroMachineBlackboardUpdate items.")
        if update.is_stale(frame):
            stale_update_ids.append(update.update_id)
        else:
            active_updates.append(update)
    return PolicyModulationDashboardSnapshot(
        generated_at_frame=frame,
        bridge_status=bridge_status,
        active_updates=tuple(active_updates),
        stale_update_ids=tuple(stale_update_ids),
        telemetry=telemetry,
        last_failure=last_failure,
        notes=tuple(notes),
    )


def default_modulation_evaluation_metrics() -> tuple[ModulationEvaluationMetric, ...]:
    """Return the required issue #10 evaluation metric set."""

    return (
        ModulationEvaluationMetric(
            key=ModulationEvaluationMetricKey.WIN_LOSS,
            description="Win/loss delta versus unmodulated MicroMachine.",
            aggregation="matchup win rate and confidence interval",
            desired_direction="non-regression or improvement",
        ),
        ModulationEvaluationMetric(
            key=ModulationEvaluationMetricKey.CRASH_RATE,
            description="Bot, sidecar, and provider crash rate.",
            aggregation="crashes per game and disconnect count",
            desired_direction="lower is better",
        ),
        ModulationEvaluationMetric(
            key=ModulationEvaluationMetricKey.INTENT_COMPLIANCE,
            description="Whether modulated play follows the user's stated intent.",
            aggregation="human/replay rubric score from 0.0 to 1.0",
            desired_direction="higher is better",
        ),
        ModulationEvaluationMetric(
            key=ModulationEvaluationMetricKey.INTERVENTION_LATENCY_MS,
            description="Time from user/provider modulation to active blackboard update.",
            aggregation="p50/p95 milliseconds",
            desired_direction="lower is better",
        ),
    )


def build_issue10_evaluation_plan() -> MicroMachineModulationEvaluationPlan:
    """Return the default baseline-vs-modulated MicroMachine evaluation plan."""

    return MicroMachineModulationEvaluationPlan(
        metrics=default_modulation_evaluation_metrics()
    )


def validate_dashboard_snapshot_payload(payload: Mapping[str, object]) -> None:
    """Validate a dashboard payload as JSON-safe and raw-control-free."""

    if not isinstance(payload, Mapping):
        raise ValueError("dashboard snapshot payload must be a mapping.")
    reject_raw_policy_control_keys(payload)


def _coerce_bridge_status(
    value: PolicyModulationBridgeStatus | str,
) -> PolicyModulationBridgeStatus:
    if isinstance(value, PolicyModulationBridgeStatus):
        return value
    if type(value) is not str:
        raise ValueError("bridge_status must be a string.")
    try:
        return PolicyModulationBridgeStatus(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported bridge status: {value!r}.") from exc


def _coerce_metric_key(
    value: ModulationEvaluationMetricKey | str,
) -> ModulationEvaluationMetricKey:
    if isinstance(value, ModulationEvaluationMetricKey):
        return value
    if type(value) is not str:
        raise ValueError("metric key must be a string.")
    try:
        return ModulationEvaluationMetricKey(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported evaluation metric: {value!r}.") from exc


def _coerce_failure_mode(
    value: MicroMachineBridgeFailureMode | str,
) -> MicroMachineBridgeFailureMode:
    if isinstance(value, MicroMachineBridgeFailureMode):
        return value
    if type(value) is not str:
        raise ValueError("failure mode must be a string.")
    try:
        return MicroMachineBridgeFailureMode(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported failure mode: {value!r}.") from exc


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer.")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return value


def _positive_int(field_name: str, value: object) -> int:
    number = _non_negative_int(field_name, value)
    if number == 0:
        raise ValueError(f"{field_name} must be positive.")
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
