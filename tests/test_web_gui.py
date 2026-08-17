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
from pathlib import Path
import shutil
import stat
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
from starcraft_commander import runtime_data
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
    WebGuiBridgeInterface,
    WebGuiServer,
)
from starcraft_commander.companion_ui import render_companion_page


POLL_DEADLINE_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.05
EXECUTED_FAMILY_STATUSES = frozenset({"executed", "partially_executed"})
BRIDGE_THREAD_NAME = "voiStarcraft2-web-gui-session-loop"


def visible_sc2_launch_receipt():
    """Return a complete native visible-launch proof for launcher unit tests."""

    return {
        "accepted": True,
        "pid": 222,
        "port": web_gui.DEFAULT_SC2_API_PORT,
        "base": web_gui.REQUIRED_SC2_BASE,
        "process_created": True,
        "api_ready": True,
        "window_created": True,
        "window_onscreen": True,
        "frontmost": True,
        "screen_locked": False,
        "render_verified": True,
    }


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
                        "min_units": 2,
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
                    "owner_id": "squad:Base Defense 44 20",
                    "owner_count": 2,
                    "owner_tags": [201, 202],
                    "composition": [
                        {
                            "family": "marine",
                            "role": "base_defender",
                            "count": 2,
                            "ground_capable_count": 2,
                            "air_capable_count": 2,
                        }
                    ],
                    "integrity_status": "valid",
                }
            ],
            "unassigned_unit_tags": [301, 302],
            "bases": [
                {
                    "base_id": "base:44:20",
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


def set_semantic_operation_identity(
    payload,
    *,
    request_update_id=None,
    execution_owner_update_id=None,
):
    """Set independent request and execution-owner identity channels."""

    operation = payload["operations"][0]
    request_id = str(
        request_update_id
        if request_update_id is not None
        else operation.get("update_id", "")
    )
    owner_id = str(
        execution_owner_update_id
        if execution_owner_update_id is not None
        else request_id
    )
    operation["update_id"] = request_id
    operation["compile_result"]["update_id"] = request_id
    if isinstance(operation.get("update"), dict):
        operation["update"]["update_id"] = request_id
    operation["operation_console_execution_owner_update_id"] = owner_id
    operation["intervention"]["command_execution"]["command_id"] = owner_id
    operation["battlefield_operation"]["identity"]["update_id"] = owner_id
    return payload


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


class MicroMachineLaunchProvenanceTest(unittest.TestCase):
    def test_launcher_construction_defers_git_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            repository_root = Path(directory)
            (repository_root / ".git").mkdir()
            with (
                mock.patch.object(
                    web_gui,
                    "_REPO_ROOT",
                    str(repository_root),
                ),
                mock.patch.object(
                    web_gui,
                    "source_repository_root",
                    side_effect=AssertionError(
                        "launcher construction invoked Git provenance"
                    ),
                ),
            ):
                launcher = web_gui._MicroMachineLaunchManager()
                snapshot = launcher.snapshot()

            self.assertTrue(snapshot["enabled"])
            self.assertTrue(
                snapshot["script_path"].startswith(str(repository_root))
            )

            with mock.patch.object(
                web_gui,
                "source_repository_root",
                return_value=None,
            ):
                started = launcher.start()

        self.assertFalse(started["enabled"])
        self.assertEqual("blocked", started["status"])
        self.assertIn("current Git provenance", started["error"])

    def test_launcher_fails_closed_when_live_cockpit_dependency_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
            with mock.patch.object(
                web_gui,
                "read_sc2_launch_receipt",
                side_effect=ModuleNotFoundError(
                    "No module named 'starcraft_commander.local_cockpit'"
                ),
            ):
                started = launcher.start(
                    directory,
                    sc2_launch_nonce="visible-launch-nonce",
                )

        self.assertEqual("failed", started["status"])
        self.assertIn("Live cockpit dependency unavailable", started["error"])
        self.assertFalse(started["runtime_attached"])
        self.assertFalse(started.get("accepted", False))

    def test_launcher_fails_closed_when_sc2_resolver_dependency_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
            validated_launcher = io.BytesIO(b"validated launcher")
            launcher._requires_source_provenance = True  # noqa: SLF001
            launcher._launch_available = True  # noqa: SLF001
            with (
                mock.patch.object(
                    launcher,
                    "_validated_source_launcher_unlocked",
                    return_value=(validated_launcher, directory),
                ),
                mock.patch.object(
                    web_gui,
                    "read_sc2_launch_receipt",
                    return_value=visible_sc2_launch_receipt(),
                ),
                mock.patch.object(
                    web_gui,
                    "resolve_required_sc2_executable",
                    side_effect=ModuleNotFoundError(
                        "No module named 'starcraft_commander.local_cockpit'"
                    ),
                ),
            ):
                started = launcher.start(
                    directory,
                    sc2_launch_nonce="visible-launch-nonce",
                )

        self.assertEqual("failed", started["status"])
        self.assertIn("Live cockpit dependency unavailable", started["error"])
        self.assertFalse(started["runtime_attached"])
        self.assertFalse(started.get("accepted", False))
        self.assertTrue(validated_launcher.closed)

    def test_launcher_executes_validated_bytes_after_path_replacement(self):
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
            clone_root = Path(directory) / "checkout"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--no-hardlinks",
                    str(Path(__file__).resolve().parents[1]),
                    str(clone_root),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/Marker-Inc-Korea/voiStarcraft2.git",
                ],
                cwd=clone_root,
                check=True,
            )
            runtime_path = (
                clone_root / "starcraft_commander" / "runtime_data.py"
            )
            launcher_path = (
                clone_root
                / "integrations"
                / "micromachine"
                / "scripts"
                / "smoke_macos_local.sh"
            )
            admitted_payload = launcher_path.read_bytes()
            replacement_payload = b"#!/bin/sh\nexit 97\n"
            observed: dict[str, object] = {}

            def replace_after_validation(
                arguments,
                *,
                env,
                launcher_input,
            ):
                launcher_path.write_bytes(replacement_payload)
                observed["argv"] = arguments
                observed["payload"] = launcher_input.read()
                observed["environment"] = env
                return FakeProcess()

            with (
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_MODULE_LOCATION",
                    runtime_path,
                ),
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_MODULE_PATH",
                    runtime_path.resolve(),
                ),
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_REPOSITORY_ROOT",
                    clone_root.resolve(),
                ),
                mock.patch.object(web_gui, "_REPO_ROOT", str(clone_root)),
            ):
                launcher = web_gui._MicroMachineLaunchManager()
                with (
                    mock.patch.object(
                        web_gui,
                        "read_sc2_launch_receipt",
                        return_value=visible_sc2_launch_receipt(),
                    ),
                    mock.patch.object(
                        launcher,
                        "_spawn_process_unlocked",
                        side_effect=replace_after_validation,
                    ),
                ):
                    started = launcher.start(
                        str(Path(directory) / "blackboard"),
                        sc2_launch_nonce="test-visible-launch-nonce",
                    )

        self.assertTrue(started["enabled"], started)
        self.assertEqual(admitted_payload, observed["payload"])
        self.assertNotEqual(replacement_payload, observed["payload"])
        self.assertEqual(
            ["/bin/bash", "-s", "--"],
            list(observed["argv"])[:3],
        )
        self.assertEqual(
            str(
                (
                    clone_root
                    / "integrations"
                    / "micromachine"
                    / "scripts"
                ).resolve()
            ),
            observed["environment"][
                web_gui._MICROMACHINE_VALIDATED_SCRIPT_DIR_ENV
            ],
        )

    def test_launcher_revalidates_git_provenance_at_start(self):
        with tempfile.TemporaryDirectory() as directory:
            clone_root = Path(directory) / "checkout"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--no-hardlinks",
                    str(Path(__file__).resolve().parents[1]),
                    str(clone_root),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/Marker-Inc-Korea/voiStarcraft2.git",
                ],
                cwd=clone_root,
                check=True,
            )
            runtime_path = (
                clone_root / "starcraft_commander" / "runtime_data.py"
            )
            launcher_path = (
                clone_root
                / "integrations"
                / "micromachine"
                / "scripts"
                / "smoke_macos_local.sh"
            )
            original_payload = launcher_path.read_bytes()
            original_mode = stat.S_IMODE(launcher_path.stat().st_mode)
            replacement = clone_root / "MANIFEST.in"
            real_popen = subprocess.Popen

            def reject_launcher_execution(*args, **kwargs):
                arguments = args[0]
                if arguments and arguments[0] == "bash":
                    raise AssertionError(
                        "mutated launcher reached subprocess execution"
                    )
                return real_popen(*args, **kwargs)

            def restore_launcher() -> None:
                if launcher_path.exists() or launcher_path.is_symlink():
                    launcher_path.unlink()
                launcher_path.write_bytes(original_payload)
                launcher_path.chmod(original_mode)

            mutations = {
                "content": lambda: launcher_path.write_bytes(
                    original_payload + b"\n# post-import mutation\n"
                ),
                "mode": lambda: launcher_path.chmod(0o644),
                "symlink": lambda: (
                    launcher_path.unlink(),
                    launcher_path.symlink_to(replacement),
                ),
            }
            with (
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_MODULE_LOCATION",
                    runtime_path,
                ),
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_MODULE_PATH",
                    runtime_path.resolve(),
                ),
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_REPOSITORY_ROOT",
                    clone_root.resolve(),
                ),
                mock.patch.object(web_gui, "_REPO_ROOT", str(clone_root)),
            ):
                self.assertEqual(
                    clone_root.resolve(),
                    web_gui.source_repository_root(),
                )
                for name, mutate in mutations.items():
                    with self.subTest(name=name):
                        restore_launcher()
                        launcher = web_gui._MicroMachineLaunchManager()
                        mutate()
                        self.assertIsNone(
                            web_gui.source_repository_root()
                        )
                        with mock.patch.object(
                            web_gui.subprocess,
                            "Popen",
                            side_effect=reject_launcher_execution,
                        ):
                            started = launcher.start()

                        self.assertFalse(started["enabled"])
                        self.assertEqual("blocked", started["status"])
                        self.assertIn(
                            "current Git provenance",
                            started["error"],
                        )
                restore_launcher()


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

    def post_command(self, text, request_id=""):
        document = {"text": text}
        if request_id:
            document["request_id"] = request_id
            document["operation_id"] = request_id
        body = json.dumps(document).encode("utf-8")
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

    def test_frame_less_command_ignores_detached_stale_telemetry_frame(self):
        class DetachedLauncher:
            def validated_snapshot(self, blackboard_dir=""):
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata={
                        "blackboard_dir": blackboard_dir,
                        "runtime_attached": False,
                        "telemetry_current_for_process": False,
                        "telemetry_stale_or_detached": True,
                        "telemetry_frame": 11_971,
                    },
                    telemetry_document=None,
                )

        self.server._http.micromachine_launcher = DetachedLauncher()
        accepted = {
            "accepted": True,
            "ok": True,
            "queued": True,
            "async_publish": True,
            "status": "queued",
            "update_id": "detached-frame-command",
            "consumption_status": "pending_compile",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                self.bridge,
                "submit_micromachine_modulation_background",
                return_value=accepted,
            ) as submit,
        ):
            status, _content_type, _payload = self.post_micromachine_modulation(
                {
                    "text": "SCV를 생산한다",
                    "blackboard_dir": directory,
                    "async_publish": True,
                    "update_id": "detached-frame-command",
                }
            )

        self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
        self.assertEqual(0, submit.call_args.kwargs["current_frame"])
        resolver = submit.call_args.kwargs["publish_frame_resolver"]
        self.assertTrue(callable(resolver))
        self.assertIsNone(resolver())

    def test_frame_less_command_uses_current_attached_telemetry_frame(self):
        runtime_instance_id = "a" * 32

        class AttachedLauncher:
            frame = 1_634

            def validated_snapshot(self, blackboard_dir=""):
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata={
                        "blackboard_dir": blackboard_dir,
                        "runtime_instance_id": runtime_instance_id,
                        "runtime_attached": True,
                        "telemetry_current_for_process": True,
                        "telemetry_stale_or_detached": False,
                        "telemetry_frame": self.frame,
                    },
                    telemetry_document={
                        "frame": self.frame,
                        "runtime_instance_id": runtime_instance_id,
                    },
                )

        launcher = AttachedLauncher()
        self.server._http.micromachine_launcher = launcher
        accepted = {
            "accepted": True,
            "ok": True,
            "queued": True,
            "async_publish": True,
            "status": "queued",
            "update_id": "attached-frame-command",
            "consumption_status": "pending_compile",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                self.bridge,
                "submit_micromachine_modulation_background",
                return_value=accepted,
            ) as submit,
        ):
            status, _content_type, _payload = self.post_micromachine_modulation(
                {
                    "text": "SCV를 생산한다",
                    "blackboard_dir": directory,
                    "async_publish": True,
                    "update_id": "attached-frame-command",
                }
            )

        self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
        self.assertEqual(1_634, submit.call_args.kwargs["current_frame"])
        resolver = submit.call_args.kwargs["publish_frame_resolver"]
        self.assertTrue(callable(resolver))
        launcher.frame = 1_700
        self.assertEqual(1_700, resolver())

    def test_explicit_command_frame_remains_fixed_for_async_publish(self):
        accepted = {
            "accepted": True,
            "ok": True,
            "queued": True,
            "async_publish": True,
            "status": "queued",
            "update_id": "explicit-frame-command",
            "consumption_status": "pending_compile",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                self.bridge,
                "submit_micromachine_modulation_background",
                return_value=accepted,
            ) as submit,
        ):
            status, _content_type, _payload = self.post_micromachine_modulation(
                {
                    "text": "SCV를 생산한다",
                    "blackboard_dir": directory,
                    "async_publish": True,
                    "current_frame": 88,
                    "update_id": "explicit-frame-command",
                }
            )

        self.assertEqual(HTTPStatus.ACCEPTED, HTTPStatus(status))
        self.assertEqual(88, submit.call_args.kwargs["current_frame"])
        self.assertIsNone(
            submit.call_args.kwargs["publish_frame_resolver"]
        )

    def attach_fake_micromachine_runtime(self, directory):
        runtime_instance_id = "f" * 32
        telemetry_path = os.path.join(directory, "latest_telemetry.json")
        if os.path.exists(telemetry_path):
            with open(telemetry_path, encoding="utf-8") as handle:
                telemetry = json.load(handle)
        else:
            telemetry = {
                "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                "frame": 1,
                "bot_name": "MicroMachine",
                "race": "Terran",
                "managers": {},
                "active_modulation_ids": [],
                "last_failure": None,
            }
        telemetry["runtime_instance_id"] = runtime_instance_id
        with open(telemetry_path, "w", encoding="utf-8") as handle:
            json.dump(telemetry, handle)

        class FakeAttachedMicroMachineLauncher:
            def snapshot(self, blackboard_dir=""):
                del blackboard_dir
                root = directory
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
                del blackboard_dir
                root = directory
                telemetry_path = os.path.join(root, "latest_telemetry.json")
                with open(telemetry_path, encoding="utf-8") as handle:
                    telemetry = json.load(handle)
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata=self.snapshot(root),
                    telemetry_document=telemetry,
                )

        self.server._http.micromachine_launcher = FakeAttachedMicroMachineLauncher()

    def detach_fake_micromachine_runtime(self, directory):
        class FakeDetachedMicroMachineLauncher:
            def validated_snapshot(self, blackboard_dir=""):
                return web_gui._MicroMachineValidatedRuntimeSnapshot(
                    metadata={
                        "enabled": True,
                        "mode": "micromachine",
                        "status": "idle",
                        "blackboard_dir": blackboard_dir or directory,
                        "runtime_instance_id": "",
                        "runtime_attached": False,
                        "telemetry_present": True,
                        "telemetry_current_for_process": False,
                        "telemetry_stale_or_detached": True,
                        "telemetry_frame": 1,
                    },
                    telemetry_document=None,
                )

        self.server._http.micromachine_launcher = (
            FakeDetachedMicroMachineLauncher()
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

    def test_index_page_serves_only_compact_controller(self):
        status, content_type, payload = self.request("GET", "/")
        page = payload.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        for fragment in (
            "전술 명령창",
            "현재 명령",
            "SC2 / MicroMachine 시작",
            "긴급 전군 후퇴",
            "/api/runtime/start",
            "/api/runtime/status",
            "/api/micromachine/modulate",
            "/api/micromachine/status",
            "SpeechRecognition",
            "async_publish: true",
            "selectCommand",
            "latest_request",
            "정책 적용",
            "명령 해석 중",
            "commandIdentity",
            'status: "queued"',
            'consumption_status: "pending_compile"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)
        for removed_fragment in (
            "cockpit-back",
            "전체 조종석",
            "커맨더 채팅",
            "전장 대시보드",
            "전략 브리핑",
            "LLM 설정",
            "Legacy python-sc2 commander",
            "window.open(",
        ):
            with self.subTest(removed_fragment=removed_fragment):
                self.assertNotIn(removed_fragment, page)

    def test_companion_page_is_compact_and_uses_existing_runtime_apis(self):
        status, content_type, payload = self.request("GET", "/companion")
        page = payload.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        for fragment in (
            "전술 명령창",
            "현재 명령",
            "SC2 / MicroMachine 시작",
            "긴급 전군 후퇴",
            "/api/runtime/start",
            "/api/runtime/status",
            "/api/micromachine/status",
            "/api/micromachine/modulate",
            "async_publish: true",
            "SpeechRecognition",
            "selectCommand",
            "submitCommandWithRuntime",
            "ensureRuntimeForCommand",
            "SC2 시작은 설치된 voiStarcraft2 앱에서만 사용할 수 있습니다.",
            "latest_request",
            "명령 해석 중",
            "SC2 실행 대기",
            "commandIdentity",
            'status: "queued"',
            'consumption_status: "pending_compile"',
            "width",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, page)
        self.assertNotIn("LLM 설정", page)
        self.assertNotIn("전장 통제", page)
        self.assertNotIn("cockpit-back", page)
        self.assertNotIn("전체 조종석", page)
        self.assertNotIn("window.open(", page)

    def test_all_controller_routes_serve_the_same_compact_page(self):
        pages = []
        for path in ("/", "/index.html", "/companion"):
            status, content_type, payload = self.request("GET", path)
            with self.subTest(path=path):
                self.assertEqual(status, 200)
                self.assertIn("text/html", content_type)
            pages.append(payload)

        self.assertEqual(pages[0], pages[1])
        self.assertEqual(pages[0], pages[2])
        self.assertFalse(hasattr(web_gui, "_WEB_GUI_PAGE_TEMPLATE"))
        self.assertFalse(hasattr(web_gui, "render_web_gui_page"))

    def test_companion_renderer_escapes_script_breakout_in_default_path(self):
        page = render_companion_page("</script><script>alert(1)</script>")

        self.assertNotIn("</script><script>alert(1)</script>", page)
        self.assertIn("\\u003c/script>", page)

    def test_companion_prefers_latest_natural_language_command(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_companion_page()
        script_start = page.index("  function unitName(")
        script_end = page.index("  function renderOperation(", script_start)
        command_script = page[script_start:script_end]
        scenario = r"""
const assert = require("assert");
const staleOperation = {
  active: true,
  operation_id: "standing-autonomy",
  update_id: "standing-update",
  command_text: "기존 자율 작전"
};
const latest = selectCommand({
  status: "queued",
  consumption_status: "pending_compile",
  latest_request: {
    update_id: "latest-scv-command",
    command_text: "SCV를 생산한다",
    consumption_status: "pending_compile"
  },
  operations: [staleOperation]
});
assert.strictEqual(latest.update_id, "latest-scv-command");
assert.strictEqual(operationGoal(latest), "SCV를 생산한다");
assert.strictEqual(operationStage(latest), "명령 해석 중");

const matching = selectCommand({
  status: "published",
  latest_request: {
    update_id: "latest-scv-command",
    command_text: "SCV를 생산한다"
  },
  operations: [
    staleOperation,
    {
      active: false,
      operation_id: "scv-production",
      update: { update_id: "latest-scv-command", vector: {} }
    }
  ]
});
assert.strictEqual(matching.operation_id, "scv-production");
assert.strictEqual(operationGoal(matching), "SCV를 생산한다");
assert.strictEqual(operationStage(matching), "명령 전달");
assert.strictEqual(
  operationStage(matching, {
    runtime_attached: false,
    telemetry_current_for_process: false,
    telemetry_stale_or_detached: true
  }),
  "SC2 실행 대기"
);

const splitOwner = selectCommand({
  status: "published",
  consumption_status: "consumed",
  latest_request: {
    update_id: "new-edit",
    command_text: "새 공격 명령"
  },
  operations: [{
    operation_id: "assault",
    update_id: "new-edit",
    operation_console_execution_owner_update_id: "old-command",
    intervention: { command_execution: { state: "effect_observed" } }
  }]
});
assert.strictEqual(operationStage(splitOwner), "정책 적용");

const currentRuntime = {
  runtime_attached: true,
  telemetry_current_for_process: true,
  telemetry_stale_or_detached: false
};
const matchedEffect = {
  operation_id: "assault",
  operation_generation: 2,
  update_id: "assault-update",
  consumption_status: "consumed",
  operation_console_execution_owner_update_id: "assault-update",
  intervention: {
    command_execution: {
      command_id: "assault-update",
      operation_id: "assault",
      operation_generation: 2,
      state: "effect_observed"
    }
  }
};
assert.strictEqual(operationStage(matchedEffect, currentRuntime), "효과 확인");
assert.strictEqual(
  operationStage({
    ...matchedEffect,
    operation_console_execution_owner_update_id: "",
    intervention: {
      command_execution: {
        ...matchedEffect.intervention.command_execution,
        command_id: ""
      }
    }
  }, currentRuntime),
  "정책 적용"
);
assert.strictEqual(
  operationStage({
    ...matchedEffect,
    intervention: {
      command_execution: {
        ...matchedEffect.intervention.command_execution,
        operation_generation: 1
      }
    }
  }, currentRuntime),
  "정책 적용"
);
assert.strictEqual(
  operationStage({
    ...matchedEffect,
    disposition: "completed"
  }, {
    runtime_attached: false,
    telemetry_current_for_process: false,
    telemetry_stale_or_detached: true
  }),
  "SC2 실행 대기"
);
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(command_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_companion_ignores_stale_command_responses_and_polls(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_companion_page()
        script_start = page.index('  "use strict";')
        script_end = page.index("  function setupVoice(", script_start)
        command_script = page[script_start:script_end]
        harness = r"""
const assert = require("assert");

class FakeElement {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = "";
    this.value = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }
  appendChild(child) {
    this.children.push(child);
    this.scrollHeight = this.children.length;
    return child;
  }
  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
  }
  get firstChild() {
    return this.children[0] || null;
  }
}

const nodes = {
  "caption-list": new FakeElement(),
  "command-feedback": new FakeElement(),
  "command-input": new FakeElement(),
  "operation-composition": new FakeElement(),
  "operation-goal": new FakeElement(),
  "operation-stage": new FakeElement(),
  "runtime-status": new FakeElement()
};
global.document = {
  createElement: function() { return new FakeElement(); },
  getElementById: function(id) { return nodes[id]; }
};
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; }
};
const requests = [];
global.fetch = function(url, options) {
  return new Promise(function(resolve, reject) {
    requests.push({ url: url, options: options, resolve: resolve, reject: reject });
  });
};
function response(payload) {
  return {
    ok: true,
    status: 200,
    text: function() { return Promise.resolve(JSON.stringify(payload)); }
  };
}
"""
        scenario = r"""
