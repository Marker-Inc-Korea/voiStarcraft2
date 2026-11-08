"""Documented Terran structure prerequisites (``UnitTypeData.tech_requirement``).

The s2client-api documents ``tech_requirement`` as "the structure required to build this unit"; the game answers an
order without it with ``ActionResult.BuildTechRequirementsNotMet`` (error.proto).  The numbers below are checked
against burnysc2 ``TERRAN_TECH_REQUIREMENT`` and stableid.json by ``tests/test_sc2_vocab.py``
(``test_prerequisite_table_matches_burnysc2``), so a drift is a red test, not a silent wrong refusal.

Names are python-sc2 ``UnitTypeId`` member names without the ``TERRAN_`` prefix.  A flying or morphed form of the
required structure satisfies it (``BARRACKSFLYING`` counts as ``BARRACKS``; an Orbital/Planetary as a Command Center).
"""

from __future__ import annotations

from typing import Final

TERRAN_TECH_REQUIREMENTS: Final[dict[str, str]] = {
    "BARRACKS": "SUPPLYDEPOT",
    "ORBITALCOMMAND": "BARRACKS",
    "BUNKER": "BARRACKS",
    "GHOSTACADEMY": "BARRACKS",
    "FACTORY": "BARRACKS",
    "GHOST": "GHOSTACADEMY",
    "ARMORY": "FACTORY",
    "THOR": "ARMORY",
    "HELLIONTANK": "ARMORY",
    "STARPORT": "FACTORY",
    "FUSIONCORE": "STARPORT",
    "BATTLECRUISER": "FUSIONCORE",
    "PLANETARYFORTRESS": "ENGINEERINGBAY",
    "MISSILETURRET": "ENGINEERINGBAY",
    "SENSORTOWER": "ENGINEERINGBAY",
}

# Absent from burnysc2 ``TERRAN_TECH_REQUIREMENT`` (so kept out of the table above, which a test pins to it), but
# python-sc2 ``TRAIN_INFO[SCV][ENGINEERINGBAY]['required_building']`` names the Command Center (any townhall form).
TERRAN_EXTRA_TECH_REQUIREMENTS: Final[dict[str, str]] = {"ENGINEERINGBAY": "COMMANDCENTER"}

# Structure type names that satisfy a requirement for ``key`` (python-sc2 EQUIVALENTS_FOR_TECH_PROGRESS + morphs).
TERRAN_TECH_EQUIVALENTS: Final[dict[str, frozenset[str]]] = {
    "COMMANDCENTER": frozenset({"COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS", "COMMANDCENTERFLYING", "ORBITALCOMMANDFLYING"}),
    "SUPPLYDEPOT": frozenset({"SUPPLYDEPOT", "SUPPLYDEPOTLOWERED", "SUPPLYDEPOTDROP"}),
    "BARRACKS": frozenset({"BARRACKS", "BARRACKSFLYING"}),
    "FACTORY": frozenset({"FACTORY", "FACTORYFLYING"}),
    "STARPORT": frozenset({"STARPORT", "STARPORTFLYING"}),
}

TERRAN_TECH_REQUIREMENT_KOREAN: Final[dict[str, str]] = {
    "COMMANDCENTER": "사령부",
    "SUPPLYDEPOT": "보급고",
    "BARRACKS": "병영",
    "FACTORY": "군수공장",
    "STARPORT": "우주공항",
    "GHOSTACADEMY": "유령 사관학교",
    "ARMORY": "무기고",
    "FUSIONCORE": "융합로",
    "ENGINEERINGBAY": "공학 연구소",
}


def required_structure(subject: str) -> str:
    """The structure ``subject`` (a unit or structure type name) needs, or ``""``."""

    name = str(subject or "").strip().upper().removeprefix("TERRAN_")
    return TERRAN_TECH_REQUIREMENTS.get(name) or TERRAN_EXTRA_TECH_REQUIREMENTS.get(name, "")


def satisfying_types(required: str) -> frozenset[str]:
    return TERRAN_TECH_EQUIVALENTS.get(required, frozenset({required}))
