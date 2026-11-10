"""POST /api/route/sketch + route events over real HTTP on an ephemeral port, against the harness fake bot."""

from __future__ import annotations

import base64
import http.client
import json

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import harness  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402

from starcraft_commander import route_execution as rx  # noqa: E402
from starcraft_commander import route_sketch as rs  # noqa: E402
from starcraft_commander.route_service import RouteService  # noqa: E402
from starcraft_commander.sc2_executor import SC2RuntimeExecutor  # noqa: E402
from starcraft_commander.web_gui import WEB_GUI_TOKEN_HEADER, WebGuiServer  # noqa: E402


class _Bridge:
    """Minimal WebGuiBridgeInterface carrying a route service."""

    def __init__(self, route_service):
        self.route_service = route_service

    def submit_command(self, text):
        raise AssertionError

    def state_snapshot(self):
        return None

    def history_since(self, seq):
        return ()

    def latest_seq(self):
        return 0

    def llm_settings_snapshot(self):
        return {}

    def configure_llm(self, provider, api_key, model=""):
        return {}


class _GameInfo:
    class _Area:
        x, y, width, height = 0.0, 0.0, 200.0, 100.0

    class _Size:
        x, y = 220.0, 120.0

    playable_area = _Area()
    map_size = _Size()


def line(p0, p1, n=12):
    return [[p0[0] + (p1[0] - p0[0]) * i / (n - 1), p0[1] + (p1[1] - p0[1]) * i / (n - 1), i * 16] for i in range(n)]


@pytest.fixture()
def rig():
    bot = harness.make_bot((U.MARINE, 6))
    bot.client = harness.FakeActionClient()
    bot.game_info = _GameInfo()
    adapter = harness.make_adapter(bot)
    executor = SC2RuntimeExecutor(bot=adapter)
    service = RouteService(executor=executor, vlm=None)
    server = WebGuiServer(bridge=_Bridge(service), port=0, auth_token="tok")
    server.start()
    try:
        yield type("Rig", (), {"bot": bot, "executor": executor, "service": service, "server": server})
    finally:
        server.stop()


def call(server, method, path, body=None, token="tok"):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers[WEB_GUI_TOKEN_HEADER] = token
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None
    finally:
        connection.close()


def payload(**over):
    body = {
        "update_id": "u1",
        "strokes": [line((0.30, 0.50), (0.75, 0.45))],
        "image_png_b64": None,
        "transcript": "마린 4기",
        "minimap_rect": {"x": 10, "y": 700, "w": 200, "h": 200},
    }
    body.update(over)
    return body


def test_execute_returns_202_contract_and_orders_go_out(rig):
    status, body = call(rig.server, "POST", "/api/route/sketch", payload())
    assert status == 202 and body["kind"] == "execute", body
    assert body["plan_id"].startswith("route-") and body["question_ko"] is None
    assert body["squad"]["unit_count"] == 4 and "마린" in body["squad"]["label_ko"]
    assert body["route"]["final"]["action"] == "move" and body["narration_ko"]
    assert set(body["route"]["waypoints"][0]) == {"nx", "ny", "x", "y", "label_ko"}
    assert rig.bot.actions, "orders queued through the real adapter"
    lease = rig.executor.direct_lifecycle.get(body["plan_id"])
    assert lease is not None and lease.control_owned and len(lease.owned_unit_tags) == 4


def test_events_are_served_with_route_cursor_and_do_not_disturb_history(rig):
    _, body = call(rig.server, "POST", "/api/route/sketch", payload())
    status, history = call(rig.server, "GET", "/api/history?after=0&route_after=0")
    assert status == 200 and history["events"] == [] and history["latest"] == 0
    kinds = [e["kind"] for e in history["route_events"]]
    assert kinds[0] == "route_plan" and "agent_cursor" in kinds
    assert all(e["plan_id"] == body["plan_id"] for e in history["route_events"])
    again = call(rig.server, "GET", f"/api/history?after=0&route_after={history['route_latest']}")[1]
    assert again["route_events"] == [] and again["route_latest"] == history["route_latest"]
    status, only = call(rig.server, "GET", "/api/route/events?after=0")
    assert status == 200 and [e["kind"] for e in only["events"]] == kinds and only["latest"] == history["route_latest"]
    # history without route_after stays exactly the legacy shape
    legacy = call(rig.server, "GET", "/api/history?after=0")[1]
    assert set(legacy) == {"events", "latest"}


