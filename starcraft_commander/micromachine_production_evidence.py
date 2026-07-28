"""Causal ProductionManager evidence shared by smoke, soak, and matrix gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterable, Mapping, Sequence


PRODUCTION_DOCTRINE_EVIDENCE_VALUES: Final[frozenset[str]] = frozenset(
    {
        "queued",
        "queued_existing",
        "command_issued",
        "represented_satisfied",
    }
)

EXPECTED_PRODUCTION_PAIRS_BY_DOCTRINE: Final[
    Mapping[str, frozenset[tuple[str, str]]]
] = {
    "marine_rush": frozenset(
        {
            ("marine_pressure", "Marine"),
            ("bio_facility", "Barracks"),
        }
    ),
    "bio_pressure": frozenset(
        {
            ("bio_facility", "Barracks"),
            ("bio_marauder_techlab", "BarracksTechLab"),
            ("bio_ghost_techlab", "BarracksTechLab"),
            ("bio_marauder_support", "Marauder"),
            ("starport_transition", "Starport"),
            ("medivac_drop_support", "Medivac"),
        }
    ),
    "tank_defensive_hold": frozenset(
        {
            ("factory_transition", "Factory"),
            ("factory_techlab", "FactoryTechLab"),
            ("siege_tank_composition", "SiegeTank"),
        }
    ),
    "siege_contain": frozenset(
        {
            ("factory_transition", "Factory"),
            ("factory_techlab", "FactoryTechLab"),
            ("siege_tank_composition", "SiegeTank"),
        }
    ),
    "contain_enemy_natural": frozenset(
        {
            ("factory_transition", "Factory"),
            ("factory_techlab", "FactoryTechLab"),
            ("siege_tank_composition", "SiegeTank"),
        }
    ),
    "mech_transition": frozenset(
        {
            ("factory_transition", "Factory"),
            ("factory_techlab", "FactoryTechLab"),
            ("hellion_harassment", "Hellion"),
            ("cyclone_mech", "Cyclone"),
            ("siege_tank_composition", "SiegeTank"),
            ("thor_mech", "Thor"),
        }
    ),
    "drop_harassment": frozenset(
        {
            ("starport_transition", "Starport"),
            ("drop_reactor", "StarportReactor"),
            ("medivac_drop_support", "Medivac"),
            ("factory_transition", "Factory"),
            ("hellion_harassment", "Hellion"),
            ("reaper_harassment", "Reaper"),
        }
    ),
    "worker_line_harassment": frozenset(
        {
            ("starport_transition", "Starport"),
            ("drop_reactor", "StarportReactor"),
            ("medivac_drop_support", "Medivac"),
            ("factory_transition", "Factory"),
            ("hellion_harassment", "Hellion"),
            ("reaper_harassment", "Reaper"),
        }
    ),
    "expand_macro": frozenset({("expand_macro", "CommandCenter")}),
    "anti_air_response": frozenset(
        {
            ("starport_transition", "Starport"),
            ("anti_air_detection_support", "EngineeringBay"),
            ("anti_air_viking", "Viking"),
        }
    ),
}

ACTUAL_PRODUCTION_ITEM_ALIASES: Final[Mapping[str, str]] = {
    "TERRAN_SUPPLYDEPOT": "SupplyDepot",
    "TERRAN_BARRACKS": "Barracks",
    "TERRAN_BARRACKSTECHLAB": "BarracksTechLab",
    "BARRACKS_TECHLAB": "BarracksTechLab",
    "TERRAN_FACTORY": "Factory",
    "TERRAN_FACTORYTECHLAB": "FactoryTechLab",
    "FACTORY_TECHLAB": "FactoryTechLab",
    "TERRAN_STARPORT": "Starport",
    "TERRAN_STARPORTTECHLAB": "StarportTechLab",
    "STARPORT_TECHLAB": "StarportTechLab",
    "TERRAN_STARPORTREACTOR": "StarportReactor",
    "TERRAN_COMMANDCENTER": "CommandCenter",
    "TERRAN_ENGINEERINGBAY": "EngineeringBay",
    "TERRAN_ARMORY": "Armory",
    "TERRAN_FUSIONCORE": "FusionCore",
    "TERRAN_GHOSTACADEMY": "GhostAcademy",
    "TERRAN_MARINE": "Marine",
    "TERRAN_MARAUDER": "Marauder",
    "TERRAN_REAPER": "Reaper",
    "TERRAN_GHOST": "Ghost",
    "TERRAN_HELLION": "Hellion",
    "TERRAN_HELLIONTANK": "Hellion",
    "TERRAN_WIDOWMINE": "WidowMine",
    "TERRAN_WIDOWMINEBURROWED": "WidowMine",
    "TERRAN_CYCLONE": "Cyclone",
    "TERRAN_THOR": "Thor",
    "TERRAN_THORAP": "Thor",
    "TERRAN_SIEGETANK": "SiegeTank",
    "TERRAN_SIEGETANKSIEGED": "SiegeTank",
    "TERRAN_MEDIVAC": "Medivac",
    "TERRAN_RAVEN": "Raven",
    "TERRAN_VIKINGFIGHTER": "Viking",
    "TERRAN_VIKINGASSAULT": "Viking",
    "TERRAN_BANSHEE": "Banshee",
    "TERRAN_LIBERATOR": "Liberator",
    "TERRAN_LIBERATORAG": "Liberator",
    "TERRAN_BATTLECRUISER": "Battlecruiser",
}

EXPECTED_ACTUAL_PRODUCTION_ITEM_BY_PAIR: Final[
    Mapping[tuple[str, str], str]
] = {
    pair: canonical_item
    for pairs in EXPECTED_PRODUCTION_PAIRS_BY_DOCTRINE.values()
    for pair in pairs
    for canonical_item in (pair[1],)
}


@dataclass(frozen=True)
class CausalProductionEvidence:
    """Result of matching one doctrine event to its exact later SC2 command."""

    doctrine_entry: Mapping[str, object] | None
    actual_command_entry: Mapping[str, object] | None
    observed_actions: frozenset[str]
    observed_doctrine_items: frozenset[str]
    observed_actual_items: frozenset[str]
    observed_actual_commands: frozenset[str]
    expected_actual_items: frozenset[str]
    expected_pairs: frozenset[tuple[str, str]]

    @property
    def matched(self) -> bool:
        return (
            self.doctrine_entry is not None
            and self.actual_command_entry is not None
        )


def canonical_actual_production_item(item: object) -> str:
    raw = str(item or "").strip()
    return ACTUAL_PRODUCTION_ITEM_ALIASES.get(raw, raw)


def expected_production_pairs(
    doctrine: str,
    *,
    expected_actions: Iterable[str] = (),
    expected_items: Iterable[str] = (),
) -> frozenset[tuple[str, str]]:
    """Return valid action/item pairs for the requested profile contract."""

    actions = {str(value).strip() for value in expected_actions if str(value).strip()}
    items = {str(value).strip() for value in expected_items if str(value).strip()}
    known_pairs = EXPECTED_PRODUCTION_PAIRS_BY_DOCTRINE.get(
        str(doctrine or "").strip(),
        frozenset(),
    )
    if known_pairs:
        return frozenset(
            pair
            for pair in known_pairs
            if (not actions or pair[0] in actions)
            and (not items or pair[1] in items)
        )
    if actions and items:
        return frozenset((action, item) for action in actions for item in items)
    return frozenset()


def find_causal_production_evidence(
    telemetry_entries: Sequence[Mapping[str, object]],
    *,
    expected_doctrine: str,
    expected_update_id: str,
    expected_pairs: Iterable[tuple[str, str]],
    allowed_doctrine_evidence: Iterable[
        str
    ] = PRODUCTION_DOCTRINE_EVIDENCE_VALUES,
    min_doctrine_frame: int = 0,
) -> CausalProductionEvidence:
    """Match an exact doctrine action/item to its same-update later command."""

    pair_filter = frozenset(
        (str(action).strip(), str(item).strip())
        for action, item in expected_pairs
        if str(action).strip() and str(item).strip()
    )
    allowed_evidence = frozenset(
        str(value).strip()
        for value in allowed_doctrine_evidence
        if str(value).strip()
    )
    production_entries = tuple(_production_entries(telemetry_entries))
    doctrine_candidates: list[
        tuple[int, str, str, str, Mapping[str, object]]
    ] = []
    actual_commands: list[
        tuple[int, str, str, Mapping[str, object]]
    ] = []
    observed_actions: set[str] = set()
    observed_doctrine_items: set[str] = set()
    observed_actual_items: set[str] = set()
    observed_actual_commands: set[str] = set()

    for production in production_entries:
        action = str(production.get("last_doctrine_action", "") or "").strip()
        doctrine_item = str(
            production.get("last_doctrine_queue_item", "") or ""
        ).strip()
        if action and action != "none":
            observed_actions.add(action)
        if doctrine_item and doctrine_item != "none":
            observed_doctrine_items.add(doctrine_item)

        if _matches_doctrine_contract(
            production,
            expected_doctrine=expected_doctrine,
            expected_update_id=expected_update_id,
            expected_pairs=pair_filter,
            allowed_evidence=allowed_evidence,
            min_doctrine_frame=min_doctrine_frame,
        ):
            doctrine_candidates.append(
                (
                    _int_value(production.get("last_doctrine_frame")),
                    str(
                        production.get("last_doctrine_update_id", "") or ""
                    ).strip(),
                    action,
                    doctrine_item,
                    production,
                )
            )

        actual_item = canonical_actual_production_item(
            production.get("last_actual_production_command_item", "")
        )
        actual_kind = str(
            production.get("last_actual_production_command_kind", "") or ""
        ).strip()
        actual_update_id = str(
            production.get("last_actual_production_command_update_id", "") or ""
        ).strip()
        actual_frame = _int_value(
            production.get("last_actual_production_command_frame")
        )
        actual_count = _int_value(
            production.get("actual_production_command_issued_count")
        )
        if actual_item and actual_item != "none":
            observed_actual_items.add(actual_item)
        if (
            actual_kind
            and actual_kind != "none"
            and actual_item
            and actual_item != "none"
        ):
            observed_actual_commands.add(
                f"{actual_kind}|{actual_item}@{actual_frame}"
            )
        if (
            actual_count > 0
            and actual_kind
            and actual_kind != "none"
            and actual_item
            and actual_item != "none"
            and actual_update_id
            and actual_frame > 0
        ):
            actual_commands.append(
                (
                    actual_frame,
                    actual_update_id,
                    actual_item,
                    production,
                )
            )

    doctrine_candidates.sort(key=lambda candidate: candidate[0])
    actual_commands.sort(key=lambda candidate: candidate[0])
    expected_actual_items = frozenset(
        EXPECTED_ACTUAL_PRODUCTION_ITEM_BY_PAIR.get(
            (action, item),
            canonical_actual_production_item(item),
        )
        for _, _, action, item, _ in doctrine_candidates
    )
    for doctrine_frame, update_id, action, item, doctrine_entry in (
        doctrine_candidates
    ):
        expected_actual_item = EXPECTED_ACTUAL_PRODUCTION_ITEM_BY_PAIR.get(
            (action, item),
            canonical_actual_production_item(item),
        )
        for actual_frame, actual_update_id, actual_item, actual_entry in (
            actual_commands
        ):
            if actual_update_id != update_id:
                continue
            if actual_item != expected_actual_item:
                continue
            if actual_frame < doctrine_frame:
                continue
            return CausalProductionEvidence(
                doctrine_entry=doctrine_entry,
                actual_command_entry=actual_entry,
                observed_actions=frozenset(observed_actions),
                observed_doctrine_items=frozenset(observed_doctrine_items),
                observed_actual_items=frozenset(observed_actual_items),
                observed_actual_commands=frozenset(observed_actual_commands),
                expected_actual_items=expected_actual_items,
                expected_pairs=pair_filter,
            )

    return CausalProductionEvidence(
        doctrine_entry=(
            doctrine_candidates[0][4] if doctrine_candidates else None
        ),
        actual_command_entry=None,
        observed_actions=frozenset(observed_actions),
        observed_doctrine_items=frozenset(observed_doctrine_items),
        observed_actual_items=frozenset(observed_actual_items),
        observed_actual_commands=frozenset(observed_actual_commands),
        expected_actual_items=expected_actual_items,
        expected_pairs=pair_filter,
    )


def _production_entries(
    telemetry_entries: Sequence[Mapping[str, object]],
) -> Iterable[Mapping[str, object]]:
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        production = managers.get("ProductionManager")
        if isinstance(production, Mapping):
            yield production


def _matches_doctrine_contract(
    production: Mapping[str, object],
    *,
    expected_doctrine: str,
    expected_update_id: str,
    expected_pairs: frozenset[tuple[str, str]],
    allowed_evidence: frozenset[str],
    min_doctrine_frame: int,
) -> bool:
    doctrine = str(production.get("strategy_doctrine", "") or "").strip()
    last_doctrine = str(production.get("last_doctrine", "") or "").strip()
    policy_update_id = str(
        production.get("policy_update_id", "") or ""
    ).strip()
    last_update_id = str(
        production.get("last_doctrine_update_id", "") or ""
    ).strip()
    action = str(production.get("last_doctrine_action", "") or "").strip()
    item = str(production.get("last_doctrine_queue_item", "") or "").strip()
    evidence = str(
        production.get("last_doctrine_evidence", "") or ""
    ).strip()
    doctrine_frame = _int_value(production.get("last_doctrine_frame"))
    policy_issued_at = _int_value(production.get("policy_issued_at_frame"))
    if production.get("bounded_intervention") is not True:
        return False
    if expected_doctrine and (
        doctrine != expected_doctrine or last_doctrine != expected_doctrine
    ):
        return False
    if expected_update_id and (
        policy_update_id != expected_update_id
        or last_update_id != expected_update_id
    ):
        return False
    if not policy_update_id or last_update_id != policy_update_id:
        return False
    if production.get("last_doctrine_fresh") is not True:
        return False
    if expected_pairs and (action, item) not in expected_pairs:
        return False
    if not expected_pairs:
        return False
    if evidence not in allowed_evidence:
        return False
    if doctrine_frame <= 0 or doctrine_frame < min_doctrine_frame:
        return False
    if policy_issued_at > 0 and doctrine_frame < policy_issued_at:
        return False
    if evidence == "represented_satisfied":
        required_count = _int_value(
            production.get("last_doctrine_required_count")
        )
        represented_count = _int_value(
            production.get("last_doctrine_represented_count")
        )
        if (
            required_count <= 0
            or represented_count < required_count
            or production.get("last_doctrine_prerequisites_satisfied")
            is not True
        ):
            return False
    return True


def _int_value(value: object) -> int:
    if type(value) is bool:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
