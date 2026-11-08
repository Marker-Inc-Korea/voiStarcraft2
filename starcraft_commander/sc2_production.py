"""Train / build / morph execution facts the Direct path needs beyond the round-4 adapter tables.

Every id below is the catalog's header command (``scripts/sc2_accuracy/gen_catalog_facts.py`` from the s2client-api
``ABILITY`` header + burnysc2); ``tests/test_sc2_exec_production.py`` re-derives them and fails on drift.
"""

from __future__ import annotations

from typing import Final

PRODUCTION_ABILITIES: Final[dict[str, tuple[str, str, tuple[str, ...]]]] = {
    # Ghost Academy: BUILD_NUKE (556-style no-target order on the academy itself, header id 710).
    "arm_nuke": ("BUILD_NUKE", "none", ("GHOSTACADEMY",)),
    # Supply depot lower/raise: header ids 556 / 558, no target; the depot changes type (SUPPLYDEPOT <-> LOWERED).
    "supply_depot_lower": ("MORPH_SUPPLYDEPOT_LOWER", "none", ("SUPPLYDEPOT",)),
    "supply_depot_raise": ("MORPH_SUPPLYDEPOT_RAISE", "none", ("SUPPLYDEPOTLOWERED",)),
}
"""Semantic ability token -> (AbilityId member, target mode, caster structure type names)."""

STRUCTURE_CASTER_TOKENS: Final[frozenset[str]] = frozenset(PRODUCTION_ABILITIES)
"""These abilities are cast by own STRUCTURES (``bot.structures``), not by army units."""

UNIT_SUBJECT_ALIASES: Final[dict[str, str]] = {"HELLBAT": "HELLIONTANK"}
"""python-sc2 has no ``UnitTypeId.HELLBAT``: the Hellbat is ``HELLIONTANK`` (trained by TRAIN_HELLBAT at a Factory)."""

_STRUCTURE_KO: Final[dict[str, str]] = {
    "barracks": "병영", "factory": "군수공장", "starport": "우주공항", "commandcenter": "사령부",
}

PRODUCTION_REFUSAL_KO: Final[dict[str, str]] = {
    "pinned_builder_missing": "건설할 수 있는 SCV가 없습니다",
    "missing_pinned_builder_capability": "건설할 수 있는 SCV가 없습니다",
    **{
        f"no_ready_idle_{key}": f"준비된 유휴 {name}이(가) 없습니다 (건설 중이거나 생산/연구 중이면 안 됩니다)"
        for key, name in _STRUCTURE_KO.items()
    },
}
"""Adapter refusal details -> Korean (merged into ``narrator.SC2_ACTION_REFUSAL_KOREAN_REASONS``)."""
