"""Live StarCraft II command pipeline: text -> intent -> plan -> narration.

This is the handoff Step 5 integration seam. One :class:`SC2CommandSession`
composes the Korean command interpreter (reused from the ToyCraft offline
harness, the one legitimate toycraft import in this package), the live
feasibility validator, the semantic SC2 action planner, the lifecycle-aware
runtime executor, the BotAI state resolver, and the Korean narrator into a
single async ``process_text`` call that returns one structured
:class:`SC2CommandOutcome` per executed (or honestly refused) command part.

The module is intentionally importable without StarCraft II, python-sc2,
faster-whisper, or sounddevice installed: every composed component is either
stdlib-only or lazy about its optional runtime, and bot objects are always
duck-typed. Conservative house rules apply end to end: unknown game state
rejects mutating commands, planner refusals surface their full supported
target listing, and skipped runtime work is never narrated as success.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from toycraft_commander.interpreter import (
    DEFAULT_COMMAND_INTERPRETER,
    CommandInterpreterInterface,
)

from starcraft_commander.contracts import (
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.feasibility import (
    DEFAULT_SC2_FEASIBILITY_VALIDATOR,
    SC2FeasibilityResult,
    SC2FeasibilityValidatorInterface,
)
from starcraft_commander.narrator import (
    DEFAULT_SC2_NARRATOR,
    SC2KoreanNarrator,
    SC2NarratorInterface,
)
from starcraft_commander.sc2_executor import (
    DEFAULT_SC2_ACTION_PLANNER,
    SC2ActionPlannerInterface,
    SC2ExecutorBoundaryInterface,
    SC2RuntimeExecutor,
)
from starcraft_commander.standing_orders import (
    CONSTRAINT_TO_STANDING_ORDER,
    STANDING_ORDER_KOREAN_LABELS,
)
from starcraft_commander.state_resolver import (
    DEFAULT_SC2_STATE_RESOLVER,
    SC2CommanderState,
    SC2StateResolverInterface,
)


SC2CommandOutcomeStatus = Literal[
    "executed",
    "partially_executed",
    "blocked",
    "read_only",
    "clarification",
]
"""Stable commander-facing outcome status values for one command part."""

SC2_COMMAND_OUTCOME_STATUSES: Final[frozenset[str]] = frozenset(
    {"executed", "partially_executed", "blocked", "read_only", "clarification"}
)
"""Every supported ``SC2CommandOutcome.status`` value.

The first four mirror the narrator statuses; ``clarification`` marks command
text the interpreter could not resolve into a supported Intent DSL payload.
"""

_SEQUENTIAL_VERB_STEM_SYLLABLES: Final[str] = "짓뽑내막찍리치키우들하"
"""Final verb-stem syllables allowed before a sequential ``고 `` split.

