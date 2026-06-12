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

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from toycraft_commander.interpreter import (
    DEFAULT_COMMAND_INTERPRETER,
    CommandInterpreterInterface,
)

from starcraft_commander.contracts import SC2ExecutionPlan, SC2PlanExecutionResult
from starcraft_commander.feasibility import (
    DEFAULT_SC2_FEASIBILITY_VALIDATOR,
    SC2FeasibilityResult,
    SC2FeasibilityValidatorInterface,
)
from starcraft_commander.narrator import DEFAULT_SC2_NARRATOR, SC2NarratorInterface
from starcraft_commander.sc2_executor import (
    DEFAULT_SC2_ACTION_PLANNER,
    SC2ActionPlannerInterface,
    SC2ExecutorBoundaryInterface,
    SC2RuntimeExecutor,
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
    """

    interpreter: CommandInterpreterInterface = DEFAULT_COMMAND_INTERPRETER
    validator: SC2FeasibilityValidatorInterface = DEFAULT_SC2_FEASIBILITY_VALIDATOR
    planner: SC2ActionPlannerInterface = DEFAULT_SC2_ACTION_PLANNER
    executor: SC2ExecutorBoundaryInterface = field(default_factory=SC2RuntimeExecutor)
    state_resolver: SC2StateResolverInterface = DEFAULT_SC2_STATE_RESOLVER
    narrator: SC2NarratorInterface = DEFAULT_SC2_NARRATOR

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
                        outcomes.append(_clarification_outcome(part_result))
                return tuple(outcomes)

        if full_resolved:
            return (await self._process_interpretation(interpretation),)
        return (_clarification_outcome(interpretation),)

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
            return SC2CommandOutcome(
                command_text=command_text,
                status="blocked",
                narration=rejection.response_text,
                intent_dsl=intent_dsl,
                feasibility=feasibility,
            )

        try:
            plan = self.planner.build_plan(payload)
        except ValueError as error:
            # The strict planner message already lists every supported target;
            # the narrator appends the standard Korean actionable alternative.
            rejection = self.narrator.narrate_rejection(str(error))
            return SC2CommandOutcome(
                command_text=command_text,
                status="blocked",
                narration=rejection.response_text,
                intent_dsl=intent_dsl,
                feasibility=feasibility,
            )

        execution_result = await self.executor.execute(plan)
        narration = self.narrator.narrate_plan_result(execution_result)
        return SC2CommandOutcome(
            command_text=command_text,
            status=narration.status,
            narration=narration.response_text,
            intent_dsl=intent_dsl,
            plan=plan,
            execution_result=execution_result,
            feasibility=feasibility,
        )

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
