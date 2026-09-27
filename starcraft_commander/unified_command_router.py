"""Unified macro/micro routing and composable command tools.

The LLM produces a validated :class:`PolicyModulationVector`. This module is
the single execution boundary after validation:

* ``macro`` publishes manager weights to MicroMachine only.
* every other layer is ``micro`` and is classified as operation, ability, or
  emergency.
* non-macro routes use the direct SC2 tool surface only. Direct execution is
  reported honestly when no live BotAI runtime is attached.

The registry is deliberately dependency-light so the same tools can be used by
the web cockpit, tests, or the stdio MCP server.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from starcraft_commander.contracts import (
    SC2_DIRECT_ACTION_TYPES,
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.direct_command_lifecycle import (
    DirectCommandLifecycle,
    DirectCommandOwnershipConflict,
)
from starcraft_commander.direct_command_registry import DirectCommandRegistry
from starcraft_commander.policy_modulation import (
    CommandLayer,
    PolicyModulationVector,
)


class UnifiedCommandLayer(str, Enum):
    MACRO = "macro"
    MICRO = "micro"


class MicroCommandKind(str, Enum):
    OPERATION = "operation"
    ABILITY = "micro"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class SC2Capability:
    """One bounded semantic capability exposed through MCP."""

    name: str
    action_type: SC2ActionType
    description: str
    category: str
    supports_parallel: bool = True
    requires_runtime: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "action_type": self.action_type.value,
            "description": self.description,
            "category": self.category,
            "supports_parallel": self.supports_parallel,
            "requires_runtime": self.requires_runtime,
        }


SC2_CAPABILITY_CATALOG: Final[tuple[SC2Capability, ...]] = (
    SC2Capability("sc2.direct.assign_workers", SC2ActionType.ASSIGN_WORKERS, "Assign workers to minerals or completed gas.", "economy"),
    SC2Capability("sc2.direct.build_structure", SC2ActionType.BUILD_STRUCTURE, "Build one semantic structure at a validated location.", "construction", supports_parallel=False),
    SC2Capability("sc2.direct.train_unit", SC2ActionType.TRAIN_UNIT, "Queue semantic unit production.", "production", supports_parallel=False),
    SC2Capability("sc2.direct.move_group", SC2ActionType.MOVE_GROUP, "Move a named or typed squad to a semantic target.", "operation"),
    SC2Capability("sc2.direct.attack_move", SC2ActionType.ATTACK_MOVE, "Attack-move a squad to a semantic target.", "operation"),
    SC2Capability("sc2.direct.repair", SC2ActionType.REPAIR, "Repair a matching damaged friendly target.", "support", supports_parallel=False),
    SC2Capability("sc2.direct.execute_ability", SC2ActionType.EXECUTE_ABILITY, "Use one validated unit ability.", "micro", supports_parallel=False),
    SC2Capability("sc2.direct.observe", SC2ActionType.OBSERVE, "Read the structured live game state.", "observation", supports_parallel=True),
    SC2Capability("sc2.direct.move_camera", SC2ActionType.MOVE_CAMERA, "Move the camera to a semantic target.", "observation"),
    SC2Capability("sc2.direct.stop_group", SC2ActionType.STOP_GROUP, "Stop a named or typed squad.", "emergency", supports_parallel=False),
    SC2Capability("sc2.direct.hold_position", SC2ActionType.HOLD_POSITION, "Hold a named or typed squad in place.", "emergency", supports_parallel=False),
    # Retreat is a first-class semantic capability even though python-sc2
    # lowers it to a move order at the adapter boundary.  Keeping the
    # capability name explicit prevents providers from having to invent a
    # target-specific ``move_group`` call for an emergency retreat.
    SC2Capability("sc2.direct.retreat", SC2ActionType.MOVE_GROUP, "Retreat a named or typed squad to a safe semantic target.", "emergency", supports_parallel=False),
)


@dataclass(frozen=True)
class ToolCall:
    """One composable command-tool invocation."""

    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("tool call name must be non-empty.")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool call arguments must be a mapping.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "arguments", dict(self.arguments))

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "arguments": dict(self.arguments)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ToolCall":
        arguments = value.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise ValueError("tool call arguments must be an object.")
        return cls(name=str(value.get("name", "")), arguments=arguments)


@dataclass(frozen=True)
class ToolExecutionResult:
    """Auditable result from one tool call."""

    name: str
    ok: bool
    result: Mapping[str, object] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "result": dict(self.result),
            "error": self.error,
        }


@dataclass(frozen=True)
class ToolSpec:
    """MCP-compatible tool metadata plus a local handler."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    handler: Callable[[Mapping[str, object]], object]

    def to_mcp_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


