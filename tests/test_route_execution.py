"""Route execution on the harness fake bot: raw ActionRawUnitCommand shapes, lease take/release, emergency, events.

Offline request-shape evidence only (python-sc2 + harness); nothing here proves a live SC2 accepted the orders.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import harness  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402

from starcraft_commander import route_execution as rx  # noqa: E402
from starcraft_commander import route_sketch as rs  # noqa: E402
from starcraft_commander.direct_command_lifecycle import DirectCommandState  # noqa: E402
from starcraft_commander.micromachine_live_session import _lease_is_combat_group  # noqa: E402
from starcraft_commander.sc2_executor import SC2RuntimeExecutor  # noqa: E402

PLAYABLE = (0.0, 0.0, 200.0, 100.0)


def line(p0, p1, n=20):
    return [(p0[0] + (p1[0] - p0[0]) * i / (n - 1), p0[1] + (p1[1] - p0[1]) * i / (n - 1), i * 16) for i in range(n)]


def circle(cx, cy, r, n=40):
    import math

    pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n), i * 16) for i in range(n)]
    return pts + [pts[0]]


class Rig:
    def __init__(self, *, marines=6, enemy_at=None, transcript="마린 4기", strokes=None):
        self.bot = harness.make_bot((U.MARINE, marines))
        self.client = harness.FakeActionClient()
        self.bot.client = self.client
        self.adapter = harness.make_adapter(self.bot)
        self.executor = SC2RuntimeExecutor(bot=self.adapter)
        for unit in self.bot.units:  # spread so the centroid is far from the route
            pass
        self.world = rs.SketchWorld(
            playable=PLAYABLE,
            map_size=(220, 120),
            own_units=[(u.type_id.name, u.tag, u.position.x, u.position.y) for u in self.bot.units],
            enemies=[("MARINE", *enemy_at)] if enemy_at else [],
            places=[("적 앞마당", 150.0, 50.0)],
        )
        self.events = rx.RouteEventLog()
        self.controller = rx.RouteController(executor=self.executor, events=self.events, reach_radius=4.0)
        self.controller.attach()
        strokes = strokes or [line((0.30, 0.50), (0.55, 0.45), 12) + line((0.55, 0.45), (0.75, 0.5), 12)[1:]]
        self.request = rs.SketchRequest(update_id="u1", strokes=tuple(tuple(s) for s in strokes), transcript=transcript)
        self.decision = rs.interpret_sketch(self.request, self.world, vlm=None)

    def start(self):
        return asyncio.run(self.controller.start(self.decision, self.world, frame=1))

    def commit(self, frame=2):
        self.bot.state.game_loop = frame
        asyncio.run(self.bot._do_actions(self.bot.actions))
        self.bot.actions.clear()

    def views(self):
        out = []
        for request in self.client.requests:
            for action in request.action.actions:
                out.append(harness.command_view(action.action_raw))
        return out

    def tick(self, frame):
        self.bot.state.game_loop = frame
        return self.executor.tick_direct_commands(frame)

    def teleport(self, x, y):
        from sc2.position import Point2

        for unit in self.bot.units:
            unit._proto.pos.x, unit._proto.pos.y = x, y
            unit.__dict__.pop("position", None)
            unit.__dict__.pop("position3d", None)


def test_decision_for_the_rig_executes_with_four_marines():
    rig = Rig()
    assert rig.decision.kind == "execute", rig.decision
    assert len(rig.decision.unit_tags) == 4


def test_raw_requests_are_move_then_queued_moves_then_queued_final_move():
    rig = Rig()
    result = rig.start()
    assert result.ok, result
    rig.commit()
    views = rig.views()
    route = rig.decision.route
    assert len(views) == len(route.waypoints) + 1
    assert all(v["ability_name"] == "MOVE_MOVE" for v in views)
    assert [v["queue"] for v in views] == [False] + [True] * (len(views) - 1)
    expected = [(p.x, p.y) for p in route.waypoints] + [(route.final.x, route.final.y)]
    assert [v["pos"] for v in views] == pytest.approx(expected) or all(
        v["pos"] == pytest.approx(e) for v, e in zip(views, expected)
    )
    tags = sorted(rig.decision.unit_tags)
    assert all(v["unit_tags"] == tags for v in views)
    assert all(harness.target_shape_ok(v) for v in views)


def test_attack_final_is_a_queued_attack_order():
    ex, ey = rs.frame_to_world(0.75, 0.45, rs.SketchWorld(playable=PLAYABLE))
    strokes = [line((0.30, 0.50), (0.70, 0.46), 12), circle(0.75, 0.45, 0.05)]
    rig = Rig(enemy_at=(ex, ey), strokes=strokes)
    assert rig.decision.kind == "execute" and rig.decision.route.final.action == "attack"
    assert rig.start().ok
    rig.commit()
    views = rig.views()
    assert views[-1]["ability_name"] == "ATTACK_ATTACK" and views[-1]["queue"] is True
    assert all(v["ability_name"] == "MOVE_MOVE" for v in views[:-1])


def test_lease_is_taken_on_exactly_the_squad_tags_and_is_a_combat_group_lease():
    rig = Rig()
    result = rig.start()
    lease = rig.executor.direct_lifecycle.get(result.command_id)
    assert lease.state is DirectCommandState.ACTIVE
    assert lease.owned_unit_tags == tuple(sorted(rig.decision.unit_tags))
    assert _lease_is_combat_group(lease)  # the real emergency preflight preempts exactly these leases
    for tag in rig.decision.unit_tags:
        assert rig.executor.direct_lifecycle.unit_owner(tag) == result.command_id


def test_second_route_on_the_same_units_is_refused_not_stacked():
    rig = Rig()
    assert rig.start().ok
    again = rig.start()
    assert not again.ok and again.reason == "direct_control_conflict"


def test_progress_reaches_waypoints_then_done_and_releases_the_lease():
    rig = Rig()
    result = rig.start()
    rig.commit()
    route = rig.decision.route
    points = [(p.x, p.y) for p in route.waypoints] + [(route.final.x, route.final.y)]
    for index, (x, y) in enumerate(points):
        rig.teleport(x, y)
        rig.tick(10 + index)
    lease = rig.executor.direct_lifecycle.get(result.command_id)
    assert lease.state is DirectCommandState.COMPLETED
    progress = [e for e in rig.events.since(0)[0] if e["kind"] == "route_progress"]
    assert [e["reached_index"] for e in progress if not e["done"]] == list(range(len(route.waypoints)))
    assert progress[-1]["done"] is True and progress[-1]["released"] is True
    assert rig.executor.direct_lifecycle.unit_owner(rig.decision.unit_tags[0]) is None


def test_squad_far_from_the_next_waypoint_is_not_progress():
    rig = Rig()
    result = rig.start()
    rig.commit()
    rig.tick(10)
    assert rig.executor.direct_lifecycle.get(result.command_id).state is DirectCommandState.ACTIVE
    assert [e for e in rig.events.since(0)[0] if e["kind"] == "route_progress" and e["reached_index"] >= 0] == []


def test_emergency_preempts_and_the_route_reports_released():
    rig = Rig()
    result = rig.start()
    rig.commit()
    # exactly what MicroMachineLiveTextSession._direct_preflight does for an emergency vector
    for lease in rig.executor.direct_lifecycle.active_leases():
        if _lease_is_combat_group(lease):
            rig.executor.direct_lifecycle.cancel(lease.command_id, reason="emergency_preempted:u9")
    assert rig.executor.direct_lifecycle.get(result.command_id).state is DirectCommandState.CANCELLED
    last = rig.events.since(0)[0][-1]
    assert last["kind"] == "route_progress" and last["released"] is True and last["done"] is False
    assert last["reason"].startswith("emergency_preempted")


def test_timeout_releases_with_ttl_reason():
    rig = Rig()
    rig.controller.ttl_seconds = 1
    result = rig.start()
    rig.tick(1 + 200)
    assert rig.executor.direct_lifecycle.get(result.command_id).state is DirectCommandState.EXPIRED
    assert rig.events.since(0)[0][-1]["reason"] == "ttl_expired"


def test_squad_wiped_out_releases_the_lease():
    rig = Rig()
    result = rig.start()
    rig.commit()
    rig.bot.units.clear()
    rig.bot.all_own_units.clear()
    rig.tick(20)
    assert rig.executor.direct_lifecycle.get(result.command_id).state is DirectCommandState.CANCELLED
    assert rig.events.since(0)[0][-1]["reason"] == "squad_lost"


def test_user_cancel_releases():
    rig = Rig()
    result = rig.start()
    assert rig.controller.cancel(result.plan_id, reason="cancelled_by_user")
    assert rig.executor.direct_lifecycle.get(result.command_id).state is DirectCommandState.CANCELLED


def test_events_plan_cursor_progress_in_order_with_frame_coordinates():
    rig = Rig()
    result = rig.start()
    events, latest = rig.events.since(0)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "route_plan" and events[0]["plan_id"] == result.plan_id
    assert events[0]["narration_ko"] and events[0]["route"]["final"]["action"] == "move"
    cursors = [e for e in events if e["kind"] == "agent_cursor"]
    assert cursors[0]["action"] == "select"
    assert [c["action"] for c in cursors[1:]] == ["move"] * len(rig.decision.route.waypoints) + ["move"]
    for c in cursors:
        assert 0.0 <= c["nx"] <= 1.0 and 0.0 <= c["ny"] <= 1.0 and c["plan_id"] == result.plan_id and c["label_ko"]
    last = cursors[-1]
    assert (last["nx"], last["ny"]) == pytest.approx((rig.decision.route.final.nx, rig.decision.route.final.ny))
    assert [e["route_seq"] for e in events] == sorted(e["route_seq"] for e in events)
    assert rig.events.since(latest)[0] == []


def test_non_execute_decisions_are_never_started():
    rig = Rig(transcript="")
    assert rig.decision.kind == "ask"
    result = rig.start()
    assert not result.ok and result.reason == "not_executable"
    assert rig.bot.actions == [] and rig.executor.direct_lifecycle.active_leases() == ()


def test_units_that_vanished_since_the_decision_refuse():
    rig = Rig()
    rig.bot.units.clear()
    rig.bot.all_own_units.clear()
    result = rig.start()
    assert not result.ok and result.reason == "squad_missing"
    assert rig.executor.direct_lifecycle.active_leases() == ()


def test_event_log_is_bounded_and_subscribers_get_every_event():
    seen = []
    log = rx.RouteEventLog(retention=3, publisher=lambda kind, payload: seen.append(kind))
    for i in range(5):
        log.emit("agent_cursor", {"plan_id": "p", "i": i})
    assert len(log.since(0)[0]) == 3 and log.since(0)[1] == 5 and len(seen) == 5