Covers the command vocabulary verbs (짓고, 뽑고, 보내고, 막고, 찍고, 올리고,
고치고, 지키고, 세우고, 만들고, 수리하고). A curated allowlist instead of any
Hangul syllable keeps nouns ending in ``고`` (보급고, 창고) from being split
apart mid-word.
"""

_COMPOUND_COMMAND_SPLIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\s+)그리고\s+"  # Explicit connective, including utterance start.
    r"|\s+하고\s+"  # Standalone connective word: "A 하고 B".
    r"|(?<=[가-힣])면서\s+"  # Simultaneous connective ending: "뽑으면서 B".
    # Sequential verb ending: "보내고 B" — only after curated verb stems so
    # nouns ending in 고 (보급고, 창고) are never split apart.
    rf"|(?<=[{_SEQUENTIAL_VERB_STEM_SYLLABLES}])고\s+"
)
"""Heuristic Korean compound-command boundaries, standalone connectives first."""

_EXPLICIT_CONNECTIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\s)그리고\s|\s하고\s"
)
"""Detector for explicit standalone connectives signaling a compound order."""

SC2_STANDING_ORDER_REGISTRATION_PREFIX: Final[str] = "상비 명령 등록"
"""Korean prefix of the narration suffix announcing new standing orders."""

_SUMMARIZE_STATE_INTENT_NAME: Final[str] = "SUMMARIZE_STATE"
"""Intent whose read-only outcomes get standing-order/memory enrichment."""

_ANSWER_QUESTION_INTENT_NAME: Final[str] = "ANSWER_QUESTION"
"""Read-only pseudo intent for help/capability questions, never game actions."""

_EXECUTED_OUTCOME_STATUSES: Final[frozenset[str]] = frozenset(
    {"executed", "partially_executed", "read_only"}
)
"""Outcome statuses that count as a successful execution for registration."""

_LOCATION_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "위치",
    "장소",
    "좌표",
    "건물에",
    "건물 위치",
    "지정",
    "place",
    "location",
    "position",
)
_VOICE_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "음성",
    "마이크",
    "말로",
    "voice",
    "microphone",
)
_CAPABILITY_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "뭐 할 수",
    "무엇을 할 수",
    "어떤 명령",
    "명령 알려",
    "사용법",
    "도움말",
    "help",
    "commands",
)
_LLM_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "llm",
    "gpt",
    "openai",
    "api 키",
    "api키",
    "키",
    "대화",
    "연결",
    "모델",
)
_QUESTION_MARKERS: Final[tuple[str, ...]] = (
    "?",
    "？",
    "가능",
    "되나",
    "돼",
    "되나요",
    "되냐",
    "할 수",
    "어떻게",
    "알려",
    "지원",
)

_LOCATION_QUESTION_ANSWER: Final[str] = (
    "네, 건물 위치는 현재 의미 기반 위치로 지정할 수 있습니다. "
    "예: `본진에 배럭 지어`, `본진 입구에 보급고 지어`, "
    "`본진 가스에 정제소 지어`, `앞마당에 커맨드 지어`, "
    "`앞마당 입구에 벙커 지어`. 지금은 마우스 좌표를 찍는 방식이 아니라 "
    "SC2 API가 이해할 수 있는 semantic target으로 변환해 건설합니다."
)
_VOICE_QUESTION_ANSWER: Final[str] = (
    "네, 음성 입력을 지원합니다. `[voice]` 의존성 설치 후 "
    "`python3 -m starcraft_commander.demo_sc2 --dry-run --voice` 또는 "
    "`python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy --voice`"
    "로 실행합니다. macOS에서는 터미널 앱에 마이크 권한을 허용해야 합니다."
)
_CAPABILITY_QUESTION_ANSWER: Final[str] = (
    "현재 지원하는 MVP 명령은 상태 확인, 일꾼 생산, 자원 채취, 구조물 건설, "
    "병력 생산, 정찰, 방어, 수리, 확장, 견제입니다. 예: `상태확인`, "
    "`SCV 여러개 뽑아`, `자원채취`, `보급고 지어`, `정찰보내`, "
    "`마린 생산해`, `본진 입구 막아`."
)
_LLM_QUESTION_ANSWER: Final[str] = (
    "네, 이 웹 GUI는 LLM 필수 모드입니다. 다만 모든 입력을 자유 대화로 "
    "보내는 구조가 아니라, 간단한 명령은 로컬 규칙 해석기가 즉시 처리하고 "
    "규칙으로 애매한 명령만 LLM intent 해석기로 보냅니다. API 키가 잘못되면 "
    "LLM 설정 영역에 실패 이유가 표시됩니다. 키가 설정됐는데 게임이 움직이지 "
    "않으면, 현재 탭이 dry-run 탭이 아닌 실제 Live GUI URL인지 확인하세요."
)


def split_compound_command(text: str) -> tuple[str, ...]:
    """Split one Korean utterance into candidate sub-commands heuristically.

    Splits on the explicit connectives ``그리고`` (also at utterance start)
    and standalone ``하고``, the simultaneous ending ``면서 ``, and sequential
    verb endings limited to a curated verb-stem allowlist (for example ``마린
    6기 입구로 보내고 SCV 계속 찍어``); nouns ending in ``고`` such as 보급고
    are never split apart. Parts are stripped and empties dropped. Simple
    commands without a connective come back as a single part.
    """

    if not isinstance(text, str):
        return ()
    parts = (part.strip() for part in _COMPOUND_COMMAND_SPLIT_PATTERN.split(text))
    return tuple(part for part in parts if part)


def _has_explicit_connective(text: str) -> bool:
    """Return whether the utterance contains a standalone 그리고/하고."""

    return _EXPLICIT_CONNECTIVE_PATTERN.search(text) is not None


def _normalize_question_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    normalized = " ".join(text.casefold().split())
    if normalized.startswith("그리고 "):
        normalized = normalized[len("그리고 ") :]
    return normalized.strip()


def _contains_question_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _question_answer_for(text: str) -> tuple[str, str] | None:
    """Return a read-only answer for known capability questions."""

    normalized = _normalize_question_text(text)
    if not normalized:
        return None
    if not _contains_question_pattern(normalized, _QUESTION_MARKERS):
        return None
    if _contains_question_pattern(normalized, _LOCATION_QUESTION_PATTERNS):
        return "building_location_help", _LOCATION_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _VOICE_QUESTION_PATTERNS):
        return "voice_help", _VOICE_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _LLM_QUESTION_PATTERNS):
        return "llm_help", _LLM_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _CAPABILITY_QUESTION_PATTERNS):
        return "capability_help", _CAPABILITY_QUESTION_ANSWER
    return None


def _question_outcome(command_text: str, topic: str, answer: str) -> SC2CommandOutcome:
    """Build a read-only outcome for commander questions without touching SC2."""

    action = SC2CommandAction(
        action_type=SC2ActionType.OBSERVE,
        subject="help",
        target=topic,
        count=1,
        metadata={"question": command_text},
    )
    plan = SC2ExecutionPlan(
        intent_name=_ANSWER_QUESTION_INTENT_NAME,
        priority="normal",
        ordered_actions=(action,),
        constraints=("answer commander question without issuing game actions",),
        requires_live_sc2=False,
        notes=("Question answers are read-only and never issue SC2 API commands.",),
        audit={"topic": topic},
    )
    execution_result = SC2PlanExecutionResult(
        plan=plan,
        attempted_actions=(action,),
        applied_actions=(action,),
        audit={"topic": topic},
    )
    return SC2CommandOutcome(
        command_text=command_text,
        status="read_only",
        narration=answer,
        intent_dsl={
            "intent": _ANSWER_QUESTION_INTENT_NAME,
            "topic": topic,
            "read_only": True,
        },
        plan=plan,
        execution_result=execution_result,
    )


@dataclass(frozen=True)
class SC2CommandOutcome:
    """Structured outcome for one commander command (or compound part).

    ``narration`` is the commander-facing Korean response. ``intent_dsl``,
    ``plan``, ``execution_result``, and ``feasibility`` carry the structured
    pipeline artifacts that were actually produced; stages that never ran stay
    ``None`` so a blocked or clarification outcome can never masquerade as an
    executed one.
    """

    command_text: str
    status: SC2CommandOutcomeStatus
    narration: str
    intent_dsl: Mapping[str, object] | None = None
    plan: SC2ExecutionPlan | None = None
    execution_result: SC2PlanExecutionResult | None = None
    feasibility: SC2FeasibilityResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_text", str(self.command_text))
        if self.status not in SC2_COMMAND_OUTCOME_STATUSES:
            supported = ", ".join(sorted(SC2_COMMAND_OUTCOME_STATUSES))
            raise ValueError(
                f"SC2 command outcome status must be one of: {supported}. "
                f"Unknown status: {self.status!r}."
            )
        if not str(self.narration).strip():
            raise ValueError("SC2 command outcome narration must be non-empty.")
        object.__setattr__(self, "narration", str(self.narration))
        if self.intent_dsl is not None:
            if not isinstance(self.intent_dsl, Mapping):
                raise TypeError("SC2 command outcome intent_dsl must be a mapping or None.")
            object.__setattr__(self, "intent_dsl", dict(self.intent_dsl))
        if self.plan is not None and not isinstance(self.plan, SC2ExecutionPlan):
            raise TypeError("SC2 command outcome plan must be an SC2ExecutionPlan or None.")
        if self.execution_result is not None and not isinstance(
            self.execution_result, SC2PlanExecutionResult
        ):
            raise TypeError(
                "SC2 command outcome execution_result must be an "
                "SC2PlanExecutionResult or None."
            )
        if self.feasibility is not None and not isinstance(
            self.feasibility, SC2FeasibilityResult
        ):
            raise TypeError(
                "SC2 command outcome feasibility must be an SC2FeasibilityResult or None."
            )
        if self.status == "clarification":
            if (
                self.intent_dsl is not None
                or self.plan is not None
                or self.execution_result is not None
                or self.feasibility is not None
            ):
                raise ValueError(
                    "clarification outcomes cannot carry pipeline artifacts."
                )
        if self.status in ("executed", "partially_executed", "read_only"):
            if self.plan is None or self.execution_result is None:
                raise ValueError(
                    f"{self.status} outcomes require both a plan and an execution result."
                )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready outcome payload."""

        return {
            "command_text": self.command_text,
            "status": self.status,
            "narration": self.narration,
            "intent_dsl": dict(self.intent_dsl) if self.intent_dsl is not None else None,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "execution_result": (
                self.execution_result.to_dict()
                if self.execution_result is not None
                else None
            ),
            "feasibility": (
                self.feasibility.to_dict() if self.feasibility is not None else None
            ),
        }