class CommanderToolRegistry:
    """Registry that supports one or many tool calls with stable results."""

    def __init__(self, tools: Sequence[ToolSpec] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate command tool: {tool.name}")
        self._tools[tool.name] = tool

    def list_tools(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools.values())

    def call(
        self,
        name: str,
        arguments: Mapping[str, object] | None = None,
    ) -> ToolExecutionResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolExecutionResult(
                name=name,
                ok=False,
                error=f"unknown_tool:{name}",
            )
        try:
            value = tool.handler(dict(arguments or {}))
            if inspect.isawaitable(value):
                value = _run_awaitable_sync(value)
            result = _mapping_result(value)
            ok = result.get("ok", True)
            return ToolExecutionResult(
                name=name,
                ok=ok if isinstance(ok, bool) else True,
                result=result,
            )
        except Exception as error:  # noqa: BLE001 - tool failures are data.
            return ToolExecutionResult(
                name=name,
                ok=False,
                error=f"{type(error).__name__}:{error}",
            )

    async def call_async(
        self,
        name: str,
        arguments: Mapping[str, object] | None = None,
    ) -> ToolExecutionResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolExecutionResult(
                name=name,
                ok=False,
                error=f"unknown_tool:{name}",
            )
        try:
            value = tool.handler(dict(arguments or {}))
            if inspect.isawaitable(value):
                value = await value
            result = _mapping_result(value)
            ok = result.get("ok", True)
            return ToolExecutionResult(
                name=name,
                ok=ok if isinstance(ok, bool) else True,
                result=result,
            )
        except Exception as error:  # noqa: BLE001 - tool failures are data.
            return ToolExecutionResult(
                name=name,
                ok=False,
                error=f"{type(error).__name__}:{error}",
            )

    def call_many(
        self,
        calls: Sequence[ToolCall | Mapping[str, object]],
    ) -> tuple[ToolExecutionResult, ...]:
        """Execute a tool sequence in order; all results are retained.

        Ordering is intentional: a direct action must not race a MicroMachine
        publish or an emergency override. Callers may still compose multiple
        tools in one request.
        """

        results: list[ToolExecutionResult] = []
        for item in calls:
            call = item if isinstance(item, ToolCall) else ToolCall.from_mapping(item)
            results.append(self.call(call.name, call.arguments))
        return tuple(results)

    async def call_many_async(
        self,
        calls: Sequence[ToolCall | Mapping[str, object]],
    ) -> tuple[ToolExecutionResult, ...]:
        normalized = tuple(
            item if isinstance(item, ToolCall) else ToolCall.from_mapping(item)
            for item in calls
        )
        # Calls explicitly marked sequential, emergency calls, and calls that
        # share a conflict key remain ordered. Independent semantic tools run
        # together and results are restored to request order.
        results: list[ToolExecutionResult] = []
        batch: list[ToolCall] = []
        batch_keys: set[tuple[str, str]] = set()

        async def flush() -> None:
            if not batch:
                return
            results.extend(await asyncio.gather(*(self.call_async(call.name, call.arguments) for call in batch)))
            batch.clear()
            batch_keys.clear()

        for call in normalized:
            if _requires_sequential_call(call):
                await flush()
                results.append(await self.call_async(call.name, call.arguments))
            else:
                conflict_keys = _call_conflict_keys(call)
                if conflict_keys.intersection(batch_keys):
                    await flush()
                    results.append(await self.call_async(call.name, call.arguments))
                else:
                    batch.append(call)
                    batch_keys.update(conflict_keys)
        await flush()
        return tuple(results)


@dataclass(frozen=True)
class UnifiedCommandRoute:
    """Routing decision and composable tools selected for one vector."""

    layer: UnifiedCommandLayer
    micro_kind: MicroCommandKind | None
    tool_calls: tuple[ToolCall, ...]
    reason: str
    direct_plan: SC2ExecutionPlan | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "micro_kind": self.micro_kind.value if self.micro_kind else None,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "reason": self.reason,
            "direct_plan": self.direct_plan.to_dict() if self.direct_plan else None,
        }


def route_policy_vector(
    vector: PolicyModulationVector,
    *,
    update_id: str = "",
    current_frame: int = 0,
    include_direct_tool: bool = True,
) -> UnifiedCommandRoute:
    """Build the deterministic macro/micro route for one validated vector."""

    if not isinstance(vector, PolicyModulationVector):
        raise TypeError("route_policy_vector requires a PolicyModulationVector.")
    common = {
        "vector": vector.to_dict(),
        "update_id": update_id,
        "current_frame": current_frame,
    }
    if vector.command_layer is CommandLayer.MACRO:
        return UnifiedCommandRoute(
            layer=UnifiedCommandLayer.MACRO,
            micro_kind=None,
            tool_calls=(ToolCall("micromachine.policy", common),),
            reason="macro commands adjust MicroMachine manager weights and policy.",
        )

    if vector.override_level.value == "emergency" or any(
        (
            vector.emergency.cancel_attacks,
            vector.emergency.force_retreat,
            vector.emergency.hold_position,
        )
    ):
        kind = MicroCommandKind.EMERGENCY
    elif vector.tactical_task.task_type == "execute_ability":
        kind = MicroCommandKind.ABILITY
    else:
        kind = MicroCommandKind.OPERATION

    direct_plan = build_direct_plan_from_vector(vector)
    # Non-macro commands are authoritative direct actions. MicroMachine is
    # deliberately absent from this call list; only macro publishes bias.
    calls: list[ToolCall] = []
    if include_direct_tool and direct_plan is not None:
        calls.append(
            ToolCall(
                "sc2.direct.execute",
                {
                    "plan": direct_plan.to_dict(),
                    "update_id": update_id,
                    "command_id": update_id or direct_plan.audit.get("command_id", ""),
                    "current_frame": current_frame,
                },
            )
        )
    return UnifiedCommandRoute(
        layer=UnifiedCommandLayer.MICRO,
        micro_kind=kind,
        tool_calls=tuple(calls),
        direct_plan=direct_plan,
        reason=(
            f"micro command routed as {kind.value}; direct SC2 owns the "
            "explicit action until completion, cancellation, failure, or TTL expiry."
        ),
    )


