"""Route sketch on the dry-run demo (no StarCraft II, no python-sc2): the user can try draw -> route -> ghost cursor.

The demo units do not move (the scripted bot has no simulation), so progress stays at the first waypoint; this test pins
only what the demo can honestly show: the plan, the queued orders and the agent-cursor events.
"""

from __future__ import annotations

from starcraft_commander import route_sketch as rs
from starcraft_commander.demo_sc2 import build_dry_run_session
from starcraft_commander.route_execution import world_from_bot
from starcraft_commander.web_gui import SessionLoopBridge


def _bridge():
    session, _bot = build_dry_run_session()
    return SessionLoopBridge(session=session), session


def _frame_points(world, points, n=10):
    out, t = [], 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        for k in range(n):
            x, y = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
            nx, ny = rs.world_to_frame(x, y, world)
            out.append([nx, ny, t])
            t += 25.0
    nx, ny = rs.world_to_frame(*points[-1], world)
    out.append([nx, ny, t])
    return out


def test_demo_bot_exposes_a_sketch_world():
    bridge, _ = _bridge()
    adapter = bridge.route_service.executor.bot
    world = world_from_bot(adapter)
    assert world is not None, "the dry-run demo must expose playable area + map size"
    x0, y0, x1, y1 = world.playable
    assert x1 > x0 and y1 > y0
    assert world.pathable((x0 + x1) / 2, (y0 + y1) / 2)
    assert not world.pathable(x1 + 50.0, y1 + 50.0)
    assert any(name == "MARINE" for name, *_ in world.own_units)

def test_a_bot_without_map_info_yields_no_world_so_no_route_is_invented():
    from types import SimpleNamespace

    bot = SimpleNamespace(game_info=SimpleNamespace(map_ramps=()), units=[], enemy_units=[], enemy_structures=[])
    assert world_from_bot(bot) is None



def test_a_bent_route_from_the_marines_executes_with_queued_orders_and_events():
    bridge, session = _bridge()
    bridge.start()
    try:
        _run_bent_route(bridge)
    finally:
        bridge.stop()


def _run_bent_route(bridge):
    service = bridge.route_service
    adapter = service.executor.bot
    world = world_from_bot(adapter)
    marines = [u for u in world.own_units if u[0] == "MARINE"]
    cx = sum(u[2] for u in marines) / len(marines)
    cy = sum(u[3] for u in marines) / len(marines)
    stroke = _frame_points(world, [(cx, cy), (80.0, 40.0), (110.0, 100.0)])
    published = []
    service.set_publisher(lambda kind, payload: published.append((kind, dict(payload))))

    body = service.handle({"update_id": "demo-route-1", "strokes": [stroke], "transcript": "마린 6기 이 길로 가"})

    assert body["kind"] == "execute", body
    assert body["route"]["final"]["action"] == "move"
    assert len(body["route"]["waypoints"]) >= 1
    assert "마린" in body["narration_ko"]
    demo_marines = [u for u in adapter.bot.units if u.name == "Marine"]
    orders = [order for unit in demo_marines for order in unit.issued_orders]
    moves = [order for order in orders if order[0] == "move"]
    assert moves, orders
    assert any(order[2] is True for order in moves), "follow-up waypoints must be queued"
    assert any(order[2] is False for order in moves), "the first waypoint replaces current orders"
    kinds = [kind for kind, _ in published]
    assert "route_plan" in kinds and "agent_cursor" in kinds, kinds
