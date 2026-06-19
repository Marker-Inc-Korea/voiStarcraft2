"""Human-interruptible commander policy tree for SC2 collaboration.

Issue #10 explores the behavior-tree shape from recent LLM + StarCraft II
collaboration work. This module intentionally starts with a small stdlib-only
surface: a strategy profile selects deterministic policy leaves, and those
leaves may only activate existing standing orders or recommend Korean
utterances that still pass the normal interpreter, feasibility, planner, and
executor gates.

The tree is not a bot brain and never calls python-sc2. It is the bounded seam
where a future SOTA model may suggest a strategy profile while the human can
override or pause automation.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from starcraft_commander.policy_modulation import reject_raw_policy_control_keys
from starcraft_commander.standing_orders import (
    STANDING_ORDER_KINDS,
    STANDING_ORDER_KOREAN_LABELS,
)


PolicyProfileKey = str
"""Stable profile key selected by a human, UI, or bounded model output."""

PolicyHumanOverride = str
"""Human intervention token that can pause or force manual control."""

MANUAL_PROFILE_KEY: Final[str] = "manual_control"
"""Profile that disables autonomous policy leaves."""

DEFAULT_PROFILE_KEY: Final[str] = "safe_macro"
"""Default conservative collaboration profile."""

_RAW_CONTROL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_call",
        "api_calls",
        "python_sc2",
        "python_sc2_call",
        "raw_action",
        "raw_actions",
        "botai_method",
        "botai_methods",
    }
)
"""Model-output keys that would imply unsafe direct runtime control."""


@dataclass(frozen=True)
class CommanderStrategyProfile:
    """Bounded strategy option exposed to humans and future model selectors."""

    key: str
    korean_label: str
    description: str
    standing_order_kinds: tuple[str, ...] = ()
    recommended_utterances: tuple[str, ...] = ()
    intervention_modes: tuple[str, ...] = ("manual", "pause", "resume")
    risk_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string("key", self.key)
        _require_non_empty_string("korean_label", self.korean_label)
        _require_non_empty_string("description", self.description)
        object.__setattr__(
            self,
            "standing_order_kinds",
            _validate_standing_order_kinds(self.standing_order_kinds),
        )
        object.__setattr__(
            self,
            "recommended_utterances",
            _validate_string_tuple(
                "recommended_utterances", self.recommended_utterances
            ),
        )
        object.__setattr__(
            self,
            "intervention_modes",
            _validate_string_tuple("intervention_modes", self.intervention_modes),
        )
        object.__setattr__(
            self,
            "risk_notes",
            _validate_string_tuple("risk_notes", self.risk_notes),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready strategy-profile document."""

        return {
            "key": self.key,
            "korean_label": self.korean_label,
            "description": self.description,
            "standing_order_kinds": list(self.standing_order_kinds),
            "standing_order_labels": {
                kind: STANDING_ORDER_KOREAN_LABELS[kind]
                for kind in self.standing_order_kinds
            },
            "recommended_utterances": list(self.recommended_utterances),
            "intervention_modes": list(self.intervention_modes),
            "risk_notes": list(self.risk_notes),
        }