def build_direct_plan_from_vector(
    vector: PolicyModulationVector,
) -> SC2ExecutionPlan | None:
    """Lower operation, ability, and emergency intent to semantic SC2 actions."""

    actions: list[SC2CommandAction] = []
    notes = [
        "Generated by unified command routing.",
        "Direct execution requires an attached python-sc2 BotAI runtime.",
    ]
    for operation in vector.operations:
        _append_task_actions(
            actions,
            operation.tactical_task,
            scope=operation.scope,
            requirements=operation.composition_requirements or vector.composition_requirements,
            metadata={"operation_id": operation.operation_id},
        )

    task = vector.tactical_task
    if not vector.operations:
        _append_task_actions(
            actions,
            task,
            scope=vector.scope,
            requirements=vector.composition_requirements,
            metadata={"source": "tactical_task"},
        )

    # Building tasks are explicit one-off actions, unlike the standing
    # production/tech biases carried by the macro vector.
    for building in vector.building_tasks:
        target = building.placement_intent or building.anchor or "self_main"
        metadata: dict[str, object] = {
            "building_type": building.building_type,
            "placement_intent": building.placement_intent,
            "anchor": building.anchor,
            "offset_direction": building.offset_direction,
            "allow_nearest_valid_fallback": building.allow_nearest_valid_fallback,
            "placement_policy": {
                "placement_intent": building.placement_intent,
                "anchor": building.anchor,
                "anchor_target": building.anchor,
                "offset_direction": building.offset_direction,
                "allow_nearest_valid_fallback": building.allow_nearest_valid_fallback,
            },
        }
        if building.target_position:
            metadata["target_position"] = list(building.target_position)
            target = "explicit_coordinate"
        for _ in range(building.count):
            actions.append(
                SC2CommandAction(
                    action_type=SC2ActionType.BUILD_STRUCTURE,
                    subject=_direct_structure_subject(building.building_type),
                    target=target,
                    count=1,
                    priority=100,
                    metadata=metadata,
                )
            )

    if vector.override_level.value == "emergency" or any(
        (
            vector.emergency.cancel_attacks,
            vector.emergency.force_retreat,
            vector.emergency.hold_position,
        )
    ):
        if vector.emergency.cancel_attacks:
            actions.append(
                SC2CommandAction(
                    action_type=SC2ActionType.STOP_GROUP,
                    subject="available combat units",
                    count=0,
                    priority=100,
                    metadata={"emergency": True},
                )
            )
        if vector.emergency.hold_position:
            actions.append(
                SC2CommandAction(
                    action_type=SC2ActionType.HOLD_POSITION,
                    subject="available combat units",
                    count=0,
                    priority=100,
                    metadata={"emergency": True},
                )
            )
        if vector.emergency.force_retreat or not actions:
            actions.append(
                SC2CommandAction(
                    action_type=SC2ActionType.MOVE_GROUP,
                    subject="available combat units",
                    target="self_main",
                    count=0,
                    priority=100,
                    metadata={"emergency": True, "retreat": True},
                )
            )

    if not actions:
        return None
    return SC2ExecutionPlan(
        intent_name=f"unified:{vector.command_layer.value}:{task.task_type or 'policy'}",
        priority="urgent" if vector.override_level.value == "emergency" else "high",
        ordered_actions=tuple(actions),
        constraints=tuple(item.key for item in vector.constraints),
        requires_live_sc2=True,
        notes=tuple(notes),
        audit={
            "source": "unified_command_router",
            "command_id": vector.goal or "direct-command",
            "ttl_seconds": vector.ttl_seconds,
            "completion_conditions": list(
                tuple(
                    condition
                    for operation in vector.operations
                    for condition in operation.lifetime.completion_conditions
                )
                or vector.lifetime.completion_conditions
                or ("order_issued",)
            ),
        },
    )


