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
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http import HTTPStatus
from types import SimpleNamespace
from unittest import mock

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
)
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
BRIDGE_THREAD_NAME = "voiStarcraft2-web-gui-session-loop"


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


class FakeFailingLLMControl:
    """LLM control test double that raises one setup failure."""

    def __init__(self, error):
        self.error = error

    def snapshot(self):
        return {
            "provider": "openai",
            "model": "gpt-test",
            "configured": False,
            "key_present": False,
        }

    def configure(self, provider, api_key, model=""):
        raise self.error


class ProviderRejectedSetupError(RuntimeError):
    """Provider-shaped setup failure without importing provider SDKs."""


class ExplodingStateBridge:
    """Bridge test double that leaks a sentinel key through a backend error."""

    def __init__(self, secret):
        self.secret = secret

    def submit_command(self, text):
        raise AssertionError("commands are not used by this bridge")

    def state_snapshot(self):
        raise RuntimeError(f"state resolver leaked {self.secret}")

    def history_since(self, seq):
        return ()

    def latest_seq(self):
        return 0

    def llm_settings_snapshot(self):
        return {
            "provider": "openai",
            "model": "gpt-test",
            "configured": True,
            "key_present": True,
        }

    def configure_llm(self, provider, api_key, model=""):
        return self.llm_settings_snapshot()


