"""Voice intent -> concrete Terran API command (catalog resolver, round 5).

The user speaks INTENT ("보병 공격력 올려줘", "밴시 은신 연구", "의료선 빠르게"), never an ability name.  The realtime model
only copies the plain phrase (V2 ``catalog.intent``) or names a catalog id (V1 ``catalog.command_id``); THIS module picks
the concrete command against the REAL game state (:class:`~starcraft_commander.sc2_catalog.CatalogState`) and says which
one it chose:

* one clear command that the state allows            -> ``execute`` (narration names it, multi-level upgrades pick the
  next unresearched level);
* more than one reasonable command / a missing place -> ``ask`` ONCE, naming at most three alternatives;
* the state forbids it (done / busy / no energy ...) -> ``refuse`` with the Korean reason, nothing executes;
* no match, no state, negation, injection/raw control -> ``none``, nothing executes.

Pure (no I/O, no game access).  Lowering goes through ``sc2_catalog.direct_task_payload`` into the existing Direct
operation-layer vector, so ``SC2RuntimeExecutor`` sends the exact AbilityId (:func:`provider_output`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Mapping

from starcraft_commander import sc2_catalog as cat

KIND_EXECUTE: Final[str] = "execute"
KIND_ASK: Final[str] = "ask"
KIND_REFUSE: Final[str] = "refuse"
KIND_NONE: Final[str] = "none"

CATALOG_MODES: Final[tuple[str, ...]] = ("off", "v1", "v2", "v3")
# A hit is decisive when it beats the runner-up by at least this many score points (search_scored scale: 10 points per
# matched character, +5 per cue part, 10 000 for an exact alias).
DECISIVE_MARGIN: Final[int] = 15
MIN_SCORE: Final[int] = 20
MAX_ALTERNATIVES: Final[int] = 3

_NAMED_ONLY_ISSUERS: Final[frozenset[str]] = frozenset({"BUNKER"})
_EXTRA_ISSUER_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "BARRACKS": ("배럭", "병영", "barracks", "rax"),
    "FACTORY": ("팩토리", "군수공장", "factory"),
    "STARPORT": ("스타포트", "우주공항", "starport"),
    "COMMANDCENTER": ("사령부", "커맨드", "command center", "cc"),
    "ORBITALCOMMAND": ("궤도", "오비탈", "orbital"),
    "SUPPLYDEPOT": ("보급고", "서플", "depot"),
    "BUNKER": ("벙커", "bunker"),
}
_LOCATION_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern, re.IGNORECASE), value)
    for pattern, value in (
        (r"(?:적|상대|enemy)\s*(?:의\s*)?(?:본진|메인|main)", "enemy_main"),
        (r"(?:적|상대|enemy)\s*(?:의\s*)?(?:앞마당|natural)", "enemy_natural"),
        (r"(?:적|상대|enemy)\s*(?:의\s*)?(?:세\s*번째|3\s*멀티|세컨|third)", "enemy_third"),
        (r"(?:적|상대|enemy)\s*(?:의\s*)?(?:일꾼|미네랄\s*라인|mineral)", "enemy_mineral_line"),
        (r"(?:우리|내|아군|our|my)?\s*(?:본진|home\b|main\b)", "home"),
        (r"앞마당|natural", "natural"),
        (r"램프|입구|ramp", "ramp"),
        (r"세\s*번째|3\s*멀티|third", "third"),
    )
)
_GENERIC_VERBS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("research", re.compile(r"업그레이드|업글|연구|개발|\b(?:upgrade|research)\b", re.IGNORECASE)),
    ("train", re.compile(r"뽑|생산|양산|\b(?:train|produce)\b", re.IGNORECASE)),
    ("build", re.compile(r"지어|건설|\b(?:build)\b", re.IGNORECASE)),
)
_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"지\s*(?:마|말)|않|못\s*하|하지\s*마|금지|말고|안\s*(?:돼|해|쓰)|그만|취소|"
    r"\b(?:don'?t|do\s+not|never|stop|cancel|no|without)\b",
    re.IGNORECASE,
)
# Reasons for which the honest answer is "the prerequisite is missing, build it first?" (an ask), not a dead end.
_PREREQUISITE_REASONS: Final[frozenset[str]] = frozenset(
    {"missing_prerequisite", "producer_needs_techlab", "no_ready_idle_producer"}
)
# Reasons for which a macro train/build call (MicroMachine would only queue or stall) is stopped and the user is asked
# instead: the structure that makes the command possible does not exist.  Transient reasons (busy, no money, no geyser
# yet) stay with the macro layer, which queues them.
STATE_VETO_REASONS: Final[frozenset[str]] = frozenset(
    {"missing_prerequisite", "producer_needs_techlab", "no_ready_idle_producer", "wrong_issuer", "no_matching_caster"}
)
_NOTES: Final[tuple[tuple[str, re.Pattern[str], str], ...]] = (
    ("morph_siegemode", re.compile(r"사거리|range", re.IGNORECASE),
     "API에 탱크 사거리 업그레이드는 없어 시즈 모드를 선택했습니다"),
)
# "꺼 / 끄고 / off" after a switchable ability means the OFF command of the same unit (decloak, unsiege, release fire):
# executing the ON command would do the opposite of what was said.
_SWITCH_OFF_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:꺼|끄(?:고|자|라|기|면|세요)?|끔|끈|오프)(?![가-힣])|\b(?:off|turn\s+off|switch\s+off)\b", re.IGNORECASE
)
_OFF_COMMAND: Final[dict[str, str]] = {
    "behavior_cloakon": "behavior_cloakoff",
    "behavior_holdfireon": "behavior_holdfireoff",
    "morph_siegemode": "morph_unsiege",
}
_VERB_TAIL: Final[re.Pattern[str]] = re.compile(r"\s*(?:지어|뽑아|달아|생산|올려|연구|업그레이드|풀어|켜|해제)$")
_GENERIC_NOUN: Final[dict[str, str]] = {"research": "연구", "train": "생산", "build": "건설"}
_PREFIX: Final[dict[str, str]] = {
    "research": "연구를 시작합니다",
    "train": "생산합니다",
    "build": "건설합니다",
    "morph": "변환합니다",
    "effect": "사용합니다",
    "behavior": "사용합니다",
    "other": "실행합니다",
}


@dataclass(frozen=True)
class Resolution:
    kind: str
    command_id: str = ""
    alternatives: tuple[str, ...] = ()
    reason_ko: str = ""
    narration_ko: str = ""
    reason: str = ""
    level: int | None = None
    upgrade: str = ""
    issuer: str = ""
    location: str = ""
    queue: bool = False
    ability: str = ""
    note_ko: str = ""
    source: str = ""
    extra: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind, "command_id": self.command_id, "alternatives": list(self.alternatives),
            "reason": self.reason, "reason_ko": self.reason_ko, "narration_ko": self.narration_ko,
            "level": self.level, "upgrade": self.upgrade, "issuer": self.issuer, "location": self.location,
            "ability": self.ability, "source": self.source,
        }


# --------------------------------------------------------------------------- names


def _family(entry: cat.CommandEntry) -> str:
    """Same-family key: a multi-level upgrade / add-on / caster variant of one thing is one family."""

    return entry.id

def command_title(entry: cat.CommandEntry) -> str:
    """Short Korean name of a command: its first alias without the trailing verb ("보병 공격력 업그레이드" -> 보병 공격력)."""

    title = _VERB_TAIL.sub("", entry.aliases_ko[0]) if entry.aliases_ko else entry.id
    return title or entry.aliases_ko[0]


def type_name_ko(type_name: str) -> str:
    return cat.TYPE_NAME_KO.get(type_name, type_name)


def _squash(text: str) -> str:
    return cat.normalize_alias(text)


# --------------------------------------------------------------------------- phrase helpers


def phrase_location(phrase: str) -> str:
    for pattern, value in _LOCATION_PATTERNS:
        if pattern.search(phrase):
            return value
    return ""


def phrase_is_negated(phrase: str) -> bool:
    return bool(_NEGATION_RE.search(phrase or ""))


def _unsafe_text(*texts: str) -> bool:
    from starcraft_commander import realtime_intent as RI

    return any(RI.transcript_is_raw_control_or_injection(t) for t in texts if t)


def _named_issuers(entry: cat.CommandEntry, phrase: str) -> tuple[str, ...]:
    squashed = _squash(phrase)
    named = []
    for issuer in entry.valid_issuers:
        words = (type_name_ko(issuer), *_EXTRA_ISSUER_WORDS.get(issuer, ()))
        if any(_squash(w) and _squash(w) in squashed for w in words):
            named.append(issuer)
    # "궤도 사령부" names the orbital, not the plain command center that also appears in the word
    if "ORBITALCOMMAND" in named and "COMMANDCENTER" in named and entry.id != "lift":
        named.remove("COMMANDCENTER")
    return tuple(named)


def _group_key(entry: cat.CommandEntry, issuer: str) -> str:
    if entry.lowering == "execute_ability":
        return entry.tokens.get(issuer, issuer)
    return entry.abilities.get(issuer, issuer)


# --------------------------------------------------------------------------- narration


def _level_of(entry: cat.CommandEntry, upgrade: str) -> int | None:
    if upgrade and len(entry.upgrades) > 1 and upgrade in entry.upgrades:
        return entry.upgrades.index(upgrade) + 1
    return None


def _narrate_execute(entry: cat.CommandEntry, ev: cat.CommandEvaluation, level: int | None, location: str, note: str) -> str:
    title = command_title(entry)
    if level:
        title = f"{title} {level}단계"
    where = f" · {location}" if location and entry.target_mode in {"explicit_point"} else ""
    issuer = f" ({type_name_ko(ev.issuer_type)})" if ev.issuer_type and entry.category in {"research", "train", "morph"} else ""
    text = f"{_PREFIX[entry.category]}: {title}{issuer}{where}"
    return f"{text} — {note}" if note else text


def _needed_structures_ko(entry: cat.CommandEntry, reason: str) -> str:
    head, _, tail = reason.partition(":")
    tail = tail.strip()
    if tail:
        return type_name_ko(tail)
    names = [type_name_ko(i) for i in entry.valid_issuers if i not in _NAMED_ONLY_ISSUERS]
    return " / ".join(dict.fromkeys(names[:3]))


def _refusal_ko(entry: cat.CommandEntry, ev: cat.CommandEvaluation) -> str:
    base = ev.reason_ko or cat.refusal_korean(ev.reason) or "지금은 실행할 수 없습니다"
    head = ev.reason.partition(":")[0].strip()
    if head in {"no_ready_idle_producer", "no_ready_idle_researcher", "producer_needs_techlab", "no_matching_caster"}:
        needed = _needed_structures_ko(entry, "")
        if needed:
            base = f"{base} (필요: {needed})"
    return f"{command_title(entry)}: {base}"


# --------------------------------------------------------------------------- core resolution


def _ask_which(entries: list[cat.CommandEntry], lead: str = "") -> tuple[str, tuple[str, ...]]:
    top = entries[:MAX_ALTERNATIVES]
    names = " / ".join(command_title(e) for e in top)
    return f"{lead}{names} 중 어느 것으로 할까요?", tuple(e.id for e in top)


def _category_options(state: cat.CatalogState, category: str) -> list[cat.CommandEntry]:
    """Issuable commands of a category; research: families with progress first, then multi-level, then the rest."""

    options = [cat.get_command(r.command_id) for r in cat.issuable_now(state, categories=(category,))]
    if category != "research":
        return sorted(options, key=lambda entry: entry.id)

    def rank(entry: cat.CommandEntry) -> tuple[int, str]:
        started = any(name in state.upgrades_done for name in entry.upgrades)
        return (0 if started else 1 if len(entry.upgrades) > 1 else 2, entry.id)

    return sorted(options, key=rank)


def decide_entry(
    entry: cat.CommandEntry,
    phrase: str,
    state: cat.CatalogState,
    *,
    location: str = "",
    queue: bool = False,
    source: str = "",
) -> Resolution:
    """Evaluate one chosen command against the state: execute / ask (which building, where, prerequisite) / refuse."""

    location = location or phrase_location(phrase)
    named = _named_issuers(entry, phrase)
    pool = [i for i in entry.valid_issuers if (not named or i in named) and (i not in _NAMED_ONLY_ISSUERS or named)]
    pool = pool or list(entry.valid_issuers)
    groups: dict[str, cat.CommandEvaluation] = {}
    failures: list[cat.CommandEvaluation] = []
    for issuer in pool:
        ev = cat.evaluate(entry.id, state, issuer=issuer)
        if ev.ok:
            groups.setdefault(_group_key(entry, ev.issuer_type or issuer), ev)
        else:
            failures.append(ev)
    if not groups:
        ev = failures[0] if failures else cat.evaluate(entry.id, state)
        head = ev.reason.partition(":")[0].strip()
        text = _refusal_ko(entry, ev)
        if head in _PREREQUISITE_REASONS:
            needed = _needed_structures_ko(entry, ev.reason)
            what = "기술실을 달" if head == "producer_needs_techlab" else f"{needed}을(를) 지을" if needed else ""
            question = f"{text} — 먼저 {what}까요?" if what else text
            return Resolution(KIND_ASK, entry.id, reason=ev.reason, reason_ko=text, narration_ko=question, source=source)
        return Resolution(KIND_REFUSE, entry.id, reason=ev.reason, reason_ko=text, narration_ko=text, source=source)
    if len(groups) > 1:
        kinds = " / ".join(type_name_ko(ev.issuer_type) for ev in groups.values())
        question = f"{command_title(entry)}: 어느 쪽에 할까요? {kinds}"
        question = question.replace("? ", "? ", 1)
        return Resolution(
            KIND_ASK, entry.id, alternatives=(entry.id,), reason="ambiguous_issuer", reason_ko=question, narration_ko=question,
            source=source, extra={"issuers": [ev.issuer_type for ev in groups.values()]},
        )
    ev = next(iter(groups.values()))
    if entry.target_mode == "explicit_point" and not location:
        question = f"{command_title(entry)}: 어디에 쓸까요? (예: 적 본진 / 앞마당 / 우리 본진)"
        return Resolution(
            KIND_ASK, entry.id, reason="explicit_point_required", reason_ko=cat.refusal_korean("explicit_point_required"),
            narration_ko=question, issuer=ev.issuer_type, source=source,
        )
    level = _level_of(entry, ev.upgrade)
    note = next((text for entry_id, pattern, text in _NOTES if entry_id == entry.id and pattern.search(phrase)), "")
    return Resolution(
        KIND_EXECUTE, entry.id, narration_ko=_narrate_execute(entry, ev, level, location, note), level=level,
        upgrade=ev.upgrade, issuer=ev.issuer_type, location=location, queue=queue, ability=ev.ability, note_ko=note, source=source,
    )


def resolve_intent(
    phrase: str,
    state: cat.CatalogState,
    *,
    category_hint: str | None = None,
    location: str = "",
    queue: bool = False,
) -> Resolution:
    """Plain phrase -> Resolution against ``state`` (V2).  Never raises."""

    phrase = " ".join(str(phrase or "").split())[:300]
    if not phrase or _unsafe_text(phrase):
        return Resolution(KIND_NONE, reason="empty_or_unsafe", narration_ko="알아듣지 못했습니다. 다시 말씀해 주세요.")
    if phrase_is_negated(phrase):
        return Resolution(KIND_NONE, reason="negated", narration_ko="부정 표현이라 실행하지 않았습니다.")
    scored = [(e, s) for e, s in cat.search_scored(phrase, limit=8) if s >= MIN_SCORE]
    if _SWITCH_OFF_RE.search(phrase):
        scored = [(cat.get_command(_OFF_COMMAND.get(e.id, e.id)), s) for e, s in scored]
        scored = list({e.id: (e, s) for e, s in scored}.values())
    if category_hint:
        scored = [(e, s) for e, s in scored if e.category == category_hint]
    if not scored:
        generic = [name for name, pattern in _GENERIC_VERBS if pattern.search(phrase) and category_hint in (None, name)]
        if len(generic) == 1:
            options = _category_options(state, generic[0])
            if not options:
                text = f"지금 할 수 있는 {_GENERIC_NOUN[generic[0]]}이(가) 없습니다"
                return Resolution(KIND_REFUSE, reason=f"nothing_to_{generic[0]}", reason_ko=text, narration_ko=text)
            question, alts = _ask_which(options)
            return Resolution(KIND_ASK, alts[0], alternatives=alts, reason=f"generic_{generic[0]}", reason_ko=question, narration_ko=question)
        return Resolution(KIND_NONE, reason="no_match", narration_ko="어떤 명령인지 알 수 없습니다. 다시 말씀해 주세요.")
    top_score = scored[0][1]
    tied = [e for e, s in scored if top_score - s < DECISIVE_MARGIN]
    if len(tied) == 1:
        return decide_entry(tied[0], phrase, state, location=location, queue=queue, source="intent")
    decided = [decide_entry(e, phrase, state, location=location, queue=queue, source="intent") for e in tied]
    viable = [d for d in decided if d.kind == KIND_EXECUTE]
    if len(viable) == 1 and len(viable) < len(decided):
        # Another equally plausible reading is blocked by the game state.  Never silently run the lesser one: say what
        # is blocked and ask whether the runnable one was meant.
        only = viable[0]
        blocked = next(d for d in decided if d is not only)
        text = (
            f"{blocked.reason_ko or command_title(cat.get_command(blocked.command_id)) + '은(는) 지금 할 수 없습니다'}"
            f" — {command_title(cat.get_command(only.command_id))}(으)로 할까요?"
        )
        return Resolution(
            KIND_ASK, only.command_id, alternatives=(only.command_id,), reason="blocked_reading", reason_ko=text,
            narration_ko=text, source="intent",
        )
    if len(viable) == 1:
        only = viable[0]
        return only
    if len(viable) > 1:
        question, alts = _ask_which([cat.get_command(d.command_id) for d in viable])
        return Resolution(KIND_ASK, alts[0], alternatives=alts, reason="ambiguous", reason_ko=question, narration_ko=question, source="intent")
    first = decided[0]
    if all(d.kind == KIND_REFUSE for d in decided):
        return Resolution(KIND_REFUSE, first.command_id, reason=first.reason, reason_ko=first.reason_ko, narration_ko=first.narration_ko)
    question, alts = _ask_which([cat.get_command(d.command_id) for d in decided])
    return Resolution(KIND_ASK, alts[0], alternatives=alts, reason="ambiguous", reason_ko=question, narration_ko=question, source="intent")


def veto_macro_call(transcript: str, state: cat.CatalogState | None) -> Resolution | None:
    """State veto for a macro train/build tool call: ``Resolution`` (ask) when the transcript names one train/build command
    whose required structure is missing in ``state``; ``None`` otherwise (the macro layer proceeds)."""

    if state is None or not str(transcript or "").strip() or _unsafe_text(transcript) or phrase_is_negated(transcript):
        return None
    scored = [(e, sc) for e, sc in cat.search_scored(transcript, limit=4) if sc >= MIN_SCORE and e.category in {"train", "build"}]
    if not scored or (len(scored) > 1 and scored[0][1] - scored[1][1] < DECISIVE_MARGIN):
        return None
    result = decide_entry(scored[0][0], transcript, state, source="state_veto")
    if result.kind == KIND_ASK and result.reason.partition(":")[0].strip() in STATE_VETO_REASONS:
        return result
    return None


def resolve_catalog_call(
    catalog: Mapping[str, object] | None,
    transcript: str,
    state: cat.CatalogState | None,
    *,
    mode: str = "v2",
) -> Resolution:
    """The model's ``catalog`` object (+ the heard transcript) -> Resolution.

    ``v1``: ``catalog.command_id`` (enum) names the command; the server still evaluates it against the state, pins the
    issuer/place from the transcript and never executes under negation/injection.  ``v2``: ``catalog.intent`` is the
    plain phrase and the resolver chooses.  Without a state snapshot nothing executes.
    """

    if mode not in {"v1", "v2", "v3"}:
        raise ValueError(f"unknown catalog mode {mode!r}")
    if not isinstance(catalog, Mapping):
        return Resolution(KIND_NONE, reason="no_catalog_call")
    transcript = " ".join(str(transcript or "").split())[:2000]
    intent = " ".join(str(catalog.get("intent", "") or "").split())[:300]
    command_id = str(catalog.get("command_id", "") or "").strip().lower()
    location = str(catalog.get("location", "") or "").strip()
    queue = catalog.get("queue") is True
    if _unsafe_text(transcript, intent):
        return Resolution(KIND_NONE, reason="raw_control_or_injection", narration_ko="좌표·코드·지시 무시 요청은 실행하지 않습니다.")
    if phrase_is_negated(transcript) or phrase_is_negated(intent):
        return Resolution(KIND_NONE, reason="negated", narration_ko="부정 표현이라 실행하지 않았습니다.")
    if state is None:
        return Resolution(
            KIND_NONE, command_id=command_id if mode == "v1" else "", reason="no_state",
            narration_ko="지금 게임 상태를 읽을 수 없어 실행하지 않았습니다.",
        )
    if mode in {"v1", "v3"}:
        try:
            entry = cat.get_command(command_id)
        except KeyError:
            return Resolution(KIND_NONE, reason="unknown_command", narration_ko="알 수 없는 명령입니다.")
        if mode == "v3" and transcript:
            # V3: the model named the command; the server checks that the HEARD words agree before anything runs.
            heard = resolve_intent(transcript, state, location=location, queue=queue)
            named = _family(entry)
            agrees = heard.kind in {KIND_EXECUTE, KIND_ASK, KIND_REFUSE} and bool(heard.command_id) and (
                _family(cat.get_command(heard.command_id)) == named
                or any(_family(cat.get_command(a)) == named for a in heard.alternatives)
            )
            if not agrees:
                question = f"'{transcript[:40]}'은(는) {command_title(entry)}이(가) 맞나요? 어떤 명령인지 다시 말씀해 주세요."
                return Resolution(
                    KIND_ASK, entry.id, reason="phrase_disagrees_with_command", reason_ko=question, narration_ko=question,
                    source="command_id_checked",
                )
        return decide_entry(entry, transcript or intent, state, location=location, queue=queue, source="command_id")
    phrase = intent or transcript
    result = resolve_intent(phrase, state, location=location or phrase_location(transcript), queue=queue)
    return result


# --------------------------------------------------------------------------- V1 prompt listing


def catalog_listing() -> str:
    """Compact ``category: id=말; ...`` list of every catalog command for the V1 prompt (derived, never hand-kept)."""

    lines = []
    for category in cat.CATEGORIES:
        items = [f"{e.id}={e.aliases_ko[0]}" for e in cat.commands_by_category(category)]
        lines.append(f"{category}: " + "; ".join(items))
    return "\n".join(lines)


# --------------------------------------------------------------------------- lowering


def provider_output(resolution: Resolution, *, goal: str = "", ttl_seconds: int = 180) -> dict[str, object]:
    """Direct-path provider output for an ``execute`` resolution (catalog ``direct_task_payload`` + lifecycle wrappers)."""

    if resolution.kind != KIND_EXECUTE:
        raise ValueError("only an execute resolution lowers to a command")
    entry = cat.get_command(resolution.command_id)
    location = resolution.location
    if entry.lowering == "build_structure" and entry.target_mode != "geyser" and location not in {"", "home", "natural", "ramp", "third"}:
        location = ""
    if entry.lowering in {"train_unit", "research_upgrade"}:
        location = ""
    payload = cat.direct_task_payload(
        entry.id, issuer=resolution.issuer or None, location=location, level=resolution.level,
    )
    ability_like = entry.lowering == "execute_ability"
    payload.update(
        {
            "goal": goal or resolution.narration_ko,
            "source": "ui",
            "confidence": 0.9,
            "rationale": resolution.narration_ko,
            "override_level": "directive",
            "ttl_seconds": ttl_seconds,
            "lifetime": {
                "mode": "until_completed",
                "completion_conditions": ["ability_cast" if ability_like else "order_issued"],
                "completion_state": "active",
                "reason": "Catalog voice command completes on runtime evidence.",
            },
            "tags": ["realtime_voice", f"command_layer:{payload['command_layer']}", f"catalog:{entry.id}"],
        }
    )
    if resolution.queue:
        payload["tactical_task"] = {**payload["tactical_task"], "queue": True}
    return payload