def _append_task_actions(
    actions: list[SC2CommandAction],
    task: object,
    *,
    scope: object,
    requirements: Sequence[object],
    metadata: Mapping[str, object],
) -> None:
    """Lower one validated tactical task without leaking manager-only fields."""

    task_type = str(getattr(task, "task_type", "") or "")
    target = str(
        getattr(task, "location_intent", "")
        or getattr(scope, "location_intent", "")
        or "enemy_natural"
    )
    priority = int(max(1.0, float(getattr(task, "priority", 0.0) or 0.0)) * 100)
    min_units = int(
        getattr(scope, "min_units", 0) or getattr(task, "min_units", 0) or 1
    )
    if task_type == "execute_ability":
        units = (
            tuple(getattr(task, "unit_classes", ()))
            or tuple(getattr(scope, "unit_classes", ()))
            or ("available combat units",)
        )
        actions.append(
            SC2CommandAction(
                action_type=SC2ActionType.EXECUTE_ABILITY,
                subject=_direct_unit_subject(units[0]),
                target=target,
                count=min_units,
                priority=priority,
                metadata={**metadata, "ability": getattr(task, "ability", "")},
            )
        )
        return

    direct_action_types = {
        "assign_workers": SC2ActionType.ASSIGN_WORKERS,
        "build_structure": SC2ActionType.BUILD_STRUCTURE,
        "train_unit": SC2ActionType.TRAIN_UNIT,
        "repair": SC2ActionType.REPAIR,
        "move_group": SC2ActionType.MOVE_GROUP,
        "attack_move": SC2ActionType.ATTACK_MOVE,
        "move_camera": SC2ActionType.MOVE_CAMERA,
        "observe": SC2ActionType.OBSERVE,
        "stop_group": SC2ActionType.STOP_GROUP,
        "hold_position": SC2ActionType.HOLD_POSITION,
    }
    if task_type in direct_action_types:
        unit_classes = tuple(getattr(task, "unit_classes", ()))
        production_targets = tuple(getattr(task, "production_targets", ()))
        subject = _direct_unit_subject(unit_classes[0]) if unit_classes else "available combat units"
        action_metadata = dict(metadata)
        if task_type == "build_structure" and production_targets:
            subject = _direct_structure_subject(production_targets[0])
            action_metadata.setdefault("building_type", subject)
        elif task_type == "train_unit" and production_targets:
            subject = _direct_unit_subject(production_targets[0])
            action_metadata.setdefault(
                "producer",
                {
                    "SCV": "COMMANDCENTER",
                    "MARINE": "BARRACKS",
                    "MARAUDER": "BARRACKS",
                    "REAPER": "BARRACKS",
                    "GHOST": "BARRACKS",
                    "HELLION": "FACTORY",
                    "SIEGETANK": "FACTORY",
                    "CYCLONE": "FACTORY",
                    "THOR": "FACTORY",
                    "MEDIVAC": "STARPORT",
                    "VIKINGFIGHTER": "STARPORT",
                    "LIBERATOR": "STARPORT",
                    "BANSHEE": "STARPORT",
                    "RAVEN": "STARPORT",
                    "BATTLECRUISER": "STARPORT",
                }.get(subject, ""),
            )
        elif task_type == "assign_workers":
            subject = "SCV"
        elif task_type == "repair":
            subject = "SCV"
        count = 0 if task_type in {"observe", "move_camera"} else min_units
        actions.append(
            SC2CommandAction(
                action_type=direct_action_types[task_type],
                subject=subject,
                target=target,
                count=count,
                priority=priority,
                metadata=action_metadata,
            )
        )
        return

    action_type = _operation_action_type(task_type)
    composition = tuple(requirements)
    if composition:
        for requirement in composition:
            actions.append(
                SC2CommandAction(
                    action_type=action_type,
                    subject=_direct_unit_subject(getattr(requirement, "unit_type", "")),
                    target=target,
                    count=int(getattr(requirement, "count", 1)),
                    priority=priority,
                    metadata=dict(metadata),
                )
            )
        return
    # Tasks without a recognized tactical kind are not executable direct
    # actions; they remain macro/manager concerns and must not become a fake
    # attack order.
    if task_type in {
        "scout_with_units",
        "pressure_with_main_army",
        "defend_with_units",
        "harass_with_units",
        "regroup_with_units",
    }:
        unit_classes = tuple(getattr(task, "unit_classes", ())) or tuple(
            getattr(scope, "unit_classes", ())
        )
        actions.append(
            SC2CommandAction(
                action_type=action_type,
                subject=(
                    _direct_unit_subject(unit_classes[0])
                    if unit_classes
                    else "available combat units"
                ),
                target=target,
                count=min_units,
                priority=priority,
                metadata=dict(metadata),
            )
        )


def _operation_action_type(task_type: str) -> SC2ActionType:
    """Map an intermediate squad task to a direct semantic SC2 action."""

    if task_type in {"scout_with_units", "defend_with_units", "regroup_with_units"}:
        return SC2ActionType.MOVE_GROUP
    return SC2ActionType.ATTACK_MOVE


def _direct_unit_subject(unit_type: str) -> str:
    """Convert provider Terran tokens to the adapter's semantic unit names."""

    normalized = str(unit_type or "").strip().upper()
    aliases = {
        "TERRAN_SCV": "SCV",
        "TERRAN_MARINE": "MARINE",
        "TERRAN_HELLION": "HELLION",
        # Brood War terminology is accepted at the semantic boundary but
        # python-sc2's Terran equivalent is the Hellion unit.
        "TERRAN_VULTURE": "HELLION",
        "TERRAN_GHOST": "GHOST",
    }
    return aliases.get(normalized, str(unit_type or "available combat units"))


def _direct_structure_subject(building_type: str) -> str:
    """Convert provider Terran structure tokens to adapter names."""

    normalized = str(building_type or "").strip().upper()
    aliases = {
        "TERRAN_SUPPLYDEPOT": "SUPPLYDEPOT",
        "TERRAN_COMMANDCENTER": "COMMANDCENTER",
        "TERRAN_REFINERY": "REFINERY",
        "TERRAN_BARRACKS": "BARRACKS",
        "TERRAN_FACTORY": "FACTORY",
        "TERRAN_STARPORT": "STARPORT",
        "TERRAN_ENGINEERINGBAY": "ENGINEERINGBAY",
        "TERRAN_ARMORY": "ARMORY",
        "TERRAN_GHOSTACADEMY": "GHOSTACADEMY",
        "TERRAN_FUSIONCORE": "FUSIONCORE",
        "TERRAN_BUNKER": "BUNKER",
    }
    return aliases.get(normalized, str(building_type or ""))


