"""Pipeline C: deterministic (no-LLM) fast paths over the transcript.

Two variants, both return submit_command args or ``None`` (= abstain, defer to a model):

* ``prod_regex``  - exactly the regex fast path that already ships in
  starcraft_commander/web_gui.py (``_micromachine_request_is_emergency`` regexes +
  negation guard). Its real behaviour on a hit is
  ``_micromachine_emergency_safety_output``: force_retreat AND cancel_attacks are both
  set, whichever words were said. We reproduce that faithfully, so a lone
  "공격 취소" is scored as the (over-broad) emergency the backend would really issue.
* ``ext_rules``   - proposed extended grammar (emergency flag-precise + single-ability micro
  + simple standing production/expansion). It is deliberately conservative: any
  negation, any unit/target/route/count it cannot account for, or conflicting verbs
  -> abstain. It is hand-written from game knowledge BEFORE seeing model results;
  it is the same author as the corpus, so its recall is optimistic (see doc caveat).
"""

from __future__ import annotations

import re

# -- production regexes (imported so we measure the shipped behaviour) ----------------
_PROD = None


def _prod():
    global _PROD
    if _PROD is None:
        from starcraft_commander import web_gui as w

        _PROD = w
    return _PROD


def prod_regex(text: str) -> dict | None:
    w = _prod()
    if not w._micromachine_request_is_emergency(text, None):
        return None
    return {
        "command_layer": "emergency",
        "goal": text,
        "emergency": {"force_retreat": True, "cancel_attacks": True},
        "confidence": 1.0,
    }


# -- extended deterministic grammar ----------------------------------------------------
_NEG = re.compile(
    r"지\s*(?:마|말|않|못)|하지\s*마|안\s*(?:해|하|돼)|\b(?:don'?t|do\s+not|never|no\s+need|without)\b|말고|금지",
    re.IGNORECASE,
)
_CONTRA = re.compile(r"(?:하면서|동시에|하고|그리고|\band\b|랑|,)", re.IGNORECASE)
_RAW = re.compile(r"\d{2,}|좌표|태그|\bid\b|클릭|click|coordinate|ignore|무시|시스템\s*프롬프트|프롬프트|개발자", re.IGNORECASE)
_ATTACKISH = re.compile(r"공격|정찰|견제|방어|수비|집결|지어|건설|attack|scout|harass|defend|build", re.IGNORECASE)

_RETREAT = re.compile(r"후퇴|퇴각|철수|물러나|빠져|도망(?!쳐)|\bretreat\b|\bfall\s*back\b", re.IGNORECASE)
_CANCEL = re.compile(r"(?:공격|러시|러쉬|압박|작전|진격)(?:을|를)?\s*(?:취소|중지|중단|멈춰|그만)|\b(?:cancel|abort|stop)\s+(?:the\s+)?(?:attack|rush|operation)", re.IGNORECASE)
_HOLD = re.compile(r"홀드|\bhold\b(?:\s+position)?", re.IGNORECASE)
_EVAC = re.compile(r"일꾼.{0,6}(?:대피|도망|피신|피해|후퇴)|\b(?:pull|evacuate).{0,12}workers?\b|\bworkers?\b.{0,12}\b(?:back|safety|evacuate)", re.IGNORECASE)
_PULL = re.compile(r"일꾼.{0,6}(?:수비|방어).{0,4}(?:동원|투입)|일꾼도 수비", re.IGNORECASE)
_STOPEXP = re.compile(r"확장.{0,3}(?:중단|중지|멈춰)|\bstop\s+expand", re.IGNORECASE)

