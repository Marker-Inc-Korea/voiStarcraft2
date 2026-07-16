"""Live text-to-MicroMachine policy modulation sidecar.

This module is intentionally stdlib-only. It accepts user text, asks a bounded
provider for semantic policy modulation, compiles that output through the issue
#10 DSL safety gate, and publishes only validated manager-level bias to a
``MicroMachineModulationBackend``.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import uuid
import zlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_GAME_LOOPS_PER_SECOND,
    MicroMachineBlackboardUpdate,
    MicroMachineTelemetry,
)
from starcraft_commander.micromachine_runtime import (
    MicroMachineBackendPublishResult,
    MicroMachineFilesystemBlackboard,
    MicroMachineModulationBackend,
)
from starcraft_commander.policy_modulation import (
    CommandLayer,
    PolicyModulationVector,
    PolicyModulationSource,
    PolicyOverrideLevel,
    WorkerModulation,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileResult,
    PolicyModulationCompileStatus,
    PolicyModulationProviderInterface,
    PolicyModulationProviderRequest,
    compile_policy_modulation_from_provider,
)
from starcraft_commander.policy_observability import (
    PolicyModulationBridgeStatus,
    PolicyModulationDashboardSnapshot,
)


class LiveModulationStatus(str, Enum):
    """Outcome of one live text submission."""

    PUBLISHED = "published"
    CLARIFICATION_REQUIRED = "clarification_required"
    REFUSED = "refused"
    PUBLISH_FAILED = "publish_failed"


class LiveModulationConsumptionStatus(str, Enum):
    """Whether telemetry proves that MicroMachine consumed an update."""

    NOT_PUBLISHED = "not_published"
    PENDING_TELEMETRY = "pending_telemetry"
    PENDING_CONSUMPTION = "pending_consumption"
    CONSUMED = "consumed"


class LiveCommandCategory(str, Enum):
    """Coarse live-command class used by the reducer before blackboard publish."""

    EMERGENCY = "emergency"
    TACTICAL = "tactical"
    PRODUCTION = "production"
    STRATEGY = "strategy"
    BUILDING = "building"
    SCOUTING = "scouting"
    CLARIFICATION = "clarification"


LLM_ONLY_PROVIDER_REQUIRED_REASON = (
    "LLM provider unavailable: MicroMachine production free-form text modulation "
    "requires an LLM-generated structured DSL output. Keyword/rule fallback is "
    "allowed only when explicit smoke mode is requested."
)

_PRODUCTION_TASK_TYPES = frozenset(
    {"sustain_production", "tech_transition", "expand_or_land_command_center"}
)
_TRANSIENT_TASK_TYPES = frozenset({"scout_with_units", "pressure_with_main_army"})
_MICRO_TASK_TYPES = frozenset({"execute_ability"})
_ACTIVE_TASK_TYPES = _TRANSIENT_TASK_TYPES | _MICRO_TASK_TYPES
_TACTICAL_ONLY_TASK_TYPES = frozenset({"pressure_with_main_army", "execute_ability"})
_PERSISTENT_LIVE_DOMAINS = ("strategy", "economy", "workers", "tech", "production")
_TACTICAL_LIVE_DOMAINS = ("combat", "scouting", "squad", "scope")
_LIVE_STANDING_MERGE_WARNING = "live_standing_orders_merged"
_LIVE_COMMAND_REDUCER_WARNING = "live_command_reducer_applied"
_LIVE_LAYER_STATE_TAG_PREFIX = "live_layer_state_v1:"
_EXPLICIT_ABILITY_PREREQUISITE_BUDGET_SECONDS = 900


class StaticJsonPolicyModulationProvider:
    """Provider adapter for externally generated bounded JSON payloads."""

    source = PolicyModulationSource.LLM

    def __init__(
        self,
        output: Mapping[str, object],
        *,
        source: PolicyModulationSource | str = PolicyModulationSource.LLM,
        force_source: bool = False,
    ) -> None:
        self.output = dict(output)
        self.source = _coerce_source(source)
        self.force_source = _coerce_bool(force_source, "force_source")

    def propose_policy_modulation(
        self,
        request: PolicyModulationProviderRequest,
    ) -> Mapping[str, object]:
        output = dict(self.output)
        if self.force_source:
            return _force_provider_output_source(output, self.source)
        return output


class KeywordPolicyModulationProvider:
    """Deterministic local provider for explicit smoke tests only."""

    source = PolicyModulationSource.SMOKE_KEYWORD

    def propose_policy_modulation(
        self,
        request: PolicyModulationProviderRequest,
    ) -> Mapping[str, object]:
        text = request.command_text.lower()
        if _is_non_tactical_chatter(text):
            return {
                "source": self.source.value,
                "status": "clarification_required",
                "clarification_prompt": (
                    "안녕하세요. 이 입력은 MicroMachine 전술 의도가 아니라서 "
                    "blackboard에 publish하지 않았습니다. 예: '마린으로 정찰하고 "
                    "적을 발견하면 안전할 때 압박해'처럼 전술 목표를 말해 주세요."
                ),
            }
        if any(token in text for token in ("?", "뭐", "어떻게")):
            return {
                "source": self.source.value,
                "status": "clarification_required",
                "clarification_prompt": "구체적인 전략 방향을 말해 주세요.",
            }
        if _has_negated_nuke_text_intent(text):
            return {
                "source": self.source.value,
                "status": "clarification_required",
                "clarification_prompt": (
                    "전술핵 사용 금지 의도로 해석했습니다. smoke keyword provider는 "
                    "금지 제약을 blackboard에 publish하지 않습니다."
                ),
            }
        if _has_nuke_text_intent(text):
            return {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "directive",
                "command_layer": "micro",
                "confidence": 0.84,
                "ttl_seconds": 900,
                "workers": {"repeat_order_guard_frames": 32},
                "combat": {
                    "defend_bias": 0.45,
                    "preserve_army_bias": 0.5,
                },
                "scouting": {
                    "scout_priority": 0.8,
                    "scout_cadence_bias": 0.65,
                    "risk_tolerance": 0.55,
                    "hidden_tech_scout_bias": 0.45,
                    "require_fresh_enemy_observation": True,
                },
                "squad": {
                    "defense_bias": 0.4,
                    "regroup_bias": 0.3,
                    "squad_role_biases": {"marine_scout": 0.8},
                },
                "scope": {
                    "army_group": "scout",
                    "unit_classes": ["TERRAN_MARINE"],
                    "location_intent": "enemy_main",
                    "min_units": 4,
                    "max_units": 4,
                    "allow_partial_scope": False,
                },
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "tactical_nuke",
                    "unit_classes": ["TERRAN_GHOST"],
                    "production_targets": [
                        "TERRAN_NUKE",
                        "TERRAN_MARINE",
                        "TERRAN_MARAUDER",
                    ],
                    "location_intent": "enemy_main",
                    "priority": 0.95,
                    "duration_seconds": 0,
                    "allow_partial": False,
                },
                "production_plan": {
                    "targets": [
                        "TERRAN_NUKE",
                        "TERRAN_MARINE",
                        "TERRAN_MARAUDER",
                    ],
                    "allow_prerequisite_buildings": True,
                    "priority": 0.95,
                },
                "composition_requirements": [
                    {
                        "unit_type": "TERRAN_MARINE",
                        "count": 4,
                        "role": "scout",
                    },
                    {
                        "unit_type": "TERRAN_MARAUDER",
                        "count": 2,
                        "role": "defensive_hold",
                    },
                ],
                "lifetime": {
                    "mode": "until_completed",
                    "completion_conditions": ["ability_cast"],
                    "completion_state": "active",
                    "reason": "tactical nuke remains active until cast evidence",
                },
                "tags": ["keyword_provider", "live_text", "tactical_nuke"],
            }
        if _has_cancel_text_intent(text):
            return {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "emergency",
                "confidence": 0.82,
                "ttl_seconds": 45,
                "posture": "defensive",
                "combat": {
                    "aggression": -0.75,
                    "defend_bias": 0.45,
                    "preserve_army_bias": 0.85,
                    "attack_condition_override": "normal",
                },
                "squad": {
                    "main_army_bias": -0.6,
                    "regroup_bias": 0.85,
                    "defense_bias": 0.55,
                },
                "emergency": {"cancel_attacks": True, "force_retreat": True},
                "workers": {"repeat_order_guard_frames": 32},
                "tags": ["keyword_provider", "live_text", "cancel_attack"],
                "rationale": "Cancel the active attack and preserve combat units.",
            }
        if (
            _has_defensive_text_intent(text)
            and not _has_conditional_tactical_retreat_intent(text)
        ):
            standing_intent = _has_standing_text_intent(text)
            return {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "constraint",
                "confidence": 0.66,
                "ttl_seconds": 900 if standing_intent else 120,
                "posture": "defensive",
                "economy": {"gas_priority": 0.25, "repair_priority": 0.25},
                "workers": {"repeat_order_guard_frames": 32},
                "combat": {
                    "aggression": -0.25,
                    "defend_bias": 0.65,
                    "preserve_army_bias": 0.35,
                },
                "scouting": {"scout_priority": 0.25, "risk_tolerance": -0.2},
                "squad": {"defense_bias": 0.45, "regroup_bias": 0.25},
                "lifetime": (
                    {
                        "mode": "standing_order",
                        "completion_conditions": ["cancelled_by_user", "ttl_expired"],
                        "completion_state": "active",
                        "reason": "standing defensive intent from live text",
                    }
                    if standing_intent
                    else {}
                ),
                "tags": (
                    ["keyword_provider", "live_text", "standing_order"]
                    if standing_intent
                    else ["keyword_provider", "live_text"]
                ),
            }
        if _has_marine_centric_macro_text_intent(text):
            return {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "bias",
                "command_layer": "macro",
                "confidence": 0.82,
                "ttl_seconds": 900,
                "strategy": {
                    "posture": "balanced",
                    "doctrine": "marine_rush",
                },
                "production": {
                    "queue_biases": {"TERRAN_MARINE": 0.9},
                    "composition_biases": {"bio": 0.8},
                    "production_continuity_bias": 0.8,
                },
                "workers": {"repeat_order_guard_frames": 32},
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": ["TERRAN_MARINE"],
                    "priority": 0.9,
                    "duration_seconds": 0,
                    "allow_partial": True,
                },
                "production_plan": {
                    "targets": ["TERRAN_MARINE"],
                    "allow_prerequisite_buildings": True,
                    "priority": 0.9,
                },
                "lifetime": {
                    "mode": "standing_order",
                    "completion_conditions": ["cancelled_by_user"],
                    "completion_state": "active",
                    "reason": "Marine-centric macro persists until cancelled",
                },
                "tags": [
                    "keyword_provider",
                    "live_text",
                    "marine_centric_macro",
                    "standing_order",
                ],
            }
        if (
            _has_unit_production_text_intent(text)
            and not _has_tactical_text_intent(text)
        ):
            composition_requirements = _extract_composition_requirements(
                text,
                default_count=1,
            )
            requested_unit_classes = _requested_unit_classes_from_composition(
                composition_requirements
            )
            requested_production_targets = _production_targets_with_prerequisites(
                requested_unit_classes
            )
            standing_intent = _has_standing_text_intent(text)
            return {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "bias",
                "confidence": 0.78,
                "ttl_seconds": 900 if standing_intent else 300,
                "posture": "balanced",
                "workers": {"repeat_order_guard_frames": 32},
                "tactical_task": {
                    "task_type": "sustain_production",
                    "production_targets": requested_production_targets,
                    "priority": 0.8,
                    "duration_seconds": 900 if standing_intent else 300,
                    "allow_partial": True,
                },
                "production_plan": {
                    "targets": requested_production_targets,
                    "allow_prerequisites": True,
                    "priority": 0.8,
                },
                "composition_requirements": composition_requirements,
                "tags": [
                    "keyword_provider",
                    "live_text",
                    "production_intent",
                    "standing_order" if standing_intent else "bounded_production",
                ],
            }
        if any(
            token in text
            for token in (
                "공격",
                "러시",
                "압박",
                "견제",
                "탐색",
                "정찰",
                "적발견",
                "적 발견",
                "마린",
                "해병",
                "pressure",
                "attack",
                "harass",
                "scout",
                "marine",
                "enemy",
            )
        ):
            immediate_attack = any(
                token in text
                for token in (
                    "바로",
                    "즉시",
                    "발견시",
                    "발견 시",
                    "보이면",
                    "asap",
                    "immediate",
                    "right away",
                )
            )
            scout_intent = any(
                token in text
                for token in (
                    "탐색",
                    "정찰",
                    "적발견",
                    "적 발견",
                    "scout",
                    "enemy",
                )
            ) and not any(
                token in text
                for token in (
                    "공격",
                    "러시",
                    "러쉬",
                    "압박",
                    "attack",
                    "rush",
                    "pressure",
                )
            )
            task_type = (
                "scout_with_units" if scout_intent else "pressure_with_main_army"
            )
            army_group = "scout" if scout_intent else "main"
            enemy_main_attack = any(
                token in text
                for token in (
                    "적진",
                    "본진",
                    "기지",
                    "base",
                    "main",
                    "enemy base",
                    "enemy main",
                )
            )
            location_intent = (
                "enemy_main"
                if scout_intent or enemy_main_attack
                else "enemy_natural"
            )
            requested_units = _extract_requested_combat_unit_count(text)
            composition_requirements = _extract_composition_requirements(
                text,
                default_count=1 if scout_intent else None,
            )
            if composition_requirements:
                requested_units = sum(
                    int(item["count"]) for item in composition_requirements
                )
            min_units = (
                requested_units
                if requested_units is not None
                else (1 if immediate_attack or scout_intent else 2)
            )
            max_units = requested_units if requested_units is not None else (2 if scout_intent else 0)
            flank_route_type = _flank_route_type(text)
            flank_intent = bool(flank_route_type)
            focus_fire_intent = _has_focus_fire_intent(text)
            kite_intent = _has_kite_intent(text)
            tactical_retreat_intent = _has_conditional_tactical_retreat_intent(text)
            standing_intent = _has_standing_text_intent(text)
            squad_payload = {
                "main_army_bias": 0.6,
                "harassment_bias": 0.4,
                "defense_bias": -0.2,
                "reinforce_bias": 0.3,
                "contain_bias": 0.1 if flank_intent else 0.35,
                "regroup_bias": 0.7 if tactical_retreat_intent else 0.2,
            }
            if flank_intent:
                squad_payload["flank_bias"] = 0.75
            tags = [
                "keyword_provider",
                "live_text",
                "aggressive_pressure",
                "scouting_map_control",
                "target_priority",
            ]
            if requested_units is not None:
                tags.append("explicit_unit_count")
            if composition_requirements:
                tags.append("explicit_composition")
            if flank_intent:
                tags.append("flank_route")
            if focus_fire_intent:
                tags.append("focus_fire")
            if kite_intent:
                tags.append("kite")
            if tactical_retreat_intent:
                tags.append("conditional_retreat_regroup")
            if standing_intent:
                tags.extend(("continuous_production", "standing_order"))
            requested_unit_classes = _requested_unit_classes_from_composition(
                composition_requirements
            )
            requested_production_targets = _production_targets_with_prerequisites(
                requested_unit_classes
            )
            proactive_supply_intent = _has_proactive_supply_intent(text)
            if (
                proactive_supply_intent
                and "TERRAN_SUPPLYDEPOT" not in requested_production_targets
            ):
                requested_production_targets.append("TERRAN_SUPPLYDEPOT")
            blind_attack_intent = _has_explicit_blind_attack_intent(text)
            require_fresh_enemy_observation = not blind_attack_intent
            payload = {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "bias",
                "confidence": 0.76,
                "ttl_seconds": 900 if standing_intent else 600,
                "posture": "pressure",
                "combat": {
                    "aggression": 0.7,
                    "engage_threshold_delta": -0.2,
                    "retreat_threshold_delta": -0.1,
                    "attack_timing_bias": 0.65,
                    "commitment_level": 0.55,
                    "attack_condition_override": "force_when_threshold_met",
                    "retreat_patience_bias": 0.45,
                    "preserve_army_bias": 0.65 if tactical_retreat_intent else 0.15,
                    "rally_before_attack_bias": 0.0,
                    "harassment_bias": 0.35,
                    "defend_bias": -0.25,
                    "combat_sim_confidence_margin": -0.2,
                    "flank_bias": 0.65 if flank_intent else 0.0,
                    "kite_bias": 0.75 if kite_intent else 0.0,
                    "target_priority_biases": {
                        "army": 0.35,
                        "worker_line": 0.3,
                        "townhall": 0.25,
                        "production": 0.15,
                    },
                },
                "production": {
                    "production_continuity_bias": 0.65,
                    "queue_biases": (
                        {"TERRAN_SUPPLYDEPOT": 0.8}
                        if proactive_supply_intent
                        else {}
                    ),
                },
                "economy": {
                    "supply_buffer_bias": 0.8 if proactive_supply_intent else 0.0,
                },
                "workers": {"repeat_order_guard_frames": 32},
                "scouting": {
                    "risk_tolerance": 0.45,
                    "scout_priority": 0.7 if scout_intent else 0.0,
                    "require_fresh_enemy_observation": (
                        require_fresh_enemy_observation
                    ),
                },
                "squad": squad_payload,
                "scope": {
                    "army_group": army_group,
                    "location_intent": location_intent,
                    "unit_classes": requested_unit_classes,
                    "min_units": min_units,
                    "max_units": max_units,
                    "require_safety_margin": 0.05,
                    "allow_partial_scope": not bool(composition_requirements),
                },
                "tactical_task": {
                    "task_type": task_type,
                    "unit_classes": requested_unit_classes,
                    "production_targets": requested_production_targets,
                    "location_intent": location_intent,
                    "priority": 0.9 if immediate_attack else 0.8,
                    "min_units": min_units,
                    "max_units": max_units,
                    "duration_seconds": (
                        180 if scout_intent else (900 if standing_intent else 300)
                    ),
                    "allow_partial": not bool(composition_requirements),
                    "safety_margin": 0.05,
                },
                "tags": tags,
            }
            if standing_intent:
                payload["lifetime"] = {
                    "mode": "standing_order",
                    "completion_conditions": ["cancelled_by_user", "ttl_expired"],
                    "completion_state": "active",
                    "reason": "standing tactical intent from live text",
                }
            if proactive_supply_intent:
                payload["tags"].append("proactive_supply")
            if blind_attack_intent:
                payload["tags"].append("explicit_blind_attack")
            else:
                payload["tags"].append("search_before_attack")
            if composition_requirements:
                payload["composition_requirements"] = composition_requirements
                payload["production_plan"] = {
                    "targets": requested_production_targets,
                    "allow_prerequisites": True,
                    "priority": 0.8,
                }
                payload["unit_roles"] = [
                    {
                        "unit_type": item["unit_type"],
                        "role": item.get("role", "frontline") or "frontline",
                        "ability_policy": _default_ability_policy_for_role(
                            str(item.get("role", "frontline") or "frontline")
                        ),
                        "priority": 0.75,
                    }
                    for item in composition_requirements
                ]
                payload["route_intent"] = {
                    "route_type": flank_route_type or "direct",
                    "avoid_enemy_strength": bool(flank_intent),
                }
                payload["target_intent"] = {
                    "target_type": location_intent,
                    "priority": 0.85,
                }
            return payload
        return {
            "source": self.source.value,
            "goal": request.command_text,
            "override_level": "bias",
            "confidence": 0.5,
            "ttl_seconds": 120,
            "posture": "balanced",
            "workers": {"repeat_order_guard_frames": 32},
            "tags": ["keyword_provider", "live_text"],
        }


class UnavailableLLMPolicyModulationProvider:
    """Fail-closed provider used when production LLM modulation is unavailable."""

    source = PolicyModulationSource.LLM

    def __init__(self, reason: str = LLM_ONLY_PROVIDER_REQUIRED_REASON) -> None:
        self.reason = _require_text("reason", reason)

    def propose_policy_modulation(
        self,
        request: PolicyModulationProviderRequest,
    ) -> Mapping[str, object]:
        return {
            "source": self.source.value,
            "status": "refused",
            "refusal_reason": self.reason,
        }


@dataclass(frozen=True)
class LiveTextModulationResult:
    """JSON-ready result for one text-to-modulation submission."""

    command_text: str
    status: LiveModulationStatus | str
    current_frame: int
    compile_result: PolicyModulationCompileResult
    update: MicroMachineBlackboardUpdate | None
    dashboard: PolicyModulationDashboardSnapshot
    consumption_status: LiveModulationConsumptionStatus | str
    command_queue: Mapping[str, object] | None = None
    provider_failure_recorded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_text", _require_text("command_text", self.command_text))
        object.__setattr__(self, "status", _coerce_live_status(self.status))
        object.__setattr__(self, "current_frame", _non_negative_int("current_frame", self.current_frame))
        if not isinstance(self.compile_result, PolicyModulationCompileResult):
            raise ValueError("compile_result must be a PolicyModulationCompileResult.")
        if self.update is not None and not isinstance(self.update, MicroMachineBlackboardUpdate):
            raise ValueError("update must be a MicroMachineBlackboardUpdate or None.")
        if not isinstance(self.dashboard, PolicyModulationDashboardSnapshot):
            raise ValueError("dashboard must be a PolicyModulationDashboardSnapshot.")
        object.__setattr__(
            self,
            "consumption_status",
            _coerce_consumption_status(self.consumption_status),
        )
        object.__setattr__(
            self,
            "provider_failure_recorded",
            _coerce_bool(self.provider_failure_recorded, "provider_failure_recorded"),
        )

    @property
    def ok(self) -> bool:
        return self.status is LiveModulationStatus.PUBLISHED and self.update is not None

    @property
    def consumed(self) -> bool:
        return self.consumption_status is LiveModulationConsumptionStatus.CONSUMED

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "command_text": self.command_text,
            "status": self.status.value,
            "provider_source": self.compile_result.source.value,
            "current_frame": self.current_frame,
            "compile_result": self.compile_result.to_dict(),
            "update": self.update.to_dict() if self.update else None,
            "dashboard": self.dashboard.to_dict(),
            "consumption_status": self.consumption_status.value,
            "consumed": self.consumed,
            "command_queue": dict(self.command_queue or {}),
            "provider_failure_recorded": self.provider_failure_recorded,
        }


class MicroMachineLiveTextSession:
    """Submit user text to a provider and publish safe MicroMachine modulation."""

    def __init__(
        self,
        backend: MicroMachineModulationBackend,
        provider: PolicyModulationProviderInterface,
        *,
        bridge_status: PolicyModulationBridgeStatus | str = (
            PolicyModulationBridgeStatus.CONNECTED
        ),
    ) -> None:
        self.backend = backend
        self.provider = provider
        self.bridge_status = _coerce_bridge_status(bridge_status)

    def submit_text(
        self,
        command_text: str,
        *,
        current_frame: int | None = None,
        update_id: str | None = None,
        rollback_update_id: str | None = None,
        allowed_override_levels: Sequence[PolicyOverrideLevel | str] = (
            PolicyOverrideLevel.BIAS,
            PolicyOverrideLevel.CONSTRAINT,
            PolicyOverrideLevel.DIRECTIVE,
            PolicyOverrideLevel.EMERGENCY,
        ),
        commander_context: Mapping[str, object] | None = None,
        tags: Sequence[str] = (),
    ) -> LiveTextModulationResult:
        """Compile and publish one live user text command."""

        text = _require_text("command_text", command_text)
        frame = self._resolve_current_frame(current_frame)
        telemetry_before = self._safe_read_latest_telemetry()
        previous_update = self._safe_read_latest_update(frame)
        previous_layers = (
            _active_live_command_layers(
                previous_update.vector.to_dict(),
                current_frame=frame,
                telemetry=telemetry_before,
                previous_update=previous_update,
            )
            if previous_update is not None
            else ()
        )
        context = {"bridge_status": self.bridge_status.value}
        if commander_context is not None:
            context.update(dict(commander_context))
        context["bridge_status"] = self.bridge_status.value
        if previous_update is not None:
            context["active_micromachine_standing_orders"] = (
                _standing_order_context(previous_update)
            )
            context["recent_commands"] = _merge_recent_command_context(
                context.get("recent_commands"),
                _recent_command_context(previous_update),
            )
            context["active_command_layers"] = list(
                previous_layers
            )
        request = PolicyModulationProviderRequest(
            command_text=text,
            source=getattr(self.provider, "source", PolicyModulationSource.LLM),
            game_state=_telemetry_game_state(telemetry_before, frame),
            commander_context=context,
            allowed_override_levels=tuple(allowed_override_levels),
            tags=tuple(tags),
        )
        compile_result = _ensure_live_worker_repeat_order_guard(
            compile_policy_modulation_from_provider(self.provider, request)
        )
        compile_result = _reject_negated_nuke_provider_output(
            text,
            compile_result,
        )
        if not compile_result.ok or compile_result.vector is None:
            failure_recorded = self._record_provider_failure_if_needed(
                frame,
                compile_result,
            )
            dashboard = self.backend.dashboard_snapshot(
                current_frame=frame,
                bridge_status=(
                    PolicyModulationBridgeStatus.PROVIDER_UNAVAILABLE
                    if failure_recorded
                    else self.bridge_status
                ),
            )
            return LiveTextModulationResult(
                command_text=text,
                status=_status_from_compile_result(compile_result),
                current_frame=frame,
                compile_result=compile_result,
                update=None,
                dashboard=dashboard,
                consumption_status=LiveModulationConsumptionStatus.NOT_PUBLISHED,
                command_queue=_command_queue_summary_for_compile_failure(
                    text,
                    compile_result,
                    update_id=update_id,
                ),
                provider_failure_recorded=failure_recorded,
            )

        try:
            effective_update_id = update_id or _new_live_update_id()
            incoming_layer_payload = compile_result.vector.to_dict()
            incoming_layer = compile_result.vector.command_layer.value
            action = _live_command_reducer_action_for_result(
                text,
                compile_result,
                previous_update=previous_update,
            )
            layer_action = _live_command_layer_action_for_result(
                compile_result,
                previous_layers=previous_layers,
            )
            reducer_category = _live_command_category(
                text,
                compile_result.vector.to_dict(),
            )
            if layer_action in {"merge_cross_layer", "supersede_same_layer"}:
                compile_result = _merge_live_standing_orders(
                    compile_result,
                    previous_update=previous_update,
                    replacing_layer=(
                        incoming_layer
                        if layer_action == "supersede_same_layer"
                        else ""
                    ),
                    current_frame=frame,
                    telemetry=telemetry_before,
                    incoming_update_id=effective_update_id,
                )
            compile_result, command_queue = _reduce_live_command_queue(
                text,
                compile_result,
                previous_update=previous_update,
                update_id=effective_update_id,
                forced_action=action,
                forced_category=reducer_category,
                forced_layer_action=layer_action,
                forced_command_layer=incoming_layer,
                incoming_layer_payload=incoming_layer_payload,
                current_frame=frame,
                telemetry=telemetry_before,
            )
            update = self.backend.publish_vector(
                compile_result.vector,
                current_frame=frame,
                update_id=effective_update_id,
                rollback_update_id=rollback_update_id,
            )
            command_queue = {
                **command_queue,
                "active_command_id": update.update_id,
                "update_id": update.update_id,
            }
        except (OSError, TypeError, ValueError) as exc:
            failure_recorded = self._try_record_provider_failure(
                frame,
                f"publish failed: {exc}",
            )
            dashboard = self.backend.dashboard_snapshot(
                current_frame=frame,
                bridge_status=PolicyModulationBridgeStatus.PROVIDER_UNAVAILABLE,
            )
            return LiveTextModulationResult(
                command_text=text,
                status=LiveModulationStatus.PUBLISH_FAILED,
                current_frame=frame,
                compile_result=compile_result,
                update=None,
                dashboard=dashboard,
                consumption_status=LiveModulationConsumptionStatus.NOT_PUBLISHED,
                command_queue=_command_queue_summary_for_compile_failure(
                    text,
                    compile_result,
                    update_id=update_id,
                ),
                provider_failure_recorded=failure_recorded,
            )
        publish_result = MicroMachineBackendPublishResult(
            compile_result=compile_result,
            update=update,
        )
        if not publish_result.ok:
            raise ValueError("compiled provider output did not publish.")
        dashboard = self.backend.dashboard_snapshot(
            current_frame=frame,
            bridge_status=self.bridge_status,
        )
        return LiveTextModulationResult(
            command_text=text,
            status=LiveModulationStatus.PUBLISHED,
            current_frame=frame,
            compile_result=compile_result,
            update=update,
            dashboard=dashboard,
            consumption_status=_consumption_status(
                update,
                self._safe_read_latest_telemetry(),
                telemetry_before,
            ),
            command_queue=command_queue,
        )

    def _resolve_current_frame(self, current_frame: int | None) -> int:
        if current_frame is not None:
            return _non_negative_int("current_frame", current_frame)
        for attempt in range(3):
            telemetry = self._safe_read_latest_telemetry()
            if telemetry is not None:
                return telemetry.frame
            if attempt < 2:
                time.sleep(0.05)
        return 0

    def _record_provider_failure_if_needed(
        self,
        current_frame: int,
        compile_result: PolicyModulationCompileResult,
    ) -> bool:
        if compile_result.status is PolicyModulationCompileStatus.CLARIFICATION_REQUIRED:
            return False
        reason = compile_result.refusal_reason or compile_result.status.value
        return self._try_record_provider_failure(current_frame, reason)

    def _safe_read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        try:
            return self.backend.read_latest_telemetry()
        except (OSError, TypeError, ValueError):
            return None

    def _safe_read_latest_update(
        self,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate | None:
        try:
            return self.backend.read_latest_update(current_frame=current_frame)
        except (OSError, TypeError, ValueError):
            return None

    def _try_record_provider_failure(self, current_frame: int, reason: str) -> bool:
        try:
            self.backend.write_provider_unavailable(
                current_frame=current_frame,
                reason=reason,
            )
        except (OSError, TypeError, ValueError):
            return False
        return True


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit one live text command to the MicroMachine modulation sidecar.",
    )
    parser.add_argument("--blackboard-dir", required=True, help="Shared MicroMachine blackboard directory.")
    parser.add_argument("--command", required=True, help="User text command to compile and publish.")
    parser.add_argument("--current-frame", type=int, default=None, help="Override the telemetry-derived frame.")
    parser.add_argument("--update-id", default=None, help="Optional deterministic update id.")
    parser.add_argument("--rollback-update-id", default=None, help="Optional rollback update id.")
    parser.add_argument(
        "--provider-output-json",
        default=None,
        help=(
            "Bounded provider JSON object produced by an LLM/tool path. "
            "If omitted, publish fails unless --allow-smoke-keyword-provider is set."
        ),
    )
    parser.add_argument(
        "--provider-output-file",
        default=None,
        help="Path to a bounded provider JSON object. Overrides --provider-output-json.",
    )
    parser.add_argument(
        "--allow-smoke-keyword-provider",
        action="store_true",
        help=(
            "Explicitly allow deterministic keyword modulation for smoke tests. "
            "Never use this as the production free-form text path."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON result.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        provider = _provider_from_args(args)
        session = MicroMachineLiveTextSession(
            MicroMachineFilesystemBlackboard(args.blackboard_dir),
            provider,
        )
        result = session.submit_text(
            args.command,
            current_frame=args.current_frame,
            update_id=args.update_id,
            rollback_update_id=args.rollback_update_id,
        )
        json.dump(
            result.to_dict(),
            sys.stdout,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
        sys.stdout.write("\n")
        return 0 if result.ok else 2
    except Exception as exc:
        json.dump(
            {"ok": False, "error": str(exc), "error_type": type(exc).__name__},
            sys.stderr,
            ensure_ascii=False,
            sort_keys=True,
        )
        sys.stderr.write("\n")
        return 1


def _provider_from_args(args: argparse.Namespace) -> PolicyModulationProviderInterface:
    if args.provider_output_file:
        payload = json.loads(Path(args.provider_output_file).read_text())
        if not isinstance(payload, Mapping):
            raise ValueError("provider output file must contain a JSON object.")
        return StaticJsonPolicyModulationProvider(payload)
    if args.provider_output_json:
        payload = json.loads(args.provider_output_json)
        if not isinstance(payload, Mapping):
            raise ValueError("--provider-output-json must be a JSON object.")
        return StaticJsonPolicyModulationProvider(payload)
    if args.allow_smoke_keyword_provider:
        return KeywordPolicyModulationProvider()
    return UnavailableLLMPolicyModulationProvider()


def _ensure_live_worker_repeat_order_guard(
    compile_result: PolicyModulationCompileResult,
) -> PolicyModulationCompileResult:
    """Keep live text updates from re-enabling the SCV repeated-order loop."""

    if not compile_result.ok or compile_result.vector is None:
        return compile_result
    guard_frames = compile_result.vector.workers.repeat_order_guard_frames
    if guard_frames >= 32:
        return compile_result
    vector = replace(
        compile_result.vector,
        workers=WorkerModulation(repeat_order_guard_frames=32),
    )
    return replace(
        compile_result,
        vector=vector,
        warnings=(
            *compile_result.warnings,
            f"live_worker_repeat_order_guard_frames_clamped={guard_frames}->32",
        ),
    )


def _reject_negated_nuke_provider_output(
    command_text: str,
    compile_result: PolicyModulationCompileResult,
) -> PolicyModulationCompileResult:
    """Reject provider output that reverses an explicit tactical-nuke ban."""

    if (
        not compile_result.ok
        or compile_result.vector is None
        or not _has_negated_nuke_text_intent(command_text)
        or not _vector_requests_tactical_nuke(compile_result.vector)
    ):
        return compile_result
    return replace(
        compile_result,
        status=PolicyModulationCompileStatus.REFUSED,
        vector=None,
        refusal_reason=(
            "provider output conflicts with the user's explicit tactical-nuke ban."
        ),
        clarification_prompt="",
    )


def _vector_requests_tactical_nuke(vector: PolicyModulationVector) -> bool:
    payload = vector.to_dict()
    return any(
        _contains_nuke_control(payload.get(domain))
        for domain in (
            "tactical_task",
            "production_plan",
            "unit_roles",
            "composition_requirements",
            "production",
            "tech",
        )
    )


def _contains_nuke_control(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_nuke_control_token(key) or _contains_nuke_control(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_nuke_control(item) for item in value)
    return _is_nuke_control_token(value)


def _is_nuke_control_token(value: object) -> bool:
    return str(value or "").strip().upper() in {
        "TACTICAL_NUKE",
        "TERRAN_NUKE",
    }


def _new_live_update_id() -> str:
    return f"voi-mm-{uuid.uuid4().hex}"


def _merge_live_standing_orders(
    compile_result: PolicyModulationCompileResult,
    *,
    previous_update: MicroMachineBlackboardUpdate | None,
    replacing_layer: str = "",
    current_frame: int = 0,
    telemetry: MicroMachineTelemetry | None = None,
    incoming_update_id: str = "",
) -> PolicyModulationCompileResult:
    """Preserve active production/economy standing intent across live commands.

    MicroMachine reads a single latest blackboard vector. Without this merge, a
    later "scout with three Marines" update erases prior "keep depots/SCVs/
    Marines going" or "transition to tanks" standing orders.
    """

    if not compile_result.ok or compile_result.vector is None:
        return compile_result
    if compile_result.vector.override_level is PolicyOverrideLevel.EMERGENCY:
        return compile_result
    incoming_payload = _lift_task_to_persistent_biases(
        compile_result.vector.to_dict()
    )
    if previous_update is None:
        merged_vector = PolicyModulationVector.from_mapping(incoming_payload)
        if merged_vector == compile_result.vector:
            return compile_result
        return replace(compile_result, vector=merged_vector)

    previous_payload = _lift_task_to_persistent_biases(
        previous_update.vector.to_dict()
    )
    layer_state = _updated_live_layer_state(
        previous_payload,
        incoming_payload,
        incoming_layer=compile_result.vector.command_layer.value,
        current_frame=current_frame,
        telemetry=telemetry,
        previous_update=previous_update,
        incoming_update_id=incoming_update_id,
    )
    merged_payload = _project_live_layer_state(layer_state)
    if not merged_payload:
        merged_payload = _merge_live_vector_payloads(
            previous_payload,
            incoming_payload,
            replacing_layer=replacing_layer,
        )
    elif (
        compile_result.vector.command_layer is CommandLayer.MACRO
        and _task_type(merged_payload) in _ACTIVE_TASK_TYPES
    ):
        merged_payload["goal"] = _merged_goal(previous_payload, incoming_payload)
    merged_payload.pop("command_layer", None)
    merged_vector = PolicyModulationVector.from_mapping(merged_payload)
    if merged_vector == compile_result.vector:
        return compile_result
    warnings = tuple(
        warning
        for warning in compile_result.warnings
        if warning != _LIVE_STANDING_MERGE_WARNING
    )
    return replace(
        compile_result,
        vector=merged_vector,
        warnings=(*warnings, _LIVE_STANDING_MERGE_WARNING),
    )


def _reduce_live_command_queue(
    command_text: str,
    compile_result: PolicyModulationCompileResult,
    *,
    previous_update: MicroMachineBlackboardUpdate | None,
    update_id: str | None,
    forced_action: str | None = None,
    forced_category: LiveCommandCategory | None = None,
    forced_layer_action: str | None = None,
    forced_command_layer: str | None = None,
    incoming_layer_payload: Mapping[str, object] | None = None,
    current_frame: int = 0,
    telemetry: MicroMachineTelemetry | None = None,
) -> tuple[PolicyModulationCompileResult, dict[str, object]]:
    """Classify and reduce the live command stream into one active plan.

    The blackboard exposes a single latest vector to MicroMachine. This reducer
    makes the overwrite/merge decision explicit so tactical commands do not get
    hidden behind stale hold orders, while standing production remains visible.
    """

    if not compile_result.ok or compile_result.vector is None:
        return compile_result, _command_queue_summary_for_compile_failure(
            command_text,
            compile_result,
            update_id=update_id,
        )
    vector_payload = compile_result.vector.to_dict()
    category = forced_category or _live_command_category(command_text, vector_payload)
    previous_payload = (
        previous_update.vector.to_dict() if previous_update is not None else None
    )
    previous_category = (
        _live_command_category("", previous_payload) if previous_payload else None
    )
    action = forced_action or _live_command_reducer_action(
        category,
        previous_category=previous_category,
        previous_payload=previous_payload,
        incoming_payload=vector_payload,
    )
    command_layer = (
        str(forced_command_layer)
        if forced_command_layer is not None
        else str(vector_payload.get("command_layer", "") or "")
    )
    previous_layers = (
        _active_live_command_layers(
            previous_payload,
            current_frame=current_frame,
            telemetry=telemetry,
            previous_update=previous_update,
        )
        if previous_payload
        else ()
    )
    previous_command_layer = (
        str(previous_payload.get("command_layer", "") or "")
        if previous_payload
        else ""
    )
    layer_action = forced_layer_action or _live_command_layer_action(
        command_layer,
        previous_layers=previous_layers,
    )
    preserved_layers, superseded_layers, active_layers = _reduced_command_layers(
        command_layer,
        previous_layers=previous_layers,
        layer_action=layer_action,
    )
    parent_ids = [previous_update.update_id] if previous_update is not None else []
    queue_summary = {
        "active_command_id": update_id or "",
        "update_id": update_id or "",
        "category": category.value,
        "action": action,
        "command_layer": command_layer,
        "previous_command_layer": previous_command_layer,
        "previous_command_layers": list(previous_layers),
        "layer_action": layer_action,
        "preserved_command_layers": list(preserved_layers),
        "superseded_command_layers": list(superseded_layers),
        "active_command_layers": list(active_layers),
        "parent_command_ids": parent_ids,
        "preserved_update_ids": (
            parent_ids
            if layer_action == "merge_cross_layer"
            else []
        ),
        "superseded_update_ids": (
            parent_ids
            if layer_action in {"supersede_same_layer", "overwrite_all_layers"}
            else []
        ),
        "merged_command_count": 1 + len(parent_ids),
        "standing_order_preserved": (
            action == "merge_standing_orders" and bool(preserved_layers)
        ),
        "superseded_previous": bool(superseded_layers),
        "command_text": command_text,
    }
    reduced_payload = deepcopy(vector_payload)
    reduced_tags = _merge_string_lists(
        (),
        _without_live_command_reducer_tags(reduced_payload.get("tags", ())),
        extra=(
            "live_command_reducer",
            f"command_category:{category.value}",
            f"command_action:{action}",
            f"command_layer:{command_layer}",
            f"layer_action:{layer_action}",
            *(f"active_command_layer:{layer}" for layer in active_layers),
        ),
    )
    reduced_payload["tags"] = reduced_tags
    reduced_payload["command_layer"] = str(
        vector_payload.get("command_layer", "") or ""
    )
    if action in {"overwrite_emergency", "supersede_tactical"}:
        reduced_payload["goal"] = str(vector_payload.get("goal", "") or command_text)
    elif (
        parent_ids
        and action != "merge_standing_orders"
        and category not in {
            LiveCommandCategory.TACTICAL,
            LiveCommandCategory.SCOUTING,
        }
    ):
        reduced_payload["goal"] = _merged_goal(previous_payload or {}, vector_payload)
    if (
        action == "overwrite_emergency"
        and previous_payload is not None
        and _stop_expansion_requested(reduced_payload)
    ):
        _preserve_safe_macro_during_stop_expansion(previous_payload, reduced_payload)
    lifetime = _live_command_lifetime(command_text, category, action, reduced_payload)
    transient_lifetime = lifetime
    update_lifetime = _merged_update_lifetime_for_standing_order(
        category=category,
        action=action,
        transient_lifetime=transient_lifetime,
        previous_payload=previous_payload,
    )
    reduced_payload["ttl_seconds"] = update_lifetime["ttl_seconds"]
    if category is LiveCommandCategory.EMERGENCY:
        reduced_payload["override_level"] = PolicyOverrideLevel.EMERGENCY.value
    reduced_payload["lifetime"] = {
        "mode": update_lifetime["mode"],
        "completion_conditions": update_lifetime["completion_conditions"],
        "completion_state": "active",
        "reason": update_lifetime["reason"],
    }
    state_incoming_payload = (
        incoming_layer_payload
        if incoming_layer_payload is not None
        else vector_payload
    )
    layer_state = _updated_live_layer_state(
        previous_payload or {},
        state_incoming_payload,
        incoming_layer=command_layer,
        current_frame=current_frame,
        telemetry=telemetry,
        previous_update=previous_update,
        incoming_update_id=update_id or "",
        incoming_lifetime=transient_lifetime,
    )
    reduced_payload["tags"] = _with_live_layer_state_tag(
        reduced_payload.get("tags", ()),
        layer_state,
    )
    _sync_lifetime_duration_fields(
        reduced_payload,
        _projected_tactical_task_lifetime(
            layer_state,
            fallback=transient_lifetime,
        ),
    )
    queue_summary["lifetime_mode"] = transient_lifetime["mode"]
    queue_summary["ttl_seconds"] = transient_lifetime["ttl_seconds"]
    queue_summary["completion_conditions"] = list(transient_lifetime["completion_conditions"])
    if update_lifetime != transient_lifetime:
        queue_summary["update_lifetime_mode"] = update_lifetime["mode"]
        queue_summary["update_ttl_seconds"] = update_lifetime["ttl_seconds"]
    reduced_payload.pop("command_layer", None)
    reduced_vector = PolicyModulationVector.from_mapping(reduced_payload)
    warnings = tuple(
        warning
        for warning in compile_result.warnings
        if warning != _LIVE_COMMAND_REDUCER_WARNING
    )
    return (
        replace(
            compile_result,
            vector=reduced_vector,
            warnings=(*warnings, _LIVE_COMMAND_REDUCER_WARNING),
        ),
        queue_summary,
    )


def _live_command_reducer_action_for_result(
    command_text: str,
    compile_result: PolicyModulationCompileResult,
    *,
    previous_update: MicroMachineBlackboardUpdate | None,
) -> str:
    if not compile_result.ok or compile_result.vector is None:
        return compile_result.status.value
    incoming_payload = compile_result.vector.to_dict()
    category = _live_command_category(command_text, incoming_payload)
    previous_payload = (
        previous_update.vector.to_dict() if previous_update is not None else None
    )
    previous_category = (
        _live_command_category("", previous_payload) if previous_payload else None
    )
    return _live_command_reducer_action(
        category,
        previous_category=previous_category,
        previous_payload=previous_payload,
        incoming_payload=incoming_payload,
    )


def _live_command_layer_action_for_result(
    compile_result: PolicyModulationCompileResult,
    *,
    previous_layers: Sequence[str],
) -> str:
    if not compile_result.ok or compile_result.vector is None:
        return compile_result.status.value
    return _live_command_layer_action(
        compile_result.vector.command_layer.value,
        previous_layers=previous_layers,
    )


def _live_command_layer_action(
    command_layer: str,
    *,
    previous_layers: Sequence[str],
) -> str:
    if command_layer == CommandLayer.EMERGENCY.value:
        return "overwrite_all_layers"
    if not previous_layers:
        return "activate_layer"
    if command_layer in previous_layers:
        return "supersede_same_layer"
    return "merge_cross_layer"


def _reduced_command_layers(
    command_layer: str,
    *,
    previous_layers: Sequence[str],
    layer_action: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    previous = tuple(dict.fromkeys(str(layer) for layer in previous_layers if layer))
    if layer_action == "overwrite_all_layers":
        return (), previous, (CommandLayer.EMERGENCY.value,)
    if layer_action == "supersede_same_layer":
        preserved = tuple(layer for layer in previous if layer != command_layer)
        superseded = (command_layer,) if command_layer in previous else ()
        return preserved, superseded, _ordered_command_layers(
            (*preserved, command_layer)
        )
    if layer_action == "merge_cross_layer":
        return previous, (), _ordered_command_layers((*previous, command_layer))
    return (), (), _ordered_command_layers((command_layer,))


def _active_command_layers(payload: Mapping[str, object]) -> tuple[str, ...]:
    layers: list[str] = []
    tags = payload.get("tags", ())
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes, bytearray)):
        for tag in tags:
            text = str(tag)
            if text.startswith("active_command_layer:"):
                layers.append(text.split(":", 1)[1])
    command_layer = str(payload.get("command_layer", "") or "")
    if command_layer:
        layers.append(command_layer)
    return _ordered_command_layers(layers)


def _active_live_command_layers(
    payload: Mapping[str, object],
    *,
    current_frame: int,
    telemetry: MicroMachineTelemetry | None,
    previous_update: MicroMachineBlackboardUpdate | None,
) -> tuple[str, ...]:
    state = _live_layer_state_from_payload(payload)
    if state:
        active_state = _prune_live_layer_state(
            state,
            current_frame=current_frame,
            telemetry=telemetry,
        )
        return _ordered_command_layers(tuple(active_state))
    layers = _active_command_layers(payload)
    if (
        previous_update is not None
        and current_frame > previous_update.expires_at_frame
        and previous_update.vector.lifetime.mode
        not in {"until_cancelled", "standing_order"}
    ):
        return ()
    return layers


def _ordered_command_layers(layers: Sequence[str]) -> tuple[str, ...]:
    requested = {str(layer) for layer in layers if str(layer)}
    return tuple(
        layer.value
        for layer in CommandLayer
        if layer.value in requested
    )


def _command_queue_summary_for_compile_failure(
    command_text: str,
    compile_result: PolicyModulationCompileResult,
    *,
    update_id: str | None,
) -> dict[str, object]:
    category = LiveCommandCategory.CLARIFICATION
    status = compile_result.status.value
    return {
        "active_command_id": update_id or "",
        "update_id": update_id or "",
        "category": category.value,
        "action": status,
        "command_layer": "",
        "previous_command_layer": "",
        "previous_command_layers": [],
        "layer_action": status,
        "preserved_command_layers": [],
        "superseded_command_layers": [],
        "active_command_layers": [],
        "parent_command_ids": [],
        "preserved_update_ids": [],
        "superseded_update_ids": [],
        "merged_command_count": 0,
        "standing_order_preserved": False,
        "superseded_previous": False,
        "command_text": command_text,
    }


def _live_command_category(
    command_text: str,
    payload: Mapping[str, object] | None,
) -> LiveCommandCategory:
    normalized_text = " ".join(str(command_text or "").lower().split())
    payload = payload or {}
    override_level = str(payload.get("override_level", "") or "").lower()
    if override_level == "emergency" or _mapping_has_signal(_mapping_value(payload, "emergency")):
        return LiveCommandCategory.EMERGENCY
    if _has_cancel_text_intent(normalized_text):
        return LiveCommandCategory.EMERGENCY
    if (
        _has_defensive_text_intent(normalized_text)
        and any(token in normalized_text for token in ("후퇴", "retreat"))
        and not _has_negated_retreat_text_intent(normalized_text)
        and not _has_conditional_tactical_retreat_intent(normalized_text)
    ):
        return LiveCommandCategory.EMERGENCY
    task_type = _task_type(payload)
    command_layer = str(payload.get("command_layer", "") or "")
    if task_type in _PRODUCTION_TASK_TYPES:
        return LiveCommandCategory.PRODUCTION
    if task_type in _TACTICAL_ONLY_TASK_TYPES or _has_tactical_text_intent(normalized_text):
        return LiveCommandCategory.TACTICAL
    if task_type == "scout_with_units" or _has_scouting_text_intent(normalized_text):
        return LiveCommandCategory.SCOUTING
    if _has_building_text_intent(normalized_text):
        return LiveCommandCategory.BUILDING
    if _has_production_intent(payload):
        return LiveCommandCategory.PRODUCTION
    if command_layer == CommandLayer.MACRO.value:
        if any(
            _mapping_has_signal(_mapping_value(payload, domain))
            for domain in ("production", "tech")
        ):
            return LiveCommandCategory.PRODUCTION
        if _has_macro_strategy_signal(payload):
            return LiveCommandCategory.STRATEGY
    if command_layer == CommandLayer.OPERATION.value and any(
        _mapping_has_signal(_mapping_value(payload, domain))
        for domain in (
            "combat",
            "scouting",
            "squad",
            "scope",
            "route_intent",
            "target_intent",
        )
    ):
        return LiveCommandCategory.TACTICAL
    if _mapping_has_signal(_mapping_value(payload, "strategy")):
        return LiveCommandCategory.STRATEGY
    return LiveCommandCategory.CLARIFICATION


def _has_macro_strategy_signal(payload: Mapping[str, object]) -> bool:
    if any(
        _mapping_has_signal(_mapping_value(payload, domain))
        for domain in ("strategy", "economy")
    ):
        return True
    workers = dict(_mapping_value(payload, "workers"))
    workers.pop("repeat_order_guard_frames", None)
    return _mapping_has_signal(workers)


def _live_command_reducer_action(
    category: LiveCommandCategory,
    *,
    previous_category: LiveCommandCategory | None,
    previous_payload: Mapping[str, object] | None,
    incoming_payload: Mapping[str, object],
) -> str:
    if category is LiveCommandCategory.EMERGENCY:
        return "overwrite_emergency"
    if previous_payload is None:
        return "activate"
    if category in {LiveCommandCategory.PRODUCTION, LiveCommandCategory.BUILDING, LiveCommandCategory.STRATEGY}:
        return "merge_standing_orders"
    if category in {LiveCommandCategory.TACTICAL, LiveCommandCategory.SCOUTING}:
        if previous_category in {LiveCommandCategory.PRODUCTION, LiveCommandCategory.BUILDING, LiveCommandCategory.STRATEGY}:
            return "merge_standing_orders"
        return "supersede_tactical"
    return "activate"


def _live_command_lifetime(
    command_text: str,
    category: LiveCommandCategory,
    action: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    task_type = _task_type(payload)
    if category is LiveCommandCategory.EMERGENCY:
        return {
            "mode": "emergency_window",
            "ttl_seconds": 45 if _has_cancel_text_intent(command_text) else 60,
            "completion_conditions": (
                "cancelled_by_user",
                "retreat_confirmed",
                "ttl_expired",
            ),
            "reason": "short emergency override window",
        }
    if category is LiveCommandCategory.SCOUTING or task_type == "scout_with_units":
        if _has_standing_text_intent(command_text):
            return {
                "mode": "until_cancelled",
                "ttl_seconds": 900,
                "completion_conditions": (
                    "enemy_observed",
                    "cancelled_by_user",
                ),
                "reason": "standing scout instruction remains active until cancelled",
            }
        return {
            "mode": "until_completed",
            "ttl_seconds": 180,
            "completion_conditions": (
                "enemy_observed",
                "target_reached",
            ),
            "reason": "combat scout remains active until it observes or reaches its target",
        }
    if task_type == "execute_ability":
        tactical_task = _mapping_value(payload, "tactical_task")
        task_duration = max(
            int(_float_at(tactical_task, ("duration_seconds",))),
            int(_float_at(_mapping_value(payload, "scope"), ("duration_seconds",))),
        )
        ttl_seconds = max(
            task_duration,
            _EXPLICIT_ABILITY_PREREQUISITE_BUDGET_SECONDS,
        )
        return {
            "mode": "until_completed",
            "ttl_seconds": min(
                _EXPLICIT_ABILITY_PREREQUISITE_BUDGET_SECONDS,
                ttl_seconds,
            ),
            "completion_conditions": ("ability_cast",),
            "reason": (
                "semantic ability task reserves a bounded prerequisite and "
                "execution window until cast evidence"
            ),
        }
    if category is LiveCommandCategory.TACTICAL:
        if _has_standing_text_intent(command_text):
            return {
                "mode": "until_cancelled",
                "ttl_seconds": 900,
                "completion_conditions": (
                    "order_issued",
                    "target_reached",
                    "cancelled_by_user",
                ),
                "reason": f"standing tactical command action={action}",
            }
        duration = max(
            int(
                _float_at(
                    _mapping_value(payload, "tactical_task"),
                    ("duration_seconds",),
                )
            ),
            int(
                _float_at(
                    _mapping_value(payload, "scope"),
                    ("duration_seconds",),
                )
            ),
        )
        ttl_seconds = max(300, duration)
        return {
            "mode": "until_completed",
            "ttl_seconds": max(180, min(900, ttl_seconds)),
            "completion_conditions": (
                "order_issued",
                "target_reached",
            ),
            "reason": f"tactical command persists until its operation completes action={action}",
        }
    if category is LiveCommandCategory.BUILDING:
        return {
            "mode": "until_completed",
            "ttl_seconds": 900,
            "completion_conditions": (
                "building_started",
                "building_completed",
            ),
            "reason": "building placement remains active until placement outcome",
        }
    if category is LiveCommandCategory.PRODUCTION:
        return {
            "mode": "until_cancelled",
            "ttl_seconds": 900,
            "completion_conditions": (
                "unit_count_reached",
                "cancelled_by_user",
            ),
            "reason": "production command remains active until its target or cancellation",
        }
    if category is LiveCommandCategory.STRATEGY:
        return {
            "mode": "standing_order",
            "ttl_seconds": 900,
            "completion_conditions": ("cancelled_by_user",),
            "reason": "strategy command persists until cancelled or superseded",
        }
    ttl_seconds = int(payload.get("ttl_seconds", 120) or 120)
    return {
        "mode": "ttl",
        "ttl_seconds": max(1, min(900, ttl_seconds)),
        "completion_conditions": ("ttl_expired",),
        "reason": "default bounded TTL",
    }


def _merged_update_lifetime_for_standing_order(
    *,
    category: LiveCommandCategory,
    action: str,
    transient_lifetime: Mapping[str, object],
    previous_payload: Mapping[str, object] | None,
) -> dict[str, object]:
    if action != "merge_standing_orders" or category not in {
        LiveCommandCategory.SCOUTING,
        LiveCommandCategory.TACTICAL,
    }:
        return dict(transient_lifetime)
    if previous_payload is None:
        return dict(transient_lifetime)
    previous_lifetime = _mapping_value(previous_payload, "lifetime")
    previous_mode = str(previous_lifetime.get("mode", "") or "")
    if previous_mode not in {"until_cancelled", "standing_order"}:
        previous_mode = "until_cancelled"
    previous_ttl = int(previous_payload.get("ttl_seconds", 900) or 900)
    conditions = previous_lifetime.get("completion_conditions", ())
    if not isinstance(conditions, Sequence) or isinstance(
        conditions,
        (str, bytes, bytearray),
    ):
        conditions = ("cancelled_by_user",)
    return {
        "mode": previous_mode,
        "ttl_seconds": max(previous_ttl, int(transient_lifetime["ttl_seconds"])),
        "completion_conditions": tuple(str(condition) for condition in conditions),
        "reason": (
            "standing order lifetime preserved while transient task duration "
            f"{transient_lifetime['ttl_seconds']}s remains scoped to tactical_task"
        ),
    }


def _sync_lifetime_duration_fields(
    payload: dict[str, object],
    lifetime: Mapping[str, object],
) -> None:
    ttl_seconds = int(lifetime.get("ttl_seconds", 120) or 120)
    lifetime_mode = str(lifetime.get("mode", "") or "")
    persistent_lifetime = lifetime_mode in {
        "until_cancelled",
        "standing_order",
    }
    for domain in ("scope", "tactical_task"):
        value = payload.get(domain)
        if not isinstance(value, Mapping):
            continue
        domain_payload = dict(value)
        if domain == "scope" and not _scope_has_lifetime_duration_target(
            domain_payload
        ):
            continue
        if domain == "tactical_task" and not str(
            domain_payload.get("task_type", "") or ""
        ):
            continue
        existing = _float_at(domain_payload, ("duration_seconds",))
        production_backed_task = (
            domain == "tactical_task"
            and bool(domain_payload.get("production_targets"))
        )
        if persistent_lifetime:
            domain_payload["duration_seconds"] = 0
        elif (
            (
                domain == "tactical_task"
                and lifetime_mode == "until_completed"
            )
            or production_backed_task
            or existing <= 0
            or existing > ttl_seconds
        ):
            domain_payload["duration_seconds"] = ttl_seconds
        payload[domain] = domain_payload


def _scope_has_lifetime_duration_target(payload: Mapping[str, object]) -> bool:
    return any(
        bool(payload.get(key))
        for key in (
            "army_group",
            "unit_classes",
            "location_intent",
            "min_units",
            "max_units",
            "require_safety_margin",
        )
    )


def _updated_live_layer_state(
    previous_payload: Mapping[str, object],
    incoming_payload: Mapping[str, object],
    *,
    incoming_layer: str,
    current_frame: int = 0,
    telemetry: MicroMachineTelemetry | None = None,
    previous_update: MicroMachineBlackboardUpdate | None = None,
    incoming_update_id: str = "",
    incoming_lifetime: Mapping[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    stored_state = _live_layer_state_from_payload(previous_payload)
    state = _prune_live_layer_state(
        stored_state,
        current_frame=current_frame,
        telemetry=telemetry,
    )
    if not stored_state and previous_payload:
        previous_layers = _active_command_layers(previous_payload)
        if len(previous_layers) == 1:
            state[previous_layers[0]] = _live_layer_state_entry(
                previous_payload,
                update_id=(
                    previous_update.update_id if previous_update is not None else ""
                ),
                issued_at_frame=(
                    previous_update.issued_at_frame
                    if previous_update is not None
                    else current_frame
                ),
                expires_at_frame=(
                    previous_update.expires_at_frame
                    if previous_update is not None
                    else _layer_expiry_frame(
                        previous_payload,
                        issued_at_frame=current_frame,
                    )
                ),
            )
    previous_same_layer = state.get(incoming_layer)
    effective_incoming_payload = incoming_payload
    if (
        previous_same_layer is not None
        and incoming_layer != CommandLayer.EMERGENCY.value
        and not _task_type(incoming_payload)
    ):
        effective_incoming_payload = _merge_live_vector_payloads(
            _live_layer_payload(previous_same_layer),
            incoming_payload,
        )
        effective_incoming_payload["command_layer"] = incoming_layer
    incoming_entry = _live_layer_state_entry(
        effective_incoming_payload,
        update_id=incoming_update_id,
        issued_at_frame=current_frame,
        expires_at_frame=_layer_expiry_frame(
            effective_incoming_payload,
            issued_at_frame=current_frame,
            lifetime=incoming_lifetime,
        ),
    )
    if incoming_lifetime is not None:
        incoming_metadata = _live_layer_metadata(incoming_entry)
        incoming_metadata["lifetime_mode"] = str(
            incoming_lifetime.get("mode", "") or ""
        )
        incoming_metadata["ttl_seconds"] = max(
            1,
            int(incoming_lifetime.get("ttl_seconds", 120) or 120),
        )
        completion_conditions = incoming_lifetime.get(
            "completion_conditions",
            (),
        )
        if isinstance(completion_conditions, Sequence) and not isinstance(
            completion_conditions,
            (str, bytes, bytearray),
        ):
            incoming_metadata["completion_conditions"] = [
                str(condition)
                for condition in completion_conditions
                if str(condition)
            ]
        incoming_entry["metadata"] = incoming_metadata
    if incoming_layer == CommandLayer.EMERGENCY.value:
        return {incoming_layer: incoming_entry}
    state.pop(CommandLayer.EMERGENCY.value, None)
    state[incoming_layer] = incoming_entry
    return {
        layer: state[layer]
        for layer in _ordered_command_layers(tuple(state))
        if layer in state
    }


def _project_live_layer_state(
    layer_state: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    projected: dict[str, object] = {}
    for layer in _ordered_command_layers(tuple(layer_state)):
        layer_entry = layer_state.get(layer)
        if not isinstance(layer_entry, Mapping):
            continue
        layer_payload = _live_layer_payload(layer_entry)
        if not projected:
            projected = deepcopy(layer_payload)
            continue
        projected = _merge_live_vector_payloads(
            projected,
            layer_payload,
            replacing_layer=layer,
        )
    projected_task = dict(_mapping_value(projected, "tactical_task"))
    if _task_type(projected) in _ACTIVE_TASK_TYPES:
        macro_payload = _live_layer_payload(
            layer_state.get(CommandLayer.MACRO.value, {})
        )
        macro_task = _mapping_value(macro_payload, "tactical_task")
        projected_task["production_targets"] = _merge_string_lists(
            projected_task.get("production_targets", ()),
            macro_task.get("production_targets", ()),
        )
        projected["tactical_task"] = projected_task
    projected.pop("command_layer", None)
    return projected


def _canonical_live_layer_payload(
    payload: Mapping[str, object],
) -> dict[str, object]:
    canonical = deepcopy(dict(payload))
    canonical.pop("command_layer", None)
    canonical["tags"] = [
        tag
        for tag in _without_live_command_reducer_tags(canonical.get("tags", ()))
        if not tag.startswith(_LIVE_LAYER_STATE_TAG_PREFIX)
    ]
    return canonical


def _live_layer_state_from_payload(
    payload: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    tags = payload.get("tags", ())
    if not isinstance(tags, Sequence) or isinstance(tags, (str, bytes, bytearray)):
        return {}
    for raw_tag in reversed(tuple(tags)):
        tag = str(raw_tag)
        if not tag.startswith(_LIVE_LAYER_STATE_TAG_PREFIX):
            continue
        encoded = tag[len(_LIVE_LAYER_STATE_TAG_PREFIX) :]
        try:
            padding = "=" * (-len(encoded) % 4)
            compressed = base64.urlsafe_b64decode(encoded + padding)
            decoded = json.loads(zlib.decompress(compressed).decode("utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(decoded, Mapping):
            return {}
        state: dict[str, dict[str, object]] = {}
        for layer in CommandLayer:
            layer_entry = decoded.get(layer.value)
            if not isinstance(layer_entry, Mapping):
                continue
            if isinstance(layer_entry.get("payload"), Mapping):
                state[layer.value] = _live_layer_state_entry_from_mapping(
                    layer_entry
                )
            else:
                state[layer.value] = _live_layer_state_entry(layer_entry)
        return state
    return {}


def _with_live_layer_state_tag(
    tags: object,
    layer_state: Mapping[str, Mapping[str, object]],
) -> list[str]:
    cleaned: list[str] = []
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes, bytearray)):
        for raw_tag in tags:
            tag = str(raw_tag).strip()
            if tag and not tag.startswith(_LIVE_LAYER_STATE_TAG_PREFIX):
                cleaned.append(tag)
    serializable = {
        layer: _live_layer_state_entry_from_mapping(entry)
        for layer, entry in layer_state.items()
        if layer in {item.value for item in CommandLayer}
    }
    if not serializable:
        return cleaned
    encoded = base64.urlsafe_b64encode(
        zlib.compress(
            json.dumps(
                serializable,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
            level=9,
        )
    ).decode("ascii").rstrip("=")
    return [*cleaned, f"{_LIVE_LAYER_STATE_TAG_PREFIX}{encoded}"]


def _live_layer_state_entry(
    payload: Mapping[str, object],
    *,
    update_id: str = "",
    issued_at_frame: int = 0,
    expires_at_frame: int = 0,
    completion_state: str = "active",
) -> dict[str, object]:
    lifetime = _mapping_value(payload, "lifetime")
    return {
        "payload": _canonical_live_layer_payload(payload),
        "metadata": {
            "update_id": str(update_id or ""),
            "issued_at_frame": max(0, int(issued_at_frame or 0)),
            "expires_at_frame": max(0, int(expires_at_frame or 0)),
            "completion_state": str(completion_state or "active"),
            "lifetime_mode": str(lifetime.get("mode", "") or ""),
        },
    }


def _live_layer_state_entry_from_mapping(
    entry: Mapping[str, object],
) -> dict[str, object]:
    payload = entry.get("payload")
    metadata = entry.get("metadata")
    if not isinstance(payload, Mapping):
        payload = entry
    if not isinstance(metadata, Mapping):
        metadata = {}
    normalized = _live_layer_state_entry(
        payload,
        update_id=str(metadata.get("update_id", "") or ""),
        issued_at_frame=int(metadata.get("issued_at_frame", 0) or 0),
        expires_at_frame=int(metadata.get("expires_at_frame", 0) or 0),
        completion_state=str(metadata.get("completion_state", "active") or "active"),
    )
    normalized_metadata = _live_layer_metadata(normalized)
    for key in ("lifetime_mode", "ttl_seconds", "completion_conditions"):
        if key in metadata:
            normalized_metadata[key] = deepcopy(metadata[key])
    normalized["metadata"] = normalized_metadata
    return normalized


def _live_layer_payload(entry: Mapping[str, object]) -> dict[str, object]:
    payload = entry.get("payload")
    if isinstance(payload, Mapping):
        return _canonical_live_layer_payload(payload)
    return _canonical_live_layer_payload(entry)


def _live_layer_metadata(entry: Mapping[str, object]) -> dict[str, object]:
    metadata = entry.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _layer_expiry_frame(
    payload: Mapping[str, object],
    *,
    issued_at_frame: int,
    lifetime: Mapping[str, object] | None = None,
) -> int:
    effective_lifetime = dict(lifetime or _mapping_value(payload, "lifetime"))
    mode = str(effective_lifetime.get("mode", "") or "")
    if mode in {"until_cancelled", "standing_order"}:
        return 0
    ttl_seconds = int(
        effective_lifetime.get(
            "ttl_seconds",
            payload.get("ttl_seconds", 120),
        )
        or 120
    )
    return (
        max(0, issued_at_frame)
        + max(1, ttl_seconds) * MICROMACHINE_GAME_LOOPS_PER_SECOND
    )


def _projected_tactical_task_lifetime(
    layer_state: Mapping[str, Mapping[str, object]],
    *,
    fallback: Mapping[str, object],
) -> dict[str, object]:
    for layer in reversed(_ordered_command_layers(tuple(layer_state))):
        entry = layer_state.get(layer)
        if not isinstance(entry, Mapping):
            continue
        layer_payload = _live_layer_payload(entry)
        if _task_type(layer_payload) not in _ACTIVE_TASK_TYPES:
            continue
        metadata = _live_layer_metadata(entry)
        payload_lifetime = _mapping_value(layer_payload, "lifetime")
        lifetime_mode = str(
            metadata.get(
                "lifetime_mode",
                payload_lifetime.get("mode", fallback.get("mode", "")),
            )
            or ""
        )
        ttl_seconds = int(metadata.get("ttl_seconds", 0) or 0)
        if ttl_seconds <= 0:
            issued_at_frame = int(metadata.get("issued_at_frame", 0) or 0)
            expires_at_frame = int(metadata.get("expires_at_frame", 0) or 0)
            if expires_at_frame > issued_at_frame:
                ttl_seconds = max(
                    1,
                    (
                        expires_at_frame
                        - issued_at_frame
                        + MICROMACHINE_GAME_LOOPS_PER_SECOND
                        - 1
                    )
                    // MICROMACHINE_GAME_LOOPS_PER_SECOND,
                )
        if ttl_seconds <= 0:
            ttl_seconds = int(
                layer_payload.get(
                    "ttl_seconds",
                    fallback.get("ttl_seconds", 120),
                )
                or 120
            )
        completion_conditions = metadata.get(
            "completion_conditions",
            payload_lifetime.get(
                "completion_conditions",
                fallback.get("completion_conditions", ()),
            ),
        )
        if not isinstance(completion_conditions, Sequence) or isinstance(
            completion_conditions,
            (str, bytes, bytearray),
        ):
            completion_conditions = ()
        return {
            "mode": lifetime_mode,
            "ttl_seconds": max(1, ttl_seconds),
            "completion_conditions": tuple(
                str(condition)
                for condition in completion_conditions
                if str(condition)
            ),
            "reason": str(
                payload_lifetime.get(
                    "reason",
                    fallback.get("reason", ""),
                )
                or ""
            ),
        }
    return dict(fallback)


def _prune_live_layer_state(
    state: Mapping[str, Mapping[str, object]],
    *,
    current_frame: int,
    telemetry: MicroMachineTelemetry | None,
) -> dict[str, dict[str, object]]:
    active: dict[str, dict[str, object]] = {}
    for layer, entry in state.items():
        if not isinstance(entry, Mapping):
            continue
        normalized = _live_layer_state_entry_from_mapping(entry)
        metadata = _live_layer_metadata(normalized)
        completion_state = str(
            metadata.get("completion_state", "active") or "active"
        ).lower()
        update_id = str(metadata.get("update_id", "") or "")
        telemetry_state = _telemetry_layer_completion_state(telemetry, update_id)
        if telemetry_state:
            completion_state = telemetry_state
            normalized_metadata = _live_layer_metadata(normalized)
            normalized_metadata["completion_state"] = telemetry_state
            normalized["metadata"] = normalized_metadata
        if completion_state in {
            "cancelled",
            "completed",
            "expired",
            "failed",
            "superseded",
        }:
            continue
        expires_at_frame = int(metadata.get("expires_at_frame", 0) or 0)
        if expires_at_frame > 0 and current_frame > expires_at_frame:
            continue
        active[layer] = normalized
    return active


def _telemetry_layer_completion_state(
    telemetry: MicroMachineTelemetry | None,
    update_id: str,
) -> str:
    if telemetry is None or not update_id:
        return ""
    terminal_states = {
        "cancelled",
        "completed",
        "expired",
        "failed",
        "superseded",
        "confirmed",
        "cast_confirmed",
        "impact_confirmed",
    }
    update_keys = (
        "update_id",
        "policy_update_id",
        "task_update_id",
        "ability_task_update_id",
    )
    for payload in telemetry.managers.values():
        if update_id not in {
            str(payload.get(key, "") or "") for key in update_keys
        }:
            continue
        for key in ("completion_state", "execution_state", "status"):
            state = str(payload.get(key, "") or "").strip().lower()
            if state in terminal_states:
                if state in {"confirmed", "cast_confirmed", "impact_confirmed"}:
                    return "completed"
                return state
    return ""


def _merge_live_vector_payloads(
    previous_payload: Mapping[str, object],
    incoming_payload: Mapping[str, object],
    *,
    replacing_layer: str = "",
) -> dict[str, object]:
    merged = deepcopy(dict(incoming_payload))
    previous_task_type = _task_type(previous_payload)
    incoming_task_type = _task_type(incoming_payload)
    incoming_production_intent = _has_production_intent(incoming_payload)
    defensive_reset = _is_defensive_or_emergency_reset(incoming_payload)
    preserve_active_tactical_operation = (
        not defensive_reset
        and previous_task_type in _ACTIVE_TASK_TYPES
        and incoming_task_type in _PRODUCTION_TASK_TYPES
    )
    previous_defensive_standing = (
        _is_defensive_or_emergency_reset(previous_payload)
        or _has_defensive_standing_marker(previous_payload)
    )

    if replacing_layer != CommandLayer.MACRO.value:
        for domain in _PERSISTENT_LIVE_DOMAINS:
            previous_domain = _mapping_value(previous_payload, domain)
            incoming_domain = _mapping_value(incoming_payload, domain)
            if previous_domain or incoming_domain:
                merged[domain] = _merge_previous_signal(
                    previous_domain,
                    incoming_domain,
                )

    if _stop_expansion_requested(incoming_payload):
        _clear_expansion_biases(merged)

    if (
        not incoming_production_intent
        and incoming_task_type in _TRANSIENT_TASK_TYPES
        and _text_at(previous_payload, ("strategy", "doctrine"))
    ):
        strategy = dict(_mapping_value(merged, "strategy"))
        strategy["doctrine"] = _text_at(previous_payload, ("strategy", "doctrine"))
        for key in ("preferred_builds", "transition_biases", "timing_biases"):
            strategy[key] = _merge_previous_signal(
                _mapping_value(_mapping_value(previous_payload, "strategy"), key),
                _mapping_value(strategy, key),
            )
        merged["strategy"] = strategy

    explicit_tactical_task = (
        incoming_task_type in _ACTIVE_TASK_TYPES
        and _mapping_has_signal(_mapping_value(incoming_payload, "tactical_task"))
    )
    preserve_operation_context_for_micro = (
        incoming_task_type in _MICRO_TASK_TYPES
        and previous_task_type in _TRANSIENT_TASK_TYPES
    )
    if not defensive_reset and preserve_operation_context_for_micro:
        for domain in _TACTICAL_LIVE_DOMAINS:
            previous_domain = _mapping_value(previous_payload, domain)
            incoming_domain = _mapping_value(incoming_payload, domain)
            if previous_domain or incoming_domain:
                merged[domain] = _merge_previous_signal(
                    previous_domain,
                    incoming_domain,
                )
    if not defensive_reset and not explicit_tactical_task:
        for domain in _TACTICAL_LIVE_DOMAINS:
            previous_domain = _mapping_value(previous_payload, domain)
            incoming_domain = _mapping_value(incoming_payload, domain)
            if previous_domain or incoming_domain:
                merged[domain] = _merge_previous_signal(previous_domain, incoming_domain)
        if not incoming_task_type:
            previous_task = _mapping_value(previous_payload, "tactical_task")
            if _task_type(previous_payload):
                merged["tactical_task"] = previous_task

    if preserve_active_tactical_operation:
        previous_task = deepcopy(
            dict(_mapping_value(previous_payload, "tactical_task"))
        )
        incoming_task = _mapping_value(incoming_payload, "tactical_task")
        previous_task["production_targets"] = _merge_string_lists(
            previous_task.get("production_targets", ()),
            incoming_task.get("production_targets", ()),
        )
        merged["tactical_task"] = previous_task
        for domain in ("scope", "route_intent", "target_intent"):
            previous_domain = _mapping_value(previous_payload, domain)
            if _mapping_has_signal(previous_domain):
                merged[domain] = deepcopy(dict(previous_domain))
        for domain in ("composition_requirements", "unit_roles"):
            previous_value = previous_payload.get(domain, ())
            if _value_has_signal(previous_value, (domain,)):
                merged[domain] = deepcopy(previous_value)

    if explicit_tactical_task:
        merged["goal"] = (
            str(incoming_payload.get("goal", "") or "").strip()
            or "live_micromachine_modulation"
        )
    else:
        merged["goal"] = _merged_goal(previous_payload, incoming_payload)
    merged["ttl_seconds"] = max(
        int(previous_payload.get("ttl_seconds", 1) or 1),
        int(incoming_payload.get("ttl_seconds", 1) or 1),
    )
    previous_tags: object = (
        ()
        if explicit_tactical_task and previous_defensive_standing
        else _without_live_command_reducer_tags(previous_payload.get("tags", ()))
    )
    merged["tags"] = _merge_string_lists(
        previous_tags,
        _without_live_command_reducer_tags(incoming_payload.get("tags", ())),
        extra=(_LIVE_STANDING_MERGE_WARNING,),
    )
    previous_rationale = str(previous_payload.get("rationale", "") or "").strip()
    incoming_rationale = str(incoming_payload.get("rationale", "") or "").strip()
    if explicit_tactical_task and previous_defensive_standing:
        if incoming_rationale:
            merged["rationale"] = incoming_rationale
    elif previous_rationale and incoming_rationale:
        merged["rationale"] = f"{incoming_rationale} Standing context preserved: {previous_rationale}"
    elif previous_rationale:
        merged["rationale"] = previous_rationale
    return merged


def _lift_task_to_persistent_biases(payload: Mapping[str, object]) -> dict[str, object]:
    """Convert production-like bounded tasks into standing manager biases."""

    result = deepcopy(dict(payload))
    tactical_task = _mapping_value(result, "tactical_task")
    task_type = str(tactical_task.get("task_type", "") or "")
    raw_targets = tactical_task.get("production_targets", ())
    if not isinstance(raw_targets, Sequence) or isinstance(
        raw_targets,
        (str, bytes, bytearray),
    ):
        raw_targets = ()
    targets = {str(target) for target in raw_targets if str(target).strip()}

    if task_type == "sustain_production" and not targets:
        targets.update({"TERRAN_SCV", "TERRAN_SUPPLYDEPOT", "TERRAN_MARINE"})
    if task_type == "tech_transition" and not targets:
        targets.update({"TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_SIEGETANK"})
    if task_type == "expand_or_land_command_center" and not targets:
        targets.update({"TERRAN_COMMANDCENTER", "TERRAN_SCV", "TERRAN_SUPPLYDEPOT"})

    priority = _float_at(tactical_task, ("priority",), default=0.65)
    bias = max(0.45, min(0.9, priority or 0.65))
    if "TERRAN_SUPPLYDEPOT" in targets:
        _set_max_float(result, ("economy", "supply_buffer_bias"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_SUPPLYDEPOT"), bias)
    if "TERRAN_SCV" in targets:
        _set_max_float(result, ("economy", "worker_production_bias"), bias)
        _set_max_float(result, ("economy", "mineral_saturation_bias"), min(0.8, bias))
    if "TERRAN_MARINE" in targets:
        _set_max_float(result, ("production", "queue_biases", "TERRAN_MARINE"), bias)
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_MARINE"), min(0.85, bias))
        _set_max_float(result, ("production", "production_continuity_bias"), min(0.8, bias))
    if "TERRAN_MARAUDER" in targets:
        _set_max_float(result, ("production", "queue_biases", "TERRAN_MARAUDER"), bias)
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_MARAUDER"), min(0.85, bias))
        _set_max_float(result, ("production", "addon_biases", "BARRACKS_TECHLAB"), bias)
    if "TERRAN_REAPER" in targets:
        _set_max_float(result, ("production", "queue_biases", "TERRAN_REAPER"), bias)
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_REAPER"), min(0.85, bias))
    if "TERRAN_GHOSTACADEMY" in targets:
        _set_max_float(result, ("tech", "structure_biases", "TERRAN_GHOSTACADEMY"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_GHOSTACADEMY"), bias)
        _set_max_float(
            result,
            ("production", "production_facility_biases", "TERRAN_GHOSTACADEMY"),
            bias,
        )
    if "TERRAN_GHOST" in targets:
        _set_max_float(result, ("production", "queue_biases", "TERRAN_GHOST"), bias)
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_GHOST"), min(0.85, bias))
        _set_max_float(result, ("production", "addon_biases", "BARRACKS_TECHLAB"), bias)
    if "TERRAN_COMMANDCENTER" in targets:
        _set_max_float(result, ("economy", "expand_bias"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_COMMANDCENTER"), bias)
        _set_max_float(result, ("production", "composition_biases", "macro"), min(0.85, bias))
    if "TERRAN_FACTORY" in targets:
        _set_max_float(result, ("tech", "structure_biases", "TERRAN_FACTORY"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_FACTORY"), bias)
        _set_max_float(result, ("production", "production_facility_biases", "TERRAN_FACTORY"), bias)
    if "FACTORY_TECHLAB" in targets or "TERRAN_FACTORYTECHLAB" in targets:
        _set_max_float(result, ("production", "queue_biases", "FACTORY_TECHLAB"), bias)
        _set_max_float(result, ("production", "addon_biases", "FACTORY_TECHLAB"), bias)
    if "TERRAN_SIEGETANK" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_SIEGETANK"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_SIEGETANK"), bias)
        _set_max_float(result, ("production", "composition_biases", "siege"), min(0.85, bias))
        _set_max_float(result, ("production", "tech_switch_urgency"), min(0.85, bias))
    if "TERRAN_HELLION" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_HELLION"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_HELLION"), bias)
        _set_max_float(result, ("production", "composition_biases", "mech"), min(0.85, bias))
    if "TERRAN_WIDOWMINE" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_WIDOWMINE"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_WIDOWMINE"), bias)
        _set_max_float(result, ("production", "composition_biases", "mech"), min(0.85, bias))
    if "TERRAN_CYCLONE" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_CYCLONE"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_CYCLONE"), bias)
        _set_max_float(result, ("production", "composition_biases", "mech"), min(0.85, bias))
    if "TERRAN_THOR" in targets:
        _set_max_float(result, ("tech", "structure_biases", "TERRAN_ARMORY"), bias)
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_THOR"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_ARMORY"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_THOR"), bias)
        _set_max_float(result, ("production", "composition_biases", "mech"), min(0.85, bias))
    if "TERRAN_STARPORT" in targets:
        _set_max_float(result, ("tech", "structure_biases", "TERRAN_STARPORT"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_STARPORT"), bias)
        _set_max_float(result, ("production", "production_facility_biases", "TERRAN_STARPORT"), bias)
    if "STARPORT_TECHLAB" in targets or "TERRAN_STARPORTTECHLAB" in targets:
        _set_max_float(result, ("production", "queue_biases", "STARPORT_TECHLAB"), bias)
        _set_max_float(result, ("production", "addon_biases", "STARPORT_TECHLAB"), bias)
    if "TERRAN_MEDIVAC" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_MEDIVAC"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_MEDIVAC"), bias)
        _set_max_float(result, ("production", "composition_biases", "medivac_support"), min(0.85, bias))
    if "TERRAN_VIKINGFIGHTER" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_VIKINGFIGHTER"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_VIKINGFIGHTER"), bias)
        _set_max_float(result, ("production", "composition_biases", "anti_air"), min(0.85, bias))
    if "TERRAN_LIBERATOR" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_LIBERATOR"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_LIBERATOR"), bias)
        _set_max_float(result, ("production", "composition_biases", "anti_air"), min(0.85, bias))
        _set_max_float(result, ("production", "composition_biases", "siege"), min(0.85, bias))
    if "TERRAN_BANSHEE" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_BANSHEE"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_BANSHEE"), bias)
        _set_max_float(result, ("production", "composition_biases", "harass"), min(0.85, bias))
    if "TERRAN_RAVEN" in targets:
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_RAVEN"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_RAVEN"), bias)
        _set_max_float(result, ("production", "composition_biases", "support"), min(0.85, bias))
    if "TERRAN_BATTLECRUISER" in targets:
        _set_max_float(result, ("tech", "structure_biases", "TERRAN_FUSIONCORE"), bias)
        _set_max_float(result, ("tech", "unit_biases", "TERRAN_BATTLECRUISER"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_FUSIONCORE"), bias)
        _set_max_float(result, ("production", "queue_biases", "TERRAN_BATTLECRUISER"), bias)
        _set_max_float(result, ("production", "composition_biases", "capital_air"), min(0.85, bias))
    return result


def _standing_order_context(update: MicroMachineBlackboardUpdate) -> dict[str, object]:
    vector = update.vector
    return {
        "update_id": update.update_id,
        "command_layer": vector.command_layer.value,
        "active_command_layers": list(_active_command_layers(vector.to_dict())),
        "expires_at_frame": update.expires_at_frame,
        "strategy": {
            "posture": vector.strategy.posture,
            "doctrine": vector.strategy.doctrine,
        },
        "economy": vector.economy.to_dict(),
        "tech": vector.tech.to_dict(),
        "production": vector.production.to_dict(),
        "tactical_task": vector.tactical_task.to_dict(),
        "tags": list(vector.tags),
    }


def _recent_command_context(
    update: MicroMachineBlackboardUpdate,
) -> dict[str, object]:
    vector = update.vector
    return {
        "update_id": update.update_id,
        "goal": vector.goal,
        "command_layer": vector.command_layer.value,
        "active_command_layers": list(_active_command_layers(vector.to_dict())),
        "override_level": vector.override_level.value,
        "expires_at_frame": update.expires_at_frame,
        "strategy": {
            "posture": vector.strategy.posture,
            "doctrine": vector.strategy.doctrine,
        },
        "economy": vector.economy.to_dict(),
        "production": vector.production.to_dict(),
        "scope": vector.scope.to_dict(),
        "tactical_task": vector.tactical_task.to_dict(),
        "tags": list(vector.tags),
    }


def _merge_recent_command_context(
    existing: object,
    latest: Mapping[str, object],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if isinstance(existing, Sequence) and not isinstance(
        existing,
        (str, bytes, bytearray),
    ):
        for item in existing:
            if isinstance(item, Mapping):
                result.append(dict(item))
    latest_id = str(latest.get("update_id", "") or "")
    result = [
        item
        for item in result
        if str(item.get("update_id", "") or "") != latest_id
    ]
    result.append(dict(latest))
    return result[-8:]


def _merge_previous_signal(
    previous: Mapping[str, object],
    incoming: Mapping[str, object],
    *,
    path: tuple[str, ...] = (),
) -> dict[str, object]:
    merged: dict[str, object] = {}
    for key in (*previous.keys(), *incoming.keys()):
        if key in merged:
            continue
        current_path = (*path, str(key))
        previous_value = previous.get(key)
        incoming_value = incoming.get(key)
        if isinstance(previous_value, Mapping) or isinstance(incoming_value, Mapping):
            merged[key] = _merge_previous_signal(
                previous_value if isinstance(previous_value, Mapping) else {},
                incoming_value if isinstance(incoming_value, Mapping) else {},
                path=current_path,
            )
        elif _value_has_signal(incoming_value, current_path):
            merged[key] = deepcopy(incoming_value)
        elif _value_has_signal(previous_value, current_path):
            merged[key] = deepcopy(previous_value)
        elif key in incoming:
            merged[key] = deepcopy(incoming_value)
        else:
            merged[key] = deepcopy(previous_value)
    return merged


def _mapping_has_signal(mapping: Mapping[str, object]) -> bool:
    return any(_value_has_signal(value, (str(key),)) for key, value in mapping.items())


def _value_has_signal(value: object, path: tuple[str, ...]) -> bool:
    key = path[-1] if path else ""
    if isinstance(value, Mapping):
        return _mapping_has_signal(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_value_has_signal(item, (*path, str(index))) for index, item in enumerate(value))
    if type(value) is bool:
        return value or key in {"allow_partial", "allow_partial_scope"}
    if isinstance(value, (int, float)) and type(value) is not bool:
        return float(value) != 0.0
    if type(value) is str:
        normalized = value.strip()
        if not normalized:
            return False
        if key == "attack_condition_override" and normalized == "normal":
            return False
        if key == "posture" and normalized == "balanced":
            return False
        return True
    return value is not None


def _has_production_intent(payload: Mapping[str, object]) -> bool:
    tactical_task = _mapping_value(payload, "tactical_task")
    task_type = str(tactical_task.get("task_type", "") or "")
    if task_type in _PRODUCTION_TASK_TYPES:
        return True
    if _value_has_signal(tactical_task.get("production_targets"), ("production_targets",)):
        return True
    for domain in ("economy", "tech", "production"):
        if _mapping_has_signal(_mapping_value(payload, domain)):
            return True
    doctrine = _text_at(payload, ("strategy", "doctrine"))
    return bool(doctrine and doctrine != "scouting_map_control")


def _is_defensive_or_emergency_reset(payload: Mapping[str, object]) -> bool:
    if _mapping_has_signal(_mapping_value(payload, "emergency")):
        return True
    strategy = _mapping_value(payload, "strategy")
    if str(strategy.get("posture", "") or "") == "defensive":
        return True
    combat = _mapping_value(payload, "combat")
    return _float_at(combat, ("defend_bias",)) > 0.45 or _float_at(combat, ("aggression",)) < 0.0


def _has_defensive_standing_marker(payload: Mapping[str, object]) -> bool:
    goal = str(payload.get("goal", "") or "").lower()
    if "micromachine_defensive_hold" in goal or "defensive_hold" in goal:
        return True
    tags = payload.get("tags", ())
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes, bytearray)):
        return any(
            str(tag).strip().lower()
            in {"defensive_hold", "micromachine_defensive_hold"}
            for tag in tags
        )
    return False


def _stop_expansion_requested(payload: Mapping[str, object]) -> bool:
    emergency = _mapping_value(payload, "emergency")
    if emergency.get("stop_expansion") is True:
        return True
    economy = _mapping_value(payload, "economy")
    production = _mapping_value(payload, "production")
    return (
        _float_at(economy, ("expand_bias",)) < 0.0
        or _float_at(production, ("queue_biases", "TERRAN_COMMANDCENTER")) < 0.0
    )


def _clear_expansion_biases(payload: dict[str, object]) -> None:
    economy = dict(_mapping_value(payload, "economy"))
    economy["expand_bias"] = min(0.0, _float_at(economy, ("expand_bias",)))
    payload["economy"] = economy
    production = deepcopy(dict(_mapping_value(payload, "production")))
    queue_biases = dict(_mapping_value(production, "queue_biases"))
    queue_biases.pop("TERRAN_COMMANDCENTER", None)
    production["queue_biases"] = queue_biases
    composition_biases = dict(_mapping_value(production, "composition_biases"))
    composition_biases.pop("macro", None)
    production["composition_biases"] = composition_biases
    payload["production"] = production
    strategy = dict(_mapping_value(payload, "strategy"))
    if strategy.get("doctrine") == "expand_macro":
        strategy["doctrine"] = ""
    payload["strategy"] = strategy


def _preserve_safe_macro_during_stop_expansion(
    previous_payload: Mapping[str, object],
    incoming_payload: dict[str, object],
) -> None:
    """Keep safe macro standing orders while explicitly cancelling expansion."""

    previous_economy = _mapping_value(previous_payload, "economy")
    economy = dict(_mapping_value(incoming_payload, "economy"))
    for key in ("worker_production_bias", "supply_buffer_bias", "mineral_saturation_bias"):
        previous_value = _float_at(previous_economy, (key,))
        if previous_value > _float_at(economy, (key,)):
            economy[key] = previous_value
    incoming_payload["economy"] = economy

    previous_production = _mapping_value(previous_payload, "production")
    previous_queue = _mapping_value(previous_production, "queue_biases")
    production = deepcopy(dict(_mapping_value(incoming_payload, "production")))
    queue = dict(_mapping_value(production, "queue_biases"))
    for key in ("TERRAN_SCV", "TERRAN_SUPPLYDEPOT", "TERRAN_MARINE"):
        previous_value = _float_at(previous_queue, (key,))
        if previous_value > _float_at(queue, (key,)):
            queue[key] = previous_value
    queue.pop("TERRAN_COMMANDCENTER", None)
    production["queue_biases"] = queue
    incoming_payload["production"] = production


def _task_type(payload: Mapping[str, object]) -> str:
    return _text_at(payload, ("tactical_task", "task_type"))


def _merged_goal(
    previous_payload: Mapping[str, object],
    incoming_payload: Mapping[str, object],
) -> str:
    incoming_goal = str(incoming_payload.get("goal", "") or "").strip()
    previous_goal = str(previous_payload.get("goal", "") or "").strip()
    if not previous_goal or previous_goal == incoming_goal:
        return incoming_goal or previous_goal or "live_micromachine_modulation"
    return f"{incoming_goal} | standing: {previous_goal}"[:512]


def _merge_string_lists(
    previous: object,
    incoming: object,
    *,
    extra: Sequence[str] = (),
) -> list[str]:
    result: list[str] = []
    for values in (previous, incoming, extra):
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
            for value in values:
                text = str(value).strip()
                if text and text not in result:
                    result.append(text)
    return result


def _without_live_command_reducer_tags(values: object) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if text == "live_command_reducer":
            continue
        if text.startswith(
            (
                "command_category:",
                "command_action:",
                "command_layer:",
                "layer_action:",
                "active_command_layer:",
            )
        ):
            continue
        result.append(text)
    return result


def _mapping_value(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key, {})
    return value if isinstance(value, Mapping) else {}


def _text_at(mapping: Mapping[str, object], path: Sequence[str]) -> str:
    value: object = mapping
    for key in path:
        if not isinstance(value, Mapping):
            return ""
        value = value.get(key, "")
    return value.strip() if type(value) is str else ""


def _float_at(
    mapping: Mapping[str, object],
    path: Sequence[str],
    *,
    default: float = 0.0,
) -> float:
    value: object = mapping
    for key in path:
        if not isinstance(value, Mapping):
            return default
        value = value.get(key, default)
    if isinstance(value, (int, float)) and type(value) is not bool:
        return float(value)
    return default


def _set_max_float(payload: dict[str, object], path: Sequence[str], value: float) -> None:
    current: dict[str, object] = payload
    for key in path[:-1]:
        nested = current.get(key)
        if not isinstance(nested, dict):
            nested = {}
            current[key] = nested
        current = nested
    leaf = path[-1]
    existing = current.get(leaf, 0.0)
    existing_float = float(existing) if isinstance(existing, (int, float)) and type(existing) is not bool else 0.0
    current[leaf] = max(existing_float, value)


def _force_provider_output_source(
    output: Mapping[str, object],
    source: PolicyModulationSource,
) -> Mapping[str, object]:
    """Force source metadata for untrusted static provider output."""

    forced = dict(output)
    forced["source"] = source.value
    for key in (
        "modulation",
        "policy_modulation",
        "policy_modulation_vector",
        "vector",
    ):
        value = forced.get(key)
        if isinstance(value, Mapping):
            nested = dict(value)
            nested["source"] = source.value
            forced[key] = nested
    return forced


_KOREAN_SMALL_NUMBERS: dict[str, int] = {
    "한": 1,
    "하나": 1,
    "두": 2,
    "둘": 2,
    "세": 3,
    "셋": 3,
    "네": 4,
    "넷": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
}


def _extract_requested_combat_unit_count(text: str) -> int | None:
    normalized = text.lower()
    digit_patterns = (
        r"(?<!\d)(\d{1,3})\s*(?:마린|해병|marine|marines)",
        r"(?:마린|해병|marine|marines)\s*(\d{1,3})\s*(?:기|마리|명|units?)?",
        r"(?<!\d)(\d{1,3})\s*(?:기|마리|명)\s*(?:마린|해병|marine|marines)?",
    )
    for pattern in digit_patterns:
        match = re.search(pattern, normalized)
        if match:
            return max(1, min(200, int(match.group(1))))

    word_pattern = (
        r"("
        + "|".join(sorted(map(re.escape, _KOREAN_SMALL_NUMBERS), key=len, reverse=True))
        + r")\s*(?:마린|해병|기|마리|명)"
    )
    match = re.search(word_pattern, normalized)
    if match:
        return _KOREAN_SMALL_NUMBERS[match.group(1)]
    return None


def _default_ability_policy_for_role(role: str) -> str:
    if role in {
        "worker_harass",
        "cloak_if_available",
        "spellcaster",
        "ambush",
        "zone_control",
        "siege_support",
        "contain",
        "defensive_hold",
    }:
        return "if_available"
    if role in {"capital_ship", "capital_ship_focus", "capital_pressure", "yamato_high_value"}:
        return "high_value_target"
    if role in {"tactical_jump_escape", "evac"}:
        return "escape"
    return "never"


def _requested_unit_classes_from_composition(
    composition_requirements: list[dict[str, object]],
) -> list[str]:
    if not composition_requirements:
        return ["marine", "marauder", "medivac", "siege_tank"]
    classes: list[str] = []
    for requirement in composition_requirements:
        unit_type = str(requirement.get("unit_type", "") or "")
        if unit_type and unit_type not in classes:
            classes.append(unit_type)
    return classes or ["marine", "marauder", "medivac", "siege_tank"]


def _production_targets_with_prerequisites(unit_classes: list[str]) -> list[str]:
    targets = list(unit_classes)
    prerequisites_by_unit = {
        "TERRAN_MARAUDER": ("BARRACKS_TECHLAB",),
        "TERRAN_GHOST": (
            "BARRACKS_TECHLAB",
            "TERRAN_GHOSTACADEMY",
        ),
        "TERRAN_NUKE": (
            "TERRAN_BARRACKS",
            "BARRACKS_TECHLAB",
            "TERRAN_GHOSTACADEMY",
            "TERRAN_GHOST",
            "TERRAN_FACTORY",
        ),
        "TERRAN_HELLION": ("TERRAN_FACTORY",),
        "TERRAN_WIDOWMINE": ("TERRAN_FACTORY",),
        "TERRAN_CYCLONE": ("TERRAN_FACTORY", "FACTORY_TECHLAB"),
        "TERRAN_THOR": ("TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_ARMORY"),
        "TERRAN_SIEGETANK": ("TERRAN_FACTORY", "FACTORY_TECHLAB"),
        "TERRAN_MEDIVAC": ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        "TERRAN_VIKINGFIGHTER": ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        "TERRAN_LIBERATOR": ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        "TERRAN_BANSHEE": (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
        ),
        "TERRAN_RAVEN": (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
        ),
        "TERRAN_BATTLECRUISER": (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
            "TERRAN_FUSIONCORE",
        ),
    }
    for unit_class in unit_classes:
        for prerequisite in prerequisites_by_unit.get(unit_class, ()):
            if prerequisite not in targets:
                targets.append(prerequisite)
    return targets


def _extract_composition_requirements(
    text: str,
    *,
    default_count: int | None = None,
) -> list[dict[str, object]]:
    normalized = text.lower()
    focus_fire_intent = _has_focus_fire_intent(normalized)
    kite_intent = _has_kite_intent(normalized)
    specs = (
        ("TERRAN_MARINE", "frontline", r"(?:마린|해병|marine|marines)"),
        ("TERRAN_MARAUDER", "frontline", r"(?:불곰|marauder|marauders)"),
        ("TERRAN_REAPER", "worker_harass", r"(?:사신|reaper|reapers)"),
        ("TERRAN_GHOST", "spellcaster", r"(?:유령|ghost|ghosts)"),
        (
            "TERRAN_HELLION",
            "worker_harass",
            r"(?:화염기갑병|화염차|hellbats?|hellions?)",
        ),
        ("TERRAN_WIDOWMINE", "ambush", r"(?:땅거미지뢰|지뢰|widow\s*mine|widow\s*mines)"),
        ("TERRAN_CYCLONE", "kite", r"(?:사이클론|cyclone|cyclones)"),
        ("TERRAN_THOR", "anti_air", r"(?:토르|thor|thors)"),
        ("TERRAN_SIEGETANK", "siege_support", r"(?:탱크|공성전차|siege\s*tanks?|tanks?)"),
        ("TERRAN_MEDIVAC", "support", r"(?:의료선|medivac|medivacs)"),
        ("TERRAN_VIKINGFIGHTER", "anti_air", r"(?:바이킹|viking|vikings)"),
        ("TERRAN_LIBERATOR", "zone_control", r"(?:해방선|liberator|liberators)"),
        ("TERRAN_BANSHEE", "worker_harass", r"(?:밴시|banshee|banshees)"),
        ("TERRAN_RAVEN", "support", r"(?:밤까마귀|raven|ravens)"),
        ("TERRAN_BATTLECRUISER", "capital_ship", r"(?:배틀크루저|전투순양함|battlecruiser|battlecruisers|bc)"),
    )
    requirements: list[dict[str, object]] = []
    for unit_type, role, unit_pattern in specs:
        count: int | None = None
        digit_before = re.search(rf"(?<!\d)(\d{{1,3}})\s*{unit_pattern}", normalized)
        digit_after = re.search(rf"{unit_pattern}\s*(\d{{1,3}})\s*(?:기|마리|대|명|units?)?", normalized)
        if digit_before:
            count = int(digit_before.group(1))
        elif digit_after:
            count = int(digit_after.group(1))
        else:
            word_match = re.search(
                r"("
                + "|".join(
                    sorted(map(re.escape, _KOREAN_SMALL_NUMBERS), key=len, reverse=True)
                )
                + rf")\s*{unit_pattern}",
                normalized,
            )
            if word_match:
                count = _KOREAN_SMALL_NUMBERS[word_match.group(1)]
        if count is None and default_count is not None and re.search(unit_pattern, normalized):
            count = default_count
        if count is not None:
            effective_role = role
            if unit_type in {"TERRAN_MARINE", "TERRAN_MARAUDER"}:
                if focus_fire_intent:
                    effective_role = "focus_fire"
                elif kite_intent:
                    effective_role = "kite"
            requirements.append(
                {
                    "unit_type": unit_type,
                    "count": max(1, min(200, count)),
                    "role": effective_role,
                }
            )
    return requirements


def _has_focus_fire_intent(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.lower())
    return any(
        token in normalized
        for token in (
            "집중사격",
            "점사",
            "한놈씩",
            "한명씩",
            "focusfire",
            "focustarget",
        )
    )


def _has_kite_intent(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.lower())
    return any(
        token in normalized
        for token in (
            "kite",
            "kiting",
            "카이트",
            "카이팅",
            "치고빠져",
            "무빙샷",
            "stutterstep",
        )
    )


def _has_flank_route_intent(text: str) -> bool:
    return bool(_flank_route_type(text))


def _flank_route_type(text: str) -> str:
    normalized = re.sub(r"\s+", "", text.lower())
    if any(token in normalized for token in ("우측", "오른쪽", "flankright", "rightflank")):
        return "flank_right"
    if any(token in normalized for token in ("좌측", "왼쪽", "flankleft", "leftflank")):
        return "flank_left"
    if any(
        token in normalized
        for token in (
            "다른길",
            "우회",
            "옆길",
            "측면",
            "flank",
            "sideroute",
            "alternateroute",
            "differentroute",
        )
    ):
        return "flank_left"
    return ""


def _has_defensive_text_intent(text: str) -> bool:
    return any(
        token in text
        for token in (
            "수비",
            "방어",
            "버텨",
            "지켜",
            "막아",
            "후퇴",
            "hold",
            "defend",
            "defense",
            "retreat",
        )
    )


def _has_conditional_tactical_retreat_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    if not _has_tactical_text_intent(normalized):
        return False
    if not any(token in normalized for token in ("후퇴", "retreat", "fall back", "fallback")):
        return False
    return any(
        token in normalized or token in compact
        for token in (
            "위험하면",
            "위험할 때",
            "위험할때",
            "불리하면",
            "불리할 때",
            "불리할때",
            "필요하면",
            "피해가 크면",
            "후퇴 후 재집결",
            "후퇴후재집결",
            "후퇴했다가",
            "재집결해서 다시 공격",
            "재집결후다시공격",
            "if retreat",
            "retreat if",
            "if needed",
            "if necessary",
            "if unsafe",
            "if outmatched",
            "fall back and regroup",
            "retreat and regroup",
            "regroup and attack again",
        )
    )


def _has_negated_retreat_text_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    return any(
        re.search(pattern, normalized)
        for pattern in (
            r"(?:후퇴|퇴각|철수)(?:하)?지\s*(?:마|말|않|안)",
            r"(?:후퇴|퇴각|철수)\s*(?:말고|금지|없이)",
            r"\b(?:no|never)\s+(?:retreat|fallback|fall\s+back)\b",
            r"\b(?:do\s+not|don't|dont)\s+(?:retreat|fall\s+back)\b",
            r"\bretreat\s+is\s+not\s+an\s+option\b",
        )
    ) or any(
        token in compact
        for token in (
            "후퇴하지마",
            "후퇴말고",
            "후퇴금지",
            "후퇴없이",
            "퇴각하지마",
            "퇴각말고",
            "철수하지마",
            "철수말고",
        )
    )


def _has_cancel_text_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    # "Keep this until I cancel it" declares a standing lifetime; it is not
    # an immediate cancel/retreat command.
    for pattern in (
        r"(?:내가\s*)?취소(?:할|하기|될)\s*때까지",
        r"(?:내가\s*)?취소하기\s*전까지",
        r"\buntil\s+(?:(?:i|you|the\s+user)\s+)?cancel(?:led|ed)?\b",
    ):
        normalized = re.sub(pattern, " ", normalized)
    # A fallback parser must not invert "do not cancel the attack" into an
    # emergency retreat. Remove only negated cancel verbs; any later positive
    # cancel clause remains available for the intent check below.
    for pattern in (
        (
            r"(?:(?:공격|러시|러쉬|압박)(?:을|를)?\s*)?"
            r"(?:취소|중지)(?:하)?지\s*"
            r"(?:마(?:라|세요)?|말(?:고|아|라)?|않(?:아|는다|도록|고)?)"
        ),
        (
            r"(?:(?:공격|러시|러쉬|압박)(?:을|를)?\s*)?"
            r"(?:멈추|그만두)지\s*"
            r"(?:마(?:라|세요)?|말(?:고|아|라)?|않(?:아|는다|도록|고)?)"
        ),
        r"\b(?:do\s+not|don't|dont|never)\s+(?:cancel|stop|abort)\b",
        r"\bwithout\s+(?:cancel(?:ing|ling)?|stopp?ing|abort(?:ing)?)\b",
    ):
        normalized = re.sub(pattern, " ", normalized)
    compact = "".join(normalized.split())
    return any(
        token in normalized or token in compact
        for token in (
            "cancel",
            "stop",
            "abort",
            "취소",
            "중지",
            "멈춰",
            "그만",
            "공격취소",
            "공격중지",
        )
    )


def _has_standing_text_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    return any(
        token in normalized or token in compact
        for token in (
            "계속",
            "유지",
            "항상",
            "상시",
            "게임 내내",
            "게임내내",
            "끝까지",
            "쭉",
            "취소할 때까지",
            "취소할때까지",
            "취소하기 전까지",
            "취소하기전까지",
            "취소될 때까지",
            "취소될때까지",
            "keep",
            "continue",
            "always",
            "until cancelled",
            "standing",
        )
    )


def _has_unit_production_text_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not any(
        token in normalized
        for token in (
            "뽑",
            "생산",
            "만들",
            "찍",
            "train",
            "produce",
            "make",
            "build units",
        )
    ):
        return False
    return bool(_extract_composition_requirements(normalized, default_count=1))


def _has_marine_centric_macro_text_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    marine_centric = any(
        marker in normalized or marker in compact
        for marker in (
            "마린 중심",
            "마린중심",
            "해병 중심",
            "해병중심",
            "marine-centric",
            "marine centric",
            "marine-focused",
            "marine focused",
            "focus on marines",
        )
    )
    return marine_centric and not any(
        marker in normalized
        for marker in (
            "공격",
            "러시",
            "러쉬",
            "압박",
            "견제",
            "정찰",
            "탐색",
            "attack",
            "rush",
            "pressure",
            "harass",
            "scout",
        )
    )


def _has_scouting_text_intent(text: str) -> bool:
    return any(
        token in text
        for token in (
            "정찰",
            "탐색",
            "수색",
            "scout",
            "recon",
            "侦察",
        )
    )


def _has_tactical_text_intent(text: str) -> bool:
    return any(
        token in text
        for token in (
            "공격",
            "러시",
            "러쉬",
            "압박",
            "견제",
            "적진",
            "attack",
            "rush",
            "pressure",
            "harass",
            "enemy base",
            "enemy main",
            "핵",
            "핵미사일",
            "전술핵",
            "nuke",
            "nuclear strike",
            "tactical nuke",
            "进攻",
        )
    )


def _has_nuke_text_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    return not _has_negated_nuke_text_intent(normalized) and any(
        token in normalized or token in compact
        for token in (
            "핵",
            "핵미사일",
            "전술핵",
            "nuke",
            "nuclear strike",
            "tactical nuke",
        )
    )


def _has_negated_nuke_text_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    has_nuke = any(
        token in normalized or token in compact
        for token in (
            "핵",
            "핵미사일",
            "전술핵",
            "nuke",
            "nuclear strike",
            "tactical nuke",
        )
    )
    if not has_nuke:
        return False
    korean_negation = any(
        marker in compact
        for marker in (
            "핵금지",
            "핵사용금지",
            "핵발사금지",
            "핵투하금지",
            "핵쓰지마",
            "핵사용하지마",
            "핵발사하지마",
            "핵투하하지마",
            "핵쏘지마",
            "핵사용하면안돼",
            "핵발사하면안돼",
            "핵투하하면안돼",
            "핵을쓰면안돼",
            "핵을사용하면안돼",
            "핵은사용하면안돼",
            "전술핵사용금지",
            "전술핵사용하지마",
            "전술핵발사하지마",
        )
    ) or bool(
        re.search(
            (
                r"(?:핵미사일|전술핵|핵)(?:은|는|이|가|을|를)?"
                r".{0,28}(?:금지|허용(?:되|하)지\s*않|안\s*돼|"
                r"(?:사용|발사|투하)(?:은|는|을|를)?\s*하지\s*마|"
                r"(?:(?:사용|발사|투하)하|쓰|쏘)지\s*(?:마|말"
                r"(?:아(?:\s*줘|\s*주(?:세요|십시오))?|라|도록)?)|"
                r"(?:(?:사용|발사|투하)하|쓰|쏘)면\s*안\s*"
                r"(?:돼|된다|됩니다|됨))"
            ),
            normalized,
        )
    )
    english_negation = bool(
        re.search(
            (
                r"\b(?:do\s+not|don't|dont|never|avoid|refrain\s+from|"
                r"under\s+no\s+circumstances|ban|disable|forbid|prohibit)"
                r"\b.{0,48}\b(?:use|using|launch|launching|fire|firing|"
                r"deploy|deploying|detonate|detonating)?\b.{0,16}"
                r"\b(?:tactical\s+)?nukes?\b"
            ),
            normalized,
        )
        or re.search(
            r"\bno\s+(?:tactical\s+)?nukes?\b",
            normalized,
        )
        or re.search(
            (
                r"\b(?:tactical\s+)?nukes?\b.{0,24}"
                r"\b(?:must\s+not|should\s+not|shouldn't|cannot|can't|banned|"
                r"disabled|forbidden|prohibited|(?:are|is)\s+not\s+allowed|"
                r"not\s+allowed)\b"
            ),
            normalized,
        )
    )
    return korean_negation or english_negation


def _has_proactive_supply_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    supply_marker = any(
        token in normalized
        for token in (
            "보급고",
            "서플라이",
            "supply depot",
            "supply",
            "depot",
        )
    )
    proactive_marker = any(
        token in normalized or token in compact
        for token in (
            "부족해지기 전에",
            "부족하기 전에",
            "막히기 전에",
            "미리",
            "계속",
            "유지",
            "before supply",
            "before getting supply blocked",
            "avoid supply block",
            "keep production",
            "continue production",
        )
    )
    return supply_marker and proactive_marker


def _has_explicit_blind_attack_intent(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    compact = "".join(normalized.split())
    return any(
        token in normalized or token in compact
        for token in (
            "정찰 없이",
            "정찰없이",
            "시야 없이",
            "시야없이",
            "확인 없이",
            "확인없이",
            "못 찾아도 바로",
            "위치 몰라도",
            "blind attack",
            "blind rush",
            "without scouting",
            "without vision",
        )
    )


def _has_building_text_intent(text: str) -> bool:
    return any(
        token in text
        for token in (
            "건물",
            "건설",
            "지어",
            "짓",
            "보급고",
            "배럭",
            "병영",
            "팩토리",
            "군수공장",
            "스타포트",
            "우주공항",
            "벙커",
            "build",
            "depot",
            "barracks",
            "factory",
            "starport",
            "bunker",
        )
    )


def _is_non_tactical_chatter(text: str) -> bool:
    compact = "".join(str(text).strip().lower().split())
    if not compact:
        return True
    tactical_markers = (
        "공격",
        "러시",
        "압박",
        "견제",
        "수비",
        "버텨",
        "탱크",
        "정찰",
        "탐색",
        "마린",
        "해병",
        "멀티",
        "확장",
        "가스",
        "일꾼",
        "핵",
        "전술핵",
        "scout",
        "attack",
        "pressure",
        "harass",
        "defend",
        "hold",
        "tank",
        "marine",
        "nuke",
        "nuclearstrike",
        "enemy",
    )
    if any(marker in compact for marker in tactical_markers):
        return False
    return compact in {
        "안녕",
        "안녕하세요",
        "ㅎㅇ",
        "하이",
        "hello",
        "hi",
        "hey",
        "테스트",
        "test",
        "고마워",
        "감사",
        "thanks",
        "thankyou",
    }


def _telemetry_game_state(
    telemetry: MicroMachineTelemetry | None,
    current_frame: int,
) -> dict[str, object]:
    payload: dict[str, object] = {"frame": current_frame}
    if telemetry is not None:
        payload["telemetry"] = telemetry.to_dict()
    return payload


def _consumption_status(
    update: MicroMachineBlackboardUpdate | None,
    telemetry: MicroMachineTelemetry | None,
    telemetry_before_publish: MicroMachineTelemetry | None = None,
) -> LiveModulationConsumptionStatus:
    if update is None:
        return LiveModulationConsumptionStatus.NOT_PUBLISHED
    if telemetry is None:
        return LiveModulationConsumptionStatus.PENDING_TELEMETRY
    if (
        telemetry_before_publish is not None
        and telemetry.frame <= telemetry_before_publish.frame
    ):
        return LiveModulationConsumptionStatus.PENDING_CONSUMPTION
    if telemetry.frame <= update.issued_at_frame:
        return LiveModulationConsumptionStatus.PENDING_CONSUMPTION
    if update.update_id in telemetry.active_modulation_ids:
        return LiveModulationConsumptionStatus.CONSUMED
    return LiveModulationConsumptionStatus.PENDING_CONSUMPTION


def _status_from_compile_result(
    compile_result: PolicyModulationCompileResult,
) -> LiveModulationStatus:
    if compile_result.status is PolicyModulationCompileStatus.CLARIFICATION_REQUIRED:
        return LiveModulationStatus.CLARIFICATION_REQUIRED
    return LiveModulationStatus.REFUSED


def _coerce_live_status(value: LiveModulationStatus | str) -> LiveModulationStatus:
    if isinstance(value, LiveModulationStatus):
        return value
    if type(value) is not str:
        raise ValueError("live modulation status must be a string.")
    try:
        return LiveModulationStatus(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported live modulation status: {value!r}.") from exc


def _coerce_consumption_status(
    value: LiveModulationConsumptionStatus | str,
) -> LiveModulationConsumptionStatus:
    if isinstance(value, LiveModulationConsumptionStatus):
        return value
    if type(value) is not str:
        raise ValueError("consumption status must be a string.")
    try:
        return LiveModulationConsumptionStatus(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported consumption status: {value!r}.") from exc


def _coerce_bridge_status(
    value: PolicyModulationBridgeStatus | str,
) -> PolicyModulationBridgeStatus:
    if isinstance(value, PolicyModulationBridgeStatus):
        return value
    if type(value) is not str:
        raise ValueError("bridge status must be a string.")
    try:
        return PolicyModulationBridgeStatus(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported bridge status: {value!r}.") from exc


def _coerce_source(value: PolicyModulationSource | str) -> PolicyModulationSource:
    if isinstance(value, PolicyModulationSource):
        return value
    if type(value) is not str:
        raise ValueError("source must be a string.")
    try:
        return PolicyModulationSource(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported source: {value!r}.") from exc


def _require_text(field_name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer.")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    return value


def _coerce_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be a bool.")
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