def create_command_tool_registry(
    *,
    micromachine_publish: Callable[[Mapping[str, object]], object] | None = None,
    direct_executor: object | None = None,
    direct_bot: object | None = None,
    lifecycle: DirectCommandLifecycle | None = None,
    command_registry: DirectCommandRegistry | None = None,
) -> CommanderToolRegistry:
    """Create the built-in MicroMachine and direct SC2 tool set."""

    def publish(arguments: Mapping[str, object]) -> Mapping[str, object]:
        if micromachine_publish is None:
            return {"ok": False, "status": "micromachine_runtime_unavailable"}
        value = micromachine_publish(arguments)
        return _mapping_result(value)

    command_lifecycle = lifecycle or DirectCommandLifecycle()
    semantic_registry = command_registry or DirectCommandRegistry()

    async def direct_execute(arguments: Mapping[str, object]) -> Mapping[str, object]:
        if direct_executor is None:
            return {
                "ok": False,
                "status": "runtime_not_attached",
                "reason": "No live python-sc2 BotAI runtime is attached.",
            }
        if hasattr(direct_executor, "bot") and getattr(direct_executor, "bot") is None:
            return {
                "ok": False,
                "status": "runtime_not_attached",
                "reason": "The SC2 executor has no bound BotAI runtime.",
            }
        plan = execution_plan_from_mapping(arguments.get("plan"))
        command_id = str(
            arguments.get("command_id")
            or plan.audit.get("command_id")
            or f"direct-{uuid.uuid4().hex}"
        )
        raw_ttl = arguments.get("ttl_seconds")
        if raw_ttl is None:
            raw_ttl = plan.audit.get("ttl_seconds", 120)
        ttl_seconds = int(raw_ttl)
        raw_conditions = arguments.get(
            "completion_conditions",
            plan.audit.get("completion_conditions", ("order_issued",)),
        )
        conditions = (
            tuple(str(item) for item in raw_conditions)
            if isinstance(raw_conditions, Sequence)
            and not isinstance(raw_conditions, (str, bytes))
            else ("order_issued",)
        )
        lifecycle_required = bool(plan.actions) and not all(
            action.action_type is SC2ActionType.OBSERVE for action in plan.actions
        )
        # Register before dispatch so a game-loop observer can see the full
        # pending -> dispatched -> active transition.  Runtime-not-attached
        # exits above intentionally create no lease and no ownership.
        lease = None
        if lifecycle_required:
            subjects = tuple(action.subject for action in plan.actions)
            command_metadata: dict[str, object] = {
                "intent_name": plan.intent_name,
                "actions": [action.to_dict() for action in plan.actions],
            }
            baseline_provider = getattr(direct_executor, "direct_command_baseline", None)
            if callable(baseline_provider):
                try:
                    baseline = baseline_provider(plan)
                except Exception:  # noqa: BLE001 - optional evidence only.
                    baseline = {}
                if isinstance(baseline, Mapping):
                    command_metadata["baseline"] = dict(baseline)
            try:
                lease = command_lifecycle.pending(
                    command_id=command_id,
                    issued_at_frame=int(arguments.get("current_frame", 0)),
                    ttl_seconds=max(1, ttl_seconds),
                    completion_conditions=conditions,
                    owned_subjects=subjects,
                    command_metadata=command_metadata,
                )
            except DirectCommandOwnershipConflict as error:
                return {
                    "ok": False,
                    "status": "direct_control_conflict",
                    "runtime_attached": True,
                    "reason": str(error),
                }
        execute = getattr(direct_executor, "execute", None)
        try:
            if not callable(execute):
                execute = getattr(direct_executor, "execute_plan", None)
                if callable(execute) and direct_bot is not None:
                    value = execute(direct_bot, plan)
                else:
                    failed = (
                        command_lifecycle.fail(
                            command_id,
                            reason="direct_executor_missing_capability",
                        )
                        if lifecycle_required
                        else None
                    )
                    return {
                        "ok": False,
                        "status": "direct_executor_missing_capability",
                        "reason": (
                            "The attached direct executor exposes neither execute "
                            "nor execute_plan."
                        ),
                        "lifecycle": failed.to_dict() if failed else {},
                    }
            else:
                value = execute(plan)
            if inspect.isawaitable(value):
                value = await value
        except asyncio.CancelledError:
            if lifecycle_required:
                command_lifecycle.cancel(command_id, reason="dispatch_cancelled")
            raise
        except Exception as error:  # noqa: BLE001 - runtime failures are data.
            failed = (
                command_lifecycle.fail(
                    command_id,
                    reason="direct_execution_exception",
                    evidence={"error": f"{type(error).__name__}:{error}"},
                )
                if lifecycle_required
                else None
            )
            return {
                "ok": False,
                "status": "direct_execution_failed",
                "runtime_attached": True,
                "lifecycle": failed.to_dict() if failed else {},
                "error": f"{type(error).__name__}:{error}",
            }
        if isinstance(value, SC2PlanExecutionResult):
            audit = dict(value.to_dict().get("audit", {}))
            if lifecycle_required:
                lease = command_lifecycle.mark_dispatched(command_id)
            if value.success and lifecycle_required:
                lease = command_lifecycle.activate(command_id)
                # An issued order is immediate evidence.  Keep it in the
                # lease so a later game-loop tick can combine it with
                # target/build/ability observations for compound conditions.
                if "order_issued" in lease.completion_conditions:
                    lease = command_lifecycle.record_evidence(
                        command_id,
                        {"order_issued": True},
                    )
            if not value.success and lifecycle_required:
                lease = command_lifecycle.fail(
                    command_id,
                    reason="direct_plan_refused",
                    evidence=audit,
                )
            if lease is not None:
                audit["direct_command_lifecycle"] = lease.to_dict()
            result_document = value.to_dict()
            result_document["audit"] = audit
            return {
                "ok": value.success,
                "status": "executed" if value.success else "direct_action_refused",
                "runtime_attached": True,
                "result": result_document,
            }
        # The executor contract carries per-action proof. A truthy object or
        # {"ok": true} is not evidence that any SC2 action was issued.
        failed = (
            command_lifecycle.fail(command_id, reason="invalid_executor_result")
            if lifecycle_required else None
        )
        return {
            "ok": False,
            "status": "invalid_executor_result",
            "lifecycle": failed.to_dict() if failed else {},
        }

    async def direct_observe(arguments: Mapping[str, object]) -> Mapping[str, object]:
        if direct_executor is None:
            return {"ok": False, "status": "runtime_not_attached"}
        return await direct_execute(
            {
                "plan": SC2ExecutionPlan(
                    intent_name="unified:observe",
                    priority="normal",
                    ordered_actions=(
                        SC2CommandAction(
                            SC2ActionType.OBSERVE,
                            subject="visible_state",
                            count=0,
                        ),
                    ),
                ).to_dict()
            }
        )

    def direct_lifecycle(arguments: Mapping[str, object]) -> Mapping[str, object]:
        command_id = str(arguments.get("command_id", "")).strip()
        if not command_id:
            raise ValueError("command_id is required")
        action = str(arguments.get("action", "observe")).strip().lower()
        if action == "cancel":
            lease = command_lifecycle.cancel(command_id)
        elif action == "observe":
            evidence = arguments.get("evidence", {})
            if not isinstance(evidence, Mapping):
                raise ValueError("evidence must be an object")
            lease = command_lifecycle.observe(
                command_id,
                frame=int(arguments.get("current_frame", 0)),
                evidence=evidence,
            )
        else:
            raise ValueError("action must be observe or cancel")
        return {"ok": True, "status": lease.state.value, "lifecycle": lease.to_dict()}

    def list_capabilities(arguments: Mapping[str, object]) -> Mapping[str, object]:
        return {
            "ok": True,
            "capabilities": [capability.to_dict() for capability in SC2_CAPABILITY_CATALOG],
            "action_types": sorted(SC2_DIRECT_ACTION_TYPES),
            "registry": semantic_registry.to_dict(),
        }

    schema = {
        "type": "object",
        "properties": {
            "vector": {"type": "object"},
            "update_id": {"type": "string"},
            "current_frame": {"type": "integer", "minimum": 0},
        },
        "required": ["vector"],
        "additionalProperties": False,
    }
    direct_schema = {
        "type": "object",
        "properties": {
            "plan": {"type": "object"},
            "update_id": {"type": "string"},
            "command_id": {"type": "string", "minLength": 1},
            "current_frame": {"type": "integer", "minimum": 0},
            "ttl_seconds": {"type": "integer", "minimum": 1, "maximum": 900},
            "completion_conditions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["plan"],
        "additionalProperties": False,
    }

    async def one_action_tool(
        action_type: SC2ActionType,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        metadata = arguments.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object.")
        subject = str(arguments.get("subject", ""))
        target_pin = str(arguments.get("target_pin", "")).strip()
        if target_pin:
            target = semantic_registry.resolve_target_pin(target_pin).target
        else:
            target = semantic_registry.resolve_target(str(arguments.get("target", "")))
        metadata_map = dict(metadata)
        try:
            squad = semantic_registry.resolve_squad(subject)
        except KeyError:
            squad = None
        if squad is None and semantic_registry.has_squads and _looks_like_named_squad(subject):
            raise ValueError(f"unknown_squad:{subject}")
        if squad is not None:
            subject = squad.unit_query
            metadata_map.setdefault("squad", squad.name)
        plan = SC2ExecutionPlan(
            intent_name=f"mcp:direct:{action_type.value}",
            priority=str(arguments.get("priority_label", "normal")),
            ordered_actions=(
                SC2CommandAction(
                    action_type=action_type,
                    subject=subject,
                    target=target,
                    count=int(arguments.get("count", 1)),
                    priority=int(arguments.get("priority", 50)),
                    metadata=metadata_map,
                ),
            ),
            requires_live_sc2=True,
            audit={"source": "mcp", "tool": action_type.value},
        )
        return await direct_execute(
            {
                "plan": plan.to_dict(),
                "command_id": arguments.get("command_id"),
                "current_frame": arguments.get("current_frame", 0),
                "ttl_seconds": arguments.get("ttl_seconds"),
                "completion_conditions": arguments.get("completion_conditions"),
            }
        )

    action_schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "minLength": 1},
            "target": {"type": "string"},
            "target_pin": {"type": "string", "minLength": 1},
            "count": {"type": "integer", "minimum": 0},
            "priority": {"type": "integer", "minimum": 0, "maximum": 100},
            "priority_label": {"type": "string"},
            "metadata": {"type": "object"},
            "command_id": {"type": "string", "minLength": 1},
            "ttl_seconds": {"type": "integer", "minimum": 1, "maximum": 900},
            "completion_conditions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["subject"],
        "additionalProperties": False,
    }

    def action_tool(
        action_type: SC2ActionType,
    ) -> Callable[[Mapping[str, object]], object]:
        return lambda arguments: one_action_tool(action_type, arguments)
    tools = [
        ToolSpec(
            "micromachine.policy",
            "Publish macro manager weights and policy to MicroMachine.",
            schema,
            publish,
        ),
        ToolSpec(
            "sc2.direct.execute",
            "Execute one or more semantic SC2 API actions through python-sc2.",
            direct_schema,
            direct_execute,
        ),
        ToolSpec(
            "sc2.direct.observe",
            "Read a structured observation from the attached SC2 runtime.",
            {"type": "object", "additionalProperties": False},
            direct_observe,
        ),
        ToolSpec(
            "sc2.direct.lifecycle",
            "Observe, cancel, or expire a direct command lease.",
            {
                "type": "object",
                "properties": {
                    "command_id": {"type": "string", "minLength": 1},
                    "action": {"type": "string", "enum": ["observe", "cancel"]},
                    "current_frame": {"type": "integer", "minimum": 0},
                    "evidence": {"type": "object"},
                },
                "required": ["command_id"],
                "additionalProperties": False,
            },
            direct_lifecycle,
        ),
        ToolSpec(
            "sc2.direct.capabilities",
            "List the bounded semantic SC2 capability catalog.",
            {"type": "object", "additionalProperties": False},
            list_capabilities,
        ),
        ToolSpec(
            "sc2.direct.registry",
            "List named squads and semantic target pins available to Direct SC2 tools.",
            {"type": "object", "additionalProperties": False},
            lambda _arguments: {"ok": True, "registry": semantic_registry.to_dict()},
        ),
        ToolSpec(
            "sc2.direct.assign_workers",
            "Assign workers to a semantic resource target through python-sc2.",
            action_schema,
            action_tool(SC2ActionType.ASSIGN_WORKERS),
        ),
        ToolSpec(
            "sc2.direct.build_structure",
            "Build one semantic structure through python-sc2.",
            action_schema,
            action_tool(SC2ActionType.BUILD_STRUCTURE),
        ),
        ToolSpec(
            "sc2.direct.train_unit",
            "Train semantic units through python-sc2.",
            action_schema,
            action_tool(SC2ActionType.TRAIN_UNIT),
        ),
        ToolSpec(
            "sc2.direct.move_group",
            "Move a semantic squad through python-sc2.",
            action_schema,
            action_tool(SC2ActionType.MOVE_GROUP),
        ),
        ToolSpec(
            "sc2.direct.attack_move",
            "Attack-move a semantic squad through python-sc2.",
            action_schema,
            action_tool(SC2ActionType.ATTACK_MOVE),
        ),
        ToolSpec(
            "sc2.direct.repair",
            "Repair a semantic target through python-sc2.",
            action_schema,
            action_tool(SC2ActionType.REPAIR),
        ),
        ToolSpec(
            "sc2.direct.execute_ability",
            "Use a semantic unit ability through python-sc2.",
            {
                **action_schema,
                "properties": {
                    **action_schema["properties"],
                    "metadata": {
                        "type": "object",
                        "required": ["ability"],
                    },
                },
                "required": ["subject", "metadata"],
            },
            action_tool(SC2ActionType.EXECUTE_ABILITY),
        ),
        ToolSpec(
            "sc2.direct.move_camera",
            "Move the live camera to a semantic target.",
            action_schema,
            action_tool(SC2ActionType.MOVE_CAMERA),
        ),
        ToolSpec(
            "sc2.direct.stop_group",
            "Stop a semantic squad immediately.",
            action_schema,
            action_tool(SC2ActionType.STOP_GROUP),
        ),
        ToolSpec(
            "sc2.direct.hold_position",
            "Hold a semantic squad in place.",
            action_schema,
            action_tool(SC2ActionType.HOLD_POSITION),
        ),
        ToolSpec(
            "sc2.direct.retreat",
            "Move a semantic squad back to the own main base.",
            action_schema,
            lambda arguments: one_action_tool(
                SC2ActionType.MOVE_GROUP,
                {**dict(arguments), "target": arguments.get("target", "self_main")},
            ),
        ),
    ]
    return CommanderToolRegistry(tuple(tools))


