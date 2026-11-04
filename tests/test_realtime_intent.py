"""Realtime voice intent: schema lowering, fast paths and HTTP endpoints.

No network except an optional gated live mint (``VOI_LIVE_REALTIME=1``); the
endpoint tests use a loopback stub that plays the Realtime proxy.
"""

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import pytest

from starcraft_commander import realtime_intent as ri
from starcraft_commander.demo_sc2 import build_dry_run_session
from starcraft_commander.policy_modulation import PolicyModulationVector
from starcraft_commander.web_gui import (
    WEB_GUI_TOKEN_HEADER,
    SessionLoopBridge,
    WebGuiServer,
)

LONG_LIVED_KEY = "-".join(("sk", "test", "LONG", "LIVED", "KEY", "0123456789"))
MINTED_SECRET = "ek_stub_ephemeral_secret_abcdef"

MARINE = "TERRAN_MARINE"


def op(task_type, count=None, unit=MARINE, **extra):
    item = {"task_type": task_type, **extra}
    if count is not None:
        item["composition"] = [{"unit_type": unit, "count": count}]
    return item


PAYLOADS = [
    # (id, tool args, expected tier, expected operation count)
    (
        "emergency-retreat",
        {
            "command_layer": "emergency",
            "goal": "후퇴",
            "emergency": {"force_retreat": True, "cancel_attacks": True},
        },
        "emergency",
        0,
    ),
    (
        "emergency-hold",
        {
            "command_layer": "emergency",
            "goal": "hold position",
            "emergency": {"hold_position": True},
        },
        "emergency",
        0,
    ),
    (
        "emergency-workers",
        {
            "command_layer": "emergency",
            "goal": "일꾼 대피",
            "emergency": {"evacuate_workers": True, "pull_workers_for_defense": False},
        },
        "emergency",
        0,
    ),
    (
        "micro-stim",
        {"command_layer": "micro", "goal": "스팀팩", "micro": [{"ability": "stimpack"}]},
        "micro",
        0,
    ),
    (
        "micro-siege",
        {
            "command_layer": "micro",
            "goal": "탱크 시즈모드",
            "micro": [{"ability": "siege_mode", "unit_classes": ["TERRAN_SIEGETANK"]}],
        },
        "micro",
        0,
    ),
    (
        "micro-two-abilities",
        {
            "command_layer": "micro",
            "goal": "시즈하고 스팀",
            "micro": [
                {"ability": "siege_mode", "unit_classes": ["TERRAN_SIEGETANK"]},
                {"ability": "stimpack"},
            ],
        },
        "micro",
        2,
    ),
    (
        "micro-nuke",
        {"command_layer": "micro", "goal": "핵 발사", "micro": [{"ability": "tactical_nuke"}]},
        "micro",
        0,
    ),
    (
        "operation-parallel-scout-attack",
        {
            "command_layer": "operation",
            "goal": "마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격",
            "operations": [
                op("scout_with_units", 2),
                op("pressure_with_main_army", 4, route_type="flank_right", target_type="enemy_natural"),
            ],
        },
        "operation",
        2,
    ),
    (
        "operation-three-way",
        {
            "command_layer": "operation",
            "goal": "정찰, 견제, 수비",
            "operations": [
                op("scout_with_units", 1),
                op("harass_with_units", 4, unit="TERRAN_BANSHEE", target_type="worker_line"),
                op("defend_with_units", 6, location="natural"),
            ],
        },
        "operation",
        3,
    ),
    (
        "operation-cancel",
        {
            "command_layer": "operation",
            "goal": "정찰 취소",
            "operations": [{"operation_id": "scout", "operation_action": "cancel"}],
        },
        "operation",
        1,
    ),
    (
        "operation-reinforce",
        {
            "command_layer": "operation",
            "goal": "공격에 마린 2기 더",
            "operations": [
                {
                    "operation_id": "attack",
                    "operation_action": "reinforce",
                    "composition": [{"unit_type": MARINE, "count": 2}],
                }
            ],
        },
        "operation",
        1,
    ),
    (
        "operation-mixed-with-micro",
        {
            "command_layer": "operation",
            "goal": "정찰하면서 스팀팩",
            "operations": [op("scout_with_units", 1)],
            "micro": [{"ability": "stimpack"}],
        },
        "operation",
        2,
    ),
    (
        "macro-production-and-bunker",
        {
            "command_layer": "macro",
            "goal": "탱크 생산하고 앞마당에 벙커",
            "macro": {
                "task_type": "tech_transition",
                "production_targets": ["TERRAN_SIEGETANK"],
                "building_tasks": [
                    {"building_type": "TERRAN_BUNKER", "placement_intent": "natural"}
                ],
            },
        },
        "macro",
        0,
    ),
    (
        "macro-standing-marine",
        {
            "command_layer": "macro",
            "goal": "마린 중심으로 계속",
            "macro": {
                "production_targets": [MARINE],
                "standing_order": True,
                "doctrine": "marine_rush",
            },
        },
        "macro",
        0,
    ),
    (
        "macro-expand",
        {
            "command_layer": "macro",
            "goal": "확장해",
            "macro": {"task_type": "expand_or_land_command_center"},
        },
        "macro",
        0,
    ),
]