# ability: (regex, token, implied unit types)
_ABILITIES: list[tuple[re.Pattern, str, list[str]]] = [
    (re.compile(r"(?:시즈|siege)\s*(?:모드|mode)?\s*(?:풀|해제)|unsiege", re.IGNORECASE), "unsiege", ["siege_tank"]),
    (re.compile(r"시즈|siege", re.IGNORECASE), "siege_mode", ["siege_tank"]),
    (re.compile(r"(?:밴시|banshee).{0,6}(?:클로킹|투명|cloak).{0,6}(?:풀|해제|꺼|off)|decloak", re.IGNORECASE), "banshee_decloak", ["banshee"]),
    (re.compile(r"(?:고스트|ghost).{0,4}(?:클로킹|투명|cloak)", re.IGNORECASE), "ghost_cloak", ["ghost"]),
    (re.compile(r"(?:밴시|banshee).{0,6}(?:클로킹|투명|cloak)|(?:클로킹|투명|cloak).{0,6}(?:밴시|banshee)|cloak the banshee", re.IGNORECASE), "banshee_cloak", ["banshee"]),
    (re.compile(r"전술핵|(?<![가-힣])핵(?![가-힣])\s*(?:발사|쏴|쏘)|nuke", re.IGNORECASE), "tactical_nuke", ["ghost"]),
    (re.compile(r"이엠피|\bemp\b", re.IGNORECASE), "emp", ["ghost"]),
    (re.compile(r"야마토|yamato", re.IGNORECASE), "yamato", ["battlecruiser"]),
    (re.compile(r"(?:메디박|의료선|medivac).{0,6}(?:내려|unload)|(?:내려놔)", re.IGNORECASE), "medivac_unload_all", ["medivac"]),
    (re.compile(r"(?:메디박|의료선|medivac).{0,6}(?:태워|load)", re.IGNORECASE), "medivac_load", ["medivac"]),
    (re.compile(r"(?:지뢰|mine).{0,6}(?:매복\s*(?:풀|해제)|unburrow)", re.IGNORECASE), "widow_mine_unburrow", ["widow_mine"]),
    (re.compile(r"(?:지뢰|mine).{0,6}(?:매복|burrow)", re.IGNORECASE), "widow_mine_burrow", ["widow_mine"]),
    (re.compile(r"자동\s*포탑|auto\s*turret", re.IGNORECASE), "auto_turret", ["raven"]),
    (re.compile(r"(?:마린|marine).{0,3}(?:스팀|stim)", re.IGNORECASE), "marine_stimpack", ["marine"]),
    (re.compile(r"(?:불곰|marauder).{0,3}(?:스팀|stim)", re.IGNORECASE), "marauder_stimpack", ["marauder"]),
    (re.compile(r"스팀|\bstim\b", re.IGNORECASE), "stimpack", []),
]
_PROD_VERB = re.compile(r"생산|뽑|찍|계속|\bkeep\s+making\b|\bstart\s+producing\b|\bbuild\s+more\b", re.IGNORECASE)
_UNITS = {
    "마린": "marine", "해병": "marine", "marine": "marine", "불곰": "marauder", "탱크": "siege_tank", "땡크": "siege_tank",
    "tank": "siege_tank", "밴시": "banshee", "banshee": "banshee", "바이킹": "viking", "viking": "viking",
    "해방선": "liberator", "토르": "thor", "의료선": "medivac", "메디박": "medivac", "일꾼": "scv", "scv": "scv",
    "고스트": "ghost", "리퍼": "reaper", "사이클론": "cyclone", "배틀크루저": "battlecruiser",
}
_RESEARCH_WORD = re.compile(r"연구|업그레이드|research|upgrade", re.IGNORECASE)
_STRUCT_WORD = re.compile(r"아카데미|병영|공장|우주공항|보급고|사령부|정제소|barracks|factory|starport|academy|depot", re.IGNORECASE)
_EXPAND = re.compile(r"멀티\s*먹|확장해|앞마당\s*확장|\bexpand\b|확장\s*해", re.IGNORECASE)
_RESEARCH = [
    (re.compile(r"스팀팩?\s*연구|research\s+stim", re.IGNORECASE), "stimpack"),
    (re.compile(r"전투\s*방패\s*연구|research\s+combat\s*shield", re.IGNORECASE), "combatshield"),
]


