"""Canonical pre-live capability contract for Terran combat unit families."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from starcraft_commander.policy_modulation import (
    MICROMACHINE_TACTICAL_ABILITIES,
)


TERRAN_OPERATION_TASKS: Final[tuple[str, ...]] = (
    "scout_with_units",
    "pressure_with_main_army",
    "defend_with_units",
    "harass_with_units",
)
TRANSIENT_PRODUCTION_BLOCKERS: Final[frozenset[str]] = frozenset(
    {
        "missing_producer",
        "missing_addon",
        "missing_tech",
        "producer_busy",
        "supply_blocked",
        "gas_pending",
        "minerals_pending",
    }
)
TRANSIENT_FAMILY_BLOCKERS: Final[frozenset[str]] = frozenset(
    {
        "production_queued",
        "training",
        "waiting_for_units",
        "waiting_for_exact_composition",
        *TRANSIENT_PRODUCTION_BLOCKERS,
    }
)


@dataclass(frozen=True)
class TerranUnitFamilyCapability:
    """One machine-readable Terran unit-family execution contract."""

    family: str
    display_name: str
    unit_types: tuple[str, ...]
    aliases: tuple[str, ...]
    default_role: str
    prerequisites: tuple[str, ...]
    abilities: tuple[str, ...]
    micro_managers: tuple[str, ...]
    operation_tasks: tuple[str, ...] = TERRAN_OPERATION_TASKS

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "display_name": self.display_name,
            "unit_types": list(self.unit_types),
            "aliases": list(self.aliases),
            "default_role": self.default_role,
            "prerequisites": list(self.prerequisites),
            "abilities": list(self.abilities),
            "micro_managers": list(self.micro_managers),
            "operation_tasks": list(self.operation_tasks),
        }


@dataclass(frozen=True)
class TerranNaturalLanguageForm:
    """One deterministic natural-language form for a Terran family."""

    family: str
    requested_unit_type: str
    execution_unit_type: str
    aliases: tuple[str, ...]
    additional_prerequisites: tuple[str, ...] = ()
    staging_unit_types: tuple[str, ...] = ()
    mode_ability: str = ""


@dataclass(frozen=True)
class TerranNaturalLanguageUnitIntent:
    """One counted unit request before it is serialized into the bounded DSL."""

    family: str
    requested_unit_type: str
    execution_unit_type: str
    count: int
    role: str
    prerequisites: tuple[str, ...]
    staging_unit_types: tuple[str, ...] = ()
    mode_ability: str = ""

    def production_targets(self) -> tuple[str, ...]:
        return _ordered_unique(
            (
                self.execution_unit_type,
                *self.prerequisites,
                *self.staging_unit_types,
            )
        )


@dataclass(frozen=True)
class TerranNaturalLanguageOperationIntent:
    """Deterministic manager-level operation selected from explicit verbs."""

    task_type: str
    army_group: str
    location_intent: str


TERRAN_UNIT_FAMILIES: Final[tuple[TerranUnitFamilyCapability, ...]] = (
    TerranUnitFamilyCapability(
        "marine",
        "Marine",
        ("TERRAN_MARINE",),
        ("marine", "marines", "마린", "해병"),
        "frontline",
        ("TERRAN_BARRACKS",),
        ("marine_stimpack",),
        ("Squad", "RangedManager"),
    ),
    TerranUnitFamilyCapability(
        "marauder",
        "Marauder",
        ("TERRAN_MARAUDER",),
        ("marauder", "marauders", "불곰"),
        "frontline",
        ("TERRAN_BARRACKS", "BARRACKS_TECHLAB"),
        ("marauder_stimpack",),
        ("Squad", "RangedManager"),
    ),
    TerranUnitFamilyCapability(
        "reaper",
        "Reaper",
        ("TERRAN_REAPER",),
        ("reaper", "reapers", "사신"),
        "worker_harass",
        ("TERRAN_BARRACKS",),
        ("kd8_charge",),
        ("Squad", "RangedManager"),
    ),
    TerranUnitFamilyCapability(
        "ghost",
        "Ghost",
        ("TERRAN_GHOST",),
        ("ghost", "ghosts", "유령"),
        "spellcaster",
        ("TERRAN_BARRACKS", "BARRACKS_TECHLAB", "TERRAN_GHOSTACADEMY"),
        ("emp", "snipe", "ghost_cloak", "ghost_decloak", "tactical_nuke"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "hellion_hellbat",
        "Hellion/Hellbat",
        ("TERRAN_HELLION", "TERRAN_HELLIONTANK"),
        ("hellion", "hellions", "hellbat", "hellbats", "화염차", "화염기갑병"),
        "worker_harass",
        ("TERRAN_FACTORY",),
        ("hellbat_mode", "hellion_mode"),
        ("Squad", "RangedManager"),
    ),
    TerranUnitFamilyCapability(
        "widow_mine",
        "Widow Mine",
        ("TERRAN_WIDOWMINE", "TERRAN_WIDOWMINEBURROWED"),
        ("widow mine", "widow mines", "widowmine", "widowmines", "지뢰", "땅거미지뢰"),
        "ambush",
        ("TERRAN_FACTORY",),
        ("widow_mine_burrow", "widow_mine_unburrow"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "cyclone",
        "Cyclone",
        ("TERRAN_CYCLONE",),
        ("cyclone", "cyclones", "사이클론"),
        "kite",
        ("TERRAN_FACTORY", "FACTORY_TECHLAB"),
        ("lock_on",),
        ("Squad", "RangedManager"),
    ),
    TerranUnitFamilyCapability(
        "siege_tank",
        "Siege Tank",
        ("TERRAN_SIEGETANK", "TERRAN_SIEGETANKSIEGED"),
        ("siege tank", "siege tanks", "tank", "tanks", "탱크", "공성전차"),
        "siege_support",
        ("TERRAN_FACTORY", "FACTORY_TECHLAB"),
        ("siege_mode", "unsiege"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "thor",
        "Thor",
        ("TERRAN_THOR", "TERRAN_THORAP"),
        ("thor", "thors", "토르"),
        "anti_air",
        ("TERRAN_FACTORY", "FACTORY_TECHLAB", "TERRAN_ARMORY"),
        ("thor_high_impact_mode", "thor_explosive_mode"),
        ("Squad", "RangedManager"),
    ),
    TerranUnitFamilyCapability(
        "medivac",
        "Medivac",
        ("TERRAN_MEDIVAC",),
        ("medivac", "medivacs", "의료선"),
        "support",
        ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        ("medivac_afterburners", "medivac_heal", "medivac_load", "medivac_unload_all"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "raven",
        "Raven",
        ("TERRAN_RAVEN",),
        ("raven", "ravens", "밤까마귀"),
        "support",
        ("TERRAN_FACTORY", "TERRAN_STARPORT", "STARPORT_TECHLAB"),
        ("auto_turret", "interference_matrix", "anti_armor_missile"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "viking",
        "Viking",
        ("TERRAN_VIKINGFIGHTER", "TERRAN_VIKINGASSAULT"),
        ("viking", "vikings", "바이킹"),
        "anti_air",
        ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        ("viking_fighter_mode", "viking_assault_mode"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "banshee",
        "Banshee",
        ("TERRAN_BANSHEE",),
        ("banshee", "banshees", "밴시"),
        "worker_harass",
        ("TERRAN_FACTORY", "TERRAN_STARPORT", "STARPORT_TECHLAB"),
        ("banshee_cloak", "banshee_decloak"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "liberator",
        "Liberator",
        ("TERRAN_LIBERATOR", "TERRAN_LIBERATORAG"),
        ("liberator", "liberators", "해방선"),
        "zone_control",
        ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        ("liberator_defender_mode", "liberator_fighter_mode"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
    TerranUnitFamilyCapability(
        "battlecruiser",
        "Battlecruiser",
        ("TERRAN_BATTLECRUISER",),
        ("battlecruiser", "battlecruisers", "bc", "배틀크루저", "전투순양함"),
        "capital_ship",
        (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
            "TERRAN_FUSIONCORE",
        ),
        ("yamato", "tactical_jump"),
        ("Squad", "RangedManager", "CombatCommander"),
    ),
)

TERRAN_UNIT_FAMILY_BY_TOKEN: Final[dict[str, TerranUnitFamilyCapability]] = {
    token: family
    for family in TERRAN_UNIT_FAMILIES
    for token in family.unit_types
}
TERRAN_UNIT_FAMILY_BY_ALIAS: Final[dict[str, TerranUnitFamilyCapability]] = {
    alias.casefold(): family
    for family in TERRAN_UNIT_FAMILIES
    for alias in (*family.aliases, family.family, family.display_name)
}
TERRAN_UNIT_FAMILY_BY_NAME: Final[dict[str, TerranUnitFamilyCapability]] = {
    family.family: family for family in TERRAN_UNIT_FAMILIES
}

TERRAN_NATURAL_LANGUAGE_FORMS: Final[
    tuple[TerranNaturalLanguageForm, ...]
] = (
    TerranNaturalLanguageForm(
        "marine",
        "TERRAN_MARINE",
        "TERRAN_MARINE",
        ("marine", "marines", "마린", "해병"),
    ),
    TerranNaturalLanguageForm(
        "marauder",
        "TERRAN_MARAUDER",
        "TERRAN_MARAUDER",
        ("marauder", "marauders", "불곰"),
    ),
    TerranNaturalLanguageForm(
        "reaper",
        "TERRAN_REAPER",
        "TERRAN_REAPER",
        ("reaper", "reapers", "사신"),
    ),
    TerranNaturalLanguageForm(
        "ghost",
        "TERRAN_GHOST",
        "TERRAN_GHOST",
        ("ghost", "ghosts", "유령"),
    ),
    TerranNaturalLanguageForm(
        "hellion_hellbat",
        "TERRAN_HELLION",
        "TERRAN_HELLION",
        ("hellion", "hellions", "화염차"),
    ),
    TerranNaturalLanguageForm(
        "hellion_hellbat",
        "TERRAN_HELLIONTANK",
        "TERRAN_HELLION",
        ("hellbat", "hellbats", "화염기갑병"),
        additional_prerequisites=("TERRAN_ARMORY",),
        staging_unit_types=("TERRAN_HELLION",),
        mode_ability="hellbat_mode",
    ),
    TerranNaturalLanguageForm(
        "widow_mine",
        "TERRAN_WIDOWMINE",
        "TERRAN_WIDOWMINE",
        (
            "widow mine",
            "widow mines",
            "widowmine",
            "widowmines",
            "지뢰",
            "땅거미지뢰",
        ),
    ),
    TerranNaturalLanguageForm(
        "cyclone",
        "TERRAN_CYCLONE",
        "TERRAN_CYCLONE",
        ("cyclone", "cyclones", "사이클론"),
    ),
    TerranNaturalLanguageForm(
        "siege_tank",
        "TERRAN_SIEGETANK",
        "TERRAN_SIEGETANK",
        ("siege tank", "siege tanks", "tank", "tanks", "탱크", "공성전차"),
    ),
    TerranNaturalLanguageForm(
        "thor",
        "TERRAN_THOR",
        "TERRAN_THOR",
        ("thor", "thors", "토르"),
    ),
    TerranNaturalLanguageForm(
        "medivac",
        "TERRAN_MEDIVAC",
        "TERRAN_MEDIVAC",
        ("medivac", "medivacs", "의료선"),
    ),
    TerranNaturalLanguageForm(
        "raven",
        "TERRAN_RAVEN",
        "TERRAN_RAVEN",
        ("raven", "ravens", "밤까마귀"),
    ),
    TerranNaturalLanguageForm(
        "viking",
        "TERRAN_VIKINGFIGHTER",
        "TERRAN_VIKINGFIGHTER",
        ("viking", "vikings", "바이킹"),
    ),
    TerranNaturalLanguageForm(
        "banshee",
        "TERRAN_BANSHEE",
        "TERRAN_BANSHEE",
        ("banshee", "banshees", "밴시"),
    ),
    TerranNaturalLanguageForm(
        "liberator",
        "TERRAN_LIBERATOR",
        "TERRAN_LIBERATOR",
        ("liberator", "liberators", "해방선"),
    ),
    TerranNaturalLanguageForm(
        "battlecruiser",
        "TERRAN_BATTLECRUISER",
        "TERRAN_BATTLECRUISER",
        (
            "battlecruiser",
            "battlecruisers",
            "bc",
            "배틀크루저",
            "전투순양함",
        ),
    ),
)

_KOREAN_SMALL_NUMBERS: Final[dict[str, int]] = {
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


def canonical_terran_unit_family(unit_type: object) -> str:
    """Return the canonical family key for a DSL or telemetry unit token."""

    raw_token = str(unit_type or "").strip()
    alias_family = TERRAN_UNIT_FAMILY_BY_ALIAS.get(raw_token.casefold())
    if alias_family is not None:
        return alias_family.family
    token = raw_token.upper()
    if token and not token.startswith("TERRAN_") and "_" not in token:
        token = f"TERRAN_{token}"
    family = TERRAN_UNIT_FAMILY_BY_TOKEN.get(token)
    return family.family if family is not None else ""


def all_terran_capability_matrix() -> tuple[dict[str, object], ...]:
    """Return the stable machine-readable 15-family capability matrix."""

    return tuple(family.to_dict() for family in TERRAN_UNIT_FAMILIES)


def lower_terran_natural_language_units(
    text: str,
    *,
    default_count: int | None = None,
) -> tuple[TerranNaturalLanguageUnitIntent, ...]:
    """Lower explicit Terran unit mentions without conflating unit forms."""

    normalized = " ".join(str(text or "").casefold().split())
    intents: list[TerranNaturalLanguageUnitIntent] = []
    for form in TERRAN_NATURAL_LANGUAGE_FORMS:
        count = _mentioned_unit_count(
            normalized,
            aliases=form.aliases,
            default_count=default_count,
        )
        if count is None:
            continue
        family = TERRAN_UNIT_FAMILY_BY_NAME[form.family]
        intents.append(
            TerranNaturalLanguageUnitIntent(
                family=form.family,
                requested_unit_type=form.requested_unit_type,
                execution_unit_type=form.execution_unit_type,
                count=max(1, min(200, count)),
                role=family.default_role,
                prerequisites=_ordered_unique(
                    (
                        *family.prerequisites,
                        *form.additional_prerequisites,
                    )
                ),
                staging_unit_types=form.staging_unit_types,
                mode_ability=form.mode_ability,
            )
        )
    return tuple(intents)


def lower_terran_natural_language_operation(
    text: str,
) -> TerranNaturalLanguageOperationIntent | None:
    """Choose a deterministic bounded operation from explicit command verbs."""

    normalized = " ".join(str(text or "").casefold().split())
    compact = "".join(normalized.split())
    explicit_defense = _contains_any(
        normalized,
        compact,
        (
            "수비",
            "방어",
            "버텨",
            "지켜",
            "막아",
            "hold",
            "defend",
            "defense",
        ),
    )
    harass = _contains_any(
        normalized,
        compact,
        ("견제", "일꾼털", "harass", "harassment", "worker raid"),
    )
    attack = _contains_any(
        normalized,
        compact,
        ("공격", "러시", "러쉬", "압박", "attack", "rush", "pressure"),
    )
    scout = _contains_any(
        normalized,
        compact,
        ("정찰", "탐색", "수색", "scout", "recon"),
    )
    if explicit_defense:
        location = (
            "ramp"
            if _contains_any(normalized, compact, ("입구", "ramp"))
            else (
                "natural"
                if _contains_any(
                    normalized,
                    compact,
                    ("앞마당", "natural", "expansion"),
                )
                else "home"
            )
        )
        return TerranNaturalLanguageOperationIntent(
            "defend_with_units",
            "defense",
            location,
        )
    if harass:
        return TerranNaturalLanguageOperationIntent(
            "harass_with_units",
            "harass",
            "enemy_mineral_line",
        )
    if attack:
        location = (
            "enemy_main"
            if _contains_any(
                normalized,
                compact,
                (
                    "적진",
                    "적 본진",
                    "적기지",
                    "enemy base",
                    "enemy main",
                ),
            )
            else "enemy_natural"
        )
        return TerranNaturalLanguageOperationIntent(
            "pressure_with_main_army",
            "main",
            location,
        )
    if scout:
        location = (
            "watchtower"
            if _contains_any(
                normalized,
                compact,
                ("감시탑", "watchtower", "watch tower"),
            )
            else "enemy_main"
        )
        return TerranNaturalLanguageOperationIntent(
            "scout_with_units",
            "scout",
            location,
        )
    return None


def terran_production_targets(
    intents: Sequence[TerranNaturalLanguageUnitIntent],
) -> tuple[str, ...]:
    """Return executable units plus every required production prerequisite."""

    return _ordered_unique(
        target
        for intent in intents
        for target in intent.production_targets()
    )


def validate_terran_capability_contract() -> tuple[str, ...]:
    """Return contract drift errors instead of overstating runtime support."""

    errors: list[str] = []
    seen_families: set[str] = set()
    seen_unit_types: set[str] = set()
    supported_abilities = MICROMACHINE_TACTICAL_ABILITIES - {""}
    for family in TERRAN_UNIT_FAMILIES:
        if family.family in seen_families:
            errors.append(f"duplicate family: {family.family}")
        seen_families.add(family.family)
        for unit_type in family.unit_types:
            if unit_type in seen_unit_types:
                errors.append(f"duplicate unit type: {unit_type}")
            seen_unit_types.add(unit_type)
        unsupported_abilities = sorted(set(family.abilities) - supported_abilities)
        if unsupported_abilities:
            errors.append(
                f"{family.family} has unsupported abilities: "
                + ",".join(unsupported_abilities)
            )
        if set(family.operation_tasks) != set(TERRAN_OPERATION_TASKS):
            errors.append(
                f"{family.family} does not expose the complete operation task set"
            )
        if "Squad" not in family.micro_managers:
            errors.append(f"{family.family} is not connected to Squad")
    form_families = {form.family for form in TERRAN_NATURAL_LANGUAGE_FORMS}
    if form_families != seen_families:
        errors.append("natural-language forms do not cover every Terran family")
    hellbat_forms = tuple(
        form
        for form in TERRAN_NATURAL_LANGUAGE_FORMS
        if form.requested_unit_type == "TERRAN_HELLIONTANK"
    )
    if len(hellbat_forms) != 1:
        errors.append("Hellbat must have exactly one natural-language form")
    else:
        hellbat = hellbat_forms[0]
        if (
            hellbat.execution_unit_type != "TERRAN_HELLION"
            or "TERRAN_ARMORY" not in hellbat.additional_prerequisites
            or "TERRAN_HELLION" not in hellbat.staging_unit_types
            or hellbat.mode_ability != "hellbat_mode"
        ):
            errors.append("Hellbat staging/mode contract is incomplete")
    return tuple(errors)


def _mentioned_unit_count(
    text: str,
    *,
    aliases: Sequence[str],
    default_count: int | None,
) -> int | None:
    unit_pattern = _unit_alias_pattern(aliases)
    digit_before = re.search(rf"(?<!\d)(\d{{1,3}})\s*{unit_pattern}", text)
    digit_after = re.search(
        rf"{unit_pattern}\s*(\d{{1,3}})\s*(?:기|마리|대|명|units?)?",
        text,
    )
    if digit_before:
        return int(digit_before.group(1))
    if digit_after:
        return int(digit_after.group(1))
    word_match = re.search(
        r"("
        + "|".join(
            sorted(
                map(re.escape, _KOREAN_SMALL_NUMBERS),
                key=len,
                reverse=True,
            )
        )
        + rf")\s*{unit_pattern}",
        text,
    )
    if word_match:
        return _KOREAN_SMALL_NUMBERS[word_match.group(1)]
    if default_count is not None and re.search(unit_pattern, text):
        return default_count
    return None


def _unit_alias_pattern(aliases: Sequence[str]) -> str:
    patterns: list[str] = []
    for alias in sorted(aliases, key=len, reverse=True):
        escaped = re.escape(alias.casefold()).replace(r"\ ", r"\s*")
        if alias.isascii():
            escaped = rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])"
        patterns.append(escaped)
    return "(?:" + "|".join(patterns) + ")"


def _contains_any(
    normalized: str,
    compact: str,
    tokens: Sequence[str],
) -> bool:
    return any(token in normalized or token.replace(" ", "") in compact for token in tokens)


def _ordered_unique(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if token and token not in result:
            result.append(token)
    return tuple(result)


def operation_family_evidence(
    operation_telemetry: Mapping[str, object],
    *,
    expected_update_id: str = "",
    expected_operation_id: str = "",
    expected_generation: int = 0,
    issued_at_frame: int = 0,
    deadline_frame: int = 0,
    snapshot_frame: int = 0,
) -> tuple[dict[str, object], ...]:
    """Normalize current runtime family evidence without inventing effects."""

    explicit = operation_telemetry.get("family_evidence")
    has_explicit_contract = isinstance(explicit, Sequence) and not isinstance(
        explicit,
        (str, bytes, bytearray),
    )
    normalized_items = (
        tuple(
            normalized
            for item in explicit
            if isinstance(item, Mapping)
            if (
                normalized := _normalized_family_evidence_item(
                    item,
                    operation_telemetry=operation_telemetry,
                    expected_update_id=expected_update_id,
                    expected_operation_id=expected_operation_id,
                    expected_generation=expected_generation,
                )
            )
            is not None
        )
        if has_explicit_contract
        else ()
    )
    effective_snapshot_frame = (
        _positive_int(snapshot_frame)
        or _positive_int(operation_telemetry.get("_snapshot_frame"))
        or _positive_int(operation_telemetry.get("telemetry_frame"))
    )
    pending_items = tuple(
        normalized
        for item in _mapping_items(
            operation_telemetry.get("pending_family_effects")
        )
        if (
            normalized := _normalized_pending_family_effect_item(
                item,
                expected_update_id=expected_update_id,
                expected_operation_id=expected_operation_id,
                expected_generation=expected_generation,
                issued_at_frame=max(0, issued_at_frame),
                deadline_frame=max(0, deadline_frame),
                snapshot_frame=effective_snapshot_frame,
            )
        )
        is not None
    )
    if has_explicit_contract or pending_items:
        merged = _coalesce_family_evidence(
            (*normalized_items, *pending_items)
        )
        if merged or has_explicit_contract:
            return merged

    progress = operation_telemetry.get("requirement_progress")
    if not isinstance(progress, Sequence) or isinstance(
        progress,
        (str, bytes, bytearray),
    ):
        return ()
    operation_blocker = str(
        operation_telemetry.get("blocked_reason", "") or ""
    ).strip()
    update_id = _evidence_update_id(
        operation_telemetry,
        expected_update_id=expected_update_id,
    )
    operation_id = str(
        operation_telemetry.get("operation_id")
        or expected_operation_id
        or ""
    ).strip()
    generation = _positive_int(
        operation_telemetry.get("generation")
    ) or max(0, expected_generation)
    squad_order = str(
        operation_telemetry.get("squad_order", "") or ""
    ).strip().lower()
    result: list[dict[str, object]] = []
    for item in progress:
        if not isinstance(item, Mapping):
            continue
        family = canonical_terran_unit_family(item.get("unit_type"))
        if not family:
            continue
        assigned = max(0, _int_value(item.get("assigned_count")))
        represented = max(0, _int_value(item.get("represented_count")))
        blocker = str(item.get("production_blocker", "") or "").strip()
        result.append(
            _family_evidence_payload(
                family=family,
                unit_type=str(item.get("unit_type", "") or ""),
                role=str(item.get("role", "") or ""),
                assigned=assigned,
                represented=represented,
                update_id=update_id,
                operation_id=operation_id,
                generation=generation,
                action=f"squad_order:{squad_order}" if squad_order else "",
                required_effect="movement_or_engagement",
                attempt_generation=generation,
                attempted_count=0,
                attempted_frame=0,
                submitted_count=0,
                submitted_frame=0,
                effect_kind="",
                effect_count=0,
                effect_frame=0,
                blocker_manager=(
                    "ProductionManager"
                    if blocker and blocker != "ready"
                    else ("OperationDirector" if operation_blocker else "")
                ),
                blocker=blocker or operation_blocker,
            )
        )
    return tuple(result)


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _normalized_pending_family_effect_item(
    item: Mapping[str, object],
    *,
    expected_update_id: str,
    expected_operation_id: str,
    expected_generation: int,
    issued_at_frame: int,
    deadline_frame: int,
    snapshot_frame: int,
) -> dict[str, object] | None:
    """Accept only an exact, fully delivered effect from the pending queue."""

    update_id = str(item.get("update_id", "") or "").strip()
    operation_id = str(item.get("operation_id", "") or "").strip()
    generation = _positive_int(item.get("generation"))
    attempt_generation = _positive_int(item.get("attempt_generation"))
    if (
        not expected_update_id
        or update_id != expected_update_id
        or not expected_operation_id
        or operation_id != expected_operation_id
        or expected_generation <= 0
        or generation != expected_generation
        or attempt_generation <= 0
        or snapshot_frame <= 0
    ):
        return None
    normalized = _normalized_family_evidence_item(
        item,
        operation_telemetry={},
        expected_update_id=expected_update_id,
        expected_operation_id=expected_operation_id,
        expected_generation=expected_generation,
    )
    if normalized is None:
        return None
    attempted_count = _positive_int(normalized.get("attempted_count"))
    attempted_frame = _positive_int(normalized.get("attempted_frame"))
    submitted_count = _positive_int(normalized.get("submitted_count"))
    submitted_frame = _positive_int(normalized.get("submitted_frame"))
    effect_count = _positive_int(normalized.get("effect_count"))
    effect_frame = _positive_int(normalized.get("effect_frame"))
    family = canonical_terran_unit_family(normalized.get("family"))
    unit_family = canonical_terran_unit_family(
        normalized.get("unit_type")
    )
    required_effect = str(
        normalized.get("required_effect", "") or ""
    ).strip()
    effect_kind = str(normalized.get("effect_kind", "") or "").strip()
    action = str(normalized.get("action", "") or "").strip()
    is_ability_action = action.lower().startswith("ability:")
    movement_contract = (
        required_effect == "movement_or_engagement"
        and effect_kind in {"movement", "engagement"}
        and not is_ability_action
    )
    ability_contract = (
        required_effect == "ability_state_or_effect"
        and effect_kind == "ability_state"
        and is_ability_action
    )
    if (
        attempted_count <= 0
        or attempted_frame <= 0
        or submitted_count <= 0
        or submitted_frame <= 0
        or effect_count <= 0
        or effect_frame <= 0
        or not family
        or family != unit_family
        or not (movement_contract or ability_contract)
        or not (
            attempted_frame <= submitted_frame <= effect_frame
        )
        or effect_frame > snapshot_frame
        or (issued_at_frame > 0 and attempted_frame < issued_at_frame)
        or (deadline_frame > 0 and effect_frame > deadline_frame)
    ):
        return None
    return normalized


def _coalesce_family_evidence(
    items: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    latest_by_action: dict[
        tuple[str, str, str, str, str],
        dict[str, object],
    ] = {}
    action_order: list[tuple[str, str, str, str, str]] = []
    for item in items:
        key = (
            str(item.get("family", "") or ""),
            str(item.get("unit_type", "") or ""),
            str(item.get("role", "") or ""),
            str(item.get("action", "") or ""),
            str(item.get("required_effect", "") or ""),
        )
        current = latest_by_action.get(key)
        if current is None:
            action_order.append(key)
            latest_by_action[key] = dict(item)
            continue
        current_attempt = _positive_int(current.get("attempt_generation"))
        incoming_attempt = _positive_int(item.get("attempt_generation"))
        if incoming_attempt < current_attempt:
            continue
        if incoming_attempt > current_attempt:
            latest_by_action[key] = dict(item)
            continue
        latest_by_action[key] = _merge_family_evidence_attempt(
            current,
            item,
        )
    return tuple(latest_by_action[key] for key in action_order)


def _merge_family_evidence_attempt(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> dict[str, object]:
    preferred = (
        current
        if _family_evidence_rank(current) >= _family_evidence_rank(previous)
        else previous
    )
    secondary = previous if preferred is current else current
    effect_kind = str(preferred.get("effect_kind", "") or "").strip()
    if not effect_kind:
        effect_kind = str(secondary.get("effect_kind", "") or "").strip()
    attempted_unit_tags = _merge_optional_unit_tags(
        previous,
        current,
        "attempted_unit_tags",
    )
    submitted_unit_tags = _merge_optional_unit_tags(
        previous,
        current,
        "submitted_unit_tags",
    )
    effect_unit_tags = _merge_optional_unit_tags(
        previous,
        current,
        "effect_unit_tags",
    )
    return _family_evidence_payload(
        family=str(
            preferred.get("family") or secondary.get("family") or ""
        ),
        unit_type=str(
            preferred.get("unit_type")
            or secondary.get("unit_type")
            or ""
        ),
        role=str(preferred.get("role") or secondary.get("role") or ""),
        assigned=max(
            _positive_int(previous.get("assigned")),
            _positive_int(current.get("assigned")),
        ),
        represented=max(
            _positive_int(previous.get("represented")),
            _positive_int(current.get("represented")),
        ),
        update_id=str(
            preferred.get("update_id")
            or secondary.get("update_id")
            or ""
        ),
        operation_id=str(
            preferred.get("operation_id")
            or secondary.get("operation_id")
            or ""
        ),
        generation=_positive_int(
            preferred.get("generation") or secondary.get("generation")
        ),
        action=str(
            preferred.get("action") or secondary.get("action") or ""
        ),
        required_effect=str(
            preferred.get("required_effect")
            or secondary.get("required_effect")
            or ""
        ),
        attempt_generation=_positive_int(
            preferred.get("attempt_generation")
            or secondary.get("attempt_generation")
        ),
        attempted_count=(
            len(attempted_unit_tags)
            if attempted_unit_tags is not None
            else max(
                _positive_int(previous.get("attempted_count")),
                _positive_int(current.get("attempted_count")),
            )
        ),
        attempted_frame=max(
            _positive_int(previous.get("attempted_frame")),
            _positive_int(current.get("attempted_frame")),
        ),
        submitted_count=(
            len(submitted_unit_tags)
            if submitted_unit_tags is not None
            else max(
                _positive_int(previous.get("submitted_count")),
                _positive_int(current.get("submitted_count")),
            )
        ),
        submitted_frame=max(
            _positive_int(previous.get("submitted_frame")),
            _positive_int(current.get("submitted_frame")),
        ),
        effect_kind=effect_kind,
        effect_count=(
            len(effect_unit_tags)
            if effect_unit_tags is not None
            else max(
                _positive_int(previous.get("effect_count")),
                _positive_int(current.get("effect_count")),
            )
        ),
        effect_frame=max(
            _positive_int(previous.get("effect_frame")),
            _positive_int(current.get("effect_frame")),
        ),
        blocker_manager=str(preferred.get("blocker_manager", "") or ""),
        blocker=str(preferred.get("blocker", "") or ""),
        attempted_unit_tags=attempted_unit_tags,
        submitted_unit_tags=submitted_unit_tags,
        effect_unit_tags=effect_unit_tags,
    )


def _merge_optional_unit_tags(
    previous: Mapping[str, object],
    current: Mapping[str, object],
    key: str,
) -> tuple[int, ...] | None:
    if key not in previous or key not in current:
        return None
    previous_tags = _normalized_unit_tags(previous.get(key))
    current_tags = _normalized_unit_tags(current.get(key))
    if previous_tags is None or current_tags is None:
        return None
    return tuple(sorted({*previous_tags, *current_tags}))


def _normalized_family_evidence_item(
    item: Mapping[str, object],
    *,
    operation_telemetry: Mapping[str, object],
    expected_update_id: str,
    expected_operation_id: str,
    expected_generation: int,
) -> dict[str, object] | None:
    family = canonical_terran_unit_family(
        item.get("family") or item.get("unit_type")
    )
    if family not in TERRAN_UNIT_FAMILY_BY_NAME:
        return None
    update_id = str(
        item.get("update_id")
        or item.get("policy_update_id")
        or _evidence_update_id(
            operation_telemetry,
            expected_update_id=expected_update_id,
        )
        or ""
    ).strip()
    operation_id = str(
        item.get("operation_id")
        or operation_telemetry.get("operation_id")
        or expected_operation_id
        or ""
    ).strip()
    generation = _positive_int(
        item.get("generation")
    ) or _positive_int(
        operation_telemetry.get("generation")
    ) or max(0, expected_generation)
    if expected_update_id and update_id != expected_update_id:
        return None
    if expected_operation_id and operation_id != expected_operation_id:
        return None
    if expected_generation > 0 and generation != expected_generation:
        return None
    action = str(item.get("action", "") or "").strip()
    attempt_generation = _positive_int(
        item.get("attempt_generation")
    ) or generation
    required_effect = str(
        item.get("required_effect", "") or ""
    ).strip()
    effect_kind = str(item.get("effect_kind", "") or "").strip()
    attempted_count = max(
        _int_value(item.get("attempted_count")),
        int(item.get("attempted") is True),
    )
    attempted_frame = max(0, _int_value(item.get("attempted_frame")))
    submitted_count = max(
        _int_value(item.get("submitted_count")),
        int(item.get("executed") is True),
    )
    submitted_frame = max(0, _int_value(item.get("submitted_frame")))
    effect_count = max(
        _int_value(item.get("effect_count")),
        int(item.get("effect") is True),
    )
    effect_frame = max(0, _int_value(item.get("effect_frame")))
    unit_tag_keys = (
        "attempted_unit_tags",
        "submitted_unit_tags",
        "effect_unit_tags",
    )
    unit_tag_fields_present = tuple(key in item for key in unit_tag_keys)
    if any(unit_tag_fields_present) and not all(unit_tag_fields_present):
        return None
    unit_tags: dict[str, tuple[int, ...] | None] = {}
    for key, expected_count in (
        ("attempted_unit_tags", attempted_count),
        ("submitted_unit_tags", submitted_count),
        ("effect_unit_tags", effect_count),
    ):
        if key not in item:
            unit_tags[key] = None
            continue
        normalized_tags = _normalized_unit_tags(item.get(key))
        if normalized_tags is None or len(normalized_tags) != expected_count:
            return None
        unit_tags[key] = normalized_tags
    if (
        not action
        or attempt_generation <= 0
        or not _family_effect_contract_is_valid(
            action=action,
            required_effect=required_effect,
            effect_kind=effect_kind,
        )
        or not _family_effect_lifecycle_is_valid(
            attempted_count=attempted_count,
            attempted_frame=attempted_frame,
            submitted_count=submitted_count,
            submitted_frame=submitted_frame,
            effect_kind=effect_kind,
            effect_count=effect_count,
            effect_frame=effect_frame,
            attempted_unit_tags=unit_tags["attempted_unit_tags"],
            submitted_unit_tags=unit_tags["submitted_unit_tags"],
            effect_unit_tags=unit_tags["effect_unit_tags"],
        )
    ):
        return None
    return _family_evidence_payload(
        family=family,
        unit_type=str(item.get("unit_type", "") or ""),
        role=str(item.get("role", "") or ""),
        assigned=max(0, _int_value(item.get("assigned"))),
        represented=max(0, _int_value(item.get("represented"))),
        update_id=update_id,
        operation_id=operation_id,
        generation=generation,
        action=action,
        required_effect=required_effect,
        attempt_generation=attempt_generation,
        attempted_count=attempted_count,
        attempted_frame=attempted_frame,
        submitted_count=submitted_count,
        submitted_frame=submitted_frame,
        effect_kind=effect_kind,
        effect_count=effect_count,
        effect_frame=effect_frame,
        blocker_manager=str(
            item.get("blocker_manager", "") or ""
        ).strip(),
        blocker=str(item.get("blocker", "") or "").strip(),
        attempted_unit_tags=unit_tags["attempted_unit_tags"],
        submitted_unit_tags=unit_tags["submitted_unit_tags"],
        effect_unit_tags=unit_tags["effect_unit_tags"],
    )


def _family_effect_contract_is_valid(
    *,
    action: str,
    required_effect: str,
    effect_kind: str,
) -> bool:
    is_ability_action = action.lower().startswith("ability:")
    if required_effect == "movement_or_engagement":
        return (
            not is_ability_action
            and effect_kind in {"", "movement", "engagement"}
        )
    if required_effect == "ability_state_or_effect":
        return is_ability_action and effect_kind in {"", "ability_state"}
    return False


def _family_effect_lifecycle_is_valid(
    *,
    attempted_count: int,
    attempted_frame: int,
    submitted_count: int,
    submitted_frame: int,
    effect_kind: str,
    effect_count: int,
    effect_frame: int,
    attempted_unit_tags: tuple[int, ...] | None,
    submitted_unit_tags: tuple[int, ...] | None,
    effect_unit_tags: tuple[int, ...] | None,
) -> bool:
    attempted = attempted_count > 0 or attempted_frame > 0
    submitted = submitted_count > 0 or submitted_frame > 0
    effect = effect_count > 0 or effect_frame > 0 or bool(effect_kind)
    if attempted and not (attempted_count > 0 and attempted_frame > 0):
        return False
    if submitted and not (
        attempted
        and submitted_count > 0
        and submitted_frame >= attempted_frame
    ):
        return False
    if effect and not (
        submitted
        and effect_count > 0
        and effect_frame >= submitted_frame
        and bool(effect_kind)
    ):
        return False
    if attempted_unit_tags is not None and (
        len(attempted_unit_tags) != attempted_count
    ):
        return False
    if submitted_unit_tags is not None:
        if len(submitted_unit_tags) != submitted_count:
            return False
        if (
            attempted_unit_tags is not None
            and not set(submitted_unit_tags).issubset(attempted_unit_tags)
        ):
            return False
    if effect_unit_tags is not None:
        if len(effect_unit_tags) != effect_count:
            return False
        if (
            submitted_unit_tags is not None
            and not set(effect_unit_tags).issubset(submitted_unit_tags)
        ):
            return False
    return True


def _family_evidence_payload(
    *,
    family: str,
    unit_type: str,
    role: str,
    assigned: int,
    represented: int,
    update_id: str,
    operation_id: str,
    generation: int,
    action: str,
    required_effect: str,
    attempt_generation: int,
    attempted_count: int,
    attempted_frame: int,
    submitted_count: int,
    submitted_frame: int,
    effect_kind: str,
    effect_count: int,
    effect_frame: int,
    blocker_manager: str,
    blocker: str,
    attempted_unit_tags: tuple[int, ...] | None = None,
    submitted_unit_tags: tuple[int, ...] | None = None,
    effect_unit_tags: tuple[int, ...] | None = None,
) -> dict[str, object]:
    attempted = attempted_count > 0 and attempted_frame > 0
    executed = submitted_count > 0 and submitted_frame > 0
    effect = effect_count > 0 and effect_frame > 0 and bool(effect_kind)
    if blocker and blocker.lower() not in TRANSIENT_FAMILY_BLOCKERS:
        stage = "blocked"
    elif effect:
        stage = "effect"
    elif executed:
        stage = "executed"
    elif attempted:
        stage = "attempted"
    elif assigned > 0:
        stage = "assigned"
    elif represented > 0:
        stage = "represented"
    else:
        stage = "waiting"
    payload: dict[str, object] = {
        "family": family,
        "display_name": TERRAN_UNIT_FAMILY_BY_NAME[family].display_name,
        "unit_type": unit_type,
        "role": role,
        "assigned": assigned,
        "represented": represented,
        "update_id": update_id,
        "operation_id": operation_id,
        "generation": generation,
        "action": action,
        "required_effect": required_effect,
        "attempt_generation": attempt_generation,
        "attempted_count": attempted_count,
        "attempted_frame": attempted_frame,
        "submitted_count": submitted_count,
        "submitted_frame": submitted_frame,
        "effect_kind": effect_kind,
        "effect_count": effect_count,
        "effect_frame": effect_frame,
        "blocker_manager": blocker_manager,
        "attempted": attempted,
        "executed": executed,
        "effect": effect,
        "blocker": blocker,
        "stage": stage,
    }
    if attempted_unit_tags is not None:
        payload["attempted_unit_tags"] = list(attempted_unit_tags)
    if submitted_unit_tags is not None:
        payload["submitted_unit_tags"] = list(submitted_unit_tags)
    if effect_unit_tags is not None:
        payload["effect_unit_tags"] = list(effect_unit_tags)
    return payload


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value or "").strip())
    except ValueError:
        return 0


def _positive_int(value: object) -> int:
    return max(0, _int_value(value))


def _normalized_unit_tags(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return None
    tags: list[int] = []
    for item in value:
        if isinstance(item, bool):
            return None
        tag = _int_value(item)
        if tag <= 0 or tag in tags:
            return None
        tags.append(tag)
    return tuple(tags)


def _evidence_update_id(
    operation_telemetry: Mapping[str, object],
    *,
    expected_update_id: str,
) -> str:
    return str(
        operation_telemetry.get("update_id")
        or operation_telemetry.get("policy_update_id")
        or operation_telemetry.get("active_update_id")
        or expected_update_id
        or ""
    ).strip()


def _family_evidence_rank(
    item: Mapping[str, object],
) -> tuple[int, int, int, int]:
    return (
        _positive_int(item.get("attempt_generation")),
        _positive_int(item.get("effect_frame")),
        _positive_int(item.get("submitted_frame")),
        _positive_int(item.get("attempted_frame")),
    )