class SchemaTest(unittest.TestCase):
    def test_tool_schema_shape_and_enums_come_from_the_dsl(self):
        tool = ri.build_submit_command_tool()
        self.assertEqual("function", tool["type"])
        self.assertEqual("submit_command", tool["name"])
        params = tool["parameters"]
        self.assertEqual(["command_layer", "goal"], params["required"])
        self.assertEqual(
            ["emergency", "macro", "micro", "operation"],
            sorted(params["properties"]["command_layer"]["enum"]),
        )
        route = params["properties"]["operations"]["items"]["properties"]["route_type"]
        self.assertIn("flank_right", route["enum"])
        self.assertNotIn("", route["enum"])
        actions = params["properties"]["operations"]["items"]["properties"]["operation_action"]
        self.assertEqual(
            {"create", "update", "resize", "reinforce", "retarget", "cancel", "restart"},
            set(actions["enum"]),
        )
        json.dumps(tool)  # JSON-serialisable for the session payload.

    def test_every_documented_enum_value_is_dsl_valid(self):
        for location in ri.LOCATION_INTENTS:
            vector = PolicyModulationVector.from_mapping(
                ri.tool_arguments_to_provider_output(
                    {
                        "command_layer": "operation",
                        "goal": "g",
                        "operations": [op("scout_with_units", 1, location=location)],
                    }
                )
            )
            self.assertEqual(location, vector.operations[0].scope.location_intent)
        for ability in ri.ABILITIES:
            PolicyModulationVector.from_mapping(
                ri.tool_arguments_to_provider_output(
                    {"command_layer": "micro", "goal": "g", "micro": [{"ability": ability}]}
                )
            )
        for route in ri.ROUTE_TYPES:
            for target in ri.TARGET_TYPES:
                PolicyModulationVector.from_mapping(
                    ri.tool_arguments_to_provider_output(
                        {
                            "command_layer": "operation",
                            "goal": "g",
                            "operations": [
                                op("pressure_with_main_army", 2, route_type=route, target_type=target)
                            ],
                        }
                    )
                )

    def test_instructions_cover_tiers_parallelism_and_safety(self):
        text = ri.build_realtime_instructions()
        for needle in ("emergency", "micro", "operation", "macro", "clarification",
                       "operations[]", "후퇴하지 마", "NEVER unit tags", "Korean"):
            self.assertIn(needle, text)


