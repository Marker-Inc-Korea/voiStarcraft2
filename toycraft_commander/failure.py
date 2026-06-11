"""Structured failure reports for the ToyCraft commander pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from toycraft_commander.compat import StrEnum
from toycraft_commander.intents import (
    FeasibilityErrorReason,
    FeasibilityIssue,
    IntentValidationResult,
)

DEFAULT_ACTIONABLE_ALTERNATIVE = (
    "명령을 하나로 구체화해 다시 내려 주세요. "
    "예: 상태 알려줘 / 일꾼 계속 찍어 / 본진에 배럭 지어."
)


class CommandFailureStage(StrEnum):
    """Pipeline boundary where a command stopped before state mutation."""

    PARSING = "parsing"
    VALIDATION = "validation"
    RULE_EXECUTION = "rule_execution"


@dataclass(frozen=True)
class CommandFailureReason:
    """One machine-readable reason for a blocked commander command."""

    stage: CommandFailureStage
    code: str
    message: str
    alternative: str
    fields: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        stage = self.stage
        if isinstance(stage, str):
            stage = CommandFailureStage(stage)
        code = _string_code(self.code)
        if not code.strip():
            raise ValueError("failure reason code must be non-empty.")
        if not self.message.strip():
            raise ValueError("failure reason message must be non-empty.")
        if not self.alternative.strip():
            raise ValueError("failure reason alternative must be non-empty.")

        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready reason payload for UI, logs, and tests."""

        return {
            "stage": self.stage.value,
            "code": self.code,
            "message": self.message,
            "alternative": self.alternative,
            "fields": list(self.fields),
            "metadata": _json_ready_mapping(self.metadata),
        }


@dataclass(frozen=True)
class CommandFailureReport:
    """Structured report for a command that stopped before execution."""

    stage: CommandFailureStage
    reasons: tuple[CommandFailureReason, ...]
    command_text: str = ""
    intent: str = "UNKNOWN"
    executed: bool = False
    state_mutated: bool = False

    def __post_init__(self) -> None:
        stage = self.stage
        if isinstance(stage, str):
            stage = CommandFailureStage(stage)
        reasons = tuple(self.reasons)
        if not reasons:
            raise ValueError("failure reports require at least one reason.")
        if self.executed:
            raise ValueError("failure reports cannot be marked executed.")
        if self.state_mutated:
            raise ValueError("failure reports cannot mutate game state.")
        if not self.intent.strip():
            raise ValueError("failure report intent must be non-empty.")
        for reason in reasons:
            if reason.stage != stage:
                raise ValueError("failure reason stage must match report stage.")

        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "reasons", reasons)

    @property
    def primary_reason(self) -> CommandFailureReason:
        """Return the first failure reason in evaluation order."""

        return self.reasons[0]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Return all reason codes in evaluation order."""

        return tuple(reason.code for reason in self.reasons)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready failure report."""

        return {
            "stage": self.stage.value,
            "command_text": self.command_text,
            "intent": self.intent,
            "executed": self.executed,
            "state_mutated": self.state_mutated,
            "reason_codes": list(self.reason_codes),
            "reasons": [reason.to_dict() for reason in self.reasons],
        }


def build_parsing_failure_report(
    *,
    command_text: str,
    code: str | Enum,
    message: str,
    alternatives: tuple[str, ...],
    intent: str = "UNKNOWN",
    metadata: Mapping[str, object] | None = None,
) -> CommandFailureReport:
    """Build a structured report for a command that failed interpretation."""

    alternative = " / ".join(alternatives) if alternatives else DEFAULT_ACTIONABLE_ALTERNATIVE
    reason_metadata = {"alternatives": list(alternatives)}
    if metadata:
        reason_metadata.update(metadata)
    reason = CommandFailureReason(
        stage=CommandFailureStage.PARSING,
        code=_string_code(code),
        message=message,
        alternative=alternative,
        metadata=reason_metadata,
    )
    return CommandFailureReport(
        stage=CommandFailureStage.PARSING,
        reasons=(reason,),
        command_text=command_text,
        intent=intent,
    )


def build_validation_failure_report(
    validation: IntentValidationResult,
    *,
    command_text: str = "",
    intent: str = "UNKNOWN",
    stage: CommandFailureStage = CommandFailureStage.VALIDATION,
) -> CommandFailureReport:
    """Build a structured failure report from a rejected validation result."""

    if not isinstance(validation, IntentValidationResult):
        raise TypeError("validation must be an IntentValidationResult.")
    if validation.executable:
        raise ValueError("failure reports require rejected validation.")

    payload = validation.payload
    if payload is not None:
        intent = payload.intent

    issues = validation.issues or (
        FeasibilityIssue(
            reason=FeasibilityErrorReason.MALFORMED_PAYLOAD,
            message=validation.reason or "Command is not executable.",
            alternative=validation.alternative or DEFAULT_ACTIONABLE_ALTERNATIVE,
            fields=validation.missing_fields,
        ),
    )
    reasons = tuple(
        CommandFailureReason(
            stage=stage,
            code=issue.reason,
            message=issue.message,
            alternative=issue.alternative,
            fields=issue.fields,
        )
        for issue in issues
    )
    return CommandFailureReport(
        stage=stage,
        reasons=reasons,
        command_text=command_text,
        intent=intent,
    )


def _string_code(code: str | Enum) -> str:
    if isinstance(code, Enum):
        return str(code.value)
    return str(code)


def _json_ready_mapping(mapping: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in mapping.items():
        if isinstance(value, tuple):
            payload[key] = list(value)
        elif isinstance(value, Mapping):
            payload[key] = _json_ready_mapping(value)
        else:
            payload[key] = value
    return payload
