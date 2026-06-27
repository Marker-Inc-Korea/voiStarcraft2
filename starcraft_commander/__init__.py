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
    # LLM interpretation (live command understanding is LLM-mandatory;
    # provider SDKs are imported lazily only when a real client is built).
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
    # Human-interruptible policy tree for LLM/BT collaboration experiments.
    "CommanderPolicyDecision": "starcraft_commander.policy_tree",
    "CommanderPolicyTree": "starcraft_commander.policy_tree",
    "CommanderPolicyTreeInterface": "starcraft_commander.policy_tree",
    "CommanderStrategyProfile": "starcraft_commander.policy_tree",
    "CombatModulation": "starcraft_commander.policy_modulation",
    "EconomyModulation": "starcraft_commander.policy_modulation",
    "EmergencyModulation": "starcraft_commander.policy_modulation",
    "PolicyModulationSource": "starcraft_commander.policy_modulation",
    "PolicyModulationVector": "starcraft_commander.policy_modulation",
    "PolicyOverrideLevel": "starcraft_commander.policy_modulation",
    "PolicySafetyConstraint": "starcraft_commander.policy_modulation",
    "ProductionModulation": "starcraft_commander.policy_modulation",
    "ScoutingModulation": "starcraft_commander.policy_modulation",
    "SquadModulation": "starcraft_commander.policy_modulation",
    "StrategyModulation": "starcraft_commander.policy_modulation",
    "TechModulation": "starcraft_commander.policy_modulation",
    "WeightedBiases": "starcraft_commander.policy_modulation",
    "PolicyModulationCompileResult": "starcraft_commander.policy_modulation_provider",
    "PolicyModulationCompileStatus": "starcraft_commander.policy_modulation_provider",
    "PolicyModulationProviderInterface": "starcraft_commander.policy_modulation_provider",
    "PolicyModulationProviderRequest": "starcraft_commander.policy_modulation_provider",
    "compile_policy_modulation_from_provider": (
        "starcraft_commander.policy_modulation_provider"
    ),
    "compile_policy_modulation_provider_output": (
        "starcraft_commander.policy_modulation_provider"
    ),
    "DEFAULT_NEURAL_REPRESENTATION_AXES": "starcraft_commander.neural_representation",
    "NeuralRepresentationModelAdapter": "starcraft_commander.neural_representation",
    "NeuralRepresentationObservation": "starcraft_commander.neural_representation",
    "NeuralRepresentationPrediction": "starcraft_commander.neural_representation",
    "NeuralRepresentationProvider": "starcraft_commander.neural_representation",
    "StaticNeuralRepresentationAdapter": "starcraft_commander.neural_representation",
    "publish_neural_representation_modulation": (
        "starcraft_commander.neural_representation"
    ),
    "MICROMACHINE_BRIDGE_PROTOCOL_VERSION": "starcraft_commander.micromachine_bridge",
    "MICROMACHINE_MANAGER_HOOKS": "starcraft_commander.micromachine_bridge",
    "MICROMACHINE_MODULATION_UPDATE_SCHEMA": "starcraft_commander.micromachine_bridge",
    "MICROMACHINE_TELEMETRY_SCHEMA": "starcraft_commander.micromachine_bridge",
    "MicroMachineBlackboardUpdate": "starcraft_commander.micromachine_bridge",
    "MicroMachineBridgeEnvelope": "starcraft_commander.micromachine_bridge",
    "MicroMachineBridgeFailureMode": "starcraft_commander.micromachine_bridge",
    "MicroMachineBridgeMessageType": "starcraft_commander.micromachine_bridge",
    "MicroMachineBridgeValidationResult": "starcraft_commander.micromachine_bridge",
    "MicroMachineManagerHook": "starcraft_commander.micromachine_bridge",
    "MicroMachineRollbackCommand": "starcraft_commander.micromachine_bridge",
    "MicroMachineTelemetry": "starcraft_commander.micromachine_bridge",
    "build_micromachine_bridge_error_envelope": (
        "starcraft_commander.micromachine_bridge"
    ),
    "validate_micromachine_blackboard_update": (
        "starcraft_commander.micromachine_bridge"
    ),
    "MicroMachineModulationEvaluationPlan": (
        "starcraft_commander.policy_observability"
    ),
    "ModulationEvaluationMetric": "starcraft_commander.policy_observability",
    "ModulationEvaluationMetricKey": "starcraft_commander.policy_observability",
    "PolicyModulationBridgeStatus": "starcraft_commander.policy_observability",
    "PolicyModulationDashboardSnapshot": "starcraft_commander.policy_observability",
    "REQUIRED_EVALUATION_METRICS": "starcraft_commander.policy_observability",
    "build_issue10_evaluation_plan": "starcraft_commander.policy_observability",
    "build_policy_modulation_dashboard_snapshot": (
        "starcraft_commander.policy_observability"
    ),
    "default_modulation_evaluation_metrics": (
        "starcraft_commander.policy_observability"
    ),
    "validate_dashboard_snapshot_payload": (
        "starcraft_commander.policy_observability"
    ),
    "LATEST_TELEMETRY_JSON_NAME": "starcraft_commander.micromachine_runtime",
    "LATEST_UPDATE_JSON_NAME": "starcraft_commander.micromachine_runtime",
    "LATEST_UPDATE_KV_NAME": "starcraft_commander.micromachine_runtime",
    "MicroMachineBackendPublishResult": "starcraft_commander.micromachine_runtime",
    "MicroMachineFilesystemBlackboard": "starcraft_commander.micromachine_runtime",
    "MicroMachineInMemoryBlackboard": "starcraft_commander.micromachine_runtime",
    "MicroMachineModulationBackend": "starcraft_commander.micromachine_runtime",
    "MicroMachineRuntimePaths": "starcraft_commander.micromachine_runtime",
    "build_aggressive_pressure_profile": "starcraft_commander.micromachine_runtime",
    "build_defensive_hold_profile": "starcraft_commander.micromachine_runtime",
    "flatten_blackboard_update": "starcraft_commander.micromachine_runtime",
    "publish_policy_modulation_provider_output": (
        "starcraft_commander.micromachine_runtime"
    ),
    "KeywordPolicyModulationProvider": "starcraft_commander.micromachine_live_session",
    "LiveModulationConsumptionStatus": "starcraft_commander.micromachine_live_session",
    "LiveModulationStatus": "starcraft_commander.micromachine_live_session",
    "LiveTextModulationResult": "starcraft_commander.micromachine_live_session",
    "MicroMachineLiveTextSession": "starcraft_commander.micromachine_live_session",
    "StaticJsonPolicyModulationProvider": "starcraft_commander.micromachine_live_session",
    "MicroMachineSoakConfig": "starcraft_commander.micromachine_soak",
    "MicroMachineSoakFailure": "starcraft_commander.micromachine_soak",
    "MicroMachineSoakObservation": "starcraft_commander.micromachine_soak",
    "MicroMachineSoakReport": "starcraft_commander.micromachine_soak",
    "build_artifact_manifest": "starcraft_commander.micromachine_soak",
    "classify_micromachine_soak": "starcraft_commander.micromachine_soak",
    "has_required_macro_evidence": "starcraft_commander.micromachine_soak",
    "missing_macro_evidence": "starcraft_commander.micromachine_soak",
    "MicroMachineTacticalEffect": (
        "starcraft_commander.micromachine_tactical_evidence"
    ),
    "MicroMachineTacticalEvidence": (
        "starcraft_commander.micromachine_tactical_evidence"
    ),
    "classify_micromachine_tactical_evidence": (
        "starcraft_commander.micromachine_tactical_evidence"
    ),
    "normalize_tactical_effect_tags": (
        "starcraft_commander.micromachine_tactical_evidence"
    ),
    "MicroMachineBuildIdentityConfig": "starcraft_commander.micromachine_build_identity",
    "build_micromachine_build_identity": "starcraft_commander.micromachine_build_identity",
    "read_build_identity": "starcraft_commander.micromachine_build_identity",
    "write_build_identity_report": "starcraft_commander.micromachine_build_identity",
    "DEFAULT_MAP_POOL_PATH": "starcraft_commander.micromachine_map_pool",
    "MicroMachineMapEntry": "starcraft_commander.micromachine_map_pool",
    "MicroMachineMapPool": "starcraft_commander.micromachine_map_pool",
    "MicroMachineQualificationTier": "starcraft_commander.micromachine_map_pool",
    "load_micromachine_map_pool": "starcraft_commander.micromachine_map_pool",
    "parse_micromachine_map_pool": "starcraft_commander.micromachine_map_pool",
    "DEFAULT_MAX_EVIDENCE_AGE_SECONDS": (
        "starcraft_commander.micromachine_release_gate"
    ),
    "MicroMachineReleaseGateConfig": (
        "starcraft_commander.micromachine_release_gate"
    ),
    "build_release_gate_report": "starcraft_commander.micromachine_release_gate",
    "render_release_gate_markdown": "starcraft_commander.micromachine_release_gate",
    "write_release_gate_outputs": "starcraft_commander.micromachine_release_gate",
    "PREFLIGHT_REPORT_NAME": "starcraft_commander.micromachine_preflight",
    "MicroMachineMapPreflightConfig": "starcraft_commander.micromachine_preflight",
    "preflight_micromachine_map": "starcraft_commander.micromachine_preflight",
    "write_preflight_failure_soak_report": "starcraft_commander.micromachine_preflight",
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
