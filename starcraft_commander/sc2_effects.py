"""Effect / behavior / other execution data for the Direct path, derived from the command catalog.

The catalog (``sc2_catalog``) owns every fact: which ability id a token sends, which unit type casts it and how its
target is chosen.  This module turns those facts into the three tables the adapter needs, so no ability id is typed
by hand here:

* ``effect_abilities``: ``token -> (AbilityId member name, target mode, caster unit types)`` for the tokens the
  adapter's hand-written table does not already carry.
* ``type_specific``: ``token -> {caster type -> AbilityId name}`` where one token must send a different
  (building specific) id per caster type.
* ``structure_caster_tokens``: tokens whose casters are structures (python-sc2 keeps those in
  ``bot.structures``).

Nothing here talks to the game.  Offline evidence only.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from starcraft_commander import sc2_catalog as _catalog

_CATEGORIES: Final[tuple[str, ...]] = ("effect", "behavior", "other")
_STRUCTURE_TYPES: Final[frozenset[str]] = _catalog._STRUCTURE_ISSUER_TYPES
_TARGET_MODES: Final[frozenset[str]] = frozenset(
    {"none", "enemy_unit", "enemy_point", "caster_point", "explicit_point", "own_cargo", "own_damaged_bio",
     "mineral_patch", "supply_depot"}
)


def _catalog_rows() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for category in _CATEGORIES:
        for entry in _catalog.commands_by_category(category):
            if entry.lowering != "execute_ability":
                continue
            for issuer in entry.valid_issuers:
                token = entry.token_for(issuer)
                row = rows.setdefault(token, {"mode": entry.target_mode, "casters": [], "abilities": {}})
                if row["mode"] != entry.target_mode:
                    raise ValueError(f"catalog token {token!r} has two target modes")
                row["casters"].append(issuer)  # type: ignore[union-attr]
                row["abilities"][issuer] = entry.abilities[issuer]  # type: ignore[index]
    return rows


_ROWS: Final[dict[str, dict[str, object]]] = _catalog_rows()


def effect_abilities(existing: frozenset[str] | set[str]) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """Adapter table rows for every catalog token (effect/behavior/other) not already in ``existing``."""

    table: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for token, row in _ROWS.items():
        if token in existing:
            continue
        mode = str(row["mode"])
        if mode not in _TARGET_MODES:
            raise ValueError(f"catalog token {token!r} has unsupported target mode {mode!r}")
        casters = tuple(row["casters"])  # type: ignore[arg-type]
        abilities = row["abilities"]  # type: ignore[assignment]
        table[token] = (abilities[casters[0]], mode, casters)  # type: ignore[index]
    return table


def type_specific(tokens: Iterable[str]) -> dict[str, dict[str, str]]:
    """``token -> {caster type -> ability}`` for the given tokens whose casters send different ids (else omitted)."""

    out: dict[str, dict[str, str]] = {}
    for token in tokens:
        row = _ROWS.get(token)
        if row is None:
            continue
        abilities: dict[str, str] = row["abilities"]  # type: ignore[assignment]
        if len(set(abilities.values())) > 1:
            out[token] = dict(abilities)
    return out


def structure_caster_tokens(tokens: Iterable[str]) -> frozenset[str]:
    """The given catalog tokens whose every caster is a structure type."""

    return frozenset(
        token for token in tokens
        if token in _ROWS and all(c in _STRUCTURE_TYPES for c in _ROWS[token]["casters"])  # type: ignore[union-attr]
    )


# --------------------------------------------------------------------------- caster / target readiness
# Observable rules only (Unit.cargo_*, visible enemies, own SCVs).  A field the observation does not carry
# (cargo_max == 0, e.g. offline fakes) is never a reason to refuse.  Refusal codes are the keys the narrator's
# SC2_CASTER_BLOCKER_KOREAN_REASONS / SC2_TARGET_REQUIRED_KOREAN_REASONS (or sc2_catalog.REFUSAL_KO) translate.

_LOAD_TOKENS: Final[frozenset[str]] = frozenset({"bunker_load", "command_center_load_all"})
_UNLOAD_TOKENS: Final[frozenset[str]] = frozenset({"bunker_unload_all", "command_center_unload_all"})
_PASSENGER_TOKENS: Final[frozenset[str]] = frozenset({"bunker_stimpack"})


def _number(entity: object, attribute: str) -> float | None:
    value = getattr(entity, attribute, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def cargo_blockers(token: str, unit: object) -> tuple[str, ...]:
    """Reasons a Bunker / Command Center cannot run a cargo ability now (empty = ready or not observable)."""

    if token not in _LOAD_TOKENS | _UNLOAD_TOKENS | _PASSENGER_TOKENS:
        return ()
    capacity = _number(unit, "cargo_max")
    if not capacity or capacity <= 0:
        return ()
    used = _number(unit, "cargo_used") or 0.0
    if token in _LOAD_TOKENS and capacity - used <= 0:
        return ("no_cargo_space",)
    if token in _UNLOAD_TOKENS and used <= 0:
        return ("no_cargo_to_unload",)
    if token in _PASSENGER_TOKENS and used <= 0:
        return ("empty_transport",)
    return ()


def target_refusal(token: str, bot: object) -> str:
    """``target_required: <reason>`` when ``token`` has no valid target in the live observation, else ``""``."""

    if token == "command_center_load_all":
        scvs = [u for u in _units(bot, "units") if _type_name(u) == "SCV"]
        return "" if scvs else "target_required: no_scv_to_load"
    if token == "tactical_nuke":
        # A nuke is a one-shot strike: never fired at a location without a visible enemy to hit.
        enemies = _units(bot, "enemy_units") + _units(bot, "enemy_structures")
        return "" if enemies else "target_required: no_enemy_target"
    return ""


def _units(bot: object, group: str) -> list[object]:
    value = getattr(bot, group, None)
    try:
        return list(value) if value is not None and not isinstance(value, (str, bytes)) else []
    except TypeError:
        return []


def _type_name(entity: object) -> str:
    type_id = getattr(entity, "type_id", None)
    return str(getattr(type_id, "name", "") or getattr(entity, "name", "") or "").upper()
