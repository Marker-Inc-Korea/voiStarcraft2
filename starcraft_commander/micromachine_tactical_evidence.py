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
    "ability_cast",
    "tactical_nuke",
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
    "ability": "ability_cast",
    "ability_cast": "ability_cast",
    "cast_ability": "ability_cast",
    "execute_ability": "ability_cast",
    "nuke": "tactical_nuke",
    "nuclear_strike": "tactical_nuke",
    "tactical_nuke": "tactical_nuke",
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
_MIN_MAIN_ATTACK_HOME_DISTANCE: Final[float] = 12.0
_MIN_COMBAT_SCOUT_HOME_DISTANCE: Final[float] = 8.0
_TACTICAL_NUKE_CONFIRMATION_EFFECTS: Final[frozenset[str]] = frozenset(
    {
        "ghost_order:effect_nukecalldown",
        "persistent_effect:nukepersistent",
        "payload_consumed:terran_nuke",
    }
)
_EXPLICIT_ABILITY_CONFIRMATION_EFFECTS: Final[
    Mapping[str, frozenset[str]]
] = {
    "stimpack": frozenset({"actor_buff:stimpack", "buff:stimpack"}),
    "marine_stimpack": frozenset(
        {"actor_buff:stimpack", "buff:stimpack"}
    ),
    "marauder_stimpack": frozenset(
        {"actor_buff:stimpackmarauder", "buff:stimpackmarauder"}
    ),
    "emp": frozenset({"target_shield_or_energy:decreased"}),
    "snipe": frozenset(
        {
            "target_health:decreased_after_actor_commitment",
            "target_removed_after_actor_commitment",
        }
    ),
    "ghost_cloak": frozenset({"cloak_state:cloaked"}),
    "ghost_decloak": frozenset({"cloak_state:not_cloaked"}),
    "widow_mine_burrow": frozenset(
        {"unit_type:terran_widowmineburrowed"}
    ),
    "widow_mine_unburrow": frozenset({"unit_type:terran_widowmine"}),
    "lock_on": frozenset({"target_buff:lockon"}),
    "siege_mode": frozenset({"unit_type:terran_siegetanksieged"}),
    "unsiege": frozenset({"unit_type:terran_siegetank"}),
    "hellbat_mode": frozenset({"unit_type:terran_helliontank"}),
    "hellion_mode": frozenset({"unit_type:terran_hellion"}),
    "thor_high_impact_mode": frozenset({"unit_type:terran_thorap"}),
    "thor_explosive_mode": frozenset({"unit_type:terran_thor"}),
    "medivac_afterburners": frozenset(
        {"actor_buff:medivacspeedboost", "buff:medivacspeedboost"}
    ),
    "medivac_heal": frozenset({"target_health:increased"}),
    "medivac_load": frozenset(
        {"cargo:loaded", "cargo:passenger_loaded"}
    ),
    "medivac_unload_all": frozenset(
        {"cargo:empty", "cargo:unloaded"}
    ),
    "viking_fighter_mode": frozenset(
        {"unit_type:terran_vikingfighter"}
    ),
    "viking_assault_mode": frozenset(
        {"unit_type:terran_vikingassault"}
    ),
    "liberator_defender_mode": frozenset(
        {"unit_type:terran_liberatorag"}
    ),
    "liberator_fighter_mode": frozenset(
        {"unit_type:terran_liberator"}
    ),
    "banshee_cloak": frozenset({"cloak_state:cloaked"}),
    "banshee_decloak": frozenset({"cloak_state:not_cloaked"}),
    "auto_turret": frozenset({"new_unit_created:terran_autoturret"}),
    "interference_matrix": frozenset(
        {"target_buff:ravenscramblermissile"}
    ),
    "anti_armor_missile": frozenset(
        {"target_buff:ravenshreddermissilearmorreduction"}
    ),
    "yamato": frozenset(
        {
            "target_health:decreased_after_actor_commitment",
            "target_removed_after_actor_commitment",
        }
    ),
    "tactical_jump": frozenset({"position:tactical_jump_destination"}),
    "kd8_charge": frozenset(
        {
            "actor_order:effect_kd8charge",
            "actor_order:kd8charge_kd8charge",
            "ability_availability:consumed",
        }
    ),
    "reaper_grenade": frozenset(
        {
            "actor_order:effect_kd8charge",
            "actor_order:kd8charge_kd8charge",
            "ability_availability:consumed",
        }
    ),
}

