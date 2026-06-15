"""StarCraft II execution adapter for commander Intent DSL payloads.

This module is intentionally importable without StarCraft II or python-sc2
installed. Unit tests can validate command planning with pure Python fakes, while
real runtime code can pass a python-sc2 ``BotAI``-like object to
``SC2RuntimeExecutor.execute_plan``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from starcraft_commander.contracts import (
    SC2_ACTION_TYPES,
    SC2ActionReport,
    SC2ActionType,
    SC2CommandAction,
    SC2CommandPlan,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)


SC2_UNIT_TYPE_IDS: Final[dict[str, str]] = {
    "SCV": "SCV",
    "Marine": "MARINE",
    "Vulture": "HELLION",
}
"""Intent unit names mapped to python-sc2 UnitTypeId attribute names.

SC2 has no Brood War Vulture. The closest Terran harassment stand-in for the SC2
MVP is Hellion, while the Brood War executor can later map Vulture directly.
"""

SC2_STRUCTURE_TYPE_IDS: Final[dict[str, str]] = {
    "Barracks": "BARRACKS",
    "Bunker": "BUNKER",
    "Command Center": "COMMANDCENTER",
    "Factory": "FACTORY",
    "Refinery": "REFINERY",
    "Supply Depot": "SUPPLYDEPOT",
}

SC2_PRODUCER_TYPE_IDS: Final[dict[str, str]] = {
    "SCV": "COMMANDCENTER",
    "Marine": "BARRACKS",
    "Vulture": "FACTORY",
}

SC2_SEMANTIC_TARGET_NAMES: Final[frozenset[str]] = frozenset(
    {
        "self_main",
        "self_ramp",
        "self_choke",
        "self_natural",
        "self_third",
        "self_mineral_line",
        "self_geyser",
        "enemy_main",
        "enemy_ramp",
        "enemy_choke",
        "enemy_front",
        "enemy_natural",
        "enemy_third",
        "enemy_mineral_line",
        "scout_location",
        "last_seen_enemy_area",
    }
)
"""Canonical semantic SC2 target names accepted for location-typed intents."""

SC2_TARGET_ALIASES: Final[dict[str, str]] = {
    # Canonical semantic runtime target identifiers.
    "self_main": "self_main",
    "self_ramp": "self_ramp",
    "self_choke": "self_choke",
    "self_natural": "self_natural",
    "self_third": "self_third",
    "self_mineral_line": "self_mineral_line",
    "self_geyser": "self_geyser",
    "enemy_main": "enemy_main",
    "enemy_ramp": "enemy_ramp",
    "enemy_choke": "enemy_choke",
    "enemy_front": "enemy_front",
    "enemy_natural": "enemy_natural",
    "enemy_third": "enemy_third",
    "enemy_mineral_line": "enemy_mineral_line",
    "scout_location": "scout_location",
    "last_seen_enemy_area": "last_seen_enemy_area",
    # Player-side ToyCraft canonical map locations.
    "main": "self_main",
    "base": "self_main",
    "main base": "self_main",
    "main base fallback": "self_main",
    "our main": "self_main",
    "our base": "self_main",
    "본진": "self_main",
    "우리 본진": "self_main",
    "우리본진": "self_main",
    "아군 본진": "self_main",
    "아군본진": "self_main",
    "내 본진": "self_main",
    "내본진": "self_main",
    "main_ramp": "self_ramp",
    "main ramp": "self_ramp",
    "본진 입구": "self_ramp",
    "본진입구": "self_ramp",
    "our ramp": "self_ramp",
    "our front": "self_ramp",
    "우리 입구": "self_ramp",
    "우리입구": "self_ramp",
    "우리 본진 입구": "self_ramp",
    "우리본진입구": "self_ramp",
    "아군 입구": "self_ramp",
    "아군입구": "self_ramp",
    "내 입구": "self_ramp",
    "내입구": "self_ramp",
    "mineral line": "self_mineral_line",
    "main mineral line": "self_mineral_line",
    "main_mineral_line": "self_mineral_line",
    "our mineral line": "self_mineral_line",
    "본진 미네랄": "self_mineral_line",
    "본진미네랄": "self_mineral_line",
    "본진 미네랄 라인": "self_mineral_line",
    "본진미네랄라인": "self_mineral_line",
    "미네랄 라인": "self_mineral_line",
    "미네랄라인": "self_mineral_line",
    "main_geyser": "self_geyser",
    "main geyser": "self_geyser",
    "geyser": "self_geyser",
    "gas": "self_geyser",
    "main gas": "self_geyser",
    "our gas": "self_geyser",
    "본진 가스": "self_geyser",
    "본진가스": "self_geyser",
    "우리 가스": "self_geyser",
    "우리가스": "self_geyser",
    "natural": "self_natural",
    "앞마당": "self_natural",
    "natural approach": "self_natural",
    "natural choke": "self_choke",
    "choke": "self_choke",
    "our choke": "self_choke",
    "앞마당 입구": "self_choke",
    "앞마당입구": "self_choke",
    "앞마당 초크": "self_choke",
    "앞마당초크": "self_choke",
    "초크": "self_choke",
    "natural expansion": "self_natural",
    "our natural": "self_natural",
    "우리 앞마당": "self_natural",
    "우리앞마당": "self_natural",
    "third": "self_third",
    "third base": "self_third",
    "third command center": "self_third",
    "3rd base": "self_third",
    "삼룡이": "self_third",
    "3멀티": "self_third",
    "세번째 멀티": "self_third",
    "셋째 멀티": "self_third",
    "front bunker": "self_ramp",
    # Enemy-side ToyCraft canonical map locations.
    "enemy main": "enemy_main",
    "enemy base": "enemy_main",
    "enemy_base": "enemy_main",
    "적 본진": "enemy_main",
    "적본진": "enemy_main",
    "적 기지": "enemy_main",
    "적기지": "enemy_main",
    "상대 본진": "enemy_main",
    "상대본진": "enemy_main",
    "상대 기지": "enemy_main",
    "상대기지": "enemy_main",
    "enemy ramp": "enemy_ramp",
    "적 램프": "enemy_ramp",
    "적램프": "enemy_ramp",
    "상대 램프": "enemy_ramp",
    "상대램프": "enemy_ramp",
    "enemy choke": "enemy_choke",
    "enemy natural choke": "enemy_choke",
    "적 초크": "enemy_choke",
    "적초크": "enemy_choke",
    "적 앞마당 입구": "enemy_choke",
    "적앞마당입구": "enemy_choke",
    "상대 초크": "enemy_choke",
    "상대초크": "enemy_choke",
    "enemy front": "enemy_front",
    "enemy_front": "enemy_front",
    "적 입구": "enemy_front",
    "적입구": "enemy_front",
    "상대 입구": "enemy_front",
    "상대입구": "enemy_front",
    "enemy natural": "enemy_natural",
    "적 앞마당": "enemy_natural",
    "적앞마당": "enemy_natural",
    "상대 앞마당": "enemy_natural",
    "상대앞마당": "enemy_natural",
    "enemy third": "enemy_third",
    "enemy third base": "enemy_third",
    "적 세번째 멀티": "enemy_third",
    "적 세 번째 멀티": "enemy_third",
    "적 삼룡이": "enemy_third",
    "상대 세번째 멀티": "enemy_third",
    "상대 삼룡이": "enemy_third",
    "enemy mineral line": "enemy_mineral_line",
    "적 미네랄 라인": "enemy_mineral_line",
    "적미네랄라인": "enemy_mineral_line",
    "상대 미네랄": "enemy_mineral_line",
    "상대미네랄": "enemy_mineral_line",
    "상대 일꾼 라인": "enemy_mineral_line",
    "상대일꾼라인": "enemy_mineral_line",
    "scout location": "scout_location",
    "scouted location": "scout_location",
    "last scout location": "scout_location",
    "정찰 위치": "scout_location",
    "정찰위치": "scout_location",
    "정찰 지점": "scout_location",
    "정찰지점": "scout_location",
    "정찰한 곳": "scout_location",
    "last seen enemy area": "last_seen_enemy_area",
    "last enemy position": "last_seen_enemy_area",
    "enemy last seen": "last_seen_enemy_area",
    "마지막 적 위치": "last_seen_enemy_area",
    "마지막적위치": "last_seen_enemy_area",
    "마지막으로 본 적": "last_seen_enemy_area",
    "최근 본 적 위치": "last_seen_enemy_area",
    "최근적위치": "last_seen_enemy_area",
}
"""Every ToyCraft canonical map location name (plus the interpreter retreat
fallback ``main base fallback``) mapped to a semantic SC2 target name. Unknown
location targets are rejected by the planner instead of being passed through."""

_NORMALIZED_SC2_TARGET_ALIASES: Final[dict[str, str]] = {
    "".join(alias.casefold().split()): canonical
    for alias, canonical in SC2_TARGET_ALIASES.items()
}
"""Whitespace-insensitive alias index shared by planner and catalog lookups."""

SC2_INTENT_ACTION_TYPE_MAP: Final[dict[str, tuple[str, ...]]] = {
    "GATHER_RESOURCE": ("assign_workers",),
    "BUILD_STRUCTURE": ("build_structure",),
    "TRAIN_WORKER": ("train_unit",),
    "TRAIN_ARMY": ("train_unit",),
    "SCOUT": ("move_group",),
    "SUMMARIZE_STATE": ("observe",),
    "DEFEND": ("attack_move",),
    "REPAIR": ("repair",),
    "EXPAND": ("build_structure",),
    "HARASS": ("attack_move",),
    "MOVE_CAMERA": ("move_camera",),
}
"""Stable public semantic action type names emitted for each Intent DSL value."""


def _gather_resource_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("GATHER_RESOURCE"),
            subject="SCV",
            target=str(_required_field(payload, "resource")),
            count=int(_required_field(payload, "worker_count")),
            metadata={"base": str(_required_field(payload, "base"))},
        ),
    )


def _build_structure_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    structure = str(_required_field(payload, "structure"))
    metadata: dict[str, Any] = {"source_structure": structure}
    placement_policy = _optional_mapping_field(payload, "placement_policy")
    if placement_policy is not None:
        metadata["placement_policy"] = placement_policy
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("BUILD_STRUCTURE"),
            subject=_structure_type_id(structure),
            target=_target_alias(str(_required_field(payload, "location"))),
            metadata=metadata,
        ),
    )


def _train_worker_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("TRAIN_WORKER"),
            subject="SCV",
            count=int(_required_field(payload, "count")),
            metadata={"producer": SC2_PRODUCER_TYPE_IDS["SCV"]},
        ),
    )


def _train_army_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    unit_type = str(_required_field(payload, "unit_type"))
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("TRAIN_ARMY"),
            subject=_unit_type_id(unit_type),
            count=int(_required_field(payload, "count")),
            metadata={
                "producer": _producer_type_id(unit_type),
                "source_unit": unit_type,
            },
        ),
    )


def _scout_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("SCOUT"),
            subject=str(_required_field(payload, "unit_group")),
            target=_target_alias(str(_required_field(payload, "target"))),
            metadata={"role": "scout"},
        ),
    )


def _defend_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("DEFEND"),
            subject=str(_required_field(payload, "unit_group")),
            target=_target_alias(str(_required_field(payload, "location"))),
            metadata={"role": "defend"},
        ),
    )


def _repair_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    # REPAIR targets are entity names (for example "front bunker"), not map
    # locations, so they intentionally stay verbatim.
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("REPAIR"),
            subject="SCV",
            target=str(_required_field(payload, "target")),
            count=int(_required_field(payload, "worker_count")),
        ),
    )


def _expand_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("EXPAND"),
            subject=SC2_STRUCTURE_TYPE_IDS["Command Center"],
            target=_target_alias(str(_required_field(payload, "location"))),
            metadata={"source_structure": "Command Center"},
        ),
    )


def _harass_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("HARASS"),
            subject=str(_required_field(payload, "unit_group")),
            target=_target_alias(str(_required_field(payload, "target"))),
            metadata={"role": "harass"},
        ),
    )


def _summarize_state_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("SUMMARIZE_STATE"),
            subject="visible_state",
            target="narrator_snapshot",
            count=0,
        ),
    )


def _move_camera_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    metadata: dict[str, Any] = {}
    target_slot = _optional_text_field(payload, "target_slot")
    if target_slot:
        metadata["target_slot"] = target_slot
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("MOVE_CAMERA"),
            subject="camera",
            target=_target_alias(str(_required_field(payload, "target"))),
            count=0,
            metadata=metadata,
        ),
    )


_SC2_INTENT_ACTION_BUILDERS: Final[
    dict[str, Callable[[object | Mapping[str, object]], tuple[SC2CommandAction, ...]]]
] = {
    "GATHER_RESOURCE": _gather_resource_actions,
    "BUILD_STRUCTURE": _build_structure_actions,
    "TRAIN_WORKER": _train_worker_actions,
    "TRAIN_ARMY": _train_army_actions,
    "SCOUT": _scout_actions,
    "SUMMARIZE_STATE": _summarize_state_actions,
    "DEFEND": _defend_actions,
    "REPAIR": _repair_actions,
    "EXPAND": _expand_actions,
    "HARASS": _harass_actions,
    "MOVE_CAMERA": _move_camera_actions,
}
"""One action-builder function per supported Intent DSL value."""


@runtime_checkable
class SC2ActionPlannerInterface(Protocol):
    """Planner boundary from typed commander intent to SC2 command plan."""

    def build_plan(self, payload: object | Mapping[str, object]) -> SC2ExecutionPlan:
        """Build a StarCraft II command plan without touching the live game."""


@dataclass(frozen=True)
class SC2ActionPlanner:
    """Default deterministic mapper from Intent DSL to StarCraft II actions."""

    def build_plan(self, payload: object | Mapping[str, object]) -> SC2ExecutionPlan:
        """Build a StarCraft II command plan from one typed Intent DSL payload."""

        intent_name = _intent_name(payload)
        priority = _priority_label(payload)
        constraints = _constraints(payload)
        actions = _actions_for_payload(payload, intent_name)
        return SC2ExecutionPlan(
            intent=intent_name,
            priority=priority,
            constraints=constraints,
            actions=actions,
            notes=_notes_for_payload(payload, intent_name),
        )


DEFAULT_SC2_ACTION_PLANNER: Final[SC2ActionPlanner] = SC2ActionPlanner()


def build_sc2_execution_plan(
    payload: object | Mapping[str, object],
) -> SC2ExecutionPlan:
    """Build the default StarCraft II plan for a commander Intent DSL payload."""

    return DEFAULT_SC2_ACTION_PLANNER.build_plan(payload)


def normalize_sc2_target_key(target: object) -> str:
    """Return the stable lookup key for Korean and English target aliases."""

    return "".join(str(target).casefold().split())


def resolve_sc2_target_name(
    target: object,
    catalog_entries: object = (),
) -> str | None:
    """Resolve raw natural-language target text to a semantic SC2 target name.

    ``catalog_entries`` may be the runtime semantic target catalog. When
    provided, its canonical target names and aliases participate in the same
    whitespace-insensitive lookup as the static planner aliases.
    """

    if type(target) is not str:
        return None
    requested = target.strip()
    if not requested:
        return None
    if requested in SC2_SEMANTIC_TARGET_NAMES:
        return requested

    exact_alias = SC2_TARGET_ALIASES.get(requested)
    if exact_alias is not None:
        return exact_alias

    normalized = normalize_sc2_target_key(requested)
    catalog_match = _resolve_catalog_target_alias(normalized, catalog_entries)
    if catalog_match is not None:
        return catalog_match

    normalized_alias = _NORMALIZED_SC2_TARGET_ALIASES.get(normalized)
    if normalized_alias is not None:
        return normalized_alias
    return None

@runtime_checkable
class SC2RuntimeExecutorInterface(Protocol):
    """Runtime boundary for applying SC2 plans to a live API object."""

    async def execute_plan(self, bot: object, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a python-sc2 BotAI-like object."""


