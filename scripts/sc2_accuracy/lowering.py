"""Voice-DSL -> Direct-action lowering helpers shared by the SC2 accuracy / doc-contract tests.

Every helper goes through the product's REAL path (provider payload -> policy compiler ->
``route_policy_vector`` -> ``direct_plan``); nothing here builds raw protobufs by hand.
"""

from __future__ import annotations

from starcraft_commander.contracts import SC2CommandAction
from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output
from starcraft_commander.realtime_intent import match_fast_path, tool_arguments_to_provider_output
from starcraft_commander.unified_command_router import route_policy_vector


def _plan_actions(output) -> tuple[SC2CommandAction, ...]:
    result = compile_policy_modulation_provider_output(output)
    assert result.vector is not None, result
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None
    return tuple(plan.ordered_actions)


def lower_micro(ability: str, unit_classes: list[str] | None = None) -> tuple[SC2CommandAction, ...]:
    item: dict[str, object] = {"ability": ability}
    if unit_classes is not None:
        item["unit_classes"] = unit_classes
    return _plan_actions(tool_arguments_to_provider_output({"goal": f"micro {ability}", "micro": [item]}))


def lower_direct(task_type: str, production_targets: list[str] | None = None, **task) -> tuple[SC2CommandAction, ...]:
    payload = {
        "goal": f"direct {task_type}",
        "command_layer": "operation",
        "tactical_task": {"task_type": task_type, "production_targets": production_targets or [], **task},
    }
    result = compile_policy_modulation_provider_output(payload)
    assert result.vector is not None, result
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None
    return tuple(plan.ordered_actions)


def lower_emergency(**flags: bool) -> tuple[SC2CommandAction, ...]:
    return _plan_actions(tool_arguments_to_provider_output({"goal": "emergency", "command_layer": "emergency", "emergency": flags}))


def lower_spoken(transcript: str) -> tuple[SC2CommandAction, ...]:
    """The deterministic (no-LLM) fast path the user's voice takes for 후퇴/홀드/멈춰/스팀팩/시즈."""

    match = match_fast_path(transcript)
    assert match is not None, f"fast path abstained on {transcript!r}"
    return _plan_actions(tool_arguments_to_provider_output(match.arguments))
