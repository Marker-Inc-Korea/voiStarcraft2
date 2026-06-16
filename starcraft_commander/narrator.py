"""Commander-facing Korean narration for StarCraft II execution outcomes.

This module is intentionally importable without StarCraft II, python-sc2,
faster-whisper, or sounddevice installed. It renders the structured
``SC2PlanExecutionResult`` and ``SC2CommanderState`` contracts into Korean
commander narration. The narration never hides skipped work: any planned
action that did not apply is reported as ``partially_executed`` or
``blocked``, never as success.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from typing import Final, Literal, Protocol, runtime_checkable

from starcraft_commander.contracts import (
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.state_resolver import SC2_WORKER_TYPE_NAME, SC2CommanderState


SC2NarrationStatus = Literal["executed", "partially_executed", "blocked", "read_only"]
"""Stable commander-facing narration status values."""

SC2_NARRATION_STATUSES: Final[frozenset[str]] = frozenset(
    {"executed", "partially_executed", "blocked", "read_only"}
)
"""Every supported ``SC2NarrationResponse.status`` value."""

SC2_KOREAN_TARGET_NAMES: Final[dict[str, str]] = {
    "self_main": "본진",
    "self_ramp": "본진 입구",
    "self_natural": "앞마당",
    "self_mineral_line": "본진 일꾼 라인",
    "self_geyser": "본진 가스 간헐천",
    "enemy_main": "적 본진",
    "enemy_ramp": "적 입구",
    "enemy_front": "적 입구",
    "enemy_natural": "적 앞마당",
    "enemy_mineral_line": "적 일꾼 라인",
}
"""Semantic SC2 target names translated into commander-facing Korean."""

SC2_KOREAN_RESOURCE_NAMES: Final[dict[str, str]] = {
    "mineral": "미네랄",
    "minerals": "미네랄",
    "gas": "가스",
    "vespene": "가스",
    "vespene_gas": "가스",
}
"""Resource target names (assign_workers targets) translated into Korean."""

SC2_KOREAN_TYPE_NAMES: Final[dict[str, str]] = {
    "SCV": "SCV",
    "MARINE": "마린",
    "HELLION": "헬리온",
    "BARRACKS": "배럭",
    "SUPPLYDEPOT": "보급고",
    "COMMANDCENTER": "사령부",
    "FACTORY": "군수공장",
    "REFINERY": "정제소",
    "BUNKER": "벙커",
    "ZERGLING": "저글링",
    "HATCHERY": "해처리",
    "DRONE": "드론",
}
"""Normalized (UPPERCASE, whitespace-free) type names translated into Korean."""

SC2_ACTION_TYPE_KOREAN_LABELS: Final[dict[SC2ActionType, str]] = {
    SC2ActionType.ASSIGN_WORKERS: "일꾼 배정",
    SC2ActionType.BUILD_STRUCTURE: "건설",
    SC2ActionType.TRAIN_UNIT: "생산",
    SC2ActionType.MOVE_GROUP: "이동",
    SC2ActionType.ATTACK_MOVE: "공격 이동",
    SC2ActionType.REPAIR: "수리",
    SC2ActionType.OBSERVE: "정찰",
    SC2ActionType.MOVE_CAMERA: "카메라 이동",
}
"""Korean labels for semantic action types, used in error narration."""

SC2_KOREAN_GROUP_SUBJECT_NAMES: Final[dict[str, str]] = {
    "available combat units": "전투 가능 병력",
    "all marines": "마린 전 병력",
    "worker_scout": "정찰 SCV",
}
"""Lowercased free-text interpreter unit-group phrases translated into Korean."""

SC2_UNENFORCED_CONSTRAINT_DISCLOSURES: Final[dict[str, str]] = {
    "keep SCV production continuous": (
        "지속 생산은 아직 지원되지 않아 이번 1회 생산 명령만 실행했습니다. "
        "계속 생산하려면 같은 명령을 다시 말해 주세요."
    ),
}
"""Plan constraints no runtime component enforces, with honest Korean disclosure.

