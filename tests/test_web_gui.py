"""W3 acceptance tests for the stdlib-only commander web GUI.

Every server test binds an ephemeral localhost port (``port=0``) and talks
plain ``http.client``; no FastAPI/Flask, no network beyond loopback, no
optional dependencies, no API keys. Asynchronous outcomes are polled with a
hard deadline instead of fixed sleeps.
"""

import contextlib
import concurrent.futures
from copy import deepcopy
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
from urllib.parse import quote

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
)
from starcraft_commander.micromachine_terran_capabilities import (
    TERRAN_UNIT_FAMILIES,
)
from starcraft_commander import web_gui
from starcraft_commander.demo_sc2 import build_dry_run_session
from starcraft_commander.llm_interpreter import LocalLLMControl
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationProviderRequest,
)
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


def battlefield_projection_telemetry(
    *,
    update_id="battlefield-current",
    frame=320,
    generation=7,
    session_epoch=1700000000000,
):
    """Return one complete authoritative battlefield projection fixture."""

    return {
        "frame": frame,
        "battlefield_overview": {
            "schema_version": 2,
            "authority": "micromachine_cpp",
            "identity": {
                "update_id": update_id,
                "scope": "battlefield",
                "session_epoch": session_epoch,
                "generation": generation,
                "stage": "observed",
                "game_frame": frame,
            },
            "eligible_combat_count": 8,
            "explicit_operation_owned_count": 4,
            "autonomous_owned_count": 2,
            "unassigned_count": 2,
            "duplicate_owner_count": 0,
            "operation_ownership": [
                {
                    "identity": {
                        "update_id": "battlefield-operation",
                        "scope": "operation:flank-alpha",
                        "session_epoch": session_epoch,
                        "operation_id": "flank-alpha",
                        "generation": 3,
                        "stage": "effect_observed",
                        "game_frame": frame,
                    },
                    "operation_id": "flank-alpha",
                    "generation": 3,
                    "operation_route": {
                        "requested_route_type": "flank_right",
                        "applied_route_type": "flank_right",
                        "location_intent": "enemy_natural",
                        "target_type": "enemy_expansion",
                        "resolved_target_label": "enemy natural",
                        "target_x": 120.0,
                        "target_y": 44.0,
                        "target_evidence": "observed_enemy_structure",
                    },
                    "operation_lifetime": {
                        "mode": "until_completed",
                        "completion_state": "active",
                        "completion_conditions": [
                            "target_reached",
                            "cancelled_by_user",
                        ],
                        "duration_seconds": 300,
                        "issued_at_frame": 200,
                        "deadline_frame": 4700,
                        "standing": False,
                        "completed": False,
                        "completion_reason": "",
                        "completed_frame": 0,
                    },
                    "operation_ownership": {
                        "owner_count": 4,
                        "owner_tags": [101, 102, 103, 104],
                        "integrity_status": "valid",
                    },
                    "operation_launch_policy": {
                        "min_units": 4,
                        "max_units": 4,
                        "allow_partial_requested": True,
                        "strict_scope": False,
                        "partial_launch_allowed": True,
                        "partial_launch_safe": True,
                        "launch_count": 4,
                        "missing_count": 0,
                        "decision": "launch",
                        "blocker": "",
                        "recommended_choices": [],
                        "safety_evidence": {
                            "evaluated_at_frame": frame,
                            "protected_defense_minimum_respected": True,
                            "source_operation_minimum_respected": True,
                            "transfer_admission": "accepted",
                            "emergency_preemption": "none",
                        },
                    },
                    "operation_completion": {
                        "movement_observed": True,
                        "engagement_observed": False,
                        "target_reached": False,
                        "terminal": False,
                        "state": "active",
                        "reason": "",
                        "frame": 0,
                        "generation": 3,
                    },
                    "operation_transfer_selection": {
                        "present": False,
                        "edit_resolution": "",
                        "identity_valid": False,
                        "blocker": "",
                        "identity": {
                            "update_id": "",
                            "source_owner_id": "",
                            "counterpart_operation_id": "",
                            "source_action": "",
                            "counterpart_action": "",
                            "source_generation": 0,
                            "counterpart_generation": 0,
                            "requested_source_generation": 0,
                            "requested_counterpart_generation": 0,
                            "requested_count": 0,
                            "selected_unit_tags": [],
                        },
                        "write_identity": {
                            "update_id": "",
                            "operation_id": "",
                            "operation_generation": 0,
                            "stage": "",
                            "game_frame": 0,
                            "selection_identity": {
                                "update_id": "",
                                "source_owner_id": "",
                                "counterpart_operation_id": "",
                                "source_action": "",
                                "counterpart_action": "",
                                "source_generation": 0,
                                "counterpart_generation": 0,
                                "requested_source_generation": 0,
                                "requested_counterpart_generation": 0,
                                "requested_count": 0,
                                "selected_unit_tags": [],
                            },
                        },
                        "successful_write_acknowledgement": {
                            "acknowledged": False,
                            "acknowledged_frame": 0,
                            "identity": {
                                "update_id": "",
                                "operation_id": "",
                                "operation_generation": 0,
                                "stage": "",
                                "game_frame": 0,
                                "selection_identity": {
                                    "update_id": "",
                                    "source_owner_id": "",
                                    "counterpart_operation_id": "",
                                    "source_action": "",
                                    "counterpart_action": "",
                                    "source_generation": 0,
                                    "counterpart_generation": 0,
                                    "requested_source_generation": 0,
                                    "requested_counterpart_generation": 0,
                                    "requested_count": 0,
                                    "selected_unit_tags": [],
                                },
                            },
                        },
                    },
                }
            ],
            "autonomous_ownership": [
                {
                    "owner_id": "BaseDefense:main",
                    "owner_count": 2,
                    "owner_tags": [201, 202],
                    "integrity_status": "valid",
                }
            ],
            "unassigned_unit_tags": [301, 302],
            "bases": [
                {
                    "base_id": "main",
                    "semantic_anchor": "self_main",
                    "base_readiness": {
                        "readiness_state": "ready",
                        "reason": "protected_minimum_satisfied",
                        "ground_threat": 2.0,
                        "air_threat": 0.0,
                        "observed_enemy_strength": 2.0,
                        "last_evidence_frame": frame - 2,
                        "evidence_class": "observed_enemy_units",
                        "assigned_defender_count": 2,
                        "ground_capable_defender_count": 2,
                        "air_capable_defender_count": 2,
                        "required_defender_count": 2,
                        "required_ground_defender_count": 2,
                        "required_air_defender_count": 0,
                        "protected_minimum": [
                            {
                                "family": "marine",
                                "role": "defender",
                                "count": 2,
                            }
                        ],
                    },
                }
            ],
            "transfer_availability": {
                "evaluated_at_frame": frame,
                "atomic_revalidation_required": True,
                "entries": [
                    {
                        "source_owner_id": "flank-alpha",
                        "source_owner_count": 4,
                        "protected_minimum": 2,
                        "transferable_count": 2,
                        "transferable_unit_tags": [103, 104],
                        "transfer_safe": True,
                        "atomic_runtime_blocker": "",
                        "recommended_resolution_choices": [],
                        "safety_evidence": {
                            "evaluated_at_frame": frame,
                            "protected_minimum_respected": True,
                            "atomic_revalidation_required": True,
                        },
                        "atomic_revalidation_inputs": {
                            "requested": False,
                            "selected_unit_tags": [103, 104],
                            "requested_count": 0,
                            "source_owner_id": "flank-alpha",
                            "action": "availability",
                            "requested_generation": 3,
                            "counterpart_operation_id": "",
                            "counterpart_action": "",
                            "counterpart_generation": 0,
                            "requested_source_generation": 0,
                            "requested_counterpart_generation": 0,
                            "edit_resolution": "none",
                            "counterpart_present": False,
                            "counterpart_pending": False,
                            "reciprocal_action": True,
                            "reciprocal_counterpart": True,
                            "reciprocal_generation": True,
                            "reciprocal_count": True,
                            "source_active": True,
                            "destination_active": True,
                            "ownership_integrity": True,
                            "operation_assignments_match": True,
                            "squad_assignments_match": True,
                            "action_assignments_match": True,
                            "role_assignments_match": True,
                            "atomic_revalidation_ready": True,
                        },
                    }
                ],
            },
        },
    }


def attached_runtime_telemetry(document, runtime_instance_id):
    payload = {
        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
        "frame": int(document["frame"]),
        "bot_name": "MicroMachine",
        "race": "Terran",
        "managers": {},
        "active_modulation_ids": [],
        "last_failure": None,
        "runtime_instance_id": runtime_instance_id,
    }
    payload.update(deepcopy(document))
    payload["runtime_instance_id"] = runtime_instance_id
    return payload


def semantic_operation_payload(
    *,
    operation_id="flank-alpha",
    generation=1,
    requested_generation=None,
    frame=100,
    session_epoch=1700000000000,
    execution_state="action_issued",
    owner_count=4,
    required_count=4,
    blocker="",
    launch_decision="launch",
    movement=False,
    engagement=False,
    target_reached=False,
    terminal=False,
    standing=False,
    disposition="active",
    operation_edit=None,
):
    """Return one reducer-ready canonical operation status fixture."""

    requested = (
        generation
        if requested_generation is None
        else requested_generation
    )
    update_id = f"update-{operation_id}-{requested}"
    overview = deepcopy(
        battlefield_projection_telemetry(
            update_id=f"battlefield-{session_epoch}",
            frame=frame,
            generation=max(1, generation),
            session_epoch=session_epoch,
        )["battlefield_overview"]
    )
    projection = overview["operation_ownership"][0]
    projection["identity"].update(
        {
            "update_id": update_id,
            "scope": f"operation:{operation_id}",
            "operation_id": operation_id,
            "generation": generation,
            "stage": "completed" if terminal else execution_state,
            "game_frame": frame,
        }
    )
    projection["operation_id"] = operation_id
    projection["generation"] = generation
    projection["operation_ownership"]["owner_count"] = owner_count
    projection["operation_ownership"]["owner_tags"] = list(
        range(1000, 1000 + owner_count)
    )
    projection["operation_launch_policy"].update(
        {
            "min_units": required_count,
            "max_units": required_count,
            "launch_count": owner_count,
            "missing_count": max(0, required_count - owner_count),
            "decision": launch_decision,
            "blocker": blocker,
        }
    )
    projection["operation_lifetime"].update(
        {
            "standing": standing,
            "completed": terminal,
            "completion_state": "completed" if terminal else "active",
            "completion_reason": "target_reached" if terminal else "",
            "completed_frame": frame if terminal else 0,
        }
    )
    projection["operation_completion"].update(
        {
            "movement_observed": movement,
            "engagement_observed": engagement,
            "target_reached": target_reached,
            "terminal": terminal,
            "state": "completed" if terminal else "active",
            "reason": "target_reached" if terminal else "",
            "frame": frame if terminal else 0,
            "generation": generation,
        }
    )
    stages = [
        {"name": "parsed", "ok": True},
        {"name": "consumed_by_manager", "ok": True},
    ]
    if owner_count:
        stages.append({"name": "queued_or_assigned", "ok": True})
    if execution_state in {
        "action_issued",
        "effect_observed",
        "moving",
        "engaged",
        "completed",
    }:
        stages.extend(
            [
                {"name": "order_issued", "ok": True},
                {"name": "action_issued", "ok": True},
            ]
        )
    operation = {
        "operation_id": operation_id,
        "operation_generation": generation,
        "requested_operation_generation": requested,
        "update_id": update_id,
        "command_text": f"operate {operation_id}",
        "mission": "attack",
        "transport_status": "published",
        "consumption_status": "consumed",
        "telemetry_frame": frame,
        "disposition": disposition,
        "compile_result": {"status": "compiled", "update_id": update_id},
        "operation_convergence": {
            "target_count": required_count,
            "represented_count": owner_count,
            "missing_count": max(0, required_count - owner_count),
            "blocker": blocker,
        },
        "operation_edit": dict(operation_edit or {}),
        "intervention": {
            "command_execution": {
                "command_id": update_id,
                "state": execution_state,
                "operation_id": operation_id,
                "operation_generation": generation,
                "blocker_reason": blocker,
                "stages": stages,
            }
        },
        "battlefield_operation": projection,
    }
    return {
        "blackboard_scope_id": "scope-semantic-test",
        "battlefield_overview": overview,
        "operations": [operation],
    }


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


class FakePolicyModulationLLMControl(FakeConfiguredLLMControl):
    """Configured LLM control that emits MicroMachine policy modulation JSON."""

    def is_available(self):
        return True

    def propose_policy_modulation(self, request):
        if request.command_text.strip() in {"안녕", "안녕하세요", "hello", "hi"}:
            return {
                "status": "clarification_required",
                "assistant_message": "전술 명령이 아니라 인사로 이해했어요. 원하는 전략을 말해 주세요.",
                "clarification_prompt": "전술 의도를 더 구체적으로 말해 주세요.",
            }
        if any(token in request.command_text for token in ("수비", "탱크", "버텨")):
            return {
                "source": "smoke_keyword",
                "status": "compiled",
                "assistant_message": "탱크 중심 수비로 해석해서 방어 성향과 병력 보존을 높였습니다.",
                "modulation": {
                    "goal": request.command_text,
                    "override_level": "constraint",
                    "confidence": 0.82,
                    "ttl_seconds": 120,
                    "strategy": {"posture": "defensive"},
                    "combat": {"defend_bias": 0.65, "aggression": -0.2},
                    "squad": {"defense_bias": 0.45},
                    "tags": ["fake_llm_policy_modulation"],
                }
            }
        return {
            "source": "smoke_keyword",
            "status": "compiled",
            "assistant_message": "공격 압박 의도로 해석해서 전투 성향을 높였습니다.",
            "modulation": {
                "goal": request.command_text,
                "override_level": "bias",
                "confidence": 0.81,
                "ttl_seconds": 120,
                "strategy": {"posture": "pressure"},
                "combat": {"aggression": 0.45},
                "tags": ["fake_llm_policy_modulation"],
            }
        }


class BlockingPolicyModulationLLMControl(FakePolicyModulationLLMControl):
    """LLM test double that blocks until the test releases forced-tool output."""

    def __init__(self, *, started, release):
        self.started = started
        self.release = release

    def propose_policy_modulation(self, request):
        self.started.set()
        if not self.release.wait(2):
            raise TimeoutError("test LLM release event was not set")
        return super().propose_policy_modulation(request)


class NoToolPolicyModulationLLMControl(FakeConfiguredLLMControl):
    """Configured LLM test double that returns plain text instead of tool JSON."""

    def is_available(self):
        return True

    def propose_policy_modulation(self, request):
        return {
            "source": "llm",
            "status": "refused",
            "refusal_reason": (
                "LLM policy modulation response had no forced-tool or "
                "structured JSON input."
            ),
        }


class TypedApiFailurePolicyModulationLLMControl(FakeConfiguredLLMControl):
    """Configured LLM test double that reports one typed API failure."""

    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    def propose_policy_modulation(self, request):
        self.calls += 1
        return {
            "source": "llm",
            "status": "refused",
            "failure_kind": "api_error",
            "llm_attempt_count": 1,
            "llm_repair_reason": "",
            "llm_duration_ms": 321,
            "refusal_reason": (
                "LLM policy modulation failed with request timed out."
            ),
        }


class SchemaInvalidPolicyModulationLLMControl(FakeConfiguredLLMControl):
    """Configured LLM test double that returns compiler-invalid DSL once."""

    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    def propose_policy_modulation(self, request):
        self.calls += 1
        return {
            "source": "llm",
            "status": "compiled",
            "assistant_message": "공격 성향을 올리겠습니다.",
            "modulation": {
                "source": "llm",
                "goal": request.command_text,
                "override_level": "bias",
                "combat": {"aggression": "very high"},
            },
        }


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
    def test_marine_scout_task_only_requires_scout_effect(self) -> None:
        vector = {
            "goal": "마린 1기로 적 본진을 정찰해 적 정보 확보",
            "combat": {
                "aggression": -0.25,
                "commitment_level": 0.2,
                "target_priority_biases": {
                    "enemy_army": -0.2,
                    "production": 0.1,
                    "townhall": 0.15,
                },
            },
            "scouting": {
                "scout_priority": 0.85,
                "risk_tolerance": 0.25,
            },
            "tactical_task": {
                "task_type": "scout_with_units",
                "unit_classes": ["TERRAN_MARINE"],
                "min_units": 1,
                "max_units": 1,
            },
            "tags": ["scouting_map_control", "single_unit_scout"],
        }

        self.assertEqual(
            ("scout",),
            web_gui._micromachine_expected_tactical_effects(vector),
        )

    """End-to-end HTTP tests against a dry-run session on an ephemeral port."""

    def setUp(self):
        self.session, self.bot = build_dry_run_session()
        self.bridge = SessionLoopBridge(
            session=self.session,
            llm_control=FakePolicyModulationLLMControl(),
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

    def get_sse(self, path="/api/events?once=1", headers=None):
        status, content_type, payload = self.request(
            "GET",
            path,
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", content_type)
        return payload.decode("utf-8")

    def parse_sse_events(self, stream):
        events = []
        for block in stream.split("\n\n"):
            fields = {}
            for line in block.splitlines():
                if not line or line.startswith(":") or ":" not in line:
                    continue
                name, value = line.split(":", 1)
                fields[name] = value.lstrip()
            if "data" not in fields:
                continue
            fields["data"] = json.loads(fields["data"])
            events.append(fields)
        return events

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

    def attach_fake_micromachine_runtime(self, directory):
        runtime_instance_id = "f" * 32
        telemetry_path = os.path.join(directory, "latest_telemetry.json")
        if os.path.exists(telemetry_path):
            with open(telemetry_path, encoding="utf-8") as handle:
                telemetry = json.load(handle)
            telemetry["runtime_instance_id"] = runtime_instance_id
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)

        class FakeAttachedMicroMachineLauncher:
            def snapshot(self, blackboard_dir=""):
                root = blackboard_dir or directory
                telemetry_path = os.path.join(root, "latest_telemetry.json")
                telemetry_frame = None
                if os.path.exists(telemetry_path):
                    with open(telemetry_path, encoding="utf-8") as handle:
                        telemetry = json.load(handle)
                    frame = telemetry.get("frame")
                    if type(frame) is int:
                        telemetry_frame = frame
                return {
                    "enabled": True,
                    "mode": "micromachine",
                    "status": "connected",
                    "blackboard_dir": root,
                    "pid": 4242,
                    "runtime_instance_id": runtime_instance_id,
                    "runtime_attached": True,
                    "telemetry_present": telemetry_frame is not None,
                    "telemetry_current_for_process": telemetry_frame is not None,
                    "telemetry_stale_or_detached": False,
                    "telemetry_frame": telemetry_frame,
                }

            def validated_snapshot(self, blackboard_dir=""):
                root = blackboard_dir or directory
                telemetry_path = os.path.join(root, "latest_telemetry.json")
                with open(telemetry_path, encoding="utf-8") as handle:
                    telemetry = json.load(handle)
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata=self.snapshot(root),
                    telemetry_document=telemetry,
                )

        self.server._http.micromachine_launcher = FakeAttachedMicroMachineLauncher()

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
            "MICROMACHINE_CHAT_TIMEOUT_MS = 35000",
            "archivedChatEvents",
            "appendCompactText",
            "renderArchivedChatDetails",
            "messageExpand",
            "window.location.assign(status.url)",
            "live-open-button",
            "runtime-start-button",
            "runtime-refresh-button",
            "micromachine-enemy-difficulty",
            "수동 live-hold 적 난이도 (1..10)",
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
            "async_publish: true",
            "if (isMicroMachineCommandMode()) { return; }",
            "microMachineStateDashboardDisabled",
            "renderMicroMachineStatePlaceholder",
            "if (isMicroMachineCommandMode()) {\n    renderMicroMachineStatePlaceholder();",
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
            "/api/events",
            "EventSource",
            "connectEventChannel",
            "startPollingFallback",
            "stopPollingFallback",
            "lastEventSeq",
            'status: "received"',
            "생산 건물 사용 중",
            "편성 배정 대기",
            "setInterval(pollHistory",
            "setInterval(pollState",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)
        self.assertNotIn(
            'id="micromachine-intervention-dashboard" aria-live=',
            page,
        )
        self.assertRegex(
            page,
            (
                r'id="command-console-announcement"\s+'
                r'class="sr-only"\s+role="status"\s+aria-live="polite"'
            ),
        )

    def test_sse_initial_snapshot_contains_authoritative_sources_and_heartbeat(self):
        stream = self.get_sse()
        events = self.parse_sse_events(stream)

        self.assertIn(": heartbeat", stream)
        self.assertEqual(events[0]["event"], "snapshot")
        document = events[0]["data"]
        self.assertEqual(document["event_type"], "snapshot")
        payload = document["payload"]
        self.assertIn("state", payload)
        self.assertIn("history", payload)
        self.assertIn("micromachine_status", payload)
        self.assertTrue(payload["state"]["available"])

    def test_operation_event_snapshot_hydration_does_not_consume_live_watermark(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        scope_id = "scope-operation-events"
        first = {
            "timeline_seq": 1,
            "operation_id": "scout-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-scout-alpha",
            "kind": "assigned",
            "game_frame": 100,
            "summary": "assigned",
            "technical": {},
        }
        status = {
            "blackboard_scope_id": scope_id,
            "operation_events": [first],
        }

        handler._publish_new_operation_events(
            status,
            blackboard_dir="/tmp/operation-events",
            publish=False,
        )
        self.assertEqual(
            [],
            [
                event
                for event in self.server._http.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
            ],
        )

        second = {
            **first,
            "timeline_seq": 2,
            "kind": "submitted",
            "game_frame": 101,
            "summary": "submitted",
        }
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": scope_id,
                "operation_events": [first, second],
            },
            blackboard_dir="/tmp/operation-events",
            publish=True,
        )
        operation_events = [
            event
            for event in self.server._http.event_journal.events_after(0)
            if event["event_type"] == "operation_event"
        ]
        self.assertEqual(2, len(operation_events))
        self.assertEqual(
            ["assigned", "submitted"],
            [event["payload"]["kind"] for event in operation_events],
        )
        self.assertEqual("update-scout-alpha", operation_events[1]["update_id"])
        self.assertEqual(1, operation_events[1]["generation"])
        self.assertEqual(101, operation_events[1]["game_frame"])

    def test_sse_snapshot_cut_does_not_block_publication_and_replays_newer_event(self):
        original = self.bridge.micromachine_status
        self.server._http.publish_event(
            "state",
            {"available": True, "marker": "snapshot-race-baseline"},
        )
        status_entered = threading.Event()
        status_release = threading.Event()
        publisher_started = threading.Event()

        def blocking_status(*, blackboard_dir=""):
            status_entered.set()
            if not status_release.wait(2):
                raise TimeoutError("test did not release snapshot status")
            return original(blackboard_dir=blackboard_dir)

        self.bridge.micromachine_status = blocking_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status",
            original,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            response_future = pool.submit(self.get_sse)
            self.assertTrue(
                status_entered.wait(1),
                "authoritative snapshot did not enter MicroMachine status",
            )

            def publish_during_source_cut():
                publisher_started.set()
                return self.server._http.publish_event(
                    "command_received",
                    {
                        "command_text": "snapshot-race-command",
                        "status": "received",
                    },
                    operation_id="snapshot-race-command",
                    generation=1,
                )

            publisher_future = pool.submit(publish_during_source_cut)
            self.assertTrue(publisher_started.wait(1))
            time.sleep(0.1)
            self.assertTrue(
                publisher_future.done(),
                "slow snapshot source reads blocked real-time publication",
            )
            published = publisher_future.result(timeout=3)
            status_release.set()
            stream = response_future.result(timeout=3)

        events = self.parse_sse_events(stream)
        snapshot = events[0]
        self.assertEqual("snapshot", snapshot["event"])
        snapshot_cursor = snapshot["data"]["event_seq"]
        self.assertGreater(published["event_seq"], snapshot_cursor)
        self.assertIn(
            "snapshot-race-command",
            [
                event["data"]["payload"].get("command_text")
                for event in events
                if event["event"] == "command_received"
            ],
        )

    def test_sse_snapshot_restarts_when_concurrent_events_roll_past_retention(self):
        original = self.bridge.micromachine_status
        self.server._http.event_journal = web_gui._WebEventJournal(
            retention=2
        )
        baseline = self.server._http.publish_event(
            "state",
            {"available": True, "marker": "rollover-baseline"},
        )
        status_entered = threading.Event()
        status_release = threading.Event()

        def blocking_status(*, blackboard_dir=""):
            status_entered.set()
            if not status_release.wait(2):
                raise TimeoutError("test did not release snapshot status")
            return original(blackboard_dir=blackboard_dir)

        self.bridge.micromachine_status = blocking_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status",
            original,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            response_future = pool.submit(self.get_sse)
            self.assertTrue(status_entered.wait(1))
            for index in range(3):
                self.server._http.publish_event(
                    "command_received",
                    {
                        "command_text": f"rollover-command-{index}",
                        "status": "received",
                    },
                    operation_id=f"rollover-command-{index}",
                    generation=1,
                )
            self.assertGreater(
                self.server._http.event_journal.oldest_seq,
                baseline["event_seq"] + 1,
            )
            status_release.set()
            stream = response_future.result(timeout=3)

        snapshots = [
            event
            for event in self.parse_sse_events(stream)
            if event["event"] == "snapshot"
        ]
        self.assertGreaterEqual(len(snapshots), 2)
        self.assertEqual(
            self.server._http.event_journal.latest_seq,
            snapshots[-1]["data"]["event_seq"],
        )

    def test_slow_event_source_read_does_not_hold_publication_lock(self):
        original = self.bridge.state_snapshot
        state_entered = threading.Event()
        state_release = threading.Event()

        def blocking_state():
            state_entered.set()
            if not state_release.wait(2):
                raise TimeoutError("test did not release state snapshot")
            return original()

        self.bridge.state_snapshot = blocking_state
        self.addCleanup(
            setattr,
            self.bridge,
            "state_snapshot",
            original,
        )
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            refresh_future = pool.submit(
                handler._refresh_event_sources,
                "/tmp/slow-source",
            )
            self.assertTrue(state_entered.wait(1))
            publish_future = pool.submit(
                self.server._http.publish_event,
                "command_received",
                {"command_text": "latency-critical-command"},
            )
            published = publish_future.result(timeout=1)
            self.assertGreater(published["event_seq"], 0)
            state_release.set()
            refresh_future.result(timeout=3)

    def test_concurrent_source_refresh_cannot_publish_older_snapshot_last(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        scope_id = "scope-refresh-high-water"
        older = semantic_operation_payload(
            generation=1,
            frame=100,
            session_epoch=1700000000000,
        )
        newer = semantic_operation_payload(
            generation=2,
            frame=200,
            session_epoch=1700000000000,
        )
        for status in (older, newer):
            status["blackboard_scope_id"] = scope_id
            status["operation_events"] = []
            status["operation_registry_authoritative"] = True

        first_entered = threading.Event()
        release_first = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def raced_status(_blackboard_dir):
            nonlocal call_count
            with call_lock:
                call_count += 1
                call_number = call_count
            if call_number == 1:
                first_entered.set()
                if not release_first.wait(2):
                    raise TimeoutError("test did not release older refresh")
                return deepcopy(older)
            return deepcopy(newer)

        handler._micromachine_status_payload = raced_status

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            older_future = pool.submit(
                handler._refresh_event_sources,
                "/tmp/source-refresh-high-water",
            )
            self.assertTrue(first_entered.wait(1))
            newer_future = pool.submit(
                handler._refresh_event_sources,
                "/tmp/source-refresh-high-water",
            )
            newer_future.result(timeout=3)
            release_first.set()
            older_future.result(timeout=3)

        published = [
            event["payload"]
            for event in self.server._http.event_journal.events_after(0)
            if event["event_type"] == "micromachine_status"
            and event["payload"].get("blackboard_scope_id") == scope_id
        ]
        self.assertEqual(1, len(published))
        self.assertEqual(
            2,
            published[0]["battlefield_overview"]["identity"][
                "generation"
            ],
        )
        self.assertEqual(
            ("1700000000000", 2, 200),
            self.server._http._observed_payload_identities[
                f"micromachine:{scope_id}"
            ],
        )

    def test_operation_event_scope_cursor_and_caches_are_lru_bounded(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        http_server = self.server._http
        retention = http_server._OPERATION_EVENT_SCOPE_RETENTION

        for index in range(retention + 3):
            scope_id = f"operation-scope-{index}"
            http_server._observed_payload_hashes[
                f"micromachine:{scope_id}"
            ] = f"digest-{index}"
            source_key = f"micromachine_status:{scope_id}"
            http_server._observed_payload_hashes[
                f"source:{source_key}"
            ] = f"source-digest-{index}"
            http_server._failed_event_sources.add(source_key)
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": scope_id,
                    "operation_events": [
                        {
                            "timeline_seq": index + 1,
                            "operation_id": f"operation-{index}",
                            "generation": 1,
                            "kind": "assigned",
                        }
                    ],
                },
                blackboard_dir=f"/tmp/{scope_id}",
                publish=True,
            )

        self.assertLessEqual(
            len(http_server._observed_operation_event_seq),
            retention,
        )
        self.assertLessEqual(
            len(http_server._observed_operation_scope_order),
            retention,
        )
        evicted_scope = "operation-scope-0"
        self.assertNotIn(
            evicted_scope,
            http_server._observed_operation_event_seq,
        )
        self.assertNotIn(
            f"micromachine:{evicted_scope}",
            http_server._observed_payload_hashes,
        )
        self.assertNotIn(
            f"source:micromachine_status:{evicted_scope}",
            http_server._observed_payload_hashes,
        )
        self.assertNotIn(
            f"micromachine_status:{evicted_scope}",
            http_server._failed_event_sources,
        )

    def test_sse_snapshot_survives_micromachine_source_failure(self):
        original = self.bridge.micromachine_status

        def unavailable_status(*, blackboard_dir=""):
            del blackboard_dir
            raise OSError("blackboard source unavailable")

        self.bridge.micromachine_status = unavailable_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status",
            original,
        )

        stream = self.get_sse()
        events = self.parse_sse_events(stream)

        self.assertIn(": heartbeat", stream)
        snapshot = events[0]["data"]["payload"]
        self.assertTrue(snapshot["state"]["available"])
        self.assertEqual(
            snapshot["micromachine_status"]["status"],
            "source_error",
        )
        self.assertIn(
            "blackboard source unavailable",
            snapshot["micromachine_status"]["error"],
        )

    def test_sse_last_event_id_replays_only_newer_events(self):
        first = self.server._http.publish_event(
            "command_received",
            {"status": "received", "command_text": "first"},
            update_id="voi-test-first",
            operation_id="first",
            generation=1,
        )
        second = self.server._http.publish_event(
            "command_received",
            {"status": "received", "command_text": "second"},
            update_id="voi-test-second",
            operation_id="second",
            generation=2,
        )

        stream = self.get_sse(
            "/api/events?once=1",
            headers={"Last-Event-ID": str(first["event_seq"])},
        )
        events = self.parse_sse_events(stream)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "command_received")
        self.assertEqual(
            events[0]["data"]["event_seq"],
            second["event_seq"],
        )
        self.assertEqual(
            events[0]["data"]["payload"]["command_text"],
            "second",
        )

    def test_sse_truncated_cursor_falls_back_to_snapshot(self):
        journal = web_gui._WebEventJournal(retention=2)
        self.server._http.event_journal = journal
        for index in range(4):
            journal.publish(
                "command_received",
                {"status": "received", "command_text": f"command-{index}"},
            )

        stream = self.get_sse("/api/events?after=1&once=1")
        events = self.parse_sse_events(stream)

        self.assertEqual(events[0]["event"], "snapshot")
        self.assertEqual(
            events[0]["data"]["event_seq"],
            journal.latest_seq,
        )

    def test_sse_future_cursor_falls_back_to_authoritative_snapshot(self):
        event = self.server._http.publish_event(
            "state",
            {"available": True, "marker": "before-restart"},
        )

        stream = self.get_sse("/api/events?after=99999&once=1")
        events = self.parse_sse_events(stream)

        self.assertEqual(events[0]["event"], "snapshot")
        self.assertEqual(
            events[0]["data"]["event_seq"],
            event["event_seq"],
        )

    def test_sse_replay_filters_other_blackboard_events_server_side(self):
        with tempfile.TemporaryDirectory() as directory:
            board_a = os.path.join(directory, "board-a")
            board_b = os.path.join(directory, "board-b")
            baseline = self.server._http.publish_event(
                "state",
                {"available": True},
            )
            event_a = self.server._http.publish_event(
                "command_received",
                {
                    "command_text": "board A secret order",
                    "blackboard_dir": board_a,
                },
                blackboard_dir=board_a,
            )
            self.server._http.publish_event(
                "command_received",
                {
                    "command_text": "board B private order",
                    "blackboard_dir": board_b,
                },
                blackboard_dir=board_b,
            )

            stream = self.get_sse(
                "/api/events"
                f"?after={baseline['event_seq']}"
                f"&blackboard_dir={quote(board_a)}"
                "&once=1"
            )
            events = self.parse_sse_events(stream)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "command_received")
        self.assertEqual(
            events[0]["data"]["event_seq"],
            event_a["event_seq"],
        )
        self.assertEqual(
            events[0]["data"]["blackboard_scope_id"],
            web_gui._micromachine_blackboard_scope_id(board_a),
        )
        self.assertIn("board A secret order", stream)
        self.assertNotIn("board B private order", stream)

    def test_sse_source_error_can_repeat_after_recovery(self):
        http_server = self.server._http
        http_server.publish_source_error(
            "micromachine_status:test",
            "micromachine_status",
            {"error": "runtime unavailable"},
        )
        first_error_seq = http_server.event_journal.latest_seq
        http_server.publish_source_error(
            "micromachine_status:test",
            "micromachine_status",
            {"error": "runtime unavailable"},
        )
        self.assertEqual(
            http_server.event_journal.latest_seq,
            first_error_seq,
        )

        http_server.publish_source_recovered(
            "micromachine_status:test",
            "micromachine_status",
        )
        recovery_seq = http_server.event_journal.latest_seq
        self.assertGreater(recovery_seq, first_error_seq)
        http_server.publish_source_error(
            "micromachine_status:test",
            "micromachine_status",
            {"error": "runtime unavailable"},
        )

        events = http_server.event_journal.events_after(first_error_seq)
        self.assertEqual(
            [event["event_type"] for event in events],
            ["source_recovered", "source_error"],
        )

    def test_server_stop_terminates_an_active_sse_handler(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.port,
            timeout=5,
        )
        connection.request("GET", "/api/events")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        for _ in range(5):
            response.readline()

        http_server = self.server._http
        started = time.monotonic()
        self.server.stop(timeout=2)
        response.read()
        elapsed = time.monotonic() - started
        connection.close()

        self.assertTrue(http_server.shutdown_event.is_set())
        self.assertFalse(self.server.is_running)
        self.assertTrue(response.isclosed())
        self.assertLess(elapsed, 2.0)

    def test_sse_endpoint_uses_existing_token_authentication(self):
        server = WebGuiServer(
            bridge=self.bridge,
            port=0,
            auth_token="event-secret",
        )
        server.start()
        self.addCleanup(server.stop)

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.port,
            timeout=5,
        )
        try:
            connection.request("GET", "/api/events?once=1")
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, 403)
        self.assertIn("application/json", response.getheader("Content-Type", ""))
        self.assertIn("인증 토큰", payload.decode("utf-8"))

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.port,
            timeout=5,
        )
        try:
            connection.request(
                "GET",
                "/api/events?once=1",
                headers={WEB_GUI_TOKEN_HEADER: "event-secret"},
            )
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn(
            "text/event-stream",
            response.getheader("Content-Type", ""),
        )
        self.assertIn(b"event: snapshot", payload)

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

    def test_micromachine_modulation_async_returns_before_slow_llm_finishes(self):
        started = threading.Event()
        release = threading.Event()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=BlockingPolicyModulationLLMControl(
                started=started,
                release=release,
            ),
        )
        bridge.start()
        self.addCleanup(bridge.stop)
        server = WebGuiServer(bridge=bridge, port=0)
        server.start()
        self.addCleanup(server.stop)

        with tempfile.TemporaryDirectory() as directory:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.port, timeout=1
            )
            try:
                body = json.dumps(
                    {
                        "text": "탱크로 수비해",
                        "blackboard_dir": directory,
                        "current_frame": 21,
                        "update_id": "async-slow-llm",
                        "async_publish": True,
                    }
                ).encode("utf-8")
                before = time.monotonic()
                connection.request(
                    "POST",
                    "/api/micromachine/modulate",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                elapsed = time.monotonic() - before
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                connection.close()

            self.assertLess(elapsed, 0.5)
            self.assertEqual(
                HTTPStatus.ACCEPTED,
                HTTPStatus(response.status),
                payload,
            )
            self.assertTrue(payload["accepted"], payload)
            self.assertTrue(payload["async_publish"], payload)
            self.assertEqual("queued", payload["status"])
            self.assertEqual("pending_compile", payload["consumption_status"])
            self.assertEqual("async-slow-llm", payload["update_id"])
            self.assertTrue(started.wait(1), "background LLM call did not start")

            release.set()
            deadline = time.monotonic() + 3
            document = {}
            while time.monotonic() < deadline:
                document = self.get_json(
                    "/api/micromachine/status?blackboard_dir=" + directory
                )
                compile_result = document.get("compile_result") or {}
                if compile_result.get("update_id") == "async-slow-llm":
                    break
                time.sleep(0.05)

            self.assertEqual("async-slow-llm", document["compile_result"]["update_id"])
            self.assertEqual("compiled", document["compile_result"]["status"])
            self.assertEqual("async-slow-llm", document["update"]["update_id"])

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
            self.assertEqual(["TERRAN_MARINE", "TERRAN_SIEGETANK"], scope["unit_classes"])
            self.assertEqual("enemy_natural", scope["location_intent"])
            self.assertEqual(120, scope["duration_seconds"])
            self.assertEqual(300, document["compile_result"]["vector"]["ttl_seconds"])
            self.assertEqual(
                "until_completed",
                document["compile_result"]["vector"]["lifetime"]["mode"],
            )
            with open(f"{directory}/latest_modulation.kv", encoding="utf-8") as handle:
                kv = handle.read()
            self.assertIn("scope.army_group=main", kv)
            self.assertIn("scope.location_intent=enemy_natural", kv)
            self.assertIn("scope.unit_classes=TERRAN_MARINE,TERRAN_SIEGETANK", kv)

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
                ("siege_tank, workers", ["TERRAN_SCV", "TERRAN_SIEGETANK"]),
                ("siege tank worker", ["TERRAN_SCV", "TERRAN_SIEGETANK"]),
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
            self.assertEqual(
                ["workers", "combat", "lifetime"],
                intervention["manager_bias_domains"],
            )
            self.assertEqual("수비", intervention["goal"])

    def test_micromachine_status_exposes_authoritative_battlefield_overview(self):
        telemetry = battlefield_projection_telemetry()
        payload = web_gui._micromachine_status_payload(
            {
                "active_updates": [
                    {
                        "update_id": "representative-operation",
                        "manager_bias_domains": ["combat"],
                        "vector": {
                            "goal": "representative operation",
                            "operations": [
                                {
                                    "operation_id": "representative-operation",
                                    "generation": 1,
                                    "goal": "representative operation",
                                    "tactical_task": {
                                        "task_type": "pressure_with_main_army",
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
            telemetry=telemetry,
        )

        self.assertTrue(payload["battlefield_projection"]["ok"])
        overview = payload["battlefield_overview"]
        self.assertEqual(8, overview["eligible_combat_count"])
        self.assertEqual(4, overview["explicit_operation_owned_count"])
        self.assertEqual(2, overview["autonomous_owned_count"])
        self.assertEqual(2, overview["unassigned_count"])
        self.assertEqual(
            overview["identity"],
            payload["battlefield_projection_identity"],
        )
        self.assertEqual(
            "valid",
            payload["battlefield_projection_integrity"]["status"],
        )
        self.assertNotEqual(
            payload["update"]["update_id"],
            overview["identity"]["update_id"],
        )

    def test_real_filesystem_status_preserves_battlefield_overview(self):
        from starcraft_commander.micromachine_runtime import (
            MicroMachineFilesystemBlackboard,
        )

        runtime_instance_id = "a" * 32
        telemetry = battlefield_projection_telemetry()
        telemetry.update(
            {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {},
                "active_modulation_ids": [],
                "last_failure": None,
                "runtime_instance_id": runtime_instance_id,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            MicroMachineFilesystemBlackboard(directory).ingest_telemetry(
                telemetry
            )
            bridge = SessionLoopBridge(session=self.session)

            payload = bridge.micromachine_status_for_runtime(
                blackboard_dir=directory,
                runtime_instance_id=runtime_instance_id,
                telemetry_document=telemetry,
            )

        self.assertTrue(
            payload["battlefield_projection"]["ok"],
            payload["battlefield_projection"],
        )
        self.assertEqual(
            "battlefield-current",
            payload["battlefield_overview"]["identity"]["update_id"],
        )
        self.assertEqual(
            8,
            payload["battlefield_overview"]["eligible_combat_count"],
        )

    def test_real_filesystem_status_rejects_other_runtime_telemetry(self):
        from starcraft_commander.micromachine_runtime import (
            MicroMachineFilesystemBlackboard,
        )

        telemetry = battlefield_projection_telemetry()
        telemetry.update(
            {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {},
                "active_modulation_ids": [],
                "last_failure": None,
                "runtime_instance_id": "b" * 32,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            MicroMachineFilesystemBlackboard(directory).ingest_telemetry(
                telemetry
            )
            bridge = SessionLoopBridge(session=self.session)

            with self.assertRaisesRegex(ValueError, "does not match"):
                bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id="c" * 32,
                    telemetry_document=telemetry,
                )

    def test_micromachine_status_malformed_latest_projection_fails_closed(self):
        valid_archive = battlefield_projection_telemetry(
            update_id="archive-valid",
            frame=300,
        )
        malformed_latest = battlefield_projection_telemetry(
            update_id="latest-malformed",
            frame=320,
        )
        del malformed_latest["battlefield_overview"]["bases"]

        payload = web_gui._micromachine_status_payload(
            {"active_updates": []},
            telemetry=malformed_latest,
            telemetry_archive=(valid_archive,),
        )

        self.assertFalse(payload["battlefield_projection"]["ok"])
        self.assertIsNone(payload["battlefield_overview"])
        self.assertEqual(
            "blocked",
            payload["battlefield_projection_integrity"]["status"],
        )
        self.assertIn(
            "invalid_projection_sequence",
            {
                blocker["code"]
                for blocker in payload["battlefield_projection"]["blockers"]
            },
        )

    def test_micromachine_status_selects_latest_monotonic_archive_projection(self):
        payload = web_gui._micromachine_status_payload(
            {"active_updates": []},
            telemetry_archive=(
                battlefield_projection_telemetry(
                    update_id="archive-older",
                    frame=300,
                    generation=6,
                ),
                battlefield_projection_telemetry(
                    update_id="archive-current",
                    frame=320,
                    generation=7,
                ),
            ),
        )

        self.assertTrue(payload["battlefield_projection"]["ok"])
        self.assertEqual(
            "archive-current",
            payload["battlefield_overview"]["identity"]["update_id"],
        )
        self.assertEqual(
            320,
            payload["battlefield_projection_identity"]["game_frame"],
        )
        self.assertEqual(
            "archive",
            payload["battlefield_projection"]["source"],
        )
        self.assertEqual(
            1,
            payload["battlefield_projection"]["source_index"],
        )

    def test_micromachine_status_redacts_battlefield_unit_identity(self):
        telemetry = battlefield_projection_telemetry()
        telemetry["battlefield_overview"]["bases"][0]["base_readiness"][
            "reason"
        ] = "protected minimum actor_tag=987654"
        telemetry["battlefield_overview"]["unit_tag_ids"] = [991, 992]
        telemetry["battlefield_overview"]["private_config"] = {
            "api_key": "battlefield-private-secret",
            "nested": {"token": "battlefield-private-token"},
        }
        telemetry["battlefield_overview"]["bases"][0]["unknown_runtime_state"] = {
            "password": "battlefield-private-password",
        }

        payload = web_gui._micromachine_status_payload(
            {"active_updates": []},
            telemetry=telemetry,
        )
        serialized = json.dumps(payload, sort_keys=True)

        for forbidden_key in (
            "owner_tags",
            "unassigned_unit_tags",
            "transferable_unit_tags",
            "duplicate_owner_tags",
            "excluded_unit_tags",
        ):
            self.assertNotIn(forbidden_key, serialized)
        for raw_tag in ("101", "102", "103", "104", "201", "202", "301", "302"):
            self.assertNotIn(raw_tag, serialized)
        for forbidden_value in (
            "991",
            "992",
            "battlefield-private-secret",
            "battlefield-private-token",
            "battlefield-private-password",
        ):
            self.assertNotIn(forbidden_value, serialized)
        self.assertNotIn("unit_tag_ids", serialized)
        self.assertNotIn("private_config", serialized)
        self.assertNotIn("unknown_runtime_state", serialized)
        self.assertNotIn("actor_tag", serialized)
        self.assertNotIn("987654", serialized)
        self.assertIn(
            "protected minimum",
            payload["battlefield_overview"]["bases"][0]["base_readiness"][
                "reason"
            ],
        )
        self.assertEqual(
            4,
            payload["battlefield_overview"]["operation_ownership"][0][
                "operation_ownership"
            ]["owner_count"],
        )
        self.assertEqual(
            2,
            payload["battlefield_overview"]["transfer_availability"]["entries"][0][
                "transferable_count"
            ],
        )

    def test_micromachine_status_persists_projection_cursor_per_blackboard(self):
        current = {
            "telemetry": battlefield_projection_telemetry(
                update_id="frame-500",
                frame=500,
                generation=9,
            )
        }

        class Backend:
            def __init__(self, _root):
                pass

            def read_recent_telemetry_archive(self, **_kwargs):
                return ()

            def read_latest_update(self, **_kwargs):
                return None

        with tempfile.TemporaryDirectory() as directory:
            bridge = SessionLoopBridge(session=self.session)
            runtime_a = "a" * 32
            runtime_b = "b" * 32
            with mock.patch(
                "starcraft_commander.micromachine_runtime."
                "MicroMachineFilesystemBlackboard",
                Backend,
            ):
                first = bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id=runtime_a,
                    telemetry_document=attached_runtime_telemetry(
                        current["telemetry"],
                        runtime_a,
                    ),
                )
                self.assertTrue(first["battlefield_projection"]["ok"])

                current["telemetry"] = battlefield_projection_telemetry(
                    update_id="frame-400",
                    frame=400,
                    generation=10,
                )
                stale = bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id=runtime_a,
                    telemetry_document=attached_runtime_telemetry(
                        current["telemetry"],
                        runtime_a,
                    ),
                )
                self.assertFalse(stale["battlefield_projection"]["ok"])
                self.assertIn(
                    "stale_game_frame",
                    {
                        blocker["code"]
                        for blocker in stale["battlefield_projection"][
                            "blockers"
                        ]
                    },
                )

                current["telemetry"] = battlefield_projection_telemetry(
                    update_id="new-session",
                    frame=320,
                    generation=1,
                    session_epoch=1700000000100,
                )
                reset = bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id=runtime_a,
                    telemetry_document=attached_runtime_telemetry(
                        current["telemetry"],
                        runtime_a,
                    ),
                )
                self.assertTrue(
                    reset["battlefield_projection"]["ok"],
                    reset["battlefield_projection"],
                )
                self.assertEqual(
                    1700000000100,
                    reset["battlefield_projection_identity"][
                        "session_epoch"
                    ],
                )

                current["telemetry"] = battlefield_projection_telemetry(
                    update_id="replacement-runtime",
                    frame=320,
                    generation=1,
                    session_epoch=1600000000000,
                )
                replacement = bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id=runtime_b,
                    telemetry_document=attached_runtime_telemetry(
                        current["telemetry"],
                        runtime_b,
                    ),
                )
                self.assertTrue(
                    replacement["battlefield_projection"]["ok"],
                    replacement["battlefield_projection"],
                )
                self.assertEqual(
                    1600000000000,
                    replacement["battlefield_projection_identity"][
                        "session_epoch"
                    ],
                )

    def test_micromachine_status_rejects_cross_poll_identity_mutation(self):
        current = {
            "telemetry": battlefield_projection_telemetry(
                update_id="stable-identity",
                frame=500,
                generation=9,
            )
        }

        class Backend:
            def __init__(self, _root):
                pass

            def read_recent_telemetry_archive(self, **_kwargs):
                return ()

            def read_latest_update(self, **_kwargs):
                return None

        with tempfile.TemporaryDirectory() as directory:
            bridge = SessionLoopBridge(session=self.session)
            runtime_id = "a" * 32
            with mock.patch(
                "starcraft_commander.micromachine_runtime."
                "MicroMachineFilesystemBlackboard",
                Backend,
            ):
                first = bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id=runtime_id,
                    telemetry_document=attached_runtime_telemetry(
                        current["telemetry"],
                        runtime_id,
                    ),
                )
                self.assertTrue(first["battlefield_projection"]["ok"])

                mutated = deepcopy(current["telemetry"])
                mutated["battlefield_overview"]["bases"][0][
                    "base_readiness"
                ]["reason"] = "changed_without_identity_advance"
                current["telemetry"] = mutated
                collision = bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id=runtime_id,
                    telemetry_document=attached_runtime_telemetry(
                        current["telemetry"],
                        runtime_id,
                    ),
                )

        self.assertFalse(collision["battlefield_projection"]["ok"])
        self.assertIn(
            "identity_collision",
            {
                blocker["code"]
                for blocker in collision["battlefield_projection"]["blockers"]
            },
        )

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
                "runtime_instance_id": "f" * 32,
            }
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            self.attach_fake_micromachine_runtime(directory)

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

    def test_micromachine_status_rejects_detached_stale_telemetry_false_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "지금 압박해",
                    "blackboard_dir": directory,
                    "current_frame": 1,
                    "update_id": "detached-false-pass",
                    "provider_output": {
                        "goal": "pressure",
                        "combat": {"aggression": 0.5},
                    },
                }
            )
            with open(
                os.path.join(directory, "latest_telemetry.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": 99,
                        "bot_name": "MicroMachine",
                        "race": "Terran",
                        "managers": {
                            "CombatCommander": {
                                "active": True,
                                "policy_active": True,
                                "update_id": "detached-false-pass",
                                "consumed_axes": "combat.aggression",
                            },
                        },
                        "active_modulation_ids": ["detached-false-pass"],
                        "last_failure": None,
                    },
                    handle,
                )

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            self.assertEqual("detached_telemetry", document["consumption_status"])
            self.assertFalse(document["consumed"])
            self.assertFalse(document["intervention"]["applied"])
            self.assertFalse(document["intervention"]["policy_active"])
            self.assertTrue(document["telemetry_stale_or_detached"])
            self.assertFalse(document["operation_registry_authoritative"])

    def test_detached_projection_cannot_poison_attached_runtime_cursor(self):
        current = {
            "telemetry": battlefield_projection_telemetry(
                update_id="detached-old",
                frame=900,
                generation=9,
                session_epoch=1700000000200,
            )
        }

        class Telemetry:
            def __init__(self, document):
                self.document = document
                self.frame = document["frame"]

            def to_dict(self):
                return dict(self.document)

        class Backend:
            def __init__(self, _root):
                pass

            def read_latest_telemetry(self):
                return Telemetry(current["telemetry"])

            def read_recent_telemetry_archive(self, **_kwargs):
                return ()

            def read_latest_update(self, **_kwargs):
                return None

            def dashboard_snapshot(self, **_kwargs):
                return SimpleNamespace(
                    to_dict=lambda: {"active_updates": []}
                )

        class Launcher:
            attached = False
            runtime_instance_id = "d" * 32

            def snapshot(self, blackboard_dir=""):
                return {
                    "status": "connected" if self.attached else "idle",
                    "runtime_instance_id": (
                        self.runtime_instance_id if self.attached else ""
                    ),
                    "runtime_attached": self.attached,
                    "telemetry_present": True,
                    "telemetry_current_for_process": self.attached,
                    "telemetry_stale_or_detached": not self.attached,
                    "telemetry_frame": current["telemetry"]["frame"],
                    "blackboard_dir": blackboard_dir,
                }

            def validated_snapshot(self, blackboard_dir=""):
                telemetry_document = (
                    attached_runtime_telemetry(
                        current["telemetry"],
                        self.runtime_instance_id,
                    )
                    if self.attached
                    else None
                )
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata=self.snapshot(blackboard_dir),
                    telemetry_document=telemetry_document,
                )

        with tempfile.TemporaryDirectory() as directory:
            launcher = Launcher()
            self.server._http.micromachine_launcher = launcher
            with mock.patch(
                "starcraft_commander.micromachine_runtime."
                "MicroMachineFilesystemBlackboard",
                Backend,
            ):
                detached = self.get_json(
                    "/api/micromachine/status?blackboard_dir=" + directory
                )
                self.assertIsNone(detached["battlefield_overview"])
                self.assertTrue(detached["telemetry_stale_or_detached"])

                current["telemetry"] = battlefield_projection_telemetry(
                    update_id="attached-current",
                    frame=320,
                    generation=1,
                    session_epoch=1700000000100,
                )
                launcher.attached = True
                attached = self.get_json(
                    "/api/micromachine/status?blackboard_dir=" + directory
                )

            self.assertTrue(
                attached["battlefield_projection"]["ok"],
                attached["battlefield_projection"],
            )
            self.assertEqual(
                1700000000100,
                attached["battlefield_projection_identity"]["session_epoch"],
            )

    def test_attached_status_consumes_validated_telemetry_snapshot_after_file_replace(
        self,
    ):
        runtime_instance_id = "e" * 32
        original = attached_runtime_telemetry(
            battlefield_projection_telemetry(
                update_id="validated-original",
                frame=320,
                generation=1,
            ),
            runtime_instance_id,
        )
        replacement = attached_runtime_telemetry(
            battlefield_projection_telemetry(
                update_id="same-runtime-replacement",
                frame=640,
                generation=2,
            ),
            runtime_instance_id,
        )

        class FakeRunningProcess:
            pid = 4242

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            telemetry_path = os.path.join(directory, "latest_telemetry.json")
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            write_ns = time.time_ns()
            os.utime(telemetry_path, ns=(write_ns, write_ns))

            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
            launcher._blackboard_dir = directory  # noqa: SLF001
            launcher._process = FakeRunningProcess()  # noqa: SLF001
            launcher._status = "running"  # noqa: SLF001
            launcher._runtime_instance_id = runtime_instance_id  # noqa: SLF001
            launcher._launch_started_at_ns = write_ns - 1_000_000  # noqa: SLF001
            self.server._http.micromachine_launcher = launcher

            original_status = self.bridge.micromachine_status_for_runtime

            def replace_after_validation(**kwargs):
                with open(telemetry_path, "w", encoding="utf-8") as handle:
                    json.dump(replacement, handle)
                replacement_ns = time.time_ns()
                os.utime(
                    telemetry_path,
                    ns=(replacement_ns, replacement_ns),
                )
                return original_status(**kwargs)

            with mock.patch.object(
                self.bridge,
                "micromachine_status_for_runtime",
                side_effect=replace_after_validation,
            ):
                document = self.get_json(
                    "/api/micromachine/status?blackboard_dir=" + directory
                )

            with open(telemetry_path, encoding="utf-8") as handle:
                file_document = json.load(handle)

        self.assertEqual(
            "same-runtime-replacement",
            file_document["battlefield_overview"]["identity"]["update_id"],
        )
        self.assertEqual(
            "validated-original",
            document["battlefield_projection_identity"]["update_id"],
        )
        self.assertEqual(320, document["telemetry_frame"])

    def test_attached_status_without_validated_bridge_consumer_fails_closed(
        self,
    ):
        runtime_instance_id = "f" * 32
        telemetry = attached_runtime_telemetry(
            battlefield_projection_telemetry(
                update_id="validated-source",
                frame=320,
            ),
            runtime_instance_id,
        )

        class LegacyBridge:
            status_calls = 0

            def micromachine_status(self, *, blackboard_dir=""):
                self.status_calls += 1
                return web_gui._micromachine_status_payload(
                    {"active_updates": []},
                    telemetry=attached_runtime_telemetry(
                        battlefield_projection_telemetry(
                            update_id="legacy-reread",
                            frame=640,
                        ),
                        runtime_instance_id,
                    ),
                    blackboard_dir=blackboard_dir,
                )

        class Launcher:
            def validated_snapshot(self, blackboard_dir=""):
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata={
                        "status": "connected",
                        "runtime_instance_id": runtime_instance_id,
                        "runtime_attached": True,
                        "telemetry_present": True,
                        "telemetry_current_for_process": True,
                        "telemetry_stale_or_detached": False,
                        "telemetry_frame": 320,
                        "blackboard_dir": blackboard_dir,
                        "error": "",
                    },
                    telemetry_document=telemetry,
                )

        legacy_bridge = LegacyBridge()
        self.server._http.micromachine_launcher = Launcher()
        with mock.patch.object(
            self.server._http,
            "bridge",
            legacy_bridge,
        ):
            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=/tmp/legacy-bridge"
            )

        self.assertEqual(0, legacy_bridge.status_calls)
        self.assertEqual("source_error", document["status"])
        self.assertIsNone(document["battlefield_overview"])
        self.assertFalse(document["telemetry_current_for_process"])
        self.assertTrue(document["telemetry_stale_or_detached"])
        self.assertIn("validated telemetry snapshot", document["error"])

    def test_attached_status_with_missing_validated_document_fails_closed(
        self,
    ):
        runtime_instance_id = "f" * 32

        class LegacyBridge:
            status_calls = 0

            def micromachine_status(self, *, blackboard_dir=""):
                self.status_calls += 1
                return web_gui._micromachine_status_payload(
                    {"active_updates": []},
                    telemetry=attached_runtime_telemetry(
                        battlefield_projection_telemetry(
                            update_id="missing-snapshot-reread",
                            frame=640,
                        ),
                        runtime_instance_id,
                    ),
                    blackboard_dir=blackboard_dir,
                )

        class Launcher:
            def validated_snapshot(self, blackboard_dir=""):
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata={
                        "status": "connected",
                        "runtime_instance_id": runtime_instance_id,
                        "runtime_attached": True,
                        "telemetry_present": True,
                        "telemetry_current_for_process": True,
                        "telemetry_stale_or_detached": False,
                        "telemetry_frame": 320,
                        "blackboard_dir": blackboard_dir,
                        "error": "",
                    },
                    telemetry_document=None,
                )

        legacy_bridge = LegacyBridge()
        self.server._http.micromachine_launcher = Launcher()
        with mock.patch.object(
            self.server._http,
            "bridge",
            legacy_bridge,
        ):
            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=/tmp/incomplete-snapshot"
            )

        self.assertEqual(0, legacy_bridge.status_calls)
        self.assertEqual("source_error", document["status"])
        self.assertIsNone(document["battlefield_overview"])
        self.assertFalse(document["telemetry_current_for_process"])
        self.assertTrue(document["telemetry_stale_or_detached"])
        self.assertIn("validated telemetry snapshot", document["error"])

    def test_attached_status_rejects_unbound_archive_family_evidence(self):
        from starcraft_commander.micromachine_bridge import (
            MicroMachineTelemetry,
        )

        update_id = "runtime-bound-archive"
        operation_id = "marine-recon"
        runtime_instance_id = "a" * 32
        dashboard = {
            "active_updates": [
                {
                    "update_id": update_id,
                    "issued_at_frame": 200,
                    "expires_at_frame": 2_000,
                    "manager_bias_domains": ["scouting", "squad"],
                    "vector": {
                        "goal": "마린 한 기로 정찰",
                        "operations": [
                            {
                                "operation_id": operation_id,
                                "generation": 1,
                                "goal": "마린 한 기로 정찰",
                                "tactical_task": {
                                    "task_type": "scout_with_units",
                                    "duration_seconds": 120,
                                },
                            }
                        ],
                    },
                }
            ],
            "telemetry": {"frame": 300},
        }
        delivered = {
            "update_id": update_id,
            "operation_id": operation_id,
            "generation": 1,
            "family": "marine",
            "unit_type": "TERRAN_MARINE",
            "role": "scout",
            "action": "move",
            "required_effect": "movement_or_engagement",
            "attempt_generation": 2,
            "attempted_count": 1,
            "attempted_frame": 210,
            "submitted_count": 1,
            "submitted_frame": 220,
            "effect_kind": "movement",
            "effect_count": 1,
            "effect_frame": 230,
            "blocker_manager": "",
            "blocker": "",
        }
        unbound_archive = MicroMachineTelemetry.from_mapping(
            {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 240,
                "managers": {
                    "OperationDirector": {
                        "policy_update_id": update_id,
                        "operations": [
                            {
                                "operation_id": operation_id,
                                "generation": 1,
                                "status": "MOVING",
                                "received_frame": 205,
                            }
                        ],
                        "pending_family_effects": [delivered],
                    }
                },
                "active_modulation_ids": [update_id],
                "runtime_instance_id": "",
            }
        )
        latest = attached_runtime_telemetry(
            {
                "frame": 300,
                "active_modulation_ids": [update_id],
                "managers": {
                    "OperationDirector": {
                        "policy_update_id": update_id,
                        "operations": [
                            {
                                "operation_id": operation_id,
                                "generation": 1,
                                "status": "MOVING",
                                "received_frame": 205,
                                "assigned_frame": 215,
                                "assigned_count": 1,
                                "submitted_frame": 220,
                            }
                        ],
                        "pending_family_effects": [],
                    }
                },
            },
            runtime_instance_id,
        )

        class Backend:
            def __init__(self, _root):
                pass

            def read_recent_telemetry_archive(self, **_kwargs):
                return (unbound_archive,)

            def read_latest_update(self, **_kwargs):
                return None

        with tempfile.TemporaryDirectory() as directory:
            bridge = SessionLoopBridge(session=self.session)
            with (
                mock.patch(
                    "starcraft_commander.micromachine_runtime."
                    "MicroMachineFilesystemBlackboard",
                    Backend,
                ),
                mock.patch(
                    "starcraft_commander.policy_observability."
                    "build_policy_modulation_dashboard_snapshot",
                    return_value=SimpleNamespace(
                        to_dict=lambda: dashboard,
                    ),
                ),
            ):
                payload = bridge.micromachine_status_for_runtime(
                    blackboard_dir=directory,
                    runtime_instance_id=runtime_instance_id,
                    telemetry_document=latest,
                )

        self.assertEqual(1, len(payload["operations"]))
        self.assertEqual([], payload["operations"][0]["family_evidence"])

    def test_micromachine_runtime_gate_redacts_runtime_identity_text(self):
        cases = (
            (
                "attached",
                {
                    "status": "running",
                    "runtime_attached": True,
                    "telemetry_current_for_process": True,
                    "last_line": "actor_tag=7001 action=attack",
                    "error": "target_unit_tags=[8001, 8002]",
                },
            ),
            (
                "detached",
                {
                    "status": "running",
                    "runtime_attached": False,
                    "telemetry_current_for_process": False,
                    "telemetry_present": True,
                    "last_line": "actor_tag=7001 action=attack",
                    "error": "target_unit_tags=[8001, 8002]",
                },
            ),
        )

        for label, runtime_snapshot in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                document = web_gui._micromachine_status_with_runtime_gate(
                    {"status": "idle"},
                    runtime_snapshot=runtime_snapshot,
                    blackboard_dir=directory,
                )
                serialized = json.dumps(document, sort_keys=True)

                self.assertNotIn("actor_tag", serialized)
                self.assertNotIn("target_unit_tags", serialized)
                self.assertNotIn("7001", serialized)
                self.assertNotIn("8001", serialized)
                self.assertNotIn("8002", serialized)
                self.assertIn("action=attack", document["last_line"])

    def test_micromachine_status_scopes_latest_compile_result_to_active_update(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "지금 압박해",
                    "blackboard_dir": directory,
                    "current_frame": 30,
                    "update_id": "active-a",
                    "provider_output": {
                        "goal": "pressure",
                        "combat": {"aggression": 0.45},
                    },
                }
            )
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 35,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "CombatCommander": {
                        "active": True,
                        "policy_active": True,
                        "update_id": "active-a",
                        "consumed_axes": "combat.aggression",
                    },
                },
                "active_modulation_ids": ["active-a"],
                "last_failure": None,
            }
            with open(f"{directory}/latest_telemetry.json", "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            with open(
                os.path.join(directory, "latest_modulation_compile_result.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "command_text": "bad latest request",
                        "status": "publish_failed",
                        "written_at_unix": time.time(),
                        "update_id": "failed-b",
                        "compile_result": {
                            "status": "refused",
                            "update_id": "failed-b",
                            "refusal_reason": "provider auth failed",
                        },
                    },
                    handle,
                )
            self.attach_fake_micromachine_runtime(directory)

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            self.assertEqual("active-a", document["update"]["update_id"])
            self.assertEqual("failed-b", document["compile_result"]["update_id"])
            self.assertEqual("consumed", document["consumption_status"])
            self.assertEqual("failed-b", document["latest_request"]["update_id"])
            self.assertEqual("refused", document["latest_request"]["status"])
            self.assertEqual(
                "not_published",
                document["latest_request"]["consumption_status"],
            )
            self.assertFalse(document["latest_request"]["is_active_update"])
            self.assertEqual("", document["intervention"]["refusal_reason"])
            self.assertNotEqual("refused", document["intervention"]["tactical_posture"])
            self.assertFalse(
                document["intervention"]["tactical_evidence"]["refusal_reasons"]
            )

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
                        "lifetime_mode": "until_completed",
                        "completion_state": "completed",
                        "completion_conditions": "order_issued,target_reached",
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
                        "self_position_command_block_count": 0,
                        "root_cause_status": "none",
                        "root_cause_reason": "none",
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
            self.attach_fake_micromachine_runtime(directory)

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
            self.assertEqual(
                0,
                intervention["manager_snapshot"]["WorkerManager"][
                    "self_position_command_block_count"
                ],
            )
            self.assertEqual(
                "none",
                intervention["manager_snapshot"]["WorkerManager"]["root_cause_status"],
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
            self.assertEqual(
                "until_completed",
                intervention["lifetime"]["telemetry"]["lifetime_mode"],
            )
            self.assertEqual(
                "completed",
                intervention["lifetime"]["telemetry"]["completion_state"],
            )

    def test_micromachine_status_exposes_command_execution_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            self.post_micromachine_modulation(
                {
                    "text": "4 마린으로 공격해",
                    "blackboard_dir": directory,
                    "current_frame": 100,
                    "update_id": "web-execution-1",
                    "provider_output": {
                        "goal": "four marine attack",
                        "combat": {"aggression": 0.75},
                        "composition_requirements": [
                            {
                                "unit_type": "TERRAN_MARINE",
                                "count": 4,
                                "role": "frontline",
                            }
                        ],
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                        },
                    },
                }
            )
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 110,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {
                    "GameCommander": {
                        "policy_active": True,
                        "update_id": "web-execution-1",
                    },
                    "CombatCommander": {
                        "active": True,
                        "policy_active": True,
                        "policy_update_id": "web-execution-1",
                        "main_attack_actual_command_issued_count": 1,
                        "main_attack_last_action_frame": 108,
                        "main_attack_last_issued_action": (
                            "MoveToGoalOrder|squad=MainAttack|type=2|x=33.5|y=138.5"
                        ),
                        "main_attack_order_status": "Attack",
                        "main_attack_max_home_distance": 18.0,
                        "consumed_axes": "combat.aggression",
                    },
                    "CompositionTask": {
                        "active": True,
                        "task_update_id": "web-execution-1",
                        "assigned_frame": 108,
                        "assigned_count": 4,
                    },
                },
                "active_modulation_ids": ["web-execution-1"],
                "last_failure": None,
            }
            with open(f"{directory}/latest_telemetry.json", "w", encoding="utf-8") as handle:
                json.dump(telemetry, handle)
            self.attach_fake_micromachine_runtime(directory)

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            execution = document["intervention"]["command_execution"]
            self.assertEqual("effect_observed", execution["state"], execution)
            self.assertFalse(execution["ok"], execution)
            self.assertFalse(execution["failed"], execution)
            self.assertEqual("web-execution-1", execution["command_id"])
            stages = {stage["name"]: stage for stage in execution["stages"]}
            self.assertTrue(stages["action_issued"]["ok"])
            self.assertFalse(stages["effect_observed"]["ok"])
            scenarios = {scenario["name"]: scenario for scenario in execution["scenarios"]}
            self.assertEqual("passed", scenarios["four_marine_attack"]["status"])
            self.assertEqual("Telemetry", execution["blocker_manager"])
            self.assertIn("No observed", execution["blocker_reason"])

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
            self.attach_fake_micromachine_runtime(directory)

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
            self.attach_fake_micromachine_runtime(directory)

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
            self.attach_fake_micromachine_runtime(directory)

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
            self.attach_fake_micromachine_runtime(directory)

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

    def test_micromachine_status_ignores_old_compile_refusal_as_current_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(
                os.path.join(directory, "latest_modulation_compile_result.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "command_text": "old failure",
                        "status": "refused",
                        "written_at_unix": time.time() - 3600,
                        "compile_result": {
                            "status": "refused",
                            "refusal_reason": "stale failure should not look current",
                        },
                    },
                    handle,
                )

            document = self.get_json(
                "/api/micromachine/status?blackboard_dir=" + directory
            )

            self.assertEqual("idle", document["status"])
            self.assertIsNone(document["compile_result"])
            self.assertEqual("", document["intervention"]["refusal_reason"])
            self.assertFalse(
                document["intervention"]["tactical_evidence"]["refusal_reasons"]
            )

    def test_micromachine_modulation_without_llm_fails_closed(self):
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
                        "update_id": "no-llm-fail-closed",
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

            self.assertEqual(HTTPStatus.OK, HTTPStatus(response.status))
            self.assertFalse(payload["accepted"], payload)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual("llm", payload["provider_source"])
            self.assertEqual("refused", payload["compile_result"]["status"])
            self.assertEqual(
                "provider_unavailable",
                payload["compile_result"]["failure_kind"],
            )
            self.assertIsNone(payload["update"])
            self.assertNotEqual(
                "smoke_keyword",
                payload["provider_source"],
            )
            self.assertEqual(directory, payload["blackboard_dir"])

    def test_micromachine_modulation_allows_keyword_only_with_explicit_smoke_flag(self):
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
                        "update_id": "keyword-smoke",
                        "allow_smoke_keyword_provider": True,
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
            self.assertEqual("smoke_keyword", payload["provider_source"])
            self.assertEqual("keyword-smoke", payload["update"]["update_id"])
            self.assertEqual(directory, payload["blackboard_dir"])

    def test_micromachine_modulation_missing_tool_does_not_use_rule_fallback(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=NoToolPolicyModulationLLMControl(),
        )
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
                        "text": "마린 러쉬 진행해",
                        "blackboard_dir": directory,
                        "current_frame": 21,
                        "update_id": "web-rush-fallback",
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

            self.assertEqual(HTTPStatus.OK, HTTPStatus(response.status))
            self.assertFalse(payload["accepted"], payload)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual("llm", payload["provider_source"])
            self.assertEqual("refused", payload["compile_result"]["status"])
            self.assertIn(
                "no forced-tool",
                payload["compile_result"]["refusal_reason"],
            )
            self.assertIsNone(payload["update"])
            self.assertEqual("clarification", payload["command_queue"]["category"])
            self.assertEqual("refused", payload["command_queue"]["action"])
            self.assertIn(
                "no forced-tool",
                payload["intervention"]["refusal_reason"],
            )

    def test_micromachine_modulation_api_failure_does_not_use_rule_fallback(self):
        llm_control = TypedApiFailurePolicyModulationLLMControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=llm_control,
        )
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
                        "text": "마린 러쉬 진행해",
                        "blackboard_dir": directory,
                        "current_frame": 21,
                        "update_id": "api-failure-web-fallback",
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

            self.assertEqual(HTTPStatus.OK, HTTPStatus(response.status))
            self.assertEqual(1, llm_control.calls)
            self.assertFalse(payload["accepted"], payload)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual("llm", payload["provider_source"])
            self.assertEqual("refused", payload["compile_result"]["status"])
            self.assertEqual(
                "api_error",
                payload["compile_result"]["failure_kind"],
            )
            self.assertEqual(
                1,
                payload["compile_result"]["llm_attempt_count"],
            )
            self.assertEqual(
                321,
                payload["compile_result"]["llm_duration_ms"],
            )
            self.assertIn(
                "request timed out",
                payload["compile_result"]["refusal_reason"],
            )
            self.assertIsNone(payload["update"])

    def test_api_failure_does_not_publish_rule_derived_tactical_state(self):
        llm_control = TypedApiFailurePolicyModulationLLMControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=llm_control,
        )
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
                        "text": (
                            "마린 6기, 공성전차 2기, 바이킹 2기를 준비하고 "
                            "정찰 후 공격해. 주변 적이 잠깐 안 보여도 공격을 "
                            "취소하지 말고 불리하면 재집결해."
                        ),
                        "blackboard_dir": directory,
                        "current_frame": 21,
                        "update_id": "negated-cancel-web-fallback",
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

            self.assertEqual(HTTPStatus.OK, HTTPStatus(response.status))
            self.assertEqual(1, llm_control.calls)
            self.assertFalse(payload["accepted"], payload)
            self.assertEqual("llm", payload["provider_source"])
            self.assertEqual(
                "api_error",
                payload["compile_result"]["failure_kind"],
            )
            self.assertIsNone(payload["update"])

    def test_micromachine_modulation_schema_failure_does_not_use_rule_fallback(self):
        llm_control = SchemaInvalidPolicyModulationLLMControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=llm_control,
        )
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
                        "update_id": "compiler-schema-web-fallback",
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

            self.assertEqual(HTTPStatus.OK, HTTPStatus(response.status))
            self.assertEqual(1, llm_control.calls)
            self.assertFalse(payload["accepted"], payload)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual("llm", payload["provider_source"])
            self.assertEqual("refused", payload["compile_result"]["status"])
            self.assertIsNone(payload["update"])

    def test_micromachine_status_scopes_command_queue_to_active_update(self):
        dashboard = {
            "active_updates": [
                {
                    "update_id": "active-pressure",
                    "manager_bias_domains": ["combat"],
                    "vector": {
                        "goal": "active pressure",
                        "combat": {},
                        "squad": {},
                        "scope": {},
                        "tactical_task": {},
                    },
                }
            ],
            "telemetry": {"frame": 200},
        }
        telemetry = SimpleNamespace(
            frame=200,
            active_modulation_ids=("active-pressure",),
            to_dict=lambda: {"frame": 200, "active_modulation_ids": ["active-pressure"]},
        )
        stale_compile = {
            "status": "refused",
            "update_id": "stale-refusal",
            "source": "llm",
            "refusal_reason": "provider auth failed",
            "command_queue": {
                "category": "clarification",
                "action": "refused",
            },
        }

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            compile_result=stale_compile,
        )

        self.assertEqual({}, payload["command_queue"])
        self.assertNotIn("command_queue", payload["intervention"])
        self.assertEqual(
            stale_compile["command_queue"],
            payload["latest_request"]["command_queue"],
        )

    def test_micromachine_status_exposes_isolated_parallel_operations(self):
        update_id = "parallel-operation-update"
        dashboard = {
            "active_updates": [
                {
                    "update_id": update_id,
                    "issued_at_frame": 200,
                    "manager_bias_domains": ["combat", "scouting", "squad"],
                    "vector": {
                        "goal": "parallel recon and assault",
                        "operations": [
                            {
                                "operation_id": "recon-alpha",
                                "goal": "마린 1기로 적 본진 정찰",
                                "tactical_task": {
                                    "task_type": "scout_with_units",
                                    "unit_classes": ["TERRAN_MARINE"],
                                },
                            },
                            {
                                "operation_id": "assault-bravo",
                                "goal": "마린 4기로 적 앞마당 공격",
                                "tactical_task": {
                                    "task_type": "pressure_with_main_army",
                                    "unit_classes": ["TERRAN_MARINE"],
                                },
                                "operation_edit": {
                                    "action": "reinforce",
                                    "before_composition": [
                                        {
                                            "unit_type": "TERRAN_MARINE",
                                            "count": 2,
                                        }
                                    ],
                                    "after_composition": [
                                        {
                                            "unit_type": "TERRAN_MARINE",
                                            "count": 4,
                                        }
                                    ],
                                },
                            },
                        ],
                    },
                }
            ],
            "telemetry": {"frame": 240},
        }
        telemetry_document = {
            "frame": 240,
            "active_modulation_ids": [update_id],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "update_id": update_id,
                            "received_frame": 205,
                            "assignment": {
                                "status": "assigned",
                                "assigned_unit_count": 1,
                                "commanded_unit_type": "marine",
                            },
                            "submission": {
                                "status": "submitted",
                                "last_actual_command": "move",
                                "target_x": 120,
                                "target_y": 44,
                            },
                            "movement": {
                                "movement_observed": True,
                                "max_home_distance": 18.5,
                            },
                        },
                        {
                            "operation_id": "assault-bravo",
                            "update_id": update_id,
                            "received_frame": 206,
                            "status": "WAITING_FOR_UNITS",
                            "blocked_reason": "composition_prerequisites_pending",
                            "requirement_target_count": 4,
                            "requirement_represented_count": 2,
                            "requirement_missing_count": 2,
                            "requirement_progress": [
                                {
                                    "unit_type": "TERRAN_MARINE",
                                    "role": "main_army",
                                    "target_count": 4,
                                    "assigned_count": 2,
                                    "represented_count": 2,
                                    "completed_count": 2,
                                    "in_progress_count": 1,
                                    "queued_count": 1,
                                    "missing_count": 2,
                                    "production_blocker": "production_queued",
                                    "prerequisites": ["TERRAN_BARRACKS"],
                                    "missing_prerequisites": [],
                                }
                            ],
                            "edit_action": "reinforce",
                            "edit_before_count": 2,
                            "edit_after_count": 4,
                            "transferred_in_count": 2,
                            "edit_resolution": "blocked",
                            "edit_blocker": "explicit_ability_owner_protected",
                            "assignment": {
                                "status": "assigned",
                                "assigned_unit_count": 4,
                                "commanded_unit_type": "marine",
                            },
                            "submission": {"status": "pending"},
                            "movement": {"movement_observed": False},
                        },
                    ]
                },
                "CombatCommander": {
                    "policy_active": True,
                    "active_modulation_ids": [update_id],
                    "main_attack_actual_command_issued_count": 99,
                    "main_attack_last_issued_action": "must-not-be-copied",
                },
            },
        }
        telemetry = SimpleNamespace(
            frame=240,
            active_modulation_ids=(update_id,),
            to_dict=lambda: telemetry_document,
        )

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            blackboard_dir="/tmp/parallel-operation-status",
            compile_result={
                "status": "compiled",
                "update_id": update_id,
                "command_text": "정찰과 공격을 동시에 수행해",
            },
        )

        self.assertEqual(2, len(payload["operations"]))
        self.assertTrue(payload["operation_registry_authoritative"])
        operations = {
            operation["operation_id"]: operation
            for operation in payload["operations"]
        }
        self.assertEqual(
            {"recon-alpha", "assault-bravo"},
            set(operations),
        )
        self.assertEqual("scouting", operations["recon-alpha"]["mission"])
        self.assertEqual("attack", operations["assault-bravo"]["mission"])
        self.assertEqual(1, payload["operation_summary"]["scouting"])
        self.assertEqual(1, payload["operation_summary"]["attacking"])
        self.assertEqual(
            ["OperationDirector"],
            list(
                operations["recon-alpha"]["intervention"][
                    "manager_snapshot"
                ]
            ),
        )
        self.assertEqual(
            ["OperationDirector"],
            list(
                operations["assault-bravo"]["intervention"][
                    "manager_snapshot"
                ]
            ),
        )
        recon_execution = operations["recon-alpha"]["intervention"][
            "command_execution"
        ]
        assault_execution = operations["assault-bravo"]["intervention"][
            "command_execution"
        ]
        self.assertEqual("recon-alpha", recon_execution["operation_id"])
        self.assertEqual("effect_observed", recon_execution["state"])
        self.assertIn(
            "action_issued",
            [stage["name"] for stage in recon_execution["stages"]],
        )
        self.assertIn(
            "effect_observed",
            [stage["name"] for stage in recon_execution["stages"]],
        )
        self.assertEqual("assault-bravo", assault_execution["operation_id"])
        self.assertEqual("queued_or_assigned", assault_execution["state"])
        self.assertEqual(
            {
                "status": "WAITING_FOR_UNITS",
                "blocker": "composition_prerequisites_pending",
                "target_count": 4,
                "represented_count": 2,
                "missing_count": 2,
                "requirements": [
                    {
                        "unit_type": "TERRAN_MARINE",
                        "role": "main_army",
                        "target_count": 4,
                        "assigned_count": 2,
                        "represented_count": 2,
                        "completed_count": 2,
                        "in_progress_count": 1,
                        "queued_count": 1,
                        "missing_count": 2,
                        "production_blocker": "production_queued",
                        "prerequisites": ["TERRAN_BARRACKS"],
                        "missing_prerequisites": [],
                    }
                ],
            },
            operations["assault-bravo"]["operation_convergence"],
        )
        self.assertEqual(
            {
                "action": "reinforce",
                "before_composition": [
                    {"unit_type": "TERRAN_MARINE", "count": 2}
                ],
                "after_composition": [
                    {"unit_type": "TERRAN_MARINE", "count": 4}
                ],
                "before_count": 2,
                "after_count": 4,
                "transferred_in_count": 2,
                "resolution": "blocked",
                "blocker": "explicit_ability_owner_protected",
            },
            operations["assault-bravo"]["operation_edit"],
        )
        self.assertNotIn(
            "action_issued",
            [stage["name"] for stage in assault_execution["stages"]],
        )
        self.assertNotIn(
            "effect_observed",
            [stage["name"] for stage in assault_execution["stages"]],
        )
        self.assertNotIn(
            "must-not-be-copied",
            json.dumps(payload["operations"], ensure_ascii=False),
        )

    def test_micromachine_status_exposes_current_all_terran_family_evidence(
        self,
    ):
        update_id = "all-terran-harass-update"
        operation_id = "mixed-harass"
        generation = 2
        family_rows = []
        for index, family in enumerate(TERRAN_UNIT_FAMILIES):
            effect_observed = family.family == "reaper"
            blocked = family.family == "banshee"
            family_rows.append(
                {
                    "update_id": update_id,
                    "operation_id": operation_id,
                    "generation": generation,
                    "family": family.family,
                    "unit_type": family.unit_types[0],
                    "role": family.default_role,
                    "assigned": 0 if blocked else 1,
                    "represented": 0 if blocked else 1,
                    "action": f"ability:{family.abilities[0]}",
                    "required_effect": "ability_state_or_effect",
                    "attempt_generation": index + 1,
                    "attempted_count": 0 if blocked else 1,
                    "attempted_frame": 0 if blocked else 215 + index,
                    "attempted_unit_tags": (
                        [] if blocked else [1000 + index]
                    ),
                    "submitted_count": 0 if blocked else 1,
                    "submitted_frame": 0 if blocked else 230 + index,
                    "submitted_unit_tags": (
                        [] if blocked else [1000 + index]
                    ),
                    "effect_kind": (
                        "ability_state" if effect_observed else ""
                    ),
                    "effect_count": 1 if effect_observed else 0,
                    "effect_frame": 260 if effect_observed else 0,
                    "effect_unit_tags": (
                        [1000 + index] if effect_observed else []
                    ),
                    "blocker_manager": (
                        "ProductionManager" if blocked else ""
                    ),
                    "blocker": (
                        "missing_starport_techlab" if blocked else ""
                    ),
                }
            )
        current_tank = next(
            row for row in family_rows if row["family"] == "siege_tank"
        )
        stale_rows = [
            {**current_tank, "update_id": "stale-update"},
            {**current_tank, "operation_id": "stale-operation"},
            {**current_tank, "generation": 1},
            {**current_tank, "action": ""},
        ]
        dashboard = {
            "active_updates": [
                {
                    "update_id": update_id,
                    "issued_at_frame": 200,
                    "manager_bias_domains": ["combat", "squad"],
                    "vector": {
                        "goal": "15-family mixed harass",
                        "operations": [
                            {
                                "operation_id": operation_id,
                                "generation": generation,
                                "goal": "가용 테란 병력으로 적 일꾼을 견제",
                                "tactical_task": {
                                    "task_type": "harass_with_units",
                                    "location_intent": "enemy_mineral_line",
                                },
                            }
                        ],
                    },
                }
            ],
            "telemetry": {"frame": 300},
        }
        telemetry_document = {
            "frame": 300,
            "active_modulation_ids": [update_id],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "update_id": update_id,
                            "operation_id": operation_id,
                            "generation": generation,
                            "status": "MOVING",
                            "received_frame": 205,
                            "assigned_frame": 210,
                            "submitted_frame": 220,
                            "last_action_frame": 240,
                            "movement_frame": 250,
                            "assigned_count": 14,
                            "assigned_unit_tags": list(range(2001, 2015)),
                            "actor_tag": 2015,
                            "commanded_unit_tag": 2016,
                            "scout_last_commanded_unit_tag": 2017,
                            "max_home_distance": 24.0,
                            "last_action": "AttackMove",
                            "squad_order": "harass",
                            "family_evidence": family_rows + stale_rows,
                        }
                    ],
                }
            },
        }
        telemetry = SimpleNamespace(
            frame=300,
            active_modulation_ids=(update_id,),
            to_dict=lambda: telemetry_document,
        )

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            blackboard_dir="/tmp/all-terran-family-evidence",
            compile_result={
                "status": "compiled",
                "update_id": update_id,
                "command_text": "가용 테란 병력으로 적 일꾼을 견제해",
            },
        )

        self.assertEqual(1, len(payload["operations"]))
        operation = payload["operations"][0]
        self.assertEqual(operation_id, operation["operation_id"])
        self.assertEqual(generation, operation["operation_generation"])
        self.assertEqual("harass", operation["squad_order"])
        evidence = operation["family_evidence"]
        self.assertEqual(15, len(evidence))
        self.assertEqual(
            {family.family for family in TERRAN_UNIT_FAMILIES},
            {row["family"] for row in evidence},
        )
        for row in evidence:
            with self.subTest(family=row["family"]):
                self.assertEqual(update_id, row["update_id"])
                self.assertEqual(operation_id, row["operation_id"])
                self.assertEqual(generation, row["generation"])
                self.assertTrue(row["action"])
                self.assertGreater(row["attempt_generation"], 0)
                self.assertNotIn("attempted_unit_tags", row)
                self.assertNotIn("submitted_unit_tags", row)
                self.assertNotIn("effect_unit_tags", row)
        public_payload_json = json.dumps(payload, ensure_ascii=False)
        for internal_key in (
            "assigned_unit_tags",
            "actor_tag",
            "commanded_unit_tag",
            "scout_last_commanded_unit_tag",
            "attempted_unit_tags",
            "submitted_unit_tags",
            "effect_unit_tags",
        ):
            self.assertNotIn(internal_key, public_payload_json)
        execution_telemetry = operation["intervention"][
            "command_execution"
        ]["telemetry"]
        self.assertNotIn("assigned_unit_tags", execution_telemetry)
        for row in execution_telemetry["family_evidence"]:
            self.assertNotIn("attempted_unit_tags", row)
            self.assertNotIn("submitted_unit_tags", row)
            self.assertNotIn("effect_unit_tags", row)
        tactical_evidence_json = json.dumps(
            operation["intervention"]["tactical_evidence"],
            ensure_ascii=False,
        )
        self.assertNotIn("scout_last_commanded_unit_tag", tactical_evidence_json)
        self.assertNotIn("2017", tactical_evidence_json)
        by_family = {row["family"]: row for row in evidence}
        self.assertEqual("effect", by_family["reaper"]["stage"])
        self.assertEqual("blocked", by_family["banshee"]["stage"])
        self.assertEqual(
            "missing_starport_techlab",
            by_family["banshee"]["blocker"],
        )
        self.assertEqual("executed", by_family["siege_tank"]["stage"])
        self.assertFalse(by_family["siege_tank"]["effect"])

    def test_micromachine_status_exposes_removed_operation_pending_effect(
        self,
    ):
        update_id = "retargeted-harass-update"
        operation_id = "reaper-harass"
        generation = 2
        delivered = {
            "update_id": update_id,
            "operation_id": operation_id,
            "generation": generation,
            "family": "reaper",
            "unit_type": "TERRAN_REAPER",
            "role": "worker_harass",
            "action": "attack_move",
            "required_effect": "movement_or_engagement",
            "attempt_generation": 3,
            "attempted_count": 1,
            "attempted_frame": 210,
            "submitted_count": 1,
            "submitted_frame": 220,
            "effect_kind": "movement",
            "effect_count": 1,
            "effect_frame": 230,
            "blocker_manager": "",
            "blocker": "",
        }
        dashboard = {
            "active_updates": [
                {
                    "update_id": update_id,
                    "issued_at_frame": 200,
                    "expires_at_frame": 2_000,
                    "manager_bias_domains": ["combat", "squad"],
                    "vector": {
                        "goal": "리퍼 견제 목표를 변경",
                        "operations": [
                            {
                                "operation_id": operation_id,
                                "generation": generation,
                                "goal": "리퍼로 적 일꾼 견제",
                                "composition_requirements": [
                                    {
                                        "unit_type": "TERRAN_REAPER",
                                        "count": 1,
                                        "role": "worker_harass",
                                    }
                                ],
                                "tactical_task": {
                                    "task_type": "harass_with_units",
                                    "duration_seconds": 120,
                                },
                            }
                        ],
                    },
                }
            ],
            "telemetry": {"frame": 300},
        }
        telemetry_document = {
            "frame": 300,
            "active_modulation_ids": [update_id],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": "replacement-update",
                    "operations": [
                        {
                            "operation_id": operation_id,
                            "generation": generation,
                            "status": "COMPLETED",
                            "completed": True,
                            "assigned_count": 9,
                            "assigned_unit_tags": list(range(1, 10)),
                            "submitted_frame": 240,
                            "last_action_frame": 250,
                            "last_action": "replacement-operation",
                        }
                    ],
                    "pending_family_effects": [
                        delivered,
                        {**delivered, "update_id": "other-update"},
                        {**delivered, "operation_id": "other-operation"},
                        {**delivered, "generation": 1},
                        {
                            **delivered,
                            "attempted_frame": 199,
                            "submitted_frame": 200,
                            "effect_frame": 201,
                        },
                        {**delivered, "effect_frame": 301},
                        {**delivered, "family": "unrelated-family"},
                        {**delivered, "action": ""},
                    ],
                }
            },
        }
        telemetry = SimpleNamespace(
            frame=300,
            active_modulation_ids=(update_id,),
            to_dict=lambda: telemetry_document,
        )

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            blackboard_dir="/tmp/removed-operation-family-effect",
            compile_result={
                "status": "compiled",
                "update_id": update_id,
                "command_text": "리퍼 견제 목표를 변경해",
            },
        )

        self.assertEqual(1, len(payload["operations"]))
        operation = payload["operations"][0]
        self.assertTrue(operation["telemetry_current"])
        self.assertEqual(generation, operation["operation_generation"])
        self.assertEqual(1, len(operation["family_evidence"]))
        evidence = operation["family_evidence"][0]
        self.assertEqual(update_id, evidence["update_id"])
        self.assertEqual(operation_id, evidence["operation_id"])
        self.assertEqual(generation, evidence["generation"])
        self.assertEqual("effect", evidence["stage"])
        self.assertTrue(evidence["effect"])
        self.assertEqual(0, evidence["assigned"])
        self.assertEqual(0, evidence["represented"])
        execution = operation["intervention"]["command_execution"]
        self.assertEqual("published", execution["state"])
        self.assertFalse(execution["completed"])
        self.assertEqual(
            set(),
            {
                stage["name"]
                for stage in execution["stages"]
                if stage["name"]
                in {
                    "queued_or_assigned",
                    "order_issued",
                    "action_issued",
                    "effect_observed",
                }
            },
        )

    def test_micromachine_status_preserves_acknowledged_effect_from_archive(
        self,
    ):
        update_id = "acknowledged-effect-update"
        operation_id = "marine-recon"
        delivered = {
            "update_id": update_id,
            "operation_id": operation_id,
            "generation": 1,
            "family": "marine",
            "unit_type": "TERRAN_MARINE",
            "role": "scout",
            "action": "move",
            "required_effect": "movement_or_engagement",
            "attempt_generation": 2,
            "attempted_count": 1,
            "attempted_frame": 210,
            "submitted_count": 1,
            "submitted_frame": 220,
            "effect_kind": "movement",
            "effect_count": 1,
            "effect_frame": 230,
            "blocker_manager": "",
            "blocker": "",
        }
        dashboard = {
            "active_updates": [
                {
                    "update_id": update_id,
                    "issued_at_frame": 200,
                    "expires_at_frame": 2_000,
                    "manager_bias_domains": ["scouting", "squad"],
                    "vector": {
                        "goal": "마린 한 기로 정찰",
                        "operations": [
                            {
                                "operation_id": operation_id,
                                "generation": 1,
                                "goal": "마린 한 기로 정찰",
                                "tactical_task": {
                                    "task_type": "scout_with_units",
                                    "duration_seconds": 120,
                                },
                            }
                        ],
                    },
                }
            ],
            "telemetry": {"frame": 300},
        }
        archived_telemetry = {
            "frame": 240,
            "active_modulation_ids": [update_id],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": operation_id,
                            "generation": 1,
                            "status": "MOVING",
                            "received_frame": 205,
                        }
                    ],
                    "pending_family_effects": [
                        delivered,
                        {**delivered, "update_id": "wrong-update"},
                        {**delivered, "generation": 2},
                        {
                            **delivered,
                            "attempted_frame": 199,
                            "submitted_frame": 200,
                            "effect_frame": 201,
                        },
                        {**delivered, "effect_frame": 241},
                    ],
                }
            },
        }
        latest_telemetry_document = {
            "frame": 300,
            "active_modulation_ids": [update_id],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": operation_id,
                            "generation": 1,
                            "status": "MOVING",
                            "received_frame": 205,
                            "assigned_frame": 215,
                            "assigned_count": 1,
                            "submitted_frame": 220,
                        }
                    ],
                    "pending_family_effects": [],
                }
            },
        }
        telemetry = SimpleNamespace(
            frame=300,
            active_modulation_ids=(update_id,),
            to_dict=lambda: latest_telemetry_document,
        )

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            telemetry_archive=(archived_telemetry,),
            blackboard_dir="/tmp/acknowledged-family-effect",
            compile_result={
                "status": "compiled",
                "update_id": update_id,
                "command_text": "마린 한 기로 정찰해",
            },
        )

        operation = payload["operations"][0]
        self.assertTrue(operation["telemetry_current"])
        self.assertEqual(1, len(operation["family_evidence"]))
        evidence = operation["family_evidence"][0]
        self.assertEqual(update_id, evidence["update_id"])
        self.assertEqual(operation_id, evidence["operation_id"])
        self.assertEqual(1, evidence["generation"])
        self.assertEqual("move", evidence["action"])
        self.assertEqual("effect", evidence["stage"])
        self.assertEqual(230, evidence["effect_frame"])

    def test_rejected_higher_generation_edit_uses_active_generation_telemetry(self):
        update_id = "rejected-operation-edit"
        dashboard = {
            "active_updates": [
                {
                    "update_id": update_id,
                    "issued_at_frame": 200,
                    "manager_bias_domains": ["combat", "squad"],
                    "vector": {
                        "goal": "transfer one scout",
                        "operations": [
                            {
                                "operation_id": "recon-alpha",
                                "generation": 2,
                                "goal": "release one scout",
                                "tactical_task": {
                                    "task_type": "scout_with_units",
                                    "duration_seconds": 120,
                                },
                                "operation_edit": {
                                    "action": "transfer_out",
                                },
                            }
                        ],
                    },
                }
            ],
            "telemetry": {"frame": 240},
        }
        telemetry_document = {
            "frame": 240,
            "active_modulation_ids": [update_id],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "status": "MOVING",
                            "received_frame": 100,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 140,
                            "movement_frame": 150,
                            "engagement_frame": 160,
                            "assigned_unit_tags": [11],
                            "assigned_count": 1,
                            "max_home_distance": 20.0,
                            "engaged": True,
                            "last_action": "AttackUnitOrder",
                            "edit_action": "transfer_out",
                            "edit_requested_generation": 2,
                            "edit_rejected_update_id": update_id,
                            "edit_rejected_frame": 225,
                            "edit_resolution": "blocked",
                            "edit_blocker": "destination_priority_not_higher",
                        }
                    ],
                    "pending_family_effects": [
                        {
                            "update_id": update_id,
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "family": "marine",
                            "unit_type": "TERRAN_MARINE",
                            "role": "scout",
                            "action": "move",
                            "required_effect": "movement_or_engagement",
                            "attempt_generation": 2,
                            "attempted_count": 1,
                            "attempted_frame": 130,
                            "submitted_count": 1,
                            "submitted_frame": 140,
                            "effect_kind": "movement",
                            "effect_count": 1,
                            "effect_frame": 150,
                            "blocker_manager": "",
                            "blocker": "",
                        },
                        {
                            "update_id": update_id,
                            "operation_id": "recon-alpha",
                            "generation": 2,
                            "family": "marine",
                            "unit_type": "TERRAN_MARINE",
                            "role": "scout",
                            "action": "attack_move",
                            "required_effect": "movement_or_engagement",
                            "attempt_generation": 3,
                            "attempted_count": 1,
                            "attempted_frame": 170,
                            "submitted_count": 1,
                            "submitted_frame": 180,
                            "effect_kind": "engagement",
                            "effect_count": 1,
                            "effect_frame": 190,
                            "blocker_manager": "",
                            "blocker": "",
                        },
                    ],
                }
            },
        }
        telemetry = SimpleNamespace(
            frame=240,
            active_modulation_ids=(update_id,),
            to_dict=lambda: telemetry_document,
        )

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            blackboard_dir="/tmp/rejected-operation-edit",
            compile_result={
                "status": "compiled",
                "update_id": update_id,
                "command_text": "정찰대 마린 한 기를 공격대로 이관해",
            },
        )

        operation = payload["operations"][0]
        self.assertTrue(operation["telemetry_current"])
        self.assertEqual(
            "destination_priority_not_higher",
            operation["operation_edit"]["blocker"],
        )
        self.assertEqual(1, operation["operation_generation"])
        self.assertEqual(2, operation["requested_operation_generation"])
        execution = operation["intervention"]["command_execution"]
        self.assertEqual(1, execution["operation_generation"])
        self.assertEqual("effect_observed", execution["state"])
        self.assertFalse(execution["failed"])
        self.assertEqual("", execution["blocker_reason"])
        successful_stages = {
            stage["name"] for stage in execution["stages"] if stage["ok"]
        }
        self.assertIn("queued_or_assigned", successful_stages)
        self.assertIn("order_issued", successful_stages)
        self.assertIn("action_issued", successful_stages)
        self.assertIn("effect_observed", successful_stages)
        self.assertEqual(1, len(operation["family_evidence"]))
        self.assertEqual(1, operation["family_evidence"][0]["generation"])
        self.assertEqual("effect", operation["family_evidence"][0]["stage"])

    def test_micromachine_operation_flat_zero_frames_are_not_success(self):
        execution = web_gui._micromachine_operation_command_execution(
            update_id="parallel-zero-frames",
            operation_id="recon-zero",
            operation_generation=1,
            operation_telemetry={
                "operation_id": "recon-zero",
                "generation": 1,
                "task_type": "scout",
                "status": "",
                "assigned_unit_tags": [],
                "assigned_count": 0,
                "target_x": 120.0,
                "target_y": 44.0,
                "route_type": "direct",
                "target_evidence": "",
                "received_frame": 0,
                "assigned_frame": 0,
                "submitted_frame": 0,
                "last_action_frame": 0,
                "movement_frame": 0,
                "engagement_frame": 0,
                "max_home_distance": 0.0,
                "engaged": False,
                "completed": False,
                "blocked_reason": "",
                "last_action": "",
            },
            fallback={},
        )

        self.assertEqual("published", execution["state"])
        self.assertFalse(execution["completed"])
        self.assertFalse(execution["failed"])
        self.assertEqual(
            set(),
            {
                stage["name"]
                for stage in execution["stages"]
                if stage["name"]
                in {
                    "consumed_by_manager",
                    "queued_or_assigned",
                    "order_issued",
                    "action_issued",
                    "effect_observed",
                }
            },
        )

    def test_micromachine_operation_rejects_foreign_result_execution(self):
        payload = web_gui._micromachine_operation_status_payload(
            {
                "update_id": "new-update",
                "issued_at_frame": 100,
                "vector": {
                    "operation_id": "new-operation",
                    "generation": 1,
                    "goal": "new operation waits for its own evidence",
                },
            },
            operation_id="new-operation",
            operation_count=1,
            active=False,
            telemetry=None,
            telemetry_archive=(),
            blackboard_dir="",
            result_item={
                "status": "completed",
                "command_text": "stale completed result",
                "intervention": {
                    "command_execution": {
                        "command_id": "old-update",
                        "operation_id": "old-operation",
                        "operation_generation": 7,
                        "state": "completed",
                        "completed": True,
                        "failed": False,
                        "expired": False,
                        "stages": [
                            {"name": "action_issued", "ok": True},
                            {"name": "effect_observed", "ok": True},
                        ],
                    }
                },
            },
            compile_result={
                "status": "compiled",
                "update_id": "new-update",
            },
        )

        execution = payload["intervention"]["command_execution"]
        self.assertEqual("new-update", execution["command_id"])
        self.assertEqual("new-operation", execution["operation_id"])
        self.assertEqual(1, execution["operation_generation"])
        self.assertEqual("published", execution["state"])
        self.assertFalse(execution["completed"])
        self.assertFalse(execution["failed"])
        self.assertEqual([], execution["stages"])
        self.assertEqual("pending", payload["disposition"])

    def test_micromachine_operation_order_only_is_not_action_submission(self):
        execution = web_gui._micromachine_operation_command_execution(
            update_id="order-only-update",
            operation_id="order-only-operation",
            operation_generation=1,
            operation_telemetry={
                "operation_id": "order-only-operation",
                "generation": 1,
                "received_frame": 100,
                "assigned_frame": 110,
                "assigned_count": 4,
                "submitted_frame": 120,
                "order_issued": True,
                "action_issued": False,
                "last_action_frame": 0,
                "last_action": "",
                "completed": False,
            },
            fallback={},
        )

        stages = {
            stage["name"]: stage
            for stage in execution["stages"]
        }
        self.assertEqual("order_issued", execution["state"])
        self.assertIn("order_issued", stages)
        self.assertNotIn("action_issued", stages)
        self.assertFalse(execution["completed"])
        self.assertFalse(execution["failed"])

    def test_micromachine_operation_ability_requires_matching_family_effect(self):
        update_id = "siege-ability-operation"
        operation_id = "siege-alpha"
        operation_update = {
            "update_id": update_id,
            "issued_at_frame": 100,
            "vector": {
                "operation_id": operation_id,
                "generation": 1,
                "composition_requirements": [
                    {
                        "unit_type": "TERRAN_SIEGETANK",
                        "count": 1,
                        "role": "siege_support",
                    }
                ],
                "unit_roles": [
                    {
                        "unit_type": "TERRAN_SIEGETANK",
                        "role": "siege_support",
                        "ability_policy": "siege_mode",
                    }
                ],
                "tactical_task": {
                    "task_type": "pressure_with_main_army",
                    "unit_classes": ["TERRAN_SIEGETANK"],
                },
            },
        }
        family_row = {
            "update_id": update_id,
            "operation_id": operation_id,
            "generation": 1,
            "family": "siege_tank",
            "unit_type": "TERRAN_SIEGETANK",
            "role": "siege_support",
            "assigned": 1,
            "represented": 1,
            "action": "attack_move",
            "required_effect": "movement_or_engagement",
            "attempt_generation": 1,
            "attempted_count": 1,
            "attempted_frame": 130,
            "attempted_unit_tags": [7001],
            "submitted_count": 1,
            "submitted_frame": 140,
            "submitted_unit_tags": [7001],
            "effect_kind": "engagement",
            "effect_count": 1,
            "effect_frame": 150,
            "effect_unit_tags": [7001],
            "blocker_manager": "",
            "blocker": "",
        }
        operation_telemetry = {
            "update_id": update_id,
            "operation_id": operation_id,
            "generation": 1,
            "requested_task_type": "pressure_with_main_army",
            "task_type": "attack",
            "squad_order": "attack",
            "status": "MOVING",
            "received_frame": 110,
            "assigned_frame": 120,
            "submitted_frame": 140,
            "last_action_frame": 145,
            "assigned_unit_tags": [7001],
            "assigned_count": 1,
            "max_home_distance": 24.0,
            "engaged": True,
            "family_evidence": [family_row],
        }

        def execution_for_current_row():
            telemetry_document = {
                "frame": 160,
                "active_modulation_ids": [update_id],
                "managers": {
                    "OperationDirector": {
                        "policy_update_id": update_id,
                        "operations": [operation_telemetry],
                    }
                },
            }
            strict = web_gui._micromachine_strict_operation_execution(
                operation_update,
                operation_id=operation_id,
                operation_generation=1,
                operation_telemetry_document=telemetry_document,
            )
            return web_gui._micromachine_operation_command_execution(
                update_id=update_id,
                operation_id=operation_id,
                operation_generation=1,
                operation_telemetry=operation_telemetry,
                fallback=strict,
            )

        movement_only = execution_for_current_row()
        movement_stages = {
            stage["name"]: stage for stage in movement_only["stages"]
        }
        self.assertTrue(
            web_gui._micromachine_execution_has_active_family_contract(
                movement_only
            )
        )
        self.assertNotIn("effect_observed", {
            name for name, stage in movement_stages.items() if stage["ok"]
        })
        self.assertNotEqual("effect_observed", movement_only["state"])
        self.assertFalse(movement_only["completed"])

        operation_telemetry["family_evidence"] = []
        missing_family_payload = (
            web_gui._micromachine_operation_status_payload(
                operation_update,
                operation_id=operation_id,
                operation_count=1,
                active=True,
                telemetry={
                    "frame": 160,
                    "active_modulation_ids": [update_id],
                    "managers": {
                        "OperationDirector": {
                            "policy_update_id": update_id,
                            "operations": [operation_telemetry],
                        }
                    },
                },
                telemetry_archive=(),
                blackboard_dir="",
                result_item={},
                compile_result={},
            )
        )
        missing_family_execution = missing_family_payload[
            "intervention"
        ]["command_execution"]
        missing_family_stages = {
            stage["name"]: stage
            for stage in missing_family_execution["stages"]
        }
        self.assertTrue(
            web_gui._micromachine_execution_has_active_family_contract(
                missing_family_execution
            )
        )
        self.assertFalse(
            missing_family_stages["effect_observed"]["ok"],
            missing_family_execution,
        )
        self.assertNotEqual(
            "effect_observed",
            missing_family_execution["state"],
        )

        legacy_only_payload = (
            web_gui._micromachine_operation_status_payload(
                operation_update,
                operation_id=operation_id,
                operation_count=1,
                active=True,
                telemetry={
                    "frame": 160,
                    "active_modulation_ids": [update_id],
                    "managers": {
                        "GameCommander": {"update_id": update_id},
                        "CombatCommander": {
                            "policy_update_id": update_id,
                            "main_attack_actual_command_issued_count": 1,
                            "main_attack_last_action_frame": 145,
                            "main_attack_last_issued_action": "attack_move",
                            "main_attack_max_home_distance": 24.0,
                        },
                        "UnitRoleTask": {
                            "task_update_id": update_id,
                            "unit_type": "TERRAN_SIEGETANK",
                            "role": "siege_support",
                            "ability_policy": "siege_mode",
                            "status": "executed",
                            "attempted_count": 1,
                            "executed_count": 1,
                            "last_action_frame": 145,
                            "issued_action": "attack_move",
                            "max_home_distance": 24.0,
                        },
                    },
                },
                telemetry_archive=(),
                blackboard_dir="",
                result_item={},
                compile_result={},
            )
        )
        legacy_only_execution = legacy_only_payload[
            "intervention"
        ]["command_execution"]
        legacy_only_stages = {
            stage["name"]: stage
            for stage in legacy_only_execution["stages"]
        }
        self.assertTrue(
            web_gui._micromachine_execution_has_active_family_contract(
                legacy_only_execution
            )
        )
        self.assertFalse(
            legacy_only_stages["effect_observed"]["ok"],
            legacy_only_execution,
        )
        self.assertNotEqual(
            "effect_observed",
            legacy_only_execution["state"],
        )

        operation_telemetry["family_evidence"] = [family_row]
        family_row.update(
            {
                "action": "ability:MORPH_SIEGEMODE",
                "required_effect": "ability_state_or_effect",
                "effect_kind": "ability_state",
            }
        )
        ability_confirmed = execution_for_current_row()
        ability_stages = {
            stage["name"]: stage for stage in ability_confirmed["stages"]
        }
        self.assertTrue(ability_stages["effect_observed"]["ok"])
        self.assertEqual("effect_observed", ability_confirmed["state"])

    def test_public_runtime_payload_redacts_unit_tag_aliases(self):
        payload = web_gui._public_runtime_launcher_payload(
            {
                "assigned_tags": [7001, 7002],
                "selected_worker_tags": [8001],
                "commanded_tags": [8101],
                "actor_tags": [8201],
                "owned_tags": [8301],
                "last_line": (
                    "assigned_tags=[7001,7002] "
                    "selected_worker_tags=[8001] "
                    "commanded_tags=[8101] "
                    "actor_tags=[8201] "
                    "owned_tags=[8301]"
                ),
                "tuple_line": "actor_tags=(7201, 7202)",
                "set_line": "owned_tags={7301,7302}",
                "csv_line": "commanded_tags=7101,7102 action=attack",
                "spaced_line": "source_tags=7401 7402 action=move",
                "unclosed_line": "target_tags=[7501,7502",
                "container_comma_line": (
                    "actor_tags=[7601,7602],7603 action=attack"
                ),
                "container_semicolon_line": (
                    "actor_tags=(7701,7702);7703 action=attack"
                ),
                "container_pipe_line": (
                    "actor_tags={7801,7802}|7803 action=attack"
                ),
                "container_space_line": (
                    "actor_tags=<7901,7902> 7903 action=attack"
                ),
                "empty_container_line": (
                    "actor_tags=[] 7951 action=attack"
                ),
                "strategic_tags": ["pressure", "flank"],
            }
        )

        self.assertNotIn("assigned_tags", payload)
        self.assertNotIn("selected_worker_tags", payload)
        self.assertNotIn("commanded_tags", payload)
        self.assertNotIn("actor_tags", payload)
        self.assertNotIn("owned_tags", payload)
        self.assertEqual(["pressure", "flank"], payload["strategic_tags"])
        for raw_tag in ("7001", "8001", "8101", "8201", "8301"):
            self.assertNotIn(raw_tag, payload["last_line"])
        self.assertEqual(
            5,
            payload["last_line"].count(
                "[internal unit identity]: [redacted]"
            ),
        )
        for line_name, raw_tags in {
            "tuple_line": ("7201", "7202"),
            "set_line": ("7301", "7302"),
            "csv_line": ("7101", "7102"),
            "spaced_line": ("7401", "7402"),
            "unclosed_line": ("7501", "7502"),
            "container_comma_line": ("7601", "7602", "7603"),
            "container_semicolon_line": ("7701", "7702", "7703"),
            "container_pipe_line": ("7801", "7802", "7803"),
            "container_space_line": ("7901", "7902", "7903"),
            "empty_container_line": ("7951",),
        }.items():
            self.assertIn(
                "[internal unit identity]: [redacted]",
                payload[line_name],
            )
            for raw_tag in raw_tags:
                self.assertNotIn(raw_tag, payload[line_name])

    def test_public_runtime_payload_semantic_tags_reject_unit_identities(self):
        payload = web_gui._public_runtime_launcher_payload(
            {
                "tags": [
                    "pressure",
                    "phase-2",
                    "squad-42",
                    9101,
                    "9102",
                    "unit_9108",
                    "tag 9109",
                    ["nested-label", 9103],
                    {"nested": 9104},
                    "tag=9105",
                    "[9106, 9107]",
                ],
                "strategic_tags": (
                    "flank",
                    9201,
                    "9202|9203",
                    ("nested-label", 9204),
                ),
                "expected_tags": [
                    "scouting_map_control",
                    {"nested": [9301]},
                    9302,
                    "<9303 9304>",
                ],
                "tech_path_tags": 9401,
                "expected_profile_tags": {
                    "label": "pressure",
                    "unit_identity": 9501,
                },
            }
        )

        self.assertEqual(
            ["pressure", "phase-2", "squad-42"],
            payload["tags"],
        )
        self.assertEqual(("flank",), payload["strategic_tags"])
        self.assertEqual(
            ["scouting_map_control"],
            payload["expected_tags"],
        )
        self.assertNotIn("tech_path_tags", payload)
        self.assertNotIn("expected_profile_tags", payload)
        serialized = json.dumps(payload, sort_keys=True)
        for raw_tag in (*range(9101, 9502), 9108, 9109):
            self.assertNotIn(str(raw_tag), serialized)

    def test_micromachine_operation_root_update_id_is_fail_closed(self):
        update_id = "parallel-current-update"

        def telemetry_document(root_update_id=...):
            director = {
                "operations": [
                    {
                        "operation_id": "recon-alpha",
                        "generation": 1,
                        "task_type": "scout",
                        "status": "ASSIGNED",
                        "assigned_unit_tags": [11],
                        "assigned_count": 1,
                        "received_frame": 205,
                        "assigned_frame": 206,
                        "submitted_frame": 0,
                        "last_action_frame": 0,
                        "max_home_distance": 0.0,
                        "engaged": False,
                        "completed": False,
                        "blocked_reason": "",
                        "last_action": "",
                    }
                ]
            }
            if root_update_id is not ...:
                director["policy_update_id"] = root_update_id
            return {
                "frame": 240,
                "active_modulation_ids": [update_id],
                "managers": {"OperationDirector": director},
            }

        with self.subTest("matching root id is propagated"):
            document, entry = web_gui._micromachine_operation_telemetry_document(
                telemetry_document(update_id),
                update_id=update_id,
                operation_id="recon-alpha",
                operation_generation=1,
            )
            self.assertIsNotNone(entry)
            self.assertEqual(update_id, entry["policy_update_id"])
            self.assertEqual(
                update_id,
                document["managers"]["OperationDirector"]["policy_update_id"],
            )

        with self.subTest("mismatched root id is rejected"):
            document, entry = web_gui._micromachine_operation_telemetry_document(
                telemetry_document("parallel-stale-update"),
                update_id=update_id,
                operation_id="recon-alpha",
                operation_generation=1,
            )
            self.assertEqual({}, document)
            self.assertIsNone(entry)

        with self.subTest("conflicting entry id is rejected"):
            document_with_conflict = telemetry_document(update_id)
            director = document_with_conflict["managers"]["OperationDirector"]
            director["operations"][0]["update_id"] = "parallel-stale-entry"
            document, entry = web_gui._micromachine_operation_telemetry_document(
                document_with_conflict,
                update_id=update_id,
                operation_id="recon-alpha",
                operation_generation=1,
            )
            self.assertEqual({}, document)
            self.assertIsNone(entry)

        with self.subTest("missing root id is rejected"):
            document, entry = web_gui._micromachine_operation_telemetry_document(
                telemetry_document(),
                update_id=update_id,
                operation_id="recon-alpha",
                operation_generation=1,
            )
            self.assertEqual({}, document)
            self.assertIsNone(entry)

    def test_micromachine_operation_flat_terminal_fields_drive_execution(self):
        flat_telemetry = {
            "operation_id": "assault-bravo",
            "generation": 1,
            "task_type": "attack",
            "status": "SUBMITTED",
            "assigned_unit_tags": [21, 22, 23, 24],
            "assigned_count": 4,
            "target_x": 130.0,
            "target_y": 48.0,
            "route_type": "flank",
            "target_evidence": "observed_enemy_structure",
            "received_frame": 205,
            "assigned_frame": 206,
            "submitted_frame": 207,
            "last_action_frame": 207,
            "max_home_distance": 14.0,
            "engaged": False,
            "completed": False,
            "cancelled": False,
            "blocked_reason": "",
            "last_action": "AttackMove|operation=assault-bravo",
        }
        cases = (
            (
                "status completed",
                {"status": "COMPLETED"},
                {
                    "state": "completed",
                    "completed": True,
                    "failed": False,
                    "superseded": False,
                    "blocker_reason": "",
                    "disposition": "completed",
                },
            ),
            (
                "completed flag",
                {"completed": True},
                {
                    "state": "completed",
                    "completed": True,
                    "failed": False,
                    "superseded": False,
                    "blocker_reason": "",
                    "disposition": "completed",
                },
            ),
            (
                "blocked status and reason",
                {
                    "status": "BLOCKED",
                    "blocked_reason": "insufficient_eligible_units",
                },
                {
                    "state": "blocked",
                    "completed": False,
                    "failed": True,
                    "superseded": False,
                    "blocker_reason": "insufficient_eligible_units",
                    "disposition": "blocked",
                },
            ),
            (
                "cancelled flag",
                {
                    "cancelled": True,
                    "blocked_reason": "cancelled_by_policy",
                },
                {
                    "state": "cancelled",
                    "completed": False,
                    "failed": False,
                    "superseded": True,
                    "blocker_reason": "cancelled_by_policy",
                    "disposition": "superseded",
                },
            ),
        )

        for case_name, overrides, expected in cases:
            with self.subTest(case_name):
                operation_telemetry = dict(flat_telemetry)
                operation_telemetry.update(overrides)
                execution = web_gui._micromachine_operation_command_execution(
                    update_id="parallel-terminal-update",
                    operation_id="assault-bravo",
                    operation_generation=1,
                    operation_telemetry=operation_telemetry,
                    fallback={},
                )
                disposition = web_gui._micromachine_operation_disposition(
                    execution,
                    active=True,
                    transport_status="published",
                )

                for key in (
                    "state",
                    "completed",
                    "failed",
                    "superseded",
                    "blocker_reason",
                ):
                    self.assertEqual(expected[key], execution[key])
                self.assertEqual(expected["disposition"], disposition)

    def test_micromachine_operation_cleanup_stop_is_not_mission_effect(self):
        fallback = {
            "command_id": "cancel-cleanup-operation",
            "operation_id": "assault-bravo",
            "operation_generation": 1,
            "state": "published",
            "stages": [],
        }
        execution = web_gui._micromachine_operation_command_execution(
            update_id="cancel-cleanup-operation",
            operation_id="assault-bravo",
            operation_generation=1,
            operation_telemetry={
                "operation_id": "assault-bravo",
                "generation": 1,
                "status": "CANCELLED",
                "received_frame": 110,
                "assigned_frame": 120,
                "submitted_frame": 0,
                "last_action_frame": 210,
                "assigned_unit_tags": [21, 22, 23, 24],
                "assigned_count": 4,
                "moving": True,
                "engaged": True,
                "blocked_reason": "cancelled_by_policy",
                "last_action": "release_stop|cancelled_by_policy",
            },
            fallback=fallback,
        )

        stages = {
            stage["name"]: stage
            for stage in execution["stages"]
        }
        self.assertEqual("cancelled", execution["state"])
        self.assertFalse(execution["completed"])
        self.assertFalse(execution["failed"])
        self.assertTrue(execution["superseded"])
        self.assertNotIn("order_issued", stages)
        self.assertNotIn("action_issued", stages)
        self.assertNotIn("effect_observed", stages)
        self.assertEqual(
            {
                "action": "release_stop|cancelled_by_policy",
                "frame": 210,
                "operation_id": "assault-bravo",
                "generation": 1,
            },
            execution["terminal_cleanup"],
        )

    def test_micromachine_operation_cleanup_without_owned_units_is_terminal(
        self,
    ):
        fallback = {
            "command_id": "cancel-empty-operation",
            "operation_id": "recon-alpha",
            "operation_generation": 3,
            "state": "published",
            "stages": [],
        }
        cases = (
            ("cancelled-before-assignment", 0),
            ("cancelled-after-owned-units-died", 4),
        )

        for label, previous_assigned_count in cases:
            with self.subTest(case=label):
                execution = web_gui._micromachine_operation_command_execution(
                    update_id="cancel-empty-operation",
                    operation_id="recon-alpha",
                    operation_generation=3,
                    operation_telemetry={
                        "operation_id": "recon-alpha",
                        "generation": 3,
                        "status": "CANCELLED",
                        "received_frame": 310,
                        "assigned_frame": (
                            0 if previous_assigned_count == 0 else 320
                        ),
                        "assigned_unit_tags": [],
                        "assigned_count": previous_assigned_count,
                        "blocked_reason": "cancelled_by_policy",
                        "last_action": (
                            "release_no_owned_units|cancelled_by_policy"
                        ),
                        "last_action_frame": 410,
                    },
                    fallback=fallback,
                )

                self.assertEqual("cancelled", execution["state"])
                self.assertTrue(execution["superseded"])
                self.assertEqual(
                    {
                        "action": (
                            "release_no_owned_units|cancelled_by_policy"
                        ),
                        "frame": 410,
                        "operation_id": "recon-alpha",
                        "generation": 3,
                    },
                    execution["terminal_cleanup"],
                )

    def test_micromachine_status_keeps_terminal_result_as_separate_operation(self):
        dashboard = {
            "active_updates": [
                {
                    "update_id": "active-recon-update",
                    "issued_at_frame": 100,
                    "manager_bias_domains": ["scouting"],
                    "vector": {
                        "operations": [
                            {
                                "operation_id": "recon-live",
                                "goal": "바이킹 정찰",
                                "tactical_task": {
                                    "task_type": "scout_with_units",
                                },
                            }
                        ]
                    },
                }
            ],
            "telemetry": {"frame": 120},
        }
        telemetry = SimpleNamespace(
            frame=120,
            active_modulation_ids=("active-recon-update",),
            to_dict=lambda: {
                "frame": 120,
                "active_modulation_ids": ["active-recon-update"],
                "managers": {
                    "OperationDirector": {
                        "operations": {
                            "recon-live": {
                                "operation_id": "recon-live",
                                "update_id": "active-recon-update",
                                "assignment": {
                                    "status": "assigned",
                                    "assigned_unit_count": 1,
                                },
                            }
                        }
                    }
                },
            },
        )
        result_stream = [
            {
                "status": "publish_failed",
                "command_text": "별도 공격 작전",
                "compile_result": {
                    "status": "refused",
                    "update_id": "failed-assault-update",
                    "refusal_reason": "no eligible assault units",
                    "vector": {
                        "operations": [
                            {
                                "operation_id": "assault-failed",
                                "goal": "탱크 공격",
                                "tactical_task": {
                                    "task_type": "pressure_with_main_army",
                                },
                            }
                        ]
                    },
                },
            }
        ]

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            result_stream=result_stream,
        )

        operations = {
            operation["operation_id"]: operation
            for operation in payload["operations"]
        }
        self.assertEqual(
            {"recon-live", "assault-failed"},
            set(operations),
        )
        self.assertEqual("active", operations["recon-live"]["disposition"])
        self.assertEqual("blocked", operations["assault-failed"]["disposition"])
        self.assertEqual(
            "no eligible assault units",
            operations["assault-failed"]["compile_result"]["refusal_reason"],
        )
        self.assertEqual(
            {},
            operations["assault-failed"]["intervention"]["manager_snapshot"],
        )

    def test_micromachine_provider_output_cannot_spoof_llm_or_smoke_source(self):
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
                        "text": "수비",
                        "blackboard_dir": directory,
                        "current_frame": 22,
                        "update_id": "provider-output-ui-source",
                        "provider_output": {
                            "source": "smoke_keyword",
                            "modulation": {
                                "source": "smoke_keyword",
                                "goal": "spoof source",
                                "combat": {"defend_bias": 0.5},
                            },
                        },
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
            self.assertTrue(payload["accepted"], payload)
            self.assertEqual("ui", payload["provider_source"])
            self.assertEqual("ui", payload["update"]["vector"]["source"])

    def test_micromachine_modulation_uses_configured_llm_provider_for_free_text(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=FakePolicyModulationLLMControl(),
        )
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
                        "text": "공격적으로 마린 탐색해서 적발견시 바로 공격해",
                        "blackboard_dir": directory,
                        "current_frame": 31,
                        "update_id": "llm-policy",
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
            self.assertTrue(payload["accepted"], payload)
            self.assertEqual("llm", payload["provider_source"])
            self.assertEqual("llm-policy", payload["update"]["update_id"])
            self.assertEqual("llm", payload["update"]["vector"]["source"])
            self.assertEqual(
                "fake_llm_policy_modulation",
                payload["update"]["vector"]["tags"][0],
            )

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
        self.assertIn("micromachine-command-execution", page)

    def test_runtime_start_routes_micromachine_mode_to_launcher(self):
        class FakeMicroMachineLauncher:
            def __init__(self):
                self.started = []

            def start(self, blackboard_dir="", enemy_difficulty=7):
                self.started.append((blackboard_dir, enemy_difficulty))
                return {
                    "enabled": True,
                    "mode": "micromachine",
                    "status": "starting",
                    "blackboard_dir": blackboard_dir,
                    "enemy_difficulty": enemy_difficulty,
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
            {
                "mode": "micromachine",
                "blackboard_dir": "/tmp/voi-mm-runtime-test",
                "enemy_difficulty": 9,
            }
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
        self.assertEqual(
            launcher.started,
            [("/tmp/voi-mm-runtime-test", 9)],
        )
        self.assertEqual(document["enemy_difficulty"], 9)

        status, _content_type, payload = self.request(
            "GET",
            "/api/runtime/status?mode=micromachine&blackboard_dir=/tmp/voi-mm-runtime-test",
        )
        self.assertEqual(HTTPStatus.OK, HTTPStatus(status))
        document = json.loads(payload.decode("utf-8"))
        self.assertEqual(document["status"], "connected")
        self.assertEqual(document["telemetry_frame"], 42)

    def test_runtime_endpoints_strip_internal_unit_identity(self):
        def launcher_payload(status):
            return {
                "enabled": True,
                "status": status,
                "last_line": "actor_tag=7001 action=attack",
                "error": "target_unit_tags=[8001, 8002]",
                "nested": {"commanded_unit_tag": 9001},
                "tags": [
                    "public-strategy-tag",
                    9101,
                    ["nested-label", 9102],
                ],
                "strategic_tags": ["pressure", 9201, "9202"],
                "expected_tags": ["scouting-map-control", {"tag": 9301}],
            }

        class FakeLegacyLauncher:
            def configure(self, provider, api_key, model=""):
                return None

            def snapshot(self):
                return launcher_payload("connected")

            def start(self):
                return launcher_payload("starting")

        class FakeMicroMachineLauncher:
            def snapshot(self, blackboard_dir=""):
                return {
                    **launcher_payload("connected"),
                    "blackboard_dir": blackboard_dir,
                }

            def start(self, blackboard_dir="", enemy_difficulty=7):
                return {
                    **launcher_payload("starting"),
                    "blackboard_dir": blackboard_dir,
                    "enemy_difficulty": enemy_difficulty,
                }

        self.server._http.live_launcher = FakeLegacyLauncher()
        self.server._http.micromachine_launcher = FakeMicroMachineLauncher()
        self.server._http.auto_launch_live = True
        requests = (
            ("GET", "/api/live/status", None),
            ("GET", "/api/runtime/status?mode=legacy_commander", None),
            (
                "POST",
                "/api/runtime/start",
                {"mode": "legacy_commander"},
            ),
            (
                "GET",
                "/api/runtime/status?mode=micromachine&blackboard_dir=/tmp/mm",
                None,
            ),
            (
                "POST",
                "/api/runtime/start",
                {
                    "mode": "micromachine",
                    "blackboard_dir": "/tmp/mm",
                    "enemy_difficulty": 7,
                },
            ),
            (
                "POST",
                "/api/llm",
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "api_key": "unit-test-sensitive",
                },
            ),
        )

        for method, path, document in requests:
            with self.subTest(method=method, path=path):
                body = (
                    json.dumps(document).encode("utf-8")
                    if document is not None
                    else None
                )
                status, content_type, payload = self.request(
                    method,
                    path,
                    body=body,
                    headers=(
                        {"Content-Type": "application/json"}
                        if body is not None
                        else None
                    ),
                )
                self.assertIn(
                    HTTPStatus(status),
                    {HTTPStatus.OK, HTTPStatus.ACCEPTED},
                )
                self.assertIn("application/json", content_type)
                response = json.loads(payload.decode("utf-8"))
                serialized = json.dumps(response, sort_keys=True)
                runtime_payload = response.get("live_start", response)

                self.assertNotIn("actor_tag", serialized)
                self.assertNotIn("target_unit_tags", serialized)
                self.assertNotIn("commanded_unit_tag", serialized)
                self.assertNotIn("7001", serialized)
                self.assertNotIn("8001", serialized)
                self.assertNotIn("8002", serialized)
                self.assertNotIn("9001", serialized)
                self.assertNotIn("9101", serialized)
                self.assertNotIn("9102", serialized)
                self.assertNotIn("9201", serialized)
                self.assertNotIn("9202", serialized)
                self.assertNotIn("9301", serialized)
                self.assertEqual(
                    ["public-strategy-tag"],
                    runtime_payload["tags"],
                )
                self.assertEqual(
                    ["pressure"],
                    runtime_payload["strategic_tags"],
                )
                self.assertEqual(
                    ["scouting-map-control"],
                    runtime_payload["expected_tags"],
                )
                self.assertIn("action=attack", runtime_payload["last_line"])

    def test_runtime_start_rejects_invalid_micromachine_enemy_difficulty(self):
        for difficulty in (0, 11, 7.5, True, "7"):
            with self.subTest(difficulty=difficulty):
                body = json.dumps(
                    {
                        "mode": "micromachine",
                        "blackboard_dir": "/tmp/voi-mm-runtime-test",
                        "enemy_difficulty": difficulty,
                    }
                ).encode("utf-8")
                status, content_type, payload = self.request(
                    "POST",
                    "/api/runtime/start",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(HTTPStatus.BAD_REQUEST, HTTPStatus(status))
                self.assertIn("application/json", content_type)
                document = json.loads(payload.decode("utf-8"))
                self.assertFalse(document["accepted"], document)
                self.assertIn("1..10", document["error"])

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

    def test_micromachine_smoke_cli_rejects_enemy_difficulty_outside_1_to_10(self):
        script = os.path.join(
            web_gui._REPO_ROOT,  # noqa: SLF001 - repo-local smoke CLI contract.
            "integrations/micromachine/scripts/smoke_macos_local.sh",
        )
        for value in ("0", "11", "7.5", "hard"):
            with self.subTest(value=value):
                result = subprocess.run(
                    ["bash", script, "--enemy-difficulty", value],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2, result)
                self.assertIn("integer from 1 to 10", result.stderr)

    def test_micromachine_launcher_starts_fresh_tactical_session(self):
        class FakeProcess:
            pid = 12345
            returncode = None
            stdout = []

            def poll(self):
                return self.returncode

            def wait(self):
                self.returncode = 0
                return 0

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                web_gui.subprocess,
                "Popen",
                return_value=FakeProcess(),
            ) as popen:
                launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
                launcher.start(directory, enemy_difficulty=9)

            argv = popen.call_args.args[0]
            env = popen.call_args.kwargs["env"]
            self.assertIn("--live-hold", argv)
            self.assertIn("--fresh-live-session", argv)
            self.assertEqual(argv[argv.index("--enemy-difficulty") + 1], "9")
            self.assertEqual(env["SMOKE_ENEMY_DIFFICULTY"], "9")
            self.assertRegex(
                env["VOI_MICROMACHINE_RUNTIME_INSTANCE_ID"],
                r"^[a-f0-9]{32}$",
            )
            self.assertEqual(
                env["VOI_MICROMACHINE_RUNTIME_INSTANCE_ID"],
                launcher._runtime_instance_id,  # noqa: SLF001
            )
            self.assertLess(
                argv.index("--fresh-live-session"),
                argv.index("--blackboard-dir"),
            )

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
            self.assertIn("already running", payload["error"])

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
            self.assertFalse(payload["runtime_attached"])
            self.assertFalse(payload["telemetry_current_for_process"])
            self.assertTrue(payload["telemetry_stale_or_detached"])

    def test_micromachine_launcher_rejects_prelaunch_tolerance_window(self):
        class FakeRunningProcess:
            pid = 12345
            returncode = None
            stdout = []

            def poll(self):
                return None

            def wait(self):
                return 0

        with tempfile.TemporaryDirectory() as directory:
            telemetry_path = os.path.join(directory, "latest_telemetry.json")
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": 99,
                    },
                    handle,
                )
            prelaunch_time = time.time() - 0.5
            os.utime(telemetry_path, (prelaunch_time, prelaunch_time))
            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
            with (
                mock.patch.object(
                    web_gui.subprocess,
                    "Popen",
                    return_value=FakeRunningProcess(),
                ),
                mock.patch.object(
                    web_gui.threading.Thread,
                    "start",
                    return_value=None,
                ),
            ):
                stale = launcher.start(directory)

            self.assertTrue(stale["runtime_attached"])
            self.assertFalse(stale["telemetry_present"])
            self.assertFalse(stale["telemetry_current_for_process"])
            self.assertNotEqual("connected", stale["status"])

            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": 100,
                        "runtime_instance_id": launcher._runtime_instance_id,  # noqa: SLF001
                    },
                    handle,
                )
            fresh_ns = launcher._launch_started_at_ns + 1_000_000_000  # noqa: SLF001
            os.utime(telemetry_path, ns=(fresh_ns, fresh_ns))

            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=fresh_ns,
            ):
                fresh = launcher.snapshot(directory)

            self.assertTrue(fresh["telemetry_current_for_process"])
            self.assertEqual(100, fresh["telemetry_frame"])
            self.assertEqual("connected", fresh["status"])

    def test_micromachine_launcher_marks_postlaunch_telemetry_stale_until_rewritten(
        self,
    ):
        class FakeRunningProcess:
            pid = 12345
            returncode = None
            stdout = []

            def poll(self):
                return None

            def wait(self):
                return 0

        launch_ns = 1_700_000_000_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            telemetry_path = os.path.join(directory, "latest_telemetry.json")
            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
            with (
                mock.patch.object(
                    web_gui.subprocess,
                    "Popen",
                    return_value=FakeRunningProcess(),
                ),
                mock.patch.object(
                    web_gui.threading.Thread,
                    "start",
                    return_value=None,
                ),
                mock.patch.object(
                    web_gui.time,
                    "time_ns",
                    return_value=launch_ns,
                ),
            ):
                launcher.start(directory)

            def write_telemetry(frame, mtime_ns):
                with open(telemetry_path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                            "frame": frame,
                            "runtime_instance_id": launcher._runtime_instance_id,  # noqa: SLF001
                        },
                        handle,
                    )
                os.utime(telemetry_path, ns=(mtime_ns, mtime_ns))

            first_write_ns = launch_ns + 1_000_000_000
            write_telemetry(100, first_write_ns)
            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=first_write_ns + 15_000_000_000,
            ):
                boundary = launcher.snapshot(directory)

            self.assertTrue(boundary["telemetry_current_for_process"])
            self.assertFalse(boundary["telemetry_stale_or_detached"])
            self.assertEqual("connected", boundary["status"])

            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=first_write_ns + 16_000_000_000,
            ):
                stale = launcher.snapshot(directory)

            self.assertTrue(stale["runtime_attached"])
            self.assertTrue(stale["telemetry_present"])
            self.assertEqual(100, stale["telemetry_frame"])
            self.assertFalse(stale["telemetry_current_for_process"])
            self.assertTrue(stale["telemetry_stale_or_detached"])
            self.assertNotEqual("connected", stale["status"])

            rewrite_ns = first_write_ns + 16_000_000_000
            write_telemetry(100, rewrite_ns)
            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=rewrite_ns,
            ):
                rewritten = launcher.snapshot(directory)

            self.assertTrue(rewritten["telemetry_current_for_process"])
            self.assertFalse(rewritten["telemetry_stale_or_detached"])
            self.assertEqual("connected", rewritten["status"])

            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=rewrite_ns - 1,
            ):
                future_dated = launcher.snapshot(directory)

            self.assertTrue(future_dated["telemetry_present"])
            self.assertFalse(future_dated["telemetry_current_for_process"])
            self.assertTrue(future_dated["telemetry_stale_or_detached"])
            self.assertNotEqual("connected", future_dated["status"])

            stale_again_ns = rewrite_ns + 16_000_000_000
            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=stale_again_ns,
            ):
                stale_again = launcher.snapshot(directory)

            self.assertTrue(stale_again["telemetry_present"])
            self.assertFalse(stale_again["telemetry_current_for_process"])
            self.assertTrue(stale_again["telemetry_stale_or_detached"])
            self.assertNotEqual("connected", stale_again["status"])

            write_telemetry(101, stale_again_ns)
            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=stale_again_ns,
            ):
                advanced = launcher.snapshot(directory)

            self.assertEqual(101, advanced["telemetry_frame"])
            self.assertTrue(advanced["telemetry_current_for_process"])
            self.assertFalse(advanced["telemetry_stale_or_detached"])
            self.assertEqual("connected", advanced["status"])

    def test_micromachine_launcher_rejects_fresh_other_runtime_telemetry(self):
        class FakeRunningProcess:
            pid = 12345
            returncode = None
            stdout = []

            def poll(self):
                return None

            def wait(self):
                return 0

        launch_ns = 1_700_000_000_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            telemetry_path = os.path.join(directory, "latest_telemetry.json")
            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
            with (
                mock.patch.object(
                    web_gui.subprocess,
                    "Popen",
                    return_value=FakeRunningProcess(),
                ),
                mock.patch.object(
                    web_gui.threading.Thread,
                    "start",
                    return_value=None,
                ),
                mock.patch.object(
                    web_gui.time,
                    "time_ns",
                    return_value=launch_ns,
                ),
            ):
                launcher.start(directory)

            write_ns = launch_ns + 1_000_000_000
            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": 100,
                        "runtime_instance_id": "0" * 32,
                    },
                    handle,
                )
            os.utime(telemetry_path, ns=(write_ns, write_ns))

            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=write_ns,
            ):
                other_runtime = launcher.snapshot(directory)

            self.assertTrue(other_runtime["telemetry_present"])
            self.assertFalse(other_runtime["telemetry_current_for_process"])
            self.assertTrue(other_runtime["telemetry_stale_or_detached"])
            self.assertNotEqual("connected", other_runtime["status"])

            with open(telemetry_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": 100,
                        "runtime_instance_id": launcher._runtime_instance_id,  # noqa: SLF001
                    },
                    handle,
                )
            os.utime(telemetry_path, ns=(write_ns, write_ns))

            with mock.patch.object(
                web_gui.time,
                "time_ns",
                return_value=write_ns,
            ):
                current_runtime = launcher.snapshot(directory)

            self.assertTrue(current_runtime["telemetry_current_for_process"])
            self.assertFalse(current_runtime["telemetry_stale_or_detached"])
            self.assertEqual("connected", current_runtime["status"])

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

    def test_web_event_journal_is_monotonic_bounded_and_redacted(self):
        journal = web_gui._WebEventJournal(retention=2)
        secret = "sk-" + "journal-secret-value-123456789"

        first = journal.publish(
            "command_received",
            {"command_text": "first", "provider_error": secret},
        )
        second = journal.publish(
            "command_received",
            {"command_text": "second"},
        )
        third = journal.publish(
            "command_received",
            {"command_text": "third"},
        )

        self.assertEqual(first["event_seq"], 1)
        self.assertEqual(second["event_seq"], 2)
        self.assertEqual(third["event_seq"], 3)
        self.assertEqual(journal.oldest_seq, 2)
        self.assertFalse(journal.replay_available(0))
        self.assertTrue(journal.replay_available(1))
        self.assertFalse(journal.replay_available(4))
        self.assertEqual(
            [event["event_seq"] for event in journal.events_after(1)],
            [2, 3],
        )
        self.assertNotIn(
            secret,
            json.dumps(first, ensure_ascii=False),
        )

    def test_operation_timeline_dedupes_unchanged_snapshots_and_boolean_transitions(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        payload = semantic_operation_payload(
            movement=True,
            engagement=True,
            target_reached=True,
        )

        first = reducer.observe(
            payload,
            blackboard_scope_id="scope-semantic-test",
        )
        second = reducer.observe(
            deepcopy(payload),
            blackboard_scope_id="scope-semantic-test",
        )

        self.assertEqual(
            first["operation_event_latest_seq"],
            second["operation_event_latest_seq"],
        )
        kinds = [
            event["kind"]
            for event in second["operations"][0]["semantic_timeline"]
        ]
        for kind in (
            "received",
            "planned",
            "assigned",
            "submitted",
            "movement_observed",
            "engagement_observed",
            "target_reached",
        ):
            self.assertEqual(kinds.count(kind), 1, kind)
        self.assertNotIn("completed", kinds)

    def test_operation_timeline_requires_action_stage_for_submission(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        payload = semantic_operation_payload(
            execution_state="action_issued",
        )
        payload["operations"][0]["intervention"]["command_execution"][
            "stages"
        ] = [
            stage
            for stage in payload["operations"][0]["intervention"][
                "command_execution"
            ]["stages"]
            if stage["name"] != "action_issued"
        ]

        result = reducer.observe(
            payload,
            blackboard_scope_id="scope-action-stage-required",
        )

        self.assertNotIn(
            "submitted",
            [event["kind"] for event in result["operation_events"]],
        )

    def test_operation_timeline_requires_matching_execution_identity_for_submission(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        payload = semantic_operation_payload(
            operation_id="alpha-operation",
            generation=2,
            execution_state="action_issued",
        )
        execution = payload["operations"][0]["intervention"][
            "command_execution"
        ]
        execution.update(
            {
                "command_id": "update-beta-operation-1",
                "operation_id": "beta-operation",
                "operation_generation": 1,
            }
        )

        result = reducer.observe(
            payload,
            blackboard_scope_id="scope-execution-identity-required",
        )

        kinds = [event["kind"] for event in result["operation_events"]]
        self.assertNotIn("submitted", kinds)
        self.assertNotIn(
            "Matching-generation SC2 action submitted.",
            [event["summary"] for event in result["operation_events"]],
        )

    def test_operation_timeline_non_authoritative_empty_snapshot_preserves_registry(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-detached-registry"
        attached = semantic_operation_payload(
            operation_id="recon-alpha",
            frame=200,
        )
        attached["operation_registry_authoritative"] = True
        reducer.observe(attached, blackboard_scope_id=scope_id)
        self.assertEqual(1, len(reducer._accepted_operations))

        detached = reducer.observe(
            {
                "blackboard_scope_id": scope_id,
                "operation_registry_authoritative": False,
                "operations": [],
            },
            blackboard_scope_id=scope_id,
        )

        self.assertEqual([], detached["operations"])
        self.assertEqual(1, len(reducer._accepted_operations))

        reducer.observe(
            {
                "blackboard_scope_id": scope_id,
                "operation_registry_authoritative": True,
                "battlefield_overview": attached["battlefield_overview"],
                "operations": [],
            },
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(0, len(reducer._accepted_operations))

    def test_operation_timeline_non_authoritative_epoch_cannot_replace_current_epoch(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-detached-foreign-epoch"
        current_epoch = 1700000000000
        attached = semantic_operation_payload(
            operation_id="recon-alpha",
            frame=200,
            session_epoch=current_epoch,
        )
        attached["operation_registry_authoritative"] = True
        accepted = reducer.observe(
            attached,
            blackboard_scope_id=scope_id,
        )

        detached = semantic_operation_payload(
            operation_id="foreign-operation",
            frame=1,
            session_epoch=current_epoch + 1,
        )
        detached["operation_registry_authoritative"] = False
        detached["operations"] = []
        restored = reducer.observe(
            detached,
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(str(current_epoch), reducer._scope_epochs[scope_id])
        self.assertEqual(accepted["operations"], restored["operations"])
        self.assertEqual(
            accepted["battlefield_overview"],
            restored["battlefield_overview"],
        )
        self.assertNotIn(
            str(current_epoch),
            reducer._retired_scope_epochs.get(scope_id, ()),
        )

        resumed = semantic_operation_payload(
            operation_id="recon-alpha",
            frame=201,
            session_epoch=current_epoch,
            movement=True,
        )
        resumed["operation_registry_authoritative"] = True
        resumed_result = reducer.observe(
            resumed,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(201, resumed_result["operations"][0]["telemetry_frame"])
        self.assertEqual(str(current_epoch), reducer._scope_epochs[scope_id])

    def test_operation_timeline_rejects_generation_frame_and_same_frame_conflicts(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-semantic-test"
        current = semantic_operation_payload(generation=2, frame=200)
        current["operations"][0]["command_text"] = "accepted command"
        accepted = reducer.observe(
            current,
            blackboard_scope_id=scope_id,
        )
        baseline_seq = accepted["operation_event_latest_seq"]
        accepted_overview = deepcopy(accepted["battlefield_overview"])
        accepted_summary = deepcopy(accepted["operation_summary"])

        lower_generation = semantic_operation_payload(
            generation=1,
            frame=300,
            movement=True,
            terminal=True,
        )
        regressing_frame = semantic_operation_payload(
            generation=2,
            frame=199,
            movement=True,
            terminal=True,
        )
        conflicting_same_frame = semantic_operation_payload(
            generation=2,
            frame=200,
            movement=True,
            terminal=True,
        )
        conflicting_same_frame["operations"][0][
            "command_text"
        ] = "rejected same-frame command"
        for stale in (
            lower_generation,
            regressing_frame,
            conflicting_same_frame,
        ):
            stale["battlefield_overview"]["eligible_combat_count"] = 999
            stale["operation_summary"] = {
                "total": 999,
                "active": 0,
                "scouting": 0,
                "attacking": 0,
                "blocked": 0,
                "completed": 999,
            }
            result = reducer.observe(
                stale,
                blackboard_scope_id=scope_id,
            )
            self.assertEqual(
                baseline_seq,
                result["operation_event_latest_seq"],
            )
            self.assertNotIn(
                "completed",
                [
                    event["kind"]
                    for event in result["operation_events"]
                ],
            )
            self.assertEqual(
                "accepted command",
                result["operations"][0]["command_text"],
            )
            self.assertFalse(
                result["operations"][0]["battlefield_operation"][
                    "operation_completion"
                ]["terminal"]
            )
            self.assertEqual(
                accepted_overview,
                result["battlefield_overview"],
            )
            self.assertEqual(
                accepted_summary,
                result["operation_summary"],
            )
            self.assertEqual(
                accepted_overview,
                reducer._scope_battlefield_overviews[scope_id],
            )

        advanced = reducer.observe(
            semantic_operation_payload(
                generation=2,
                frame=201,
                movement=True,
                terminal=True,
            ),
            blackboard_scope_id=scope_id,
        )
        self.assertIn(
            "completed",
            [event["kind"] for event in advanced["operation_events"]],
        )

    def test_operation_timeline_session_epoch_resets_generation_and_retained_state(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        first = reducer.observe(
            semantic_operation_payload(
                generation=3,
                frame=300,
                session_epoch=111,
            ),
            blackboard_scope_id="scope-semantic-test",
        )

        reset = reducer.observe(
            semantic_operation_payload(
                generation=1,
                frame=10,
                session_epoch=222,
            ),
            blackboard_scope_id="scope-semantic-test",
        )

        self.assertGreater(
            reset["operation_event_latest_seq"],
            first["operation_event_latest_seq"],
        )
        self.assertTrue(reset["operation_events"])
        self.assertEqual(
            {"222"},
            {
                event["session_epoch"]
                for event in reset["operation_events"]
            },
        )
        self.assertEqual(
            "222",
            reducer._scope_epochs["scope-semantic-test"],
        )
        self.assertTrue(
            any(key[1] == "222" for key in reducer._states),
        )
        self.assertLessEqual(
            len(reducer._states),
            reducer._GLOBAL_OPERATION_RETENTION,
        )

    def test_conflicting_new_epoch_snapshot_does_not_reset_accepted_scope(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-atomic-new-epoch"
        current = reducer.observe(
            semantic_operation_payload(
                frame=300,
                session_epoch=111,
            ),
            blackboard_scope_id=scope_id,
        )
        conflicting = semantic_operation_payload(
            frame=10,
            session_epoch=222,
        )
        duplicate = deepcopy(conflicting["operations"][0])
        duplicate["command_text"] = "same-frame conflicting command"
        duplicate["battlefield_operation"]["operation_completion"].update(
            {
                "terminal": True,
                "state": "completed",
                "reason": "conflicting_terminal_state",
                "frame": 10,
            }
        )
        conflicting["operations"].append(duplicate)

        rejected = reducer.observe(
            conflicting,
            blackboard_scope_id=scope_id,
        )

        self.assertEqual("111", reducer._scope_epochs[scope_id])
        self.assertEqual(
            current["battlefield_overview"],
            rejected["battlefield_overview"],
        )
        self.assertEqual(
            current["operations"],
            rejected["operations"],
        )
        self.assertEqual(
            current["operation_event_latest_seq"],
            rejected["operation_event_latest_seq"],
        )

    def test_operation_timeline_rejects_delayed_retired_session_epoch(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-retired-session"
        reducer.observe(
            semantic_operation_payload(
                frame=111,
                session_epoch=111,
            ),
            blackboard_scope_id=scope_id,
        )
        current = reducer.observe(
            semantic_operation_payload(
                frame=10,
                session_epoch=222,
            ),
            blackboard_scope_id=scope_id,
        )
        current_seq = current["operation_event_latest_seq"]

        delayed = reducer.observe(
            semantic_operation_payload(
                frame=999,
                session_epoch=111,
                movement=True,
                engagement=True,
                target_reached=True,
                terminal=True,
            ),
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(current_seq, delayed["operation_event_latest_seq"])
        self.assertEqual(
            222,
            delayed["battlefield_overview"]["identity"]["session_epoch"],
        )
        self.assertEqual(
            10,
            delayed["operations"][0]["telemetry_frame"],
        )
        self.assertNotIn(
            "completed",
            [event["kind"] for event in delayed["operation_events"]],
        )

    def test_operation_timeline_standing_operation_is_not_completed_by_activity(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        result = reducer.observe(
            semantic_operation_payload(
                standing=True,
                movement=True,
                engagement=True,
                target_reached=True,
                terminal=False,
            ),
            blackboard_scope_id="scope-semantic-test",
        )

        kinds = [
            event["kind"]
            for event in result["operations"][0]["semantic_timeline"]
        ]
        self.assertIn("movement_observed", kinds)
        self.assertIn("engagement_observed", kinds)
        self.assertIn("target_reached", kinds)
        self.assertNotIn("completed", kinds)

    def test_operation_timeline_requires_matching_canonical_completion_projection(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        cases = []

        missing_projection = semantic_operation_payload(
            execution_state="completed",
            terminal=True,
        )
        missing_projection["operations"][0].pop(
            "battlefield_operation",
            None,
        )
        cases.append(missing_projection)

        mismatched_id = semantic_operation_payload(
            execution_state="completed",
            terminal=True,
        )
        mismatched_id["operations"][0]["battlefield_operation"][
            "operation_id"
        ] = "different-operation"
        cases.append(mismatched_id)

        mismatched_generation = semantic_operation_payload(
            execution_state="completed",
            terminal=True,
        )
        mismatched_generation["operations"][0]["battlefield_operation"][
            "generation"
        ] = 99
        cases.append(mismatched_generation)

        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                result = reducer.observe(
                    payload,
                    blackboard_scope_id=f"canonical-completion-{index}",
                )
                kinds = [
                    event["kind"]
                    for event in result["operation_events"]
                ]
                self.assertTrue(
                    {
                        "movement_observed",
                        "engagement_observed",
                        "target_reached",
                        "completed",
                    }.isdisjoint(kinds),
                )

    def test_operation_timeline_requires_matching_projection_identity_for_observation(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        payload = semantic_operation_payload(
            movement=True,
            engagement=True,
            target_reached=True,
            terminal=True,
        )
        payload["operations"][0]["battlefield_operation"]["identity"][
            "operation_id"
        ] = "different-operation"

        result = reducer.observe(
            payload,
            blackboard_scope_id="canonical-identity-mismatch",
        )

        kinds = [event["kind"] for event in result["operation_events"]]
        self.assertTrue(
            {
                "movement_observed",
                "engagement_observed",
                "target_reached",
                "completed",
            }.isdisjoint(kinds),
        )

    def test_operation_timeline_rejects_stale_accepted_and_transfer_edits(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-requested-generation"
        latest = reducer.observe(
            semantic_operation_payload(
                requested_generation=4,
                operation_edit={
                    "action": "reinforce",
                    "resolution": "applied",
                    "transferred_in_count": 2,
                },
            ),
            blackboard_scope_id=scope_id,
        )
        baseline_seq = latest["operation_event_latest_seq"]

        stale = reducer.observe(
            semantic_operation_payload(
                requested_generation=3,
                frame=101,
                operation_edit={
                    "action": "transfer",
                    "resolution": "transferred",
                    "transferred_in_count": 3,
                    "transferred_out_count": 1,
                },
            ),
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(
            baseline_seq,
            stale["operation_event_latest_seq"],
        )
        self.assertEqual(
            4,
            reducer._requested_generation_high_water[
                (scope_id, "1700000000000", "flank-alpha")
            ],
        )
        stale_kinds = [
            event["kind"]
            for event in stale["operations"][0]["semantic_timeline"]
        ]
        self.assertNotIn("ownership_released", stale_kinds)
        self.assertEqual(
            latest["operations"][0]["operation_edit"],
            stale["operations"][0]["operation_edit"],
        )

    def test_rejected_snapshot_does_not_poison_requested_generation_high_water(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-requested-generation-poison"
        reducer.observe(
            semantic_operation_payload(
                requested_generation=4,
                frame=100,
            ),
            blackboard_scope_id=scope_id,
        )
        rejected = semantic_operation_payload(
            requested_generation=99,
            frame=100,
            terminal=True,
        )
        reducer.observe(
            rejected,
            blackboard_scope_id=scope_id,
        )
        family_key = (
            scope_id,
            "1700000000000",
            "flank-alpha",
        )
        self.assertEqual(
            4,
            reducer._requested_generation_high_water[family_key],
        )

        accepted = reducer.observe(
            semantic_operation_payload(
                requested_generation=5,
                frame=101,
            ),
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            5,
            reducer._requested_generation_high_water[family_key],
        )
        self.assertEqual(
            5,
            accepted["operations"][0][
                "requested_operation_generation"
            ],
        )

    def test_operation_timeline_milestones_do_not_reemit_after_token_eviction(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-permanent-milestones"
        reducer.observe(
            semantic_operation_payload(movement=True),
            blackboard_scope_id=scope_id,
        )
        for frame in range(
            101,
            101 + reducer._PER_OPERATION_TOKEN_RETENTION + 8,
        ):
            reducer.observe(
                semantic_operation_payload(
                    frame=frame,
                    blocker=f"dynamic-wait-{frame}",
                    launch_decision="wait",
                    movement=True,
                ),
                blackboard_scope_id=scope_id,
            )

        result = reducer.observe(
            semantic_operation_payload(
                frame=200,
                movement=True,
            ),
            blackboard_scope_id=scope_id,
        )
        events = result["operation_events"]
        for kind in (
            "received",
            "planned",
            "assigned",
            "submitted",
            "movement_observed",
        ):
            self.assertEqual(
                1,
                sum(event["kind"] == kind for event in events),
                kind,
            )
        state = next(iter(reducer._states.values()))
        self.assertLessEqual(
            len(state["milestones"]),
            len(reducer._PERMANENT_MILESTONE_KINDS),
        )

    def test_operation_timeline_supports_concurrent_operations_and_bounded_retention(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        attack = semantic_operation_payload(
            operation_id="attack-alpha",
            movement=True,
        )
        scout = semantic_operation_payload(
            operation_id="scout-bravo",
            owner_count=1,
            required_count=1,
            movement=True,
        )
        concurrent = deepcopy(attack)
        concurrent["operations"].extend(scout["operations"])
        concurrent["battlefield_overview"]["operation_ownership"].extend(
            scout["battlefield_overview"]["operation_ownership"]
        )

        result = reducer.observe(
            concurrent,
            blackboard_scope_id="scope-semantic-test",
        )
        self.assertEqual(
            {"attack-alpha", "scout-bravo"},
            {
                event["operation_id"]
                for event in result["operation_events"]
            },
        )

        for frame in range(101, 150):
            result = reducer.observe(
                semantic_operation_payload(
                    operation_id="attack-alpha",
                    frame=frame,
                    blocker=f"wait-{frame}",
                    launch_decision="wait",
                ),
                blackboard_scope_id="scope-semantic-test",
            )
        attack_timeline = result["operations"][0]["semantic_timeline"]
        self.assertLessEqual(
            len(attack_timeline),
            reducer._PER_OPERATION_RETENTION,
        )

        for generation in range(2, 90):
            result = reducer.observe(
                semantic_operation_payload(
                    operation_id="retention-operation",
                    generation=generation,
                    frame=200 + generation,
                    blocker=f"generation-wait-{generation}",
                    launch_decision="wait",
                ),
                blackboard_scope_id="scope-semantic-test",
            )
        self.assertEqual(
            reducer._PER_SCOPE_RETENTION,
            len(result["operation_events"]),
        )
        self.assertLessEqual(
            len(reducer._states),
            reducer._PER_SCOPE_OPERATION_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._generation_high_water),
            reducer._PER_SCOPE_OPERATION_RETENTION,
        )
        self.assertTrue(
            all(
                len(state["tokens"])
                <= reducer._PER_OPERATION_TOKEN_RETENTION
                for state in reducer._states.values()
            )
        )

    def test_authoritative_registry_above_active_retention_does_not_reemit(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-authoritative-retention-plus-one"
        payload = semantic_operation_payload(
            operation_id="operation-0",
            frame=100,
        )
        payload["operations"] = []
        payload["battlefield_overview"]["operation_ownership"] = []
        for index in range(
            reducer._PER_SCOPE_OPERATION_RETENTION + 1
        ):
            operation_payload = semantic_operation_payload(
                operation_id=f"operation-{index}",
                frame=100 + index,
            )
            payload["operations"].extend(
                operation_payload["operations"]
            )
            payload["battlefield_overview"][
                "operation_ownership"
            ].extend(
                operation_payload["battlefield_overview"][
                    "operation_ownership"
                ]
            )
        payload["operation_registry_authoritative"] = True

        first = reducer.observe(
            payload,
            blackboard_scope_id=scope_id,
        )
        second = reducer.observe(
            deepcopy(payload),
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(
            reducer._PER_SCOPE_OPERATION_RETENTION + 1,
            len(first["operations"]),
        )
        self.assertEqual(
            first["operation_event_latest_seq"],
            second["operation_event_latest_seq"],
        )
        self.assertEqual(
            first["operation_events"],
            second["operation_events"],
        )
        self.assertLessEqual(
            len(reducer._accepted_operations),
            reducer._PER_SCOPE_OPERATION_RETENTION,
        )
        self.assertGreater(
            len(reducer._generation_high_water),
            reducer._PER_SCOPE_OPERATION_RETENTION,
        )

    def test_operation_timeline_bounds_scope_and_operation_state(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        for scope_number in range(reducer._SCOPE_RETENTION + 3):
            scope_id = f"scope-{scope_number}"
            for operation_number in range(
                reducer._PER_SCOPE_OPERATION_RETENTION + 3
            ):
                reducer.observe(
                    semantic_operation_payload(
                        operation_id=f"operation-{operation_number}",
                        frame=100 + operation_number,
                        session_epoch=scope_number + 1,
                    ),
                    blackboard_scope_id=scope_id,
                )

        self.assertLessEqual(
            len(reducer._scope_order),
            reducer._SCOPE_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._scope_events),
            reducer._SCOPE_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._scope_epochs),
            reducer._SCOPE_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._scope_families),
            reducer._SCOPE_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._states),
            reducer._GLOBAL_OPERATION_HISTORY_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._generation_high_water),
            reducer._GLOBAL_OPERATION_HISTORY_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._family_order),
            reducer._GLOBAL_OPERATION_HISTORY_RETENTION,
        )
        self.assertLessEqual(
            len(reducer._scope_epoch_history),
            reducer._SCOPE_EPOCH_HISTORY_RETENTION,
        )

    def test_operation_timeline_scope_lru_revisit_emits_only_new_transition(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        original_scope = "scope-lru-original"
        first = reducer.observe(
            semantic_operation_payload(frame=100),
            blackboard_scope_id=original_scope,
        )
        first_kinds = {
            event["kind"] for event in first["operation_events"]
        }
        self.assertIn("submitted", first_kinds)

        for index in range(reducer._SCOPE_RETENTION):
            reducer.observe(
                semantic_operation_payload(
                    operation_id=f"other-operation-{index}",
                    frame=200 + index,
                    session_epoch=2000 + index,
                ),
                blackboard_scope_id=f"scope-lru-other-{index}",
            )
        self.assertNotIn(original_scope, reducer._scope_epochs)

        replayed = reducer.observe(
            semantic_operation_payload(frame=100),
            blackboard_scope_id=original_scope,
        )
        self.assertEqual([], replayed["operation_events"])

        advanced = reducer.observe(
            semantic_operation_payload(
                frame=101,
                movement=True,
            ),
            blackboard_scope_id=original_scope,
        )
        self.assertEqual(
            ["movement_observed"],
            [event["kind"] for event in advanced["operation_events"]],
        )

    def test_operation_timeline_emits_edit_and_ownership_identity(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        result = reducer.observe(
            semantic_operation_payload(
                generation=1,
                requested_generation=2,
                operation_edit={
                    "action": "transfer",
                    "resolution": "transferred",
                    "transferred_in_count": 2,
                    "transferred_out_count": 1,
                },
            ),
            blackboard_scope_id="scope-semantic-test",
        )

        events = result["operations"][0]["semantic_timeline"]
        kinds = {event["kind"] for event in events}
        self.assertTrue(
            {
                "edit_applied",
                "ownership_transferred",
                "ownership_released",
            }.issubset(kinds)
        )
        edit_event = next(
            event for event in events if event["kind"] == "edit_applied"
        )
        self.assertEqual(1, edit_event["generation"])
        self.assertEqual(2, edit_event["requested_generation"])
        self.assertEqual("update-flank-alpha-2", edit_event["update_id"])

    def test_operation_timeline_ignores_generation_zero_transport_records(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        payload = semantic_operation_payload(generation=1)
        payload["operations"][0]["operation_generation"] = 0
        payload["operations"][0]["battlefield_operation"]["generation"] = 0

        result = reducer.observe(
            payload,
            blackboard_scope_id="scope-semantic-test",
        )

        self.assertEqual([], result["operation_events"])
        self.assertEqual(
            [],
            result["operations"][0]["semantic_timeline"],
        )

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

    def test_micromachine_emergency_supersedes_inflight_publish_and_runs_next(self):
        started = threading.Event()
        release = threading.Event()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=BlockingPolicyModulationLLMControl(
                started=started,
                release=release,
            ),
        )
        bridge.start()
        self.addCleanup(bridge.stop)

        with tempfile.TemporaryDirectory() as directory:
            first = bridge.submit_micromachine_modulation_background(
                "탱크로 수비해",
                blackboard_dir=directory,
                current_frame=10,
                update_id="slow-normal",
            )
            self.assertEqual("queued", first["status"])
            self.assertTrue(started.wait(1))

            emergency = bridge.submit_micromachine_modulation_background(
                "긴급 즉시 후퇴",
                blackboard_dir=directory,
                provider_output={
                    "goal": "긴급 즉시 후퇴",
                    "override_level": "emergency",
                    "command_layer": "emergency",
                    "ttl_seconds": 45,
                    "emergency": {
                        "cancel_attacks": True,
                        "force_retreat": True,
                    },
                },
                current_frame=11,
                update_id="urgent-retreat",
            )
            self.assertEqual("queued", emergency["status"])

            deadline = time.monotonic() + 3
            latest = {}
            while time.monotonic() < deadline:
                path = os.path.join(directory, "latest_modulation.json")
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as handle:
                        latest = json.load(handle)
                    if latest.get("update_id") == "urgent-retreat":
                        break
                time.sleep(0.02)

            self.assertEqual("urgent-retreat", latest.get("update_id"))
            self.assertFalse(
                release.is_set(),
                "emergency waited for the blocked normal LLM request",
            )
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with bridge._micromachine_request_lock:
                    pending = "slow-normal" in bridge._micromachine_requests
                if not pending:
                    break
                time.sleep(0.02)
            archive_path = os.path.join(directory, "modulation_updates.jsonl")
            with open(archive_path, encoding="utf-8") as handle:
                archive_ids = [
                    json.loads(line)["update_id"]
                    for line in handle
                    if line.strip()
                ]
            self.assertEqual(["urgent-retreat"], archive_ids)

            status = bridge.micromachine_status(blackboard_dir=directory)
            stream = {
                item.get("compile_result", {}).get("update_id"): item
                for item in status["modulation_results"]
            }
            self.assertEqual("superseded", stream["slow-normal"]["status"])
            self.assertEqual("published", stream["urgent-retreat"]["status"])

    def test_latest_compile_result_preserves_request_acceptance_order(self):
        normal_write_ready = threading.Event()
        release_normal_write = threading.Event()
        self.addCleanup(release_normal_write.set)
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        original_write = web_gui._write_micromachine_compile_result

        def delay_normal_result(blackboard_dir, payload):
            if payload.get("update_id") == "normal-first":
                normal_write_ready.set()
                if not release_normal_write.wait(2):
                    raise TimeoutError("normal result persistence was not released")
            return original_write(blackboard_dir, payload)

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                web_gui,
                "_write_micromachine_compile_result",
                side_effect=delay_normal_result,
            ),
        ):
            bridge.submit_micromachine_modulation_background(
                "메인 병력으로 압박해",
                blackboard_dir=directory,
                provider_output={
                    "goal": "normal pressure",
                    "override_level": "directive",
                    "command_layer": "operation",
                    "ttl_seconds": 120,
                    "combat": {"aggression": 0.7},
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "task_id": "normal-pressure",
                        "min_units": 1,
                        "allow_partial": True,
                    },
                },
                current_frame=10,
                update_id="normal-first",
            )
            self.assertTrue(
                normal_write_ready.wait(1),
                "normal result did not reach delayed post-publish persistence",
            )

            bridge.submit_micromachine_modulation_background(
                "긴급 즉시 후퇴",
                blackboard_dir=directory,
                provider_output={
                    "goal": "emergency retreat",
                    "override_level": "emergency",
                    "command_layer": "emergency",
                    "ttl_seconds": 45,
                    "emergency": {
                        "cancel_attacks": True,
                        "force_retreat": True,
                    },
                },
                current_frame=11,
                update_id="emergency-second",
            )

            deadline = time.monotonic() + 2
            latest_result = {}
            while time.monotonic() < deadline:
                latest_result = (
                    web_gui._read_micromachine_compile_result(directory) or {}
                )
                if latest_result.get("update_id") == "emergency-second":
                    break
                time.sleep(0.02)
            self.assertEqual("emergency-second", latest_result.get("update_id"))

            release_normal_write.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with bridge._micromachine_request_lock:
                    pending = "normal-first" in bridge._micromachine_requests
                if not pending:
                    break
                time.sleep(0.02)

            latest_result = web_gui._read_micromachine_compile_result(directory)
            self.assertIsNotNone(latest_result)
            self.assertEqual("emergency-second", latest_result["update_id"])
            self.assertEqual(2, latest_result["acceptance_ordinal"])

            status = bridge.micromachine_status(blackboard_dir=directory)
            self.assertEqual(
                "emergency-second",
                status["latest_request"]["update_id"],
            )
            stream = {
                item.get("compile_result", {}).get("update_id"): item
                for item in status["modulation_results"]
            }
            self.assertEqual(
                {"normal-first", "emergency-second"},
                set(stream),
            )

    def test_emergency_commit_blocks_normal_publish_from_stale_snapshot(self):
        normal_snapshot_ready = threading.Event()
        release_normal = threading.Event()
        emergency_publish_ready = threading.Event()
        release_emergency = threading.Event()
        self.addCleanup(release_normal.set)
        self.addCleanup(release_emergency.set)
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=BlockingPolicyModulationLLMControl(
                started=normal_snapshot_ready,
                release=release_normal,
            ),
        )
        bridge.start()
        self.addCleanup(bridge.stop)
        original_publish_vector = web_gui._GuardedMicroMachineBackend.publish_vector

        def gate_emergency_publish(backend, *args, **kwargs):
            if backend._request.update_id == "urgent-retreat":
                emergency_publish_ready.set()
                if not release_emergency.wait(2):
                    raise TimeoutError("test emergency publish release was not set")
            return original_publish_vector(backend, *args, **kwargs)

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                web_gui._GuardedMicroMachineBackend,
                "publish_vector",
                autospec=True,
                side_effect=gate_emergency_publish,
            ),
        ):
            bridge.submit_micromachine_modulation_background(
                "긴급 즉시 후퇴",
                blackboard_dir=directory,
                provider_output={
                    "goal": "긴급 즉시 후퇴",
                    "override_level": "emergency",
                    "command_layer": "emergency",
                    "ttl_seconds": 45,
                    "emergency": {
                        "cancel_attacks": True,
                        "force_retreat": True,
                    },
                },
                current_frame=11,
                update_id="urgent-retreat",
            )
            self.assertTrue(emergency_publish_ready.wait(1))

            bridge.submit_micromachine_modulation_background(
                "탱크로 수비해",
                blackboard_dir=directory,
                current_frame=10,
                update_id="stale-normal",
            )
            self.assertTrue(
                normal_snapshot_ready.wait(1),
                "normal request did not capture its pre-emergency snapshot",
            )

            release_emergency.set()
            deadline = time.monotonic() + 2
            latest = {}
            while time.monotonic() < deadline:
                path = os.path.join(directory, "latest_modulation.json")
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as handle:
                        latest = json.load(handle)
                    if latest.get("update_id") == "urgent-retreat":
                        break
                time.sleep(0.02)
            self.assertEqual("urgent-retreat", latest.get("update_id"))

            release_normal.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with bridge._micromachine_request_lock:
                    pending = "stale-normal" in bridge._micromachine_requests
                if not pending:
                    break
                time.sleep(0.02)

            with open(
                os.path.join(directory, "latest_modulation.json"),
                encoding="utf-8",
            ) as handle:
                latest = json.load(handle)
            self.assertEqual("urgent-retreat", latest.get("update_id"))

            with open(
                os.path.join(directory, "modulation_updates.jsonl"),
                encoding="utf-8",
            ) as handle:
                archive_ids = [
                    json.loads(line)["update_id"]
                    for line in handle
                    if line.strip()
                ]
            self.assertEqual(["urgent-retreat"], archive_ids)

            status = bridge.micromachine_status(blackboard_dir=directory)
            stream = {
                item.get("compile_result", {}).get("update_id"): item
                for item in status["modulation_results"]
            }
            self.assertEqual("superseded", stream["stale-normal"]["status"])
            self.assertEqual("published", stream["urgent-retreat"]["status"])

    def test_emergency_safety_path_bypasses_llm_and_keeps_latest_runnable(self):
        class RejectEmergencyLLMControl(FakeConfiguredLLMControl):
            def __init__(self):
                self._lock = threading.Lock()
                self._call_count = 0

            def is_available(self):
                return True

            def propose_policy_modulation(self, request):
                with self._lock:
                    self._call_count += 1
                raise AssertionError("safety emergency must not call the LLM")

        control = RejectEmergencyLLMControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, llm_control=control)
        bridge.start()

        with tempfile.TemporaryDirectory() as directory:
            try:
                bridge.submit_micromachine_modulation_background(
                    "긴급 후퇴",
                    blackboard_dir=directory,
                    current_frame=10,
                    update_id="blocked-emergency",
                )
                bridge.submit_micromachine_modulation_background(
                    "공격 취소하고 즉시 복귀",
                    blackboard_dir=directory,
                    current_frame=11,
                    update_id="replacement-emergency",
                )

                deadline = time.monotonic() + 3
                latest = {}
                while time.monotonic() < deadline:
                    path = os.path.join(directory, "latest_modulation.json")
                    if os.path.isfile(path):
                        with open(path, encoding="utf-8") as handle:
                            latest = json.load(handle)
                        if latest.get("update_id") == "replacement-emergency":
                            break
                    time.sleep(0.02)

                self.assertEqual("replacement-emergency", latest.get("update_id"))
                self.assertEqual(0, control._call_count)
                archive_path = os.path.join(directory, "modulation_updates.jsonl")
                with open(archive_path, encoding="utf-8") as handle:
                    archive_ids = [
                        json.loads(line)["update_id"]
                        for line in handle
                        if line.strip()
                    ]
                self.assertEqual("replacement-emergency", archive_ids[-1])
                self.assertLessEqual(len(archive_ids), 2)
            finally:
                bridge.stop()

    def test_micromachine_emergency_cancellation_is_scoped_to_blackboard(self):
        started = threading.Event()
        release = threading.Event()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=BlockingPolicyModulationLLMControl(
                started=started,
                release=release,
            ),
        )
        bridge.start()

        with (
            tempfile.TemporaryDirectory() as blackboard_a,
            tempfile.TemporaryDirectory() as blackboard_b,
        ):
            try:
                bridge.submit_micromachine_modulation_background(
                    "탱크로 수비해",
                    blackboard_dir=blackboard_b,
                    current_frame=10,
                    update_id="blackboard-b-normal",
                )
                self.assertTrue(started.wait(1))
                bridge.submit_micromachine_modulation_background(
                    "긴급 즉시 후퇴",
                    blackboard_dir=blackboard_a,
                    provider_output={
                        "goal": "긴급 즉시 후퇴",
                        "override_level": "emergency",
                        "command_layer": "emergency",
                        "ttl_seconds": 45,
                        "emergency": {
                            "cancel_attacks": True,
                            "force_retreat": True,
                        },
                    },
                    current_frame=11,
                    update_id="blackboard-a-emergency",
                )

                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    path = os.path.join(
                        blackboard_a,
                        "latest_modulation.json",
                    )
                    if os.path.isfile(path):
                        break
                    time.sleep(0.02)
                with bridge._micromachine_request_lock:
                    normal_request = bridge._micromachine_requests[
                        "blackboard-b-normal"
                    ]
                    self.assertFalse(normal_request.cancel_event.is_set())

                release.set()
                deadline = time.monotonic() + 2
                latest_b = {}
                while time.monotonic() < deadline:
                    path = os.path.join(
                        blackboard_b,
                        "latest_modulation.json",
                    )
                    if os.path.isfile(path):
                        with open(path, encoding="utf-8") as handle:
                            latest_b = json.load(handle)
                        break
                    time.sleep(0.02)
                self.assertEqual(
                    "blackboard-b-normal",
                    latest_b.get("update_id"),
                )
            finally:
                release.set()
                bridge.stop()

    def test_micromachine_emergency_classifier_ignores_negated_commands(self):
        for command in (
            "공격을 취소하지 말고 계속 압박해",
            "후퇴하지 말고 버텨",
            "철수하지 말고 계속 공격해",
            "공격을 중단하지 말고 계속 압박해",
            "작전을 중단하지 마",
            "공격 중단 없이 계속 밀어",
            "공격 중단 금지",
            "후퇴 금지",
            "철수 없이 압박 유지",
            "후퇴 말고 공격해",
            "no retreat",
            "retreat is not an option",
            "do not stop the attack",
            "never retreat; hold the line",
            "不要撤退，继续进攻",
            "긴급 공격 시작",
            "emergency attack now",
            "마린 생산 중단하고 탱크 생산해",
            "stop producing marines and build tanks",
            "배럭 건설 취소하고 팩토리 지어",
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    web_gui._micromachine_request_is_emergency(command, None)
                )
        for command in (
            "긴급 후퇴",
            "후퇴해",
            "공격 취소하고 복귀",
            "emergency retreat",
            "fall back now",
            "stop the attack and regroup",
            "立即撤退",
        ):
            with self.subTest(command=command):
                self.assertTrue(
                    web_gui._micromachine_request_is_emergency(command, None)
                )

    def test_production_cancellation_stays_on_llm_macro_path(self):
        class RecordingPolicyControl(FakePolicyModulationLLMControl):
            def __init__(self):
                self.commands = []

            def propose_policy_modulation(self, request):
                self.commands.append(request.command_text)
                return super().propose_policy_modulation(request)

        control = RecordingPolicyControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, llm_control=control)
        bridge.start()
        self.addCleanup(bridge.stop)

        with tempfile.TemporaryDirectory() as directory:
            result = bridge.submit_micromachine_modulation(
                "마린 생산 중단하고 탱크 생산해",
                blackboard_dir=directory,
                current_frame=10,
                update_id="production-transition",
            )

        self.assertEqual(["마린 생산 중단하고 탱크 생산해"], control.commands)
        vector = result["update"]["vector"]
        self.assertNotEqual("emergency", vector["command_layer"])
        self.assertFalse(vector["emergency"]["cancel_attacks"])
        self.assertFalse(vector["emergency"]["force_retreat"])

    def test_negated_attack_cancellation_stays_on_llm_operation_path(self):
        class RecordingPolicyControl(FakePolicyModulationLLMControl):
            def __init__(self):
                self.commands = []

            def propose_policy_modulation(self, request):
                self.commands.append(request.command_text)
                return super().propose_policy_modulation(request)

        control = RecordingPolicyControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, llm_control=control)
        bridge.start()
        self.addCleanup(bridge.stop)

        with tempfile.TemporaryDirectory() as directory:
            result = bridge.submit_micromachine_modulation(
                "공격을 중단하지 말고 계속 압박해",
                blackboard_dir=directory,
                current_frame=10,
                update_id="continue-pressure",
            )

        self.assertEqual(["공격을 중단하지 말고 계속 압박해"], control.commands)
        vector = result["update"]["vector"]
        self.assertNotEqual("emergency", vector["command_layer"])
        self.assertFalse(vector["emergency"]["cancel_attacks"])
        self.assertFalse(vector["emergency"]["force_retreat"])

    def test_attack_cancel_prohibition_stays_on_llm_operation_path(self):
        class RecordingPolicyControl(FakePolicyModulationLLMControl):
            def __init__(self):
                self.commands = []

            def propose_policy_modulation(self, request):
                self.commands.append(request.command_text)
                return super().propose_policy_modulation(request)

        control = RecordingPolicyControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, llm_control=control)
        bridge.start()
        self.addCleanup(bridge.stop)

        with tempfile.TemporaryDirectory() as directory:
            result = bridge.submit_micromachine_modulation(
                "공격 중단 없이 계속 밀어",
                blackboard_dir=directory,
                current_frame=10,
                update_id="no-attack-cancel",
            )

        self.assertEqual(["공격 중단 없이 계속 밀어"], control.commands)
        vector = result["update"]["vector"]
        self.assertNotEqual("emergency", vector["command_layer"])
        self.assertFalse(vector["emergency"]["cancel_attacks"])
        self.assertFalse(vector["emergency"]["force_retreat"])

    def test_prohibitive_retreat_stays_on_llm_operation_path(self):
        class RecordingPolicyControl(FakePolicyModulationLLMControl):
            def __init__(self):
                self.commands = []

            def propose_policy_modulation(self, request):
                self.commands.append(request.command_text)
                return super().propose_policy_modulation(request)

        control = RecordingPolicyControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, llm_control=control)
        bridge.start()
        self.addCleanup(bridge.stop)

        with tempfile.TemporaryDirectory() as directory:
            result = bridge.submit_micromachine_modulation(
                "후퇴 말고 공격해",
                blackboard_dir=directory,
                current_frame=10,
                update_id="no-retreat-pressure",
            )

        self.assertEqual(["후퇴 말고 공격해"], control.commands)
        vector = result["update"]["vector"]
        self.assertNotEqual("emergency", vector["command_layer"])
        self.assertFalse(vector["emergency"]["cancel_attacks"])
        self.assertFalse(vector["emergency"]["force_retreat"])

    def test_synchronous_timeout_cancels_late_blackboard_publish(self):
        started = threading.Event()
        release = threading.Event()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=BlockingPolicyModulationLLMControl(
                started=started,
                release=release,
            ),
        )
        bridge.start()
        self.addCleanup(bridge.stop)

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                web_gui,
                "_MICROMACHINE_REQUEST_TIMEOUT_SECONDS",
                0.1,
            ),
            mock.patch.object(
                web_gui,
                "_MICROMACHINE_SYNC_PUBLISH_DEADLINE_SECONDS",
                0.05,
            ),
        ):
            with self.assertRaises(concurrent.futures.TimeoutError):
                bridge.submit_micromachine_modulation(
                    "탱크로 수비해",
                    blackboard_dir=directory,
                    current_frame=10,
                    update_id="sync-timeout",
                )
            self.assertTrue(started.is_set())
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with bridge._micromachine_request_lock:
                    pending = "sync-timeout" in bridge._micromachine_requests
                if not pending:
                    break
                time.sleep(0.02)

            self.assertFalse(
                os.path.exists(os.path.join(directory, "latest_modulation.json"))
            )

    def test_compile_result_persistence_failures_do_not_reverse_committed_publish(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        original_atomic_write = web_gui._atomic_write_json

        for failure_target in ("latest", "history"):
            with self.subTest(failure_target=failure_target):
                with tempfile.TemporaryDirectory() as directory:
                    update_id = f"post-commit-{failure_target}-failure"
                    latest_path = web_gui._micromachine_compile_result_path(directory)
                    history_path = web_gui._micromachine_compile_result_history_path(
                        directory,
                        update_id,
                    )

                    def flaky_atomic_write(path, payload):
                        should_fail = (
                            failure_target == "latest" and path == latest_path
                        ) or (
                            failure_target == "history" and path == history_path
                        )
                        if should_fail:
                            raise OSError(f"scripted {failure_target} persistence failure")
                        return original_atomic_write(path, payload)

                    with mock.patch.object(
                        web_gui,
                        "_atomic_write_json",
                        side_effect=flaky_atomic_write,
                    ):
                        result = bridge.submit_micromachine_modulation(
                            "마린 생산 유지",
                            blackboard_dir=directory,
                            provider_output={
                                "goal": "마린 생산 유지",
                                "override_level": "bias",
                                "command_layer": "macro",
                                "ttl_seconds": 120,
                                "production": {
                                    "queue_biases": {"TERRAN_MARINE": 0.8},
                                },
                            },
                            current_frame=10,
                            update_id=update_id,
                        )

                    self.assertEqual("published", result["status"])
                    self.assertTrue(result["ok"])
                    self.assertTrue(
                        os.path.isfile(
                            os.path.join(directory, "latest_modulation.json")
                        )
                    )
                    warnings = result.get("persistence_warnings", [])
                    self.assertEqual(1, len(warnings), warnings)
                    self.assertIn(failure_target, warnings[0])
                    self.assertNotEqual("publish_failed", result["status"])

    def test_micromachine_status_returns_bounded_per_update_result_stream(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                update_id = f"stream-{index}"
                compile_result = {
                    "status": "refused",
                    "update_id": update_id,
                    "refusal_reason": f"failure-{index}",
                }
                result = {
                    "status": "publish_failed",
                    "compile_result": compile_result,
                    "update": None,
                    "runtime_debug": {
                        "actor_tag": 9000 + index,
                        "assigned_unit_tags": [9100 + index],
                        "family_evidence": [
                            {
                                "attempted_unit_tags": [9200 + index],
                                "submitted_unit_tags": [9200 + index],
                                "effect_unit_tags": [9200 + index],
                            }
                        ],
                    },
                }
                web_gui._write_micromachine_compile_result(
                    directory,
                    {
                        "command_text": f"command-{index}",
                        "status": "publish_failed",
                        "compile_result": compile_result,
                        "update_id": update_id,
                        "result": result,
                        "written_at_unix": time.time() + index * 0.001,
                    },
                )

            status = bridge.micromachine_status(blackboard_dir=directory)

        self.assertEqual(
            ["stream-0", "stream-1"],
            [
                item["compile_result"]["update_id"]
                for item in status["modulation_results"]
            ],
        )
        serialized = json.dumps(status, ensure_ascii=False)
        for internal_key in (
            "actor_tag",
            "assigned_unit_tags",
            "attempted_unit_tags",
            "submitted_unit_tags",
            "effect_unit_tags",
        ):
            self.assertNotIn(internal_key, serialized)

    def test_micromachine_recent_command_retains_operation_edit_context(self):
        operation = {
            "operation_id": "assault-bravo",
            "generation": 3,
            "goal": "attack with six marines",
            "tactical_task": {
                "task_type": "pressure_with_main_army",
                "unit_classes": ["TERRAN_MARINE"],
                "min_units": 6,
                "max_units": 6,
            },
            "composition_requirements": [
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": 6,
                    "role": "frontline",
                }
            ],
            "operation_edit": {
                "action": "reinforce",
                "before_composition": [
                    {"unit_type": "TERRAN_MARINE", "count": 4}
                ],
                "after_composition": [
                    {"unit_type": "TERRAN_MARINE", "count": 6}
                ],
            },
        }
        entry = web_gui._micromachine_recent_command_entry(
            "assault-bravo에 마린 두 기 증원",
            {
                "status": "published",
                "compile_result": {
                    "status": "compiled",
                    "update_id": "operation-edit-context",
                    "vector": {
                        "goal": "reinforce assault",
                        "command_layer": "operation",
                        "operations": [operation],
                    },
                },
                "update": {"update_id": "operation-edit-context"},
            },
        )

        self.assertEqual([operation], entry["operations"])
        operation["generation"] = 99
        self.assertEqual(3, entry["operations"][0]["generation"])
        self.assertEqual(
            "reinforce",
            entry["operations"][0]["operation_edit"]["action"],
        )

    def test_micromachine_recent_commands_are_bounded_and_isolated_per_blackboard(self):
        class RecordingPolicyModulationControl(FakePolicyModulationLLMControl):
            def __init__(self):
                self.requests = []

            def propose_policy_modulation(self, request):
                self.requests.append(
                    (
                        request.command_text,
                        json.loads(
                            json.dumps(
                                request.commander_context,
                                ensure_ascii=False,
                            )
                        ),
                    )
                )
                result = dict(super().propose_policy_modulation(request))
                modulation = dict(result["modulation"])
                strategy = dict(modulation["strategy"])
                strategy["doctrine"] = "bio_pressure"
                modulation.update(
                    {
                        "command_layer": "operation",
                        "strategy": strategy,
                        "tactical_task": {
                            "task_type": "pressure_with_main_army",
                            "unit_classes": ["TERRAN_MARINE"],
                            "min_units": 4,
                            "max_units": 4,
                        },
                        "composition_requirements": [
                            {
                                "unit_type": "TERRAN_MARINE",
                                "count": 4,
                                "role": "frontline",
                            }
                        ],
                        "route_intent": {
                            "route_type": "flank_right",
                            "avoid_enemy_strength": True,
                        },
                        "target_intent": {
                            "target_type": "enemy_main",
                            "priority": 0.9,
                        },
                    }
                )
                result["modulation"] = modulation
                return result

        control = RecordingPolicyModulationControl()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session, llm_control=control)
        bridge.start()
        self.addCleanup(bridge.stop)

        with (
            tempfile.TemporaryDirectory() as blackboard_a,
            tempfile.TemporaryDirectory() as blackboard_b,
        ):
            for index in range(1, 11):
                if index == 3:
                    bridge.submit_micromachine_modulation(
                        "B 명령 1",
                        blackboard_dir=blackboard_b,
                        current_frame=1,
                        update_id="context-b-1",
                    )
                bridge.submit_micromachine_modulation(
                    f"A 명령 {index}",
                    blackboard_dir=blackboard_a,
                    current_frame=index,
                    update_id=f"context-a-{index}",
                )

        contexts = {
            command_text: context for command_text, context in control.requests
        }
        self.assertEqual(contexts["A 명령 1"]["recent_commands"], [])
        self.assertEqual(contexts["B 명령 1"]["recent_commands"], [])
        self.assertEqual(
            [entry["command_text"] for entry in contexts["A 명령 10"]["recent_commands"]],
            [f"A 명령 {index}" for index in range(2, 10)],
        )
        self.assertTrue(
            contexts["A 명령 2"]["recent_commands"][0]["assistant_message"]
        )
        first_entry = contexts["A 명령 2"]["recent_commands"][0]
        self.assertEqual(first_entry["update_id"], "context-a-1")
        self.assertEqual(first_entry["command_layer"], "operation")
        self.assertEqual(first_entry["category"], "tactical")
        self.assertEqual(first_entry["reducer_action"], "activate")
        self.assertEqual(first_entry["goal"], "A 명령 1")
        self.assertEqual(first_entry["doctrine"], "bio_pressure")
        self.assertEqual(
            first_entry["tactical_task"],
            {
                "type": "pressure_with_main_army",
                "ability": "",
                "units": ["TERRAN_MARINE"],
                "count": {"min": 4, "max": 4, "requested": 4},
            },
        )
        self.assertEqual(first_entry["route"], "flank_right")
        self.assertEqual(first_entry["target"], "enemy_main")
        self.assertEqual(first_entry["consumption_status"], "pending_telemetry")
        self.assertEqual(first_entry["execution_status"], "consumed_by_manager")
        self.assertLessEqual(
            len(first_entry["tactical_task"]["units"]),
            web_gui._MICROMACHINE_RECENT_COMMAND_LIST_LIMIT,  # noqa: SLF001
        )

    def test_llm_provider_preserves_blackboard_context_after_web_history_loss(self):
        class RecordingControl(FakePolicyModulationLLMControl):
            def __init__(self):
                self.request = None

            def propose_policy_modulation(self, request):
                self.request = request
                return super().propose_policy_modulation(request)

        control = RecordingControl()
        provider = web_gui._LocalLLMPolicyModulationProvider(  # noqa: SLF001
            control,
            recent_commands=[
                {
                    "update_id": "web-old",
                    "command_text": "마린 중심으로 가",
                    "command_layer": "macro",
                }
            ],
        )
        provider.propose_policy_modulation(
            PolicyModulationProviderRequest(
                command_text="그 병력으로 더 강하게 공격해",
                commander_context={
                    "recent_commands": [
                        {
                            "update_id": "blackboard-current",
                            "goal": "마린 4기로 적 본진 압박",
                            "command_layer": "operation",
                            "tactical_task": {
                                "task_type": "pressure_with_main_army",
                                "unit_classes": ["TERRAN_MARINE"],
                                "min_units": 4,
                                "max_units": 4,
                            },
                        }
                    ]
                },
            )
        )

        self.assertIsNotNone(control.request)
        recent = control.request.commander_context["recent_commands"]
        self.assertEqual(
            ["web-old", "blackboard-current"],
            [item["update_id"] for item in recent],
        )
        self.assertEqual(
            "pressure_with_main_army",
            recent[-1]["tactical_task"]["task_type"],
        )

        empty_memory_provider = web_gui._LocalLLMPolicyModulationProvider(  # noqa: SLF001
            control,
            recent_commands=[],
        )
        empty_memory_provider.propose_policy_modulation(
            PolicyModulationProviderRequest(
                command_text="계속 진행해",
                commander_context={"recent_commands": recent[-1:]},
            )
        )
        self.assertEqual(
            ["blackboard-current"],
            [
                item["update_id"]
                for item in control.request.commander_context["recent_commands"]
            ],
        )

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

    def test_stop_timeout_prevents_restart_until_old_worker_terminates(self):
        started = threading.Event()
        release = threading.Event()
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(
            session=session,
            llm_control=BlockingPolicyModulationLLMControl(
                started=started,
                release=release,
            ),
        )
        bridge.start()
        self.addCleanup(release.set)
        self.addCleanup(bridge.stop)

        with tempfile.TemporaryDirectory() as directory:
            bridge.submit_micromachine_modulation_background(
                "탱크로 수비해",
                blackboard_dir=directory,
                current_frame=10,
                update_id="stop-blocked-normal",
            )
            self.assertTrue(started.wait(1))
            old_thread = bridge._thread
            self.assertIsNotNone(old_thread)
            with bridge._micromachine_request_lock:
                blocked_request = bridge._micromachine_requests[
                    "stop-blocked-normal"
                ]

            bridge.stop(timeout=0.01)

            self.assertTrue(old_thread.is_alive())
            self.assertFalse(bridge.is_running)
            self.assertTrue(blocked_request.cancel_event.is_set())
            self.assertTrue(blocked_request.future.done())
            self.assertIsInstance(blocked_request.future.exception(), RuntimeError)
            with self.assertRaisesRegex(RuntimeError, "not running"):
                bridge.submit_micromachine_modulation_background(
                    "종료 중에는 받지 마",
                    blackboard_dir=directory,
                    current_frame=10,
                    update_id="rejected-during-stopping",
                )
            with bridge._micromachine_request_lock:
                self.assertNotIn(
                    "rejected-during-stopping",
                    bridge._micromachine_requests,
                )
            with self.assertRaisesRegex(RuntimeError, "still stopping"):
                bridge.start()

            release.set()
            old_thread.join(timeout=2)
            self.assertFalse(old_thread.is_alive())

            bridge.start()
            self.assertTrue(bridge.is_running)
            result = bridge.submit_micromachine_modulation(
                "마린 생산 유지",
                blackboard_dir=directory,
                provider_output={
                    "goal": "마린 생산 유지",
                    "override_level": "bias",
                    "command_layer": "macro",
                    "ttl_seconds": 120,
                    "production": {
                        "queue_biases": {"TERRAN_MARINE": 0.8},
                    },
                },
                current_frame=11,
                update_id="restart-after-stop",
            )
            self.assertEqual("published", result["status"])

    def test_stop_during_initialization_never_exposes_a_running_bridge(self):
        entered = threading.Event()
        release = threading.Event()
        session, _bot = build_dry_run_session()

        class DelayedStartBridge(SessionLoopBridge):
            def _run_loop(self):
                entered.set()
                release.wait(2)
                super()._run_loop()

        bridge = DelayedStartBridge(session=session)
        start_errors = []

        def start_bridge():
            try:
                bridge.start()
            except Exception as error:  # noqa: BLE001 - asserted below.
                start_errors.append(error)

        starter = threading.Thread(target=start_bridge)
        starter.start()
        self.assertTrue(entered.wait(1))

        bridge.stop(timeout=0.01)
        self.assertFalse(bridge.is_running)
        self.assertEqual(
            web_gui._BRIDGE_LIFECYCLE_STOPPING,
            bridge._lifecycle_state,
        )

        release.set()
        starter.join(timeout=2)
        self.assertFalse(starter.is_alive())
        self.assertEqual(1, len(start_errors))
        self.assertIsInstance(start_errors[0], RuntimeError)
        self.assertFalse(bridge.is_running)
        self.assertEqual(
            web_gui._BRIDGE_LIFECYCLE_STOPPED,
            bridge._lifecycle_state,
        )
        self.assertIsNone(bridge._thread)
        self.assertIsNone(bridge._loop)
        self.assertIsNone(bridge._queue)

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
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
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
            "animation: none !important;\n"
            "      transform: none;\n"
            "      will-change: auto;",
            page,
        )
        self.assertIn(
            ".message-pending .narration::after {\n"
            "      animation: none !important;\n"
            '      content: "..." !important;',
            page,
        )
        self.assertIn(
            ".typing-indicator span, .voice-wave span {\n"
            "      animation: none !important;",
            page,
        )
        self.assertIn(
            ".active-command-console::after {\n"
            "      transition: none !important;",
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
            "@media (max-width: 1180px)",
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
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
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
assert.strictEqual(logBox.querySelector(".message-pending").getAttribute("role"), null);
assert.strictEqual(logBox.querySelectorAll(".log-entry").length, MAX_CHAT_EVENTS);
assert(document.getElementById("voice-recording-entry"));
assert.strictEqual(logBox.querySelector(".voice-wave").querySelectorAll("span").length, 5);
appendPendingCommand("상황 보고해줘");
assert.strictEqual(pendingCommandCount(), 2);
assert(pendingStatus.textContent.includes("대기 중인 응답 2개"));
assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 1);
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
      var active = document.activeElement;
      while (active) {
        if (active === child) {
          document.activeElement = null;
          break;
        }
        active = active.parentNode;
      }
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

  focus() {
    document.activeElement = this;
  }

  setSelectionRange() {}

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
    this.children.slice().forEach(function(child) {
      this.removeChild(child);
    }, this);
  }

  set innerHTML(value) {
    this._textContent = "";
    this.children.slice().forEach(function(child) {
      this.removeChild(child);
    }, this);
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
  "micromachine-enemy-difficulty-control": element("micromachine-enemy-difficulty-control"),
  "micromachine-enemy-difficulty": element("micromachine-enemy-difficulty", "input"),
  "connection-status": element("connection-status"),
  "state-minerals": element("state-minerals"),
  "state-vespene": element("state-vespene"),
  "state-supply": element("state-supply"),
  "state-workers": element("state-workers"),
  "state-army": element("state-army"),
  "state-structures": element("state-structures"),
  "state-availability": element("state-availability"),
  "strategy-briefing": element("strategy-briefing"),
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
  "micromachine-command-execution": element("micromachine-command-execution"),
  "micromachine-refusal": element("micromachine-refusal"),
  "micromachine-log-snippets": element("micromachine-log-snippets", "ul"),
  "micromachine-raw-evidence": element("micromachine-raw-evidence", "pre"),
  "operation-console": element("operation-console", "section"),
      "operation-list": element("operation-list", "div"),
      "operation-summary": element("operation-summary", "span"),
      "operation-timeline": element("operation-timeline", "ol"),
      "operation-timeline-selection": element("operation-timeline-selection", "span"),
  "active-command-console": element("active-command-console", "section"),
  "command-console-title": element("command-console-title", "h2"),
  "command-console-state": element("command-console-state", "span"),
  "command-console-announcement": element("command-console-announcement", "span"),
  "command-console-intent": element("command-console-intent", "strong"),
  "command-console-units": element("command-console-units", "strong"),
  "command-console-action": element("command-console-action", "strong"),
  "command-console-target": element("command-console-target", "strong"),
  "command-console-verification": element("command-console-verification", "strong"),
  "command-console-technical": element("command-console-technical", "pre"),
  "command-stage-interpret": element("command-stage-interpret"),
  "command-stage-assign": element("command-stage-assign"),
  "command-stage-execute": element("command-stage-execute"),
  "command-stage-verify": element("command-stage-verify"),
  "command-refresh-button": element("command-refresh-button", "button"),
  "command-revise-button": element("command-revise-button", "button"),
  "command-retreat-button": element("command-retreat-button", "button"),
  "battlefield-link-badge": element("battlefield-link-badge", "span"),
  "battlefield-command-state": element("battlefield-command-state"),
  "battlefield-frame": element("battlefield-frame"),
      "battlefield-force": element("battlefield-force"),
      "battlefield-posture": element("battlefield-posture"),
      "battlefield-unassigned": element("battlefield-unassigned"),
      "battlefield-readiness": element("battlefield-readiness"),
      "battlefield-transfer": element("battlefield-transfer"),
      "battlefield-integrity": element("battlefield-integrity"),
      "battlefield-production-waits": element("battlefield-production-waits"),
      "battlefield-control-summary": element("battlefield-control-summary")
};
nodes["log"] = logBox;
nodes["llm-model-select"].value = "gpt-test";
nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-js-test";
nodes["micromachine-enemy-difficulty"].value = "10";
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
  activeElement: null,
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
      error: function (message) {
        global.__consoleError = message;
        if (typeof process !== "undefined" && process.stderr) {
          process.stderr.write(String(message) + "\n");
        }
      }
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
  if (data && typeof data === "object" && !data.blackboard_scope_id) {
    data.blackboard_scope_id = "server-blackboard-scope-a";
    var compileResult = data.compile_result || {};
    var update = data.update || {};
    var intervention = data.intervention || {};
    var execution = intervention.command_execution || {};
    var updateId = String(
      compileResult.update_id ||
      data.update_id ||
      update.update_id ||
      execution.command_id ||
      ""
    );
    if (updateId) {
      data.result_id = data.result_id || (
        "server-result-server-blackboard-scope-a-" + updateId
      );
      if (data.compile_result && typeof data.compile_result === "object") {
        data.compile_result.blackboard_scope_id = data.blackboard_scope_id;
        data.compile_result.result_id = data.result_id;
      }
    }
  }
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
  var SERVER_SCOPE_A = "server-blackboard-scope-a";
  var SERVER_SCOPE_B = "server-blackboard-scope-b";
  function serverResult(data, scopeId) {
    if (!data || typeof data !== "object") { return data; }
    var scope = scopeId || data.blackboard_scope_id || SERVER_SCOPE_A;
    data.blackboard_scope_id = scope;
    var compileResult = data.compile_result || {};
    var update = data.update || {};
    var intervention = data.intervention || {};
    var execution = intervention.command_execution || {};
    var updateId = String(
      compileResult.update_id ||
      data.update_id ||
      update.update_id ||
      execution.command_id ||
      ""
    );
    if (updateId) {
      data.result_id = data.result_id || (
        "server-result-" + scope + "-" + updateId
      );
      if (data.compile_result && typeof data.compile_result === "object") {
        data.compile_result.blackboard_scope_id = scope;
        data.compile_result.result_id = data.result_id;
      }
    }
    if (Array.isArray(data.modulation_results)) {
      data.modulation_results.forEach(function(item) {
        serverResult(item, scope);
      });
    }
    return data;
  }
  function pendingFor(scopeId, updateId) {
    return pendingMicroMachineRecord(scopeId || SERVER_SCOPE_A, updateId);
  }
  function hasPending(scopeId, updateId) {
    return Boolean(pendingFor(scopeId, updateId));
  }
  function rememberServerPending(text, updateId, scopeId) {
    var pendingId = appendMicroMachinePendingPlan(text);
    rememberPendingMicroMachineAsync(
      text,
      serverResult(
        {
          async_publish: true,
          update_id: updateId
        },
        scopeId || SERVER_SCOPE_A
      ),
      pendingId
    );
    return pendingId;
  }
  function observedExecutionStages(effectEvidence) {
    return [
      { name: "parsed", ok: true, manager: "CommandGateway", evidence: {} },
      { name: "reduced", ok: true, manager: "PolicyReducer", evidence: {} },
      { name: "consumed_by_manager", ok: true, manager: "CombatCommander", evidence: {} },
      {
        name: "queued_or_assigned",
        ok: true,
        manager: "CombatCommander",
        evidence: { assigned_unit_count: 4, commanded_unit_type: "marine" }
      },
      {
        name: "order_issued",
        ok: true,
        manager: "Squad",
        evidence: { last_issued_action: "attack", target_x: 120, target_y: 44 }
      },
      {
        name: "action_issued",
        ok: true,
        manager: "RangedManager",
        evidence: { last_actual_command: "attack", commanded_unit_type: "marine" }
      },
      {
        name: "effect_observed",
        ok: true,
        manager: "TacticalEvidence",
        evidence: effectEvidence || { confirmation_effect: "requested movement observed" }
      }
    ];
  }
  var orderOnlyData = {
    status: "published",
    intervention: {
      command_execution: {
        state: "order_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: observedExecutionStages().slice(0, 5)
      }
    }
  };
  var orderOnlyModel = commandConsoleStageModel(orderOnlyData);
  assert.strictEqual(orderOnlyModel.assignmentReady, true);
  assert.strictEqual(orderOnlyModel.actionIssued, false);
  assert.strictEqual(orderOnlyModel.done.execute, false);
  assert(
    commandConsoleActualAction(orderOnlyModel).includes(
      "구체적인 이동·공격·생산 명령 대기"
    )
  );
  assert.strictEqual(
    operationRecordLane({
      data: orderOnlyData,
      disposition: "active",
      terminal: false
    }),
    "planning"
  );
  var stateOnlyActionData = JSON.parse(JSON.stringify(orderOnlyData));
  stateOnlyActionData.intervention.command_execution.state =
    "action_issued";
  var stateOnlyActionModel = commandConsoleStageModel(
    stateOnlyActionData
  );
  assert.strictEqual(stateOnlyActionModel.actionIssued, false);
  assert.strictEqual(
    operationRecordLane({
      data: stateOnlyActionData,
      disposition: "active",
      terminal: false
    }),
    "planning"
  );
  pollState();
  await flushPromises();
  assert.strictEqual(requests.length, 0);
  assert.strictEqual(nodes["state-minerals"].textContent, "-");
  assert.strictEqual(nodes["state-vespene"].textContent, "-");
  assert(nodes["state-availability"].textContent.includes("MicroMachine 모드"));

  setCommandMode(COMMAND_MODE_LEGACY_COMMANDER);
  assert.strictEqual(requests.length, 1);
  assert.strictEqual(requests[0].url, "/api/state");
  var legacyStateRequest = requests[0];
  setCommandMode(COMMAND_MODE_MICROMACHINE);
  legacyStateRequest.deferred.resolve(response(200, {
    minerals: 400,
    vespene: 0,
    supply_used: 12,
    supply_cap: 15,
    availability: "legacy-state"
  }));
  await flushPromises();
  assert.strictEqual(nodes["state-minerals"].textContent, "-");
  assert.strictEqual(nodes["state-vespene"].textContent, "-");
  assert(nodes["state-availability"].textContent.includes("MicroMachine 모드"));
  requests = [];
  setCommandMode(COMMAND_MODE_MICROMACHINE);
  assert.strictEqual(requests.length, 0);
  beginActiveCommandConsole("같은 연속 명령", "");
  var firstCommandAnnouncement = nodes["command-console-announcement"].textContent;
  beginActiveCommandConsole("같은 연속 명령", "");
  var secondCommandAnnouncement = nodes["command-console-announcement"].textContent;
  assert(firstCommandAnnouncement.includes("같은 연속 명령"));
  assert(secondCommandAnnouncement.includes("같은 연속 명령"));
  assert.notStrictEqual(firstCommandAnnouncement, secondCommandAnnouncement);
  resetActiveCommandConsole();
  assert.strictEqual(buildMicroMachineModulationPayload("marine rush").response_language, "en");
  assert.strictEqual(buildMicroMachineModulationPayload("마린 러쉬").response_language, "ko");
  assert.strictEqual(buildMicroMachineModulationPayload("进攻").response_language, "zh");
  assert.strictEqual(buildMicroMachineModulationPayload("marine rush").async_publish, true);
  assert.strictEqual(buildMicroMachineModulationPayload("hello").async_publish, true);
  assert.strictEqual(buildMicroMachineModulationPayload("marine rush").allow_smoke_keyword_provider, undefined);
  assert.strictEqual(buildMicroMachineModulationPayload("마린 러쉬").allow_smoke_keyword_provider, undefined);
  assert.strictEqual(buildMicroMachineModulationPayload("hello").allow_smoke_keyword_provider, undefined);
  assert.strictEqual(runtimeStartPayload().enemy_difficulty, 10);
  nodes["micromachine-enemy-difficulty"].value = "7.5";
  assert.throws(function () { runtimeStartPayload(); }, /integer from 1 to 10/);
  startSelectedRuntime();
  assert.strictEqual(requests.length, 0);
  assert(nodes["live-status"].textContent.includes("integer from 1 to 10"));
  nodes["micromachine-enemy-difficulty"].value = "7";

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
  assert.strictEqual(firstBody.async_publish, true);
  assert.strictEqual(firstBody.allow_smoke_keyword_provider, undefined);
  assert.strictEqual(firstBody.ui_language, "ko");
  assert.strictEqual(firstBody.response_language, "ko");
  assert.strictEqual(firstBody.ttl_seconds, 600);
  assert.strictEqual(pendingCommandCount(), 1);
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 1);
  var pendingMessage = logBox.querySelectorAll(".message-pending")[0];
  assert.strictEqual(pendingMessage.getAttribute("role"), null);
  assert.strictEqual(pendingMessage.getAttribute("aria-live"), null);

  var originalRenderMicroMachineStatus = renderMicroMachineStatus;
  renderMicroMachineStatus = function () {
    throw new Error("dashboard boom");
  };
  requests[0].deferred.resolve(response(202, {
    ok: true,
    accepted: true,
    queued: false,
    async_publish: false,
    status: "published",
    update_id: "unit-update-1",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      source: "smoke_keyword",
      update_id: "unit-update-1",
      assistant_message: "",
      vector: { goal: "enemy natural 압박" }
    },
    update: { update_id: "unit-update-1" }
  }));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "false");
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 0);
  assert(!logBox.textContent.includes("백그라운드에서 시작"));
  assert(logBox.textContent.includes("enemy natural 압박"));
  assert(nodes["micromachine-status"].textContent.includes("dashboard render failed"));

  renderMicroMachineStatus = originalRenderMicroMachineStatus;
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "false");
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 0);
  assert(!logBox.textContent.includes("attack_gate="));
  assert.strictEqual(nodes["command-input"].value, "");

  rememberServerPending("4마린 전진 상태 추적", "stage-contract");
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "stage-contract",
      vector: { goal: "4 marine advance" }
    },
    update: { update_id: "stage-contract" },
    intervention: {
      latest_update_id: "stage-contract",
      telemetry_frame: 120,
      command_execution: {
        command_id: "stage-contract",
        state: "consumed_by_manager",
        completed: false,
        failed: false,
        expired: false,
        stages: [
          { name: "parsed", ok: true, manager: "CommandGateway" },
          { name: "reduced", ok: true, manager: "PolicyReducer" },
          { name: "consumed_by_manager", ok: true, manager: "CombatCommander" }
        ]
      }
    }
  }));
  assert(!nodes["active-command-console"].className.includes("command-console-verified"));
  assert.notStrictEqual(nodes["micromachine-applied-badge"].className, "micro-badge micro-badge-applied");
  assert.strictEqual(nodes["command-console-state"].textContent, "MicroMachine 배정 중");
  assert(nodes["command-stage-interpret"].className.includes("stage-done"));
  assert(!nodes["command-stage-interpret"].className.includes("stage-verified"));
  assert(hasPending(SERVER_SCOPE_A, "stage-contract"));

  var issuedStages = observedExecutionStages().slice(0, 6);
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "stage-contract",
      vector: { goal: "4 marine advance" }
    },
    update: { update_id: "stage-contract" },
    intervention: {
      latest_update_id: "stage-contract",
      telemetry_frame: 200,
      command_execution: {
        command_id: "stage-contract",
        state: "action_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: issuedStages
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "전장에서 실행 중");
  assert(nodes["active-command-console"].className.includes("command-console-executing"));
  assert(!nodes["active-command-console"].className.includes("command-console-verified"));
  assert(nodes["command-console-action"].textContent.includes("attack"));
  assert(nodes["command-console-verification"].textContent.includes("실제 이동"));
  assert(nodes["command-stage-execute"].className.includes("stage-done"));
  assert(!nodes["command-stage-execute"].className.includes("stage-verified"));
  assert(hasPending(SERVER_SCOPE_A, "stage-contract"));

  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "stage-contract"
    },
    update: { update_id: "stage-contract" },
    intervention: {
      latest_update_id: "stage-contract",
      telemetry_frame: 150,
      command_execution: {
        command_id: "stage-contract",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "stale effect must stay ignored"
        })
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "전장에서 실행 중");
  assert(!nodes["active-command-console"].className.includes("command-console-verified"));
  assert(!nodes["command-console-verification"].textContent.includes("stale effect"));
  assert.strictEqual(
    nodes["micromachine-applied-badge"].className,
    "micro-badge micro-badge-active"
  );

  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "stage-contract"
    },
    update: { update_id: "stage-contract" },
    intervention: {
      latest_update_id: "stage-contract",
      telemetry_frame: 100,
      command_execution: {
        command_id: "stage-contract",
        state: "consumed_by_manager",
        completed: false,
        failed: false,
        expired: false,
        stages: [
          { name: "parsed", ok: true, manager: "CommandGateway" },
          { name: "reduced", ok: true, manager: "PolicyReducer" }
        ]
      }
    }
  }));
  assert.strictEqual(
    nodes["command-console-state"].textContent,
    "전장에서 실행 중",
    "older telemetry must not regress the active operation card"
  );
  assert(nodes["command-console-action"].textContent.includes("attack"));

  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "stage-contract",
      assistant_message: "실제 전진을 확인했습니다."
    },
    update: { update_id: "stage-contract" },
    intervention: {
      latest_update_id: "stage-contract",
      telemetry_frame: 220,
      command_execution: {
        command_id: "stage-contract",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "four marines moved away from home"
        })
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 확인");
  assert(nodes["active-command-console"].className.includes("command-console-verified"));
  assert(nodes["command-stage-interpret"].className.includes("stage-verified"));
  assert(nodes["command-stage-assign"].className.includes("stage-verified"));
  assert(nodes["command-stage-execute"].className.includes("stage-verified"));
  assert(nodes["command-stage-verify"].className.includes("stage-verified"));
  assert(nodes["command-console-announcement"].textContent.includes("실행 확인"));
  assert(nodes["command-console-announcement"].textContent.includes("four marines moved away from home"));
  assert.strictEqual(nodes["micromachine-applied-badge"].className, "micro-badge micro-badge-applied");
  assert(!hasPending(SERVER_SCOPE_A, "stage-contract"));

  renderMicroMachineStatus(serverResult({
    ok: true,
    accepted: true,
    status: "published",
    compile_result: {
      status: "refused",
      update_id: "external-refused-b",
      refusal_reason: "external B could not be compiled"
    },
    latest_request: {
      update_id: "external-refused-b",
      command_text: "external B refused order",
      status: "refused",
      is_active_update: false
    },
    update: { update_id: "stage-contract" },
    intervention: {
      latest_update_id: "stage-contract",
      telemetry_frame: 220,
      command_execution: {
        command_id: "stage-contract",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages()
      }
    }
  }));
  assert.strictEqual(activeCommandConsoleRecord.updateId, "external-refused-b");
  assert.strictEqual(nodes["command-console-title"].textContent, "external B refused order");
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 실패");
  assert(nodes["command-console-verification"].textContent.includes("external B could not be compiled"));

  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "external-handoff-b",
      vector: { goal: "external B takes battlefield control" }
    },
    update: { update_id: "external-handoff-b" },
    intervention: {
      latest_update_id: "external-handoff-b",
      telemetry_frame: 230,
      command_execution: {
        command_id: "external-handoff-b",
        state: "action_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: observedExecutionStages().slice(0, 6)
      }
    }
  }));
  assert.strictEqual(activeCommandConsoleRecord.updateId, "external-handoff-b");
  assert.strictEqual(
    nodes["command-console-title"].textContent,
    "external B takes battlefield control"
  );
  assert.strictEqual(nodes["command-console-state"].textContent, "전장에서 실행 중");

  rememberServerPending("active A consumed", "race-active-a");
  rememberServerPending("latest B refused", "race-failed-b");
  assert(hasPending(SERVER_SCOPE_A, "race-active-a"));
  assert(hasPending(SERVER_SCOPE_A, "race-failed-b"));
  renderMicroMachineStatus(serverResult({
    ok: true,
    accepted: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "refused",
      update_id: "race-failed-b",
      refusal_reason: "provider auth failed"
    },
    latest_request: {
      update_id: "race-failed-b",
      status: "refused",
      consumption_status: "not_published",
      is_active_update: false
    },
    update: { update_id: "race-active-a" },
    intervention: {
      latest_update_id: "race-active-a",
      tactical_posture: "pressure",
      manager_bias_domains: ["combat"],
      goal: "active pressure",
      command_execution: {
        command_id: "race-active-a",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: [
          { name: "effect_observed", ok: true, manager: "TacticalEvidence" }
        ],
        scenarios: [
          { name: "four_marine_attack", ok: true },
          { name: "marine_scout", ok: false }
        ]
      }
    },
    dashboard: {
      active_updates: [
        { update_id: "race-active-a", manager_bias_domains: ["combat"] }
      ]
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "race-active-a"));
  assert(!hasPending(SERVER_SCOPE_A, "race-failed-b"));
  assert(logBox.textContent.includes("active A consumed"));
  assert(logBox.textContent.includes("latest B refused"));
  assert(logBox.textContent.includes("active pressure"));
  assert(logBox.textContent.includes("provider auth failed"));
  assert.strictEqual(nodes["command-console-title"].textContent, "latest B refused");
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 실패");
  assert(!nodes["command-console-technical"].textContent.includes("race-active-a"));
  assert(nodes["micromachine-command-execution"].textContent.includes("state=completed"));
  assert(nodes["micromachine-command-execution"].textContent.includes("four_marine_attack"));
  var activeEntry = logBox.querySelectorAll(".log-entry").find(function (entry) {
    return entry.textContent.includes("active A consumed");
  });
  assert(activeEntry);
  assert(!activeEntry.textContent.includes("provider auth failed"));

  rememberServerPending("혼합 payload 실행 A", "mixed-active-a");
  rememberServerPending("혼합 payload compile B", "mixed-compile-b");
  var mixedPayload = serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "mixed-compile-b"
    },
    latest_request: {
      update_id: "mixed-compile-b",
      status: "compiled",
      is_active_update: false
    },
    update: { update_id: "mixed-active-a" },
    intervention: {
      latest_update_id: "mixed-active-a",
      command_execution: {
        command_id: "mixed-active-a",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "mixed A effect"
        })
      }
    }
  });
  var mixedCompileResultId = mixedPayload.result_id;
  renderMicroMachineStatus(mixedPayload);
  assert(!hasPending(SERVER_SCOPE_A, "mixed-active-a"));
  assert(hasPending(SERVER_SCOPE_A, "mixed-compile-b"));
  assert.strictEqual(
    Boolean(
      consumedMicroMachineResultIdsByScope[SERVER_SCOPE_A] &&
      consumedMicroMachineResultIdsByScope[SERVER_SCOPE_A][mixedCompileResultId]
    ),
    false,
    "active A completion must not consume compile B result identity"
  );
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "mixed-compile-b"
    },
    update: { update_id: "mixed-compile-b" },
    intervention: {
      latest_update_id: "mixed-compile-b",
      command_execution: {
        command_id: "mixed-compile-b",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "mixed B effect"
        })
      }
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "mixed-compile-b"));
  var mixedCompileEntries = logBox.querySelectorAll(".log-entry").filter(function(entry) {
    return entry.textContent.includes("혼합 payload compile B");
  });
  assert.strictEqual(
    mixedCompileEntries.length,
    1,
    mixedCompileEntries.map(function(entry) { return entry.textContent; }).join("\\n---\\n")
  );

  rememberServerPending("스트림 결과 A", "stream-result-a");
  rememberServerPending("스트림 결과 B", "stream-result-b");
  renderMicroMachineStatus(serverResult({
    enabled: true,
    status: "idle",
    modulation_results: [
      {
        status: "publish_failed",
        compile_result: {
          status: "refused",
          update_id: "stream-result-a",
          refusal_reason: "stream failure A"
        }
      },
      {
        status: "publish_failed",
        compile_result: {
          status: "refused",
          update_id: "stream-result-b",
          refusal_reason: "stream failure B"
        }
      }
    ],
    dashboard: { active_updates: [] },
    intervention: {}
  }));
  assert(!hasPending(SERVER_SCOPE_A, "stream-result-a"));
  assert(!hasPending(SERVER_SCOPE_A, "stream-result-b"));
  assert(logBox.textContent.includes("stream failure A"));
  assert(logBox.textContent.includes("stream failure B"));

  resetActiveCommandConsole();
  renderMicroMachineStatus(serverResult({
    enabled: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "high-frame-active"
    },
    update: { update_id: "high-frame-active" },
    intervention: {
      latest_update_id: "high-frame-active",
      telemetry_frame: 950,
      command_execution: {
        command_id: "high-frame-active",
        state: "action_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: observedExecutionStages().slice(0, 6)
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "전장에서 실행 중");
  var staleFramePendingId = appendPendingCommand("낮은 frame에도 완료될 다른 명령");
  rememberPendingMicroMachineAsync(
    "낮은 frame에도 완료될 다른 명령",
    serverResult({
      async_publish: true,
      update_id: "stale-frame-stream-result"
    }),
    staleFramePendingId,
    false
  );
  renderMicroMachineStatus(serverResult({
    enabled: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "high-frame-active"
    },
    update: { update_id: "high-frame-active" },
    modulation_results: [
      {
        status: "publish_failed",
        compile_result: {
          status: "refused",
          update_id: "stale-frame-stream-result",
          refusal_reason: "terminal result survives stale active frame"
        }
      }
    ],
    intervention: {
      latest_update_id: "high-frame-active",
      telemetry_frame: 940,
      command_execution: {
        command_id: "high-frame-active",
        state: "action_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: observedExecutionStages().slice(0, 6)
      }
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "stale-frame-stream-result"));
  assert(logBox.textContent.includes("terminal result survives stale active frame"));
  assert.strictEqual(activeCommandConsoleRecord.updateId, "high-frame-active");
  assert.strictEqual(activeCommandConsoleRecord.telemetryFrame, 950);
  resetActiveCommandConsole();

  rememberServerPending("소비 후 효과 대기 테스트", "consumed-still-running");
  renderMicroMachineStatus(serverResult({
    ok: true,
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "consumed-still-running",
      source: "llm",
      assistant_message: "공격 명령을 전술 큐에 반영했습니다."
    },
    update: { update_id: "consumed-still-running" },
    intervention: {
      latest_update_id: "consumed-still-running",
      command_execution: {
      command_id: "consumed-still-running",
      state: "consumed_by_manager",
      completed: false,
      failed: false,
      expired: false,
      blocker_manager: "CombatCommander",
      blocker_reason: "Manager consumed the update; assignment is still pending."
      }
    }
  }));
  assert(hasPending(SERVER_SCOPE_A, "consumed-still-running"));
  assert.strictEqual(pendingCommandCount(), 1);
  renderMicroMachineStatus(serverResult({
    ok: true,
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "consumed-still-running",
      source: "llm",
      assistant_message: "공격 명령을 전술 큐에 반영했습니다."
    },
    update: { update_id: "consumed-still-running" },
    intervention: {
      latest_update_id: "consumed-still-running",
      command_execution: {
        command_id: "consumed-still-running",
        state: "failed",
        completed: false,
        failed: true,
        expired: false,
        blocker_manager: "TacticalEvidence",
        blocker_reason: "No observed tactical effect before the QA deadline."
      }
    }
  }));
  var failedExecutionEntry = logBox.querySelectorAll(".log-entry").find(function (entry) {
    return entry.textContent.includes("소비 후 효과 대기 테스트");
  });
  assert(failedExecutionEntry);
  assert(failedExecutionEntry.textContent.includes("공격 명령을 전술 큐에 반영했습니다."));
  assert(failedExecutionEntry.textContent.includes("실행 실패"));
  assert(failedExecutionEntry.textContent.includes("실제 효과 확인까지 도달하지 못했습니다"));
  assert(failedExecutionEntry.textContent.includes("차단 원인"));
  assert(failedExecutionEntry.textContent.includes("TacticalEvidence"));
  assert(nodes["command-console-verification"].textContent.includes("TacticalEvidence"));
  assert(nodes["command-console-verification"].textContent.includes("No observed tactical effect"));
  assert(nodes["command-console-announcement"].textContent.includes("실행 실패"));
  assert(nodes["command-console-announcement"].textContent.includes("No observed tactical effect"));
  assert.strictEqual(pendingCommandCount(), 0);

  rememberServerPending("LLM 경계 완료 테스트", "async-boundary-complete");
  pendingFor(SERVER_SCOPE_A, "async-boundary-complete").createdAt -= (
    MICROMACHINE_ASYNC_PENDING_TIMEOUT_MS + 1
  );
  renderMicroMachineStatus(serverResult({
    ok: true,
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "async-boundary-complete",
      source: "llm",
      assistant_message: "경계에서도 정상 완료"
    },
    update: { update_id: "async-boundary-complete" },
    intervention: {
      latest_update_id: "async-boundary-complete",
      command_execution: {
        command_id: "async-boundary-complete",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "boundary effect observed"
        })
      }
    }
  }));
  var boundaryEntry = logBox.querySelectorAll(".log-entry").find(function (entry) {
    return entry.textContent.includes("LLM 경계 완료 테스트");
  });
  assert(boundaryEntry);
  assert(boundaryEntry.textContent.includes("경계에서도 정상 완료"));
  assert(boundaryEntry.textContent.includes("실행 확인"));
  assert(!boundaryEntry.textContent.includes("효과 확인 지연"));

  rememberServerPending("LLM 응답 만료 테스트", "async-timeout");
  var asyncCreatedAt = pendingFor(SERVER_SCOPE_A, "async-timeout").createdAt;
  expirePendingMicroMachineAsync(
    asyncCreatedAt + MICROMACHINE_ASYNC_PENDING_TIMEOUT_MS + 1
  );
  assert(hasPending(SERVER_SCOPE_A, "async-timeout"));
  assert.strictEqual(
    pendingFor(SERVER_SCOPE_A, "async-timeout").observationTimedOut,
    true
  );
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 0);
  assert(nodes["command-console-state"].textContent.includes("효과 확인 지연"));
  assert(nodes["command-console-verification"].textContent.includes("늦게 도착한 실제 효과"));
  renderMicroMachineStatus(serverResult({
    ok: true,
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "async-timeout",
      source: "llm",
      assistant_message: "늦게 도착한 완료도 반영합니다."
    },
    update: { update_id: "async-timeout" },
    intervention: {
      latest_update_id: "async-timeout",
      command_execution: {
        command_id: "async-timeout",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "late effect observed"
        })
      }
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "async-timeout"));
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 확인");
  assert(nodes["command-console-verification"].textContent.includes("late effect observed"));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function (entry) {
      return entry.textContent.includes("LLM 응답 만료 테스트");
    }).length,
    1
  );

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
  assert.strictEqual(nodes["command-console-state"].textContent, "게이트웨이 응답 지연");
  assert(nodes["command-console-verification"].textContent.includes("실패로 확정하지 않고"));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function (entry) {
      return entry.textContent.includes("응답이 없어도 pending은 풀어");
    }).length,
    0
  );
  requests[2].deferred.resolve(response(202, {
    ok: true,
    accepted: true,
    async_publish: true,
    status: "accepted",
    update_id: "late-after-submit-timeout",
    consumption_status: "pending_compile"
  }));
  await flushPromises();
  await flushPromises();
  assert(hasPending(SERVER_SCOPE_A, "late-after-submit-timeout"));
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(nodes["command-console-state"].textContent, "명령 해석 중");
  renderMicroMachineStatus = originalRenderMicroMachineStatus;
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "late-after-submit-timeout",
      assistant_message: "지연된 게이트웨이 응답 이후 실행을 확인했습니다."
    },
    update: { update_id: "late-after-submit-timeout" },
    intervention: {
      latest_update_id: "late-after-submit-timeout",
      command_execution: {
        command_id: "late-after-submit-timeout",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "late gateway result reconciled"
        })
      }
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "late-after-submit-timeout"));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function (entry) {
      return entry.textContent.includes("응답이 없어도 pending은 풀어");
    }).length,
    1
  );
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 확인");

  var repeatedText = "같은 문장으로 4마린 공격해";
  var repeatedOldPendingId = appendMicroMachinePendingPlan(repeatedText);
  rememberPendingMicroMachineAsync(
    repeatedText,
    serverResult({
      async_publish: true,
      update_id: "same-text-old"
    }),
    repeatedOldPendingId
  );
  var repeatedOldCreatedAt = pendingFor(SERVER_SCOPE_A, "same-text-old").createdAt;
  expirePendingMicroMachineAsync(
    repeatedOldCreatedAt + MICROMACHINE_ASYNC_PENDING_TIMEOUT_MS + 1
  );
  assert.strictEqual(
    pendingFor(SERVER_SCOPE_A, "same-text-old").pendingId,
    repeatedOldPendingId
  );
  assert.strictEqual(pendingCommandCount(), 0);
  var repeatedNewPendingId = appendMicroMachinePendingPlan(repeatedText);
  assert.notStrictEqual(repeatedOldPendingId, repeatedNewPendingId);
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "same-text-old",
      command_text: repeatedText
    },
    latest_request: {
      update_id: "same-text-old",
      command_text: repeatedText
    },
    update: { update_id: "same-text-old" },
    intervention: {
      latest_update_id: "same-text-old",
      command_execution: {
        command_id: "same-text-old",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "old repeated command effect"
        })
      }
    }
  }));
  assert.strictEqual(activeCommandConsoleRecord.pendingId, repeatedNewPendingId);
  assert.strictEqual(activeCommandConsoleRecord.updateId, "");
  assert.strictEqual(nodes["command-console-state"].textContent, "명령 수신");
  assert(!nodes["active-command-console"].className.includes("command-console-verified"));
  assert.strictEqual(pendingCommandCount(), 1);
  assert.deepStrictEqual(pendingCommandTexts(), [repeatedText]);
  rememberPendingMicroMachineAsync(
    repeatedText,
    serverResult({
      async_publish: true,
      update_id: "same-text-new"
    }),
    repeatedNewPendingId
  );
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      update_id: "same-text-new"
    },
    update: { update_id: "same-text-new" },
    intervention: {
      latest_update_id: "same-text-new",
      command_execution: {
        command_id: "same-text-new",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "new repeated command effect"
        })
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 확인");
  assert(nodes["command-console-verification"].textContent.includes("new repeated command effect"));
  assert(!nodes["command-console-verification"].textContent.includes("old repeated command effect"));

  nodes["command-input"].value = "마린으로 앞마당 압박해";
  nodes["command-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, 4);
  assert.strictEqual(pendingCommandCount(), 1);
  nodes["command-input"].value = "아니 4마린으로 적 본진 우회 공격해";
  nodes["command-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, 5);
  assert.strictEqual(pendingCommandCount(), 2);
  assert.strictEqual(logBox.querySelectorAll(".message-pending").length, 1);
  assert(!logBox.textContent.includes("마린으로 앞마당 압박해"));
  assert(logBox.textContent.includes("아니 4마린으로 적 본진 우회 공격해"));
  requests[3].deferred.resolve(response(202, {
    ok: true,
    accepted: true,
    async_publish: true,
    status: "queued",
    consumption_status: "pending_compile",
    update_id: "stale-pressure",
    command_queue: {
      category: "tactical",
      action: "supersede_tactical",
      superseded_previous: true
    },
    compile_result: {
      status: "compiled",
      source: "ui",
      update_id: "stale-pressure",
      vector: { goal: "stale pressure" }
    },
    update: { update_id: "stale-pressure" }
  }));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(pendingCommandCount(), 2);
  assert(!logBox.textContent.includes("stale pressure"));
  assert.strictEqual(
    nodes["command-console-title"].textContent,
    "아니 4마린으로 적 본진 우회 공격해"
  );
  requests[4].deferred.resolve(response(202, {
    ok: true,
    accepted: true,
    async_publish: true,
    status: "queued",
    consumption_status: "pending_compile",
    update_id: "latest-flank",
    command_queue: {
      category: "tactical",
      action: "supersede_tactical",
      superseded_previous: true,
      superseded_update_ids: ["stale-pressure"]
    },
    compile_result: {
      status: "compiled",
      source: "ui",
      update_id: "latest-flank",
      vector: {
        goal: "latest flank",
        lifetime: {
          mode: "until_completed",
          completion_state: "active",
          completion_conditions: ["order_issued", "target_reached", "ttl_expired"]
        }
      }
    },
    intervention: {
      latest_update_id: "latest-flank",
      goal: "latest flank",
      lifetime: {
        mode: "until_completed",
        completion_state: "active",
        completion_conditions: ["order_issued", "target_reached", "ttl_expired"]
      }
    },
    update: { update_id: "latest-flank" }
  }));
  await flushPromises();
  await flushPromises();
  assert(!hasPending(SERVER_SCOPE_A, "stale-pressure"));
  assert(hasPending(SERVER_SCOPE_A, "latest-flank"));
  assert.strictEqual(pendingCommandCount(), 1);
  assert(!logBox.textContent.includes("stale pressure"));
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    command_queue: {
      category: "tactical",
      action: "supersede_tactical",
      superseded_previous: true
    },
    compile_result: {
      status: "compiled",
      source: "llm",
      update_id: "latest-flank",
      assistant_message: "최신 우회 공격 명령으로 steering했습니다.",
      vector: { goal: "latest flank" }
    },
    update: { update_id: "latest-flank" },
    intervention: {
      latest_update_id: "latest-flank",
      goal: "latest flank",
      command_execution: {
        command_id: "latest-flank",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "flank reached enemy main"
        })
      },
      lifetime: {
        mode: "until_completed",
        completion_state: "completed",
        completion_conditions: ["order_issued", "target_reached", "ttl_expired"]
      }
    }
  }));
  assert.strictEqual(pendingCommandCount(), 0);
  assert(logBox.textContent.includes("latest flank"));
  assert(logBox.textContent.includes("최신 우회 공격 명령으로 steering했습니다."));
  assert(logBox.textContent.includes("실행 확인"));
  assert(logBox.textContent.includes("작전 변경"));
  assert(!logBox.textContent.includes("command_queue |"));
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 확인");
  assert(nodes["active-command-console"].className.includes("command-console-verified"));
  assert(nodes["command-stage-interpret"].className.includes("stage-verified"));
  assert(nodes["command-stage-assign"].className.includes("stage-verified"));
  assert(nodes["command-stage-execute"].className.includes("stage-verified"));
  assert(nodes["command-stage-verify"].className.includes("stage-verified"));

  rememberServerPending("마린 중심 생산 유지", "preserved-macro");
  rememberServerPending("4마린으로 우회 공격", "merged-operation");
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    command_queue: {
      category: "tactical",
      action: "merge_standing_orders",
      layer_action: "merge_cross_layer",
      parent_command_ids: ["preserved-macro"],
      preserved_command_layers: ["macro"],
      merged_command_count: 2
    },
    compile_result: {
      status: "compiled",
      source: "llm",
      update_id: "merged-operation",
      assistant_message: "생산 방침을 유지하면서 우회 공격을 시작합니다.",
      vector: { goal: "marine macro plus flank operation" }
    },
    update: { update_id: "merged-operation" },
    intervention: {
      latest_update_id: "merged-operation",
      command_execution: {
        command_id: "merged-operation",
        state: "consumed_by_manager",
        completed: false,
        failed: false,
        expired: false
      }
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "preserved-macro"));
  assert(hasPending(SERVER_SCOPE_A, "merged-operation"));
  assert.deepStrictEqual(
    pendingFor(SERVER_SCOPE_A, "merged-operation").preservedUpdateIds,
    ["preserved-macro"]
  );
  assert.strictEqual(pendingCommandCount(), 1);
  assert.deepStrictEqual(pendingCommandTexts(), ["4마린으로 우회 공격"]);
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    command_queue: {
      category: "tactical",
      action: "merge_standing_orders",
      layer_action: "merge_cross_layer",
      parent_command_ids: ["preserved-macro"],
      preserved_command_layers: ["macro"],
      merged_command_count: 2
    },
    compile_result: {
      status: "compiled",
      source: "llm",
      update_id: "merged-operation",
      assistant_message: "생산 방침을 유지하면서 우회 공격을 시작합니다.",
      vector: { goal: "marine macro plus flank operation" }
    },
    update: { update_id: "merged-operation" },
    intervention: {
      latest_update_id: "merged-operation",
      command_execution: {
        command_id: "merged-operation",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages()
      }
    }
  }));
  assert.strictEqual(pendingCommandCount(), 0);
  var mergedOperationEntry = logBox.querySelectorAll(".log-entry").find(function (entry) {
    return entry.textContent.includes("4마린으로 우회 공격");
  });
  assert(mergedOperationEntry);
  assert(mergedOperationEntry.textContent.includes("지속 명령 유지"));
  assert(mergedOperationEntry.textContent.includes("명령 통합"));
  assert(!mergedOperationEntry.textContent.includes("preserved_ids="));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function (entry) {
      return entry.textContent.includes("마린 중심 생산 유지");
    }).length,
    0
  );

  rememberServerPending("보존되면 안 되는 이전 명령", "overlap-predecessor");
  rememberServerPending("중복 edge 교체 명령", "overlap-replacement");
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    command_queue: {
      parent_command_ids: ["overlap-predecessor"],
      superseded_update_ids: ["overlap-predecessor"]
    },
    compile_result: {
      status: "compiled",
      update_id: "overlap-replacement"
    },
    update: { update_id: "overlap-replacement" },
    intervention: {
      command_execution: {
        command_id: "overlap-replacement",
        state: "consumed_by_manager",
        completed: false,
        failed: false,
        expired: false
      }
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "overlap-predecessor"));
  assert.deepStrictEqual(
    pendingFor(SERVER_SCOPE_A, "overlap-replacement").supersededUpdateIds,
    ["overlap-predecessor"]
  );
  assert.deepStrictEqual(
    pendingFor(SERVER_SCOPE_A, "overlap-replacement").preservedUpdateIds,
    []
  );
  assert.deepStrictEqual(
    pendingFor(SERVER_SCOPE_A, "overlap-replacement").preservedCommandTexts,
    []
  );
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "overlap-replacement"
    },
    update: { update_id: "overlap-replacement" },
    intervention: {
      command_execution: {
        command_id: "overlap-replacement",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages()
      }
    }
  }));
  var overlapEntry = logBox.querySelectorAll(".log-entry").find(function (entry) {
    return entry.textContent.includes("중복 edge 교체 명령");
  });
  assert(overlapEntry);
  assert(overlapEntry.textContent.includes("작전 변경"));
  assert(!overlapEntry.textContent.includes("지속 명령 유지"));
  assert(!overlapEntry.textContent.includes("superseded_ids="));
  assert.strictEqual(pendingCommandCount(), 0);

  rememberServerPending("실행 중 교체되는 명령", "superseded-running");
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "superseded-running"
    },
    update: { update_id: "superseded-running" },
    intervention: {
      latest_update_id: "superseded-running",
      telemetry_frame: 700,
      command_execution: {
        command_id: "superseded-running",
        state: "action_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: observedExecutionStages().slice(0, 6)
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "전장에서 실행 중");
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "superseded",
    compile_result: {
      status: "compiled",
      update_id: "superseded-running"
    },
    update: { update_id: "superseded-running" },
    intervention: {
      latest_update_id: "superseded-running",
      telemetry_frame: 710,
      command_execution: {
        command_id: "superseded-running",
        state: "superseded",
        completed: false,
        failed: false,
        expired: false,
        stages: []
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "작전 교체");
  assert(nodes["active-command-console"].className.includes("command-console-superseded"));
  assert(!hasPending(SERVER_SCOPE_A, "superseded-running"));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function (entry) {
      return entry.textContent.includes("실행 중 교체되는 명령");
    }).length,
    1
  );
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "superseded-running"
    },
    update: { update_id: "superseded-running" },
    intervention: {
      latest_update_id: "superseded-running",
      telemetry_frame: 720,
      command_execution: {
        command_id: "superseded-running",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "late effect must not revive superseded order"
        })
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "작전 교체");
  assert(nodes["active-command-console"].className.includes("command-console-superseded"));
  assert(!nodes["active-command-console"].className.includes("command-console-verified"));
  assert(!nodes["command-console-verification"].textContent.includes("late effect must not revive"));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function (entry) {
      return entry.textContent.includes("실행 중 교체되는 명령");
    }).length,
    1
  );

  rememberServerPending("실패 terminal 고정 명령", "failed-terminal-sticky");
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "failed-terminal-sticky"
    },
    update: { update_id: "failed-terminal-sticky" },
    intervention: {
      latest_update_id: "failed-terminal-sticky",
      telemetry_frame: 730,
      command_execution: {
        command_id: "failed-terminal-sticky",
        state: "failed",
        completed: false,
        failed: true,
        expired: false,
        blocker_manager: "CombatCommander",
        blocker_reason: "no eligible combat units",
        stages: []
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 실패");
  assert(nodes["command-console-verification"].textContent.includes("no eligible combat units"));
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "failed-terminal-sticky"
    },
    update: { update_id: "failed-terminal-sticky" },
    intervention: {
      latest_update_id: "failed-terminal-sticky",
      telemetry_frame: 740,
      command_execution: {
        command_id: "failed-terminal-sticky",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "late effect must not revive failed order"
        })
      }
    }
  }));
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 실패");
  assert(nodes["active-command-console"].className.includes("command-console-blocked"));
  assert(!nodes["active-command-console"].className.includes("command-console-verified"));
  assert(!nodes["command-console-verification"].textContent.includes("late effect must not revive"));

  [
    {
      state: "cancelled",
      suffix: "cancelled",
      blockerReason: "cancelled_by_policy",
      expectedNarration: "작전 취소"
    },
    {
      state: "canceled",
      suffix: "canceled",
      blockerReason: "cancelled_by_policy",
      expectedNarration: "작전 취소"
    },
    {
      state: "blocked",
      suffix: "blocked",
      blockerReason: "no eligible combat units",
      expectedNarration: "실행 실패"
    },
    {
      state: "rejected",
      suffix: "rejected",
      blockerReason: "invalid operation target",
      expectedNarration: "실행 실패"
    }
  ].forEach(function(contract) {
    var updateId = "async-terminal-" + contract.suffix;
    var commandText = "async terminal " + contract.suffix;
    rememberServerPending(commandText, updateId);
    assert(hasPending(SERVER_SCOPE_A, updateId));
    renderMicroMachineStatus(serverResult({
      ok: true,
      status: "published",
      compile_result: {
        status: "compiled",
        update_id: updateId
      },
      update: { update_id: updateId },
      intervention: {
        latest_update_id: updateId,
        telemetry_frame: 750,
        command_execution: {
          command_id: updateId,
          state: contract.state,
          completed: false,
          failed: (
            contract.state === "blocked" ||
            contract.state === "rejected"
          ),
          expired: false,
          blocker_manager: "OperationDirector",
          blocker_reason: contract.blockerReason,
          stages: []
        }
      }
    }));
    assert(!hasPending(SERVER_SCOPE_A, updateId));
    var terminalEntries = logBox.querySelectorAll(".log-entry").filter(
      function(entry) {
        return entry.textContent.includes(commandText);
      }
    );
    assert.strictEqual(terminalEntries.length, 1);
    assert(
      terminalEntries[0].textContent.includes(contract.expectedNarration)
    );
  });

  var supersededModel = commandConsoleStageModel({
    status: "superseded",
    intervention: {
      command_execution: {
        command_id: "superseded-ui-contract",
        state: "superseded",
        completed: false,
        failed: false,
        expired: false,
        stages: []
      }
    }
  });
  assert.strictEqual(supersededModel.superseded, true);
  assert.strictEqual(supersededModel.blocked, false);
  assert(commandConsoleClassName(supersededModel).includes("command-console-superseded"));

  var cancelledData = {
    status: "published",
    intervention: {
      command_execution: {
        command_id: "cancelled-ui-contract",
        state: "cancelled",
        completed: false,
        failed: false,
        expired: false,
        blocker_reason: "cancelled_by_policy",
        terminal_cleanup: {
          action: "release_stop|cancelled_by_policy",
          frame: 810,
          operation_id: "assault-bravo",
          generation: 1
        },
        operation_id: "assault-bravo",
        operation_generation: 1,
        stages: observedExecutionStages({
          confirmation_effect: "cleanup stop must not become mission effect"
        })
      }
    }
  };
  var cancelledModel = commandConsoleStageModel(cancelledData);
  assert.strictEqual(cancelledModel.cancelled, true);
  assert.strictEqual(cancelledModel.effectObserved, false);
  assert.strictEqual(cancelledModel.blocked, false);
  assert.strictEqual(commandConsoleStateLabel(cancelledModel), "작전 취소");
  assert(commandConsoleClassName(cancelledModel).includes("command-console-superseded"));
  assert(commandConsoleVerification(cancelledData, cancelledModel).includes("작전 취소"));
  assert(!commandConsoleVerification(cancelledData, cancelledModel).includes("cleanup stop must not become mission effect"));
  updateMicroMachineBadge(cancelledData.intervention, cancelledData.status);
  assert.strictEqual(nodes["micromachine-applied-badge"].textContent, "작전 취소");
  assert(nodes["micromachine-applied-badge"].className.includes("micro-badge-cancelled"));
  assert(!nodes["micromachine-applied-badge"].className.includes("micro-badge-applied"));
  var cancelledNarration = microMachineChatNarration(cancelledData);
  assert(cancelledNarration.includes("작전 취소"));
  assert(!cancelledNarration.includes("실행 확인"));

  var noOwnedUnitsCancellationData = {
    status: "published",
    intervention: {
      command_execution: {
        command_id: "cancelled-no-owned-units-ui-contract",
        state: "cancelled",
        completed: false,
        failed: false,
        expired: false,
        blocker_reason: "cancelled_by_policy",
        terminal_cleanup: {
          action: "release_no_owned_units|cancelled_by_policy",
          frame: 815,
          operation_id: "recon-alpha",
          generation: 3
        },
        operation_id: "recon-alpha",
        operation_generation: 3,
        stages: []
      }
    }
  };
  var noOwnedUnitsCancellationModel = commandConsoleStageModel(
    noOwnedUnitsCancellationData
  );
  assert.strictEqual(
    commandConsoleTerminalCleanupVerified(noOwnedUnitsCancellationModel),
    true
  );
  assert.strictEqual(
    commandConsoleTerminalCleanupStoppedOwnedUnits(
      noOwnedUnitsCancellationModel
    ),
    false
  );
  assert.strictEqual(
    commandConsoleStateLabel(noOwnedUnitsCancellationModel),
    "작전 취소"
  );
  assert(
    commandConsoleVerification(
      noOwnedUnitsCancellationData,
      noOwnedUnitsCancellationModel
    ).includes("중지 명령 없이")
  );
  assert(
    microMachineChatNarration(noOwnedUnitsCancellationData).includes(
      "중지 명령 없이"
    )
  );

  var cancellationPendingCleanupData = {
    status: "published",
    intervention: {
      command_execution: {
        command_id: "cancelled-pending-cleanup-ui-contract",
        operation_id: "assault-bravo",
        operation_generation: 1,
        state: "cancelled",
        completed: false,
        failed: false,
        expired: false,
        blocker_reason: "cancelled_by_policy",
        terminal_cleanup: {},
        stages: []
      }
    }
  };
  var cancellationPendingCleanupModel = commandConsoleStageModel(
    cancellationPendingCleanupData
  );
  var cancellationPendingCleanupVerification = commandConsoleVerification(
    cancellationPendingCleanupData,
    cancellationPendingCleanupModel
  );
  assert(cancellationPendingCleanupVerification.includes("취소 요청 수락"));
  assert(cancellationPendingCleanupVerification.includes("증거를 기다립니다"));
  assert(!cancellationPendingCleanupVerification.includes("기존 명령을 중지"));
  assert.strictEqual(
    commandConsoleStateLabel(cancellationPendingCleanupModel),
    "취소 정리 확인 중"
  );
  assert(
    commandConsoleClassName(cancellationPendingCleanupModel).includes(
      "command-console-waiting"
    )
  );
  assert(
    !commandConsoleClassName(cancellationPendingCleanupModel).includes(
      "command-console-executing"
    )
  );
  assert(
    !commandConsoleClassName(cancellationPendingCleanupModel).includes(
      "command-console-superseded"
    )
  );
  updateMicroMachineBadge(
    cancellationPendingCleanupData.intervention,
    cancellationPendingCleanupData.status
  );
  assert.strictEqual(
    nodes["micromachine-applied-badge"].textContent,
    "취소 정리 확인 중"
  );
  assert(
    nodes["micromachine-applied-badge"].className.includes(
      "micro-badge-pending"
    )
  );
  assert(
    !nodes["micromachine-applied-badge"].className.includes(
      "micro-badge-cancelled"
    )
  );
  var cancellationPendingCleanupNarration = microMachineChatNarration(
    cancellationPendingCleanupData
  );
  assert(cancellationPendingCleanupNarration.includes("취소 요청을 수락"));
  assert(cancellationPendingCleanupNarration.includes("증거를 기다립니다"));
  assert(!cancellationPendingCleanupNarration.includes("기존 명령을 중지"));

  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("공격을 취소하지 말고 계속 압박해"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("후퇴하지 말고 버텨"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("철수하지 말고 계속 공격해"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("공격을 중단하지 말고 계속 압박해"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("작전을 중단하지 마"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("공격 중단 없이 계속 밀어"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("공격 중단 금지"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("후퇴 금지"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("철수 없이 압박 유지"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("후퇴 말고 공격해"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("no retreat"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("retreat is not an option"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("do not stop the attack"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("긴급 공격 시작"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("emergency attack now"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("마린 생산 중단하고 탱크 생산해"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("stop producing marines and build tanks"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("배럭 건설 취소하고 팩토리 지어"),
    false
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("긴급 후퇴"),
    true
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("후퇴해"),
    true
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("fall back now"),
    true
  );
  assert.strictEqual(
    looksLikeMicroMachineEmergencyCommand("stop the attack and regroup"),
    true
  );

  rememberServerPending("오래된 일반 공격", "stale-before-emergency");
  pendingFor(SERVER_SCOPE_A, "stale-before-emergency").supersededUpdateIds.push(
    "older-root-command"
  );
  rememberServerPending("긴급 즉시 후퇴", "urgent-retreat");
  assert(hasPending(SERVER_SCOPE_A, "stale-before-emergency"));
  assert(hasPending(SERVER_SCOPE_A, "urgent-retreat"));
  // Text alone cannot sweep pending commands, including emergency-looking text.
  assert.strictEqual(pendingCommandCount(), 2);
  renderMicroMachineStatus(serverResult({
    enabled: true,
    status: "superseded",
    command_queue: {
      action: "superseded_by_emergency",
      superseded_by_update_id: "urgent-retreat"
    },
    compile_result: {
      status: "refused",
      update_id: "stale-before-emergency",
      refusal_reason: "superseded by emergency"
    },
    dashboard: { active_updates: [] },
    intervention: {}
  }));
  assert(!hasPending(SERVER_SCOPE_A, "stale-before-emergency"));
  assert(hasPending(SERVER_SCOPE_A, "urgent-retreat"));
  assert.deepStrictEqual(
    pendingFor(SERVER_SCOPE_A, "urgent-retreat").supersededUpdateIds,
    ["stale-before-emergency", "older-root-command"]
  );
  renderMicroMachineStatus(serverResult({
    ok: true,
    status: "published",
    consumption_status: "consumed",
    compile_result: {
      status: "compiled",
      source: "llm",
      update_id: "urgent-retreat",
      assistant_message: "긴급 후퇴를 최우선 명령으로 적용했습니다."
    },
    update: { update_id: "urgent-retreat" },
    intervention: {
      latest_update_id: "urgent-retreat",
      command_execution: {
        command_id: "urgent-retreat",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "retreat movement observed"
        })
      }
    }
  }));
  assert.strictEqual(pendingCommandCount(), 0);
  var emergencyEntry = logBox.querySelectorAll(".log-entry").find(function (entry) {
    return entry.textContent.includes("긴급 즉시 후퇴");
  });
  assert(emergencyEntry);
  assert(emergencyEntry.textContent.includes("긴급 후퇴를 최우선 명령으로 적용했습니다."));
  assert(emergencyEntry.textContent.includes("작전 변경"));
  assert(!emergencyEntry.textContent.includes("superseded_previous=true"));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function (entry) {
      return entry.textContent.includes("오래된 일반 공격");
    }).length,
    0
  );

  // Replay must not append a second terminal chat result for one immutable ID.
  rememberServerPending("replay once", "replay-once");
  var replayResult = serverResult({
    ok: true,
    status: "published",
    compile_result: { status: "compiled", update_id: "replay-once" },
    update: { update_id: "replay-once" },
    intervention: {
      command_execution: {
        command_id: "replay-once",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages()
      }
    }
  });
  renderMicroMachineStatus(replayResult);
  renderMicroMachineStatus(replayResult);
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function(entry) {
      return entry.textContent.includes("replay once");
    }).length,
    1
  );

  // Out-of-order C, B, A processing follows only their explicit edges.
  rememberServerPending("chain A", "chain-a");
  rememberServerPending("chain B", "chain-b");
  rememberServerPending("chain C", "chain-c");
  renderMicroMachineStatus(serverResult({
    status: "published",
    command_queue: { superseded_update_ids: ["chain-b"] },
    compile_result: { status: "compiled", update_id: "chain-c" },
    update: { update_id: "chain-c" },
    intervention: {
      command_execution: {
        command_id: "chain-c",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages()
      }
    }
  }));
  assert(hasPending(SERVER_SCOPE_A, "chain-a"));
  assert(!hasPending(SERVER_SCOPE_A, "chain-b"));
  renderMicroMachineStatus(serverResult({
    status: "superseded",
    command_queue: { superseded_update_ids: ["chain-a"] },
    compile_result: {
      status: "refused",
      update_id: "chain-b",
      refusal_reason: "replaced by C"
    }
  }));
  assert(!hasPending(SERVER_SCOPE_A, "chain-a"));
  assert.strictEqual(
    logBox.querySelectorAll(".log-entry").filter(function(entry) {
      return entry.textContent.includes("chain A") ||
        entry.textContent.includes("chain B");
    }).length,
    0
  );

  // A predecessor result may arrive before the replacement HTTP 202. Keep the
  // predecessor pending until the replacement record exists, then transfer it.
  rememberServerPending("early predecessor", "early-predecessor");
  renderMicroMachineStatus(serverResult({
    status: "superseded",
    command_queue: {
      superseded_by_update_id: "late-replacement"
    },
    compile_result: {
      status: "refused",
      update_id: "early-predecessor",
      refusal_reason: "replacement accepted before its 202 response"
    }
  }));
  assert(hasPending(SERVER_SCOPE_A, "early-predecessor"));
  assert.strictEqual(
    pendingCommandCount(),
    1,
    "deferred predecessor remains pending"
  );
  assert.deepStrictEqual(pendingCommandTexts(), ["early predecessor"]);
  rememberServerPending(
    "late replacement command",
    "late-replacement",
    SERVER_SCOPE_A
  );
  assert(!hasPending(SERVER_SCOPE_A, "early-predecessor"));
  assert(hasPending(SERVER_SCOPE_A, "late-replacement"));
  assert.deepStrictEqual(
    pendingFor(SERVER_SCOPE_A, "late-replacement").supersededUpdateIds,
    ["early-predecessor"]
  );
  assert.strictEqual(
    pendingCommandCount(),
    1,
    "replacement owns one pending bubble"
  );
  renderMicroMachineStatus(serverResult({
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "late-replacement"
    },
    update: { update_id: "late-replacement" },
    intervention: {
      command_execution: {
        command_id: "late-replacement",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages()
      }
    }
  }));
  assert.strictEqual(
    pendingCommandCount(),
    0,
    "replacement terminal result clears pending"
  );

  // Equal update IDs in two server scopes remain isolated.
  rememberServerPending("scope A pending", "shared-id", SERVER_SCOPE_A);
  rememberServerPending("scope B pending", "shared-id", SERVER_SCOPE_B);
  renderMicroMachineStatus(serverResult({
    status: "superseded",
    compile_result: {
      status: "refused",
      update_id: "shared-id",
      refusal_reason: "scope A only"
    }
  }, SERVER_SCOPE_A));
  assert(!hasPending(SERVER_SCOPE_A, "shared-id"));
  assert(hasPending(SERVER_SCOPE_B, "shared-id"));

  renderMicroMachineStatus(serverResult({
    enabled: true,
    status: "published",
    intervention: {
      latest_update_id: "old-directory-evidence",
      telemetry_frame: 799,
      tactical_posture: "old-directory-posture",
      command_execution: {
        command_id: "old-directory-evidence",
        state: "action_issued",
        stages: observedExecutionStages().slice(0, 6)
      },
      log_snippets: [
        { source: "old.log", line: "old-directory-marker" }
      ]
    }
  }, SERVER_SCOPE_A));
  assert(nodes["micromachine-raw-evidence"].textContent.includes("old-directory-marker"));
  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-accepted-scope-a";
  synchronizeMicroMachineBlackboardDirectory("/tmp/voi-mm-accepted-scope-a");
  assert(!hasPending(SERVER_SCOPE_B, "shared-id"));
  assert.strictEqual(nodes["micromachine-latest-update"].textContent, "-");
  assert.strictEqual(nodes["micromachine-frame"].textContent, "-");
  assert.strictEqual(nodes["micromachine-command-execution"].textContent, "-");
  assert(!nodes["micromachine-raw-evidence"].textContent.includes("old-directory-marker"));
  assert(nodes["micromachine-status"].textContent.includes("새 MicroMachine blackboard"));
  nodes["command-input"].value = "accepted scope A async order";
  nodes["command-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  var acceptedScopeARequest = requests[requests.length - 1];
  acceptedScopeARequest.deferred.resolve(response(202, serverResult({
    ok: true,
    accepted: true,
    async_publish: true,
    status: "queued",
    update_id: "accepted-scope-a"
  }, SERVER_SCOPE_A)));
  await flushPromises();
  await flushPromises();
  assert(hasPending(SERVER_SCOPE_A, "accepted-scope-a"));
  assert.strictEqual(pendingCommandCount(), 1);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "true");
  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-accepted-scope-b";
  synchronizeMicroMachineBlackboardDirectory("/tmp/voi-mm-accepted-scope-b");
  assert(!hasPending(SERVER_SCOPE_A, "accepted-scope-a"));
  assert.strictEqual(pendingCommandCount(), 0);
  assert.strictEqual(logBox.getAttribute("aria-busy"), "false");
  assert.strictEqual(activeCommandConsoleRecord.updateId, "");

  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-slow-poll";
  synchronizeMicroMachineBlackboardDirectory("/tmp/voi-mm-slow-poll");
  var slowPollStart = requests.length;
  pollMicroMachineStatus();
  pollMicroMachineStatus();
  assert.strictEqual(requests.length, slowPollStart + 1);
  var slowPollFirst = requests[slowPollStart];
  slowPollFirst.deferred.resolve(response(200, {
    enabled: true,
    status: "slow-first-applied",
    dashboard: { telemetry: { frame: 801 } }
  }));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(requests.length, slowPollStart + 2);
  var slowPollSecond = requests[slowPollStart + 1];
  assert(nodes["micromachine-status"].textContent.includes("slow-first-applied"));
  assert.strictEqual(microMachinePollAppliedSeq, microMachinePollRequestSeq - 1);
  slowPollSecond.deferred.resolve(response(200, {
    enabled: true,
    status: "slow-second-applied",
    dashboard: { telemetry: { frame: 802 } }
  }));
  await flushPromises();
  await flushPromises();
  assert(nodes["micromachine-status"].textContent.includes("slow-second-applied"));
  assert.strictEqual(microMachinePollAppliedSeq, microMachinePollRequestSeq);

  var stuckPollStart = requests.length;
  pollMicroMachineStatus();
  pollMicroMachineStatus();
  assert.strictEqual(requests.length, stuckPollStart + 1);
  var stuckPoll = requests[stuckPollStart];
  var stuckPollTimeout = timeoutCallbacks[timeoutCallbacks.length - 1];
  stuckPollTimeout();
  assert.strictEqual(requests.length, stuckPollStart + 2);
  assert.strictEqual(microMachinePollInFlight, true);
  assert(nodes["micromachine-status"].textContent.includes("새 요청으로 재시도"));
  if (stuckPoll.options.signal) {
    assert.strictEqual(stuckPoll.options.signal.aborted, true);
  }
  stuckPoll.deferred.resolve(response(200, {
    enabled: true,
    status: "timed-out poll must be ignored",
    dashboard: { telemetry: { frame: 999 } }
  }));
  await flushPromises();
  await flushPromises();
  assert(!nodes["micromachine-status"].textContent.includes("timed-out poll must be ignored"));
  var retryPoll = requests[stuckPollStart + 1];
  retryPoll.deferred.resolve(response(200, {
    enabled: true,
    status: "timeout retry recovered",
    dashboard: { telemetry: { frame: 803 } }
  }));
  await flushPromises();
  await flushPromises();
  assert(nodes["micromachine-status"].textContent.includes("timeout retry recovered"));
  assert.strictEqual(microMachinePollInFlight, false);

  var failedPollStart = requests.length;
  pollMicroMachineStatus();
  pollMicroMachineStatus();
  assert.strictEqual(requests.length, failedPollStart + 1);
  var failedPoll = requests[failedPollStart];
  failedPoll.deferred.resolve(response(500, {
    error: "newest poll failed"
  }));
  await flushPromises();
  await flushPromises();
  assert(nodes["micromachine-status"].textContent.includes("newest poll failed"));
  assert.strictEqual(requests.length, failedPollStart + 2);
  var queuedPollAfterFailure = requests[failedPollStart + 1];
  queuedPollAfterFailure.deferred.resolve(response(200, {
    enabled: true,
    status: "queued poll recovered",
    dashboard: { telemetry: { frame: 803 } }
  }));
  await flushPromises();
  await flushPromises();
  assert(nodes["micromachine-status"].textContent.includes("queued poll recovered"));
  assert.strictEqual(microMachinePollInFlight, false);

  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-submit-scope-a";
  synchronizeMicroMachineBlackboardDirectory("/tmp/voi-mm-submit-scope-a");
  var pendingCountBeforeStaleSubmit = pendingCommandCount();
  nodes["command-input"].value = "scope A late order";
  nodes["command-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  var staleSubmitRequest = requests[requests.length - 1];
  var staleSubmitPendingId = activeCommandConsoleRecord.pendingId;
  assert(staleSubmitPendingId);
  assert.strictEqual(pendingCommandCount(), pendingCountBeforeStaleSubmit + 1);
  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-submit-scope-b";
  synchronizeMicroMachineBlackboardDirectory("/tmp/voi-mm-submit-scope-b");
  var currentScopePollStart = requests.length;
  pollMicroMachineStatus();
  var currentScopePoll = requests[currentScopePollStart];
  currentScopePoll.deferred.resolve(response(200, serverResult({
    enabled: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "scope-b-current-order",
      vector: { goal: "scope B current order" }
    },
    update: { update_id: "scope-b-current-order" },
    intervention: {
      latest_update_id: "scope-b-current-order",
      telemetry_frame: 810,
      command_execution: {
        command_id: "scope-b-current-order",
        state: "action_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: observedExecutionStages().slice(0, 6)
      }
    }
  }, SERVER_SCOPE_B)));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(nodes["command-console-title"].textContent, "scope B current order");
  staleSubmitRequest.deferred.resolve(response(202, serverResult({
    ok: true,
    accepted: true,
    async_publish: false,
    status: "published",
    update_id: "scope-a-late-order",
    compile_result: {
      status: "compiled",
      update_id: "scope-a-late-order",
      vector: { goal: "scope A late order" }
    },
    update: { update_id: "scope-a-late-order" }
  }, SERVER_SCOPE_A)));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(nodes["command-console-title"].textContent, "scope B current order");
  assert.strictEqual(activeCommandConsoleRecord.scopeId, SERVER_SCOPE_B);
  assert(!logBox.textContent.includes("scope A late order"));
  assert.strictEqual(pendingCommandCount(), pendingCountBeforeStaleSubmit);

  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-scope-a";
  microMachinePollBlackboardDir = "/tmp/voi-mm-scope-a";
  var scopePollStart = requests.length;
  pollMicroMachineStatus();
  assert.strictEqual(requests.length, scopePollStart + 1);
  var staleScopePoll = requests[scopePollStart];
  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-scope-b";
  pollMicroMachineStatus();
  assert.strictEqual(requests.length, scopePollStart + 2);
  assert.strictEqual(nodes["command-console-announcement"].textContent, "");
  var latestScopePoll = requests[scopePollStart + 1];
  staleScopePoll.deferred.resolve(response(200, serverResult({
    enabled: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "scope-a-effect",
      vector: { goal: "stale scope A effect" }
    },
    update: { update_id: "scope-a-effect" },
    intervention: {
      latest_update_id: "scope-a-effect",
      goal: "stale scope A effect",
      command_execution: {
        command_id: "scope-a-effect",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "scope A should be ignored"
        })
      }
    }
  }, SERVER_SCOPE_A)));
  latestScopePoll.deferred.resolve(response(200, serverResult({
    enabled: true,
    status: "published",
    compile_result: {
      status: "compiled",
      update_id: "scope-b-effect",
      vector: { goal: "current scope B effect" }
    },
    update: { update_id: "scope-b-effect" },
    intervention: {
      latest_update_id: "scope-b-effect",
      goal: "current scope B effect",
      command_execution: {
        command_id: "scope-b-effect",
        state: "completed",
        completed: true,
        failed: false,
        expired: false,
        stages: observedExecutionStages({
          confirmation_effect: "scope B is current"
        })
      }
    }
  }, SERVER_SCOPE_B)));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(activeCommandConsoleRecord.scopeId, SERVER_SCOPE_B);
  assert.strictEqual(nodes["command-console-title"].textContent, "current scope B effect");
  assert(nodes["command-console-verification"].textContent.includes("scope B is current"));
  assert(!nodes["command-console-verification"].textContent.includes("scope A should be ignored"));

  nodes["micromachine-command-input"].value = "고급 직접 publish timeout";
  var directTimeoutStart = requests.length;
  nodes["micromachine-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, directTimeoutStart + 1);
  var directTimeoutRequest = requests[directTimeoutStart];
  timeoutCallbacks[timeoutCallbacks.length - 1]();
  assert.strictEqual(nodes["command-console-state"].textContent, "게이트웨이 응답 지연");
  assert(nodes["command-console-verification"].textContent.includes("실패로 확정하지 않고"));

  nodes["micromachine-command-input"].value = "고급 직접 publish A";
  var directPublishStart = requests.length;
  nodes["micromachine-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, directPublishStart + 1);
  assert.strictEqual(
    nodes["command-console-title"].textContent,
    "고급 직접 publish A"
  );
  var directPublishARequest = requests[directPublishStart];
  var directPublishAOperationId = activeCommandConsoleRecord.pendingId;
  var directPublishATimeout = timeoutCallbacks[timeoutCallbacks.length - 1];
  directTimeoutRequest.deferred.resolve(response(202, serverResult({
    ok: true,
    accepted: true,
    async_publish: false,
    status: "published",
    update_id: "direct-publish-timeout-late",
    compile_result: {
      status: "compiled",
      update_id: "direct-publish-timeout-late",
      vector: { goal: "late timeout response must not replace direct A" }
    },
    update: { update_id: "direct-publish-timeout-late" }
  }, SERVER_SCOPE_B)));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(nodes["command-console-title"].textContent, "고급 직접 publish A");
  nodes["micromachine-command-input"].value = "고급 직접 publish B";
  nodes["micromachine-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  assert.strictEqual(requests.length, directPublishStart + 2);
  assert.strictEqual(nodes["command-console-title"].textContent, "고급 직접 publish B");
  var directPublishBRequest = requests[directPublishStart + 1];
  var directPublishBOperationId = activeCommandConsoleRecord.pendingId;
  assert.notStrictEqual(directPublishAOperationId, directPublishBOperationId);
  directPublishATimeout();
  assert.strictEqual(nodes["command-console-title"].textContent, "고급 직접 publish B");
  assert.strictEqual(nodes["command-console-state"].textContent, "명령 수신");
  directPublishBRequest.deferred.resolve(response(202, serverResult({
    ok: true,
    accepted: true,
    async_publish: false,
    status: "published",
    update_id: "direct-publish-b",
    compile_result: {
      status: "compiled",
      update_id: "direct-publish-b",
      vector: { goal: "direct publish B battlefield operation" }
    },
    update: { update_id: "direct-publish-b" }
  }, SERVER_SCOPE_B)));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(activeCommandConsoleRecord.updateId, "direct-publish-b");
  assert.strictEqual(activeCommandConsoleRecord.scopeId, SERVER_SCOPE_B);
  directPublishARequest.deferred.resolve(response(202, serverResult({
    ok: true,
    accepted: true,
    async_publish: false,
    status: "published",
    update_id: "direct-publish-a",
    compile_result: {
      status: "compiled",
      update_id: "direct-publish-a",
      vector: { goal: "stale direct publish A operation" }
    },
    update: { update_id: "direct-publish-a" }
  }, SERVER_SCOPE_B)));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(activeCommandConsoleRecord.updateId, "direct-publish-b");
  assert.strictEqual(nodes["command-console-title"].textContent, "고급 직접 publish B");

  nodes["micromachine-command-input"].value = "고급 직접 publish 실패";
  var directPublishFailureStart = requests.length;
  nodes["micromachine-form"].dispatchEvent({
    type: "submit",
    preventDefault: function () {}
  });
  var directPublishFailureRequest = requests[directPublishFailureStart];
  directPublishFailureRequest.deferred.resolve(response(500, {
    error: "direct publish backend down"
  }));
  await flushPromises();
  await flushPromises();
  assert.strictEqual(nodes["command-console-title"].textContent, "고급 직접 publish 실패");
  assert.strictEqual(nodes["command-console-state"].textContent, "실행 실패");
  assert(nodes["command-console-verification"].textContent.includes("direct publish backend down"));

  // Parallel operation cards reconcile independently, even when responses arrive B then A.
  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-operation-cards";
  synchronizeMicroMachineBlackboardDirectory("/tmp/voi-mm-operation-cards");
  var OPERATION_SCOPE = "parallel-operation-scope";
  function operationResult(
    operationId,
    updateId,
    commandText,
    mission,
    frame,
    state,
    stages,
    generation
  ) {
    generation = generation || 1;
    var terminal = state === "completed";
    var submitted = state === "action_issued" || terminal;
    var requestedCount = mission === "scouting" ? 1 : 4;
    return {
      operation_id: operationId,
      operation_generation: generation,
      update_id: updateId,
      command_text: commandText,
      mission: mission,
      transport_status: "published",
      consumption_status: "consumed",
      telemetry_frame: frame,
      disposition: "active",
      operation_convergence: {
        target_count: requestedCount,
        represented_count: requestedCount,
        missing_count: 0,
        blocker: "",
        requirements: []
      },
      battlefield_operation: {
        identity: {
          update_id: updateId,
          scope: "operation:" + operationId,
          session_epoch: 1700000000000,
          operation_id: operationId,
          generation: generation,
          stage: terminal ? "completed" : (submitted ? "submitted" : "assigned"),
          game_frame: frame
        },
        operation_id: operationId,
        generation: generation,
        operation_route: {
          requested_route_type: mission === "scouting" ? "direct" : "flank_right",
          applied_route_type: mission === "scouting" ? "direct" : "flank_right",
          location_intent: "enemy_natural",
          target_type: "enemy_expansion",
          resolved_target_label: "enemy natural",
          target_x: 120,
          target_y: 44,
          target_evidence: "observed_enemy_structure"
        },
        operation_lifetime: {
          mode: "until_completed",
          completion_state: terminal ? "completed" : "active",
          completion_conditions: ["target_reached", "cancelled_by_user"],
          duration_seconds: 300,
          issued_at_frame: 200,
          deadline_frame: 4700,
          standing: false,
          completed: terminal,
          completion_reason: terminal ? "target_reached" : "",
          completed_frame: terminal ? frame : 0
        },
        operation_ownership: {
          owner_count: requestedCount,
          integrity_status: "valid"
        },
        operation_launch_policy: {
          min_units: requestedCount,
          max_units: requestedCount,
          allow_partial_requested: false,
          strict_scope: true,
          partial_launch_allowed: false,
          partial_launch_safe: false,
          launch_count: requestedCount,
          missing_count: 0,
          decision: "launch",
          blocker: "",
          recommended_choices: [],
          safety_evidence: {}
        },
        operation_completion: {
          movement_observed: submitted,
          engagement_observed: terminal,
          target_reached: terminal,
          terminal: terminal,
          state: terminal ? "completed" : "active",
          reason: terminal ? "target_reached" : "",
          frame: terminal ? frame : 0,
          generation: generation
        }
      },
      semantic_timeline: [
        {
          timeline_seq: frame,
          operation_id: operationId,
          generation: generation,
          kind: submitted ? "submitted" : "assigned",
          game_frame: frame,
          summary: submitted ? "SC2 action submitted" : "Force assigned",
          technical: { state: state }
        }
      ],
      update: {
        update_id: updateId,
        vector: { goal: commandText, operation_id: operationId }
      },
      intervention: {
        telemetry_frame: frame,
        command_execution: {
          command_id: updateId,
          operation_id: operationId,
          operation_generation: generation,
          state: state,
          completed: state === "completed",
          failed: false,
          expired: false,
          stages: stages
        }
      }
    };
  }
  function actionStages(action, effect) {
    var stages = observedExecutionStages(
      effect ? { confirmation_effect: effect } : undefined
    );
    stages[4].evidence.last_issued_action = action;
    stages[5].evidence.last_actual_command = action;
    return stages;
  }

  var foreignExecutionOperation = operationResult(
    "new-operation",
    "new-update",
    "새 작전은 자기 실행 증거를 기다림",
    "attack",
    205,
    "queued_or_assigned",
    observedExecutionStages().slice(0, 4),
    1
  );
  foreignExecutionOperation.intervention.command_execution = {
    command_id: "old-update",
    operation_id: "old-operation",
    operation_generation: 7,
    state: "completed",
    completed: true,
    failed: false,
    expired: false,
    stages: actionStages(
      "attack",
      "foreign operation effect must not be reused"
    )
  };
  var sanitizedForeignOperation = commandOperationData(
    foreignExecutionOperation,
    serverResult({}, OPERATION_SCOPE)
  );
  var sanitizedForeignModel = commandConsoleStageModel(
    sanitizedForeignOperation
  );
  assert.deepStrictEqual(
    sanitizedForeignOperation.intervention.command_execution,
    {}
  );
  assert.strictEqual(sanitizedForeignOperation.operation_generation, 1);
  assert.strictEqual(sanitizedForeignModel.actionIssued, false);
  assert.strictEqual(sanitizedForeignModel.effectObserved, false);
  assert.strictEqual(sanitizedForeignModel.terminal, false);

  var pendingA = beginOperationRecord("마린 1기 정찰", "parallel-pending-a");
  var pendingB = beginOperationRecord("마린 4기 공격", "parallel-pending-b");
  bindOperationRecordUpdate(
    pendingA.text,
    pendingA.pendingId,
    OPERATION_SCOPE,
    "parallel-update-a"
  );
  bindOperationRecordUpdate(
    pendingB.text,
    pendingB.pendingId,
    OPERATION_SCOPE,
    "parallel-update-b"
  );
  renderOperationConsole(serverResult({
    status: "published",
    operations: [
      operationResult(
        "assault-bravo",
        "parallel-update-b",
        "마린 4기 공격",
        "attack",
        210,
        "queued_or_assigned",
        observedExecutionStages().slice(0, 4)
      )
    ]
  }, OPERATION_SCOPE));
  renderOperationConsole(serverResult({
    status: "published",
    operations: [
      operationResult(
        "recon-alpha",
        "parallel-update-a",
        "마린 1기 정찰",
        "scouting",
        300,
        "action_issued",
        actionStages("move").slice(0, 6)
      )
    ]
  }, OPERATION_SCOPE));

  var reconKey = operationRecordKey(OPERATION_SCOPE, "recon-alpha");
  var assaultKey = operationRecordKey(OPERATION_SCOPE, "assault-bravo");
  var reconRecord = operationRecords[reconKey];
  var assaultRecord = operationRecords[assaultKey];
  assert(reconRecord);
  assert(assaultRecord);
  assert.strictEqual(Object.keys(operationRecords).length, 2);
  assert.strictEqual(nodes["operation-list"].querySelectorAll(".operation-card").length, 2);
  assert.strictEqual(reconRecord.node.parentNode.id, "operation-lane-executing");
  assert.strictEqual(assaultRecord.node.parentNode.id, "operation-lane-planning");
  assert(reconRecord.node.textContent.includes("recon-alpha#1"));
  assert(reconRecord.node.textContent.includes("direct → direct"));
  assert(reconRecord.node.textContent.includes("until_completed"));
  assert(reconRecord.node.textContent.includes("실제 소유 1"));
  assert(reconRecord.node.textContent.includes("이동"));
  assert.strictEqual(
    nodes["operation-timeline-selection"].textContent,
    "recon-alpha#1"
  );
  assert.strictEqual(
    nodes["operation-timeline"].querySelectorAll(".operation-timeline-item").length,
    1
  );
  assert(reconRecord.node.textContent.includes("move"));
  assert(!assaultRecord.node.textContent.includes("move"));
  assert(reconRecord.node.className.includes("command-console-executing"));
  assert(
    !assaultRecord.node.className.includes("command-console-executing"),
    "published or assigned must not be labeled as executing"
  );
  assert.strictEqual(
    assaultRecord.node.querySelector(".operation-card-state").textContent,
    "유닛 편성 완료"
  );
  var reconNodeBeforeDetachedSnapshot = reconRecord.node;
  renderOperationConsole(serverResult({
    status: "idle",
    runtime_attached: false,
    telemetry_stale_or_detached: true,
    operation_registry_authoritative: false,
    operations: []
  }, OPERATION_SCOPE));
  assert.strictEqual(Object.keys(operationRecords).length, 2);
  assert.strictEqual(
    operationRecords[reconKey].node,
    reconNodeBeforeDetachedSnapshot
  );
  var operationEpochBeforeForeignDetached =
    operationConsoleSessionEpoch;
  var operationCountBeforeForeignDetached =
    Object.keys(operationRecords).length;
  var activeConsoleEpochBeforeForeignDetached =
    activeCommandConsoleRecord.sessionEpoch;
  var activeConsoleUpdateBeforeForeignDetached =
    activeCommandConsoleRecord.updateId;
  reconRecord.node.querySelectorAll("button")[1].focus();
  var foreignDetachedSnapshot = serverResult({
    status: "idle",
    runtime_attached: false,
    telemetry_stale_or_detached: true,
    operation_registry_authoritative: false,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: []
  }, OPERATION_SCOPE);
  assert.strictEqual(renderOperationConsole(foreignDetachedSnapshot), false);
  renderActiveCommandConsole(foreignDetachedSnapshot, true);
  renderMicroMachineStatus(foreignDetachedSnapshot);
  assert.strictEqual(
    operationConsoleSessionEpoch,
    operationEpochBeforeForeignDetached
  );
  assert.strictEqual(
    Object.keys(operationRecords).length,
    operationCountBeforeForeignDetached
  );
  assert.strictEqual(operationRecords[reconKey].node, reconRecord.node);
  assert.strictEqual(
    activeCommandConsoleRecord.sessionEpoch,
    activeConsoleEpochBeforeForeignDetached
  );
  assert.strictEqual(
    activeCommandConsoleRecord.updateId,
    activeConsoleUpdateBeforeForeignDetached
  );
  assert.strictEqual(
    document.activeElement.getAttribute("data-operation-action"),
    "revise"
  );

  [reconRecord, assaultRecord].forEach(function(record) {
    var statusNode = record.node.querySelector(".operation-card-state");
    var controls = record.node.querySelectorAll("button");
    var rail = record.node.querySelectorAll(".operation-stage");
    assert.strictEqual(statusNode.getAttribute("role"), "status");
    assert.strictEqual(record.node.getAttribute("role"), "listitem");
    assert(record.node.getAttribute("aria-labelledby"));
    assert.strictEqual(rail.length, 4);
    assert.deepStrictEqual(
      rail.map(function(stage) { return stage.textContent; }),
      ["해석", "배정", "제출", "관측"]
    );
    rail.forEach(function(stage) {
      assert.strictEqual(stage.getAttribute("role"), "listitem");
      assert(["step", "false"].includes(stage.getAttribute("aria-current")));
    });
    assert.strictEqual(controls.length, 5);
    assert.deepStrictEqual(
      controls.map(function(control) {
        return control.getAttribute("data-operation-action");
      }),
      ["view", "revise", "reinforce", "retarget", "cancel"]
    );
    controls.forEach(function(control) {
      assert(control.getAttribute("aria-label").includes(record.text));
    });
  });
  assert.notStrictEqual(
    reconRecord.node.getAttribute("aria-labelledby"),
    assaultRecord.node.getAttribute("aria-labelledby")
  );

  // A stale response may not regress one card or contaminate the other card.
  assaultRecord.node.querySelectorAll("button")[1].focus();
  renderOperationConsole(serverResult({
    status: "published",
    operations: [
      operationResult(
        "recon-alpha",
        "parallel-update-a",
        "마린 1기 정찰",
        "scouting",
        250,
        "queued_or_assigned",
        observedExecutionStages().slice(0, 4)
      ),
      operationResult(
        "assault-bravo",
        "parallel-update-b",
        "마린 4기 공격",
        "attack",
        320,
        "action_issued",
        actionStages("attack").slice(0, 6)
      )
    ]
  }, OPERATION_SCOPE));
  reconRecord = operationRecords[reconKey];
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(reconRecord.telemetryFrame, 300);
  assert.strictEqual(reconRecord.stageRank, 3);
  assert(reconRecord.node.textContent.includes("move"));
  assert.strictEqual(assaultRecord.telemetryFrame, 320);
  assert(assaultRecord.node.textContent.includes("attack"));
  assert(!assaultRecord.node.textContent.includes("move"));
  assert.strictEqual(assaultRecord.node.parentNode.id, "operation-lane-executing");
  assert.strictEqual(
    document.activeElement.getAttribute("data-operation-action"),
    "revise"
  );

  // Keyed rerenders preserve the focused standard action and timeline
  // disclosure state while semantic events arrive immediately over SSE.
  reconRecord.node.querySelectorAll("button")[1].focus();
  var focusedReconUpdate = operationResult(
    "recon-alpha",
    "parallel-update-a",
    "마린 1기 정찰",
    "scouting",
    301,
    "action_issued",
    actionStages("move").slice(0, 6)
  );
  focusedReconUpdate.semantic_timeline = [
    {
      timeline_seq: 300,
      operation_id: "recon-alpha",
      generation: 1,
      kind: "submitted",
      game_frame: 300,
      summary: "SC2 action submitted",
      technical: { state: "action_issued" }
    },
    {
      timeline_seq: 301,
      operation_id: "recon-alpha",
      generation: 1,
      kind: "movement_observed",
      game_frame: 301,
      summary: "movement observed",
      technical: { movement_observed: true }
    }
  ];
  renderOperationConsole(serverResult({
    status: "published",
    operations: [focusedReconUpdate]
  }, OPERATION_SCOPE));
  reconRecord = operationRecords[reconKey];
  assert.strictEqual(
    document.activeElement.getAttribute("data-operation-action"),
    "revise"
  );
  var firstTimelineItem = nodes["operation-timeline"]
    .querySelectorAll(".operation-timeline-item")[0];
  var firstTimelineDetails = firstTimelineItem.querySelector("details");
  firstTimelineDetails.open = true;
  firstTimelineDetails.querySelector("summary").focus();
  var operationEventSeq = lastEventSeq + 1;
  var immediateOperationEvent = {
    type: "operation_event",
    lastEventId: String(operationEventSeq),
    data: JSON.stringify({
      event_type: "operation_event",
      event_seq: operationEventSeq,
      blackboard_scope_id: OPERATION_SCOPE,
      operation_id: "recon-alpha",
      generation: 1,
      payload: {
        timeline_seq: 302,
        blackboard_scope_id: OPERATION_SCOPE,
        session_epoch: "1700000000000",
        operation_id: "recon-alpha",
        generation: 1,
        kind: "engagement_observed",
        game_frame: 302,
        summary: "engagement observed",
        technical: { engagement_observed: true }
      }
    })
  };
  applyServerEvent(immediateOperationEvent);
  assert.strictEqual(
    nodes["operation-timeline"]
      .querySelectorAll(".operation-timeline-item").length,
    3
  );
  assert.strictEqual(
    nodes["operation-timeline"]
      .querySelectorAll(".operation-timeline-item")[0]
      .querySelector("details").open,
    true
  );
  assert.strictEqual(document.activeElement.tagName, "SUMMARY");
  applyServerEvent(immediateOperationEvent);
  assert.strictEqual(
    nodes["operation-timeline"]
      .querySelectorAll(".operation-timeline-item").length,
    3
  );

  // All-Terran evidence extends the existing Operation card and four-stage
  // rail instead of creating a separate family dashboard.
  var allTerranFamilies = [
    ["marine", "Marine", "frontline", "marine_stimpack"],
    ["marauder", "Marauder", "frontline", "marauder_stimpack"],
    ["reaper", "Reaper", "worker_harass", "kd8_charge"],
    ["ghost", "Ghost", "spellcaster", "emp"],
    ["hellion_hellbat", "Hellion/Hellbat", "worker_harass", "hellbat_mode"],
    ["widow_mine", "Widow Mine", "ambush", "widow_mine_burrow"],
    ["cyclone", "Cyclone", "kite", "lock_on"],
    ["siege_tank", "Siege Tank", "siege_support", "siege_mode"],
    ["thor", "Thor", "anti_air", "thor_high_impact_mode"],
    ["medivac", "Medivac", "support", "medivac_heal"],
    ["raven", "Raven", "support", "auto_turret"],
    ["viking", "Viking", "anti_air", "viking_fighter_mode"],
    ["banshee", "Banshee", "worker_harass", "banshee_cloak"],
    ["liberator", "Liberator", "zone_control", "liberator_defender_mode"],
    ["battlecruiser", "Battlecruiser", "capital_ship", "yamato"]
  ];
  var currentFamilyEvidence = allTerranFamilies.map(function(definition, index) {
    var isTank = definition[0] === "siege_tank";
    var isBlocked = definition[0] === "banshee";
    return {
      family: definition[0],
      display_name: definition[1],
      role: definition[2],
      assigned: isBlocked ? 0 : 1,
      represented: isBlocked ? 0 : 1,
      action: definition[3],
      attempt_generation: index + 1,
      attempted: !isBlocked,
      executed: !isBlocked,
      effect: isTank,
      stage: isBlocked ? "blocked" : (isTank ? "effect" : "executed"),
      blocker: isBlocked ? "missing_starport_techlab" : ""
    };
  });
  var allTerranAssault = operationResult(
    "assault-bravo",
    "parallel-update-b",
    "테란 혼성 견제",
    "harass",
    325,
    "action_issued",
    actionStages("harass").slice(0, 6)
  );
  allTerranAssault.squad_order = "harass";
  allTerranAssault.family_evidence = currentFamilyEvidence;
  renderOperationConsole(serverResult({
    status: "published",
    operations: [allTerranAssault]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(Object.keys(operationRecords).length, 2);
  assert.strictEqual(
    nodes["operation-list"].querySelectorAll(".operation-lane").length,
    4
  );
  assert.strictEqual(
    nodes["operation-list"].querySelectorAll(".operation-card").length,
    2
  );
  assert.strictEqual(assaultRecord.node.querySelectorAll(".operation-stage").length, 4);
  assert.strictEqual(assaultRecord.node.querySelectorAll("button").length, 5);
  assert(assaultRecord.node.textContent.includes("유닛 실행"));
  assert(assaultRecord.node.textContent.includes("Squad 오더"));
  assert(assaultRecord.node.textContent.includes("harass"));
  allTerranFamilies.forEach(function(definition) {
    assert(
      assaultRecord.node.textContent.includes(definition[1]),
      "missing family evidence in Operation card: " + definition[1]
    );
  });
  assert(assaultRecord.node.textContent.includes("missing_starport_techlab"));
  assert.strictEqual(
    assaultRecord.data.family_evidence.find(function(item) {
      return item.family === "siege_tank";
    }).attempt_generation,
    8
  );

  // A later envelope carrying an older family/action attempt may advance the
  // operation frame, but it cannot overwrite the newer family evidence row.
  var staleFamilyEvidence = currentFamilyEvidence.map(function(item) {
    if (item.family !== "siege_tank") { return Object.assign({}, item); }
    return Object.assign({}, item, {
      attempt_generation: 7,
      effect: false,
      stage: "blocked",
      blocker: "stale_family_attempt_must_not_replace_latest"
    });
  });
  var staleFamilyAssault = operationResult(
    "assault-bravo",
    "parallel-update-b",
    "테란 혼성 견제",
    "harass",
    326,
    "action_issued",
    actionStages("harass").slice(0, 6)
  );
  staleFamilyAssault.squad_order = "harass";
  staleFamilyAssault.family_evidence = staleFamilyEvidence;
  renderOperationConsole(serverResult({
    status: "published",
    operations: [staleFamilyAssault]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.telemetryFrame, 326);
  var retainedTankEvidence = assaultRecord.data.family_evidence.find(
    function(item) { return item.family === "siege_tank"; }
  );
  assert.strictEqual(retainedTankEvidence.attempt_generation, 8);
  assert.strictEqual(retainedTankEvidence.stage, "effect");
  assert.strictEqual(retainedTankEvidence.blocker, "");
  assert(
    !assaultRecord.node.textContent.includes(
      "stale_family_attempt_must_not_replace_latest"
    )
  );

  // The overview consumes only the canonical battlefield projection. A
  // representative manager snapshot cannot change ownership/readiness totals.
  var authoritativeOverview = {
    schema_version: 2,
    authority: "micromachine_cpp",
    identity: { game_frame: 327 },
    eligible_combat_count: 11,
    explicit_operation_owned_count: 6,
    autonomous_owned_count: 3,
    unassigned_count: 2,
    duplicate_owner_count: 0,
    operation_ownership: [],
    bases: [
      {
        base_id: "main",
        semantic_anchor: "self_main",
        base_readiness: {
          readiness_state: "ready",
          reason: "protected_minimum_satisfied",
          protected_minimum: [
            { family: "marine", count: 2 }
          ]
        }
      }
    ],
    transfer_availability: {
      entries: [
        {
          source_owner_id: "assault-bravo",
          source_owner_count: 4,
          transferable_count: 2,
          transfer_safe: true,
          atomic_runtime_blocker: ""
        }
      ]
    }
  };
  renderBattlefieldControlOverview({
    battlefield_overview: authoritativeOverview,
    intervention: {
      manager_snapshot: {
        CombatCommander: { combat_unit_count: 999 },
        ScoutManager: { scout_unit_count: 999 }
      }
    }
  });
  assert.strictEqual(nodes["battlefield-force"].textContent, "11");
  assert(nodes["battlefield-posture"].textContent.includes("명시 6"));
  assert(nodes["battlefield-posture"].textContent.includes("자율 3"));
  assert.strictEqual(nodes["battlefield-unassigned"].textContent, "2");
  assert(nodes["battlefield-readiness"].textContent.includes("self_main"));
  assert(nodes["battlefield-readiness"].textContent.includes("marine 2"));
  assert(nodes["battlefield-transfer"].textContent.includes("2/4"));
  assert(!nodes["battlefield-control-summary"].textContent.includes("999"));

  // Contextual resolution controls are separate from the five standard card
  // actions and fail closed until canonical runtime safety says they are safe.
  var unsafeResolutionAssault = operationResult(
    "assault-bravo",
    "parallel-update-b",
    "부분 출동 안전성 확인",
    "attack",
    327,
    "action_issued",
    actionStages("attack").slice(0, 6)
  );
  unsafeResolutionAssault.battlefield_operation.operation_launch_policy
    .recommended_choices = ["launch_partial", "wait_for_full_force"];
  unsafeResolutionAssault.battlefield_operation.operation_launch_policy
    .partial_launch_allowed = true;
  unsafeResolutionAssault.battlefield_operation.operation_launch_policy
    .partial_launch_safe = false;
  unsafeResolutionAssault.battlefield_operation.operation_launch_policy
    .decision = "wait";
  unsafeResolutionAssault.battlefield_operation.operation_launch_policy
    .missing_count = 1;
  unsafeResolutionAssault.battlefield_operation.operation_launch_policy
    .launch_count = 3;
  unsafeResolutionAssault.battlefield_operation.operation_launch_policy
    .blocker = "protected_minimum_not_respected";
  renderOperationConsole(serverResult({
    status: "published",
    battlefield_overview: {
      authority: "micromachine_cpp",
      transfer_availability: {
        atomic_revalidation_required: true,
        entries: [
          {
            source_owner_id: "assault-bravo",
            transferable_count: 2,
            transfer_safe: false,
            atomic_runtime_blocker: "protected_minimum_not_respected",
            recommended_resolution_choices: [
              "transfer_two_units",
              "raw_tag_123_frame_script"
            ],
            safety_evidence: {
              protected_minimum_respected: false,
              atomic_revalidation_required: true
            },
            atomic_revalidation_inputs: {
              atomic_revalidation_ready: false,
              source_active: true,
              ownership_integrity: true
            }
          }
        ]
      }
    },
    operations: [unsafeResolutionAssault]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  var standardActions = assaultRecord.node.querySelector(
    ".operation-card-actions"
  ).querySelectorAll("button");
  assert.strictEqual(standardActions.length, 5);
  var unsafeResolutionButtons = assaultRecord.node.querySelector(
    ".operation-resolution-actions"
  ).querySelectorAll("button");
  var unsafePartialButton = unsafeResolutionButtons.find(function(button) {
    return button.getAttribute("data-operation-resolution") === "launch_partial";
  });
  var safeWaitButton = unsafeResolutionButtons.find(function(button) {
    return button.getAttribute("data-operation-resolution") === "wait_for_full_force";
  });
  var unsafeTransferButton = unsafeResolutionButtons.find(function(button) {
    return button.getAttribute("data-operation-resolution") === "transfer_two_units";
  });
  assert.strictEqual(unsafeResolutionButtons.length, 3);
  assert.strictEqual(unsafePartialButton.getAttribute("aria-disabled"), "true");
  assert.strictEqual(safeWaitButton.getAttribute("aria-disabled"), "false");
  assert.strictEqual(unsafeTransferButton.getAttribute("aria-disabled"), "true");
  assert(
    unsafePartialButton.getAttribute("aria-describedby")
  );
  assert(
    assaultRecord.node.querySelector(".operation-resolution-reason")
      .textContent.includes("protected_minimum_not_respected")
  );
  assert(!assaultRecord.node.textContent.includes("raw_tag_123_frame_script"));
  var unsafeRequestCount = requests.length;
  unsafePartialButton.dispatchEvent({
    type: "click",
    preventDefault: function() {}
  });
  assert.strictEqual(requests.length, unsafeRequestCount);
  unsafeTransferButton.focus();
  assert.strictEqual(
    document.activeElement.getAttribute("data-operation-resolution"),
    "transfer_two_units"
  );

  var safeResolutionAssault = operationResult(
    "assault-bravo",
    "parallel-update-b",
    "부분 출동 안전성 확인",
    "attack",
    328,
    "action_issued",
    actionStages("attack").slice(0, 6)
  );
  safeResolutionAssault.battlefield_operation.operation_launch_policy
    .recommended_choices = ["launch_partial"];
  safeResolutionAssault.battlefield_operation.operation_launch_policy
    .partial_launch_allowed = true;
  safeResolutionAssault.battlefield_operation.operation_launch_policy
    .partial_launch_safe = true;
  safeResolutionAssault.battlefield_operation.operation_launch_policy
    .safety_evidence = {
      protected_defense_minimum_respected: true,
      source_operation_minimum_respected: true
    };
  safeResolutionAssault.battlefield_operation.operation_launch_policy
    .decision = "launch";
  renderOperationConsole(serverResult({
    status: "published",
    battlefield_overview: {
      transfer_availability: { entries: [] }
    },
    operations: [safeResolutionAssault]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  var safePartialButton = assaultRecord.node.querySelector(
    ".operation-resolution-actions"
  ).querySelectorAll("button").find(function(button) {
    return button.getAttribute("data-operation-resolution") === "launch_partial";
  });
  assert.strictEqual(safePartialButton.getAttribute("aria-disabled"), "false");
  assert.strictEqual(
    document.activeElement.getAttribute("data-operation-action"),
    "view"
  );
  assert(operationNodeContains(assaultRecord.node, document.activeElement));
  assert(
    operationNodeContains(
      assaultRecord.node.parentNode,
      document.activeElement
    )
  );
  var safeResolutionText = operationResolutionCommand(
    "launch_partial",
    assaultRecord
  );
  assert(safeResolutionText.includes("assault-bravo"));
  assert(safeResolutionText.includes("권위 안전 판정을 통과한"));
  assert(!safeResolutionText.includes("launch_partial"));

  // A newer generation edits the existing card instead of creating a duplicate.
  var editedAssault = operationResult(
    "assault-bravo",
    "parallel-update-b-edit",
    "공격조를 마린 6기로 증원",
    "attack",
    330,
    "queued_or_assigned",
    observedExecutionStages().slice(0, 4),
    2
  );
  editedAssault.operation_edit = {
    action: "reinforce",
    before_composition: [
      { unit_type: "TERRAN_MARINE", count: 4 }
    ],
    after_composition: [
      { unit_type: "TERRAN_MARINE", count: 6 }
    ],
    resolution: "blocked",
    blocker: "explicit_ability_owner_protected"
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [editedAssault]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(Object.keys(operationRecords).length, 2);
  assert.strictEqual(assaultRecord.operationGeneration, 2);
  assert.strictEqual(assaultRecord.telemetryFrame, 330);
  assert(assaultRecord.node.textContent.includes("reinforce"));
  assert(assaultRecord.node.textContent.includes("MARINE ×4 → MARINE ×6"));
  assert(assaultRecord.node.textContent.includes("explicit_ability_owner_protected"));
  var assaultTimelineLength = assaultRecord.data.semantic_timeline.length;
  applyServerEvent({
    type: "operation_event",
    lastEventId: String(lastEventSeq + 1),
    data: JSON.stringify({
      event_type: "operation_event",
      event_seq: lastEventSeq + 1,
      blackboard_scope_id: OPERATION_SCOPE,
      operation_id: "assault-bravo",
      generation: 1,
      payload: {
        timeline_seq: 999,
        blackboard_scope_id: OPERATION_SCOPE,
        session_epoch: "1700000000000",
        operation_id: "assault-bravo",
        generation: 1,
        kind: "submitted",
        game_frame: 329,
        summary: "stale generation event",
        technical: {}
      }
    })
  });
  assert.strictEqual(
    assaultRecord.data.semantic_timeline.length,
    assaultTimelineLength
  );
  var editControls = assaultRecord.node.querySelectorAll("button");
  editControls[2].dispatchEvent({ type: "click" });
  assert(nodes["command-input"].value.includes("assault-bravo"));
  assert(nodes["command-input"].value.includes("증원"));
  editControls[3].dispatchEvent({ type: "click" });
  assert(nodes["command-input"].value.includes("목표를 변경"));

  // Non-terminal cards keep the newest rejected edit when an older rejected
  // generation arrives later with a higher telemetry frame.
  var latestActiveRejectedEdit = operationResult(
    "assault-bravo",
    "parallel-update-b-edit-latest",
    "공격조 편성을 다시 변경",
    "attack",
    331,
    "queued_or_assigned",
    observedExecutionStages().slice(0, 4),
    2
  );
  latestActiveRejectedEdit.requested_operation_generation = 4;
  latestActiveRejectedEdit.operation_edit = {
    action: "reinforce",
    before_composition: [
      { unit_type: "TERRAN_MARINE", count: 6 }
    ],
    after_composition: [
      { unit_type: "TERRAN_MARINE", count: 8 }
    ],
    resolution: "blocked",
    blocker: "latest_active_edit_blocker"
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [latestActiveRejectedEdit]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.terminal, false);
  assert.strictEqual(assaultRecord.requestedOperationGeneration, 4);
  assert(assaultRecord.node.textContent.includes("latest_active_edit_blocker"));

  var staleActiveRejectedEdit = operationResult(
    "assault-bravo",
    "parallel-update-b-edit-stale",
    "오래된 공격조 변경",
    "attack",
    332,
    "queued_or_assigned",
    observedExecutionStages().slice(0, 4),
    2
  );
  staleActiveRejectedEdit.requested_operation_generation = 3;
  staleActiveRejectedEdit.operation_edit = {
    action: "reinforce",
    before_composition: [
      { unit_type: "TERRAN_MARINE", count: 6 }
    ],
    after_composition: [
      { unit_type: "TERRAN_MARINE", count: 7 }
    ],
    resolution: "blocked",
    blocker: "stale_active_edit_must_not_replace_latest"
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [staleActiveRejectedEdit]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.requestedOperationGeneration, 4);
  assert.strictEqual(assaultRecord.telemetryFrame, 331);
  assert(assaultRecord.node.textContent.includes("latest_active_edit_blocker"));
  assert(
    !assaultRecord.node.textContent.includes(
      "stale_active_edit_must_not_replace_latest"
    )
  );

  var staleAcceptedEdit = operationResult(
    "assault-bravo",
    "parallel-update-b-edit-stale-accepted",
    "오래된 승인 공격조 변경",
    "attack",
    332,
    "queued_or_assigned",
    observedExecutionStages().slice(0, 4),
    2
  );
  staleAcceptedEdit.requested_operation_generation = 3;
  staleAcceptedEdit.operation_edit = {
    action: "transfer",
    resolution: "transferred",
    transferred_in_count: 2,
    transferred_out_count: 1
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [staleAcceptedEdit]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.requestedOperationGeneration, 4);
  assert.strictEqual(assaultRecord.telemetryFrame, 331);
  assert(assaultRecord.node.textContent.includes("latest_active_edit_blocker"));
  assert(!assaultRecord.node.textContent.includes("오래된 승인 공격조 변경"));

  // A delayed lower requested generation may carry a newer active generation.
  // Accept its execution telemetry without replacing the newest rejected edit.
  var staleEditWithNewerActiveGeneration = operationResult(
    "assault-bravo",
    "parallel-update-b-edit-stale-new-active",
    "오래된 공격조 변경과 새 실행 세대",
    "attack",
    333,
    "queued_or_assigned",
    observedExecutionStages().slice(0, 4),
    3
  );
  staleEditWithNewerActiveGeneration.requested_operation_generation = 3;
  staleEditWithNewerActiveGeneration.operation_edit = {
    action: "reinforce",
    before_composition: [
      { unit_type: "TERRAN_MARINE", count: 6 }
    ],
    after_composition: [
      { unit_type: "TERRAN_MARINE", count: 7 }
    ],
    resolution: "blocked",
    blocker: "stale_new_active_edit_must_not_replace_latest"
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [staleEditWithNewerActiveGeneration]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.operationGeneration, 3);
  assert.strictEqual(assaultRecord.requestedOperationGeneration, 4);
  assert.strictEqual(assaultRecord.telemetryFrame, 333);
  assert.strictEqual(assaultRecord.updateId, "parallel-update-b-edit-latest");
  assert.strictEqual(assaultRecord.text, "공격조 편성을 다시 변경");
  assert.strictEqual(
    assaultRecord.data.intervention.command_execution.operation_generation,
    3
  );
  assert(assaultRecord.node.textContent.includes("latest_active_edit_blocker"));
  assert(
    !assaultRecord.node.textContent.includes(
      "stale_new_active_edit_must_not_replace_latest"
    )
  );
  assert(
    !assaultRecord.node.textContent.includes(
      "오래된 공격조 변경과 새 실행 세대"
    )
  );
  assaultRecord.node.querySelectorAll("button")[0].dispatchEvent({
    type: "click"
  });
  assert.strictEqual(
    activeCommandConsoleRecord.updateId,
    "parallel-update-b-edit-latest"
  );
  assert.strictEqual(
    activeCommandConsoleRecord.data.intervention.command_execution
      .operation_generation,
    3
  );
  assert(nodes["command-console-units"].textContent.includes("marine"));
  assert(nodes["command-console-units"].textContent.includes("4기"));

  // Older-generation cleanup may not overwrite the edited operation card.
  var staleGenerationCleanup = operationResult(
    "assault-bravo",
    "parallel-update-b",
    "마린 4기 공격",
    "attack",
    335,
    "cancelled",
    actionStages("attack").slice(0, 6),
    1
  );
  staleGenerationCleanup.intervention.command_execution.blocker_reason =
    "cancelled_by_policy";
  staleGenerationCleanup.intervention.command_execution.terminal_cleanup = {
    action: "release_stop|cancelled_by_policy",
    frame: 335,
    operation_id: "assault-bravo",
    generation: 1
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [staleGenerationCleanup]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.terminal, false);
  assert.strictEqual(assaultRecord.operationGeneration, 3);
  assert.strictEqual(assaultRecord.telemetryFrame, 333);

  // Cancellation remains active until matching release_stop cleanup arrives.
  var cancellationPending = operationResult(
    "assault-bravo",
    "parallel-update-b-edit",
    "공격조를 마린 6기로 증원",
    "attack",
    340,
    "cancelled",
    actionStages("attack").slice(0, 6),
    3
  );
  cancellationPending.intervention.command_execution.blocker_reason =
    "cancelled_by_policy";
  cancellationPending.intervention.command_execution.terminal_cleanup = {};
  renderOperationConsole(serverResult({
    status: "published",
    operations: [cancellationPending]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.terminal, false);
  assert.strictEqual(assaultRecord.operationGeneration, 3);
  assert.strictEqual(assaultRecord.disposition, "active");
  assert.strictEqual(
    assaultRecord.node.parentNode.id,
    "operation-lane-waiting"
  );
  assert.strictEqual(
    assaultRecord.node.querySelector(".operation-card-state").textContent,
    "취소 정리 확인 중"
  );
  assert(nodes["operation-summary"].textContent.includes("활성 2"));

  var verifiedCancellation = operationResult(
    "assault-bravo",
    "parallel-update-b-edit",
    "공격조를 마린 6기로 증원",
    "attack",
    350,
    "cancelled",
    actionStages("attack").slice(0, 6),
    3
  );
  verifiedCancellation.intervention.command_execution.blocker_reason =
    "cancelled_by_policy";
  verifiedCancellation.intervention.command_execution.terminal_cleanup = {
    action: "release_no_owned_units|cancelled_by_policy",
    frame: 350,
    operation_id: "assault-bravo",
    generation: 3
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [verifiedCancellation]
  }, OPERATION_SCOPE));
  assaultRecord = operationRecords[assaultKey];
  assert.strictEqual(assaultRecord.terminal, true);
  assert.strictEqual(assaultRecord.telemetryFrame, 350);
  assert.strictEqual(assaultRecord.disposition, "superseded");
  assert.strictEqual(
    assaultRecord.node.querySelector(".operation-card-state").textContent,
    "작전 취소"
  );
  assert(assaultRecord.node.textContent.includes("중지 명령 없이"));

  // Terminal evidence is sticky even if a newer non-terminal payload arrives.
  var canonicalReconCompletion = operationResult(
    "recon-alpha",
    "parallel-update-a",
    "마린 1기 정찰",
    "scouting",
    400,
    "action_issued",
    actionStages("move").slice(0, 6)
  );
  canonicalReconCompletion.battlefield_operation.operation_lifetime.completed =
    true;
  canonicalReconCompletion.battlefield_operation.operation_lifetime
    .completion_state = "completed";
  canonicalReconCompletion.battlefield_operation.operation_completion.terminal =
    true;
  canonicalReconCompletion.battlefield_operation.operation_completion.state =
    "completed";
  canonicalReconCompletion.battlefield_operation.operation_completion.reason =
    "recon_waypoint_reached";
  renderOperationConsole(serverResult({
    status: "published",
    operations: [canonicalReconCompletion]
  }, OPERATION_SCOPE));
  assert.strictEqual(operationRecords[reconKey].terminal, true);
  assert.strictEqual(operationRecords[reconKey].telemetryFrame, 400);
  assert.strictEqual(
    operationRecords[reconKey].node.parentNode.id,
    "operation-lane-completed"
  );
  assert.strictEqual(
    operationRecords[reconKey].node.querySelector(".operation-card-state").textContent,
    "실행 확인"
  );

  operationRecords[reconKey].node
    .querySelectorAll(".operation-stage")
    .forEach(function(stage) {
      assert(stage.className.includes("stage-done"));
    });

  // A rejected higher-generation edit augments the terminal card without
  // replacing its verified execution state or creating another card.
  var terminalReconNodeId = operationRecords[reconKey].node.id;
  var rejectedTerminalEdit = operationResult(
    "recon-alpha",
    "parallel-update-a-edit",
    "정찰대를 마린 2기로 증원",
    "scouting",
    410,
    "completed",
    actionStages("move", "recon waypoint reached"),
    1
  );
  rejectedTerminalEdit.requested_operation_generation = 2;
  rejectedTerminalEdit.operation_edit = {
    action: "reinforce",
    before_composition: [
      { unit_type: "TERRAN_MARINE", count: 1 }
    ],
    after_composition: [
      { unit_type: "TERRAN_MARINE", count: 2 }
    ],
    resolution: "blocked",
    blocker: "terminal_operation_edit_rejected"
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [rejectedTerminalEdit]
  }, OPERATION_SCOPE));
  assert.strictEqual(Object.keys(operationRecords).length, 2);
  assert.strictEqual(nodes["operation-list"].querySelectorAll(".operation-card").length, 2);
  assert.strictEqual(operationRecords[reconKey].node.id, terminalReconNodeId);
  assert.strictEqual(operationRecords[reconKey].terminal, true);
  assert.strictEqual(operationRecords[reconKey].operationGeneration, 1);
  assert.strictEqual(operationRecords[reconKey].requestedOperationGeneration, 2);
  assert.strictEqual(
    operationRecords[reconKey].node.querySelector(".operation-card-state").textContent,
    "실행 확인"
  );
  assert(operationRecords[reconKey].node.textContent.includes("reinforce"));
  assert(operationRecords[reconKey].node.textContent.includes("MARINE ×1 → MARINE ×2"));
  assert(
    operationRecords[reconKey].node.textContent.includes(
      "terminal_operation_edit_rejected"
    )
  );

  // A stale requested generation may not overwrite the latest edit blocker.
  var staleRejectedTerminalEdit = operationResult(
    "recon-alpha",
    "parallel-update-a-stale-edit",
    "정찰대를 다시 변경",
    "scouting",
    420,
    "completed",
    actionStages("move", "recon waypoint reached"),
    1
  );
  staleRejectedTerminalEdit.requested_operation_generation = 1;
  staleRejectedTerminalEdit.operation_edit = {
    action: "reinforce",
    before_composition: [
      { unit_type: "TERRAN_MARINE", count: 1 }
    ],
    after_composition: [
      { unit_type: "TERRAN_MARINE", count: 3 }
    ],
    resolution: "blocked",
    blocker: "stale_edit_must_not_replace_latest"
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [staleRejectedTerminalEdit]
  }, OPERATION_SCOPE));
  assert.strictEqual(operationRecords[reconKey].requestedOperationGeneration, 2);
  assert(
    operationRecords[reconKey].node.textContent.includes(
      "terminal_operation_edit_rejected"
    )
  );
  assert(
    !operationRecords[reconKey].node.textContent.includes(
      "stale_edit_must_not_replace_latest"
    )
  );

  var staleTerminalEditWithNewerActiveGeneration = operationResult(
    "recon-alpha",
    "parallel-update-a-stale-new-active",
    "오래된 정찰대 변경과 새 실행 세대",
    "scouting",
    421,
    "completed",
    actionStages("move", "new active generation waypoint reached"),
    2
  );
  staleTerminalEditWithNewerActiveGeneration.requested_operation_generation = 1;
  staleTerminalEditWithNewerActiveGeneration.operation_edit = {
    action: "reinforce",
    before_composition: [
      { unit_type: "TERRAN_MARINE", count: 1 }
    ],
    after_composition: [
      { unit_type: "TERRAN_MARINE", count: 3 }
    ],
    resolution: "blocked",
    blocker: "stale_terminal_new_active_must_not_replace_latest"
  };
  renderOperationConsole(serverResult({
    status: "published",
    operations: [staleTerminalEditWithNewerActiveGeneration]
  }, OPERATION_SCOPE));
  assert.strictEqual(operationRecords[reconKey].terminal, true);
  assert.strictEqual(operationRecords[reconKey].operationGeneration, 2);
  assert.strictEqual(operationRecords[reconKey].requestedOperationGeneration, 2);
  assert.strictEqual(operationRecords[reconKey].telemetryFrame, 421);
  assert.strictEqual(operationRecords[reconKey].updateId, "parallel-update-a-edit");
  assert.strictEqual(operationRecords[reconKey].text, "정찰대를 마린 2기로 증원");
  assert.strictEqual(
    operationRecords[reconKey].data.intervention.command_execution
      .operation_generation,
    2
  );
  assert(
    operationRecords[reconKey].node.textContent.includes(
      "terminal_operation_edit_rejected"
    )
  );
  assert(
    operationRecords[reconKey].node.textContent.includes(
      "new active generation waypoint reached"
    )
  );
  assert(
    !operationRecords[reconKey].node.textContent.includes(
      "stale_terminal_new_active_must_not_replace_latest"
    )
  );
  assert(
    !operationRecords[reconKey].node.textContent.includes(
      "오래된 정찰대 변경과 새 실행 세대"
    )
  );
  operationRecords[reconKey].node.querySelectorAll("button")[0].dispatchEvent({
    type: "click"
  });
  assert.strictEqual(
    activeCommandConsoleRecord.updateId,
    "parallel-update-a-edit"
  );
  assert.strictEqual(
    activeCommandConsoleRecord.data.intervention.command_execution
      .operation_generation,
    2
  );
  assert(nodes["command-console-action"].textContent.includes("move"));
  assert(
    nodes["command-console-verification"].textContent.includes(
      "new active generation waypoint reached"
    )
  );

  renderOperationConsole(serverResult({
    status: "published",
    operations: [
      operationResult(
        "recon-alpha",
        "parallel-update-a",
        "마린 1기 정찰",
        "scouting",
        500,
        "action_issued",
        actionStages("move").slice(0, 6)
      )
    ]
  }, OPERATION_SCOPE));
  assert.strictEqual(operationRecords[reconKey].terminal, true);
  assert.strictEqual(operationRecords[reconKey].telemetryFrame, 421);
  assert.strictEqual(
    operationRecords[reconKey].node.querySelector(".operation-card-state").textContent,
    "실행 확인"
  );

  // A new authoritative game epoch replaces the old terminal registry even
  // when scope, operation ID, and generation are reused. Reported/execution
  // completion without a matching canonical projection remains non-terminal.
  var restartedRecon = operationResult(
    "recon-alpha",
    "restart-update-a",
    "새 게임 정찰",
    "scouting",
    10,
    "completed",
    actionStages("move", "reported completion only"),
    1
  );
  restartedRecon.battlefield_operation.identity.session_epoch =
    1800000000000;
  restartedRecon.battlefield_operation.operation_id =
    "mismatched-canonical-operation";
  var restartedSnapshot = serverResult({
    status: "published",
    operation_registry_authoritative: true,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: [restartedRecon]
  }, OPERATION_SCOPE);
  renderMicroMachineStatus(restartedSnapshot);
  reconKey = operationRecordKey(OPERATION_SCOPE, "recon-alpha");
  assert.strictEqual(operationConsoleSessionEpoch, "1800000000000");
  assert.strictEqual(Object.keys(operationRecords).length, 1);
  assert.strictEqual(
    activeCommandConsoleRecord.sessionEpoch,
    "1800000000000"
  );
  assert.strictEqual(activeCommandConsoleRecord.updateId, "restart-update-a");
  assert.strictEqual(activeCommandConsoleRecord.telemetryFrame, 10);
  assert.strictEqual(
    activeCommandConsoleRecord.data.intervention.command_execution.command_id,
    "restart-update-a"
  );
  assert.strictEqual(
    nodes["command-console-title"].textContent,
    "새 게임 정찰"
  );
  assert.strictEqual(operationRecords[reconKey].terminal, false);
  assert.notStrictEqual(
    operationRecords[reconKey].node.parentNode.id,
    "operation-lane-completed"
  );
  assert(
    !operationRecords[reconKey].node
      .querySelector(".operation-card-state").textContent.includes("실행 확인")
  );

  var restartedReconNode = operationRecords[reconKey].node;
  var delayedRetiredEpoch = operationResult(
    "recon-alpha",
    "delayed-retired-update",
    "이전 게임에서 늦게 도착한 정찰",
    "scouting",
    999,
    "completed",
    actionStages("move", "stale completion"),
    1
  );
  assert.strictEqual(
    renderOperationConsole(serverResult({
      status: "published",
      operation_registry_authoritative: true,
      battlefield_overview: {
        identity: { session_epoch: 1700000000000 }
      },
      operations: [delayedRetiredEpoch]
    }, OPERATION_SCOPE)),
    false
  );
  assert.strictEqual(operationConsoleSessionEpoch, "1800000000000");
  assert.strictEqual(operationRecords[reconKey].node, restartedReconNode);
  assert.strictEqual(operationRecords[reconKey].telemetryFrame, 10);
  assert.strictEqual(operationRecords[reconKey].text, "새 게임 정찰");

  // Authoritative snapshots remove absent server operations, preserve only a
  // bounded unacknowledged local pending card, then expire it.
  var localPending = beginOperationRecord(
    "로컬 승인 대기",
    "local-unacknowledged"
  );
  renderOperationConsole(serverResult({
    status: "published",
    operation_registry_authoritative: true,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: [restartedRecon]
  }, OPERATION_SCOPE));
  assert.strictEqual(Object.keys(operationRecords).length, 2);
  localPending.createdAt =
    Date.now() - OPERATION_PENDING_RECORD_TIMEOUT_MS - 1;
  renderOperationConsole(serverResult({
    status: "published",
    operation_registry_authoritative: true,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: [restartedRecon]
  }, OPERATION_SCOPE));
  assert.strictEqual(Object.keys(operationRecords).length, 1);

  var authoritativeEmptySnapshot = serverResult({
    status: "published",
    operation_registry_authoritative: true,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: [],
    compile_result: {
      status: "compiled",
      update_id: "must-not-become-an-operation"
    },
    update: {
      update_id: "must-not-become-an-operation",
      vector: { goal: "top-level transport result" }
    },
    intervention: {
      latest_update_id: "must-not-become-an-operation",
      telemetry_frame: 11,
      command_execution: {
        command_id: "must-not-become-an-operation",
        state: "action_issued",
        completed: false,
        failed: false,
        expired: false,
        stages: actionStages("move").slice(0, 6)
      }
    }
  }, OPERATION_SCOPE);
  assert.strictEqual(commandOperationPayloads(authoritativeEmptySnapshot).length, 0);
  assert.strictEqual(renderOperationConsole(authoritativeEmptySnapshot), false);
  assert.strictEqual(Object.keys(operationRecords).length, 0);
  assert.strictEqual(
    nodes["operation-list"].querySelectorAll(".operation-card").length,
    0
  );

  var manyActiveOperations = [];
  for (var operationIndex = 0; operationIndex < 30; operationIndex += 1) {
    var boundedOperation = operationResult(
      "bounded-" + operationIndex,
      "bounded-update-" + operationIndex,
      "bounded operation " + operationIndex,
      "attack",
      20 + operationIndex,
      "action_issued",
      actionStages("attack").slice(0, 6),
      1
    );
    boundedOperation.battlefield_operation.identity.session_epoch =
      1800000000000;
    manyActiveOperations.push(boundedOperation);
  }
  renderOperationConsole(serverResult({
    status: "published",
    operation_registry_authoritative: true,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: manyActiveOperations
  }, OPERATION_SCOPE));
  assert.strictEqual(OPERATION_RECORD_MAXIMUM, 24);
  assert.strictEqual(
    Object.keys(operationRecords).length,
    24
  );
  assert.strictEqual(
    nodes["operation-list"].querySelectorAll(".operation-card").length,
    24
  );
  var expectedNewestBoundedIds = [];
  for (var boundedIndex = 6; boundedIndex < 30; boundedIndex += 1) {
    expectedNewestBoundedIds.push("bounded-" + boundedIndex);
  }
  assert.deepStrictEqual(
    operationRecordOrder.map(function(key) {
      return operationRecords[key].operationId;
    }),
    expectedNewestBoundedIds
  );
  var stableBoundedKeys = Object.keys(operationRecords).sort();
  var stableBoundedNodes = {};
  stableBoundedKeys.forEach(function(key) {
    stableBoundedNodes[key] = operationRecords[key].node;
  });
  renderOperationConsole(serverResult({
    status: "published",
    operation_registry_authoritative: true,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: manyActiveOperations.slice().reverse()
  }, OPERATION_SCOPE));
  assert.deepStrictEqual(
    Object.keys(operationRecords).sort(),
    stableBoundedKeys
  );
  stableBoundedKeys.forEach(function(key) {
    assert.strictEqual(operationRecords[key].node, stableBoundedNodes[key]);
  });

  var boundedPending = beginOperationRecord(
    "최신 작전과 함께 보존할 로컬 pending",
    "bounded-local-pending"
  );
  assert.strictEqual(Object.keys(operationRecords).length, 24);
  assert(operationRecords[boundedPending.key]);
  renderOperationConsole(serverResult({
    status: "published",
    operation_registry_authoritative: true,
    battlefield_overview: {
      identity: { session_epoch: 1800000000000 }
    },
    operations: manyActiveOperations.slice().reverse()
  }, OPERATION_SCOPE));
  var expectedNewestWithPending = [];
  for (var pendingBoundedIndex = 7; pendingBoundedIndex < 30; pendingBoundedIndex += 1) {
    expectedNewestWithPending.push("bounded-" + pendingBoundedIndex);
  }
  assert.strictEqual(Object.keys(operationRecords).length, 24);
  assert(operationRecords[boundedPending.key]);
  assert.deepStrictEqual(
    operationRecordOrder.filter(function(key) {
      return key !== boundedPending.key;
    }).map(function(key) {
      return operationRecords[key].operationId;
    }),
    expectedNewestWithPending
  );

  nodes["micromachine-blackboard-dir"].value = "/tmp/voi-mm-operation-cards-next";
  synchronizeMicroMachineBlackboardDirectory("/tmp/voi-mm-operation-cards-next");
  assert.strictEqual(Object.keys(operationRecords).length, 0);
  assert.strictEqual(nodes["operation-list"].querySelectorAll(".operation-card").length, 0);
  assert(nodes["operation-summary"].textContent.includes("0"));

  var requestCountBeforeEmergency = requests.length;
  nodes["command-retreat-button"].dispatchEvent({ type: "click" });
  assert.strictEqual(requests.length, requestCountBeforeEmergency + 1);
  var emergencyButtonRequest = requests[requests.length - 1];
  assert.strictEqual(emergencyButtonRequest.url, "/api/micromachine/modulate");
  assert.strictEqual(
    JSON.parse(emergencyButtonRequest.options.body).text,
    "긴급 전군 즉시 후퇴해"
  );
  assert.strictEqual(
    nodes["command-console-title"].textContent,
    "긴급 전군 즉시 후퇴해"
  );
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

    def test_active_command_console_accessibility_and_color_contract(self):
        page = render_web_gui_page()
        self.assertLess(
            page.index('id="active-command-console"'),
            page.index('class="runtime-mode-panel"'),
        )
        self.assertIn('id="command-console-announcement"', page)
        self.assertIn('class="sr-only"', page)
        self.assertIn('role="status"', page)
        self.assertIn(
            '<p id="assistant-pending-status" class="assistant-pending-status"></p>',
            page,
        )
        self.assertIn('<div id="log" aria-live="off" role="log"></div>', page)
        self.assertIn(
            'id="operation-timeline"\n'
            '            class="operation-timeline"\n'
            '            role="log"\n'
            '            aria-live="off"',
            page,
        )
        self.assertIn('<div id="micromachine-status" aria-live="off">', page)
        self.assertNotIn('botMessage.setAttribute("role", "status")', page)
        self.assertNotIn('botMessage.setAttribute("aria-live", "polite")', page)
        self.assertEqual(page.count('class="command-stage" role="listitem"'), 4)
        self.assertEqual(page.count('data-operation-lane="'), 4)
        for lane in ("planning", "executing", "completed", "waiting"):
            self.assertIn(f'id="operation-lane-{lane}"', page)
        for action in ("view", "revise", "reinforce", "retarget", "cancel"):
            self.assertIn(f'"{action}"', page)
        self.assertIn('className = "operation-resolution-actions"', page)
        self.assertIn('"operation_event",', page)
        self.assertIn(
            'button.setAttribute(\n'
            '      "aria-disabled",\n'
            '      choice.safe === true ? "false" : "true"\n'
            "    );",
            page,
        )
        self.assertIn('reason.className = "operation-resolution-reason"', page)
        self.assertIn('button.setAttribute("aria-describedby", reason.id)', page)
        self.assertIn('"data-operation-card-fingerprint"', page)
        self.assertIn('"data-operation-timeline-fingerprint"', page)
        self.assertIn(
            ".command-stage.stage-done {\n"
            "    color: #7dd3fc;",
            page,
        )
        self.assertIn(
            ".command-stage.stage-verified {\n"
            "    color: #7ee7b0;",
            page,
        )

    def test_chat_panel_is_bounded_and_log_scrolls_internally(self):
        page = render_web_gui_page()
        for fragment in (
            "main {\n    display: grid; grid-template-columns: minmax(540px, 1.32fr) minmax(420px, 0.88fr);\n    gap: 24px; align-items: start; min-height: 0;",
            "#command-panel {\n    min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden;",
            "height: clamp(560px, calc(100vh - 160px), 860px); max-height: calc(100vh - 160px);",
            "#state-panel {\n    min-width: 0; min-height: 0; max-height: calc(100vh - 160px); overflow-y: auto;",
            "display: flex; flex-direction: column; gap: 16px; scrollbar-gutter: stable;",
            "#briefing-panel, #llm-panel, #micromachine-panel {",
            "grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));",
            "#log {\n    order: 3;\n    flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain;",
            "#command-panel { height: auto; min-height: 0; max-height: none; overflow: visible; }",
            "#log { min-height: clamp(280px, 42vh, 520px); max-height: 52vh; }",
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
        self.assertIn("/api/events", page)
        self.assertIn("new window.EventSource", page)
        self.assertIn("source.onopen", page)
        self.assertIn("source.onerror", page)
        self.assertIn("startPollingFallback", page)
        self.assertIn("stopPollingFallback", page)
        self.assertIn(f"POLL_INTERVAL_MS = {web_gui.WEB_GUI_POLL_INTERVAL_MS}", page)
        for forbidden in ("https://cdn.", "http://cdn.", "unpkg.com", "jsdelivr"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, page)

    def test_eventsource_callbacks_recover_scope_cursor_and_fallback(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_web_gui_page()
        script_start = page.index("function currentEventBlackboardDirectory()")
        script_end = page.index("function setText(", script_start)
        event_script = page[script_start:script_end]
        harness = r"""
const assert = require("assert");
const blackboardInput = { value: "/tmp/board-a" };
const statusNode = { textContent: "" };
const nodes = {
  "micromachine-blackboard-dir": blackboardInput,
  "micromachine-status": statusNode
};
var document = {
  getElementById: function(id) { return nodes[id] || null; }
};
var intervalSeq = 0;
var timeoutSeq = 0;
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, handler) {
    if (!this.listeners[name]) { this.listeners[name] = []; }
    this.listeners[name].push(handler);
  }
  emit(name, envelope) {
    (this.listeners[name] || []).forEach(function(handler) {
      handler({
        type: name,
        lastEventId: String(envelope.event_seq || 0),
        data: JSON.stringify(envelope)
      });
    });
  }
  close() { this.closed = true; }
}
FakeEventSource.instances = [];
var window = {
  EventSource: FakeEventSource,
  setInterval: function() { intervalSeq += 1; return intervalSeq; },
  clearInterval: function() {},
  setTimeout: function() { timeoutSeq += 1; return timeoutSeq; },
  clearTimeout: function() {}
};
var token = "";
var authJoin = "";
var lastSeq = 0;
var lastEventSeq = 0;
var commandEventBlackboardDir = "";
var commandEventSource = null;
var commandEventReconnectTimer = null;
var commandEventHealthy = false;
var commandEventFailedSources = {};
var fallbackPollingIntervals = [];
var microMachinePollQueued = false;
var microMachinePollAbortController = null;
var operationRecords = {};
var activeCommandConsoleRecord = {
  state: "idle",
  pendingId: "",
  updateId: ""
};
var renderedStatuses = [];
var appendedHistory = [];
var pollCounts = { history: 0, state: 0, micromachine: 0 };
var POLL_INTERVAL_MS = 1000;
function isMicroMachineCommandMode() { return true; }
function pollHistory() { pollCounts.history += 1; }
function pollState() { pollCounts.state += 1; }
function pollMicroMachineStatus() { pollCounts.micromachine += 1; }
function appendLog(payload) { appendedHistory.push(payload); }
function renderState() {}
function safeRenderMicroMachineStatus(payload) { renderedStatuses.push(payload); }
function commandUiText(ko) { return ko; }
function beginOperationRecord(text, pendingId) {
  operationRecords[pendingId] = {
    pendingId: pendingId,
    operationId: pendingId,
    updateId: "",
    operationGeneration: 0,
    telemetryFrame: -1,
    text: text
  };
}
function beginActiveCommandConsole(text, pendingId) {
  activeCommandConsoleRecord = {
    state: "received",
    pendingId: pendingId,
    updateId: "",
    text: text
  };
  beginOperationRecord(text, pendingId);
}
function bindOperationRecordUpdate(text, pendingId, scopeId, updateId) {
  var record = operationRecords[pendingId];
  if (!record) { return false; }
  record.text = text;
  record.scopeId = scopeId;
  record.updateId = updateId;
  return true;
}
function bindActiveCommandConsoleUpdate(text, pendingId, scopeId, updateId) {
  if (activeCommandConsoleRecord.pendingId !== pendingId) { return false; }
  activeCommandConsoleRecord.text = text;
  activeCommandConsoleRecord.scopeId = scopeId;
  activeCommandConsoleRecord.updateId = updateId;
  return true;
}
function renderActiveCommandConsole(payload) {
  activeCommandConsoleRecord.data = payload;
  activeCommandConsoleRecord.state = payload.status || activeCommandConsoleRecord.state;
}
"""
        scenario = r"""
connectEventChannel();
const sourceA = FakeEventSource.instances[0];
assert(sourceA.url.includes("blackboard_dir=%2Ftmp%2Fboard-a"));
assert(!sourceA.url.includes("after="));
assert.strictEqual(fallbackPollingIntervals.length, 3);
sourceA.onopen();
assert.strictEqual(fallbackPollingIntervals.length, 0);

sourceA.emit("snapshot", {
  event_seq: 5,
  event_type: "snapshot",
  blackboard_scope_id: "scope-a",
  payload: {
    history: { events: [], latest: 0 },
    micromachine_status: {
      blackboard_dir: "/tmp/board-a",
      blackboard_scope_id: "scope-a",
      status: "idle"
    }
  }
});
assert.strictEqual(lastEventSeq, 5);
assert.strictEqual(renderedStatuses.length, 1);

lastEventSeq = 999;
sourceA.emit("snapshot", {
  event_seq: 2,
  event_type: "snapshot",
  blackboard_scope_id: "scope-a",
  payload: {
    history: { events: [], latest: 0 },
    micromachine_status: {
      blackboard_dir: "/tmp/board-a",
      blackboard_scope_id: "scope-a",
      status: "idle"
    }
  }
});
assert.strictEqual(lastEventSeq, 2, "authoritative snapshot lowers a future cursor");

beginActiveCommandConsole("local order", "operation-local");
const localCardCount = Object.keys(operationRecords).length;
sourceA.emit("command_received", {
  event_seq: 3,
  event_type: "command_received",
  update_id: "update-local",
  operation_id: "operation-local",
  blackboard_scope_id: "scope-a",
  payload: {
    command_text: "local order",
    status: "received",
    blackboard_dir: "/tmp/board-a",
    blackboard_scope_id: "scope-a"
  }
});
assert.strictEqual(Object.keys(operationRecords).length, localCardCount);
sourceA.emit("command_received", {
  event_seq: 3,
  event_type: "command_received",
  update_id: "update-local",
  operation_id: "operation-local",
  blackboard_scope_id: "scope-a",
  payload: {
    command_text: "local order",
    status: "received",
    blackboard_dir: "/tmp/board-a",
    blackboard_scope_id: "scope-a"
  }
});
assert.strictEqual(Object.keys(operationRecords).length, localCardCount);

sourceA.emit("source_error", {
  event_seq: 4,
  event_type: "source_error",
  blackboard_scope_id: "scope-a",
  payload: {
    source: "micromachine_status",
    error: "temporarily unavailable",
    blackboard_dir: "/tmp/board-a",
    blackboard_scope_id: "scope-a"
  }
});
assert.strictEqual(fallbackPollingIntervals.length, 3);
assert(statusNode.textContent.includes("temporarily unavailable"));
sourceA.emit("state", {
  event_seq: 5,
  event_type: "state",
  blackboard_scope_id: "scope-a",
  payload: { available: true }
});
assert.strictEqual(
  fallbackPollingIntervals.length,
  3,
  "unrelated source recovery cannot clear micromachine fallback"
);
sourceA.emit("source_recovered", {
  event_seq: 6,
  event_type: "source_recovered",
  blackboard_scope_id: "scope-a",
  payload: {
    source: "micromachine_status",
    blackboard_dir: "/tmp/board-a",
    blackboard_scope_id: "scope-a"
  }
});
assert.strictEqual(fallbackPollingIntervals.length, 0);
sourceA.emit("micromachine_status", {
  event_seq: 7,
  event_type: "micromachine_status",
  blackboard_scope_id: "scope-a",
  payload: {
    blackboard_dir: "/tmp/board-a",
    blackboard_scope_id: "scope-a",
    status: "connected"
  }
});
assert.strictEqual(fallbackPollingIntervals.length, 0);

beginActiveCommandConsole("legacy active order", "legacy-active");
const beforeLegacyCardCount = Object.keys(operationRecords).length;
sourceA.emit("command_received", {
  event_seq: 8,
  event_type: "command_received",
  update_id: "",
  operation_id: "legacy-other-tab",
  blackboard_scope_id: "",
  payload: {
    command_text: "legacy other-tab order",
    status: "received",
    mode: "legacy_commander"
  }
});
assert.strictEqual(
  Object.keys(operationRecords).length,
  beforeLegacyCardCount + 1,
  "empty update IDs cannot collapse distinct operations"
);
assert.strictEqual(
  activeCommandConsoleRecord.pendingId,
  "legacy-active",
  "another empty-update operation cannot take active console ownership"
);
assert.notStrictEqual(
  activeCommandConsoleRecord.data && activeCommandConsoleRecord.data.command_text,
  "legacy other-tab order"
);

blackboardInput.value = "/tmp/board-b";
reconnectEventChannel();
const sourceB = FakeEventSource.instances[1];
assert(sourceA.closed);
assert(sourceB.url.includes("blackboard_dir=%2Ftmp%2Fboard-b"));
assert(!sourceB.url.includes("after="));
assert.strictEqual(lastEventSeq, 0);
sourceA.emit("snapshot", {
  event_seq: 100,
  event_type: "snapshot",
  blackboard_scope_id: "scope-a",
  payload: {
    history: { events: [], latest: 0 },
    micromachine_status: {
      blackboard_dir: "/tmp/board-a",
      blackboard_scope_id: "scope-a"
    }
  }
});
assert.strictEqual(lastEventSeq, 0, "closed scope cannot update the new board");
sourceB.onopen();
sourceB.emit("command_received", {
  event_seq: 1,
  event_type: "command_received",
  update_id: "update-b",
  operation_id: "operation-b",
  blackboard_scope_id: "scope-b",
  payload: {
    command_text: "board B order",
    status: "received",
    blackboard_dir: "/tmp/board-b",
    blackboard_scope_id: "scope-b"
  }
});
assert(operationRecords["operation-b"]);
assert.strictEqual(lastEventSeq, 1);
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(event_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_live_launch_passes_sc2_root_as_normalized_sc2path(self):
        configured_root = "~/custom-sc2-root"

        class FakeProcess:
            pid = 4321
            returncode = None
            stdout = []

            def poll(self):
                return None

        with mock.patch.dict(
            web_gui.os.environ,
            {
                "SC2_ROOT": configured_root,
                "SC2PATH": "/ignored-sc2path",
            },
            clear=False,
        ):
            with mock.patch.object(
                web_gui.subprocess,
                "Popen",
                return_value=FakeProcess(),
            ) as popen:
                launcher = web_gui._LiveLaunchManager()
                launcher.start("openai", "unit-test-key", "gpt-test")

        child_environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            os.path.abspath(os.path.expanduser(configured_root)),
            child_environment["SC2PATH"],
        )

    def test_default_sc2_install_path_prefers_environment_over_discovery(self):
        with mock.patch.dict(
            web_gui.os.environ,
            {
                "SC2_ROOT": "~/custom-sc2-root",
                "SC2PATH": "/ignored-sc2path",
            },
            clear=False,
        ):
            self.assertEqual(
                web_gui._default_sc2_install_path(),  # noqa: SLF001
                os.path.abspath(os.path.expanduser("~/custom-sc2-root")),
            )
        with mock.patch.dict(
            web_gui.os.environ,
            {"SC2_ROOT": "", "SC2PATH": "~/custom-sc2path"},
            clear=False,
        ):
            self.assertEqual(
                web_gui._default_sc2_install_path(),  # noqa: SLF001
                os.path.abspath(os.path.expanduser("~/custom-sc2path")),
            )

    def test_default_sc2_install_path_discovers_common_macos_location(self):
        desktop_candidate = os.path.expanduser(
            "~/Desktop/StarCraft2/StarCraft II"
        )
        with mock.patch.dict(
            web_gui.os.environ,
            {"SC2_ROOT": "", "SC2PATH": ""},
            clear=False,
        ):
            with mock.patch.object(
                web_gui.os.path,
                "isdir",
                side_effect=lambda path: path == desktop_candidate,
            ):
                self.assertEqual(
                    web_gui._default_sc2_install_path(),  # noqa: SLF001
                    os.path.abspath(desktop_candidate),
                )


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