@dataclass(frozen=True)
class SC2CommandSession:
    """Composable live command pipeline session for one StarCraft II runtime.

    Defaults wire the real components: the Korean ToyCraft interpreter, the
    conservative live feasibility validator, the deterministic SC2 action
    planner, a fresh (unbound) runtime executor, the duck-typed BotAI state
    resolver, and the Korean narrator. Bind a runtime by constructing the
    session with ``executor=SC2RuntimeExecutor(bot=adapter)`` where ``adapter``
    is typically a ``PythonSC2BotAdapter`` wrapping the live BotAI object.

    Two optional, duck-typed integrations:

    - ``event_memory`` (``record(outcome, game_time_seconds=None)``, for
      example :class:`~starcraft_commander.event_memory.CommanderEventMemory`)
      records every produced outcome — including blocked and clarification
      ones — stamped with the resolved state's game time when available.
    - ``standing_orders`` (``register_from_payload(payload)`` +
      ``korean_status()``, for example
      :class:`~starcraft_commander.standing_orders.StandingOrderController`)
      is registered from each successfully executed payload's constraints.
      Newly registered orders are announced with an honest Korean narration
      suffix, and because the controller genuinely enforces the
      continuous-production constraint, a default ``SC2KoreanNarrator`` is
      upgraded to treat those constraints as enforced (full execution)
      instead of disclosing them as dropped. Sessions WITHOUT a controller
      keep today's honest ``지속 생산 미지원`` disclosure.
    """

    interpreter: CommandInterpreterInterface = DEFAULT_COMMAND_INTERPRETER
    validator: SC2FeasibilityValidatorInterface = DEFAULT_SC2_FEASIBILITY_VALIDATOR
    planner: SC2ActionPlannerInterface = DEFAULT_SC2_ACTION_PLANNER
    executor: SC2ExecutorBoundaryInterface = field(default_factory=SC2RuntimeExecutor)
    state_resolver: SC2StateResolverInterface = DEFAULT_SC2_STATE_RESOLVER
    narrator: SC2NarratorInterface = DEFAULT_SC2_NARRATOR
    event_memory: object | None = None
    standing_orders: object | None = None

    def __post_init__(self) -> None:
        seams = (
            ("interpreter", self.interpreter, "interpret"),
            ("validator", self.validator, "validate_payload"),
            ("planner", self.planner, "build_plan"),
            ("executor", self.executor, "execute"),
            ("state_resolver", self.state_resolver, "resolve"),
            ("narrator", self.narrator, "narrate_plan_result"),
            ("narrator", self.narrator, "narrate_rejection"),
        )
        for field_name, component, method_name in seams:
            if not callable(getattr(component, method_name, None)):
                raise TypeError(
                    f"SC2 command session {field_name} must implement {method_name}()."
                )
        if self.event_memory is not None and not callable(
            getattr(self.event_memory, "record", None)
        ):
            raise TypeError("SC2 command session event_memory must implement record().")
        if self.standing_orders is not None:
            for method_name in ("register_from_payload", "korean_status"):
                if not callable(getattr(self.standing_orders, method_name, None)):
                    raise TypeError(
                        "SC2 command session standing_orders must implement "
                        f"{method_name}()."
                    )
            # The controller genuinely enforces the standing-order constraints,
            # so a default Korean narrator must stop disclosing them as
            # dropped. Custom narrator implementations are left untouched.
            if isinstance(self.narrator, SC2KoreanNarrator):
                enforced = self.narrator.enforced_constraints | frozenset(
                    CONSTRAINT_TO_STANDING_ORDER
                )
                if enforced != self.narrator.enforced_constraints:
                    object.__setattr__(
                        self,
                        "narrator",
                        SC2KoreanNarrator(enforced_constraints=enforced),
                    )

    async def process_text(self, command_text: str) -> tuple[SC2CommandOutcome, ...]:
        """Process one commander utterance into one outcome per command part.

        Compound utterances are honored part by part so no command part is
        ever silently dropped inside a single-outcome success. Per-part
        processing is preferred when the splitter recovers at least two
        resolvable parts, or when an explicit connective (그리고/하고) signals
        a compound order, or when the whole text fails to interpret but at
        least one part resolves; unsupported parts become honest
        clarification outcomes. Otherwise a resolved whole text executes as
        one command, and unresolvable text returns the interpreter's own
        Korean clarification unchanged.
        """

        question_answer = _question_answer_for(command_text)
        if question_answer is not None:
            topic, answer = question_answer
            return (
                self._finalize_outcome(
                    _question_outcome(command_text, topic, answer),
                    None,
                ),
            )

        interpretation = self.interpreter.interpret(command_text)
        full_payload = getattr(interpretation, "payload", None)
        full_resolved = full_payload is not None

        parts = split_compound_command(command_text)
        if len(parts) >= 2:
            part_interpretations = tuple(
                self.interpreter.interpret(part) for part in parts
            )
            resolved_payloads = tuple(
                payload
                for part_result in part_interpretations
                if (payload := getattr(part_result, "payload", None)) is not None
            )
            resolved_part_count = len(resolved_payloads)
            # When the whole text resolves to exactly one part's payload, the
            # interpreter ignored the other parts: executing the whole text
            # as one command would silently drop them.
            full_collapses_to_one_part = full_resolved and any(
                payload == full_payload for payload in resolved_payloads
            )
            prefer_parts = resolved_part_count >= 2 or (
                resolved_part_count >= 1
                and (
                    _has_explicit_connective(command_text)
                    or not full_resolved
                    or full_collapses_to_one_part
                )
            )
            if prefer_parts:
                outcomes = []
                for part_result in part_interpretations:
                    if getattr(part_result, "payload", None) is not None:
                        outcomes.append(await self._process_interpretation(part_result))
                    else:
                        outcomes.append(self._finalize_clarification(part_result))
                return tuple(outcomes)

        if full_resolved:
            return (await self._process_interpretation(interpretation),)
        return (self._finalize_clarification(interpretation),)

    async def _process_interpretation(
        self,
        interpretation: object,
    ) -> SC2CommandOutcome:
        """Validate, plan, execute, and narrate one resolved Intent DSL payload."""

        payload = getattr(interpretation, "payload")
        command_text = str(getattr(interpretation, "command_text", ""))
        intent_dsl = _payload_document(payload)

        state = self._resolve_state()
        feasibility = self.validator.validate_payload(payload, state)
        if not feasibility.executable:
            rejection = self.narrator.narrate_rejection(feasibility)
            return self._finalize_outcome(
                SC2CommandOutcome(
                    command_text=command_text,
                    status="blocked",
                    narration=rejection.response_text,
                    intent_dsl=intent_dsl,
                    feasibility=feasibility,
                ),
                state,
            )

        try:
            plan = self.planner.build_plan(payload)
        except ValueError as error:
            # The strict planner message already lists every supported target;
            # the narrator appends the standard Korean actionable alternative.
            rejection = self.narrator.narrate_rejection(str(error))
            return self._finalize_outcome(
                SC2CommandOutcome(
                    command_text=command_text,
                    status="blocked",
                    narration=rejection.response_text,
                    intent_dsl=intent_dsl,
                    feasibility=feasibility,
                ),
                state,
            )

        execution_result = await self.executor.execute(plan)
        narration = self.narrator.narrate_plan_result(execution_result)
        narration_text = narration.response_text
        if (
            self.standing_orders is not None
            and narration.status in _EXECUTED_OUTCOME_STATUSES
        ):
            newly_registered = tuple(
                self.standing_orders.register_from_payload(payload)
            )
            if newly_registered:
                narration_text += _standing_order_registration_suffix(
                    newly_registered
                )
        if narration.status == "read_only" and (
            plan.intent_name == _SUMMARIZE_STATE_INTENT_NAME
        ):
            narration_text = self._enriched_state_narration(narration_text)
        return self._finalize_outcome(
            SC2CommandOutcome(
                command_text=command_text,
                status=narration.status,
                narration=narration_text,
                intent_dsl=intent_dsl,
                plan=plan,
                execution_result=execution_result,
                feasibility=feasibility,
            ),
            state,
        )

    def _finalize_clarification(self, interpretation: object) -> SC2CommandOutcome:
        """Build and record one clarification outcome (no resolved state)."""

        return self._finalize_outcome(_clarification_outcome(interpretation), None)

    def _finalize_outcome(
        self,
        outcome: SC2CommandOutcome,
        state: SC2CommanderState | None,
    ) -> SC2CommandOutcome:
        """Record one outcome into the optional event memory and return it.

        The game time stamp comes from the resolved commander state when one
        was available for this command; clarification outcomes (no state was
        ever resolved) are recorded without a game time.
        """

        if self.event_memory is not None:
            self.event_memory.record(
                outcome,
                game_time_seconds=_state_game_time_seconds(state),
            )
        return outcome

    def _enriched_state_narration(self, narration_text: str) -> str:
        """Append standing-order status and recent-command lines, if present.

        ``SUMMARIZE_STATE`` is the commander's situation report: when the
        session carries a standing-order controller and/or an event memory
        with a ``korean_summary`` renderer, the report honestly includes the
        currently active standing orders and the most recent command log.
        """

        sections = [narration_text]
        if self.standing_orders is not None:
            status_line = str(self.standing_orders.korean_status()).strip()
            if status_line:
                sections.append(status_line)
        summary_renderer = (
            getattr(self.event_memory, "korean_summary", None)
            if self.event_memory is not None
            else None
        )
        if callable(summary_renderer):
            summary_text = str(summary_renderer()).strip()
            if summary_text:
                sections.append(summary_text)
        return "\n".join(sections)

    def _resolve_state(self) -> SC2CommanderState | None:
        """Resolve live commander state from the executor's bound runtime.

        Returns ``None`` when no runtime is bound so the validator can reject
        conservatively. When the bound runtime is an adapter that wraps the
        actual game bot (duck-typed via its ``bot`` attribute, like
        ``PythonSC2BotAdapter``), the inner game bot is observed instead of
        the adapter itself.
        """

        runtime = getattr(self.executor, "bot", None)
        if runtime is None:
            return None
        inner_bot = getattr(runtime, "bot", None)
        game_bot = inner_bot if inner_bot is not None else runtime
        return self.state_resolver.resolve(game_bot)