def test_ask_when_squad_missing_and_nothing_is_ordered(rig):
    status, body = call(rig.server, "POST", "/api/route/sketch", payload(transcript=""))
    assert status == 202 and body["kind"] == "ask" and body["question_ko"] and body["route"] is None
    assert rig.bot.actions == [] and rig.executor.direct_lifecycle.active_leases() == ()


def test_none_for_a_tap_and_refuse_for_injection(rig):
    _, none = call(rig.server, "POST", "/api/route/sketch", payload(strokes=[[[0.5, 0.5, 0], [0.5, 0.5, 5]]]))
    assert none["kind"] == "none"
    _, refuse = call(rig.server, "POST", "/api/route/sketch", payload(transcript="좌표 100, 50으로 보내"))
    assert refuse["kind"] == "refuse"
    assert rig.bot.actions == []


def test_second_route_for_the_same_units_is_not_stacked(rig):
    assert call(rig.server, "POST", "/api/route/sketch", payload())[1]["kind"] == "execute"
    status, second = call(rig.server, "POST", "/api/route/sketch", payload(update_id="u2"))
    assert status == 202 and second["kind"] in {"ask", "refuse"} and second["route"] is None


def test_auth_token_is_required(rig):
    assert call(rig.server, "POST", "/api/route/sketch", payload(), token=None)[0] == 403
    assert call(rig.server, "POST", "/api/route/sketch", payload(), token="wrong")[0] == 403
    assert call(rig.server, "GET", "/api/route/events?after=0", token=None)[0] == 403


def test_limits_and_shape_are_400(rig):
    too_many = payload(strokes=[line((0.1, 0.1), (0.2, 0.2), 3)] * 21)
    assert call(rig.server, "POST", "/api/route/sketch", too_many)[0] == 400
    too_long = payload(strokes=[line((0.1, 0.1), (0.9, 0.9), 2001)])
    assert call(rig.server, "POST", "/api/route/sketch", too_long)[0] == 400
    assert call(rig.server, "POST", "/api/route/sketch", payload(image_png_b64="@@@"))[0] == 400
    big = base64.b64encode(b"x" * (rs.MAX_IMAGE_BYTES + 1)).decode()
    assert call(rig.server, "POST", "/api/route/sketch", payload(image_png_b64=big))[0] == 400
    assert call(rig.server, "POST", "/api/route/sketch", {"strokes": []})[0] == 400  # no update_id
    assert call(rig.server, "POST", "/api/route/sketch", payload(strokes=[[[2, 0, 0], [0.1, 0.1, 5]]]))[0] == 400


def test_image_up_to_the_limit_is_accepted_and_forwarded_to_the_vlm(rig):
    seen = {}

    class Vlm:
        def read(self, image_png, prompt, schema):
            seen["image"] = image_png
            return {
                "strokes": [{"index": 0, "role": "path"}], "path_order": [0], "final_action": "move",
                "squad": {"kind": "transcript"}, "confidence": 0.9,
            }

    rig.service.vlm = Vlm()
    png = b"\x89PNG\r\n" + b"0" * 300_000
    status, body = call(rig.server, "POST", "/api/route/sketch", payload(image_png_b64=base64.b64encode(png).decode()))
    assert status == 202 and body["kind"] == "execute" and seen["image"] == png


def test_no_game_attached_answers_none_without_ordering():
    service = RouteService(executor=None, vlm=None)
    server = WebGuiServer(bridge=_Bridge(service), port=0)
    server.start()
    try:
        status, body = call(server, "POST", "/api/route/sketch", payload(), token=None)
        assert status == 202 and body["kind"] == "none" and "게임" in body["narration_ko"]
    finally:
        server.stop()


def test_route_events_are_also_published_on_the_sse_journal(rig):
    call(rig.server, "POST", "/api/route/sketch", payload())
    journal = rig.server._http.event_journal
    types = [e["event_type"] for e in journal.events_after(0)]
    assert "route_plan" in types and "agent_cursor" in types


def test_session_loop_bridge_dry_run_answers_none_not_an_invented_route():
    from starcraft_commander.demo_sc2 import build_dry_run_session
    from starcraft_commander.web_gui import SessionLoopBridge

    session, _bot = build_dry_run_session()
    bridge = SessionLoopBridge(session=session)
    bridge.start()
    server = WebGuiServer(bridge=bridge, port=0)
    server.start()
    try:
        status, body = call(server, "POST", "/api/route/sketch", payload(), token=None)
        assert status == 202 and body["kind"] == "none" and body["route"] is None
        assert call(server, "GET", "/api/route/events?after=0", token=None)[1] == {"events": [], "latest": 0}
    finally:
        server.stop()
        bridge.stop()