A fully applied plan that carries one of these constraints is narrated as
``partially_executed`` with the disclosure line instead of pretending the
constraint took effect.
"""

_COUNTED_GROUP_SUBJECT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(\d+)\s+(.+)"
)
"""Parser for counted interpreter unit-group phrases such as ``6 Marines``."""

SC2_ACTION_REFUSAL_KOREAN_REASONS: Final[dict[str, str]] = {
    "insufficient_units": "요청한 유닛 그룹에 해당하는 아군 유닛이 없습니다",
    "insufficient_workers": "사용할 수 있는 일꾼이 없습니다",
    "no_gather_target": "채취할 수 있는 자원 대상이 없습니다 (가스는 완성된 정제소가 필요합니다)",
    "no_damaged_repair_target": "수리할 손상된 대상이 없습니다",
    "no_ready_idle_producer": "준비된 유휴 생산 건물이 없습니다",
    "unaffordable": "자원이 부족합니다",
    "unresolvable_target": "지정한 위치를 지도에서 해석할 수 없습니다",
    "non_positive_count": "요청 수량이 1 미만입니다",
    "missing_producer_metadata": "생산 건물 정보가 계획에 없습니다",
    "producers_stalled": "생산 건물이 훈련 명령을 받지 못했습니다",
    "missing_camera_capability": "현재 SC2 런타임 어댑터가 카메라 이동 API를 제공하지 않습니다",
    "camera_refused": "SC2 런타임이 카메라 이동 요청을 거부했습니다",
    "ambiguous_camera_target": "카메라 대상 위치가 여러 후보와 맞아 하나로 확정할 수 없습니다",
    "unknown_camera_target": "지정한 카메라 대상이 지원되는 semantic target이 아닙니다",
    "unscouted_camera_target": "지정한 카메라 대상은 아직 정찰/관측되지 않아 안전하게 이동할 수 없습니다",
}
"""Machine-readable adapter refusal details translated into Korean reasons."""

DEFAULT_SC2_REJECTION_ALTERNATIVE: Final[str] = (
    "명령을 하나로 구체화해 다시 말해 주세요. "
    "예: 상태 알려줘 / SCV 계속 찍어 / 본진에 배럭 지어."
)
"""Fallback actionable alternative when a rejection carries no alternative."""

DEFAULT_SC2_REJECTION_REASON: Final[str] = "명령을 실행할 수 없다고 판정되었습니다."
"""Fallback reason when a rejection carries no readable reason."""


@dataclass(frozen=True)
class SC2NarrationResponse:
    """One commander-facing Korean narration outcome."""

    response_text: str
    status: SC2NarrationStatus
    intent_name: str = ""
    detail_lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.response_text.strip():
            raise ValueError("SC2 narration response_text must be non-empty.")
        if self.status not in SC2_NARRATION_STATUSES:
            supported = ", ".join(sorted(SC2_NARRATION_STATUSES))
            raise ValueError(
                f"SC2 narration status must be one of: {supported}. "
                f"Unknown status: {self.status!r}."
            )
        object.__setattr__(self, "intent_name", str(self.intent_name))
        object.__setattr__(
            self,
            "detail_lines",
            tuple(str(line) for line in self.detail_lines),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready narrated response payload."""

        return {
            "response_text": self.response_text,
            "status": self.status,
            "intent_name": self.intent_name,
            "detail_lines": list(self.detail_lines),
        }


@runtime_checkable
class SC2NarratorInterface(Protocol):
    """Boundary for producing commander-facing narration from SC2 outcomes."""

    def narrate_plan_result(self, result: SC2PlanExecutionResult) -> SC2NarrationResponse:
        """Render one structured plan execution result into Korean narration."""

    def narrate_state(self, state: SC2CommanderState) -> SC2NarrationResponse:
        """Render one commander semantic state snapshot into Korean narration."""

    def narrate_rejection(self, feasibility: object) -> SC2NarrationResponse:
        """Render a rejected command (feasibility result or reason string)."""