async def process_commander_text(
    session: SC2CommandSession,
    text: str,
) -> tuple[SC2CommandOutcome, ...]:
    """Process one commander utterance through an existing session."""

    return await session.process_text(text)


def _payload_document(payload: object) -> dict[str, object] | None:
    """Render one Intent DSL payload as a JSON-ready mapping, if possible."""

    if payload is None:
        return None
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if isinstance(payload, Mapping):
        return dict(payload)
    return None


def _standing_order_registration_suffix(kinds: tuple[str, ...]) -> str:
    """Render the Korean narration suffix for newly registered standing orders."""

    labels = ", ".join(
        STANDING_ORDER_KOREAN_LABELS.get(kind, kind) for kind in kinds
    )
    return f" {SC2_STANDING_ORDER_REGISTRATION_PREFIX}: {labels}."


def _state_game_time_seconds(state: SC2CommanderState | None) -> float | None:
    """Read a recordable game time from one resolved state, defensively."""

    if state is None:
        return None
    value = getattr(state, "game_time_seconds", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0.0:
        return None
    return seconds


def _clarification_outcome(interpretation: object) -> SC2CommandOutcome:
    """Build one clarification outcome reusing the interpreter's own wording."""

    command_text = str(getattr(interpretation, "command_text", ""))
    prompt = str(getattr(interpretation, "clarification_prompt", "") or "").strip()
    reason = str(getattr(interpretation, "reason", "") or "").strip()
    return SC2CommandOutcome(
        command_text=command_text,
        status="clarification",
        narration=prompt or reason,
    )