class LoweringRoundTripTest(unittest.TestCase):
    def test_payloads_round_trip_through_from_mapping(self):
        self.assertGreaterEqual(len(PAYLOADS), 12)
        tiers = {tier for _, _, tier, _ in PAYLOADS}
        self.assertEqual({"emergency", "micro", "operation", "macro"}, tiers)
        for name, args, tier, op_count in PAYLOADS:
            with self.subTest(name):
                self.assertEqual(tier, ri.classify_tier(args))
                output = ri.tool_arguments_to_provider_output(args)
                vector = PolicyModulationVector.from_mapping(output)
                self.assertEqual(tier, vector.command_layer.value)
                self.assertEqual(op_count, len(vector.operations))
                self.assertEqual(output["goal"], vector.goal)
                if tier == "emergency":
                    self.assertEqual("emergency", vector.override_level.value)
                    self.assertLessEqual(vector.ttl_seconds, 60)
                    self.assertTrue(any(vector.emergency.to_dict().values()))

    def test_parallel_operations_keep_counts_routes_and_independent_ids(self):
        args = next(a for n, a, _, _ in PAYLOADS if n == "operation-parallel-scout-attack")
        vector = PolicyModulationVector.from_mapping(
            ri.tool_arguments_to_provider_output(args)
        )
        scout, attack = vector.operations
        self.assertNotEqual(scout.operation_id, attack.operation_id)
        self.assertEqual("scout_with_units", scout.tactical_task.task_type)
        self.assertEqual("pressure_with_main_army", attack.tactical_task.task_type)
        self.assertEqual([(MARINE, 2)], [(c.unit_type, c.count) for c in scout.composition_requirements])
        self.assertEqual([(MARINE, 4)], [(c.unit_type, c.count) for c in attack.composition_requirements])
        self.assertEqual("flank_right", attack.route_intent.route_type)
        self.assertTrue(attack.route_intent.avoid_enemy_strength)
        self.assertEqual("enemy_natural", attack.target_intent.target_type)
        self.assertEqual("", scout.route_intent.route_type)

    def test_micro_nuke_adds_prerequisite_token_and_siege_keeps_unit_class(self):
        nuke = PolicyModulationVector.from_mapping(
            ri.tool_arguments_to_provider_output(
                {"command_layer": "micro", "goal": "g", "micro": [{"ability": "tactical_nuke"}]}
            )
        )
        self.assertIn("TERRAN_NUKE", nuke.tactical_task.production_targets)
        self.assertEqual(("TERRAN_GHOST",), nuke.tactical_task.unit_classes)
        siege = PolicyModulationVector.from_mapping(
            ri.tool_arguments_to_provider_output(
                {
                    "command_layer": "micro",
                    "goal": "g",
                    "micro": [{"ability": "siege_mode", "unit_classes": ["TERRAN_SIEGETANK"]}],
                }
            )
        )
        self.assertEqual("execute_ability", siege.tactical_task.task_type)
        self.assertEqual(("TERRAN_SIEGETANK",), siege.tactical_task.unit_classes)

    def test_macro_building_and_standing_order(self):
        macro = PolicyModulationVector.from_mapping(
            ri.tool_arguments_to_provider_output(
                next(a for n, a, _, _ in PAYLOADS if n == "macro-production-and-bunker")
            )
        )
        self.assertEqual("TERRAN_BUNKER", macro.building_tasks[0].building_type)
        self.assertEqual("natural", macro.building_tasks[0].placement_intent)
        self.assertIn("TERRAN_SIEGETANK", macro.production_plan.targets)
        self.assertTrue(macro.production_plan.allow_prerequisite_buildings)
        standing = PolicyModulationVector.from_mapping(
            ri.tool_arguments_to_provider_output(
                next(a for n, a, _, _ in PAYLOADS if n == "macro-standing-marine")
            )
        )
        self.assertEqual("standing_order", standing.lifetime.mode)
        self.assertEqual("marine_rush", standing.strategy.doctrine)

    def test_emergency_drops_other_sections(self):
        output = ri.tool_arguments_to_provider_output(
            {
                "command_layer": "emergency",
                "goal": "후퇴",
                "emergency": {"force_retreat": True},
                "operations": [op("scout_with_units", 1)],
            }
        )
        self.assertNotIn("operations", output)
        self.assertTrue(any(tag.startswith("dropped_for_emergency") for tag in output["tags"]))
        PolicyModulationVector.from_mapping(output)

    def test_clarification_is_terminal_and_not_a_vector(self):
        args = {"command_layer": "macro", "goal": "?", "clarification": "어떤 유닛을 말씀하세요?"}
        self.assertEqual("clarification", ri.classify_tier(args))
        output = ri.tool_arguments_to_provider_output(args)
        self.assertEqual("clarification_required", output["status"])
        self.assertEqual("어떤 유닛을 말씀하세요?", output["clarification_prompt"])

    def test_rejections(self):
        bad = {
            "raw tag": {"command_layer": "micro", "goal": "g", "micro": [{"ability": "stimpack"}], "unit_tags": [1]},
            "nested raw key": {
                "command_layer": "operation",
                "goal": "g",
                "operations": [{**op("scout_with_units", 1), "attack_move": True}],
            },
            "coordinates": {
                "command_layer": "operation",
                "goal": "g",
                "operations": [{**op("scout_with_units", 1), "position": [10, 20]}],
            },
            "unknown top key": {"command_layer": "macro", "goal": "g", "macro": {"production_targets": [MARINE]}, "x": 1},
            "unknown ability": {"command_layer": "micro", "goal": "g", "micro": [{"ability": "launch_missiles"}]},
            "bad unit": {
                "command_layer": "operation",
                "goal": "g",
                "operations": [op("scout_with_units", 1, unit="TERRAN_PROBE")],
            },
            "bool count": {
                "command_layer": "operation",
                "goal": "g",
                "operations": [{"task_type": "scout_with_units", "composition": [{"unit_type": MARINE, "count": True}]}],
            },
            "duplicate ids": {
                "command_layer": "operation",
                "goal": "g",
                "operations": [
                    {**op("scout_with_units", 1), "operation_id": "a"},
                    {**op("defend_with_units", 1), "operation_id": "a"},
                ],
            },
            "empty": {"command_layer": "macro", "goal": "g"},
            "layer only": {"command_layer": "operation", "goal": "g", "operations": []},
            "missing goal": {"command_layer": "micro", "micro": [{"ability": "stimpack"}]},
            "reinforce without counts": {
                "command_layer": "operation",
                "goal": "g",
                "operations": [{"operation_id": "attack", "operation_action": "reinforce"}],
            },
            "non-mapping": [],
        }
        for name, args in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                ri.tool_arguments_to_provider_output(args)  # type: ignore[arg-type]