def ext_rules(text: str) -> dict | None:
    t = text.strip()
    if not t or _NEG.search(t) or _RAW.search(t):
        return None
    # --- emergency (flag precise). Anything with extra army verbs/units abstains.
    flags = {}
    if _RETREAT.search(t):
        flags["force_retreat"] = True
    if _CANCEL.search(t):
        flags["cancel_attacks"] = True
    if _HOLD.search(t):
        flags["hold_position"] = True
    if _EVAC.search(t):
        flags["evacuate_workers"] = True
    if _PULL.search(t):
        flags["pull_workers_for_defense"] = True
    if _STOPEXP.search(t):
        flags["stop_expansion"] = True
    if flags:
        stripped = _CANCEL.sub(" ", t)
        if _ATTACKISH.search(stripped) or any(u in t.lower() for u in _UNITS if u not in ("일꾼", "scv")):
            return None
        if flags.get("force_retreat") and "공격" in t and "cancel_attacks" not in flags:
            return None
        if "hold_position" in flags and len(flags) > 1:
            return None
        if "evacuate_workers" in flags and "pull_workers_for_defense" in flags:
            flags.pop("evacuate_workers")
        return {"command_layer": "emergency", "goal": t, "emergency": flags, "confidence": 0.95}
    # --- research / structure words mean macro (tech), never a micro ability (revised once after the first full
    #     run showed '스팀팩 연구해' fired the stim ability; see doc section 8)
    if _RESEARCH_WORD.search(t) or _STRUCT_WORD.search(t):
        pass
    # --- micro: exactly one ability, no army/production verbs
    elif _ATTACKISH.search(t) or _PROD_VERB.search(t):
        pass
    else:
        hits = []
        for rx, token, units in _ABILITIES:
            if rx.search(t):
                hits.append((token, units))
                break  # ordered most-specific first
        if hits:
            token, units = hits[0]
            m: dict = {"ability": token}
            if units:
                m["unit_types"] = units
            return {"command_layer": "micro", "goal": t, "micro": m, "confidence": 0.9}
    # --- macro: research / expand / single-unit-type standing production
    for rx, tgt in _RESEARCH:
        if rx.search(t):
            return {"command_layer": "macro", "goal": t, "confidence": 0.9,
                    "production": {"task_type": "tech_transition", "targets": [tgt], "allow_prerequisites": True}}
    if _EXPAND.search(t) and not _ATTACKISH.search(t):
        return {"command_layer": "macro", "goal": t, "confidence": 0.85,
                "production": {"task_type": "expand_or_land_command_center"}}
    if _PROD_VERB.search(t) and not _ATTACKISH.search(t) and not _CONTRA.search(t) and not _STRUCT_WORD.search(t):
        found = {v for k, v in _UNITS.items() if k in t.lower()}
        if len(found) == 1:
            return {"command_layer": "macro", "goal": t, "confidence": 0.85,
                    "production": {"task_type": "sustain_production", "targets": sorted(found),
                                   "allow_prerequisites": True}}
    return None


FASTPATHS = {"prod_regex": prod_regex, "ext_rules": ext_rules}


def run_named(which: str, text: str):
    """Dispatch a deterministic path; result is a shipped-contract tool call or None.

    shipped_regex : web_gui emergency regex only (current code)
    shipped_fast  : baseline deterministic layer = emergency regex + match_micro_fast_path + negation guards
    ext_rules     : the extended grammar as promoted into the product (realtime_intent.match_fast_path); the private
                    bench grammar in this file is kept only for the private-schema run
    """
    from starcraft_commander import realtime_intent as RI

    w = _prod()
    if which == "ext_rules":
        m = RI.match_fast_path(text)
        return None if m is None else m.arguments
    if which in ("shipped_regex", "prod_regex", "shipped_fast"):
        if w._micromachine_request_is_emergency(text, None):
            out = w._micromachine_emergency_safety_output(text)
            return {"command_layer": "emergency", "goal": text, "emergency": {k: v for k, v in out["emergency"].items() if v}}
        if which != "shipped_fast":
            return None
        micro = RI.match_micro_fast_path(text)
        if micro is not None:
            return micro
        if RI.transcript_is_pure_negated_emergency(text) or RI.micro_phrase_is_negated(text):
            return {"command_layer": "operation", "goal": text, "clarification": "negation_guard"}
        return None
    raise KeyError(which)
