"""Execute a validated route sketch through the SC2 API only, visibly, under a Direct lease.

* The squad is leased (``DirectCommandLifecycle``) for the whole transit, so MicroMachine does not re-command those units.
* Orders go through the real executor/adapter: ``MOVE(w1)``, ``MOVE(wk, queue=True)``..., ``MOVE|ATTACK(final, queue=True)``.
  Route points are registered in the adapter under ``route:<plan_id>:<i>`` names; only this module can register one, so a
  raw coordinate can never reach the adapter from a plan that arrives over MCP/HTTP.
* Progress = squad centroid within ``reach_radius`` of the next point.  The lease is released on final reached
  (``route_done``), timeout (``ttl_expired``), cancel, emergency preemption (the existing preflight cancels combat-group
  leases; emergency still wins) or when the whole squad is gone (``squad_lost``).
* The real mouse is never touched: ``agent_cursor`` events only tell the overlay where the agent just issued an order.

Releasing a lease hands the units back to MicroMachine; it does not clear queued SC2 orders (an emergency vector brings
its own stop/retreat orders).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from starcraft_commander.contracts import SC2ActionType, SC2CommandAction, SC2ExecutionPlan
from starcraft_commander.direct_command_lifecycle import (
    DirectCommandLease,
    DirectCommandOwnershipConflict,
)
from starcraft_commander.route_sketch import RouteDecision, SketchWorld, world_to_frame

DEFAULT_TTL_SECONDS = 180
DEFAULT_REACH_RADIUS = 4.0
ROUTE_DONE = "route_done"
_RELEASE_REASONS = {"completion_conditions": "route_done"}


class RouteEventLog:
    """Bounded, thread-safe journal of ``route_plan`` / ``route_progress`` / ``agent_cursor`` events."""

    def __init__(self, retention: int = 500, publisher: Callable[[str, Mapping[str, object]], object] | None = None) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict[str, object]] = deque(maxlen=retention)
        self._seq = 0
        self.publisher = publisher

    def emit(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            self._seq += 1
            event = {"route_seq": self._seq, "kind": kind, **payload}
            self._events.append(event)
        publisher = self.publisher
        if publisher is not None:
            try:
                publisher(kind, dict(event))
            except Exception:  # noqa: BLE001 - a UI sink failure must never break the game loop.
                pass
        return dict(event)

    def since(self, after: int = 0) -> tuple[list[dict[str, object]], int]:
        with self._lock:
            return [dict(e) for e in self._events if int(e["route_seq"]) > after], self._seq


@dataclass(frozen=True)
class RouteStart:
    ok: bool
    reason: str = ""
    plan_id: str = ""
    command_id: str = ""
    detail: str = ""


@dataclass
class _State:
    plan_id: str
    tags: tuple[int, ...]
    points: tuple[tuple[float, float], ...]  # world points, waypoints then final
    frames: tuple[tuple[float, float], ...]
    next_index: int = 0
    reached_index: int = -1
    done: bool = False
    squad_lost: bool = False


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _units_by_tag(adapter: object, tags: Sequence[int]) -> list[object]:
    bot = getattr(adapter, "bot", adapter)
    pool = list(getattr(bot, "units", None) or []) + list(getattr(bot, "workers", None) or [])
    by_tag = {getattr(u, "tag", None): u for u in pool}
    return [by_tag[t] for t in tags if t in by_tag]


class RouteController:
    def __init__(
        self,
        *,
        executor: object,
        events: RouteEventLog | None = None,
        reach_radius: float = DEFAULT_REACH_RADIUS,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.executor = executor
        self.events = events or RouteEventLog()
        self.reach_radius = reach_radius
        self.ttl_seconds = ttl_seconds
        self._states: dict[str, _State] = {}
        self._lock = threading.RLock()
        self._attached = False

    @property
    def lifecycle(self):
        return self.executor.direct_lifecycle  # type: ignore[attr-defined]

    @property
    def adapter(self) -> object:
        return self.executor.bot  # type: ignore[attr-defined]

    def attach(self) -> None:
        """Install the progress provider + target registry on the adapter and the release listener (idempotent)."""

        if self._attached:
            return
        adapter = self.adapter
        if not hasattr(adapter, "route_target_points"):
            adapter.route_target_points = {}  # type: ignore[attr-defined]
        adapter.route_evidence_provider = self._evidence  # type: ignore[attr-defined]
        self.executor.add_direct_release_listener(self._on_release)  # type: ignore[attr-defined]
        self._attached = True

    # ------------------------------------------------------------------ start / cancel

    async def start(self, decision: RouteDecision, world: SketchWorld, *, frame: int = 0) -> RouteStart:
        if decision.kind != "execute" or decision.route is None or decision.squad is None or not decision.unit_tags:
            return RouteStart(False, "not_executable", decision.plan_id)
        self.attach()
        plan_id = decision.plan_id
        tags = tuple(decision.unit_tags)
        units = _units_by_tag(self.adapter, tags)
        if len(units) != len(tags):
            return RouteStart(False, "squad_missing", plan_id, detail=f"{len(units)}/{len(tags)}")
        route = decision.route
        points = tuple((p.x, p.y) for p in route.waypoints) + ((route.final.x, route.final.y),)
        frames = tuple((p.nx, p.ny) for p in route.waypoints) + ((route.final.nx, route.final.ny),)
        registry: dict[str, tuple[float, float]] = self.adapter.route_target_points  # type: ignore[attr-defined]
        names = [f"route:{plan_id}:{i}" for i in range(len(points))]
        actions = []
        for i, name in enumerate(names):
            attack = i == len(points) - 1 and route.final.action == "attack"
            actions.append(SC2CommandAction(
                SC2ActionType.ATTACK_MOVE if attack else SC2ActionType.MOVE_GROUP,
                subject="route squad", target=name, count=len(tags), queue=i > 0,
                metadata={"_direct_unit_tags": tags, "_direct_requested_count": len(tags), "route_plan_id": plan_id},
            ))
        plan = SC2ExecutionPlan(intent_name="route_sketch", ordered_actions=tuple(actions))
        with self._lock:
            try:
                self.lifecycle.pending(
                    command_id=plan_id, issued_at_frame=max(0, int(frame)), ttl_seconds=self.ttl_seconds,
                    completion_conditions=(ROUTE_DONE,), owned_unit_tags=tags,
                    command_metadata={
                        "intent_name": "route_sketch", "route_plan_id": plan_id,
                        "actions": [a.to_dict() for a in actions],
                    },
                )
            except DirectCommandOwnershipConflict as error:
                return RouteStart(False, "direct_control_conflict", plan_id, detail=str(error))
            registry.update(zip(names, points))
            self._states[plan_id] = _State(plan_id, tags, points, frames)
        try:
            result = await self.executor.execute(plan)  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - runtime failures are data.
            self._abort(plan_id, "execution_exception", names)
            return RouteStart(False, "execution_exception", plan_id, detail=f"{type(error).__name__}:{error}")
        if not result.success:
            detail = "; ".join(str(e.message) for e in result.errors)[:300]
            self._abort(plan_id, "execution_refused", names)
            return RouteStart(False, "execution_refused", plan_id, detail=detail)
        self.lifecycle.mark_dispatched(plan_id)
        self.lifecycle.activate(plan_id)
        self._emit_start_events(decision, world, units)
        return RouteStart(True, "", plan_id, command_id=plan_id)

    def _abort(self, plan_id: str, reason: str, names: Sequence[str]) -> None:
        self._states.pop(plan_id, None)
        registry = self.adapter.route_target_points  # type: ignore[attr-defined]
        for name in names:
            registry.pop(name, None)
        self.lifecycle.fail(plan_id, reason=reason)

    def cancel(self, plan_id: str, *, reason: str = "cancelled_by_user") -> bool:
        lease = self.lifecycle.get(plan_id)
        if lease is None or not lease.control_owned:
            return False
        self.lifecycle.cancel(plan_id, reason=reason)
        return True

    def _emit_start_events(self, decision: RouteDecision, world: SketchWorld, units: Sequence[object]) -> None:
        route, squad, plan_id = decision.route, decision.squad, decision.plan_id
        self.events.emit("route_plan", {
            "plan_id": plan_id, "route": route.to_dict(), "squad": squad.to_dict(),
            "narration_ko": decision.narration_ko,
        })
        xs = [u.position.x for u in units]
        ys = [u.position.y for u in units]
        cx, cy = world_to_frame(sum(xs) / len(xs), sum(ys) / len(ys), world)
        self.events.emit("agent_cursor", {
            "plan_id": plan_id, "nx": _clamp01(cx), "ny": _clamp01(cy), "action": "select",
            "label_ko": f"{squad.label_ko} 선택",
        })
        for point in route.waypoints:
            self.events.emit("agent_cursor", {
                "plan_id": plan_id, "nx": point.nx, "ny": point.ny, "action": "move", "label_ko": point.label_ko,
            })
        final = route.final
        self.events.emit("agent_cursor", {
            "plan_id": plan_id, "nx": final.nx, "ny": final.ny, "action": final.action, "label_ko": final.label_ko,
        })

    # ------------------------------------------------------------------ game-loop side

    def _evidence(self, lease: Mapping[str, object]) -> Mapping[str, object]:
        """Called by the adapter on every Direct tick for a route lease; returns completion evidence."""

        meta = lease.get("command_metadata", {})
        plan_id = str(meta.get("route_plan_id", "")) if isinstance(meta, Mapping) else ""
        state = self._states.get(plan_id)
        if state is None or state.done:
            return {ROUTE_DONE: True} if state is not None else {}
        units = _units_by_tag(self.adapter, state.tags)
        if not units:
            state.squad_lost = True
            self.lifecycle.cancel(plan_id, reason="squad_lost")
            return {}
        cx = sum(u.position.x for u in units) / len(units)
        cy = sum(u.position.y for u in units) / len(units)
        while state.next_index < len(state.points):
            px, py = state.points[state.next_index]
            if math.hypot(cx - px, cy - py) > self.reach_radius:
                break
            state.reached_index = state.next_index
            state.next_index += 1
            if state.next_index >= len(state.points):
                state.done = True
                return {ROUTE_DONE: True}
            self.events.emit("route_progress", {
                "plan_id": plan_id, "reached_index": state.reached_index, "done": False, "released": False,
            })
        return {}

    def _on_release(self, lease: DirectCommandLease) -> None:
        meta = lease.command_metadata
        plan_id = str(meta.get("route_plan_id", "")) if isinstance(meta, Mapping) else ""
        state = self._states.pop(plan_id, None)
        if state is None:
            return
        registry = getattr(self.adapter, "route_target_points", {})
        for i in range(len(state.points)):
            registry.pop(f"route:{plan_id}:{i}", None)
        reason = _RELEASE_REASONS.get(lease.release_reason, lease.release_reason)
        self.events.emit("route_progress", {
            "plan_id": plan_id, "reached_index": state.reached_index, "done": state.done, "released": True,
            "reason": reason,
        })


# ---------------------------------------------------------------------- world snapshot from a live BotAI

_PLACE_NAMES_KO = {
    "self_main": "내 본진", "self_natural": "내 앞마당", "self_third": "내 세 번째 기지", "self_ramp": "내 입구",
    "enemy_main": "적 본진", "enemy_natural": "적 앞마당", "enemy_third": "적 세 번째 기지", "enemy_ramp": "적 입구",
}


def world_from_bot(adapter: object, *, calibration: tuple[float, float] = (0.0, 0.0)) -> SketchWorld | None:
    """Snapshot a :class:`SketchWorld` from the live bot; ``None`` when the game info is not available (fail closed)."""

    bot = getattr(adapter, "bot", adapter)
    info = getattr(bot, "game_info", None)
    area = getattr(info, "playable_area", None)
    if area is None:
        return None
    try:
        x0, y0 = float(area.x), float(area.y)
        x1, y1 = x0 + float(area.width), y0 + float(area.height)
        map_size = (float(info.map_size.x), float(info.map_size.y))
    except (AttributeError, TypeError, ValueError):
        return None

    try:
        from sc2.position import Point2 as _point
    except ImportError:  # dry-run demo without the sc2 extra: any x/y object answers in_pathing_grid
        from types import SimpleNamespace

        def _point(xy: tuple[float, float]) -> object:
            return SimpleNamespace(x=xy[0], y=xy[1])

    def pathable(x: float, y: float) -> bool:
        try:
            return bool(bot.in_pathing_grid(_point((x, y))))
        except Exception:  # noqa: BLE001 - unknown terrain is not walkable.
            return False

    own = [
        (u.type_id.name, int(u.tag), float(u.position.x), float(u.position.y))
        for u in list(getattr(bot, "units", None) or [])
    ]
    enemies = [
        (u.type_id.name, float(u.position.x), float(u.position.y))
        for u in list(getattr(bot, "enemy_units", None) or []) + list(getattr(bot, "enemy_structures", None) or [])
    ]
    places: list[tuple[str, float, float]] = []
    resolver = getattr(adapter, "map_resolver", None)
    positions = getattr(getattr(resolver, "snapshot", lambda: resolver)(), "positions", None)
    if isinstance(positions, Mapping):
        for key, name in _PLACE_NAMES_KO.items():
            point = positions.get(key)
            if point is not None:
                places.append((name, float(point.x), float(point.y)))
    return SketchWorld(
        playable=(x0, y0, x1, y1), map_size=map_size, pathable=pathable, own_units=own, enemies=enemies,
        places=places, calibration=calibration,
    )
