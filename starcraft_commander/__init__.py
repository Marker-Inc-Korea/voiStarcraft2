"""Real StarCraft commander execution surfaces.

The project keeps ToyCraft only as an offline test harness. The semantic SC2
contracts are importable without ToyCraft, StarCraft II, or python-sc2. Every
other surface (planner, runtime executor, state/map resolvers, BotAI adapter,
feasibility validator, narrator, live pipeline, voice input, and dependency
guards) is loaded lazily on first attribute access so importing the package
itself never pulls ToyCraft or optional runtime dependencies.
"""

from __future__ import annotations

import importlib
from typing import Any, Final

from starcraft_commander.contracts import (
    SC2_ACTION_TYPES,
    SC2ActionReport,
    SC2ActionType,
    SC2CommandAction,
    SC2CommandPlan,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)

_LAZY_EXPORTS: Final[dict[str, str]] = {
    # Planner / runtime executor surfaces.
    "DEFAULT_SC2_ACTION_PLANNER": "starcraft_commander.sc2_executor",
    "SC2ActionPlanner": "starcraft_commander.sc2_executor",
    "SC2ActionPlannerInterface": "starcraft_commander.sc2_executor",
    "SC2ExecutorBoundaryInterface": "starcraft_commander.sc2_executor",
    "SC2_INTENT_ACTION_TYPE_MAP": "starcraft_commander.sc2_executor",
    "SC2_SEMANTIC_TARGET_NAMES": "starcraft_commander.sc2_executor",
    "SC2_TARGET_ALIASES": "starcraft_commander.sc2_executor",
    "SC2RuntimeExecutor": "starcraft_commander.sc2_executor",
    "SC2RuntimeExecutorInterface": "starcraft_commander.sc2_executor",
    "build_sc2_execution_plan": "starcraft_commander.sc2_executor",
    # Commander state resolution.
    "DEFAULT_SC2_STATE_RESOLVER": "starcraft_commander.state_resolver",
    "SC2CommanderState": "starcraft_commander.state_resolver",
    "SC2StateResolver": "starcraft_commander.state_resolver",
    "SC2StateResolverInterface": "starcraft_commander.state_resolver",
    "resolve_commander_state": "starcraft_commander.state_resolver",
    # Semantic map resolution.
    "MapBaseCluster": "starcraft_commander.map_resolver",
    "MapGeometryInference": "starcraft_commander.map_resolver",
    "MapGeometryObservation": "starcraft_commander.map_resolver",
    "MapAnchorPositionResolution": "starcraft_commander.map_resolver",
    "MapPoint": "starcraft_commander.map_resolver",
    "MapTargetResolution": "starcraft_commander.map_resolver",
    "SC2MapResolver": "starcraft_commander.map_resolver",
    "SC2MapResolverInterface": "starcraft_commander.map_resolver",
    "SC2RuntimeMapResolver": "starcraft_commander.map_resolver",
    # python-sc2 BotAI adapter.
    "MissingPythonSC2Error": "starcraft_commander.python_sc2_adapter",
    "PythonSC2BotAdapter": "starcraft_commander.python_sc2_adapter",
    "SC2BotAdapterInterface": "starcraft_commander.python_sc2_adapter",
    # Live feasibility validation.
    "DEFAULT_SC2_FEASIBILITY_VALIDATOR": "starcraft_commander.feasibility",
    "SC2FeasibilityResult": "starcraft_commander.feasibility",
    "SC2FeasibilityValidator": "starcraft_commander.feasibility",
    "SC2FeasibilityValidatorInterface": "starcraft_commander.feasibility",
    "validate_sc2_feasibility": "starcraft_commander.feasibility",
    # Korean narration.
    "DEFAULT_SC2_NARRATOR": "starcraft_commander.narrator",
    "SC2KoreanNarrator": "starcraft_commander.narrator",
    "SC2NarrationResponse": "starcraft_commander.narrator",
    "SC2NarratorInterface": "starcraft_commander.narrator",
    "narrate_sc2_plan_result": "starcraft_commander.narrator",
    "narrate_sc2_state": "starcraft_commander.narrator",
    "render_sc2_state_lines": "starcraft_commander.narrator",
    # Live command pipeline (the one surface that reuses the Korean
    # ToyCraft interpreter; loaded lazily so importing the package stays
    # ToyCraft-free).
    "SC2CommandOutcome": "starcraft_commander.live_pipeline",
    "SC2CommandSession": "starcraft_commander.live_pipeline",
    "process_commander_text": "starcraft_commander.live_pipeline",
    "split_compound_command": "starcraft_commander.live_pipeline",
    # Voice input (lazy optional deps inside the module itself).
    # MissingVoiceDependencyError is exported from voice_input: it is the
    # class actually raised by the microphone/transcriber seams.
    "DEFAULT_VOICE_TRANSCRIBER": "starcraft_commander.voice_input",
    "FasterWhisperTranscriber": "starcraft_commander.voice_input",
    "MicrophoneListener": "starcraft_commander.voice_input",
    "MissingVoiceDependencyError": "starcraft_commander.voice_input",
    "VoiceTranscriberInterface": "starcraft_commander.voice_input",
    "VoiceTranscription": "starcraft_commander.voice_input",
    "transcribe_command_audio": "starcraft_commander.voice_input",
    # LLM interpretation (rules-first hybrid; anthropic imported lazily
    # inside the module only when a real client must be built).
    "HybridCommandInterpreter": "starcraft_commander.llm_interpreter",
    "LLMComboPlan": "starcraft_commander.llm_interpreter",
    "LLMComboPlanStep": "starcraft_commander.llm_interpreter",
    "LLMCommandInterpreter": "starcraft_commander.llm_interpreter",
    "build_hybrid_interpreter": "starcraft_commander.llm_interpreter",
    # Commander event memory (stdlib-only ring buffer).
    "CommanderEvent": "starcraft_commander.event_memory",
    "CommanderEventMemory": "starcraft_commander.event_memory",
    # Local web GUI (stdlib http.server, 127.0.0.1 only).
    "SessionLoopBridge": "starcraft_commander.web_gui",
    "WebGuiServer": "starcraft_commander.web_gui",
    # Standing orders (in-game-loop code policies, never LLM-per-frame).
    "StandingOrderController": "starcraft_commander.standing_orders",
    # Optional runtime dependency guards.
    "MissingLLMDependencyError": "starcraft_commander.runtime_deps",
    "MissingSC2RuntimeError": "starcraft_commander.runtime_deps",
    "is_anthropic_available": "starcraft_commander.runtime_deps",
    "is_faster_whisper_available": "starcraft_commander.runtime_deps",
    "is_python_sc2_available": "starcraft_commander.runtime_deps",
    "is_sounddevice_available": "starcraft_commander.runtime_deps",
    "require_anthropic": "starcraft_commander.runtime_deps",
    "require_faster_whisper": "starcraft_commander.runtime_deps",
    "require_python_sc2": "starcraft_commander.runtime_deps",
    "require_sounddevice": "starcraft_commander.runtime_deps",
}
"""Lazily loaded public symbols mapped to their defining modules."""

_EAGER_EXPORTS: Final[tuple[str, ...]] = (
    "SC2_ACTION_TYPES",
    "SC2ActionReport",
    "SC2ActionType",
    "SC2CommandAction",
    "SC2CommandPlan",
    "SC2ExecutionError",
    "SC2ExecutionPlan",
    "SC2PlanExecutionResult",
)
"""Contract symbols imported eagerly (stdlib-only, dependency-free)."""

__all__ = sorted({*_EAGER_EXPORTS, *_LAZY_EXPORTS})


def __getattr__(name: str) -> Any:
    """Load planner/runtime/pipeline surfaces only when callers ask for them."""

    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports to ``dir()`` without importing them."""

    return sorted({*globals(), *__all__})
