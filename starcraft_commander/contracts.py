"""Semantic StarCraft II command-plan and execution-result contracts.

These contracts are intentionally independent of ToyCraft simulation types and
python-sc2 runtime imports. They describe the stable public boundary used by
planners, API responses, logs, and BotAI-style runtime adapters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final


class SC2ActionType(str, Enum):
    """Stable semantic StarCraft II action categories."""

    ASSIGN_WORKERS = "assign_workers"
    BUILD_STRUCTURE = "build_structure"
    TRAIN_UNIT = "train_unit"
    MOVE_GROUP = "move_group"
    ATTACK_MOVE = "attack_move"
    REPAIR = "repair"
    OBSERVE = "observe"
    MOVE_CAMERA = "move_camera"


SC2_PRIORITY_VALUES: Final[dict[str, int]] = {
    "low": 25,
    "normal": 50,
    "high": 75,
    "urgent": 100,
}
"""Numeric priority values used by JSON command-plan contracts."""

SC2_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    action_type.value for action_type in SC2ActionType
)
"""Stable public semantic SC2 action type values."""


@dataclass(frozen=True)
class SC2CommandAction:
    """One semantic SC2 API command derived from a commander intent."""

    action_type: SC2ActionType
    subject: str
    target: str = ""
    count: int = 1
    priority: int = SC2_PRIORITY_VALUES["normal"]
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        action_type = _coerce_action_type(
            self.action_type,
            field_name="SC2 command action action_type",
        )
        if not self.subject.strip():
            raise ValueError("SC2 command action subject must be non-empty.")
        if self.count < 0:
            raise ValueError("SC2 command action count cannot be negative.")
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "constraints", tuple(str(item) for item in self.constraints))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready semantic action payload."""

        return {
            "action_type": self.action_type.value,
            "subject": self.subject,
            "target": self.target,
            "count": self.count,
            "priority": self.priority,
            "constraints": list(self.constraints),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, init=False)
