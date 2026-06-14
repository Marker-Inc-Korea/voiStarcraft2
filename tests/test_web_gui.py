"""W3 acceptance tests for the stdlib-only commander web GUI.

Every server test binds an ephemeral localhost port (``port=0``) and talks
plain ``http.client``; no FastAPI/Flask, no network beyond loopback, no
optional dependencies, no API keys. Asynchronous outcomes are polled with a
hard deadline instead of fixed sleeps.
"""

import contextlib
import http.client
import inspect
import io
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from starcraft_commander import web_gui
from starcraft_commander.demo_sc2 import build_dry_run_session
from starcraft_commander.llm_interpreter import LocalLLMControl
from starcraft_commander.web_gui import (
    DEFAULT_WEB_GUI_PORT,
    WEB_GUI_TOKEN_HEADER,
    SessionLoopBridge,
    WEB_GUI_HOST,
    WEB_GUI_PAGE_TITLE,
    WEB_GUI_STATUS_COLORS,
    WebGuiBridgeInterface,
    WebGuiServer,
    render_web_gui_page,
)


POLL_DEADLINE_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.05
EXECUTED_FAMILY_STATUSES = frozenset({"executed", "partially_executed"})
BRIDGE_THREAD_NAME = "voistarcraft-web-gui-session-loop"


def contains_hangul(text):
    """Return whether the text contains at least one Hangul syllable."""

    return any("가" <= character <= "힣" for character in str(text))


def bridge_threads_alive():
    """Return every live bridge worker thread (should be empty after stop)."""

    return [
        thread
        for thread in threading.enumerate()
        if thread.name == BRIDGE_THREAD_NAME and thread.is_alive()
    ]


class FakeConfiguredLLMControl:
    """Configured LLM control test double that avoids provider SDK calls."""

    def snapshot(self):
        return {
            "provider": "openai",
            "model": "gpt-test",
            "configured": True,
            "key_present": True,
        }

    def configure(self, provider, api_key, model=""):
        return self.snapshot()


