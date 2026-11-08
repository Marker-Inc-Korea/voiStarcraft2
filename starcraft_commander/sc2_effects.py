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
