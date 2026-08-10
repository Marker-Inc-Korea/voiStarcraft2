"""Release-blocking Playwright gate for the Battlefield Commander cockpit."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import re
import selectors
import secrets
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Final


BROWSER_GATE_SCHEMA_VERSION: Final[int] = 1
BROWSER_GATE_PRODUCER: Final[str] = "battlefield-playwright-gate"
DEFAULT_BASELINE_DIR: Final[Path] = (
    Path(__file__).resolve().parents[1] / "tests" / "browser_baselines"
)
VIEWPORTS: Final[tuple[tuple[str, int, int], ...]] = (
    ("desktop", 1440, 1100),
    ("mobile", 390, 844),
)
VISUAL_DIFF_THRESHOLD: Final[float] = 0.01
PIXEL_CHANNEL_TOLERANCE: Final[int] = 12
STANDARD_OPERATION_ACTIONS: Final[tuple[str, ...]] = (
    "view",
    "revise",
    "reinforce",
    "retarget",
    "cancel",
)
_SHA_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_BUILD_RE: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_FIXTURE_READY_TIMEOUT_SECONDS: Final[float] = 20.0
_FIXTURE_STOP_TIMEOUT_SECONDS: Final[float] = 10.0
_FIXTURE_STAGING_ROOT: Final[Path] = Path("/tmp").resolve()
_FIXTURE_STAGING_ENTRY_LIMIT: Final[int] = 4096
_FIXTURE_STAGING_BYTE_LIMIT: Final[int] = 64 * 1024 * 1024
_FIXTURE_STAGING_CHUNK_SIZE: Final[int] = 1024 * 1024
_FIXTURE_GIT_RECORD_LIMIT: Final[int] = 4096
_GIT_EXECUTABLE: Final[Path] = Path("/usr/bin/git")
_GIT_OBJECT_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_IMPORT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".dll",
        ".dylib",
        ".pyc",
        ".pyd",
        ".pyo",
        ".so",
    }
)
_FIXTURE_BOOTSTRAP: Final[str] = (
    "import runpy,sys;"
    "root=sys.argv[1];script=sys.argv[2];"
    "sys.path.insert(0,root);"
    "sys.argv=[script,'serve-fixture'];"
    "runpy.run_path(script,run_name='__main__')"
)


def _projection(
    operation_id: str,
    generation: int,
    *,
    stage: str,
    completed: bool = False,
    blocker: str = "",
) -> dict[str, object]:
    return {
        "identity": {
            "update_id": f"browser-{operation_id}",
            "scope": f"operation:{operation_id}",
            "session_epoch": 1_700_000_000_000,
            "operation_id": operation_id,
            "generation": generation,
            "stage": stage,
            "game_frame": 480,
        },
        "operation_id": operation_id,
        "generation": generation,
        "operation_route": {
            "requested_route_type": "direct",
            "applied_route_type": "direct",
            "location_intent": "enemy_natural",
            "target_type": "enemy_expansion",
            "resolved_target_label": "enemy natural",
            "target_x": 120.0,
            "target_y": 44.0,
            "target_evidence": "semantic_anchor",
        },
        "operation_lifetime": {
            "mode": "until_completed",
            "completion_state": "completed" if completed else "active",
            "completion_conditions": ["target_reached", "cancelled_by_user"],
            "duration_seconds": 300,
            "issued_at_frame": 400,
            "deadline_frame": 4_700,
            "standing": False,
            "completed": completed,
            "completion_reason": "target_reached" if completed else "",
            "completed_frame": 480 if completed else 0,
        },
        "operation_ownership": {
            "owner_count": 4,
            "integrity_status": "valid",
        },
        "operation_launch_policy": {
            "min_units": 2,
            "max_units": 4,
            "allow_partial_requested": True,
            "strict_scope": True,
            "partial_launch_allowed": True,
            "partial_launch_safe": not blocker,
            "launch_count": 4 if not blocker else 2,
            "missing_count": 0 if not blocker else 2,
            "decision": "launch" if not blocker else "wait",
            "blocker": blocker,
            "recommended_choices": (
                [] if not blocker else ["wait_for_full_force"]
            ),
            "safety_evidence": {
                "evaluated_at_frame": 480,
                "protected_defense_minimum_respected": True,
                "source_operation_minimum_respected": True,
                "transfer_admission": "accepted",
                "emergency_preemption": "none",
            },
        },
        "operation_completion": {
            "movement_observed": stage in {"moving", "engaged", "completed"},
            "engagement_observed": stage in {"engaged", "completed"},
            "target_reached": completed,
            "terminal": completed,
            "state": "completed" if completed else "active",
            "reason": "target_reached" if completed else "",
            "frame": 480 if completed else 0,
            "generation": generation,
        },
    }


def _operation(
    operation_id: str,
    generation: int,
    lane: str,
) -> dict[str, object]:
    update_id = f"browser-{operation_id}"
    stage = {
        "planning": "assigned",
        "executing": "submitted",
        "completed": "completed",
        "waiting": "assigned",
    }[lane]
    completed = lane == "completed"
    blocker = "composition_prerequisites_pending" if lane == "waiting" else ""
    execution_state = {
        "planning": "queued_or_assigned",
        "executing": "action_issued",
        "completed": "action_issued",
        "waiting": "blocked",
    }[lane]
    stages = [
        {"name": "parsed", "ok": True},
        {"name": "reduced", "ok": True},
        {"name": "consumed_by_manager", "ok": True},
        {"name": "queued_or_assigned", "ok": True},
    ]
    if lane in {"executing", "completed"}:
        stages.extend(
            [
                {"name": "order_issued", "ok": True},
                {
                    "name": "action_issued",
                    "ok": True,
                    "evidence": {"submitted_count": 1},
                },
            ]
        )
    if completed:
        stages.append(
            {
                "name": "effect_observed",
                "ok": True,
                "evidence": {"movement_observed": True},
            }
        )
    return {
        "operation_id": operation_id,
        "operation_generation": generation,
        "requested_operation_generation": generation,
        "update_id": update_id,
        "operation_console_execution_owner_update_id": update_id,
        "operation_console_execution_owner_vector": {
            "operation_id": operation_id,
            "generation": generation,
            "composition_requirements": [
                {"unit_type": "TERRAN_MARINE", "count": 4}
            ],
            "tactical_task": {"task_type": "pressure_with_main_army"},
            "route_intent": {
                "route_type": "direct",
                "target_intent": "enemy_natural",
            },
        },
        "command_text": f"Execute {operation_id}",
        "operation_mission": "pressure",
        "transport_status": "published",
        "status": "published",
        "consumption_status": "consumed",
        "telemetry_frame": 480,
        "telemetry_current": True,
        "disposition": "completed" if completed else "active",
        "operation_convergence": {
            "target_count": 4,
            "represented_count": 4 if not blocker else 2,
            "missing_count": 0 if not blocker else 2,
            "blocker": blocker,
            "requirements": [],
            "prerequisite_integrity_status": "valid",
            "prerequisite_integrity_blockers": [],
        },
        "battlefield_projection_join": {
            "status": "matched",
            "reason": "",
            "update_id": update_id,
            "scope": f"operation:{operation_id}",
            "session_epoch": "1700000000000",
            "operation_id": operation_id,
            "generation": generation,
        },
        "battlefield_operation": _projection(
            operation_id,
            generation,
            stage=stage,
            completed=completed,
            blocker=blocker,
        ),
        "semantic_timeline": [
            {
                "timeline_seq": 1,
                "kind": "planned",
                "summary": f"{operation_id} planned",
                "game_frame": 420,
                "technical": {},
            }
        ],
        "update": {
            "update_id": update_id,
            "vector": {
                "goal": f"Execute {operation_id}",
                "operation_id": operation_id,
                "generation": generation,
                "tactical_task": {"task_type": "pressure_with_main_army"},
            },
        },
        "intervention": {
            "telemetry_frame": 480,
            "command_execution": {
                "command_id": update_id,
                "operation_id": operation_id,
                "operation_generation": generation,
                "state": execution_state,
                "completed": completed,
                "failed": lane == "waiting",
                "expired": False,
                "blocker_reason": blocker,
                "stages": stages,
            },
        },
    }


def _status_payload(operations: Sequence[Mapping[str, object]]) -> dict[str, object]:
    projections = [
        dict(operation["battlefield_operation"])  # type: ignore[index]
        for operation in operations
    ]
    return {
        "ok": True,
        "status": "published",
        "blackboard_dir": "/tmp/voi-browser-gate",
        "battlefield_projection_identity": {
            "update_id": "browser-overview",
            "scope": "battlefield",
            "session_epoch": 1_700_000_000_000,
            "generation": 9,
            "stage": "observed",
            "game_frame": 480,
        },
        "battlefield_projection_fingerprint": "e" * 64,
        "battlefield_overview": {
            "schema_version": 2,
            "authority": "micromachine_cpp",
            "identity": {
                "update_id": "browser-overview",
                "scope": "battlefield",
                "session_epoch": 1_700_000_000_000,
                "generation": 9,
                "stage": "observed",
                "game_frame": 480,
            },
            "eligible_combat_count": 18,
            "explicit_operation_owned_count": 16,
            "autonomous_owned_count": 2,
            "unassigned_count": 0,
            "duplicate_owner_count": 0,
            "operation_ownership": projections,
            "autonomous_ownership": [
                {
                    "owner_id": "squad:Base Defense",
                    "owner_count": 2,
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
            "bases": [],
            "transfer_availability": {
                "evaluated_at_frame": 480,
                "atomic_revalidation_required": True,
                "entries": [],
            },
        },
        "battlefield_projection_integrity": {
            "status": "valid",
            "blocker_count": 0,
        },
        "operation_registry_authoritative": True,
        "operations": [dict(operation) for operation in operations],
    }


class _BrowserFixtureBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._operations = [
            _operation("planning-alpha", 1, "planning"),
            _operation("assault-bravo", 2, "executing"),
            _operation("completed-charlie", 3, "completed"),
            _operation("waiting-delta", 4, "waiting"),
        ]
        self._submission_count = 0

    def submit_command(self, text: str) -> None:
        del text

    def state_snapshot(self) -> None:
        return None

    def history_since(self, seq: int) -> list[dict[str, object]]:
        del seq
        return []

    def latest_seq(self) -> int:
        return 0

    def llm_settings_snapshot(self) -> dict[str, object]:
        return {"configured": True, "provider": "fixture", "model": "fixture"}

    def configure_llm(
        self,
        provider: str,
        api_key: str,
        model: str = "",
    ) -> dict[str, object]:
        del provider, api_key, model
        return self.llm_settings_snapshot()

    def micromachine_blackboard_dir(self) -> str:
        return "/tmp/voi-browser-gate"

    def micromachine_status(
        self,
        *,
        blackboard_dir: str = "",
    ) -> dict[str, object]:
        del blackboard_dir
        with self._lock:
            return _status_payload(self._operations)

    def micromachine_status_detached(
        self,
        *,
        blackboard_dir: str = "",
    ) -> dict[str, object]:
        return self.micromachine_status(blackboard_dir=blackboard_dir)

    def micromachine_status_for_runtime(
        self,
        *,
        blackboard_dir: str = "",
        runtime_instance_id: str,
        telemetry_document: Mapping[str, object],
    ) -> dict[str, object]:
        del runtime_instance_id, telemetry_document
        return self.micromachine_status(blackboard_dir=blackboard_dir)

    def submit_micromachine_modulation(
        self,
        text: str,
        **kwargs: object,
    ) -> dict[str, object]:
        del kwargs
        with self._lock:
            self._submission_count += 1
            ordinal = self._submission_count
        if ordinal == 1:
            time.sleep(0.12)
        else:
            time.sleep(0.04)
        created = [
            _operation(f"voice-{ordinal}-recon", ordinal + 10, "planning"),
            _operation(f"voice-{ordinal}-attack", ordinal + 20, "executing"),
        ]
        for item in created:
            item["command_text"] = text
        with self._lock:
            self._operations.extend(created)
            return _status_payload(self._operations)

    def submit_micromachine_modulation_background(
        self,
        text: str,
        **kwargs: object,
    ) -> dict[str, object]:
        result = self.submit_micromachine_modulation(text, **kwargs)
        return {
            **result,
            "accepted": True,
            "queued": True,
            "async_publish": True,
        }


class _BrowserFixtureLauncher:
    def __init__(self, blackboard_dir: str) -> None:
        self.blackboard_dir = blackboard_dir
        self.runtime_instance_id = "f" * 32

    def snapshot(self, blackboard_dir: str = "") -> dict[str, object]:
        return dict(
            self.validated_snapshot(
                blackboard_dir=blackboard_dir,
            ).metadata
        )

    def validated_snapshot(
        self,
        *,
        blackboard_dir: str = "",
    ) -> object:
        from starcraft_commander.web_gui import (
            _MicroMachineValidatedRuntimeSnapshot,
        )

        root = blackboard_dir or self.blackboard_dir
        telemetry = {
            "protocol_version": 1,
            "frame": 480,
            "bot_name": "MicroMachine",
            "race": "Terran",
            "managers": {},
            "active_modulation_ids": [],
            "runtime_instance_id": self.runtime_instance_id,
        }
        return _MicroMachineValidatedRuntimeSnapshot(
            metadata={
                "enabled": True,
                "mode": "micromachine",
                "status": "connected",
                "blackboard_dir": root,
                "pid": 4242,
                "runtime_instance_id": self.runtime_instance_id,
                "runtime_attached": True,
                "telemetry_present": True,
                "telemetry_current_for_process": True,
                "telemetry_stale_or_detached": False,
                "telemetry_frame": 480,
            },
            telemetry_document=telemetry,
        )


_BROWSER_INIT_SCRIPT = r"""
(() => {
  window.__voiSpeechInstances = [];
  class FixtureSpeechRecognition {
    constructor() {
      this.lang = "ko-KR";
      this.interimResults = true;
      this.continuous = false;
      window.__voiSpeechInstances.push(this);
    }
    start() {
      if (this.onstart) { this.onstart(); }
    }
    stop() {
      if (this.onend) { this.onend(); }
    }
    abort() {
      if (this.onend) { this.onend(); }
    }
    emitFinal(text) {
      const result = [{ transcript: text }];
      result.isFinal = true;
      if (this.onresult) { this.onresult({ results: [result] }); }
    }
  }
  window.SpeechRecognition = FixtureSpeechRecognition;
  window.webkitSpeechRecognition = FixtureSpeechRecognition;
  window.SpeechSynthesisUtterance = function(text) { this.text = text; };
  window.speechSynthesis = {
    speaking: false,
    pending: false,
    speak() {},
    cancel() {}
  };
})();
"""


def _wait_for_cards(page: Any, count: int = 4) -> None:
    try:
        page.wait_for_function(
            "(count) => document.querySelectorAll('.operation-card').length >= count",
            arg=count,
            timeout=15_000,
        )
    except Exception as error:
        diagnostics = page.evaluate(
            """async () => {
              let response = {};
              try {
                const result = await fetch("/api/micromachine/status");
                response = {
                  status: result.status,
                  body: (await result.text()).slice(0, 4000)
                };
              } catch (fetchError) {
                response = { error: String(fetchError) };
              }
              return {
                cardCount: document.querySelectorAll(".operation-card").length,
                statusText:
                  document.getElementById("micromachine-status")?.textContent || "",
                response
              };
            }"""
        )
        raise AssertionError(
            "operation cards did not render: "
            + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        ) from error


def _tab_until(page: Any, selector: str, *, reverse: bool = False) -> None:
    key = "Shift+Tab" if reverse else "Tab"
    for _ in range(160):
        if page.evaluate(
            "(selector) => document.activeElement?.matches(selector) === true",
            selector,
        ):
            return
        page.keyboard.press(key)
    raise AssertionError(f"keyboard focus did not reach {selector}")


def _focused_outline(page: Any) -> dict[str, str]:
    return page.evaluate(
        """() => {
          const node = document.activeElement;
          const style = node ? getComputedStyle(node) : null;
          return {
            tag: node ? node.tagName : "",
            id: node ? node.id : "",
            outline: style ? style.outlineStyle : "",
            width: style ? style.outlineWidth : ""
          };
        }"""
    )


def _keyboard_journey(page: Any) -> dict[str, object]:
    page.locator("body").focus()
    _tab_until(page, "#command-input")
    page.keyboard.type("마린 정찰조와 공격조를 동시에 편성해")
    page.keyboard.press("Enter")
    _wait_for_cards(page, 6)
    _tab_until(page, ".operation-card[tabindex='0']")
    first_key = page.evaluate(
        "() => document.activeElement.getAttribute('data-operation-key')"
    )
    timeline_before = page.locator("#operation-timeline-selection").text_content()
    page.keyboard.press("ArrowDown")
    second_key = page.evaluate(
        "() => document.activeElement.getAttribute('data-operation-key')"
    )
    if not first_key or not second_key or first_key == second_key:
        raise AssertionError("operation lane navigation did not move focus")
    if "\ufffd" in first_key or "\ufffd" in second_key:
        raise AssertionError("operation DOM key contains a replacement character")
    focused_operation_id = page.evaluate(
        "() => document.activeElement.getAttribute('data-operation-id')"
    )
    page.keyboard.press("Enter")
    selected = page.evaluate(
        "() => document.activeElement.getAttribute('data-operation-selected')"
    )
    if selected != "true":
        raise AssertionError("keyboard-selected operation did not retain selection")
    timeline_after = page.locator("#operation-timeline-selection").text_content()
    if (
        not focused_operation_id
        or timeline_after == timeline_before
        or not str(timeline_after or "").startswith(f"{focused_operation_id}#")
    ):
        raise AssertionError(
            "keyboard selection did not update the focused operation timeline"
        )

    exercised: list[str] = []
    for action in ("view", "revise", "reinforce", "retarget", "cancel"):
        selector = (
            f".operation-card[data-operation-selected='true'] "
            f"[data-operation-action='{action}']"
        )
        _tab_until(page, selector)
        before = _focused_outline(page)
        if before["outline"] == "none" or before["width"] in {"", "0px"}:
            raise AssertionError(f"{action} control has no visible focus")
        page.keyboard.press("Enter")
        exercised.append(action)
        if action in {"revise", "reinforce", "retarget"}:
            if not page.locator("#command-input").input_value().strip():
                raise AssertionError(f"{action} did not update the command input")
            page.locator("#command-input").fill("")
        if action == "cancel":
            page.wait_for_timeout(180)

    page.locator("body").focus()
    _tab_until(page, "#tactical-radio-mute")
    page.keyboard.press("Space")
    if page.locator("#tactical-radio-mute").get_attribute("aria-pressed") != "true":
        raise AssertionError("keyboard mute did not update aria-pressed")
    return {
        "first_operation_key": first_key,
        "second_operation_key": second_key,
        "selected_operation_id": focused_operation_id,
        "timeline_selection": timeline_after,
        "actions": exercised,
        "focus": _focused_outline(page),
    }


def _voice_journey(page: Any) -> dict[str, object]:
    for ordinal in (1, 2):
        page.locator("body").focus()
        _tab_until(page, "#voice-button")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "(count) => window.__voiSpeechInstances.length >= count",
            arg=ordinal,
        )
        page.evaluate(
            """({ index, text }) => {
              window.__voiSpeechInstances[index].emitFinal(text);
            }""",
            {
                "index": ordinal - 1,
                "text": f"음성 병렬 명령 {ordinal}",
            },
        )
    try:
        page.wait_for_function(
            """() =>
              document.querySelectorAll('[data-operation-id^="voice-"]').length >= 4
              && document.querySelectorAll('.voice-session-entry').length === 1
            """,
            timeout=15_000,
        )
    except Exception as error:
        diagnostics = page.evaluate(
            """async () => {
              let response = {};
              try {
                const result = await fetch("/api/micromachine/status");
                response = {
                  status: result.status,
                  body: (await result.text()).slice(0, 4000)
                };
              } catch (fetchError) {
                response = { error: String(fetchError) };
              }
              return {
                voiceSessionCount:
                  document.querySelectorAll(".voice-session-entry").length,
                operationIds: Array.from(
                  document.querySelectorAll("[data-operation-id]")
                ).map(node => node.getAttribute("data-operation-id")),
                pendingIds: Array.from(
                  document.querySelectorAll("[data-pending-id]")
                ).map(node => node.getAttribute("data-pending-id")),
                response
              };
            }"""
        )
        raise AssertionError(
            "voice operation identities did not render: "
            + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        ) from error
    duplicate_pending = page.evaluate(
        """() => {
          const ids = Array.from(
            document.querySelectorAll('[data-pending-id]')
          ).map(node => node.getAttribute('data-pending-id')).filter(Boolean);
          return ids.length !== new Set(ids).size;
        }"""
    )
    if duplicate_pending:
        raise AssertionError("voice pending identities are duplicated")
    operation_ids = page.locator(
        '[data-operation-id^="voice-"]'
    ).evaluate_all(
        "nodes => nodes.map(node => node.getAttribute('data-operation-id'))"
    )
    if len(operation_ids) != len(set(operation_ids)):
        raise AssertionError("voice operations do not have independent identities")
    voice_session_nodes = page.locator(".voice-session-entry").count()
    if voice_session_nodes != 1:
        raise AssertionError("voice commands did not retain one aggregate surface")
    return {
        "voice_session_nodes": voice_session_nodes,
        "operation_ids": operation_ids,
        "duplicate_pending": duplicate_pending,
    }


def _structure_items(
    value: object,
    *,
    label: str,
) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise AssertionError(f"{label} visibility snapshot is invalid")
    return value


def _visibility_failure(details: Mapping[str, object]) -> str:
    if details.get("hidden") is True:
        return "hidden attribute or hidden ancestor"
    if details.get("display") == "none":
        return "display:none"
    if details.get("visibility") in {"hidden", "collapse"}:
        return f"visibility:{details['visibility']}"
    if details.get("content_visibility") == "hidden":
        return "content-visibility:hidden"
    if details.get("transparent") is True:
        return "zero opacity"
    try:
        width = float(details.get("width", 0))
        height = float(details.get("height", 0))
        client_rects = int(details.get("client_rects", 0))
    except (TypeError, ValueError):
        return "invalid rendered size"
    if width <= 0 or height <= 0 or client_rects <= 0:
        return (
            "zero-size rendering "
            f"(width={width:g}, height={height:g}, client_rects={client_rects})"
        )
    return ""


def _assert_visible_structure(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    lanes = _structure_items(snapshot.get("lanes"), label="operation lanes")
    if len(lanes) != 4:
        raise AssertionError(f"expected four operation lanes, got {len(lanes)}")
    for index, lane in enumerate(lanes):
        if not isinstance(lane, Mapping):
            raise AssertionError(f"operation lane {index} snapshot is invalid")
        failure = _visibility_failure(lane)
        if failure:
            raise AssertionError(f"operation lane {index} is not visible: {failure}")

    cards = _structure_items(snapshot.get("cards"), label="operation cards")
    if len(cards) < 4:
        raise AssertionError("expected at least four operation cards")
    stage_count = 0
    action_count = 0
    for card_index, card in enumerate(cards):
        if not isinstance(card, Mapping):
            raise AssertionError(f"operation card {card_index} snapshot is invalid")
        card_visibility = card.get("visibility")
        if not isinstance(card_visibility, Mapping):
            raise AssertionError(
                f"operation card {card_index} visibility snapshot is invalid"
            )
        failure = _visibility_failure(card_visibility)
        if failure:
            raise AssertionError(
                f"operation card {card_index} is not visible: {failure}"
            )

        stages = _structure_items(
            card.get("stages"),
            label=f"operation card {card_index} stages",
        )
        if len(stages) != 4:
            raise AssertionError(
                f"operation card {card_index} does not have four stages"
            )
        stage_count += len(stages)
        for stage_index, stage in enumerate(stages):
            if not isinstance(stage, Mapping):
                raise AssertionError(
                    f"operation card {card_index} stage {stage_index} "
                    "snapshot is invalid"
                )
            failure = _visibility_failure(stage)
            if failure:
                raise AssertionError(
                    f"operation card {card_index} stage {stage_index} "
                    f"is not visible: {failure}"
                )

        actions = _structure_items(
            card.get("actions"),
            label=f"operation card {card_index} actions",
        )
        action_names = [
            action.get("name") if isinstance(action, Mapping) else None
            for action in actions
        ]
        if action_names != list(STANDARD_OPERATION_ACTIONS):
            raise AssertionError(f"operation actions changed: {action_names!r}")
        action_count += len(actions)
        for action in actions:
            if not isinstance(action, Mapping):
                raise AssertionError(
                    f"operation card {card_index} action snapshot is invalid"
                )
            failure = _visibility_failure(action)
            if failure:
                raise AssertionError(
                    f"operation card {card_index} action "
                    f"{action.get('name')!r} is not visible: {failure}"
                )
    return {
        "lanes": len(lanes),
        "cards": len(cards),
        "stages": stage_count,
        "actions": action_count,
        "all_visible": True,
    }


def _structural_assertions(page: Any) -> dict[str, object]:
    visible_structure = page.evaluate(
        """() => {
          const visibility = (node) => {
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            let transparent = false;
            for (let current = node; current; current = current.parentElement) {
              if (parseFloat(getComputedStyle(current).opacity) <= 0) {
                transparent = true;
                break;
              }
            }
            return {
              hidden: node.hidden || Boolean(node.closest("[hidden]")),
              display: style.display,
              visibility: style.visibility,
              content_visibility: style.contentVisibility || "visible",
              transparent,
              width: rect.width,
              height: rect.height,
              client_rects: node.getClientRects().length
            };
          };
          return {
            lanes: Array.from(
              document.querySelectorAll("[data-operation-lane]")
            ).map(visibility),
            cards: Array.from(
              document.querySelectorAll(".operation-card")
            ).map(card => ({
              visibility: visibility(card),
              stages: Array.from(
                card.querySelectorAll(".operation-stage")
              ).map(visibility),
              actions: Array.from(
                card.querySelectorAll(
                  ".operation-card-actions [data-operation-action]"
                )
              ).map(action => ({
                name: action.getAttribute("data-operation-action"),
                ...visibility(action)
              }))
            }))
          };
        }"""
    )
    if not isinstance(visible_structure, Mapping):
        raise AssertionError("visible structure snapshot is invalid")
    structural = _assert_visible_structure(visible_structure)

    ids = page.locator("[id]").evaluate_all(
        "nodes => nodes.map(node => node.id)"
    )
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate DOM ID detected")
    published = page.locator(
        ".operation-card[data-operation-transport-status='published']"
        "[data-operation-execution-state='queued_or_assigned']"
    ).first
    if "executing" in published.inner_text().lower():
        raise AssertionError("published-only operation rendered as executing")
    overflow = page.evaluate(
        """() => ({
          document: document.documentElement.scrollWidth
            > document.documentElement.clientWidth + 1,
          body: document.body.scrollWidth > document.body.clientWidth + 1,
          cards: Array.from(document.querySelectorAll('.operation-card'))
            .some(node => node.scrollWidth > node.clientWidth + 1)
        })"""
    )
    if any(overflow.values()):
        raise AssertionError(f"horizontal overflow detected: {overflow!r}")
    body_text = page.locator("body").inner_text()
    for runtime_failure in (
        "런타임 시작 실패",
        "Runtime launch failed",
        "运行时启动失败",
    ):
        if runtime_failure in body_text:
            raise AssertionError(
                f"browser fixture exposed a runtime failure: {runtime_failure}"
            )
    return {
        **structural,
        "unique_ids": len(ids),
        "overflow": overflow,
    }


def _accessibility_assertions(page: Any) -> dict[str, object]:
    from axe_playwright_python.sync_playwright import Axe

    response = Axe().run(page)
    violations = response.response.get("violations", [])
    blockers = [
        violation
        for violation in violations
        if violation.get("impact") in {"serious", "critical"}
    ]
    if blockers:
        summary = [
            {
                "id": violation.get("id"),
                "impact": violation.get("impact"),
                "nodes": [
                    {
                        "target": node.get("target"),
                        "html": node.get("html"),
                        "failure_summary": node.get("failureSummary"),
                    }
                    for node in violation.get("nodes", [])
                ],
            }
            for violation in blockers
        ]
        raise AssertionError(
            "axe serious/critical violations detected: "
            + json.dumps(summary, sort_keys=True)
        )
    return {
        "serious_critical_count": 0,
        "violation_count": len(violations),
        "violations": violations,
    }


def _media_assertions(
    browser: Any,
    server_url: str,
) -> dict[str, object]:
    results: dict[str, object] = {}
    for name, kwargs in (
        ("reduced_motion", {"reduced_motion": "reduce"}),
        ("forced_colors", {"forced_colors": "active"}),
    ):
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            color_scheme="light",
            **kwargs,
        )
        try:
            context.add_init_script(_BROWSER_INIT_SCRIPT)
            page = context.new_page()
            page.goto(server_url, wait_until="domcontentloaded")
            _wait_for_cards(page)
            structural = _structural_assertions(page)
            if name == "reduced_motion":
                active = page.evaluate(
                    "() => matchMedia('(prefers-reduced-motion: reduce)').matches"
                )
                animated = page.evaluate(
                    """() => Array.from(document.querySelectorAll('*')).some(node => {
                      const style = getComputedStyle(node);
                      const values = [style.animationDuration, style.transitionDuration];
                      return values.some(value => value.split(',').some(item => {
                        const text = item.trim();
                        return text.endsWith('s') && parseFloat(text) > 0.02;
                      }));
                    })"""
                )
                if not active or animated:
                    raise AssertionError(
                        "reduced-motion context retained meaningful animation"
                    )
                results[name] = {"active": active, "animated": animated, **structural}
            else:
                page.locator(".operation-card[tabindex='0']").focus()
                outline = _focused_outline(page)
                active = page.evaluate(
                    "() => matchMedia('(forced-colors: active)').matches"
                )
                if (
                    not active
                    or outline["outline"] == "none"
                    or outline["width"] in {"", "0px"}
                ):
                    raise AssertionError(
                        "forced-colors context lost visible focus information"
                    )
                results[name] = {"active": active, "focus": outline, **structural}
        finally:
            context.close()
    return results


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return (
        struct.pack(">I", len(payload))
        + body
        + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
    )


def _write_rgba_png(
    path: Path,
    width: int,
    height: int,
    pixels: bytes,
) -> None:
    if len(pixels) != width * height * 4:
        raise ValueError("RGBA pixel payload size mismatch")
    scanlines = b"".join(
        b"\x00" + pixels[offset : offset + width * 4]
        for offset in range(0, len(pixels), width * 4)
    )
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(payload)


def _read_png(path: Path) -> tuple[int, int, bytes]:
    payload = path.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    offset = 8
    width = height = color_type = bit_depth = 0
    compressed = bytearray()
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(payload):
            raise ValueError("truncated PNG payload")
        data = payload[data_start:data_end]
        expected_crc = struct.unpack(">I", payload[data_end : data_end + 4])[0]
        if (binascii.crc32(kind + data) & 0xFFFFFFFF) != expected_crc:
            raise ValueError("PNG chunk checksum mismatch")
        if kind == b"IHDR":
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filtering,
                interlace,
            ) = struct.unpack(">IIBBBBB", data)
            if (
                bit_depth != 8
                or color_type not in {2, 6}
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise ValueError("unsupported PNG encoding")
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            break
        offset = data_end + 4
    channels = 4 if color_type == 6 else 3
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG decompressed size mismatch")
    previous = bytearray(stride)
    rgba = bytearray()
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        encoded = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        decoded = bytearray(stride)
        for index, value in enumerate(encoded):
            left = decoded[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                base = left + up - upper_left
                distance_left = abs(base - left)
                distance_up = abs(base - up)
                distance_upper_left = abs(base - upper_left)
                predictor = (
                    left
                    if distance_left <= distance_up
                    and distance_left <= distance_upper_left
                    else (
                        up
                        if distance_up <= distance_upper_left
                        else upper_left
                    )
                )
            else:
                raise ValueError("unsupported PNG filter")
            decoded[index] = (value + predictor) & 0xFF
        previous = decoded
        for index in range(0, stride, channels):
            rgba.extend(decoded[index : index + channels])
            if channels == 3:
                rgba.append(255)
    return width, height, bytes(rgba)


def _pixel_diff(
    expected_path: Path,
    actual_path: Path,
    diff_path: Path,
) -> float:
    expected_width, expected_height, expected = _read_png(expected_path)
    actual_width, actual_height, actual = _read_png(actual_path)
    if (expected_width, expected_height) != (actual_width, actual_height):
        raise AssertionError(
            "baseline size mismatch: "
            f"expected={(expected_width, expected_height)} "
            f"actual={(actual_width, actual_height)}"
        )
    diff_pixels = bytearray(len(expected))
    changed = 0
    for offset in range(0, len(expected), 4):
        channels = [
            abs(expected[offset + channel] - actual[offset + channel])
            for channel in range(4)
        ]
        is_changed = any(
            value > PIXEL_CHANNEL_TOLERANCE for value in channels
        )
        if is_changed:
            changed += 1
            diff_pixels[offset : offset + 4] = b"\xff\x00\x00\xff"
        else:
            diff_pixels[offset : offset + 4] = b"\x00\x00\x00\x00"
    total = expected_width * expected_height
    ratio = changed / total
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    _write_rgba_png(
        diff_path,
        expected_width,
        expected_height,
        bytes(diff_pixels),
    )
    return ratio


@dataclass(frozen=True)
class BrowserGateConfig:
    repository_sha: str
    build_identity: str
    artifact_dir: Path
    baseline_dir: Path = DEFAULT_BASELINE_DIR
    chromium_executable: Path | None = None
    candidate_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[1]
    )
    candidate_python: Path = field(
        default_factory=lambda: Path(sys.executable).resolve()
    )
    candidate_uid: int | None = None
    candidate_gid: int | None = None

    def __post_init__(self) -> None:
        if _SHA_RE.fullmatch(self.repository_sha) is None:
            raise ValueError("repository_sha must be an exact lowercase Git SHA")
        if _BUILD_RE.fullmatch(self.build_identity) is None:
            raise ValueError("build_identity must be a canonical sha256 identity")
        if self.chromium_executable is not None:
            executable = self.chromium_executable
            if (
                executable.is_symlink()
                or not executable.is_file()
                or not os.access(executable, os.X_OK)
            ):
                raise ValueError(
                    "chromium_executable must be a regular executable file"
                )
        candidate_root = self.candidate_root
        if (
            candidate_root.is_symlink()
            or not candidate_root.is_dir()
            or candidate_root.resolve() != candidate_root.absolute()
        ):
            raise ValueError("candidate_root must be an absolute regular directory")
        package_root = candidate_root / "starcraft_commander"
        if package_root.is_symlink() or not package_root.is_dir():
            raise ValueError("candidate package is missing or linked")
        for relative in (
            "starcraft_commander/__init__.py",
            "starcraft_commander/web_gui.py",
        ):
            candidate = candidate_root / relative
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"candidate source is missing or linked: {relative}")
        candidate_python = self.candidate_python
        if (
            candidate_python.is_symlink()
            or not candidate_python.is_file()
            or not os.access(candidate_python, os.X_OK)
        ):
            raise ValueError("candidate_python must be a regular executable file")
        if (self.candidate_uid is None) != (self.candidate_gid is None):
            raise ValueError("candidate UID and GID must be provided together")
        if self.candidate_uid is not None and (
            type(self.candidate_uid) is not int
            or type(self.candidate_gid) is not int
            or self.candidate_uid <= 0
            or self.candidate_gid <= 0
        ):
            raise ValueError("candidate UID and GID must be positive integers")


def _sha256_regular_file(path: Path) -> str:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"candidate source is missing or linked: {path}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            while chunk := stream.read(_FIXTURE_STAGING_CHUNK_SIZE):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeError(f"candidate source changed while reading: {path}")
    return digest.hexdigest()


class _CandidateFixtureProcess:
    def __init__(self, config: BrowserGateConfig) -> None:
        self._config = config
        self._process: subprocess.Popen[str] | None = None
        self._nonce = secrets.token_hex(32)
        self._stderr = bytearray()
        self._stdout_extra = bytearray()
        self._drain_lock = threading.Lock()
        self._drain_threads: list[threading.Thread] = []
        self._staged_fixture_script: Path | None = None
        self._staged_candidate_root: Path | None = None
        self._staged_fixture_manifest: dict[
            Path,
            tuple[str, int, str],
        ] = {}
        self._staged_fixture_cleanup_started = False
        self._staged_fixture_cleanup_verification_error: (
            BaseException | None
        ) = None
        self._candidate_web_gui_sha256: str | None = None

    def _command(
        self,
        fixture_script: Path | None = None,
        *,
        candidate_root: Path | None = None,
    ) -> list[str]:
        if fixture_script is None:
            if self._config.candidate_uid is not None:
                raise RuntimeError(
                    "dedicated candidate execution requires staged fixture source"
                )
            fixture_script = Path(__file__).resolve()
        if candidate_root is None:
            candidate_root = self._config.candidate_root
        command = [
            str(self._config.candidate_python),
            "-I",
            "-B",
            "-c",
            _FIXTURE_BOOTSTRAP,
            str(candidate_root),
            str(fixture_script),
        ]
        if self._config.candidate_uid is None:
            return command
        sudo = Path("/usr/bin/sudo")
        if not sudo.is_file() or not os.access(sudo, os.X_OK):
            raise RuntimeError("dedicated candidate execution requires /usr/bin/sudo")
        return [
            str(sudo),
            "--non-interactive",
            f"--user=#{self._config.candidate_uid}",
            f"--group=#{self._config.candidate_gid}",
            "--",
            *command,
        ]

    def _prepare_fixture_script(self) -> Path:
        if self._config.candidate_uid is None:
            return Path(__file__).resolve()
        if self._config.candidate_uid == os.geteuid():
            raise RuntimeError("candidate UID must differ from trusted verifier UID")
        if self._staged_fixture_script is not None:
            raise RuntimeError("candidate fixture source is already staged")
        if self._staged_candidate_root is not None:
            raise RuntimeError("candidate package source is already staged")
        self._candidate_web_gui_sha256 = None

        staging_root = _FIXTURE_STAGING_ROOT
        if (
            staging_root.is_symlink()
            or not staging_root.is_dir()
            or staging_root.resolve() != staging_root.absolute()
            or stat.S_IMODE(staging_root.stat().st_mode) & stat.S_IXOTH == 0
        ):
            raise RuntimeError("candidate fixture staging root is not traversable")

        staged_root = Path(
            tempfile.mkdtemp(
                dir=staging_root,
                prefix="voi-browser-fixture-",
            )
        )
        manifest: dict[Path, tuple[str, int, str]] = {
            Path("."): ("directory", 0, ""),
        }
        staged_script = staged_root / "fixture.py"
        self._staged_candidate_root = staged_root
        self._staged_fixture_script = staged_script
        self._staged_fixture_manifest = manifest
        total_bytes = 0
        try:
            source_size, source_digest = self._copy_staged_file(
                Path(__file__).resolve(),
                staged_script,
                byte_limit=_FIXTURE_STAGING_BYTE_LIMIT - total_bytes,
            )
            total_bytes += source_size
            manifest[Path("fixture.py")] = (
                "file",
                source_size,
                source_digest,
            )
            entries, directories = self._candidate_package_git_tree()
            entry_count = 1 + len(entries) + len(directories)
            if entry_count > _FIXTURE_STAGING_ENTRY_LIMIT:
                raise RuntimeError(
                    "candidate fixture staging entry limit exceeded"
                )
            for relative in sorted(
                directories,
                key=lambda path: len(path.parts),
            ):
                (staged_root / relative).mkdir(mode=0o755)
                manifest[relative] = ("directory", 0, "")
            staged_bytes, web_gui_digest = self._stage_candidate_git_blobs(
                staged_root,
                entries,
                manifest,
                byte_limit=_FIXTURE_STAGING_BYTE_LIMIT - total_bytes,
            )
            total_bytes += staged_bytes
            self._candidate_web_gui_sha256 = web_gui_digest

            for relative, (kind, _, _) in sorted(
                manifest.items(),
                key=lambda item: len(item[0].parts),
                reverse=True,
            ):
                if kind == "directory":
                    os.chmod(staged_root / relative, 0o555)
            self._verify_staged_fixture_tree(staged_root, manifest)
        except BaseException as prepare_error:
            self._candidate_web_gui_sha256 = None
            self._staged_fixture_cleanup_started = True
            self._staged_fixture_cleanup_verification_error = None
            try:
                self._discard_staged_fixture_tree(staged_root)
            except BaseException as cleanup_error:
                raise cleanup_error from prepare_error
            self._clear_staged_fixture_state()
            raise

        return staged_script

    @staticmethod
    def _candidate_git_environment() -> dict[str, str]:
        return {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "HOME": "/tmp",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        }

    def _candidate_git_command(self, *arguments: str) -> list[str]:
        candidate_root = str(self._config.candidate_root)
        return [
            str(_GIT_EXECUTABLE),
            "-c",
            f"safe.directory={candidate_root}",
            "-C",
            candidate_root,
            *arguments,
        ]

    def _run_candidate_git(self, *arguments: str) -> bytes:
        if (
            _GIT_EXECUTABLE.is_symlink()
            or not _GIT_EXECUTABLE.is_file()
            or not os.access(_GIT_EXECUTABLE, os.X_OK)
        ):
            raise RuntimeError("trusted Git executable is unavailable")
        result = subprocess.run(
            self._candidate_git_command(*arguments),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._candidate_git_environment(),
            timeout=30,
        )
        if result.returncode != 0:
            details = result.stderr[:400].decode("utf-8", errors="replace")
            raise RuntimeError(f"candidate Git tree query failed: {details}")
        return result.stdout

    def _candidate_package_git_tree(
        self,
    ) -> tuple[list[tuple[Path, str]], set[Path]]:
        repository_root = Path(
            self._run_candidate_git(
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
            )
            .decode("utf-8")
            .strip()
        )
        if repository_root.resolve() != self._config.candidate_root:
            raise RuntimeError("candidate root is not the Git repository root")
        head = (
            self._run_candidate_git("rev-parse", "--verify", "HEAD^{commit}")
            .decode("ascii")
            .strip()
        )
        expected = (
            self._run_candidate_git(
                "rev-parse",
                "--verify",
                f"{self._config.repository_sha}^{{commit}}",
            )
            .decode("ascii")
            .strip()
        )
        if (
            head != self._config.repository_sha
            or expected != self._config.repository_sha
        ):
            raise RuntimeError("candidate Git HEAD does not match repository SHA")

        entries: list[tuple[Path, str]] = []
        directories: set[Path] = set()
        observed: set[Path] = set()
        for record in self._candidate_git_tree_records():
            metadata, separator, raw_path = record.partition(b"\t")
            fields = metadata.split()
            if separator != b"\t" or len(fields) != 3:
                raise RuntimeError("candidate Git tree record is malformed")
            mode, object_type, raw_object_id = fields
            try:
                path_text = raw_path.decode("utf-8")
                object_id = raw_object_id.decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError(
                    "candidate Git tree contains a non-UTF-8 path or object"
                ) from error
            posix_path = PurePosixPath(path_text)
            if (
                posix_path.is_absolute()
                or not posix_path.parts
                or posix_path.parts[0] != "starcraft_commander"
                or any(part in {"", ".", ".."} for part in posix_path.parts)
            ):
                raise ValueError(
                    f"candidate Git tree path is unsafe: {path_text!r}"
                )
            relative = Path(*posix_path.parts)
            if relative in observed:
                raise RuntimeError("candidate Git tree contains a duplicate path")
            observed.add(relative)
            if (
                any(
                    part.casefold() == "__pycache__"
                    for part in posix_path.parts
                )
                or relative.suffix.lower() in _FORBIDDEN_IMPORT_SUFFIXES
            ):
                raise ValueError(
                    "candidate Git tree contains a forbidden import artifact: "
                    f"{relative}"
                )
            if (
                mode not in {b"100644", b"100755"}
                or object_type != b"blob"
                or _GIT_OBJECT_RE.fullmatch(object_id) is None
            ):
                raise ValueError(
                    "candidate Git tree contains a linked or non-regular entry: "
                    f"{relative}"
                )
            entries.append((relative, object_id))
            parent = relative.parent
            while parent != Path("."):
                directories.add(parent)
                parent = parent.parent
            if 1 + len(entries) + len(directories) > (
                _FIXTURE_STAGING_ENTRY_LIMIT
            ):
                raise RuntimeError(
                    "candidate fixture staging entry limit exceeded"
                )

        required = {
            Path("starcraft_commander/__init__.py"),
            Path("starcraft_commander/web_gui.py"),
        }
        if not required.issubset(observed):
            raise ValueError("candidate Git tree is missing required package source")
        return entries, directories

    def _candidate_git_tree_records(self) -> list[bytes]:
        with tempfile.TemporaryFile() as error_stream:
            process = subprocess.Popen(
                self._candidate_git_command(
                    "ls-tree",
                    "-r",
                    "-z",
                    "--full-tree",
                    self._config.repository_sha,
                    "--",
                    "starcraft_commander",
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=error_stream,
                env=self._candidate_git_environment(),
            )
            records: list[bytes] = []
            pending = bytearray()
            try:
                assert process.stdout is not None
                while chunk := process.stdout.read(_FIXTURE_GIT_RECORD_LIMIT):
                    pending.extend(chunk)
                    while True:
                        delimiter = pending.find(b"\0")
                        if delimiter < 0:
                            break
                        record = bytes(pending[:delimiter])
                        del pending[: delimiter + 1]
                        if not record:
                            raise RuntimeError(
                                "candidate Git tree record is empty"
                            )
                        if len(record) > _FIXTURE_GIT_RECORD_LIMIT:
                            raise RuntimeError(
                                "candidate Git tree record is oversized"
                            )
                        records.append(record)
                        if 1 + len(records) > _FIXTURE_STAGING_ENTRY_LIMIT:
                            raise RuntimeError(
                                "candidate fixture staging entry limit exceeded"
                            )
                    if len(pending) > _FIXTURE_GIT_RECORD_LIMIT:
                        raise RuntimeError(
                            "candidate Git tree record is oversized"
                        )
                if pending:
                    raise RuntimeError(
                        "candidate Git tree output is unterminated"
                    )
                return_code = process.wait(timeout=30)
                if return_code != 0:
                    error_stream.seek(0)
                    details = error_stream.read(400).decode(
                        "utf-8",
                        errors="replace",
                    )
                    raise RuntimeError(
                        f"candidate Git tree query failed: {details}"
                    )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
        return records

    def _stage_candidate_git_blobs(
        self,
        staged_root: Path,
        entries: Sequence[tuple[Path, str]],
        manifest: dict[Path, tuple[str, int, str]],
        *,
        byte_limit: int,
    ) -> tuple[int, str]:
        if byte_limit < 0:
            raise RuntimeError("candidate fixture staging byte limit exceeded")
        with tempfile.TemporaryFile() as error_stream:
            process = subprocess.Popen(
                self._candidate_git_command(
                    "cat-file",
                    "--batch",
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=error_stream,
                env=self._candidate_git_environment(),
            )
            total_bytes = 0
            web_gui_digest = ""
            operation_failed = False
            try:
                assert process.stdin is not None
                assert process.stdout is not None
                for relative, object_id in entries:
                    process.stdin.write(f"{object_id}\n".encode("ascii"))
                    process.stdin.flush()
                    header = process.stdout.readline(4097)
                    if len(header) > 4096 or not header.endswith(b"\n"):
                        raise RuntimeError(
                            "candidate Git blob header is malformed"
                        )
                    fields = header.rstrip(b"\n").split()
                    if (
                        len(fields) != 3
                        or fields[0] != object_id.encode("ascii")
                        or fields[1] != b"blob"
                    ):
                        raise RuntimeError(
                            "candidate Git blob identity changed"
                        )
                    try:
                        blob_size = int(fields[2])
                    except ValueError as error:
                        raise RuntimeError(
                            "candidate Git blob size is malformed"
                        ) from error
                    size, digest = self._copy_staged_git_blob(
                        process.stdout,
                        staged_root / relative,
                        object_id=object_id,
                        size=blob_size,
                        byte_limit=byte_limit - total_bytes,
                    )
                    if process.stdout.read(1) != b"\n":
                        raise RuntimeError(
                            "candidate Git blob delimiter is malformed"
                        )
                    total_bytes += size
                    manifest[relative] = ("file", size, digest)
                    if relative == Path("starcraft_commander/web_gui.py"):
                        web_gui_digest = digest
                process.stdin.close()
                return_code = process.wait(timeout=30)
                if return_code != 0:
                    error_stream.seek(0)
                    details = error_stream.read(400).decode(
                        "utf-8",
                        errors="replace",
                    )
                    raise RuntimeError(
                        f"candidate Git blob read failed: {details}"
                    )
            except BaseException:
                operation_failed = True
                raise
            finally:
                cleanup_error: BaseException | None = None
                try:
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()
                except BaseException as error:
                    cleanup_error = error
                finally:
                    try:
                        if process.poll() is None:
                            process.kill()
                    except BaseException as error:
                        if cleanup_error is None:
                            cleanup_error = error
                    finally:
                        try:
                            process.wait(timeout=5)
                        except BaseException as error:
                            if cleanup_error is None:
                                cleanup_error = error
                        finally:
                            try:
                                if process.stdout is not None:
                                    process.stdout.close()
                            except BaseException as error:
                                if cleanup_error is None:
                                    cleanup_error = error
                if not operation_failed and cleanup_error is not None:
                    raise cleanup_error
        if not web_gui_digest:
            raise RuntimeError("candidate web_gui Git blob was not staged")
        return total_bytes, web_gui_digest

    @staticmethod
    def _copy_staged_git_blob(
        source_stream: object,
        destination: Path,
        *,
        object_id: str,
        size: int,
        byte_limit: int,
    ) -> tuple[int, str]:
        if size < 0 or size > byte_limit:
            raise RuntimeError("candidate fixture staging byte limit exceeded")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            destination_stream = os.fdopen(destination_descriptor, "wb")
            destination_descriptor = -1
            sha256_digest = hashlib.sha256()
            git_digest = hashlib.sha1(usedforsecurity=False)
            git_digest.update(f"blob {size}\0".encode("ascii"))
            remaining = size
            try:
                while remaining:
                    chunk = source_stream.read(
                        min(_FIXTURE_STAGING_CHUNK_SIZE, remaining)
                    )
                    if not chunk:
                        raise RuntimeError(
                            "candidate Git blob ended before declared size"
                        )
                    remaining -= len(chunk)
                    sha256_digest.update(chunk)
                    git_digest.update(chunk)
                    destination_stream.write(chunk)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            finally:
                destination_stream.close()
            if git_digest.hexdigest() != object_id:
                raise RuntimeError("candidate Git blob failed object verification")
            os.chmod(destination, 0o444, follow_symlinks=False)
            return size, sha256_digest.hexdigest()
        except BaseException:
            if destination_descriptor >= 0:
                os.close(destination_descriptor)
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _copy_staged_file(
        source: Path | str,
        destination: Path,
        *,
        byte_limit: int,
        expected_snapshot: os.stat_result | None = None,
        source_dir_fd: int | None = None,
    ) -> tuple[int, str]:
        if byte_limit < 0:
            raise RuntimeError("candidate fixture staging byte limit exceeded")
        source_descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=source_dir_fd,
        )
        destination_descriptor = -1
        try:
            before = os.fstat(source_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(
                    f"candidate source is missing or linked: {source}"
                )
            if expected_snapshot is not None and (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                expected_snapshot.st_dev,
                expected_snapshot.st_ino,
                expected_snapshot.st_mode,
                expected_snapshot.st_size,
                expected_snapshot.st_mtime_ns,
            ):
                raise RuntimeError(
                    f"candidate source changed before reading: {source}"
                )
            if before.st_size > byte_limit:
                raise RuntimeError(
                    "candidate fixture staging byte limit exceeded"
                )
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            digest = hashlib.sha256()
            total_bytes = 0
            source_stream = os.fdopen(source_descriptor, "rb")
            source_descriptor = -1
            try:
                destination_stream = os.fdopen(destination_descriptor, "wb")
                destination_descriptor = -1
                try:
                    while chunk := source_stream.read(
                        _FIXTURE_STAGING_CHUNK_SIZE
                    ):
                        total_bytes += len(chunk)
                        if total_bytes > byte_limit:
                            raise RuntimeError(
                                "candidate fixture staging byte limit exceeded"
                            )
                        digest.update(chunk)
                        destination_stream.write(chunk)
                    after = os.fstat(source_stream.fileno())
                    if (
                        before.st_dev,
                        before.st_ino,
                        before.st_mode,
                        before.st_size,
                        before.st_mtime_ns,
                    ) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_mode,
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise RuntimeError(
                            f"candidate source changed while reading: {source}"
                        )
                    destination_stream.flush()
                    os.fsync(destination_stream.fileno())
                finally:
                    destination_stream.close()
            finally:
                source_stream.close()
            os.chmod(destination, 0o444, follow_symlinks=False)
            return total_bytes, digest.hexdigest()
        except BaseException:
            if destination_descriptor >= 0:
                os.close(destination_descriptor)
            destination.unlink(missing_ok=True)
            raise
        finally:
            if source_descriptor >= 0:
                os.close(source_descriptor)

    @staticmethod
    def _verify_staged_fixture_tree(
        staged_root: Path,
        manifest: Mapping[Path, tuple[str, int, str]],
    ) -> None:
        observed: set[Path] = set()
        stack = [staged_root]
        while stack:
            directory = stack.pop()
            relative_directory = directory.relative_to(staged_root)
            if relative_directory == Path("."):
                relative_directory = Path(".")
            observed.add(relative_directory)
            snapshot = directory.lstat()
            if (
                directory.is_symlink()
                or not stat.S_ISDIR(snapshot.st_mode)
                or snapshot.st_uid != os.geteuid()
                or stat.S_IMODE(snapshot.st_mode) != 0o555
            ):
                raise RuntimeError(
                    "candidate fixture staged directory failed integrity checks"
                )
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(staged_root)
                    snapshot = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(snapshot.st_mode):
                        raise RuntimeError(
                            "candidate fixture staged tree contains a symlink"
                        )
                    if stat.S_ISDIR(snapshot.st_mode):
                        stack.append(path)
                        continue
                    observed.add(relative)
                    expected = manifest.get(relative)
                    if (
                        expected is None
                        or expected[0] != "file"
                        or not stat.S_ISREG(snapshot.st_mode)
                        or snapshot.st_uid != os.geteuid()
                        or stat.S_IMODE(snapshot.st_mode) != 0o444
                    ):
                        raise RuntimeError(
                            "candidate fixture staged file failed integrity "
                            "checks"
                        )
                    if (
                        snapshot.st_size != expected[1]
                        or _sha256_regular_file(path) != expected[2]
                    ):
                        raise RuntimeError(
                            "candidate fixture staged bytes failed integrity "
                            "checks"
                        )
        if observed != set(manifest):
            raise RuntimeError("candidate fixture staged tree manifest changed")

    @staticmethod
    def _discard_staged_fixture_tree(staged_root: Path) -> None:
        pending = [(staged_root, False)]
        while pending:
            path, visited = pending.pop()
            try:
                snapshot = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(snapshot.st_mode) or not stat.S_ISDIR(
                snapshot.st_mode
            ):
                path.unlink()
                continue
            if visited:
                path.rmdir()
                continue
            os.chmod(path, 0o700, follow_symlinks=False)
            pending.append((path, True))
            with os.scandir(path) as entries:
                pending.extend((Path(entry.path), False) for entry in entries)

    def _cleanup_fixture_script(self) -> None:
        staged_root = self._staged_candidate_root
        staged_script = self._staged_fixture_script
        manifest = self._staged_fixture_manifest
        if staged_root is None and staged_script is None:
            return
        if staged_root is None or staged_script is None:
            raise RuntimeError("candidate fixture staging state is incomplete")
        if staged_script != staged_root / "fixture.py":
            raise RuntimeError("candidate fixture staged script path changed")
        verification_error = self._staged_fixture_cleanup_verification_error
        if not self._staged_fixture_cleanup_started:
            try:
                self._verify_staged_fixture_tree(staged_root, manifest)
            except BaseException as error:
                verification_error = error
            self._staged_fixture_cleanup_started = True
            self._staged_fixture_cleanup_verification_error = (
                verification_error
            )
        try:
            self._discard_staged_fixture_tree(staged_root)
        except BaseException as cleanup_error:
            if verification_error is not None:
                raise cleanup_error from verification_error
            raise
        self._clear_staged_fixture_state()
        if verification_error is not None:
            raise verification_error

    def _clear_staged_fixture_state(self) -> None:
        self._staged_candidate_root = None
        self._staged_fixture_script = None
        self._staged_fixture_manifest = {}
        self._staged_fixture_cleanup_started = False
        self._staged_fixture_cleanup_verification_error = None

    def candidate_web_gui_sha256(self) -> str:
        if self._candidate_web_gui_sha256 is not None:
            return self._candidate_web_gui_sha256
        return _sha256_regular_file(
            self._config.candidate_root
            / "starcraft_commander"
            / "web_gui.py"
        )

    def start(self) -> str:
        if self._process is not None:
            raise RuntimeError("candidate fixture process already started")
        environment = {
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        fixture_script = self._prepare_fixture_script()
        candidate_root = (
            self._staged_candidate_root or self._config.candidate_root
        )
        try:
            process = subprocess.Popen(
                self._command(
                    fixture_script,
                    candidate_root=candidate_root,
                ),
                cwd=candidate_root,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except BaseException:
            self._cleanup_fixture_script()
            raise
        self._process = process
        try:
            assert process.stdin is not None
            process.stdin.write(
                json.dumps(
                    {
                        "candidate_sha": self._config.repository_sha,
                        "nonce": self._nonce,
                        "schema_version": 1,
                        "type": "battlefield-webgui-start",
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            process.stdin.close()
            assert process.stdout is not None
            assert process.stderr is not None
            self._start_drain(process.stderr, self._stderr, "candidate-stderr")
            return self._wait_until_ready(process)
        except BaseException:
            try:
                self.stop()
            finally:
                raise

    def _wait_until_ready(self, process: subprocess.Popen[str]) -> str:
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + _FIXTURE_READY_TIMEOUT_SECONDS
        line = ""
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                ready = selector.select(
                    timeout=min(0.1, max(0.0, deadline - time.monotonic()))
                )
                if ready:
                    line = process.stdout.readline(4097)
                    break
        finally:
            selector.close()
        if len(line.encode("utf-8")) > 4096:
            raise RuntimeError("candidate fixture readiness line is oversized")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            details = self._stderr_tail()
            raise RuntimeError(
                f"candidate fixture did not publish readiness: {details}"
            ) from error
        origin = payload.get("origin") if isinstance(payload, dict) else None
        parsed = urllib.parse.urlsplit(origin if isinstance(origin, str) else "")
        try:
            parsed_port = parsed.port
        except ValueError:
            parsed_port = None
        readiness_process_matches = (
            isinstance(payload, dict)
            and self._readiness_process_matches(
                process,
                payload.get("pid"),
            )
        )
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {
                "candidate_sha",
                "nonce",
                "origin",
                "pid",
                "schema_version",
                "type",
            }
            or payload.get("schema_version") != 1
            or payload.get("type") != "battlefield-webgui-ready"
            or payload.get("nonce") != self._nonce
            or payload.get("candidate_sha") != self._config.repository_sha
            or not readiness_process_matches
            or parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed_port is None
            or not 1 <= parsed_port <= 65535
            or process.poll() is not None
        ):
            details = self._stderr_tail()
            raise RuntimeError(
                f"candidate fixture readiness contract failed: {details}"
            )
        self._start_drain(
            process.stdout,
            self._stdout_extra,
            "candidate-stdout",
        )
        return f"http://127.0.0.1:{parsed_port}/"

    def _readiness_process_matches(
        self,
        process: subprocess.Popen[str],
        reported_pid: object,
    ) -> bool:
        if type(reported_pid) is not int or reported_pid <= 0:
            return False
        if self._config.candidate_uid is None:
            return reported_pid == process.pid
        if sys.platform != "linux":
            return False
        current_pid = reported_pid
        visited: set[int] = set()
        for depth in range(64):
            if current_pid in visited or current_pid <= 1:
                return False
            visited.add(current_pid)
            try:
                parent_pid, uids, gids = self._linux_process_identity(
                    current_pid
                )
            except (OSError, RuntimeError, ValueError):
                return False
            if depth == 0 and (
                set(uids) != {self._config.candidate_uid}
                or set(gids) != {self._config.candidate_gid}
            ):
                return False
            if current_pid == process.pid:
                return True
            if parent_pid == process.pid:
                return True
            current_pid = parent_pid
        return False

    @staticmethod
    def _linux_process_identity(
        pid: int,
    ) -> tuple[int, tuple[int, ...], tuple[int, ...]]:
        stat_before = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        stat_after = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")

        def process_identity(stat_payload: str) -> tuple[int, int]:
            closing_parenthesis = stat_payload.rfind(")")
            if closing_parenthesis < 0:
                raise RuntimeError("candidate process stat is malformed")
            fields = stat_payload[closing_parenthesis + 1 :].split()
            if len(fields) < 20:
                raise RuntimeError("candidate process stat is incomplete")
            return int(fields[1]), int(fields[19])

        before_identity = process_identity(stat_before)
        if process_identity(stat_after) != before_identity:
            raise RuntimeError("candidate process identity changed")
        identifiers: dict[str, tuple[int, ...]] = {}
        for line in status.splitlines():
            key, separator, values = line.partition(":")
            if separator and key in {"Uid", "Gid"}:
                identifiers[key] = tuple(
                    int(value) for value in values.split()
                )
        uids = identifiers.get("Uid", ())
        gids = identifiers.get("Gid", ())
        if len(uids) != 4 or len(gids) != 4:
            raise RuntimeError("candidate process identity is incomplete")
        return before_identity[0], uids, gids

    def _start_drain(
        self,
        stream: object,
        destination: bytearray,
        name: str,
    ) -> None:
        def drain() -> None:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                payload = str(chunk).encode("utf-8", errors="replace")
                with self._drain_lock:
                    destination.extend(payload)
                    if len(destination) > 64 * 1024:
                        del destination[: len(destination) - 64 * 1024]

        thread = threading.Thread(target=drain, name=name, daemon=True)
        thread.start()
        self._drain_threads.append(thread)

    def _stderr_tail(self) -> str:
        with self._drain_lock:
            return bytes(self._stderr[-4000:]).decode(
                "utf-8",
                errors="replace",
            ).replace("\n", " ")

    def assert_quiet(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            raise RuntimeError("candidate fixture exited before browser verdict")
        with self._drain_lock:
            if bytes(self._stdout_extra).strip():
                raise RuntimeError("candidate fixture emitted duplicate protocol data")

    def stop(self) -> None:
        process = self._process
        if process is None:
            self._cleanup_fixture_script()
            return
        termination_confirmed = process.poll() is not None
        dedicated_cleanup_attempted = False
        try:
            if not termination_confirmed:
                self._signal_group(process, signal.SIGTERM)
                try:
                    process.wait(timeout=_FIXTURE_STOP_TIMEOUT_SECONDS)
                    termination_confirmed = True
                except subprocess.TimeoutExpired:
                    self._signal_group(process, signal.SIGKILL)
                    try:
                        process.wait(timeout=_FIXTURE_STOP_TIMEOUT_SECONDS)
                        termination_confirmed = True
                    except subprocess.TimeoutExpired:
                        dedicated_cleanup_attempted = True
                        self._cleanup_dedicated_uid()
                        process.wait(timeout=_FIXTURE_STOP_TIMEOUT_SECONDS)
                        termination_confirmed = True
        finally:
            try:
                if termination_confirmed:
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()
                    for thread in self._drain_threads:
                        thread.join(timeout=1)
                    if not dedicated_cleanup_attempted:
                        self._cleanup_dedicated_uid()
            finally:
                self._process = None if termination_confirmed else process
                self._cleanup_fixture_script()

    def _signal_group(
        self,
        process: subprocess.Popen[str],
        requested_signal: signal.Signals,
    ) -> None:
        if process.poll() is not None:
            return
        if self._config.candidate_uid is None:
            try:
                os.killpg(process.pid, requested_signal)
            except ProcessLookupError:
                pass
            return
        subprocess.run(
            [
                "/usr/bin/sudo",
                "--non-interactive",
                "/bin/kill",
                f"-{requested_signal.value}",
                f"-{process.pid}",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

    def _cleanup_dedicated_uid(self) -> None:
        uid = self._config.candidate_uid
        pkill = Path("/usr/bin/pkill")
        if uid is None or not pkill.is_file():
            return
        subprocess.run(
            [
                "/usr/bin/sudo",
                "--non-interactive",
                str(pkill),
                "-KILL",
                "-U",
                str(uid),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )


def _serve_candidate_fixture() -> int:
    from starcraft_commander.web_gui import WebGuiServer

    raw_request = sys.stdin.readline(4097)
    if (
        len(raw_request.encode("utf-8")) > 4096
        or sys.stdin.read(1) != ""
    ):
        raise ValueError("candidate fixture start request is malformed")
    request = json.loads(raw_request)
    if (
        not isinstance(request, dict)
        or set(request)
        != {"candidate_sha", "nonce", "schema_version", "type"}
        or request.get("schema_version") != 1
        or request.get("type") != "battlefield-webgui-start"
        or _SHA_RE.fullmatch(str(request.get("candidate_sha", ""))) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(request.get("nonce", ""))) is None
    ):
        raise ValueError("candidate fixture start contract failed")
    bridge = _BrowserFixtureBridge()
    server = WebGuiServer(bridge=bridge, port=0)
    stopped = threading.Event()

    def request_stop(
        signum: int,
        frame: object,
    ) -> None:
        del signum, frame
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    server.start()
    if server._http is None:
        raise RuntimeError("candidate browser fixture server did not start")
    server._http.micromachine_launcher = _BrowserFixtureLauncher(
        bridge.micromachine_blackboard_dir()
    )
    print(
        json.dumps(
            {
                "candidate_sha": request["candidate_sha"],
                "nonce": request["nonce"],
                "origin": server.url,
                "pid": os.getpid(),
                "schema_version": 1,
                "type": "battlefield-webgui-ready",
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        stopped.wait()
    finally:
        server.stop()
    return 0


def run_browser_gate(config: BrowserGateConfig) -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    config.artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir = config.artifact_dir / "screenshots"
    diff_dir = config.artifact_dir / "diffs"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    diff_dir.mkdir(parents=True, exist_ok=True)
    candidate_fixture = _CandidateFixtureProcess(config)
    server_url = candidate_fixture.start()
    started_at = datetime.now(timezone.utc)
    viewport_reports: list[dict[str, object]] = []
    visual_failures: list[str] = []
    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, object] = {"headless": True}
            if config.chromium_executable is not None:
                launch_options["executable_path"] = str(
                    config.chromium_executable
                )
            browser = playwright.chromium.launch(**launch_options)
            try:
                for name, width, height in VIEWPORTS:
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        color_scheme="light",
                        locale="ko-KR",
                    )
                    try:
                        context.add_init_script(_BROWSER_INIT_SCRIPT)
                        page = context.new_page()
                        page.goto(server_url, wait_until="domcontentloaded")
                        _wait_for_cards(page)
                        structural = _structural_assertions(page)
                        accessibility = _accessibility_assertions(page)
                        keyboard = _keyboard_journey(page)
                        voice = _voice_journey(page) if name == "desktop" else {}
                        actual_path = screenshot_dir / f"{name}.png"
                        page.screenshot(path=str(actual_path), full_page=False)
                        baseline_path = config.baseline_dir / f"{name}.png"
                        if not baseline_path.is_file():
                            raise AssertionError(
                                f"tracked baseline is missing: {baseline_path}"
                            )
                        diff_ratio = _pixel_diff(
                            baseline_path,
                            actual_path,
                            diff_dir / f"{name}.png",
                        )
                        if diff_ratio > VISUAL_DIFF_THRESHOLD:
                            visual_failures.append(
                                f"{name} visual diff {diff_ratio:.6f} exceeds "
                                f"{VISUAL_DIFF_THRESHOLD:.6f}"
                            )
                        viewport_reports.append(
                            {
                                "name": name,
                                "viewport": {"width": width, "height": height},
                                "structural": structural,
                                "accessibility": accessibility,
                                "keyboard": keyboard,
                                "voice": voice,
                                "visual_diff_ratio": diff_ratio,
                                "baseline_sha256": hashlib.sha256(
                                    baseline_path.read_bytes()
                                ).hexdigest(),
                                "actual_sha256": hashlib.sha256(
                                    actual_path.read_bytes()
                                ).hexdigest(),
                            }
                        )
                    finally:
                        context.close()
                media = _media_assertions(browser, server_url)
                candidate_fixture.assert_quiet()
                if visual_failures:
                    raise AssertionError("; ".join(visual_failures))
            finally:
                browser.close()
    finally:
        candidate_fixture.stop()
    ended_at = datetime.now(timezone.utc)
    report: dict[str, object] = {
        "schema_version": BROWSER_GATE_SCHEMA_VERSION,
        "producer": BROWSER_GATE_PRODUCER,
        "repository_sha": config.repository_sha,
        "build_identity": config.build_identity,
        "generated_at": ended_at.isoformat().replace("+00:00", "Z"),
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "status": "passed",
        "ok": True,
        "visual_diff_threshold": VISUAL_DIFF_THRESHOLD,
        "candidate_web_gui_sha256": (
            candidate_fixture.candidate_web_gui_sha256()
        ),
        "viewports": viewport_reports,
        "media": media,
        "manual_live_qa_remaining": True,
    }
    json_path = config.artifact_dir / "battlefield-browser-gate.json"
    markdown_path = config.artifact_dir / "battlefield-browser-gate.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    return report


def _markdown_report(report: Mapping[str, object]) -> str:
    lines = [
        "# Battlefield Commander browser gate",
        "",
        f"- Status: `{report['status']}`",
        f"- Repository SHA: `{report['repository_sha']}`",
        f"- Build identity: `{report['build_identity']}`",
        f"- Visual threshold: `{report['visual_diff_threshold']}`",
        "- Manual SC2 visual/audio QA remaining: `true`",
        "",
        "| Viewport | Cards | Axe serious/critical | Visual diff |",
        "|---|---:|---:|---:|",
    ]
    for viewport in report["viewports"]:  # type: ignore[index]
        item = dict(viewport)
        structural = dict(item["structural"])
        accessibility = dict(item["accessibility"])
        lines.append(
            f"| `{item['name']}` | {structural['cards']} | "
            f"{accessibility['serious_critical_count']} | "
            f"{float(item['visual_diff_ratio']):.6f} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the non-skippable Battlefield Commander browser gate."
    )
    parser.add_argument("--repository-sha", required=True)
    parser.add_argument("--build-identity", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=DEFAULT_BASELINE_DIR,
    )
    parser.add_argument("--chromium-executable", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--candidate-python", type=Path)
    parser.add_argument("--candidate-uid", type=int)
    parser.add_argument("--candidate-gid", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "serve-fixture":
        try:
            return _serve_candidate_fixture()
        except Exception as error:  # noqa: BLE001 - child must fail closed.
            print(f"candidate browser fixture failed: {error}", file=sys.stderr)
            return 1
    args = build_argument_parser().parse_args(argv)
    try:
        report = run_browser_gate(
            BrowserGateConfig(
                repository_sha=args.repository_sha,
                build_identity=args.build_identity,
                artifact_dir=args.artifact_dir,
                baseline_dir=args.baseline_dir,
                chromium_executable=args.chromium_executable,
                candidate_root=(
                    args.candidate_root.resolve()
                    if args.candidate_root is not None
                    else Path(__file__).resolve().parents[1]
                ),
                candidate_python=(
                    args.candidate_python.resolve()
                    if args.candidate_python is not None
                    else Path(sys.executable).resolve()
                ),
                candidate_uid=args.candidate_uid,
                candidate_gid=args.candidate_gid,
            )
        )
    except Exception as error:  # noqa: BLE001 - CLI must fail closed.
        print(f"battlefield browser gate failed: {error}")
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