class WebGuiServerHTTPTest(unittest.TestCase):
    """End-to-end HTTP tests against a dry-run session on an ephemeral port."""

    def setUp(self):
        session, self.bot = build_dry_run_session()
        self.bridge = SessionLoopBridge(
            session=session,
            llm_control=FakeConfiguredLLMControl(),
        )
        self.bridge.start()
        self.addCleanup(self.bridge.stop)
        self.server = WebGuiServer(bridge=self.bridge, port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=5
        )
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            content_type = response.getheader("Content-Type", "")
            return response.status, content_type, payload
        finally:
            connection.close()

    def get_json(self, path, expected_status=200):
        status, content_type, payload = self.request("GET", path)
        self.assertEqual(status, expected_status)
        self.assertIn("application/json", content_type)
        return json.loads(payload.decode("utf-8"))

    def post_command(self, text):
        body = json.dumps({"text": text}).encode("utf-8")
        return self.request(
            "POST",
            "/api/command",
            body=body,
            headers={"Content-Type": "application/json"},
        )

    def poll_history_until(self, predicate, description):
        deadline = time.monotonic() + POLL_DEADLINE_SECONDS
        events = []
        while time.monotonic() < deadline:
            document = self.get_json("/api/history?after=0")
            events = document["events"]
            matched = [event for event in events if predicate(event)]
            if matched:
                return matched
            time.sleep(POLL_INTERVAL_SECONDS)
        self.fail(
            f"No history event matched within {POLL_DEADLINE_SECONDS}s "
            f"({description}). Events: {events!r}"
        )

    def test_index_page_serves_korean_ui_with_polling_script(self):
        status, content_type, payload = self.request("GET", "/")
        page = payload.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        for fragment in (
            "커맨더",
            WEB_GUI_PAGE_TITLE,
            "/api/history?after=",
            "/api/state",
            "/api/command",
            "전송",
            "커맨더 채팅",
            "전장 대시보드",
            "전략 브리핑",
            "이전 대화 일부 생략",
            "녹음중",
            "응답 하는중",
            "voice-wave",
            "SpeechRecognition",
            "English",
            "中文",
            "LLM 필수",
            "setInterval(pollHistory",
            "setInterval(pollState",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_report_command_yields_read_only_event_with_korean_narration(self):
        status, _content_type, payload = self.post_command("상황 보고해줘")
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload.decode("utf-8")), {"accepted": True})

        matched = self.poll_history_until(
            lambda event: event.get("status") == "read_only",
            "read_only outcome for 상황 보고해줘",
        )
        event = matched[0]
        self.assertEqual(event["command_text"], "상황 보고해줘")
        self.assertTrue(str(event["narration"]).strip())
        self.assertTrue(contains_hangul(event["narration"]))
        self.assertIsInstance(event["seq"], int)
        self.assertGreaterEqual(event["seq"], 1)

    def test_train_command_yields_executed_family_event(self):
        status, _content_type, _payload = self.post_command("SCV 계속 찍어")
        self.assertEqual(status, 202)

        matched = self.poll_history_until(
            lambda event: event.get("status") in EXECUTED_FAMILY_STATUSES,
            "executed-family outcome for SCV 계속 찍어",
        )
        event = matched[0]
        self.assertEqual(event["command_text"], "SCV 계속 찍어")
        self.assertTrue(str(event["narration"]).strip())
        self.assertTrue(contains_hangul(event["narration"]))

    def test_state_endpoint_exposes_fake_bot_economy(self):
        document = self.get_json("/api/state")
        self.assertIs(document["available"], True)
        self.assertEqual(document["minerals"], 400)
        for key in (
            "minerals",
            "vespene",
            "supply_used",
            "supply_cap",
            "supply_left",
            "own_units",
            "own_structures",
            "idle_worker_count",
            "army_count",
        ):
            with self.subTest(key=key):
                self.assertIn(key, document)
        self.assertEqual(document["supply_used"], 20)
        self.assertEqual(document["supply_cap"], 21)
        self.assertEqual(document["own_units"].get("SCV"), 12)

    def test_llm_status_endpoint_never_exposes_key(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        server = WebGuiServer(bridge=bridge, port=0)
        server.start()
        self.addCleanup(server.stop)

        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            connection.request("GET", "/api/llm")
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        document = json.loads(payload.decode("utf-8"))
        self.assertFalse(document["configured"])
        self.assertNotIn("api_key", document)

    def test_command_is_rejected_until_llm_is_configured(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        server = WebGuiServer(bridge=bridge, port=0)
        server.start()
        self.addCleanup(server.stop)

        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            body = json.dumps({"text": "상태확인"}).encode("utf-8")
            connection.request(
                "POST",
                "/api/command",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["accepted"], False)
        self.assertIn("LLM", payload["error"])
        self.assertTrue(contains_hangul(payload["error"]))

    def test_llm_config_endpoint_sets_process_local_key(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=LocalLLMControl(provider="openai"),
        )
        bridge.start()
        self.addCleanup(bridge.stop)
        server = WebGuiServer(bridge=bridge, port=0)
        server.start()
        self.addCleanup(server.stop)

        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            body = json.dumps(
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "api_key": "sk-local-only-test",
                }
            )
            connection.request(
                "POST",
                "/api/llm",
                body=body.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["key_present"])
        self.assertEqual(payload["provider"], "openai")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertNotIn("sk-local-only-test", json.dumps(payload))

    def test_history_after_param_filters_already_seen_events(self):
        self.post_command("상황 보고해줘")
        self.poll_history_until(
            lambda event: event.get("status") == "read_only",
            "read_only outcome before after-filter check",
        )
        document = self.get_json("/api/history?after=0")
        latest = document["latest"]
        self.assertGreaterEqual(latest, 1)
        filtered = self.get_json(f"/api/history?after={latest}")
        self.assertEqual(filtered["events"], [])
        self.assertEqual(filtered["latest"], latest)

    def test_malformed_command_bodies_are_rejected_with_400(self):
        bad_bodies = (
            ("not json", b"this is not json"),
            ("non-object json", b'["text"]'),
            ("missing text", b"{}"),
            ("empty text", json.dumps({"text": ""}).encode("utf-8")),
            ("blank text", json.dumps({"text": "   "}).encode("utf-8")),
            ("non-string text", json.dumps({"text": 42}).encode("utf-8")),
        )
        for label, body in bad_bodies:
            with self.subTest(label=label):
                status, _content_type, payload = self.request(
                    "POST",
                    "/api/command",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                document = json.loads(payload.decode("utf-8"))
                self.assertEqual(status, 400)
                self.assertIs(document["accepted"], False)
                self.assertTrue(contains_hangul(document["error"]))

    def test_bad_history_after_param_is_rejected_with_400(self):
        document = self.get_json("/api/history?after=abc", expected_status=400)
        self.assertTrue(contains_hangul(document["error"]))

    def test_unknown_routes_return_404_json(self):
        for method, path in (("GET", "/nope"), ("POST", "/nope"), ("GET", "/api/nope")):
            with self.subTest(method=method, path=path):
                body = b"{}" if method == "POST" else None
                headers = (
                    {"Content-Type": "application/json"} if method == "POST" else {}
                )
                status, content_type, payload = self.request(
                    method, path, body=body, headers=headers
                )
                self.assertEqual(status, 404)
                self.assertIn("application/json", content_type)
                document = json.loads(payload.decode("utf-8"))
                self.assertTrue(contains_hangul(document["error"]))

    def test_server_defaults_to_localhost_without_token(self):
        self.assertEqual(self.server.host, "127.0.0.1")
        self.assertEqual(WEB_GUI_HOST, "127.0.0.1")
        self.assertTrue(self.server.url.startswith("http://127.0.0.1:"))
        parameters = inspect.signature(WebGuiServer.__init__).parameters
        self.assertEqual(
            list(parameters), ["self", "bridge", "port", "host", "auth_token"]
        )

    def test_token_protects_network_exposed_server(self):
        server = WebGuiServer(
            bridge=self.bridge,
            port=0,
            host="0.0.0.0",
            auth_token="secret-token",
        )
        server.start()
        self.addCleanup(server.stop)
        self.assertEqual(server.host, "0.0.0.0")
        self.assertIn("?token=secret-token", server.url)

        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            connection.request("GET", "/api/state")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 403)
        finally:
            connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            connection.request("GET", "/api/state?token=secret-token")
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(payload.decode("utf-8"))["available"])
        finally:
            connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            connection.request(
                "GET",
                "/api/state",
                headers={WEB_GUI_TOKEN_HEADER: "secret-token"},
            )
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(payload.decode("utf-8"))["available"])
        finally:
            connection.close()

    def test_server_stop_is_idempotent_and_joins_thread(self):
        self.assertTrue(self.server.is_running)
        self.server.stop()
        self.assertFalse(self.server.is_running)
        self.server.stop()  # Second stop must be a quiet no-op.