def execution_plan_from_mapping(value: object) -> SC2ExecutionPlan:
    """Rehydrate a JSON MCP argument into a validated execution plan."""

    if isinstance(value, SC2ExecutionPlan):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("direct SC2 tool requires a plan object.")
    raw_actions = value.get("ordered_actions", value.get("actions", ()))
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, (str, bytes)):
        raise ValueError("direct SC2 plan actions must be a list.")
    actions = []
    for raw in raw_actions:
        if not isinstance(raw, Mapping):
            raise ValueError("direct SC2 plan action must be an object.")
        actions.append(
            SC2CommandAction(
                action_type=str(raw.get("action_type", "")),
                subject=str(raw.get("subject", "")),
                target=str(raw.get("target", "")),
                count=int(raw.get("count", 1)),
                priority=int(raw.get("priority", 50)),
                constraints=tuple(raw.get("constraints", ())),
                metadata=(
                    raw.get("metadata", {})
                    if isinstance(raw.get("metadata", {}), Mapping)
                    else {}
                ),
            )
        )
    return SC2ExecutionPlan(
        intent_name=str(value.get("intent_name", value.get("intent", "mcp:direct"))),
        priority=value.get("priority_label", "normal"),
        ordered_actions=tuple(actions),
        constraints=tuple(value.get("constraints", ())),
        requires_live_sc2=bool(value.get("requires_live_sc2", True)),
        notes=tuple(value.get("notes", ())),
        audit=value.get("audit", {}) if isinstance(value.get("audit", {}), Mapping) else {},
    )