@dataclass(frozen=True)
class SC2KoreanNarrator:
    """Default Korean narrator for live SC2 plan results and state summaries.

    ``enforced_constraints`` names the plan constraints some runtime
    component genuinely enforces (for example ``keep SCV production
    continuous`` once a standing-order controller is registered by the live
    session). Those constraints are narrated as full execution instead of
    the unenforced-constraint partial disclosure. The default (empty set)
    keeps today's honest ``지속 생산 미지원`` disclosure for every session
    without standing-order support.
    """

    enforced_constraints: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if isinstance(self.enforced_constraints, (str, bytes)):
            raise TypeError(
                "SC2 narrator enforced_constraints must be a set of constraint "
                "strings, not a single string."
            )
        object.__setattr__(
            self,
            "enforced_constraints",
            frozenset(str(item) for item in self.enforced_constraints),
        )

    def narrate_plan_result(self, result: SC2PlanExecutionResult) -> SC2NarrationResponse:
        """Render one structured plan execution result into Korean narration.

        Full success narrates every applied action; an observe-only success is
        a read-only state report; a successful plan carrying a constraint no
        runtime enforces (for example continuous production) is disclosed as
        partial unless the constraint is listed in
        :attr:`enforced_constraints`; anything else is clearly narrated as
        partial or blocked so skipped or unsupported work is never reported
        as success.
        """

        if result.success:
            if _is_observe_only(result.plan):
                return _render_read_only_result(result)
            disclosures = _unenforced_constraint_disclosures(
                result.plan, self.enforced_constraints
            )
            if disclosures:
                return _render_constraint_disclosure_result(result, disclosures)
            return _render_full_success(result)
        if result.applied_actions:
            return _render_partial_result(result)
        return _render_blocked_result(result)

    def narrate_state(self, state: SC2CommanderState) -> SC2NarrationResponse:
        """Render one commander semantic state snapshot into Korean narration."""

        lines = render_sc2_state_lines(state)
        return SC2NarrationResponse(
            response_text=f"현재 상황을 보고합니다. {_join_sentences(lines)}",
            status="read_only",
            detail_lines=lines,
        )

    def narrate_rejection(self, feasibility: object) -> SC2NarrationResponse:
        """Render a rejected command from a feasibility-shaped object or string."""

        reasons, alternative, intent_name = _rejection_fields(feasibility)
        reason_text = ", ".join(reasons)
        detail_lines = (
            *(f"이유: {reason}" for reason in reasons),
            f"대안: {alternative}",
        )
        return SC2NarrationResponse(
            response_text=(
                f"실행하지 않았습니다. 이유: {reason_text}. 대안: {alternative}"
            ),
            status="blocked",
            intent_name=intent_name,
            detail_lines=detail_lines,
        )


DEFAULT_SC2_NARRATOR: Final[SC2KoreanNarrator] = SC2KoreanNarrator()
"""Shared default narrator used by the module-level convenience functions."""


def narrate_sc2_plan_result(result: SC2PlanExecutionResult) -> SC2NarrationResponse:
    """Narrate one plan execution result with the default Korean narrator."""

    return DEFAULT_SC2_NARRATOR.narrate_plan_result(result)


def narrate_sc2_state(state: SC2CommanderState) -> SC2NarrationResponse:
    """Narrate one commander state snapshot with the default Korean narrator."""

    return DEFAULT_SC2_NARRATOR.narrate_state(state)