_TERRAN_COMBAT_FAMILY_TYPES: Final[Mapping[str, frozenset[str]]] = {
    "marine": frozenset({"MARINE", "TERRANMARINE", "UNITTYPEIDTERRANMARINE"}),
    "marauder": frozenset(
        {"MARAUDER", "TERRANMARAUDER", "UNITTYPEIDTERRANMARAUDER"}
    ),
    "reaper": frozenset({"REAPER", "TERRANREAPER", "UNITTYPEIDTERRANREAPER"}),
    "ghost": frozenset({"GHOST", "TERRANGHOST", "UNITTYPEIDTERRANGHOST"}),
    "hellion_hellbat": frozenset(
        {
            "HELLION",
            "HELLIONTANK",
            "HELLBAT",
            "TERRANHELLION",
            "TERRANHELLIONTANK",
            "UNITTYPEIDTERRANHELLION",
            "UNITTYPEIDTERRANHELLIONTANK",
        }
    ),
    "widow_mine": frozenset(
        {
            "WIDOWMINE",
            "WIDOWMINEBURROWED",
            "TERRANWIDOWMINE",
            "TERRANWIDOWMINEBURROWED",
            "UNITTYPEIDTERRANWIDOWMINE",
            "UNITTYPEIDTERRANWIDOWMINEBURROWED",
        }
    ),
    "cyclone": frozenset(
        {"CYCLONE", "TERRANCYCLONE", "UNITTYPEIDTERRANCYCLONE"}
    ),
    "siege_tank": frozenset(
        {
            "SIEGETANK",
            "SIEGETANKSIEGED",
            "TANK",
            "TERRANSIEGETANK",
            "TERRANSIEGETANKSIEGED",
            "UNITTYPEIDTERRANSIEGETANK",
            "UNITTYPEIDTERRANSIEGETANKSIEGED",
        }
    ),
    "thor": frozenset(
        {
            "THOR",
            "THORAP",
            "TERRANTHOR",
            "TERRANTHORAP",
            "UNITTYPEIDTERRANTHOR",
            "UNITTYPEIDTERRANTHORAP",
        }
    ),
    "medivac": frozenset(
        {"MEDIVAC", "TERRANMEDIVAC", "UNITTYPEIDTERRANMEDIVAC"}
    ),
    "raven": frozenset({"RAVEN", "TERRANRAVEN", "UNITTYPEIDTERRANRAVEN"}),
    "viking": frozenset(
        {
            "VIKING",
            "VIKINGFIGHTER",
            "VIKINGASSAULT",
            "TERRANVIKINGFIGHTER",
            "TERRANVIKINGASSAULT",
            "UNITTYPEIDTERRANVIKINGFIGHTER",
            "UNITTYPEIDTERRANVIKINGASSAULT",
        }
    ),
    "banshee": frozenset(
        {"BANSHEE", "TERRANBANSHEE", "UNITTYPEIDTERRANBANSHEE"}
    ),
    "liberator": frozenset(
        {
            "LIBERATOR",
            "LIBERATORAG",
            "TERRANLIBERATOR",
            "TERRANLIBERATORAG",
            "UNITTYPEIDTERRANLIBERATOR",
            "UNITTYPEIDTERRANLIBERATORAG",
        }
    ),
    "battlecruiser": frozenset(
        {
            "BATTLECRUISER",
            "BC",
            "TERRANBATTLECRUISER",
            "UNITTYPEIDTERRANBATTLECRUISER",
        }
    ),
}
_TERRAN_COMBAT_FAMILY_ALIASES: Final[Mapping[str, str]] = {
    "hellion": "hellion_hellbat",
    "hellbat": "hellion_hellbat",
    "widowmine": "widow_mine",
    "siegetank": "siege_tank",
    "tank": "siege_tank",
    "bc": "battlecruiser",
    **{
        family.replace("_", ""): family
        for family in _TERRAN_COMBAT_FAMILY_TYPES
    },
}


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
    concrete_effects = _concrete_tactical_task_effects(telemetry_entries)
    current_update_ids = _current_update_ids(telemetry_entries)
    effects = [
        *_effects_from_telemetry(
            telemetry_entries,
            concrete_effects=concrete_effects,
            current_update_ids=current_update_ids,
        ),
        *_effects_from_log(log_text, concrete_effects=concrete_effects),
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


def explicit_ability_confirmation_is_valid(
    *,
    ability: object,
    submission_action: object,
    confirmation_effect: object,
) -> bool:
    """Require telemetry to prove the requested ability's own SC2 effect."""

    normalized_ability = str(ability or "").strip().lower()
    if not normalized_ability or normalized_ability == "tactical_nuke":
        return False
    action_prefix = (
        str(submission_action or "")
        .split("|", 1)[0]
        .strip()
        .lower()
    )
    if action_prefix != f"voiexplicitability:{normalized_ability}":
        return False
    return explicit_ability_effect_is_valid(
        ability=normalized_ability,
        confirmation_effect=confirmation_effect,
    )


def explicit_ability_terminal_is_valid(
    payload: Mapping[str, object],
    *,
    issued_at_frame: int = 0,
) -> bool:
    """Validate one exact C++ ability-attempt terminal without weakening it."""

    ability = str(payload.get("ability", "") or "").strip().lower()
    if not ability or ability == "tactical_nuke":
        return False
    status = str(payload.get("status", "") or "").strip().lower()
    phase = str(payload.get("phase", "") or "").strip().lower()
    confirmation_state = str(
        payload.get("confirmation_state", "") or ""
    ).strip().lower()
    confirmation_effect = str(
        payload.get("confirmation_effect", "") or ""
    ).strip()
    attempt_generation = int(_number(payload.get("attempt_generation")))
    terminal_generation = int(
        _number(payload.get("terminal_attempt_generation"))
    )
    if (
        status != "completed"
        or attempt_generation <= 0
        or terminal_generation != attempt_generation
    ):
        return False

    confirmation_frame = _explicit_ability_confirmation_frame(payload)
    if (
        phase == "effect_observed"
        and confirmation_state == "confirmed"
        and confirmation_effect.lower().startswith("already_satisfied:")
    ):
        observed_effect = confirmation_effect.split(":", 1)[1]
        return (
            _number(payload.get("submitted_count")) == 0
            and _number(payload.get("confirmation_count")) > 0
            and confirmation_frame > 0
            and (
                not issued_at_frame
                or confirmation_frame >= issued_at_frame
            )
            and explicit_ability_effect_is_valid(
                ability=ability,
                confirmation_effect=observed_effect,
            )
        )

    submission_action = _explicit_ability_submission_action(payload)
    submission_frame = _explicit_ability_submission_frame(payload)
    submitted_generation = int(
        _number(payload.get("submitted_attempt_generation"))
    )
    if (
        _number(payload.get("submitted_count")) <= 0
        or not submission_action
        or submission_frame <= 0
        or (issued_at_frame and submission_frame < issued_at_frame)
        or submitted_generation != attempt_generation
    ):
        return False

    if phase == "effect_observed" and confirmation_state == "confirmed":
        return (
            _number(payload.get("confirmation_count")) > 0
            and confirmation_frame >= submission_frame
            and explicit_ability_confirmation_is_valid(
                ability=ability,
                submission_action=submission_action,
                confirmation_effect=confirmation_effect,
            )
        )

    observed_accepted_frame = int(
        _number(payload.get("observed_accepted_frame"))
    )
    observed_accepted_generation = int(
        _number(payload.get("observed_accepted_attempt_generation"))
    )
    observed_accepted_evidence = str(
        payload.get("observed_accepted_evidence", "") or ""
    ).strip()
    return (
        phase == "observed_accepted"
        and confirmation_state == "accepted"
        and observed_accepted_generation == attempt_generation
        and observed_accepted_frame >= submission_frame
        and bool(observed_accepted_evidence)
        and (
            confirmation_effect.lower().startswith(
                "accepted_without_observable_effect:"
            )
            or confirmation_effect == observed_accepted_evidence
        )
        and (
            ability not in {"kd8_charge", "reaper_grenade"}
            or explicit_ability_effect_is_valid(
                ability=ability,
                confirmation_effect=observed_accepted_evidence,
            )
        )
        and str(submission_action)
        .split("|", 1)[0]
        .strip()
        .lower()
        == f"voiexplicitability:{ability}"
    )


def explicit_ability_effect_is_valid(
    *,
    ability: object,
    confirmation_effect: object,
) -> bool:
    """Validate an observed state tag independently of command submission."""

    normalized_ability = str(ability or "").strip().lower()
    normalized_effect = str(confirmation_effect or "").strip().lower()
    allowed_effects = _EXPLICIT_ABILITY_CONFIRMATION_EFFECTS.get(
        normalized_ability
    )
    if not allowed_effects:
        return False
    if normalized_effect in allowed_effects:
        return True
    return False


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


def _effects_from_log(
    log_text: str,
    *,
    concrete_effects: frozenset[str] = frozenset(),
) -> list[MicroMachineTacticalEffect]:
    effects: list[MicroMachineTacticalEffect] = []
    for line in log_text.splitlines():
        cleaned = " ".join(line.strip().split())
        if not cleaned:
            continue
        lowered = cleaned.lower()
        frame = _log_frame(cleaned)
        if "pressure" not in concrete_effects and _is_attack_order_line(lowered):
            effects.append(_log_effect("pressure", cleaned, frame))
        if "pressure" not in concrete_effects and (
            "contain" in lowered
            or ("enemy natural" in lowered and _is_attack_order_line(lowered))
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
        if "scout" not in concrete_effects and _is_scout_line(lowered):
            effects.append(_log_effect("scout", cleaned, frame))
    return _dedupe_effects(effects)


def _effects_from_telemetry(
    telemetry_entries: Sequence[Mapping[str, object]],
    *,
    concrete_effects: frozenset[str] = frozenset(),
    current_update_ids: frozenset[str] = frozenset(),
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
            if _manager_reports_tactical_scout_task(
                manager,
                payload,
            ) or _manager_reports_combat_scout_command(
                manager,
                payload,
            ) or _manager_reports_operation_scout_command(
                manager,
                payload,
                current_update_ids=current_update_ids,
            ):
                effects.append(_telemetry_effect("scout", manager, payload, frame))
            if _manager_reports_tactical_pressure_task(
                manager,
                payload,
            ) or _manager_reports_main_attack_command(manager, payload):
                effects.append(_telemetry_effect("pressure", manager, payload, frame))
            if "pressure" not in concrete_effects and _manager_reports_attack_order(payload):
                effects.append(_telemetry_effect("pressure", manager, payload, frame))
            if _manager_reports_hold(payload):
                effects.append(_telemetry_effect("hold", manager, payload, frame))
            if (
                "pressure" not in concrete_effects
                or not _manager_reports_attack_order(payload)
            ) and _manager_reports_contain(payload):
                effects.append(_telemetry_effect("contain", manager, payload, frame))
            if _manager_reports_harass(payload):
                effects.append(_telemetry_effect("harass", manager, payload, frame))
            if _manager_reports_target_selection(payload):
                effects.append(
                    _telemetry_effect("target_priority", manager, payload, frame)
                )
            if "scout" not in concrete_effects and _manager_reports_scouting(manager, payload):
                effects.append(_telemetry_effect("scout", manager, payload, frame))
            if _manager_reports_explicit_ability_cast(
                manager,
                payload,
                current_update_ids=current_update_ids,
            ):
                effects.append(
                    _telemetry_effect(
                        "ability_cast",
                        manager,
                        payload,
                        _explicit_ability_confirmation_frame(payload),
                    )
                )
            if _manager_reports_tactical_nuke_cast(manager, payload):
                confirmation_frame = _tactical_nuke_confirmation_frame(payload)
                effects.append(
                    _telemetry_effect(
                        "ability_cast",
                        manager,
                        payload,
                        confirmation_frame,
                    )
                )
                effects.append(
                    _telemetry_effect(
                        "tactical_nuke",
                        manager,
                        payload,
                        confirmation_frame,
                    )
                )
    return _dedupe_effects(effects)


def _concrete_tactical_task_effects(
    telemetry_entries: Sequence[Mapping[str, object]],
) -> frozenset[str]:
    effects: set[str] = set()
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        tactical = managers.get("TacticalTask")
        if not isinstance(tactical, Mapping):
            continue
        task_type = str(tactical.get("task_type", "") or "")
        if task_type == "scout_with_units":
            effects.add("scout")
        elif task_type in {"pressure_with_main_army", "harass_with_units"}:
            effects.add("pressure")
    return frozenset(effects)


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


def _manager_reports_tactical_pressure_task(manager: str, payload: Mapping[str, object]) -> bool:
    if manager != "TacticalTask":
        return False
    # TacticalTask may report intent or a delegated command string before
    # CombatCommander proves that units actually moved. Do not count it as a
    # concrete live effect without the movement telemetry.
    return (
        str(payload.get("task_type", "") or "")
        in {"pressure_with_main_army", "harass_with_units"}
        and str(payload.get("status", "") or "") == "executing"
        and _number(payload.get("actual_command_issued_count")) > 0
        and "squad=MainAttack" in str(payload.get("last_actual_command", "") or "")
        and _number(payload.get("main_attack_max_home_distance"))
        >= _MIN_MAIN_ATTACK_HOME_DISTANCE
    )


def _manager_reports_tactical_scout_task(manager: str, payload: Mapping[str, object]) -> bool:
    if manager != "TacticalTask":
        return False
    # A delegated Scout order is not proof that a Marine moved. The task may
    # expose these fields directly in future telemetry; today CombatCommander
    # normally provides the exact unit identity and displacement evidence.
    return (
        str(payload.get("task_type", "") or "") == "scout_with_units"
        and str(payload.get("status", "") or "") == "executing"
        and _payload_reports_combat_scout_command(
            payload,
            command_count_key="actual_command_issued_count",
            command_key="last_actual_command",
        )
    )


def _manager_reports_main_attack_command(manager: str, payload: Mapping[str, object]) -> bool:
    if manager != "CombatCommander":
        return False
    return (
        _number(payload.get("main_attack_actual_command_issued_count")) > 0
        and _number(payload.get("main_attack_last_action_frame")) > 0
        and "squad=MainAttack"
        in str(payload.get("main_attack_last_issued_action", "") or "")
        and str(payload.get("main_attack_order_status", "") or "") == "Attack"
        and _number(payload.get("main_attack_max_home_distance"))
        >= _MIN_MAIN_ATTACK_HOME_DISTANCE
    )


def _manager_reports_combat_scout_command(manager: str, payload: Mapping[str, object]) -> bool:
    if manager != "CombatCommander":
        return False
    return (
        _number(payload.get("scout_last_action_frame")) > 0
        and _payload_reports_combat_scout_command(
            payload,
            command_count_key="scout_actual_command_issued_count",
            command_key="scout_last_issued_action",
        )
    )


def _manager_reports_operation_scout_command(
    manager: str,
    payload: Mapping[str, object],
    *,
    current_update_ids: frozenset[str],
) -> bool:
    if manager != "OperationDirector":
        return False
    policy_update_id = str(
        payload.get("policy_update_id", "") or ""
    ).strip()
    if (
        not policy_update_id
        or (
            current_update_ids
            and policy_update_id not in current_update_ids
        )
    ):
        return False
    operations = _mapping_items(payload.get("operations"))
    latest_generation_by_id: dict[str, int] = {}
    for operation in operations:
        operation_id = str(
            operation.get("operation_id", "") or ""
        ).strip()
        generation = int(_number(operation.get("generation")))
        if operation_id and generation > 0:
            latest_generation_by_id[operation_id] = max(
                generation,
                latest_generation_by_id.get(operation_id, 0),
            )
    return any(
        _operation_scout_evidence_is_valid(
            operation,
            policy_update_id=policy_update_id,
            latest_generation_by_id=latest_generation_by_id,
        )
        for operation in operations
    )


def _operation_scout_evidence_is_valid(
    operation: Mapping[str, object],
    *,
    policy_update_id: str,
    latest_generation_by_id: Mapping[str, int],
) -> bool:
    operation_id = str(
        operation.get("operation_id", "") or ""
    ).strip()
    generation = int(_number(operation.get("generation")))
    requested_task_type = str(
        operation.get("requested_task_type", "") or ""
    ).strip().lower()
    task_type = str(operation.get("task_type", "") or "").strip().lower()
    if (
        not operation_id
        or generation <= 0
        or latest_generation_by_id.get(operation_id) != generation
        or requested_task_type != "scout_with_units"
        or task_type not in {"scout", "scout_with_units"}
        or str(operation.get("squad_order", "") or "").strip().lower()
        != "scout"
        or str(operation.get("status", "") or "").strip().upper()
        not in {"MOVING", "ENGAGED", "COMPLETED"}
        or operation.get("submission_observed") is not True
        or _number(operation.get("submitted_frame")) <= 0
        or _number(operation.get("last_action_frame")) <= 0
        or _number(operation.get("max_home_distance"))
        < _MIN_COMBAT_SCOUT_HOME_DISTANCE
    ):
        return False

    assigned_tags = _positive_unique_unit_tags(
        operation.get("assigned_unit_tags")
    )
    assigned_count = int(_number(operation.get("assigned_count")))
    if (
        not assigned_tags
        or assigned_count != len(assigned_tags)
        or not _operation_requested_tags_match(
            operation,
            assigned_tags=assigned_tags,
        )
    ):
        return False

    requirements = tuple(
        requirement
        for requirement in _mapping_items(
            operation.get("requirement_progress")
        )
        if int(_number(requirement.get("target_count"))) > 0
    )
    if not requirements:
        return False
    target_count = sum(
        int(_number(requirement.get("target_count")))
        for requirement in requirements
    )
    if (
        target_count != assigned_count
        or (
            _number(operation.get("requirement_target_count")) > 0
            and int(
                _number(operation.get("requirement_target_count"))
            )
            != target_count
        )
    ):
        return False

    family_evidence = _mapping_items(operation.get("family_evidence"))
    for requirement in requirements:
        requested_family = _canonical_terran_combat_family(
            requirement.get("unit_type")
        )
        requested_count = int(_number(requirement.get("target_count")))
        requested_role = str(
            requirement.get("role", "") or ""
        ).strip()
        if (
            not requested_family
            or int(_number(requirement.get("assigned_count")))
            != requested_count
            or not _matching_operation_family_submission(
                family_evidence,
                policy_update_id=policy_update_id,
                operation_id=operation_id,
                generation=generation,
                requested_family=requested_family,
                requested_role=requested_role,
                requested_count=requested_count,
                assigned_tags=assigned_tags,
            )
        ):
            return False
    return True


def _matching_operation_family_submission(
    rows: Sequence[Mapping[str, object]],
    *,
    policy_update_id: str,
    operation_id: str,
    generation: int,
    requested_family: str,
    requested_role: str,
    requested_count: int,
    assigned_tags: tuple[int, ...],
) -> bool:
    candidates: list[Mapping[str, object]] = []
    for row in rows:
        row_family = _canonical_terran_combat_family(
            row.get("family")
        )
        row_type_family = _canonical_terran_combat_family(
            row.get("unit_type")
        )
        row_role = str(row.get("role", "") or "").strip()
        if (
            str(row.get("update_id", "") or "").strip()
            != policy_update_id
            or str(row.get("operation_id", "") or "").strip()
            != operation_id
            or int(_number(row.get("generation"))) != generation
            or not row_family
            or row_family != requested_family
            or row_type_family != requested_family
            or (requested_role and row_role != requested_role)
            or int(_number(row.get("attempt_generation"))) <= 0
        ):
            continue
        candidates.append(row)
    if not candidates:
        return False
    latest_attempt = max(
        int(_number(row.get("attempt_generation")))
        for row in candidates
    )
    latest_rows = tuple(
        row
        for row in candidates
        if int(_number(row.get("attempt_generation")))
        == latest_attempt
    )
    return any(
        int(_number(row.get("assigned"))) == requested_count
        and _number(row.get("attempted_count")) >= requested_count
        and _number(row.get("attempted_frame")) > 0
        and _number(row.get("submitted_count")) >= requested_count
        and _number(row.get("submitted_frame")) > 0
        and bool(str(row.get("action", "") or "").strip())
        and _family_evidence_tags_match(
            row,
            assigned_tags=assigned_tags,
        )
        for row in latest_rows
    )


def _operation_requested_tags_match(
    operation: Mapping[str, object],
    *,
    assigned_tags: tuple[int, ...],
) -> bool:
    assigned = set(assigned_tags)
    requested_tag = int(_number(operation.get("requested_unit_tag")))
    if requested_tag > 0 and requested_tag not in assigned:
        return False
    if "requested_unit_tags" in operation:
        requested_tags = _positive_unique_unit_tags(
            operation.get("requested_unit_tags")
        )
        if not requested_tags or set(requested_tags) != assigned:
            return False
    return True


def _family_evidence_tags_match(
    row: Mapping[str, object],
    *,
    assigned_tags: tuple[int, ...],
) -> bool:
    assigned = set(assigned_tags)
    for key in (
        "assigned_unit_tags",
        "submitted_unit_tags",
        "effect_unit_tags",
    ):
        if key not in row:
            continue
        tags = _positive_unique_unit_tags(row.get(key))
        if not tags or not set(tags).issubset(assigned):
            return False
        if (
            key == "effect_unit_tags"
            and _number(row.get("effect_count")) > 0
            and len(tags) != int(_number(row.get("effect_count")))
        ):
            return False
    return True


def _payload_reports_combat_scout_command(
    payload: Mapping[str, object],
    *,
    command_count_key: str,
    command_key: str,
) -> bool:
    requested_family = _requested_scout_family(payload) or "marine"
    commanded_family = _canonical_terran_combat_family(
        payload.get("scout_last_commanded_unit_type")
    )
    assigned_count = _scout_assigned_count(
        payload,
        requested_family=requested_family,
    )
    commanded_tag = int(
        _number(payload.get("scout_last_commanded_unit_tag"))
    )
    return (
        _number(payload.get(command_count_key)) > 0
        and "squad=Scout" in str(payload.get(command_key, "") or "")
        and commanded_family == requested_family
        and assigned_count > 0
        and _scout_assignment_matches_requested_count(
            payload,
            assigned_count=assigned_count,
        )
        and commanded_tag > 0
        and _scout_commanded_tag_matches_request(
            payload,
            commanded_tag=commanded_tag,
        )
        and _scout_generations_match(payload)
        and _scout_update_ids_match(payload)
        and _scout_family_max_home_distance(
            payload,
            requested_family=requested_family,
        )
        >= _MIN_COMBAT_SCOUT_HOME_DISTANCE
    )


def _scout_assignment_matches_requested_count(
    payload: Mapping[str, object],
    *,
    assigned_count: int | None = None,
) -> bool:
    assigned = (
        int(assigned_count)
        if assigned_count is not None
        else int(_number(payload.get("scout_marine_assigned_count")))
    )
    if assigned <= 0:
        return False
    requested_min = int(
        max(
            _number(payload.get("scout_scope_requested_min_units")),
            _number(payload.get("min_units")),
        )
    )
    requested_max = int(
        max(
            _number(payload.get("scout_scope_requested_max_units")),
            _number(payload.get("max_units")),
        )
    )
    if requested_min > 0 and requested_max > 0:
        if requested_min == requested_max:
            return assigned == requested_min
        return requested_min <= assigned <= requested_max
    if requested_min > 0:
        return assigned >= requested_min
    if requested_max > 0:
        return assigned <= requested_max
    return True


def _requested_scout_family(payload: Mapping[str, object]) -> str:
    for key in (
        "scout_requested_unit_family",
        "scout_scope_requested_unit_family",
        "requested_unit_family",
        "scout_requested_unit_type",
        "scout_scope_requested_unit_type",
        "requested_unit_type",
    ):
        family = _canonical_terran_combat_family(payload.get(key))
        if family:
            return family
    unit_classes = payload.get("unit_classes")
    if isinstance(unit_classes, Sequence) and not isinstance(
        unit_classes,
        (str, bytes, bytearray),
    ):
        families = {
            _canonical_terran_combat_family(unit_type)
            for unit_type in unit_classes
        } - {""}
        if len(families) == 1:
            return families.pop()
    if isinstance(unit_classes, str):
        families = {
            _canonical_terran_combat_family(unit_type)
            for unit_type in unit_classes.split(",")
        } - {""}
        if len(families) == 1:
            return families.pop()
    return ""


def _scout_assigned_count(
    payload: Mapping[str, object],
    *,
    requested_family: str,
) -> int:
    family_key = requested_family.replace("-", "_")
    return int(
        max(
            _number(payload.get(f"scout_{family_key}_assigned_count")),
            _number(payload.get("scout_requested_family_assigned_count")),
            _number(payload.get("scout_scope_assigned_unit_count")),
            (
                _number(payload.get("scout_marine_assigned_count"))
                if requested_family == "marine"
                else 0
            ),
        )
    )


def _scout_family_max_home_distance(
    payload: Mapping[str, object],
    *,
    requested_family: str,
) -> float:
    family_key = requested_family.replace("-", "_")
    return max(
        _number(payload.get(f"scout_{family_key}_max_home_distance")),
        _number(payload.get("scout_requested_family_max_home_distance")),
        _number(payload.get("scout_max_home_distance")),
        (
            _number(payload.get("scout_marine_max_home_distance"))
            if requested_family == "marine"
            else 0
        ),
    )


def _scout_commanded_tag_matches_request(
    payload: Mapping[str, object],
    *,
    commanded_tag: int,
) -> bool:
    requested_tag = int(
        max(
            _number(payload.get("scout_requested_unit_tag")),
            _number(payload.get("requested_unit_tag")),
        )
    )
    if requested_tag > 0 and requested_tag != commanded_tag:
        return False
    for key in ("scout_assigned_unit_tags", "assigned_unit_tags"):
        if key not in payload:
            continue
        assigned_tags = _positive_unique_unit_tags(payload.get(key))
        if not assigned_tags or commanded_tag not in assigned_tags:
            return False
    return True


def _scout_generations_match(payload: Mapping[str, object]) -> bool:
    requested = _single_positive_number(
        payload,
        (
            "scout_requested_generation",
            "scout_scope_generation",
            "generation",
        ),
    )
    commanded = _single_positive_number(
        payload,
        (
            "scout_command_generation",
            "scout_last_command_generation",
            "command_generation",
        ),
    )
    if requested < 0 or commanded < 0:
        return False
    if requested == 0 and commanded == 0:
        return True
    return requested > 0 and requested == commanded


def _scout_update_ids_match(payload: Mapping[str, object]) -> bool:
    requested_update_id = str(
        payload.get("update_id")
        or payload.get("policy_update_id")
        or ""
    ).strip()
    command_update_id = str(
        payload.get("scout_explicit_update_id")
        or payload.get("scout_command_update_id")
        or ""
    ).strip()
    return (
        not requested_update_id
        or not command_update_id
        or requested_update_id == command_update_id
    )


def _single_positive_number(
    payload: Mapping[str, object],
    keys: Sequence[str],
) -> int:
    values = {
        int(_number(payload.get(key)))
        for key in keys
        if int(_number(payload.get(key))) > 0
    }
    if len(values) > 1:
        return -1
    return next(iter(values), 0)


def _manager_reports_tactical_nuke_cast(
    manager: str,
    payload: Mapping[str, object],
) -> bool:
    if manager != "AbilityTask":
        return False
    task_type = str(payload.get("task_type", "") or "").strip().lower()
    ability_values = {
        str(payload.get(key, "") or "").strip().lower()
        for key in (
            "ability",
            "ability_name",
            "ability_policy",
            "executed_ability",
            "requested_ability",
        )
    }
    if "tactical_nuke" not in ability_values or task_type not in {
        "",
        "execute_ability",
    }:
        return False
    location_intent = str(
        payload.get("location_intent", "") or ""
    ).strip().lower()
    if location_intent in {
        "enemy_main",
        "enemy_base",
        "enemy_start",
        "enemy_start_location",
        "enemy_natural",
        "contain_enemy_natural",
        "enemy_front",
    } and payload.get("target_location_match") is not True:
        return False
    submitted_action = str(payload.get("cast_submitted_action", "") or "")
    submitted_count = _number(payload.get("cast_submitted_count"))
    submission_frame = int(_number(payload.get("cast_submission_frame")))
    if (
        submitted_count <= 0
        or submission_frame <= 0
        or not _action_reports_ability(
            submitted_action,
            "EFFECT_NUKECALLDOWN",
        )
    ):
        return False
    confirmation_state = str(
        payload.get("confirmation_state", "") or ""
    ).strip().lower()
    confirmation_effect = str(
        payload.get("confirmation_effect", "") or ""
    ).strip().lower()
    confirmation_count = _number(payload.get("confirmation_count"))
    confirmation_frame = _tactical_nuke_confirmation_frame(payload)
    if (
        confirmation_state != "confirmed"
        or confirmation_effect not in _TACTICAL_NUKE_CONFIRMATION_EFFECTS
        or confirmation_count <= 0
        or confirmation_frame < submission_frame
    ):
        return False
    return True


def _manager_reports_explicit_ability_cast(
    manager: str,
    payload: Mapping[str, object],
    *,
    current_update_ids: frozenset[str],
) -> bool:
    if manager != "AbilityTask":
        return False
    task_type = str(payload.get("task_type", "") or "").strip().lower()
    ability = str(payload.get("ability", "") or "").strip().lower()
    update_id = str(payload.get("update_id", "") or "").strip()
    if (
        not ability
        or ability == "tactical_nuke"
        or task_type not in {"", "execute_ability"}
        or not update_id
        or (current_update_ids and update_id not in current_update_ids)
    ):
        return False
    return explicit_ability_terminal_is_valid(payload)


def _explicit_ability_submission_action(payload: Mapping[str, object]) -> str:
    return str(
        payload.get("last_action")
        or payload.get("last_actual_command")
        or ""
    ).strip()


def _explicit_ability_submission_frame(payload: Mapping[str, object]) -> int:
    return max(
        int(_number(payload.get("submission_frame"))),
        int(_number(payload.get("last_actual_command_frame"))),
    )


def _explicit_ability_confirmation_frame(payload: Mapping[str, object]) -> int:
    return max(
        int(_number(payload.get("confirmation_frame"))),
        int(_number(payload.get("observed_accepted_frame"))),
    )


def _action_reports_ability(action: object, ability: str) -> bool:
    expected = f"ability={ability}".lower()
    return any(
        field.strip().lower() == expected
        for field in str(action or "").split("|")
    )


def _tactical_nuke_confirmation_frame(payload: Mapping[str, object]) -> int:
    return int(_number(payload.get("confirmation_frame")))


def _is_marine_unit_type(value: object) -> bool:
    return _canonical_terran_combat_family(value) == "marine"


def _canonical_terran_combat_family(value: object) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if not normalized:
        return ""
    alias = _TERRAN_COMBAT_FAMILY_ALIASES.get(normalized.lower())
    if alias:
        return alias
    for family, unit_types in _TERRAN_COMBAT_FAMILY_TYPES.items():
        if normalized in unit_types:
            return family
    return ""


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _positive_unique_unit_tags(value: object) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return ()
    tags: list[int] = []
    for raw_tag in value:
        tag = int(_number(raw_tag))
        if tag <= 0 or tag in tags:
            return ()
        tags.append(tag)
    return tuple(tags)


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
        "task_type",
        "status",
        "last_actual_command",
        "last_actual_command_frame",
        "actual_command_issued_count",
        "main_attack_last_issued_action",
        "main_attack_last_action_frame",
        "main_attack_home_distance",
        "main_attack_max_home_distance",
        "main_attack_actual_command_issued_count",
        "scout_last_issued_action",
        "scout_last_action_frame",
        "scout_home_distance",
        "scout_max_home_distance",
        "scout_marine_assigned_count",
        "scout_marine_home_distance",
        "scout_marine_max_home_distance",
        "scout_actual_command_issued_count",
        "scout_last_commanded_unit_tag",
        "scout_last_commanded_unit_type",
        "scout_scope_requested_min_units",
        "scout_scope_requested_max_units",
        "ability",
        "ability_name",
        "ability_policy",
        "location_intent",
        "target_anchor_x",
        "target_anchor_y",
        "target_anchor_distance",
        "target_location_match",
        "role",
        "cast_attempted_count",
        "cast_submitted_count",
        "cast_submission_frame",
        "cast_submitted_action",
        "submitted_count",
        "last_action",
        "submission_frame",
        "confirmation_state",
        "confirmation_count",
        "confirmation_frame",
        "confirmation_effect",
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


def _current_update_ids(
    telemetry_entries: Sequence[Mapping[str, object]],
) -> frozenset[str]:
    for entry in reversed(tuple(telemetry_entries)):
        update_ids: set[str] = set()
        active_ids = entry.get("active_modulation_ids")
        if isinstance(active_ids, Sequence) and not isinstance(
            active_ids,
            (str, bytes, bytearray),
        ):
            update_ids.update(
                str(item) for item in active_ids if isinstance(item, str) and item
            )
        managers = entry.get("managers")
        if isinstance(managers, Mapping):
            game_commander = managers.get("GameCommander")
            if isinstance(game_commander, Mapping):
                update_ids.update(
                    value
                    for value in (
                        str(game_commander.get("update_id", "") or ""),
                        str(game_commander.get("policy_update_id", "") or ""),
                    )
                    if value
                )
            if not update_ids:
                ability_task = managers.get("AbilityTask")
                if isinstance(ability_task, Mapping):
                    ability_update_id = str(
                        ability_task.get("update_id", "") or ""
                    )
                    if ability_update_id:
                        update_ids.add(ability_update_id)
            if not update_ids:
                operation_director = managers.get("OperationDirector")
                if isinstance(operation_director, Mapping):
                    operation_update_id = str(
                        operation_director.get(
                            "policy_update_id",
                            "",
                        )
                        or ""
                    )
                    if operation_update_id:
                        update_ids.add(operation_update_id)
        if update_ids:
            return frozenset(update_ids)
    return frozenset()


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