class SC2ExecutionPlan:
    """Ordered semantic command plan for a StarCraft II runtime adapter."""

    intent_name: str
    priority: str
    priority_value: int
    ordered_actions: tuple[SC2CommandAction, ...]
    constraints: tuple[str, ...]
    requires_live_sc2: bool
    notes: tuple[str, ...]
    audit: Mapping[str, object]

    def __init__(
        self,
        intent_name: str | None = None,
        priority: str | int = "normal",
        ordered_actions: Sequence[SC2CommandAction] | None = None,
        constraints: Sequence[str] = (),
        requires_live_sc2: bool = True,
        notes: Sequence[str] = (),
        audit: Mapping[str, object] | None = None,
        *,
        intent: str | None = None,
        actions: Sequence[SC2CommandAction] | None = None,
        priority_value: int | None = None,
    ) -> None:
        resolved_intent = intent_name if intent_name is not None else intent
        if resolved_intent is None or not resolved_intent.strip():
            raise ValueError("SC2 execution plan intent_name must be non-empty.")

        resolved_actions = ordered_actions if ordered_actions is not None else actions
        if resolved_actions is None:
            raise ValueError("SC2 execution plan ordered_actions must be provided.")

        priority_label, resolved_priority_value = _coerce_priority(priority, priority_value)
        ordered = tuple(resolved_actions)
        resolved_constraints = tuple(str(item) for item in constraints)
        action_constraints = resolved_constraints
        ordered = tuple(
            _with_plan_defaults(action, resolved_priority_value, action_constraints)
            for action in ordered
        )

        object.__setattr__(self, "intent_name", resolved_intent)
        object.__setattr__(self, "priority", priority_label)
        object.__setattr__(self, "priority_value", resolved_priority_value)
        object.__setattr__(self, "ordered_actions", ordered)
        object.__setattr__(self, "constraints", resolved_constraints)
        object.__setattr__(self, "requires_live_sc2", bool(requires_live_sc2))
        object.__setattr__(self, "notes", tuple(str(item) for item in notes))
        object.__setattr__(self, "audit", dict(audit or {}))

    @property
    def intent(self) -> str:
        """Backward-compatible alias for the canonical intent name."""

        return self.intent_name

    @property
    def actions(self) -> tuple[SC2CommandAction, ...]:
        """Backward-compatible alias for the ordered semantic actions."""

        return self.ordered_actions

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready execution plan."""

        ordered_actions = [action.to_dict() for action in self.ordered_actions]
        return {
            "intent_name": self.intent_name,
            "intent": self.intent_name,
            "priority": self.priority_value,
            "priority_label": self.priority,
            "constraints": list(self.constraints),
            "requires_live_sc2": self.requires_live_sc2,
            "notes": list(self.notes),
            "ordered_actions": ordered_actions,
            "actions": ordered_actions,
            "audit": dict(self.audit),
        }


SC2CommandPlan = SC2ExecutionPlan
"""Public semantic command-plan alias."""


@dataclass(frozen=True)
class SC2ActionReport:
    """Structured per-action outcome reported by runtime adapters.

    ``requested_count`` is what the commander asked for and ``issued_count``
    is how many real orders actually went out. Adapters report both so the
    executor and narrator can surface partial issuance honestly instead of
    collapsing it into an unqualified boolean success. ``bool(report)`` is
    ``True`` only for a full, shortfall-free application, so legacy callers
    that treat the adapter result as a boolean never overclaim.
    """

    applied: bool
    requested_count: int | None = None
    issued_count: int | None = None
    detail: str = ""
    audit: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "applied", bool(self.applied))
        for field_name in ("requested_count", "issued_count"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if type(value) is bool or not isinstance(value, int):
                raise TypeError(
                    f"SC2 action report {field_name} must be an int or None."
                )
            if value < 0:
                raise ValueError(
                    f"SC2 action report {field_name} cannot be negative."
                )
        object.__setattr__(self, "detail", str(self.detail))
        object.__setattr__(self, "audit", dict(self.audit))

    @property
    def is_partial(self) -> bool:
        """Return whether fewer orders were issued than were requested."""

        return (
            self.applied
            and self.requested_count is not None
            and self.issued_count is not None
            and self.issued_count < self.requested_count
        )

    def __bool__(self) -> bool:
        """Truthy only when fully applied without an issuance shortfall."""

        return self.applied and not self.is_partial

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready per-action report payload."""

        return {
            "applied": self.applied,
            "requested_count": self.requested_count,
            "issued_count": self.issued_count,
            "is_partial": self.is_partial,
            "detail": self.detail,
            "audit": dict(self.audit),
        }


