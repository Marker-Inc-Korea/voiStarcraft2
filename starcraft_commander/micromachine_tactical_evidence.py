"""Stdlib-only tactical-effect evidence for MicroMachine DSL interventions.

This classifier is deliberately independent of StarCraft II, s2client-api, and
MicroMachine imports. It only inspects JSON-like telemetry mappings and log text
that the patched C++ bot already emits into the blackboard/artifact directory.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final


TACTICAL_EFFECT_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {"passed", "partial", "missing", "refused", "unsupported"}
)
"""Stable status values exposed in soak reports and the cockpit API."""

TACTICAL_EFFECT_ORDER: Final[tuple[str, ...]] = (
    "pressure",
    "hold",
    "contain",
    "harass",
    "target_priority",
    "scout",
    "refused",
)
"""Deterministic effect ordering for reports."""

_SUPPORTED_EXPECTED_EFFECTS: Final[frozenset[str]] = frozenset(
    effect for effect in TACTICAL_EFFECT_ORDER if effect != "refused"
)

_EFFECT_ALIASES: Final[Mapping[str, str]] = {
    "aggression": "pressure",
    "aggressive": "pressure",
    "aggressive_pressure": "pressure",
    "attack": "pressure",
    "attack_timing": "pressure",
    "commitment": "pressure",
    "commitment_level": "pressure",
    "push": "pressure",
    "pressure": "pressure",
    "defend": "hold",
    "defense": "hold",
    "defensive": "hold",
    "defensive_hold": "hold",
    "force_retreat": "hold",
    "hold": "hold",
    "retreat": "hold",
    "contain": "contain",
    "containment": "contain",
    "enemy_natural_contain": "contain",
    "harass": "harass",
    "harassment": "harass",
    "worker_harass": "harass",
    "worker_line_harass": "harass",
    "target": "target_priority",
    "target_priority": "target_priority",
    "target_priority_bias": "target_priority",
    "target_priority_biases": "target_priority",
    "worker_line": "target_priority",
    "townhall": "target_priority",
    "production_target": "target_priority",
    "army_target": "target_priority",
    "map_control": "scout",
    "scout": "scout",
    "scouting": "scout",
    "scouting_map_control": "scout",
}

_FRAME_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(\d+):\s*(.*)$")
_TARGET_WORDS: Final[tuple[str, ...]] = (
    "worker_line",
    "worker line",
    "townhall",
    "town hall",
    "production",
    "army",
    "base",
    "mineral line",
)
_REFUSAL_MARKERS: Final[tuple[str, ...]] = (
    "clarification_required",
    "clarification prompt",
    "clarification needed",
    "refusal_reason",
    "refused",
    "unsupported tactical",
)
_ACTUAL_ORDER_KEYS: Final[tuple[str, ...]] = (
    "current_order",
    "main_attack_order",
    "attack_order",
    "last_order",
    "last_executed_order",
    "squad_order",
    "current_squad_order",
)
_ACTUAL_DECISION_KEYS: Final[tuple[str, ...]] = (
    "last_behavior_effect",
    "behavior_effect",
    "last_decision",
    "last_action",
    "decision",
    "behavior_status",
)


@dataclass(frozen=True)
class MicroMachineTacticalEffect:
    """One observed tactical behavior signal."""

    tag: str
    source: str
    detail: str
    frame: int | None = None
    manager: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "source": self.source,
            "detail": self.detail,
            "frame": self.frame,
            "manager": self.manager,
        }


@dataclass(frozen=True)
class MicroMachineTacticalEvidence:
    """JSON-ready tactical-effect evidence summary."""

    status: str
    observed_effects: tuple[str, ...]
    missing_effects: tuple[str, ...]
    expected_effects: tuple[str, ...] = ()
    unsupported_effects: tuple[str, ...] = ()
    refusal_reasons: tuple[str, ...] = ()
    consumed_axes_by_manager: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    source_paths: Mapping[str, str] = field(default_factory=dict)
    latest_frame: int = 0
    effects: tuple[MicroMachineTacticalEffect, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "passed" and not self.missing_effects

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ok": self.ok,
            "observed_effects": list(self.observed_effects),
            "missing_effects": list(self.missing_effects),
            "expected_effects": list(self.expected_effects),
            "unsupported_effects": list(self.unsupported_effects),
            "refusal_reasons": list(self.refusal_reasons),
            "consumed_axes_by_manager": {
                manager: list(axes)
                for manager, axes in self.consumed_axes_by_manager.items()
            },
            "source_paths": dict(self.source_paths),
            "latest_frame": self.latest_frame,
            "effects": [effect.to_dict() for effect in self.effects],
        }


def classify_micromachine_tactical_evidence(
    *,
    latest_telemetry: Mapping[str, object] | None = None,
    telemetry_archive: Sequence[Mapping[str, object]] = (),
    log_text: str = "",
    expected_effects: Sequence[str] = (),
    source_paths: Mapping[str, object] | None = None,
    refusal_reasons: Sequence[str] = (),
) -> MicroMachineTacticalEvidence:
    """Classify whether DSL modulation produced observable tactical effects."""

    latest = dict(latest_telemetry) if isinstance(latest_telemetry, Mapping) else {}
    archive = [dict(entry) for entry in telemetry_archive if isinstance(entry, Mapping)]
    telemetry_entries = [*archive, latest] if latest else archive
    normalized_expected, unsupported = _normalize_expected_effects(expected_effects)
    consumed_axes = _consumed_axes_by_manager(telemetry_entries)
    effects = [
        *_effects_from_telemetry(telemetry_entries),
        *_effects_from_log(log_text),
    ]
    observed = _ordered_unique(effect.tag for effect in effects)
    refusals = _ordered_unique(
        [
            *(
                reason.strip()
                for reason in refusal_reasons
                if isinstance(reason, str) and reason.strip()
            ),
            *_refusal_reasons_from_telemetry(telemetry_entries),
            *_refusal_reasons_from_log(log_text),
        ]
    )
    if refusals and "refused" not in observed:
        observed = _ordered_unique([*observed, "refused"])
        effects.append(
            MicroMachineTacticalEffect(
                tag="refused",
                source="refusal",
                detail=refusals[0],
                frame=_latest_frame(telemetry_entries),
            )
        )
    observed_expected = tuple(effect for effect in observed if effect != "refused")
    missing = tuple(effect for effect in normalized_expected if effect not in observed)
    source_payload = {
        str(key): str(value)
        for key, value in (source_paths or {}).items()
        if str(value)
    }
    status = _classify_status(
        expected=normalized_expected,
        missing=missing,
        observed=observed_expected,
        unsupported=unsupported,
        refusal_reasons=refusals,
        has_artifacts=bool(latest or archive or log_text.strip()),
    )
    return MicroMachineTacticalEvidence(
        status=status,
        observed_effects=tuple(observed),
        missing_effects=missing,
        expected_effects=normalized_expected,
        unsupported_effects=unsupported,
        refusal_reasons=tuple(refusals),
        consumed_axes_by_manager=consumed_axes,
        source_paths=source_payload,
        latest_frame=_latest_frame(telemetry_entries),
        effects=tuple(effects[:32]),
    )


def normalize_tactical_effect_tags(tags: Sequence[str]) -> tuple[str, ...]:
    """Normalize public effect/profile aliases into classifier effect tags."""

    normalized, _unsupported = _normalize_expected_effects(tags)
    return normalized


def _classify_status(
    *,
    expected: tuple[str, ...],
    missing: tuple[str, ...],
    observed: tuple[str, ...],
    unsupported: tuple[str, ...],
    refusal_reasons: tuple[str, ...],
    has_artifacts: bool,
) -> str:
    if unsupported:
        return "unsupported"
    if refusal_reasons:
        return "refused"
    if expected:
        if not missing:
            return "passed"
        if any(effect in observed for effect in expected):
            return "partial"
        return "missing"
    if observed:
        return "passed"
    return "missing" if has_artifacts else "unsupported"


def _normalize_expected_effects(
    tags: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    normalized: list[str] = []
    unsupported: list[str] = []
    for item in tags:
        if not isinstance(item, str) or not item.strip():
            continue
        key = item.strip().lower().replace("-", "_").replace(" ", "_")
        effect = _EFFECT_ALIASES.get(key, key)
        if effect in _SUPPORTED_EXPECTED_EFFECTS:
            normalized.append(effect)
        else:
            unsupported.append(item.strip())
    return _ordered_unique(normalized), _ordered_unique(unsupported)


def _effects_from_log(log_text: str) -> list[MicroMachineTacticalEffect]:
    effects: list[MicroMachineTacticalEffect] = []
    for line in log_text.splitlines():
        cleaned = " ".join(line.strip().split())
        if not cleaned:
            continue
        lowered = cleaned.lower()
        frame = _log_frame(cleaned)
        if _is_attack_order_line(lowered):
            effects.append(_log_effect("pressure", cleaned, frame))
        if "contain" in lowered or (
            "enemy natural" in lowered and _is_attack_order_line(lowered)
        ):
            effects.append(_log_effect("contain", cleaned, frame))
        if "harass" in lowered or (
            "worker_line" in lowered and ("calctargets" in lowered or "target" in lowered)
        ):
            effects.append(_log_effect("harass", cleaned, frame))
        if _is_target_priority_line(lowered):
            effects.append(_log_effect("target_priority", cleaned, frame))
        if _is_hold_line(lowered):
            effects.append(_log_effect("hold", cleaned, frame))
        if _is_scout_line(lowered):
            effects.append(_log_effect("scout", cleaned, frame))
    return _dedupe_effects(effects)


def _effects_from_telemetry(
    telemetry_entries: Sequence[Mapping[str, object]],
) -> list[MicroMachineTacticalEffect]:
    effects: list[MicroMachineTacticalEffect] = []
    for entry in telemetry_entries:
        frame = _int_value(entry.get("frame"))
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        for manager_name, payload in managers.items():
            if not isinstance(payload, Mapping):
                continue
            manager = str(manager_name)
            if _manager_reports_attack_order(payload):
                effects.append(_telemetry_effect("pressure", manager, payload, frame))
            if _manager_reports_hold(payload):
                effects.append(_telemetry_effect("hold", manager, payload, frame))
            if _manager_reports_contain(payload):
                effects.append(_telemetry_effect("contain", manager, payload, frame))
            if _manager_reports_harass(payload):
                effects.append(_telemetry_effect("harass", manager, payload, frame))
            if _manager_reports_target_selection(payload):
                effects.append(
                    _telemetry_effect("target_priority", manager, payload, frame)
                )
            if _manager_reports_scouting(manager, payload):
                effects.append(_telemetry_effect("scout", manager, payload, frame))
    return _dedupe_effects(effects)


def _is_attack_order_line(lowered: str) -> bool:
    if "cancel offensive" in lowered:
        return False
    return (
        "updateattacksquads" in lowered
        and ("new order" in lowered or "order =" in lowered)
        and "attack" in lowered
    ) or "mainattacksquad new order = attack" in lowered


def _is_target_priority_line(lowered: str) -> bool:
    if "calctargets" in lowered and "target" in lowered:
        return True
    if "target priority" in lowered or "selected target" in lowered:
        return True
    return "target" in lowered and any(word in lowered for word in _TARGET_WORDS)


def _is_hold_line(lowered: str) -> bool:
    return any(
        marker in lowered
        for marker in (
            "cancel offensive",
            "force retreat",
            "hold position",
            "new order = defend",
            "defensive hold",
            "retreat",
        )
    )


def _is_scout_line(lowered: str) -> bool:
    return (
        "scout" in lowered
        and ("policy" in lowered or "target" in lowered or "map control" in lowered)
    )


def _manager_reports_attack_order(payload: Mapping[str, object]) -> bool:
    if any("attack" in text for text in _actual_behavior_texts(payload)):
        return True
    return any(
        _number(payload.get(key)) > 0
        for key in ("active_attack_squad_count", "offensive_squad_count")
    )


def _manager_reports_hold(payload: Mapping[str, object]) -> bool:
    return any(
        marker in text
        for text in _actual_behavior_texts(payload)
        for marker in (
            "cancel offensive",
            "defend",
            "defensive hold",
            "force retreat",
            "hold position",
            "retreat",
        )
    )


def _manager_reports_contain(payload: Mapping[str, object]) -> bool:
    return any(
        marker in text
        for text in _actual_behavior_texts(payload)
        for marker in (
            "contain",
            "enemy natural",
            "enemy_natural",
            "enemy third",
            "enemy_third",
        )
    )


def _manager_reports_harass(payload: Mapping[str, object]) -> bool:
    if any("harass" in text for text in _actual_behavior_texts(payload)):
        return True
    return _number(payload.get("active_harass_squad_count")) > 0


def _manager_reports_target_selection(payload: Mapping[str, object]) -> bool:
    for key in (
        "selected_target_class",
        "last_selected_target_class",
        "target_class",
        "current_target_class",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _manager_reports_scouting(manager: str, payload: Mapping[str, object]) -> bool:
    if "scout" not in manager.lower():
        return False
    return any(
        bool(payload.get(key))
        for key in (
            "target_location",
            "current_scout_goal",
            "current_scout_status",
            "last_scout_target",
        )
    )


def _actual_behavior_texts(payload: Mapping[str, object]) -> tuple[str, ...]:
    texts: list[str] = []
    for key in (*_ACTUAL_ORDER_KEYS, *_ACTUAL_DECISION_KEYS):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value.strip().lower())
    return tuple(texts)


def _log_effect(tag: str, line: str, frame: int | None) -> MicroMachineTacticalEffect:
    return MicroMachineTacticalEffect(
        tag=tag,
        source="log",
        detail=line[:500],
        frame=frame,
    )


def _telemetry_effect(
    tag: str,
    manager: str,
    payload: Mapping[str, object],
    frame: int,
) -> MicroMachineTacticalEffect:
    detail_keys = (
        "current_order",
        "main_attack_order",
        "attack_order",
        "force_retreat",
        "hold_position",
        "contain_bias",
        "harassment_bias",
        "selected_target_class",
        "last_selected_target_class",
        "scope_location_intent",
        "scout_priority",
    )
    details = {
        key: payload.get(key)
        for key in detail_keys
        if payload.get(key) not in (None, "", 0, False)
    }
    return MicroMachineTacticalEffect(
        tag=tag,
        source="telemetry",
        detail=str(details)[:500],
        frame=frame,
        manager=manager,
    )


def _dedupe_effects(
    effects: Sequence[MicroMachineTacticalEffect],
) -> list[MicroMachineTacticalEffect]:
    result: list[MicroMachineTacticalEffect] = []
    seen: set[tuple[str, str, str, int | None, str]] = set()
    for effect in effects:
        key = (effect.tag, effect.source, effect.detail, effect.frame, effect.manager)
        if key in seen:
            continue
        seen.add(key)
        result.append(effect)
    return result


def _consumed_axes_by_manager(
    telemetry_entries: Sequence[Mapping[str, object]],
) -> dict[str, tuple[str, ...]]:
    axes_by_manager: dict[str, list[str]] = {}
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        for manager_name, payload in managers.items():
            if not isinstance(payload, Mapping):
                continue
            axes = _axis_list(payload.get("consumed_axes"))
            if not axes:
                continue
            target = axes_by_manager.setdefault(str(manager_name), [])
            for axis in axes:
                if axis not in target:
                    target.append(axis)
    return {manager: tuple(axes) for manager, axes in axes_by_manager.items()}


def _refusal_reasons_from_telemetry(
    telemetry_entries: Sequence[Mapping[str, object]],
) -> list[str]:
    reasons: list[str] = []
    for entry in telemetry_entries:
        last_failure = entry.get("last_failure")
        if isinstance(last_failure, str) and _is_refusal_text(last_failure):
            reasons.append(last_failure.strip())
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        for payload in managers.values():
            if not isinstance(payload, Mapping):
                continue
            for key in ("refusal_reason", "last_refusal", "clarification_prompt"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    reasons.append(value.strip())
    return reasons


def _refusal_reasons_from_log(log_text: str) -> list[str]:
    reasons: list[str] = []
    for line in log_text.splitlines():
        cleaned = " ".join(line.strip().split())
        if cleaned and _is_refusal_text(cleaned):
            reasons.append(cleaned[:500])
    return reasons


def _is_refusal_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def _latest_frame(telemetry_entries: Sequence[Mapping[str, object]]) -> int:
    return max((_int_value(entry.get("frame")) for entry in telemetry_entries), default=0)


def _log_frame(line: str) -> int | None:
    match = _FRAME_PREFIX_RE.match(line)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _axis_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [axis.strip() for axis in value.split(",") if axis.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(axis) for axis in value if str(axis).strip()]
    return []


def _int_value(value: object) -> int:
    if type(value) is bool:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _number(value: object) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _ordered_unique(values: Sequence[str] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    order_index = {tag: index for index, tag in enumerate(TACTICAL_EFFECT_ORDER)}
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(sorted(ordered, key=lambda item: order_index.get(item, 999)))