class FastPathTest(unittest.TestCase):
    def test_micro_matches(self):
        cases = {
            "스팀팩": "stimpack",
            "스팀팩 써": "stimpack",
            "스팀팩 사용해": "stimpack",
            "stim": "stimpack",
            "마린 스팀팩": "marine_stimpack",
            "불곰 스팀팩": "marauder_stimpack",
            "시즈모드": "siege_mode",
            "탱크 시즈 모드 켜": "siege_mode",
            "시즈 풀어": "unsiege",
            "언시즈": "unsiege",
            "밴시 클로킹": "banshee_cloak",
            "유령 클로킹": "ghost_cloak",
            "밴시 클로킹 해제": "banshee_decloak",
            "EMP": "emp",
            "야마토": "yamato",
        }
        for text, ability in cases.items():
            with self.subTest(text):
                match = ri.match_micro_fast_path(text)
                self.assertIsNotNone(match)
                self.assertEqual(ability, match["micro"][0]["ability"])
                PolicyModulationVector.from_mapping(
                    ri.tool_arguments_to_provider_output(match)
                )

    def test_ambiguous_negated_or_compound_never_match(self):
        for text in (
            "클로킹",  # which unit?
            "스팀팩 쓰지 마",
            "스팀팩 쓰지마",
            "don't stim",
            "시즈모드 하지 마",
            "시즈모드 풀지 마",
            "마린 정찰하고 스팀팩",
            "스팀팩이 뭐야?",
            "시즈모드랑 스팀팩",
            "",
            "안녕하세요",
            "x" * 200,
        ):
            with self.subTest(text):
                self.assertIsNone(ri.match_micro_fast_path(text))

    def test_negation_helpers(self):
        for text in ("후퇴하지 마", "후퇴하지마", "don't retreat", "공격 취소하지 마", "후퇴 금지"):
            self.assertTrue(ri.transcript_negates_emergency(text), text)
            self.assertTrue(ri.transcript_is_pure_negated_emergency(text), text)
        self.assertFalse(ri.transcript_negates_emergency("후퇴"))
        self.assertFalse(ri.transcript_negates_emergency("마린 정찰해"))
        self.assertFalse(ri.transcript_is_pure_negated_emergency("후퇴하지 말고 정찰해"))
        self.assertTrue(ri.micro_phrase_is_negated("스팀팩 쓰지 마"))
        self.assertFalse(ri.micro_phrase_is_negated("스팀팩"))
        self.assertFalse(ri.micro_phrase_is_negated("정찰하지 마"))


