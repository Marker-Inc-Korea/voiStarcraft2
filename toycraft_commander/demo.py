"""Runnable Korean natural-language demo for Phase 0 ToyCraft Commander."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from toycraft_commander.executor import (
    ToyCraftExecutionResult,
    advance_toycraft_time,
    build_commander_response,
    execute_toycraft_intent,
    summarize_toycraft_state,
)
from toycraft_commander.feasibility import ToyCraftState
from toycraft_commander.interpreter import CommandInterpretationResult, interpret_command
from toycraft_commander.resources import ResourceState, SupplyState


@dataclass(frozen=True)
class KoreanDemoCommand:
    """One commander utterance plus optional deterministic simulator progress."""

    command_text: str
    advance_seconds_after: int = 0

    def __post_init__(self) -> None:
        if not self.command_text.strip():
            raise ValueError("command_text must be non-empty.")
        if self.advance_seconds_after < 0:
            raise ValueError("advance_seconds_after cannot be negative.")


DEFAULT_KOREAN_DEMO_COMMANDS: tuple[KoreanDemoCommand, ...] = (
    KoreanDemoCommand("상태 알려줘"),
    KoreanDemoCommand("미네랄에 일꾼 세 기 붙여"),
    KoreanDemoCommand("가스에 SCV 하나 붙여"),
    KoreanDemoCommand("일꾼 계속 찍어", advance_seconds_after=20),
    KoreanDemoCommand("인구 막히기 전에 서플 하나 지어", advance_seconds_after=30),
    KoreanDemoCommand("마린 계속 뽑아", advance_seconds_after=24),
    KoreanDemoCommand("입구 막아"),
    KoreanDemoCommand("앞마당 가져가"),
    KoreanDemoCommand("마린 두 기로 적 미네랄 라인 견제해"),
    KoreanDemoCommand("현재 상황 요약해"),
)
"""Five-to-seven-minute Phase 0 demo path using Korean commander inputs."""


def build_demo_initial_state() -> ToyCraftState:
    """Return a demo-ready early Terran state with coherent RTS affordances."""

    return ToyCraftState(
        resources=ResourceState(minerals=900, gas=100),
        supply=SupplyState(used_supply=12, supply_capacity=23),
        units={"SCV": 10, "Marine": 6, "Vulture": 1},
        structures={
            "Command Center": 1,
            "Supply Depot": 1,
            "Barracks": 1,
            "Refinery": 1,
            "Bunker": 1,
        },
        claimed_locations=("main", "main base"),
        damaged_targets=("front bunker",),
    )


def run_korean_demo(
    commands: Iterable[KoreanDemoCommand] = DEFAULT_KOREAN_DEMO_COMMANDS,
    *,
    initial_state: ToyCraftState | None = None,
) -> str:
    """Run the Korean text commander demo and return a printable transcript."""

    state = initial_state or build_demo_initial_state()
    lines = [
        "ToyCraft Commander Phase 0 Korean Demo",
        "입력은 한국어 자연어 명령이고, 출력은 typed Intent DSL과 ToyCraft 내레이션입니다.",
        "",
    ]

    for index, command in enumerate(tuple(commands), start=1):
        interpretation = interpret_command(command.command_text)
        lines.append(f"{index}. Commander: {command.command_text}")
        if interpretation.payload is None:
            lines.extend(_format_blocked_interpretation(interpretation))
            lines.append("")
            continue

        result = execute_toycraft_intent(interpretation.payload, state)
        lines.extend(
            _format_execution_result(
                command_text=command.command_text,
                result=result,
            )
        )
        state = result.after_state
        if command.advance_seconds_after:
            time_result = advance_toycraft_time(state, command.advance_seconds_after)
            lines.extend(_format_time_advance(command.advance_seconds_after, time_result))
            state = time_result.after_state
        lines.append("")

    summary = summarize_toycraft_state(state)
    lines.extend(
        (
            "Final ToyCraft State",
            f"- resources: {summary['resources']}",
            f"- supply: {summary['supply']}",
            f"- units: {summary['units']}",
            f"- structures: {summary['structures']}",
            f"- claimed_locations: {summary['claimed_locations']}",
        )
    )
    return "\n".join(lines)


def main() -> None:
    """Console entrypoint for `python -m toycraft_commander.demo`."""

    print(run_korean_demo())


def _format_blocked_interpretation(
    interpretation: CommandInterpretationResult,
) -> list[str]:
    return [
        "- status: blocked_before_validation",
        f"- reason: {interpretation.reason}",
        f"- alternative: {', '.join(interpretation.alternatives)}",
        f"- narration: {interpretation.clarification_prompt}",
    ]


def _format_execution_result(
    *,
    command_text: str,
    result: ToyCraftExecutionResult,
) -> list[str]:
    if result.validation.payload is None:
        dsl_document: dict[str, object] = {
            "command_text": command_text,
            "intent_dsl": {"intent": result.intent},
        }
    else:
        dsl_document = CommandInterpretationResult(
            command_text=command_text,
            payload=result.validation.payload,
        ).to_dsl_document()
    response = build_commander_response(result, command_text=command_text)
    lines = [
        "- Intent DSL:",
        _indent(json.dumps(dsl_document, ensure_ascii=False, indent=2), prefix="  "),
        f"- executed: {result.executed}",
        f"- narration: {response}",
    ]
    if result.state_delta.raw_changes:
        lines.append(f"- state_changes: {', '.join(result.state_delta.raw_changes)}")
    return lines


def _format_time_advance(seconds: int, result: ToyCraftExecutionResult) -> list[str]:
    lines = [
        f"- ToyCraft time +{seconds}s: {result.narration}",
    ]
    if result.state_delta.raw_changes:
        lines.append(f"- time_changes: {', '.join(result.state_delta.raw_changes)}")
    return lines


def _indent(text: str, *, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())


if __name__ == "__main__":
    main()