def render_sc2_state_lines(state: SC2CommanderState) -> tuple[str, ...]:
    """Render one commander state snapshot into Korean summary lines."""

    worker_count = state.own_units.get(SC2_WORKER_TYPE_NAME, 0)
    lines = [
        f"미네랄 {state.minerals}, 가스 {state.vespene}",
        f"보급 {state.supply_used}/{state.supply_cap} (여유 {state.supply_left})",
        f"일꾼 {worker_count}기 (유휴 {state.idle_worker_count}기)",
        _army_line(state),
        _structure_line(state),
        _enemy_line(state),
    ]
    if not state.observation_complete:
        lines.append(
            "정찰 정보가 불완전합니다. 일부 관측값이 누락되어 기본값으로 "
            f"보고되었습니다 (누락 {len(state.observation_notes)}건)"
        )
    return tuple(lines)


def _is_observe_only(plan: SC2ExecutionPlan) -> bool:
    """Return whether the plan consists exclusively of observe actions."""

    return bool(plan.ordered_actions) and all(
        action.action_type is SC2ActionType.OBSERVE for action in plan.ordered_actions
    )


def _render_full_success(result: SC2PlanExecutionResult) -> SC2NarrationResponse:
    action_lines = tuple(
        _action_line(action, _action_issue(result, action))
        for action in result.applied_actions
    )
    return SC2NarrationResponse(
        response_text=f"명령을 실행했습니다. {', '.join(action_lines)}.",
        status="executed",
        intent_name=result.plan.intent_name,
        detail_lines=action_lines,
    )


def _render_constraint_disclosure_result(
    result: SC2PlanExecutionResult,
    disclosures: tuple[str, ...],
) -> SC2NarrationResponse:
    """Narrate a fully applied plan whose constraints no runtime enforces."""

    action_lines = [
        _action_line(action, _action_issue(result, action))
        for action in result.applied_actions
    ]
    disclosure_text = " ".join(disclosures)
    detail_lines = (
        *(f"실행: {line}" for line in action_lines),
        *(f"보류: {line}" for line in disclosures),
    )
    return SC2NarrationResponse(
        response_text=(
            f"일부만 실행되었습니다. 실행: {', '.join(action_lines)}. "
            f"보류: {disclosure_text}"
        ),
        status="partially_executed",
        intent_name=result.plan.intent_name,
        detail_lines=detail_lines,
    )