class _Upstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, reject_transcription=False, reject_all=False):
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)
        self.requests = []
        self.reject_transcription = reject_transcription
        self.reject_all = reject_all


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        session = body.get("session", {})
        audio_in = session.get("audio", {}).get("input", {})
        if self.server.reject_all or (
            self.server.reject_transcription and "transcription" in audio_in
        ):
            payload = json.dumps(
                {"error": f"bad request echoing {LONG_LIVED_KEY}"}
            ).encode()
            self.send_response(400)
        else:
            payload = json.dumps(
                {"value": MINTED_SECRET, "expires_at": 1893456000, "session": session}
            ).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class EndpointTest(unittest.TestCase):
    def setUp(self):
        session, _bot = build_dry_run_session()
        self.bridge = SessionLoopBridge(session=session)
        self.bridge.start()
        self.addCleanup(self.bridge.stop)
        self.server = WebGuiServer(bridge=self.bridge, port=0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.upstream = _Upstream()
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.upstream.server_close)
        self.addCleanup(self.upstream.shutdown)
        env = {
            "VOI_REALTIME_BASE_URL": f"http://127.0.0.1:{self.upstream.server_port}/v1",
            "VOI_REALTIME_MODEL": "gpt-realtime-stub",
        }
        env["VOI_REALTIME_" + "API_KEY"] = LONG_LIVED_KEY
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, method, path, doc=None, headers=None, server=None):
        server = server or self.server
        body = None if doc is None else json.dumps(doc).encode("utf-8")
        merged = {"Content-Type": "application/json", **(headers or {})}
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            raw = response.read()
            return response.status, raw
        finally:
            connection.close()

    def post(self, path, doc=None, **kwargs):
        status, raw = self.request("POST", path, doc if doc is not None else {}, **kwargs)
        return status, json.loads(raw.decode("utf-8"))

    def events(self):
        journal = self.server._http.event_journal
        return [
            event["payload"]
            for event in journal.events_after(0)
            if event["event_type"] == "voice_intent"
        ]

    # -- session ------------------------------------------------------------

    def test_session_mints_secret_without_leaking_the_long_lived_key(self):
        status, raw = self.request("POST", "/api/realtime/session", {})
        self.assertEqual(200, status)
        text = raw.decode("utf-8")
        self.assertNotIn(LONG_LIVED_KEY, text)
        document = json.loads(text)
        self.assertEqual(MINTED_SECRET, document["client_secret"])
        self.assertEqual(1893456000, document["expires_at"])
        self.assertEqual("gpt-realtime-stub", document["model"])
        self.assertEqual(
            f"ws://127.0.0.1:{self.upstream.server_port}/v1/realtime?model=gpt-realtime-stub",
            document["ws_url"],
        )
        self.assertEqual("submit_command", document["tools"][0]["name"])
        self.assertTrue(document["tier_hint"])
        self.assertEqual(
            {"transcription": True, "text_output": True, "vad_silence_ms": 350},
            document["features"],
        )
        (request,) = self.upstream.requests
        self.assertEqual("/v1/realtime/client_secrets", request["path"])
        self.assertEqual(f"Bearer {LONG_LIVED_KEY}", request["headers"]["Authorization"])
        self.assertEqual("curl/8.7.1", request["headers"]["User-Agent"])
        session = request["body"]["session"]
        self.assertEqual("realtime", session["type"])
        self.assertEqual("required", session["tool_choice"])
        self.assertEqual("submit_command", session["tools"][0]["name"])
        self.assertEqual(session["tools"], document["tools"])
        audio_in = session["audio"]["input"]
        self.assertEqual({"type": "audio/pcm", "rate": 24000}, audio_in["format"])
        self.assertEqual("server_vad", audio_in["turn_detection"]["type"])
        self.assertEqual(350, audio_in["turn_detection"]["silence_duration_ms"])
        self.assertTrue(audio_in["turn_detection"]["create_response"])
        self.assertEqual({"model": "gpt-4o-mini-transcribe"}, audio_in["transcription"])
        self.assertEqual(["text"], session["output_modalities"])
        self.assertIn("submit_command", session["instructions"])

    def test_vad_silence_is_env_tunable(self):
        with mock.patch.dict(os.environ, {"VOI_REALTIME_VAD_SILENCE_MS": "500"}):
            status, document = self.post("/api/realtime/session")
        self.assertEqual(200, status)
        self.assertEqual(500, document["features"]["vad_silence_ms"])
        self.assertEqual(
            500,
            self.upstream.requests[-1]["body"]["session"]["audio"]["input"]["turn_detection"]["silence_duration_ms"],
        )

    def test_transcription_rejection_degrades_silently(self):
        self.upstream.reject_transcription = True
        status, raw = self.request("POST", "/api/realtime/session", {})
        self.assertEqual(200, status)
        self.assertNotIn(LONG_LIVED_KEY, raw.decode("utf-8"))
        document = json.loads(raw)
        self.assertFalse(document["features"]["transcription"])
        self.assertEqual(MINTED_SECRET, document["client_secret"])
        self.assertNotIn(
            "transcription", self.upstream.requests[-1]["body"]["session"]["audio"]["input"]
        )

    def test_upstream_error_is_502_and_scrubbed(self):
        self.upstream.reject_all = True
        status, raw = self.request("POST", "/api/realtime/session", {})
        self.assertEqual(502, status)
        self.assertNotIn(LONG_LIVED_KEY, raw.decode("utf-8"))
        self.assertIn("error", json.loads(raw))

    def test_unreachable_upstream_is_502(self):
        with mock.patch.dict(os.environ, {"VOI_REALTIME_BASE_URL": "http://127.0.0.1:9/v1"}):
            status, document = self.post("/api/realtime/session")
        self.assertEqual(502, status)
        self.assertNotIn(LONG_LIVED_KEY, json.dumps(document))

    def test_missing_key_is_503_without_calling_upstream(self):
        stripped = {k: v for k, v in os.environ.items() if k not in ri.REALTIME_API_KEY_ENV_VARS}
        with mock.patch.dict(os.environ, stripped, clear=True):
            status, document = self.post("/api/realtime/session")
        self.assertEqual(503, status)
        self.assertEqual([], self.upstream.requests)
        self.assertIn("VOI_REALTIME_API_KEY", document["error"])

    def test_auth_token_is_required_and_routes_are_listed_in_404(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        guarded = WebGuiServer(bridge=bridge, port=0, auth_token="voice-secret")
        guarded.start()
        self.addCleanup(guarded.stop)
        for path in ("/api/realtime/session", "/api/realtime/intent"):
            status, _raw = self.request("POST", path, {"transcript": "스팀팩"}, server=guarded)
            self.assertEqual(403, status, path)
        self.assertEqual([], self.upstream.requests)
        status, raw = self.request(
            "POST",
            "/api/realtime/session",
            {},
            headers={WEB_GUI_TOKEN_HEADER: "voice-secret"},
            server=guarded,
        )
        self.assertEqual(200, status)
        self.assertNotIn(LONG_LIVED_KEY, raw.decode("utf-8"))
        status, raw = self.request("GET", "/api/nope")
        self.assertEqual(404, status)
        text = raw.decode("utf-8")
        self.assertIn("POST /api/realtime/session", text)
        self.assertIn("POST /api/realtime/intent", text)

    # -- intent -------------------------------------------------------------

    def wait_for_compile(self, directory, update_id, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, raw = self.request(
                "GET", "/api/micromachine/status?blackboard_dir=" + directory
            )
            document = json.loads(raw)
            compile_result = document.get("compile_result") or {}
            if compile_result.get("update_id") == update_id:
                return document
            time.sleep(0.05)
        self.fail("modulation was never compiled")

    def intent(self, directory, doc, **kwargs):
        return self.post(
            "/api/realtime/intent", {"blackboard_dir": directory, **doc}, **kwargs
        )

    def test_tool_call_parallel_operations_are_submitted_and_compiled(self):
        args = next(a for n, a, _, _ in PAYLOADS if n == "operation-parallel-scout-attack")
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory,
                {
                    "tool_arguments": args,
                    "transcript": "마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격",
                    "latency_ms": {"stt": 410, "model": 900, "bogus": "x"},
                    "update_id": "voice-parallel-1",
                },
            )
            self.assertEqual(202, status, payload)
            self.assertTrue(payload["accepted"])
            self.assertEqual("operation", payload["tier"])
            self.assertEqual("tool_call", payload["source"])
            self.assertEqual("voice-parallel-1", payload["update_id"])
            self.assertEqual(
                "dry_run_fake_bot", payload["direct_runtime"]["kind"]
            )
            self.assertEqual(410, payload["latency_ms"]["stt"])
            self.assertNotIn("bogus", payload["latency_ms"])
            document = self.wait_for_compile(directory, "voice-parallel-1")
            self.assertEqual("compiled", document["compile_result"]["status"])
            vector = document["compile_result"]["vector"]
            self.assertEqual(
                2, len(vector["operations"]), json.dumps(vector)[:400]
            )
            self.assertEqual(
                "flank_right",
                vector["operations"][1]["route_intent"]["route_type"],
            )

    def test_voice_intent_event_is_published(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory,
                {"transcript": "스팀팩", "latency_ms": {"stt": 120}, "update_id": "voice-ev-1"},
            )
            self.assertEqual(202, status)
        events = self.events()
        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("스팀팩", event["transcript"])
        self.assertEqual("micro", event["tier"])
        self.assertEqual("micro_fast_path", event["source"])
        self.assertEqual(120, event["latency_ms"]["stt"])
        self.assertIn("backend_handle", event["latency_ms"])
        self.assertEqual("dry_run_fake_bot", event["direct_runtime"]["kind"])

    def test_transcript_only_micro_fast_path_compiles_without_an_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory, {"transcript": "시즈모드", "update_id": "voice-siege-1"}
            )
            self.assertEqual(202, status, payload)
            self.assertEqual("micro", payload["tier"])
            self.assertEqual("micro_fast_path", payload["source"])
            document = self.wait_for_compile(directory, "voice-siege-1")
            self.assertEqual("compiled", document["compile_result"]["status"])
            task = document["compile_result"]["vector"]["tactical_task"]
            self.assertEqual("siege_mode", task["ability"])

    def test_transcript_only_emergency_reuses_existing_deterministic_path(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory, {"transcript": "후퇴", "update_id": "voice-retreat-1"}
            )
            self.assertEqual(202, status, payload)
            self.assertEqual("emergency", payload["tier"])
            self.assertEqual("emergency_fast_path", payload["source"])
            document = self.wait_for_compile(directory, "voice-retreat-1")
            self.assertEqual(
                "emergency", document["compile_result"]["vector"]["command_layer"]
            )

    def test_negation_guards_never_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            for text in ("후퇴하지 마", "공격 취소하지 마", "스팀팩 쓰지 마"):
                with self.subTest(text):
                    status, payload = self.intent(directory, {"transcript": text})
                    self.assertEqual(200, status, payload)
                    self.assertFalse(payload["accepted"])
                    self.assertEqual("negation_blocked", payload["status"])
                    self.assertNotIn("update_id", payload)
            # A model that inverted the negation is also refused.
            status, payload = self.intent(
                directory,
                {
                    "transcript": "후퇴하지 마",
                    "tool_arguments": {
                        "command_layer": "emergency",
                        "goal": "후퇴",
                        "emergency": {"force_retreat": True},
                    },
                },
            )
            self.assertEqual(200, status)
            self.assertEqual("negation_blocked", payload["status"])
            self.assertEqual("emergency", payload["tier"])
            status, payload = self.intent(
                directory,
                {
                    "transcript": "스팀팩 쓰지 마",
                    "tool_arguments": {
                        "command_layer": "micro",
                        "goal": "스팀팩",
                        "micro": [{"ability": "stimpack"}],
                    },
                },
            )
            self.assertEqual("negation_blocked", payload["status"])
        events = self.events()
        self.assertEqual(5, len(events))
        self.assertTrue(all(not event["accepted"] for event in events))
        self.assertTrue(all(event["status"] == "negation_blocked" for event in events))

    def test_exact_retreat_transcript_overrides_a_wrong_model_tier(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory,
                {
                    "transcript": "후퇴",
                    "update_id": "voice-override-1",
                    "tool_arguments": {
                        "command_layer": "macro",
                        "goal": "후퇴",
                        "macro": {"production_targets": [MARINE]},
                    },
                },
            )
            self.assertEqual(202, status, payload)
            self.assertEqual("emergency", payload["tier"])
            self.assertEqual("emergency_override", payload["source"])

    def test_named_operation_cancel_is_not_overridden_into_an_emergency(self):
        # "정찰 작전 취소해" cancels one named force; the generic attack-cancel
        # regex must not turn it into an army-wide emergency.
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory,
                {
                    "transcript": "정찰 작전 취소해",
                    "update_id": "voice-cancel-op-1",
                    "tool_arguments": {
                        "command_layer": "operation",
                        "goal": "정찰 작전 취소",
                        "operations": [
                            {"operation_id": "scout", "operation_action": "cancel", "goal": "정찰 취소"}
                        ],
                    },
                },
            )
            self.assertEqual(202, status, payload)
            self.assertEqual("operation", payload["tier"])
            self.assertEqual("tool_call", payload["source"])

    def test_plain_attack_cancel_without_a_named_operation_still_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory,
                {
                    "transcript": "공격 취소",
                    "update_id": "voice-cancel-all-1",
                    "tool_arguments": {
                        "command_layer": "macro",
                        "goal": "공격 취소",
                        "macro": {"production_targets": [MARINE]},
                    },
                },
            )
            self.assertEqual(202, status, payload)
            self.assertEqual("emergency", payload["tier"])
            self.assertEqual("emergency_override", payload["source"])

    def test_clarification_is_not_submitted(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory,
                {
                    "transcript": "클로킹",
                    "tool_arguments": {
                        "command_layer": "micro",
                        "goal": "클로킹",
                        "clarification": "어떤 유닛을 클로킹할까요?",
                    },
                },
            )
        self.assertEqual(200, status)
        self.assertFalse(payload["accepted"])
        self.assertEqual("clarification", payload["tier"])
        self.assertEqual("clarification_required", payload["status"])
        self.assertEqual("어떤 유닛을 클로킹할까요?", payload["clarification_prompt"])
        self.assertNotIn("update_id", payload)

    def test_invalid_payloads_are_400_and_event_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = [
                {"tool_arguments": {"command_layer": "micro", "goal": "g",
                                    "micro": [{"ability": "stimpack"}], "unit_tags": [7]}},
                {"tool_arguments": {"command_layer": "operation", "goal": "g",
                                    "operations": [{"task_type": "scout_with_units", "x": 1, "y": 2}]}},
                {"tool_arguments": {"command_layer": "macro", "goal": "g"}},
            ]
            for doc in cases:
                with self.subTest(doc):
                    status, payload = self.intent(directory, doc)
                    self.assertEqual(400, status, payload)
                    self.assertFalse(payload["accepted"])
                    self.assertEqual("invalid_intent", payload["status"])
        self.assertEqual(3, len(self.events()))

    def test_malformed_requests(self):
        status, raw = self.request("POST", "/api/realtime/intent", None)
        self.assertEqual(400, status)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request(
                "POST", "/api/realtime/intent", body=b"{nope",
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(400, connection.getresponse().status)
        finally:
            connection.close()
        for doc in ([], {}, {"transcript": 5}, {"tool_arguments": []}, {"transcript": "   "}):
            with self.subTest(doc):
                status, _raw = self.request("POST", "/api/realtime/intent", doc)
                self.assertEqual(400, status)

    def test_unresolved_transcript_falls_to_text_llm_lane(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory, {"transcript": "마린 두 기 정찰해", "update_id": "voice-text-1"}
            )
        self.assertEqual(202, status, payload)
        self.assertEqual("unresolved", payload["tier"])
        self.assertEqual("text_llm", payload["source"])

    def test_micromachine_status_reports_direct_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            status, raw = self.request(
                "GET", "/api/micromachine/status?blackboard_dir=" + directory
            )
        self.assertEqual(200, status)
        runtime = json.loads(raw)["direct_runtime"]
        self.assertEqual("dry_run_fake_bot", runtime["kind"])
        self.assertIn("DemoFakeBotAI", runtime["evidence"])

    def test_direct_runtime_is_server_derived_never_client_set(self):
        with tempfile.TemporaryDirectory() as directory:
            status, payload = self.intent(
                directory,
                {"transcript": "스팀팩", "direct_runtime": {"kind": "live", "evidence": "lie"}},
            )
        self.assertEqual("dry_run_fake_bot", payload["direct_runtime"]["kind"])

    def test_direct_runtime_live_and_unknown_descriptors(self):
        class Bot:
            pass

        class Adapter:
            bot = Bot()

        class Executor:
            bot = Adapter()

        class Session:
            executor = Executor()

            async def process_text(self, text):  # pragma: no cover - not invoked.
                raise AssertionError

        live = SessionLoopBridge(session=Session()).direct_runtime_descriptor()
        self.assertEqual("live", live["kind"])
        self.assertIn("Bot", live["evidence"])

        class Bare:
            async def process_text(self, text):  # pragma: no cover - not invoked.
                raise AssertionError

        unknown = SessionLoopBridge(session=Bare()).direct_runtime_descriptor()
        self.assertEqual("unknown", unknown["kind"])


@pytest.mark.skipif(
    os.environ.get("VOI_LIVE_REALTIME") != "1",
    reason="live proxy test; set VOI_LIVE_REALTIME=1 (needs MYPROXY_API_KEY)",
)
def test_live_proxy_mints_an_ephemeral_secret():
    config = ri.load_upstream_config()
    assert config.api_key, "set MYPROXY_API_KEY / VOI_REALTIME_API_KEY"
    started = time.monotonic()
    session = ri.mint_client_secret(config)
    elapsed = time.monotonic() - started
    assert session["client_secret"].startswith("ek_")
    assert session["client_secret"] != config.api_key
    assert session["expires_at"] > time.time()
    assert session["ws_url"].startswith("wss://")
    assert session["tools"][0]["name"] == "submit_command"
    print(
        f"live mint ok in {elapsed:.2f}s model={session['model']} "
        f"features={session['features']}"
    )




class RateLimitedUpstreamTest(unittest.TestCase):
    def test_upstream_429_is_reported_as_429_and_scrubbed(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.dumps(
                    {"error": {"code": "realtime_client_secret_capacity_exhausted",
                               "echo": LONG_LIVED_KEY}}
                ).encode()
                self.send_response(429)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        config = ri.RealtimeUpstreamConfig(
            base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
            model="m",
            api_key=LONG_LIVED_KEY,
        )
        with self.assertRaises(ri.RealtimeSessionError) as caught:
            ri.mint_client_secret(config)
        self.assertEqual(429, caught.exception.status)
        self.assertNotIn(LONG_LIVED_KEY, caught.exception.message)
        self.assertNotIn(LONG_LIVED_KEY, repr(config))


if __name__ == "__main__":
    unittest.main()