@dataclass(frozen=True)
class CommanderPolicyDecision:
    """Result of one policy-tree selection.

    ``accepted=False`` is a safe refusal: no policy leaf should activate and
    callers should surface ``rejection_reason`` to the user or logs.
    """

    profile_key: str
    accepted: bool
    standing_order_kinds: tuple[str, ...] = ()
    recommended_utterances: tuple[str, ...] = ()
    human_override: str = ""
    warnings: tuple[str, ...] = ()
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        _require_non_empty_string("profile_key", self.profile_key)
        object.__setattr__(
            self,
            "standing_order_kinds",
            _validate_standing_order_kinds(self.standing_order_kinds),
        )
        object.__setattr__(
            self,
            "recommended_utterances",
            _validate_string_tuple(
                "recommended_utterances", self.recommended_utterances
            ),
        )
        object.__setattr__(
            self,
            "warnings",
            _validate_string_tuple("warnings", self.warnings),
        )
        if type(self.human_override) is not str:
            raise ValueError("human_override must be a string.")
        if type(self.rejection_reason) is not str:
            raise ValueError("rejection_reason must be a string.")
        if not self.accepted and not self.rejection_reason:
            raise ValueError("rejected CommanderPolicyDecision needs a reason.")
        if self.accepted and self.rejection_reason:
            raise ValueError("accepted CommanderPolicyDecision cannot reject.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready policy decision."""

        return {
            "profile_key": self.profile_key,
            "accepted": self.accepted,
            "standing_order_kinds": list(self.standing_order_kinds),
            "standing_order_labels": {
                kind: STANDING_ORDER_KOREAN_LABELS[kind]
                for kind in self.standing_order_kinds
            },
            "recommended_utterances": list(self.recommended_utterances),
            "human_override": self.human_override,
            "warnings": list(self.warnings),
            "rejection_reason": self.rejection_reason,
        }


@runtime_checkable
class CommanderPolicyTreeInterface(Protocol):
    """Policy selection seam for UI or future bounded model output."""

    def decide(
        self,
        requested_profile: object = None,
        *,
        human_override: object = "",
        allow_autonomy: bool = True,
    ) -> CommanderPolicyDecision:
        """Select one bounded policy profile."""

    def decide_from_model_output(
        self,
        output: Mapping[str, object],
    ) -> CommanderPolicyDecision:
        """Validate bounded model output and select one policy profile."""

    def apply_to_standing_orders(
        self,
        decision: CommanderPolicyDecision,
        standing_orders: object,
    ) -> tuple[str, ...]:
        """Activate standing-order leaves through the existing controller."""


def _require_non_empty_string(name: str, value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_string_tuple(name: str, values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence of strings.")
    result = tuple(values)
    if any(type(value) is not str or not value.strip() for value in result):
        raise ValueError(f"{name} must contain only non-empty strings.")
    return result


def _validate_standing_order_kinds(values: object) -> tuple[str, ...]:
    result = _validate_string_tuple("standing_order_kinds", values)
    unknown = tuple(kind for kind in result if kind not in STANDING_ORDER_KINDS)
    if unknown:
        raise ValueError(
            "standing_order_kinds contains unsupported kinds: "
            + ", ".join(unknown)
        )
    return result


def _normalize_optional_text(value: object) -> str:
    if type(value) is not str:
        return ""
    return value.strip()


DEFAULT_COMMANDER_STRATEGY_PROFILES: Final[tuple[CommanderStrategyProfile, ...]] = (
    CommanderStrategyProfile(
        key=MANUAL_PROFILE_KEY,
        korean_label="수동 지휘",
        description="No autonomous policy leaf activates; the human issues commands.",
        intervention_modes=("manual", "pause", "resume"),
        risk_notes=("macro can stall unless the human keeps issuing commands",),
    ),
    CommanderStrategyProfile(
        key="safe_macro",
        korean_label="안전 운영",
        description="Stabilize economy and prevent supply block before pressure.",
        standing_order_kinds=("keep_worker_production", "prevent_supply_block"),
        recommended_utterances=("정찰 보내", "본진 입구 수비해"),
        risk_notes=("pressure is delayed until economy and supply are stable",),
    ),
    CommanderStrategyProfile(
        key="information_first",
        korean_label="정보 우선",
        description="Keep macro stable while prioritizing scouting and map awareness.",
        standing_order_kinds=("keep_worker_production", "prevent_supply_block"),
        recommended_utterances=("적 본진 정찰 보내", "앞마당 확인해"),
        risk_notes=("scouting units may be exposed without escort",),
    ),
    CommanderStrategyProfile(
        key="defensive_hold",
        korean_label="방어 고정",
        description="Preserve economy while holding the main ramp safely.",
        standing_order_kinds=("keep_worker_production", "prevent_supply_block"),
        recommended_utterances=("본진 입구 수비해", "마린 계속 뽑아"),
        risk_notes=("map control can fall behind if defense lasts too long",),
    ),
    CommanderStrategyProfile(
        key="pressure_when_safe",
        korean_label="안전 압박",
        description="Maintain macro, then pressure only after scouting or army setup.",
        standing_order_kinds=("keep_worker_production", "prevent_supply_block"),
        recommended_utterances=("마린 생산해", "적 앞마당 압박해"),
        risk_notes=("attack commands must still pass fresh feasibility checks",),
    ),
)
"""Profiles exposed as bounded strategy choices."""


class CommanderPolicyTree:
    """Small behavior-tree-like selector with human intervention hooks."""

    def __init__(
        self,
        profiles: Sequence[CommanderStrategyProfile] = DEFAULT_COMMANDER_STRATEGY_PROFILES,
    ) -> None:
        if not profiles:
            raise ValueError("CommanderPolicyTree requires at least one profile.")
        profile_by_key: dict[str, CommanderStrategyProfile] = {}
        for profile in profiles:
            if profile.key in profile_by_key:
                raise ValueError(f"duplicate policy profile key: {profile.key!r}.")
            profile_by_key[profile.key] = profile
        if MANUAL_PROFILE_KEY not in profile_by_key:
            raise ValueError("CommanderPolicyTree requires a manual_control profile.")
        self._lock = threading.Lock()
        self._profiles = dict(profile_by_key)
        self._last_decision = CommanderPolicyDecision(
            profile_key=MANUAL_PROFILE_KEY,
            accepted=True,
        )

    def profile_keys(self) -> tuple[str, ...]:
        """Return strategy keys in deterministic insertion order."""

        return tuple(self._profiles)

    def profiles(self) -> tuple[CommanderStrategyProfile, ...]:
        """Return bounded profiles in deterministic insertion order."""

        return tuple(self._profiles.values())

    def last_decision(self) -> CommanderPolicyDecision:
        """Return the most recent accepted or rejected decision."""

        with self._lock:
            return self._last_decision

    def decide(
        self,
        requested_profile: object = None,
        *,
        human_override: object = "",
        allow_autonomy: bool = True,
    ) -> CommanderPolicyDecision:
        """Select a bounded profile without issuing any game order."""

        warnings: list[str] = []
        normalized_override = _normalize_optional_text(human_override)
        if normalized_override in {"manual", "pause", "hold", "stop"}:
            warnings.append(f"human override active: {normalized_override}")
            decision = CommanderPolicyDecision(
                profile_key=MANUAL_PROFILE_KEY,
                accepted=True,
                human_override=normalized_override,
                warnings=tuple(warnings),
            )
            return self._remember(decision)
        if not allow_autonomy:
            decision = CommanderPolicyDecision(
                profile_key=MANUAL_PROFILE_KEY,
                accepted=True,
                human_override="autonomy_disabled",
                warnings=("autonomy disabled by human",),
            )
            return self._remember(decision)
        profile_key = _normalize_optional_text(requested_profile) or DEFAULT_PROFILE_KEY
        profile = self._profiles.get(profile_key)
        if profile is None:
            decision = CommanderPolicyDecision(
                profile_key=profile_key,
                accepted=False,
                rejection_reason=(
                    f"unknown policy profile: {profile_key}. "
                    f"Supported profiles: {', '.join(self.profile_keys())}."
                ),
            )
            return self._remember(decision)
        decision = CommanderPolicyDecision(
            profile_key=profile.key,
            accepted=True,
            standing_order_kinds=profile.standing_order_kinds,
            recommended_utterances=profile.recommended_utterances,
            human_override=normalized_override,
            warnings=tuple(warnings),
        )
        return self._remember(decision)

    def decide_from_model_output(
        self,
        output: Mapping[str, object],
    ) -> CommanderPolicyDecision:
        """Validate bounded model output and select one profile.

        The only accepted model control fields are profile/override/autonomy
        selectors. Any raw action/API field is rejected before reaching a
        standing-order controller or SC2 runtime.
        """

        if not isinstance(output, Mapping):
            return self._remember(
                CommanderPolicyDecision(
                    profile_key=DEFAULT_PROFILE_KEY,
                    accepted=False,
                    rejection_reason="policy model output must be a mapping.",
                )
            )
        unsafe_keys = tuple(
            str(key) for key in output if isinstance(key, str) and key in _RAW_CONTROL_KEYS
        )
        if unsafe_keys:
            return self._remember(
                CommanderPolicyDecision(
                    profile_key=DEFAULT_PROFILE_KEY,
                    accepted=False,
                    rejection_reason=(
                        "policy model output attempted raw runtime control: "
                        + ", ".join(sorted(unsafe_keys))
                    ),
                )
            )
        return self.decide(
            output.get("strategy_profile", output.get("profile")),
            human_override=output.get("human_override", ""),
            allow_autonomy=bool(output.get("allow_autonomy", True)),
        )

    def apply_to_standing_orders(
        self,
        decision: CommanderPolicyDecision,
        standing_orders: object,
    ) -> tuple[str, ...]:
        """Activate standing-order leaves through the existing controller.

        Returns only newly activated kinds. Rejected decisions and manual
        decisions activate nothing.
        """

        register = getattr(standing_orders, "register", None)
        if not callable(register):
            raise TypeError("standing_orders must implement register(kind).")
        if not decision.accepted:
            return ()
        newly_registered: list[str] = []
        for kind in decision.standing_order_kinds:
            if register(kind):
                newly_registered.append(kind)
        return tuple(newly_registered)

    def to_dict(
        self,
        *,
        modulation_snapshot: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Return a JSON-ready tree snapshot for dashboards."""

        document: dict[str, object] = {
            "profiles": [profile.to_dict() for profile in self.profiles()],
            "last_decision": self.last_decision().to_dict(),
        }
        if modulation_snapshot is not None:
            if not isinstance(modulation_snapshot, Mapping):
                raise ValueError("modulation_snapshot must be a mapping.")
            reject_raw_policy_control_keys(modulation_snapshot)
            document["policy_modulation"] = dict(modulation_snapshot)
        return document

    def _remember(self, decision: CommanderPolicyDecision) -> CommanderPolicyDecision:
        with self._lock:
            self._last_decision = decision
        return decision