@dataclass(frozen=True)
class SC2ExecutionError:
    """Structured runtime or planning error captured without crashing."""

    message: str
    action_type: SC2ActionType | None = None
    action_index: int | None = None
    exception_type: str | None = None
    recoverable: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        action_type = _coerce_optional_action_type(
            self.action_type,
            field_name="SC2 execution error action_type",
        )
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready structured error."""

        return {
            "message": self.message,
            "action_type": self.action_type.value if self.action_type else None,
            "action_index": self.action_index,
            "exception_type": self.exception_type,
            "recoverable": self.recoverable,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, init=False)
class SC2PlanExecutionResult:
    """Structured result from applying an SC2 command plan."""

    plan: SC2ExecutionPlan
    attempted_actions: tuple[SC2CommandAction, ...]
    applied_actions: tuple[SC2CommandAction, ...]
    skipped_actions: tuple[SC2CommandAction, ...]
    errors: tuple[SC2ExecutionError, ...]
    audit: Mapping[str, object]

    def __init__(
        self,
        plan: SC2ExecutionPlan,
        attempted_actions: Sequence[SC2CommandAction] | None = None,
        applied_actions: Sequence[SC2CommandAction] | None = None,
        skipped_actions: Sequence[SC2CommandAction] = (),
        errors: Sequence[SC2ExecutionError | str] = (),
        audit: Mapping[str, object] | None = None,
        *,
        attempted: Sequence[SC2CommandAction] | None = None,
        applied: Sequence[SC2CommandAction] | None = None,
        skipped: Sequence[SC2CommandAction] | None = None,
    ) -> None:
        resolved_attempted = attempted_actions if attempted_actions is not None else attempted
        resolved_applied = applied_actions if applied_actions is not None else applied
        resolved_skipped = skipped if skipped is not None else skipped_actions
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "attempted_actions", tuple(resolved_attempted or ()))
        object.__setattr__(self, "applied_actions", tuple(resolved_applied or ()))
        object.__setattr__(self, "skipped_actions", tuple(resolved_skipped or ()))
        object.__setattr__(self, "errors", tuple(_coerce_error(error) for error in errors))
        object.__setattr__(self, "audit", dict(audit or {}))

    @property
    def attempted(self) -> tuple[SC2CommandAction, ...]:
        """Canonical alias for attempted actions."""

        return self.attempted_actions

    @property
    def applied(self) -> tuple[SC2CommandAction, ...]:
        """Canonical alias for applied actions."""

        return self.applied_actions

    @property
    def skipped(self) -> tuple[SC2CommandAction, ...]:
        """Canonical alias for skipped actions."""

        return self.skipped_actions

    @property
    def success(self) -> bool:
        """Return whether every planned action was applied without skips/errors."""

        return (
            not self.errors
            and not self.skipped_actions
            and bool(self.plan.ordered_actions)
            and len(self.applied_actions) == len(self.plan.ordered_actions)
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready runtime result."""

        attempted = [action.to_dict() for action in self.attempted_actions]
        applied = [action.to_dict() for action in self.applied_actions]
        skipped = [action.to_dict() for action in self.skipped_actions]
        return {
            "success": self.success,
            "plan": self.plan.to_dict(),
            "attempted": attempted,
            "applied": applied,
            "skipped": skipped,
            "attempted_actions": attempted,
            "applied_actions": applied,
            "skipped_actions": skipped,
            "errors": [error.to_dict() for error in self.errors],
            "audit": dict(self.audit),
        }


def _coerce_priority(priority: str | int, priority_value: int | None) -> tuple[str, int]:
    if isinstance(priority, int):
        label = "custom"
        value = priority
    else:
        label = priority
        if not label.strip():
            raise ValueError("SC2 execution plan priority must be non-empty.")
        try:
            value = SC2_PRIORITY_VALUES[label]
        except KeyError as exc:
            supported = ", ".join(SC2_PRIORITY_VALUES)
            raise ValueError(
                f"SC2 execution plan priority must be one of: {supported}. "
                f"Unknown priority: {label!r}."
            ) from exc
    if priority_value is not None:
        value = int(priority_value)
    return label, int(value)


def _with_plan_defaults(
    action: SC2CommandAction,
    priority: int,
    constraints: tuple[str, ...],
) -> SC2CommandAction:
    if action.priority != SC2_PRIORITY_VALUES["normal"] or action.constraints:
        return action
    return SC2CommandAction(
        action_type=action.action_type,
        subject=action.subject,
        target=action.target,
        count=action.count,
        priority=priority,
        constraints=constraints,
        metadata=action.metadata,
    )


def _coerce_error(error: SC2ExecutionError | str) -> SC2ExecutionError:
    if isinstance(error, SC2ExecutionError):
        return error
    return SC2ExecutionError(message=str(error))


def _coerce_optional_action_type(
    action_type: SC2ActionType | str | None,
    *,
    field_name: str,
) -> SC2ActionType | None:
    if action_type is None:
        return None
    return _coerce_action_type(action_type, field_name=field_name)


def _coerce_action_type(
    action_type: SC2ActionType | str,
    *,
    field_name: str,
) -> SC2ActionType:
    if isinstance(action_type, SC2ActionType):
        return action_type
    if isinstance(action_type, str):
        try:
            return SC2ActionType(action_type)
        except ValueError as exc:
            supported = ", ".join(sorted(SC2_ACTION_TYPES))
            raise ValueError(
                f"{field_name} must be one of: {supported}. "
                f"Unknown action_type: {action_type!r}."
            ) from exc
    raise TypeError(f"{field_name} must be a SC2ActionType or string.")


def first_or_none(items: Sequence[Any]) -> Any | None:
    """Return the first item from a sequence-like object, or ``None``."""

    return items[0] if items else None