(async function() {
  const firstPromise = submitCommand("첫 번째 명령");
  const firstRequest = JSON.parse(requests[0].options.body);
  const secondPromise = submitCommand("두 번째 최신 명령");
  const secondRequest = JSON.parse(requests[1].options.body);
  nodes["command-input"].value = "사용자가 새로 작성 중";

  renderOperation({
    status: "published",
    latest_request: {
      update_id: "older-standing-update",
      command_text: "오래된 상시 작전"
    },
    operations: []
  });
  assert.strictEqual(nodes["operation-goal"].textContent, "두 번째 최신 명령");

  requests[1].resolve(response({
    status: "queued",
    async_publish: true,
    consumption_status: "pending_compile",
    update_id: secondRequest.update_id
  }));
  await secondPromise;
  requests[0].resolve(response({
    status: "queued",
    async_publish: true,
    consumption_status: "pending_compile",
    update_id: firstRequest.update_id
  }));
  await firstPromise;

  assert.strictEqual(lastSubmittedUpdateId, secondRequest.update_id);
  assert.strictEqual(nodes["operation-goal"].textContent, "두 번째 최신 명령");
  assert.strictEqual(nodes["command-input"].value, "사용자가 새로 작성 중");
  assert.strictEqual(operationStage(selectCommand(pendingCommand.payload)), "명령 해석 중");

  renderOperation({
    status: "published",
    consumption_status: "consumed",
    latest_request: {
      update_id: secondRequest.update_id,
      command_text: "두 번째 최신 명령"
    },
    operations: []
  });
  assert.strictEqual(nodes["operation-stage"].textContent, "정책 적용");
  assert.strictEqual(pendingCommand, null);
})().catch(function(error) {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(command_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_operation_event_snapshot_hydration_is_read_only(self):
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
            0,
            self.server._http._observed_operation_event_seq.get(
                scope_id,
                0,
            ),
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
            if (
                event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == scope_id
            )
        ]
        self.assertEqual([], operation_events)
        self.assertEqual(
            2,
            self.server._http._observed_operation_event_seq[scope_id],
        )

        third = {
            **first,
            "timeline_seq": 3,
            "kind": "movement_observed",
            "game_frame": 102,
            "summary": "movement observed",
        }
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": scope_id,
                "operation_events": [first, second, third],
            },
            blackboard_dir="/tmp/operation-events",
            publish=True,
        )
        operation_events = [
            event
            for event in self.server._http.event_journal.events_after(0)
            if (
                event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == scope_id
            )
        ]
        self.assertEqual(1, len(operation_events))
        self.assertEqual("movement_observed", operation_events[0]["payload"]["kind"])
        self.assertEqual(3, operation_events[0]["payload"]["timeline_seq"])
        self.assertEqual("update-scout-alpha", operation_events[0]["update_id"])
        self.assertEqual(1, operation_events[0]["generation"])
        self.assertEqual(102, operation_events[0]["game_frame"])

    def test_new_subscriber_snapshot_cannot_hide_lifecycle_from_existing_subscriber(
        self,
    ):
        snapshot_handler = object.__new__(web_gui._WebGuiRequestHandler)
        snapshot_handler.server = self.server._http
        publisher_handler = object.__new__(web_gui._WebGuiRequestHandler)
        publisher_handler.server = self.server._http
        scope_id = "scope-snapshot-lifecycle-high-water"
        first = {
            "timeline_seq": 1,
            "operation_id": "snapshot-alpha",
            "generation": 2,
            "requested_generation": 2,
            "update_id": "update-snapshot-alpha",
            "kind": "movement_observed",
            "game_frame": 200,
            "summary": "movement observed",
            "technical": {},
        }
        status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first],
        }
        snapshot_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: status
        )
        snapshot_handler._authoritative_event_snapshot = (
            lambda _directory, **_kwargs: {
            "state": {"available": True},
            "history": {"events": [], "latest": 0},
            "micromachine_status": status,
            }
        )
        written = []
        snapshot_handler._write_sse_event = written.append

        cursor = snapshot_handler._write_authoritative_sse_snapshot(
            self.server._http.event_journal,
            "/tmp/snapshot-lifecycle",
            scope_id,
        )

        self.assertEqual(self.server._http.event_journal.latest_seq, cursor)
        self.assertEqual("snapshot", written[0]["event_type"])
        self.assertEqual(
            1,
            self.server._http._observed_operation_event_seq.get(
                scope_id,
                0,
            ),
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
            "kind": "engagement_observed",
            "game_frame": 201,
            "summary": "engagement observed",
        }
        publisher_handler._publish_new_operation_events(
            {
                "blackboard_scope_id": scope_id,
                "operation_events": [first, second],
            },
            blackboard_dir="/tmp/snapshot-lifecycle",
            publish=True,
        )
        operation_events = [
            event
            for event in self.server._http.event_journal.events_after(0)
            if (
                event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == scope_id
            )
        ]
        self.assertEqual(1, len(operation_events))
        self.assertEqual(
            "engagement_observed",
            operation_events[0]["payload"]["kind"],
        )
        self.assertEqual(
            2,
            operation_events[0]["payload"]["timeline_seq"],
        )

        third = {
            **first,
            "timeline_seq": 3,
            "kind": "target_reached",
            "game_frame": 202,
            "summary": "target reached",
        }
        publisher_handler._publish_new_operation_events(
            {
                "blackboard_scope_id": scope_id,
                "operation_events": [first, second, third],
            },
            blackboard_dir="/tmp/snapshot-lifecycle",
            publish=True,
        )
        operation_events = [
            event
            for event in self.server._http.event_journal.events_after(0)
            if (
                event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == scope_id
            )
        ]
        self.assertEqual(2, len(operation_events))
        self.assertEqual("target_reached", operation_events[-1]["payload"]["kind"])

    def test_post_cut_event_in_snapshot_and_refresh_is_still_published(self):
        snapshot_handler = object.__new__(web_gui._WebGuiRequestHandler)
        snapshot_handler.server = self.server._http
        refresh_handler = object.__new__(web_gui._WebGuiRequestHandler)
        refresh_handler.server = self.server._http
        scope_id = "scope-snapshot-same-read-race"
        blackboard_dir = "/tmp/snapshot-same-read-race"
        first = {
            "timeline_seq": 1,
            "operation_id": "snapshot-same-read-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-snapshot-same-read-alpha",
            "kind": "movement_observed",
            "game_frame": 300,
            "summary": "movement observed",
            "technical": {},
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 301,
            "summary": "engagement observed",
        }
        source_events = [first]
        capture_entered = threading.Event()
        capture_release = threading.Event()
        source_lock = threading.Lock()
        source_reads = 0

        def status(_directory, **_kwargs):
            nonlocal source_reads
            with source_lock:
                source_reads += 1
                read_number = source_reads
            if read_number == 2:
                capture_entered.set()
                if not capture_release.wait(2):
                    raise TimeoutError("test did not release source capture")
            return {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": scope_id,
                "operation_events": list(source_events),
            }

        def snapshot(_directory, **kwargs):
            return {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }

        snapshot_handler._micromachine_status_payload = status
        snapshot_handler._authoritative_event_snapshot = snapshot
        snapshot_handler._write_sse_event = lambda _event: None
        refresh_handler._state_payload = lambda: {"available": True}
        refresh_handler._micromachine_status_payload = status

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            snapshot_future = pool.submit(
                snapshot_handler._write_authoritative_sse_snapshot,
                self.server._http.event_journal,
                blackboard_dir,
                scope_id,
            )
            self.assertTrue(capture_entered.wait(1))

            # The first read materialized event 1 as historical. Event 2 is
            # created during the official capture and must be journaled.
            source_events.append(second)
            refresh_future = pool.submit(
                refresh_handler._refresh_event_sources,
                blackboard_dir,
            )
            time.sleep(0.05)
            self.assertFalse(refresh_future.done())

            capture_release.set()
            snapshot_future.result(timeout=3)
            refresh_future.result(timeout=3)
        operation_events = [
            event
            for event in self.server._http.event_journal.events_after(0)
            if (
                event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == scope_id
            )
        ]
        self.assertEqual(1, len(operation_events))
        self.assertEqual(2, operation_events[0]["payload"]["timeline_seq"])
        self.assertEqual(
            "engagement_observed",
            operation_events[0]["payload"]["kind"],
        )

    def test_failed_snapshot_does_not_absorb_post_cut_event(self):
        snapshot_handler = object.__new__(web_gui._WebGuiRequestHandler)
        snapshot_handler.server = self.server._http
        scope_id = "scope-failed-snapshot-race"
        blackboard_dir = "/tmp/failed-snapshot-race"
        first = {
            "timeline_seq": 1,
            "operation_id": "failed-snapshot-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-failed-snapshot-alpha",
            "kind": "movement_observed",
            "game_frame": 350,
            "summary": "movement observed",
            "technical": {},
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 351,
            "summary": "engagement observed",
        }
        baseline_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first],
        }
        current_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first, second],
        }
        statuses = iter((baseline_status, current_status))

        def status(_directory, **_kwargs):
            return next(statuses)

        def failing_snapshot(_directory, **_kwargs):
            raise RuntimeError("snapshot failed")

        snapshot_handler._micromachine_status_payload = status
        snapshot_handler._authoritative_event_snapshot = failing_snapshot

        with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
            snapshot_handler._write_authoritative_sse_snapshot(
                self.server._http.event_journal,
                blackboard_dir,
                scope_id,
            )

        operation_events = [
            event
            for event in self.server._http.event_journal.events_after(0)
            if (
                event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == scope_id
            )
        ]
        self.assertEqual(
            [2],
            [
                event["payload"]["timeline_seq"]
                for event in operation_events
            ],
        )
        snapshot_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: current_status
        )
        snapshot_handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        snapshot_handler._write_sse_event = written.append
        cursor = snapshot_handler._write_authoritative_sse_snapshot(
            self.server._http.event_journal,
            blackboard_dir,
            scope_id,
        )
        replay_available, replay_events = (
            self.server._http.event_journal.replay_batch(cursor)
        )
        self.assertTrue(replay_available)
        snapshot_handler._write_visible_sse_events(
            replay_events,
            cursor=cursor,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            ["snapshot", "operation_event"],
            [event["event_type"] for event in written],
        )
        self.assertIn(
            scope_id,
            self.server._http._pending_operation_events,
        )

    def test_failed_snapshot_write_does_not_absorb_post_cut_event(self):
        snapshot_handler = object.__new__(web_gui._WebGuiRequestHandler)
        snapshot_handler.server = self.server._http
        scope_id = "scope-failed-snapshot-write"
        blackboard_dir = "/tmp/failed-snapshot-write"
        first = {
            "timeline_seq": 1,
            "operation_id": "failed-write-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-failed-write-alpha",
            "kind": "movement_observed",
            "game_frame": 360,
            "summary": "movement observed",
            "technical": {},
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 361,
            "summary": "engagement observed",
        }
        baseline_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first],
        }
        current_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first, second],
        }
        statuses = iter((baseline_status, current_status))

        snapshot_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: next(statuses)
        )
        snapshot_handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        snapshot_handler._write_sse_event = lambda _event: (
            (_ for _ in ()).throw(BrokenPipeError("snapshot write failed"))
        )

        with self.assertRaisesRegex(
            BrokenPipeError,
            "snapshot write failed",
        ):
            snapshot_handler._write_authoritative_sse_snapshot(
                self.server._http.event_journal,
                blackboard_dir,
                scope_id,
            )

        operation_events = [
            event
            for event in self.server._http.event_journal.events_after(0)
            if (
                event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == scope_id
            )
        ]
        self.assertEqual(
            [2],
            [
                event["payload"]["timeline_seq"]
                for event in operation_events
            ],
        )
        snapshot_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: current_status
        )
        written = []
        snapshot_handler._write_sse_event = written.append
        cursor = snapshot_handler._write_authoritative_sse_snapshot(
            self.server._http.event_journal,
            blackboard_dir,
            scope_id,
        )
        replay_available, replay_events = (
            self.server._http.event_journal.replay_batch(cursor)
        )
        self.assertTrue(replay_available)
        snapshot_handler._write_visible_sse_events(
            replay_events,
            cursor=cursor,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            ["snapshot", "operation_event"],
            [event["event_type"] for event in written],
        )
        self.assertIn(
            scope_id,
            self.server._http._pending_operation_events,
        )

    def test_failed_snapshot_event_survives_journal_rollover(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        journal = web_gui._WebEventJournal(retention=2)
        original_journal = server.event_journal
        server.event_journal = journal
        self.addCleanup(setattr, server, "event_journal", original_journal)
        scope_id = "scope-failed-snapshot-rollover"
        blackboard_dir = "/tmp/failed-snapshot-rollover"
        first = {
            "timeline_seq": 1,
            "operation_id": "rollover-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-rollover-alpha",
            "kind": "movement_observed",
            "game_frame": 370,
            "summary": "movement observed",
            "technical": {},
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 371,
            "summary": "engagement observed",
        }
        third = {
            **first,
            "timeline_seq": 3,
            "kind": "target_reached",
            "game_frame": 372,
            "summary": "target reached",
        }
        fourth = {
            **first,
            "timeline_seq": 4,
            "kind": "completed",
            "game_frame": 373,
            "summary": "completed",
        }
        baseline_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first],
        }
        current_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first, second, third, fourth],
        }
        statuses = iter((baseline_status, current_status))
        handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: next(statuses)
        )
        handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        handler._write_sse_event = lambda _event: (
            (_ for _ in ()).throw(BrokenPipeError("snapshot write failed"))
        )

        with self.assertRaises(BrokenPipeError):
            handler._write_authoritative_sse_snapshot(
                journal,
                blackboard_dir,
                scope_id,
            )
        for index in range(2):
            server.publish_event(
                "command_received",
                {"command_text": f"rollover-{index}"},
            )
        self.assertGreater(journal.oldest_seq, 1)

        handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: current_status
        )
        written = []
        handler._write_sse_event = written.append
        cursor = handler._write_authoritative_sse_snapshot(
            journal,
            blackboard_dir,
            scope_id,
        )
        replay_available, replay_events = journal.replay_batch(cursor)
        self.assertTrue(replay_available)
        handler._write_visible_sse_events(
            replay_events,
            cursor=cursor,
            blackboard_scope_id=scope_id,
        )

        lifecycle = [
            event
            for event in written
            if event["event_type"] == "operation_event"
        ]
        self.assertEqual(
            [2, 3, 4],
            [
                event["payload"]["timeline_seq"]
                for event in lifecycle
            ],
        )
        self.assertIn(scope_id, server._pending_operation_events)

    def test_each_new_subscriber_receives_recent_operation_lifecycle(self):
        first_handler = object.__new__(web_gui._WebGuiRequestHandler)
        first_handler.server = self.server._http
        second_handler = object.__new__(web_gui._WebGuiRequestHandler)
        second_handler.server = self.server._http
        server = self.server._http
        scope_id = "scope-independent-subscriber-replay"
        blackboard_dir = "/tmp/independent-subscriber-replay"
        first = {
            "timeline_seq": 1,
            "operation_id": "subscriber-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-subscriber-alpha",
            "kind": "movement_observed",
            "game_frame": 380,
            "summary": "movement observed",
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 381,
            "summary": "engagement observed",
        }
        baseline_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first],
        }
        current_status = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [first, second],
        }
        first_handler._publish_new_operation_events(
            baseline_status,
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        first_handler._publish_new_operation_events(
            current_status,
            blackboard_dir=blackboard_dir,
            publish=True,
        )

        def configure(handler, written):
            handler._micromachine_status_payload = (
                lambda _directory, **_kwargs: current_status
            )
            handler._authoritative_event_snapshot = (
                lambda _directory, **kwargs: {
                    "state": {"available": True},
                    "history": {"events": [], "latest": 0},
                    "micromachine_status": kwargs[
                        "micromachine_status"
                    ],
                }
            )
            handler._write_sse_event = written.append

        first_written = []
        second_written = []
        configure(first_handler, first_written)
        configure(second_handler, second_written)

        first_handler._write_authoritative_sse_snapshot(
            server.event_journal,
            blackboard_dir,
            scope_id,
        )
        for index in range(web_gui.WEB_GUI_EVENT_RETENTION):
            server.publish_event(
                "command_received",
                {"command_text": f"subscriber-rollover-{index}"},
            )
        second_handler._write_authoritative_sse_snapshot(
            server.event_journal,
            blackboard_dir,
            scope_id,
        )

        for written in (first_written, second_written):
            lifecycle = [
                event
                for event in written
                if event["event_type"] == "operation_event"
            ]
            self.assertEqual(1, len(lifecycle))
            self.assertEqual(
                2,
                lifecycle[0]["payload"]["timeline_seq"],
            )

    def test_snapshot_socket_write_does_not_hold_scope_source_lock(self):
        snapshot_handler = object.__new__(web_gui._WebGuiRequestHandler)
        snapshot_handler.server = self.server._http
        refresh_handler = object.__new__(web_gui._WebGuiRequestHandler)
        refresh_handler.server = self.server._http
        scope_id = "scope-snapshot-socket-write"
        blackboard_dir = "/tmp/snapshot-socket-write"
        baseline = {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "operation_events": [],
        }
        advanced = {
            **baseline,
            "status": "advanced",
        }
        snapshot_handler._publish_new_operation_events(
            baseline,
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        snapshot_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: baseline
        )
        snapshot_handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        write_entered = threading.Event()
        write_release = threading.Event()

        def blocking_write(_event):
            write_entered.set()
            if not write_release.wait(2):
                raise TimeoutError("test did not release snapshot write")

        snapshot_handler._write_sse_event = blocking_write
        refresh_handler._state_payload = lambda: {"available": True}
        refresh_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: advanced
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            snapshot_future = pool.submit(
                snapshot_handler._write_authoritative_sse_snapshot,
                self.server._http.event_journal,
                blackboard_dir,
                scope_id,
            )
            self.assertTrue(write_entered.wait(1))
            refresh_future = pool.submit(
                refresh_handler._refresh_event_sources,
                blackboard_dir,
            )
            refresh_future.result(timeout=1)
            write_release.set()
            snapshot_future.result(timeout=3)

    def test_snapshot_rejects_reported_scope_mismatch(self):
        snapshot_handler = object.__new__(web_gui._WebGuiRequestHandler)
        snapshot_handler.server = self.server._http
        requested_scope = "scope-requested-snapshot"
        actual_scope = "scope-actual-snapshot"
        first = {
            "timeline_seq": 1,
            "operation_id": "scope-race-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-scope-race-alpha",
            "kind": "movement_observed",
            "game_frame": 400,
            "summary": "movement observed",
            "technical": {},
        }
        snapshot_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": actual_scope,
                "operation_events": [first],
            }
        )
        snapshot_handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        snapshot_handler._write_sse_event = written.append

        snapshot_handler._write_authoritative_sse_snapshot(
            self.server._http.event_journal,
            "/tmp/scope-changing-snapshot",
            requested_scope,
        )

        rejected = written[0]["payload"]["micromachine_status"]
        self.assertEqual("scope_identity_mismatch", rejected["status"])
        self.assertEqual(requested_scope, rejected["blackboard_scope_id"])
        self.assertEqual(
            actual_scope,
            rejected["reported_blackboard_scope_id"],
        )
        self.assertEqual(
            (False, 0),
            self.server._http.operation_event_source_cursor(
                requested_scope
            ),
        )
        self.assertNotIn(
            actual_scope,
            self.server._http._observed_operation_event_high_water,
        )
        self.assertEqual(
            [],
            [
                event
                for event in self.server._http.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
            ],
        )

    def test_cold_snapshot_ignores_failed_second_source_read(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        scope_id = "scope-cold-second-read-failure"
        blackboard_dir = "/tmp/cold-second-read-failure"
        first = {
            "timeline_seq": 1,
            "session_epoch": "epoch-cold-read",
            "operation_id": "cold-read-operation",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "cold-read-update",
            "kind": "movement_observed",
            "game_frame": 400,
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 401,
        }
        statuses = iter(
            (
                {
                    "enabled": True,
                    "status": "live",
                    "operation_registry_authoritative": True,
                    "blackboard_scope_id": scope_id,
                    "operation_events": [first],
                },
                {
                    "enabled": False,
                    "status": "source_error",
                    "operation_registry_authoritative": True,
                    "blackboard_scope_id": scope_id,
                    "error": "second read failed",
                    "operation_events": [first, second],
                },
            )
        )
        handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: next(statuses)
        )
        handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        handler._write_sse_event = written.append

        handler._write_authoritative_sse_snapshot(
            server.event_journal,
            blackboard_dir,
            scope_id,
        )

        status = written[0]["payload"]["micromachine_status"]
        self.assertEqual("live", status["status"])
        self.assertNotIn("second read failed", json.dumps(status))
        cached = server._observed_payload_snapshots[
            f"micromachine:{scope_id}"
        ]
        self.assertEqual("live", cached["status"])
        self.assertEqual(
            [],
            [
                event
                for event in server.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
            ],
        )

    def test_rejected_warm_snapshot_does_not_replay_pending_lifecycle(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        requested_scope = "scope-warm-snapshot-rejected"
        actual_scope = "scope-warm-snapshot-foreign"
        blackboard_dir = "/tmp/warm-snapshot-rejected"
        self.assertTrue(server.admit_operation_event_scope(requested_scope))
        server._materialized_operation_event_scopes.add(requested_scope)
        server._observed_operation_event_seq[requested_scope] = 0
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": requested_scope,
                "operation_events": [
                    {
                        "timeline_seq": 1,
                        "session_epoch": "epoch-warm",
                        "operation_id": "warm-operation",
                        "generation": 1,
                        "kind": "movement_observed",
                    }
                ],
            },
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        self.assertIn(
            requested_scope,
            server._pending_operation_events,
        )

        handler._micromachine_status_payload = lambda _directory, **_kwargs: {
            "status": "live",
            "operation_registry_authoritative": True,
            "blackboard_scope_id": actual_scope,
            "operation_events": [],
        }
        handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        handler._write_sse_event = written.append

        cursor = handler._write_authoritative_sse_snapshot(
            server.event_journal,
            blackboard_dir,
            requested_scope,
        )

        self.assertEqual(server.event_journal.latest_seq, cursor)
        self.assertEqual(1, len(written))
        rejected = written[0]["payload"]["micromachine_status"]
        self.assertEqual("scope_identity_mismatch", rejected["status"])
        self.assertIn(
            requested_scope,
            server._pending_operation_events,
            "a later accepted snapshot must still be able to replay it",
        )

    def test_stale_authoritative_snapshot_does_not_admit_pending_replay(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        scope_id = "scope-stale-authoritative-snapshot"
        blackboard_dir = "/tmp/stale-authoritative-snapshot"
        first = {
            "timeline_seq": 1,
            "session_epoch": "1700000000000",
            "operation_id": "stale-authoritative-operation",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "stale-authoritative-update",
            "kind": "assigned",
            "game_frame": 700,
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "movement_observed",
            "game_frame": 701,
        }
        current_status = {
            "status": "live",
            "operation_registry_authoritative": True,
            "blackboard_scope_id": scope_id,
            "battlefield_overview": {
                "identity": {
                    "session_epoch": "1700000000000",
                    "generation": 2,
                    "game_frame": 800,
                }
            },
            "operation_events": [first, second],
        }
        stale_status = {
            **current_status,
            "battlefield_overview": {
                "identity": {
                    "session_epoch": "1600000000000",
                    "generation": 1,
                    "game_frame": 750,
                }
            },
        }
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": scope_id,
                "operation_events": [first],
            },
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        handler._publish_new_operation_events(
            current_status,
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        accepted, _ = server.authoritative_snapshot_payload(
            f"micromachine:{scope_id}",
            current_status,
        )
        self.assertTrue(accepted)
        handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: stale_status
        )
        handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        handler._write_sse_event = written.append

        cursor = handler._write_authoritative_sse_snapshot(
            server.event_journal,
            blackboard_dir,
            scope_id,
        )

        self.assertFalse(handler._last_authoritative_snapshot_admitted)
        self.assertEqual(server.event_journal.latest_seq, cursor)
        self.assertEqual(["snapshot"], [event["event_type"] for event in written])
        self.assertEqual(
            "1700000000000",
            written[0]["payload"]["micromachine_status"][
                "battlefield_overview"
            ]["identity"]["session_epoch"],
        )
        self.assertIn(scope_id, server._pending_operation_events)

    def test_concurrent_cold_scope_reservation_is_atomic_at_capacity(self):
        server = self.server._http
        history_retention = (
            server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION
        )
        for index in range(history_retention - 1):
            self.assertTrue(
                server.admit_operation_event_scope(
                    f"reserved-scope-{index}"
                )
            )
        self.assertEqual(
            (False, 0),
            server.operation_event_source_cursor("reserved-scope-0"),
        )

        candidates = (
            ("race-scope-alpha", "/tmp/race-scope-alpha"),
            ("race-scope-beta", "/tmp/race-scope-beta"),
        )
        start = threading.Barrier(3)
        status_reads = []
        status_reads_lock = threading.Lock()

        def run_snapshot(scope_id, blackboard_dir):
            handler = object.__new__(web_gui._WebGuiRequestHandler)
            handler.server = server
            written = []

            def status(_directory, **_kwargs):
                with status_reads_lock:
                    status_reads.append(scope_id)
                return {
                    "status": "live",
                    "operation_registry_authoritative": True,
                    "blackboard_scope_id": scope_id,
                    "operation_events": [],
                }

            handler._micromachine_status_payload = status
            handler._authoritative_event_snapshot = (
                lambda _directory, **kwargs: {
                    "state": {"available": True},
                    "history": {"events": [], "latest": 0},
                    "micromachine_status": kwargs[
                        "micromachine_status"
                    ],
                }
            )
            handler._write_sse_event = written.append
            start.wait()
            handler._write_authoritative_sse_snapshot(
                server.event_journal,
                blackboard_dir,
                scope_id,
            )
            return written[0]["payload"]["micromachine_status"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(run_snapshot, scope_id, blackboard_dir)
                for scope_id, blackboard_dir in candidates
            ]
            start.wait()
            statuses = [
                future.result(timeout=3)
                for future in futures
            ]

        self.assertEqual(
            {"live", "scope_capacity_rejected"},
            {str(status.get("status", "")) for status in statuses},
        )
        self.assertEqual(
            history_retention,
            len(server._observed_operation_event_high_water),
        )
        admitted_candidates = {
            scope_id
            for scope_id, _ in candidates
            if scope_id in server._observed_operation_event_high_water
        }
        self.assertEqual(1, len(admitted_candidates))
        self.assertEqual(
            {scope_id for scope_id, _ in candidates},
            set(status_reads),
            "source identity is checked before permanent scope admission",
        )
        admitted_scope = next(iter(admitted_candidates))
        self.assertEqual(
            (True, 0),
            server.operation_event_source_cursor(admitted_scope),
        )
        rejected_scope = next(
            scope_id
            for scope_id, _ in candidates
            if scope_id != admitted_scope
        )
        self.assertNotIn(
            f"micromachine:{rejected_scope}",
            server._observed_payload_snapshots,
        )

        operation_events = [
            event
            for event in server.event_journal.events_after(0)
            if event["event_type"] == "operation_event"
        ]
        self.assertEqual([], operation_events)

    def test_capacity_rejected_refreshes_do_not_grow_source_error_caches(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        handler._state_payload = lambda: {"available": True}
        retention = server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION
        for index in range(retention - 1):
            self.assertTrue(
                server.admit_operation_event_scope(
                    f"refresh-reserved-scope-{index}"
                )
            )
        baseline_dir = "/tmp/refresh-admitted-baseline"
        baseline_scope = web_gui._micromachine_blackboard_scope_id(
            baseline_dir
        )
        self.assertTrue(server.admit_operation_event_scope(baseline_scope))
        handler._micromachine_status_payload = lambda _directory, **_kwargs: {
            "status": "live",
            "operation_registry_authoritative": True,
            "blackboard_scope_id": baseline_scope,
            "operation_events": [],
        }
        handler._refresh_event_sources(baseline_dir)

        failed_before = set(server._failed_event_sources)
        hashes_before = dict(server._observed_payload_hashes)
        snapshots_before = deepcopy(server._observed_payload_snapshots)
        status_reads = 0

        def capacity_checked_status(directory, **_kwargs):
            nonlocal status_reads
            status_reads += 1
            return {
                "status": "live",
                "operation_registry_authoritative": True,
                "blackboard_scope_id": (
                    web_gui._micromachine_blackboard_scope_id(directory)
                ),
                "operation_events": [],
            }

        handler._micromachine_status_payload = (
            capacity_checked_status
        )
        for index in range(64):
            handler._refresh_event_sources(
                f"/tmp/refresh-capacity-rejected-{index}"
            )

        self.assertEqual(64, status_reads)
        self.assertEqual(failed_before, server._failed_event_sources)
        self.assertEqual(hashes_before, server._observed_payload_hashes)
        self.assertEqual(
            snapshots_before,
            server._observed_payload_snapshots,
        )

    def test_unadmitted_source_errors_do_not_grow_cache_or_evict_replay(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        server.event_journal = web_gui._WebEventJournal()
        handler._state_payload = lambda: {"available": True}

        def failed_status(_directory, **_kwargs):
            raise RuntimeError("untrusted status source failed")

        handler._micromachine_status_payload = failed_status
        legitimate = server.publish_event(
            "command_received",
            {"status": "received", "command_text": "preserve replay"},
            update_id="legitimate-replay-update",
        )
        handler._refresh_event_sources("/tmp/unadmitted-error-warmup")
        failed_before = set(server._failed_event_sources)
        hashes_before = dict(server._observed_payload_hashes)
        snapshots_before = deepcopy(server._observed_payload_snapshots)
        journal_latest_before = server.event_journal.latest_seq

        for index in range(600):
            handler._refresh_event_sources(
                f"/tmp/unadmitted-source-error-{index}"
            )

        self.assertEqual(
            {},
            server._observed_operation_event_high_water,
        )
        self.assertEqual(failed_before, server._failed_event_sources)
        self.assertEqual(hashes_before, server._observed_payload_hashes)
        self.assertEqual(
            snapshots_before,
            server._observed_payload_snapshots,
        )
        self.assertEqual(
            journal_latest_before,
            server.event_journal.latest_seq,
        )
        replay_available, replay_events = server.event_journal.replay_batch(
            int(legitimate["event_seq"]) - 1
        )
        self.assertTrue(replay_available)
        self.assertEqual(
            "command_received",
            replay_events[0]["event_type"],
        )

    def test_status_endpoint_does_not_consume_replay_scope_capacity(self):
        server = self.server._http
        for index in range(
            server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION
        ):
            self.assertTrue(
                server.admit_operation_event_scope(
                    f"status-reserved-scope-{index}"
                )
            )

        novel_dir = "/tmp/status-capacity-read-only"
        novel_scope = web_gui._micromachine_blackboard_scope_id(novel_dir)
        document = self.get_json(
            "/api/micromachine/status?"
            f"blackboard_dir={novel_dir}"
        )

        self.assertTrue(document["enabled"])
        self.assertNotEqual("scope_capacity_rejected", document["status"])
        self.assertNotIn(
            novel_scope,
            server._observed_operation_event_high_water,
        )
        self.assertEqual(
            server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION,
            len(server._observed_operation_event_high_water),
        )

    def test_many_read_only_probes_cannot_deny_attached_sse_scope(self):
        server = self.server._http
        timeline = self.bridge._micromachine_operation_timeline
        legitimate_directory = tempfile.TemporaryDirectory()
        self.addCleanup(legitimate_directory.cleanup)
        legitimate_dir = legitimate_directory.name
        self.attach_fake_micromachine_runtime(legitimate_dir)
        legitimate_scope = web_gui._micromachine_blackboard_scope_id(
            legitimate_dir
        )
        scope_history_before = dict(
            timeline._scope_epoch_history
        )

        for index in range(
            server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION + 8
        ):
            document = self.get_json(
                "/api/micromachine/status?"
                f"blackboard_dir=/tmp/untrusted-status-probe-{index}"
            )
            self.assertNotEqual(
                "scope_capacity_rejected",
                document["status"],
            )

        for index in range(
            server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION + 8
        ):
            stream = self.get_sse(
                "/api/events?"
                f"blackboard_dir=/tmp/untrusted-sse-probe-{index}"
                "&once=1"
            )
            events = self.parse_sse_events(stream)
            detached_status = events[0]["data"]["payload"][
                "micromachine_status"
            ]
            self.assertNotEqual(
                "scope_capacity_rejected",
                detached_status["status"],
            )
            self.assertFalse(
                detached_status["operation_registry_authoritative"]
            )

        self.assertEqual(
            {},
            server._observed_operation_event_high_water,
            "read-only status/SSE probes must not reserve replay tombstones",
        )
        self.assertEqual(
            scope_history_before,
            timeline._scope_epoch_history,
            "read-only status/SSE probes must not reserve reducer scope history",
        )

        stream = self.get_sse(
            "/api/events?"
            f"blackboard_dir={quote(legitimate_dir)}&once=1"
        )
        events = self.parse_sse_events(stream)

        self.assertEqual("snapshot", events[0]["event"])
        self.assertNotEqual(
            "scope_capacity_rejected",
            events[0]["data"]["payload"][
                "micromachine_status"
            ]["status"],
        )
        self.assertTrue(
            events[0]["data"]["payload"]["micromachine_status"][
                "operation_registry_authoritative"
            ]
        )
        self.assertIn(
            legitimate_scope,
            server._observed_operation_event_high_water,
        )
        self.assertIn(
            legitimate_scope,
            timeline._scope_epoch_history,
            "a later authoritative source must still admit the legitimate scope",
        )

    def test_status_endpoint_rejects_reported_scope_mismatch(self):
        original = self.bridge.micromachine_status_detached

        def mismatched_status(*, blackboard_dir=""):
            return {
                "enabled": True,
                "status": "live",
                "blackboard_dir": blackboard_dir,
                "blackboard_scope_id": "foreign-status-scope",
            }

        self.bridge.micromachine_status_detached = mismatched_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status_detached",
            original,
        )

        blackboard_dir = "/tmp/status-scope-mismatch"
        document = self.get_json(
            "/api/micromachine/status?blackboard_dir="
            + blackboard_dir
        )

        self.assertFalse(document["enabled"])
        self.assertEqual("scope_identity_mismatch", document["status"])
        self.assertEqual(
            web_gui._micromachine_blackboard_scope_id(blackboard_dir),
            document["blackboard_scope_id"],
        )
        self.assertEqual(
            "foreign-status-scope",
            document["reported_blackboard_scope_id"],
        )

    def test_regressive_status_cannot_advance_operation_source_cursor(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        blackboard_dir = "/tmp/regressive-source-cursor"
        scope_id = web_gui._micromachine_blackboard_scope_id(
            blackboard_dir
        )
        first = {
            "timeline_seq": 1,
            "operation_id": "regressive-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-regressive-alpha",
            "kind": "movement_observed",
            "game_frame": 500,
            "summary": "movement observed",
            "technical": {},
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 501,
            "summary": "engagement observed",
        }
        third = {
            **first,
            "timeline_seq": 3,
            "kind": "target_reached",
            "game_frame": 502,
            "summary": "target reached",
        }

        def status(generation, frame, events):
            return {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": scope_id,
                "battlefield_overview": {
                    "identity": {
                        "session_epoch": "1700000000000",
                        "generation": generation,
                        "game_frame": frame,
                    }
                },
                "operation_events": events,
            }

        baseline = status(1, 100, [first])
        current = status(2, 200, [first, second])
        stale = status(1, 150, [first])
        advanced = status(3, 300, [first, second, third])
        handler._publish_new_operation_events(
            baseline,
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        handler._state_payload = lambda: {"available": True}
        statuses = iter((current, stale, advanced))
        handler._micromachine_status_payload = lambda _directory, **_kwargs: next(
            statuses
        )

        handler._refresh_event_sources(blackboard_dir)
        self.assertEqual(
            2,
            server._observed_operation_event_seq[scope_id],
        )
        self.assertEqual(
            [2],
            [
                event["payload"]["timeline_seq"]
                for event in server.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
            ],
        )

        handler._refresh_event_sources(blackboard_dir)
        self.assertEqual(
            [2],
            [
                event["payload"]["timeline_seq"]
                for event in server.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
            ],
        )

        handler._refresh_event_sources(blackboard_dir)
        self.assertEqual(
            [2, 3],
            [
                event["payload"]["timeline_seq"]
                for event in server.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
            ],
        )

    def test_regressive_authoritative_snapshot_uses_last_accepted_status(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        scope_id = "scope-regressive-authoritative-snapshot"
        blackboard_dir = "/tmp/regressive-authoritative-snapshot"

        def status(generation, frame):
            return {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": scope_id,
                "battlefield_overview": {
                    "identity": {
                        "session_epoch": "1700000000000",
                        "generation": generation,
                        "game_frame": frame,
                    }
                },
                "operation_events": [],
            }

        accepted = status(2, 200)
        stale = status(1, 100)
        self.assertTrue(
            server.publish_changed_snapshot(
                f"micromachine:{scope_id}",
                "micromachine_status",
                accepted,
                blackboard_dir=blackboard_dir,
            )
        )
        handler._publish_new_operation_events(
            accepted,
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: deepcopy(stale)
        )
        handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        handler._write_sse_event = written.append

        handler._write_authoritative_sse_snapshot(
            server.event_journal,
            blackboard_dir,
            scope_id,
        )

        identity = written[0]["payload"]["micromachine_status"][
            "battlefield_overview"
        ]["identity"]
        self.assertEqual(2, identity["generation"])
        self.assertEqual(200, identity["game_frame"])
        self.assertEqual(
            ("1700000000000", 2, 200),
            server._observed_payload_identities[
                f"micromachine:{scope_id}"
            ],
        )

    def test_authoritative_identity_survives_active_scope_churn(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        scope_id = "scope-history-backed-authoritative-status"
        blackboard_dir = "/tmp/history-backed-authoritative-status"

        def status(scope, generation, frame):
            return {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": scope,
                "battlefield_overview": {
                    "identity": {
                        "session_epoch": "1700000000000",
                        "generation": generation,
                        "game_frame": frame,
                    }
                },
                "operation_events": [],
            }

        accepted = status(scope_id, 2, 200)
        stale = status(scope_id, 1, 100)
        self.assertTrue(
            server.publish_changed_snapshot(
                f"micromachine:{scope_id}",
                "micromachine_status",
                accepted,
                blackboard_dir=blackboard_dir,
            )
        )
        handler._publish_new_operation_events(
            accepted,
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        for index in range(
            server._OPERATION_EVENT_SCOPE_RETENTION + 2
        ):
            churn_scope = f"scope-authoritative-churn-{index}"
            churn_status = status(churn_scope, 1, index)
            server.publish_changed_snapshot(
                f"micromachine:{churn_scope}",
                "micromachine_status",
                churn_status,
                blackboard_dir=f"/tmp/{churn_scope}",
            )
            handler._publish_new_operation_events(
                churn_status,
                blackboard_dir=f"/tmp/{churn_scope}",
                publish=True,
            )
        self.assertNotIn(
            scope_id,
            server._observed_operation_event_seq,
        )
        self.assertIn(
            f"micromachine:{scope_id}",
            server._observed_payload_snapshots,
        )

        handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: deepcopy(stale)
        )
        handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        handler._write_sse_event = written.append
        handler._write_authoritative_sse_snapshot(
            server.event_journal,
            blackboard_dir,
            scope_id,
        )

        identity = written[0]["payload"]["micromachine_status"][
            "battlefield_overview"
        ]["identity"]
        self.assertEqual(2, identity["generation"])
        self.assertEqual(200, identity["game_frame"])

    def test_same_scope_snapshot_waits_for_refresh_commit_boundary(self):
        refresh_handler = object.__new__(web_gui._WebGuiRequestHandler)
        refresh_handler.server = self.server._http
        snapshot_handler = object.__new__(web_gui._WebGuiRequestHandler)
        snapshot_handler.server = self.server._http
        server = self.server._http
        blackboard_dir = "/tmp/serialized-source-commit"
        scope_id = web_gui._micromachine_blackboard_scope_id(
            blackboard_dir
        )
        first = {
            "timeline_seq": 1,
            "operation_id": "serialized-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-serialized-alpha",
            "kind": "movement_observed",
            "game_frame": 600,
            "summary": "movement observed",
            "technical": {},
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 601,
            "summary": "engagement observed",
        }

        def status(generation, frame, events):
            return {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": scope_id,
                "battlefield_overview": {
                    "identity": {
                        "session_epoch": "1700000000000",
                        "generation": generation,
                        "game_frame": frame,
                    }
                },
                "operation_events": events,
            }

        baseline = status(2, 200, [first])
        stale = status(1, 100, [first])
        advanced = status(3, 300, [first, second])
        self.assertTrue(
            server.publish_changed_snapshot(
                f"micromachine:{scope_id}",
                "micromachine_status",
                baseline,
                blackboard_dir=blackboard_dir,
            )
        )
        refresh_handler._publish_new_operation_events(
            baseline,
            blackboard_dir=blackboard_dir,
            publish=True,
        )

        publish_entered = threading.Event()
        publish_release = threading.Event()
        snapshot_source_entered = threading.Event()
        original_publish = server.publish_changed_snapshot

        def blocking_publish(cache_key, event_type, payload, **kwargs):
            if payload is stale:
                publish_entered.set()
                if not publish_release.wait(2):
                    raise TimeoutError("test did not release stale publish")
            return original_publish(
                cache_key,
                event_type,
                payload,
                **kwargs,
            )

        server.publish_changed_snapshot = blocking_publish
        self.addCleanup(
            setattr,
            server,
            "publish_changed_snapshot",
            original_publish,
        )
        refresh_handler._state_payload = lambda: {"available": True}
        refresh_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: stale
        )

        def snapshot_status(_directory, **_kwargs):
            snapshot_source_entered.set()
            return advanced

        snapshot_handler._micromachine_status_payload = snapshot_status
        snapshot_handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        snapshot_handler._write_sse_event = lambda _event: None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            refresh_future = pool.submit(
                refresh_handler._refresh_event_sources,
                blackboard_dir,
            )
            self.assertTrue(publish_entered.wait(1))
            snapshot_future = pool.submit(
                snapshot_handler._write_authoritative_sse_snapshot,
                server.event_journal,
                blackboard_dir,
                scope_id,
            )
            time.sleep(0.05)
            self.assertFalse(snapshot_source_entered.is_set())

            publish_release.set()
            refresh_future.result(timeout=3)
            snapshot_future.result(timeout=3)

        self.assertTrue(snapshot_source_entered.is_set())
        self.assertEqual(
            [2],
            [
                event["payload"]["timeline_seq"]
                for event in server.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
            ],
        )

    def test_blocked_scope_source_does_not_block_other_scope_snapshot(self):
        blocked_handler = object.__new__(web_gui._WebGuiRequestHandler)
        blocked_handler.server = self.server._http
        free_handler = object.__new__(web_gui._WebGuiRequestHandler)
        free_handler.server = self.server._http
        blocked_dir = "/tmp/blocked-operation-source"
        free_dir = "/tmp/free-operation-source"
        blocked_scope = "scope-blocked-operation-source"
        free_scope = "scope-free-operation-source"
        blocked_entered = threading.Event()
        blocked_release = threading.Event()

        def blocked_status(_directory, **_kwargs):
            blocked_entered.set()
            if not blocked_release.wait(2):
                raise TimeoutError("test did not release blocked source")
            return {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": blocked_scope,
                "operation_events": [],
            }

        blocked_handler._micromachine_status_payload = blocked_status
        blocked_handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        blocked_handler._write_sse_event = lambda _event: None
        free_handler._micromachine_status_payload = (
            lambda _directory, **_kwargs: {
                "operation_registry_authoritative": True,
                "blackboard_scope_id": free_scope,
                "operation_events": [],
            }
        )
        free_handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        free_handler._write_sse_event = lambda _event: None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            blocked_future = pool.submit(
                blocked_handler._write_authoritative_sse_snapshot,
                self.server._http.event_journal,
                blocked_dir,
                blocked_scope,
            )
            self.assertTrue(blocked_entered.wait(1))
            free_future = pool.submit(
                free_handler._write_authoritative_sse_snapshot,
                self.server._http.event_journal,
                free_dir,
                free_scope,
            )
            free_future.result(timeout=1)
            blocked_release.set()
            blocked_future.result(timeout=3)

    def test_sse_snapshot_cut_does_not_block_publication_and_replays_newer_event(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.attach_fake_micromachine_runtime(directory.name)
        original = self.bridge.micromachine_status_for_runtime
        self.server._http.publish_event(
            "state",
            {"available": True, "marker": "snapshot-race-baseline"},
        )
        status_entered = threading.Event()
        status_release = threading.Event()
        publisher_started = threading.Event()

        def blocking_status(
            *,
            blackboard_dir="",
            runtime_instance_id,
            telemetry_document,
        ):
            status_entered.set()
            if not status_release.wait(2):
                raise TimeoutError("test did not release snapshot status")
            return original(
                blackboard_dir=blackboard_dir,
                runtime_instance_id=runtime_instance_id,
                telemetry_document=telemetry_document,
            )

        self.bridge.micromachine_status_for_runtime = blocking_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status_for_runtime",
            original,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            response_future = pool.submit(
                self.get_sse,
                "/api/events?"
                f"blackboard_dir={quote(directory.name)}&once=1",
            )
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

    def test_sse_snapshot_advances_cut_when_events_roll_past_retention(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.attach_fake_micromachine_runtime(directory.name)
        original = self.bridge.micromachine_status_for_runtime
        self.server._http.event_journal = web_gui._WebEventJournal(
            retention=2
        )
        baseline = self.server._http.publish_event(
            "state",
            {"available": True, "marker": "rollover-baseline"},
        )
        status_entered = threading.Event()
        status_release = threading.Event()

        def blocking_status(
            *,
            blackboard_dir="",
            runtime_instance_id,
            telemetry_document,
        ):
            status_entered.set()
            if not status_release.wait(2):
                raise TimeoutError("test did not release snapshot status")
            return original(
                blackboard_dir=blackboard_dir,
                runtime_instance_id=runtime_instance_id,
                telemetry_document=telemetry_document,
            )

        self.bridge.micromachine_status_for_runtime = blocking_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status_for_runtime",
            original,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            response_future = pool.submit(
                self.get_sse,
                "/api/events?"
                f"blackboard_dir={quote(directory.name)}&once=1",
            )
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
        self.assertEqual(1, len(snapshots))
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
        blackboard_dir = "/tmp/source-refresh-high-water"
        scope_id = web_gui._micromachine_blackboard_scope_id(
            blackboard_dir
        )
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

        def raced_status(_blackboard_dir, **_kwargs):
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
                blackboard_dir,
            )
            self.assertTrue(first_entered.wait(1))
            newer_future = pool.submit(
                handler._refresh_event_sources,
                blackboard_dir,
            )
            time.sleep(0.05)
            self.assertFalse(newer_future.done())
            release_first.set()
            older_future.result(timeout=3)
            newer_future.result(timeout=3)

        published = [
            event["payload"]
            for event in self.server._http.event_journal.events_after(0)
            if event["event_type"] == "micromachine_status"
            and event["payload"].get("blackboard_scope_id") == scope_id
        ]
        self.assertEqual(2, len(published))
        self.assertEqual(
            [1, 2],
            [
                payload["battlefield_overview"]["identity"][
                    "generation"
                ]
                for payload in published
            ],
        )
        self.assertEqual(
            2,
            published[-1]["battlefield_overview"]["identity"][
                "generation"
            ],
        )
        self.assertEqual(
            ("1700000000000", 2, 200),
            self.server._http._observed_payload_identities[
                f"micromachine:{scope_id}"
            ],
        )

    def test_operation_event_active_cursor_is_lru_bounded_with_history_cache(self):
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
        self.assertIn(
            f"micromachine:{evicted_scope}",
            http_server._observed_payload_hashes,
        )
        self.assertIn(
            f"source:micromachine_status:{evicted_scope}",
            http_server._observed_payload_hashes,
        )
        self.assertIn(
            f"micromachine_status:{evicted_scope}",
            http_server._failed_event_sources,
        )
        self.assertEqual(
            1,
            http_server._observed_operation_event_high_water[
                evicted_scope
            ],
        )

        operation_events_before_revisit = [
            event
            for event in http_server.event_journal.events_after(0)
            if event["event_type"] == "operation_event"
            and event["blackboard_scope_id"] == evicted_scope
        ]
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": evicted_scope,
                "operation_events": [
                    {
                        "timeline_seq": 1,
                        "operation_id": "operation-0",
                        "generation": 1,
                        "kind": "assigned",
                    }
                ],
            },
            blackboard_dir=f"/tmp/{evicted_scope}",
            publish=True,
        )
        operation_events_after_replay = [
            event
            for event in http_server.event_journal.events_after(0)
            if event["event_type"] == "operation_event"
            and event["blackboard_scope_id"] == evicted_scope
        ]
        self.assertEqual(
            operation_events_before_revisit,
            operation_events_after_replay,
        )

        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": evicted_scope,
                "operation_events": [
                    {
                        "timeline_seq": retention + 100,
                        "operation_id": "operation-0",
                        "generation": 1,
                        "kind": "movement_observed",
                    }
                ],
            },
            blackboard_dir=f"/tmp/{evicted_scope}",
            publish=True,
        )
        operation_events_after_advance = [
            event
            for event in http_server.event_journal.events_after(0)
            if event["event_type"] == "operation_event"
            and event["blackboard_scope_id"] == evicted_scope
        ]
        self.assertEqual(
            len(operation_events_before_revisit) + 1,
            len(operation_events_after_advance),
        )
        self.assertEqual(
            "movement_observed",
            operation_events_after_advance[-1]["payload"]["kind"],
        )

        for index in range(70):
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": f"history-scope-{index}",
                    "operation_events": [
                        {
                            "timeline_seq": index + 1,
                            "operation_id": f"history-operation-{index}",
                            "generation": 1,
                            "kind": "assigned",
                        }
                    ],
                },
                blackboard_dir=f"/tmp/history-scope-{index}",
                publish=True,
            )
        self.assertNotIn(
            evicted_scope,
            http_server._observed_operation_event_seq,
        )
        self.assertEqual(
            retention + 100,
            http_server._observed_operation_event_high_water[
                evicted_scope
            ],
        )

        operation_events_before_retired_revisit = [
            event
            for event in http_server.event_journal.events_after(0)
            if event["event_type"] == "operation_event"
            and event["blackboard_scope_id"] == evicted_scope
        ]
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": evicted_scope,
                "operation_events": [
                    {
                        "timeline_seq": 1,
                        "operation_id": "operation-0",
                        "generation": 1,
                        "kind": "assigned",
                    },
                    {
                        "timeline_seq": retention + 100,
                        "operation_id": "operation-0",
                        "generation": 1,
                        "kind": "movement_observed",
                    },
                ],
            },
            blackboard_dir=f"/tmp/{evicted_scope}",
            publish=True,
        )
        self.assertEqual(
            operation_events_before_retired_revisit,
            [
                event
                for event in http_server.event_journal.events_after(0)
                if event["event_type"] == "operation_event"
                and event["blackboard_scope_id"] == evicted_scope
            ],
        )

        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": evicted_scope,
                "operation_events": [
                    {
                        "timeline_seq": retention + 101,
                        "operation_id": "operation-0",
                        "generation": 1,
                        "kind": "engagement_observed",
                    }
                ],
            },
            blackboard_dir=f"/tmp/{evicted_scope}",
            publish=True,
        )
        operation_events_after_retired_advance = [
            event
            for event in http_server.event_journal.events_after(0)
            if event["event_type"] == "operation_event"
            and event["blackboard_scope_id"] == evicted_scope
        ]
        self.assertEqual(
            len(operation_events_before_retired_revisit) + 1,
            len(operation_events_after_retired_advance),
        )
        self.assertEqual(
            "engagement_observed",
            operation_events_after_retired_advance[-1]["payload"]["kind"],
        )

    def test_pending_operation_event_survives_history_scope_churn(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        server = self.server._http
        scope_id = "scope-pending-history-churn"
        first = {
            "timeline_seq": 1,
            "operation_id": "pending-history-alpha",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "update-pending-history-alpha",
            "kind": "movement_observed",
            "game_frame": 700,
            "summary": "movement observed",
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "engagement_observed",
            "game_frame": 701,
            "summary": "engagement observed",
        }
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": scope_id,
                "operation_events": [first],
            },
            blackboard_dir=f"/tmp/{scope_id}",
            publish=True,
        )
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": scope_id,
                "operation_events": [first, second],
            },
            blackboard_dir=f"/tmp/{scope_id}",
            publish=True,
        )
        self.assertIn(scope_id, server._pending_operation_events)

        for index in range(
            server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION + 5
        ):
            churn_scope = f"scope-pending-history-churn-{index}"
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": churn_scope,
                    "operation_events": [
                        {
                            "timeline_seq": 1,
                            "operation_id": f"operation-{index}",
                            "generation": 1,
                            "kind": "assigned",
                        }
                    ],
                },
                blackboard_dir=f"/tmp/{churn_scope}",
                publish=True,
            )

        self.assertIn(scope_id, server._pending_operation_events)
        self.assertIn(
            scope_id,
            server._observed_operation_event_high_water,
        )
        self.assertLessEqual(
            len(server._observed_operation_event_high_water),
            server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION,
        )

    def test_operation_event_scope_capacity_rejects_novel_scope_atomically(
        self,
    ):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        http_server = self.server._http
        active_retention = http_server._OPERATION_EVENT_SCOPE_RETENTION
        history_retention = (
            http_server._OPERATION_EVENT_SCOPE_HISTORY_RETENTION
        )

        for index in range(history_retention):
            scope_id = f"bounded-history-scope-{index}"
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": scope_id,
                    "operation_events": [
                        {
                            "timeline_seq": 1,
                            "operation_id": f"bounded-operation-{index}",
                            "generation": 1,
                            "kind": "assigned",
                        }
                    ],
                },
                blackboard_dir=f"/tmp/{scope_id}",
                publish=True,
            )

        self.assertEqual(
            active_retention,
            len(http_server._observed_operation_event_seq),
        )
        self.assertEqual(
            active_retention,
            len(http_server._observed_operation_scope_order),
        )
        self.assertEqual(
            history_retention,
            len(http_server._observed_operation_event_high_water),
        )

        novel_scope = "bounded-history-scope-overflow"
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": novel_scope,
                "operation_events": [
                    {
                        "timeline_seq": 1,
                        "operation_id": "overflow-operation",
                        "generation": 1,
                        "kind": "assigned",
                    }
                ],
            },
            blackboard_dir=f"/tmp/{novel_scope}",
            publish=True,
        )
        self.assertEqual(
            history_retention,
            len(http_server._observed_operation_event_high_water),
        )
        self.assertNotIn(
            novel_scope,
            http_server._observed_operation_event_high_water,
        )
        self.assertNotIn(
            novel_scope,
            http_server._observed_operation_event_seq,
        )
        self.assertNotIn(
            novel_scope,
            http_server._observed_operation_scope_order,
        )
        self.assertNotIn(
            f"micromachine:{novel_scope}",
            http_server._observed_payload_snapshots,
        )

        handler._micromachine_status_payload = lambda _directory, **_kwargs: {
            "operation_registry_authoritative": True,
            "blackboard_scope_id": novel_scope,
            "operation_events": [
                {
                    "timeline_seq": 2,
                    "operation_id": "overflow-operation",
                    "generation": 1,
                    "kind": "movement_observed",
                }
            ],
        }
        handler._authoritative_event_snapshot = (
            lambda _directory, **kwargs: {
                "state": {"available": True},
                "history": {"events": [], "latest": 0},
                "micromachine_status": kwargs["micromachine_status"],
            }
        )
        written = []
        handler._write_sse_event = written.append
        handler._write_authoritative_sse_snapshot(
            http_server.event_journal,
            f"/tmp/{novel_scope}",
            novel_scope,
        )

        rejected = written[0]["payload"]["micromachine_status"]
        self.assertEqual(
            "scope_capacity_rejected",
            rejected["status"],
        )
        self.assertNotIn(
            f"micromachine:{novel_scope}",
            http_server._observed_payload_snapshots,
        )
        self.assertNotIn(
            novel_scope,
            http_server._pending_operation_events,
        )

    def test_sse_snapshot_survives_micromachine_source_failure(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.attach_fake_micromachine_runtime(directory.name)
        original = self.bridge.micromachine_status_for_runtime

        def unavailable_status(**_kwargs):
            raise OSError("blackboard source unavailable")

        self.bridge.micromachine_status_for_runtime = unavailable_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status_for_runtime",
            original,
        )

        stream = self.get_sse(
            "/api/events?"
            f"blackboard_dir={quote(directory.name)}&once=1"
        )
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

    def test_rejected_snapshot_does_not_emit_concurrent_lifecycle_event(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.attach_fake_micromachine_runtime(directory.name)
        original = self.bridge.micromachine_status_for_runtime
        requested_scope = web_gui._micromachine_blackboard_scope_id(
            directory.name
        )
        status_entered = threading.Event()
        status_release = threading.Event()

        def foreign_status(**_kwargs):
            status_entered.set()
            if not status_release.wait(2):
                raise TimeoutError("test did not release rejected status")
            return {
                "enabled": True,
                "status": "live",
                "operation_registry_authoritative": True,
                "blackboard_scope_id": "scope-foreign-rejected",
                "operation_events": [],
            }

        self.bridge.micromachine_status_for_runtime = foreign_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status_for_runtime",
            original,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            response_future = pool.submit(
                self.get_sse,
                "/api/events?"
                f"blackboard_dir={quote(directory.name)}&once=1",
            )
            self.assertTrue(status_entered.wait(1))
            self.server._http.publish_event(
                "operation_event",
                {
                    "timeline_seq": 1,
                    "session_epoch": "epoch-rejected",
                    "operation_id": "rejected-operation",
                    "generation": 1,
                    "requested_generation": 1,
                    "kind": "movement_observed",
                    "blackboard_scope_id": requested_scope,
                },
                operation_id="rejected-operation",
                generation=1,
                blackboard_scope_id=requested_scope,
            )
            status_release.set()
            stream = response_future.result(timeout=3)

        events = self.parse_sse_events(stream)
        self.assertEqual(["snapshot"], [event["event"] for event in events])
        self.assertEqual(
            "scope_identity_mismatch",
            events[0]["data"]["payload"][
                "micromachine_status"
            ]["status"],
        )

    def test_rejected_snapshot_reconnect_cannot_replay_lifecycle(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        blackboard_dir = directory.name
        self.attach_fake_micromachine_runtime(blackboard_dir)
        original = self.bridge.micromachine_status_for_runtime
        requested_scope = web_gui._micromachine_blackboard_scope_id(
            blackboard_dir
        )
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        first = {
            "timeline_seq": 1,
            "session_epoch": "epoch-rejected-reconnect",
            "operation_id": "rejected-reconnect-operation",
            "generation": 1,
            "requested_generation": 1,
            "update_id": "rejected-reconnect-update",
            "kind": "assigned",
            "game_frame": 600,
        }
        second = {
            **first,
            "timeline_seq": 2,
            "kind": "movement_observed",
            "game_frame": 601,
        }
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": requested_scope,
                "operation_events": [first],
            },
            blackboard_dir=blackboard_dir,
            publish=True,
        )
        handler._publish_new_operation_events(
            {
                "blackboard_scope_id": requested_scope,
                "operation_events": [first, second],
            },
            blackboard_dir=blackboard_dir,
            publish=True,
        )

        def foreign_status(**_kwargs):
            return {
                "enabled": True,
                "status": "live",
                "operation_registry_authoritative": True,
                "blackboard_scope_id": "scope-rejected-reconnect-foreign",
                "operation_events": [],
            }

        self.bridge.micromachine_status_for_runtime = foreign_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status_for_runtime",
            original,
        )

        event_path = (
            "/api/events?"
            f"blackboard_dir={quote(blackboard_dir)}&once=1"
        )
        initial = self.parse_sse_events(self.get_sse(event_path))
        rejected_cursor = initial[0]["data"]["event_seq"]
        self.assertEqual(
            "scope_identity_mismatch",
            initial[0]["data"]["payload"][
                "micromachine_status"
            ]["status"],
        )

        rejected_reconnect = self.parse_sse_events(
            self.get_sse(
                event_path,
                headers={"Last-Event-ID": str(rejected_cursor)}
            )
        )
        self.assertEqual(
            ["snapshot"],
            [event["event"] for event in rejected_reconnect],
        )
        self.assertEqual(
            "scope_identity_mismatch",
            rejected_reconnect[0]["data"]["payload"][
                "micromachine_status"
            ]["status"],
        )

        def admitted_status(*, blackboard_dir="", **_kwargs):
            return {
                "enabled": True,
                "status": "live",
                "operation_registry_authoritative": True,
                "blackboard_dir": blackboard_dir,
                "blackboard_scope_id": requested_scope,
                "battlefield_overview": {
                    "identity": {
                        "session_epoch": "1700000000000",
                        "generation": 1,
                        "game_frame": 601,
                    }
                },
                "operation_events": [first, second],
            }

        self.bridge.micromachine_status_for_runtime = admitted_status
        admitted_reconnect = self.parse_sse_events(
            self.get_sse(
                event_path,
                headers={"Last-Event-ID": str(rejected_cursor)}
            )
        )

        self.assertEqual(
            ["snapshot", "operation_event"],
            [event["event"] for event in admitted_reconnect],
        )
        self.assertTrue(
            admitted_reconnect[1]["data"]["subscriber_local_replay"]
        )
        self.assertEqual(
            2,
            admitted_reconnect[1]["data"]["payload"]["timeline_seq"],
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

    def test_equal_cursor_reconnect_revalidates_detached_runtime_snapshot(
        self,
    ):
        with tempfile.TemporaryDirectory() as blackboard_dir:
            self.attach_fake_micromachine_runtime(blackboard_dir)
            self.server._http.publish_event(
                "state",
                {"available": True, "marker": "attached-before-detach"},
            )
            event_path = (
                "/api/events?"
                f"blackboard_dir={quote(blackboard_dir)}&once=1"
            )
            initial = self.parse_sse_events(self.get_sse(event_path))
            cursor = initial[0]["data"]["event_seq"]
            self.assertTrue(
                initial[0]["data"]["payload"]["micromachine_status"][
                    "operation_registry_authoritative"
                ]
            )

            self.detach_fake_micromachine_runtime(blackboard_dir)
            reconnect = self.parse_sse_events(
                self.get_sse(
                    event_path,
                    headers={"Last-Event-ID": str(cursor)},
                )
            )

        self.assertEqual(["snapshot"], [event["event"] for event in reconnect])
        detached = reconnect[0]["data"]["payload"]["micromachine_status"]
        self.assertFalse(detached["operation_registry_authoritative"])
        self.assertFalse(detached["runtime_attached"])
        self.assertFalse(detached["telemetry_current_for_process"])
        self.assertTrue(detached["telemetry_stale_or_detached"])

    def test_equal_cursor_reconnect_revalidates_detach_with_unrelated_replay(
        self,
    ):
        for replay_kind in ("foreign_operation", "state"):
            with (
                self.subTest(replay_kind=replay_kind),
                tempfile.TemporaryDirectory() as blackboard_dir,
            ):
                self.server._http.event_journal = web_gui._WebEventJournal()
                self.attach_fake_micromachine_runtime(blackboard_dir)
                self.server._http.publish_event(
                    "state",
                    {
                        "available": True,
                        "marker": f"baseline-{replay_kind}",
                    },
                )
                event_path = (
                    "/api/events?"
                    f"blackboard_dir={quote(blackboard_dir)}&once=1"
                )
                initial = self.parse_sse_events(self.get_sse(event_path))
                cursor = initial[0]["data"]["event_seq"]
                self.detach_fake_micromachine_runtime(blackboard_dir)

                if replay_kind == "foreign_operation":
                    self.server._http.publish_event(
                        "operation_event",
                        {
                            "timeline_seq": 1,
                            "operation_id": "foreign-operation",
                            "kind": "movement_observed",
                        },
                        operation_id="foreign-operation",
                        blackboard_scope_id="foreign-operation-scope",
                    )
                else:
                    self.server._http.publish_event(
                        "state",
                        {
                            "available": True,
                            "marker": "unrelated-state-after-detach",
                        },
                    )

                reconnect = self.parse_sse_events(
                    self.get_sse(
                        event_path,
                        headers={"Last-Event-ID": str(cursor)},
                    )
                )

                self.assertEqual(
                    ["snapshot"],
                    [event["event"] for event in reconnect],
                )
                detached = reconnect[0]["data"]["payload"][
                    "micromachine_status"
                ]
                self.assertFalse(
                    detached["operation_registry_authoritative"]
                )
                self.assertFalse(detached["runtime_attached"])
                self.assertTrue(detached["telemetry_stale_or_detached"])

    def test_open_sse_refresh_publishes_authority_loss_and_recovery(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        with tempfile.TemporaryDirectory() as blackboard_dir:
            self.attach_fake_micromachine_runtime(blackboard_dir)
            event_path = (
                "/api/events?"
                f"blackboard_dir={quote(blackboard_dir)}&once=1"
            )
            initial = self.parse_sse_events(self.get_sse(event_path))
            scope_id = web_gui._micromachine_blackboard_scope_id(
                blackboard_dir
            )
            self.assertTrue(
                initial[0]["data"]["payload"]["micromachine_status"][
                    "operation_registry_authoritative"
                ]
            )
            self.assertTrue(
                self.server._http.has_admitted_operation_event_scope(
                    scope_id
                )
            )

            self.detach_fake_micromachine_runtime(blackboard_dir)
            detach_cursor = self.server._http.event_journal.latest_seq
            handler._refresh_event_sources(blackboard_dir)
            detached_events = (
                self.server._http.event_journal.events_after(detach_cursor)
            )
            detached_statuses = [
                event["payload"]
                for event in detached_events
                if event["event_type"] == "micromachine_status"
            ]
            self.assertEqual(1, len(detached_statuses))
            self.assertFalse(
                detached_statuses[0][
                    "operation_registry_authoritative"
                ]
            )
            self.assertFalse(detached_statuses[0]["runtime_attached"])
            self.assertTrue(
                detached_statuses[0]["telemetry_stale_or_detached"]
            )
            self.assertFalse(
                any(
                    event["event_type"] == "operation_event"
                    for event in detached_events
                )
            )

            duplicate_cursor = self.server._http.event_journal.latest_seq
            handler._refresh_event_sources(blackboard_dir)
            duplicate_statuses = [
                event
                for event in self.server._http.event_journal.events_after(
                    duplicate_cursor
                )
                if event["event_type"] == "micromachine_status"
            ]
            self.assertEqual([], duplicate_statuses)

            self.attach_fake_micromachine_runtime(blackboard_dir)
            recovery_cursor = self.server._http.event_journal.latest_seq
            handler._refresh_event_sources(blackboard_dir)
            recovered_statuses = [
                event["payload"]
                for event in self.server._http.event_journal.events_after(
                    recovery_cursor
                )
                if event["event_type"] == "micromachine_status"
            ]
            self.assertEqual(1, len(recovered_statuses))
            self.assertTrue(
                recovered_statuses[0][
                    "operation_registry_authoritative"
                ]
            )

    def test_open_sse_refresh_publishes_source_error_status(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        with tempfile.TemporaryDirectory() as blackboard_dir:
            self.attach_fake_micromachine_runtime(blackboard_dir)
            self.get_sse(
                "/api/events?"
                f"blackboard_dir={quote(blackboard_dir)}&once=1"
            )
            scope_id = web_gui._micromachine_blackboard_scope_id(
                blackboard_dir
            )
            handler._micromachine_status_payload = (
                lambda _directory, **_kwargs: {
                    "enabled": False,
                    "status": "source_error",
                    "blackboard_dir": blackboard_dir,
                    "blackboard_scope_id": scope_id,
                    "operation_registry_authoritative": False,
                    "error": "status source failed",
                }
            )
            cursor = self.server._http.event_journal.latest_seq
            handler._refresh_event_sources(blackboard_dir)
            events = self.server._http.event_journal.events_after(cursor)

        status_events = [
            event
            for event in events
            if event["event_type"] == "micromachine_status"
        ]
        source_errors = [
            event
            for event in events
            if event["event_type"] == "source_error"
        ]
        self.assertEqual(1, len(status_events))
        self.assertFalse(
            status_events[0]["payload"][
                "operation_registry_authoritative"
            ]
        )
        self.assertEqual("source_error", status_events[0]["payload"]["status"])
        self.assertEqual(1, len(source_errors))

    def test_sse_reconnect_replays_pending_local_lifecycle_at_equal_cursor(
        self,
    ):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = self.server._http
        with tempfile.TemporaryDirectory() as directory:
            blackboard_dir = os.path.join(directory, "pending-reconnect")
            os.makedirs(blackboard_dir)
            self.attach_fake_micromachine_runtime(blackboard_dir)
            scope_id = web_gui._micromachine_blackboard_scope_id(
                blackboard_dir
            )
            first = {
                "timeline_seq": 1,
                "session_epoch": "epoch-pending-reconnect",
                "operation_id": "pending-reconnect-operation",
                "generation": 1,
                "requested_generation": 1,
                "update_id": "pending-reconnect-update",
                "kind": "movement_observed",
                "game_frame": 500,
            }
            second = {
                **first,
                "timeline_seq": 2,
                "kind": "engagement_observed",
                "game_frame": 501,
            }
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": scope_id,
                    "operation_events": [first],
                },
                blackboard_dir=blackboard_dir,
                publish=True,
            )
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": scope_id,
                    "operation_events": [first, second],
                },
                blackboard_dir=blackboard_dir,
                publish=True,
            )
            cursor = self.server._http.event_journal.latest_seq

            stream = self.get_sse(
                "/api/events"
                f"?blackboard_dir={quote(blackboard_dir)}"
                "&once=1",
                headers={"Last-Event-ID": str(cursor)},
            )

        events = self.parse_sse_events(stream)
        self.assertEqual(2, len(events))
        self.assertEqual("snapshot", events[0]["event"])
        self.assertEqual("operation_event", events[1]["event"])
        self.assertTrue(events[1]["data"]["subscriber_local_replay"])
        self.assertEqual(
            events[0]["data"]["event_seq"],
            events[1]["data"]["event_seq"],
        )
        self.assertEqual(
            2,
            events[1]["data"]["payload"]["timeline_seq"],
        )

    def test_sse_reconnect_rollover_forces_snapshot_before_pending_replay(self):
        server = self.server._http
        journal = web_gui._WebEventJournal(retention=2)
        server.event_journal = journal
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.server = server
        with tempfile.TemporaryDirectory() as directory:
            blackboard_dir = os.path.join(directory, "rollover-reconnect")
            os.makedirs(blackboard_dir)
            self.attach_fake_micromachine_runtime(blackboard_dir)
            scope_id = web_gui._micromachine_blackboard_scope_id(
                blackboard_dir
            )
            first = {
                "timeline_seq": 1,
                "session_epoch": "epoch-rollover-reconnect",
                "operation_id": "rollover-reconnect-operation",
                "generation": 1,
                "requested_generation": 1,
                "update_id": "rollover-reconnect-update",
                "kind": "assigned",
                "game_frame": 800,
            }
            second = {
                **first,
                "timeline_seq": 2,
                "kind": "movement_observed",
                "game_frame": 801,
            }
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": scope_id,
                    "operation_events": [first],
                },
                blackboard_dir=blackboard_dir,
                publish=True,
            )
            handler._publish_new_operation_events(
                {
                    "blackboard_scope_id": scope_id,
                    "operation_events": [first, second],
                },
                blackboard_dir=blackboard_dir,
                publish=True,
            )
            cursor = journal.latest_seq
            original_replay_batch = journal.replay_batch
            replay_calls = 0

            def rollover_before_replay(after):
                nonlocal replay_calls
                replay_calls += 1
                if replay_calls == 1:
                    for index in range(3):
                        journal.publish(
                            "state",
                            {"rollover": index},
                        )
                return original_replay_batch(after)

            journal.replay_batch = rollover_before_replay
            original_status = self.bridge.micromachine_status_for_runtime

            def admitted_status(*, blackboard_dir="", **_kwargs):
                return {
                    "enabled": True,
                    "status": "live",
                    "operation_registry_authoritative": True,
                    "blackboard_dir": blackboard_dir,
                    "blackboard_scope_id": scope_id,
                    "battlefield_overview": {
                        "identity": {
                            "session_epoch": "1700000000000",
                            "generation": 1,
                            "game_frame": 801,
                        }
                    },
                    "operation_events": [first, second],
                }

            self.bridge.micromachine_status_for_runtime = admitted_status
            try:
                stream = self.get_sse(
                    "/api/events"
                    f"?blackboard_dir={quote(blackboard_dir)}"
                    "&once=1",
                    headers={"Last-Event-ID": str(cursor)},
                )
            finally:
                self.bridge.micromachine_status_for_runtime = (
                    original_status
                )
                journal.replay_batch = original_replay_batch

        events = self.parse_sse_events(stream)
        self.assertEqual(
            ["snapshot", "operation_event"],
            [event["event"] for event in events],
        )
        self.assertGreater(events[0]["data"]["event_seq"], cursor)
        self.assertEqual(
            events[0]["data"]["event_seq"],
            events[1]["data"]["event_seq"],
        )
        self.assertTrue(events[1]["data"]["subscriber_local_replay"])
        self.assertEqual(2, events[1]["data"]["payload"]["timeline_seq"])
        self.assertGreaterEqual(replay_calls, 2)

    def test_partial_sse_operation_frame_reconnects_without_event_loss(self):
        server = self.server._http
        original_status = self.bridge.micromachine_status_for_runtime
        statuses = {}

        def admitted_status(*, blackboard_dir="", **_kwargs):
            return statuses[blackboard_dir]

        class StagedWriter:
            def __init__(self, *, fail_write_call=0, fail_flush=False):
                self.fail_write_call = fail_write_call
                self.fail_flush = fail_flush
                self.write_calls = 0
                self.parts = []

            def write(self, data):
                self.write_calls += 1
                if self.write_calls == self.fail_write_call:
                    raise BrokenPipeError("partial SSE frame failed")
                self.parts.append(data)

            def flush(self):
                if self.fail_flush:
                    raise BrokenPipeError("partial SSE flush failed")

        self.bridge.micromachine_status_for_runtime = admitted_status
        self.addCleanup(
            setattr,
            self.bridge,
            "micromachine_status_for_runtime",
            original_status,
        )

        for name, fail_write_call, fail_flush in (
            ("after_id", 2, False),
            ("after_event", 3, False),
            ("after_data", 0, True),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                blackboard_dir = os.path.join(directory, name)
                os.makedirs(blackboard_dir)
                self.attach_fake_micromachine_runtime(blackboard_dir)
                scope_id = web_gui._micromachine_blackboard_scope_id(
                    blackboard_dir
                )
                first = {
                    "timeline_seq": 1,
                    "session_epoch": f"epoch-partial-{name}",
                    "operation_id": f"partial-{name}-operation",
                    "generation": 1,
                    "requested_generation": 1,
                    "update_id": f"partial-{name}-update",
                    "kind": "assigned",
                    "game_frame": 900,
                }
                second = {
                    **first,
                    "timeline_seq": 2,
                    "kind": "movement_observed",
                    "game_frame": 901,
                }
                status = {
                    "enabled": True,
                    "status": "live",
                    "operation_registry_authoritative": True,
                    "blackboard_dir": blackboard_dir,
                    "blackboard_scope_id": scope_id,
                    "battlefield_overview": {
                        "identity": {
                            "session_epoch": "1700000000000",
                            "generation": 1,
                            "game_frame": 901,
                        }
                    },
                    "operation_events": [first, second],
                }
                statuses[blackboard_dir] = status
                handler = object.__new__(web_gui._WebGuiRequestHandler)
                handler.server = server
                handler._publish_new_operation_events(
                    {
                        "blackboard_scope_id": scope_id,
                        "operation_events": [first],
                    },
                    blackboard_dir=blackboard_dir,
                    publish=True,
                )
                handler._publish_new_operation_events(
                    status,
                    blackboard_dir=blackboard_dir,
                    publish=True,
                )
                cursor = server.event_journal.latest_seq
                replay_cursor, replay_events = (
                    server.prepare_authoritative_operation_replay(
                        scope_id,
                        snapshot_cursor=cursor,
                    )
                )
                self.assertEqual(cursor, replay_cursor)
                self.assertEqual(1, len(replay_events))
                writer = StagedWriter(
                    fail_write_call=fail_write_call,
                    fail_flush=fail_flush,
                )
                handler.wfile = writer

                with self.assertRaisesRegex(
                    BrokenPipeError,
                    "partial SSE",
                ):
                    handler._write_sse_event(replay_events[0])

                stream = self.get_sse(
                    "/api/events"
                    f"?blackboard_dir={quote(blackboard_dir)}"
                    "&once=1",
                    headers={"Last-Event-ID": str(cursor)},
                )
                events = self.parse_sse_events(stream)
                self.assertEqual(
                    ["snapshot", "operation_event"],
                    [event["event"] for event in events],
                )
                self.assertEqual(
                    1,
                    len(
                        [
                            event
                            for event in events
                            if event["event"] == "operation_event"
                        ]
                    ),
                )
                self.assertTrue(
                    events[1]["data"]["subscriber_local_replay"]
                )
                self.assertEqual(
                    2,
                    events[1]["data"]["payload"]["timeline_seq"],
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

    def test_single_shot_sse_closes_after_snapshot(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.port,
            timeout=5,
        )
        try:
            connection.request("GET", "/api/events?once=1")
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()

        self.assertEqual(200, response.status)
        self.assertEqual("close", response.getheader("Connection"))
        self.assertIn(b"event: snapshot", payload)

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

    def test_micromachine_status_attaches_operation_with_authoritative_epoch(self):
        telemetry = battlefield_projection_telemetry()
        payload = web_gui._micromachine_status_payload(
            {
                "active_updates": [
                    {
                        "update_id": "battlefield-operation",
                        "manager_bias_domains": ["combat"],
                        "vector": {
                            "goal": "canonical flank",
                            "operations": [
                                {
                                    "operation_id": "flank-alpha",
                                    "generation": 3,
                                    "goal": "canonical flank",
                                    "tactical_task": {
                                        "task_type": (
                                            "pressure_with_main_army"
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
            telemetry=telemetry,
            result_stream=[
                {
                    "update_id": "battlefield-operation",
                    "battlefield_session_epoch": "1700000000000",
                }
            ],
        )

        operation = payload["operations"][0]
        self.assertEqual(
            "1700000000000",
            operation["battlefield_session_epoch"],
        )
        self.assertEqual(
            "matched",
            operation["battlefield_projection_join"]["status"],
        )
        self.assertEqual(
            "battlefield-operation",
            operation["battlefield_operation"]["identity"]["update_id"],
        )

    def test_active_operation_requires_persisted_epoch_and_generation(self):
        telemetry = battlefield_projection_telemetry()
        active_update = {
            "update_id": "battlefield-operation",
            "manager_bias_domains": ["combat"],
            "vector": {
                "goal": "canonical flank",
                "operations": [
                    {
                        "operation_id": "flank-alpha",
                        "generation": 3,
                        "goal": "canonical flank",
                    }
                ],
            },
        }

        missing_epoch = web_gui._micromachine_status_payload(
            {"active_updates": [active_update]},
            telemetry=telemetry,
        )["operations"][0]
        self.assertFalse(
            missing_epoch["battlefield_projection_identity_complete"]
        )
        self.assertIsNone(missing_epoch["battlefield_operation"])
        self.assertEqual(
            "operation_session_epoch_missing",
            missing_epoch["battlefield_projection_join"]["reason"],
        )

        missing_generation_update = deepcopy(active_update)
        del missing_generation_update["vector"]["operations"][0][
            "generation"
        ]
        missing_generation = web_gui._micromachine_status_payload(
            {"active_updates": [missing_generation_update]},
            telemetry=telemetry,
            result_stream=[
                {
                    "update_id": "battlefield-operation",
                    "battlefield_session_epoch": "1700000000000",
                }
            ],
        )["operations"][0]
        self.assertFalse(
            missing_generation["battlefield_projection_identity_complete"]
        )
        self.assertIsNone(missing_generation["battlefield_operation"])
        self.assertEqual(
            "operation_canonical_identity_incomplete",
            missing_generation["battlefield_projection_join"]["reason"],
        )

    def test_historical_operation_preserves_published_epoch_and_fails_closed(
        self,
    ):
        telemetry = battlefield_projection_telemetry()
        overview = telemetry["battlefield_overview"]
        operation = overview["operation_ownership"][0]
        operation["identity"].update(
            {
                "update_id": "historical-update",
                "scope": "operation:historical-operation",
                "session_epoch": 1700000000000,
                "operation_id": "historical-operation",
                "generation": 4,
            }
        )
        operation["operation_id"] = "historical-operation"
        operation["generation"] = 4
        operation["operation_completion"]["generation"] = 4
        overview["operation_ownership"] = [operation]
        overview["transfer_availability"]["entries"] = []
        historical_update = {
            "update_id": "historical-update",
            "vector": {
                "operation_id": "historical-operation",
                "generation": 4,
                "goal": "historical operation",
            },
        }

        stale = web_gui._micromachine_status_payload(
            {"active_updates": []},
            telemetry=telemetry,
            result_stream=[
                {
                    "status": "published",
                    "update": historical_update,
                    "battlefield_session_epoch": "1699999999999",
                }
            ],
        )["operations"][0]
        self.assertEqual(
            "1699999999999",
            stale["battlefield_session_epoch"],
        )
        self.assertIsNone(stale["battlefield_operation"])
        self.assertEqual(
            "operation_session_epoch_mismatch",
            stale["battlefield_projection_join"]["reason"],
        )

        missing = web_gui._micromachine_status_payload(
            {"active_updates": []},
            telemetry=telemetry,
            result_stream=[
                {
                    "status": "published",
                    "update": historical_update,
                }
            ],
        )["operations"][0]
        self.assertEqual("", missing["battlefield_session_epoch"])
        self.assertIsNone(missing["battlefield_operation"])
        self.assertEqual(
            "operation_session_epoch_missing",
            missing["battlefield_projection_join"]["reason"],
        )

        with tempfile.TemporaryDirectory() as directory:
            restored = web_gui._micromachine_compile_result_stream(
                [
                    {
                        "written_at_unix": time.time(),
                        "battlefield_session_epoch": "1699999999999",
                        "result": {
                            "status": "published",
                            "update": historical_update,
                        },
                    }
                ],
                blackboard_dir=directory,
            )
        self.assertEqual(
            "1699999999999",
            restored[0]["battlefield_session_epoch"],
        )

    def test_operation_prerequisites_use_canonical_family_contract(self):
        update_id = "canonical-prerequisite-update"
        operation_id = "marauder-defense"
        operation_update = {
            "update_id": update_id,
            "vector": {
                "operation_id": operation_id,
                "generation": 2,
                "goal": "marauder defense",
            },
        }

        def status_for(
            prerequisites,
            missing_prerequisites,
            *,
            unit_type="TERRAN_MARAUDER",
            include_counts=True,
            include_requirement=True,
            operation_count_values=None,
        ):
            requirement = {
                "unit_type": unit_type,
                "role": "defender",
                "production_blocker": "missing_addon",
                "prerequisites": prerequisites,
                "missing_prerequisites": missing_prerequisites,
            }
            operation = {
                "operation_id": operation_id,
                "update_id": update_id,
                "generation": 2,
                "status": "WAITING_FOR_UNITS",
            }
            if include_requirement:
                operation["requirement_progress"] = [requirement]
            if include_counts:
                requirement.update(
                    {
                        "target_count": 4,
                        "assigned_count": 2,
                        "represented_count": 2,
                        "completed_count": 1,
                        "in_progress_count": 1,
                        "queued_count": 0,
                        "missing_count": 2,
                    }
                )
                operation.update(
                    {
                        "requirement_target_count": 4,
                        "requirement_represented_count": 2,
                        "requirement_missing_count": 2,
                    }
                )
            if operation_count_values is not None:
                operation.update(operation_count_values)
            return web_gui._micromachine_operation_status_payload(
                operation_update,
                operation_id=operation_id,
                operation_count=1,
                active=True,
                telemetry={
                    "frame": 240,
                    "active_modulation_ids": [update_id],
                    "managers": {
                        "OperationDirector": {
                            "policy_update_id": update_id,
                            "operations": [operation],
                        }
                    },
                },
                telemetry_archive=(),
                blackboard_dir="",
                result_item={},
                compile_result={},
            )

        valid = status_for(
            ["TERRAN_BARRACKS", "TERRAN_BARRACKSTECHLAB"],
            ["TERRAN_BARRACKSTECHLAB"],
        )
        valid_requirement = valid["operation_convergence"]["requirements"][0]
        self.assertEqual("marauder", valid_requirement["canonical_family"])
        self.assertEqual(
            ["TERRAN_BARRACKS", "BARRACKS_TECHLAB"],
            valid_requirement["prerequisites"],
        )
        self.assertEqual(
            ["BARRACKS_TECHLAB"],
            valid_requirement["missing_prerequisites"],
        )
        self.assertEqual(
            "valid",
            valid_requirement["prerequisite_integrity_status"],
        )

        unrelated = status_for(
            ["TERRAN_BARRACKS", "TERRAN_FUSIONCORE"],
            ["TERRAN_FUSIONCORE"],
        )
        unrelated_convergence = unrelated["operation_convergence"]
        unrelated_requirement = unrelated_convergence["requirements"][0]
        self.assertEqual(
            ["TERRAN_BARRACKS"],
            unrelated_requirement["prerequisites"],
        )
        self.assertEqual([], unrelated_requirement["missing_prerequisites"])
        self.assertEqual(
            "blocked",
            unrelated_requirement["prerequisite_integrity_status"],
        )
        self.assertEqual(
            ["unrelated_prerequisite_evidence"],
            unrelated_convergence["prerequisite_integrity_blockers"],
        )
        self.assertNotIn(
            "FUSIONCORE",
            json.dumps(unrelated_convergence),
        )

        incomplete = status_for(
            ["TERRAN_BARRACKS"],
            [],
        )
        incomplete_requirement = incomplete["operation_convergence"][
            "requirements"
        ][0]
        self.assertEqual(
            "blocked",
            incomplete_requirement["prerequisite_integrity_status"],
        )
        self.assertEqual(
            ["prerequisite_chain_incomplete"],
            incomplete_requirement["prerequisite_integrity_blockers"],
        )

        hellbat_valid = status_for(
            ["TERRAN_FACTORY", "TERRAN_ARMORY"],
            [],
            unit_type="TERRAN_HELLIONTANK",
        )
        hellbat_requirement = hellbat_valid["operation_convergence"][
            "requirements"
        ][0]
        self.assertEqual(
            ["TERRAN_FACTORY", "TERRAN_ARMORY"],
            hellbat_requirement["prerequisites"],
        )
        self.assertEqual(
            "valid",
            hellbat_requirement["prerequisite_integrity_status"],
        )

        hellbat_incomplete = status_for(
            ["TERRAN_FACTORY"],
            [],
            unit_type="TERRAN_HELLIONTANK",
        )
        self.assertEqual(
            ["prerequisite_chain_incomplete"],
            hellbat_incomplete["operation_convergence"][
                "prerequisite_integrity_blockers"
            ],
        )

        missing_counts = status_for(
            ["TERRAN_BARRACKS", "TERRAN_BARRACKSTECHLAB"],
            ["TERRAN_BARRACKSTECHLAB"],
            include_counts=False,
        )
        missing_count_convergence = missing_counts["operation_convergence"]
        missing_count_requirement = missing_count_convergence["requirements"][0]
        for field in (
            "target_count",
            "assigned_count",
            "represented_count",
            "completed_count",
            "in_progress_count",
            "queued_count",
            "missing_count",
        ):
            with self.subTest(field=field):
                self.assertIsNone(missing_count_requirement[field])
        self.assertIsNone(missing_count_convergence["target_count"])
        self.assertIsNone(missing_count_convergence["represented_count"])
        self.assertIsNone(missing_count_convergence["missing_count"])
        self.assertEqual(
            "blocked",
            missing_count_convergence["prerequisite_integrity_status"],
        )
        self.assertEqual(
            [
                "requirement_count_evidence_missing",
                "operation_count_evidence_missing",
            ],
            missing_count_convergence["prerequisite_integrity_blockers"],
        )
        invalid_operation_counts = (
            {},
            {
                "requirement_target_count": "invalid",
                "requirement_represented_count": "invalid",
                "requirement_missing_count": "invalid",
            },
            {
                "requirement_target_count": True,
                "requirement_represented_count": True,
                "requirement_missing_count": True,
            },
            {
                "requirement_target_count": -1,
                "requirement_represented_count": -1,
                "requirement_missing_count": -1,
            },
        )
        for values in invalid_operation_counts:
            with self.subTest(operation_count_values=values):
                missing_operation_counts = status_for(
                    [],
                    [],
                    include_counts=False,
                    include_requirement=False,
                    operation_count_values=values,
                )["operation_convergence"]
                self.assertEqual([], missing_operation_counts["requirements"])
                self.assertIsNone(missing_operation_counts["target_count"])
                self.assertIsNone(
                    missing_operation_counts["represented_count"]
                )
                self.assertIsNone(missing_operation_counts["missing_count"])
                self.assertEqual(
                    "blocked",
                    missing_operation_counts["prerequisite_integrity_status"],
                )
                self.assertEqual(
                    ["operation_count_evidence_missing"],
                    missing_operation_counts[
                        "prerequisite_integrity_blockers"
                    ],
                )

    def test_battlefield_projection_attachment_requires_exact_update_identity(self):
        overview = deepcopy(
            battlefield_projection_telemetry()["battlefield_overview"]
        )
        first = overview["operation_ownership"][0]
        first["identity"].update(
            {
                "update_id": "update-a",
                "scope": "operation:shared-operation",
                "operation_id": "shared-operation",
                "generation": 3,
            }
        )
        first["operation_id"] = "shared-operation"
        first["generation"] = 3
        second = deepcopy(first)
        second["identity"]["update_id"] = "update-b"
        overview["operation_ownership"] = [first, second]

        attached = web_gui._attach_battlefield_operation_projections(
            [
                {
                    "update_id": "update-a",
                    "battlefield_session_epoch": "1700000000000",
                    "battlefield_projection_identity_complete": True,
                    "operation_id": "shared-operation",
                    "operation_generation": 3,
                },
                {
                    "update_id": "update-b",
                    "battlefield_session_epoch": "1700000000000",
                    "battlefield_projection_identity_complete": True,
                    "operation_id": "shared-operation",
                    "operation_generation": 3,
                },
                {
                    "update_id": "update-c",
                    "operation_console_execution_owner_update_id": "update-a",
                    "battlefield_session_epoch": "1700000000000",
                    "battlefield_projection_identity_complete": True,
                    "operation_id": "shared-operation",
                    "operation_generation": 3,
                },
            ],
            overview,
        )

        self.assertEqual(
            "update-a",
            attached[0]["battlefield_operation"]["identity"]["update_id"],
        )
        self.assertEqual(
            "update-b",
            attached[1]["battlefield_operation"]["identity"]["update_id"],
        )
        self.assertEqual(
            "update-a",
            attached[2]["battlefield_operation"]["identity"]["update_id"],
        )
        self.assertTrue(
            all(
                item["battlefield_projection_join"]["status"] == "matched"
                for item in attached
            )
        )

    def test_battlefield_projection_attachment_rejects_foreign_join_identity(self):
        overview = deepcopy(
            battlefield_projection_telemetry()["battlefield_overview"]
        )
        operation = overview["operation_ownership"][0]
        operation["identity"].update(
            {
                "update_id": "join-update",
                "scope": "operation:join-operation",
                "session_epoch": 1700000000000,
                "operation_id": "join-operation",
                "generation": 4,
            }
        )
        operation["operation_id"] = "join-operation"
        operation["generation"] = 4
        overview["identity"]["session_epoch"] = 1700000000000
        overview["operation_ownership"] = [operation]

        cases = {
            "wrong_update": {
                "update_id": "foreign-update",
                "battlefield_session_epoch": "1700000000000",
                "battlefield_projection_identity_complete": True,
                "operation_id": "join-operation",
                "operation_generation": 4,
            },
            "wrong_generation": {
                "update_id": "join-update",
                "battlefield_session_epoch": "1700000000000",
                "battlefield_projection_identity_complete": True,
                "operation_id": "join-operation",
                "operation_generation": 5,
            },
            "wrong_operation": {
                "update_id": "join-update",
                "battlefield_session_epoch": "1700000000000",
                "battlefield_projection_identity_complete": True,
                "operation_id": "foreign-operation",
                "operation_generation": 4,
            },
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                attached = web_gui._attach_battlefield_operation_projections(
                    [payload],
                    overview,
                )[0]
                self.assertIsNone(attached["battlefield_operation"])
                self.assertEqual(
                    "missing",
                    attached["battlefield_projection_join"]["status"],
                )
                self.assertEqual(
                    "exact_projection_identity_not_found",
                    attached["battlefield_projection_join"]["reason"],
                )

        for label, operation_epoch, reason in (
            (
                "missing_operation_session",
                "",
                "operation_session_epoch_missing",
            ),
            (
                "stale_operation_session",
                "1699999999999",
                "operation_session_epoch_mismatch",
            ),
        ):
            with self.subTest(label=label):
                attached = web_gui._attach_battlefield_operation_projections(
                    [
                        {
                            "update_id": "join-update",
                            "battlefield_session_epoch": operation_epoch,
                            "battlefield_projection_identity_complete": True,
                            "operation_id": "join-operation",
                            "operation_generation": 4,
                        }
                    ],
                    overview,
                )[0]
                self.assertIsNone(attached["battlefield_operation"])
                self.assertEqual(
                    reason,
                    attached["battlefield_projection_join"]["reason"],
                )

        wrong_scope = deepcopy(overview)
        wrong_scope["operation_ownership"][0]["identity"]["scope"] = (
            "operation:foreign-operation"
        )
        wrong_session = deepcopy(overview)
        wrong_session["operation_ownership"][0]["identity"]["session_epoch"] = (
            1699999999999
        )
        for label, candidate in (
            ("wrong_scope", wrong_scope),
            ("wrong_session", wrong_session),
        ):
            with self.subTest(label=label):
                attached = web_gui._attach_battlefield_operation_projections(
                    [
                        {
                            "update_id": "join-update",
                            "battlefield_session_epoch": (
                                "1700000000000"
                            ),
                            "battlefield_projection_identity_complete": True,
                            "operation_id": "join-operation",
                            "operation_generation": 4,
                        }
                    ],
                    candidate,
                )[0]
                self.assertIsNone(attached["battlefield_operation"])
                self.assertEqual(
                    "missing",
                    attached["battlefield_projection_join"]["status"],
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
        pending_values = [payload]
        public_integer_values = set()
        while pending_values:
            value = pending_values.pop()
            if isinstance(value, dict):
                pending_values.extend(value.values())
            elif isinstance(value, list):
                pending_values.extend(value)
            elif type(value) is int:
                public_integer_values.add(value)
        for raw_tag in (101, 102, 103, 104, 201, 202, 301, 302):
            self.assertNotIn(raw_tag, public_integer_values)
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
        self.assertEqual(
            [
                {
                    "family": "marine",
                    "role": "base_defender",
                    "count": 2,
                    "ground_capable_count": 2,
                    "air_capable_count": 2,
                }
            ],
            payload["battlefield_overview"]["autonomous_ownership"][0][
                "composition"
            ],
        )
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
                    "1700000000100",
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
                    "1600000000000",
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
                update_id="attached-initial",
                frame=320,
                generation=1,
                session_epoch=1700000000100,
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
            launcher.attached = True
            self.server._http.micromachine_launcher = launcher
            with mock.patch(
                "starcraft_commander.micromachine_runtime."
                "MicroMachineFilesystemBlackboard",
                Backend,
            ):
                initial = self.get_json(
                    "/api/micromachine/status?blackboard_dir=" + directory
                )
                self.assertEqual(
                    "1700000000100",
                    initial["battlefield_overview"]["identity"][
                        "session_epoch"
                    ],
                )

                current["telemetry"] = battlefield_projection_telemetry(
                    update_id="detached-same-epoch-ahead",
                    frame=900,
                    generation=9,
                    session_epoch=1700000000100,
                )
                launcher.attached = False
                detached = self.get_json(
                    "/api/micromachine/status?blackboard_dir=" + directory
                )
                self.assertIsNone(detached["battlefield_overview"])
                self.assertTrue(detached["telemetry_stale_or_detached"])
                self.assertFalse(detached["operation_registry_authoritative"])

                current["telemetry"] = battlefield_projection_telemetry(
                    update_id="attached-resumed",
                    frame=321,
                    generation=2,
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
                "1700000000100",
                attached["battlefield_projection_identity"]["session_epoch"],
            )
            self.assertEqual(
                "1700000000100",
                attached["battlefield_overview"]["identity"][
                    "session_epoch"
                ],
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

    def test_micromachine_status_preserves_latest_natural_language_command(self):
        original_command = "SCV를 생산한다"
        compile_result = {
            "status": "published",
            "update_id": "latest-scv-command",
            "source": "llm",
            "command_queue": {
                "command_text": original_command,
                "category": "production",
            },
        }

        payload = web_gui._micromachine_status_payload(
            {"active_updates": []},
            telemetry=None,
            compile_result=compile_result,
        )

        self.assertEqual(
            original_command,
            payload["latest_request"]["command_text"],
        )
        self.assertEqual(
            original_command,
            payload["latest_request"]["command_queue"]["command_text"],
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
                        "canonical_family": "marine",
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
                        "prerequisite_integrity_status": "valid",
                        "prerequisite_integrity_blockers": [],
                    }
                ],
                "prerequisite_integrity_status": "valid",
                "prerequisite_integrity_blockers": [],
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
        execution_owner_update_id = "active-recon-alpha-owner"
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
                            "policy_update_id": execution_owner_update_id,
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
                            "update_id": execution_owner_update_id,
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
        self.assertEqual(update_id, operation["update_id"])
        self.assertEqual(
            execution_owner_update_id,
            operation["operation_console_execution_owner_update_id"],
        )
        execution = operation["intervention"]["command_execution"]
        self.assertEqual(
            execution_owner_update_id,
            execution["command_id"],
        )
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
        self.assertEqual(
            execution_owner_update_id,
            operation["family_evidence"][0]["update_id"],
        )
        self.assertEqual("effect", operation["family_evidence"][0]["stage"])

    def test_archive_only_rejected_edit_restores_execution_owner_identity(self):
        update_id = "archive-rejected-operation-edit"
        execution_owner_update_id = "archive-active-recon-owner"
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
            "telemetry": {"frame": 260},
        }
        archived_document = {
            "frame": 240,
            "active_modulation_ids": [
                update_id,
                execution_owner_update_id,
            ],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [
                        {
                            "operation_id": "recon-alpha",
                            "generation": 1,
                            "policy_update_id": execution_owner_update_id,
                            "status": "MOVING",
                            "received_frame": 100,
                            "assigned_frame": 120,
                            "submitted_frame": 130,
                            "last_action_frame": 140,
                            "movement_frame": 150,
                            "assigned_unit_tags": [11],
                            "assigned_count": 1,
                            "max_home_distance": 20.0,
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
                            "update_id": execution_owner_update_id,
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
                        }
                    ],
                }
            },
        }
        current_document = {
            "frame": 260,
            "active_modulation_ids": [update_id],
            "managers": {
                "OperationDirector": {
                    "policy_update_id": update_id,
                    "operations": [],
                    "pending_family_effects": [
                        {
                            "update_id": update_id,
                            "operation_id": "recon-alpha",
                            "generation": 2,
                            "family": "marine",
                            "unit_type": "TERRAN_MARINE",
                            "role": "scout",
                            "action": "move",
                            "required_effect": "movement_or_engagement",
                            "attempt_generation": 1,
                            "attempted_count": 1,
                            "attempted_frame": 250,
                            "submitted_count": 0,
                            "submitted_frame": 0,
                            "effect_kind": "",
                            "effect_count": 0,
                            "effect_frame": 0,
                            "blocker_manager": "",
                            "blocker": "",
                        }
                    ],
                }
            },
        }
        telemetry = SimpleNamespace(
            frame=260,
            active_modulation_ids=(update_id,),
            to_dict=lambda: current_document,
        )

        payload = web_gui._micromachine_status_payload(
            dashboard,
            telemetry=telemetry,
            telemetry_archive=(archived_document,),
            blackboard_dir="/tmp/archive-rejected-operation-edit",
            compile_result={
                "status": "compiled",
                "update_id": update_id,
                "command_text": "정찰대 마린 한 기를 공격대로 이관해",
            },
        )

        operation = payload["operations"][0]
        self.assertFalse(operation["telemetry_current"])
        self.assertEqual(1, operation["operation_generation"])
        self.assertEqual(2, operation["requested_operation_generation"])
        self.assertEqual(
            execution_owner_update_id,
            operation["operation_console_execution_owner_update_id"],
        )
        execution = operation["intervention"]["command_execution"]
        self.assertEqual(execution_owner_update_id, execution["command_id"])
        self.assertEqual(1, execution["operation_generation"])
        self.assertEqual("effect_observed", execution["state"])
        self.assertEqual(1, len(operation["family_evidence"]))
        self.assertEqual(
            execution_owner_update_id,
            operation["family_evidence"][0]["update_id"],
        )

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
        self.assertIn(
            'var DEFAULT_BLACKBOARD_DIR = "/tmp/voi-mm-custom&safe";',
            page,
        )
        self.assertIn("전술 명령창", page)
        self.assertNotIn("micromachine-tactical-evidence", page)
        self.assertNotIn("micromachine-command-execution", page)

    def test_runtime_start_routes_micromachine_mode_to_launcher(self):
        class FakeMicroMachineLauncher:
            def __init__(self):
                self.started = []

            def start(
                self,
                blackboard_dir="",
                enemy_difficulty=7,
                sc2_launch_nonce="",
            ):
                self.started.append(
                    (blackboard_dir, enemy_difficulty, sc2_launch_nonce)
                )
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
                "sc2_launch_nonce": "visible-launch-nonce",
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
            [
                (
                    "/tmp/voi-mm-runtime-test",
                    9,
                    "visible-launch-nonce",
                )
            ],
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

            def start(
                self,
                blackboard_dir="",
                enemy_difficulty=7,
                sc2_launch_nonce="",
            ):
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

    def test_installed_wheel_disables_source_provenance_launcher(self):
        with (
            mock.patch.object(web_gui, "_REPO_ROOT", ""),
            mock.patch.object(
                web_gui,
                "source_repository_root",
                return_value=None,
            ),
        ):
            launcher = web_gui._MicroMachineLaunchManager()
            snapshot = launcher.snapshot()
            started = launcher.start()

        self.assertFalse(snapshot["enabled"])
        self.assertFalse(started["enabled"])
        self.assertEqual("", snapshot["script_path"])
        self.assertEqual("", started["script_path"])
        self.assertEqual("blocked", started["status"])
        self.assertIn("source checkout", started["error"])

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
            with (
                mock.patch.object(
                    web_gui.subprocess,
                    "Popen",
                    return_value=FakeProcess(),
                ) as popen,
                mock.patch.object(
                    web_gui.threading.Thread,
                    "start",
                    return_value=None,
                ),
                mock.patch.object(
                    web_gui,
                    "read_sc2_launch_receipt",
                    return_value=visible_sc2_launch_receipt(),
                ),
            ):
                launcher = web_gui._MicroMachineLaunchManager(script_path=__file__)
                started = launcher.start(
                    directory,
                    enemy_difficulty=9,
                    sc2_launch_nonce="visible-launch-nonce",
                )

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
            self.assertEqual(12345, started["pid"])
            self.assertEqual(222, started["sc2_pid"])
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
                mock.patch.object(
                    web_gui,
                    "read_sc2_launch_receipt",
                    return_value=visible_sc2_launch_receipt(),
                ),
            ):
                stale = launcher.start(
                    directory,
                    sc2_launch_nonce="visible-launch-nonce",
                )

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
                mock.patch.object(
                    web_gui,
                    "read_sc2_launch_receipt",
                    return_value=visible_sc2_launch_receipt(),
                ),
            ):
                launcher.start(
                    directory,
                    sc2_launch_nonce="visible-launch-nonce",
                )

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
                mock.patch.object(
                    web_gui,
                    "read_sc2_launch_receipt",
                    return_value=visible_sc2_launch_receipt(),
                ),
            ):
                launcher.start(
                    directory,
                    sc2_launch_nonce="visible-launch-nonce",
                )

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

    def test_legacy_command_preserves_exact_browser_request_correlation(self):
        request_id = "pending-http-correlation-17"
        status, _content_type, payload = self.post_command(
            "상황 보고해줘",
            request_id=request_id,
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload.decode("utf-8")), {"accepted": True})

        matched = self.poll_history_until(
            lambda event: (
                event.get("detail", {}).get("web_request_id")
                == request_id
            ),
            "legacy outcome carrying the exact browser request identity",
        )
        event = matched[0]
        self.assertEqual(request_id, event["request_id"])
        self.assertEqual(
            request_id,
            event["detail"]["web_request_id"],
        )

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
            (
                "non-string request id",
                json.dumps(
                    {"text": "상태확인", "request_id": 42}
                ).encode("utf-8"),
            ),
            (
                "blank request id",
                json.dumps(
                    {"text": "상태확인", "request_id": "   "}
                ).encode("utf-8"),
            ),
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

    def test_operation_timeline_emits_authoritative_safety_transitions(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-authoritative-safety-transitions"

        baseline = semantic_operation_payload(
            operation_id="safety-alpha",
            generation=2,
            frame=200,
            owner_count=4,
            required_count=4,
        )
        operation = baseline["operations"][0]
        operation["update"] = {
            "update_id": operation["update_id"],
            "vector": {
                "operation_id": "safety-alpha",
                "generation": 2,
                "command_layer": "emergency",
                "emergency": {
                    "force_retreat": True,
                    "cancel_attacks": True,
                },
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "tactical_nuke",
                },
            },
        }
        operation["family_evidence"] = [
            {
                "update_id": operation["update_id"],
                "operation_id": "safety-alpha",
                "generation": 2,
                "family": "ghost",
                "action": "ability:tactical_nuke",
                "required_effect": "ability_state_or_effect",
                "attempt_generation": 1,
                "attempted_count": 1,
                "attempted_frame": 199,
                "submitted_count": 0,
                "effect_count": 0,
                "blocker_manager": "CombatCommander",
                "blocker": "no_valid_nuke_target",
                "stage": "blocked",
            }
        ]
        operation["intervention"]["command_execution"].update(
            {
                "state": "failed",
                "failed": True,
                "blocker_manager": "CombatCommander",
                "blocker_reason": "no_valid_nuke_target",
            }
        )

        initial = reducer.observe(
            baseline,
            blackboard_scope_id=scope_id,
        )
        initial_kinds = {
            event["kind"] for event in initial["operation_events"]
        }
        self.assertIn("critical_ability_failure", initial_kinds)
        self.assertNotIn("emergency_retreat", initial_kinds)
        self.assertNotIn("force_loss", initial_kinds)
        self.assertNotIn("base_under_attack", initial_kinds)

        retreat = deepcopy(baseline)
        retreat["operations"][0]["telemetry_frame"] = 201
        retreat_operation = retreat["operations"][0]
        retreat_projection = retreat_operation["battlefield_operation"]
        retreat_projection["identity"]["game_frame"] = 201
        retreat_projection["operation_completion"].update(
            {
                "movement_observed": True,
                "frame": 201,
            }
        )
        retreat_operation["operation_convergence"].update(
            {
                "status": "BLOCKED",
                "blocker": "emergency_retreat_preempted",
            }
        )
        retreat_operation["squad_order"] = "retreat"
        retreat_projection["operation_launch_policy"]["safety_evidence"][
            "emergency_preemption"
        ] = "active"
        retreat["battlefield_overview"]["identity"]["game_frame"] = 201
        retreat_result = reducer.observe(
            retreat,
            blackboard_scope_id=scope_id,
        )
        retreat_kinds = [
            event["kind"]
            for event in retreat_result["operations"][0][
                "semantic_timeline"
            ]
        ]
        self.assertEqual(1, retreat_kinds.count("emergency_retreat"))

        force_loss = semantic_operation_payload(
            operation_id="safety-alpha",
            generation=2,
            frame=202,
            owner_count=2,
            required_count=4,
        )
        force_loss["operations"][0]["update"] = deepcopy(
            baseline["operations"][0]["update"]
        )
        force_loss["operations"][0]["family_evidence"] = deepcopy(
            baseline["operations"][0]["family_evidence"]
        )
        force_loss_result = reducer.observe(
            force_loss,
            blackboard_scope_id=scope_id,
        )
        force_loss_events = [
            event
            for event in force_loss_result["operation_events"]
            if event["kind"] == "force_loss"
        ]
        self.assertEqual(1, len(force_loss_events))
        self.assertEqual(2, force_loss_events[0]["owner_count"])
        self.assertEqual(4, force_loss_events[0]["required_count"])

        persistent_loss = semantic_operation_payload(
            operation_id="safety-alpha",
            generation=2,
            frame=203,
            owner_count=2,
            required_count=4,
        )
        persistent_loss_result = reducer.observe(
            persistent_loss,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            1,
            sum(
                event["kind"] == "force_loss"
                for event in persistent_loss_result["operation_events"]
            ),
        )

        recovered = semantic_operation_payload(
            operation_id="safety-alpha",
            generation=2,
            frame=204,
            owner_count=4,
            required_count=4,
        )
        reducer.observe(recovered, blackboard_scope_id=scope_id)
        second_loss = semantic_operation_payload(
            operation_id="safety-alpha",
            generation=2,
            frame=205,
            owner_count=1,
            required_count=4,
        )
        second_loss_result = reducer.observe(
            second_loss,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            2,
            sum(
                event["kind"] == "force_loss"
                for event in second_loss_result["operation_events"]
            ),
        )

        clear_base = deepcopy(second_loss)
        clear_base["operations"][0]["telemetry_frame"] = 206
        clear_base["operations"][0]["battlefield_operation"]["identity"][
            "game_frame"
        ] = 206
        clear_base["battlefield_overview"]["identity"]["game_frame"] = 206
        clear_readiness = clear_base["battlefield_overview"]["bases"][0][
            "base_readiness"
        ]
        clear_readiness.update(
            {
                "readiness_state": "ready",
                "ground_threat": 0.0,
                "air_threat": 0.0,
                "observed_enemy_strength": 0.0,
                "last_evidence_frame": 206,
            }
        )
        reducer.observe(clear_base, blackboard_scope_id=scope_id)

        threatened_base = deepcopy(clear_base)
        threatened_base["operations"][0]["telemetry_frame"] = 207
        threatened_base["operations"][0]["battlefield_operation"]["identity"][
            "game_frame"
        ] = 207
        threatened_base["battlefield_overview"]["identity"]["game_frame"] = 207
        readiness = threatened_base["battlefield_overview"]["bases"][0][
            "base_readiness"
        ]
        readiness.update(
            {
                "readiness_state": "unsafe",
                "reason": "visible_ground_attack",
                "ground_threat": 3.0,
                "observed_enemy_strength": 3.0,
                "last_evidence_frame": 207,
                "evidence_class": "visible_enemy_units",
            }
        )
        threatened_projection = threatened_base["operations"][0][
            "battlefield_operation"
        ]
        threatened_projection["operation_launch_policy"].update(
            {
                "decision": "blocked",
                "blocker": "base_protected_minimum_not_met",
            }
        )
        threatened_projection["operation_launch_policy"][
            "safety_evidence"
        ]["protected_defense_minimum_respected"] = False
        threatened_result = reducer.observe(
            threatened_base,
            blackboard_scope_id=scope_id,
        )
        base_events = [
            event
            for event in threatened_result["operation_events"]
            if event["kind"] == "base_under_attack"
        ]
        self.assertEqual(1, len(base_events))
        self.assertEqual("safety-alpha", base_events[0]["operation_id"])
        self.assertEqual("visible_ground_attack", base_events[0]["summary"])

        same_threat = deepcopy(threatened_base)
        same_threat["operations"][0]["telemetry_frame"] = 208
        same_threat["operations"][0]["battlefield_operation"]["identity"][
            "game_frame"
        ] = 208
        same_threat["battlefield_overview"]["identity"]["game_frame"] = 208
        same_threat["battlefield_overview"]["bases"][0]["base_readiness"][
            "last_evidence_frame"
        ] = 208
        same_threat_result = reducer.observe(
            same_threat,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            1,
            sum(
                event["kind"] == "base_under_attack"
                for event in same_threat_result["operation_events"]
            ),
        )

    def test_operation_timeline_safety_transitions_rearm_after_clear(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-safety-transition-rearm"

        def emergency_payload(frame, *, active):
            payload = semantic_operation_payload(
                operation_id="rearm-alpha",
                generation=2,
                frame=frame,
            )
            operation = payload["operations"][0]
            projection = operation["battlefield_operation"]
            if active:
                operation["operation_convergence"].update(
                    {
                        "status": "BLOCKED",
                        "blocker": "emergency_retreat_preempted",
                    }
                )
                operation["squad_order"] = "retreat"
                projection["operation_launch_policy"]["safety_evidence"][
                    "emergency_preemption"
                ] = "active"
            return payload

        reducer.observe(
            emergency_payload(400, active=False),
            blackboard_scope_id=scope_id,
        )
        first = reducer.observe(
            emergency_payload(401, active=True),
            blackboard_scope_id=scope_id,
        )
        reducer.observe(
            emergency_payload(402, active=False),
            blackboard_scope_id=scope_id,
        )
        second = reducer.observe(
            emergency_payload(403, active=True),
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(
            1,
            sum(
                event["kind"] == "emergency_retreat"
                for event in first["operation_events"]
            ),
        )
        self.assertEqual(
            2,
            sum(
                event["kind"] == "emergency_retreat"
                for event in second["operation_events"]
            ),
        )

    def test_operation_timeline_force_loss_excludes_transfer_and_terminal_drop(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-force-loss-exclusions"
        reducer.observe(
            semantic_operation_payload(
                operation_id="loss-alpha",
                generation=3,
                frame=500,
                owner_count=4,
                required_count=4,
            ),
            blackboard_scope_id=scope_id,
        )
        transferred = reducer.observe(
            semantic_operation_payload(
                operation_id="loss-alpha",
                generation=3,
                frame=501,
                owner_count=2,
                required_count=4,
                operation_edit={
                    "action": "transfer_out",
                    "resolution": "transferred",
                    "transferred_out_count": 2,
                },
            ),
            blackboard_scope_id=scope_id,
        )
        self.assertNotIn(
            "force_loss",
            [event["kind"] for event in transferred["operation_events"]],
        )

        terminal_reducer = web_gui._OperationSemanticTimelineReducer()
        terminal_reducer.observe(
            semantic_operation_payload(
                operation_id="terminal-loss",
                generation=1,
                frame=600,
                owner_count=4,
                required_count=4,
            ),
            blackboard_scope_id=scope_id,
        )
        terminal = terminal_reducer.observe(
            semantic_operation_payload(
                operation_id="terminal-loss",
                generation=1,
                frame=601,
                owner_count=0,
                required_count=4,
                terminal=True,
                disposition="completed",
            ),
            blackboard_scope_id=scope_id,
        )
        self.assertNotIn(
            "force_loss",
            [event["kind"] for event in terminal["operation_events"]],
        )

    def test_operation_timeline_rejects_mismatched_critical_ability_evidence(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        payload = semantic_operation_payload(
            operation_id="ability-alpha",
            generation=3,
            frame=300,
            execution_state="failed",
            blocker="ability_failed",
        )
        operation = payload["operations"][0]
        operation["update"] = {
            "update_id": operation["update_id"],
            "vector": {
                "operation_id": "ability-alpha",
                "generation": 3,
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "yamato",
                },
            },
        }
        operation["family_evidence"] = [
            {
                "update_id": operation["update_id"],
                "operation_id": "ability-alpha",
                "generation": 2,
                "action": "ability:yamato",
                "required_effect": "ability_state_or_effect",
                "attempt_generation": 1,
                "attempted_count": 1,
                "attempted_frame": 299,
                "effect_count": 0,
                "blocker_manager": "CombatCommander",
                "blocker": "ability_failed",
                "stage": "blocked",
            },
            {
                "update_id": operation["update_id"],
                "operation_id": "ability-alpha",
                "generation": 3,
                "action": "ability:stimpack",
                "required_effect": "ability_state_or_effect",
                "attempt_generation": 2,
                "attempted_count": 1,
                "attempted_frame": 300,
                "effect_count": 0,
                "blocker_manager": "CombatCommander",
                "blocker": "ability_failed",
                "stage": "blocked",
            },
        ]

        result = reducer.observe(
            payload,
            blackboard_scope_id="scope-ability-generation-mismatch",
        )

        self.assertNotIn(
            "critical_ability_failure",
            [event["kind"] for event in result["operation_events"]],
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
        accepted = reducer.observe(
            attached,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(1, len(reducer._accepted_operations))

        detached = reducer.observe(
            {
                "blackboard_scope_id": scope_id,
                "operation_registry_authoritative": False,
                "operations": [],
            },
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(accepted["operations"], detached["operations"])
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

    def test_operation_timeline_non_authoritative_same_epoch_cannot_advance_state(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-detached-same-epoch"
        session_epoch = 1700000000000
        attached = semantic_operation_payload(
            operation_id="recon-alpha",
            generation=1,
            frame=200,
            session_epoch=session_epoch,
        )
        attached["operation_registry_authoritative"] = True
        accepted = reducer.observe(
            attached,
            blackboard_scope_id=scope_id,
        )

        detached = semantic_operation_payload(
            operation_id="recon-alpha",
            generation=9,
            frame=900,
            session_epoch=session_epoch,
            movement=True,
        )
        detached["operation_registry_authoritative"] = False
        restored = reducer.observe(
            detached,
            blackboard_scope_id=scope_id,
        )

        family_key = (
            scope_id,
            str(session_epoch),
            "recon-alpha",
        )
        self.assertEqual(accepted["operations"], restored["operations"])
        self.assertEqual(1, reducer._generation_high_water[family_key])
        self.assertEqual(200, reducer._family_last_frame[family_key])

        resumed = semantic_operation_payload(
            operation_id="recon-alpha",
            generation=2,
            frame=201,
            session_epoch=session_epoch,
            movement=True,
        )
        resumed["operation_registry_authoritative"] = True
        resumed_result = reducer.observe(
            resumed,
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(
            2,
            resumed_result["operations"][0][
                "operation_generation"
            ],
        )
        self.assertEqual(
            201,
            resumed_result["operations"][0]["telemetry_frame"],
        )
        self.assertEqual(2, reducer._generation_high_water[family_key])
        self.assertEqual(201, reducer._family_last_frame[family_key])

    def test_operation_timeline_non_authoritative_snapshot_cannot_establish_state(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-detached-without-authority"
        session_epoch = 1700000000000
        detached = semantic_operation_payload(
            operation_id="recon-alpha",
            generation=9,
            frame=900,
            session_epoch=session_epoch,
            movement=True,
        )
        detached["operation_registry_authoritative"] = False

        detached_result = reducer.observe(
            detached,
            blackboard_scope_id=scope_id,
        )

        self.assertEqual("", reducer._scope_epochs.get(scope_id, ""))
        self.assertEqual({}, reducer._generation_high_water)
        self.assertEqual({}, reducer._family_last_frame)
        self.assertEqual({}, reducer._accepted_operations)
        self.assertEqual([], detached_result["operation_events"])
        self.assertEqual(
            [],
            detached_result["operations"][0]["semantic_timeline"],
        )

        attached = semantic_operation_payload(
            operation_id="recon-alpha",
            generation=1,
            frame=100,
            session_epoch=session_epoch,
        )
        attached["operation_registry_authoritative"] = True
        attached_result = reducer.observe(
            attached,
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(
            1,
            attached_result["operations"][0][
                "operation_generation"
            ],
        )
        self.assertEqual(
            100,
            attached_result["operations"][0]["telemetry_frame"],
        )

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

    def test_operation_timeline_uses_top_level_projection_identity_for_epoch_guard(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-top-level-projection-epoch"
        current_epoch = 1700000000000
        attached = semantic_operation_payload(
            operation_id="alpha-operation",
            frame=200,
            session_epoch=current_epoch,
        )
        attached["operation_registry_authoritative"] = True
        accepted = reducer.observe(
            attached,
            blackboard_scope_id=scope_id,
        )

        foreign = semantic_operation_payload(
            operation_id="beta-operation",
            frame=1,
            session_epoch=current_epoch + 1,
        )
        foreign["operation_registry_authoritative"] = False
        foreign["battlefield_projection_identity"] = {
            "session_epoch": current_epoch + 1,
            "generation": 1,
            "game_frame": 1,
        }
        foreign["battlefield_overview"] = None
        foreign["operations"][0]["battlefield_operation"] = None
        restored = reducer.observe(
            foreign,
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(accepted["operations"], restored["operations"])
        self.assertEqual(str(current_epoch), reducer._scope_epochs[scope_id])
        self.assertNotIn(
            "beta-operation",
            {
                operation["operation_id"]
                for operation in restored["operations"]
            },
        )

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

    def test_operation_timeline_rejects_foreign_update_for_same_generation(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-foreign-update"
        accepted = reducer.observe(
            semantic_operation_payload(
                operation_id="identity-alpha",
                generation=2,
                requested_generation=2,
                frame=200,
            ),
            blackboard_scope_id=scope_id,
        )
        accepted_operation = deepcopy(accepted["operations"][0])
        accepted_seq = accepted["operation_event_latest_seq"]

        foreign = semantic_operation_payload(
            operation_id="identity-alpha",
            generation=2,
            requested_generation=2,
            frame=201,
            movement=True,
            engagement=True,
        )
        foreign_operation = foreign["operations"][0]
        foreign_update_id = "foreign-update-identity-alpha-2"
        foreign_operation["update_id"] = foreign_update_id
        foreign_operation["compile_result"]["update_id"] = foreign_update_id
        foreign_operation["intervention"]["command_execution"][
            "command_id"
        ] = foreign_update_id
        foreign_operation["battlefield_operation"]["identity"][
            "update_id"
        ] = foreign_update_id

        rejected = reducer.observe(
            foreign,
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(accepted_seq, rejected["operation_event_latest_seq"])
        self.assertEqual(accepted_operation, rejected["operations"][0])
        self.assertNotIn(
            "engagement_observed",
            [
                event["kind"]
                for event in rejected["operations"][0]["semantic_timeline"]
            ],
        )

        missing_identity = semantic_operation_payload(
            operation_id="identity-alpha",
            generation=2,
            requested_generation=2,
            frame=202,
            movement=True,
        )
        missing_operation = missing_identity["operations"][0]
        missing_operation["update_id"] = ""
        missing_operation["compile_result"]["update_id"] = ""
        missing_operation["intervention"]["command_execution"][
            "command_id"
        ] = ""
        missing_operation["battlefield_operation"]["identity"][
            "update_id"
        ] = ""
        missing_result = reducer.observe(
            missing_identity,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            accepted_operation,
            missing_result["operations"][0],
        )

        forged_request = semantic_operation_payload(
            operation_id="identity-alpha",
            generation=2,
            requested_generation=3,
            frame=203,
            movement=True,
        )
        forged_result = reducer.observe(
            forged_request,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            accepted_operation,
            forged_result["operations"][0],
        )

        forged_cancellation = semantic_operation_payload(
            operation_id="identity-alpha",
            generation=2,
            requested_generation=2,
            frame=204,
            execution_state="cancelled",
        )
        forged_execution = forged_cancellation["operations"][0][
            "intervention"
        ]["command_execution"]
        forged_cancel_operation = forged_cancellation["operations"][0]
        forged_cancel_update_id = "foreign-cancel-identity-alpha-2"
        forged_cancel_operation["update_id"] = forged_cancel_update_id
        forged_cancel_operation["compile_result"][
            "update_id"
        ] = forged_cancel_update_id
        forged_cancel_operation["battlefield_operation"]["identity"][
            "update_id"
        ] = forged_cancel_update_id
        forged_execution["command_id"] = forged_cancel_update_id
        forged_execution["blocker_reason"] = "cancelled_by_policy"
        forged_execution["terminal_cleanup"] = {
            "action": "release_stop|cancelled_by_policy",
            "frame": 204,
            "operation_id": "identity-alpha",
            "generation": 2,
        }
        forged_cancel_result = reducer.observe(
            forged_cancellation,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            accepted_operation,
            forged_cancel_result["operations"][0],
        )

        accepted_edit_payload = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="identity-alpha",
                generation=2,
                requested_generation=3,
                frame=205,
                operation_edit={
                    "action": "reinforce",
                    "resolution": "blocked",
                    "blocker": "awaiting_reinforcement",
                },
            ),
            execution_owner_update_id="update-identity-alpha-2",
        )
        accepted_edit = reducer.observe(
            accepted_edit_payload,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            3,
            accepted_edit["operations"][0][
                "requested_operation_generation"
            ],
        )
        self.assertEqual(
            "update-identity-alpha-3",
            accepted_edit["operations"][0]["update_id"],
        )
        self.assertEqual(
            "update-identity-alpha-2",
            accepted_edit["operations"][0][
                "operation_console_execution_owner_update_id"
            ],
        )

        advanced = reducer.observe(
            semantic_operation_payload(
                operation_id="identity-alpha",
                generation=3,
                requested_generation=3,
                frame=206,
                movement=True,
            ),
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            3,
            advanced["operations"][0]["operation_generation"],
        )
        self.assertEqual(
            "update-identity-alpha-3",
            advanced["operations"][0]["update_id"],
        )

    def test_operation_timeline_authenticates_split_identity_channels(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-split-identity-authentication"
        owner_update_id = "update-split-alpha-3"
        latest_request_update_id = "update-split-alpha-4"

        reducer.observe(
            semantic_operation_payload(
                operation_id="split-alpha",
                generation=2,
                requested_generation=3,
                frame=100,
            ),
            blackboard_scope_id=scope_id,
        )
        latest_request = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="split-alpha",
                generation=2,
                requested_generation=4,
                frame=101,
                operation_edit={
                    "action": "reinforce",
                    "resolution": "blocked",
                    "blocker": "latest_request_waiting",
                },
            ),
            execution_owner_update_id=owner_update_id,
        )
        accepted = reducer.observe(
            latest_request,
            blackboard_scope_id=scope_id,
        )
        accepted_operation = deepcopy(accepted["operations"][0])
        accepted_seq = accepted["operation_event_latest_seq"]
        self.assertEqual(
            latest_request_update_id,
            accepted_operation["update_id"],
        )
        self.assertEqual(
            owner_update_id,
            accepted_operation[
                "operation_console_execution_owner_update_id"
            ],
        )

        channel_swap = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="split-alpha",
                generation=2,
                requested_generation=4,
                frame=102,
                movement=True,
            ),
            request_update_id=owner_update_id,
            execution_owner_update_id=owner_update_id,
        )
        swapped = reducer.observe(
            channel_swap,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(accepted_operation, swapped["operations"][0])
        self.assertEqual(accepted_seq, swapped["operation_event_latest_seq"])

        arbitrary_edit = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="split-alpha",
                generation=2,
                requested_generation=5,
                frame=103,
                movement=True,
                operation_edit={
                    "action": "not-a-real-operation-edit",
                    "resolution": "blocked",
                },
            ),
            execution_owner_update_id=owner_update_id,
        )
        arbitrary_result = reducer.observe(
            arbitrary_edit,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            accepted_operation,
            arbitrary_result["operations"][0],
        )

        forged_cancellation = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="split-alpha",
                generation=2,
                requested_generation=5,
                frame=104,
                execution_state="cancelled",
                operation_edit={
                    "action": "cancel",
                    "resolution": "applied",
                },
            ),
            execution_owner_update_id="foreign-split-owner",
        )
        forged_execution = forged_cancellation["operations"][0][
            "intervention"
        ]["command_execution"]
        forged_execution["blocker_reason"] = "cancelled_by_policy"
        forged_execution["terminal_cleanup"] = {
            "action": "release_stop|cancelled_by_policy",
            "frame": 104,
            "operation_id": "split-alpha",
            "generation": 2,
        }
        forged_result = reducer.observe(
            forged_cancellation,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            accepted_operation,
            forged_result["operations"][0],
        )

        valid_cancellation = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="split-alpha",
                generation=2,
                requested_generation=5,
                frame=105,
                execution_state="cancelled",
                operation_edit={
                    "action": "cancel",
                    "resolution": "applied",
                },
            ),
            execution_owner_update_id=owner_update_id,
        )
        valid_execution = valid_cancellation["operations"][0][
            "intervention"
        ]["command_execution"]
        valid_execution["blocker_reason"] = "cancelled_by_policy"
        valid_execution["terminal_cleanup"] = {
            "action": "release_stop|cancelled_by_policy",
            "frame": 105,
            "operation_id": "split-alpha",
            "generation": 2,
        }
        cancelled = reducer.observe(
            valid_cancellation,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            5,
            cancelled["operations"][0][
                "requested_operation_generation"
            ],
        )
        self.assertEqual(
            "update-split-alpha-5",
            cancelled["operations"][0]["update_id"],
        )
        self.assertEqual(
            owner_update_id,
            cancelled["operations"][0][
                "operation_console_execution_owner_update_id"
            ],
        )

    def test_operation_timeline_accepts_delayed_execution_owner_telemetry(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-delayed-owner-telemetry"
        owner_update_id = "update-delayed-alpha-3"

        reducer.observe(
            semantic_operation_payload(
                operation_id="delayed-alpha",
                generation=2,
                requested_generation=3,
                frame=100,
            ),
            blackboard_scope_id=scope_id,
        )
        latest_request = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="delayed-alpha",
                generation=2,
                requested_generation=4,
                frame=101,
                operation_edit={
                    "action": "reinforce",
                    "resolution": "blocked",
                    "blocker": "latest_intent_waiting",
                },
            ),
            execution_owner_update_id=owner_update_id,
        )
        reducer.observe(
            latest_request,
            blackboard_scope_id=scope_id,
        )

        delayed_owner_telemetry = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="delayed-alpha",
                generation=2,
                requested_generation=3,
                frame=102,
                movement=True,
                engagement=True,
            ),
            execution_owner_update_id=owner_update_id,
        )
        result = reducer.observe(
            delayed_owner_telemetry,
            blackboard_scope_id=scope_id,
        )

        operation = result["operations"][0]
        self.assertEqual(102, operation["telemetry_frame"])
        self.assertEqual(4, operation["requested_operation_generation"])
        self.assertEqual("update-delayed-alpha-4", operation["update_id"])
        self.assertEqual(
            "latest_intent_waiting",
            operation["operation_edit"]["blocker"],
        )
        self.assertEqual(
            owner_update_id,
            operation["operation_console_execution_owner_update_id"],
        )
        self.assertTrue(
            operation["battlefield_operation"]["operation_completion"][
                "movement_observed"
            ]
        )
        self.assertTrue(
            operation["battlefield_operation"]["operation_completion"][
                "engagement_observed"
            ]
        )
        self.assertTrue(
            {"movement_observed", "engagement_observed"}.issubset(
                {
                    event["kind"]
                    for event in result["operation_events"]
                }
            )
        )

    def test_operation_timeline_retirement_keeps_identity_tombstone(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        reducer._PER_SCOPE_OPERATION_RETENTION = 1
        scope_id = "scope-retired-family-identity"

        reducer.observe(
            semantic_operation_payload(
                operation_id="retired-alpha",
                generation=1,
                frame=100,
            ),
            blackboard_scope_id=scope_id,
        )
        active = reducer.observe(
            semantic_operation_payload(
                operation_id="active-beta",
                generation=1,
                frame=101,
            ),
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            ["active-beta"],
            [item["operation_id"] for item in active["operations"]],
        )
        self.assertIn(
            (scope_id, "1700000000000", "retired-alpha"),
            reducer._retired_operation_identities,
        )

        retired_replay = semantic_operation_payload(
            operation_id="retired-alpha",
            generation=1,
            frame=102,
            movement=True,
        )
        active_advance = semantic_operation_payload(
            operation_id="active-beta",
            generation=1,
            frame=102,
            movement=True,
        )
        combined = deepcopy(active_advance)
        combined["operations"].insert(
            0,
            retired_replay["operations"][0],
        )
        combined["battlefield_overview"][
            "operation_ownership"
        ].insert(
            0,
            retired_replay["battlefield_overview"][
                "operation_ownership"
            ][0],
        )
        advanced_active = reducer.observe(
            combined,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            ["active-beta"],
            [
                item["operation_id"]
                for item in advanced_active["operations"]
            ],
        )
        self.assertEqual(
            102,
            advanced_active["operations"][0]["telemetry_frame"],
        )
        self.assertIn(
            "movement_observed",
            {
                event["kind"]
                for event in advanced_active["operation_events"]
                if event["operation_id"] == "active-beta"
            },
        )
        self.assertEqual(
            ["active-beta"],
            [
                item["operation_id"]
                for item in advanced_active["battlefield_overview"][
                    "operation_ownership"
                ]
            ],
        )
        self.assertEqual(
            4,
            advanced_active["battlefield_overview"][
                "explicit_operation_owned_count"
            ],
        )

        foreign = set_semantic_operation_identity(
            semantic_operation_payload(
                operation_id="retired-alpha",
                generation=1,
                frame=103,
                movement=True,
            ),
            request_update_id="foreign-retired-alpha",
            execution_owner_update_id="foreign-retired-alpha",
        )
        rejected = reducer.observe(
            foreign,
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            ["active-beta"],
            [item["operation_id"] for item in rejected["operations"]],
        )

        advanced = reducer.observe(
            semantic_operation_payload(
                operation_id="retired-alpha",
                generation=2,
                frame=104,
                movement=True,
            ),
            blackboard_scope_id=scope_id,
        )
        self.assertEqual(
            2,
            advanced["operations"][0]["operation_generation"],
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

    def test_operation_timeline_epoch_high_water_survives_scope_churn(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-retired-session-history"
        reducer.observe(
            semantic_operation_payload(
                frame=111,
                session_epoch=111,
            ),
            blackboard_scope_id=scope_id,
        )
        reducer.observe(
            semantic_operation_payload(
                frame=10,
                session_epoch=222,
            ),
            blackboard_scope_id=scope_id,
        )
        for index in range(
            reducer._SCOPE_EPOCH_HISTORY_RETENTION + 5
        ):
            reducer.observe(
                semantic_operation_payload(
                    operation_id=f"history-operation-{index}",
                    frame=20 + index,
                    session_epoch=1000 + index,
                ),
                blackboard_scope_id=f"history-scope-{index}",
            )

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

        self.assertEqual(
            222,
            delayed["battlefield_overview"]["identity"][
                "session_epoch"
            ],
        )
        self.assertEqual(
            10,
            delayed["operations"][0]["telemetry_frame"],
        )
        self.assertNotIn(
            "completed",
            [event["kind"] for event in delayed["operation_events"]],
        )
        self.assertEqual(
            "222",
            reducer._scope_epoch_history[scope_id],
        )

    def test_operation_timeline_opaque_epoch_history_fails_closed_when_full(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        reducer._RETIRED_OPAQUE_EPOCH_RETENTION = 2
        scope_id = "scope-opaque-epoch-history"

        reducer.observe(
            semantic_operation_payload(
                frame=100,
                session_epoch="epoch-alpha",
            ),
            blackboard_scope_id=scope_id,
        )
        reducer.observe(
            semantic_operation_payload(
                frame=10,
                session_epoch="epoch-beta",
            ),
            blackboard_scope_id=scope_id,
        )
        current = reducer.observe(
            semantic_operation_payload(
                frame=1,
                session_epoch="epoch-gamma",
            ),
            blackboard_scope_id=scope_id,
        )

        rejected = reducer.observe(
            semantic_operation_payload(
                frame=1,
                session_epoch="epoch-delta",
                movement=True,
            ),
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(
            current["operation_event_latest_seq"],
            rejected["operation_event_latest_seq"],
        )
        self.assertEqual("epoch-gamma", reducer._scope_epochs[scope_id])
        self.assertEqual(
            ["epoch-alpha", "epoch-beta"],
            list(reducer._retired_scope_epochs[scope_id]),
        )
        self.assertIn(scope_id, reducer._opaque_epoch_history_saturated)

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

        mismatched_update = semantic_operation_payload(
            execution_state="completed",
            terminal=True,
        )
        mismatched_update["operations"][0]["battlefield_operation"][
            "identity"
        ]["update_id"] = "different-update"
        cases.append(mismatched_update)

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

    def test_operation_timeline_never_emits_completed_for_negative_terminal_states(
        self,
    ):
        for index, (terminal_state, disposition) in enumerate(
            (
                ("failed", "blocked"),
                ("cancelled", "superseded"),
                ("expired", "expired"),
                ("superseded", "superseded"),
            )
        ):
            with self.subTest(terminal_state=terminal_state):
                payload = semantic_operation_payload(
                    operation_id=f"negative-{terminal_state}",
                    frame=500 + index,
                    terminal=True,
                    disposition=disposition,
                )
                projection = payload["operations"][0][
                    "battlefield_operation"
                ]
                projection["operation_lifetime"]["completion_state"] = (
                    terminal_state
                )
                projection["operation_lifetime"]["completion_reason"] = (
                    f"canonical_{terminal_state}"
                )
                projection["operation_completion"]["state"] = terminal_state
                projection["operation_completion"]["reason"] = (
                    f"canonical_{terminal_state}"
                )

                result = web_gui._OperationSemanticTimelineReducer().observe(
                    payload,
                    blackboard_scope_id=(
                        f"negative-terminal-{terminal_state}"
                    ),
                )
                kinds = [
                    event["kind"] for event in result["operation_events"]
                ]

                self.assertNotIn("completed", kinds)

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

    def test_operation_timeline_preserves_new_execution_owner_for_stale_intent(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-stale-intent-new-execution"
        accepted = semantic_operation_payload(
            generation=1,
            requested_generation=4,
            frame=100,
            operation_edit={
                "action": "reinforce",
                "resolution": "blocked",
                "blocker": "latest_intent_blocker",
            },
        )
        accepted["operations"][0]["update"] = {
            "update_id": "update-flank-alpha-4",
            "vector": {
                "operation_id": "flank-alpha",
                "generation": 1,
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "yamato_cannon",
                },
            },
        }
        reducer.observe(
            accepted,
            blackboard_scope_id=scope_id,
        )

        incoming = semantic_operation_payload(
            generation=2,
            requested_generation=3,
            frame=101,
            execution_state="completed",
            movement=True,
            engagement=True,
            target_reached=True,
            terminal=True,
            operation_edit={
                "action": "reinforce",
                "resolution": "blocked",
                "blocker": "stale_intent_blocker",
            },
        )
        incoming_operation = incoming["operations"][0]
        execution_owner_update_id = incoming_operation["update_id"]
        incoming_operation["update"] = {
            "update_id": execution_owner_update_id,
            "vector": {
                "operation_id": "flank-alpha",
                "generation": 2,
                "tactical_task": {
                    "task_type": "execute_ability",
                    "ability": "tactical_nuke",
                },
            },
        }
        incoming_operation["family_evidence"] = [
            {
                "update_id": execution_owner_update_id,
                "operation_id": "flank-alpha",
                "generation": 2,
                "family": "ghost",
                "action": "ability:tactical_nuke",
                "required_effect": "ability_state_or_effect",
                "attempt_generation": 1,
                "attempted_count": 1,
                "attempted_frame": 100,
                "submitted_count": 0,
                "effect_count": 0,
                "blocker_manager": "CombatCommander",
                "blocker": "no_valid_nuke_target",
                "stage": "blocked",
            }
        ]
        incoming_execution = incoming_operation["intervention"][
            "command_execution"
        ]
        incoming_execution.update(
            {
                "state": "failed",
                "failed": True,
                "blocker_manager": "CombatCommander",
                "blocker_reason": "no_valid_nuke_target",
            }
        )
        result = reducer.observe(
            incoming,
            blackboard_scope_id=scope_id,
        )

        operation = result["operations"][0]
        self.assertEqual(4, operation["requested_operation_generation"])
        self.assertEqual(
            "update-flank-alpha-4",
            operation["update_id"],
        )
        self.assertEqual(
            execution_owner_update_id,
            operation[
                "operation_console_execution_owner_update_id"
            ],
        )
        self.assertEqual(
            "yamato_cannon",
            operation["update"]["vector"]["tactical_task"]["ability"],
        )
        self.assertEqual(
            "tactical_nuke",
            operation["operation_console_execution_owner_vector"][
                "tactical_task"
            ]["ability"],
        )
        self.assertEqual(
            execution_owner_update_id,
            operation["battlefield_operation"]["identity"]["update_id"],
        )
        self.assertIn(
            "completed",
            [event["kind"] for event in operation["semantic_timeline"]],
        )
        critical_failures = [
            event
            for event in operation["semantic_timeline"]
            if event["kind"] == "critical_ability_failure"
        ]
        self.assertEqual(1, len(critical_failures))
        self.assertEqual(
            execution_owner_update_id,
            critical_failures[0]["update_id"],
        )
        self.assertEqual(
            "tactical_nuke",
            critical_failures[0]["technical"]["ability"],
        )

        wrong_owner_reducer = web_gui._OperationSemanticTimelineReducer()
        wrong_owner_reducer.observe(
            accepted,
            blackboard_scope_id=scope_id,
        )
        wrong_owner = deepcopy(incoming)
        wrong_owner["operations"][0]["family_evidence"][0]["update_id"] = (
            "update-flank-alpha-4"
        )
        wrong_owner_result = wrong_owner_reducer.observe(
            wrong_owner,
            blackboard_scope_id=scope_id,
        )
        self.assertNotIn(
            "critical_ability_failure",
            [
                event["kind"]
                for event in wrong_owner_result["operation_events"]
            ],
        )

    def test_operation_timeline_force_loss_requires_matching_projection_identity(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-force-loss-projection-identity"
        baseline = reducer.observe(
            semantic_operation_payload(
                operation_id="identity-alpha",
                generation=2,
                frame=200,
                owner_count=4,
                required_count=4,
            ),
            blackboard_scope_id=scope_id,
        )
        baseline_seq = baseline["operation_event_latest_seq"]
        mismatched = semantic_operation_payload(
            operation_id="identity-alpha",
            generation=2,
            frame=999,
            owner_count=2,
            required_count=4,
        )
        projection = mismatched["operations"][0]["battlefield_operation"]
        projection["identity"]["update_id"] = "stale-projection-update"
        canonical = semantic_operation_payload(
            operation_id="identity-alpha",
            generation=2,
            frame=201,
            owner_count=2,
            required_count=4,
        )
        self.assertTrue(
            reducer._snapshot_operations_are_monotonic(
                [
                    mismatched["operations"][0],
                    canonical["operations"][0],
                ],
                scope_id=scope_id,
                session_epoch="1700000000000",
            )
        )

        result = reducer.observe(
            mismatched,
            blackboard_scope_id=scope_id,
        )

        self.assertNotIn(
            "force_loss",
            [event["kind"] for event in result["operation_events"]],
        )
        mismatch_events = [
            event
            for event in result["operation_events"]
            if event["timeline_seq"] > baseline_seq
        ]
        self.assertNotIn(
            "partially_assigned",
            [event["kind"] for event in mismatch_events],
        )
        self.assertTrue(
            all(event["game_frame"] <= 200 for event in mismatch_events)
        )
        family_key = (
            scope_id,
            "1700000000000",
            "identity-alpha",
        )
        self.assertEqual(200, reducer._family_last_frame[family_key])
        canonical_result = reducer.observe(
            canonical,
            blackboard_scope_id=scope_id,
        )
        force_losses = [
            event
            for event in canonical_result["operation_events"]
            if event["kind"] == "force_loss"
        ]
        self.assertEqual(1, len(force_losses))
        self.assertEqual(
            4,
            force_losses[0]["technical"]["previous_owner_count"],
        )
        self.assertEqual(2, force_losses[0]["owner_count"])

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

        accepted_payload = set_semantic_operation_identity(
            semantic_operation_payload(
                requested_generation=5,
                frame=101,
                operation_edit={
                    "action": "reinforce",
                    "resolution": "blocked",
                    "blocker": "awaiting_reinforcement",
                },
            ),
            execution_owner_update_id="update-flank-alpha-4",
        )
        accepted = reducer.observe(
            accepted_payload,
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

    def test_operation_timeline_retains_family_tombstones_past_1024(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        original_scope = "scope-family-history-original"
        original_payload = semantic_operation_payload(
            operation_id="original-operation",
            frame=100,
            session_epoch=1000,
        )
        first = reducer.observe(
            original_payload,
            blackboard_scope_id=original_scope,
        )
        first_seq = first["operation_event_latest_seq"]

        for scope_index in range(17):
            scope_id = f"scope-family-history-{scope_index}"
            payload = semantic_operation_payload(
                operation_id=f"operation-{scope_index}-0",
                frame=200 + scope_index,
                session_epoch=2000 + scope_index,
            )
            for operation_index in range(
                1,
                reducer._PER_SCOPE_OPERATION_RETENTION,
            ):
                operation_payload = semantic_operation_payload(
                    operation_id=(
                        f"operation-{scope_index}-{operation_index}"
                    ),
                    frame=200 + scope_index + operation_index,
                    session_epoch=2000 + scope_index,
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
            reducer.observe(payload, blackboard_scope_id=scope_id)

        self.assertGreater(len(reducer._family_order), 1024)
        self.assertLessEqual(
            len(reducer._family_order),
            reducer._GLOBAL_OPERATION_HISTORY_RETENTION,
        )
        replayed = reducer.observe(
            deepcopy(original_payload),
            blackboard_scope_id=original_scope,
        )

        self.assertEqual([], replayed["operation_events"])
        self.assertEqual(
            first_seq,
            replayed["operation_event_latest_seq"],
        )

    def test_operation_timeline_rejects_capacity_plus_one_without_reviving_oldest(
        self,
    ):
        reducer = web_gui._OperationSemanticTimelineReducer()
        scope_id = "scope-family-history-capacity"
        session_epoch = "epoch-family-history-capacity"
        original_payload = semantic_operation_payload(
            operation_id="oldest-operation",
            frame=100,
            session_epoch=session_epoch,
        )
        first = reducer.observe(
            original_payload,
            blackboard_scope_id=scope_id,
        )
        first_seq = first["operation_event_latest_seq"]

        for index in range(
            1,
            reducer._GLOBAL_OPERATION_HISTORY_RETENTION,
        ):
            family_key = (
                scope_id,
                session_epoch,
                f"retired-operation-{index}",
            )
            reducer._family_order.append(family_key)
            reducer._generation_high_water[family_key] = 1
            reducer._requested_generation_high_water[family_key] = 1
            reducer._family_last_frame[family_key] = index
            reducer._retired_operation_identities[family_key] = {
                "operation_id": family_key[2],
                "operation_generation": 1,
                "requested_operation_generation": 1,
                "update_id": f"retired-update-{index}",
                "operation_console_execution_owner_update_id": "",
            }

        overflow = reducer.observe(
            semantic_operation_payload(
                operation_id="capacity-plus-one",
                frame=200,
                session_epoch=session_epoch,
            ),
            blackboard_scope_id=scope_id,
        )
        replayed = reducer.observe(
            deepcopy(original_payload),
            blackboard_scope_id=scope_id,
        )

        self.assertEqual(
            "operation_history_capacity_rejected",
            overflow["status"],
        )
        self.assertFalse(overflow["enabled"])
        self.assertEqual([], overflow["operation_events"])
        self.assertEqual(
            reducer._GLOBAL_OPERATION_HISTORY_RETENTION,
            len(reducer._family_order),
        )
        self.assertEqual(
            first["operation_events"],
            replayed["operation_events"],
        )
        self.assertEqual(
            first_seq,
            replayed["operation_event_latest_seq"],
        )

    def test_operation_timeline_scope_history_capacity_fails_closed(self):
        reducer = web_gui._OperationSemanticTimelineReducer()
        capacity = reducer._SCOPE_EPOCH_HISTORY_RETENTION
        for index in range(capacity):
            result = reducer.observe(
                semantic_operation_payload(
                    operation_id=f"bounded-operation-{index}",
                    frame=100 + index,
                    session_epoch=1000 + index,
                ),
                blackboard_scope_id=f"bounded-scope-{index}",
            )
            self.assertNotEqual(
                "scope_capacity_rejected",
                result.get("operation_timeline_status"),
            )

        overflow = reducer.observe(
            semantic_operation_payload(
                operation_id="overflow-operation",
                frame=999,
                session_epoch=9999,
            ),
            blackboard_scope_id="bounded-scope-overflow",
        )

        self.assertEqual(
            "scope_capacity_rejected",
            overflow["operation_timeline_status"],
        )
        self.assertEqual(
            "scope_capacity_rejected",
            overflow["status"],
        )
        self.assertFalse(overflow["enabled"])
        self.assertIn("capacity is exhausted", overflow["error"])
        self.assertFalse(overflow["operation_registry_authoritative"])
        self.assertEqual([], overflow["operations"])
        self.assertEqual(capacity, len(reducer._scope_epoch_history))
        self.assertEqual(capacity, len(reducer._scope_epoch_history_order))
        self.assertEqual(capacity, len(reducer._scope_event_high_water))
        self.assertEqual(
            capacity,
            len(reducer._scope_battlefield_overviews),
        )
        self.assertNotIn(
            "bounded-scope-overflow",
            reducer._scope_epoch_history,
        )

    def test_operation_timeline_cursor_survives_numeric_and_opaque_scope_lru(
        self,
    ):
        for session_epoch in (1700000000000, "epoch-live"):
            with self.subTest(session_epoch=session_epoch):
                reducer = web_gui._OperationSemanticTimelineReducer()
                scope_id = f"scope-lru-{session_epoch}"
                first = reducer.observe(
                    semantic_operation_payload(
                        frame=100,
                        session_epoch=session_epoch,
                    ),
                    blackboard_scope_id=scope_id,
                )
                first_seq = first["operation_event_latest_seq"]
                for index in range(reducer._SCOPE_RETENTION):
                    reducer.observe(
                        semantic_operation_payload(
                            operation_id=f"other-operation-{index}",
                            frame=200 + index,
                            session_epoch=2000 + index,
                        ),
                        blackboard_scope_id=f"other-scope-{index}",
                    )
                self.assertNotIn(scope_id, reducer._scope_events)

                replayed = reducer.observe(
                    semantic_operation_payload(
                        frame=100,
                        session_epoch=session_epoch,
                    ),
                    blackboard_scope_id=scope_id,
                )

                self.assertEqual([], replayed["operation_events"])
                self.assertEqual(
                    first_seq,
                    replayed["operation_event_latest_seq"],
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
        self.assertEqual(
            first["operation_event_latest_seq"],
            replayed["operation_event_latest_seq"],
        )

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
        with self.assertRaises(TypeError):
            bridge.submit_correlated_command("상황 보고해줘", 123)
        with self.assertRaises(ValueError):
            bridge.submit_correlated_command("상황 보고해줘", "   ")
        with self.assertRaises(ValueError):
            bridge.submit_correlated_command(
                "상황 보고해줘",
                "x" * (web_gui.MAX_WEB_REQUEST_ID_CHARS + 1),
            )

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

    def test_correlated_commands_record_exact_request_identity(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)
        requests = (
            ("동일 명령", "pending-correlated-first"),
            ("동일 명령", "pending-correlated-second"),
        )
        for text, request_id in requests:
            bridge.submit_correlated_command(text, request_id)

        deadline = time.monotonic() + POLL_DEADLINE_SECONDS
        while time.monotonic() < deadline and bridge.latest_seq() < 2:
            time.sleep(POLL_INTERVAL_SECONDS)

        events = bridge.history_since(0)
        self.assertEqual(2, len(events))
        self.assertEqual(
            [request_id for _text, request_id in requests],
            [event["request_id"] for event in events],
        )
        self.assertEqual(
            [request_id for _text, request_id in requests],
            [
                event["detail"]["web_request_id"]
                for event in events
            ],
        )

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

    def test_async_publish_refreshes_frame_after_delayed_llm_compile(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
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
        publish_frame = {"value": 10}

        with tempfile.TemporaryDirectory() as directory:
            bridge.submit_micromachine_modulation_background(
                "탱크로 수비해",
                blackboard_dir=directory,
                current_frame=10,
                publish_frame_resolver=lambda: publish_frame["value"],
                update_id="fresh-publish-frame",
            )
            self.assertTrue(started.wait(1))
            publish_frame["value"] = 250
            release.set()

            deadline = time.monotonic() + 2
            latest = {}
            while time.monotonic() < deadline:
                path = os.path.join(directory, "latest_modulation.json")
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as handle:
                        latest = json.load(handle)
                    if latest.get("update_id") == "fresh-publish-frame":
                        break
                time.sleep(0.02)

        self.assertEqual("fresh-publish-frame", latest.get("update_id"))
        self.assertEqual(250, latest.get("issued_at_frame"))
        self.assertGreater(latest.get("expires_at_frame", 0), 250)

    def test_async_publish_keeps_last_valid_frame_when_snapshot_is_unavailable(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
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
            bridge.submit_micromachine_modulation_background(
                "탱크로 수비해",
                blackboard_dir=directory,
                current_frame=100_000,
                publish_frame_resolver=lambda: None,
                update_id="retain-valid-frame",
            )
            self.assertTrue(started.wait(1))
            release.set()

            deadline = time.monotonic() + 2
            latest = {}
            while time.monotonic() < deadline:
                path = os.path.join(directory, "latest_modulation.json")
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as handle:
                        latest = json.load(handle)
                    if latest.get("update_id") == "retain-valid-frame":
                        break
                time.sleep(0.02)

        self.assertEqual("retain-valid-frame", latest.get("update_id"))
        self.assertEqual(100_000, latest.get("issued_at_frame"))
        self.assertGreater(latest.get("expires_at_frame", 0), 100_000)

    def test_publish_frame_resolver_error_is_not_recorded_as_provider_failure(self):
        session, _bot = build_dry_run_session()
        bridge = SessionLoopBridge(session=session)
        bridge.start()
        self.addCleanup(bridge.stop)

        def fail_frame_resolution() -> int:
            raise OSError("validated snapshot unavailable")

        with tempfile.TemporaryDirectory() as directory:
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
                current_frame=100,
                publish_frame_resolver=fail_frame_resolution,
                update_id="frame-resolution-error",
            )

            deadline = time.monotonic() + 2
            compile_result = {}
            while time.monotonic() < deadline:
                compile_result = (
                    web_gui._read_micromachine_compile_result(directory) or {}
                )
                if compile_result.get("update_id") == "frame-resolution-error":
                    break
                time.sleep(0.02)

            self.assertEqual("publish_failed", compile_result.get("status"))
            self.assertFalse(
                os.path.exists(
                    os.path.join(directory, "latest_telemetry.json")
                )
            )

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

    def test_forwards_explicit_micromachine_runtime_paths(self):
        server = WebGuiServer(bridge=self.bridge)
        server.configure_micromachine_runtime(
            script_path="/tmp/runtime/smoke_macos_local.sh",
            cwd="/tmp/runtime",
        )

        self.assertEqual(
            server._micromachine_launcher._script_path,
            "/tmp/runtime/smoke_macos_local.sh",
        )
        self.assertEqual(server._micromachine_launcher._cwd, "/tmp/runtime")
        self.assertFalse(
            server._micromachine_launcher._requires_source_provenance
        )

    def test_rejects_runtime_path_change_after_server_start(self):
        server = WebGuiServer(bridge=self.bridge, port=0)
        server.start()
        self.addCleanup(server.stop)

        with self.assertRaisesRegex(RuntimeError, "after server start"):
            server.configure_micromachine_runtime(
                script_path="/tmp/runtime/smoke_macos_local.sh",
                cwd="/tmp/runtime",
            )

    def test_plain_http_response_ignores_disconnected_client(self):
        handler = object.__new__(web_gui._WebGuiRequestHandler)
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock(
            side_effect=BrokenPipeError("client disconnected")
        )
        handler.wfile = mock.Mock()

        handler._send_body(
            HTTPStatus.OK,
            "application/json; charset=utf-8",
            b"{}",
        )

        handler.wfile.write.assert_not_called()

    def test_http_server_suppresses_expected_client_disconnects(self):
        server = object.__new__(web_gui._BridgedThreadingHTTPServer)

        with mock.patch.object(
            web_gui.ThreadingHTTPServer,
            "handle_error",
        ) as parent_handle_error:
            try:
                raise ConnectionResetError("client disconnected")
            except ConnectionResetError:
                server.handle_error(object(), ("127.0.0.1", 4321))

        parent_handle_error.assert_not_called()

    def test_http_server_reports_unexpected_request_errors(self):
        server = object.__new__(web_gui._BridgedThreadingHTTPServer)

        with mock.patch.object(
            web_gui.ThreadingHTTPServer,
            "handle_error",
        ) as parent_handle_error:
            try:
                raise RuntimeError("unexpected request failure")
            except RuntimeError:
                server.handle_error(object(), ("127.0.0.1", 4321))

        parent_handle_error.assert_called_once()

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

    def test_standalone_dry_run_wires_process_local_llm_control(self):
        source = inspect.getsource(web_gui.main)
        self.assertIn("LocalLLMControl", source)
        self.assertIn("HybridCommandInterpreter", source)
        self.assertIn("llm_control=llm_control", source)

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
