"""Live text-to-MicroMachine policy modulation sidecar.

This module is intentionally stdlib-only. It accepts user text, asks a bounded
provider for semantic policy modulation, compiles that output through the issue
#10 DSL safety gate, and publishes only validated manager-level bias to a
``MicroMachineModulationBackend``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from starcraft_commander.micromachine_bridge import (
    MicroMachineBlackboardUpdate,
    MicroMachineTelemetry,
)
from starcraft_commander.micromachine_runtime import (
    MicroMachineBackendPublishResult,
    MicroMachineFilesystemBlackboard,
    MicroMachineModulationBackend,
)
from starcraft_commander.policy_modulation import (
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


class StaticJsonPolicyModulationProvider:
    """Provider adapter for externally generated bounded JSON payloads."""

    source = PolicyModulationSource.LLM

    def __init__(
        self,
        output: Mapping[str, object],
        *,
        source: PolicyModulationSource | str = PolicyModulationSource.LLM,
    ) -> None:
        self.output = dict(output)
        self.source = _coerce_source(source)

    def propose_policy_modulation(
        self,
        request: PolicyModulationProviderRequest,
    ) -> Mapping[str, object]:
        return dict(self.output)


class KeywordPolicyModulationProvider:
    """Deterministic local provider for smoke tests and no-SDK operation."""

    source = PolicyModulationSource.LLM

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
            return {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "bias",
                "confidence": 0.76,
                "ttl_seconds": 600,
                "posture": "pressure",
                "combat": {
                    "aggression": 0.7,
                    "engage_threshold_delta": -0.2,
                    "retreat_threshold_delta": -0.1,
                    "attack_timing_bias": 0.65,
                    "commitment_level": 0.55,
                    "attack_condition_override": "force_when_threshold_met",
                    "retreat_patience_bias": 0.45,
                    "rally_before_attack_bias": 0.0,
                    "harassment_bias": 0.35,
                    "defend_bias": -0.25,
                    "combat_sim_confidence_margin": -0.2,
                    "target_priority_biases": {
                        "army": 0.35,
                        "worker_line": 0.3,
                        "townhall": 0.25,
                        "production": 0.15,
                    },
                },
                "production": {
                    "production_continuity_bias": 0.65,
                },
                "workers": {"repeat_order_guard_frames": 32},
                "scouting": {
                    "risk_tolerance": 0.45,
                    "scout_priority": 0.7,
                    "require_fresh_enemy_observation": False,
                },
                "squad": {
                    "main_army_bias": 0.6,
                    "harassment_bias": 0.4,
                    "defense_bias": -0.2,
                    "reinforce_bias": 0.3,
                    "contain_bias": 0.35,
                },
                "scope": {
                    "army_group": "main",
                    "location_intent": "enemy_natural",
                    "unit_classes": ["marine", "marauder", "medivac", "siege_tank"],
                    "min_units": 1 if immediate_attack else 2,
                    "require_safety_margin": 0.05,
                    "allow_partial_scope": True,
                },
                "tags": [
                    "keyword_provider",
                    "live_text",
                    "aggressive_pressure",
                    "scouting_map_control",
                    "target_priority",
                ],
            }
        if any(token in text for token in ("수비", "버텨", "hold", "defend", "탱크")):
            return {
                "source": self.source.value,
                "goal": request.command_text,
                "override_level": "constraint",
                "confidence": 0.66,
                "ttl_seconds": 120,
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
                "tags": ["keyword_provider", "live_text"],
            }
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
            "current_frame": self.current_frame,
            "compile_result": self.compile_result.to_dict(),
            "update": self.update.to_dict() if self.update else None,
            "dashboard": self.dashboard.to_dict(),
            "consumption_status": self.consumption_status.value,
            "consumed": self.consumed,
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
        tags: Sequence[str] = (),
    ) -> LiveTextModulationResult:
        """Compile and publish one live user text command."""

        text = _require_text("command_text", command_text)
        frame = self._resolve_current_frame(current_frame)
        telemetry_before = self._safe_read_latest_telemetry()
        request = PolicyModulationProviderRequest(
            command_text=text,
            source=getattr(self.provider, "source", PolicyModulationSource.LLM),
            game_state=_telemetry_game_state(telemetry_before, frame),
            commander_context={"bridge_status": self.bridge_status.value},
            allowed_override_levels=tuple(allowed_override_levels),
            tags=tuple(tags),
        )
        compile_result = _ensure_live_worker_repeat_order_guard(
            compile_policy_modulation_from_provider(self.provider, request)
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
                provider_failure_recorded=failure_recorded,
            )

        try:
            update = self.backend.publish_vector(
                compile_result.vector,
                current_frame=frame,
                update_id=update_id,
                rollback_update_id=rollback_update_id,
            )
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
        help="Bounded provider JSON object. If omitted, a deterministic keyword provider is used.",
    )
    parser.add_argument(
        "--provider-output-file",
        default=None,
        help="Path to a bounded provider JSON object. Overrides --provider-output-json.",
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
    return KeywordPolicyModulationProvider()


def _ensure_live_worker_repeat_order_guard(
    compile_result: PolicyModulationCompileResult,
) -> PolicyModulationCompileResult:
    """Keep live text updates from re-enabling the SCV repeated-order loop."""

    if not compile_result.ok or compile_result.vector is None:
        return compile_result
    default_guard = WorkerModulation().repeat_order_guard_frames
    if compile_result.vector.workers.repeat_order_guard_frames != default_guard:
        return compile_result
    vector = replace(
        compile_result.vector,
        workers=WorkerModulation(repeat_order_guard_frames=32),
    )
    return PolicyModulationCompileResult(
        status=compile_result.status,
        source=compile_result.source,
        vector=vector,
        warnings=(
            *compile_result.warnings,
            "live_worker_repeat_order_guard_frames=32",
        ),
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
        "scout",
        "attack",
        "pressure",
        "harass",
        "defend",
        "hold",
        "tank",
        "marine",
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