def _mapping_result(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, SC2PlanExecutionResult):
        return value.to_dict()
    if value is None:
        return {"ok": True}
    return {"ok": bool(value), "value": value}


def _requires_sequential_call(call: ToolCall) -> bool:
    """Return whether a tool must remain ordered with neighboring calls."""

    if call.arguments.get("parallel") is False:
        return True
    if call.name in {
        "sc2.direct.build_structure",
        "sc2.direct.train_unit",
        "sc2.direct.repair",
        "sc2.direct.execute_ability",
        "sc2.direct.stop_group",
        "sc2.direct.hold_position",
        "sc2.direct.retreat",
        "sc2.direct.lifecycle",
    }:
        return True
    if call.name == "sc2.direct.execute":
        plan = call.arguments.get("plan")
        if isinstance(plan, Mapping):
            actions = plan.get("ordered_actions", plan.get("actions", ()))
            if isinstance(actions, Sequence) and len(actions) > 1:
                return True
            if isinstance(actions, Sequence) and any(
                isinstance(action, Mapping)
                and bool((action.get("metadata") or {}).get("emergency"))
                for action in actions
            ):
                return True
    return False


def _looks_like_named_squad(subject: str) -> bool:
    normalized = " ".join(str(subject).strip().casefold().split())
    if not normalized:
        return False
    if any(token in normalized for token in ("분대", "squad", "army group", "주력", "정찰", "방어")):
        return True
    return bool(normalized[0].isdigit() and len(normalized) <= 12)


