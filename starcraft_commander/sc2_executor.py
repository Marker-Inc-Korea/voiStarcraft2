"""StarCraft II execution adapter for commander Intent DSL payloads.

This module is intentionally importable without StarCraft II or python-sc2
installed. Unit tests can validate command planning with pure Python fakes, while
real runtime code can pass a python-sc2 ``BotAI``-like object to
``SC2RuntimeExecutor.execute_plan``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
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
from starcraft_commander.direct_command_lifecycle import DirectCommandLifecycle
from starcraft_commander.direct_command_lifecycle import DirectCommandLease


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
    "EXECUTE_ABILITY": ("execute_ability",),
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
    direct_lifecycle: DirectCommandLifecycle = field(
        default_factory=DirectCommandLifecycle
    )
    on_direct_release: Callable[[DirectCommandLease], object] | None = None
    _started: bool = field(default=False, init=False, repr=False)
    _previous_direct_release: Callable[[DirectCommandLease], object] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _dependent_workflows: dict[str, tuple[Mapping[str, object], ...]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _ready_workflows: list[tuple[str, int, Mapping[str, object]]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _deferred_workflow_releases: dict[str, DirectCommandLease] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _workflow_remaining: dict[str, int] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lifecycle_errors: list[SC2ExecutionError] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._started = self.bot is not None
        # The runtime executor owns the match-lifetime lifecycle.  Install one
        # wrapper at construction time so terminal completion/cancel/expiry
        # can hand control back to a host MicroMachine integration without
        # requiring every caller to remember to wire the callback manually.
        # Preserve a callback supplied by an embedding integration and invoke
        # it before the executor's explicit/auto-discovered resume seam.
        self._previous_direct_release = self.direct_lifecycle.on_release
        self.direct_lifecycle.on_release = self._handle_direct_release

    def _handle_direct_release(self, lease: DirectCommandLease) -> None:
        """Notify release listeners and, when available, resume MicroMachine.

        ``SC2RuntimeExecutor`` cannot manufacture a MicroMachine runtime.  It
        therefore only calls an explicitly supplied ``on_direct_release``
        callback, or a BotAI adapter method named ``resume_micromachine``.  A
        missing seam is a no-op; a callback failure is recorded as structured
        lifecycle evidence and does not break the game loop.
        """

        dependent_steps = self._dependent_workflows.get(lease.command_id, ())
        if (
            lease.release_reason == "completion_conditions"
            and dependent_steps
        ):
            # Keep Direct ownership logically continuous across a dependent
            # workflow.  The parent lease is terminal, but MicroMachine must
            # not reacquire its subjects in the frame between build completion
            # and the queued train step.
            self._deferred_workflow_releases[lease.command_id] = lease
            return
        if dependent_steps:
            # Cancellation, failure, and TTL expiry abort the remaining
            # workflow rather than leaving a stale train step queued.
            self._dependent_workflows.pop(lease.command_id, None)
            self._workflow_remaining.pop(lease.command_id, None)
            self._ready_workflows = [
                item for item in self._ready_workflows if item[0] != lease.command_id
            ]

        workflow_parent = lease.command_metadata.get("workflow_parent")
        if isinstance(workflow_parent, str) and workflow_parent:
            remaining = self._workflow_remaining.get(workflow_parent, 1) - 1
            self._workflow_remaining[workflow_parent] = remaining
            if remaining > 0:
                return
            parent_lease = self._deferred_workflow_releases.pop(workflow_parent, None)
            self._workflow_remaining.pop(workflow_parent, None)
            if parent_lease is not None:
                self._finish_workflow_release(parent_lease)
            return

        self._emit_direct_release(lease)

    def _emit_direct_release(self, lease: DirectCommandLease) -> None:
        """Invoke preserved and configured terminal-release listeners."""

        callbacks: list[Callable[[DirectCommandLease], object]] = []
        if self._previous_direct_release is not None:
            callbacks.append(self._previous_direct_release)
        callback = self.on_direct_release
        if callback is None:
            candidate = getattr(self.bot, "resume_micromachine", None)
            if callable(candidate):
                callback = candidate
        if callback is not None and callback not in callbacks:
            callbacks.append(callback)
        for listener in callbacks:
            try:
                listener(lease)
            except Exception as error:  # noqa: BLE001 - release is fail-closed.
                self._lifecycle_errors.append(
                    SC2ExecutionError(
                        message=f"direct release callback failed: {error}",
                        exception_type=type(error).__name__,
                        metadata={
                            "command_id": lease.command_id,
                            "release_reason": lease.release_reason,
                        },
                    )
                )

    def _finish_workflow_release(self, lease: DirectCommandLease) -> None:
        """Propagate a completed dependent release through nested parents."""

        parent_id = lease.command_metadata.get("workflow_parent")
        if not isinstance(parent_id, str) or not parent_id:
            self._emit_direct_release(lease)
            return
        remaining = self._workflow_remaining.get(parent_id, 1) - 1
        self._workflow_remaining[parent_id] = remaining
        if remaining > 0:
            return
        self._workflow_remaining.pop(parent_id, None)
        parent_lease = self._deferred_workflow_releases.pop(parent_id, None)
        if parent_lease is not None:
            self._finish_workflow_release(parent_lease)

    @property
    def is_started(self) -> bool:
        """Return whether the executor has an active lifecycle."""

        return self._started

    @property
    def lifecycle_errors(self) -> tuple[SC2ExecutionError, ...]:
        """Structured lifecycle errors captured without crashing callers."""

        return tuple(self._lifecycle_errors)

    def bind_direct_lifecycle(self, lifecycle: DirectCommandLifecycle) -> None:
        """Share an integration-owned lease registry with this executor.

        Live sessions may construct their lifecycle before they construct the
        MCP registry.  Rebinding preserves the lifecycle's existing release
        listener (for example, the session callback) and installs the
        executor's workflow/resume wrapper on the shared registry.
        """

        if not isinstance(lifecycle, DirectCommandLifecycle):
            raise TypeError("lifecycle must be a DirectCommandLifecycle")
        if lifecycle is self.direct_lifecycle:
            return
        self.direct_lifecycle = lifecycle
        self._previous_direct_release = lifecycle.on_release
        lifecycle.on_release = self._handle_direct_release

    def observe_direct_commands(
        self,
        *,
        current_frame: int,
        evidence_by_command: Mapping[str, Mapping[str, object]] | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Tick Direct leases owned by this live runtime executor.

        The executor is the natural lifetime owner for a BotAI match.  Both
        MCP modulation sessions and the BotAI ``on_step`` callback can use this
        seam without creating separate registries that lose squad ownership.
        """

        supplied = evidence_by_command or {}
        observed_evidence: dict[str, Mapping[str, object]] = {}
        for lease in self.direct_lifecycle.active_leases():
            evidence: dict[str, object] = {}
            provider = getattr(self.bot, "direct_command_evidence", None)
            if callable(provider):
                try:
                    candidate = provider(lease.to_dict())
                    if isinstance(candidate, Mapping):
                        evidence.update(candidate)
                except Exception as error:  # noqa: BLE001 - fail closed.
                    self._lifecycle_errors.append(
                        SC2ExecutionError(
                            message=f"direct evidence provider failed: {error}",
                            exception_type=type(error).__name__,
                            metadata={"command_id": lease.command_id},
                        )
                    )
            supplied_evidence = supplied.get(lease.command_id)
            if isinstance(supplied_evidence, Mapping):
                evidence.update(supplied_evidence)
            observed_evidence[lease.command_id] = evidence
        return tuple(
            lease.to_dict()
            for lease in self.direct_lifecycle.observe_all(
                frame=int(current_frame),
                evidence_by_command=observed_evidence,
            )
        )

    def direct_command_baseline(
        self, plan: SC2ExecutionPlan
    ) -> Mapping[str, object]:
        """Capture pre-dispatch runtime counts for completion evidence."""

        provider = getattr(self.bot, "direct_command_baseline", None)
        if not callable(provider):
            return {}
        try:
            value = provider(plan.to_dict())
        except Exception as error:  # noqa: BLE001 - baseline is optional evidence.
            self._lifecycle_errors.append(
                SC2ExecutionError(
                    message=f"direct baseline provider failed: {error}",
                    exception_type=type(error).__name__,
                )
            )
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    def register_dependent_plans(
        self,
        parent_command_id: str,
        dependent_plans: Sequence[Mapping[str, object]],
    ) -> None:
        """Register plans that may run only after a parent lease completes.

        Each item is ``{"resume_on": "building_completed", "plan": {...}}``.
        The plan is parsed immediately so malformed workflow payloads fail at
        dispatch time, never half-way through a live game.  The next step is
        scheduled by :meth:`tick_direct_commands`; callers must await
        :meth:`drain_completed_workflows` from their game-loop callback.
        """

        parent_id = str(parent_command_id).strip()
        if not parent_id:
            raise ValueError("parent_command_id must be non-empty")
        lease = self.direct_lifecycle.get(parent_id)
        if lease is None or not lease.control_owned:
            raise ValueError("parent direct lease must own control")
        normalized: list[Mapping[str, object]] = []
        for item in dependent_plans:
            if not isinstance(item, Mapping):
                raise ValueError("dependent workflow steps must be objects")
            resume_on = item.get("resume_on", "building_completed")
            if isinstance(resume_on, str):
                resume_conditions = (resume_on,)
            elif isinstance(resume_on, Sequence) and not isinstance(
                resume_on, (str, bytes)
            ):
                resume_conditions = tuple(str(value) for value in resume_on)
            else:
                raise ValueError("dependent workflow resume_on must be a string or list")
            if not resume_conditions or any(not condition.strip() for condition in resume_conditions):
                raise ValueError("dependent workflow resume_on cannot be empty")
            raw_plan = item.get("plan", item)
            if not isinstance(raw_plan, Mapping):
                raise ValueError("dependent workflow plan must be an object")
            # Import lazily to avoid the sc2_executor <-> unified router cycle.
            from starcraft_commander.unified_command_router import (
                execution_plan_from_mapping,
            )

            parsed = execution_plan_from_mapping(raw_plan)
            normalized.append(
                {
                    "resume_on": list(resume_conditions),
                    "plan": parsed.to_dict(),
                }
            )
        self._dependent_workflows[parent_id] = tuple(normalized)
        self._workflow_remaining[parent_id] = len(normalized)

    async def drain_completed_workflows(
        self,
        *,
        current_frame: int = 0,
    ) -> tuple[SC2PlanExecutionResult, ...]:
        """Execute the next ready dependent step in deterministic order.

        A dependent step receives its own lifecycle lease.  If that step has
        more dependent plans, they are registered only after the step is
        successfully dispatched, preserving ``build -> observe completion ->
        train`` ordering across multiple frames.
        """

        if self.bot is None:
            return ()
        results: list[SC2PlanExecutionResult] = []
        # At most one ready step is run per drain call.  This prevents a chain
        # from skipping its own completion observation in the same frame.
        if not self._ready_workflows:
            return ()
        parent_id, step_index, payload = self._ready_workflows.pop(0)
        from starcraft_commander.unified_command_router import (
            execution_plan_from_mapping,
        )

        child_id = f"{parent_id}:step:{step_index}"
        try:
            plan = execution_plan_from_mapping(payload["plan"])
            # Dependent steps are dispatched on a later game-loop frame. Rebind
            # the semantic action against the current observation before
            # admitting its child lease, just as the MCP entry point does for
            # the parent. python-sc2 recreates Unit objects between
            # observations, so the child must reserve the current concrete
            # producer tags rather than inherit stale instances or a
            # type-only subject.
            plan = self._bind_direct_plan(plan)
        except Exception as error:  # noqa: BLE001 - workflow failures are data.
            self._lifecycle_errors.append(
                SC2ExecutionError(
                    message=f"dependent workflow binding failed: {error}",
                    exception_type=type(error).__name__,
                    metadata={"command_id": child_id},
                )
            )
            deferred = self._deferred_workflow_releases.pop(parent_id, None)
            self._workflow_remaining.pop(parent_id, None)
            if deferred is not None:
                self._emit_direct_release(deferred)
            return ()
        conditions_raw = plan.audit.get("completion_conditions", ("order_issued",))
        conditions = (
            tuple(str(item) for item in conditions_raw)
            if isinstance(conditions_raw, Sequence)
            and not isinstance(conditions_raw, (str, bytes))
            else ("order_issued",)
        )
        lease = self.direct_lifecycle.pending(
            command_id=child_id,
            issued_at_frame=max(0, int(current_frame)),
            ttl_seconds=max(1, int(plan.audit.get("ttl_seconds", 120))),
            completion_conditions=conditions,
            owned_subjects=tuple(
                action.subject
                for action in plan.actions
                if not action.metadata.get("_direct_unit_tags")
            ),
            owned_unit_tags=tuple(
                tag
                for action in plan.actions
                for tag in action.metadata.get("_direct_unit_tags", ())
                if type(tag) is int and tag > 0
            ),
            command_metadata={
                "intent_name": plan.intent_name,
                "actions": [action.to_dict() for action in plan.actions],
                "workflow_parent": parent_id,
                "workflow_step": step_index,
            },
        )
        try:
            result = await self._execute_with_bot(self.bot, plan)
        except Exception as error:  # noqa: BLE001 - workflow failures are data.
            self.direct_lifecycle.fail(
                child_id,
                reason="dependent_workflow_exception",
                evidence={"error": f"{type(error).__name__}:{error}"},
            )
            return ()
        if result.success:
            self.direct_lifecycle.mark_dispatched(child_id)
            active = self.direct_lifecycle.activate(child_id)
            if "order_issued" in active.completion_conditions:
                self.direct_lifecycle.record_evidence(child_id, {"order_issued": True})
            nested = plan.audit.get("dependent_plans", ())
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                try:
                    self.register_dependent_plans(child_id, nested)
                except Exception as error:  # noqa: BLE001 - workflow is data.
                    self.direct_lifecycle.fail(
                        child_id,
                        reason="invalid_dependent_workflow",
                        evidence={"error": f"{type(error).__name__}:{error}"},
                    )
        else:
            self.direct_lifecycle.fail(child_id, reason="dependent_plan_refused")
        results.append(result)
        return tuple(results)

    def _bind_direct_plan(self, plan: SC2ExecutionPlan) -> SC2ExecutionPlan:
        """Bind every action in a plan to the current runtime observation."""

        binder = getattr(self.bot, "bind_direct_action", None)
        if not callable(binder):
            return plan
        bound_actions = tuple(binder(action) for action in plan.actions)
        if bound_actions == plan.actions:
            return plan
        return SC2ExecutionPlan(
            intent_name=plan.intent_name,
            priority=plan.priority,
            ordered_actions=bound_actions,
            constraints=plan.constraints,
            requires_live_sc2=plan.requires_live_sc2,
            notes=plan.notes,
            audit=plan.audit,
        )

    def tick_direct_commands(
        self,
        current_frame: int,
        evidence_by_command: Mapping[str, Mapping[str, object]] | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Concise game-loop callback alias for Direct lifecycle observation."""

        before = {
            lease.command_id: lease
            for lease in self.direct_lifecycle.active_leases()
        }
        observed = self.observe_direct_commands(
            current_frame=current_frame,
            evidence_by_command=evidence_by_command,
        )
        for item in observed:
            command_id = str(item.get("command_id", ""))
            previous = before.get(command_id)
            if previous is None or item.get("state") != "completed":
                continue
            evidence = item.get("evidence", {})
            if not isinstance(evidence, Mapping):
                evidence = {}
            steps = self._dependent_workflows.pop(command_id, ())
            matched_steps: list[tuple[str, int, Mapping[str, object]]] = []
            for index, step in enumerate(steps):
                resume_on = step.get("resume_on", ())
                if isinstance(resume_on, str):
                    resume_on = (resume_on,)
                if any(bool(evidence.get(str(condition))) for condition in resume_on):
                    matched_steps.append((command_id, index, step))
            if matched_steps:
                # Only steps whose resume condition was actually observed
                # participate in the final release count.  Unmatched
                # alternatives must not leave the parent lease deferred
                # forever.
                self._workflow_remaining[command_id] = len(matched_steps)
                self._ready_workflows.extend(matched_steps)
            elif steps:
                deferred = self._deferred_workflow_releases.pop(command_id, None)
                self._workflow_remaining.pop(command_id, None)
                if deferred is not None:
                    self._emit_direct_release(deferred)
        return observed

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
    inferred = _infer_semantic_target_from_freeform(normalized, compact)
    if inferred is not None:
        return inferred
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
    has_gas_marker = any(
        marker in normalized or marker in compact for marker in gas_markers
    )
    has_enemy_marker = any(
        marker in normalized or marker in compact
        for marker in ("enemy", "적", "상대")
    )
    if not has_gas_marker or has_enemy_marker:
        return False
    return True


def _infer_semantic_target_from_freeform(
    normalized: str,
    compact: str,
) -> str | None:
    """Infer a conservative semantic target from LLM free-form location text."""

    has_enemy_marker = any(
        marker in normalized or marker in compact
        for marker in ("enemy", "적", "상대")
    )
    if has_enemy_marker:
        return None

    has_self_marker = any(
        marker in normalized or marker in compact
        for marker in (
            "main",
            "self",
            "our",
            "friendly",
            "home",
            "본진",
            "우리",
            "아군",
            "내",
        )
    )
    has_natural_marker = any(
        marker in normalized or marker in compact
        for marker in ("natural", "앞마당", "앞마당쪽", "아군앞마당")
    )
    has_ramp_marker = any(
        marker in normalized or marker in compact
        for marker in (
            "ramp",
            "choke",
            "front",
            "entrance",
            "near ramp",
            "입구",
            "초크",
        )
    )
    if has_natural_marker:
        return "self_choke" if has_ramp_marker else "self_natural"
    if has_ramp_marker and has_self_marker:
        return "self_ramp"
    if has_self_marker:
        return "self_main"
    if any(
        marker in normalized or marker in compact
        for marker in (
            "buildable",
            "construction",
            "safe location",
            "safe build",
            "default build",
            "good location",
            "near command center",
            "건설가능",
            "건설 가능한",
            "안전한 위치",
            "좋은 위치",
            "적당한 위치",
            "사령부 근처",
            "커맨드센터 근처",
        )
    ):
        return "self_main"
    return None


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
        SC2ActionType.GATHER_RESOURCE: "gather_resource",
        SC2ActionType.BUILD_STRUCTURE: "build_structure",
        SC2ActionType.TRAIN_UNIT: "train_unit",
        SC2ActionType.RESEARCH_UPGRADE: "research_upgrade",
        SC2ActionType.WARP_IN: "warp_in",
        SC2ActionType.MOVE_GROUP: "move_group",
        SC2ActionType.ATTACK_MOVE: "attack_move",
        SC2ActionType.SMART: "smart",
        SC2ActionType.PATROL: "patrol",
        SC2ActionType.RETURN_RESOURCE: "return_resource",
        SC2ActionType.REPAIR: "repair",
        SC2ActionType.EXECUTE_ABILITY: "execute_ability",
        SC2ActionType.OBSERVE: "observe",
        SC2ActionType.MOVE_CAMERA: "move_camera",
        SC2ActionType.STOP_GROUP: "stop_group",
        SC2ActionType.HOLD_POSITION: "hold_position",
    }[action_type]
