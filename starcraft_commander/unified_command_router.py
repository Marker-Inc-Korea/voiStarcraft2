"""Unified macro/micro routing and composable command tools.

The LLM produces a validated :class:`PolicyModulationVector`. This module is
the single execution boundary after validation:

* ``macro`` publishes manager weights to MicroMachine only.
* every other layer is ``micro`` and is classified as operation, ability, or
  emergency.
* micro routes may include both the existing MicroMachine tool and a direct
  SC2 tool. Direct execution is reported honestly when no live BotAI runtime
  is attached.

The registry is deliberately dependency-light so the same tools can be used by
the web cockpit, tests, or the stdio MCP server.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from starcraft_commander.contracts import (
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
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
                raise RuntimeError(
                    "async tool handler requires call_async; "
                    f"tool={name}"
                )
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
        results: list[ToolExecutionResult] = []
        for item in calls:
            call = item if isinstance(item, ToolCall) else ToolCall.from_mapping(item)
            results.append(await self.call_async(call.name, call.arguments))
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
        mm_tool = "micromachine.emergency"
    elif vector.tactical_task.task_type == "execute_ability":
        kind = MicroCommandKind.ABILITY
        mm_tool = "micromachine.ability"
    else:
        kind = MicroCommandKind.OPERATION
        mm_tool = "micromachine.operation"

    direct_plan = build_direct_plan_from_vector(vector)
    calls = [ToolCall(mm_tool, common)]
    if include_direct_tool and direct_plan is not None:
        calls.append(
            ToolCall(
                "sc2.direct.execute",
                {
                    "plan": direct_plan.to_dict(),
                    "update_id": update_id,
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
            f"micro command routed as {kind.value}; "
            "MicroMachine remains the autonomous policy path and direct SC2 "
            "is an additional explicit tool when a live adapter is attached."
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
        task = operation.tactical_task
        target = task.location_intent or operation.scope.location_intent or "enemy_natural"
        requirements = operation.composition_requirements or vector.composition_requirements
        if requirements:
            for requirement in requirements:
                action_type = _operation_action_type(task.task_type)
                actions.append(
                    SC2CommandAction(
                        action_type=action_type,
                        subject=_direct_unit_subject(requirement.unit_type),
                        target=target,
                        count=requirement.count,
                        priority=int(max(1.0, task.priority) * 100),
                        metadata={"operation_id": operation.operation_id},
                    )
                )
        else:
            actions.append(
                SC2CommandAction(
                    action_type=_operation_action_type(task.task_type),
                    subject="available combat units",
                    target=target,
                    count=max(1, operation.scope.min_units or task.min_units or 1),
                    priority=int(max(1.0, task.priority) * 100),
                    metadata={"operation_id": operation.operation_id},
                )
            )

    task = vector.tactical_task
    if task.task_type == "execute_ability":
        units = task.unit_classes or vector.scope.unit_classes or ("available combat units",)
        actions.append(
            SC2CommandAction(
                action_type=SC2ActionType.EXECUTE_ABILITY,
                subject=_direct_unit_subject(units[0]),
                target=task.location_intent,
                count=max(1, vector.scope.min_units or task.min_units or 1),
                priority=int(max(1.0, task.priority) * 100),
                metadata={"ability": task.ability},
            )
        )

    if vector.override_level.value == "emergency" or any(
        (
            vector.emergency.cancel_attacks,
            vector.emergency.force_retreat,
            vector.emergency.hold_position,
        )
    ):
        actions.append(
            SC2CommandAction(
                action_type=SC2ActionType.MOVE_GROUP,
                subject="available combat units",
                target="self_main",
                count=1,
                priority=100,
                metadata={"emergency": True},
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
        audit={"source": "unified_command_router"},
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
        "TERRAN_HELLION": "VULTURE",
        "TERRAN_VULTURE": "VULTURE",
        "TERRAN_GHOST": "GHOST",
    }
    return aliases.get(normalized, str(unit_type or "available combat units"))


def create_command_tool_registry(
    *,
    micromachine_publish: Callable[[Mapping[str, object]], object] | None = None,
    direct_executor: object | None = None,
    direct_bot: object | None = None,
) -> CommanderToolRegistry:
    """Create the built-in MicroMachine and direct SC2 tool set."""

    def publish(arguments: Mapping[str, object]) -> Mapping[str, object]:
        if micromachine_publish is None:
            return {"ok": False, "status": "micromachine_runtime_unavailable"}
        value = micromachine_publish(arguments)
        return _mapping_result(value)

    def direct_execute(arguments: Mapping[str, object]) -> Mapping[str, object]:
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
        execute = getattr(direct_executor, "execute", None)
        if not callable(execute):
            execute = getattr(direct_executor, "execute_plan", None)
            if callable(execute) and direct_bot is not None:
                value = execute(direct_bot, plan)
            else:
                return {
                    "ok": False,
                    "status": "direct_executor_missing_capability",
                    "reason": (
                        "The attached direct executor exposes neither execute "
                        "nor execute_plan."
                    ),
                }
        else:
            value = execute(plan)
        if inspect.isawaitable(value):
            value = _run_awaitable_sync(value)
        if isinstance(value, SC2PlanExecutionResult):
            return {
                "ok": value.success,
                "status": "executed" if value.success else "direct_action_refused",
                "runtime_attached": True,
                "result": value.to_dict(),
            }
        return _mapping_result(value)

    def direct_observe(arguments: Mapping[str, object]) -> Mapping[str, object]:
        if direct_executor is None:
            return {"ok": False, "status": "runtime_not_attached"}
        return direct_execute(
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
            "current_frame": {"type": "integer", "minimum": 0},
        },
        "required": ["plan"],
        "additionalProperties": False,
    }

    def one_action_tool(
        action_type: SC2ActionType,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        metadata = arguments.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object.")
        plan = SC2ExecutionPlan(
            intent_name=f"mcp:direct:{action_type.value}",
            priority=str(arguments.get("priority_label", "normal")),
            ordered_actions=(
                SC2CommandAction(
                    action_type=action_type,
                    subject=str(arguments.get("subject", "")),
                    target=str(arguments.get("target", "")),
                    count=int(arguments.get("count", 1)),
                    priority=int(arguments.get("priority", 50)),
                    metadata=metadata,
                ),
            ),
            requires_live_sc2=True,
            audit={"source": "mcp", "tool": action_type.value},
        )
        return direct_execute({"plan": plan.to_dict()})

    action_schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "minLength": 1},
            "target": {"type": "string"},
            "count": {"type": "integer", "minimum": 0},
            "priority": {"type": "integer", "minimum": 0, "maximum": 100},
            "priority_label": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "required": ["subject"],
        "additionalProperties": False,
    }

    def action_tool(
        action_type: SC2ActionType,
    ) -> Callable[[Mapping[str, object]], object]:
        return lambda arguments: one_action_tool(action_type, arguments)
    tools = (
        ToolSpec(
            "micromachine.policy",
            "Publish macro manager weights and policy to MicroMachine.",
            schema,
            publish,
        ),
        ToolSpec(
            "micromachine.operation",
            "Publish a squad operation to MicroMachine operation ownership.",
            schema,
            publish,
        ),
        ToolSpec(
            "micromachine.ability",
            "Publish a bounded ability policy to MicroMachine.",
            schema,
            publish,
        ),
        ToolSpec(
            "micromachine.emergency",
            "Publish an emergency retreat or stop override to MicroMachine.",
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
    )
    return CommanderToolRegistry(tools)


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


def _run_awaitable_sync(value: object) -> object:
    """Resolve an async tool from a synchronous MCP/web worker boundary."""

    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    raise RuntimeError(
        "async direct SC2 execution must use call_async from an active event loop."
    )


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
        "ok": all(result.ok and result.result.get("ok", True) for result in results),
    }


def serialize_tool_specs(registry: CommanderToolRegistry) -> str:
    """Return deterministic JSON for MCP discovery tests and diagnostics."""

    return json.dumps(
        [tool.to_mcp_dict() for tool in registry.list_tools()],
        ensure_ascii=False,
        sort_keys=True,
    )