def _call_conflict_key(call: ToolCall) -> tuple[str, str] | None:
    """Return the semantic ownership key for a potentially conflicting call.

    Two commands addressing the same named squad must not race, while calls
    with different subjects (for example worker assignment and scout move)
    remain eligible for one parallel batch.
    """

    keys = _call_conflict_keys(call)
    return next(iter(keys), None)


def _call_conflict_keys(call: ToolCall) -> set[tuple[str, str]]:
    """Return every semantic ownership key embedded in a direct call."""

    keys: set[tuple[str, str]] = set()
    if call.name.startswith("sc2.direct."):
        subject = str(call.arguments.get("subject", "")).strip().casefold()
        if subject:
            keys.add(("subject", subject))
        if call.name == "sc2.direct.execute" and not subject:
            plan = call.arguments.get("plan")
            if isinstance(plan, Mapping):
                actions = plan.get("ordered_actions", plan.get("actions", ()))
                subjects = {
                    str(action.get("subject", "")).strip().casefold()
                    for action in actions
                    if isinstance(action, Mapping) and str(action.get("subject", "")).strip()
                }
                keys.update(("subject", subject) for subject in subjects)
    return keys


def _run_awaitable_sync(value: object) -> object:
    """Resolve an async tool from a synchronous MCP/web worker boundary."""

    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    # BotAI's websocket and asyncio primitives belong to their runtime loop.
    # Never block that loop or move its coroutine to a temporary thread/loop.
    if inspect.iscoroutine(value):
        value.close()
    raise RuntimeError("async tool handler requires call_async in a running event loop")


def route_and_execute(
    vector: PolicyModulationVector,
    registry: CommanderToolRegistry,
    *,
    update_id: str = "",
    current_frame: int = 0,
    include_direct_tool: bool = True,
) -> dict[str, object]:
    """Build and execute a route, returning a UI/MCP-safe audit document."""

    route = route_policy_vector(
        vector,
        update_id=update_id,
        current_frame=current_frame,
        include_direct_tool=include_direct_tool,
    )
    results = registry.call_many(route.tool_calls)
    return {
        "route": route.to_dict(),
        "tool_results": [result.to_dict() for result in results],
        "ok": bool(results) and all(
            result.ok and result.result.get("ok", True) for result in results
        ),
    }


def serialize_tool_specs(registry: CommanderToolRegistry) -> str:
    """Return deterministic JSON for MCP discovery tests and diagnostics."""

    return json.dumps(
        [tool.to_mcp_dict() for tool in registry.list_tools()],
        ensure_ascii=False,
        sort_keys=True,
    )