class SessionLoopBridgeTest(unittest.TestCase):
    """Bridge lifecycle, protocol conformance, and honesty tests (no HTTP)."""

    def test_bridge_satisfies_web_gui_bridge_protocol(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        self.assertIsInstance(bridge, WebGuiBridgeInterface)

    def test_constructor_rejects_invalid_seams(self):
        session, _bot = build_dry_run_session()
        cases = (
            ("session without process_text", dict(session=object())),
            (
                "history without record",
                dict(session=session, history=SimpleNamespace(since=len, latest_seq=len)),
            ),
            (
                "state resolver without resolve",
                dict(session=session, state_resolver=object()),
            ),
        )
        for label, kwargs in cases:
            with self.subTest(label=label):
                with self.assertRaises(TypeError):
                    SessionLoopBridge(**kwargs)

    def test_submit_command_rejects_bad_text_and_requires_start(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        with self.assertRaises(RuntimeError):
            bridge.submit_command("상황 보고해줘")
        bridge.start()
        self.addCleanup(bridge.stop)
        with self.assertRaises(TypeError):
            bridge.submit_command(123)
        with self.assertRaises(ValueError):
            bridge.submit_command("   ")

    def test_commands_record_sequential_history_events(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        bridge.submit_command("상황 보고해줘")
        bridge.submit_command("SCV 계속 찍어")

        deadline = time.monotonic() + POLL_DEADLINE_SECONDS
        while time.monotonic() < deadline and bridge.latest_seq() < 2:
            time.sleep(POLL_INTERVAL_SECONDS)
        self.assertGreaterEqual(bridge.latest_seq(), 2)

        events = bridge.history_since(0)
        sequences = [event["seq"] for event in events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        statuses = [event["status"] for event in events]
        self.assertIn("read_only", statuses)
        self.assertTrue(EXECUTED_FAMILY_STATUSES.intersection(statuses))
        self.assertEqual(bridge.history_since(bridge.latest_seq()), ())

    def test_session_exception_recorded_as_blocked_outcome(self):
        class ExplodingSession:
            async def process_text(self, text):
                raise RuntimeError("scripted session failure")

        bridge = SessionLoopBridge(session=ExplodingSession())
        bridge.start()
        self.addCleanup(bridge.stop)
        bridge.submit_command("마린 뽑아")

        deadline = time.monotonic() + POLL_DEADLINE_SECONDS
        while time.monotonic() < deadline and bridge.latest_seq() < 1:
            time.sleep(POLL_INTERVAL_SECONDS)
        events = bridge.history_since(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "blocked")
        self.assertEqual(events[0]["command_text"], "마린 뽑아")
        self.assertTrue(contains_hangul(events[0]["narration"]))

    def test_state_snapshot_reads_fake_bot_through_adapter(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        snapshot = bridge.state_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["minerals"], 400)
        self.assertEqual(snapshot["supply_used"], 20)
        self.assertEqual(snapshot["supply_cap"], 21)

    def test_state_snapshot_is_none_safe_without_bound_runtime(self):
        async def process_text(text):
            return ()

        cases = (
            ("session without executor", SimpleNamespace(process_text=process_text)),
            (
                "executor without bot",
                SimpleNamespace(
                    process_text=process_text,
                    executor=SimpleNamespace(bot=None),
                ),
            ),
        )
        for label, session in cases:
            with self.subTest(label=label):
                bridge = SessionLoopBridge(session=session)
                self.assertIsNone(bridge.state_snapshot())

    def test_stop_terminates_worker_thread_cleanly(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.assertTrue(bridge.is_running)
        self.assertTrue(bridge_threads_alive())
        bridge.submit_command("상황 보고해줘")
        bridge.stop()
        self.assertFalse(bridge.is_running)
        self.assertEqual(bridge_threads_alive(), [])
        bridge.stop()  # Second stop must be a quiet no-op.
        with self.assertRaises(RuntimeError):
            bridge.submit_command("상황 보고해줘")
        # Pending commands submitted before stop() were drained, not dropped.
        self.assertGreaterEqual(bridge.latest_seq(), 1)

    def test_injected_history_store_is_duck_typed(self):
        recorded = []

        class RecordingHistory:
            def record(self, outcome):
                recorded.append(outcome)
                return len(recorded)

            def since(self, seq):
                return [{"seq": index + 1} for index in range(len(recorded))][seq:]

            def latest_seq(self):
                return len(recorded)

        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, history=RecordingHistory())
        bridge.start()
        bridge.submit_command("상황 보고해줘")
        deadline = time.monotonic() + POLL_DEADLINE_SECONDS
        while time.monotonic() < deadline and not recorded:
            time.sleep(POLL_INTERVAL_SECONDS)
        bridge.stop()
        self.assertTrue(recorded)
        self.assertEqual(recorded[0].status, "read_only")
        self.assertEqual(bridge.latest_seq(), len(recorded))


class RenderWebGuiPageTest(unittest.TestCase):
    """Static checks on the embedded single-page Korean UI."""

    def test_page_contains_korean_chrome_and_state_panel_labels(self):
        page = render_web_gui_page()
        for fragment in (
            WEB_GUI_PAGE_TITLE,
            "커맨더",
            "대시보드",
            "커맨더 채팅",
            "전송",
            "미네랄",
            "가스",
            "보급",
            "일꾼",
            "병력",
            "건물",
            "전략 브리핑",
            "Strategy Briefing",
            "战略简报",
            "MAX_CHAT_EVENTS",
            "briefingAdvice",
            "appendPendingCommand",
            "setupVoiceInput",
            "voice-wave",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_page_has_status_color_class_per_outcome_status(self):
        page = render_web_gui_page()
        for status, color in WEB_GUI_STATUS_COLORS.items():
            with self.subTest(status=status):
                self.assertIn(f".status-{status}", page)
                self.assertIn(color, page)

    def test_page_polls_without_external_cdn(self):
        page = render_web_gui_page()
        self.assertIn("/api/history?after=", page)
        self.assertIn("/api/state", page)
        self.assertIn(f"POLL_INTERVAL_MS = {web_gui.WEB_GUI_POLL_INTERVAL_MS}", page)
        for forbidden in ("https://cdn.", "http://cdn.", "unpkg.com", "jsdelivr"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, page)


class WebGuiServerConstructionTest(unittest.TestCase):
    """Constructor validation without binding any sockets."""

    def setUp(self):
        session, _bot = build_dry_run_session()
        self.bridge = SessionLoopBridge(session=session)

    def test_default_port_is_8350(self):
        self.assertEqual(DEFAULT_WEB_GUI_PORT, 8350)
        server = WebGuiServer(bridge=self.bridge)
        self.assertEqual(server.port, 8350)
        self.assertEqual(server.url, "http://127.0.0.1:8350")

    def test_rejects_non_bridge_and_bad_ports(self):
        with self.assertRaises(TypeError):
            WebGuiServer(bridge=object())
        for bad_port, error_type in ((True, TypeError), ("80", TypeError), (-1, ValueError), (70000, ValueError)):
            with self.subTest(bad_port=bad_port):
                with self.assertRaises(error_type):
                    WebGuiServer(bridge=self.bridge, port=bad_port)

    def test_rejects_network_bind_without_token(self):
        with self.assertRaises(ValueError):
            WebGuiServer(bridge=self.bridge, host="0.0.0.0")
        server = WebGuiServer(
            bridge=self.bridge,
            host="0.0.0.0",
            auth_token="secret-token",
        )
        self.assertEqual(server.host, "0.0.0.0")


class WebGuiMainTest(unittest.TestCase):
    """Entrypoint behavior: dry-run wiring and the non-dry-run Korean pointer."""

    def test_main_without_dry_run_prints_korean_pointer(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = web_gui.main([])
        output = stdout.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertTrue(contains_hangul(output))
        self.assertIn("--dry-run", output)
        self.assertIn("demo_sc2 --gui", output)

    def test_main_dry_run_serves_until_interrupt_then_cleans_up(self):
        stdout = io.StringIO()
        with mock.patch.object(
            web_gui, "_wait_for_interrupt", side_effect=KeyboardInterrupt
        ):
            with contextlib.redirect_stdout(stdout):
                exit_code = web_gui.main(["--dry-run", "--port", "0"])
        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("http://127.0.0.1:", output)
        self.assertTrue(contains_hangul(output))
        self.assertEqual(bridge_threads_alive(), [])

    def test_main_accepts_companion_host_with_token(self):
        stdout = io.StringIO()
        with mock.patch.object(
            web_gui, "_wait_for_interrupt", side_effect=KeyboardInterrupt
        ):
            with contextlib.redirect_stdout(stdout):
                exit_code = web_gui.main(
                    [
                        "--dry-run",
                        "--port",
                        "0",
                        "--host",
                        "0.0.0.0",
                        "--token",
                        "secret-token",
                    ]
                )
        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("http://0.0.0.0:", output)
        self.assertIn("?token=secret-token", output)
        self.assertEqual(bridge_threads_alive(), [])


if __name__ == "__main__":
    unittest.main()