@runtime_checkable
class SC2ExecutorBoundaryInterface(Protocol):
    """Lifecycle-aware SC2 executor boundary used by API and bot adapters."""

    @property
    def is_started(self) -> bool:
        """Return whether the executor lifecycle has been started."""

    async def start(self, bot: object | None = None) -> None:
        """Bind and initialize a BotAI-like runtime adapter if one is provided."""

    async def execute(self, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        """Execute one ordered semantic SC2 command plan and return a result."""

    async def close(self) -> None:
        """Release runtime lifecycle resources without raising to callers."""


@dataclass
class SC2RuntimeExecutor:
    """Lifecycle-aware async adapter around a python-sc2 ``BotAI``-like runtime."""

    bot: object | None = None
    _started: bool = field(default=False, init=False, repr=False)
    _lifecycle_errors: list[SC2ExecutionError] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._started = self.bot is not None

    @property
    def is_started(self) -> bool:
        """Return whether the executor has an active lifecycle."""

        return self._started

    @property
    def lifecycle_errors(self) -> tuple[SC2ExecutionError, ...]:
        """Structured lifecycle errors captured without crashing callers."""

        return tuple(self._lifecycle_errors)

    async def start(self, bot: object | None = None) -> None:
        """Start the executor lifecycle and optionally bind a BotAI-like object.

        Each ``start`` call begins a fresh lifecycle cycle: errors captured by a
        previous cycle's hooks are cleared so they cannot poison later results.
        """

        self._lifecycle_errors.clear()
        if bot is not None:
            self.bot = bot
        self._started = True
        await _call_optional_lifecycle_hook(
            self.bot,
            ("on_start", "start"),
            self._lifecycle_errors,
        )

    async def execute(self, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        """Execute a semantic SC2 command plan against the bound runtime adapter."""

        if self.bot is None:
            return _missing_runtime_result(
                plan,
                self._started,
                self._drain_lifecycle_errors(),
            )
        return await self._execute_with_bot(self.bot, plan)

    async def close(self) -> None:
        """Close the executor lifecycle while preserving structured errors."""

        await _call_optional_lifecycle_hook(
            self.bot,
            ("on_end", "close", "stop"),
            self._lifecycle_errors,
        )
        self._started = False

    async def execute_plan(
        self,
        bot: object,
        plan: SC2ExecutionPlan,
    ) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a live SC2 runtime adapter."""

        return await self._execute_with_bot(bot, plan)

    async def _execute_with_bot(
        self,
        bot: object,
        plan: SC2ExecutionPlan,
    ) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a live SC2 runtime adapter.

        Structured :class:`SC2ActionReport` returns are audited per action
        index; an applied action that issued fewer orders than requested adds
        a ``PartialActionApplication`` error so the result can never narrate
        partial issuance as unqualified success. Lifecycle hook errors are
        drained into the first result after the hook ran, so one transient
        hook failure cannot poison every later execution in the cycle.
        """

        applied: list[SC2CommandAction] = []
        skipped: list[SC2CommandAction] = []
        errors: list[SC2ExecutionError] = []
        observations: dict[str, dict[str, object]] = {}
        action_reports: dict[str, dict[str, object]] = {}

        for action_index, action in enumerate(plan.actions):
            try:
                application = await _apply_action(bot, action)
            except Exception as exc:
                errors.append(
                    SC2ExecutionError(
                        message=str(exc),
                        action_type=action.action_type,
                        action_index=action_index,
                        exception_type=type(exc).__name__,
                    )
                )
                skipped.append(action)
                continue
            if application.missing_method is not None:
                errors.append(
                    SC2ExecutionError(
                        message=(
                            "bot runtime adapter implements neither "
                            f"'{application.missing_method}' nor "
                            "'execute_commander_action'."
                        ),
                        action_type=action.action_type,
                        action_index=action_index,
                        exception_type="MissingBotCapability",
                        metadata={"expected_method": application.missing_method},
                    )
                )
                skipped.append(action)
                continue
            if application.observation is not None:
                observations[str(action_index)] = dict(application.observation)
            report = application.report
            if report is not None:
                action_reports[str(action_index)] = report.to_dict()
                if report.is_partial:
                    errors.append(_partial_application_error(action, action_index, report))
                elif not report.applied and report.detail:
                    errors.append(_refused_action_error(action, action_index, report))
            if application.applied:
                applied.append(action)
            else:
                skipped.append(action)

        return SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.actions,
            applied_actions=tuple(applied),
            skipped_actions=tuple(skipped),
            errors=tuple((*self._drain_lifecycle_errors(), *errors)),
            audit={
                "runtime_adapter": type(bot).__name__,
                "executor_started": self._started,
                "planned_action_count": len(plan.actions),
                "observations": observations,
                "action_reports": action_reports,
            },
        )

    def _drain_lifecycle_errors(self) -> tuple[SC2ExecutionError, ...]:
        """Consume captured lifecycle errors so they are reported exactly once."""

        drained = tuple(self._lifecycle_errors)
        self._lifecycle_errors.clear()
        return drained


_MISSING: Final[object] = object()


def _actions_for_payload(
    payload: object | Mapping[str, object],
    intent_name: str,
) -> tuple[SC2CommandAction, ...]:
    builder = _SC2_INTENT_ACTION_BUILDERS.get(intent_name)
    if builder is None:
        raise ValueError(f"unsupported SC2 intent payload: {intent_name}")
    return builder(payload)


def _notes_for_payload(
    payload: object | Mapping[str, object],
    intent_name: str,
) -> tuple[str, ...]:
    notes = [
        "SC2 executor plans semantic API commands, not mouse clicks.",
        "Live execution requires StarCraft II plus a python-sc2 BotAI runtime.",
    ]
    if intent_name == "TRAIN_ARMY" and str(_field(payload, "unit_type", "")) == "Vulture":
        notes.append("SC2 maps Brood War Vulture intent to Hellion for MVP harass.")
    return tuple(notes)


def _intent_name(payload: object | Mapping[str, object]) -> str:
    intent_name = str(_required_field(payload, "intent"))
    if not intent_name.strip():
        raise ValueError("SC2 intent payload must include a non-empty intent.")
    return intent_name


def _priority_label(payload: object | Mapping[str, object]) -> str:
    return str(_field(payload, "priority", "normal"))


def _constraints(payload: object | Mapping[str, object]) -> tuple[str, ...]:
    return tuple(str(item) for item in _field(payload, "constraints", ()))


def _required_field(payload: object | Mapping[str, object], field_name: str) -> Any:
    value = _field(payload, field_name, _MISSING)
    if value is _MISSING:
        raise ValueError(f"SC2 intent payload missing required field: {field_name}")
    return value


def _field(
    payload: object | Mapping[str, object],
    field_name: str,
    default: object = _MISSING,
) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(field_name, default)
    return getattr(payload, field_name, default)


def _unit_type_id(unit_name: str) -> str:
    try:
        return SC2_UNIT_TYPE_IDS[unit_name]
    except KeyError as exc:
        raise ValueError(f"unsupported SC2 unit: {unit_name}") from exc


def _structure_type_id(structure_name: str) -> str:
    try:
        return SC2_STRUCTURE_TYPE_IDS[structure_name]
    except KeyError as exc:
        raise ValueError(f"unsupported SC2 structure: {structure_name}") from exc


def _producer_type_id(unit_name: str) -> str:
    try:
        return SC2_PRODUCER_TYPE_IDS[unit_name]
    except KeyError as exc:
        raise ValueError(f"unsupported SC2 producer for unit: {unit_name}") from exc


def _target_alias(target: str) -> str:
    """Resolve a map-location target strictly to a semantic SC2 target name."""

    alias = resolve_sc2_target_name(target)
    if alias is not None:
        return alias
    normalized = " ".join(str(target or "").casefold().split())
    compact = normalized.replace(" ", "")
    if _looks_like_self_geyser_target(normalized, compact):
        return "self_geyser"
    supported = ", ".join(sorted({*SC2_TARGET_ALIASES, *SC2_SEMANTIC_TARGET_NAMES}))
    raise ValueError(
        f"unsupported SC2 target location: {target!r}. "
        f"Supported targets: {supported}."
    )


def _looks_like_self_geyser_target(normalized: str, compact: str) -> bool:
    """Accept LLM natural-language gas/geyser phrases as the main geyser."""

    gas_markers = (
        "gas",
        "geyser",
        "vespene",
        "refinery target",
        "가스",
        "간헐천",
        "베스핀",
        "배스핀",
        "배프빈",
    )
    self_markers = (
        "main",
        "base",
        "self",
        "our",
        "nearest available",
        "본진",
        "우리",
        "아군",
        "내",
    )
    return any(marker in normalized or marker in compact for marker in gas_markers) and (
        any(marker in normalized or marker in compact for marker in self_markers)
        or normalized in {"gas", "geyser", "가스", "정제소"}
    )


def _optional_mapping_field(
    payload: object | Mapping[str, object],
    field_name: str,
) -> dict[str, object] | None:
    if isinstance(payload, Mapping):
        value = payload.get(field_name)
    else:
        value = getattr(payload, field_name, None)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping when provided.")
    return {str(key): item for key, item in value.items()}


def _optional_text_field(
    payload: object | Mapping[str, object],
    field_name: str,
) -> str:
    value = _field(payload, field_name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string when provided.")
    return value.strip()


def _resolve_catalog_target_alias(
    normalized_target: str,
    catalog_entries: object,
) -> str | None:
    try:
        entries = tuple(catalog_entries)
    except Exception:
        return None
    for entry in entries:
        target = getattr(entry, "target", "")
        if (
            type(target) is str
            and target in SC2_SEMANTIC_TARGET_NAMES
            and normalize_sc2_target_key(target) == normalized_target
        ):
            return target
        aliases = getattr(entry, "aliases", ())
        try:
            alias_values = tuple(aliases)
        except Exception:
            alias_values = ()
        for alias in alias_values:
            if normalize_sc2_target_key(alias) == normalized_target:
                if type(target) is str and target in SC2_SEMANTIC_TARGET_NAMES:
                    return target
    return None


def _action_type_for_intent(intent: str) -> SC2ActionType:
    action_types = SC2_INTENT_ACTION_TYPE_MAP[intent]
    if len(action_types) != 1:
        raise ValueError(f"SC2 intent emits multiple action types: {intent}")
    action_type = action_types[0]
    if action_type not in SC2_ACTION_TYPES:
        raise ValueError(f"unsupported public SC2 action type: {action_type}")
    return SC2ActionType(action_type)


@dataclass(frozen=True)
class _SC2ActionApplication:
    """Outcome of dispatching one semantic action to a bot runtime adapter."""

    applied: bool
    observation: Mapping[str, object] | None = None
    missing_method: str | None = None
    report: SC2ActionReport | None = None


async def _apply_action(bot: object, action: SC2CommandAction) -> _SC2ActionApplication:
    method_name = _method_name_for_action(action.action_type)
    method = getattr(bot, method_name, None)
    if method is None:
        method = getattr(bot, "execute_commander_action", None)
    if method is None:
        return _SC2ActionApplication(applied=False, missing_method=method_name)

    result = method(action)
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, SC2ActionReport):
        return _SC2ActionApplication(applied=result.applied, report=result)
    if isinstance(result, Mapping):
        return _SC2ActionApplication(applied=True, observation=result)
    if result is None:
        return _SC2ActionApplication(applied=True)
    return _SC2ActionApplication(applied=bool(result))


def _refused_action_error(
    action: SC2CommandAction,
    action_index: int,
    report: SC2ActionReport,
) -> SC2ExecutionError:
    """Build the structured error explaining why an adapter refused an action."""

    metadata: dict[str, object] = {"detail": report.detail}
    if report.audit:
        metadata["audit"] = dict(report.audit)
    return SC2ExecutionError(
        message=(
            f"action '{action.action_type.value}' was refused without issuing "
            f"orders: {report.detail}."
        ),
        action_type=action.action_type,
        action_index=action_index,
        exception_type="ActionRefused",
        metadata=metadata,
    )


def _partial_application_error(
    action: SC2CommandAction,
    action_index: int,
    report: SC2ActionReport,
) -> SC2ExecutionError:
    """Build the structured error surfacing a within-action issuance shortfall."""

    metadata: dict[str, object] = {
        "requested_count": report.requested_count,
        "issued_count": report.issued_count,
    }
    if report.detail:
        metadata["detail"] = report.detail
    return SC2ExecutionError(
        message=(
            f"only {report.issued_count} of {report.requested_count} requested "
            f"orders were issued for action '{action.action_type.value}'."
        ),
        action_type=action.action_type,
        action_index=action_index,
        exception_type="PartialActionApplication",
        metadata=metadata,
    )


async def _call_optional_lifecycle_hook(
    bot: object | None,
    hook_names: tuple[str, ...],
    errors: list[SC2ExecutionError],
) -> None:
    if bot is None:
        return
    for hook_name in hook_names:
        hook = getattr(bot, hook_name, None)
        if hook is None:
            continue
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            errors.append(
                SC2ExecutionError(
                    message=str(exc),
                    exception_type=type(exc).__name__,
                    metadata={"lifecycle_hook": hook_name},
                )
            )
        return


def _missing_runtime_result(
    plan: SC2ExecutionPlan,
    executor_started: bool,
    lifecycle_errors: tuple[SC2ExecutionError, ...],
) -> SC2PlanExecutionResult:
    missing_runtime_error = SC2ExecutionError(
        message="SC2 runtime adapter has not been bound to a BotAI-like object.",
        exception_type="MissingRuntimeAdapter",
        metadata={"executor_started": executor_started},
    )
    return SC2PlanExecutionResult(
        plan=plan,
        attempted_actions=(),
        applied_actions=(),
        skipped_actions=plan.actions,
        errors=(*lifecycle_errors, missing_runtime_error),
        audit={
            "runtime_adapter": None,
            "executor_started": executor_started,
            "planned_action_count": len(plan.actions),
        },
    )


def _method_name_for_action(action_type: SC2ActionType) -> str:
    return {
        SC2ActionType.ASSIGN_WORKERS: "assign_workers",
        SC2ActionType.BUILD_STRUCTURE: "build_structure",
        SC2ActionType.TRAIN_UNIT: "train_unit",
        SC2ActionType.MOVE_GROUP: "move_group",
        SC2ActionType.ATTACK_MOVE: "attack_move",
        SC2ActionType.REPAIR: "repair",
        SC2ActionType.OBSERVE: "observe",
        SC2ActionType.MOVE_CAMERA: "move_camera",
    }[action_type]
