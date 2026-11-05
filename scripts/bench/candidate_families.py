"""Candidate deterministic families (frozen copy of the pre-round-2 product grammar, ALL 20+ families live).

Used ONLY by family_ranking.py to rank families on the recorded real-ASR transcripts; the product keeps at most five
(realtime_intent.PROMOTED_FAST_PATH_FAMILIES). Do not import from product code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from starcraft_commander.realtime_intent import transcript_is_raw_control_or_injection

_FAST_NEG_RE: Final[re.Pattern[str]] = re.compile(
    r"지\s*(?:마|말|않|못)|하지\s*마|안\s*(?:해|하|돼)|\b(?:don'?t|do\s+not|never|no\s+need|without)\b|말고|금지",
    re.IGNORECASE,
)
_FAST_CONTRA_RE: Final[re.Pattern[str]] = re.compile(r"(?:하면서|동시에|하고|그리고|\band\b|랑|,)", re.IGNORECASE)
_FAST_ATTACKISH_RE: Final[re.Pattern[str]] = re.compile(
    r"공격|정찰|견제|방어|수비|집결|지어|건설|attack|scout|harass|defend|build", re.IGNORECASE
)
_FAST_RETREAT_RE: Final[re.Pattern[str]] = re.compile(
    r"후퇴|퇴각|철수|물러나|빠져|도망(?!쳐)|\bretreat\b|\bfall\s*back\b", re.IGNORECASE
)
_FAST_CANCEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:공격|러시|러쉬|압박|작전|진격)(?:을|를)?\s*(?:취소|중지|중단|멈춰|그만)|"
    r"\b(?:cancel|abort|stop)\s+(?:the\s+)?(?:attack|rush|operation)",
    re.IGNORECASE,
)
_FAST_HOLD_RE: Final[re.Pattern[str]] = re.compile(r"홀드|\bhold\b(?:\s+position)?", re.IGNORECASE)
_FAST_EVAC_RE: Final[re.Pattern[str]] = re.compile(
    r"일꾼.{0,6}(?:대피|도망|피신|피해|후퇴)|\b(?:pull|evacuate).{0,12}workers?\b|"
    r"\bworkers?\b.{0,12}\b(?:back|safety|evacuate)",
    re.IGNORECASE,
)
_FAST_PULL_RE: Final[re.Pattern[str]] = re.compile(r"일꾼.{0,6}(?:수비|방어).{0,4}(?:동원|투입)|일꾼도 수비", re.IGNORECASE)
_FAST_STOP_EXPANSION_RE: Final[re.Pattern[str]] = re.compile(r"확장.{0,3}(?:중단|중지|멈춰)|\bstop\s+expand", re.IGNORECASE)
_FAST_UNIT_WORDS: Final[dict[str, str]] = {
    "마린": "TERRAN_MARINE", "해병": "TERRAN_MARINE", "marine": "TERRAN_MARINE", "불곰": "TERRAN_MARAUDER",
    "탱크": "TERRAN_SIEGETANK", "땡크": "TERRAN_SIEGETANK", "tank": "TERRAN_SIEGETANK", "밴시": "TERRAN_BANSHEE",
    "banshee": "TERRAN_BANSHEE", "바이킹": "TERRAN_VIKINGFIGHTER", "viking": "TERRAN_VIKINGFIGHTER",
    "해방선": "TERRAN_LIBERATOR", "토르": "TERRAN_THOR", "의료선": "TERRAN_MEDIVAC", "메디박": "TERRAN_MEDIVAC",
    "일꾼": "TERRAN_SCV", "scv": "TERRAN_SCV", "고스트": "TERRAN_GHOST", "리퍼": "TERRAN_REAPER",
    "사이클론": "TERRAN_CYCLONE", "배틀크루저": "TERRAN_BATTLECRUISER",
}
# (family, regex, ability, implied caster) - most specific first; the first hit wins.
_FAST_ABILITY_RULES: Final[tuple[tuple[str, re.Pattern[str], str, tuple[str, ...]], ...]] = tuple(
    (f"micro:{ability}", re.compile(pattern, re.IGNORECASE), ability, units)
    for ability, pattern, units in (
        ("unsiege", r"(?:시즈|siege)\s*(?:모드|mode)?\s*(?:풀|해제)|unsiege", ("TERRAN_SIEGETANK",)),
        ("siege_mode", r"시즈|siege", ("TERRAN_SIEGETANK",)),
        ("banshee_decloak", r"(?:밴시|banshee).{0,6}(?:클로킹|투명|cloak).{0,6}(?:풀|해제|꺼|off)|decloak", ("TERRAN_BANSHEE",)),
        ("ghost_cloak", r"(?:고스트|ghost).{0,4}(?:클로킹|투명|cloak)", ("TERRAN_GHOST",)),
        ("banshee_cloak", r"(?:밴시|banshee).{0,6}(?:클로킹|투명|cloak)|(?:클로킹|투명|cloak).{0,6}(?:밴시|banshee)|cloak the banshee", ("TERRAN_BANSHEE",)),
        ("tactical_nuke", r"전술핵|(?<![가-힣])핵(?![가-힣])\s*(?:발사|쏴|쏘)|nuke", ("TERRAN_GHOST",)),
        ("emp", r"이엠피|\bemp\b", ("TERRAN_GHOST",)),
        ("yamato", r"야마토|yamato", ("TERRAN_BATTLECRUISER",)),
        ("medivac_unload_all", r"(?:메디박|의료선|medivac).{0,6}(?:내려|unload)|(?:내려놔)", ("TERRAN_MEDIVAC",)),
        ("medivac_load", r"(?:메디박|의료선|medivac).{0,6}(?:태워|load)", ("TERRAN_MEDIVAC",)),
        ("widow_mine_unburrow", r"(?:지뢰|mine).{0,6}(?:매복\s*(?:풀|해제)|unburrow)", ("TERRAN_WIDOWMINE",)),
        ("widow_mine_burrow", r"(?:지뢰|mine).{0,6}(?:매복|burrow)", ("TERRAN_WIDOWMINE",)),
        ("auto_turret", r"자동\s*포탑|auto\s*turret", ("TERRAN_RAVEN",)),
        ("marine_stimpack", r"(?:마린|marine).{0,3}(?:스팀|stim)", ("TERRAN_MARINE",)),
        ("marauder_stimpack", r"(?:불곰|marauder).{0,3}(?:스팀|stim)", ("TERRAN_MARAUDER",)),
        ("stimpack", r"스팀|\bstim\b", ()),
    )
)
_FAST_PROD_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"생산|뽑|찍|계속|\bkeep\s+making\b|\bstart\s+producing\b|\bbuild\s+more\b", re.IGNORECASE
)
_FAST_RESEARCH_WORD_RE: Final[re.Pattern[str]] = re.compile(r"연구|업그레이드|research|upgrade", re.IGNORECASE)
_FAST_STRUCT_WORD_RE: Final[re.Pattern[str]] = re.compile(
    r"아카데미|병영|공장|우주공항|보급고|사령부|정제소|barracks|factory|starport|academy|depot", re.IGNORECASE
)
_FAST_EXPAND_RE: Final[re.Pattern[str]] = re.compile(r"멀티\s*먹|확장해|앞마당\s*확장|\bexpand\b|확장\s*해", re.IGNORECASE)
_FAST_RESEARCH_RULES: Final[tuple[tuple[str, re.Pattern[str], str], ...]] = (
    ("macro:research_stimpack", re.compile(r"스팀팩?\s*연구|research\s+stim", re.IGNORECASE), "STIMPACK"),
    ("macro:research_combatshield", re.compile(r"전투\s*방패\s*연구|research\s+combat\s*shield", re.IGNORECASE), "COMBATSHIELD"),
)

@dataclass(frozen=True)
class FastPathMatch:
    family: str
    arguments: dict[str, object]


def match_fast_path(transcript: str) -> FastPathMatch | None:
    """Extended deterministic layer; ``None`` means "ask the model".

    Families: single-flag emergencies, single-ability micro, research of
    stimpack/combat shield, expansion, and single-unit-type standing production.
    """

    if not isinstance(transcript, str):
        return None
    text = " ".join(transcript.split()).strip()
    if not text or len(text) > 80:
        return None
    if _FAST_NEG_RE.search(text) or transcript_is_raw_control_or_injection(text):
        return None
    match = _fast_emergency(text) or _fast_micro(text) or _fast_macro(text)
    if match is None:
        return None
    return match


def _fast_emergency(text: str) -> FastPathMatch | None:
    flags: dict[str, str] = {}
    if _FAST_RETREAT_RE.search(text):
        flags["force_retreat"] = "emergency:force_retreat"
    if _FAST_CANCEL_RE.search(text):
        flags["cancel_attacks"] = "emergency:cancel_attacks"
    if _FAST_HOLD_RE.search(text):
        flags["hold_position"] = "emergency:hold_position"
    if _FAST_EVAC_RE.search(text):
        flags["evacuate_workers"] = "emergency:evacuate_workers"
    if _FAST_PULL_RE.search(text):
        flags["pull_workers_for_defense"] = "emergency:pull_workers"
    if _FAST_STOP_EXPANSION_RE.search(text):
        flags["stop_expansion"] = "emergency:stop_expansion"
    if not flags:
        return None
    stripped = _FAST_CANCEL_RE.sub(" ", text)
    lowered = text.lower()
    if _FAST_ATTACKISH_RE.search(stripped) or any(
        word in lowered for word in _FAST_UNIT_WORDS if word not in ("일꾼", "scv")
    ):
        return None
    if flags.get("force_retreat") and "공격" in text and "cancel_attacks" not in flags:
        return None
    if "hold_position" in flags and len(flags) > 1:
        return None
    if "evacuate_workers" in flags and "pull_workers_for_defense" in flags:
        flags.pop("evacuate_workers")
    family = next(iter(flags.values())) if len(flags) == 1 else "emergency:combo"
    return FastPathMatch(
        family,
        {"command_layer": "emergency", "goal": text, "emergency": {name: True for name in flags}},
    )


def _fast_micro(text: str) -> FastPathMatch | None:
    if (
        _FAST_RESEARCH_WORD_RE.search(text)
        or _FAST_STRUCT_WORD_RE.search(text)
        or _FAST_ATTACKISH_RE.search(text)
        or _FAST_PROD_VERB_RE.search(text)
    ):
        return None
    for family, pattern, ability, units in _FAST_ABILITY_RULES:
        if pattern.search(text):
            item: dict[str, object] = {"ability": ability}
            if units:
                item["unit_classes"] = list(units)
            return FastPathMatch(family, {"command_layer": "micro", "goal": text, "micro": [item]})
    return None


def _fast_macro(text: str) -> FastPathMatch | None:
    for family, pattern, target in _FAST_RESEARCH_RULES:
        if pattern.search(text):
            return FastPathMatch(
                family,
                {
                    "command_layer": "macro",
                    "goal": text,
                    "macro": {"task_type": "tech_transition", "production_targets": [target]},
                },
            )
    if _FAST_ATTACKISH_RE.search(text):
        return None
    if _FAST_EXPAND_RE.search(text):
        return FastPathMatch(
            "macro:expand",
            {"command_layer": "macro", "goal": text, "macro": {"task_type": "expand_or_land_command_center"}},
        )
    if (
        _FAST_PROD_VERB_RE.search(text)
        and not _FAST_CONTRA_RE.search(text)
        and not _FAST_STRUCT_WORD_RE.search(text)
    ):
        lowered = text.lower()
        found = sorted({token for word, token in _FAST_UNIT_WORDS.items() if word in lowered})
        if len(found) == 1:
            return FastPathMatch(
                "macro:production",
                {
                    "command_layer": "macro",
                    "goal": text,
                    "macro": {
                        "task_type": "sustain_production",
                        "production_targets": found,
                        "standing_order": True,
                    },
                },
            )
    return None