class WebGuiServerHTTPTest(unittest.TestCase):
    """End-to-end HTTP tests against a dry-run session on an ephemeral port."""

    def setUp(self):
        self.session, self.bot = build_dry_run_session()
        self.bridge = SessionLoopBridge(
            session=self.session,
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

    def post_micromachine_modulation(self, payload):
        body = json.dumps(payload).encode("utf-8")
        return self.request(
            "POST",
            "/api/micromachine/modulate",
            body=body,
            headers={"Content-Type": "application/json"},
        )

    def post_llm_config_with_control(self, llm_control, api_key="unit-test-sensitive"):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, llm_control=llm_control)
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
                    "api_key": api_key,
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
        return response.status, payload

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
            "/api/micromachine/modulate",
            "/api/micromachine/status",
            "전송",
            "커맨더 채팅",
            "전장 대시보드",
            "전략 브리핑",
            "이전 대화 일부 생략",
            "녹음중",
            "응답 하는중",
            "압축 메모리",
            "COMPACT_AFTER_EVENTS",
            "compactRecentEventsIfNeeded",
            "voice-wave",
            "SpeechRecognition",
            "English",
            "中文",
            "LLM 필수",
            "LLM 키 상태 확인 실패",
            "MicroMachine 런타임 대기 중입니다.",
            "🚀 시작 메뉴얼",
            "startup-guide-entry",
            "renderStartupGuide",
            "collapsible-panel",
            "<details id=\"briefing-panel\" class=\"collapsible-panel\">",
            "MAX_CHAT_EVENTS = 36",
            "MAX_MESSAGE_PREVIEW_CHARS",
            "archivedChatEvents",
            "appendCompactText",
            "renderArchivedChatDetails",
            "messageExpand",
            "window.location.assign(status.url)",
            "live-open-button",
            "runtime-start-button",
            "runtime-refresh-button",
            "llm-provider-choice",
            "llm-model-select",
            "handleProviderChoiceChange",
            "onchange=\"handleProviderChoiceChange",
            "type=\"radio\"",
            "gpt-5.5",
            "gpt-5.4-mini",
            "gemini-3.5-flash",
            "grok-4.3",
            "/api/runtime/status",
            "/api/runtime/start",
            "parseJsonResponse",
            "micromachine-panel",
            "명령 라우팅 모드",
            "MicroMachine policy cockpit",
            "Legacy python-sc2 commander",
            "legacy-mode-warning",
            "COMMAND_MODE_MICROMACHINE",
            "COMMAND_MODE_LEGACY_COMMANDER",
            "setCommandMode",
            "submitMicroMachineModulation",
            "buildMicroMachineModulationPayload",
            "<details id=\"micromachine-panel\" class=\"collapsible-panel\">",
            "MicroMachine runtime / DSL evidence",
            "고급 직접 publish 테스트 텍스트",
            "고급 직접 publish 전송",
            "Semantic army group",
            "Location intent",
            "Unit classes",
            "Safety margin",
            "Scope duration seconds",
            "TTL seconds",
            "MicroMachine DSL publish",
            "micromachine-intervention-dashboard",
            "DSL intervention dashboard",
            "Consumed axes by manager",
            "Attack gate",
            "Recent tactical logs",
            "Raw modulation / telemetry evidence",
            "renderMicroMachineStatus",
            "renderMicroMachineIntervention",
            "pollMicroMachineStatus",
            "setInterval(pollHistory",
            "setInterval(pollState",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_micromachine_modulation_endpoint_publishes_to_blackboard(self):
        with tempfile.TemporaryDirectory() as directory:
            status, content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "탱크로 수비해",
                    "blackboard_dir": directory,
                    "current_frame": 12,
                    "update_id": "web-live-1",
                    "provider_output": {
                        "goal": "탱크로 수비해",
                        "override_level": "constraint",
                        "combat": {"defend_bias": 0.7, "aggression": -0.2},
                    },
                }
            )

            self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
            self.assertIn("application/json", content_type)
            document = json.loads(payload.decode("utf-8"))
            self.assertTrue(document["accepted"], document)
            self.assertTrue(document["ok"], document)
            self.assertEqual("published", document["status"])
            self.assertEqual("web-live-1", document["update"]["update_id"])
            self.assertEqual(directory, document["blackboard_dir"])
            with open(f"{directory}/latest_modulation.kv", encoding="utf-8") as handle:
                kv_text = handle.read()
                self.assertIn("combat.defend_bias=0.7", kv_text)
                self.assertIn("workers.repeat_order_guard_frames=32", kv_text)

    def test_micromachine_modulation_endpoint_compiles_plain_gui_text(self):
        with tempfile.TemporaryDirectory() as directory:
            status, content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "탱크로 안전하게 수비하면서 버텨",
                    "blackboard_dir": directory,
                    "current_frame": 21,
                    "update_id": "web-keyword-1",
                }
            )

            self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
            self.assertIn("application/json", content_type)
            document = json.loads(payload.decode("utf-8"))
            self.assertTrue(document["accepted"], document)
            self.assertTrue(document["ok"], document)
            self.assertEqual("published", document["status"])
            self.assertEqual("web-keyword-1", document["update"]["update_id"])
            self.assertEqual("constraint", document["compile_result"]["vector"]["override_level"])
            with open(f"{directory}/latest_modulation.kv", encoding="utf-8") as handle:
                kv = handle.read()
            self.assertIn("combat.defend_bias=0.65", kv)
            self.assertIn("squad.defense_bias=0.45", kv)

    def test_micromachine_modulation_endpoint_publishes_semantic_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "메인 병력으로 적 앞마당을 압박해",
                    "blackboard_dir": directory,
                    "current_frame": 30,
                    "update_id": "web-scope-1",
                    "semantic_scope": {
                        "army_group": "main",
                        "unit_classes": ["marine", "siege_tank"],
                        "location_intent": "enemy_natural",
                        "duration_seconds": 120,
                        "require_safety_margin": 0.25,
                    },
                    "ttl_seconds": 180,
                }
            )

            self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
            document = json.loads(payload.decode("utf-8"))
            self.assertTrue(document["ok"], document)
            scope = document["compile_result"]["vector"]["scope"]
            self.assertEqual("main", scope["army_group"])
            self.assertEqual(["marine", "siege_tank"], scope["unit_classes"])
            self.assertEqual("enemy_natural", scope["location_intent"])
            self.assertEqual(120, scope["duration_seconds"])
            self.assertEqual(180, document["compile_result"]["vector"]["ttl_seconds"])
            with open(f"{directory}/latest_modulation.kv", encoding="utf-8") as handle:
                kv = handle.read()
            self.assertIn("scope.army_group=main", kv)
            self.assertIn("scope.location_intent=enemy_natural", kv)
            self.assertIn("scope.unit_classes=marine,siege_tank", kv)

    def test_micromachine_modulation_preserves_strict_partial_scope_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "메인 병력만 엄격하게 적 앞마당 압박",
                    "blackboard_dir": directory,
                    "current_frame": 31,
                    "update_id": "web-strict-scope-1",
                    "provider_output": {
                        "goal": "strict main pressure",
                        "override_level": "bias",
                        "combat": {"aggression": 0.25},
                    },
                    "semantic_scope": {
                        "allow_partial_scope": False,
                    },
                }
            )

            self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
            document = json.loads(payload.decode("utf-8"))
            self.assertTrue(document["ok"], document)
            scope = document["compile_result"]["vector"]["scope"]
            self.assertIn("allow_partial_scope", scope)
            self.assertFalse(scope["allow_partial_scope"])
            with open(f"{directory}/latest_modulation.kv", encoding="utf-8") as handle:
                self.assertIn("scope.allow_partial_scope=false", handle.read())
            status_document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )
            requested_scope = status_document["intervention"]["tactical_scope"]["requested"]
            self.assertIn("allow_partial_scope", requested_scope)
            self.assertFalse(requested_scope["allow_partial_scope"])

    def test_micromachine_modulation_accepts_string_unit_class_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            for raw_unit_classes, expected in (
                ("siege_tank, workers", ["siege_tank", "workers"]),
                ("siege tank worker", ["siege_tank", "workers"]),
            ):
                with self.subTest(raw_unit_classes=raw_unit_classes):
                    status, _content_type, payload = self.post_micromachine_modulation(
                        {
                            "text": "유닛 클래스 범위 테스트",
                            "blackboard_dir": directory,
                            "current_frame": 32,
                            "provider_output": {
                                "goal": "scope unit class alias",
                                "override_level": "bias",
                                "combat": {"aggression": 0.1},
                            },
                            "semantic_scope": {
                                "unit_classes": raw_unit_classes,
                            },
                        }
                    )

                    self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
                    document = json.loads(payload.decode("utf-8"))
                    self.assertTrue(document["ok"], document)
                    scope = document["compile_result"]["vector"]["scope"]
                    self.assertEqual(expected, scope["unit_classes"])

    def test_micromachine_modulation_endpoint_rejects_raw_scope_control(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "이 유닛으로 공격해",
                    "blackboard_dir": directory,
                    "semantic_scope": {
                        "unit_tag": 123,
                    },
                }
            )

            self.assertEqual(HTTPStatus.BAD_REQUEST, HTTPStatus(status))
            document = json.loads(payload.decode("utf-8"))
            self.assertFalse(document["accepted"])
            self.assertIn("raw runtime control", document["error"])

    def test_micromachine_modulation_endpoint_rejects_raw_keyboard_control(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "단축키로 유닛을 보내",
                    "blackboard_dir": directory,
                    "provider_output": {
                        "goal": "unsafe direct control",
                        "keyboard": {"press": "a"},
                    },
                }
            )

            self.assertEqual(HTTPStatus.BAD_REQUEST, HTTPStatus(status))
            document = json.loads(payload.decode("utf-8"))
            self.assertFalse(document["accepted"])
            self.assertIn("raw runtime control", document["error"])
            self.assertIn("keyboard", document["error"])

    def test_micromachine_modulation_merges_scope_into_wrapped_provider_output(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "적 앞마당 압박",
                    "blackboard_dir": directory,
                    "current_frame": 12,
                    "update_id": "web-wrapper-scope-1",
                    "provider_output": {
                        "modulation": {
                            "goal": "wrapped pressure",
                            "override_level": "bias",
                            "combat": {"aggression": 0.25},
                        },
                    },
                    "semantic_scope": {
                        "army_group": "main",
                        "location_intent": "enemy_natural",
                    },
                }
            )

            self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
            document = json.loads(payload.decode("utf-8"))
            self.assertTrue(document["ok"], document)
            scope = document["compile_result"]["vector"]["scope"]
            self.assertEqual("main", scope["army_group"])
            self.assertEqual("enemy_natural", scope["location_intent"])
            with open(f"{directory}/latest_modulation.kv", encoding="utf-8") as handle:
                kv = handle.read()
            self.assertIn("scope.army_group=main", kv)
            self.assertIn("scope.location_intent=enemy_natural", kv)

    def test_micromachine_modulation_preserves_wrapped_terminal_provider_output(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "불확실하면 물어봐",
                    "blackboard_dir": directory,
                    "provider_output": {
                        "modulation": {
                            "status": "clarification_required",
                            "clarification_prompt": "공격 타이밍을 더 구체화해 주세요.",
                        },
                    },
                    "semantic_scope": {
                        "army_group": "main",
                        "location_intent": "enemy_natural",
                    },
                }
            )

            self.assertEqual(HTTPStatus.OK, HTTPStatus(status))
            document = json.loads(payload.decode("utf-8"))
            self.assertFalse(document["accepted"])
            self.assertFalse(document["ok"])
            self.assertIsNone(document["update"])
            self.assertEqual("clarification_required", document["status"])
            self.assertEqual(
                "clarification_required",
                document["compile_result"]["status"],
            )
            self.assertEqual(
                "공격 타이밍을 더 구체화해 주세요.",
                document["compile_result"]["clarification_prompt"],
            )
            self.assertFalse(os.path.exists(f"{directory}/latest_modulation.kv"))

    def test_micromachine_modulation_rejects_unsafe_update_id(self):
        with tempfile.TemporaryDirectory() as directory:
            status, content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "수비",
                    "blackboard_dir": directory,
                    "current_frame": 1,
                    "update_id": 'bad"id',
                    "provider_output": {
                        "goal": "수비",
                        "combat": {"defend_bias": 0.5},
                    },
                }
            )

            self.assertEqual(HTTPStatus.BAD_REQUEST, HTTPStatus(status))
            self.assertIn("application/json", content_type)
            document = json.loads(payload.decode("utf-8"))
            self.assertFalse(document["accepted"])
            self.assertIn("update_id", document["error"])

    def test_micromachine_status_endpoint_renders_latest_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "수비",
                    "blackboard_dir": directory,
                    "current_frame": 1,
                    "update_id": "web-status-1",
                    "provider_output": {
                        "goal": "수비",
                        "combat": {"defend_bias": 0.5},
                    },
                }
            )

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            self.assertTrue(document["enabled"])
            self.assertEqual(directory, document["blackboard_dir"])
            active = document["dashboard"]["active_updates"]
            self.assertEqual("web-status-1", active[0]["update_id"])
            self.assertIn("combat", active[0]["manager_bias_domains"])
            self.assertEqual("published", document["status"])
            self.assertEqual("web-status-1", document["update"]["update_id"])
            self.assertEqual("pending_telemetry", document["consumption_status"])
            self.assertFalse(document["consumed"])
            intervention = document["intervention"]
            self.assertFalse(intervention["applied"])
            self.assertEqual("web-status-1", intervention["latest_update_id"])
            self.assertEqual(["workers", "combat"], intervention["manager_bias_domains"])
            self.assertEqual("수비", intervention["goal"])

    def test_micromachine_status_requires_post_publish_telemetry_before_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "수비",
                    "blackboard_dir": directory,
                    "current_frame": 10,
                    "update_id": "web-consume-1",
                    "provider_output": {
                        "goal": "수비",
                        "combat": {"defend_bias": 0.5},
                    },
                }
            )
            telemetry_path = f"{directory}/latest_telemetry.json"
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 10,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {},
                "active_modulation_ids": ["web-consume-1"],
                "last_failure": None,
            }
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)

            same_frame = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )
            self.assertEqual("pending_consumption", same_frame["consumption_status"])
            self.assertFalse(same_frame["consumed"])
            self.assertFalse(same_frame["intervention"]["applied"])
            self.assertTrue(same_frame["intervention"]["policy_active"] is False)

            telemetry["frame"] = 11
            telemetry["active_modulation_ids"] = ["stale-update"]
            telemetry["managers"] = {
                "GameCommander": {
                    "policy_active": True,
                    "update_id": "stale-update",
                }
            }
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)

            stale_frame = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )
            self.assertEqual("pending_consumption", stale_frame["consumption_status"])
            self.assertFalse(stale_frame["intervention"]["applied"])
            self.assertFalse(stale_frame["intervention"]["policy_active"])

            telemetry["active_modulation_ids"] = ["web-consume-1"]
            telemetry["managers"] = {
                "GameCommander": {
                    "policy_active": True,
                    "update_id": "web-consume-1",
                }
            }
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)

            later_frame = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )
            self.assertEqual("consumed", later_frame["consumption_status"])
            self.assertTrue(later_frame["consumed"])
            self.assertTrue(later_frame["intervention"]["applied"])
            self.assertTrue(later_frame["intervention"]["policy_active"])
            self.assertEqual(
                ["web-consume-1"],
                later_frame["intervention"]["active_modulation_ids"],
            )
            self.assertEqual(11, later_frame["intervention"]["telemetry_frame"])

    def test_micromachine_status_exposes_tactical_dashboard_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "메인 병력으로 적 앞마당을 contain 해",
                    "blackboard_dir": directory,
                    "current_frame": 40,
                    "update_id": "web-tactical-1",
                    "provider_output": {
                        "goal": "contain enemy natural",
                        "override_level": "bias",
                        "combat": {
                            "aggression": 0.45,
                            "target_priority_biases": {
                                "worker_line": 0.4,
                                "townhall": 0.25,
                            },
                        },
                        "squad": {"contain_bias": 0.35, "reinforce_bias": 0.2},
                        "scope": {
                            "army_group": "main",
                            "location_intent": "enemy_natural",
                            "min_units": 2,
                        },
                    },
                }
            )
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 46,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "GameCommander": {
                        "policy_active": True,
                        "update_id": "web-tactical-1",
                    },
                    "CombatCommander": {
                        "active": True,
                        "policy_active": True,
                        "aggression": 0.45,
                        "main_attack_order_status": "Attack",
                        "main_attack_order_reason": "VOI force threshold met",
                        "main_attack_unit_count": 2,
                        "main_attack_scope_min_units": 2,
                        "main_attack_scope_threshold_met": True,
                        "main_attack_simulation_won": True,
                        "consumed_axes": "combat.aggression,combat.target_priority_biases.*",
                    },
                    "Squad": {
                        "active": True,
                        "contain_bias": 0.35,
                        "scope_army_group": "main",
                        "scope_location_intent": "enemy_natural",
                        "scope_min_units": 2,
                        "target_worker_line_bias": 0.4,
                        "target_townhall_bias": 0.25,
                        "consumed_axes": "squad.contain_bias,scope.location_intent",
                    },
                    "WorkerManager": {
                        "active": True,
                        "repeat_order_guard_active": True,
                        "repeat_order_guard_frames": 32,
                        "repeat_order_suppressed_count": 7,
                        "consumed_axes": "workers.repeat_order_guard_frames",
                    },
                },
                "active_modulation_ids": ["web-tactical-1"],
                "last_failure": None,
            }
            with open(f"{directory}/latest_telemetry.json", "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            with open(f"{directory}/micromachine.log", "w", encoding="utf-8") as handle:
                handle.write(
                    "45: updateAttackSquads | MainAttackSquad new order = Attack enemy natural\n"
                    "46: calcTargets | target worker_line selected by policy modulation\n"
                )

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            intervention = document["intervention"]
            self.assertEqual("consumed", document["consumption_status"])
            self.assertEqual("contain", intervention["tactical_posture"])
            self.assertEqual(
                ["combat.aggression", "combat.target_priority_biases.*"],
                intervention["consumed_axes_by_manager"]["CombatCommander"],
            )
            self.assertEqual(
                ["workers.repeat_order_guard_frames"],
                intervention["consumed_axes_by_manager"]["WorkerManager"],
            )
            self.assertEqual(
                7,
                intervention["manager_snapshot"]["WorkerManager"][
                    "repeat_order_suppressed_count"
                ],
            )
            self.assertEqual("main", intervention["tactical_scope"]["requested"]["army_group"])
            self.assertEqual(
                "worker_line",
                intervention["target_priority"]["selected_target_class"],
            )
            self.assertEqual("Attack", intervention["attack_gate"]["status"])
            self.assertEqual(
                "VOI force threshold met",
                intervention["attack_gate"]["reason"],
            )
            self.assertEqual(2, intervention["attack_gate"]["unit_count"])
            self.assertTrue(intervention["attack_gate"]["scope_threshold_met"])
            tactical_evidence = intervention["tactical_evidence"]
            self.assertEqual("passed", tactical_evidence["status"])
            self.assertIn("contain", tactical_evidence["observed_effects"])
            self.assertIn("target_priority", tactical_evidence["observed_effects"])
            self.assertEqual([], tactical_evidence["missing_effects"])
            self.assertTrue(intervention["log_snippets"])
            self.assertIn("calcTargets", intervention["log_snippets"][-1]["line"])

    def test_micromachine_tactical_evidence_ignores_stale_unscoped_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "이제 새로 contain 해",
                    "blackboard_dir": directory,
                    "current_frame": 100,
                    "update_id": "web-new-scope-1",
                    "provider_output": {
                        "goal": "contain enemy natural",
                        "combat": {"aggression": 0.45},
                        "squad": {"contain_bias": 0.35},
                        "scope": {"location_intent": "enemy_natural"},
                    },
                }
            )
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 105,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "CombatCommander": {
                        "active": True,
                        "policy_active": True,
                        "update_id": "web-new-scope-1",
                        "consumed_axes": "combat.aggression",
                    },
                    "Squad": {
                        "active": True,
                        "main_attack_order": "Attack enemy natural",
                        "selected_target_class": "worker_line",
                    },
                },
                "active_modulation_ids": ["web-new-scope-1"],
                "last_failure": None,
            }
            with open(f"{directory}/latest_telemetry.json", "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            with open(f"{directory}/micromachine.log", "w", encoding="utf-8") as handle:
                handle.write(
                    "45: updateAttackSquads | MainAttackSquad new order = Attack enemy natural\n"
                    "46: calcTargets | target worker_line selected by policy modulation\n"
                )

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            tactical_evidence = document["intervention"]["tactical_evidence"]
            self.assertEqual("consumed", document["consumption_status"])
            self.assertNotEqual("passed", tactical_evidence["status"])
            self.assertIn("contain", tactical_evidence["missing_effects"])
            self.assertEqual([], tactical_evidence["observed_effects"])
            self.assertNotIn("Squad", document["intervention"]["manager_snapshot"])
            self.assertEqual(
                "",
                document["intervention"]["target_priority"]["selected_target_class"],
            )
            self.assertEqual("", document["intervention"]["attack_gate"]["status"])

    def test_micromachine_tactical_evidence_ignores_future_frame_stale_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "지금부터 압박해",
                    "blackboard_dir": directory,
                    "current_frame": 100,
                    "update_id": "new",
                    "provider_output": {
                        "goal": "attack pressure",
                        "combat": {"aggression": 0.45},
                    },
                }
            )
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 105,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "CombatCommander": {
                        "active": True,
                        "policy_active": True,
                        "update_id": "new",
                        "consumed_axes": "combat.aggression",
                    },
                },
                "active_modulation_ids": ["new"],
                "last_failure": None,
            }
            with open(f"{directory}/latest_telemetry.json", "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            with open(f"{directory}/micromachine.log", "w", encoding="utf-8") as handle:
                handle.write(
                    "10000: update_id=new updateAttackSquads | MainAttackSquad new order = Attack enemy natural\n"
                    "10001: update_id=new calcTargets | target worker_line selected by policy modulation\n"
                )

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            tactical_evidence = document["intervention"]["tactical_evidence"]
            self.assertEqual("consumed", document["consumption_status"])
            self.assertNotEqual("passed", tactical_evidence["status"])
            self.assertIn("pressure", tactical_evidence["missing_effects"])
            self.assertEqual([], tactical_evidence["observed_effects"])

    def test_micromachine_tactical_evidence_uses_more_than_display_log_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "지금 압박해",
                    "blackboard_dir": directory,
                    "current_frame": 100,
                    "update_id": "web-noisy-log-1",
                    "provider_output": {
                        "goal": "attack pressure",
                        "combat": {"aggression": 0.45},
                    },
                }
            )
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 120,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "CombatCommander": {
                        "active": True,
                        "policy_active": True,
                        "update_id": "web-noisy-log-1",
                        "consumed_axes": "combat.aggression",
                    },
                },
                "active_modulation_ids": ["web-noisy-log-1"],
                "last_failure": None,
            }
            with open(f"{directory}/latest_telemetry.json", "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            noise = "\n".join(
                f"{frame}: policy heartbeat modulation noise"
                for frame in range(102, 242)
            )
            with open(f"{directory}/micromachine.log", "w", encoding="utf-8") as handle:
                handle.write(
                    "101: updateAttackSquads | MainAttackSquad new order = Attack enemy natural\n"
                    f"{noise}\n"
                )

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            tactical_evidence = document["intervention"]["tactical_evidence"]
            self.assertEqual("passed", tactical_evidence["status"])
            self.assertIn("pressure", tactical_evidence["observed_effects"])
            self.assertNotIn(
                "Attack enemy natural",
                json.dumps(document["intervention"]["log_snippets"]),
            )

    def test_micromachine_tactical_evidence_ignores_partial_tail_stale_line(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "지금 압박해",
                    "blackboard_dir": directory,
                    "current_frame": 100,
                    "update_id": "new",
                    "provider_output": {
                        "goal": "attack pressure",
                        "combat": {"aggression": 0.45},
                    },
                }
            )
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 105,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "CombatCommander": {
                        "active": True,
                        "policy_active": True,
                        "update_id": "new",
                        "consumed_axes": "combat.aggression",
                    },
                },
                "active_modulation_ids": ["new"],
                "last_failure": None,
            }
            with open(f"{directory}/latest_telemetry.json", "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            line_prefix = b"10000: "
            line_rest = (
                b"update_id=new updateAttackSquads | "
                b"MainAttackSquad new order = Attack enemy natural\n"
            )
            tail_padding = b"x" * (
                web_gui._MICROMACHINE_MAX_LOG_READ_BYTES - len(line_rest)
            )
            with open(f"{directory}/micromachine.log", "wb") as handle:
                handle.write(b"safe old prefix\n")
                handle.write(line_prefix)
                handle.write(line_rest)
                handle.write(tail_padding)

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            tactical_evidence = document["intervention"]["tactical_evidence"]
            self.assertEqual("consumed", document["consumption_status"])
            self.assertNotEqual("passed", tactical_evidence["status"])
            self.assertIn("pressure", tactical_evidence["missing_effects"])
            self.assertEqual([], tactical_evidence["observed_effects"])

    def test_micromachine_status_does_not_read_symlinked_tactical_logs(self):
        if not hasattr(os, "symlink"):
            self.skipTest("os.symlink is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8") as outside:
                outside.write(
                    "99: calcTargets | leaked outside blackboard policy modulation\n"
                )
                outside.flush()
                os.symlink(outside.name, f"{directory}/micromachine.log")
                self.post_micromachine_modulation(
                    {
                        "text": "적 앞마당 압박",
                        "blackboard_dir": directory,
                        "current_frame": 20,
                        "update_id": "web-log-symlink-1",
                        "provider_output": {
                            "goal": "pressure",
                            "combat": {"aggression": 0.3},
                        },
                    }
                )

                document = self.get_json(
                    "/api/micromachine/status?blackboard_dir=" + directory
                )

            snippets = document["intervention"]["log_snippets"]
            self.assertFalse(
                any("leaked outside blackboard" in item["line"] for item in snippets)
            )

    def test_micromachine_status_persists_refusal_after_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            status, _content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "불확실하면 물어봐",
                    "blackboard_dir": directory,
                    "provider_output": {
                        "status": "clarification_required",
                        "clarification_prompt": "공격 타이밍을 더 구체화해 주세요.",
                    },
                }
            )

            self.assertEqual(HTTPStatus.OK, HTTPStatus(status))
            submitted = json.loads(payload.decode("utf-8"))
            self.assertFalse(submitted["accepted"])
            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            self.assertEqual("idle", document["status"])
            compile_result = document["compile_result"]
            self.assertEqual("clarification_required", compile_result["status"])
            self.assertEqual(
                "공격 타이밍을 더 구체화해 주세요.",
                compile_result["clarification_prompt"],
            )
            intervention = document["intervention"]
            self.assertEqual("refused", intervention["tactical_posture"])
            self.assertEqual(
                "공격 타이밍을 더 구체화해 주세요.",
                intervention["refusal_reason"],
            )
            self.assertEqual("refused", intervention["tactical_evidence"]["status"])
            self.assertTrue(intervention["tactical_evidence"]["refusal_reasons"])

    def test_micromachine_modulation_accepts_keyword_provider_without_llm_configuration(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        server = WebGuiServer(bridge=bridge, port=0)
        server.start()
        self.addCleanup(server.stop)
        with tempfile.TemporaryDirectory() as directory:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.port, timeout=5
            )
            try:
                body = json.dumps(
                    {
                        "text": "탱크로 수비해",
                        "blackboard_dir": directory,
                        "current_frame": 21,
                        "update_id": "keyword-no-llm",
                    }
                ).encode("utf-8")
                connection.request(
                    "POST",
                    "/api/micromachine/modulate",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                connection.close()

            self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(response.status))
            self.assertTrue(payload["accepted"])
            self.assertEqual("keyword-no-llm", payload["update"]["update_id"])
            self.assertEqual(directory, payload["blackboard_dir"])

    def test_micromachine_modulation_does_not_publish_plain_greeting(self):
        with tempfile.TemporaryDirectory() as directory:
            status, content_type, payload = self.post_micromachine_modulation(
                {
                    "text": "안녕",
                    "blackboard_dir": directory,
                    "current_frame": 21,
                    "update_id": "web-hello-noop",
                }
            )

            self.assertEqual(HTTPStatus.OK, HTTPStatus(status))
            self.assertIn("application/json", content_type)
            document = json.loads(payload.decode("utf-8"))
            self.assertFalse(document["accepted"], document)
            self.assertFalse(document["ok"], document)
            self.assertEqual("clarification_required", document["status"])
            self.assertEqual("not_published", document["consumption_status"])
            self.assertIsNone(document["update"])
            self.assertIn(
                "전술 의도",
                document["compile_result"]["clarification_prompt"],
            )
            self.assertFalse(os.path.exists(f"{directory}/latest_modulation.kv"))

    def test_micromachine_modulation_requests_are_serialized_on_bridge_queue(self):
        active_count = 0
        max_active_count = 0
        lock = threading.Lock()
        release_first = threading.Event()
        first_entered = threading.Event()

        def slow_publish(text, **kwargs):
            nonlocal active_count, max_active_count
            with lock:
                active_count += 1
                max_active_count = max(max_active_count, active_count)
                is_first = active_count == 1 and not first_entered.is_set()
            if is_first:
                first_entered.set()
                release_first.wait(timeout=5)
            time.sleep(0.02)
            with lock:
                active_count -= 1
            return {
                "ok": True,
                "status": "published",
                "consumption_status": "pending_telemetry",
                "dashboard": {"active_updates": []},
            }

        results = []
        start = threading.Barrier(3)

        def submit(index):
            start.wait(timeout=5)
            status, _content_type, payload = self.post_micromachine_modulation(
                {"text": f"수비 {index}"}
            )
            results.append((status, json.loads(payload.decode("utf-8"))))

        with mock.patch.object(
            self.bridge,
            "_publish_micromachine_modulation",
            side_effect=slow_publish,
        ):
            threads = [
                threading.Thread(target=submit, args=(index,))
                for index in range(2)
            ]
            for thread in threads:
                thread.start()
            start.wait(timeout=5)
            self.assertTrue(first_entered.wait(timeout=5))
            release_first.set()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(2, len(results))
        self.assertTrue(
            all(HTTPStatus(status) is HTTPStatus.ACCEPTED for status, _ in results)
        )
        self.assertEqual(1, max_active_count)

    def test_index_page_uses_bridge_micromachine_blackboard_default(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=FakeConfiguredLLMControl(),
            micromachine_blackboard_dir="/tmp/voi-mm-custom&safe",
        )
        bridge.start()
        self.addCleanup(bridge.stop)
        server = WebGuiServer(bridge=bridge, port=0)
        server.start()
        self.addCleanup(server.stop)
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            page = response.read().decode("utf-8")
        finally:
            connection.close()

        self.assertEqual(HTTPStatus.OK, HTTPStatus(response.status))
        self.assertIn('value="/tmp/voi-mm-custom&amp;safe"', page)
        self.assertIn("micromachine-tactical-evidence", page)

    def test_runtime_start_routes_micromachine_mode_to_launcher(self):
        class FakeMicroMachineLauncher:
            def __init__(self):
                self.started_blackboards = []

            def start(self, blackboard_dir=""):
                self.started_blackboards.append(blackboard_dir)
                return {
                    "enabled": True,
                    "mode": "micromachine",
                    "status": "starting",
                    "blackboard_dir": blackboard_dir,
                    "pid": 1234,
                }

            def snapshot(self, blackboard_dir=""):
                return {
                    "enabled": True,
                    "mode": "micromachine",
                    "status": "connected",
                    "blackboard_dir": blackboard_dir,
                    "telemetry_present": True,
                    "telemetry_frame": 42,
                }

        launcher = FakeMicroMachineLauncher()
        self.server._http.micromachine_launcher = launcher

        body = json.dumps(
            {"mode": "micromachine", "blackboard_dir": "/tmp/voi-mm-runtime-test"}
        ).encode("utf-8")
        status, content_type, payload = self.request(
            "POST",
            "/api/runtime/start",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
        self.assertIn("application/json", content_type)
        document = json.loads(payload.decode("utf-8"))
        self.assertTrue(document["accepted"], document)
        self.assertEqual(document["status"], "starting")
        self.assertEqual(launcher.started_blackboards, ["/tmp/voi-mm-runtime-test"])

        status, _content_type, payload = self.request(
            "GET",
            "/api/runtime/status?mode=micromachine&blackboard_dir=/tmp/voi-mm-runtime-test",
        )
        self.assertEqual(HTTPStatus.OK, HTTPStatus(status))
        document = json.loads(payload.decode("utf-8"))
        self.assertEqual(document["status"], "connected")
        self.assertEqual(document["telemetry_frame"], 42)

    def test_micromachine_launcher_default_script_is_repo_relative_not_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = web_gui._MicroMachineLaunchManager(cwd=directory)

            self.assertTrue(
                launcher._script_path.endswith(  # noqa: SLF001 - private launch seam.
                    "integrations/micromachine/scripts/smoke_macos_local.sh"
                )
            )
            self.assertTrue(
                launcher._script_path.startswith(web_gui._REPO_ROOT)  # noqa: SLF001
            )
            self.assertFalse(launcher._script_path.startswith(directory))  # noqa: SLF001

    def test_micromachine_launcher_blocks_blackboard_switch_while_running(self):
        class FakeRunningProcess:
            pid = 12345
            returncode = None

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as old_dir, tempfile.TemporaryDirectory() as new_dir:
            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
            launcher._blackboard_dir = old_dir  # noqa: SLF001 - private launch seam.
            launcher._process = FakeRunningProcess()  # noqa: SLF001

            payload = launcher.start(new_dir)

            self.assertEqual("blocked", payload["status"])
            self.assertFalse(payload["accepted"])
            self.assertEqual(old_dir, payload["blackboard_dir"])
            self.assertEqual(new_dir, payload["requested_blackboard_dir"])
            self.assertIn("different blackboard_dir", payload["error"])

    def test_micromachine_launcher_does_not_mark_stale_telemetry_connected(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(
                os.path.join(directory, "latest_telemetry.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": 99,
                    },
                    handle,
                )
            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)

            payload = launcher.snapshot(directory)

            self.assertEqual("idle", payload["status"])
            self.assertTrue(payload["telemetry_present"])
            self.assertEqual(99, payload["telemetry_frame"])

    def test_runtime_start_legacy_mode_is_blocked_until_key_is_saved(self):
        body = json.dumps({"mode": "legacy_commander"}).encode("utf-8")
        status, content_type, payload = self.request(
            "POST",
            "/api/runtime/start",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(HTTPStatus.CONFLICT, HTTPStatus(status))
        self.assertIn("application/json", content_type)
        document = json.loads(payload.decode("utf-8"))
        self.assertFalse(document["accepted"], document)
        self.assertEqual(document["mode"], "legacy_commander")
        self.assertEqual(document["status"], "blocked")
        self.assertTrue(contains_hangul(document["error"]))

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

    def test_state_endpoint_exposes_active_standing_orders_for_briefing(self):
        self.session.standing_orders.register("keep_worker_production")
        self.session.standing_orders.register("prevent_supply_block")

        document = self.get_json("/api/state")

        standing_orders = document["standing_orders"]
        self.assertEqual(
            standing_orders["active_kinds"],
            ["keep_worker_production", "prevent_supply_block"],
        )
        self.assertIn("상비 명령", standing_orders["korean_status"])
        self.assertIn("지속 SCV 생산", standing_orders["korean_status"])
        self.assertIn("보급 차단 방지", standing_orders["korean_status"])

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
                    "api_key": "unit-test-input-value",
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
        self.assertNotIn("unit-test-input-value", json.dumps(payload))

    def test_llm_config_validation_failure_reports_specific_reason(self):
        status, payload = self.post_llm_config_with_control(
            FakeFailingLLMControl(ValueError("provider must be openai or anthropic")),
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["failure_category"], "validation")
        self.assertEqual(payload["reason_code"], "llm_setup_validation_failed")
        self.assertIn("검증 실패", payload["error"])
        self.assertIn("provider must be openai or anthropic", payload["error"])

    def test_llm_config_network_failure_reports_specific_reason_without_key(self):
        submitted_key = "unit-test-sensitive-network"
        status, payload = self.post_llm_config_with_control(
            FakeFailingLLMControl(
                TimeoutError(
                    f"connection timed out while checking {submitted_key}"
                )
            ),
            api_key=submitted_key,
        )

        self.assertEqual(status, 503)
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["failure_category"], "network")
        self.assertEqual(payload["reason_code"], "llm_setup_network_failed")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertIn("연결 실패", payload["error"])
        self.assertIn("[redacted]", payload["error"])
        self.assertNotIn(submitted_key, json.dumps(payload, ensure_ascii=False))

    def test_llm_config_provider_failure_reports_specific_reason_without_key(self):
        submitted_key = "unit-test-sensitive-provider"
        status, payload = self.post_llm_config_with_control(
            FakeFailingLLMControl(
                ProviderRejectedSetupError(
                    f"authentication failed: invalid api key {submitted_key}"
                )
            ),
            api_key=submitted_key,
        )

        self.assertEqual(status, 502)
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["failure_category"], "provider")
        self.assertEqual(payload["reason_code"], "llm_setup_provider_rejected")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertIn("제공자 거부", payload["error"])
        self.assertIn("[redacted]", payload["error"])
        self.assertNotIn(submitted_key, json.dumps(payload, ensure_ascii=False))

    def test_internal_error_response_redacts_api_key_shaped_values(self):
        submitted_key = "sk-" + "test-internal-error-secret-123456789"
        server = WebGuiServer(
            bridge=ExplodingStateBridge(submitted_key),
            port=0,
        )
        server.start()
        self.addCleanup(server.stop)

        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            connection.request("GET", "/api/state")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

        self.assertEqual(response.status, 500)
        self.assertIn("[redacted]", payload["error"])
        self.assertNotIn(submitted_key, json.dumps(payload, ensure_ascii=False))

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
            list(parameters),
            ["self", "bridge", "port", "host", "auth_token", "auto_launch_live"],
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
        submitted_key = "sk-" + "test-session-secret-123456789"

        class ExplodingSession:
            async def process_text(self, text):
                raise RuntimeError(f"scripted session failure {submitted_key}")

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
        self.assertIn("[redacted]", events[0]["narration"])
        self.assertNotIn(submitted_key, json.dumps(events, ensure_ascii=False))

    def test_state_snapshot_reads_fake_bot_through_adapter(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        snapshot = bridge.state_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["minerals"], 400)
        self.assertEqual(snapshot["supply_used"], 20)
        self.assertEqual(snapshot["supply_cap"], 21)

    def test_state_snapshot_attaches_safe_briefing_memory_and_llm_summary(self):
        submitted_key = "sk-" + "test-briefing-secret-123456789"

        async def process_text(text):
            return ()

        class Memory:
            def korean_summary(self):
                return "최근 명령 2건:\n- #1 [executed] 생산 성공"

        class Resolver:
            def resolve(self, bot):
                return {
                    "minerals": 400,
                    "vespene": 0,
                    "supply_used": 12,
                    "supply_cap": 15,
                }

        session = SimpleNamespace(
            process_text=process_text,
            executor=SimpleNamespace(bot=object()),
            event_memory=Memory(),
            llm_summary=lambda: {
                "summary": f"경제 안정화 중심입니다. {submitted_key}",
                "raw_prompt": "system prompt must not reach state JSON",
                "api_key": submitted_key,
            },
        )
        bridge = SessionLoopBridge(session=session, state_resolver=Resolver())

        snapshot = bridge.state_snapshot()

        self.assertIsNotNone(snapshot)
        self.assertEqual(
            snapshot["compacted_memory"]["korean_summary"],
            "최근 명령 2건:\n- #1 [executed] 생산 성공",
        )
        self.assertEqual(
            snapshot["llm_summary"]["summary"],
            "경제 안정화 중심입니다. [redacted]",
        )
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn(submitted_key, serialized)
        self.assertNotIn("raw_prompt", serialized)
        self.assertNotIn("system prompt", serialized)
        self.assertNotIn("api_key", serialized)

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

    def run_briefing_advice_scenario(self, scenario):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        app_script = app_script[: app_script.index('document.getElementById("command-form")')]
        harness = r"""
class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.id = "";
    this.open = false;
    this.attributes = {};
    this.listeners = {};
    this._textContent = "";
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    var index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name] || null;
  }

  addEventListener(name, callback) {
    this.listeners[name] = this.listeners[name] || [];
    this.listeners[name].push(callback);
  }

  dispatchEvent(name) {
    (this.listeners[name] || []).forEach(function (callback) { callback(); });
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get textContent() {
    return this._textContent + this.children.map(function (child) {
      return child.textContent || "";
    }).join("");
  }

  set innerHTML(value) {
    this._textContent = String(value);
    this.children = [];
  }
}

var briefing = new FakeElement("div");
briefing.id = "strategy-briefing";
var document = {
  documentElement: new FakeElement("html"),
  _roots: [briefing],
  createElement: function (tagName) { return new FakeElement(tagName); },
  getElementById: function (id) {
    return this._roots.find(function (node) { return node.id === id; }) || null;
  },
  querySelectorAll: function () { return []; }
};
var window = { location: { search: "" } };
var URLSearchParams = global.URLSearchParams;

function renderAdviceBriefing(events) {
  recentEvents = events;
  renderStrategyBriefing({
    minerals: 314,
    vespene: 82,
    supply_used: 19,
    supply_cap: 27,
    supply_left: 8,
    own_units: { SCV: 14 },
    army_count: 5,
    own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
    visible_enemy_units: { ZERGLING: 3 },
    visible_enemy_structures: { HATCHERY: 1 },
    observation_complete: true
  });
  return briefing.children[5];
}
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

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
            "MAX_MESSAGE_PREVIEW_CHARS",
            "COMPACT_KEEP_EVENTS",
            "compactedContextSummary",
            "archivedChatEvents",
            "appendCompactText",
            "renderArchivedChatDetails",
            "briefingEvidence",
            "briefingAdvice",
            "appendPendingCommand",
            "removeOldestPendingCommand",
            "setupVoiceInput",
            "voice-wave",
            "assistant-pending-status",
            "typing-indicator",
            "assistantWaiting",
            "provider-option",
            "claude-fable-4-5-20251001",
            "claude-haiku-4-5-20251001",
            "grok-build-0.1",
            "selectedLlmChoice",
            "selectedProviderValue",
            "handleLiveStart",
            "if (data.configured)",
            "setLiveStatusText",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_page_has_status_color_class_per_outcome_status(self):
        page = render_web_gui_page()
        for status, color in WEB_GUI_STATUS_COLORS.items():
            with self.subTest(status=status):
                self.assertIn(f".status-{status}", page)
                self.assertIn(color, page)

    def test_space_background_uses_nebula_depth_without_flat_dot_grid(self):
        page = render_web_gui_page()
        self.assertIn('<div class="space-background" aria-hidden="true"></div>', page)
        self.assertIn(".space-background {", page)
        self.assertIn("position: fixed; inset: 0; z-index: 0; pointer-events: none", page)
        self.assertIn("radial-gradient(ellipse at 18% 24%", page)
        self.assertIn("conic-gradient(from 220deg", page)
        self.assertIn("linear-gradient(145deg, #02030b", page)
        self.assertIn(".space-background::before", page)
        self.assertIn(".space-background::after", page)
        self.assertIn('<div class="star-depth star-depth-far" aria-hidden="true"></div>', page)
        self.assertIn('<div class="star-depth star-depth-near" aria-hidden="true"></div>', page)
        self.assertIn(".star-depth {", page)
        self.assertIn("mix-blend-mode: screen", page)
        self.assertIn("animation: star-parallax-far 64s linear infinite", page)
        self.assertIn("animation: star-parallax-near 42s linear infinite", page)
        self.assertIn("@media (prefers-reduced-motion: reduce)", page)
        self.assertIn(
            ".star-depth { animation: none; transform: none; will-change: auto; }",
            page,
        )
        self.assertIn("transform: translate3d", page)
        self.assertIn("contain: paint", page)
        self.assertNotIn("body::before", page)
        self.assertNotIn("background-size: 230px 210px", page)
        self.assertNotIn("radial-gradient(circle at 12% 18%", page)

    def test_space_background_has_responsive_and_accessibility_fallbacks(self):
        page = render_web_gui_page()
        for fragment in (
            "@media (max-width: 980px)",
            ".space-background::after { inset: 20% -20% -18% 24%; width: 105vw; height: 105vw; opacity: 0.48; }",
            ".star-depth { inset: -14vmax; }",
            "@media (max-width: 620px)",
            "radial-gradient(ellipse at 22% 12%, rgba(64, 224, 255, 0.22)",
            ".space-background::before { inset: -24% -30%; opacity: 0.35; filter: blur(16px); }",
            ".star-depth-far { opacity: 0.24; }",
            ".star-depth-near { opacity: 0.28; }",
            "@media (prefers-contrast: more)",
            "--panel: rgba(1, 5, 18, 0.94);",
            ".star-depth { opacity: 0.18; mix-blend-mode: normal; }",
            "#command-panel, #state-panel { backdrop-filter: none; }",
            "@media (forced-colors: active)",
            "body { background: Canvas; color: CanvasText; }",
            ".space-background, .space-background::before, .space-background::after, .star-depth { display: none; }",
            "forced-color-adjust: auto; background: Canvas; color: CanvasText;",
            "background: ButtonFace; color: ButtonText; border: 1px solid ButtonText;",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_assistant_pending_typing_state_renders_until_response_arrives(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        app_script = app_script[: app_script.index('document.getElementById("command-form")')]
        harness = r"""
