"""Bounded System 1 -> JEV route authorization contract.

JEV is intentionally an injected boundary.  This repository does not assume a
network endpoint, authentication scheme, or private protocol.  When an
endpoint is supplied, it may authorize or deny the already-lowered local route;
it may not provide raw python-sc2 method calls, a replacement policy vector, or
an arbitrary execution plan.  The existing deterministic router therefore
remains authoritative for command-layer classification and tool lowering.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol

from starcraft_commander.policy_modulation import CommandLayer, PolicyModulationVector
from starcraft_commander.unified_command_router import UnifiedCommandRoute, route_policy_vector


JEV_PROTOCOL_VERSION: Final[str] = "jev.system1.route/v1"
JEV_DEFAULT_TIMEOUT_SECONDS: Final[float] = 5.0
JEV_DEFAULT_RETRIES: Final[int] = 1
JEV_COMMAND_LAYERS: Final[frozenset[str]] = frozenset(
    {layer.value for layer in CommandLayer}
)
JEV_DIRECT_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"sc2.direct.execute"}
)
JEV_ALLOWED_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {*JEV_DIRECT_TOOL_NAMES, "micromachine.policy"}
)


class JEVError(RuntimeError):
    """Base error for bounded JEV authorization failures."""


class JEVUnavailableError(JEVError):
    """Raised when a configured JEV endpoint cannot be reached."""


class JEVInvalidDecisionError(JEVError):
    """Raised when JEV returns a malformed or unsafe decision."""


class JEVDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class JEVEndpoint(Protocol):
    """Minimal transport-independent endpoint contract used by the router."""

    def decide(self, request: Mapping[str, object]) -> object | Awaitable[object]:
        """Return one JSON-like authorization response."""


@dataclass(frozen=True)
class JEVRouteRequest:
    """Auditable request sent to an injected JEV endpoint."""

    request_id: str
    command_layer: str
    vector: Mapping[str, object]
    route: Mapping[str, object]
    eligible_layers: frozenset[str]
    allowed_tools: frozenset[str] = JEV_DIRECT_TOOL_NAMES
    protocol_version: str = JEV_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        request_id = str(self.request_id).strip()
        layer = str(self.command_layer).strip().casefold()
        if not request_id:
            raise ValueError("JEV request_id must be non-empty")
        if layer not in JEV_COMMAND_LAYERS:
            raise ValueError(f"unsupported JEV command layer: {layer}")
        if not isinstance(self.vector, Mapping):
            raise TypeError("JEV vector must be a mapping")
        if not isinstance(self.route, Mapping):
            raise TypeError("JEV route must be a mapping")
        eligible = frozenset(str(value).strip().casefold() for value in self.eligible_layers)
        if not eligible.issubset(JEV_COMMAND_LAYERS):
            raise ValueError("JEV eligible_layers contains an unsupported layer")
        tools = frozenset(str(value).strip() for value in self.allowed_tools)
        if not tools.issubset(JEV_ALLOWED_TOOL_NAMES):
            raise ValueError("JEV allowed_tools contains an unsupported tool")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "command_layer", layer)
        object.__setattr__(self, "vector", dict(self.vector))
        object.__setattr__(self, "route", dict(self.route))
        object.__setattr__(self, "eligible_layers", eligible)
        object.__setattr__(self, "allowed_tools", tools)
        object.__setattr__(self, "protocol_version", str(self.protocol_version))

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "command_layer": self.command_layer,
            "vector": dict(self.vector),
            "route": dict(self.route),
            "eligible_layers": sorted(self.eligible_layers),
            "allowed_tools": sorted(self.allowed_tools),
        }


@dataclass(frozen=True)
class JEVRouteDecision:
    """Validated JEV authorization response and local routing evidence."""

    request_id: str
    status: str
    decision: JEVDecision | None = None
    reason: str = ""
    route: UnifiedCommandRoute | None = None
    audit: Mapping[str, object] = field(default_factory=dict)
    error: str = ""

    @property
    def allowed(self) -> bool:
        return self.status in {"disabled", "not_eligible", "allowed"}

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "allowed": self.allowed,
            "decision": self.decision.value if self.decision else None,
            "reason": self.reason,
            "route": self.route.to_dict() if self.route else None,
            "audit": dict(self.audit),
            "error": self.error,
        }


_ALLOWED_RESPONSE_KEYS: Final[frozenset[str]] = frozenset(
    {"protocol_version", "request_id", "decision", "reason", "audit"}
)


class JEVRouter:
    """Authorize deterministic local routes through an optional JEV endpoint."""

    def __init__(
        self,
        endpoint: JEVEndpoint | Callable[[Mapping[str, object]], object] | None = None,
        *,
        eligible_layers: Sequence[str] = (),
        timeout_seconds: float = JEV_DEFAULT_TIMEOUT_SECONDS,
        retries: int = JEV_DEFAULT_RETRIES,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("JEV timeout_seconds must be positive")
        if type(retries) is not int or retries < 0 or retries > 3:
            raise ValueError("JEV retries must be an integer between 0 and 3")
        normalized = frozenset(str(value).strip().casefold() for value in eligible_layers)
        if not normalized.issubset(JEV_COMMAND_LAYERS):
            raise ValueError("JEV eligible_layers contains an unsupported layer")
        self.endpoint = endpoint
        self.eligible_layers = normalized
        self.timeout_seconds = float(timeout_seconds)
        self.retries = retries

    async def authorize(
        self,
        vector: PolicyModulationVector,
        *,
        request_id: str = "",
        current_frame: int = 0,
        include_direct_tool: bool = True,
    ) -> JEVRouteDecision:
        """Build the local route and optionally authorize it through JEV."""

        route = route_policy_vector(
            vector,
            update_id=request_id,
            current_frame=current_frame,
            include_direct_tool=include_direct_tool,
        )
        resolved_id = str(request_id or route.direct_plan.audit.get("command_id", "") or "jev-route")
        layer = vector.command_layer.value
        if self.endpoint is None:
            return JEVRouteDecision(
                request_id=resolved_id,
                status="disabled",
                reason="JEV endpoint is not configured; local route remains authoritative.",
                route=route,
                audit={"jev_enabled": False, "command_layer": layer},
            )
        if layer not in self.eligible_layers:
            return JEVRouteDecision(
                request_id=resolved_id,
                status="not_eligible",
                reason=f"JEV is not enabled for command layer {layer}.",
                route=route,
                audit={"jev_enabled": True, "command_layer": layer},
            )
        route_tools = frozenset(call.name for call in route.tool_calls)
        request = JEVRouteRequest(
            request_id=resolved_id,
            command_layer=layer,
            vector=vector.to_dict(),
            route=route.to_dict(),
            eligible_layers=self.eligible_layers,
            allowed_tools=route_tools,
        )
        try:
            response = await self._call_endpoint(request.to_dict())
        except JEVUnavailableError as error:
            return JEVRouteDecision(
                request_id=resolved_id,
                status="unavailable",
                reason="JEV authorization failed closed.",
                audit={"jev_enabled": True, "command_layer": layer},
                error=str(error),
            )
        try:
            decision = self._validate_response(response, request)
        except JEVInvalidDecisionError as error:
            return JEVRouteDecision(
                request_id=resolved_id,
                status="invalid",
                reason="JEV returned an invalid decision.",
                route=None,
                audit={"jev_enabled": True, "command_layer": layer},
                error=str(error),
            )
        if decision is JEVDecision.DENY:
            return JEVRouteDecision(
                request_id=resolved_id,
                status="denied",
                decision=decision,
                reason=str(response.get("reason", "JEV denied the route.")),
                route=None,
                audit={"jev_enabled": True, "command_layer": layer},
            )
        return JEVRouteDecision(
            request_id=resolved_id,
            status="allowed",
            decision=decision,
            reason=str(response.get("reason", "JEV authorized the deterministic local route.")),
            route=route,
            audit={
                "jev_enabled": True,
                "command_layer": layer,
                "jev_decision": decision.value,
                "jev_response_audit": dict(response.get("audit", {})),
            },
        )

    async def _call_endpoint(self, request: Mapping[str, object]) -> Mapping[str, object]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                target = self.endpoint.decide if hasattr(self.endpoint, "decide") else self.endpoint
                # Run synchronous injected transports off the event loop so the
                # same timeout contract applies to both sync and async endpoints.
                if inspect.iscoroutinefunction(target):
                    value = target(request)  # type: ignore[misc]
                    value = await asyncio.wait_for(value, timeout=self.timeout_seconds)
                else:
                    value = await asyncio.wait_for(
                        asyncio.to_thread(target, request), timeout=self.timeout_seconds  # type: ignore[misc]
                    )
                    if inspect.isawaitable(value):
                        value = await asyncio.wait_for(value, timeout=self.timeout_seconds)
                if not isinstance(value, Mapping):
                    raise JEVInvalidDecisionError("JEV response must be an object")
                return dict(value)
            except JEVInvalidDecisionError:
                raise
            except (asyncio.TimeoutError, TimeoutError) as error:
                last_error = error
            except Exception as error:  # noqa: BLE001 - transport failures are data.
                last_error = error
            if attempt < self.retries:
                await asyncio.sleep(0)
        raise JEVUnavailableError(
            f"JEV endpoint unavailable after {self.retries + 1} attempt(s): {last_error}"
        )

    @staticmethod
    def _validate_response(
        response: Mapping[str, object],
        request: JEVRouteRequest,
    ) -> JEVDecision:
        if response.get("protocol_version", JEV_PROTOCOL_VERSION) != JEV_PROTOCOL_VERSION:
            raise JEVInvalidDecisionError("JEV protocol_version mismatch")
        if str(response.get("request_id", "")) != request.request_id:
            raise JEVInvalidDecisionError("JEV request_id mismatch")
        unknown = set(response).difference(_ALLOWED_RESPONSE_KEYS)
        if unknown:
            raise JEVInvalidDecisionError(
                "JEV response contains unsupported fields: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        try:
            decision = JEVDecision(str(response["decision"]).strip().casefold())
        except (KeyError, ValueError) as error:
            raise JEVInvalidDecisionError("JEV decision must be 'allow' or 'deny'") from error
        audit = response.get("audit", {})
        if not isinstance(audit, Mapping):
            raise JEVInvalidDecisionError("JEV audit must be an object")
        return decision


async def route_policy_vector_with_jev(
    vector: PolicyModulationVector,
    *,
    endpoint: JEVEndpoint | Callable[[Mapping[str, object]], object] | None = None,
    eligible_layers: Sequence[str] = (),
    request_id: str = "",
    current_frame: int = 0,
    include_direct_tool: bool = True,
    timeout_seconds: float = JEV_DEFAULT_TIMEOUT_SECONDS,
    retries: int = JEV_DEFAULT_RETRIES,
) -> JEVRouteDecision:
    """Convenience wrapper for the injected JEV authorization boundary."""

    router = JEVRouter(
        endpoint,
        eligible_layers=eligible_layers,
        timeout_seconds=timeout_seconds,
        retries=retries,
    )
    try:
        return await router.authorize(
            vector,
            request_id=request_id,
            current_frame=current_frame,
            include_direct_tool=include_direct_tool,
        )
    except JEVUnavailableError as error:
        return JEVRouteDecision(
            request_id=str(request_id or "jev-route"),
            status="unavailable",
            reason="JEV authorization failed closed.",
            audit={"jev_enabled": True},
            error=str(error),
        )