def _unenforced_constraint_disclosures(
    plan: SC2ExecutionPlan,
    enforced_constraints: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Return Korean disclosures for plan constraints no runtime enforces.

    Constraints listed in ``enforced_constraints`` (for example the
    continuous-production constraint once a standing-order controller is
    wired into the session) are excluded: a runtime really enforces them, so
    disclosing them as dropped would itself be dishonest narration.
    """

    return tuple(
        SC2_UNENFORCED_CONSTRAINT_DISCLOSURES[constraint]
        for constraint in plan.constraints
        if constraint in SC2_UNENFORCED_CONSTRAINT_DISCLOSURES
        and constraint not in enforced_constraints
    )


def _render_read_only_result(result: SC2PlanExecutionResult) -> SC2NarrationResponse:
    state = _state_from_observations(result.audit)
    if state is None:
        fallback_line = "관측 스냅샷이 없어 상태 요약을 제공할 수 없습니다"
        return SC2NarrationResponse(
            response_text=f"전장 상태를 확인했습니다. {fallback_line}.",
            status="read_only",
            intent_name=result.plan.intent_name,
            detail_lines=(fallback_line,),
        )
    lines = render_sc2_state_lines(state)
    return SC2NarrationResponse(
        response_text=f"전장 상태를 확인했습니다. {_join_sentences(lines)}",
        status="read_only",
        intent_name=result.plan.intent_name,
        detail_lines=lines,
    )


def _render_partial_result(result: SC2PlanExecutionResult) -> SC2NarrationResponse:
    applied_lines = [
        _action_line(action, _action_issue(result, action))
        for action in result.applied_actions
    ]
    skipped_lines = [_action_line(action) for action in result.skipped_actions]
    error_lines = [_error_line(error) for error in result.errors]

    parts = ["일부만 실행되었습니다.", f"실행: {', '.join(applied_lines)}."]
    if skipped_lines:
        parts.append(f"보류: {', '.join(skipped_lines)}.")
    if error_lines:
        parts.append(f"오류: {' / '.join(error_lines)}")

    detail_lines = (
        *(f"실행: {line}" for line in applied_lines),
        *(f"보류: {line}" for line in skipped_lines),
        *(f"오류: {line}" for line in error_lines),
    )
    return SC2NarrationResponse(
        response_text=" ".join(parts),
        status="partially_executed",
        intent_name=result.plan.intent_name,
        detail_lines=detail_lines,
    )


def _render_blocked_result(result: SC2PlanExecutionResult) -> SC2NarrationResponse:
    error_lines = [_error_line(error) for error in result.errors]
    if not error_lines:
        error_lines = ["실행기가 어떤 동작도 적용하지 않았습니다."]
    skipped_lines = [_action_line(action) for action in result.skipped_actions]

    detail_lines = (
        *(f"오류: {line}" for line in error_lines),
        *(f"보류: {line}" for line in skipped_lines),
    )
    return SC2NarrationResponse(
        response_text=f"명령을 실행하지 못했습니다. 이유: {' / '.join(error_lines)}",
        status="blocked",
        intent_name=result.plan.intent_name,
        detail_lines=detail_lines,
    )


def _state_from_observations(audit: Mapping[str, object]) -> SC2CommanderState | None:
    """Find and rebuild a commander state payload from result observations."""

    observations = audit.get("observations")
    if not isinstance(observations, Mapping):
        return None
    for key in sorted(observations, key=str):
        payload = observations[key]
        if isinstance(payload, SC2CommanderState):
            return payload
        if not isinstance(payload, Mapping):
            continue
        nested = payload.get("state")
        if isinstance(nested, SC2CommanderState):
            return nested
        if isinstance(nested, Mapping):
            state = _coerce_state_payload(nested)
            if state is not None:
                return state
        state = _coerce_state_payload(payload)
        if state is not None:
            return state
    return None


def _coerce_state_payload(payload: Mapping[str, object]) -> SC2CommanderState | None:
    """Rebuild a commander state from a JSON-ready mapping, or ``None``."""

    field_names = {state_field.name for state_field in fields(SC2CommanderState)}
    kwargs = {key: value for key, value in payload.items() if key in field_names}
    if not kwargs:
        return None
    try:
        return SC2CommanderState(**kwargs)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _ActionIssue:
    """Requested versus issued counts for one partially applied action."""

    requested: int
    issued: int


def _action_issue(
    result: SC2PlanExecutionResult,
    action: SC2CommandAction,
) -> _ActionIssue | None:
    """Read the audited issuance shortfall for one applied action, if any."""

    reports = result.audit.get("action_reports")
    if not isinstance(reports, Mapping):
        return None
    try:
        index = result.plan.ordered_actions.index(action)
    except ValueError:
        return None
    report = reports.get(str(index))
    if not isinstance(report, Mapping):
        return None
    requested = report.get("requested_count")
    issued = report.get("issued_count")
    if not isinstance(requested, int) or not isinstance(issued, int):
        return None
    if isinstance(requested, bool) or isinstance(issued, bool):
        return None
    if issued >= requested:
        return None
    return _ActionIssue(requested=requested, issued=issued)


def _assign_workers_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    target = _translate_target(action.target) or "자원"
    if issue is not None:
        return f"일꾼 {issue.requested}기 중 {issue.issued}기만 {target} 채취에 배정"
    return f"일꾼 {action.count}기를 {target} 채취에 배정"


def _train_unit_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    subject = _translate_type_name(action.subject)
    if issue is not None:
        return f"{subject} {issue.requested}기 중 {issue.issued}기만 생산 명령"
    return f"{subject} {action.count}기 생산 명령"


def _build_structure_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    subject = _translate_type_name(action.subject)
    target = _translate_target(action.target)
    if target:
        return f"{subject} 건설 시작 ({target})"
    return f"{subject} 건설 시작"


def _move_group_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    subject = _translate_group_subject(action.subject)
    target = _translate_target(action.target) or "지정 위치"
    if issue is not None:
        return (
            f"{subject} 중 {issue.issued}기만 {target}"
            f"{_ro_particle(target)} 이동"
        )
    return f"{subject} 그룹을 {target}{_ro_particle(target)} 이동"


def _attack_move_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    subject = _translate_group_subject(action.subject)
    target = _translate_target(action.target) or "지정 위치"
    if issue is not None:
        return (
            f"{subject} 중 {issue.issued}기만 {target}"
            f"{_ro_particle(target)} 공격 이동"
        )
    return f"{subject} 그룹이 {target}{_ro_particle(target)} 공격 이동"


def _repair_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    target = _translate_target(action.target) or "지정 대상"
    if issue is not None:
        return f"SCV {issue.requested}기 중 {issue.issued}기만 {target} 수리"
    return f"SCV {action.count}기가 {target} 수리"


def _observe_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    return "전장 상태 확인"


def _move_camera_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    target = _translate_target(action.target) or "지정 위치"
    return f"카메라를 {target}{_ro_particle(target)} 이동"


_SC2_ACTION_LINE_RENDERERS: Final[
    dict[SC2ActionType, Callable[[SC2CommandAction, _ActionIssue | None], str]]
] = {
    SC2ActionType.ASSIGN_WORKERS: _assign_workers_line,
    SC2ActionType.BUILD_STRUCTURE: _build_structure_line,
    SC2ActionType.TRAIN_UNIT: _train_unit_line,
    SC2ActionType.MOVE_GROUP: _move_group_line,
    SC2ActionType.ATTACK_MOVE: _attack_move_line,
    SC2ActionType.REPAIR: _repair_line,
    SC2ActionType.OBSERVE: _observe_line,
    SC2ActionType.MOVE_CAMERA: _move_camera_line,
}
"""One Korean line renderer per stable semantic SC2 action type."""


def _action_line(
    action: SC2CommandAction,
    issue: _ActionIssue | None = None,
) -> str:
    """Render one semantic action into a commander-facing Korean line.

    ``issue`` carries the audited requested-versus-issued shortfall for a
    partially applied action so the line states the honest issued count.
    """

    renderer = _SC2_ACTION_LINE_RENDERERS.get(action.action_type)
    if renderer is None:  # Defensive: every public action type is registered.
        return f"{action.action_type.value} 동작 수행"
    return renderer(action, issue)


def _error_line(error: SC2ExecutionError) -> str:
    """Render one structured execution error into a Korean reason line."""

    if error.exception_type == "PartialActionApplication":
        requested = error.metadata.get("requested_count")
        issued = error.metadata.get("issued_count")
        label = "명령"
        if error.action_type is not None:
            label = SC2_ACTION_TYPE_KOREAN_LABELS.get(error.action_type, label)
        return (
            f"{label} 부분 실행: 요청 {requested}기 중 {issued}기만 "
            "실제 명령으로 실행되었습니다 (가용 자원/유닛 부족)."
        )
    if error.exception_type == "ActionRefused":
        label = "명령"
        if error.action_type is not None:
            label = SC2_ACTION_TYPE_KOREAN_LABELS.get(error.action_type, label)
        detail = str(error.metadata.get("detail", ""))
        reason = SC2_ACTION_REFUSAL_KOREAN_REASONS.get(detail)
        if reason is None:
            reason = _sanitized_action_refusal_reason(detail or error.message)
        if reason is None:
            reason = f"실행기가 동작을 거부했습니다 (세부: {detail or error.message})"
        return f"{label} 거부: {reason}."
    if error.exception_type == "MissingRuntimeAdapter":
        return (
            "게임 연결이 없습니다. StarCraft II를 실행하고 BotAI 런타임을 "
            f"연결한 뒤 다시 시도해 주세요. 세부: {error.message}"
        )
    if error.exception_type == "MissingBotCapability":
        expected = error.metadata.get("expected_method")
        expected_text = f" (필요 기능: {expected})" if expected else ""
        return (
            f"실행기에 해당 기능이 연결되지 않았습니다{expected_text}. "
            f"세부: {error.message}"
        )
    if error.action_type is not None:
        label = SC2_ACTION_TYPE_KOREAN_LABELS.get(error.action_type)
        if label is not None:
            return f"{label} 동작 실패: {error.message}"
    return f"실행 실패: {error.message}"


def _sanitized_action_refusal_reason(detail: str) -> str | None:
    """Convert adapter debug details into commander-safe Korean guidance."""

    normalized = str(detail or "")
    if "unsupported SC2 target location" in normalized:
        return (
            "위치 표현을 현재 지도 의미 좌표로 연결하지 못했습니다. "
            "LLM은 본진, 본진 입구, 앞마당, 본진 가스처럼 관측 가능한 "
            "semantic target 중 하나로 다시 좁혀야 합니다."
        )
    if "no_safe_placement" in normalized:
        return (
            "보이는 지형 안에서 안전한 건설 칸을 찾지 못했습니다. "
            "길목/미네랄 라인을 피해서 본진 안쪽, 본진 앞, 본진 입구처럼 "
            "다른 위치를 지정해 주세요."
        )
    if "invalid_refinery_target" in normalized:
        if "no_free_geyser" in normalized:
            return (
                "사용 가능한 가스 간헐천을 찾지 못했습니다. 이미 가까운 "
                "간헐천에 정제소가 있거나 아직 다른 간헐천을 관측하지 못했습니다."
            )
        if "target_is_not_geyser" in normalized:
            return "정제소는 일반 지형이 아니라 가스 간헐천 위에만 지을 수 있습니다."
        return (
            "정제소 위치를 확정하지 못했습니다. 본진 가스 또는 앞마당 가스처럼 "
            "관측 가능한 간헐천 기준으로 다시 지정해 주세요."
        )
    return None


def _army_line(state: SC2CommanderState) -> str:
    parts = [
        f"{_translate_type_name(type_name)} {count}기"
        for type_name, count in sorted(state.own_units.items())
        if type_name != SC2_WORKER_TYPE_NAME and count > 0
    ]
    if parts:
        return f"병력: {', '.join(parts)}"
    if state.army_count > 0:
        return f"병력 {state.army_count}기 (세부 유형 미상)"
    return "병력 없음"


def _structure_line(state: SC2CommanderState) -> str:
    ready_parts = [
        f"{_translate_type_name(type_name)} {count}동"
        for type_name, count in sorted(state.own_structures.items())
        if count > 0
    ]
    progress_parts = [
        f"{_translate_type_name(type_name)} {count}동"
        for type_name, count in sorted(state.structures_in_progress.items())
        if count > 0
    ]
    segments = []
    if ready_parts:
        segments.append(f"완성 {', '.join(ready_parts)}")
    if progress_parts:
        segments.append(f"건설 중 {', '.join(progress_parts)}")
    if not segments:
        return "건물 없음"
    return f"건물: {' / '.join(segments)}"


def _enemy_line(state: SC2CommanderState) -> str:
    unit_parts = [
        f"{_translate_type_name(type_name)} {count}기"
        for type_name, count in sorted(state.visible_enemy_units.items())
        if count > 0
    ]
    structure_parts = [
        f"{_translate_type_name(type_name)} {count}동"
        for type_name, count in sorted(state.visible_enemy_structures.items())
        if count > 0
    ]
    if not unit_parts and not structure_parts:
        return "발견된 적 없음"
    return f"보이는 적: {', '.join((*unit_parts, *structure_parts))}"


def _translate_target(target: str) -> str:
    """Translate a semantic/resource/type target name into Korean."""

    cleaned = target.strip()
    if not cleaned:
        return ""
    semantic = SC2_KOREAN_TARGET_NAMES.get(cleaned)
    if semantic is not None:
        return semantic
    resource = SC2_KOREAN_RESOURCE_NAMES.get(cleaned.lower())
    if resource is not None:
        return resource
    return SC2_KOREAN_TYPE_NAMES.get(_normalized_type_key(cleaned), cleaned)


def _translate_type_name(name: str) -> str:
    """Translate a unit/structure type name into Korean, or keep it verbatim."""

    cleaned = name.strip()
    if not cleaned:
        return name
    return SC2_KOREAN_TYPE_NAMES.get(_normalized_type_key(cleaned), cleaned)


def _translate_group_subject(subject: str) -> str:
    """Translate a free-text unit-group phrase into commander-facing Korean.

    Handles the interpreter's English unit-group vocabulary: counted phrases
    (``6 Marines`` -> ``마린 6기``, ``1 SCV`` -> ``SCV 1기``), bare plurals
    (``Marines`` -> ``마린 전 병력``), and the generic phrases registered in
    :data:`SC2_KOREAN_GROUP_SUBJECT_NAMES`. Unknown phrases fall back to the
    plain type-name translation.
    """

    cleaned = subject.strip()
    if not cleaned:
        return subject
    special = SC2_KOREAN_GROUP_SUBJECT_NAMES.get(cleaned.lower())
    if special is not None:
        return special
    counted = _COUNTED_GROUP_SUBJECT_PATTERN.fullmatch(cleaned)
    if counted is not None:
        korean_type = _translate_singular_type(counted.group(2))
        if korean_type is not None:
            return f"{korean_type} {int(counted.group(1))}기"
    plural_type = _translate_singular_type(cleaned)
    if plural_type is not None and _normalized_type_key(cleaned) not in SC2_KOREAN_TYPE_NAMES:
        return f"{plural_type} 전 병력"
    return _translate_type_name(cleaned)


def _translate_singular_type(name: str) -> str | None:
    """Translate a possibly pluralized unit type name into Korean, or None."""

    key = _normalized_type_key(name.strip())
    if not key:
        return None
    direct = SC2_KOREAN_TYPE_NAMES.get(key)
    if direct is not None:
        return direct
    if key.endswith("S"):
        return SC2_KOREAN_TYPE_NAMES.get(key[:-1])
    return None


def _normalized_type_key(name: str) -> str:
    return "".join(name.split()).upper()


def _ro_particle(word: str) -> str:
    """Pick the Korean directional particle (로/으로) for the final syllable."""

    if not word:
        return "로"
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        final_consonant = (code - 0xAC00) % 28
        if final_consonant in (0, 8):  # No batchim, or ㄹ batchim.
            return "로"
        return "으로"
    return "(으)로"


def _join_sentences(lines: Iterable[str]) -> str:
    return " ".join(f"{line}." for line in lines)


def _rejection_fields(feasibility: object) -> tuple[tuple[str, ...], str, str]:
    """Extract reasons, alternative, and intent name from a rejection input."""

    if isinstance(feasibility, str):
        reasons = _string_items(feasibility)
        alternative = ""
        intent_name = ""
    else:
        raw_reasons = getattr(feasibility, "reasons", None)
        if raw_reasons is None:
            raw_reasons = getattr(feasibility, "reason", None)
        reasons = _string_items(raw_reasons)
        alternative = _string_or_empty(getattr(feasibility, "alternative", ""))
        intent_name = _string_or_empty(getattr(feasibility, "intent_name", ""))
        if not intent_name:
            intent_name = _string_or_empty(getattr(feasibility, "intent", ""))
    if not reasons:
        reasons = (DEFAULT_SC2_REJECTION_REASON,)
    if not alternative:
        alternative = DEFAULT_SC2_REJECTION_ALTERNATIVE
    return reasons, alternative, intent_name


def _string_items(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, Iterable):
        items = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text)
        return tuple(items)
    text = str(value).strip()
    return (text,) if text else ()


def _string_or_empty(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""