class FakeText {
  constructor(text) {
    this.textContent = text;
    this.parentNode = null;
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = "";
    this.id = "";
    this._textContent = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  insertBefore(child, reference) {
    child.parentNode = this;
    var index = this.children.indexOf(reference);
    if (index < 0) {
      this.children.push(child);
    } else {
      this.children.splice(index, 0, child);
    }
    return child;
  }

  removeChild(child) {
    var index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  addEventListener() {}

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") {
      this.id = String(value);
    }
    if (name === "class") {
      this.className = String(value);
    }
  }

  getAttribute(name) {
    if (name === "id") {
      return this.id;
    }
    if (name === "class") {
      return this.className;
    }
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  get firstChild() {
    return this.children[0] || null;
  }

  get firstElementChild() {
    return this.children.find(function (child) { return child instanceof FakeElement; }) || null;
  }

  get textContent() {
    return this._textContent + this.children.map(function (child) { return child.textContent || ""; }).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    var matches = [];
    function hasClass(node, className) {
      return (" " + (node.className || "") + " ").indexOf(" " + className + " ") >= 0;
    }
    function isMatch(node) {
      if (!(node instanceof FakeElement)) {
        return false;
      }
      if (selector.charAt(0) === ".") {
        return hasClass(node, selector.slice(1));
      }
      if (selector.charAt(0) === "#") {
        return node.id === selector.slice(1);
      }
      return node.tagName.toLowerCase() === selector.toLowerCase();
    }
    function visit(node) {
      node.children.forEach(function (child) {
        if (isMatch(child)) {
          matches.push(child);
        }
        if (child instanceof FakeElement) {
          visit(child);
        }
      });
    }
    visit(this);
    return matches;
  }
}

var logBox = new FakeElement("div");
logBox.id = "log";
var pendingStatus = new FakeElement("p");
pendingStatus.id = "assistant-pending-status";
var document = {
  documentElement: new FakeElement("html"),
  _roots: [logBox, pendingStatus],
  createElement: function (tagName) { return new FakeElement(tagName); },
  createTextNode: function (text) { return new FakeText(text); },
  getElementById: function (id) {
    var found = null;
    function visit(node) {
      if (found || !(node instanceof FakeElement)) { return; }
      if (node.id === id) {
        found = node;
        return;
      }
      node.children.forEach(visit);
    }
    this._roots.forEach(visit);
    return found;
  },
  querySelectorAll: function (selector) {
    return this._roots.reduce(function (matches, root) {
      return matches.concat(root.querySelectorAll(selector));
    }, []);
  },
  querySelector: function (selector) { return this.querySelectorAll(selector)[0] || null; }
};
var window = {
  location: { search: "" },
  setTimeout: function () {},
  SpeechRecognition: null,
  webkitSpeechRecognition: null
};
var fetch = function () { return Promise.resolve({ json: function () { return {}; } }); };
var setInterval = function () {};
var URLSearchParams = global.URLSearchParams;
"""
        scenario = r"""
const assert = require("assert");
for (let index = 0; index < MAX_CHAT_EVENTS - 1; index += 1) {
  appendLog({
    seq: index + 1,
    command_text: "이전 명령 " + index,
    status: "read_only",
    narration: "이전 응답 " + index
  });
}
appendVoiceRecordingBubble();
assert.strictEqual(logBox.querySelectorAll(".log-entry").length, MAX_CHAT_EVENTS);
appendPendingCommand("상황 보고해줘");
assert.strictEqual(pendingCommandCount(), 1);
assert.strictEqual(logBox.getAttribute("aria-busy"), "true");
assert(pendingStatus.textContent.includes("LLM 응답을 기다리는 중"));
assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 1);
assert.strictEqual(logBox.querySelectorAll(".typing-indicator").length, 1);
assert.strictEqual(logBox.querySelector(".message-pending").getAttribute("role"), "status");
assert.strictEqual(logBox.querySelectorAll(".log-entry").length, MAX_CHAT_EVENTS);
assert(document.getElementById("voice-recording-entry"));
assert.strictEqual(logBox.querySelector(".voice-wave").querySelectorAll("span").length, 5);
appendPendingCommand("상황 보고해줘");
assert.strictEqual(pendingCommandCount(), 2);
assert(pendingStatus.textContent.includes("대기 중인 응답 2개"));
assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 2);
assert(document.getElementById("voice-recording-entry"));
appendLog({
  seq: MAX_CHAT_EVENTS + 1,
  command_text: "상황 보고해줘",
  status: "read_only",
  narration: "현재 상태를 요약했습니다."
});
assert.strictEqual(pendingCommandCount(), 1);
assert.strictEqual(logBox.getAttribute("aria-busy"), "true");
assert(pendingStatus.textContent.includes("LLM 응답을 기다리는 중"));
assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 1);
assert(document.getElementById("voice-recording-entry"));
appendLog({
  seq: MAX_CHAT_EVENTS + 2,
  command_text: "상황 보고해줘",
  status: "read_only",
  narration: "두 번째 응답입니다."
});
assert.strictEqual(pendingCommandCount(), 0);
assert.strictEqual(logBox.getAttribute("aria-busy"), "false");
assert.strictEqual(pendingStatus.textContent, "");
assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 0);
assert(document.getElementById("voice-recording-entry"));
removeVoiceRecordingBubble();
assert.strictEqual(document.getElementById("voice-recording-entry"), null);
assert(logBox.textContent.includes("현재 상태를 요약했습니다."));
assert(logBox.textContent.includes("두 번째 응답입니다."));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_micromachine_commander_chat_submit_clears_pending_after_publish(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        app_script = app_script[
            : app_script.index('var providerOptions = document.getElementById("llm-provider-options")')
        ]
        harness = r"""
class FakeText {
  constructor(text) {
    this.textContent = text;
    this.parentNode = null;
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this.className = "";
    this.id = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.placeholder = "";
    this._textContent = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.classList = {
      add: function () {},
      remove: function () {},
      toggle: function () {}
    };
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  insertBefore(child, reference) {
    child.parentNode = this;
    var index = this.children.indexOf(reference);
    if (index < 0) {
      this.children.push(child);
    } else {
      this.children.splice(index, 0, child);
    }
    return child;
  }

  removeChild(child) {
    var index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }

  dispatchEvent(event) {
    if (this.listeners[event.type]) {
      this.listeners[event.type](event);
    }
  }

  focus() {}

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") {
      this.id = String(value);
    }
    if (name === "class") {
      this.className = String(value);
    }
  }

  getAttribute(name) {
    if (name === "id") {
      return this.id;
    }
    if (name === "class") {
      return this.className;
    }
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  closest() {
    return null;
  }

  get firstChild() {
    return this.children[0] || null;
  }

  get firstElementChild() {
    return this.children.find(function (child) { return child instanceof FakeElement; }) || null;
  }

  get lastChild() {
    return this.children[this.children.length - 1] || null;
  }

  get childNodes() {
    return this.children;
  }

  get textContent() {
    return this._textContent + this.children.map(function (child) { return child.textContent || ""; }).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  set innerHTML(value) {
    this._textContent = "";
    this.children = [];
  }

  get innerHTML() {
    return "";
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    if (selector.indexOf(">") >= 0) {
      return [];
    }
    var matches = [];
    function hasClass(node, className) {
      return (" " + (node.className || "") + " ").indexOf(" " + className + " ") >= 0;
    }
    function isMatch(node) {
      if (!(node instanceof FakeElement)) {
        return false;
      }
      if (selector.charAt(0) === ".") {
        return hasClass(node, selector.slice(1));
      }
      if (selector.charAt(0) === "#") {
        return node.id === selector.slice(1);
      }
      return node.tagName.toLowerCase() === selector.toLowerCase();
    }
    function visit(node) {
      node.children.forEach(function (child) {
        if (isMatch(child)) {
          matches.push(child);
        }
        if (child instanceof FakeElement) {
          visit(child);
        }
      });
    }
    visit(this);
    return matches;
  }
}

function element(id, tagName) {
  var node = new FakeElement(tagName || "div");
  node.id = id;
  return node;
}

var logBox = element("log");
var nodes = {
  "assistant-pending-status": element("assistant-pending-status", "p"),
  "command-form": element("command-form", "form"),
  "command-input": element("command-input", "input"),
  "send-button": element("send-button", "button"),
  "voice-button": element("voice-button", "button"),
  "llm-form": element("llm-form", "form"),
  "llm-api-key": element("llm-api-key", "input"),
  "llm-status": element("llm-status"),
  "llm-status-label": element("llm-status-label"),
  "llm-status-message": element("llm-status-message"),
  "llm-model-select": element("llm-model-select", "select"),
  "live-status": element("live-status"),
  "live-open-button": element("live-open-button", "button"),
  "runtime-start-button": element("runtime-start-button", "button"),
  "runtime-refresh-button": element("runtime-refresh-button", "button"),
  "runtime-mode-summary": element("runtime-mode-summary"),
  "legacy-mode-warning": element("legacy-mode-warning"),
  "micromachine-form": element("micromachine-form", "form"),
  "micromachine-command-input": element("micromachine-command-input", "textarea"),
  "micromachine-blackboard-dir": element("micromachine-blackboard-dir", "input"),
  "micromachine-army-group": element("micromachine-army-group", "input"),
  "micromachine-location-intent": element("micromachine-location-intent", "input"),
  "micromachine-unit-classes": element("micromachine-unit-classes", "input"),
  "micromachine-safety-margin": element("micromachine-safety-margin", "input"),
  "micromachine-duration-seconds": element("micromachine-duration-seconds", "input"),
  "micromachine-ttl-seconds": element("micromachine-ttl-seconds", "input"),
  "micromachine-status": element("micromachine-status"),
  "micromachine-applied-badge": element("micromachine-applied-badge"),
  "micromachine-latest-update": element("micromachine-latest-update"),
  "micromachine-active-ids": element("micromachine-active-ids"),
  "micromachine-frame": element("micromachine-frame"),
  "micromachine-domains": element("micromachine-domains"),
  "micromachine-goal": element("micromachine-goal"),
  "micromachine-managers": element("micromachine-managers"),
  "micromachine-posture": element("micromachine-posture"),
  "micromachine-scope": element("micromachine-scope"),
  "micromachine-consumed-axes": element("micromachine-consumed-axes"),
  "micromachine-target-priority": element("micromachine-target-priority"),
  "micromachine-attack-gate": element("micromachine-attack-gate"),
  "micromachine-tactical-evidence": element("micromachine-tactical-evidence"),
  "micromachine-refusal": element("micromachine-refusal"),
  "micromachine-log-snippets": element("micromachine-log-snippets", "ul"),
  "micromachine-raw-evidence": element("micromachine-raw-evidence", "pre")
};
nodes["log"] = logBox;
nodes["llm-model-select"].value = "gpt-test";
nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-js-test";
nodes["micromachine-ttl-seconds"].value = "600";

var providerRadios = [
  { value: "openai", checked: true, addEventListener: function () {} },
  { value: "anthropic", checked: false, addEventListener: function () {} },
  { value: "gemini", checked: false, addEventListener: function () {} },
  { value: "grok", checked: false, addEventListener: function () {} }
];
var commandModeRadios = [
  { value: "micromachine", checked: true, addEventListener: function (name, handler) { this.listener = handler; } },
  { value: "legacy_commander", checked: false, addEventListener: function (name, handler) { this.listener = handler; } }
];

var document = {
  documentElement: new FakeElement("html"),
  createElement: function (tagName) { return new FakeElement(tagName); },
  createTextNode: function (text) { return new FakeText(text); },
  getElementById: function (id) {
    if (nodes[id]) { return nodes[id]; }
    var found = null;
    function visit(node) {
      if (found || !(node instanceof FakeElement)) { return; }
      if (node.id === id) {
        found = node;
        return;
      }
      node.children.forEach(visit);
    }
    Object.keys(nodes).forEach(function (key) { visit(nodes[key]); });
    return found;
  },
  querySelectorAll: function (selector) {
    if (selector === "input[name='llm-provider-choice']") { return providerRadios; }
    if (selector === "input[name='command-mode']") { return commandModeRadios; }
    if (selector === "[data-command]") { return []; }
    if (selector === "[data-lang-button]") { return []; }
    return Object.keys(nodes).reduce(function (matches, key) {
      return matches.concat(nodes[key].querySelectorAll(selector));
    }, []);
  },
  querySelector: function (selector) {
    if (selector === "input[name='llm-provider-choice']:checked") {
      return providerRadios.find(function (radio) { return radio.checked; }) || null;
    }
    if (selector === "input[name='command-mode']:checked") {
      return commandModeRadios.find(function (radio) { return radio.checked; }) || null;
    }
    return this.querySelectorAll(selector)[0] || null;
  }
};
var timeoutCallbacks = [];
var window = {
  location: { search: "" },
  setTimeout: function (callback) {
    timeoutCallbacks.push(callback);
    return timeoutCallbacks.length - 1;
  },
  clearTimeout: function (id) {
    timeoutCallbacks[id] = function () {};
  },
  open: function () {},
  SpeechRecognition: null,
  webkitSpeechRecognition: null
};
var console = {
  warn: function () {},
  error: function (message) { global.__consoleError = message; }
};
var setInterval = function () {};
var URLSearchParams = global.URLSearchParams;
var requests = [];
function deferred() {
  var resolve;
  var reject;
  var promise = new Promise(function (resolveFn, rejectFn) {
    resolve = resolveFn;
    reject = rejectFn;
  });
  return { promise: promise, resolve: resolve, reject: reject };
}
function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    text: function () { return Promise.resolve(JSON.stringify(data)); }
  };
}
var fetch = function (url, options) {
  var item = { url: url, options: options || {}, deferred: deferred() };
  requests.push(item);
  return item.deferred.promise;
};
function flushPromises() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}
"""
        scenario = r"""
const assert = require("assert");
(async function () {
  nodes["command-input"].value = "enemy natural 압박하고 탱크는 안전하게";
  nodes["command-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, 1);
  assert.strictEqual(requests[0].url, "/api/micromachine/modulate");
  var firstBody = JSON.parse(requests[0].options.body);
  assert.strictEqual(firstBody.text, "enemy natural 압박하고 탱크는 안전하게");
  assert.strictEqual(firstBody.blackboard_dir, "/tmp/voi-mm-js-test");
  assert.strictEqual(firstBody.ttl_seconds, 600);
  assert.strictEqual(pendingCommandCount(), 1);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "true");
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 1);

  renderMicroMachineStatus = function () {
    throw new Error("dashboard boom");
  };
  requests[0].deferred.resolve(response(202, {
    ok: true,
    accepted: true,
    status: "published",
    consumption_status: "pending_telemetry",
    intervention: {
      latest_update_id: "unit-update-1",
      tactical_posture: "pressure",
      manager_bias_domains: ["combat", "squad"]
    },
    dashboard: {
      active_updates: [
        { update_id: "unit-update-1", manager_bias_domains: ["combat", "squad"] }
      ]
    }
  }));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "false");
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 0);
  assert(logBox.textContent.includes("MicroMachine DSL modulation"));
  assert(logBox.textContent.includes("pending_telemetry"));
  assert(nodes["micromachine-status"].textContent.includes("dashboard render failed"));
  assert.strictEqual(nodes["command-input"].value, "");

  renderMicroMachineStatus = function () {};
  nodes["command-input"].value = "실패 케이스도 pending 남기지 마";
  nodes["command-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, 2);
  assert.strictEqual(pendingCommandCount(), 1);
  requests[1].deferred.resolve(response(500, { error: "backend down" }));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "false");
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 0);
  assert(logBox.textContent.includes("backend down"));

  nodes["command-input"].value = "응답이 없어도 pending은 풀어";
  nodes["command-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, 3);
  assert.strictEqual(pendingCommandCount(), 1);
  timeoutCallbacks[timeoutCallbacks.length - 1]();
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "false");
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 0);
  assert(logBox.textContent.includes("pending을 해제했습니다"));
})().catch(function (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chat_panel_is_bounded_and_log_scrolls_internally(self):
        page = render_web_gui_page()
        for fragment in (
            "main {\n    display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(330px, 0.75fr);\n    gap: 18px; align-items: stretch; min-height: 0;",
            "#command-panel {\n    min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden;",
            "height: clamp(420px, calc(100vh - 150px), 780px); max-height: calc(100vh - 150px);",
            "#state-panel {\n    min-width: 0; min-height: 0; max-height: calc(100vh - 150px); overflow-y: auto;",
            "#log {\n    flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain;",
            "#command-panel { height: 68vh; min-height: 0; max-height: 68vh; }",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_long_and_trimmed_messages_keep_full_content_access(self):
        page = render_web_gui_page()
        for fragment in (
            "var MAX_MESSAGE_PREVIEW_CHARS = 280;",
            "normalized.slice(0, MAX_MESSAGE_PREVIEW_CHARS)",
            "summary.setAttribute(\"data-message-length\"",
            "full.textContent = normalized;",
            "archiveTrimmedEntry(oldestEntry);",
            "archivedChatEvents.push(item);",
            "existingNote = document.createElement(\"details\");",
            "if (existingNote.open) { renderArchivedChatDetails(existingNote); }",
            "appendCompactText(item, t(\"userLabel\") + \": \" + ev.command_text",
            "appendCompactText(item, t(\"commanderLabel\") + \": \" + ev.narration",
            ".archived-chat {",
            ".message-full {",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_high_volume_natural_language_question_responses_stay_bounded(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        # Avoid browser event wiring/startup polling; this test drives appendLog() directly.
        app_script = app_script[: app_script.index('document.getElementById("command-form")')]
        harness = r"""
class FakeText {
  constructor(text) {
    this.textContent = text;
    this.parentNode = null;
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = "";
    this.id = "";
    this._textContent = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  insertBefore(child, reference) {
    child.parentNode = this;
    var index = this.children.indexOf(reference);
    if (index < 0) {
      this.children.push(child);
    } else {
      this.children.splice(index, 0, child);
    }
    return child;
  }

  removeChild(child) {
    var index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  addEventListener() {}

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") {
      this.id = String(value);
    }
    if (name === "class") {
      this.className = String(value);
    }
  }

  getAttribute(name) {
    if (name === "id") {
      return this.id;
    }
    if (name === "class") {
      return this.className;
    }
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  get firstChild() {
    return this.children[0] || null;
  }

  get firstElementChild() {
    return this.children.find(function (child) { return child instanceof FakeElement; }) || null;
  }

  get textContent() {
    return this._textContent + this.children.map(function (child) { return child.textContent || ""; }).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    var matches = [];
    function hasClass(node, className) {
      return (" " + (node.className || "") + " ").indexOf(" " + className + " ") >= 0;
    }
    function isMatch(node) {
      if (!(node instanceof FakeElement)) {
        return false;
      }
      if (selector.charAt(0) === ".") {
        return hasClass(node, selector.slice(1));
      }
      if (selector.charAt(0) === "#") {
        return node.id === selector.slice(1);
      }
      return node.tagName.toLowerCase() === selector.toLowerCase();
    }
    function visit(node) {
      node.children.forEach(function (child) {
        if (isMatch(child)) {
          matches.push(child);
        }
        if (child instanceof FakeElement) {
          visit(child);
        }
      });
    }
    visit(this);
    return matches;
  }
}

var logBox = new FakeElement("div");
logBox.id = "log";
var document = {
  _roots: [logBox],
  createElement: function (tagName) { return new FakeElement(tagName); },
  createTextNode: function (text) { return new FakeText(text); },
  getElementById: function (id) {
    if (id === "log") { return logBox; }
    var found = null;
    function visit(node) {
      if (found || !(node instanceof FakeElement)) { return; }
      if (node.id === id) {
        found = node;
        return;
      }
      node.children.forEach(visit);
    }
    this._roots.forEach(visit);
    return found;
  },
  querySelectorAll: function (selector) { return logBox.querySelectorAll(selector); },
  querySelector: function (selector) { return logBox.querySelector(selector); }
};
var window = {
  location: { search: "" },
  setTimeout: function () {},
  SpeechRecognition: null,
  webkitSpeechRecognition: null
};
var fetch = function () { return Promise.resolve({ json: function () { return {}; } }); };
var setInterval = function () {};
var URLSearchParams = global.URLSearchParams;
"""
        scenario = r"""
const assert = require("assert");
const questionTexts = [
  "지금 뭐 해야 해?",
  "다음 할 일 알려줘",
  "왜 안돼?",
  "어떤 명령을 할 수 있어?"
];
const longAdvice = "추천 흐름: 현재 관측을 기준으로 SCV 생산을 유지하고 보급 여유를 확인한 뒤 정찰 정보를 갱신하세요. 이 답변은 읽기 전용이며 게임 명령을 실행하지 않습니다. ";
const longCapability = "지원 질문 예시: 지금 뭐 해야 해, 왜 안돼, 어떤 명령을 할 수 있어. 지원 명령 예시는 안전 계층을 통과해야 실행되며 질문 답변은 채팅에만 표시됩니다. ";
for (let index = 1; index <= 64; index += 1) {
  appendLog({
    seq: index,
    command_text: questionTexts[index % questionTexts.length] + " #" + index,
    status: "read_only",
    narration: (index % 2 ? longAdvice : longCapability).repeat(4) + "응답-" + index
  });
}
assert.strictEqual(logBox.querySelectorAll(".log-entry").length, MAX_CHAT_EVENTS);
assert.strictEqual(trimmedChatEvents, 64 - MAX_CHAT_EVENTS);
assert.strictEqual(archivedChatEvents.length, trimmedChatEvents);
assert(document.getElementById("chat-trim-note"), "trim note should be visible after bounding");
assert(archivedChatEvents.every(function (event) {
  return event.status === "read_only";
}), "archived natural-language question responses preserve read-only status");
assert(archivedChatEvents[0].command_text.includes("다음 할 일 알려줘"), "archived question text remains available");
assert(archivedChatEvents[1].narration.includes("지원 질문 예시"), "archived answer text remains available");
assert.strictEqual(logBox.querySelectorAll(".status-read_only").length, MAX_CHAT_EVENTS);
assert(logBox.querySelectorAll(".message-expander").length > 0, "long question answers use expandable previews");
assert(logBox.querySelectorAll(".message-preview").every(function (node) {
  return node.textContent.length <= MAX_MESSAGE_PREVIEW_CHARS + 1;
}), "visible previews stay bounded");
assert(logBox.querySelectorAll(".message-full").some(function (node) {
  return node.textContent.includes("지원 질문 예시") && node.textContent.includes("응답-64");
}), "full long question answer remains mounted for expansion");
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_page_polls_without_external_cdn(self):
        page = render_web_gui_page()
        self.assertIn("/api/history?after=", page)
        self.assertIn("/api/state", page)
        self.assertIn(f"POLL_INTERVAL_MS = {web_gui.WEB_GUI_POLL_INTERVAL_MS}", page)
        for forbidden in ("https://cdn.", "http://cdn.", "unpkg.com", "jsdelivr"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, page)

    def test_llm_setup_panel_starts_collapsed_with_toggle_inside_box(self):
        page = render_web_gui_page()
        start = page.index('<details id="llm-panel" class="collapsible-panel">')
        end = page.index("</details>", start)
        llm_panel = page[start:end]
        opening_tag = llm_panel.split(">", 1)[0]

        self.assertNotIn(" open", opening_tag)
        self.assertIn(
            '<summary><span data-i18n="llmTitle">LLM 설정</span></summary>',
            llm_panel,
        )
        self.assertLess(
            llm_panel.index("<summary>"),
            llm_panel.index('<form id="llm-form">'),
        )

    def test_llm_api_key_status_renders_distinct_state_labels(self):
        page = render_web_gui_page()
        self.assertIn('id="llm-status"', page)
        self.assertIn('data-llm-state="checking"', page)
        for fragment in (
            "llm-status-setting",
            "llm-status-success",
            "llm-status-failed",
            'llmSettingLabel: "설정 중"',
            'llmSuccessLabel: "설정 완료"',
            'llmFailedLabel: "설정 실패"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)

    def test_briefing_panel_starts_collapsed_with_toggle_inside_box(self):
        page = render_web_gui_page()
        start = page.index('<details id="briefing-panel" class="collapsible-panel">')
        end = page.index("</details>", start)
        briefing_panel = page[start:end]
        opening_tag = briefing_panel.split(">", 1)[0]

        self.assertNotIn(" open", opening_tag)
        self.assertIn(
            '<summary><span data-i18n="briefingTitle">전략 브리핑</span></summary>',
            briefing_panel,
        )
        self.assertLess(
            briefing_panel.index("<summary>"),
            briefing_panel.index('<div id="strategy-briefing"'),
        )

    def test_briefing_advice_is_hidden_by_default(self):
        scenario = r"""
const assert = require("assert");
briefingAdviceToggleEnabled = false;
var adviceDisclosure = renderAdviceBriefing([
  { command_text: "상태 알려줘", status: "read_only", narration: "현재 상태를 요약합니다." }
]);

assert.strictEqual(adviceDisclosure.tagName, "DETAILS");
assert.strictEqual(adviceDisclosure.open, false);
assert.strictEqual(adviceDisclosure.children.length, 1);
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-requested"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "suppressed");
assert.strictEqual(adviceDisclosure.getAttribute("aria-expanded"), "false");
assert(!briefing.textContent.includes("경제와 생산을 유지하세요"));
"""
        self.run_briefing_advice_scenario(scenario)

    def test_briefing_advice_opens_for_explicit_advice_request(self):
        scenario = r"""
const assert = require("assert");
briefingAdviceToggleEnabled = false;
var adviceDisclosure = renderAdviceBriefing([
  { command_text: "지금 뭐 해야 해?", status: "read_only", narration: "추천 흐름을 답합니다." }
]);

assert.strictEqual(adviceDisclosure.tagName, "DETAILS");
assert.strictEqual(adviceDisclosure.open, true);
assert.strictEqual(adviceDisclosure.children.length, 2);
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-requested"), "true");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "visible");
assert.strictEqual(adviceDisclosure.getAttribute("aria-expanded"), "true");
assert(adviceDisclosure.textContent.includes("경제와 생산을 유지하세요"));
"""
        self.run_briefing_advice_scenario(scenario)

    def test_briefing_advice_toggle_persists_across_state_refreshes(self):
        scenario = r"""
const assert = require("assert");
briefingAdviceToggleEnabled = false;
var events = [
  { command_text: "상태 알려줘", status: "read_only", narration: "현재 상태를 요약합니다." }
];
var adviceDisclosure = renderAdviceBriefing(events);

adviceDisclosure.open = true;
adviceDisclosure.dispatchEvent("toggle");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "true");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "visible");
assert(adviceDisclosure.textContent.includes("경제와 생산을 유지하세요"));

adviceDisclosure = renderAdviceBriefing(events);
assert.strictEqual(adviceDisclosure.open, true);
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-requested"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "true");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "visible");
assert.strictEqual(adviceDisclosure.getAttribute("aria-expanded"), "true");
assert(adviceDisclosure.textContent.includes("경제와 생산을 유지하세요"));

adviceDisclosure.open = false;
adviceDisclosure.dispatchEvent("toggle");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "suppressed");
assert.strictEqual(adviceDisclosure.getAttribute("aria-expanded"), "false");
assert(!briefing.textContent.includes("경제와 생산을 유지하세요"));
"""
        self.run_briefing_advice_scenario(scenario)

    def test_briefing_evidence_section_uses_korean_current_state_summary(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        app_script = app_script[: app_script.index('document.getElementById("command-form")')]
        harness = r"""
class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.id = "";
    this.open = false;
    this.attributes = {};
    this.listeners = {};
    this._textContent = "";
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    var index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name] || null;
  }

  addEventListener(name, callback) {
    this.listeners[name] = this.listeners[name] || [];
    this.listeners[name].push(callback);
  }

  dispatchEvent(name) {
    (this.listeners[name] || []).forEach(function (callback) { callback(); });
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get textContent() {
    return this._textContent + this.children.map(function (child) {
      return child.textContent || "";
    }).join("");
  }

  set innerHTML(value) {
    this._textContent = String(value);
    this.children = [];
  }
}

var logBox = new FakeElement("div");
logBox.id = "log";
var briefing = new FakeElement("div");
briefing.id = "strategy-briefing";
var document = {
  documentElement: new FakeElement("html"),
  _roots: [logBox, briefing],
  createElement: function (tagName) { return new FakeElement(tagName); },
  getElementById: function (id) {
    return this._roots.find(function (node) { return node.id === id; }) || null;
  },
  querySelectorAll: function () { return []; }
};
var window = { location: { search: "" } };
var URLSearchParams = global.URLSearchParams;
"""
        scenario = r"""
const assert = require("assert");
recentEvents = [
  { command_text: "SCV 계속 찍어", status: "executed", narration: "SCV 생산을 시작했습니다." },
  { command_text: "보급고 지어", status: "blocked", narration: "미네랄 부족으로 건설이 차단되었습니다." },
  { command_text: "상태 알려줘", status: "read_only", narration: "현재 상태를 요약합니다." }
];
renderStrategyBriefing({
  minerals: 314,
  vespene: 82,
  supply_used: 19,
  supply_cap: 27,
  supply_left: 8,
  own_units: { SCV: 14 },
  army_count: 5,
  own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
  visible_enemy_units: { ZERGLING: 3 },
  visible_enemy_structures: { HATCHERY: 1 },
  observation_complete: false,
  compacted_memory: {
    total: 7,
    successful: 5,
    failed: 2,
    commands: ["SCV 계속 찍어", "정찰 보내", "보급고 지어"]
  },
  llm_summary: {
    summary: "경제 안정화 뒤 정찰을 이어가는 운영입니다. sk-test-briefing-secret-123456789",
    raw_prompt: "system prompt must not render",
    api_key: "sk-test-briefing-secret-123456789"
  },
  standing_orders: {
    active_kinds: ["keep_worker_production", "prevent_supply_block"],
    korean_status: "상비 명령: 지속 SCV 생산 활성, 보급 차단 방지 활성"
  }
});
assert.strictEqual(briefing.children[1].children[0].textContent, "판단 근거");
var evidenceText = briefing.children[1].children[1].textContent;
assert(evidenceText.includes("현재 관측 요약"));
assert(evidenceText.includes("미네랄 314"));
assert(evidenceText.includes("가스 82"));
assert(evidenceText.includes("보급 19/27(여유 8)"));
assert(evidenceText.includes("SCV 14기"));
assert(evidenceText.includes("병력 5기"));
assert(evidenceText.includes("적 3기/건물 1개 관측"));
assert(evidenceText.includes("관측 불완전"));
assert(evidenceText.includes("최근 명령 흐름"));
assert(evidenceText.includes("생산/건설 중심"));
assert(evidenceText.includes("성공/정보 2건"));
assert(evidenceText.includes("확인 필요 1건"));
assert(evidenceText.includes("성과/차단 요약"));
assert(evidenceText.includes("성공/정보 2건, 그중 정보 확인 1건"));
assert(evidenceText.includes("차단/확인 필요 1건"));
assert(evidenceText.includes("성공 흐름이 우세"));
assert(evidenceText.includes("성공은 생산/상황 확인 중심"));
assert(evidenceText.includes("차단은 건설 중심"));
assert(evidenceText.includes("주요 차단 사유는 자원/조건 확인"));
assert(evidenceText.includes("상비 명령 요약"));
assert(evidenceText.includes("지속 SCV 생산/보급 차단 방지 정책이 활성"));
assert(evidenceText.includes("경제 생산 유지와 보급 차단 예방"));
assert(evidenceText.includes("압축 메모리 입력"));
assert(evidenceText.includes("누적 7건"));
assert(evidenceText.includes("성공/정보 5건"));
assert(evidenceText.includes("차단/확인 필요 2건"));
assert(evidenceText.includes("LLM 요약 입력"));
assert(evidenceText.includes("경제 안정화 뒤 정찰을 이어가는 운영"));
assert(evidenceText.includes("[redacted]"));
assert(!evidenceText.includes("SCV 계속 찍어"));
assert(!evidenceText.includes("미네랄 부족"));
assert(!evidenceText.includes("sk-test-briefing-secret"));
assert(!evidenceText.includes("system prompt"));
assert(!evidenceText.includes("api_key"));
var adviceDisclosure = briefing.children[5];
assert.strictEqual(adviceDisclosure.tagName, "DETAILS");
assert.strictEqual(adviceDisclosure.children.length, 1);
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "suppressed");
assert(!briefing.textContent.includes("경제와 생산을 유지하세요"));
adviceDisclosure.open = true;
adviceDisclosure.dispatchEvent("toggle");
assert.strictEqual(adviceDisclosure.children.length, 2);
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "visible");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "true");
assert.strictEqual(adviceDisclosure.getAttribute("aria-expanded"), "true");
assert(adviceDisclosure.textContent.includes("경제와 생산을 유지하세요"));
renderStrategyBriefing({
  minerals: 314,
  vespene: 82,
  supply_used: 19,
  supply_cap: 27,
  supply_left: 8,
  own_units: { SCV: 14 },
  army_count: 5,
  own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
  visible_enemy_units: { ZERGLING: 3 },
  visible_enemy_structures: { HATCHERY: 1 },
  observation_complete: true
});
adviceDisclosure = briefing.children[5];
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "true");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "visible");
assert(adviceDisclosure.textContent.includes("경제와 생산을 유지하세요"));
adviceDisclosure.open = false;
adviceDisclosure.dispatchEvent("toggle");
assert.strictEqual(adviceDisclosure.children.length, 1);
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "suppressed");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("aria-expanded"), "false");
assert(!briefing.textContent.includes("경제와 생산을 유지하세요"));
renderStrategyBriefing({
  minerals: 314,
  vespene: 82,
  supply_used: 19,
  supply_cap: 27,
  supply_left: 8,
  own_units: { SCV: 14 },
  army_count: 5,
  own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
  visible_enemy_units: { ZERGLING: 3 },
  visible_enemy_structures: { HATCHERY: 1 },
  observation_complete: true
});
adviceDisclosure = briefing.children[5];
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-toggle-enabled"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "suppressed");
assert(!briefing.textContent.includes("경제와 생산을 유지하세요"));

recentEvents = [
  { command_text: "상태 알려줘", status: "read_only", narration: "현재 상태를 요약합니다." }
];
briefingAdviceToggleEnabled = false;
renderStrategyBriefing({
  minerals: 314,
  vespene: 82,
  supply_used: 19,
  supply_cap: 27,
  supply_left: 8,
  own_units: { SCV: 14 },
  army_count: 5,
  own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
  visible_enemy_units: { ZERGLING: 3 },
  visible_enemy_structures: { HATCHERY: 1 },
  observation_complete: true
});
adviceDisclosure = briefing.children[5];
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-requested"), "false");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "suppressed");
assert.strictEqual(adviceDisclosure.children.length, 1);
assert(!briefing.textContent.includes("경제와 생산을 유지하세요"));

recentEvents = [
  { command_text: "지금 뭐 해야 해?", status: "read_only", narration: "추천 흐름을 답합니다." }
];
briefingAdviceToggleEnabled = false;
renderStrategyBriefing({
  minerals: 314,
  vespene: 82,
  supply_used: 19,
  supply_cap: 27,
  supply_left: 8,
  own_units: { SCV: 14 },
  army_count: 5,
  own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
  visible_enemy_units: { ZERGLING: 3 },
  visible_enemy_structures: { HATCHERY: 1 },
  observation_complete: true
});
adviceDisclosure = briefing.children[5];
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-requested"), "true");
assert.strictEqual(adviceDisclosure.getAttribute("data-advice-state"), "visible");
assert.strictEqual(adviceDisclosure.children.length, 2);
assert(adviceDisclosure.textContent.includes("경제와 생산을 유지하세요"));

renderStrategyBriefing({
  minerals: 314,
  vespene: 82,
  supply_used: 19,
  supply_cap: 27,
  supply_left: 8,
  own_units: { SCV: 14 },
  army_count: 5,
  own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
  visible_enemy_units: { ZERGLING: 3 },
  visible_enemy_structures: { HATCHERY: 1 },
  observation_complete: false,
  compacted_memory: {
    korean_summary: "미네랄 314, 가스 82, 보급 19/27, SCV 14기, 병력 5기"
  },
  llm_summary: {
    summary: "미네랄 314, 가스 82, 보급 19/27, SCV 14기, 병력 5기"
  },
  standing_orders: {
    active_kinds: ["keep_worker_production", "prevent_supply_block"],
    korean_status: "상비 명령: 지속 SCV 생산 활성, 보급 차단 방지 활성"
  }
});
evidenceText = briefing.children[1].children[1].textContent;
assert(evidenceText.includes("현재 관측 요약"));
assert(!evidenceText.includes("압축 메모리 입력"));
assert(!evidenceText.includes("LLM 요약 입력"));

function countOccurrences(text, needle) {
  return (text.match(new RegExp(needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g")) || []).length;
}

var repeatedObservation = Array(12).fill(
  "미네랄 314, 가스 82, 보급 19/27, SCV 14기, 병력 5기"
).join(". ");
var oversizedStrategicContext = Array(20).fill(
  "새 전략은 은폐 밴시 대비 터렛 방어와 앞마당 안정화 확장 생산 정찰 방어 병력 유지입니다"
).join(" ");
renderStrategyBriefing({
  minerals: 314,
  vespene: 82,
  supply_used: 19,
  supply_cap: 27,
  supply_left: 8,
  own_units: { SCV: 14 },
  army_count: 5,
  own_structures: { COMMANDCENTER: 1, BARRACKS: 1 },
  visible_enemy_units: { ZERGLING: 3 },
  visible_enemy_structures: { HATCHERY: 1 },
  observation_complete: false,
  compacted_memory: {
    korean_summary: repeatedObservation + ". " + oversizedStrategicContext
  },
  llm_summary: {
    summary: repeatedObservation + ". " + oversizedStrategicContext
  },
  standing_orders: {
    active_kinds: ["keep_worker_production", "prevent_supply_block"],
    korean_status: "상비 명령: 지속 SCV 생산 활성, 보급 차단 방지 활성"
  }
});
evidenceText = briefing.children[1].children[1].textContent;
assert.strictEqual(countOccurrences(evidenceText, "미네랄 314"), 1);
assert.strictEqual(countOccurrences(evidenceText, "보급 19/27"), 1);
assert(evidenceText.includes("은폐 밴시 대비 터렛 방어"));
assert(evidenceText.includes("...(축약)"));
assert(evidenceText.length <= 1350, "briefing evidence is bounded: " + evidenceText.length);
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_provider_radio_change_immediately_refreshes_model_choices(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        app_script = app_script[: app_script.index('document.getElementById("command-form")')]
        harness = r"""
var radios = [
  { value: "openai", checked: true },
  { value: "anthropic", checked: false },
  { value: "gemini", checked: false },
  { value: "grok", checked: false }
];
var logBox = { setAttribute: function () {}, querySelectorAll: function () { return []; } };
var modelSelect = {
  children: [],
  value: "",
  appendChild: function (child) {
    this.children.push(child);
    return child;
  },
  set innerHTML(value) {
    this.children = [];
  },
  get innerHTML() {
    return "";
  }
};
var document = {
  documentElement: { setAttribute: function () {} },
  createElement: function () { return { value: "", textContent: "" }; },
  getElementById: function (id) {
    if (id === "log") { return logBox; }
    if (id === "llm-model-select") { return modelSelect; }
    return null;
  },
  querySelectorAll: function (selector) {
    return selector === "input[name='llm-provider-choice']" ? radios : [];
  },
  querySelector: function (selector) {
    if (selector === "input[name='llm-provider-choice']:checked") {
      return radios.find(function (radio) { return radio.checked; }) || null;
    }
    var valueMatch = selector.match(/input\[name='llm-provider-choice'\]\[value='([^']+)'\]/);
    if (valueMatch) {
      return radios.find(function (radio) { return radio.value === valueMatch[1]; }) || null;
    }
    return null;
  }
};
var window = {
  location: { search: "" },
  setTimeout: function () {},
  SpeechRecognition: null,
  webkitSpeechRecognition: null
};
var fetch = function () { return Promise.resolve({ json: function () { return {}; } }); };
var setInterval = function () {};
var URLSearchParams = global.URLSearchParams;
function modelValues() {
  return modelSelect.children.map(function (option) { return option.value; });
}
"""
        scenario = r"""
const assert = require("assert");
handleProviderChoiceChange("anthropic");
assert.strictEqual(selectedProviderValue(), "anthropic");
assert(modelValues().includes("claude-fable-4-5-20251001"));
assert(!modelValues().includes("gpt-5.5"));
assert.strictEqual(modelSelect.value, "claude-fable-4-5-20251001");
handleProviderChoiceChange("gemini");
assert.strictEqual(selectedProviderValue(), "gemini");
assert(modelValues().includes("gemini-3.5-flash"));
assert(!modelValues().includes("claude-fable-4-5-20251001"));
assert.strictEqual(modelSelect.value, "gemini-3.5-flash");
handleProviderChoiceChange("grok");
assert.strictEqual(selectedProviderValue(), "grok");
assert(modelValues().includes("grok-4.3"));
assert(!modelValues().includes("gemini-3.5-flash"));
assert.strictEqual(modelSelect.value, "grok-4.3");
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_llm_api_key_status_js_transitions_are_labeled(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        app_script = app_script[: app_script.index('document.getElementById("command-form")')]
        harness = r"""
function element(id) {
  return {
    id: id,
    textContent: "",
    className: "",
    disabled: false,
    placeholder: "",
    value: "",
    children: [],
    attributes: {},
    setAttribute: function (name, value) { this.attributes[name] = value; },
    getAttribute: function (name) { return this.attributes[name] || ""; },
    appendChild: function (child) { this.children.push(child); return child; },
    set innerHTML(value) { this.children = []; },
    get innerHTML() { return ""; }
  };
}
var nodes = {
  "llm-status": element("llm-status"),
  "llm-status-label": element("llm-status-label"),
  "llm-status-message": element("llm-status-message"),
  "command-input": element("command-input"),
  "send-button": element("send-button"),
  "voice-button": element("voice-button"),
  "llm-model-select": element("llm-model-select"),
  "log": element("log")
};
var radios = [
  { value: "openai", checked: true },
  { value: "anthropic", checked: false },
  { value: "gemini", checked: false },
  { value: "grok", checked: false }
];
var document = {
  documentElement: { setAttribute: function () {} },
  createElement: function () { return element(""); },
  getElementById: function (id) { return nodes[id] || null; },
  querySelectorAll: function (selector) {
    if (selector === "input[name='llm-provider-choice']") { return radios; }
    return [];
  },
  querySelector: function (selector) {
    if (selector === "input[name='llm-provider-choice']:checked") {
      return radios.find(function (radio) { return radio.checked; }) || null;
    }
    var valueMatch = selector.match(/input\[name='llm-provider-choice'\]\[value='([^']+)'\]/);
    if (valueMatch) {
      return radios.find(function (radio) { return radio.value === valueMatch[1]; }) || null;
    }
    return null;
  }
};
var window = {
  location: { search: "" },
  setTimeout: function () {},
  SpeechRecognition: null,
  webkitSpeechRecognition: null
};
var fetch = function () { return Promise.resolve({ json: function () { return {}; } }); };
var setInterval = function () {};
var URLSearchParams = global.URLSearchParams;
"""
        scenario = r"""
const assert = require("assert");
setLlmStatus("setting", "llmSettingLabel", t("llmSaving"));
assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "setting");
assert.strictEqual(nodes["llm-status-label"].textContent, "설정 중");
assert.strictEqual(nodes["llm-status-message"].textContent, "LLM 키 설정 중...");

renderLlmSettings({ configured: false, provider: "openai", model: "gpt-5.5" });
assert.strictEqual(nodes["llm-model-select"].value, "gpt-5.5");
assert.strictEqual(nodes["send-button"].disabled, false);
assert(nodes["command-input"].placeholder.includes("MicroMachine"));

renderLlmSettings({ configured: true, provider: "openai", model: "gpt-test" });
assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "success");
assert.strictEqual(nodes["llm-status-label"].textContent, "설정 완료");
assert(nodes["llm-status-message"].textContent.includes("LLM 키 설정됨"));
assert.strictEqual(nodes["send-button"].disabled, false);

setLlmStatus("failed", "llmFailedLabel", t("llmSaveFailed") + ": provider rejected");
assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "failed");
assert.strictEqual(nodes["llm-status-label"].textContent, "설정 실패");
assert(nodes["llm-status-message"].textContent.includes("provider rejected"));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_llm_api_key_async_setup_attempts_transition_safely(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("<script>") + len("<script>")
        script_end = page.index("</script>", script_start)
        app_script = page[script_start:script_end]
        app_script = app_script[
            : app_script.index('var providerOptions = document.getElementById("llm-provider-options")')
        ]
        harness = r"""
function element(id) {
  return {
    id: id,
    textContent: "",
    className: "",
    disabled: false,
    placeholder: "",
    value: "",
    children: [],
    attributes: {},
    listeners: {},
    setAttribute: function (name, value) { this.attributes[name] = value; },
    getAttribute: function (name) { return this.attributes[name] || ""; },
    appendChild: function (child) { this.children.push(child); return child; },
    addEventListener: function (name, handler) { this.listeners[name] = handler; },
    dispatchEvent: function (event) {
      if (this.listeners[event.type]) { this.listeners[event.type](event); }
    },
    focus: function () {},
    set innerHTML(value) { this.children = []; },
    get innerHTML() { return ""; }
  };
}
var nodes = {
  "command-form": element("command-form"),
  "llm-form": element("llm-form"),
  "llm-api-key": element("llm-api-key"),
  "llm-status": element("llm-status"),
  "llm-status-label": element("llm-status-label"),
  "llm-status-message": element("llm-status-message"),
  "command-input": element("command-input"),
  "send-button": element("send-button"),
  "voice-button": element("voice-button"),
  "llm-model-select": element("llm-model-select"),
  "live-status": element("live-status"),
  "live-open-button": element("live-open-button"),
  "runtime-start-button": element("runtime-start-button"),
  "runtime-refresh-button": element("runtime-refresh-button"),
  "micromachine-blackboard-dir": element("micromachine-blackboard-dir"),
  "log": element("log")
};
nodes["llm-model-select"].value = "gpt-test";
nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-js-test";
var radios = [
  { value: "openai", checked: true, addEventListener: function () {} },
  { value: "anthropic", checked: false, addEventListener: function () {} },
  { value: "gemini", checked: false, addEventListener: function () {} },
  { value: "grok", checked: false, addEventListener: function () {} }
];
var commandModeRadios = [
  { value: "micromachine", checked: true, addEventListener: function () {} },
  { value: "legacy_commander", checked: false, addEventListener: function () {} }
];
var document = {
  documentElement: { setAttribute: function () {} },
  createElement: function () { return element(""); },
  getElementById: function (id) { return nodes[id] || null; },
  querySelectorAll: function (selector) {
    if (selector === "input[name='llm-provider-choice']") { return radios; }
    if (selector === "input[name='command-mode']") { return commandModeRadios; }
    if (selector === "[data-command]") { return []; }
    return [];
  },
  querySelector: function (selector) {
    if (selector === "input[name='llm-provider-choice']:checked") {
      return radios.find(function (radio) { return radio.checked; }) || null;
    }
    var valueMatch = selector.match(/input\[name='llm-provider-choice'\]\[value='([^']+)'\]/);
    if (valueMatch) {
      return radios.find(function (radio) { return radio.value === valueMatch[1]; }) || null;
    }
    if (selector === "input[name='command-mode']:checked") {
      return commandModeRadios.find(function (radio) { return radio.checked; }) || null;
    }
    return null;
  }
};
var window = {
  location: { search: "" },
  setTimeout: function () {},
  open: function () {},
  SpeechRecognition: null,
  webkitSpeechRecognition: null
};
var setInterval = function () {};
var URLSearchParams = global.URLSearchParams;
var requests = [];
function deferred() {
  var resolve;
  var reject;
  var promise = new Promise(function (resolveFn, rejectFn) {
    resolve = resolveFn;
    reject = rejectFn;
  });
  return { promise: promise, resolve: resolve, reject: reject };
}
function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    text: function () { return Promise.resolve(JSON.stringify(data)); }
  };
}
var fetch = function (url, options) {
  var item = { url: url, options: options || {}, deferred: deferred() };
  requests.push(item);
  return item.deferred.promise;
};
function submitKey(value) {
  nodes["llm-api-key"].value = value;
  nodes["llm-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
}
function flushPromises() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}
"""
        scenario = r"""
const assert = require("assert");
(async function () {
  submitKey("unit-test-success-input");
  assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "setting");
  assert.strictEqual(nodes["llm-status-label"].textContent, "설정 중");
  assert.strictEqual(nodes["llm-status-message"].textContent, "LLM 키 설정 중...");
  assert.strictEqual(requests[0].url, "/api/llm");
  assert.strictEqual(JSON.parse(requests[0].options.body).api_key, "unit-test-success-input");

  requests[0].deferred.resolve(response(200, {
    configured: true,
    key_present: true,
    provider: "openai",
    model: "gpt-test"
  }));
  await flushPromises();
  assert(requests[1].url.indexOf("/api/runtime/status?mode=micromachine") === 0);
  requests[1].deferred.resolve(response(200, {
    enabled: true,
    status: "idle",
    mode: "micromachine",
    url: "",
    error: ""
  }));
  await flushPromises();
  assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "success");
  assert.strictEqual(nodes["llm-status-label"].textContent, "설정 완료");
  assert(nodes["llm-status-message"].textContent.includes("LLM 키 설정됨"));
  assert(!nodes["llm-status-message"].textContent.includes("unit-test-success-input"));
  assert.strictEqual(nodes["llm-api-key"].value, "");
  assert.strictEqual(nodes["send-button"].disabled, false);
  assert(nodes["live-status"].textContent.includes("MicroMachine 런타임 대기 중"));

  submitKey("unit-test-failed-input");
  assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "setting");
  requests[2].deferred.resolve(response(400, {
    configured: false,
    error: "provider rejected request"
  }));
  await flushPromises();
  assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "failed");
  assert.strictEqual(nodes["llm-status-label"].textContent, "설정 실패");
  assert(nodes["llm-status-message"].textContent.includes("provider rejected request"));
  assert(!nodes["llm-status-message"].textContent.includes("unit-test-failed-input"));

  submitKey("unit-test-stale-success");
  var staleSuccess = requests[3];
  submitKey("unit-test-latest-failure");
  var latestFailure = requests[4];
  latestFailure.deferred.resolve(response(400, {
    configured: false,
    error: "latest attempt failed"
  }));
  await flushPromises();
  assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "failed");
  assert(nodes["llm-status-message"].textContent.includes("latest attempt failed"));

  staleSuccess.deferred.resolve(response(200, {
    configured: true,
    key_present: true,
    provider: "openai",
    model: "stale-model"
  }));
  await flushPromises();
  assert.strictEqual(nodes["llm-status"].getAttribute("data-llm-state"), "failed");
  assert(nodes["llm-status-message"].textContent.includes("latest attempt failed"));
  assert(!nodes["llm-status-message"].textContent.includes("stale-model"));
})().catch(function (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(app_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_embedded_javascript_is_syntax_valid(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        start = page.index("<script>") + len("<script>")
        end = page.index("</script>", start)
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(page[start:end])
            script_file.flush()
            result = subprocess.run(
                [node, "--check", script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_standalone_dry_run_wires_process_local_llm_control(self):
        source = inspect.getsource(web_gui.main)
        self.assertIn("LocalLLMControl", source)
        self.assertIn("HybridCommandInterpreter", source)
        self.assertIn("llm_control=llm_control", source)


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

    def test_live_launch_status_redacts_submitted_api_key_from_child_output(self):
        submitted_key = "unit-test-" + "live-launch-key"

        class FakeProcess:
            pid = 4321
            returncode = None
            stdout = [
                f"booting with {submitted_key}\n",
                f"voiStarcraft2 커맨더 웹 GUI 시작: http://127.0.0.1:9876/?key={submitted_key}\n",
            ]

            def poll(self):
                return None

        with mock.patch.object(web_gui.subprocess, "Popen", return_value=FakeProcess()):
            launcher = web_gui._LiveLaunchManager()
            started = launcher.start("openai", submitted_key, "gpt-test")

        deadline = time.monotonic() + POLL_DEADLINE_SECONDS
        snapshot = launcher.snapshot()
        while time.monotonic() < deadline and snapshot.get("status") != "ready":
            time.sleep(POLL_INTERVAL_SECONDS)
            snapshot = launcher.snapshot()

        document = json.dumps({"started": started, "snapshot": snapshot}, ensure_ascii=False)
        self.assertIn("[redacted]", document)
        self.assertNotIn(submitted_key, document)
        self.assertEqual(snapshot["status"], "ready")


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
        self.assertIn("MicroMachine", output)
        self.assertIn("legacy commander mode", output)

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
