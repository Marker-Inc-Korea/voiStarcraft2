"""Stdlib-only local web GUI for the StarCraft II Korean commander.

``python -m starcraft_commander.web_gui --dry-run`` serves a single-page
Korean interface (title: "voiStarcraft2 커맨더") on hard-coded localhost where
a human types commands, watches per-outcome narration with status colors, and
sees a live economy/army state panel. No FastAPI, Flask, or any third-party
dependency is used: the server is :class:`http.server.ThreadingHTTPServer`
and the page is embedded vanilla HTML/JS (no external CDN).

Architecture (three seams, each independently swappable):

- :class:`WebGuiBridgeInterface` — the duck-typed boundary the HTTP layer
  talks to: non-blocking command submission, read-only state snapshots, and
  monotonically sequenced outcome history.
- :class:`SessionLoopBridge` — the default bridge. It owns a daemon thread
  running its own asyncio event loop that drains submitted texts sequentially
  through an injected ``SC2CommandSession`` (``await session.process_text``).
  Every outcome is recorded into an injected history store (duck-typed
  ``record``/``since``/``latest_seq``; the internal :class:`_SimpleHistory`
  default is swapped for ``CommanderEventMemory`` by the integrator).
- :class:`WebGuiServer` — the threaded HTTP server, bound to ``127.0.0.1``
  only (hard-coded for security; the GUI is a local cockpit, never a network
  service).

The LLM-free invariant holds: nothing here runs per game frame. Commands flow
only when the human submits text, exactly like the terminal demo. The browser
uses an authenticated SSE event journal for command/state feedback and falls
back to read-only JSON polling; neither path touches the interpreter.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit
from weakref import WeakValueDictionary

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_GAME_LOOPS_PER_SECOND,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
    require_micromachine_update_id,
)
from starcraft_commander.micromachine_battlefield_projection import (
    BattlefieldProjectionIdentity,
    BattlefieldProjectionResult,
    battlefield_overview_fingerprint,
    select_latest_battlefield_projection,
)
from starcraft_commander.micromachine_command_execution import (
    EXPIRY_OPERATION_REASONS,
    HARD_OPERATION_BLOCK_REASONS,
    HARD_OPERATION_STATUSES,
    TRANSIENT_OPERATION_BLOCK_REASONS,
    classify_micromachine_command_execution,
    classify_micromachine_operation_executions,
    operation_requires_specific_family_ability_evidence,
)
from starcraft_commander.micromachine_tactical_evidence import (
    classify_micromachine_tactical_evidence,
    normalize_tactical_effect_tags,
)
from starcraft_commander.micromachine_terran_capabilities import (
    operation_family_evidence,
)
from starcraft_commander.policy_modulation import (
    MICROMACHINE_OPERATION_EDIT_ACTIONS,
    POLICY_MODULATION_TTL_MAX_SECONDS,
    POLICY_MODULATION_TTL_MIN_SECONDS,
    PolicyModulationSource,
    TacticalScopeModulation,
    reject_raw_policy_control_keys,
)
from starcraft_commander.runtime_deps import MissingLLMDependencyError
from starcraft_commander.state_resolver import (
    DEFAULT_SC2_STATE_RESOLVER,
    SC2StateResolverInterface,
)


WEB_GUI_HOST: Final[str] = "127.0.0.1"
"""Default localhost binding for the web GUI."""

WEB_GUI_TOKEN_QUERY_PARAM: Final[str] = "token"
"""Query parameter accepted as the web GUI auth token."""

WEB_GUI_TOKEN_HEADER: Final[str] = "X-voiStarcraft2-Token"
"""HTTP header accepted as the web GUI auth token."""

DEFAULT_WEB_GUI_PORT: Final[int] = 8350
"""Default web GUI port; ``0`` requests an ephemeral port (used by tests)."""

_REPO_ROOT: Final[str] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
"""Repository root resolved from this module, independent of process cwd."""


def _default_sc2_install_path() -> str:
    """Resolve a portable local StarCraft II root for live launch."""

    for variable in ("SC2_ROOT", "SC2PATH"):
        configured = os.environ.get(variable, "").strip()
        if configured:
            return os.path.abspath(os.path.expanduser(configured))
    candidates = (
        os.path.expanduser("~/Desktop/StarCraft2/StarCraft II"),
        "/Applications/StarCraft II",
        os.path.expanduser("~/Applications/StarCraft II"),
    )
    for candidate in candidates:
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return "/Applications/StarCraft II"


DEFAULT_SC2_INSTALL_PATH: Final[str] = _default_sc2_install_path()
"""Environment-aware StarCraft II install path used by auto live launch."""

DEFAULT_LIVE_MAP: Final[str] = "AcropolisLE"
"""Default map for opt-in legacy python-sc2 auto-launch sessions."""

DEFAULT_LIVE_DIFFICULTY: Final[str] = "easy"
"""Default difficulty for opt-in legacy python-sc2 auto-launch sessions."""

DEFAULT_MICROMACHINE_LIVE_ENEMY_DIFFICULTY: Final[int] = 10
"""Default maximum enemy difficulty for UI-triggered manual MicroMachine live QA."""

_MICROMACHINE_ENEMY_DIFFICULTY_MIN: Final[int] = 1
_MICROMACHINE_ENEMY_DIFFICULTY_MAX: Final[int] = 10

COMMAND_MODE_MICROMACHINE: Final[str] = "micromachine"
"""Default cockpit mode: publish text/voice intent to MicroMachine DSL blackboard."""

COMMAND_MODE_LEGACY_COMMANDER: Final[str] = "legacy_commander"
"""Compatibility mode: route chat through the legacy python-sc2 commander."""

_LOCAL_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://127\.0\.0\.1:\d+(?:/[^\s]*)?"
)

_MICROMACHINE_SCOPE_UNIT_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "air",
        "banshee",
        "battlecruiser",
        "bio",
        "cyclone",
        "ghost",
        "hellbat",
        "hellion",
        "liberator",
        "marine",
        "marauder",
        "mech",
        "medivac",
        "raven",
        "reaper",
        "scv",
        "siege",
        "siege_tank",
        "thor",
        "viking",
        "widow_mine",
        "worker",
        "workers",
    }
)
"""Bounded semantic unit classes accepted from the cockpit."""

_MICROMACHINE_SCOPE_UNIT_CLASS_ALIASES: Final[Mapping[str, str]] = {
    "siege tank": "siege_tank",
    "tank": "siege_tank",
    "widow mine": "widow_mine",
    "worker": "workers",
}
"""Human-friendly unit-class aliases normalized before DSL validation."""

_MICROMACHINE_TACTICAL_LOG_FILES: Final[tuple[str, ...]] = (
    "micromachine.log",
    "micromachine_combined.log",
)
"""Blackboard-local logs that may contain MicroMachine tactical decisions."""

_MICROMACHINE_TACTICAL_LOG_TERMS: Final[tuple[str, ...]] = (
    "policy",
    "modulation",
    "updateattacksquads",
    "mainattacksquad",
    "calctargets",
    "target",
    "scope",
    "contain",
    "harass",
    "retreat",
    "attack",
    "reinforce",
    "squad",
    "refus",
)
"""Lowercase filters for tactical snippets shown in the cockpit."""

_MICROMACHINE_MAX_LOG_READ_BYTES: Final[int] = 256 * 1024
"""Upper bound for reading the tail of one MicroMachine log file."""

_MICROMACHINE_LOG_FRAME_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(\d+):")
"""Frame prefix parser for MicroMachine tactical log snippets."""

_MICROMACHINE_PROVIDER_VECTOR_WRAPPER_KEYS: Final[tuple[str, ...]] = (
    "modulation",
    "policy_modulation",
    "policy_modulation_vector",
    "vector",
)
"""Provider wrapper keys whose nested vector must receive UI scope overrides."""


def _api_key_env_var_for_provider(provider: str) -> str:
    """Return the child-process env var used by one supported provider."""

    normalized = provider.strip().lower()
    if normalized == "anthropic":
        return "ANTHROPIC_API_KEY"
    if normalized == "gemini":
        return "GEMINI_API_KEY"
    if normalized == "grok":
        return "XAI_API_KEY"
    return "OPENAI_API_KEY"


def _build_llm_setup_failure_response(
    error: Exception,
    *,
    provider: str,
    model: str,
    api_key: str,
) -> tuple[HTTPStatus, dict[str, object]]:
    """Convert setup exceptions into safe, specific user-facing failures."""

    category, reason_code, status = _classify_llm_setup_failure(error)
    detail = _sanitize_llm_setup_error(error, redactions=(api_key,))
    if category == "validation":
        message = f"LLM 설정 검증 실패: {detail}"
    elif category == "dependency":
        message = f"LLM 제공자 준비 실패: {detail}"
    elif category == "network":
        message = f"LLM 제공자 연결 실패: {detail}"
    elif category == "provider":
        message = f"LLM 제공자 거부: {detail}"
    else:
        message = f"LLM 키 설정 실패: {detail}"
    return status, {
        "configured": False,
        "provider": provider.strip().lower(),
        "model": model.strip(),
        "failure_category": category,
        "reason_code": reason_code,
        "error": message,
    }


def _classify_llm_setup_failure(error: Exception) -> tuple[str, str, HTTPStatus]:
    """Classify setup failure source without depending on provider SDK classes."""

    if isinstance(error, MissingLLMDependencyError):
        return "dependency", "llm_setup_dependency_missing", HTTPStatus.SERVICE_UNAVAILABLE
    if isinstance(error, (ValueError, TypeError)):
        return "validation", "llm_setup_validation_failed", HTTPStatus.BAD_REQUEST
    marker_text = f"{type(error).__module__}.{type(error).__name__} {error}".lower()
    if isinstance(error, (ConnectionError, TimeoutError, OSError)) or any(
        marker in marker_text for marker in _LLM_SETUP_NETWORK_MARKERS
    ):
        return "network", "llm_setup_network_failed", HTTPStatus.SERVICE_UNAVAILABLE
    if any(marker in marker_text for marker in _LLM_SETUP_PROVIDER_MARKERS):
        return "provider", "llm_setup_provider_rejected", HTTPStatus.BAD_GATEWAY
    return "unknown", "llm_setup_failed", HTTPStatus.BAD_REQUEST


def _sanitize_llm_setup_error(
    error: Exception,
    *,
    redactions: Sequence[str] = (),
) -> str:
    """Return one bounded setup error string with submitted key material removed."""

    message = str(error).strip() or type(error).__name__
    return _redact_sensitive_text(
        message,
        redactions=redactions,
        normalize_whitespace=True,
        max_chars=500,
    ) or type(error).__name__


def _redact_sensitive_text(
    value: object,
    *,
    redactions: Sequence[str] = (),
    normalize_whitespace: bool = False,
    max_chars: int | None = None,
) -> str:
    """Return text with API-key-shaped and explicitly known secrets removed."""

    message = str(value)
    for secret in redactions:
        cleaned = secret.strip() if isinstance(secret, str) else ""
        if cleaned:
            message = message.replace(cleaned, _LLM_SETUP_REDACTION)
    for pattern in _API_KEY_REDACTION_PATTERNS:
        message = pattern.sub(_LLM_SETUP_REDACTION, message)
    if normalize_whitespace:
        message = " ".join(message.split())
    if max_chars is not None and len(message) > max_chars:
        message = message[: max_chars - 3].rstrip() + "..."
    return message


def _redact_json_ready(value: object, *, redactions: Sequence[str] = ()) -> object:
    """Return a JSON-ready value with secret-bearing string values redacted."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_sensitive_text(value, redactions=redactions)
    if isinstance(value, Mapping):
        return {
            (
                _redact_sensitive_text(key, redactions=redactions)
                if isinstance(key, str)
                else key
            ): _redact_json_ready(item, redactions=redactions)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_json_ready(item, redactions=redactions) for item in value]
    return _redact_sensitive_text(value, redactions=redactions)


def _clean_blackboard_dir(value: str, fallback: str) -> str:
    if not isinstance(value, str):
        raise TypeError("MicroMachine blackboard_dir must be a string.")
    cleaned = value.strip() or fallback.strip()
    if not cleaned:
        raise ValueError("MicroMachine blackboard_dir must be configured.")
    return cleaned


def _normalize_runtime_mode(value: str) -> str:
    """Return the only two runtime modes accepted by the local cockpit."""

    return (
        COMMAND_MODE_LEGACY_COMMANDER
        if str(value).strip() == COMMAND_MODE_LEGACY_COMMANDER
        else COMMAND_MODE_MICROMACHINE
    )


def _require_micromachine_enemy_difficulty(
    value: object,
    *,
    default: int = DEFAULT_MICROMACHINE_LIVE_ENEMY_DIFFICULTY,
) -> int:
    """Return a validated SC2 API enemy difficulty in the supported 1..10 range."""

    candidate = default if value is None else value
    if type(candidate) is not int:
        raise TypeError("enemy_difficulty 필드는 1..10 정수여야 합니다.")
    if not _MICROMACHINE_ENEMY_DIFFICULTY_MIN <= candidate <= _MICROMACHINE_ENEMY_DIFFICULTY_MAX:
        raise ValueError("enemy_difficulty 필드는 1..10 범위여야 합니다.")
    return candidate


def _default_micromachine_blackboard_dir() -> str:
    configured = os.environ.get("VOI_MICROMACHINE_BLACKBOARD_DIR", "").strip()
    if configured:
        return configured
    temp_root = "/private/tmp" if os.path.isdir("/private/tmp") else tempfile.gettempdir()
    return os.path.join(temp_root, "voi-mm-live")


def _micromachine_compile_result_path(blackboard_dir: str) -> str:
    return os.path.join(blackboard_dir, "latest_modulation_compile_result.json")


def _micromachine_blackboard_scope_id(blackboard_dir: str) -> str:
    """Return the server-owned opaque identity for one resolved blackboard."""

    root = os.path.realpath(os.path.abspath(blackboard_dir))
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()
    return f"voi-mm-scope-{digest[:24]}"


def _micromachine_compile_result_id(
    blackboard_scope_id: str,
    update_id: str,
) -> str:
    """Return the immutable browser de-duplication ID for one update result."""

    digest = hashlib.sha256(
        f"{blackboard_scope_id}\0{update_id}".encode("utf-8")
    ).hexdigest()
    return f"voi-mm-result-{digest}"


def _micromachine_compile_result_metadata(
    blackboard_dir: str,
    update_id: object,
) -> dict[str, str]:
    """Build canonical result metadata without trusting client-provided scope."""

    scope_id = _micromachine_blackboard_scope_id(blackboard_dir)
    normalized_update_id = str(update_id or "").strip()
    metadata = {"blackboard_scope_id": scope_id}
    if normalized_update_id:
        metadata["result_id"] = _micromachine_compile_result_id(
            scope_id,
            normalized_update_id,
        )
    return metadata


def _micromachine_compile_result_history_dir(blackboard_dir: str) -> str:
    return os.path.join(blackboard_dir, "modulation_compile_results")


def _micromachine_compile_result_history_path(
    blackboard_dir: str,
    update_id: str,
) -> str:
    digest = hashlib.sha256(update_id.encode("utf-8")).hexdigest()
    return os.path.join(
        _micromachine_compile_result_history_dir(blackboard_dir),
        f"{digest}.json",
    )


_MICROMACHINE_COMPILE_RESULT_LOCKS_GUARD = threading.Lock()
_MICROMACHINE_COMPILE_RESULT_LOCKS: WeakValueDictionary[
    str,
    threading.Lock,
] = WeakValueDictionary()


def _micromachine_compile_result_lock(blackboard_dir: str) -> threading.Lock:
    """Return one process-local persistence lock per resolved blackboard."""

    key = os.path.realpath(os.path.abspath(blackboard_dir))
    with _MICROMACHINE_COMPILE_RESULT_LOCKS_GUARD:
        return _MICROMACHINE_COMPILE_RESULT_LOCKS.setdefault(
            key,
            threading.Lock(),
        )


def _micromachine_compile_result_order(
    payload: Mapping[str, object],
) -> tuple[int, int]:
    """Order results by request acceptance, never by completion time."""

    accepted_at_unix_ns = payload.get("accepted_at_unix_ns")
    if type(accepted_at_unix_ns) is not int or accepted_at_unix_ns < 0:
        written_at_unix = payload.get("written_at_unix")
        accepted_at_unix_ns = (
            int(written_at_unix * 1_000_000_000)
            if isinstance(written_at_unix, (int, float))
            and not isinstance(written_at_unix, bool)
            else 0
        )
    acceptance_ordinal = payload.get("acceptance_ordinal")
    if type(acceptance_ordinal) is not int or acceptance_ordinal < 0:
        acceptance_ordinal = 0
    return accepted_at_unix_ns, acceptance_ordinal


def _micromachine_compile_result_is_newer(
    candidate: Mapping[str, object],
    current: Mapping[str, object] | None,
) -> bool:
    if current is None:
        return True
    candidate_order = _micromachine_compile_result_order(candidate)
    current_order = _micromachine_compile_result_order(current)
    if candidate_order != current_order:
        return candidate_order > current_order
    return (
        str(candidate.get("update_id", "") or "").strip()
        == str(current.get("update_id", "") or "").strip()
    )


def _new_micromachine_update_id() -> str:
    return f"voi-mm-{uuid.uuid4().hex}"


def _atomic_write_json(path: str, payload: Mapping[str, object]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def _write_micromachine_compile_result(
    blackboard_dir: str,
    payload: Mapping[str, object],
) -> tuple[str, ...]:
    """Persist ordered latest/history records and return safe warnings."""

    document = dict(payload)
    update_id = str(document.get("update_id", "") or "").strip()
    document.update(_micromachine_compile_result_metadata(blackboard_dir, update_id))
    warnings: list[str] = []
    with _micromachine_compile_result_lock(blackboard_dir):
        latest = _read_micromachine_compile_result(blackboard_dir)
        if _micromachine_compile_result_is_newer(document, latest):
            try:
                _atomic_write_json(
                    _micromachine_compile_result_path(blackboard_dir),
                    document,
                )
            except Exception as error:  # noqa: BLE001 - persistence is never publish control flow.
                warnings.append(
                    "latest compile result persistence failed: "
                    f"{type(error).__name__}"
                )
        if not update_id:
            return tuple(warnings)
        history_path = _micromachine_compile_result_history_path(
            blackboard_dir,
            update_id,
        )
        try:
            _atomic_write_json(history_path, document)
        except Exception as error:  # noqa: BLE001 - persistence is never publish control flow.
            warnings.append(
                "compile result history persistence failed: "
                f"{type(error).__name__}"
            )
        try:
            _prune_micromachine_compile_result_history(blackboard_dir)
        except Exception as error:  # noqa: BLE001 - retention is best effort.
            warnings.append(
                "compile result history retention failed: "
                f"{type(error).__name__}"
            )
    return tuple(warnings)


def _prune_micromachine_compile_result_history(blackboard_dir: str) -> None:
    directory = _micromachine_compile_result_history_dir(blackboard_dir)
    try:
        paths = [
            os.path.join(directory, name)
            for name in os.listdir(directory)
            if name.endswith(".json")
        ]
    except OSError:
        return
    paths.sort(
        key=lambda path: os.path.getmtime(path),
        reverse=True,
    )
    for path in paths[_MICROMACHINE_COMPILE_RESULT_HISTORY_LIMIT:]:
        try:
            os.unlink(path)
        except OSError:
            pass


def _read_micromachine_compile_result(blackboard_dir: str) -> dict[str, object] | None:
    path = _micromachine_compile_result_path(blackboard_dir)
    root_real = os.path.realpath(blackboard_dir)
    path_real = os.path.realpath(path)
    if not path_real.startswith(root_real + os.sep) or not os.path.isfile(path_real):
        return None
    try:
        with open(path_real, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _read_micromachine_compile_result_history(
    blackboard_dir: str,
) -> tuple[dict[str, object], ...]:
    directory = _micromachine_compile_result_history_dir(blackboard_dir)
    root_real = os.path.realpath(blackboard_dir)
    directory_real = os.path.realpath(directory)
    if not directory_real.startswith(root_real + os.sep):
        return ()
    try:
        paths = [
            os.path.join(directory_real, name)
            for name in os.listdir(directory_real)
            if name.endswith(".json")
        ]
    except OSError:
        return ()
    documents: list[dict[str, object]] = []
    for path in paths:
        path_real = os.path.realpath(path)
        if not path_real.startswith(directory_real + os.sep):
            continue
        try:
            with open(path_real, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            documents.append(dict(payload))
    documents.sort(
        key=lambda item: float(item.get("written_at_unix", 0.0) or 0.0)
    )
    return tuple(documents[-_MICROMACHINE_COMPILE_RESULT_HISTORY_LIMIT:])


def _micromachine_compile_result_stream(
    documents: Sequence[Mapping[str, object]],
    *,
    blackboard_dir: str,
    now_unix: float | None = None,
) -> list[dict[str, object]]:
    now = time.time() if now_unix is None else float(now_unix)
    results: list[dict[str, object]] = []
    for document in documents:
        written_at = document.get("written_at_unix")
        if isinstance(written_at, (int, float)) and not isinstance(written_at, bool):
            if now - float(written_at) > _MICROMACHINE_COMPILE_RESULT_FRESH_SECONDS:
                continue
        result = document.get("result")
        if isinstance(result, Mapping):
            item = dict(result)
            update = item.get("update")
            update_id = (
                str(update.get("update_id", "") or "")
                if isinstance(update, Mapping)
                else str(
                    item.get("update_id")
                    or _mapping_child(item, "compile_result").get("update_id")
                    or document.get("update_id")
                    or ""
                )
            )
            item.update(
                _micromachine_compile_result_metadata(blackboard_dir, update_id)
            )
            results.append(item)
            continue
        compile_result = _latest_compile_result_payload(document, now_unix=now)
        if compile_result is None:
            continue
        item = {
            "status": str(document.get("status", "") or ""),
            "command_text": str(document.get("command_text", "") or ""),
            "compile_result": compile_result,
            "command_queue": (
                dict(document["command_queue"])
                if isinstance(document.get("command_queue"), Mapping)
                else {}
            ),
        }
        item.update(
            _micromachine_compile_result_metadata(
                blackboard_dir,
                document.get("update_id")
                or compile_result.get("update_id")
                or "",
            )
        )
        results.append(item)
    return results


def _latest_compile_result_payload(
    compile_document: object | None,
    *,
    now_unix: float | None = None,
) -> dict[str, object] | None:
    if not isinstance(compile_document, Mapping):
        return None
    written_at = compile_document.get("written_at_unix")
    if isinstance(written_at, (int, float)) and not isinstance(written_at, bool):
        now = time.time() if now_unix is None else float(now_unix)
        if now - float(written_at) > _MICROMACHINE_COMPILE_RESULT_FRESH_SECONDS:
            return None
    payload = compile_document.get("compile_result")
    if isinstance(payload, Mapping):
        result = dict(payload)
        update_id = compile_document.get("update_id")
        if isinstance(update_id, str) and update_id.strip():
            result.setdefault("update_id", update_id.strip())
        command_text = compile_document.get("command_text")
        if isinstance(command_text, str) and command_text.strip():
            result.setdefault("command_text", command_text.strip())
        duration_ms = compile_document.get("duration_ms")
        if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
            result.setdefault("duration_ms", int(duration_ms))
        command_queue = compile_document.get("command_queue")
        if isinstance(command_queue, Mapping):
            result.setdefault("command_queue", dict(command_queue))
        return result
    return None


def _extract_micromachine_semantic_scope(
    document: Mapping[str, object],
) -> tuple[dict[str, object] | None, int | None]:
    reject_raw_policy_control_keys(document)
    raw_scope = document.get("semantic_scope")
    scope_payload: dict[str, object] = {}
    if raw_scope is not None:
        if not isinstance(raw_scope, Mapping):
            raise ValueError("semantic_scope 필드는 JSON 객체여야 합니다.")
        scope_payload.update(dict(raw_scope))
    for field_name in (
        "army_group",
        "unit_classes",
        "location_intent",
        "duration_seconds",
        "min_units",
        "max_units",
        "require_safety_margin",
        "allow_partial_scope",
    ):
        if field_name in document:
            scope_payload[field_name] = document[field_name]
    ttl_seconds = scope_payload.pop("ttl_seconds", document.get("ttl_seconds", None))
    normalized_scope = _normalize_micromachine_scope_payload(scope_payload)
    normalized_ttl = (
        None
        if ttl_seconds in (None, "")
        else _bounded_int(
            "ttl_seconds",
            ttl_seconds,
            lower=POLICY_MODULATION_TTL_MIN_SECONDS,
            upper=POLICY_MODULATION_TTL_MAX_SECONDS,
        )
    )
    if not normalized_scope and normalized_ttl is None:
        return None, None
    return normalized_scope or None, normalized_ttl


def _extract_micromachine_language_context(
    document: Mapping[str, object],
    command_text: str,
) -> dict[str, object]:
    """Return response-language hints for the LLM policy modulation prompt."""

    ui_code = _normalize_language_code(document.get("ui_language")) or "ko"
    detected_code = _detect_text_language_code(command_text)
    response_code = (
        _normalize_language_code(document.get("response_language"))
        or detected_code
        or ui_code
    )
    return {
        "ui_language_code": ui_code,
        "ui_language": _language_label(ui_code),
        "detected_user_language_code": detected_code or "",
        "detected_user_language": _language_label(detected_code)
        if detected_code
        else "",
        "response_language_code": response_code,
        "response_language": _language_label(response_code),
    }


def _normalize_language_code(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower().replace("_", "-")
    if not normalized:
        return ""
    primary = normalized.split("-", 1)[0]
    if primary in _MICROMACHINE_LANGUAGE_LABELS:
        return primary
    if len(normalized) <= 32 and all(
        character.isalnum() or character in {"-", " "}
        for character in normalized
    ):
        return normalized
    return ""


def _language_label(code: str) -> str:
    if not code:
        return ""
    return _MICROMACHINE_LANGUAGE_LABELS.get(code, code)


def _detect_text_language_code(text: str) -> str:
    if any("\uac00" <= character <= "\ud7a3" for character in text):
        return "ko"
    if any("\u4e00" <= character <= "\u9fff" for character in text):
        return "zh"
    if any("a" <= character.lower() <= "z" for character in text):
        return "en"
    return ""


def _normalize_micromachine_scope_payload(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if not payload:
        return {}
    unknown = set(payload) - {
        "army_group",
        "unit_classes",
        "location_intent",
        "duration_seconds",
        "min_units",
        "max_units",
        "require_safety_margin",
        "allow_partial_scope",
    }
    if unknown:
        raise ValueError(
            "semantic_scope contains unsupported fields: "
            + ", ".join(sorted(str(key) for key in unknown))
        )
    normalized: dict[str, object] = {}
    for key in ("army_group", "location_intent"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip().lower()
        elif value not in (None, ""):
            raise ValueError(f"{key} must be a string.")
    unit_classes = _normalize_micromachine_unit_classes(payload.get("unit_classes"))
    if unit_classes:
        normalized["unit_classes"] = unit_classes
    for key in ("duration_seconds", "min_units", "max_units"):
        value = payload.get(key)
        if value in (None, ""):
            continue
        normalized[key] = _bounded_int(key, value, lower=0, upper=200_000)
    value = payload.get("require_safety_margin")
    if value not in (None, ""):
        normalized["require_safety_margin"] = _bounded_float(
            "require_safety_margin",
            value,
            lower=0.0,
            upper=1.0,
        )
    value = payload.get("allow_partial_scope")
    if value not in (None, ""):
        if type(value) is not bool:
            raise ValueError("allow_partial_scope must be a bool.")
        normalized["allow_partial_scope"] = value
    if not normalized:
        return {}
    scope = TacticalScopeModulation(**normalized).to_dict()
    return {
        key: value
        for key, value in scope.items()
        if not _is_empty_micromachine_scope_value(value)
    }


def _normalize_micromachine_unit_classes(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_values = _split_micromachine_unit_class_text(value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_values = list(value)
    else:
        raise ValueError("unit_classes must be a string or string list.")
    normalized: list[str] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            raise ValueError("unit_classes must contain only strings.")
        unit_class = raw_value.strip().lower().replace("-", "_").replace(" ", "_")
        unit_class = str(_MICROMACHINE_SCOPE_UNIT_CLASS_ALIASES.get(unit_class, unit_class))
        if not unit_class:
            continue
        if unit_class not in _MICROMACHINE_SCOPE_UNIT_CLASSES:
            raise ValueError(f"unsupported semantic unit class: {unit_class}")
        if unit_class not in normalized:
            normalized.append(unit_class)
    return normalized


def _split_micromachine_unit_class_text(value: str) -> list[str]:
    text = value.strip()
    for alias, canonical in _MICROMACHINE_SCOPE_UNIT_CLASS_ALIASES.items():
        if " " not in alias:
            continue
        text = re.sub(
            rf"(?<!\w){re.escape(alias)}(?!\w)",
            canonical,
            text,
            flags=re.IGNORECASE,
        )
    return [part for part in re.split(r"[\s,]+", text) if part]


def _is_empty_micromachine_scope_value(value: object) -> bool:
    if value in ("", None, [], ()):
        return True
    return type(value) is int and value == 0


def _bounded_int(
    field_name: str,
    value: object,
    *,
    lower: int,
    upper: int,
) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer.")
    if value < lower or value > upper:
        raise ValueError(f"{field_name} must be between {lower} and {upper}.")
    return value


def _bounded_float(
    field_name: str,
    value: object,
    *,
    lower: float,
    upper: float,
) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number.")
    numeric = float(value)
    if numeric < lower or numeric > upper:
        raise ValueError(f"{field_name} must be between {lower} and {upper}.")
    return numeric


def _micromachine_payload_update_id(payload: Mapping[str, object]) -> str:
    update = payload.get("update")
    compile_result = payload.get("compile_result")
    intervention = payload.get("intervention")
    execution = (
        intervention.get("command_execution")
        if isinstance(intervention, Mapping)
        else None
    )
    return str(
        (
            update.get("update_id")
            if isinstance(update, Mapping)
            else None
        )
        or payload.get("update_id")
        or (
            compile_result.get("update_id")
            if isinstance(compile_result, Mapping)
            else None
        )
        or (
            execution.get("command_id")
            if isinstance(execution, Mapping)
            else None
        )
        or ""
    ).strip()


def _micromachine_operation_updates(
    update: Mapping[str, object],
) -> list[tuple[str, dict[str, object]]]:
    """Expand one blackboard update into operation-specific update views."""

    update_id = str(update.get("update_id", "") or "").strip()
    vector = update.get("vector")
    vector_payload = dict(vector) if isinstance(vector, Mapping) else {}
    raw_operations = vector_payload.get("operations")
    operation_payloads: list[dict[str, object]] = []
    if isinstance(raw_operations, Mapping):
        for operation_key, raw_operation in raw_operations.items():
            if not isinstance(raw_operation, Mapping):
                continue
            operation = dict(raw_operation)
            operation.setdefault("operation_id", str(operation_key))
            operation_payloads.append(operation)
    elif isinstance(raw_operations, Sequence) and not isinstance(
        raw_operations,
        (str, bytes),
    ):
        operation_payloads.extend(
            dict(raw_operation)
            for raw_operation in raw_operations
            if isinstance(raw_operation, Mapping)
        )

    if not operation_payloads:
        tactical_task = vector_payload.get("tactical_task")
        task_id = (
            str(tactical_task.get("task_id", "") or "").strip()
            if isinstance(tactical_task, Mapping)
            else ""
        )
        operation_id = (
            str(vector_payload.get("operation_id", "") or "").strip()
            or task_id
            or update_id
        )
        operation_update = dict(update)
        operation_update["operation_id"] = operation_id
        operation_update["vector"] = vector_payload
        return [(operation_id, operation_update)] if operation_id else []

    expanded: list[tuple[str, dict[str, object]]] = []
    seen_ids: set[str] = set()
    for index, operation in enumerate(operation_payloads):
        nested_vector = operation.get("vector")
        operation_vector = dict(vector_payload)
        operation_vector.pop("operations", None)
        if isinstance(nested_vector, Mapping):
            operation_vector.update(dict(nested_vector))
        operation_vector.update(
            {
                key: value
                for key, value in operation.items()
                if key not in {"operation_id", "vector"}
            }
        )
        tactical_task = operation_vector.get("tactical_task")
        task_id = (
            str(tactical_task.get("task_id", "") or "").strip()
            if isinstance(tactical_task, Mapping)
            else ""
        )
        operation_id = (
            str(operation.get("operation_id", "") or "").strip()
            or task_id
            or f"{update_id}:operation-{index + 1}"
        )
        if operation_id in seen_ids:
            operation_id = f"{operation_id}:{index + 1}"
        seen_ids.add(operation_id)
        operation_vector["operation_id"] = operation_id
        operation_update = dict(update)
        operation_update["operation_id"] = operation_id
        operation_update["vector"] = operation_vector
        expanded.append((operation_id, operation_update))
    return expanded


def _micromachine_operation_director_entries(
    telemetry_document: Mapping[str, object],
) -> dict[tuple[str, int], dict[str, object]]:
    managers = telemetry_document.get("managers")
    operation_director = (
        managers.get("OperationDirector")
        if isinstance(managers, Mapping)
        else None
    )
    raw_operations = (
        operation_director.get("operations")
        if isinstance(operation_director, Mapping)
        else None
    )
    director_update_id = str(
        operation_director.get("policy_update_id", "")
        if isinstance(operation_director, Mapping)
        else ""
    ).strip()
    snapshot_frame = (
        int(telemetry_document.get("frame"))
        if type(telemetry_document.get("frame")) is int
        and int(telemetry_document.get("frame")) > 0
        else 0
    )
    entries: dict[tuple[str, int], dict[str, object]] = {}
    if isinstance(raw_operations, Mapping):
        iterator = raw_operations.items()
    elif isinstance(raw_operations, Sequence) and not isinstance(
        raw_operations,
        (str, bytes),
    ):
        iterator = (("", item) for item in raw_operations)
    else:
        iterator = ()
    for operation_key, raw_entry in iterator:
        if not isinstance(raw_entry, Mapping):
            continue
        entry = dict(raw_entry)
        operation_id = str(
            entry.get("operation_id") or operation_key or ""
        ).strip()
        if not operation_id:
            continue
        generation = (
            entry.get("generation")
            if type(entry.get("generation")) is int
            and int(entry.get("generation")) > 0
            else 1
        )
        entry["operation_id"] = operation_id
        entry["generation"] = generation
        entry["_director_policy_update_id"] = director_update_id
        entry["_snapshot_frame"] = snapshot_frame
        if director_update_id:
            entry.setdefault("policy_update_id", director_update_id)
        entries[(operation_id, generation)] = entry
    raw_pending = (
        operation_director.get("pending_family_effects")
        if isinstance(operation_director, Mapping)
        else None
    )
    for pending in (
        raw_pending
        if isinstance(raw_pending, Sequence)
        and not isinstance(raw_pending, (str, bytes, bytearray))
        else ()
    ):
        if not isinstance(pending, Mapping):
            continue
        operation_id = str(pending.get("operation_id", "") or "").strip()
        generation = (
            int(pending.get("generation"))
            if type(pending.get("generation")) is int
            and int(pending.get("generation")) > 0
            else 0
        )
        if not operation_id or generation <= 0:
            continue
        operation_key = (operation_id, generation)
        entry = entries.setdefault(
            operation_key,
            {
                "operation_id": operation_id,
                "generation": generation,
                "_director_policy_update_id": director_update_id,
                "_snapshot_frame": snapshot_frame,
                "_pending_only": True,
            },
        )
        queued = entry.get("pending_family_effects")
        pending_rows = (
            list(queued)
            if isinstance(queued, Sequence)
            and not isinstance(queued, (str, bytes, bytearray))
            else []
        )
        pending_rows.append(dict(pending))
        entry["pending_family_effects"] = pending_rows
        if director_update_id:
            entry.setdefault("policy_update_id", director_update_id)
    return entries


def _micromachine_operation_entry_for_request(
    entries: Mapping[tuple[str, int], Mapping[str, object]],
    *,
    update_id: str,
    operation_id: str,
    operation_generation: int,
) -> dict[str, object] | None:
    for (candidate_id, _active_generation), candidate in entries.items():
        if (
            candidate_id == operation_id
            and candidate.get("edit_requested_generation")
            == operation_generation
            and str(candidate.get("edit_rejected_update_id", "") or "").strip()
            == update_id
            and str(candidate.get("edit_resolution", "") or "").strip()
            == "blocked"
        ):
            active = dict(candidate)
            active["active_generation"] = candidate.get("generation")
            active["requested_generation"] = operation_generation
            active["edit_rejected"] = True
            execution_owner_update_id = str(
                candidate.get("policy_update_id", "")
                or candidate.get("active_update_id", "")
                or candidate.get("update_id", "")
                or ""
            ).strip()
            if execution_owner_update_id:
                active[
                    "operation_console_execution_owner_update_id"
                ] = execution_owner_update_id
            return active
    exact = entries.get((operation_id, operation_generation))
    if exact is not None:
        return dict(exact)
    return None


def _micromachine_operation_telemetry_document(
    telemetry_document: Mapping[str, object],
    *,
    update_id: str,
    operation_id: str,
    operation_generation: int = 1,
    issued_at_frame: int = 0,
    deadline_frame: int = 0,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Return only OperationDirector evidence owned by one operation."""

    entry = _micromachine_operation_entry_for_request(
        _micromachine_operation_director_entries(telemetry_document),
        update_id=update_id,
        operation_id=operation_id,
        operation_generation=operation_generation,
    )
    if entry is None:
        return {}, None
    director_update_id = str(
        entry.get("_director_policy_update_id", "") or ""
    ).strip()
    if not update_id:
        return {}, None
    edit_rejected_for_request = bool(
        entry.get("edit_rejected") is True
        and str(
            entry.get("edit_rejected_update_id", "") or ""
        ).strip()
        == update_id
    )
    director_matches_update = director_update_id == update_id
    if director_matches_update or edit_rejected_for_request:
        entry_update_ids = {
            str(entry.get(key, "") or "").strip()
            for key in ("update_id", "policy_update_id", "active_update_id")
            if str(entry.get(key, "") or "").strip()
        }
        if not edit_rejected_for_request and any(
            entry_update_id != update_id
            for entry_update_id in entry_update_ids
        ):
            return {}, None
        scoped_entry = dict(entry)
    else:
        raw_entry_pending = entry.get("pending_family_effects")
        pending_candidates = (
            raw_entry_pending
            if isinstance(raw_entry_pending, Sequence)
            and not isinstance(
                raw_entry_pending,
                (str, bytes, bytearray),
            )
            else ()
        )
        matching_pending = [
            dict(row)
            for row in pending_candidates
            if isinstance(row, Mapping)
            if str(row.get("update_id", "") or "").strip() == update_id
            and str(row.get("operation_id", "") or "").strip()
            == operation_id
            and type(row.get("generation")) is int
            and int(row.get("generation")) == operation_generation
        ]
        if not matching_pending:
            return {}, None
        scoped_entry = {
            "operation_id": operation_id,
            "generation": operation_generation,
            "pending_family_effects": matching_pending,
            "_snapshot_frame": entry.get("_snapshot_frame", 0),
            "_pending_only": True,
        }
    scoped_entry.pop("_director_policy_update_id", None)
    pending_only = scoped_entry.pop("_pending_only", False) is True
    scoped_entry.setdefault("operation_id", operation_id)
    evidence_generation = operation_generation
    if (
        scoped_entry.get("edit_rejected") is True
        and type(scoped_entry.get("active_generation")) is int
        and int(scoped_entry.get("active_generation")) > 0
    ):
        evidence_generation = int(scoped_entry["active_generation"])
        active_received_frame = scoped_entry.get("received_frame")
        if (
            type(active_received_frame) is int
            and int(active_received_frame) > 0
        ):
            issued_at_frame = int(active_received_frame)
        deadline_frame = 0
    execution_owner_update_id = str(
        scoped_entry.get(
            "operation_console_execution_owner_update_id",
            "",
        )
        or scoped_entry.get("policy_update_id", "")
        or scoped_entry.get("active_update_id", "")
        or scoped_entry.get("update_id", "")
        or update_id
    ).strip()
    if execution_owner_update_id:
        scoped_entry[
            "operation_console_execution_owner_update_id"
        ] = execution_owner_update_id
    snapshot_frame = (
        int(scoped_entry.get("_snapshot_frame"))
        if type(scoped_entry.get("_snapshot_frame")) is int
        else 0
    )
    family_evidence = operation_family_evidence(
        scoped_entry,
        expected_update_id=execution_owner_update_id,
        expected_operation_id=operation_id,
        expected_generation=evidence_generation,
        issued_at_frame=max(0, issued_at_frame),
        deadline_frame=max(0, deadline_frame),
        snapshot_frame=snapshot_frame,
    )
    if pending_only and not family_evidence:
        return {}, None
    if family_evidence or "family_evidence" in scoped_entry:
        scoped_entry["family_evidence"] = list(family_evidence)
    scoped_entry.pop("pending_family_effects", None)
    scoped_entry.pop("_snapshot_frame", None)
    frame = scoped_entry.get("telemetry_frame", telemetry_document.get("frame"))
    active_ids = _string_list(
        telemetry_document.get("active_modulation_ids", ())
    )
    for active_update_id in (update_id, execution_owner_update_id):
        if active_update_id and active_update_id not in active_ids:
            active_ids.append(active_update_id)
    return (
        {
            "frame": frame,
            "active_modulation_ids": active_ids,
            "managers": {"OperationDirector": scoped_entry},
            "_pending_only": pending_only,
        },
        scoped_entry,
    )


def _micromachine_operation_signal(
    entry: Mapping[str, object],
    section_name: str,
    *,
    boolean_keys: Sequence[str],
    text_keys: Sequence[str] = (),
    frame_keys: Sequence[str] = (),
    count_keys: Sequence[str] = (),
    accepted_statuses: Sequence[str] = (),
) -> tuple[bool, dict[str, object]]:
    section = entry.get(section_name)
    section_payload = dict(section) if isinstance(section, Mapping) else {}
    evidence = dict(section_payload)
    sources = (section_payload, entry)
    signaled = False
    for source in sources:
        if any(_truthy(source.get(key)) for key in boolean_keys):
            signaled = True
        if any(
            isinstance(source.get(key), str)
            and bool(str(source.get(key)).strip())
            for key in text_keys
        ):
            signaled = True
        if any(
            type(source.get(key)) is int and int(source.get(key)) > 0
            for key in frame_keys
        ):
            signaled = True
        if any(
            isinstance(source.get(key), (int, float))
            and not isinstance(source.get(key), bool)
            and float(source.get(key)) > 0
            for key in count_keys
        ):
            signaled = True
        status = str(source.get("status", "") or "").strip().lower()
        if status and status in accepted_statuses:
            signaled = True
    for key in (*boolean_keys, *text_keys, *frame_keys, *count_keys):
        if key in entry and key not in evidence:
            evidence[key] = entry[key]
    return signaled, evidence


def _micromachine_terminal_cleanup_action(
    operation_telemetry: Mapping[str, object],
    *,
    operation_id: str,
    operation_generation: int,
) -> dict[str, object]:
    action = str(operation_telemetry.get("last_action", "") or "").strip()
    normalized_action = action.lower()
    telemetry_operation_id = str(
        operation_telemetry.get("operation_id", "") or ""
    ).strip()
    telemetry_generation = operation_telemetry.get("generation")
    frame = operation_telemetry.get("last_action_frame", 0)
    if (
        not normalized_action.startswith(
            ("release_stop|", "release_no_owned_units|")
        )
        or telemetry_operation_id != operation_id
        or type(telemetry_generation) is not int
        or telemetry_generation != operation_generation
        or type(frame) is not int
        or frame <= 0
    ):
        return {}
    return {
        "action": action,
        "frame": frame,
        "operation_id": operation_id,
        "generation": operation_generation,
    }


def _micromachine_execution_stage_ok(
    execution: Mapping[str, object],
    *stage_names: str,
) -> bool:
    stages = execution.get("stages")
    if not isinstance(stages, Sequence) or isinstance(
        stages,
        (str, bytes, bytearray),
    ):
        return False
    accepted = set(stage_names)
    return any(
        isinstance(stage, Mapping)
        and str(stage.get("name", "") or "") in accepted
        and stage.get("ok") is True
        for stage in stages
    )


def _micromachine_operation_execution_matches(
    execution: Mapping[str, object],
    *,
    update_id: str,
    operation_id: str,
    operation_generation: int,
) -> bool:
    execution_generation = _int_or_none(
        execution.get("operation_generation")
    )
    if execution_generation is None:
        execution_generation = _int_or_none(execution.get("generation"))
    return bool(
        update_id
        and operation_id
        and operation_generation > 0
        and str(execution.get("command_id", "") or "").strip()
        == update_id
        and str(execution.get("operation_id", "") or "").strip()
        == operation_id
        and execution_generation == operation_generation
    )


def _micromachine_execution_has_active_family_contract(
    execution: Mapping[str, object],
) -> bool:
    stages = execution.get("stages")
    if not isinstance(stages, Sequence) or isinstance(
        stages,
        (str, bytes, bytearray),
    ):
        return False
    return any(
        isinstance(stage, Mapping)
        and isinstance(stage.get("evidence"), Mapping)
        and isinstance(stage["evidence"].get("family_lifecycle"), Mapping)
        and stage["evidence"]["family_lifecycle"].get("active") is True
        for stage in stages
    )


def _micromachine_strict_operation_execution(
    operation_update: Mapping[str, object],
    *,
    operation_id: str,
    operation_generation: int,
    operation_telemetry_document: Mapping[str, object],
) -> dict[str, object]:
    operation = dict(_mapping_child(operation_update, "vector"))
    operation["operation_id"] = operation_id
    operation["generation"] = operation_generation
    classifier_update = dict(operation_update)
    classifier_update["vector"] = {"operations": [operation]}
    latest_frame = operation_telemetry_document.get("frame")
    reports = classify_micromachine_operation_executions(
        latest_update=classifier_update,
        latest_telemetry=operation_telemetry_document,
        latest_frame=(
            int(latest_frame)
            if type(latest_frame) is int and latest_frame > 0
            else 0
        ),
    )
    if len(reports) != 1:
        return {}
    report = reports[0]
    if (
        report.operation_id != operation_id
        or report.operation_generation != operation_generation
    ):
        return {}
    result = report.to_dict()
    if (
        str(result.get("state", "") or "") in {"moving", "engaged"}
        and _micromachine_execution_stage_ok(result, "effect_observed")
    ):
        result["state"] = "effect_observed"
    return result


def _micromachine_operation_command_execution(
    *,
    update_id: str,
    operation_id: str,
    operation_generation: int,
    operation_telemetry: Mapping[str, object],
    fallback: Mapping[str, object],
    strict_identity: bool = True,
) -> dict[str, object]:
    if not operation_telemetry:
        if strict_identity and not _micromachine_operation_execution_matches(
            fallback,
            update_id=update_id,
            operation_id=operation_id,
            operation_generation=operation_generation,
        ):
            return {
                "command_id": update_id,
                "operation_id": operation_id,
                "operation_generation": operation_generation,
                "state": "published",
                "completed": False,
                "failed": False,
                "expired": False,
                "superseded": False,
                "blocker_manager": "",
                "blocker_reason": "",
                "stages": [],
                "terminal_cleanup": {},
                "telemetry": {},
            }
        result = dict(fallback)
        result.setdefault("operation_id", operation_id)
        result.setdefault("operation_generation", operation_generation)
        return result

    terminal_cleanup = _micromachine_terminal_cleanup_action(
        operation_telemetry,
        operation_id=operation_id,
        operation_generation=operation_generation,
    )
    if (
        str(fallback.get("command_id", "") or "") == update_id
        and str(fallback.get("operation_id", "") or "") == operation_id
        and fallback.get("operation_generation") == operation_generation
        and _micromachine_execution_has_active_family_contract(fallback)
    ):
        result = dict(fallback)
        state = str(result.get("state", "") or "").lower()
        result["superseded"] = state in {
            "superseded",
            "replaced",
            "cancelled",
            "canceled",
        }
        result["terminal_cleanup"] = terminal_cleanup
        result["telemetry"] = dict(operation_telemetry)
        return result

    received, received_evidence = _micromachine_operation_signal(
        operation_telemetry,
        "received",
        boolean_keys=("received", "command_received"),
        frame_keys=("received_frame", "command_received_frame"),
        accepted_statuses=("received", "accepted", "parsed", "reduced"),
    )
    assigned, assignment_evidence = _micromachine_operation_signal(
        operation_telemetry,
        "assignment",
        boolean_keys=("assigned", "assignment_ready"),
        frame_keys=("assigned_frame", "assignment_frame"),
        count_keys=("assigned_unit_count", "assigned_count"),
        accepted_statuses=("assigned", "ready", "partial"),
    )
    ordered, order_evidence = _micromachine_operation_signal(
        operation_telemetry,
        "submission",
        boolean_keys=(
            "submitted",
            "command_submitted",
            "order_issued",
        ),
        frame_keys=("submitted_frame", "submission_frame", "order_frame"),
        count_keys=("submitted_count", "command_submitted_count", "order_count"),
        accepted_statuses=("submitted", "issued", "accepted", "success"),
    )
    action_issued, action_evidence = _micromachine_operation_signal(
        operation_telemetry,
        "submission",
        boolean_keys=(
            "action_issued",
            "actual_command_issued",
        ),
        text_keys=("last_action", "last_actual_command"),
        frame_keys=(
            "action_frame",
            "last_action_frame",
            "last_actual_command_frame",
        ),
        count_keys=("action_count", "actual_command_issued_count"),
        accepted_statuses=("action_issued", "executed", "commanded"),
    )
    moving, movement_evidence = _micromachine_operation_signal(
        operation_telemetry,
        "movement",
        boolean_keys=("moving", "movement_observed", "target_reached"),
        frame_keys=("movement_frame", "movement_observed_frame", "target_reached_frame"),
        count_keys=("moved_unit_count",),
        accepted_statuses=("moving", "observed", "target_reached"),
    )
    engaged, engagement_evidence = _micromachine_operation_signal(
        operation_telemetry,
        "engagement",
        boolean_keys=("engaged", "engagement_observed", "damage_dealt"),
        frame_keys=("engagement_frame", "engagement_observed_frame"),
        count_keys=("engaged_unit_count", "attack_count"),
        accepted_statuses=("engaged", "observed", "combat"),
    )
    if terminal_cleanup:
        ordered = _micromachine_execution_stage_ok(
            fallback,
            "order_issued",
        )
        action_issued = _micromachine_execution_stage_ok(
            fallback,
            "action_issued",
        )
        moving = False
        engaged = _micromachine_execution_stage_ok(
            fallback,
            "effect_observed",
        )
        if not ordered:
            order_evidence = {}
        if not action_issued:
            action_evidence = {}
        if not engaged:
            movement_evidence = {}
            engagement_evidence = {}
    terminal = operation_telemetry.get("terminal")
    terminal_payload = dict(terminal) if isinstance(terminal, Mapping) else {}
    terminal_state = str(
        terminal_payload.get("state")
        or terminal_payload.get("status")
        or operation_telemetry.get("terminal_state")
        or operation_telemetry.get("state")
        or ""
    ).strip().lower()
    director_status = str(
        operation_telemetry.get("status", "") or ""
    ).strip().lower()
    blocked_reason = str(
        operation_telemetry.get("blocked_reason", "") or ""
    ).strip().lower()
    if not terminal_state:
        if _truthy(operation_telemetry.get("cancelled")):
            terminal_state = "cancelled"
        elif director_status in {
            "completed",
            "cancelled",
            "canceled",
            "expired",
            "failed",
            "rejected",
            "superseded",
        }:
            terminal_state = director_status
        elif _truthy(operation_telemetry.get("completed")):
            terminal_state = "completed"
        elif director_status == "blocked":
            if blocked_reason in EXPIRY_OPERATION_REASONS:
                terminal_state = "expired"
            elif (
                blocked_reason in HARD_OPERATION_BLOCK_REASONS
                or blocked_reason not in TRANSIENT_OPERATION_BLOCK_REASONS
            ):
                terminal_state = "blocked"
        elif director_status in HARD_OPERATION_STATUSES:
            terminal_state = director_status
    completed = terminal_state in {"completed", "succeeded", "success"}
    superseded = terminal_state in {"superseded", "replaced", "cancelled", "canceled"}
    expired = terminal_state == "expired"
    blocked = terminal_state in {"blocked", "failed", "rejected"} or expired
    effect_observed = moving or engaged

    stages: list[dict[str, object]] = []
    if (
        received
        or assigned
        or ordered
        or action_issued
        or effect_observed
        or terminal_state
    ):
        stages.extend(
            (
                {
                    "name": "parsed",
                    "ok": True,
                    "manager": "OperationDirector",
                    "evidence": received_evidence,
                },
                {
                    "name": "reduced",
                    "ok": True,
                    "manager": "OperationDirector",
                    "evidence": {"operation_id": operation_id},
                },
                {
                    "name": "consumed_by_manager",
                    "ok": True,
                    "manager": "OperationDirector",
                    "evidence": {"operation_id": operation_id},
                },
            )
        )
    if assigned:
        stages.append(
            {
                "name": "queued_or_assigned",
                "ok": True,
                "manager": "OperationDirector",
                "evidence": assignment_evidence,
            }
        )
    if ordered:
        stages.append(
            {
                "name": "order_issued",
                "ok": True,
                "manager": "OperationDirector",
                "evidence": order_evidence,
            }
        )
    if action_issued:
        stages.append(
            {
                "name": "action_issued",
                "ok": True,
                "manager": "OperationDirector",
                "evidence": action_evidence,
            }
        )
    if effect_observed:
        effect_evidence = {
            "operation_id": operation_id,
            "movement": movement_evidence if moving else {},
            "engagement": engagement_evidence if engaged else {},
            "confirmation_effect": (
                "engagement observed"
                if engaged
                else "movement observed"
            ),
        }
        stages.append(
            {
                "name": "effect_observed",
                "ok": True,
                "manager": "OperationDirector",
                "evidence": effect_evidence,
            }
        )

    state = "published"
    if received:
        state = "consumed_by_manager"
    if assigned:
        state = "queued_or_assigned"
    if ordered:
        state = "order_issued"
    if action_issued:
        state = "action_issued"
    if effect_observed:
        state = "effect_observed"
    if terminal_state:
        state = terminal_state
    blocker_reason = str(
        terminal_payload.get("reason")
        or operation_telemetry.get("blocked_reason")
        or operation_telemetry.get("blocker_reason")
        or operation_telemetry.get("reason")
        or ""
    )
    return {
        "command_id": update_id,
        "operation_id": operation_id,
        "operation_generation": operation_generation,
        "state": state,
        "completed": completed,
        "failed": blocked,
        "expired": expired,
        "superseded": superseded,
        "blocker_manager": "OperationDirector" if blocked else "",
        "blocker_reason": blocker_reason,
        "stages": stages,
        "terminal_cleanup": terminal_cleanup,
        "telemetry": dict(operation_telemetry),
    }


def _micromachine_operation_mission(
    operation_update: Mapping[str, object],
) -> str:
    vector = _mapping_child(operation_update, "vector")
    tactical_task = _mapping_child(vector, "tactical_task")
    task_type = str(tactical_task.get("task_type", "") or "").lower()
    goal = str(vector.get("goal", "") or "").lower()
    command_layer = str(vector.get("command_layer", "") or "").lower()
    if "scout" in task_type or any(
        token in goal for token in ("scout", "recon", "정찰", "탐색")
    ):
        return "scouting"
    if any(token in task_type for token in ("attack", "pressure", "harass", "contain")):
        return "attack"
    if any(token in goal for token in ("attack", "pressure", "rush", "공격", "압박", "러시", "러쉬")):
        return "attack"
    if any(token in goal for token in ("defend", "hold", "수비", "방어", "사수")):
        return "defense"
    if _mapping_child(vector, "emergency"):
        return "emergency"
    if command_layer == "macro" or _mapping_child(vector, "production"):
        return "production"
    return command_layer or "operation"


def _micromachine_operation_disposition(
    execution: Mapping[str, object],
    *,
    active: bool,
    transport_status: str,
) -> str:
    state = str(execution.get("state", "") or "").strip().lower()
    if execution.get("superseded") is True or state in {
        "superseded",
        "replaced",
        "cancelled",
        "canceled",
    }:
        return "superseded"
    if execution.get("expired") is True or state == "expired":
        return "expired"
    if execution.get("failed") is True or state in {"blocked", "failed", "rejected"}:
        return "blocked"
    if execution.get("completed") is True or state in {
        "completed",
        "succeeded",
        "success",
    }:
        return "completed"
    if transport_status in {"publish_failed", "refused", "clarification_required"}:
        return "blocked"
    return "active" if active else "pending"


def _micromachine_operation_status_payload(
    operation_update: Mapping[str, object],
    *,
    operation_id: str,
    operation_count: int,
    active: bool,
    telemetry: object | None,
    telemetry_archive: Sequence[object],
    blackboard_dir: str,
    result_item: Mapping[str, object] | None,
    compile_result: Mapping[str, object] | None,
) -> dict[str, object]:
    update_id = str(operation_update.get("update_id", "") or "").strip()
    operation_vector = _mapping_child(operation_update, "vector")
    operation_generation = (
        operation_vector.get("generation")
        if type(operation_vector.get("generation")) is int
        and int(operation_vector.get("generation")) > 0
        else 1
    )
    explicit_operation_identity = bool(
        str(operation_vector.get("operation_id", "") or "").strip()
        and type(operation_vector.get("generation")) is int
        and int(operation_vector["generation"]) > 0
    )
    telemetry_document = _telemetry_to_mapping(telemetry)
    issued_at_frame, deadline_frame = (
        _micromachine_operation_evidence_window(
            operation_update,
            operation_vector,
        )
    )
    operation_telemetry_document, operation_telemetry = (
        _micromachine_operation_telemetry_document(
            telemetry_document,
            update_id=update_id,
            operation_id=operation_id,
            operation_generation=operation_generation,
            issued_at_frame=issued_at_frame,
            deadline_frame=deadline_frame,
        )
    )
    operation_telemetry_is_current = operation_telemetry is not None
    archived_operation_matches: list[
        tuple[int, dict[str, object], dict[str, object]]
    ] = []
    for archived_telemetry in telemetry_archive:
        archived_document = _telemetry_to_mapping(archived_telemetry)
        if not archived_document:
            continue
        archived_operation_document, archived_operation = (
            _micromachine_operation_telemetry_document(
                archived_document,
                update_id=update_id,
                operation_id=operation_id,
                operation_generation=operation_generation,
                issued_at_frame=issued_at_frame,
                deadline_frame=deadline_frame,
            )
        )
        if archived_operation is None:
            continue
        archived_frame = _int_or_none(
            archived_operation_document.get("frame")
        ) or 0
        archived_operation_matches.append(
            (
                archived_frame,
                archived_operation_document,
                archived_operation,
            )
        )
    if (
        operation_telemetry is None
        or operation_telemetry_document.get("_pending_only") is True
    ) and archived_operation_matches:
        (
            _archived_frame,
            operation_telemetry_document,
            operation_telemetry,
        ) = max(
            archived_operation_matches,
            key=lambda item: item[0],
        )
        operation_telemetry_is_current = False
    active_operation_generation = operation_generation
    if (
        operation_telemetry is not None
        and operation_telemetry.get("edit_rejected") is True
        and type(operation_telemetry.get("active_generation")) is int
        and int(operation_telemetry["active_generation"]) > 0
    ):
        active_operation_generation = int(
            operation_telemetry["active_generation"]
        )
    execution_owner_update_id = str(
        operation_telemetry.get(
            "operation_console_execution_owner_update_id",
            "",
        )
        if isinstance(operation_telemetry, Mapping)
        else ""
    ).strip() or update_id
    execution_owner_vector = dict(
        _mapping_child(operation_telemetry or {}, "operation_vector")
        or _mapping_child(operation_telemetry or {}, "vector")
        or operation_vector
    )
    execution_owner_vector["operation_id"] = operation_id
    execution_owner_vector["generation"] = active_operation_generation
    current_family_evidence = (
        list(
            operation_family_evidence(
                operation_telemetry,
                expected_update_id=execution_owner_update_id,
                expected_operation_id=operation_id,
                expected_generation=active_operation_generation,
            )
        )
        if operation_telemetry is not None
        else []
    )
    archived_family_evidence: list[dict[str, object]] = []
    archived_effect_frame = 0
    for (
        archived_frame,
        _archived_operation_document,
        archived_operation,
    ) in archived_operation_matches:
        archived_effect_frame = max(
            archived_effect_frame,
            archived_frame,
        )
        for row in operation_family_evidence(
            archived_operation,
            expected_update_id=execution_owner_update_id,
            expected_operation_id=operation_id,
            expected_generation=active_operation_generation,
        ):
            if row.get("effect") is not True:
                continue
            archived_family_evidence.append(row)
            effect_frame = row.get("effect_frame")
            if type(effect_frame) is int:
                archived_effect_frame = max(
                    archived_effect_frame,
                    effect_frame,
                )
    aggregate_family_evidence = list(
        operation_family_evidence(
            {
                "family_evidence": [
                    *current_family_evidence,
                    *archived_family_evidence,
                ]
            },
            expected_update_id=execution_owner_update_id,
            expected_operation_id=operation_id,
            expected_generation=active_operation_generation,
        )
    )
    if aggregate_family_evidence:
        if operation_telemetry is None:
            operation_telemetry = {
                "operation_id": operation_id,
                "generation": active_operation_generation,
                "family_evidence": aggregate_family_evidence,
            }
            telemetry_active_ids = _string_list(
                telemetry_document.get("active_modulation_ids", ())
            )
            if update_id and update_id not in telemetry_active_ids:
                telemetry_active_ids.append(update_id)
            operation_telemetry_document = {
                "frame": archived_effect_frame,
                "active_modulation_ids": telemetry_active_ids,
                "managers": {
                    "OperationDirector": dict(operation_telemetry)
                },
            }
        else:
            operation_telemetry = dict(operation_telemetry)
            operation_telemetry["family_evidence"] = (
                aggregate_family_evidence
            )
            operation_telemetry_document = dict(
                operation_telemetry_document
            )
            operation_telemetry_document["managers"] = {
                "OperationDirector": dict(operation_telemetry)
            }
    consumption_status = _micromachine_consumption_status(
        operation_update if active else None,
        telemetry,
    )
    telemetry_active_ids = set(
        _string_list(telemetry_document.get("active_modulation_ids", ()))
    )
    issued_at_frame = operation_update.get("issued_at_frame")
    if (
        active
        and type(issued_at_frame) is not int
        and update_id
        and update_id in telemetry_active_ids
    ):
        consumption_status = "consumed"
    evidence_log_snippets = _micromachine_recent_tactical_log_snippets(
        blackboard_dir,
        update_id=update_id,
        limit=None,
    )
    use_legacy_telemetry = operation_count == 1 and operation_telemetry is None
    intervention = _micromachine_intervention_summary(
        operation_update,
        telemetry if use_legacy_telemetry else operation_telemetry_document,
        consumption_status=consumption_status,
        log_snippets=evidence_log_snippets[-8:],
        evidence_log_snippets=evidence_log_snippets,
        compile_result=compile_result,
    )
    result_intervention = (
        result_item.get("intervention")
        if isinstance(result_item, Mapping)
        else None
    )
    if (
        not active
        and operation_telemetry is None
        and isinstance(result_intervention, Mapping)
    ):
        result_execution = result_intervention.get("command_execution")
        if (
            isinstance(result_execution, Mapping)
            and (
                not explicit_operation_identity
                or _micromachine_operation_execution_matches(
                    result_execution,
                    update_id=update_id,
                    operation_id=operation_id,
                    operation_generation=active_operation_generation,
                )
            )
        ):
            intervention = dict(result_intervention)
    fallback_execution = intervention.get("command_execution")
    if not isinstance(fallback_execution, Mapping):
        fallback_execution = {}
    operation_requires_ability_evidence = (
        operation_requires_specific_family_ability_evidence(
            operation_vector
        )
    )
    if (
        current_family_evidence
        or operation_requires_ability_evidence
    ) and operation_telemetry_document.get("_pending_only") is not True:
        execution_owner_update = dict(operation_update)
        execution_owner_update["update_id"] = execution_owner_update_id
        execution_owner_update["vector"] = execution_owner_vector
        strict_operation_execution = _micromachine_strict_operation_execution(
            execution_owner_update,
            operation_id=operation_id,
            operation_generation=active_operation_generation,
            operation_telemetry_document=operation_telemetry_document,
        )
        if (
            strict_operation_execution
            and _micromachine_execution_has_active_family_contract(
                strict_operation_execution
            )
        ):
            fallback_execution = strict_operation_execution
    command_execution = _micromachine_operation_command_execution(
        update_id=execution_owner_update_id,
        operation_id=operation_id,
        operation_generation=active_operation_generation,
        operation_telemetry=operation_telemetry or {},
        fallback=fallback_execution,
        strict_identity=explicit_operation_identity,
    )
    intervention = dict(intervention)
    intervention["command_execution"] = command_execution
    intervention["operation_id"] = operation_id
    vector = _mapping_child(operation_update, "vector")
    command_text = ""
    if isinstance(result_item, Mapping):
        command_text = str(result_item.get("command_text", "") or "")
    if not command_text and isinstance(compile_result, Mapping):
        command_text = str(compile_result.get("command_text", "") or "")
    command_text = command_text or str(vector.get("goal", "") or "")
    scope_id = (
        _micromachine_blackboard_scope_id(blackboard_dir)
        if blackboard_dir
        else str(
            result_item.get("blackboard_scope_id", "")
            if isinstance(result_item, Mapping)
            else ""
        )
    )
    transport_status = (
        "published"
        if active
        else str(
            (
                result_item.get("status")
                if isinstance(result_item, Mapping)
                else None
            )
            or "pending"
        )
    )
    disposition = _micromachine_operation_disposition(
        command_execution,
        active=active,
        transport_status=transport_status,
    )
    telemetry_frame = intervention.get("telemetry_frame")
    telemetry_current = bool(
        operation_telemetry_is_current
        and type(telemetry_frame) is int
        and consumption_status == "consumed"
    )
    operation_key = (
        f"{scope_id}\0{operation_id}\0{active_operation_generation}"
        if scope_id
        else f"{operation_id}\0{active_operation_generation}"
    )
    operation_edit = _mapping_child(operation_vector, "operation_edit")
    if operation_telemetry is not None:
        telemetry_edit = {
            "action": operation_telemetry.get("edit_action"),
            "counterpart_operation_id": operation_telemetry.get(
                "edit_counterpart_operation_id"
            ),
            "before_count": operation_telemetry.get("edit_before_count"),
            "after_count": operation_telemetry.get("edit_after_count"),
            "transferred_in_count": operation_telemetry.get(
                "transferred_in_count"
            ),
            "transferred_out_count": operation_telemetry.get(
                "transferred_out_count"
            ),
            "resolution": operation_telemetry.get("edit_resolution"),
            "blocker": operation_telemetry.get("edit_blocker"),
        }
        operation_edit = {
            **operation_edit,
            **{
                key: value
                for key, value in telemetry_edit.items()
                if value not in {None, ""}
            },
        }
    requirement_progress = (
        operation_telemetry.get("requirement_progress")
        if isinstance(operation_telemetry, Mapping)
        else None
    )
    normalized_requirement_progress = [
        {
            "unit_type": str(requirement.get("unit_type", "") or ""),
            "role": str(requirement.get("role", "") or ""),
            "target_count": max(
                0,
                _int_or_none(requirement.get("target_count")) or 0,
            ),
            "assigned_count": max(
                0,
                _int_or_none(requirement.get("assigned_count")) or 0,
            ),
            "represented_count": max(
                0,
                _int_or_none(requirement.get("represented_count")) or 0,
            ),
            "completed_count": max(
                0,
                _int_or_none(requirement.get("completed_count")) or 0,
            ),
            "in_progress_count": max(
                0,
                _int_or_none(requirement.get("in_progress_count")) or 0,
            ),
            "queued_count": max(
                0,
                _int_or_none(requirement.get("queued_count")) or 0,
            ),
            "missing_count": max(
                0,
                _int_or_none(requirement.get("missing_count")) or 0,
            ),
            "production_blocker": str(
                requirement.get("production_blocker", "") or ""
            ),
            "prerequisites": _string_list(
                requirement.get("prerequisites", ())
            ),
            "missing_prerequisites": _string_list(
                requirement.get("missing_prerequisites", ())
            ),
        }
        for requirement in (
            requirement_progress
            if isinstance(requirement_progress, Sequence)
            and not isinstance(requirement_progress, (str, bytes))
            else ()
        )
        if isinstance(requirement, Mapping)
    ]
    operation_convergence = {
        "status": str(
            operation_telemetry.get("status", "")
            if isinstance(operation_telemetry, Mapping)
            else ""
        ),
        "blocker": str(
            operation_telemetry.get("blocked_reason", "")
            if isinstance(operation_telemetry, Mapping)
            else ""
        ),
        "target_count": max(
            0,
            _int_or_none(
                operation_telemetry.get("requirement_target_count")
                if isinstance(operation_telemetry, Mapping)
                else None
            )
            or 0,
        ),
        "represented_count": max(
            0,
            _int_or_none(
                operation_telemetry.get(
                    "requirement_represented_count",
                )
                if isinstance(operation_telemetry, Mapping)
                else None
            )
            or 0,
        ),
        "missing_count": max(
            0,
            _int_or_none(
                operation_telemetry.get("requirement_missing_count")
                if isinstance(operation_telemetry, Mapping)
                else None
            )
            or 0,
        ),
        "requirements": normalized_requirement_progress,
    }
    family_evidence = _public_operation_family_evidence(
        aggregate_family_evidence
    )
    payload = {
        "operation_key": operation_key,
        "operation_id": operation_id,
        "operation_generation": active_operation_generation,
        "requested_operation_generation": operation_generation,
        "update_id": update_id,
        "operation_console_execution_owner_update_id": (
            execution_owner_update_id
        ),
        "operation_console_execution_owner_vector": execution_owner_vector,
        "command_text": command_text,
        "mission": _micromachine_operation_mission(operation_update),
        "active": active,
        "transport_status": transport_status,
        "consumption_status": consumption_status,
        "compile_result": (
            dict(compile_result)
            if isinstance(compile_result, Mapping)
            else {}
        ),
        "update": dict(operation_update),
        "intervention": intervention,
        "command_queue": (
            dict(result_item.get("command_queue"))
            if isinstance(result_item, Mapping)
            and isinstance(result_item.get("command_queue"), Mapping)
            else {}
        ),
        "telemetry_frame": telemetry_frame,
        "telemetry_current": telemetry_current,
        "disposition": disposition,
        "operation_edit": operation_edit,
        "operation_convergence": operation_convergence,
        "squad_order": str(
            operation_telemetry.get("squad_order", "")
            if isinstance(operation_telemetry, Mapping)
            else ""
        ),
        "family_evidence": family_evidence,
    }
    public_payload = _public_micromachine_runtime_payload(payload)
    return (
        dict(public_payload)
        if isinstance(public_payload, Mapping)
        else {}
    )


def _public_operation_family_evidence(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    public_rows: list[dict[str, object]] = []
    for row in rows:
        public_row = _public_micromachine_runtime_payload(row)
        if isinstance(public_row, Mapping):
            public_rows.append(dict(public_row))
    return public_rows


def _battlefield_operation_index(
    battlefield_overview: Mapping[str, object] | None,
) -> dict[tuple[str, str, int], dict[str, object]]:
    if not isinstance(battlefield_overview, Mapping):
        return {}
    operations = battlefield_overview.get("operation_ownership")
    if not isinstance(operations, Sequence) or isinstance(
        operations,
        (str, bytes, bytearray),
    ):
        return {}
    index: dict[tuple[str, str, int], dict[str, object]] = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        identity = _mapping_child(operation, "identity")
        update_id = str(identity.get("update_id", "") or "").strip()
        operation_id = str(operation.get("operation_id", "") or "").strip()
        generation = operation.get("generation")
        if (
            not update_id
            or not operation_id
            or type(generation) is not int
            or int(generation) <= 0
        ):
            continue
        index[(update_id, operation_id, int(generation))] = dict(operation)
    return index


def _attach_battlefield_operation_projections(
    operations: Sequence[Mapping[str, object]],
    battlefield_overview: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    index = _battlefield_operation_index(battlefield_overview)
    attached: list[dict[str, object]] = []
    for operation in operations:
        item = dict(operation)
        update_id = str(
            item.get(
                "operation_console_execution_owner_update_id",
                "",
            )
            or item.get("update_id", "")
            or ""
        ).strip()
        operation_id = str(item.get("operation_id", "") or "").strip()
        generation = item.get("operation_generation")
        projection = (
            index.get((update_id, operation_id, int(generation)))
            if update_id
            and operation_id
            and type(generation) is int
            and int(generation) > 0
            else None
        )
        item["battlefield_operation"] = (
            dict(projection) if projection is not None else None
        )
        attached.append(item)
    return attached


_PUBLIC_BATTLEFIELD_SCALAR: Final[object] = object()
_PUBLIC_BATTLEFIELD_DROP: Final[object] = object()
_PUBLIC_BATTLEFIELD_IDENTITY_SCHEMA: Final[Mapping[str, object]] = {
    "update_id": _PUBLIC_BATTLEFIELD_SCALAR,
    "scope": _PUBLIC_BATTLEFIELD_SCALAR,
    "session_epoch": _PUBLIC_BATTLEFIELD_SCALAR,
    "operation_id": _PUBLIC_BATTLEFIELD_SCALAR,
    "generation": _PUBLIC_BATTLEFIELD_SCALAR,
    "stage": _PUBLIC_BATTLEFIELD_SCALAR,
    "game_frame": _PUBLIC_BATTLEFIELD_SCALAR,
}
_PUBLIC_BATTLEFIELD_OPERATION_SCHEMA: Final[Mapping[str, object]] = {
    "identity": _PUBLIC_BATTLEFIELD_IDENTITY_SCHEMA,
    "operation_id": _PUBLIC_BATTLEFIELD_SCALAR,
    "generation": _PUBLIC_BATTLEFIELD_SCALAR,
    "operation_route": {
        "requested_route_type": _PUBLIC_BATTLEFIELD_SCALAR,
        "applied_route_type": _PUBLIC_BATTLEFIELD_SCALAR,
        "location_intent": _PUBLIC_BATTLEFIELD_SCALAR,
        "target_type": _PUBLIC_BATTLEFIELD_SCALAR,
        "resolved_target_label": _PUBLIC_BATTLEFIELD_SCALAR,
        "target_x": _PUBLIC_BATTLEFIELD_SCALAR,
        "target_y": _PUBLIC_BATTLEFIELD_SCALAR,
        "target_evidence": _PUBLIC_BATTLEFIELD_SCALAR,
    },
    "operation_lifetime": {
        "mode": _PUBLIC_BATTLEFIELD_SCALAR,
        "completion_state": _PUBLIC_BATTLEFIELD_SCALAR,
        "completion_conditions": (_PUBLIC_BATTLEFIELD_SCALAR,),
        "duration_seconds": _PUBLIC_BATTLEFIELD_SCALAR,
        "issued_at_frame": _PUBLIC_BATTLEFIELD_SCALAR,
        "deadline_frame": _PUBLIC_BATTLEFIELD_SCALAR,
        "standing": _PUBLIC_BATTLEFIELD_SCALAR,
        "completed": _PUBLIC_BATTLEFIELD_SCALAR,
        "completion_reason": _PUBLIC_BATTLEFIELD_SCALAR,
        "completed_frame": _PUBLIC_BATTLEFIELD_SCALAR,
    },
    "operation_ownership": {
        "owner_count": _PUBLIC_BATTLEFIELD_SCALAR,
        "integrity_status": _PUBLIC_BATTLEFIELD_SCALAR,
    },
    "operation_launch_policy": {
        "min_units": _PUBLIC_BATTLEFIELD_SCALAR,
        "max_units": _PUBLIC_BATTLEFIELD_SCALAR,
        "allow_partial_requested": _PUBLIC_BATTLEFIELD_SCALAR,
        "strict_scope": _PUBLIC_BATTLEFIELD_SCALAR,
        "partial_launch_allowed": _PUBLIC_BATTLEFIELD_SCALAR,
        "partial_launch_safe": _PUBLIC_BATTLEFIELD_SCALAR,
        "launch_count": _PUBLIC_BATTLEFIELD_SCALAR,
        "missing_count": _PUBLIC_BATTLEFIELD_SCALAR,
        "decision": _PUBLIC_BATTLEFIELD_SCALAR,
        "blocker": _PUBLIC_BATTLEFIELD_SCALAR,
        "recommended_choices": (_PUBLIC_BATTLEFIELD_SCALAR,),
        "safety_evidence": {
            "evaluated_at_frame": _PUBLIC_BATTLEFIELD_SCALAR,
            "protected_defense_minimum_respected": _PUBLIC_BATTLEFIELD_SCALAR,
            "source_operation_minimum_respected": _PUBLIC_BATTLEFIELD_SCALAR,
            "transfer_admission": _PUBLIC_BATTLEFIELD_SCALAR,
            "emergency_preemption": _PUBLIC_BATTLEFIELD_SCALAR,
        },
    },
    "operation_completion": {
        "movement_observed": _PUBLIC_BATTLEFIELD_SCALAR,
        "engagement_observed": _PUBLIC_BATTLEFIELD_SCALAR,
        "target_reached": _PUBLIC_BATTLEFIELD_SCALAR,
        "terminal": _PUBLIC_BATTLEFIELD_SCALAR,
        "state": _PUBLIC_BATTLEFIELD_SCALAR,
        "reason": _PUBLIC_BATTLEFIELD_SCALAR,
        "frame": _PUBLIC_BATTLEFIELD_SCALAR,
        "generation": _PUBLIC_BATTLEFIELD_SCALAR,
    },
    "operation_transfer_selection": {
        "present": _PUBLIC_BATTLEFIELD_SCALAR,
        "edit_resolution": _PUBLIC_BATTLEFIELD_SCALAR,
        "identity_valid": _PUBLIC_BATTLEFIELD_SCALAR,
        "blocker": _PUBLIC_BATTLEFIELD_SCALAR,
        "successful_write_acknowledgement": {
            "acknowledged": _PUBLIC_BATTLEFIELD_SCALAR,
            "acknowledged_frame": _PUBLIC_BATTLEFIELD_SCALAR,
        },
    },
}
_PUBLIC_BATTLEFIELD_TRANSFER_INPUT_SCHEMA: Final[Mapping[str, object]] = {
    "requested": _PUBLIC_BATTLEFIELD_SCALAR,
    "requested_count": _PUBLIC_BATTLEFIELD_SCALAR,
    "source_owner_id": _PUBLIC_BATTLEFIELD_SCALAR,
    "action": _PUBLIC_BATTLEFIELD_SCALAR,
    "requested_generation": _PUBLIC_BATTLEFIELD_SCALAR,
    "counterpart_operation_id": _PUBLIC_BATTLEFIELD_SCALAR,
    "counterpart_action": _PUBLIC_BATTLEFIELD_SCALAR,
    "counterpart_generation": _PUBLIC_BATTLEFIELD_SCALAR,
    "requested_source_generation": _PUBLIC_BATTLEFIELD_SCALAR,
    "requested_counterpart_generation": _PUBLIC_BATTLEFIELD_SCALAR,
    "edit_resolution": _PUBLIC_BATTLEFIELD_SCALAR,
    "counterpart_present": _PUBLIC_BATTLEFIELD_SCALAR,
    "counterpart_pending": _PUBLIC_BATTLEFIELD_SCALAR,
    "reciprocal_action": _PUBLIC_BATTLEFIELD_SCALAR,
    "reciprocal_counterpart": _PUBLIC_BATTLEFIELD_SCALAR,
    "reciprocal_generation": _PUBLIC_BATTLEFIELD_SCALAR,
    "reciprocal_count": _PUBLIC_BATTLEFIELD_SCALAR,
    "source_active": _PUBLIC_BATTLEFIELD_SCALAR,
    "destination_active": _PUBLIC_BATTLEFIELD_SCALAR,
    "ownership_integrity": _PUBLIC_BATTLEFIELD_SCALAR,
    "operation_assignments_match": _PUBLIC_BATTLEFIELD_SCALAR,
    "squad_assignments_match": _PUBLIC_BATTLEFIELD_SCALAR,
    "action_assignments_match": _PUBLIC_BATTLEFIELD_SCALAR,
    "role_assignments_match": _PUBLIC_BATTLEFIELD_SCALAR,
    "atomic_revalidation_ready": _PUBLIC_BATTLEFIELD_SCALAR,
}
_PUBLIC_BATTLEFIELD_SCHEMA: Final[Mapping[str, object]] = {
    "schema_version": _PUBLIC_BATTLEFIELD_SCALAR,
    "authority": _PUBLIC_BATTLEFIELD_SCALAR,
    "identity": _PUBLIC_BATTLEFIELD_IDENTITY_SCHEMA,
    "eligible_combat_count": _PUBLIC_BATTLEFIELD_SCALAR,
    "explicit_operation_owned_count": _PUBLIC_BATTLEFIELD_SCALAR,
    "autonomous_owned_count": _PUBLIC_BATTLEFIELD_SCALAR,
    "unassigned_count": _PUBLIC_BATTLEFIELD_SCALAR,
    "duplicate_owner_count": _PUBLIC_BATTLEFIELD_SCALAR,
    "operation_ownership": (_PUBLIC_BATTLEFIELD_OPERATION_SCHEMA,),
    "autonomous_ownership": (
        {
            "owner_id": _PUBLIC_BATTLEFIELD_SCALAR,
            "owner_count": _PUBLIC_BATTLEFIELD_SCALAR,
            "integrity_status": _PUBLIC_BATTLEFIELD_SCALAR,
        },
    ),
    "bases": (
        {
            "base_id": _PUBLIC_BATTLEFIELD_SCALAR,
            "semantic_anchor": _PUBLIC_BATTLEFIELD_SCALAR,
            "base_readiness": {
                "readiness_state": _PUBLIC_BATTLEFIELD_SCALAR,
                "reason": _PUBLIC_BATTLEFIELD_SCALAR,
                "ground_threat": _PUBLIC_BATTLEFIELD_SCALAR,
                "air_threat": _PUBLIC_BATTLEFIELD_SCALAR,
                "observed_enemy_strength": _PUBLIC_BATTLEFIELD_SCALAR,
                "last_evidence_frame": _PUBLIC_BATTLEFIELD_SCALAR,
                "evidence_class": _PUBLIC_BATTLEFIELD_SCALAR,
                "assigned_defender_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "ground_capable_defender_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "air_capable_defender_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "required_defender_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "required_ground_defender_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "required_air_defender_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "protected_minimum": (
                    {
                        "family": _PUBLIC_BATTLEFIELD_SCALAR,
                        "role": _PUBLIC_BATTLEFIELD_SCALAR,
                        "count": _PUBLIC_BATTLEFIELD_SCALAR,
                    },
                ),
            },
        },
    ),
    "transfer_availability": {
        "evaluated_at_frame": _PUBLIC_BATTLEFIELD_SCALAR,
        "atomic_revalidation_required": _PUBLIC_BATTLEFIELD_SCALAR,
        "entries": (
            {
                "source_owner_id": _PUBLIC_BATTLEFIELD_SCALAR,
                "source_owner_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "protected_minimum": _PUBLIC_BATTLEFIELD_SCALAR,
                "transferable_count": _PUBLIC_BATTLEFIELD_SCALAR,
                "transfer_safe": _PUBLIC_BATTLEFIELD_SCALAR,
                "atomic_runtime_blocker": _PUBLIC_BATTLEFIELD_SCALAR,
                "recommended_resolution_choices": (
                    _PUBLIC_BATTLEFIELD_SCALAR,
                ),
                "safety_evidence": {
                    "evaluated_at_frame": _PUBLIC_BATTLEFIELD_SCALAR,
                    "protected_minimum_respected": _PUBLIC_BATTLEFIELD_SCALAR,
                    "atomic_revalidation_required": _PUBLIC_BATTLEFIELD_SCALAR,
                },
                "atomic_revalidation_inputs": (
                    _PUBLIC_BATTLEFIELD_TRANSFER_INPUT_SCHEMA
                ),
            },
        ),
    },
}


def _micromachine_sensitive_public_key(key: object) -> bool:
    normalized = str(key or "").strip().lower()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    parts = {
        part for part in re.split(r"[^a-z0-9]+", normalized) if part
    }
    if normalized.startswith("private_"):
        return True
    if compact in {
        "apikey",
        "accesskey",
        "privatekey",
        "clientsecret",
        "authorization",
        "authtoken",
        "password",
        "passwd",
        "credential",
        "credentials",
        "cookie",
    }:
        return True
    return bool(
        parts
        & {
            "password",
            "passwd",
            "secret",
            "token",
            "credential",
            "credentials",
        }
    )


def _public_battlefield_projection_value(
    value: object,
    schema: object,
) -> object:
    if schema is _PUBLIC_BATTLEFIELD_SCALAR:
        if isinstance(value, str):
            return _redact_micromachine_internal_unit_tag_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _PUBLIC_BATTLEFIELD_DROP
    if isinstance(schema, Mapping):
        if not isinstance(value, Mapping):
            return _PUBLIC_BATTLEFIELD_DROP
        projected: dict[str, object] = {}
        for key, child_schema in schema.items():
            if key not in value or _micromachine_sensitive_public_key(key):
                continue
            child = _public_battlefield_projection_value(
                value[key],
                child_schema,
            )
            if child is not _PUBLIC_BATTLEFIELD_DROP:
                projected[key] = child
        return projected
    if isinstance(schema, tuple) and len(schema) == 1:
        if not isinstance(value, (list, tuple)):
            return _PUBLIC_BATTLEFIELD_DROP
        projected_items = []
        for item in value:
            projected = _public_battlefield_projection_value(item, schema[0])
            if projected is not _PUBLIC_BATTLEFIELD_DROP:
                projected_items.append(projected)
        return projected_items
    return _PUBLIC_BATTLEFIELD_DROP


def _public_battlefield_overview_payload(value: object) -> object:
    return _public_battlefield_projection_value(
        value,
        _PUBLIC_BATTLEFIELD_SCHEMA,
    )


def _public_micromachine_runtime_payload(value: object) -> object:
    if isinstance(value, Mapping):
        public_payload: dict[object, object] = {}
        for key, item in value.items():
            if _micromachine_sensitive_public_key(key):
                continue
            if str(key or "").strip().lower() == "battlefield_overview":
                if item is None:
                    public_payload[key] = None
                    continue
                overview = _public_battlefield_overview_payload(item)
                if overview is not _PUBLIC_BATTLEFIELD_DROP:
                    public_payload[key] = overview
                continue
            if _micromachine_internal_unit_tag_key(key):
                continue
            if _public_micromachine_semantic_tag_key(key):
                semantic_tags = _public_micromachine_semantic_tag_value(item)
                if semantic_tags is _MICROMACHINE_DROP_PUBLIC_FIELD:
                    continue
                public_payload[key] = semantic_tags
                continue
            public_payload[key] = _public_micromachine_runtime_payload(item)
        return public_payload
    if isinstance(value, list):
        return [
            _public_micromachine_runtime_payload(item)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _public_micromachine_runtime_payload(item)
            for item in value
        )
    if isinstance(value, str):
        return _redact_micromachine_internal_unit_tag_text(value)
    return value


def _public_runtime_launcher_payload(
    value: Mapping[str, object],
) -> dict[str, object]:
    public_payload = _public_micromachine_runtime_payload(value)
    return dict(public_payload) if isinstance(public_payload, Mapping) else {}


_MICROMACHINE_INTERNAL_UNIT_TAG_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""(?imx)
    ['"]?
    (?:
        tag
        |tags
        |[a-z][a-z0-9_]*_tags?
    )
    ['"]?
    \s*[:=]\s*
    [^\r\n]*?
    (?=
        \s+['"]?[a-z][a-z0-9_]*['"]?\s*[:=]
        |[\r\n]
        |$
    )
    """
)

_MICROMACHINE_PUBLIC_SEMANTIC_TAG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "tags",
        "strategic_tags",
        "tech_path_tags",
        "expected_tags",
        "expected_profile_tags",
    }
)

_MICROMACHINE_DROP_PUBLIC_FIELD: Final[object] = object()
_MICROMACHINE_SEMANTIC_TAG_NUMERIC_WRAPPER_PATTERN: Final[re.Pattern[str]] = (
    re.compile(r"[\s\[\](){}<>,;|]+")
)
_MICROMACHINE_SEMANTIC_TAG_RAW_IDENTITY_PATTERN: Final[re.Pattern[str]] = (
    re.compile(
        r"""(?ix)
        (?:^|[^a-z0-9])
        (?:
            (?:unit|actor|owner|selected|assigned|commanded|target)
            (?:[_\s-]*tags?)?
            |
            tags?
        )
        [_:\s=#-]*\d+
        |
        (?<!\d)\d{4,}(?!\d)
        """
    )
)


def _redact_micromachine_internal_unit_tag_text(value: str) -> str:
    return _MICROMACHINE_INTERNAL_UNIT_TAG_TEXT_PATTERN.sub(
        "[internal unit identity]: [redacted]",
        value,
    )


def _public_micromachine_semantic_tag_key(key: object) -> bool:
    return str(key or "").strip().lower() in _MICROMACHINE_PUBLIC_SEMANTIC_TAG_KEYS


def _micromachine_safe_semantic_tag(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if _redact_micromachine_internal_unit_tag_text(value) != value:
        return False
    if _MICROMACHINE_SEMANTIC_TAG_RAW_IDENTITY_PATTERN.search(stripped):
        return False
    numeric_identity = _MICROMACHINE_SEMANTIC_TAG_NUMERIC_WRAPPER_PATTERN.sub(
        "",
        stripped,
    )
    return not numeric_identity.isdigit()


def _public_micromachine_semantic_tag_value(value: object) -> object:
    if isinstance(value, str):
        return value if _micromachine_safe_semantic_tag(value) else (
            _MICROMACHINE_DROP_PUBLIC_FIELD
        )
    if isinstance(value, list):
        return [
            item
            for item in value
            if isinstance(item, str)
            and _micromachine_safe_semantic_tag(item)
        ]
    if isinstance(value, tuple):
        return tuple(
            item
            for item in value
            if isinstance(item, str)
            and _micromachine_safe_semantic_tag(item)
        )
    return _MICROMACHINE_DROP_PUBLIC_FIELD


def _micromachine_internal_unit_tag_key(key: object) -> bool:
    normalized = str(key or "").strip().lower()
    return (
        normalized in {"tag", "unit_tags"}
        or normalized.endswith("_tag")
        or normalized.endswith("_unit_tags")
        or normalized.endswith("_units_tags")
        or normalized.endswith("_owner_tags")
        or (
            normalized.endswith("_tags")
            and normalized
            not in _MICROMACHINE_PUBLIC_SEMANTIC_TAG_KEYS
        )
        or normalized
        in {
            "owner_tags",
            "unassigned_tags",
            "transferable_tags",
        }
    )


def _micromachine_operation_evidence_window(
    operation_update: Mapping[str, object],
    operation_vector: Mapping[str, object],
) -> tuple[int, int]:
    issued_at_frame = (
        int(operation_vector.get("issued_at_frame"))
        if type(operation_vector.get("issued_at_frame")) is int
        else (
            int(operation_update.get("issued_at_frame"))
            if type(operation_update.get("issued_at_frame")) is int
            else 0
        )
    )
    issued_at_frame = max(0, issued_at_frame)
    lifetime = _mapping_child(operation_vector, "lifetime")
    lifetime_mode = str(lifetime.get("mode", "") or "").strip().lower()
    if lifetime_mode in {"standing_order", "until_cancelled"}:
        return issued_at_frame, 0
    for source in (operation_vector, lifetime, operation_update):
        for field_name in ("deadline_frame", "expires_at_frame"):
            value = source.get(field_name)
            if type(value) is int and int(value) > issued_at_frame:
                return issued_at_frame, int(value)
    tactical_task = _mapping_child(operation_vector, "tactical_task")
    scope = _mapping_child(operation_vector, "scope")
    duration_seconds = 0
    for source in (tactical_task, scope):
        value = source.get("duration_seconds")
        if type(value) is int and int(value) > 0:
            duration_seconds = int(value)
            break
    if duration_seconds <= 0:
        return issued_at_frame, 0
    return (
        issued_at_frame,
        issued_at_frame
        + duration_seconds * MICROMACHINE_GAME_LOOPS_PER_SECOND,
    )


def _micromachine_operations_payload(
    dashboard: Mapping[str, object],
    *,
    telemetry: object | None,
    telemetry_archive: Sequence[object] = (),
    blackboard_dir: str,
    compile_result: object | None,
    result_stream: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    updates = dashboard.get("active_updates")
    active_updates = [
        dict(update)
        for update in updates
        if isinstance(update, Mapping)
    ] if isinstance(updates, list) else []
    stream_items = [dict(item) for item in result_stream if isinstance(item, Mapping)]
    latest_compile = (
        dict(compile_result)
        if isinstance(compile_result, Mapping)
        else None
    )
    latest_compile_update_id = (
        str(latest_compile.get("update_id", "") or "").strip()
        if latest_compile is not None
        else ""
    )
    if latest_compile_update_id and not any(
        _micromachine_payload_update_id(item) == latest_compile_update_id
        for item in stream_items
    ):
        stream_items.append(
            {
                "status": str(latest_compile.get("status", "") or ""),
                "command_text": str(latest_compile.get("command_text", "") or ""),
                "compile_result": latest_compile,
            }
        )
    stream_by_update_id = {
        update_id: item
        for item in stream_items
        if (update_id := _micromachine_payload_update_id(item))
    }
    telemetry_active_update_ids = set(
        _string_list(
            _telemetry_to_mapping(telemetry).get(
                "active_modulation_ids",
                (),
            )
        )
    )
    operations: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str]] = set()
    active_update_ids: set[str] = set()
    for update in active_updates:
        update_id = str(update.get("update_id", "") or "").strip()
        if not update_id:
            continue
        active_update_ids.add(update_id)
        expanded = _micromachine_operation_updates(update)
        result_item = stream_by_update_id.get(update_id)
        scoped_compile = (
            _micromachine_compile_result_for_update(
                result_item.get("compile_result")
                if isinstance(result_item, Mapping)
                else latest_compile,
                update_id=update_id,
            )
        )
        for operation_id, operation_update in expanded:
            operations.append(
                _micromachine_operation_status_payload(
                    operation_update,
                    operation_id=operation_id,
                    operation_count=len(expanded),
                    active=True,
                    telemetry=telemetry,
                    telemetry_archive=telemetry_archive,
                    blackboard_dir=blackboard_dir,
                    result_item=result_item,
                    compile_result=scoped_compile,
                )
            )
            seen_keys.add((update_id, operation_id))

    for result_item in stream_items:
        update_id = _micromachine_payload_update_id(result_item)
        if not update_id or update_id in active_update_ids:
            continue
        result_is_active = update_id in telemetry_active_update_ids
        result_update = result_item.get("update")
        compile_payload = result_item.get("compile_result")
        if isinstance(result_update, Mapping):
            update = dict(result_update)
        else:
            vector = (
                compile_payload.get("vector")
                if isinstance(compile_payload, Mapping)
                else None
            )
            update = {
                "update_id": update_id,
                "vector": (
                    dict(vector)
                    if isinstance(vector, Mapping)
                    else {"goal": str(result_item.get("command_text", "") or "")}
                ),
                "manager_bias_domains": [],
            }
        update.setdefault("update_id", update_id)
        expanded = _micromachine_operation_updates(update)
        scoped_compile = _micromachine_compile_result_for_update(
            compile_payload,
            update_id=update_id,
        )
        for operation_id, operation_update in expanded:
            identity = (update_id, operation_id)
            if identity in seen_keys:
                continue
            operations.append(
                _micromachine_operation_status_payload(
                    operation_update,
                    operation_id=operation_id,
                    operation_count=len(expanded),
                    active=result_is_active,
                    telemetry=telemetry,
                    telemetry_archive=telemetry_archive,
                    blackboard_dir=blackboard_dir,
                    result_item=result_item,
                    compile_result=scoped_compile,
                )
            )
            seen_keys.add(identity)
    return operations


def _micromachine_operation_summary(
    operations: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    summary = {
        "total": len(operations),
        "active": 0,
        "scouting": 0,
        "attacking": 0,
        "blocked": 0,
        "completed": 0,
    }
    for operation in operations:
        disposition = str(operation.get("disposition", "") or "")
        mission = str(operation.get("mission", "") or "")
        if disposition == "active":
            summary["active"] += 1
        if mission == "scouting":
            summary["scouting"] += 1
        if mission == "attack":
            summary["attacking"] += 1
        if disposition in {"blocked", "expired", "superseded"}:
            summary["blocked"] += 1
        if disposition == "completed":
            summary["completed"] += 1
    return summary


def _micromachine_status_payload(
    dashboard: Mapping[str, object],
    *,
    telemetry: object | None = None,
    telemetry_archive: Sequence[object] = (),
    blackboard_dir: str = "",
    compile_result: object | None = None,
    result_stream: Sequence[Mapping[str, object]] = (),
    previous_battlefield_identity: (
        BattlefieldProjectionIdentity | Mapping[str, object] | None
    ) = None,
    previous_battlefield_payload_fingerprint: str = "",
    battlefield_projection: BattlefieldProjectionResult | None = None,
) -> dict[str, object]:
    """Promote latest blackboard state into the same top-level UI contract."""

    latest_telemetry_document = (
        _telemetry_to_mapping(telemetry)
        if telemetry is not None
        else None
    )
    if battlefield_projection is None:
        battlefield_projection = select_latest_battlefield_projection(
            latest_telemetry=latest_telemetry_document,
            telemetry_archive=tuple(
                _telemetry_to_mapping(entry)
                for entry in telemetry_archive
            ),
            expected_scope="battlefield",
            previous_identity=previous_battlefield_identity,
            previous_payload_fingerprint=(
                previous_battlefield_payload_fingerprint
            ),
        )
    updates = dashboard.get("active_updates")
    active_updates = updates if isinstance(updates, list) else []
    latest = (
        active_updates[0]
        if active_updates and isinstance(active_updates[0], Mapping)
        else None
    )
    consumption_status = _micromachine_consumption_status(latest, telemetry)
    update_id = str(latest.get("update_id", "") or "") if latest else ""
    evidence_log_snippets = _micromachine_recent_tactical_log_snippets(
        blackboard_dir,
        update_id=update_id,
        limit=None,
    )
    log_snippets = evidence_log_snippets[-8:]
    intervention_compile_result = _micromachine_compile_result_for_update(
        compile_result,
        update_id=update_id,
    )
    latest_request = _micromachine_latest_request_summary(
        compile_result,
        active_update_id=update_id,
        active_consumption_status=consumption_status,
    )
    command_queue = (
        dict(intervention_compile_result.get("command_queue"))
        if isinstance(intervention_compile_result, Mapping)
        and isinstance(intervention_compile_result.get("command_queue"), Mapping)
        else {}
    )
    intervention = _micromachine_intervention_summary(
        latest,
        telemetry,
        consumption_status=consumption_status,
        log_snippets=log_snippets,
        evidence_log_snippets=evidence_log_snippets,
        compile_result=intervention_compile_result,
    )
    if command_queue:
        intervention["command_queue"] = command_queue
    operations = _micromachine_operations_payload(
        dashboard,
        telemetry=telemetry,
        telemetry_archive=telemetry_archive,
        blackboard_dir=blackboard_dir,
        compile_result=compile_result,
        result_stream=result_stream,
    )
    battlefield_overview = (
        dict(battlefield_projection.battlefield_overview)
        if battlefield_projection.ok
        and battlefield_projection.battlefield_overview is not None
        else None
    )
    operations = _attach_battlefield_operation_projections(
        operations,
        battlefield_overview,
    )
    representative = next(
        (operation for operation in operations if operation.get("active") is True),
        None,
    )
    if isinstance(representative, Mapping):
        representative_update = representative.get("update")
        representative_intervention = representative.get("intervention")
        if isinstance(representative_update, Mapping):
            latest = representative_update
        if isinstance(representative_intervention, Mapping):
            intervention = dict(representative_intervention)
        consumption_status = str(
            representative.get("consumption_status", consumption_status) or ""
        )
    telemetry_document = _telemetry_to_mapping(telemetry)
    telemetry_managers = telemetry_document.get("managers")
    telemetry_active_ids = telemetry_document.get(
        "active_modulation_ids"
    )
    operation_registry_authoritative = bool(
        type(telemetry_document.get("frame")) is int
        and int(telemetry_document["frame"]) >= 0
        and isinstance(telemetry_managers, Mapping)
        and isinstance(telemetry_active_ids, Sequence)
        and not isinstance(
            telemetry_active_ids,
            (str, bytes, bytearray),
        )
    )
    payload = {
        "status": "published" if latest is not None else "idle",
        "dashboard": dict(dashboard),
        "update": dict(latest) if latest is not None else None,
        "intervention": intervention,
        "operations": operations,
        "operation_registry_authoritative": (
            operation_registry_authoritative
        ),
        "operation_summary": _micromachine_operation_summary(operations),
        "compile_result": dict(compile_result) if isinstance(compile_result, Mapping) else None,
        "latest_request": latest_request,
        "latest_request_consumption_status": (
            latest_request.get("consumption_status")
            if isinstance(latest_request, Mapping)
            else ""
        ),
        "command_queue": command_queue,
        "consumption_status": consumption_status,
        "consumed": consumption_status == "consumed",
        "battlefield_projection": battlefield_projection.to_dict(),
        "battlefield_overview": battlefield_overview,
        "battlefield_projection_identity": (
            battlefield_projection.identity.to_dict()
            if battlefield_projection.identity is not None
            else None
        ),
        "battlefield_projection_integrity": dict(
            battlefield_projection.integrity
        ),
    }
    public_payload = _public_micromachine_runtime_payload(payload)
    return (
        dict(public_payload)
        if isinstance(public_payload, Mapping)
        else {}
    )


def _micromachine_status_with_runtime_gate(
    payload: Mapping[str, object],
    *,
    runtime_snapshot: Mapping[str, object] | None,
    blackboard_dir: str,
) -> dict[str, object]:
    """Attach runtime metadata and fail closed when telemetry is detached."""

    result = dict(payload)
    source_status = str(result.get("status", "") or "")
    source_error = str(result.get("error", "") or "")
    if not isinstance(runtime_snapshot, Mapping):
        public_result = _public_micromachine_runtime_payload(result)
        return dict(public_result) if isinstance(public_result, Mapping) else {}

    runtime_status = str(runtime_snapshot.get("status", "") or "")
    for key in (
        "runtime_attached",
        "telemetry_current_for_process",
        "telemetry_stale_or_detached",
        "telemetry_present",
        "telemetry_frame",
        "pid",
        "last_line",
        "error",
    ):
        if key in runtime_snapshot:
            if key == "error" and source_status == "source_error" and source_error:
                continue
            result[key] = runtime_snapshot[key]
    result["runtime_status"] = runtime_status

    telemetry_is_current = runtime_snapshot.get("telemetry_current_for_process") is True
    runtime_attached = runtime_snapshot.get("runtime_attached") is True
    if runtime_attached and telemetry_is_current:
        public_result = _public_micromachine_runtime_payload(result)
        return dict(public_result) if isinstance(public_result, Mapping) else {}

    dashboard = result.get("dashboard", {})
    if not isinstance(dashboard, Mapping):
        dashboard = {}
    rebuilt = _micromachine_status_payload(
        dashboard,
        telemetry=None,
        blackboard_dir=blackboard_dir,
        compile_result=result.get("compile_result"),
        result_stream=(
            result.get("modulation_results")
            if isinstance(result.get("modulation_results"), Sequence)
            and not isinstance(result.get("modulation_results"), (str, bytes))
            else ()
        ),
    )
    result.update(rebuilt)
    result["operation_registry_authoritative"] = False
    if source_status == "source_error":
        result["status"] = source_status
    result["runtime_status"] = runtime_status
    for key in (
        "runtime_attached",
        "telemetry_current_for_process",
        "telemetry_stale_or_detached",
        "telemetry_present",
        "telemetry_frame",
        "pid",
        "last_line",
        "error",
    ):
        if key in runtime_snapshot:
            if key == "error" and source_status == "source_error" and source_error:
                continue
            result[key] = runtime_snapshot[key]
    if (
        result.get("update") is not None
        and runtime_snapshot.get("telemetry_present") is True
        and not telemetry_is_current
    ):
        result["consumption_status"] = "detached_telemetry"
        result["consumed"] = False
        intervention = result.get("intervention")
        if isinstance(intervention, Mapping):
            intervention_payload = dict(intervention)
            intervention_payload["applied"] = False
            result["intervention"] = intervention_payload
    public_result = _public_micromachine_runtime_payload(result)
    return dict(public_result) if isinstance(public_result, Mapping) else {}


def _micromachine_compile_result_for_update(
    compile_result: object | None,
    *,
    update_id: str,
) -> dict[str, object] | None:
    """Scope latest async compile status to the active update evidence it describes."""

    if not isinstance(compile_result, Mapping):
        return None
    result = dict(compile_result)
    if not update_id:
        return result
    result_update_id = str(result.get("update_id", "") or "").strip()
    if result_update_id == update_id:
        return result
    return None


def _micromachine_latest_request_summary(
    compile_result: object | None,
    *,
    active_update_id: str,
    active_consumption_status: str,
) -> dict[str, object] | None:
    """Describe the newest UI/LLM request separately from current active policy."""

    if not isinstance(compile_result, Mapping):
        return None
    result_update_id = str(compile_result.get("update_id", "") or "").strip()
    result_status = str(compile_result.get("status", "") or "").strip()
    if not result_update_id and not result_status:
        return None
    if result_update_id and result_update_id == active_update_id:
        request_consumption_status = active_consumption_status
    elif result_status in {"refused", "clarification_required"}:
        request_consumption_status = "not_published"
    elif result_status in {"compiled", "published"}:
        request_consumption_status = "pending_consumption"
    else:
        request_consumption_status = result_status or "unknown"
    return {
        "update_id": result_update_id,
        "status": result_status,
        "source": str(compile_result.get("source", "") or ""),
        "consumption_status": request_consumption_status,
        "active_update_id": active_update_id,
        "is_active_update": bool(result_update_id and result_update_id == active_update_id),
        "refusal_reason": str(compile_result.get("refusal_reason", "") or ""),
        "clarification_prompt": str(
            compile_result.get("clarification_prompt", "") or ""
        ),
        "duration_ms": compile_result.get("duration_ms"),
        "command_queue": (
            dict(compile_result.get("command_queue"))
            if isinstance(compile_result.get("command_queue"), Mapping)
            else {}
        ),
    }


def _micromachine_consumption_status(
    update: Mapping[str, object] | None,
    telemetry: object | None,
) -> str:
    if update is None:
        return "not_published"
    if telemetry is None:
        return "pending_telemetry"
    update_id = str(update.get("update_id", "") or "")
    issued_at_frame = update.get("issued_at_frame")
    telemetry_frame = getattr(telemetry, "frame", 0)
    if (
        type(issued_at_frame) is not int
        or type(telemetry_frame) is not int
        or telemetry_frame <= issued_at_frame
    ):
        return "pending_consumption"
    active_ids = getattr(telemetry, "active_modulation_ids", ())
    if update_id and update_id in active_ids:
        return "consumed"
    return "pending_consumption"


def _micromachine_intervention_summary(
    update: Mapping[str, object] | None,
    telemetry: object | None,
    *,
    consumption_status: str,
    compile_result: object | None = None,
    log_snippets: Sequence[Mapping[str, object]] = (),
    evidence_log_snippets: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Return a compact UI contract proving whether DSL reached MicroMachine."""

    telemetry_document = _telemetry_to_mapping(telemetry)
    active_ids = _string_list(telemetry_document.get("active_modulation_ids", ()))
    managers = telemetry_document.get("managers", {})
    if not isinstance(managers, Mapping):
        managers = {}
    update_id = str(update.get("update_id", "") or "") if update else ""
    update_is_active = bool(update_id and update_id in active_ids)
    policy_active = any(
        isinstance(payload, Mapping)
        and payload.get("policy_active") is True
        and (
            (update_id and payload.get("update_id") == update_id)
            or update_is_active
        )
        for payload in managers.values()
    )
    vector = update.get("vector", {}) if update else {}
    if not isinstance(vector, Mapping):
        vector = {}
    compile_payload = dict(compile_result) if isinstance(compile_result, Mapping) else {}
    refusal_reason = _micromachine_refusal_reason(compile_payload)
    telemetry_frame = telemetry_document.get("frame")
    if type(telemetry_frame) is not int:
        telemetry_frame = None
    issued_at_frame = update.get("issued_at_frame") if update else None
    if type(issued_at_frame) is not int:
        issued_at_frame = None
    evidence_can_be_current = consumption_status == "consumed" and update_is_active
    evidence_telemetry = (
        _micromachine_current_update_telemetry(
            telemetry_document,
            update_id=update_id,
            telemetry_frame=telemetry_frame,
        )
        if evidence_can_be_current
        else ({"frame": telemetry_frame, "managers": {}} if telemetry_frame is not None else {})
    )
    tactical_log_text = (
        _micromachine_scoped_tactical_log_text(
            evidence_log_snippets if evidence_log_snippets is not None else log_snippets,
            update_id=update_id,
            issued_at_frame=issued_at_frame,
            telemetry_frame=telemetry_frame,
        )
        if evidence_can_be_current
        else ""
    )
    public_evidence_telemetry = _public_micromachine_runtime_payload(
        evidence_telemetry
    )
    if not isinstance(public_evidence_telemetry, Mapping):
        public_evidence_telemetry = {
            "frame": telemetry_frame,
            "managers": {},
        }
    tactical_evidence = classify_micromachine_tactical_evidence(
        latest_telemetry=public_evidence_telemetry,
        telemetry_archive=(),
        log_text=tactical_log_text,
        expected_effects=_micromachine_expected_tactical_effects(vector),
        source_paths=_micromachine_log_snippet_sources(log_snippets),
        refusal_reasons=(refusal_reason,) if refusal_reason else (),
    )
    command_execution = classify_micromachine_command_execution(
        latest_update=update if isinstance(update, Mapping) else {},
        latest_telemetry=evidence_telemetry,
        telemetry_archive=(),
        tactical_evidence=tactical_evidence,
        expected_tactical_effects=_micromachine_expected_tactical_effects(vector),
        latest_frame=telemetry_frame or 0,
        target_frame=0,
    ).to_dict()
    tactical_evidence_payload = tactical_evidence.to_dict()
    dashboard_managers = public_evidence_telemetry.get("managers", {})
    if not isinstance(dashboard_managers, Mapping):
        dashboard_managers = {}
    payload = {
        "applied": consumption_status == "consumed",
        "policy_active": policy_active,
        "latest_update_id": update_id,
        "active_modulation_ids": active_ids,
        "telemetry_frame": telemetry_frame,
        "issued_at_frame": issued_at_frame,
        "manager_bias_domains": _string_list(
            update.get("manager_bias_domains", ()) if update else ()
        ),
        "goal": str(vector.get("goal", "") or ""),
        "override_level": str(vector.get("override_level", "") or ""),
        "confidence": vector.get("confidence"),
        "source": str(vector.get("source", "") or ""),
        "manager_snapshot": {
            str(manager): dict(payload)
            for manager, payload in dashboard_managers.items()
            if isinstance(payload, Mapping)
        },
        "strategy_mode": _micromachine_strategy_mode(vector, dashboard_managers),
        "consumed_axes_by_manager": _micromachine_consumed_axes_by_manager(
            dashboard_managers
        ),
        "tactical_scope": _micromachine_tactical_scope(vector, dashboard_managers),
        "lifetime": _micromachine_lifetime(vector, dashboard_managers),
        "tactical_posture": _micromachine_tactical_posture(
            vector,
            dashboard_managers,
            compile_payload,
        ),
        "target_priority": _micromachine_target_priority(vector, dashboard_managers),
        "attack_gate": _micromachine_attack_gate(vector, dashboard_managers),
        "tactical_evidence": tactical_evidence_payload,
        "command_execution": command_execution,
        "refusal_reason": refusal_reason,
        "log_snippets": [dict(item) for item in log_snippets],
    }
    public_payload = _public_micromachine_runtime_payload(payload)
    return (
        dict(public_payload)
        if isinstance(public_payload, Mapping)
        else {}
    )


def _provider_output_is_terminal(output: Mapping[str, object]) -> bool:
    return _terminal_micromachine_provider_output(output) is not None


def _terminal_micromachine_provider_output(
    output: Mapping[str, object],
) -> dict[str, object] | None:
    status = str(output.get("status", "") or "").strip().lower()
    if status in {"clarification_required", "refused"}:
        return dict(output)
    for key in _MICROMACHINE_PROVIDER_VECTOR_WRAPPER_KEYS:
        value = output.get(key)
        if not isinstance(value, Mapping):
            continue
        nested_status = str(value.get("status", "") or "").strip().lower()
        if nested_status in {"clarification_required", "refused"}:
            terminal = dict(value)
            for metadata_key in ("source", "refusal_reason", "clarification_prompt"):
                if metadata_key in output and metadata_key not in terminal:
                    terminal[metadata_key] = output[metadata_key]
            return terminal
    return None


def _merge_micromachine_semantic_scope_into_provider_output(
    output: Mapping[str, object],
    *,
    semantic_scope: Mapping[str, object],
    ttl_seconds: int | None,
) -> dict[str, object]:
    merged = dict(output)
    wrapper_key = next(
        (
            key
            for key in _MICROMACHINE_PROVIDER_VECTOR_WRAPPER_KEYS
            if isinstance(merged.get(key), Mapping)
        ),
        "",
    )
    target = (
        dict(merged[wrapper_key])  # type: ignore[index]
        if wrapper_key
        else merged
    )
    if semantic_scope:
        existing_scope = target.get("scope", {})
        scope_payload = dict(existing_scope) if isinstance(existing_scope, Mapping) else {}
        scope_payload.update(semantic_scope)
        target["scope"] = scope_payload
    if ttl_seconds is not None:
        target["ttl_seconds"] = ttl_seconds
        if wrapper_key:
            merged["ttl_seconds"] = ttl_seconds
    if wrapper_key:
        merged[wrapper_key] = target
    return merged


def _micromachine_consumed_axes_by_manager(
    managers: Mapping[str, object],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for manager, payload in managers.items():
        if not isinstance(payload, Mapping):
            continue
        axes = _axis_list(payload.get("consumed_axes"))
        if axes:
            result[str(manager)] = axes
    return result


def _micromachine_strategy_mode(
    vector: Mapping[str, object],
    managers: Mapping[str, object],
) -> str:
    production = managers.get("ProductionManager")
    if isinstance(production, Mapping):
        for key in ("strategy_doctrine", "last_doctrine"):
            value = production.get(key)
            if isinstance(value, str) and value.strip() and value != "none":
                return value.strip()
    strategy = vector.get("strategy")
    if isinstance(strategy, Mapping):
        value = strategy.get("doctrine")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _micromachine_tactical_scope(
    vector: Mapping[str, object],
    managers: Mapping[str, object],
) -> dict[str, object]:
    scope = vector.get("scope", {})
    if not isinstance(scope, Mapping):
        scope = {}
    requested = {
        key: value
        for key, value in {
            "army_group": scope.get("army_group"),
            "unit_classes": _string_list(scope.get("unit_classes", ())),
            "location_intent": scope.get("location_intent"),
            "duration_seconds": scope.get("duration_seconds"),
            "min_units": scope.get("min_units"),
            "max_units": scope.get("max_units"),
            "require_safety_margin": scope.get("require_safety_margin"),
            "allow_partial_scope": scope.get("allow_partial_scope"),
        }.items()
        if not _is_empty_micromachine_scope_value(value)
    }
    squad = managers.get("Squad", {})
    telemetry: dict[str, object] = {}
    if isinstance(squad, Mapping):
        telemetry = {
            key: value
            for key, value in {
                "army_group": squad.get("scope_army_group"),
                "location_intent": squad.get("scope_location_intent"),
                "min_units": squad.get("scope_min_units"),
            }.items()
            if value not in ("", None, 0)
        }
    return {"requested": requested, "telemetry": telemetry}


def _micromachine_lifetime(
    vector: Mapping[str, object],
    managers: Mapping[str, object],
) -> dict[str, object]:
    lifetime = vector.get("lifetime", {})
    if not isinstance(lifetime, Mapping):
        lifetime = {}
    commander = managers.get("GameCommander", {})
    if not isinstance(commander, Mapping):
        commander = managers.get("Commander", {})
    telemetry: dict[str, object] = {}
    if isinstance(commander, Mapping):
        telemetry = {
            key: value
            for key, value in {
                "lifetime_mode": commander.get("lifetime_mode"),
                "completion_state": commander.get("completion_state"),
                "completion_conditions": commander.get("completion_conditions"),
            }.items()
            if value not in ("", None, ())
        }
    return {
        "mode": str(lifetime.get("mode", "") or ""),
        "completion_state": str(lifetime.get("completion_state", "") or ""),
        "completion_conditions": _string_list(
            lifetime.get("completion_conditions", ())
        ),
        "reason": str(lifetime.get("reason", "") or ""),
        "telemetry": telemetry,
    }


def _micromachine_tactical_posture(
    vector: Mapping[str, object],
    managers: Mapping[str, object],
    compile_result: Mapping[str, object],
) -> str:
    if _micromachine_refusal_reason(compile_result):
        return "refused"
    combat = _mapping_child(vector, "combat")
    squad = _mapping_child(vector, "squad")
    emergency = _mapping_child(vector, "emergency")
    combat_manager = _mapping_child(managers, "CombatCommander")
    squad_manager = _mapping_child(managers, "Squad")
    if (
        _truthy(emergency.get("force_retreat"))
        or _truthy(emergency.get("cancel_attacks"))
        or _truthy(combat_manager.get("force_retreat"))
    ):
        return "retreat"
    contain_bias = max(
        _number(squad.get("contain_bias")),
        _number(squad_manager.get("contain_bias")),
    )
    if contain_bias > 0.05:
        return "contain"
    harass_bias = max(
        _number(squad.get("harassment_bias")),
        _number(combat.get("harassment_bias")),
        _number(squad_manager.get("target_worker_line_bias")),
    )
    if harass_bias > 0.1:
        return "harass"
    aggression = max(
        _number(combat.get("aggression")),
        _number(combat_manager.get("aggression")),
    )
    attack_timing = max(
        _number(combat.get("attack_timing_bias")),
        _number(combat_manager.get("attack_timing_bias")),
    )
    commitment = max(
        _number(combat.get("commitment_level")),
        _number(combat_manager.get("commitment_level")),
    )
    if aggression > 0.15 or attack_timing > 0.05 or commitment > 0.05:
        return "pressure"
    defend_bias = max(
        _number(combat.get("defend_bias")),
        _number(combat_manager.get("defend_bias")),
        _number(squad.get("defense_bias")),
    )
    if _truthy(emergency.get("hold_position")) or defend_bias > max(0.15, aggression):
        return "hold"
    return "balanced"


def _micromachine_target_priority(
    vector: Mapping[str, object],
    managers: Mapping[str, object],
) -> dict[str, object]:
    combat = _mapping_child(vector, "combat")
    requested = combat.get("target_priority_biases", {})
    requested_biases = (
        {str(key): value for key, value in requested.items()}
        if isinstance(requested, Mapping)
        else {}
    )
    squad = _mapping_child(managers, "Squad")
    telemetry_biases = {
        "worker_line": squad.get("target_worker_line_bias"),
        "townhall": squad.get("target_townhall_bias"),
        "production": squad.get("target_production_bias"),
        "army": squad.get("target_army_bias"),
    }
    telemetry_biases = {
        key: value
        for key, value in telemetry_biases.items()
        if isinstance(value, (int, float)) and type(value) is not bool and value != 0
    }
    scored: dict[str, float] = {}
    for key, value in requested_biases.items():
        scored[key] = _number(value)
    for key, value in telemetry_biases.items():
        scored[key] = max(scored.get(key, 0.0), _number(value))
    selected = max(scored, key=scored.get) if scored else ""
    return {
        "requested_biases": requested_biases,
        "telemetry_biases": telemetry_biases,
        "selected_target_class": selected,
    }


def _micromachine_attack_gate(
    vector: Mapping[str, object],
    managers: Mapping[str, object],
) -> dict[str, object]:
    """Explain the final MicroMachine attack gate in UI-safe terms."""

    combat = _mapping_child(managers, "CombatCommander")
    squad = _mapping_child(managers, "Squad")
    scope = _mapping_child(vector, "scope")
    combat_vector = _mapping_child(vector, "combat")
    status = str(combat.get("main_attack_order_status", "") or "")
    reason = str(combat.get("main_attack_order_reason", "") or "")
    unit_count = _int_or_none(
        combat.get("main_attack_unit_count", combat.get("combat_unit_count"))
    )
    min_units = _int_or_none(
        combat.get(
            "main_attack_scope_min_units",
            squad.get("scope_min_units", scope.get("min_units")),
        )
    )
    threshold_met = _bool_or_none(combat.get("main_attack_scope_threshold_met"))
    if threshold_met is None and unit_count is not None and min_units is not None:
        threshold_met = min_units <= 0 or unit_count >= min_units
    if not reason:
        if unit_count is not None and min_units is not None and unit_count < min_units:
            reason = f"waiting_for_min_units:{unit_count}/{min_units}"
        elif str(combat_vector.get("attack_condition_override", "") or "") == "never":
            reason = "attack_condition_override_never"
    return {
        "status": status,
        "reason": reason,
        "unit_count": unit_count,
        "min_units": min_units,
        "scope_threshold_met": threshold_met,
        "simulation_won": _bool_or_none(combat.get("main_attack_simulation_won")),
        "order_x": _number_or_none(combat.get("main_attack_order_x")),
        "order_y": _number_or_none(combat.get("main_attack_order_y")),
    }


def _micromachine_expected_tactical_effects(
    vector: Mapping[str, object],
) -> tuple[str, ...]:
    candidates: list[str] = []
    tactical_task = _mapping_child(vector, "tactical_task")
    task_type = str(tactical_task.get("task_type", "") or "")
    if task_type == "scout_with_units":
        # Combat/target biases on a scout task describe risk and target
        # selection, not additional attack effects that must be observed.
        return ("scout",)
    if task_type == "pressure_with_main_army":
        candidates.append("pressure")
    tags = vector.get("tags")
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes)):
        candidates.extend(str(tag) for tag in tags if tag is not None)
    goal = vector.get("goal")
    if isinstance(goal, str):
        lowered = goal.lower()
        for marker, effect in (
            ("contain", "contain"),
            ("harass", "harass"),
            ("worker", "target_priority"),
            ("target", "target_priority"),
            ("scout", "scout"),
            ("map control", "scout"),
            ("hold", "hold"),
            ("defend", "hold"),
            ("retreat", "hold"),
            ("attack", "pressure"),
            ("pressure", "pressure"),
        ):
            if marker in lowered:
                candidates.append(effect)
    posture = _micromachine_tactical_posture(vector, {}, {})
    if posture in {"pressure", "hold", "contain", "harass"}:
        candidates.append(posture)
    target_biases = _mapping_child(_mapping_child(vector, "combat"), "target_priority_biases")
    if target_biases:
        candidates.append("target_priority")
    scouting = _mapping_child(vector, "scouting")
    if any(_number(value) > 0 for value in scouting.values()):
        candidates.append("scout")
    return normalize_tactical_effect_tags(candidates)


def _micromachine_log_snippet_sources(
    log_snippets: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    sources: list[str] = []
    for snippet in log_snippets:
        source = snippet.get("source") if isinstance(snippet, Mapping) else None
        if isinstance(source, str) and source and source not in sources:
            sources.append(source)
    return {"log_snippets": ", ".join(sources)} if sources else {}


def _micromachine_scoped_tactical_log_text(
    log_snippets: Sequence[Mapping[str, object]],
    *,
    update_id: str,
    issued_at_frame: int | None,
    telemetry_frame: int | None,
) -> str:
    update_token = update_id.strip().lower()
    if not update_token:
        return ""
    lines: list[str] = []
    for snippet in log_snippets:
        line = str(snippet.get("line", "") or "") if isinstance(snippet, Mapping) else ""
        if not line.strip():
            continue
        frame = _micromachine_log_frame(line)
        if _micromachine_log_has_update_id(line, update_id=update_id):
            if frame is None or _micromachine_log_frame_in_current_window(
                frame,
                issued_at_frame=issued_at_frame,
                telemetry_frame=telemetry_frame,
            ):
                lines.append(line)
            continue
        if _micromachine_log_frame_in_current_window(
            frame,
            issued_at_frame=issued_at_frame,
            telemetry_frame=telemetry_frame,
        ):
            lines.append(line)
            continue
    return "\n".join(lines)


def _micromachine_log_frame_in_current_window(
    frame: int | None,
    *,
    issued_at_frame: int | None,
    telemetry_frame: int | None,
) -> bool:
    return (
        issued_at_frame is not None
        and telemetry_frame is not None
        and frame is not None
        and issued_at_frame < frame <= telemetry_frame
    )


def _micromachine_log_has_update_id(line: str, *, update_id: str) -> bool:
    token = update_id.strip()
    if not token:
        return False
    escaped = re.escape(token)
    key_pattern = r"(?:update_id|policy_update_id|active_update_id|last_update_id)"
    patterns = (
        rf"\b{key_pattern}\s*=\s*[\"']?{escaped}(?=[\"'\s,;)\]]|$)",
        rf"[\"']{key_pattern}[\"']\s*:\s*[\"']{escaped}[\"']",
    )
    return any(re.search(pattern, line) for pattern in patterns)


def _micromachine_current_update_telemetry(
    telemetry_document: Mapping[str, object],
    *,
    update_id: str,
    telemetry_frame: int | None,
) -> dict[str, object]:
    if not update_id:
        return {"frame": telemetry_frame, "managers": {}} if telemetry_frame is not None else {}
    managers = telemetry_document.get("managers")
    manager_payloads: dict[str, object] = {}
    scoped_managers: dict[str, object] = {}
    if isinstance(managers, Mapping):
        for manager, payload in managers.items():
            if not isinstance(payload, Mapping):
                continue
            manager_payloads[str(manager)] = dict(payload)
            if _micromachine_manager_matches_update(payload, update_id=update_id):
                scoped_managers[str(manager)] = dict(payload)
    game_commander = manager_payloads.get("GameCommander")
    if isinstance(game_commander, Mapping) and _micromachine_manager_matches_update(
        game_commander,
        update_id=update_id,
    ):
        scoped_managers = manager_payloads
    return {
        "frame": telemetry_frame,
        "active_modulation_ids": _string_list(
            telemetry_document.get("active_modulation_ids", ())
        ),
        "managers": scoped_managers,
    }


def _micromachine_manager_matches_update(
    payload: Mapping[str, object],
    *,
    update_id: str,
) -> bool:
    for key in ("update_id", "policy_update_id", "active_update_id", "last_update_id"):
        value = payload.get(key)
        if isinstance(value, str) and value == update_id:
            return True
    active_ids = payload.get("active_modulation_ids")
    return update_id in _string_list(active_ids)


def _micromachine_log_frame(line: str) -> int | None:
    match = _MICROMACHINE_LOG_FRAME_RE.match(line)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _micromachine_refusal_reason(compile_result: Mapping[str, object]) -> str:
    reason = compile_result.get("refusal_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    prompt = compile_result.get("clarification_prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return ""


def _micromachine_recent_tactical_log_snippets(
    blackboard_dir: str,
    *,
    update_id: str = "",
    limit: int | None = 8,
) -> list[dict[str, str]]:
    if not blackboard_dir:
        return []
    root = os.path.abspath(blackboard_dir)
    root_real = os.path.realpath(root)
    if not os.path.isdir(root_real):
        return []
    update_token = update_id.strip().lower()
    snippets: list[dict[str, str]] = []
    for filename in _MICROMACHINE_TACTICAL_LOG_FILES:
        path = os.path.abspath(os.path.join(root, filename))
        path_real = os.path.realpath(path)
        if not path_real.startswith(root_real + os.sep) or not os.path.isfile(path_real):
            continue
        try:
            size = os.path.getsize(path_real)
            with open(path_real, "rb") as handle:
                if size > _MICROMACHINE_MAX_LOG_READ_BYTES:
                    start = size - _MICROMACHINE_MAX_LOG_READ_BYTES
                    handle.seek(start - 1)
                    previous = handle.read(1)
                    text = handle.read().decode("utf-8", errors="replace")
                    lines = text.splitlines()
                    if previous != b"\n" and lines:
                        lines = lines[1:]
                else:
                    lines = handle.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            cleaned = _redact_sensitive_text(
                line.strip(),
                normalize_whitespace=True,
                max_chars=500,
            )
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if update_token and update_token in lowered:
                snippets.append({"source": filename, "line": cleaned})
            elif any(term in lowered for term in _MICROMACHINE_TACTICAL_LOG_TERMS):
                snippets.append({"source": filename, "line": cleaned})
    return snippets if limit is None else snippets[-limit:]


def _axis_list(values: object) -> list[str]:
    if isinstance(values, str):
        return [axis.strip() for axis in values.split(",") if axis.strip()]
    return _string_list(values)


def _mapping_child(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key, {})
    return value if isinstance(value, Mapping) else {}


def _web_event_int(value: object, default: int = 0) -> int:
    if type(value) is int:
        return value
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


def _web_event_identity(
    payload: Mapping[str, object],
) -> tuple[str, str, int, int]:
    """Extract stable operation identity from one web/runtime payload."""

    compile_result = _mapping_child(payload, "compile_result")
    update = _mapping_child(payload, "update")
    latest_request = _mapping_child(payload, "latest_request")
    intervention = _mapping_child(payload, "intervention")
    execution = _mapping_child(intervention, "command_execution")
    dashboard = _mapping_child(payload, "dashboard")
    telemetry = _mapping_child(dashboard, "telemetry")
    update_id = str(
        execution.get("command_id")
        or update.get("update_id")
        or compile_result.get("update_id")
        or latest_request.get("update_id")
        or payload.get("update_id")
        or ""
    )
    operation_id = str(
        execution.get("operation_id")
        or payload.get("operation_id")
        or update_id
        or ""
    )
    generation = _web_event_int(
        execution.get("operation_generation")
        or payload.get("operation_generation")
        or payload.get("generation"),
        0,
    )
    game_frame = _web_event_int(
        intervention.get("telemetry_frame")
        if intervention.get("telemetry_frame") is not None
        else telemetry.get("frame"),
        -1,
    )
    return update_id, operation_id, max(0, generation), game_frame


def _web_snapshot_order_identity(
    payload: Mapping[str, object],
) -> tuple[str, int, int] | None:
    """Extract a monotonic source identity for concurrent refresh rejection."""

    overview = _mapping_child(payload, "battlefield_overview")
    overview_identity = _mapping_child(overview, "identity")
    projection_identity = _mapping_child(
        payload,
        "battlefield_projection_identity",
    )
    payload_identity = _mapping_child(payload, "identity")
    identity = overview_identity or projection_identity or payload_identity
    session_epoch = str(identity.get("session_epoch", "") or "")
    generation = max(
        0,
        _web_event_int(
            identity.get("generation")
            if identity.get("generation") is not None
            else payload.get("generation"),
            0,
        ),
    )
    frame = _web_event_int(
        identity.get("game_frame")
        if identity.get("game_frame") is not None
        else payload.get("frame"),
        -1,
    )
    if not session_epoch and generation <= 0 and frame < 0:
        return None
    return session_epoch, generation, frame


def _web_snapshot_identity_regresses(
    previous: tuple[str, int, int],
    incoming: tuple[str, int, int],
) -> bool:
    previous_epoch, previous_generation, previous_frame = previous
    incoming_epoch, incoming_generation, incoming_frame = incoming
    if previous_epoch and incoming_epoch and previous_epoch != incoming_epoch:
        try:
            return int(incoming_epoch) < int(previous_epoch)
        except ValueError:
            return False
    if (
        previous_generation > 0
        and incoming_generation > 0
        and incoming_generation < previous_generation
    ):
        return True
    return (
        previous_frame >= 0
        and incoming_frame >= 0
        and incoming_frame < previous_frame
    )


def _web_event_blackboard_scope_id(
    payload: Mapping[str, object],
    *,
    blackboard_dir: str = "",
    blackboard_scope_id: str = "",
) -> str:
    """Return the opaque blackboard boundary attached to one web event."""

    if blackboard_scope_id.strip():
        return blackboard_scope_id.strip()
    candidates = (
        payload,
        _mapping_child(payload, "compile_result"),
        _mapping_child(payload, "latest_request"),
        _mapping_child(payload, "update"),
        _mapping_child(payload, "intervention"),
    )
    for candidate in candidates:
        scope_id = str(candidate.get("blackboard_scope_id", "") or "").strip()
        if scope_id:
            return scope_id
    resolved_dir = blackboard_dir.strip()
    if not resolved_dir:
        for candidate in candidates:
            resolved_dir = str(candidate.get("blackboard_dir", "") or "").strip()
            if resolved_dir:
                break
    return (
        _micromachine_blackboard_scope_id(resolved_dir)
        if resolved_dir
        else ""
    )


def _number(value: object) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _number_or_none(value: object) -> float | None:
    if type(value) is bool or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int_or_none(value: object) -> int | None:
    if type(value) is bool:
        return None
    if isinstance(value, int):
        return value
    return None


def _bool_or_none(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _telemetry_to_mapping(telemetry: object | None) -> dict[str, object]:
    if telemetry is None:
        return {}
    to_dict = getattr(telemetry, "to_dict", None)
    if callable(to_dict):
        try:
            document = to_dict()
        except Exception:
            document = None
        if isinstance(document, Mapping):
            return dict(document)
    if isinstance(telemetry, Mapping):
        return dict(telemetry)
    return {}


def _string_list(values: object) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return []
    return [str(value) for value in values if value is not None]


WEB_GUI_PAGE_TITLE: Final[str] = "voiStarcraft2 커맨더"
"""Korean single-page UI title."""

LLM_REQUIRED_COMMAND_ERROR: Final[str] = (
    "LLM 키가 설정되지 않아 명령을 실행하지 않았습니다. "
    "이 프로젝트는 LLM 기반 해석을 필수로 사용합니다. "
    "우측 LLM 설정에서 OpenAI 또는 Anthropic API 키를 먼저 설정하세요."
)
"""User-facing refusal when a command arrives before local LLM configuration."""

_LLM_SETUP_REDACTION: Final[str] = "[redacted]"
"""Replacement used when provider errors echo submitted key material."""

_API_KEY_REDACTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-.]{8,}\b"),
    re.compile(r"\bxai-[A-Za-z0-9_\-.]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_\-.]{8,}\b"),
)
"""Provider API key patterns that must never reach UI/log JSON surfaces."""

_LLM_SETUP_PROVIDER_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "apierror",
        "apistatuserror",
        "authentication",
        "auth",
        "badrequest",
        "forbidden",
        "invalid api key",
        "invalid_api_key",
        "permission",
        "provider",
        "quota",
        "rate limit",
        "ratelimit",
        "unauthorized",
    }
)
"""SDK error markers that mean the provider rejected setup."""

_LLM_SETUP_NETWORK_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "api_connection",
        "connection",
        "connect",
        "dns",
        "network",
        "socket",
        "timeout",
        "timed out",
        "unreachable",
    }
)
"""SDK error markers that mean the provider could not be reached."""

WEB_GUI_POLL_INTERVAL_MS: Final[int] = 1000
"""Browser polling interval used only while the SSE channel is unavailable."""

WEB_GUI_EVENT_RETENTION: Final[int] = 512
"""Maximum append-only web lifecycle events retained for SSE replay."""

WEB_GUI_SSE_HEARTBEAT_SECONDS: Final[float] = 10.0
"""Maximum quiet period before an SSE heartbeat comment is emitted."""

WEB_GUI_SSE_REFRESH_SECONDS: Final[float] = 0.25
"""Server-side source refresh cadence while at least one SSE client is open."""

WEB_GUI_STATUS_COLORS: Final[Mapping[str, str]] = {
    "executed": "#1d8a3a",
    "partially_executed": "#c77700",
    "blocked": "#c62828",
    "clarification": "#6b6b6b",
    "read_only": "#1565c0",
}
"""Outcome status -> log entry color (green/amber/red/gray/blue)."""

MAX_COMMAND_BODY_BYTES: Final[int] = 64 * 1024
"""Upper bound for one ``POST /api/command`` body; larger bodies are rejected."""

MAX_WEB_REQUEST_ID_CHARS: Final[int] = 128
"""Upper bound for browser-generated command correlation identities."""

_BRIDGE_THREAD_NAME: Final[str] = "voiStarcraft2-web-gui-session-loop"
"""Daemon thread name for the bridge's asyncio loop (asserted clean in tests)."""

_SERVER_THREAD_NAME: Final[str] = "voiStarcraft2-web-gui-http-server"
"""Daemon thread name for the HTTP server's serve_forever loop."""

_STOP_SENTINEL: Final[object] = object()
"""Internal queue sentinel asking the bridge worker loop to exit."""

_MICROMACHINE_REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0
"""Maximum HTTP wait for one queued MicroMachine modulation submission."""

_MICROMACHINE_SYNC_PUBLISH_DEADLINE_SECONDS: Final[float] = 25.0
"""Publish deadline kept below the synchronous HTTP wait budget."""

_MICROMACHINE_COMPILE_RESULT_FRESH_SECONDS: Final[float] = 300.0
"""How long a failed/clarifying compile result remains current in the dashboard."""

_MICROMACHINE_TELEMETRY_FRESHNESS_NS: Final[int] = 15 * 1_000_000_000
"""Maximum age of post-launch telemetry accepted as current for the process."""

_MICROMACHINE_COMPILE_RESULT_HISTORY_LIMIT: Final[int] = 64
"""Maximum per-update compile/publish results retained for browser polling."""

_BRIDGE_QUEUE_PRIORITY_EMERGENCY: Final[int] = 0
_BRIDGE_QUEUE_PRIORITY_NORMAL: Final[int] = 10
_BRIDGE_QUEUE_PRIORITY_STOP: Final[int] = 100

_BRIDGE_LIFECYCLE_STOPPED: Final[str] = "STOPPED"
_BRIDGE_LIFECYCLE_STARTING: Final[str] = "STARTING"
_BRIDGE_LIFECYCLE_RUNNING: Final[str] = "RUNNING"
_BRIDGE_LIFECYCLE_STOPPING: Final[str] = "STOPPING"

_MICROMACHINE_RETREAT_TEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:긴급|즉시|당장|지금|전원|모두)\s*)*"
    r"(?:후퇴|퇴각|철수)"
    r"(?:\s*(?:해|하라|하세요|해라|해줘|해\s*주세요|진행해|시작해))?"
    r"[.!]?$|"
    r"^(?:please\s+)?(?:emergency\s+)?"
    r"(?:retreat|fall\s+back)"
    r"(?:\s+(?:now|immediately))?[.!]?$|"
    r"^(?:(?:立即|马上|紧急)\s*)?撤退(?:吧|！|。)?$",
    re.IGNORECASE,
)

_MICROMACHINE_ATTACK_CANCEL_TEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:공격|러시|러쉬|압박|작전|진격)(?:을|를|은|는)?\s*"
    r"(?:취소|중지|중단|멈춰|그만)|"
    r"(?:cancel|abort|stop)\s+(?:the\s+)?"
    r"(?:attack|attacking|rush|pressure|operation|advance)|"
    r"(?:attack|rush|pressure|operation|advance)\s+"
    r"(?:cancel|abort|stop)|"
    r"(?:取消|停止)\s*(?:进攻|攻击|行动)|"
    r"(?:进攻|攻击|行动)\s*(?:取消|停止)",
    re.IGNORECASE,
)

_MICROMACHINE_NEGATED_EMERGENCY_PATTERNS: Final[
    tuple[re.Pattern[str], ...]
] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        (
            r"(?:(?:공격|러시|러쉬|압박|작전|진격)(?:을|를|은|는)?\s*)?"
            r"(?:취소|중지|중단|멈추|그만두|그만하)(?:하)?지\s*"
            r"(?:마(?:라|세요)?|말(?:고|아|라)?|않(?:아|는다|도록|고)?)"
        ),
        (
            r"(?:공격|러시|러쉬|압박|작전|진격)(?:을|를|은|는)?\s*"
            r"(?:취소|중지|중단|멈춤|그만두기)\s*"
            r"(?:없이|금지|불가|안\s*돼|안돼|없(?:다|어|음))"
        ),
        (
            r"(?:후퇴|퇴각|철수|물러나)(?:하)?지\s*"
            r"(?:마(?:라|세요)?|말(?:고|아|라)?|않(?:아|는다|도록|고)?)"
        ),
        (
            r"(?:후퇴|퇴각|철수|물러나)(?:은|는|이|가)?\s*"
            r"(?:금지|말고|없이|불가|안\s*돼|안돼|없(?:다|어|음))"
        ),
        (
            r"(?:후퇴|퇴각|철수|물러나)(?:은|는|이|가)?\s*"
            r"(?:선택지|옵션)(?:가|이)?\s*"
            r"(?:아니(?:다|야|고)?|아님|될\s*수\s*없)"
        ),
        (
            r"(?:후퇴|퇴각|철수|물러나)\s*안\s*"
            r"(?:하|해|하고|한다|할)"
        ),
        (
            r"\b(?:do\s+not|don't|dont|never)\s+"
            r"(?:cancel|stop|abort|retreat|fall\s+back)\b"
        ),
        (
            r"\bwithout\s+"
            r"(?:cancel(?:ing|ling)?|stopp?ing|abort(?:ing)?|"
            r"retreat(?:ing)?|fall(?:ing)?\s+back)\b"
        ),
        r"\bno\s+(?:retreat|fall(?:ing)?\s+back)\b",
        (
            r"\b(?:retreat|fall(?:ing)?\s+back)\s+(?:is|are)\s+not\s+"
            r"(?:an?\s+)?(?:option|allowed)\b"
        ),
        (
            r"\b(?:retreat|fall(?:ing)?\s+back)\s+"
            r"(?:forbidden|prohibited|banned)\b"
        ),
        r"(?:禁止|不得|不许|不要|别)\s*(?:撤退|取消|停止)",
    )
)

_MICROMACHINE_SMOKE_SCRIPT_RELATIVE_PATH: Final[str] = (
    "integrations/micromachine/scripts/smoke_macos_local.sh"
)
"""Repo-local MicroMachine smoke/live launcher used by the web cockpit."""

_MICROMACHINE_UI_SMOKE_MAX_ATTEMPTS_ENV: Final[str] = (
    "VOI_MICROMACHINE_UI_SMOKE_MAX_ATTEMPTS"
)
"""Optional env override for UI-triggered MicroMachine smoke retries."""

_MICROMACHINE_LANGUAGE_LABELS: Final[Mapping[str, str]] = {
    "ko": "Korean",
    "en": "English",
    "zh": "Chinese",
}
"""Language labels passed to the LLM policy modulation context."""

_MICROMACHINE_RECENT_COMMAND_LIMIT: Final[int] = 8
"""Maximum recent commands retained per blackboard for LLM context."""

_MICROMACHINE_RECENT_COMMAND_TEXT_LIMIT: Final[int] = 500
"""Maximum text stored for one recent commander-context field."""

_MICROMACHINE_RECENT_COMMAND_VALUE_LIMIT: Final[int] = 160
"""Maximum text stored for one compact recent-command metadata value."""

_MICROMACHINE_RECENT_COMMAND_LIST_LIMIT: Final[int] = 8
"""Maximum unit-like values retained inside one recent-command entry."""


def _micromachine_recent_context_text(
    value: object,
    *,
    max_chars: int = _MICROMACHINE_RECENT_COMMAND_VALUE_LIMIT,
) -> str:
    return _redact_sensitive_text(
        value or "",
        normalize_whitespace=True,
        max_chars=max_chars,
    )


def _micromachine_recent_context_strings(value: object) -> list[str]:
    if isinstance(value, str):
        values: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(
        value, (bytes, bytearray)
    ):
        values = value
    else:
        values = ()
    result: list[str] = []
    for item in values:
        text = _micromachine_recent_context_text(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= _MICROMACHINE_RECENT_COMMAND_LIST_LIMIT:
            break
    return result


def _micromachine_recent_context_count(value: object) -> int:
    if type(value) is not int:
        return 0
    return max(0, min(value, 200))


def _merge_micromachine_provider_recent_commands(
    supplemental: object,
    runtime_context: object,
) -> list[dict[str, object]]:
    """Merge web-memory history with blackboard-restored runtime context."""

    result: list[dict[str, object]] = []
    identities: dict[str, int] = {}
    for source in (supplemental, runtime_context):
        if not isinstance(source, Sequence) or isinstance(
            source,
            (str, bytes, bytearray),
        ):
            continue
        for item in source:
            if not isinstance(item, Mapping):
                continue
            document = dict(item)
            update_id = _micromachine_recent_context_text(
                document.get("update_id", "")
            )
            identity = (
                f"update:{update_id}"
                if update_id
                else "content:"
                + "|".join(
                    (
                        _micromachine_recent_context_text(
                            document.get("command_text", "")
                        ),
                        _micromachine_recent_context_text(
                            document.get("goal", "")
                        ),
                        _micromachine_recent_context_text(
                            document.get("command_layer", "")
                        ),
                    )
                )
            )
            if identity in identities:
                index = identities[identity]
                result[index] = {
                    **document,
                    **result[index],
                }
                continue
            identities[identity] = len(result)
            result.append(document)
    return result[-_MICROMACHINE_RECENT_COMMAND_LIMIT:]


def _micromachine_recent_command_entry(
    command_text: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    compile_result = _mapping_child(payload, "compile_result")
    vector = _mapping_child(compile_result, "vector")
    update = _mapping_child(payload, "update")
    command_queue = _mapping_child(payload, "command_queue")
    if not command_queue:
        command_queue = _mapping_child(compile_result, "command_queue")
    strategy = _mapping_child(vector, "strategy")
    tactical_task = _mapping_child(vector, "tactical_task")
    route_intent = _mapping_child(vector, "route_intent")
    target_intent = _mapping_child(vector, "target_intent")
    scope = _mapping_child(vector, "scope")
    intervention = _mapping_child(payload, "intervention")
    execution = _mapping_child(intervention, "command_execution")

    unit_classes = _micromachine_recent_context_strings(
        tactical_task.get("unit_classes", ())
    )
    requested_count = 0
    composition_requirements = vector.get("composition_requirements", ())
    if isinstance(composition_requirements, Sequence) and not isinstance(
        composition_requirements,
        (str, bytes, bytearray),
    ):
        for requirement in composition_requirements[
            :_MICROMACHINE_RECENT_COMMAND_LIST_LIMIT
        ]:
            if not isinstance(requirement, Mapping):
                continue
            unit_type = _micromachine_recent_context_text(
                requirement.get("unit_type", "")
            )
            if unit_type and unit_type not in unit_classes:
                unit_classes.append(unit_type)
                unit_classes = unit_classes[
                    :_MICROMACHINE_RECENT_COMMAND_LIST_LIMIT
                ]
            requested_count += _micromachine_recent_context_count(
                requirement.get("count")
            )

    assistant_message = (
        compile_result.get("assistant_message")
        or vector.get("assistant_message")
        or ""
    )
    update_id = (
        update.get("update_id")
        or compile_result.get("update_id")
        or payload.get("update_id")
        or ""
    )
    target = (
        target_intent.get("target_type")
        or tactical_task.get("location_intent")
        or scope.get("location_intent")
        or ""
    )
    raw_operations = vector.get("operations")
    operations = (
        [
            json.loads(json.dumps(dict(operation), ensure_ascii=False))
            for operation in raw_operations
            if isinstance(operation, Mapping)
        ]
        if isinstance(raw_operations, Sequence)
        and not isinstance(raw_operations, (str, bytes, bytearray))
        else []
    )
    return {
        "command_text": _micromachine_recent_context_text(
            command_text,
            max_chars=_MICROMACHINE_RECENT_COMMAND_TEXT_LIMIT,
        ),
        "status": _micromachine_recent_context_text(
            payload.get("status") or compile_result.get("status") or ""
        ),
        "update_id": _micromachine_recent_context_text(update_id),
        "assistant_message": _micromachine_recent_context_text(
            assistant_message,
            max_chars=_MICROMACHINE_RECENT_COMMAND_TEXT_LIMIT,
        ),
        "command_layer": _micromachine_recent_context_text(
            vector.get("command_layer", "")
        ),
        "category": _micromachine_recent_context_text(
            command_queue.get("category", "")
        ),
        "reducer_action": _micromachine_recent_context_text(
            command_queue.get("action", "")
        ),
        "goal": _micromachine_recent_context_text(
            vector.get("goal", ""),
            max_chars=_MICROMACHINE_RECENT_COMMAND_TEXT_LIMIT,
        ),
        "doctrine": _micromachine_recent_context_text(
            strategy.get("doctrine", "")
        ),
        "tactical_task": {
            "type": _micromachine_recent_context_text(
                tactical_task.get("task_type", "")
            ),
            "ability": _micromachine_recent_context_text(
                tactical_task.get("ability", "")
            ),
            "units": unit_classes,
            "count": {
                "min": _micromachine_recent_context_count(
                    tactical_task.get("min_units")
                ),
                "max": _micromachine_recent_context_count(
                    tactical_task.get("max_units")
                ),
                "requested": min(requested_count, 200),
            },
        },
        "route": _micromachine_recent_context_text(
            route_intent.get("route_type", "")
        ),
        "target": _micromachine_recent_context_text(target),
        "consumption_status": _micromachine_recent_context_text(
            payload.get("consumption_status", "")
        ),
        "execution_status": _micromachine_recent_context_text(
            execution.get("state", "")
        ),
        "operations": operations,
    }


class _MicroMachineRequestSupersededError(RuntimeError):
    """Raised when an emergency command supersedes unpublished queued work."""

    def __init__(self, request_id: str, replacement_update_id: str) -> None:
        self.request_id = request_id
        self.replacement_update_id = replacement_update_id
        super().__init__(
            f"MicroMachine request {request_id} was superseded by emergency "
            f"request {replacement_update_id}."
        )


class _MicroMachinePublishCancelledError(RuntimeError):
    """Raised when a cancelled or expired request reaches the publish boundary."""


@dataclass
class _MicroMachineModulationRequest:
    """Queued MicroMachine write request, serialized with commander commands."""

    text: str
    blackboard_dir: str
    provider_output: Mapping[str, object] | None
    allow_smoke_keyword_provider: bool
    semantic_scope: Mapping[str, object] | None
    commander_context: Mapping[str, object]
    ttl_seconds: int | None
    current_frame: int | None
    update_id: str | None
    future: concurrent.futures.Future[Mapping[str, object]]
    cancel_event: threading.Event
    deadline_monotonic: float | None = None
    emergency: bool = False
    emergency_epoch: int = 0
    accepted_at_unix_ns: int = 0
    acceptance_ordinal: int = 0
    publish_committed: bool = False


@dataclass(frozen=True)
class _CorrelatedWebCommand:
    """One legacy commander utterance bound to its browser pending identity."""

    text: str
    request_id: str


class _GuardedMicroMachineBackend:
    """Make cancellation/deadline checks atomic with blackboard publication."""

    def __init__(
        self,
        backend: object,
        request: _MicroMachineModulationRequest,
        coordinator_lock: threading.Lock,
        emergency_epochs: dict[str, tuple[int, str]],
    ) -> None:
        self._backend = backend
        self._request = request
        self._coordinator_lock = coordinator_lock
        self._emergency_epochs = emergency_epochs

    def publish_vector(self, *args, **kwargs):
        request = self._request
        with self._coordinator_lock:
            blackboard_key = os.path.realpath(request.blackboard_dir)
            emergency_epoch, latest_emergency_update_id = (
                self._emergency_epochs.get(blackboard_key, (0, ""))
            )
            deadline = request.deadline_monotonic
            if request.cancel_event.is_set():
                raise _MicroMachinePublishCancelledError(
                    f"MicroMachine request {request.update_id or '<pending>'} was cancelled."
                )
            if deadline is not None and time.monotonic() >= deadline:
                request.cancel_event.set()
                raise _MicroMachinePublishCancelledError(
                    f"MicroMachine request {request.update_id or '<pending>'} exceeded its publish deadline."
                )
            if not request.emergency and request.emergency_epoch != emergency_epoch:
                request.cancel_event.set()
                raise _MicroMachineRequestSupersededError(
                    request.update_id or "<pending>",
                    latest_emergency_update_id or "<emergency>",
                )
            result = self._backend.publish_vector(*args, **kwargs)
            request.publish_committed = True
            if request.emergency:
                self._emergency_epochs[blackboard_key] = (
                    emergency_epoch + 1,
                    request.update_id or "",
                )
            return result

    def __getattr__(self, name: str) -> object:
        return getattr(self._backend, name)


def _micromachine_request_is_emergency(
    text: str,
    provider_output: Mapping[str, object] | None,
) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    for pattern in _MICROMACHINE_NEGATED_EMERGENCY_PATTERNS:
        normalized = pattern.sub(" ", normalized)
    normalized = " ".join(normalized.split()).strip(" ,;:")
    if (
        _MICROMACHINE_RETREAT_TEXT_RE.search(normalized)
        or _MICROMACHINE_ATTACK_CANCEL_TEXT_RE.search(normalized)
    ):
        return True
    if not isinstance(provider_output, Mapping):
        return False
    if str(provider_output.get("command_layer", "") or "").lower() == "emergency":
        return True
    if str(provider_output.get("override_level", "") or "").lower() == "emergency":
        return True
    emergency = provider_output.get("emergency")
    return isinstance(emergency, Mapping) and any(bool(value) for value in emergency.values())


def _micromachine_emergency_safety_output(text: str) -> dict[str, object]:
    """Compile explicit retreat/cancel intent without waiting on an LLM."""

    return {
        "source": PolicyModulationSource.UI.value,
        "goal": text,
        "assistant_message": "긴급 후퇴·공격 취소를 safety override로 즉시 적용했습니다.",
        "override_level": "emergency",
        "command_layer": "emergency",
        "confidence": 1.0,
        "ttl_seconds": 45,
        "strategy": {"posture": "defensive"},
        "combat": {
            "aggression": -0.9,
            "defend_bias": 0.6,
            "preserve_army_bias": 0.95,
            "attack_condition_override": "normal",
        },
        "squad": {
            "main_army_bias": -0.8,
            "regroup_bias": 0.95,
            "defense_bias": 0.7,
        },
        "emergency": {
            "cancel_attacks": True,
            "force_retreat": True,
        },
        "workers": {"repeat_order_guard_frames": 32},
        "lifetime": {
            "mode": "emergency_window",
            "completion_conditions": [
                "retreat_confirmed",
                "ttl_expired",
            ],
            "completion_state": "active",
            "reason": "deterministic safety override",
        },
        "tags": [
            "web_gui",
            "deterministic_emergency",
            "safety_override",
        ],
        "rationale": (
            "Safety-critical retreat and attack cancellation bypass LLM latency."
        ),
    }


class _SemanticScopePolicyModulationProvider:
    """Merge UI semantic scope into a bounded provider output."""

    def __init__(
        self,
        base_provider: object,
        *,
        semantic_scope: Mapping[str, object] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self.base_provider = base_provider
        self.semantic_scope = dict(semantic_scope or {})
        self.ttl_seconds = ttl_seconds
        self.source = getattr(base_provider, "source", None)

    def propose_policy_modulation(self, request: object) -> Mapping[str, object]:
        method = getattr(self.base_provider, "propose_policy_modulation", None)
        if not callable(method):
            raise RuntimeError("base policy modulation provider is not callable.")
        output = method(request)
        if not isinstance(output, Mapping):
            return output
        terminal_output = _terminal_micromachine_provider_output(output)
        if terminal_output is not None:
            return terminal_output
        return _merge_micromachine_semantic_scope_into_provider_output(
            output,
            semantic_scope=self.semantic_scope,
            ttl_seconds=self.ttl_seconds,
        )


class _LocalLLMPolicyModulationProvider:
    """Adapter from LocalLLMControl to the MicroMachine provider protocol."""

    source = PolicyModulationSource.LLM

    def __init__(
        self,
        llm_control: object | None,
        *,
        recent_commands: Sequence[Mapping[str, object]] | None = None,
    ) -> None:
        self.llm_control = llm_control
        self.recent_commands = (
            json.loads(json.dumps(list(recent_commands), ensure_ascii=False))
            if recent_commands is not None
            else None
        )

    def propose_policy_modulation(self, request: object) -> Mapping[str, object]:
        control = self.llm_control
        if control is None:
            return _llm_policy_modulation_unavailable_output(
                "LLM 설정이 없어 MicroMachine production 텍스트를 publish하지 않았습니다."
            )
        snapshot = getattr(control, "snapshot", None)
        if callable(snapshot):
            try:
                document = dict(snapshot())
            except Exception as error:  # noqa: BLE001 - fail-closed provider seam.
                return _llm_policy_modulation_unavailable_output(
                    f"LLM 설정 상태를 확인하지 못했습니다: {type(error).__name__}: {error}"
                )
            if not bool(document.get("configured")):
                return _llm_policy_modulation_unavailable_output(
                    "LLM 키가 설정되지 않아 MicroMachine production 텍스트를 publish하지 않았습니다."
                )
        available = getattr(control, "is_available", None)
        if callable(available):
            try:
                if not bool(available()):
                    return _llm_policy_modulation_unavailable_output(
                        "LLM provider가 사용 가능하지 않아 MicroMachine production 텍스트를 publish하지 않았습니다."
                    )
            except Exception as error:  # noqa: BLE001 - fail-closed provider seam.
                return _llm_policy_modulation_unavailable_output(
                    f"LLM provider 확인에 실패했습니다: {type(error).__name__}: {error}"
                )
        propose = getattr(control, "propose_policy_modulation", None)
        if not callable(propose):
            return _llm_policy_modulation_unavailable_output(
                "LLM control이 MicroMachine policy modulation provider를 지원하지 않습니다."
            )
        provider_request = request
        if self.recent_commands is not None:
            commander_context = getattr(request, "commander_context", {})
            if isinstance(commander_context, Mapping):
                compact_context = dict(commander_context)
                compact_context["recent_commands"] = (
                    _merge_micromachine_provider_recent_commands(
                        self.recent_commands,
                        commander_context.get("recent_commands"),
                    )
                )
                try:
                    provider_request = replace(
                        request,
                        commander_context=compact_context,
                    )
                except TypeError:
                    provider_request = request
        try:
            output = propose(provider_request)
        except Exception as error:  # noqa: BLE001 - normalize provider boundary.
            return {
                **_llm_policy_modulation_unavailable_output(
                    f"LLM provider 호출에 실패했습니다: {type(error).__name__}: {error}"
                ),
                "failure_kind": "api_error",
            }
        if not isinstance(output, Mapping):
            return _llm_policy_modulation_unavailable_output(
                "LLM provider가 JSON 객체가 아닌 응답을 반환했습니다."
            )
        return {**dict(output), "source": "llm"}


def _llm_policy_modulation_unavailable_output(reason: str) -> Mapping[str, object]:
    return {
        "source": "llm",
        "status": "refused",
        "refusal_reason": reason,
        "failure_kind": "provider_unavailable",
    }


@runtime_checkable
class WebGuiBridgeInterface(Protocol):
    """Boundary between the HTTP layer and the command session loop."""

    def submit_command(self, text: str) -> None:
        """Enqueue one commander utterance without blocking on execution."""

    def state_snapshot(self) -> Mapping[str, object] | None:
        """Return a JSON-ready commander state snapshot, or ``None``."""

    def history_since(self, seq: int) -> Sequence[Mapping[str, object]]:
        """Return JSON-ready outcome events recorded after sequence ``seq``."""

    def latest_seq(self) -> int:
        """Return the highest recorded event sequence number (0 when empty)."""

    def llm_settings_snapshot(self) -> Mapping[str, object]:
        """Return safe LLM setting metadata, never the API key."""

    def configure_llm(self, provider: str, api_key: str, model: str = "") -> Mapping[str, object]:
        """Configure local process-memory LLM credentials."""


class _SimpleHistory:
    """Minimal thread-safe in-memory outcome history store.

    This is the default history seam for :class:`SessionLoopBridge` so the
    web GUI works standalone; the integrator swaps in the richer
    ``CommanderEventMemory`` (same duck-typed ``record``/``since``/
    ``latest_seq`` surface) once event memory lands. Sequence numbers are
    monotonically increasing from 1.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, object]] = []
        self._seq = 0

    def record(self, outcome: object) -> int:
        """Record one outcome-like object; return its assigned sequence."""

        event = _outcome_event(outcome)
        with self._lock:
            self._seq += 1
            event["seq"] = self._seq
            self._events.append(event)
            return self._seq

    def since(self, seq: int) -> list[dict[str, object]]:
        """Return copies of every event recorded after sequence ``seq``."""

        threshold = int(seq)
        with self._lock:
            return [
                dict(event)
                for event in self._events
                if int(event.get("seq", 0)) > threshold  # type: ignore[call-overload]
            ]

    def latest_seq(self) -> int:
        """Return the highest assigned sequence number (0 when empty)."""

        with self._lock:
            return self._seq


class _WebEventJournal:
    """Thread-safe append-only event journal with bounded replay retention."""

    def __init__(self, retention: int = WEB_GUI_EVENT_RETENTION) -> None:
        if type(retention) is not int or retention < 1:
            raise ValueError("Web event journal retention must be a positive int.")
        self._retention = retention
        self._condition = threading.Condition()
        self._events: deque[dict[str, object]] = deque(maxlen=retention)
        self._seq = 0

    @property
    def latest_seq(self) -> int:
        with self._condition:
            return self._seq

    @property
    def oldest_seq(self) -> int:
        with self._condition:
            if not self._events:
                return self._seq + 1
            return int(self._events[0]["event_seq"])

    def publish(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        update_id: str = "",
        operation_id: str = "",
        generation: int = 0,
        game_frame: int = -1,
        blackboard_scope_id: str = "",
    ) -> dict[str, object]:
        safe_payload = _redact_json_ready(payload)
        if not isinstance(safe_payload, Mapping):
            safe_payload = {"value": safe_payload}
        with self._condition:
            self._seq += 1
            event = {
                "event_seq": self._seq,
                "event_type": str(event_type),
                "created_at_unix_ms": int(time.time() * 1000),
                "update_id": str(update_id or ""),
                "operation_id": str(operation_id or ""),
                "generation": max(0, int(generation)),
                "game_frame": int(game_frame),
                "blackboard_scope_id": str(blackboard_scope_id or ""),
                "payload": dict(safe_payload),
            }
            self._events.append(event)
            self._condition.notify_all()
            return dict(event)

    def replay_available(self, after: int) -> bool:
        threshold = max(0, int(after))
        with self._condition:
            return self._replay_available_locked(threshold)

    def _replay_available_locked(self, threshold: int) -> bool:
        if threshold > self._seq:
            return False
        if not self._events:
            return threshold >= self._seq
        return threshold >= int(self._events[0]["event_seq"]) - 1

    def replay_batch(
        self,
        after: int,
    ) -> tuple[bool, tuple[dict[str, object], ...]]:
        """Atomically verify replay retention and return the retained suffix."""

        threshold = max(0, int(after))
        with self._condition:
            if not self._replay_available_locked(threshold):
                return False, ()
            return True, tuple(
                dict(event)
                for event in self._events
                if int(event["event_seq"]) > threshold
            )

    def events_after(self, after: int) -> tuple[dict[str, object], ...]:
        threshold = max(0, int(after))
        with self._condition:
            return tuple(
                dict(event)
                for event in self._events
                if int(event["event_seq"]) > threshold
            )

    def wake_waiters(self) -> None:
        """Wake blocked SSE handlers so server shutdown does not leak threads."""

        with self._condition:
            self._condition.notify_all()

    def wait_for_events(
        self,
        after: int,
        timeout: float,
    ) -> tuple[dict[str, object], ...]:
        threshold = max(0, int(after))
        with self._condition:
            if self._seq <= threshold:
                self._condition.wait(timeout=max(0.0, float(timeout)))
            return tuple(
                dict(event)
                for event in self._events
                if int(event["event_seq"]) > threshold
            )

    def wait_for_replay_batch(
        self,
        after: int,
        timeout: float,
    ) -> tuple[bool, tuple[dict[str, object], ...]]:
        """Wait for data and atomically report whether replay is still whole."""

        threshold = max(0, int(after))
        with self._condition:
            if self._seq <= threshold:
                self._condition.wait(timeout=max(0.0, float(timeout)))
            if not self._replay_available_locked(threshold):
                return False, ()
            return True, tuple(
                dict(event)
                for event in self._events
                if int(event["event_seq"]) > threshold
            )


class _OperationSemanticTimelineReducer:
    """Reduce repeated operation snapshots into bounded semantic events."""

    _SCOPE_RETENTION = 8
    _PER_SCOPE_OPERATION_RETENTION = 64
    _GLOBAL_OPERATION_RETENTION = (
        _SCOPE_RETENTION * _PER_SCOPE_OPERATION_RETENTION
    )
    _GLOBAL_OPERATION_HISTORY_RETENTION = (
        _GLOBAL_OPERATION_RETENTION * 2
    )
    _SCOPE_EPOCH_HISTORY_RETENTION = _SCOPE_RETENTION * 8
    _PER_OPERATION_RETENTION = 32
    _PER_OPERATION_TOKEN_RETENTION = 64
    _PER_SCOPE_RETENTION = 192
    _PERMANENT_MILESTONE_KINDS = frozenset(
        {
            "received",
            "planned",
            "assigned",
            "submitted",
            "movement_observed",
            "engagement_observed",
            "target_reached",
            "completed",
        }
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._states: dict[
            tuple[str, str, str, int],
            dict[str, object],
        ] = {}
        self._generation_high_water: dict[
            tuple[str, str, str],
            int,
        ] = {}
        self._requested_generation_high_water: dict[
            tuple[str, str, str],
            int,
        ] = {}
        self._family_last_frame: dict[
            tuple[str, str, str],
            int,
        ] = {}
        self._accepted_operations: dict[
            tuple[str, str, str],
            dict[str, object],
        ] = {}
        self._retired_operation_identities: dict[
            tuple[str, str, str],
            dict[str, object],
        ] = {}
        self._scope_events: dict[str, deque[dict[str, object]]] = {}
        self._scope_epochs: dict[str, str] = {}
        self._retired_scope_epochs: dict[str, deque[str]] = {}
        self._scope_battlefield_overviews: dict[
            str,
            dict[str, object],
        ] = {}
        self._scope_order: deque[str] = deque()
        self._scope_epoch_history: dict[str, str] = {}
        self._scope_epoch_history_order: deque[str] = deque()
        self._scope_families: dict[
            str,
            deque[tuple[str, str, str]],
        ] = {}
        self._family_order: deque[tuple[str, str, str]] = deque()

    def _drop_scope(self, scope_id: str) -> None:
        self._scope_events.pop(scope_id, None)
        self._scope_epochs.pop(scope_id, None)
        self._scope_families.pop(scope_id, None)

    def _touch_scope(self, scope_id: str) -> None:
        try:
            self._scope_order.remove(scope_id)
        except ValueError:
            pass
        self._scope_order.append(scope_id)
        while len(self._scope_order) > self._SCOPE_RETENTION:
            evicted_scope = self._scope_order.popleft()
            self._drop_scope(evicted_scope)

    def _remember_scope_epoch(
        self,
        scope_id: str,
        session_epoch: str,
    ) -> None:
        if not session_epoch:
            return
        try:
            self._scope_epoch_history_order.remove(scope_id)
        except ValueError:
            pass
        self._scope_epoch_history_order.append(scope_id)
        self._scope_epoch_history[scope_id] = session_epoch
        while (
            len(self._scope_epoch_history_order)
            > self._SCOPE_EPOCH_HISTORY_RETENTION
        ):
            evicted_scope = self._scope_epoch_history_order.popleft()
            if evicted_scope in self._scope_epochs:
                self._scope_epoch_history_order.append(evicted_scope)
                continue
            self._scope_epoch_history.pop(evicted_scope, None)
            self._retired_scope_epochs.pop(evicted_scope, None)
            self._scope_battlefield_overviews.pop(evicted_scope, None)

    def _reset_scope_epoch(self, scope_id: str, session_epoch: str) -> None:
        previous_epoch = (
            self._scope_epochs.get(scope_id, "")
            or self._scope_epoch_history.get(scope_id, "")
        )
        retired = self._retired_scope_epochs.setdefault(
            scope_id,
            deque(maxlen=self._SCOPE_RETENTION),
        )
        if previous_epoch and previous_epoch != session_epoch:
            try:
                retired.remove(previous_epoch)
            except ValueError:
                pass
            retired.append(previous_epoch)
        self._drop_scope(scope_id)
        self._scope_battlefield_overviews.pop(scope_id, None)
        self._scope_epochs[scope_id] = session_epoch
        self._remember_scope_epoch(scope_id, session_epoch)
        self._scope_events[scope_id] = deque(
            maxlen=self._PER_SCOPE_RETENTION
        )
        self._scope_families[scope_id] = deque()

    def _incoming_epoch_is_stale(
        self,
        scope_id: str,
        current_epoch: str,
        incoming_epoch: str,
    ) -> bool:
        if not incoming_epoch or not current_epoch:
            return False
        retired = self._retired_scope_epochs.get(scope_id, ())
        if incoming_epoch in retired:
            return True
        try:
            return int(incoming_epoch) < int(current_epoch)
        except (TypeError, ValueError):
            return False

    def _accepted_scope_operations(
        self,
        scope_id: str,
        session_epoch: str,
    ) -> list[dict[str, object]]:
        return [
            deepcopy(operation)
            for family_key, operation in self._accepted_operations.items()
            if family_key[0] == scope_id and family_key[1] == session_epoch
        ]

    def _restore_accepted_snapshot(
        self,
        result: dict[str, object],
        *,
        scope_id: str,
        session_epoch: str,
    ) -> dict[str, object]:
        """Return one internally consistent previously accepted scope view."""

        accepted_operations = self._accepted_scope_operations(
            scope_id,
            session_epoch,
        )
        result["operations"] = accepted_operations
        result["operation_summary"] = _micromachine_operation_summary(
            accepted_operations
        )
        accepted_overview = self._scope_battlefield_overviews.get(scope_id)
        result["battlefield_overview"] = (
            deepcopy(accepted_overview)
            if accepted_overview is not None
            else None
        )
        scope_events = self._scope_events.setdefault(
            scope_id,
            deque(maxlen=self._PER_SCOPE_RETENTION),
        )
        result["operation_events"] = [
            dict(event) for event in scope_events
        ]
        result["operation_event_latest_seq"] = (
            int(scope_events[-1]["timeline_seq"])
            if scope_events
            else 0
        )
        return result

    @staticmethod
    def _overview_for_accepted_operations(
        battlefield_overview: Mapping[str, object],
        accepted_operations: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        """Keep the authoritative overview aligned with admitted operations."""

        accepted_ids = {
            str(operation.get("operation_id", "") or "").strip()
            for operation in accepted_operations
            if str(operation.get("operation_id", "") or "").strip()
        }
        overview = deepcopy(dict(battlefield_overview))
        raw_ownership = overview.get("operation_ownership")
        ownership = [
            deepcopy(dict(item))
            for item in (
                raw_ownership
                if isinstance(raw_ownership, Sequence)
                and not isinstance(
                    raw_ownership,
                    (str, bytes, bytearray),
                )
                else ()
            )
            if isinstance(item, Mapping)
            and str(item.get("operation_id", "") or "").strip()
            in accepted_ids
        ]
        overview["operation_ownership"] = ownership
        overview["explicit_operation_owned_count"] = sum(
            max(
                0,
                _int_or_none(
                    _mapping_child(item, "operation_ownership").get(
                        "owner_count"
                    )
                )
                or 0,
            )
            for item in ownership
        )
        transfer_availability = overview.get("transfer_availability")
        if isinstance(transfer_availability, Mapping):
            transfer_payload = deepcopy(dict(transfer_availability))
            raw_entries = transfer_payload.get("entries")
            transfer_payload["entries"] = [
                deepcopy(dict(item))
                for item in (
                    raw_entries
                    if isinstance(raw_entries, Sequence)
                    and not isinstance(
                        raw_entries,
                        (str, bytes, bytearray),
                    )
                    else ()
                )
                if isinstance(item, Mapping)
                and str(item.get("source_owner_id", "") or "").strip()
                in accepted_ids
                and (
                    not str(
                        item.get("counterpart_operation_id", "") or ""
                    ).strip()
                    or str(
                        item.get("counterpart_operation_id", "") or ""
                    ).strip()
                    in accepted_ids
                )
            ]
            overview["transfer_availability"] = transfer_payload
        return overview

    def _touch_family(
        self,
        family_key: tuple[str, str, str],
    ) -> None:
        scope_id = family_key[0]
        families = self._scope_families.setdefault(scope_id, deque())
        try:
            families.remove(family_key)
        except ValueError:
            pass
        families.append(family_key)
        try:
            self._family_order.remove(family_key)
        except ValueError:
            pass
        self._family_order.append(family_key)
        while len(families) > self._PER_SCOPE_OPERATION_RETENTION:
            self._retire_family(families.popleft())
        while (
            len(self._family_order)
            > self._GLOBAL_OPERATION_HISTORY_RETENTION
        ):
            self._drop_family(self._family_order.popleft())

    def _retire_family(
        self,
        family_key: tuple[str, str, str],
    ) -> None:
        """Drop active projection state while retaining semantic high-water."""

        accepted = self._accepted_operations.pop(family_key, None)
        if accepted is not None:
            self._retired_operation_identities[family_key] = {
                "operation_id": str(
                    accepted.get("operation_id", "") or ""
                ),
                "operation_generation": max(
                    0,
                    _int_or_none(
                        accepted.get("operation_generation")
                    )
                    or 0,
                ),
                "requested_operation_generation": max(
                    0,
                    _int_or_none(
                        accepted.get("requested_operation_generation")
                    )
                    or 0,
                ),
                "update_id": self._operation_request_update_id(accepted),
                "operation_console_execution_owner_update_id": (
                    self._operation_execution_owner_update_id(accepted)
                ),
            }
        families = self._scope_families.get(family_key[0])
        if families is not None:
            try:
                families.remove(family_key)
            except ValueError:
                pass

    def _drop_family(
        self,
        family_key: tuple[str, str, str],
    ) -> None:
        self._generation_high_water.pop(family_key, None)
        self._requested_generation_high_water.pop(family_key, None)
        self._family_last_frame.pop(family_key, None)
        self._accepted_operations.pop(family_key, None)
        self._retired_operation_identities.pop(family_key, None)
        self._states = {
            key: value
            for key, value in self._states.items()
            if key[:3] != family_key
        }
        families = self._scope_families.get(family_key[0])
        if families is not None:
            try:
                families.remove(family_key)
            except ValueError:
                pass
        try:
            self._family_order.remove(family_key)
        except ValueError:
            pass

    def _snapshot_operations_are_monotonic(
        self,
        operations: Sequence[Mapping[str, object]],
        *,
        scope_id: str,
        session_epoch: str,
    ) -> bool:
        """Validate a complete source snapshot before mutating reducer state."""

        provisional_generation = dict(self._generation_high_water)
        provisional_frame = dict(self._family_last_frame)
        provisional_fingerprints: dict[
            tuple[str, str, str, int],
            tuple[int, str],
        ] = {
            key: (
                int(state["last_frame"]),
                str(state["last_fingerprint"]),
            )
            for key, state in self._states.items()
        }
        identified_operation_seen = False
        admissible_operation_seen = False
        for raw_operation in operations:
            operation = dict(raw_operation)
            operation_id = str(
                operation.get("operation_id", "") or ""
            ).strip()
            generation = max(
                0,
                _int_or_none(operation.get("operation_generation")) or 0,
            )
            if not operation_id or generation <= 0:
                continue
            identified_operation_seen = True
            family_key = (scope_id, session_epoch, operation_id)
            high_water = provisional_generation.get(family_key, 0)
            if (
                family_key in self._retired_operation_identities
                and generation <= high_water
            ):
                continue
            admissible_operation_seen = True
            requested_generation = max(
                generation,
                _int_or_none(
                    operation.get("requested_operation_generation")
                )
                or generation,
            )
            requested_high_water = (
                self._requested_generation_high_water.get(family_key, 0)
            )
            accepted = self._accepted_operations.get(family_key)
            execution_owner_update = (
                self._same_generation_execution_owner_update(
                    operation,
                    accepted,
                    generation=generation,
                    generation_high_water=high_water,
                    requested_generation=requested_generation,
                    requested_generation_high_water=requested_high_water,
                )
            )
            if (
                requested_generation > 0
                and requested_generation < requested_high_water
                and generation <= high_water
                and not execution_owner_update
            ):
                return False
            if self._same_generation_update_conflicts(
                operation,
                accepted,
                generation=generation,
                generation_high_water=high_water,
                requested_generation=requested_generation,
                requested_generation_high_water=requested_high_water,
            ):
                return False
            if (
                requested_generation < requested_high_water
                and (
                    generation > high_water
                    or execution_owner_update
                )
                and accepted is not None
            ):
                operation = self._preserve_latest_requested_intent(
                    operation,
                    accepted,
                    requested_high_water,
                )
            if generation < high_water:
                return False
            battlefield_operation = operation.get(
                "battlefield_operation"
            )
            battlefield_operation = (
                dict(battlefield_operation)
                if isinstance(battlefield_operation, Mapping)
                else {}
            )
            projection_advances_monotonic_state = bool(
                not battlefield_operation
                or self._projection_matches_operation(
                    operation,
                    battlefield_operation,
                )
            )
            frame = self._operation_frame(
                operation,
                battlefield_operation,
            )
            fingerprint = self._semantic_fingerprint(
                operation,
                battlefield_operation,
            )
            family_last_frame = provisional_frame.get(family_key, -1)
            key = (
                scope_id,
                session_epoch,
                operation_id,
                generation,
            )
            last_frame, last_fingerprint = provisional_fingerprints.get(
                key,
                (-1, ""),
            )
            if (
                projection_advances_monotonic_state
                and family_last_frame >= 0
                and (frame < 0 or frame < family_last_frame)
            ):
                return False
            if (
                projection_advances_monotonic_state
                and generation == high_water
                and frame >= 0
                and frame == last_frame
                and last_fingerprint
                and fingerprint != last_fingerprint
            ):
                return False
            if generation > high_water:
                provisional_generation[family_key] = generation
                provisional_fingerprints = {
                    state_key: value
                    for state_key, value in provisional_fingerprints.items()
                    if state_key[:3] != family_key
                }
            if projection_advances_monotonic_state and frame >= 0:
                provisional_frame[family_key] = max(
                    family_last_frame,
                    frame,
                )
                provisional_fingerprints[key] = (frame, fingerprint)
        return not identified_operation_seen or admissible_operation_seen

    @staticmethod
    def _preserve_latest_requested_intent(
        operation: Mapping[str, object],
        accepted: Mapping[str, object],
        requested_high_water: int,
    ) -> dict[str, object]:
        """Keep newer execution telemetry without reviving an older edit."""

        merged = deepcopy(dict(operation))
        incoming_intervention = _mapping_child(operation, "intervention")
        incoming_execution = _mapping_child(
            incoming_intervention,
            "command_execution",
        )
        execution_owner_vector = deepcopy(
            dict(
                _mapping_child(
                    _mapping_child(operation, "update"),
                    "vector",
                )
                or _mapping_child(
                    _mapping_child(operation, "compile_result"),
                    "vector",
                )
                or _mapping_child(
                    operation,
                    "operation_console_execution_owner_vector",
                )
            )
        )
        execution_owner_update_id = str(
            operation.get(
                "operation_console_execution_owner_update_id",
                "",
            )
            or incoming_execution.get("command_id", "")
            or operation.get("update_id", "")
            or ""
        )
        for field in (
            "command_text",
            "compile_result",
            "latest_request",
            "update",
            "command_queue",
            "operation_edit",
            "update_id",
        ):
            if field in accepted:
                merged[field] = deepcopy(accepted[field])
        if execution_owner_update_id:
            merged["operation_console_execution_owner_update_id"] = (
                execution_owner_update_id
            )
        if execution_owner_vector:
            merged["operation_console_execution_owner_vector"] = (
                execution_owner_vector
            )
        merged["requested_operation_generation"] = requested_high_water
        return merged

    @staticmethod
    def _execution_stage_ok(
        execution: Mapping[str, object],
        *names: str,
    ) -> bool:
        stages = execution.get("stages")
        if not isinstance(stages, Sequence) or isinstance(
            stages,
            (str, bytes, bytearray),
        ):
            return False
        accepted = set(names)
        return any(
            isinstance(stage, Mapping)
            and str(stage.get("name", "") or "") in accepted
            and stage.get("ok") is True
            for stage in stages
        )

    @staticmethod
    def _operation_frame(
        operation: Mapping[str, object],
        battlefield_operation: Mapping[str, object],
    ) -> int:
        candidates: list[object] = [
            operation.get("telemetry_frame"),
            _mapping_child(battlefield_operation, "identity").get(
                "game_frame"
            ),
            _mapping_child(
                battlefield_operation,
                "operation_completion",
            ).get("frame"),
        ]
        frames = [
            int(value)
            for value in candidates
            if type(value) is int and int(value) >= 0
        ]
        return max(frames) if frames else -1

    @staticmethod
    def _semantic_fingerprint(
        operation: Mapping[str, object],
        battlefield_operation: Mapping[str, object],
    ) -> str:
        intervention = _mapping_child(operation, "intervention")
        document = {
            "update_id": str(operation.get("update_id", "") or ""),
            "operation_id": str(operation.get("operation_id", "") or ""),
            "operation_generation": _int_or_none(
                operation.get("operation_generation")
            ),
            "requested_operation_generation": _int_or_none(
                operation.get("requested_operation_generation")
            ),
            "telemetry_frame": _int_or_none(
                operation.get("telemetry_frame")
            ),
            "disposition": str(operation.get("disposition", "") or ""),
            "transport_status": str(
                operation.get("transport_status", "") or ""
            ),
            "consumption_status": str(
                operation.get("consumption_status", "") or ""
            ),
            "compile_result": _mapping_child(operation, "compile_result"),
            "command_execution": _mapping_child(
                intervention,
                "command_execution",
            ),
            "operation_console_execution_owner_vector": _mapping_child(
                operation,
                "operation_console_execution_owner_vector",
            ),
            "update": _mapping_child(operation, "update"),
            "family_evidence": operation.get("family_evidence", ()),
            "operation_convergence": _mapping_child(
                operation,
                "operation_convergence",
            ),
            "operation_edit": _mapping_child(
                operation,
                "operation_edit",
            ),
            "squad_order": str(operation.get("squad_order", "") or ""),
            "battlefield_operation": dict(battlefield_operation),
        }
        return hashlib.sha256(
            json.dumps(
                _redact_json_ready(document),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _operation_vector(
        operation: Mapping[str, object],
    ) -> Mapping[str, object]:
        execution_owner_vector = _mapping_child(
            operation,
            "operation_console_execution_owner_vector",
        )
        if execution_owner_vector:
            return execution_owner_vector
        update_vector = _mapping_child(
            _mapping_child(operation, "update"),
            "vector",
        )
        if update_vector:
            return update_vector
        return _mapping_child(
            _mapping_child(operation, "compile_result"),
            "vector",
        )

    @staticmethod
    def _operation_request_update_id(
        operation: Mapping[str, object],
    ) -> str:
        update = _mapping_child(operation, "update")
        compile_result = _mapping_child(operation, "compile_result")
        return str(
            operation.get("update_id", "")
            or update.get("update_id", "")
            or compile_result.get("update_id", "")
            or ""
        )

    @staticmethod
    def _operation_execution_owner_update_id(
        operation: Mapping[str, object],
    ) -> str:
        execution = _mapping_child(
            _mapping_child(operation, "intervention"),
            "command_execution",
        )
        return str(
            operation.get(
                "operation_console_execution_owner_update_id",
                "",
            )
            or execution.get("command_id", "")
            or ""
        )

    @classmethod
    def _operation_update_id(
        cls,
        operation: Mapping[str, object],
    ) -> str:
        return (
            cls._operation_request_update_id(operation)
            or cls._operation_execution_owner_update_id(operation)
        )

    @staticmethod
    def _operation_edit_action(
        operation: Mapping[str, object],
    ) -> str:
        action = str(
            _mapping_child(operation, "operation_edit").get(
                "action",
                "",
            )
            or ""
        ).strip()
        return (
            action
            if action
            and action in MICROMACHINE_OPERATION_EDIT_ACTIONS
            else ""
        )

    @classmethod
    def _same_generation_execution_owner_update(
        cls,
        operation: Mapping[str, object],
        accepted: Mapping[str, object] | None,
        *,
        generation: int,
        generation_high_water: int,
        requested_generation: int,
        requested_generation_high_water: int,
    ) -> bool:
        """Recognize delayed telemetry from the preserved execution owner."""

        if (
            accepted is None
            or generation <= 0
            or generation != generation_high_water
            or requested_generation >= requested_generation_high_water
        ):
            return False
        incoming_request_id = cls._operation_request_update_id(operation)
        incoming_owner_id = cls._operation_execution_owner_update_id(
            operation
        )
        accepted_request_id = cls._operation_request_update_id(accepted)
        accepted_owner_id = cls._operation_execution_owner_update_id(
            accepted
        )
        return bool(
            accepted_request_id
            and accepted_owner_id
            and incoming_request_id
            in {accepted_request_id, accepted_owner_id}
            and incoming_owner_id == accepted_owner_id
        )

    @classmethod
    def _same_generation_update_conflicts(
        cls,
        operation: Mapping[str, object],
        accepted: Mapping[str, object] | None,
        *,
        generation: int,
        generation_high_water: int,
        requested_generation: int,
        requested_generation_high_water: int,
    ) -> bool:
        """Reject a foreign snapshot masquerading as the current generation."""

        if (
            accepted is None
            or generation <= 0
            or generation != generation_high_water
        ):
            return False
        incoming_request_id = cls._operation_request_update_id(operation)
        incoming_owner_id = cls._operation_execution_owner_update_id(
            operation
        )
        accepted_request_id = cls._operation_request_update_id(accepted)
        accepted_owner_id = cls._operation_execution_owner_update_id(
            accepted
        )
        exact_identity = bool(
            accepted_request_id
            and accepted_owner_id
            and incoming_request_id == accepted_request_id
            and incoming_owner_id == accepted_owner_id
        )
        if exact_identity:
            return False
        if cls._same_generation_execution_owner_update(
            operation,
            accepted,
            generation=generation,
            generation_high_water=generation_high_water,
            requested_generation=requested_generation,
            requested_generation_high_water=(
                requested_generation_high_water
            ),
        ):
            return False
        edit_action = cls._operation_edit_action(operation)
        is_new_request = bool(
            requested_generation > requested_generation_high_water
            and incoming_request_id
            and incoming_request_id
            not in {accepted_request_id, accepted_owner_id}
            and incoming_owner_id == accepted_owner_id
            and edit_action
        )
        execution = _mapping_child(
            _mapping_child(operation, "intervention"),
            "command_execution",
        )
        execution_state = str(
            execution.get("state", "") or ""
        ).lower()
        execution_generation = max(
            0,
            _int_or_none(
                execution.get("operation_generation")
                or execution.get("generation")
            )
            or 0,
        )
        cancellation_identity_matches = bool(
            execution_state in {"cancelled", "canceled"}
            and str(
                execution.get("blocker_reason", "") or ""
            ).lower()
            == "cancelled_by_policy"
            and incoming_owner_id
            and incoming_owner_id == accepted_owner_id
            and str(execution.get("operation_id", "") or "")
            == str(operation.get("operation_id", "") or "")
            and execution_generation == generation
        )
        is_cancellation_transition = bool(
            cancellation_identity_matches
            and incoming_request_id == accepted_request_id
        )
        is_new_cancellation_request = bool(
            cancellation_identity_matches
            and is_new_request
            and edit_action == "cancel"
        )
        if execution_state in {"cancelled", "canceled"}:
            return not (
                is_cancellation_transition
                or is_new_cancellation_request
            )
        return not is_new_request

    @staticmethod
    def _operation_force_counts(
        operation: Mapping[str, object],
        battlefield_operation: Mapping[str, object],
    ) -> tuple[int, int]:
        ownership = _mapping_child(
            battlefield_operation,
            "operation_ownership",
        )
        launch = _mapping_child(
            battlefield_operation,
            "operation_launch_policy",
        )
        convergence = _mapping_child(operation, "operation_convergence")
        return (
            max(
                0,
                _int_or_none(ownership.get("owner_count")) or 0,
            ),
            max(
                0,
                _int_or_none(launch.get("min_units"))
                or _int_or_none(convergence.get("target_count"))
                or 0,
            ),
        )

    @staticmethod
    def _projection_matches_operation(
        operation: Mapping[str, object],
        battlefield_operation: Mapping[str, object],
    ) -> bool:
        operation_id = str(operation.get("operation_id", "") or "")
        generation = max(
            0,
            _int_or_none(operation.get("operation_generation")) or 0,
        )
        update_id = str(
            operation.get(
                "operation_console_execution_owner_update_id",
                "",
            )
            or operation.get("update_id", "")
            or ""
        )
        projection_identity = _mapping_child(
            battlefield_operation,
            "identity",
        )
        return bool(
            update_id
            and operation_id
            and generation > 0
            and str(projection_identity.get("update_id", "") or "")
            == update_id
            and str(battlefield_operation.get("operation_id", "") or "")
            == operation_id
            and _int_or_none(
                battlefield_operation.get("generation")
            )
            == generation
            and str(projection_identity.get("operation_id", "") or "")
            == operation_id
            and _int_or_none(projection_identity.get("generation"))
            == generation
        )

    @staticmethod
    def _critical_ability_failure(
        operation: Mapping[str, object],
        execution: Mapping[str, object],
        *,
        update_id: str,
        operation_id: str,
        generation: int,
    ) -> tuple[str, str, int] | None:
        vector = _OperationSemanticTimelineReducer._operation_vector(
            operation
        )
        tactical_task = _mapping_child(vector, "tactical_task")
        ability = str(tactical_task.get("ability", "") or "").strip()
        expected_action = ability.lower()
        if expected_action and not expected_action.startswith("ability:"):
            expected_action = f"ability:{expected_action}"
        task_type = str(
            tactical_task.get("task_type", "") or ""
        ).strip().lower()
        if not ability or task_type != "execute_ability":
            return None
        execution_state = str(execution.get("state", "") or "").lower()
        execution_blocker = str(
            execution.get("blocker_reason", "") or ""
        ).strip()
        execution_blocker_manager = str(
            execution.get("blocker_manager", "") or ""
        ).strip()
        if (
            execution.get("failed") is not True
            and execution_state
            not in {"blocked", "failed", "rejected", "expired"}
        ) or not execution_blocker:
            return None
        raw_evidence = operation.get("family_evidence")
        evidence_rows = (
            raw_evidence
            if isinstance(raw_evidence, Sequence)
            and not isinstance(
                raw_evidence,
                (str, bytes, bytearray),
            )
            else ()
        )
        for raw_row in evidence_rows:
            if not isinstance(raw_row, Mapping):
                continue
            row = dict(raw_row)
            if (
                str(row.get("update_id", "") or "") != update_id
                or str(row.get("operation_id", "") or "")
                != operation_id
                or _int_or_none(row.get("generation")) != generation
            ):
                continue
            action = str(row.get("action", "") or "").strip().lower()
            blocker = str(row.get("blocker", "") or "").strip()
            blocker_manager = str(
                row.get("blocker_manager", "") or ""
            ).strip()
            required_effect = str(
                row.get("required_effect", "") or ""
            ).lower()
            effect_count = max(
                0,
                _int_or_none(row.get("effect_count")) or 0,
            )
            effect_frame = max(
                0,
                _int_or_none(row.get("effect_frame")) or 0,
            )
            attempted_count = max(
                0,
                _int_or_none(row.get("attempted_count")) or 0,
            )
            attempted_frame = max(
                0,
                _int_or_none(row.get("attempted_frame")) or 0,
            )
            stage = str(row.get("stage", "") or "").lower()
            if (
                action != expected_action
                or required_effect != "ability_state_or_effect"
                or stage != "blocked"
                or attempted_count <= 0
                or attempted_frame <= 0
                or effect_count > 0
                or effect_frame > 0
                or not blocker
                or blocker != execution_blocker
                or (
                    blocker_manager
                    and execution_blocker_manager
                    and blocker_manager != execution_blocker_manager
                )
            ):
                continue
            attempt_generation = max(
                0,
                _int_or_none(row.get("attempt_generation")) or 0,
            )
            reason = (
                f"{blocker_manager}: {blocker}"
                if blocker_manager
                else blocker
            )
            return ability, reason, attempt_generation
        return None

    @staticmethod
    def _base_threats(
        battlefield_overview: Mapping[str, object] | None,
    ) -> dict[str, dict[str, object]]:
        if not isinstance(battlefield_overview, Mapping):
            return {}
        raw_bases = battlefield_overview.get("bases")
        bases = (
            raw_bases
            if isinstance(raw_bases, Sequence)
            and not isinstance(raw_bases, (str, bytes, bytearray))
            else ()
        )
        result: dict[str, dict[str, object]] = {}
        for raw_base in bases:
            if not isinstance(raw_base, Mapping):
                continue
            base_id = str(raw_base.get("base_id", "") or "").strip()
            readiness = _mapping_child(raw_base, "base_readiness")
            if not base_id or not readiness:
                continue
            ground_threat = max(
                0.0,
                _number(readiness.get("ground_threat")),
            )
            air_threat = max(
                0.0,
                _number(readiness.get("air_threat")),
            )
            observed_strength = max(
                0.0,
                _number(readiness.get("observed_enemy_strength")),
            )
            readiness_state = str(
                readiness.get("readiness_state", "") or ""
            ).lower()
            result[base_id] = {
                "active": bool(
                    readiness_state == "unsafe"
                    and (
                        ground_threat > 0
                        or air_threat > 0
                        or observed_strength > 0
                    )
                ),
                "semantic_anchor": str(
                    raw_base.get("semantic_anchor", "") or ""
                ),
                "readiness_state": readiness_state,
                "reason": str(readiness.get("reason", "") or ""),
                "evidence_class": str(
                    readiness.get("evidence_class", "") or ""
                ),
                "last_evidence_frame": max(
                    0,
                    _int_or_none(
                        readiness.get("last_evidence_frame")
                    )
                    or 0,
                ),
                "ground_threat": ground_threat,
                "air_threat": air_threat,
                "observed_enemy_strength": observed_strength,
            }
        return result

    @staticmethod
    def _emergency_retreat_active(
        operation: Mapping[str, object],
        battlefield_operation: Mapping[str, object],
    ) -> bool:
        convergence = _mapping_child(operation, "operation_convergence")
        launch = _mapping_child(
            battlefield_operation,
            "operation_launch_policy",
        )
        launch_safety = _mapping_child(launch, "safety_evidence")
        emergency_preemption = str(
            launch_safety.get("emergency_preemption", "") or ""
        ).lower()
        return bool(
            str(convergence.get("status", "") or "").upper() == "BLOCKED"
            and str(convergence.get("blocker", "") or "")
            == "emergency_retreat_preempted"
            and str(operation.get("squad_order", "") or "").lower()
            == "retreat"
            and emergency_preemption
            not in {"", "none", "inactive", "not_required"}
        )

    @staticmethod
    def _base_attack_active(
        battlefield_operation: Mapping[str, object],
        base_threats: Mapping[str, Mapping[str, object]],
    ) -> bool:
        launch = _mapping_child(
            battlefield_operation,
            "operation_launch_policy",
        )
        launch_safety = _mapping_child(launch, "safety_evidence")
        return bool(
            any(
                threat.get("active") is True
                for threat in base_threats.values()
            )
            and str(launch.get("blocker", "") or "")
            == "base_protected_minimum_not_met"
            and launch_safety.get(
                "protected_defense_minimum_respected"
            )
            is False
        )

    @staticmethod
    def _event_candidates(
        operation: Mapping[str, object],
        battlefield_operation: Mapping[str, object],
        *,
        allow_edit_events: bool = True,
        previous_owner_count: int | None = None,
        previous_required_count: int = 0,
        previous_transferred_out_count: int = 0,
        previous_emergency_retreat_active: bool = False,
        previous_base_attack_active: bool = False,
        base_threats: Mapping[str, Mapping[str, object]] | None = None,
        frame: int = -1,
    ) -> list[tuple[str, str, str, dict[str, object]]]:
        operation_id = str(operation.get("operation_id", "") or "")
        generation = max(
            0,
            _int_or_none(operation.get("operation_generation")) or 0,
        )
        disposition = str(operation.get("disposition", "") or "").lower()
        transport_status = str(
            operation.get("transport_status", "") or ""
        ).lower()
        consumption_status = str(
            operation.get("consumption_status", "") or ""
        ).lower()
        update_id = str(operation.get("update_id", "") or "")
        compile_result = _mapping_child(operation, "compile_result")
        intervention = _mapping_child(operation, "intervention")
        execution = _mapping_child(intervention, "command_execution")
        execution_owner_update_id = str(
            operation.get(
                "operation_console_execution_owner_update_id",
                "",
            )
            or update_id
        )
        if not _micromachine_operation_execution_matches(
            execution,
            update_id=execution_owner_update_id,
            operation_id=operation_id,
            operation_generation=generation,
        ):
            execution = {}
        execution_state = str(execution.get("state", "") or "").lower()
        convergence = _mapping_child(operation, "operation_convergence")
        edit = _mapping_child(operation, "operation_edit")
        ownership = _mapping_child(
            battlefield_operation,
            "operation_ownership",
        )
        launch = _mapping_child(
            battlefield_operation,
            "operation_launch_policy",
        )
        completion = _mapping_child(
            battlefield_operation,
            "operation_completion",
        )
        projection_identity = _mapping_child(
            battlefield_operation,
            "identity",
        )
        lifetime = _mapping_child(
            battlefield_operation,
            "operation_lifetime",
        )
        projection_matches_operation = (
            _OperationSemanticTimelineReducer
            ._projection_matches_operation(
                operation,
                battlefield_operation,
            )
        )
        projection_identity_valid = bool(
            not battlefield_operation
            or projection_matches_operation
        )
        owner_count, required_count = (
            _OperationSemanticTimelineReducer._operation_force_counts(
                operation,
                battlefield_operation,
            )
        )
        represented_count = max(
            0,
            _int_or_none(convergence.get("represented_count")) or 0,
        )
        if not projection_identity_valid:
            owner_count = 0
            required_count = max(
                0,
                _int_or_none(convergence.get("target_count")) or 0,
            )
        requested_generation = max(
            generation,
            _int_or_none(
                operation.get("requested_operation_generation")
            )
            or generation,
        )
        launch_decision = (
            str(launch.get("decision", "") or "").lower()
            if projection_identity_valid
            else ""
        )
        blocker = str(
            (
                launch.get("blocker")
                if projection_identity_valid
                else ""
            )
            or convergence.get("blocker")
            or execution.get("blocker_reason")
            or ""
        )
        common = {
            "operation_id": operation_id,
            "generation": generation,
            "owner_count": owner_count,
            "required_count": required_count,
            "represented_count": represented_count,
            "disposition": disposition,
            "requested_generation": requested_generation,
            "update_id": update_id,
            "projection_identity_valid": projection_identity_valid,
        }
        canonical_completion_identity = bool(
            projection_matches_operation
            and _int_or_none(completion.get("generation")) == generation
        )
        candidates: list[tuple[str, str, str, dict[str, object]]] = [
            (
                "received",
                "received",
                f"Operation {operation_id} received.",
                common,
            )
        ]
        if (
            str(compile_result.get("status", "") or "").lower() == "compiled"
            or transport_status == "published"
            or consumption_status
            not in {"", "received", "pending_compile"}
        ):
            candidates.append(
                (
                    "planned",
                    "planned",
                    f"Operation {operation_id}#{generation} planned.",
                    common,
                )
            )
        assigned = bool(
            (
                projection_identity_valid
                and owner_count > 0
            )
            or execution_state in {
                "queued_or_assigned",
                "assigned",
            }
        )
        if assigned:
            if (
                projection_identity_valid
                and required_count > 0
                and owner_count < required_count
            ):
                candidates.append(
                    (
                        "partially_assigned",
                        "partially_assigned",
                        (
                            f"{owner_count}/{required_count} units assigned; "
                            f"{max(0, required_count - owner_count)} still needed."
                        ),
                        common,
                    )
                )
            else:
                candidates.append(
                    (
                        "assigned",
                        "assigned",
                        f"{owner_count or represented_count} units assigned.",
                        common,
                    )
                )
        waiting = (
            launch_decision in {"wait", "waiting", "blocked"}
            or execution_state.startswith("waiting")
            or bool(blocker)
            and disposition not in {"blocked", "expired", "superseded"}
        )
        if waiting:
            waiting_token = (
                f"waiting:{blocker}:{owner_count}:{required_count}:"
                f"{launch_decision}"
            )
            candidates.append(
                (
                    "waiting",
                    waiting_token,
                    blocker or "Waiting for operation conditions.",
                    {
                        **common,
                        "launch_decision": launch_decision,
                        "blocker": blocker,
                    },
                )
            )
        if _OperationSemanticTimelineReducer._execution_stage_ok(
            execution,
            "action_issued",
        ):
            candidates.append(
                (
                    "submitted",
                    "submitted",
                    "Matching-generation SC2 action submitted.",
                    {**common, "execution_state": execution_state},
                )
            )
        launch_safety = _mapping_child(launch, "safety_evidence")
        emergency_preemption = str(
            launch_safety.get("emergency_preemption", "") or ""
        ).lower()
        emergency_retreat_active = bool(
            projection_matches_operation
            and _OperationSemanticTimelineReducer
            ._emergency_retreat_active(
                operation,
                battlefield_operation,
            )
        )
        if (
            emergency_retreat_active
            and not previous_emergency_retreat_active
        ):
            candidates.append(
                (
                    "emergency_retreat",
                    f"emergency_retreat:{frame}",
                    "Emergency retreat order is active.",
                    {
                        **common,
                        "operation_convergence": dict(convergence),
                        "squad_order": str(
                            operation.get("squad_order", "") or ""
                        ),
                        "emergency_preemption": emergency_preemption,
                    },
                )
            )
        active_base_threats = {
            base_id: dict(threat)
            for base_id, threat in (base_threats or {}).items()
            if threat.get("active") is True
        }
        base_attack_active = bool(
            projection_matches_operation
            and _OperationSemanticTimelineReducer._base_attack_active(
                battlefield_operation,
                active_base_threats,
            )
        )
        if base_attack_active and not previous_base_attack_active:
            base_ids = sorted(active_base_threats)
            base_reasons = [
                str(active_base_threats[base_id].get("reason", "") or "")
                for base_id in base_ids
            ]
            candidates.append(
                (
                    "base_under_attack",
                    f"base_under_attack:{frame}:{','.join(base_ids)}",
                    (
                        "; ".join(
                            reason
                            for reason in base_reasons
                            if reason
                        )
                        or "Base defense minimum is not met under attack."
                    ),
                    {
                        **common,
                        "base_ids": base_ids,
                        "base_threats": active_base_threats,
                        "launch_safety": dict(launch_safety),
                    },
                )
            )
        critical_failure = (
            _OperationSemanticTimelineReducer._critical_ability_failure(
                operation,
                execution,
                update_id=execution_owner_update_id,
                operation_id=operation_id,
                generation=generation,
            )
        )
        if critical_failure is not None:
            ability, reason, attempt_generation = critical_failure
            candidates.append(
                (
                    "critical_ability_failure",
                    (
                        "critical_ability_failure:"
                        f"{ability}:{attempt_generation}:{reason}"
                    ),
                    f"{ability} failed: {reason}",
                    {
                        **common,
                        "update_id": execution_owner_update_id,
                        "ability": ability,
                        "attempt_generation": attempt_generation,
                        "blocker": reason,
                    },
                )
            )
        previous_minimum = max(
            0,
            previous_required_count or required_count,
        )
        current_transferred_out_count = max(
            0,
            _int_or_none(edit.get("transferred_out_count")) or 0,
        )
        transferred_out_delta = max(
            0,
            current_transferred_out_count
            - max(0, previous_transferred_out_count),
        )
        ownership_integrity = str(
            ownership.get("integrity_status", "") or ""
        ).lower()
        completion_state = str(
            completion.get("state", "") or ""
        ).lower()
        lifetime_state = str(
            lifetime.get("completion_state", "") or ""
        ).lower()
        nonterminal = (
            completion_state
            not in {
                "completed",
                "failed",
                "cancelled",
                "expired",
                "superseded",
            }
            and lifetime_state
            not in {
                "completed",
                "failed",
                "cancelled",
                "expired",
                "superseded",
            }
        )
        raw_owner_loss = (
            max(0, previous_owner_count - owner_count)
            if previous_owner_count is not None
            else 0
        )
        net_owner_loss = max(
            0,
            raw_owner_loss - transferred_out_delta,
        )
        if (
            projection_matches_operation
            and previous_owner_count is not None
            and previous_minimum > 0
            and previous_owner_count >= previous_minimum
            and owner_count < required_count
            and net_owner_loss > 0
            and ownership_integrity == "valid"
            and nonterminal
        ):
            candidates.append(
                (
                    "force_loss",
                    (
                        f"force_loss:{frame}:{previous_owner_count}:"
                        f"{owner_count}:{required_count}"
                    ),
                    (
                        f"Operation force fell from {previous_owner_count} "
                        f"to {owner_count}; minimum is {required_count}."
                    ),
                    {
                        **common,
                        "previous_owner_count": previous_owner_count,
                        "transferred_out_delta": transferred_out_delta,
                        "net_owner_loss": net_owner_loss,
                    },
                )
            )
        if (
            canonical_completion_identity
            and completion.get("movement_observed") is True
        ):
            candidates.append(
                (
                    "movement_observed",
                    "movement_observed",
                    "Operation movement observed.",
                    {**common, "completion": dict(completion)},
                )
            )
        if (
            canonical_completion_identity
            and completion.get("engagement_observed") is True
        ):
            candidates.append(
                (
                    "engagement_observed",
                    "engagement_observed",
                    "Operation engagement observed.",
                    {**common, "completion": dict(completion)},
                )
            )
        if (
            canonical_completion_identity
            and completion.get("target_reached") is True
        ):
            candidates.append(
                (
                    "target_reached",
                    "target_reached",
                    "Operation target reached.",
                    {**common, "completion": dict(completion)},
                )
            )
        completion_state = str(
            completion.get("state", "") or ""
        ).lower()
        lifetime_state = str(
            lifetime.get("completion_state", "") or ""
        ).lower()
        if completion_state in {"success", "succeeded"}:
            completion_state = "completed"
        if lifetime_state in {"success", "succeeded"}:
            lifetime_state = "completed"
        authoritative_completed = bool(
            canonical_completion_identity
            and completion.get("terminal") is True
            and lifetime.get("completed") is True
            and completion_state == "completed"
            and lifetime_state in {"", "completed"}
        )
        if authoritative_completed:
            candidates.append(
                (
                    "completed",
                    "completed",
                    str(
                        completion.get("reason")
                        or lifetime.get("completion_reason")
                        or "Operation completed."
                    ),
                    {**common, "completion": dict(completion)},
                )
            )
        if disposition in {"blocked", "expired", "superseded"}:
            candidates.append(
                (
                    "blocked",
                    f"blocked:{disposition}:{blocker}",
                    blocker or f"Operation {disposition}.",
                    {**common, "blocker": blocker},
                )
            )
        edit_action = str(edit.get("action", "") or "")
        edit_resolution = str(edit.get("resolution", "") or "").lower()
        edit_blocker = str(edit.get("blocker", "") or "")
        edit_identity = (
            f"{requested_generation}:{update_id}:{edit_action}"
        )
        if allow_edit_events and edit_action and (
            edit_resolution in {"blocked", "rejected"}
            or bool(edit_blocker)
        ):
            candidates.append(
                (
                    "edit_rejected",
                    f"edit_rejected:{edit_identity}:{edit_blocker}",
                    edit_blocker or f"{edit_action} edit rejected.",
                    {**common, "operation_edit": dict(edit)},
                )
            )
        elif allow_edit_events and edit_action and edit_resolution in {
            "applied",
            "accepted",
            "resolved",
            "transferred",
        }:
            candidates.append(
                (
                    "edit_applied",
                    f"edit_applied:{edit_identity}:{edit_resolution}",
                    f"{edit_action} edit applied.",
                    {**common, "operation_edit": dict(edit)},
                )
            )
        transferred_in = max(
            0,
            _int_or_none(edit.get("transferred_in_count")) or 0,
        )
        transferred_out = max(
            0,
            _int_or_none(edit.get("transferred_out_count")) or 0,
        )
        if allow_edit_events and transferred_in:
            candidates.append(
                (
                    "ownership_transferred",
                    f"ownership_transferred:{edit_identity}:{transferred_in}",
                    f"{transferred_in} units transferred into the operation.",
                    {**common, "operation_edit": dict(edit)},
                )
            )
        if allow_edit_events and transferred_out:
            candidates.append(
                (
                    "ownership_released",
                    f"ownership_released:{edit_identity}:{transferred_out}",
                    f"{transferred_out} units released from the operation.",
                    {**common, "operation_edit": dict(edit)},
                )
            )
        return candidates

    def observe(
        self,
        payload: Mapping[str, object],
        *,
        blackboard_scope_id: str,
    ) -> dict[str, object]:
        result = dict(payload)
        raw_operations = payload.get("operations")
        operations = (
            [dict(item) for item in raw_operations if isinstance(item, Mapping)]
            if isinstance(raw_operations, Sequence)
            and not isinstance(raw_operations, (str, bytes, bytearray))
            else []
        )
        scope_id = str(blackboard_scope_id or "")
        battlefield_overview = payload.get("battlefield_overview")
        battlefield_identity = (
            battlefield_overview.get("identity")
            if isinstance(battlefield_overview, Mapping)
            else None
        )
        if not isinstance(battlefield_identity, Mapping):
            projection_identity = payload.get(
                "battlefield_projection_identity"
            )
            battlefield_identity = (
                projection_identity
                if isinstance(projection_identity, Mapping)
                else None
            )
        incoming_epoch = str(
            battlefield_identity.get("session_epoch", "")
            if isinstance(battlefield_identity, Mapping)
            else ""
        )
        epoch_authoritative = (
            payload.get("operation_registry_authoritative") is not False
        )
        with self._lock:
            active_epoch = self._scope_epochs.get(scope_id, "")
            current_epoch = (
                active_epoch
                or self._scope_epoch_history.get(scope_id, "")
            )
            if not epoch_authoritative and current_epoch:
                return self._restore_accepted_snapshot(
                    result,
                    scope_id=scope_id,
                    session_epoch=current_epoch,
                )
            if not epoch_authoritative:
                for operation in operations:
                    operation["semantic_timeline"] = []
                result["operations"] = operations
                result["operation_summary"] = (
                    _micromachine_operation_summary(operations)
                )
                result["operation_events"] = []
                result["operation_event_latest_seq"] = 0
                return result
            self._touch_scope(scope_id)
            if (
                incoming_epoch
                and current_epoch
                and incoming_epoch != current_epoch
                and self._incoming_epoch_is_stale(
                    scope_id,
                    current_epoch,
                    incoming_epoch,
                )
            ):
                return self._restore_accepted_snapshot(
                    result,
                    scope_id=scope_id,
                    session_epoch=current_epoch,
                )
            session_epoch = incoming_epoch or current_epoch
            if not self._snapshot_operations_are_monotonic(
                operations,
                scope_id=scope_id,
                session_epoch=session_epoch,
            ):
                return self._restore_accepted_snapshot(
                    result,
                    scope_id=scope_id,
                    session_epoch=current_epoch or session_epoch,
                )
            if incoming_epoch and incoming_epoch != current_epoch:
                self._reset_scope_epoch(scope_id, incoming_epoch)
                session_epoch = incoming_epoch
            elif session_epoch and not active_epoch:
                self._scope_epochs[scope_id] = session_epoch
                self._remember_scope_epoch(scope_id, session_epoch)
                self._scope_events[scope_id] = deque(
                    maxlen=self._PER_SCOPE_RETENTION
                )
                self._scope_families[scope_id] = deque()
            scope_events = self._scope_events.setdefault(
                scope_id,
                deque(maxlen=self._PER_SCOPE_RETENTION),
            )
            base_threats = self._base_threats(
                battlefield_overview
                if isinstance(battlefield_overview, Mapping)
                else None
            )
            accepted_operations: list[dict[str, object]] = []
            incoming_family_keys: set[tuple[str, str, str]] = set()
            for operation in operations:
                operation_id = str(
                    operation.get("operation_id", "") or ""
                ).strip()
                generation = max(
                    0,
                    _int_or_none(operation.get("operation_generation")) or 0,
                )
                if not operation_id or generation <= 0:
                    operation["semantic_timeline"] = []
                    accepted_operations.append(operation)
                    continue
                family_key = (scope_id, session_epoch, operation_id)
                incoming_family_keys.add(family_key)
                high_water = self._generation_high_water.get(family_key, 0)
                if (
                    family_key in self._retired_operation_identities
                    and generation <= high_water
                ):
                    continue
                requested_generation = max(
                    generation,
                    _int_or_none(
                        operation.get("requested_operation_generation")
                    )
                    or generation,
                )
                requested_high_water = (
                    self._requested_generation_high_water.get(
                        family_key,
                        0,
                    )
                )
                stale_requested_generation = bool(
                    requested_generation > 0
                    and requested_generation < requested_high_water
                )
                accepted = self._accepted_operations.get(family_key)
                execution_owner_update = (
                    self._same_generation_execution_owner_update(
                        operation,
                        accepted,
                        generation=generation,
                        generation_high_water=high_water,
                        requested_generation=requested_generation,
                        requested_generation_high_water=(
                            requested_high_water
                        ),
                    )
                )
                if self._same_generation_update_conflicts(
                    operation,
                    accepted,
                    generation=generation,
                    generation_high_water=high_water,
                    requested_generation=requested_generation,
                    requested_generation_high_water=requested_high_water,
                ):
                    if accepted is not None:
                        accepted_operations.append(deepcopy(accepted))
                    continue
                if (
                    stale_requested_generation
                    and generation <= high_water
                    and not execution_owner_update
                ):
                    if accepted is not None:
                        accepted_operations.append(deepcopy(accepted))
                    continue
                if (
                    stale_requested_generation
                    and (
                        generation > high_water
                        or execution_owner_update
                    )
                    and accepted is not None
                ):
                    operation = self._preserve_latest_requested_intent(
                        operation,
                        accepted,
                        requested_high_water,
                    )
                key = (
                    scope_id,
                    session_epoch,
                    operation_id,
                    generation,
                )
                if generation < high_water:
                    if accepted is not None:
                        accepted_operations.append(deepcopy(accepted))
                    continue
                battlefield_operation = operation.get(
                    "battlefield_operation"
                )
                battlefield_operation = (
                    dict(battlefield_operation)
                    if isinstance(battlefield_operation, Mapping)
                    else {}
                )
                projection_matches_operation = (
                    self._projection_matches_operation(
                        operation,
                        battlefield_operation,
                    )
                )
                projection_advances_monotonic_state = bool(
                    not battlefield_operation
                    or projection_matches_operation
                )
                frame = self._operation_frame(
                    operation,
                    battlefield_operation,
                )
                fingerprint = self._semantic_fingerprint(
                    operation,
                    battlefield_operation,
                )
                family_last_frame = self._family_last_frame.get(
                    family_key,
                    -1,
                )
                state = self._states.get(key)
                last_frame = (
                    int(state["last_frame"])
                    if state is not None
                    else -1
                )
                regressing = (
                    projection_advances_monotonic_state
                    and family_last_frame >= 0
                    and (frame < 0 or frame < family_last_frame)
                )
                conflicting_same_frame = bool(
                    projection_advances_monotonic_state
                    and generation == high_water
                    and state is not None
                    and frame >= 0
                    and frame == last_frame
                    and state["last_fingerprint"]
                    and fingerprint != state["last_fingerprint"]
                )
                if regressing or conflicting_same_frame:
                    if accepted is not None:
                        accepted_operations.append(deepcopy(accepted))
                    continue
                if generation > high_water:
                    self._generation_high_water[family_key] = generation
                    self._states = {
                        state_key: value
                        for state_key, value in self._states.items()
                        if state_key[:3] != family_key
                    }
                    state = None
                self._touch_family(family_key)
                state = self._states.setdefault(
                    key,
                    {
                        "last_frame": -1,
                        "last_fingerprint": "",
                        "tokens": deque(
                            maxlen=self._PER_OPERATION_TOKEN_RETENTION
                        ),
                        "milestones": set(),
                        "events": deque(
                            maxlen=self._PER_OPERATION_RETENTION
                        ),
                        "last_owner_count": None,
                        "last_required_count": 0,
                        "last_transferred_out_count": 0,
                        "emergency_retreat_active": False,
                        "base_attack_active": False,
                    },
                )
                if requested_generation > requested_high_water:
                    self._requested_generation_high_water[family_key] = (
                        requested_generation
                    )
                last_frame = int(state["last_frame"])
                if projection_advances_monotonic_state and frame >= 0:
                    state["last_frame"] = max(last_frame, frame)
                    state["last_fingerprint"] = fingerprint
                    self._family_last_frame[family_key] = max(
                        family_last_frame,
                        frame,
                    )
                for kind, token, summary, technical in (
                    self._event_candidates(
                        operation,
                        battlefield_operation,
                        allow_edit_events=(
                            not stale_requested_generation
                        ),
                        previous_owner_count=(
                            state.get("last_owner_count")
                            if type(state.get("last_owner_count")) is int
                            else None
                        ),
                        previous_required_count=max(
                            0,
                            _int_or_none(
                                state.get("last_required_count")
                            )
                            or 0,
                        ),
                        previous_transferred_out_count=max(
                            0,
                            _int_or_none(
                                state.get(
                                    "last_transferred_out_count"
                                )
                            )
                            or 0,
                        ),
                        previous_emergency_retreat_active=bool(
                            state.get("emergency_retreat_active")
                        ),
                        previous_base_attack_active=bool(
                            state.get("base_attack_active")
                        ),
                        base_threats=base_threats,
                        frame=frame,
                    )
                ):
                    if kind in self._PERMANENT_MILESTONE_KINDS:
                        milestones = state["milestones"]
                        if kind in milestones:
                            continue
                        milestones.add(kind)
                    else:
                        tokens = state["tokens"]
                        if token in tokens:
                            continue
                        tokens.append(token)
                    self._seq += 1
                    event = {
                        "timeline_seq": self._seq,
                        "blackboard_scope_id": scope_id,
                        "session_epoch": session_epoch,
                        "operation_id": operation_id,
                        "generation": generation,
                        "requested_generation": max(
                            generation,
                            _int_or_none(
                                technical.get(
                                    "requested_generation"
                                )
                            )
                            or generation,
                        ),
                        "update_id": str(
                            technical.get("update_id", "") or ""
                        ),
                        "kind": kind,
                        "game_frame": (
                            frame
                            if technical.get(
                                "projection_identity_valid"
                            )
                            is not False
                            else last_frame
                        ),
                        "owner_count": max(
                            0,
                            _int_or_none(
                                technical.get("owner_count")
                            )
                            or 0,
                        ),
                        "required_count": max(
                            0,
                            _int_or_none(
                                technical.get("required_count")
                            )
                            or 0,
                        ),
                        "summary": str(summary or kind),
                        "technical": dict(technical),
                    }
                    state["events"].append(event)
                    scope_events.append(event)
                owner_count, required_count = (
                    self._operation_force_counts(
                        operation,
                        battlefield_operation,
                    )
                )
                if projection_matches_operation:
                    state["last_owner_count"] = owner_count
                    state["last_required_count"] = required_count
                    state["last_transferred_out_count"] = max(
                        0,
                        _int_or_none(
                            _mapping_child(
                                operation,
                                "operation_edit",
                            ).get("transferred_out_count")
                        )
                        or 0,
                    )
                    state["emergency_retreat_active"] = bool(
                        self._emergency_retreat_active(
                            operation,
                            battlefield_operation,
                        )
                    )
                    state["base_attack_active"] = bool(
                        self._base_attack_active(
                            battlefield_operation,
                            base_threats,
                        )
                    )
                operation["semantic_timeline"] = [
                    dict(event) for event in state["events"]
                ]
                accepted = deepcopy(operation)
                self._retired_operation_identities.pop(family_key, None)
                self._accepted_operations[family_key] = accepted
                accepted_operations.append(deepcopy(accepted))
            if payload.get("operation_registry_authoritative") is True:
                for family_key in tuple(self._accepted_operations):
                    if (
                        family_key[0] == scope_id
                        and family_key[1] == session_epoch
                        and family_key not in incoming_family_keys
                    ):
                        self._retire_family(family_key)
            result["operations"] = accepted_operations
            result["operation_summary"] = _micromachine_operation_summary(
                accepted_operations
            )
            if isinstance(battlefield_overview, Mapping):
                accepted_overview = self._overview_for_accepted_operations(
                    battlefield_overview,
                    accepted_operations,
                )
                self._scope_battlefield_overviews[scope_id] = deepcopy(
                    accepted_overview
                )
                result["battlefield_overview"] = accepted_overview
            result["operation_events"] = [
                dict(event) for event in scope_events
            ]
            result["operation_event_latest_seq"] = (
                int(scope_events[-1]["timeline_seq"])
                if scope_events
                else 0
            )
        return result


class _LiveLaunchManager:
    """Start one legacy python-sc2 live process and expose safe metadata."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._status = "idle"
        self._url = ""
        self._error = ""
        self._last_line = ""
        self._redactions: tuple[str, ...] = ()
        self._provider = ""
        self._api_key = ""
        self._model = ""

    def configure(self, provider: str, api_key: str, model: str) -> None:
        """Store process-local launch credentials for an explicit UI start."""

        with self._lock:
            self._provider = provider.strip().lower()
            self._api_key = api_key.strip()
            self._model = model.strip()
            self._redactions = (self._api_key,) if self._api_key else ()
            if self._status == "blocked":
                self._status = "idle"
                self._error = ""

    def start(
        self,
        provider: str = "",
        api_key: str = "",
        model: str = "",
    ) -> dict[str, object]:
        """Start the legacy live demo process once, passing the key only via env."""

        with self._lock:
            if provider or api_key or model:
                self._provider = provider.strip().lower()
                self._api_key = api_key.strip()
                self._model = model.strip()
                self._redactions = (self._api_key,) if self._api_key else ()
            provider = self._provider
            api_key = self._api_key
            model = self._model
            if not provider or not api_key:
                self._status = "blocked"
                self._error = (
                    "Legacy python-sc2 실행에는 먼저 LLM 키 설정이 필요합니다."
                )
                self._last_line = ""
                return self._snapshot_unlocked()
            if self._process is not None and self._process.poll() is None:
                return self._snapshot_unlocked()
            self._status = "starting"
            self._url = ""
            self._error = ""
            self._last_line = ""
            env = os.environ.copy()
            sc2_root = env.get("SC2_ROOT", "").strip()
            if sc2_root:
                env["SC2PATH"] = os.path.abspath(os.path.expanduser(sc2_root))
            else:
                env["SC2PATH"] = env.get("SC2PATH", DEFAULT_SC2_INSTALL_PATH)
            env[_api_key_env_var_for_provider(provider)] = api_key
            argv = [
                sys.executable,
                "-u",
                "-m",
                "starcraft_commander.demo_sc2",
                "--map",
                DEFAULT_LIVE_MAP,
                "--difficulty",
                DEFAULT_LIVE_DIFFICULTY,
                "--gui",
                "0",
                "--llm-provider",
                provider,
                "--llm-model",
                model,
            ]
            try:
                self._process = subprocess.Popen(
                    argv,
                    cwd=os.getcwd(),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as error:
                self._status = "failed"
                self._error = _redact_sensitive_text(
                    error,
                    redactions=self._redactions,
                    normalize_whitespace=True,
                )
                self._process = None
                return self._snapshot_unlocked()
            threading.Thread(
                target=self._read_output,
                name="voiStarcraft2-live-launch-reader",
                daemon=True,
            ).start()
            return self._snapshot_unlocked()

    def snapshot(self) -> dict[str, object]:
        """Return safe live startup metadata without secrets."""

        with self._lock:
            process = self._process
            if process is not None and process.poll() is not None and not self._url:
                self._status = "failed" if process.returncode else "stopped"
                if not self._error:
                    self._error = self._last_line or f"process exited {process.returncode}"
            return _redact_json_ready(
                {
                    "enabled": True,
                    "status": self._status,
                    "url": self._url,
                    "error": self._error,
                    "pid": process.pid if process is not None else None,
                    "last_line": self._last_line,
                },
                redactions=self._redactions,
            )  # type: ignore[return-value]

    def _snapshot_unlocked(self) -> dict[str, object]:
        process = self._process
        return _redact_json_ready(
            {
                "enabled": True,
                "status": self._status,
                "url": self._url,
                "error": self._error,
                "pid": process.pid if process is not None else None,
                "last_line": self._last_line,
            },
            redactions=self._redactions,
        )  # type: ignore[return-value]

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            clean = _redact_sensitive_text(
                line.strip(),
                redactions=self._redactions,
                normalize_whitespace=True,
            )
            if not clean:
                continue
            with self._lock:
                self._last_line = clean
                match = _LOCAL_URL_PATTERN.search(clean)
                if match:
                    self._url = match.group(0)
                    self._status = "ready"
        with self._lock:
            if not self._url and self._process is process:
                self._status = "failed"
                self._error = self._last_line or "live process exited before GUI URL"


@dataclass(frozen=True)
class _MicroMachineTelemetryFileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class _MicroMachineTelemetrySnapshot:
    document: Mapping[str, object] | None = None
    file_identity: _MicroMachineTelemetryFileIdentity | None = None
    frame: int | None = None
    current_for_process: bool = False


@dataclass(frozen=True)
class _MicroMachineValidatedRuntimeSnapshot:
    metadata: Mapping[str, object]
    telemetry_document: Mapping[str, object] | None = None
    telemetry_file_identity: _MicroMachineTelemetryFileIdentity | None = None


def _read_micromachine_telemetry_file(
    path: str,
) -> tuple[object | None, _MicroMachineTelemetryFileIdentity] | None:
    try:
        with open(path, "rb") as handle:
            before = os.fstat(handle.fileno())
            payload = handle.read()
            after = os.fstat(handle.fileno())
    except OSError:
        return None
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_identity != after_identity or after.st_size != len(payload):
        return None
    identity = _MicroMachineTelemetryFileIdentity(
        device=after.st_dev,
        inode=after.st_ino,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        document = None
    return document, identity


class _MicroMachineLaunchManager:
    """Start the patched MicroMachine runtime script and expose cockpit status."""

    def __init__(self, script_path: str = "", cwd: str = "") -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._status = "idle"
        self._error = ""
        self._last_line = ""
        self._blackboard_dir = _default_micromachine_blackboard_dir()
        self._enemy_difficulty = DEFAULT_MICROMACHINE_LIVE_ENEMY_DIFFICULTY
        self._launch_started_at_ns = 0
        self._launch_telemetry_baseline: (
            _MicroMachineTelemetryFileIdentity | None
        ) = None
        self._runtime_instance_id = ""
        self._cwd = cwd.strip() or _REPO_ROOT
        candidate_script = script_path.strip()
        if candidate_script and not os.path.isabs(candidate_script):
            candidate_script = os.path.join(self._cwd, candidate_script)
        self._script_path = candidate_script or os.path.join(
            _REPO_ROOT,
            _MICROMACHINE_SMOKE_SCRIPT_RELATIVE_PATH,
        )

    def start(
        self,
        blackboard_dir: str = "",
        enemy_difficulty: int = DEFAULT_MICROMACHINE_LIVE_ENEMY_DIFFICULTY,
    ) -> dict[str, object]:
        """Launch MicroMachine smoke/live runtime for the selected blackboard."""

        root = _clean_blackboard_dir(blackboard_dir, self._blackboard_dir)
        difficulty = _require_micromachine_enemy_difficulty(enemy_difficulty)
        with self._lock:
            self._refresh_unlocked()
            if self._process is not None and self._process.poll() is None:
                blackboard_changed = (
                    os.path.realpath(root) != os.path.realpath(self._blackboard_dir)
                )
                difficulty_changed = difficulty != self._enemy_difficulty
                if blackboard_changed or difficulty_changed:
                    payload = self._snapshot_unlocked()
                    payload["status"] = "blocked"
                    payload["accepted"] = False
                    payload["requested_blackboard_dir"] = root
                    payload["requested_enemy_difficulty"] = difficulty
                    payload["error"] = (
                        "MicroMachine runtime is already running with "
                        f"blackboard_dir={self._blackboard_dir} and "
                        f"enemy_difficulty={self._enemy_difficulty}."
                    )
                    return payload
                return self._snapshot_unlocked()
            self._blackboard_dir = root
            self._enemy_difficulty = difficulty
            self._status = "starting"
            self._error = ""
            self._last_line = ""
            if not os.path.isfile(self._script_path):
                self._status = "failed"
                self._error = (
                    "MicroMachine launcher script not found: "
                    f"{self._script_path}"
                )
                return self._snapshot_unlocked()
            env = os.environ.copy()
            env["BLACKBOARD_DIR"] = root
            env.setdefault("SC2_ROOT", DEFAULT_SC2_INSTALL_PATH)
            env.setdefault("SMOKE_KEEP_RUNNING_AFTER_PASS", "1")
            env["SMOKE_ENEMY_DIFFICULTY"] = str(difficulty)
            max_attempts = env.get(_MICROMACHINE_UI_SMOKE_MAX_ATTEMPTS_ENV, "1")
            env.setdefault("SMOKE_MAX_ATTEMPTS", max_attempts)
            self._runtime_instance_id = uuid.uuid4().hex
            env["VOI_MICROMACHINE_RUNTIME_INSTANCE_ID"] = (
                self._runtime_instance_id
            )
            argv = [
                "bash",
                self._script_path,
                "--live-hold",
                "--fresh-live-session",
                "--blackboard-dir",
                root,
                "--enemy-difficulty",
                str(difficulty),
                "--max-attempts",
                max_attempts,
            ]
            try:
                telemetry_path = os.path.realpath(
                    os.path.join(root, "latest_telemetry.json")
                )
                baseline = _read_micromachine_telemetry_file(telemetry_path)
                self._launch_telemetry_baseline = (
                    baseline[1] if baseline is not None else None
                )
                self._launch_started_at_ns = time.time_ns()
                self._process = subprocess.Popen(
                    argv,
                    cwd=self._cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as error:
                self._status = "failed"
                self._error = _redact_sensitive_text(
                    error,
                    normalize_whitespace=True,
                )
                self._process = None
                self._launch_started_at_ns = 0
                self._launch_telemetry_baseline = None
                self._runtime_instance_id = ""
                return self._snapshot_unlocked()
            threading.Thread(
                target=self._read_output,
                args=(self._process,),
                name="voiStarcraft2-micromachine-launch-reader",
                daemon=True,
            ).start()
            return self._snapshot_unlocked()

    def snapshot(self, blackboard_dir: str = "") -> dict[str, object]:
        """Return safe MicroMachine runtime metadata and telemetry presence."""

        root = _clean_blackboard_dir(blackboard_dir, self._blackboard_dir)
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._blackboard_dir = root
            telemetry = self._refresh_unlocked()
            return self._snapshot_unlocked(telemetry)

    def validated_snapshot(
        self,
        blackboard_dir: str = "",
    ) -> _MicroMachineValidatedRuntimeSnapshot:
        """Capture metadata and the exact telemetry document validated with it."""

        root = _clean_blackboard_dir(blackboard_dir, self._blackboard_dir)
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._blackboard_dir = root
            telemetry = self._refresh_unlocked()
            telemetry_document = (
                deepcopy(dict(telemetry.document))
                if telemetry.current_for_process
                and isinstance(telemetry.document, Mapping)
                else None
            )
            return _MicroMachineValidatedRuntimeSnapshot(
                metadata=self._snapshot_unlocked(telemetry),
                telemetry_document=telemetry_document,
                telemetry_file_identity=telemetry.file_identity,
            )

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            clean = _redact_sensitive_text(
                line.strip(),
                normalize_whitespace=True,
            )
            if not clean:
                continue
            with self._lock:
                if self._process is not process:
                    continue
                self._last_line = clean
                if self._status == "starting":
                    self._status = "running"
                if "MicroMachine smoke passed" in clean:
                    self._status = "passed"
                elif self._latest_telemetry_unlocked().current_for_process:
                    self._status = "connected"
        process.wait()
        with self._lock:
            if self._process is not process:
                return
            if process.returncode == 0:
                self._status = "passed"
                self._error = ""
            else:
                self._status = "failed"
                self._error = self._last_line or f"process exited {process.returncode}"

    def _refresh_unlocked(self) -> _MicroMachineTelemetrySnapshot:
        process = self._process
        telemetry = self._latest_telemetry_unlocked()
        if process is not None and process.poll() is None:
            if (
                telemetry.current_for_process
                and self._status in {"starting", "running"}
            ):
                self._status = "connected"
            elif (
                not telemetry.current_for_process
                and self._status == "connected"
            ):
                self._status = "running"
            return telemetry
        if process is not None and process.poll() is not None:
            if process.returncode == 0:
                self._status = "passed"
                self._error = ""
            elif self._status not in {"failed", "passed"}:
                self._status = "failed"
                self._error = self._last_line or f"process exited {process.returncode}"
            return _MicroMachineTelemetrySnapshot(
                document=telemetry.document,
                file_identity=telemetry.file_identity,
                frame=telemetry.frame,
            )
        return telemetry

    def _snapshot_unlocked(
        self,
        telemetry: _MicroMachineTelemetrySnapshot | None = None,
    ) -> dict[str, object]:
        process = self._process
        telemetry_snapshot = (
            telemetry
            if telemetry is not None
            else self._latest_telemetry_unlocked()
        )
        runtime_attached = process is not None and process.poll() is None
        telemetry_current_for_process = bool(
            runtime_attached and telemetry_snapshot.current_for_process
        )
        return {
            "enabled": True,
            "mode": COMMAND_MODE_MICROMACHINE,
            "status": self._status,
            "pid": process.pid if runtime_attached else None,
            "runtime_instance_id": (
                self._runtime_instance_id if runtime_attached else ""
            ),
            "runtime_attached": runtime_attached,
            "blackboard_dir": self._blackboard_dir,
            "enemy_difficulty": self._enemy_difficulty,
            "script_path": self._script_path,
            "last_line": self._last_line,
            "error": self._error,
            "telemetry_present": telemetry_snapshot.frame is not None,
            "telemetry_current_for_process": telemetry_current_for_process,
            "telemetry_stale_or_detached": (
                telemetry_snapshot.frame is not None
                and not telemetry_current_for_process
            ),
            "telemetry_frame": telemetry_snapshot.frame,
        }

    def _latest_telemetry_unlocked(self) -> _MicroMachineTelemetrySnapshot:
        path = os.path.join(self._blackboard_dir, "latest_telemetry.json")
        root_real = os.path.realpath(self._blackboard_dir)
        path_real = os.path.realpath(path)
        if not path_real.startswith(root_real + os.sep) or not os.path.isfile(path_real):
            return _MicroMachineTelemetrySnapshot()
        telemetry_file = _read_micromachine_telemetry_file(path_real)
        if telemetry_file is None:
            return _MicroMachineTelemetrySnapshot()
        document, file_identity = telemetry_file
        if not isinstance(document, Mapping):
            return _MicroMachineTelemetrySnapshot(file_identity=file_identity)
        if document.get("protocol_version") != "voi-mm-bridge/v1":
            return _MicroMachineTelemetrySnapshot(
                document=document,
                file_identity=file_identity,
            )
        frame = document.get("frame")
        if type(frame) is not int:
            return _MicroMachineTelemetrySnapshot(
                document=document,
                file_identity=file_identity,
            )
        snapshot = _MicroMachineTelemetrySnapshot(
            document=document,
            file_identity=file_identity,
            frame=frame,
        )
        process = self._process
        if (
            process is not None
            and process.poll() is None
            and self._launch_started_at_ns
        ):
            if file_identity.mtime_ns <= self._launch_started_at_ns:
                return _MicroMachineTelemetrySnapshot()
            if (
                self._launch_telemetry_baseline is not None
                and file_identity == self._launch_telemetry_baseline
            ):
                return _MicroMachineTelemetrySnapshot()
            if (
                document.get("runtime_instance_id")
                != self._runtime_instance_id
            ):
                return snapshot
            age_ns = time.time_ns() - file_identity.mtime_ns
            return _MicroMachineTelemetrySnapshot(
                document=document,
                file_identity=file_identity,
                frame=frame,
                current_for_process=(
                    0 <= age_ns <= _MICROMACHINE_TELEMETRY_FRESHNESS_NS
                ),
            )
        return snapshot


@dataclass(frozen=True)
class _BattlefieldProjectionCursor:
    identity: Mapping[str, object]
    payload_fingerprint: str


class SessionLoopBridge:
    """Default web GUI bridge owning one daemon asyncio loop thread.

    Submitted texts are drained strictly sequentially through the injected
    session's ``process_text`` coroutine, so two browser submissions can never
    interleave half-executed plans. Every resulting outcome — including honest
    blocked/clarification ones — is recorded into the history store; a session
    exception becomes a recorded ``blocked`` outcome instead of a silent drop.
    """

    def __init__(
        self,
        session: object,
        history: object | None = None,
        state_resolver: SC2StateResolverInterface = DEFAULT_SC2_STATE_RESOLVER,
        llm_control: object | None = None,
        micromachine_blackboard_dir: str = "",
    ) -> None:
        if not callable(getattr(session, "process_text", None)):
            raise TypeError("Session loop bridge session must implement process_text().")
        store = history if history is not None else _SimpleHistory()
        for method_name in ("record", "since", "latest_seq"):
            if not callable(getattr(store, method_name, None)):
                raise TypeError(
                    f"Session loop bridge history must implement {method_name}()."
                )
        if not callable(getattr(state_resolver, "resolve", None)):
            raise TypeError("Session loop bridge state_resolver must implement resolve().")
        self._session = session
        self._history = store
        self._state_resolver = state_resolver
        self._llm_control = llm_control
        self._micromachine_blackboard_dir = (
            micromachine_blackboard_dir.strip()
            or _default_micromachine_blackboard_dir()
        )
        self._micromachine_recent_commands: dict[
            str, deque[dict[str, object]]
        ] = {}
        self._micromachine_recent_commands_lock = threading.Lock()
        self._micromachine_battlefield_identity_lock = threading.Lock()
        self._micromachine_battlefield_cursors: dict[
            tuple[str, str], _BattlefieldProjectionCursor
        ] = {}
        self._micromachine_operation_timeline = (
            _OperationSemanticTimelineReducer()
        )
        self._lifecycle_lock = threading.Lock()
        self._lifecycle_state = _BRIDGE_LIFECYCLE_STOPPED
        self._micromachine_request_lock = threading.Lock()
        self._micromachine_requests: dict[
            str,
            _MicroMachineModulationRequest,
        ] = {}
        self._micromachine_emergency_epochs: dict[str, tuple[int, str]] = {}
        self._micromachine_acceptance_ordinals: dict[str, int] = {}
        self._queue_sequence = 0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: "asyncio.PriorityQueue[tuple[int, int, object]]" | None = None
        self._micromachine_normal_executor: (
            concurrent.futures.ThreadPoolExecutor | None
        ) = None
        self._micromachine_emergency_executor: (
            concurrent.futures.ThreadPoolExecutor | None
        ) = None
        self._stopping = threading.Event()
        self._ready = threading.Event()

    @property
    def is_running(self) -> bool:
        """Return whether the worker loop thread is alive and accepting work."""

        with self._lifecycle_lock:
            thread = self._thread
            return (
                self._lifecycle_state == _BRIDGE_LIFECYCLE_RUNNING
                and thread is not None
                and thread.is_alive()
                and self._loop is not None
                and self._queue is not None
            )

    def start(self) -> None:
        """Start the daemon loop thread; idempotent while already running."""

        with self._lifecycle_lock:
            if self._lifecycle_state == _BRIDGE_LIFECYCLE_RUNNING:
                return
            if self._lifecycle_state == _BRIDGE_LIFECYCLE_STOPPING:
                raise RuntimeError(
                    "Session loop bridge is still stopping; wait for the "
                    "previous worker to terminate before restarting."
                )
            if self._lifecycle_state == _BRIDGE_LIFECYCLE_STARTING:
                ready = self._ready
            else:
                self._stopping.clear()
                self._ready.clear()
                self._lifecycle_state = _BRIDGE_LIFECYCLE_STARTING
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name=_BRIDGE_THREAD_NAME,
                    daemon=True,
                )
                ready = self._ready
                try:
                    self._thread.start()
                except Exception:
                    self._thread = None
                    self._lifecycle_state = _BRIDGE_LIFECYCLE_STOPPED
                    self._stopping.set()
                    ready.set()
                    raise
        if not ready.wait(timeout=10.0):
            raise RuntimeError("Session loop bridge event loop failed to start in 10s.")
        with self._lifecycle_lock:
            if self._lifecycle_state == _BRIDGE_LIFECYCLE_RUNNING:
                return
            if self._lifecycle_state == _BRIDGE_LIFECYCLE_STOPPING:
                raise RuntimeError(
                    "Session loop bridge stopped while the worker was starting."
                )
            raise RuntimeError("Session loop bridge event loop failed to start.")

    def stop(self, timeout: float = 10.0) -> None:
        """Drain pending commands, stop the loop, and join the thread."""

        with self._lifecycle_lock:
            thread = self._thread
            if self._lifecycle_state == _BRIDGE_LIFECYCLE_STOPPED or thread is None:
                return
            self._lifecycle_state = _BRIDGE_LIFECYCLE_STOPPING
            self._stopping.set()
            loop = self._loop
            queue = self._queue
            self._terminate_pending_micromachine_requests(
                "Session loop bridge stopped before the MicroMachine request completed."
            )
            if thread.is_alive() and loop is not None and queue is not None:
                try:
                    self._enqueue_bridge_item(
                        loop,
                        queue,
                        _STOP_SENTINEL,
                        priority=_BRIDGE_QUEUE_PRIORITY_STOP,
                    )
                except RuntimeError:
                    # The loop already closed on its own; just join below.
                    pass
        if thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lifecycle_lock:
            if not thread.is_alive() and self._thread is thread:
                self._thread = None
                self._loop = None
                self._queue = None
                self._lifecycle_state = _BRIDGE_LIFECYCLE_STOPPED
                self._stopping.set()
                self._ready.set()

    def submit_command(self, text: str) -> None:
        """Enqueue one utterance for sequential processing (non-blocking)."""

        cleaned = self._validate_command_text(text)
        self._accept_bridge_item(
            cleaned,
            priority=_BRIDGE_QUEUE_PRIORITY_NORMAL,
        )

    def submit_correlated_command(self, text: str, request_id: str) -> None:
        """Enqueue one utterance with an exact browser request identity."""

        cleaned = self._validate_command_text(text)
        normalized_request_id = _normalize_web_request_id(request_id)
        if not normalized_request_id:
            raise ValueError("Web GUI request_id must be non-empty.")
        self._accept_bridge_item(
            _CorrelatedWebCommand(
                text=cleaned,
                request_id=normalized_request_id,
            ),
            priority=_BRIDGE_QUEUE_PRIORITY_NORMAL,
        )

    @staticmethod
    def _validate_command_text(text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("Web GUI command text must be a string.")
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Web GUI command text must be non-empty.")
        return cleaned

    def _accept_bridge_item(self, item: object, *, priority: int) -> None:
        """Atomically validate RUNNING state and schedule one accepted item."""

        with self._lifecycle_lock:
            if self._lifecycle_state != _BRIDGE_LIFECYCLE_RUNNING:
                raise RuntimeError(
                    "Session loop bridge is not running; call start() first."
                )
            loop = self._loop
            queue = self._queue
            if loop is None or queue is None:
                raise RuntimeError(
                    "Session loop bridge is not running; call start() first."
                )
            self._enqueue_bridge_item(loop, queue, item, priority=priority)

    def _enqueue_bridge_item(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: "asyncio.PriorityQueue[tuple[int, int, object]]",
        item: object,
        *,
        priority: int,
    ) -> None:
        self._queue_sequence += 1
        sequence = self._queue_sequence
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (priority, sequence, item),
        )

    def _register_micromachine_request(
        self,
        request: _MicroMachineModulationRequest,
    ) -> None:
        update_id = request.update_id or ""
        with self._micromachine_request_lock:
            if update_id in self._micromachine_requests:
                raise ValueError(
                    f"MicroMachine update_id is already queued: {update_id}."
                )
            request_blackboard = os.path.realpath(request.blackboard_dir)
            request.accepted_at_unix_ns = time.time_ns()
            request.acceptance_ordinal = (
                self._micromachine_acceptance_ordinals.get(request_blackboard, 0)
                + 1
            )
            self._micromachine_acceptance_ordinals[request_blackboard] = (
                request.acceptance_ordinal
            )
            request.emergency_epoch = self._micromachine_emergency_epochs.get(
                request_blackboard,
                (0, ""),
            )[0]
            if request.emergency:
                for pending_id, pending in tuple(
                    self._micromachine_requests.items()
                ):
                    if (
                        pending_id == update_id
                        or pending.publish_committed
                        or os.path.realpath(pending.blackboard_dir)
                        != request_blackboard
                    ):
                        continue
                    pending.cancel_event.set()
                    if not pending.future.done():
                        pending.future.set_exception(
                            _MicroMachineRequestSupersededError(
                                pending_id,
                                update_id,
                            )
                        )
            self._micromachine_requests[update_id] = request

    def _accept_micromachine_request(
        self,
        request: _MicroMachineModulationRequest,
    ) -> None:
        """Register and enqueue a request under one lifecycle decision."""

        with self._lifecycle_lock:
            if self._lifecycle_state != _BRIDGE_LIFECYCLE_RUNNING:
                raise RuntimeError(
                    "Session loop bridge is not running; call start() first."
                )
            loop = self._loop
            queue = self._queue
            if loop is None or queue is None:
                raise RuntimeError(
                    "Session loop bridge is not running; call start() first."
                )
            self._register_micromachine_request(request)
            try:
                self._enqueue_bridge_item(
                    loop,
                    queue,
                    request,
                    priority=(
                        _BRIDGE_QUEUE_PRIORITY_EMERGENCY
                        if request.emergency
                        else _BRIDGE_QUEUE_PRIORITY_NORMAL
                    ),
                )
            except Exception as error:
                if not request.future.done():
                    request.future.set_exception(error)
                self._forget_micromachine_request(request)
                raise

    def _forget_micromachine_request(
        self,
        request: _MicroMachineModulationRequest,
    ) -> None:
        update_id = request.update_id or ""
        with self._micromachine_request_lock:
            if self._micromachine_requests.get(update_id) is request:
                del self._micromachine_requests[update_id]

    def _terminate_pending_micromachine_requests(self, reason: str) -> None:
        """Give every non-committed request a terminal future during shutdown."""

        with self._micromachine_request_lock:
            for request in self._micromachine_requests.values():
                request.cancel_event.set()
                if not request.publish_committed and not request.future.done():
                    request.future.set_exception(RuntimeError(reason))

    def state_snapshot(self) -> Mapping[str, object] | None:
        """Resolve the session's bound bot into a JSON-ready state snapshot.

        Returns ``None`` when no runtime is bound (no executor, or an executor
        without a bot). Mirrors the live pipeline's adapter unwrap: when the
        executor's runtime wraps the actual game bot via a ``bot`` attribute
        (``PythonSC2BotAdapter``), the inner game bot is observed.
        """

        executor = getattr(self._session, "executor", None)
        runtime = getattr(executor, "bot", None)
        if runtime is None:
            return None
        inner_bot = getattr(runtime, "bot", None)
        game_bot = inner_bot if inner_bot is not None else runtime
        state = self._state_resolver.resolve(game_bot)
        to_dict = getattr(state, "to_dict", None)
        if callable(to_dict):
            snapshot = dict(to_dict())
            _attach_standing_order_snapshot(snapshot, self._session)
            _attach_briefing_context_snapshot(snapshot, self._session)
            return snapshot
        if isinstance(state, Mapping):
            snapshot = dict(state)
            _attach_standing_order_snapshot(snapshot, self._session)
            _attach_briefing_context_snapshot(snapshot, self._session)
            return snapshot
        return None

    def history_since(self, seq: int) -> tuple[dict[str, object], ...]:
        """Return JSON-ready outcome events recorded after sequence ``seq``."""

        entries = self._history.since(int(seq))
        return tuple(_as_event_mapping(entry) for entry in entries)

    def latest_seq(self) -> int:
        """Return the history store's highest sequence number."""

        return int(self._history.latest_seq())

    def llm_settings_snapshot(self) -> Mapping[str, object]:
        control = self._llm_control
        snapshot = getattr(control, "snapshot", None)
        if callable(snapshot):
            return dict(snapshot())
        return {"provider": "", "model": "", "configured": False, "key_present": False}

    def micromachine_blackboard_dir(self) -> str:
        return self._micromachine_blackboard_dir

    def configure_llm(self, provider: str, api_key: str, model: str = "") -> Mapping[str, object]:
        control = self._llm_control
        configure = getattr(control, "configure", None)
        if not callable(configure):
            raise RuntimeError("이 세션은 웹 LLM 키 설정을 지원하지 않습니다.")
        return dict(configure(provider, api_key, model))

    def submit_micromachine_modulation(
        self,
        text: str,
        *,
        blackboard_dir: str = "",
        provider_output: Mapping[str, object] | None = None,
        allow_smoke_keyword_provider: bool = False,
        semantic_scope: Mapping[str, object] | None = None,
        commander_context: Mapping[str, object] | None = None,
        ttl_seconds: int | None = None,
        current_frame: int | None = None,
        update_id: str | None = None,
    ) -> Mapping[str, object]:
        if not isinstance(text, str):
            raise TypeError("MicroMachine command text must be a string.")
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("MicroMachine command text must be non-empty.")
        root = _clean_blackboard_dir(blackboard_dir, self._micromachine_blackboard_dir)
        resolved_update_id = update_id or _new_micromachine_update_id()
        future: concurrent.futures.Future[Mapping[str, object]] = (
            concurrent.futures.Future()
        )
        request = _MicroMachineModulationRequest(
            text=cleaned,
            blackboard_dir=root,
            provider_output=provider_output,
            allow_smoke_keyword_provider=allow_smoke_keyword_provider,
            semantic_scope=semantic_scope,
            commander_context=dict(commander_context or {}),
            ttl_seconds=ttl_seconds,
            current_frame=current_frame,
            update_id=resolved_update_id,
            future=future,
            cancel_event=threading.Event(),
            deadline_monotonic=(
                time.monotonic() + _MICROMACHINE_SYNC_PUBLISH_DEADLINE_SECONDS
            ),
            emergency=_micromachine_request_is_emergency(
                cleaned,
                provider_output,
            ),
        )
        self._accept_micromachine_request(request)
        try:
            return future.result(timeout=_MICROMACHINE_REQUEST_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            with self._micromachine_request_lock:
                publish_committed = request.publish_committed
                if not publish_committed:
                    request.cancel_event.set()
                    future.cancel()
            if publish_committed:
                return future.result()
            raise

    def submit_micromachine_modulation_background(
        self,
        text: str,
        *,
        blackboard_dir: str = "",
        provider_output: Mapping[str, object] | None = None,
        allow_smoke_keyword_provider: bool = False,
        semantic_scope: Mapping[str, object] | None = None,
        commander_context: Mapping[str, object] | None = None,
        ttl_seconds: int | None = None,
        current_frame: int | None = None,
        update_id: str | None = None,
    ) -> Mapping[str, object]:
        """Queue one MicroMachine update and return immediately for chat UX."""

        if not isinstance(text, str):
            raise TypeError("MicroMachine command text must be a string.")
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("MicroMachine command text must be non-empty.")
        root = _clean_blackboard_dir(blackboard_dir, self._micromachine_blackboard_dir)
        resolved_update_id = update_id or _new_micromachine_update_id()
        future: concurrent.futures.Future[Mapping[str, object]] = (
            concurrent.futures.Future()
        )
        request = _MicroMachineModulationRequest(
            text=cleaned,
            blackboard_dir=root,
            provider_output=provider_output,
            allow_smoke_keyword_provider=allow_smoke_keyword_provider,
            semantic_scope=semantic_scope,
            commander_context=dict(commander_context or {}),
            ttl_seconds=ttl_seconds,
            current_frame=current_frame,
            update_id=resolved_update_id,
            future=future,
            cancel_event=threading.Event(),
            emergency=_micromachine_request_is_emergency(
                cleaned,
                provider_output,
            ),
        )

        def observe_background_result(
            done: concurrent.futures.Future[Mapping[str, object]],
        ) -> None:
            try:
                done.result()
            except Exception as error:  # noqa: BLE001 - persist async failures for UI polling.
                # A post-commit warning must never manufacture a failed publish.
                if request.publish_committed:
                    return
                superseded = isinstance(
                    error,
                    _MicroMachineRequestSupersededError,
                )
                superseded_by_update_id = (
                    error.replacement_update_id
                    if isinstance(error, _MicroMachineRequestSupersededError)
                    else ""
                )
                compile_result = {
                    "status": "refused",
                    "source": "system",
                    "failure_kind": (
                        "superseded" if superseded else "publish_failed"
                    ),
                    "refusal_reason": str(error),
                    "update_id": resolved_update_id,
                }
                result = {
                    "ok": False,
                    "status": "superseded" if superseded else "publish_failed",
                    "command_text": cleaned,
                    "compile_result": compile_result,
                    "update": None,
                    "command_queue": {
                        "active_command_id": resolved_update_id,
                        "update_id": resolved_update_id,
                        "action": (
                            "superseded_by_emergency"
                            if superseded
                            else "publish_failed"
                        ),
                        "superseded_previous": False,
                        "superseded_by_update_id": superseded_by_update_id,
                    },
                    "consumption_status": "not_published",
                }
                compile_document = {
                    "command_text": cleaned,
                    "status": result["status"],
                    "current_frame": current_frame,
                    "compile_result": compile_result,
                    "update_id": resolved_update_id,
                    "command_queue": result["command_queue"],
                    "duration_ms": 0,
                    "result": result,
                    "accepted_at_unix_ns": request.accepted_at_unix_ns,
                    "acceptance_ordinal": request.acceptance_ordinal,
                    "written_at_unix": time.time(),
                }
                _write_micromachine_compile_result(
                    root,
                    _redact_json_ready(compile_document),
                )

        future.add_done_callback(observe_background_result)
        self._accept_micromachine_request(request)
        metadata = _micromachine_compile_result_metadata(root, resolved_update_id)
        return {
            "accepted": True,
            "ok": True,
            "queued": True,
            "async_publish": True,
            "status": "queued",
            "command_text": cleaned,
            "update_id": resolved_update_id,
            "blackboard_dir": root,
            **metadata,
            "consumption_status": "pending_compile",
            "message": (
                "MicroMachine publish를 백그라운드에서 시작했습니다. "
                "LLM DSL 컴파일과 publish 결과는 status polling으로 갱신됩니다."
            ),
        }

    def _publish_micromachine_modulation(
        self,
        text: str,
        *,
        blackboard_dir: str = "",
        provider_output: Mapping[str, object] | None = None,
        allow_smoke_keyword_provider: bool = False,
        semantic_scope: Mapping[str, object] | None = None,
        commander_context: Mapping[str, object] | None = None,
        ttl_seconds: int | None = None,
        current_frame: int | None = None,
        update_id: str | None = None,
        request: _MicroMachineModulationRequest | None = None,
    ) -> Mapping[str, object]:
        from starcraft_commander.micromachine_live_session import (
            KeywordPolicyModulationProvider,
            MicroMachineLiveTextSession,
            StaticJsonPolicyModulationProvider,
        )
        from starcraft_commander.micromachine_runtime import (
            MicroMachineFilesystemBlackboard,
        )

        root = _clean_blackboard_dir(blackboard_dir, self._micromachine_blackboard_dir)
        if provider_output is not None:
            provider = StaticJsonPolicyModulationProvider(
                provider_output,
                source=PolicyModulationSource.UI,
                force_source=True,
            )
        elif request is not None and request.emergency:
            provider = StaticJsonPolicyModulationProvider(
                _micromachine_emergency_safety_output(text),
                source=PolicyModulationSource.UI,
                force_source=True,
            )
        elif allow_smoke_keyword_provider:
            provider = KeywordPolicyModulationProvider()
        else:
            recent_commands = (
                commander_context.get("recent_commands")
                if isinstance(commander_context, Mapping)
                else None
            )
            provider = _LocalLLMPolicyModulationProvider(
                self._llm_control,
                recent_commands=(
                    recent_commands
                    if isinstance(recent_commands, Sequence)
                    and not isinstance(recent_commands, (str, bytes, bytearray))
                    else ()
                ),
            )
        if semantic_scope or ttl_seconds is not None:
            provider = _SemanticScopePolicyModulationProvider(
                provider,
                semantic_scope=semantic_scope,
                ttl_seconds=ttl_seconds,
            )
        started_at = time.monotonic()
        backend: object = MicroMachineFilesystemBlackboard(root)
        if request is not None:
            backend = _GuardedMicroMachineBackend(
                backend,
                request,
                self._micromachine_request_lock,
                self._micromachine_emergency_epochs,
            )
        result = MicroMachineLiveTextSession(
            backend,
            provider,
        ).submit_text(
            text,
            current_frame=current_frame,
            update_id=update_id,
            commander_context=commander_context,
            tags=("web_gui",),
        )
        payload = result.to_dict()
        duration_ms = int((time.monotonic() - started_at) * 1000)
        payload["duration_ms"] = duration_ms
        payload["blackboard_dir"] = root
        update_for_compile = payload.get("update")
        compile_update_id = (
            str(update_for_compile.get("update_id", "") or "")
            if isinstance(update_for_compile, Mapping)
            else (update_id or "")
        )
        result_metadata = _micromachine_compile_result_metadata(
            root,
            compile_update_id,
        )
        payload.update(result_metadata)
        compile_result_for_document = payload.get("compile_result")
        if isinstance(compile_result_for_document, Mapping) and compile_update_id:
            compile_result_for_document = dict(compile_result_for_document)
            compile_result_for_document.setdefault("update_id", compile_update_id)
            compile_result_for_document.update(result_metadata)
            payload["compile_result"] = compile_result_for_document
        dashboard = payload.get("dashboard", {})
        telemetry = dashboard.get("telemetry") if isinstance(dashboard, Mapping) else None
        update = payload.get("update")
        update_id_for_logs = str(update.get("update_id", "") or "") if isinstance(update, Mapping) else ""
        payload["intervention"] = _micromachine_intervention_summary(
            update if isinstance(update, Mapping) else None,
            telemetry,
            consumption_status=str(payload.get("consumption_status", "") or ""),
            compile_result=payload.get("compile_result"),
            log_snippets=_micromachine_recent_tactical_log_snippets(
                root,
                update_id=update_id_for_logs,
            ),
        )
        if isinstance(payload.get("intervention"), dict):
            payload["intervention"]["command_queue"] = dict(
                payload.get("command_queue")
                if isinstance(payload.get("command_queue"), Mapping)
                else {}
            )
        result_snapshot = {
            key: payload.get(key)
            for key in (
                "ok",
                "command_text",
                "status",
                "provider_source",
                "current_frame",
                "compile_result",
                "update",
                "consumption_status",
                "consumed",
                "command_queue",
                "intervention",
                "blackboard_scope_id",
                "result_id",
            )
        }
        compile_document: dict[str, object] = {
            "command_text": text,
            "status": str(payload.get("status", "") or ""),
            "current_frame": payload.get("current_frame"),
            "compile_result": compile_result_for_document,
            "update_id": compile_update_id,
            "command_queue": payload.get("command_queue"),
            "duration_ms": duration_ms,
            "result": result_snapshot,
            "accepted_at_unix_ns": (
                request.accepted_at_unix_ns if request is not None else time.time_ns()
            ),
            "acceptance_ordinal": (
                request.acceptance_ordinal if request is not None else 0
            ),
            "written_at_unix": time.time(),
        }
        compile_document.update(result_metadata)
        persistence_warnings = _write_micromachine_compile_result(
            root,
            _redact_json_ready(compile_document),
        )
        if persistence_warnings:
            payload["persistence_warnings"] = list(persistence_warnings)
        return payload

    def micromachine_status(self, *, blackboard_dir: str = "") -> Mapping[str, object]:
        return self._micromachine_status(
            blackboard_dir=blackboard_dir,
            runtime_instance_id="",
        )

    def micromachine_status_detached(
        self,
        *,
        blackboard_dir: str = "",
    ) -> Mapping[str, object]:
        """Build detached status without granting telemetry epoch authority."""

        return self._micromachine_status(
            blackboard_dir=blackboard_dir,
            runtime_instance_id="",
            operation_registry_authoritative_override=False,
        )

    def micromachine_status_for_runtime(
        self,
        *,
        blackboard_dir: str = "",
        runtime_instance_id: str,
        telemetry_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Build status from the exact telemetry document validated by launcher."""

        instance_id = str(runtime_instance_id or "").strip()
        if not instance_id:
            raise ValueError("runtime_instance_id must not be empty.")
        if not isinstance(telemetry_document, Mapping):
            raise ValueError("telemetry_document must be a mapping.")
        telemetry = MicroMachineTelemetry.from_mapping(
            deepcopy(dict(telemetry_document))
        )
        if telemetry.runtime_instance_id != instance_id:
            raise ValueError(
                "telemetry_document runtime_instance_id does not match "
                "the attached runtime."
            )
        return self._micromachine_status(
            blackboard_dir=blackboard_dir,
            runtime_instance_id=instance_id,
            validated_runtime_telemetry=telemetry,
        )

    def _micromachine_status(
        self,
        *,
        blackboard_dir: str,
        runtime_instance_id: str,
        validated_runtime_telemetry: MicroMachineTelemetry | None = None,
        operation_registry_authoritative_override: bool | None = None,
    ) -> Mapping[str, object]:
        from starcraft_commander.micromachine_runtime import (
            MicroMachineFilesystemBlackboard,
        )
        from starcraft_commander.policy_observability import (
            PolicyModulationBridgeStatus,
            build_policy_modulation_dashboard_snapshot,
        )

        root = _clean_blackboard_dir(blackboard_dir, self._micromachine_blackboard_dir)
        backend = MicroMachineFilesystemBlackboard(root)
        telemetry = (
            validated_runtime_telemetry
            if runtime_instance_id
            else backend.read_latest_telemetry()
        )
        telemetry_archive = backend.read_recent_telemetry_archive(
            pending_family_effects_only=True,
        )
        if runtime_instance_id:
            if telemetry is None:
                raise ValueError(
                    "validated_runtime_telemetry is required for attached runtime."
                )
            if telemetry.runtime_instance_id != runtime_instance_id:
                raise ValueError(
                    "validated telemetry does not belong to attached runtime."
                )
            telemetry_archive = tuple(
                entry
                for entry in telemetry_archive
                if entry.runtime_instance_id == runtime_instance_id
                and entry.frame <= telemetry.frame
            )
        frame = telemetry.frame if telemetry is not None else 0
        if runtime_instance_id:
            updates = ()
            last_failure = telemetry.last_failure
            try:
                update = backend.read_latest_update(current_frame=frame)
                if update is not None:
                    updates = (update,)
            except (OSError, TypeError, ValueError):
                last_failure = MicroMachineBridgeFailureMode.INVALID_PAYLOAD
            snapshot = build_policy_modulation_dashboard_snapshot(
                updates,
                current_frame=frame,
                bridge_status=PolicyModulationBridgeStatus.CONNECTED,
                telemetry=telemetry,
                last_failure=last_failure,
            )
        else:
            snapshot = backend.dashboard_snapshot(
                current_frame=frame,
                bridge_status=PolicyModulationBridgeStatus.CONNECTED,
            )
        compile_document = _read_micromachine_compile_result(root)
        compile_result = _latest_compile_result_payload(compile_document)
        compile_history = _read_micromachine_compile_result_history(root)
        compile_result_stream = _micromachine_compile_result_stream(
            compile_history,
            blackboard_dir=root,
        )
        result_metadata = _micromachine_compile_result_metadata(
            root,
            (
                compile_document.get("update_id")
                if isinstance(compile_document, Mapping)
                else ""
            ),
        )
        with self._micromachine_battlefield_identity_lock:
            root_key = os.path.realpath(root)
            identity_key = (root_key, runtime_instance_id)
            previous_cursor = (
                self._micromachine_battlefield_cursors.get(identity_key)
                if runtime_instance_id
                else None
            )
            battlefield_projection = select_latest_battlefield_projection(
                latest_telemetry=(
                    _telemetry_to_mapping(telemetry)
                    if telemetry is not None
                    else None
                ),
                telemetry_archive=(
                    ()
                    if runtime_instance_id
                    else tuple(
                        _telemetry_to_mapping(entry)
                        for entry in telemetry_archive
                    )
                ),
                expected_scope="battlefield",
                previous_identity=(
                    previous_cursor.identity
                    if previous_cursor is not None
                    else None
                ),
                previous_payload_fingerprint=(
                    previous_cursor.payload_fingerprint
                    if previous_cursor is not None
                    else ""
                ),
            )
            status_payload = _micromachine_status_payload(
                snapshot.to_dict(),
                telemetry=telemetry,
                telemetry_archive=telemetry_archive,
                blackboard_dir=root,
                compile_result=compile_result,
                result_stream=compile_result_stream,
                battlefield_projection=battlefield_projection,
            )
            if operation_registry_authoritative_override is not None:
                status_payload["operation_registry_authoritative"] = (
                    operation_registry_authoritative_override
                )
            blackboard_scope_id = _micromachine_blackboard_scope_id(root)
            status_payload["blackboard_scope_id"] = blackboard_scope_id
            status_payload = self._micromachine_operation_timeline.observe(
                status_payload,
                blackboard_scope_id=blackboard_scope_id,
            )
            projection = status_payload.get("battlefield_projection")
            identity = status_payload.get("battlefield_projection_identity")
            if (
                runtime_instance_id
                and isinstance(projection, Mapping)
                and projection.get("ok") is True
                and isinstance(identity, Mapping)
                and battlefield_projection.battlefield_overview is not None
            ):
                self._micromachine_battlefield_cursors[
                    identity_key
                ] = _BattlefieldProjectionCursor(
                    identity=dict(identity),
                    payload_fingerprint=battlefield_overview_fingerprint(
                        battlefield_projection.battlefield_overview
                    ),
                )
        payload = {
            "enabled": True,
            "blackboard_dir": root,
            **result_metadata,
            **status_payload,
        }
        payload["modulation_results"] = compile_result_stream
        public_payload = _public_micromachine_runtime_payload(payload)
        if not isinstance(public_payload, Mapping):
            return {}
        result = dict(public_payload)
        self._update_micromachine_recent_lifecycle(root, result)
        return result

    def _run_loop(self) -> None:
        """Daemon thread body: run a private asyncio loop draining commands."""

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        queue: "asyncio.PriorityQueue[tuple[int, int, object]]" = (
            asyncio.PriorityQueue()
        )
        normal_executor: concurrent.futures.ThreadPoolExecutor | None = None
        emergency_executor: concurrent.futures.ThreadPoolExecutor | None = None
        active = False
        try:
            normal_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="voi-mm-normal",
            )
            emergency_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="voi-mm-emergency",
            )
            with self._lifecycle_lock:
                if self._lifecycle_state == _BRIDGE_LIFECYCLE_STARTING:
                    self._loop = loop
                    self._queue = queue
                    self._micromachine_normal_executor = normal_executor
                    self._micromachine_emergency_executor = emergency_executor
                    self._lifecycle_state = _BRIDGE_LIFECYCLE_RUNNING
                    active = True
                self._ready.set()
            if active:
                loop.run_until_complete(self._drain_commands())
        finally:
            if normal_executor is not None:
                normal_executor.shutdown(wait=True)
            if emergency_executor is not None:
                emergency_executor.shutdown(wait=True)
            self._terminate_pending_micromachine_requests(
                "Session loop bridge stopped before the MicroMachine request completed."
            )
            with self._lifecycle_lock:
                if self._loop is loop:
                    self._loop = None
                    self._queue = None
                    self._micromachine_normal_executor = None
                    self._micromachine_emergency_executor = None
                if self._thread is threading.current_thread():
                    self._thread = None
                self._lifecycle_state = _BRIDGE_LIFECYCLE_STOPPED
                self._stopping.set()
                self._ready.set()
            asyncio.set_event_loop(None)
            loop.close()

    async def _drain_commands(self) -> None:
        """Drain normal work serially while dispatching emergency work immediately."""

        queue = self._queue
        assert queue is not None  # Set by _run_loop before _ready fires.
        while True:
            _priority, _sequence, item = await queue.get()
            if item is _STOP_SENTINEL:
                return
            if isinstance(item, _MicroMachineModulationRequest):
                executor = (
                    self._micromachine_emergency_executor
                    if item.emergency
                    else self._micromachine_normal_executor
                )
                if executor is None:
                    if not item.future.done():
                        item.future.set_exception(
                            RuntimeError(
                                "MicroMachine request executor is not running."
                            )
                        )
                    self._forget_micromachine_request(item)
                    continue
                executor.submit(self._process_one_micromachine_request, item)
                continue
            if isinstance(item, _CorrelatedWebCommand):
                await self._process_one(
                    item.text,
                    web_request_id=item.request_id,
                )
                continue
            await self._process_one(str(item))

    def _process_one_micromachine_request(
        self,
        request: _MicroMachineModulationRequest,
    ) -> None:
        """Compile and publish one MicroMachine update on its assigned lane."""

        if request.future.cancelled() or request.cancel_event.is_set():
            if not request.future.done():
                request.future.set_exception(
                    RuntimeError(
                        "MicroMachine request was cancelled before publication."
                    )
                )
            self._forget_micromachine_request(request)
            return
        root = _clean_blackboard_dir(
            request.blackboard_dir,
            self._micromachine_blackboard_dir,
        )
        commander_context = self._micromachine_commander_context(
            root,
            request.commander_context,
        )
        try:
            payload = self._publish_micromachine_modulation(
                request.text,
                blackboard_dir=root,
                provider_output=request.provider_output,
                allow_smoke_keyword_provider=request.allow_smoke_keyword_provider,
                semantic_scope=request.semantic_scope,
                commander_context=commander_context,
                ttl_seconds=request.ttl_seconds,
                current_frame=request.current_frame,
                update_id=request.update_id,
                request=request,
            )
        except Exception as error:  # noqa: BLE001 - returned to HTTP handler.
            if not request.cancel_event.is_set():
                self._remember_micromachine_command(
                    root,
                    request.text,
                    {
                        "status": "publish_failed",
                        "update_id": request.update_id or "",
                        "error": f"{type(error).__name__}: {error}",
                    },
                )
            if not request.future.done():
                request.future.set_exception(error)
            self._forget_micromachine_request(request)
            return
        self._remember_micromachine_command(root, request.text, payload)
        if not request.future.done():
            request.future.set_result(payload)
        self._forget_micromachine_request(request)

    def _micromachine_commander_context(
        self,
        blackboard_dir: str,
        supplied_context: Mapping[str, object],
    ) -> dict[str, object]:
        context = dict(supplied_context)
        key = os.path.realpath(blackboard_dir)
        with self._micromachine_recent_commands_lock:
            has_history = bool(self._micromachine_recent_commands.get(key))
        if has_history:
            try:
                self.micromachine_status(blackboard_dir=blackboard_dir)
            except Exception:
                pass
        with self._micromachine_recent_commands_lock:
            history = self._micromachine_recent_commands.get(key)
            context["recent_commands"] = (
                json.loads(json.dumps(list(history), ensure_ascii=False))
                if history is not None
                else []
            )
        return context

    def _remember_micromachine_command(
        self,
        blackboard_dir: str,
        command_text: str,
        payload: Mapping[str, object],
    ) -> None:
        entry = _micromachine_recent_command_entry(command_text, payload)
        key = os.path.realpath(blackboard_dir)
        with self._micromachine_recent_commands_lock:
            history = self._micromachine_recent_commands.setdefault(
                key,
                deque(maxlen=_MICROMACHINE_RECENT_COMMAND_LIMIT),
            )
            history.append(entry)

    def _update_micromachine_recent_lifecycle(
        self,
        blackboard_dir: str,
        payload: Mapping[str, object],
    ) -> None:
        update = _mapping_child(payload, "update")
        intervention = _mapping_child(payload, "intervention")
        execution = _mapping_child(intervention, "command_execution")
        update_id = (
            execution.get("command_id")
            or update.get("update_id")
            or ""
        )
        normalized_update_id = _micromachine_recent_context_text(update_id)
        if not normalized_update_id:
            return
        consumption_status = _micromachine_recent_context_text(
            payload.get("consumption_status", "")
        )
        execution_status = _micromachine_recent_context_text(
            execution.get("state", "")
        )
        key = os.path.realpath(blackboard_dir)
        with self._micromachine_recent_commands_lock:
            history = self._micromachine_recent_commands.get(key)
            if history is None:
                return
            for entry in reversed(history):
                if entry.get("update_id") != normalized_update_id:
                    continue
                if consumption_status:
                    entry["consumption_status"] = consumption_status
                if execution_status:
                    entry["execution_status"] = execution_status
                break

    async def _process_one(
        self,
        text: str,
        *,
        web_request_id: str = "",
    ) -> None:
        """Run one utterance through the session; never drop it silently."""

        try:
            outcomes = await self._session.process_text(text)
        except Exception as error:  # noqa: BLE001 - recorded honestly, never dropped.
            outcome = _internal_error_outcome(text, error)
            self._history.record(
                _correlate_web_outcome(outcome, web_request_id)
                if web_request_id
                else outcome
            )
            return
        for outcome in outcomes:
            self._history.record(
                _correlate_web_outcome(outcome, web_request_id)
                if web_request_id
                else outcome
            )


def _normalize_web_request_id(value: object) -> str:
    """Validate one optional browser correlation identity."""

    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise TypeError("Web GUI request_id must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Web GUI request_id must be non-empty.")
    if len(normalized) > MAX_WEB_REQUEST_ID_CHARS:
        raise ValueError(
            "Web GUI request_id exceeds "
            f"{MAX_WEB_REQUEST_ID_CHARS} characters."
        )
    if any(character.isspace() or ord(character) < 33 for character in normalized):
        raise ValueError(
            "Web GUI request_id may not contain whitespace or control characters."
        )
    return normalized


def _correlate_web_outcome(
    outcome: object,
    request_id: str,
) -> dict[str, object]:
    """Attach exact browser correlation without mutating session outcomes."""

    document = _outcome_event(outcome)
    detail = document.get("detail")
    correlated_detail = dict(detail) if isinstance(detail, Mapping) else {}
    correlated_detail["web_request_id"] = request_id
    document["detail"] = correlated_detail
    document["request_id"] = request_id
    return document


def _outcome_event(outcome: object) -> dict[str, object]:
    """Render one outcome-like object into a JSON-ready history event."""

    document: dict[str, object] = {}
    to_dict = getattr(outcome, "to_dict", None)
    if callable(to_dict):
        try:
            rendered = to_dict()
        except Exception:
            rendered = None
        if isinstance(rendered, Mapping):
            document = dict(rendered)
    elif isinstance(outcome, Mapping):
        document = dict(outcome)
    for key in ("command_text", "status", "narration"):
        value = document.get(key, getattr(outcome, key, ""))
        document[key] = "" if value is None else str(value)
    return _redact_json_ready(document)  # type: ignore[return-value]


def _as_event_mapping(entry: object) -> dict[str, object]:
    """Normalize one duck-typed history entry into a JSON-ready mapping."""

    if isinstance(entry, Mapping):
        return _redact_json_ready(dict(entry))  # type: ignore[return-value]
    to_dict = getattr(entry, "to_dict", None)
    if callable(to_dict):
        try:
            rendered = to_dict()
        except Exception:
            rendered = None
        if isinstance(rendered, Mapping):
            return _redact_json_ready(dict(rendered))  # type: ignore[return-value]
    document: dict[str, object] = {}
    for attribute in ("seq", "command_text", "status", "narration"):
        value = getattr(entry, attribute, None)
        if value is not None:
            document[attribute] = value
    return _redact_json_ready(document)  # type: ignore[return-value]


def _attach_standing_order_snapshot(
    snapshot: dict[str, object],
    session: object,
) -> None:
    """Attach safe standing-order state for dashboard-only briefing evidence."""

    standing_orders = getattr(session, "standing_orders", None)
    if standing_orders is None:
        return
    status = _call_string(standing_orders, "korean_status")
    active_kinds = _call_string_tuple(standing_orders, "active_kinds")
    document: dict[str, object] = {
        "active_kinds": list(active_kinds),
        "korean_status": status,
    }
    labels = _safe_mapping(getattr(standing_orders, "korean_labels", None))
    if labels:
        document["korean_labels"] = labels
    snapshot["standing_orders"] = _redact_json_ready(document)


def _attach_briefing_context_snapshot(
    snapshot: dict[str, object],
    session: object,
) -> None:
    """Attach optional safe summaries consumed by the dashboard briefing."""

    event_memory = getattr(session, "event_memory", None)
    memory_summary = _call_summary_value(event_memory, ("korean_summary",))
    if memory_summary:
        snapshot["compacted_memory"] = _redact_json_ready(
            {"source": "event_memory", "korean_summary": memory_summary}
        )

    llm_summary = _call_summary_value(
        session,
        ("briefing_llm_summary", "strategic_llm_summary", "llm_summary"),
    )
    if llm_summary:
        safe_llm_summary = _safe_briefing_context_value(llm_summary)
        if safe_llm_summary not in ({}, [], "", None):
            snapshot["llm_summary"] = _redact_json_ready(safe_llm_summary)


def _call_summary_value(source: object | None, names: tuple[str, ...]) -> object | None:
    if source is None:
        return None
    for name in names:
        try:
            value = getattr(source, name, None)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value is None or value == "":
            continue
        return value
    return None


def _safe_briefing_context_value(value: object) -> object:
    """Drop prompt/key-shaped fields from optional LLM briefing context."""

    if isinstance(value, Mapping):
        safe: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_unsafe_briefing_context_key(key):
                continue
            safe[key] = _safe_briefing_context_value(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_briefing_context_value(item) for item in value]
    return value


def _is_unsafe_briefing_context_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (
        "prompt" in normalized
        or "apikey" in normalized
        or normalized == "key"
        or "secret" in normalized
    )


def _call_string(source: object, method_name: str) -> str:
    method = getattr(source, method_name, None)
    if not callable(method):
        return ""
    try:
        value = method()
    except Exception:  # noqa: BLE001 - dashboard state should stay available.
        return ""
    return "" if value is None else str(value)


def _call_string_tuple(source: object, method_name: str) -> tuple[str, ...]:
    method = getattr(source, method_name, None)
    if not callable(method):
        return ()
    try:
        values = method()
    except Exception:  # noqa: BLE001 - dashboard state should stay available.
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return ()
    return tuple(str(value) for value in values if value is not None)


def _safe_mapping(source: object) -> dict[str, str]:
    if not isinstance(source, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in source.items()
        if key is not None and value is not None
    }


def _internal_error_outcome(text: str, error: Exception) -> object:
    """Build one honest blocked outcome for a session-level failure."""

    # Lazy import: the bridge itself duck-types sessions, so importing the
    # module never needs the live pipeline (and its ToyCraft interpreter).
    from starcraft_commander.live_pipeline import SC2CommandOutcome

    return SC2CommandOutcome(
        command_text=str(text),
        status="blocked",
        narration=(
            "내부 오류로 명령을 실행하지 못했습니다 "
            f"(이유: {_redact_sensitive_text(error, normalize_whitespace=True)}). "
            "같은 명령을 다시 입력해 보시고, 문제가 반복되면 터미널 로그를 확인해 주세요."
        ),
    )


_WEB_GUI_PAGE_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    color-scheme: light;
    --ink: #eff6ff;
    --muted: #a9bce4;
    --panel: rgba(7, 13, 34, 0.8);
    --panel-soft: rgba(13, 21, 47, 0.64);
    --panel-strong: rgba(14, 23, 54, 0.94);
    --field: rgba(240, 247, 255, 0.94);
    --line: rgba(136, 169, 255, 0.24);
    --line-strong: rgba(77, 238, 234, 0.34);
    --accent: #4deeea;
    --accent-dark: #33c7ff;
    --amber: #ffd166;
    --red: #ff6b8a;
    --blue: #80a7ff;
    --violet: #b58cff;
    --shadow: 0 28px 90px rgba(0, 0, 0, 0.38);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; padding: 24px; color: var(--ink);
    font-family: "Avenir Next", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
    background:
      radial-gradient(circle at 18% 12%, rgba(77, 238, 234, 0.13), transparent 30%),
      radial-gradient(circle at 88% 8%, rgba(181, 140, 255, 0.12), transparent 32%),
      linear-gradient(145deg, #02030b 0%, #070c22 42%, #10061c 100%);
    overflow-x: hidden;
  }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
  .space-background {
    position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden;
    contain: paint;
    background:
      radial-gradient(ellipse at 18% 24%, rgba(64, 224, 255, 0.34) 0%, rgba(64, 224, 255, 0.08) 28%, transparent 54%),
      radial-gradient(ellipse at 72% 18%, rgba(214, 129, 255, 0.35) 0%, rgba(214, 129, 255, 0.1) 25%, transparent 50%),
      radial-gradient(ellipse at 78% 76%, rgba(255, 195, 97, 0.22) 0%, rgba(255, 195, 97, 0.06) 24%, transparent 52%),
      radial-gradient(circle at 50% 115%, rgba(77, 238, 234, 0.16), transparent 42%),
      linear-gradient(145deg, #02030b 0%, #070c22 34%, #160a28 67%, #030611 100%);
  }
  .space-background::before {
    content: ""; position: absolute; inset: -18% -8% -22%; opacity: 0.46;
    background:
      radial-gradient(ellipse at 32% 52%, rgba(51, 199, 255, 0.16), transparent 36%),
      radial-gradient(ellipse at 64% 42%, rgba(255, 107, 138, 0.12), transparent 32%),
      linear-gradient(180deg, rgba(128, 167, 255, 0.12), rgba(2, 3, 11, 0.78) 78%);
    filter: blur(22px);
  }
  .space-background::after {
    content: ""; position: fixed; inset: 4% -12% -28% 38%; width: 80vw; height: 80vw;
    pointer-events: none; border-radius: 999px; opacity: 0.62;
    background:
      conic-gradient(from 220deg, transparent 0 18%, rgba(77, 238, 234, 0.18) 26%, rgba(181, 140, 255, 0.18) 38%, transparent 55% 100%),
      radial-gradient(circle, rgba(77, 238, 234, 0.16), transparent 58%);
    filter: blur(18px);
  }
  .star-depth {
    position: fixed; inset: -10vmax; z-index: 0; pointer-events: none;
    contain: paint; transform: translate3d(0, 0, 0); will-change: transform;
    mix-blend-mode: screen;
  }
  .star-depth-far {
    opacity: 0.34;
    background:
      radial-gradient(circle at 9% 18%, rgba(255, 255, 255, 0.72) 0 1px, transparent 1.7px),
      radial-gradient(circle at 23% 64%, rgba(128, 167, 255, 0.58) 0 1px, transparent 1.8px),
      radial-gradient(circle at 41% 32%, rgba(255, 255, 255, 0.5) 0 0.8px, transparent 1.5px),
      radial-gradient(circle at 58% 74%, rgba(77, 238, 234, 0.48) 0 1px, transparent 1.8px),
      radial-gradient(circle at 78% 28%, rgba(255, 255, 255, 0.6) 0 1px, transparent 1.9px),
      radial-gradient(circle at 91% 68%, rgba(181, 140, 255, 0.52) 0 1px, transparent 1.8px);
    animation: star-parallax-far 64s linear infinite;
  }
  .star-depth-near {
    opacity: 0.52;
    background:
      radial-gradient(circle at 14% 72%, rgba(255, 255, 255, 0.86) 0 1.2px, transparent 2.3px),
      radial-gradient(circle at 30% 23%, rgba(77, 238, 234, 0.68) 0 1.1px, transparent 2.2px),
      radial-gradient(circle at 53% 56%, rgba(255, 255, 255, 0.72) 0 1px, transparent 2px),
      radial-gradient(circle at 67% 14%, rgba(255, 209, 102, 0.62) 0 1.2px, transparent 2.3px),
      radial-gradient(circle at 85% 83%, rgba(255, 255, 255, 0.78) 0 1.1px, transparent 2.1px);
    animation: star-parallax-near 42s linear infinite;
  }
  @keyframes star-parallax-far {
    from { transform: translate3d(-1.2vmax, -0.6vmax, 0); }
    to { transform: translate3d(1.2vmax, 0.6vmax, 0); }
  }
  @keyframes star-parallax-near {
    from { transform: translate3d(1.8vmax, 1.1vmax, 0); }
    to { transform: translate3d(-1.8vmax, -1.1vmax, 0); }
  }
  @media (prefers-reduced-motion: reduce) {
    .star-depth {
      animation: none !important;
      transform: none;
      will-change: auto;
    }
    .active-command-console::after {
      transition: none !important;
    }
    .command-stage.stage-current::before {
      animation: none !important;
    }
    .message-pending .narration::after {
      animation: none !important;
      content: "..." !important;
    }
    .typing-indicator span, .voice-wave span {
      animation: none !important;
      transform: none;
    }
    .tactical-radio-status.is-speaking::before {
      animation: none !important;
    }
  }
  @media (prefers-contrast: more) {
    :root {
      --panel: rgba(1, 5, 18, 0.94);
      --panel-strong: rgba(1, 5, 18, 0.98);
      --line: rgba(239, 246, 255, 0.48);
      --muted: #dbeafe;
    }
    .space-background { opacity: 0.78; filter: saturate(0.86) contrast(1.08); }
    .star-depth { opacity: 0.18; mix-blend-mode: normal; }
    #command-panel, #state-panel { backdrop-filter: none; }
  }
  @media (forced-colors: active) {
    body { background: Canvas; color: CanvasText; }
    .space-background, .space-background::before, .space-background::after, .star-depth { display: none; }
    .language-switcher button, .connection-pill, #command-panel, #state-panel,
    .metric-card, .collapsible-panel, .message, #log, #command-form,
    .runtime-mode-panel, .mode-option, .operation-console, .operation-card,
    .operation-lane, .operation-card-detail, .operation-timeline-panel,
    .operation-timeline-item,
    .active-command-console, .battlefield-control-overview,
    .command-console-field, .command-stage, .tactical-radio {
      forced-color-adjust: auto; background: Canvas; color: CanvasText;
      border-color: CanvasText; box-shadow: none; backdrop-filter: none;
    }
    #send-button, #voice-button, #tactical-radio-mute,
    #llm-panel button, .runtime-actions button {
      background: ButtonFace; color: ButtonText; border: 1px solid ButtonText;
    }
  }
  .app-shell { position: relative; z-index: 1; max-width: 1540px; margin: 0 auto; }
  .language-switcher {
    display: flex; gap: 8px; justify-content: flex-end; margin-bottom: 12px;
  }
  .language-switcher button {
    border: 1px solid var(--line); border-radius: 999px; padding: 8px 11px;
    color: var(--ink); background: rgba(255, 255, 255, 0.08); cursor: pointer;
    font-weight: 900;
  }
  .language-switcher button.active {
    background: linear-gradient(135deg, var(--accent), var(--violet));
    color: #04111f; border-color: transparent;
  }
  .hero {
    display: flex; align-items: flex-end; justify-content: space-between; gap: 18px;
    margin-bottom: 22px;
  }
  .eyebrow {
    margin: 0 0 8px; color: var(--accent); font-weight: 800;
    letter-spacing: 0.12em; text-transform: uppercase; font-size: 0.76rem;
  }
  h1 { margin: 0; font-size: clamp(2rem, 4vw, 4.2rem); line-height: 0.95; letter-spacing: -0.06em; }
  p.hint { margin: 8px 0 0; color: var(--muted); font-size: 0.95rem; }
  .connection-pill {
    flex: 0 0 auto; padding: 10px 14px; border: 1px solid var(--line);
    border-radius: 999px; background: rgba(7, 13, 34, 0.72);
    box-shadow: 0 10px 30px rgba(17, 24, 39, 0.08); font-weight: 800;
  }
  main {
    display: grid; grid-template-columns: minmax(540px, 1.32fr) minmax(420px, 0.88fr);
    gap: 24px; align-items: start; min-height: 0;
  }
  #command-panel {
    min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden;
    height: clamp(560px, calc(100vh - 160px), 860px); max-height: calc(100vh - 160px);
    border: 1px solid var(--line);
    border-radius: 28px; background: var(--panel); box-shadow: var(--shadow);
    backdrop-filter: blur(18px);
  }
  .chat-header {
    display: flex; justify-content: space-between; gap: 18px; align-items: flex-start;
    padding: 20px 22px; border-bottom: 1px solid var(--line);
    background: linear-gradient(90deg, rgba(77, 238, 234, 0.15), rgba(181, 140, 255, 0.13));
  }
  .chat-header > div:first-child { min-width: min(320px, 52%); }
  .chat-title { margin: 0; font-size: 1rem; font-weight: 900; }
  .chat-subtitle { margin: 3px 0 0; color: var(--muted); font-size: 0.82rem; }
  .assistant-pending-status {
    min-height: 1.2em; margin: 5px 0 0; color: var(--accent);
    font-size: 0.78rem; font-weight: 900; letter-spacing: 0.01em;
  }
  .assistant-pending-status:empty { visibility: hidden; }
  .quick-commands {
    display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end;
    max-width: 48%; min-width: 240px;
  }
  .quick-commands button {
    border: 1px solid rgba(77, 238, 234, 0.3); background: rgba(255, 255, 255, 0.08); color: var(--ink);
    border-radius: 999px; padding: 8px 10px; font-weight: 800; cursor: pointer;
  }
  .runtime-mode-panel {
    order: 2;
    padding: 16px 22px; border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, rgba(2, 6, 23, 0.5), rgba(8, 13, 32, 0.34));
  }
  .runtime-mode-title {
    display: flex; justify-content: space-between; gap: 10px; margin: 0 0 10px;
    color: var(--muted); font-size: 0.78rem; font-weight: 900;
  }
  #runtime-mode-summary {
    color: var(--accent); overflow-wrap: anywhere;
  }
  .mode-options {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px;
  }
  .mode-option {
    display: flex; gap: 10px; align-items: flex-start; padding: 11px 12px;
    border: 1px solid var(--line); border-radius: 16px;
    background: rgba(255, 255, 255, 0.07); cursor: pointer; min-width: 0;
  }
  .mode-option:has(input:checked) {
    border-color: var(--line-strong);
    background: linear-gradient(135deg, rgba(77, 238, 234, 0.13), rgba(181, 140, 255, 0.1));
  }
  .mode-option input { margin-top: 3px; accent-color: var(--accent); }
  .mode-label { display: block; color: var(--ink); font-weight: 900; font-size: 0.85rem; }
  .mode-description { display: block; margin-top: 3px; color: var(--muted); font-size: 0.76rem; line-height: 1.35; }
  .legacy-mode-warning {
    display: none; margin: 9px 0 0; padding: 9px 10px;
    border: 1px solid rgba(245, 158, 11, 0.34); border-radius: 13px;
    background: rgba(245, 158, 11, 0.12); color: #facc15;
    font-size: 0.78rem; font-weight: 800; line-height: 1.45;
  }
  #live-status {
    margin: 10px 0 0; padding: 10px 11px; border: 1px solid var(--line); border-radius: 14px;
    background: rgba(255, 255, 255, 0.08); color: var(--ink); font-size: 0.8rem; line-height: 1.45;
  }
  #live-status a { color: var(--accent); font-weight: 900; }
  .runtime-actions { display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
  .runtime-config {
    display: flex; align-items: center; gap: 10px; margin-top: 10px;
    color: var(--muted); font-size: 0.78rem; font-weight: 800;
  }
  .runtime-config input {
    width: 84px; margin: 0; padding: 8px 10px;
  }
  .runtime-actions button {
    flex: 1 1 160px; margin-top: 0 !important; padding: 10px 12px !important;
    background: rgba(255, 255, 255, 0.9) !important; color: #071225 !important;
  }
  .operation-console {
    order: 1; margin: 14px 18px 0; padding: 14px;
    border: 1px solid rgba(136, 169, 255, 0.28); border-radius: 22px;
    background:
      linear-gradient(145deg, rgba(4, 11, 29, 0.96), rgba(10, 23, 46, 0.9)),
      radial-gradient(circle at 10% 0, rgba(77, 238, 234, 0.14), transparent 36%);
    box-shadow: 0 16px 42px rgba(0, 0, 0, 0.24);
  }
  .operation-console-header {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 12px; margin-bottom: 10px;
  }
  .operation-console-title {
    margin: 0; color: var(--ink); font-size: 0.94rem; font-weight: 900;
  }
  .operation-console-hint {
    margin: 4px 0 0; color: var(--muted); font-size: 0.72rem; line-height: 1.4;
  }
  .operation-summary {
    flex: 0 0 auto; padding: 6px 9px; border: 1px solid var(--line);
    border-radius: 999px; color: var(--accent); background: rgba(77, 238, 234, 0.08);
    font-size: 0.68rem; font-weight: 900; white-space: nowrap;
  }
  .operation-list {
    display: grid; grid-template-columns: repeat(4, minmax(250px, 1fr));
    gap: 10px; max-height: 430px; overflow: auto; overscroll-behavior: contain;
    scrollbar-gutter: stable both-edges; align-items: start;
  }
  .operation-lane {
    min-width: 0; padding: 9px; border: 1px solid rgba(136, 169, 255, 0.18);
    border-radius: 16px; background: rgba(255, 255, 255, 0.028);
  }
  .operation-lane-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 8px; margin-bottom: 8px;
  }
  .operation-lane-title {
    margin: 0; color: var(--ink); font-size: 0.7rem; font-weight: 900;
  }
  .operation-lane-count {
    min-width: 1.7rem; padding: 3px 6px; border: 1px solid var(--line);
    border-radius: 999px; color: var(--muted); font-size: 0.58rem;
    font-weight: 900; text-align: center;
  }
  .operation-lane-list {
    display: flex; flex-direction: column; gap: 8px; min-height: 54px;
  }
  .operation-empty {
    grid-column: 1 / -1; margin: 0; padding: 12px;
    border: 1px dashed var(--line); border-radius: 14px;
    color: var(--muted); font-size: 0.78rem; text-align: center;
  }
  .operation-card {
    position: relative; min-width: 0; padding: 12px;
    border: 1px solid rgba(136, 169, 255, 0.22); border-radius: 17px;
    background: rgba(255, 255, 255, 0.045); overflow: hidden;
  }
  .operation-card::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--amber);
  }
  .operation-card.command-console-executing::before { background: var(--accent); }
  .operation-card.command-console-waiting::before { background: var(--amber); }
  .operation-card.command-console-verified::before { background: #4ade80; }
  .operation-card.command-console-blocked::before { background: var(--red); }
  .operation-card.command-console-superseded::before { background: var(--amber); }
  .operation-card-header {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 10px;
  }
  .operation-card-kicker {
    display: block; margin-bottom: 4px; color: var(--accent);
    font-size: 0.62rem; font-weight: 900; letter-spacing: 0.11em;
    text-transform: uppercase; overflow-wrap: anywhere;
  }
  .operation-card-title {
    margin: 0; color: var(--ink); font-size: 0.86rem; line-height: 1.35;
    font-weight: 900; overflow-wrap: anywhere;
  }
  .operation-card-state {
    flex: 0 0 auto; padding: 5px 7px; border: 1px solid var(--line);
    border-radius: 999px; color: var(--amber); background: rgba(245, 158, 11, 0.1);
    font-size: 0.62rem; font-weight: 900; white-space: nowrap;
  }
  .operation-card.command-console-executing .operation-card-state {
    color: var(--accent); border-color: rgba(77, 238, 234, 0.34);
  }
  .operation-card.command-console-waiting .operation-card-state {
    color: var(--amber); border-color: rgba(255, 209, 102, 0.38);
  }
  .operation-card.command-console-verified .operation-card-state {
    color: #4ade80; border-color: rgba(74, 222, 128, 0.34);
  }
  .operation-card.command-console-blocked .operation-card-state {
    color: #ff9eb2; border-color: rgba(255, 107, 138, 0.38);
  }
  .operation-stage-line {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 5px; margin: 10px 0;
  }
  .operation-stage {
    min-width: 0; padding: 5px 4px; border: 1px solid var(--line);
    border-radius: 8px; color: var(--muted); background: rgba(255, 255, 255, 0.035);
    font-size: 0.58rem; font-weight: 900; text-align: center;
  }
  .operation-stage.stage-current { color: var(--accent); border-color: rgba(77, 238, 234, 0.35); }
  .operation-stage.stage-done { color: #7dd3fc; border-color: rgba(56, 189, 248, 0.3); }
  .operation-stage.stage-verified { color: #7ee7b0; border-color: rgba(74, 222, 128, 0.3); }
  .operation-stage.stage-blocked { color: #ff9eb2; border-color: rgba(255, 107, 138, 0.34); }
  .operation-card-details {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px;
  }
  .operation-card-detail {
    min-width: 0; padding: 7px 8px; border: 1px solid rgba(136, 169, 255, 0.16);
    border-radius: 10px; background: rgba(255, 255, 255, 0.035);
  }
  .operation-card-detail.operation-card-verification { grid-column: 1 / -1; }
  .operation-card-detail span {
    display: block; margin-bottom: 3px; color: var(--muted);
    font-size: 0.58rem; font-weight: 900;
  }
  .operation-card-detail strong {
    display: block; color: var(--ink); font-size: 0.68rem; line-height: 1.35;
    font-weight: 800; overflow-wrap: anywhere;
  }
  .operation-card-actions {
    display: flex; gap: 6px; flex-wrap: wrap; margin-top: 9px;
  }
  .operation-card-actions button {
    margin: 0; padding: 6px 8px; border: 1px solid var(--line); border-radius: 9px;
    color: var(--ink); background: rgba(255, 255, 255, 0.07);
    font-size: 0.62rem; font-weight: 900; cursor: pointer;
  }
  .operation-card-actions button[aria-disabled="true"] {
    opacity: 0.48; cursor: not-allowed;
  }
  .operation-card-actions .operation-cancel-button {
    color: #fff1f4; border-color: rgba(255, 107, 138, 0.36);
  }
  .operation-resolution-actions {
    margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line);
  }
  .operation-resolution-actions > span {
    display: block; margin-bottom: 6px; color: var(--muted);
    font-size: 0.58rem; font-weight: 900;
  }
  .operation-resolution-actions div {
    display: flex; gap: 6px; flex-wrap: wrap;
  }
  .operation-resolution-actions button {
    margin: 0; padding: 6px 8px; border: 1px solid rgba(77, 238, 234, 0.3);
    border-radius: 9px; color: var(--accent); background: rgba(77, 238, 234, 0.07);
    font-size: 0.62rem; font-weight: 900; cursor: pointer;
  }
  .operation-resolution-actions button[aria-disabled="true"] {
    color: var(--muted); border-color: var(--line); opacity: 0.62; cursor: not-allowed;
  }
  .operation-resolution-reason {
    flex: 1 1 100%; color: #ffb4c4; font-size: 0.58rem;
    font-weight: 800; overflow-wrap: anywhere;
  }
  .operation-timeline-panel {
    margin-top: 11px; padding: 11px; border: 1px solid rgba(136, 169, 255, 0.2);
    border-radius: 16px; background: rgba(255, 255, 255, 0.025);
  }
  .operation-timeline-header {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 10px; margin-bottom: 8px;
  }
  .operation-timeline-header h3 {
    margin: 0; color: var(--ink); font-size: 0.74rem; font-weight: 900;
  }
  .operation-timeline-selection {
    color: var(--accent); font-size: 0.62rem; font-weight: 900;
    overflow-wrap: anywhere; text-align: right;
  }
  .operation-timeline {
    display: grid; gap: 7px; max-height: 190px; margin: 0; padding: 0;
    overflow-y: auto; list-style: none; overscroll-behavior: contain;
  }
  .operation-timeline-item {
    display: grid; grid-template-columns: auto minmax(0, 1fr) auto;
    gap: 8px; align-items: start; padding: 8px;
    border: 1px solid rgba(136, 169, 255, 0.14); border-radius: 11px;
    background: rgba(255, 255, 255, 0.035);
  }
  .operation-timeline-kind {
    color: var(--accent); font-size: 0.58rem; font-weight: 900;
    text-transform: uppercase;
  }
  .operation-timeline-summary {
    color: var(--ink); font-size: 0.68rem; line-height: 1.35;
    font-weight: 800; overflow-wrap: anywhere;
  }
  .operation-timeline-frame {
    color: var(--muted); font-size: 0.58rem; font-weight: 900;
    white-space: nowrap;
  }
  .operation-timeline-item details { grid-column: 1 / -1; color: var(--muted); }
  .operation-timeline-item summary { cursor: pointer; font-size: 0.58rem; font-weight: 900; }
  .operation-timeline-item pre {
    max-height: 110px; overflow: auto; margin: 6px 0 0; white-space: pre-wrap;
    overflow-wrap: anywhere; color: var(--ink); font-size: 0.58rem;
  }
  .active-command-console {
    order: 1;
    position: relative; margin: 14px 18px 0; padding: 16px;
    border: 1px solid rgba(77, 238, 234, 0.3); border-radius: 22px;
    background:
      linear-gradient(135deg, rgba(5, 16, 34, 0.96), rgba(8, 27, 37, 0.9)),
      radial-gradient(circle at 86% 18%, rgba(77, 238, 234, 0.2), transparent 34%);
    box-shadow: 0 16px 42px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.06);
    overflow: hidden;
  }
  .active-command-console::after {
    content: ""; position: absolute; top: 0; left: 0; width: 34%; height: 2px;
    background: linear-gradient(90deg, var(--accent), transparent);
    transition: width 0.3s ease, background 0.3s ease;
  }
  .active-command-console.command-console-assigning::after { width: 58%; }
  .active-command-console.command-console-waiting::after {
    width: 76%; background: var(--amber);
  }
  .active-command-console.command-console-executing::after {
    width: 82%; background: linear-gradient(90deg, var(--amber), var(--accent));
  }
  .active-command-console.command-console-verified::after {
    width: 100%; background: linear-gradient(90deg, #4ade80, var(--accent));
  }
  .active-command-console.command-console-blocked::after {
    width: 100%; background: linear-gradient(90deg, var(--red), var(--amber));
  }
  .active-command-console.command-console-superseded::after {
    width: 100%; background: linear-gradient(90deg, var(--amber), var(--accent));
  }
  .command-console-header {
    display: flex; justify-content: space-between; gap: 14px; align-items: flex-start;
  }
  .command-console-kicker {
    display: block; margin-bottom: 5px; color: var(--accent);
    font-size: 0.68rem; font-weight: 900; letter-spacing: 0.14em; text-transform: uppercase;
  }
  .command-console-title {
    margin: 0; color: var(--ink); font-size: 1rem; line-height: 1.35;
    font-weight: 900; letter-spacing: -0.02em; overflow-wrap: anywhere;
  }
  .command-console-state {
    flex: 0 0 auto; padding: 6px 9px; border: 1px solid var(--line);
    border-radius: 999px; color: var(--amber); background: rgba(245, 158, 11, 0.12);
    font-size: 0.7rem; font-weight: 900; white-space: nowrap;
  }
  .command-console-verified .command-console-state {
    color: #4ade80; border-color: rgba(74, 222, 128, 0.34); background: rgba(34, 197, 94, 0.12);
  }
  .command-console-executing .command-console-state {
    color: var(--accent); border-color: rgba(77, 238, 234, 0.34); background: rgba(77, 238, 234, 0.1);
  }
  .command-console-waiting .command-console-state {
    color: var(--amber); border-color: rgba(255, 209, 102, 0.38); background: rgba(255, 209, 102, 0.12);
  }
  .command-console-blocked .command-console-state {
    color: #ff9eb2; border-color: rgba(255, 107, 138, 0.38); background: rgba(255, 107, 138, 0.12);
  }
  .command-console-superseded .command-console-state {
    color: var(--amber); border-color: rgba(255, 209, 102, 0.38); background: rgba(255, 209, 102, 0.12);
  }
  .command-stage-rail {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px;
    margin: 14px 0 12px;
  }
  .command-stage {
    position: relative; min-width: 0; padding: 9px 8px 8px 26px;
    border: 1px solid var(--line); border-radius: 12px;
    background: rgba(255, 255, 255, 0.05); color: var(--muted);
    font-size: 0.68rem; font-weight: 900; line-height: 1.25;
  }
  .command-stage::before {
    content: ""; position: absolute; left: 9px; top: 50%; width: 8px; height: 8px;
    margin-top: -4px; border: 1px solid currentColor; border-radius: 999px;
  }
  .command-stage.stage-current {
    color: var(--accent); border-color: rgba(77, 238, 234, 0.35);
    background: rgba(77, 238, 234, 0.09);
  }
  .command-stage.stage-current::before {
    background: currentColor; box-shadow: 0 0 0 4px rgba(77, 238, 234, 0.11);
    animation: command-stage-pulse 1.2s ease-in-out infinite;
  }
  .command-stage.stage-done {
    color: #7dd3fc; border-color: rgba(56, 189, 248, 0.3);
    background: rgba(14, 165, 233, 0.08);
  }
  .command-stage.stage-done::before { background: currentColor; }
  .command-stage.stage-verified {
    color: #7ee7b0; border-color: rgba(74, 222, 128, 0.3);
    background: rgba(34, 197, 94, 0.08);
  }
  .command-stage.stage-verified::before { background: currentColor; }
  .command-stage.stage-blocked {
    color: #ff9eb2; border-color: rgba(255, 107, 138, 0.34);
    background: rgba(255, 107, 138, 0.09);
  }
  @keyframes command-stage-pulse {
    0%, 100% { opacity: 0.5; transform: scale(0.84); }
    50% { opacity: 1; transform: scale(1.12); }
  }
  .command-console-grid {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px;
  }
  .command-console-field {
    min-width: 0; padding: 9px 10px; border: 1px solid rgba(136, 169, 255, 0.18);
    border-radius: 13px; background: rgba(255, 255, 255, 0.045);
  }
  .command-console-field span {
    display: block; margin-bottom: 4px; color: var(--muted);
    font-size: 0.64rem; font-weight: 900; letter-spacing: 0.05em;
  }
  .command-console-field strong {
    display: block; color: var(--ink); font-size: 0.76rem; line-height: 1.35;
    font-weight: 800; overflow-wrap: anywhere;
  }
  .command-console-verification {
    grid-column: 1 / -1; border-color: rgba(77, 238, 234, 0.24);
  }
  .command-console-actions {
    display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 11px;
  }
  .command-console-actions button {
    margin: 0; padding: 8px 10px; border: 1px solid var(--line); border-radius: 11px;
    color: var(--ink); background: rgba(255, 255, 255, 0.07);
    font-size: 0.7rem; font-weight: 900; cursor: pointer;
  }
  .command-console-actions .command-emergency-button {
    margin-left: auto; color: #fff1f4; border-color: rgba(255, 107, 138, 0.4);
    background: rgba(255, 107, 138, 0.13);
  }
  .command-console-technical {
    margin-top: 10px; color: var(--muted); font-size: 0.7rem;
  }
  .command-console-technical summary { cursor: pointer; color: var(--accent); font-weight: 900; }
  .command-console-technical pre {
    max-height: 140px; overflow: auto; margin: 7px 0 0; padding: 9px;
    border: 1px solid var(--line); border-radius: 10px; background: rgba(0, 0, 0, 0.24);
    color: var(--ink); white-space: pre-wrap; overflow-wrap: anywhere;
  }
  #state-panel {
    min-width: 0; min-height: 0; max-height: calc(100vh - 160px); overflow-y: auto;
    display: flex; flex-direction: column; gap: 16px; scrollbar-gutter: stable;
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 28px; padding: 20px; box-shadow: var(--shadow); backdrop-filter: blur(18px);
  }
  #state-panel > * { min-width: 0; }
  #state-panel h2, #llm-panel h2, #briefing-panel h2 { margin: 0; font-size: 1rem; letter-spacing: -0.02em; }
  .battlefield-control-overview {
    position: relative; padding: 16px; border: 1px solid rgba(77, 238, 234, 0.3);
    border-radius: 22px; overflow: hidden;
    background:
      linear-gradient(145deg, rgba(4, 13, 30, 0.96), rgba(8, 30, 38, 0.78)),
      radial-gradient(circle at 100% 0, rgba(255, 209, 102, 0.15), transparent 40%);
  }
  .battlefield-control-overview::after {
    content: ""; position: absolute; right: -44px; top: -44px; width: 126px; height: 126px;
    border: 1px solid rgba(77, 238, 234, 0.16); border-radius: 999px;
    box-shadow: 0 0 0 22px rgba(77, 238, 234, 0.025), 0 0 0 44px rgba(77, 238, 234, 0.018);
    pointer-events: none;
  }
  .battlefield-control-header {
    position: relative; z-index: 1; display: flex; justify-content: space-between;
    gap: 12px; align-items: center; margin-bottom: 12px;
  }
  .battlefield-control-header h2 { margin: 0; }
  .battlefield-link-badge {
    padding: 5px 8px; border: 1px solid var(--line); border-radius: 999px;
    color: var(--amber); background: rgba(245, 158, 11, 0.11);
    font-size: 0.68rem; font-weight: 900;
  }
  .battlefield-link-badge.control-linked {
    color: #4ade80; border-color: rgba(74, 222, 128, 0.34); background: rgba(34, 197, 94, 0.11);
  }
  .battlefield-control-grid {
    position: relative; z-index: 1; display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 0;
  }
  .battlefield-control-grid > div {
    min-width: 0; padding: 10px; border: 1px solid rgba(136, 169, 255, 0.18);
    border-radius: 13px; background: rgba(255, 255, 255, 0.045);
  }
  .battlefield-control-grid dt {
    margin: 0 0 4px; color: var(--muted); font-size: 0.66rem; font-weight: 900;
  }
  .battlefield-control-grid dd {
    margin: 0; color: var(--ink); font-size: 0.82rem; font-weight: 900;
    overflow-wrap: anywhere;
  }
  .battlefield-control-summary {
    position: relative; z-index: 1; margin: 10px 0 0; padding-top: 10px;
    border-top: 1px solid var(--line); color: var(--muted);
    font-size: 0.78rem; line-height: 1.45;
  }
  .dashboard-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
    gap: 12px; margin: 0;
  }
  .metric-card {
    min-height: 88px; padding: 14px; border-radius: 20px; background: var(--panel-strong);
    border: 1px solid var(--line); position: relative; overflow: hidden;
  }
  .metric-card::after {
    content: ""; position: absolute; right: -20px; top: -26px; width: 70px; height: 70px;
    border-radius: 50%; background: rgba(15, 118, 110, 0.12);
  }
  .metric-card dt { margin: 0 0 8px; color: var(--muted); font-weight: 800; font-size: 0.76rem; }
  .metric-card dd { margin: 0; font-size: 1.28rem; font-weight: 900; font-variant-numeric: tabular-nums; }
  .wide-card { grid-column: 1 / -1; }
  #state-availability { margin: 0; font-size: 0.82rem; color: var(--muted); }
  #briefing-panel, #llm-panel, #micromachine-panel {
    margin: 0; padding: 16px; border: 1px solid var(--line); border-radius: 22px;
    background: var(--panel-soft);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
  }
  .collapsible-panel > summary {
    display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none;
    margin: 0; color: var(--ink); font-size: 1rem; font-weight: 900; letter-spacing: -0.02em;
    border-radius: 14px; padding: 8px 10px; background: rgba(255, 255, 255, 0.06);
  }
  .collapsible-panel > summary::-webkit-details-marker { display: none; }
  .collapsible-panel > summary::before {
    content: "▸"; color: var(--accent); font-size: 0.9rem; transition: transform 0.16s ease;
  }
  .collapsible-panel[open] > summary::before { transform: rotate(90deg); }
  .collapsible-panel[open] > summary { margin-bottom: 12px; }
  #strategy-briefing {
    margin: 0; color: var(--ink); line-height: 1.55; font-size: 0.92rem; white-space: pre-wrap;
  }
  .chat-trim-note {
    position: sticky; top: 0; z-index: 2; margin: 0 auto 14px; width: fit-content; max-width: 90%; padding: 7px 11px;
    color: var(--muted); border: 1px solid var(--line); border-radius: 999px;
    background: rgba(7, 13, 34, 0.86); font-size: 0.78rem; font-weight: 800;
  }
  .chat-trim-note summary {
    cursor: pointer; list-style: none;
  }
  .chat-trim-note summary::-webkit-details-marker { display: none; }
  .chat-trim-note summary::before {
    content: "▸"; display: inline-block; margin-right: 6px; color: var(--accent);
    transition: transform 0.16s ease;
  }
  .chat-trim-note[open] summary::before { transform: rotate(90deg); }
  .archived-chat {
    margin-top: 9px; max-height: 280px; overflow-y: auto; overscroll-behavior: contain;
    border-top: 1px solid var(--line); padding-top: 8px; text-align: left;
  }
  .archived-chat-item {
    margin: 0 0 8px; padding: 8px 9px; border-radius: 12px;
    background: rgba(255, 255, 255, 0.08); white-space: normal;
  }
  .archived-chat-meta {
    display: block; margin-bottom: 5px; color: var(--accent); font-size: 0.72rem; font-weight: 900;
  }
  #llm-panel label { display: block; margin: 8px 0 4px; font-size: 0.78rem; font-weight: 900; color: var(--muted); }
  #llm-panel select, #llm-panel input {
    width: 100%; padding: 10px 11px; border: 1px solid rgba(96, 112, 128, 0.28);
    border-radius: 12px; background: var(--field); color: #071225;
  }
  .provider-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 8px 0 10px; }
  .provider-option {
    display: flex !important; align-items: center; gap: 9px; margin: 0 !important;
    padding: 9px 10px; border: 1px solid rgba(96, 112, 128, 0.28);
    border-radius: 13px; background: rgba(255, 255, 255, 0.08); color: var(--ink) !important;
    cursor: pointer;
  }
  .provider-option input { width: auto !important; padding: 0 !important; accent-color: var(--accent); }
  #llm-panel button {
    width: 100%; margin-top: 10px; padding: 11px 12px; border: none; border-radius: 14px;
    background: linear-gradient(135deg, var(--accent), var(--violet)); color: #061126; font-weight: 900; cursor: pointer;
  }
  .llm-status {
    display: flex; gap: 8px; align-items: flex-start; margin: 10px 0 0;
    padding: 9px 10px; border: 1px solid var(--line); border-radius: 14px;
    background: rgba(255, 255, 255, 0.08); color: var(--muted); font-size: 0.78rem; line-height: 1.4;
  }
  .llm-status-label {
    flex: 0 0 auto; padding: 2px 7px; border-radius: 999px;
    background: rgba(255, 255, 255, 0.14); color: var(--ink);
    font-size: 0.7rem; font-weight: 900; letter-spacing: 0.01em;
  }
  .llm-status-message { min-width: 0; color: var(--muted); }
  .llm-status-setting .llm-status-label { background: rgba(245, 158, 11, 0.22); color: #fbbf24; }
  .llm-status-success .llm-status-label { background: rgba(34, 197, 94, 0.18); color: #4ade80; }
  .llm-status-failed .llm-status-label { background: rgba(248, 113, 113, 0.18); color: #fca5a5; }
  #micromachine-panel label {
    display: block; margin: 8px 0 4px; color: var(--muted);
    font-size: 0.78rem; font-weight: 900;
  }
  #micromachine-panel input, #micromachine-panel select {
    width: 100%; padding: 10px 11px; border: 1px solid rgba(96, 112, 128, 0.28);
    border-radius: 12px; background: var(--field); color: #071225; min-width: 0;
  }
  .micro-scope-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px;
    margin-top: 10px;
  }
  #micromachine-panel button {
    width: 100%; margin-top: 10px; padding: 11px 12px; border: none; border-radius: 14px;
    background: linear-gradient(135deg, var(--amber), var(--accent)); color: #061126;
    font-weight: 900; cursor: pointer;
  }
  #micromachine-status {
    margin-top: 10px; padding: 10px 11px; border: 1px solid var(--line);
    border-radius: 14px; background: rgba(255, 255, 255, 0.08);
    color: var(--ink); font-size: 0.8rem; line-height: 1.45;
  }
  #micromachine-intervention-dashboard {
    margin-top: 12px; padding: 14px; border: 1px solid rgba(77, 238, 234, 0.28);
    border-radius: 20px; background: rgba(2, 6, 23, 0.48);
  }
  .micro-intervention-header {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
    margin-bottom: 10px;
  }
  .micro-badge {
    flex: 0 0 auto; padding: 4px 8px; border-radius: 999px;
    border: 1px solid var(--line); font-size: 0.68rem; font-weight: 900;
  }
  .micro-badge-applied { color: #4ade80; background: rgba(34, 197, 94, 0.14); }
  .micro-badge-active { color: var(--accent); background: rgba(77, 238, 234, 0.12); }
  .micro-badge-pending { color: var(--amber); background: rgba(245, 158, 11, 0.14); }
  .micro-badge-blocked { color: #ff9eb2; background: rgba(255, 107, 138, 0.14); }
  .micro-badge-cancelled { color: var(--amber); background: rgba(245, 158, 11, 0.14); }
  .micro-intervention-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 10px; margin: 0;
  }
  .micro-intervention-grid > div {
    min-width: 0; margin: 0; padding: 10px; border: 1px solid var(--line);
    border-radius: 14px; background: rgba(255, 255, 255, 0.07);
  }
  .micro-intervention-grid dt {
    margin: 0 0 5px; color: var(--muted); font-size: 0.68rem; font-weight: 900;
  }
  .micro-intervention-grid dd {
    margin: 0; min-width: 0; color: var(--ink); font-size: 0.82rem; font-weight: 800;
    overflow-wrap: anywhere;
  }
  .micro-json-panel {
    margin-top: 9px; color: var(--muted); font-size: 0.78rem;
  }
  .micro-json-panel summary { cursor: pointer; font-weight: 900; color: var(--accent); }
  #micromachine-raw-evidence {
    max-height: 220px; overflow: auto; margin: 8px 0 0; padding: 10px;
    border: 1px solid var(--line); border-radius: 12px;
    background: rgba(0, 0, 0, 0.28); color: var(--ink); font-size: 0.72rem;
    white-space: pre-wrap; overflow-wrap: anywhere;
  }
  #micromachine-log-snippets {
    margin: 0; padding-left: 16px; color: var(--ink); font-size: 0.74rem;
    line-height: 1.45; max-height: 180px; overflow: auto;
  }
  #micromachine-log-snippets li { margin-bottom: 6px; overflow-wrap: anywhere; }
  #log {
    order: 3;
    flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 20px;
    scrollbar-gutter: stable;
    background:
      linear-gradient(180deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.02)),
      radial-gradient(circle at 20% 20%, rgba(77, 238, 234, 0.11), transparent 32%);
  }
  .log-entry { display: grid; gap: 8px; margin: 0 0 16px; }
  .message {
    max-width: min(74ch, 86%); padding: 12px 14px; border-radius: 18px;
    box-shadow: 0 10px 24px rgba(17, 24, 39, 0.08); white-space: pre-wrap; overflow-wrap: anywhere;
  }
  .message-text, .message-preview, .message-full {
    white-space: pre-wrap; overflow-wrap: anywhere;
  }
  .message-expander {
    margin-top: 6px; white-space: normal;
  }
  .message-expander summary {
    cursor: pointer; color: var(--accent); font-weight: 900; font-size: 0.78rem;
  }
  .message-full {
    display: block; margin-top: 7px; padding-top: 7px; border-top: 1px solid var(--line);
  }
  .message-user {
    justify-self: end; color: #03101e; background: linear-gradient(135deg, var(--accent), var(--accent-dark));
    border-bottom-right-radius: 6px;
  }
  .message-bot {
    justify-self: start; background: rgba(255, 255, 255, 0.1); border: 1px solid var(--line);
    border-bottom-left-radius: 6px;
  }
  .message-pending .narration::after {
    content: ""; display: inline-block; width: 1.5em; text-align: left;
    animation: pending-dots 1.2s steps(4, end) infinite;
  }
  .typing-indicator {
    display: inline-flex; align-items: center; gap: 4px; margin-left: 8px;
    vertical-align: middle;
  }
  .typing-indicator span {
    width: 6px; height: 6px; border-radius: 999px; background: var(--accent);
    animation: typing-pulse 0.9s ease-in-out infinite; opacity: 0.45;
  }
  .typing-indicator span:nth-child(2) { animation-delay: 0.12s; }
  .typing-indicator span:nth-child(3) { animation-delay: 0.24s; }
  @keyframes typing-pulse {
    0%, 100% { transform: translateY(0); opacity: 0.4; }
    50% { transform: translateY(-4px); opacity: 1; }
  }
  @keyframes pending-dots {
    0% { content: ""; }
    25% { content: "."; }
    50% { content: ".."; }
    75%, 100% { content: "..."; }
  }
  .voice-wave {
    display: inline-flex; gap: 4px; align-items: end; height: 24px; margin-left: 8px;
  }
  .voice-wave span {
    width: 4px; border-radius: 999px; background: var(--accent);
    animation: voice-wave 0.72s ease-in-out infinite;
  }
  .voice-wave span:nth-child(1) { height: 9px; animation-delay: 0s; }
  .voice-wave span:nth-child(2) { height: 18px; animation-delay: 0.08s; }
  .voice-wave span:nth-child(3) { height: 12px; animation-delay: 0.16s; }
  .voice-wave span:nth-child(4) { height: 22px; animation-delay: 0.24s; }
  .voice-wave span:nth-child(5) { height: 10px; animation-delay: 0.32s; }
  @keyframes voice-wave {
    0%, 100% { transform: scaleY(0.5); opacity: 0.55; }
    50% { transform: scaleY(1.25); opacity: 1; }
  }
  .voice-transcript {
    display: block; margin-top: 5px; min-height: 1.35em;
    color: #03101e; font-weight: 900;
  }
  .voice-transcript-interim { opacity: 0.7; }
  .voice-session-state {
    display: inline-block; margin-left: 7px; font-size: 0.68rem;
    font-weight: 900; color: rgba(3, 16, 30, 0.72);
  }
  .message-meta { display: block; margin-bottom: 5px; color: rgba(255, 255, 255, 0.72); font-size: 0.74rem; font-weight: 800; }
  .message-bot .message-meta { color: var(--muted); }
  .status { display: none; font-weight: 900; margin-right: 7px; white-space: nowrap; }
  .status-executed { color: __COLOR_EXECUTED__; }
  .status-partially_executed { color: __COLOR_PARTIAL__; }
  .status-blocked { color: __COLOR_BLOCKED__; }
  .status-clarification { color: __COLOR_CLARIFICATION__; }
  .status-read_only { color: __COLOR_READ_ONLY__; }
  #command-form {
    order: 5;
    display: flex; gap: 12px; padding: 16px 18px; border-top: 1px solid var(--line);
    background: rgba(7, 13, 34, 0.72);
  }
  #command-input {
    flex: 1; font-size: 1.02rem; padding: 14px 16px;
    border: 1px solid rgba(136, 169, 255, 0.28); border-radius: 18px; background: var(--field); color: #071225;
    min-width: 0;
  }
  #command-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.12); }
  #send-button {
    font-size: 1rem; font-weight: 900; padding: 12px 22px; border: none;
    border-radius: 18px; background: linear-gradient(135deg, var(--accent), var(--violet)); color: #061126; cursor: pointer;
  }
  #voice-button {
    flex: 0 0 auto; width: 50px; border: 1px solid rgba(77, 238, 234, 0.35);
    border-radius: 18px; color: var(--ink); background: rgba(255, 255, 255, 0.08);
    font-size: 1.08rem; cursor: pointer;
  }
  #voice-button.recording {
    color: #061126; background: linear-gradient(135deg, var(--amber), var(--accent));
  }
  .tactical-radio {
    order: 4; margin: 0 18px 12px; padding: 11px 12px;
    border: 1px solid rgba(77, 238, 234, 0.24); border-radius: 17px;
    background:
      linear-gradient(135deg, rgba(4, 14, 31, 0.94), rgba(10, 23, 46, 0.84)),
      radial-gradient(circle at 100% 0, rgba(77, 238, 234, 0.12), transparent 44%);
  }
  .tactical-radio-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px;
  }
  .tactical-radio-copy { min-width: 0; }
  .tactical-radio-title {
    margin: 0; color: var(--ink); font-size: 0.78rem; font-weight: 900;
    letter-spacing: 0.03em;
  }
  .tactical-radio-status {
    display: inline-flex; align-items: center; gap: 6px; margin-top: 3px;
    color: var(--muted); font-size: 0.66rem; font-weight: 800;
  }
  .tactical-radio-status::before {
    content: ""; width: 6px; height: 6px; border-radius: 999px;
    background: var(--accent); box-shadow: 0 0 0 3px rgba(77, 238, 234, 0.1);
  }
  .tactical-radio-status.is-speaking::before {
    animation: tactical-radio-pulse 1s ease-in-out infinite;
  }
  .tactical-radio-status.is-muted::before,
  .tactical-radio-status.is-unavailable::before {
    background: var(--amber); box-shadow: 0 0 0 3px rgba(255, 209, 102, 0.1);
  }
  @keyframes tactical-radio-pulse {
    0%, 100% { transform: scale(0.8); opacity: 0.58; }
    50% { transform: scale(1.25); opacity: 1; }
  }
  #tactical-radio-mute {
    flex: 0 0 auto; margin: 0; padding: 7px 9px;
    border: 1px solid var(--line); border-radius: 10px;
    color: var(--ink); background: rgba(255, 255, 255, 0.07);
    font-size: 0.66rem; font-weight: 900; cursor: pointer;
  }
  .tactical-radio-captions {
    display: grid; gap: 5px; max-height: 86px; margin: 9px 0 0;
    padding: 0; overflow-y: auto; list-style: none;
    overscroll-behavior: contain;
  }
  .tactical-radio-caption {
    display: grid; grid-template-columns: auto minmax(0, 1fr);
    gap: 7px; align-items: baseline; padding: 6px 7px;
    border: 1px solid rgba(136, 169, 255, 0.14); border-radius: 9px;
    background: rgba(255, 255, 255, 0.035);
  }
  .tactical-radio-priority {
    color: var(--accent); font-size: 0.58rem; font-weight: 900;
  }
  .tactical-radio-caption-text {
    min-width: 0; color: var(--ink); font-size: 0.68rem;
    line-height: 1.35; font-weight: 800; overflow-wrap: anywhere;
  }
  #send-button:disabled, #command-input:disabled, #voice-button:disabled {
    opacity: 0.55; cursor: not-allowed;
  }
  #send-button:hover:not(:disabled) { filter: brightness(1.08); }
  .briefing-block {
    margin: 0 0 12px; padding: 12px 13px; border: 1px solid var(--line);
    border-radius: 16px; background: rgba(255, 255, 255, 0.07);
  }
  .briefing-label {
    display: block; margin-bottom: 5px; color: var(--accent); font-size: 0.74rem;
    font-weight: 900; letter-spacing: 0.08em; text-transform: uppercase;
  }
  #strategy-briefing details {
    margin-top: 10px; border-top: 1px solid var(--line); padding-top: 10px;
  }
  #strategy-briefing summary {
    cursor: pointer; color: var(--amber); font-weight: 900;
  }
  @media (max-width: 1180px) {
    body { padding: 12px; }
    .space-background::after { inset: 20% -20% -18% 24%; width: 105vw; height: 105vw; opacity: 0.48; }
    .star-depth { inset: -14vmax; }
    .star-depth-near { opacity: 0.42; }
    .hero { display: block; }
    .connection-pill { display: inline-block; margin-top: 12px; }
    main { grid-template-columns: 1fr; gap: 16px; }
    .quick-commands { max-width: none; min-width: 0; justify-content: flex-start; }
    #command-panel { height: auto; min-height: 0; max-height: none; overflow: visible; }
    #log { min-height: clamp(280px, 42vh, 520px); max-height: 52vh; }
    #state-panel { max-height: none; }
  }
  @media (max-width: 620px) {
    .space-background {
      background:
        radial-gradient(ellipse at 22% 12%, rgba(64, 224, 255, 0.22) 0%, rgba(64, 224, 255, 0.06) 30%, transparent 56%),
        radial-gradient(ellipse at 80% 72%, rgba(214, 129, 255, 0.2) 0%, rgba(214, 129, 255, 0.06) 24%, transparent 54%),
        linear-gradient(145deg, #02030b 0%, #070c22 45%, #10071f 100%);
    }
    .space-background::before { inset: -24% -30%; opacity: 0.35; filter: blur(16px); }
    .space-background::after { inset: 36% -38% -12% 8%; width: 128vw; height: 128vw; opacity: 0.36; }
    .star-depth-far { opacity: 0.24; }
    .star-depth-near { opacity: 0.28; }
    body { padding: 8px; }
    .chat-header { display: block; }
    .chat-header, .runtime-mode-panel, #command-form { padding-left: 14px; padding-right: 14px; }
    .quick-commands { margin-top: 12px; }
    .runtime-mode-title, .operation-console-header, .command-console-header { display: block; }
    .command-console-state { display: inline-block; margin-top: 9px; }
    .dashboard-grid, .mode-options, .micro-scope-grid, .micro-intervention-grid,
    .provider-options, .operation-list, .operation-card-details,
    .command-console-grid, .battlefield-control-grid { grid-template-columns: 1fr; }
    .operation-list { max-height: 640px; }
    .operation-timeline-item { grid-template-columns: 1fr; }
    .operation-timeline-frame, .operation-timeline-selection { text-align: left; }
    .command-stage-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .operation-console { margin: 10px 10px 0; padding: 12px; }
    .operation-summary { display: inline-block; margin-top: 9px; }
    .active-command-console { margin: 10px 10px 0; padding: 13px; }
    .tactical-radio { margin: 0 10px 10px; }
    .tactical-radio-header { align-items: flex-start; }
    .command-console-actions { display: grid; grid-template-columns: 1fr; }
    .command-console-actions button { width: 100%; }
    .command-console-actions .command-emergency-button { margin-left: 0; }
    #log { min-height: 320px; max-height: 58vh; }
    #command-form { flex-direction: column; }
    #voice-button, #send-button { width: 100%; }
    .message { max-width: 94%; }
  }
</style>
</head>
<body>
<div class="space-background" aria-hidden="true"></div>
<div class="star-depth star-depth-far" aria-hidden="true"></div>
<div class="star-depth star-depth-near" aria-hidden="true"></div>
<div class="app-shell">
<nav class="language-switcher" aria-label="Language">
  <button type="button" data-lang-button="ko" class="active">한국어</button>
  <button type="button" data-lang-button="en">English</button>
  <button type="button" data-lang-button="zh">中文</button>
</nav>
<header class="hero">
  <div>
    <p class="eyebrow" data-i18n="eyebrow">Live RTS Command Center</p>
    <h1>__TITLE__</h1>
    <p class="hint" data-i18n="heroHint">대화하듯 명령하고, 우측 대시보드에서 전장 상태를 확인하세요.</p>
  </div>
  <div class="connection-pill" id="connection-status" data-i18n="connectionChecking">SC2 연결 확인 중</div>
</header>
<main>
  <section id="command-panel" aria-label="대화형 명령 채팅">
    <div class="chat-header">
      <div>
        <p class="chat-title" data-i18n="chatTitle">커맨더 채팅</p>
        <p class="chat-subtitle" data-i18n="chatSubtitle">명령, 질문, 상태 확인을 한 창에서 처리합니다.</p>
        <p id="assistant-pending-status" class="assistant-pending-status"></p>
      </div>
      <div class="quick-commands">
        <button type="button" data-command="현재 작전과 병력 상태를 보고해" data-i18n="quickStatus">작전상황</button>
        <button type="button" data-command="마린 1기로 적 본진 정찰해" data-i18n="quickScout">1마린 정찰</button>
        <button type="button" data-command="주력 병력을 편성해서 적진을 공격해" data-i18n="quickScv">주력 공격</button>
        <button type="button" data-command="긴급 전군 후퇴해" data-i18n="quickPosition">전군 후퇴</button>
      </div>
    </div>
    <section id="operation-console"
             class="operation-console"
             aria-labelledby="operation-console-title">
      <div class="operation-console-header">
        <div>
          <h2 id="operation-console-title"
              class="operation-console-title">병렬 전장 작전</h2>
          <p class="operation-console-hint">정찰·공격·수비 작전을 서로 덮어쓰지 않고 실제 실행 증거별로 추적합니다.</p>
        </div>
        <span id="operation-summary"
              class="operation-summary"
              role="status"
              aria-live="polite"
              aria-atomic="true">활성 작전 0개</span>
      </div>
      <div id="operation-list"
           class="operation-list"
           role="group"
           aria-label="MicroMachine 병렬 작전 상태 lane">
        <section class="operation-lane" data-operation-lane="planning"
                 aria-labelledby="operation-lane-planning-title">
          <div class="operation-lane-header">
            <h3 id="operation-lane-planning-title" class="operation-lane-title">해석/편성</h3>
            <span id="operation-lane-planning-count" class="operation-lane-count">0</span>
          </div>
          <div id="operation-lane-planning" class="operation-lane-list"
               role="list" aria-label="해석 및 편성 중인 작전"></div>
        </section>
        <section class="operation-lane" data-operation-lane="executing"
                 aria-labelledby="operation-lane-executing-title">
          <div class="operation-lane-header">
            <h3 id="operation-lane-executing-title" class="operation-lane-title">실행 중</h3>
            <span id="operation-lane-executing-count" class="operation-lane-count">0</span>
          </div>
          <div id="operation-lane-executing" class="operation-lane-list"
               role="list" aria-label="실행 중인 작전"></div>
        </section>
        <section class="operation-lane" data-operation-lane="completed"
                 aria-labelledby="operation-lane-completed-title">
          <div class="operation-lane-header">
            <h3 id="operation-lane-completed-title" class="operation-lane-title">관측 완료</h3>
            <span id="operation-lane-completed-count" class="operation-lane-count">0</span>
          </div>
          <div id="operation-lane-completed" class="operation-lane-list"
               role="list" aria-label="권위 있게 완료된 작전"></div>
        </section>
        <section class="operation-lane" data-operation-lane="waiting"
                 aria-labelledby="operation-lane-waiting-title">
          <div class="operation-lane-header">
            <h3 id="operation-lane-waiting-title" class="operation-lane-title">대기/차단</h3>
            <span id="operation-lane-waiting-count" class="operation-lane-count">0</span>
          </div>
          <div id="operation-lane-waiting" class="operation-lane-list"
               role="list" aria-label="대기 또는 차단된 작전"></div>
        </section>
      </div>
      <section class="operation-timeline-panel"
               aria-labelledby="operation-timeline-title">
        <div class="operation-timeline-header">
          <h3 id="operation-timeline-title">작전 사건 기록</h3>
          <span id="operation-timeline-selection"
                class="operation-timeline-selection">작전을 선택하세요</span>
        </div>
        <ol id="operation-timeline"
            class="operation-timeline"
            role="log"
            aria-live="off"
            aria-label="선택된 작전의 의미 사건 기록"></ol>
      </section>
    </section>
    <section id="active-command-console"
             class="active-command-console command-console-idle"
             aria-labelledby="command-console-title">
      <div class="command-console-header">
        <div>
          <span class="command-console-kicker" data-i18n="commandConsoleKicker">Active field order</span>
          <h2 id="command-console-title" class="command-console-title" data-i18n="commandConsoleIdleTitle">명령을 입력하면 실제 실행 단계가 여기에 표시됩니다.</h2>
        </div>
        <span id="command-console-state"
              class="command-console-state"
              data-i18n="commandConsoleIdleState">명령 대기</span>
        <span id="command-console-announcement"
              class="sr-only"
              role="status"
              aria-live="polite"
              aria-atomic="true"></span>
      </div>
      <div id="command-stage-rail" class="command-stage-rail" role="list">
        <div id="command-stage-interpret" class="command-stage" role="listitem" data-command-stage="interpret" data-i18n="commandStageInterpret">명령 해석</div>
        <div id="command-stage-assign" class="command-stage" role="listitem" data-command-stage="assign" data-i18n="commandStageAssign">유닛 배정</div>
        <div id="command-stage-execute" class="command-stage" role="listitem" data-command-stage="execute" data-i18n="commandStageExecute">SC2 실행</div>
        <div id="command-stage-verify" class="command-stage" role="listitem" data-command-stage="verify" data-i18n="commandStageVerify">효과 확인</div>
      </div>
      <div class="command-console-grid">
        <div class="command-console-field">
          <span data-i18n="commandConsoleIntent">작전 해석</span>
          <strong id="command-console-intent" data-i18n="commandConsoleWaiting">대기 중</strong>
        </div>
        <div class="command-console-field">
          <span data-i18n="commandConsoleUnits">배정된 전력</span>
          <strong id="command-console-units" data-i18n="commandConsoleWaiting">대기 중</strong>
        </div>
        <div class="command-console-field">
          <span data-i18n="commandConsoleAction">실제 명령</span>
          <strong id="command-console-action" data-i18n="commandConsoleWaiting">대기 중</strong>
        </div>
        <div class="command-console-field">
          <span data-i18n="commandConsoleTarget">목표</span>
          <strong id="command-console-target" data-i18n="commandConsoleWaiting">대기 중</strong>
        </div>
        <div class="command-console-field command-console-verification">
          <span data-i18n="commandConsoleVerification">화면에서 확인해야 할 결과</span>
          <strong id="command-console-verification" data-i18n="commandConsoleWaiting">대기 중</strong>
        </div>
      </div>
      <div class="command-console-actions">
        <button id="command-refresh-button" type="button" data-i18n="commandConsoleRefresh">실행 상태 새로고침</button>
        <button id="command-revise-button" type="button" data-i18n="commandConsoleRevise">현재 명령 수정</button>
        <button id="command-retreat-button" class="command-emergency-button" type="button" data-i18n="commandConsoleRetreat">긴급 전군 후퇴</button>
      </div>
      <details class="command-console-technical">
        <summary data-i18n="commandConsoleTechnical">기술 상세 보기</summary>
        <pre id="command-console-technical">{}</pre>
      </details>
    </section>
    <section class="runtime-mode-panel" aria-label="Command runtime mode">
      <p class="runtime-mode-title">
        <span data-i18n="runtimeModeTitle">명령 라우팅 모드</span>
        <span id="runtime-mode-summary" data-i18n="runtimeModeMicroSummary">MicroMachine DSL blackboard가 기본입니다.</span>
      </p>
      <div class="mode-options">
        <label class="mode-option">
          <input type="radio" name="command-mode" value="micromachine" checked>
            <span>
              <span class="mode-label" data-i18n="microModeLabel">MicroMachine policy cockpit</span>
            <span class="mode-description" data-i18n="microModeDescription">채팅/음성은 LLM forced-tool DSL만 사용하며, 구조화 응답 검증에 성공한 명령만 MicroMachine blackboard에 publish됩니다.</span>
            </span>
          </label>
        <label class="mode-option">
          <input type="radio" name="command-mode" value="legacy_commander">
          <span>
            <span class="mode-label" data-i18n="legacyModeLabel">Legacy python-sc2 commander</span>
            <span class="mode-description" data-i18n="legacyModeDescription">이전 데모 호환 모드입니다. MicroMachine이 아니며, LLM 키가 있어야 /api/command로 전송됩니다.</span>
          </span>
        </label>
      </div>
      <p id="legacy-mode-warning" class="legacy-mode-warning" data-i18n="legacyModeWarning">Legacy mode는 MicroMachine이 아닙니다. SC2 실행/명령이 python-sc2 demo 경로로 가므로 MicroMachine QA와 혼동하지 마세요.</p>
      <div id="live-status" data-i18n="runtimeIdleMicro">MicroMachine 런타임 대기 중입니다. 선택 모드 실행을 누르면 SC2/MicroMachine smoke session을 시작합니다.</div>
      <label id="micromachine-enemy-difficulty-control" class="runtime-config" for="micromachine-enemy-difficulty">
        <span data-i18n="microMachineEnemyDifficulty">수동 live-hold 적 난이도 (1..10)</span>
        <input id="micromachine-enemy-difficulty" type="number" min="1" max="10" step="1" value="10">
      </label>
      <div class="runtime-actions">
        <button id="runtime-start-button" type="button" data-i18n="runtimeStartButton">선택 모드 실행</button>
        <button id="live-open-button" type="button" data-i18n="runtimeOpenButton" disabled>Live GUI 열기</button>
        <button id="runtime-refresh-button" type="button" data-i18n="runtimeRefreshButton">런타임 상태 확인</button>
      </div>
    </section>
    <div id="log" aria-live="off" role="log"></div>
    <section id="tactical-radio"
             class="tactical-radio"
             aria-labelledby="tactical-radio-title">
      <div class="tactical-radio-header">
        <div class="tactical-radio-copy">
          <h2 id="tactical-radio-title"
              class="tactical-radio-title"
              data-i18n="tacticalRadioTitle">전술 무전</h2>
          <span id="tactical-radio-status"
                class="tactical-radio-status"
                role="status"
                aria-live="polite"
                aria-atomic="true"
                data-i18n="tacticalRadioReady">음성 준비 · 자막은 항상 표시</span>
        </div>
        <button id="tactical-radio-mute"
                type="button"
                aria-pressed="false"
                data-i18n="tacticalRadioMute">음소거</button>
      </div>
      <ol id="tactical-radio-captions"
          class="tactical-radio-captions"
          role="log"
          aria-live="off"
          aria-relevant="additions text"
          aria-label="전술 무전 자막"
          data-i18n-aria-label="tacticalRadioCaptionsLabel"></ol>
    </section>
    <form id="command-form">
      <input id="command-input" type="text" autocomplete="off" autofocus
             placeholder="대화하듯 입력하세요. 예: 보급고 지어 / 음성지원도 되나?">
      <button type="button" id="voice-button"
              title="음성 입력"
              aria-label="음성 입력"
              aria-pressed="false"
              data-i18n-title="voiceInputLabel"
              data-i18n-aria-label="voiceInputLabel">◉</button>
      <button type="submit" id="send-button" data-i18n="send">전송</button>
    </form>
  </section>
  <aside id="state-panel">
    <section id="battlefield-control-overview" class="battlefield-control-overview">
      <div class="battlefield-control-header">
        <h2 data-i18n="dashboardTitle">전장 통제</h2>
        <span id="battlefield-link-badge" class="battlefield-link-badge" data-i18n="battlefieldLinkWaiting">MicroMachine 대기</span>
      </div>
      <dl class="battlefield-control-grid">
        <div>
          <dt>권위 소스</dt>
          <dd id="battlefield-command-state">대기</dd>
        </div>
        <div>
          <dt data-i18n="battlefieldFrame">전장 프레임</dt>
          <dd id="battlefield-frame">-</dd>
        </div>
        <div>
          <dt>전투 가능 병력</dt>
          <dd id="battlefield-force">-</dd>
        </div>
        <div>
          <dt>소유권 분포</dt>
          <dd id="battlefield-posture">-</dd>
        </div>
        <div>
          <dt>미배정 병력</dt>
          <dd id="battlefield-unassigned">-</dd>
        </div>
        <div>
          <dt>기지 준비도</dt>
          <dd id="battlefield-readiness">-</dd>
        </div>
        <div>
          <dt>이관 가능성</dt>
          <dd id="battlefield-transfer">-</dd>
        </div>
        <div>
          <dt>소유권 무결성</dt>
          <dd id="battlefield-integrity">-</dd>
        </div>
        <div class="wide-card">
          <dt>생산/선행조건 대기</dt>
          <dd id="battlefield-production-waits">-</dd>
        </div>
      </dl>
      <p id="battlefield-control-summary" class="battlefield-control-summary" data-i18n="battlefieldControlWaiting">명령을 입력하면 MicroMachine의 실제 배정·실행·효과 확인 상태를 추적합니다.</p>
    </section>
    <dl class="dashboard-grid">
      <div class="metric-card"><dt data-i18n="minerals">미네랄</dt><dd id="state-minerals">-</dd></div>
      <div class="metric-card"><dt data-i18n="vespene">가스</dt><dd id="state-vespene">-</dd></div>
      <div class="metric-card"><dt data-i18n="supply">보급</dt><dd id="state-supply">-</dd></div>
      <div class="metric-card"><dt data-i18n="workers">일꾼</dt><dd id="state-workers">-</dd></div>
      <div class="metric-card"><dt data-i18n="army">병력</dt><dd id="state-army">-</dd></div>
      <div class="metric-card wide-card"><dt data-i18n="structures">건물</dt><dd id="state-structures">-</dd></div>
    </dl>
    <p id="state-availability"></p>
    <details id="briefing-panel" class="collapsible-panel">
      <summary><span data-i18n="briefingTitle">전략 브리핑</span></summary>
      <div id="strategy-briefing" data-i18n="briefingWaiting">상태 데이터를 기다리는 중입니다.</div>
    </details>
    <details id="llm-panel" class="collapsible-panel">
      <summary><span data-i18n="llmTitle">LLM 설정</span></summary>
      <p class="hint" data-i18n="llmHint">API 키는 이 로컬 프로세스 메모리에만 보관됩니다.</p>
      <form id="llm-form">
        <label data-i18n="llmProviderLabel">모델사 선택</label>
        <div id="llm-provider-options" class="provider-options">
          <label class="provider-option">
            <input type="radio" name="llm-provider-choice" value="openai" onchange="handleProviderChoiceChange('openai')" checked>
            OpenAI / GPT
          </label>
          <label class="provider-option">
            <input type="radio" name="llm-provider-choice" value="myproxy" onchange="handleProviderChoiceChange('myproxy')">
            MyProxy / GPT
          </label>
          <label class="provider-option">
            <input type="radio" name="llm-provider-choice" value="anthropic" onchange="handleProviderChoiceChange('anthropic')">
            Anthropic / Claude
          </label>
          <label class="provider-option">
            <input type="radio" name="llm-provider-choice" value="gemini" onchange="handleProviderChoiceChange('gemini')">
            Google / Gemini
          </label>
          <label class="provider-option">
            <input type="radio" name="llm-provider-choice" value="grok" onchange="handleProviderChoiceChange('grok')">
            xAI / Grok
          </label>
        </div>
        <label for="llm-model-select" data-i18n="llmModelLabel">모델 선택</label>
        <select id="llm-model-select">
          <option value="gpt-5.5">GPT-5.5</option>
          <option value="gpt-4.1-mini">GPT-4.1 Mini</option>
          <option value="gpt-5.4-mini">GPT-5.4 Mini</option>
        </select>
        <label for="llm-api-key">API Key</label>
        <input id="llm-api-key" type="password" autocomplete="off" placeholder="sk-...">
        <button type="submit" data-i18n="saveLlm">로컬 키 설정</button>
      </form>
      <p id="llm-status" class="llm-status llm-status-checking" data-llm-state="checking" aria-live="polite">
        <span id="llm-status-label" class="llm-status-label">상태 확인</span>
        <span id="llm-status-message" class="llm-status-message">LLM 키 상태를 확인 중입니다.</span>
      </p>
    </details>
    <details id="micromachine-panel" class="collapsible-panel">
      <summary><span data-i18n="microMachineTitle">MicroMachine runtime / DSL evidence</span></summary>
      <p class="hint" data-i18n="microMachineHint">기본 입력은 왼쪽 커맨더 채팅/음성입니다. 이 패널은 그 입력이 publish될 blackboard, semantic scope, telemetry 소비 증거를 확인하는 runtime/debug control입니다. SC2 화면/키보드 자동화나 raw unit 명령은 쓰지 않습니다.</p>
      <form id="micromachine-form">
        <label for="micromachine-blackboard-dir" data-i18n="microMachineBlackboardLabel">Blackboard directory</label>
        <input id="micromachine-blackboard-dir" type="text" value="__MICROMACHINE_BLACKBOARD_DIR__">
        <label for="micromachine-command-input" data-i18n="microMachineCommandLabel">고급 직접 publish 테스트 텍스트</label>
        <input id="micromachine-command-input" type="text" autocomplete="off" placeholder="보통은 왼쪽 커맨더 채팅에 입력하세요. 예: 탱크 중심으로 안전하게 버텨">
        <div class="micro-scope-grid" aria-label="MicroMachine semantic scope controls">
          <div>
            <label for="micromachine-army-group" data-i18n="microMachineArmyGroup">Semantic army group</label>
            <select id="micromachine-army-group">
              <option value="">auto</option>
              <option value="main">main</option>
              <option value="harass">harass</option>
              <option value="defense">defense</option>
              <option value="scout">scout</option>
              <option value="air">air</option>
              <option value="bio">bio</option>
              <option value="mech">mech</option>
              <option value="siege">siege</option>
              <option value="workers">workers</option>
            </select>
          </div>
          <div>
            <label for="micromachine-location-intent" data-i18n="microMachineLocationIntent">Location intent</label>
            <select id="micromachine-location-intent">
              <option value="">auto</option>
              <option value="home">home</option>
              <option value="natural">natural</option>
              <option value="enemy_main">enemy_main</option>
              <option value="enemy_natural">enemy_natural</option>
              <option value="enemy_third">enemy_third</option>
              <option value="watchtower">watchtower</option>
              <option value="ramp">ramp</option>
              <option value="last_seen_enemy_army">last_seen_enemy_army</option>
            </select>
          </div>
          <div>
            <label for="micromachine-unit-classes" data-i18n="microMachineUnitClasses">Unit classes</label>
            <input id="micromachine-unit-classes" type="text" autocomplete="off" placeholder="marine, siege_tank, medivac">
          </div>
          <div>
            <label for="micromachine-safety-margin" data-i18n="microMachineSafetyMargin">Safety margin</label>
            <input id="micromachine-safety-margin" type="number" min="0" max="1" step="0.05" placeholder="0.15">
          </div>
          <div>
            <label for="micromachine-duration-seconds" data-i18n="microMachineDuration">Scope duration seconds</label>
            <input id="micromachine-duration-seconds" type="number" min="0" max="900" step="1" placeholder="120">
          </div>
          <div>
            <label for="micromachine-ttl-seconds" data-i18n="microMachineTtl">TTL seconds</label>
            <input id="micromachine-ttl-seconds" type="number" min="1" max="900" step="1" value="600" placeholder="600">
          </div>
        </div>
        <button type="submit" data-i18n="microMachineSend">고급 직접 publish 전송</button>
      </form>
      <div id="micromachine-status" aria-live="off">왼쪽 커맨더 채팅 또는 고급 직접 publish 입력을 기다리는 중입니다.</div>
      <section id="micromachine-intervention-dashboard">
        <div class="micro-intervention-header">
          <strong data-i18n="microMachineDashboardTitle">DSL intervention dashboard</strong>
          <span id="micromachine-applied-badge" class="micro-badge micro-badge-pending" data-i18n="microMachinePending">텔레메트리 대기</span>
        </div>
        <dl class="micro-intervention-grid">
          <div>
            <dt data-i18n="microMachineLatestUpdate">Latest update</dt>
            <dd id="micromachine-latest-update">-</dd>
          </div>
          <div>
            <dt data-i18n="microMachineActiveIds">Active ids in MicroMachine</dt>
            <dd id="micromachine-active-ids">-</dd>
          </div>
          <div>
            <dt data-i18n="microMachineFrame">Telemetry frame</dt>
            <dd id="micromachine-frame">-</dd>
          </div>
          <div>
            <dt data-i18n="microMachineDomains">Bias domains</dt>
            <dd id="micromachine-domains">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineGoal">Compiled DSL goal</dt>
            <dd id="micromachine-goal">-</dd>
          </div>
          <div>
            <dt data-i18n="microMachineStrategyMode">Strategy mode / play style</dt>
            <dd id="micromachine-strategy-mode">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineManagers">Manager evidence</dt>
            <dd id="micromachine-managers">-</dd>
          </div>
          <div>
            <dt data-i18n="microMachinePosture">Tactical posture</dt>
            <dd id="micromachine-posture">-</dd>
          </div>
          <div>
            <dt data-i18n="microMachineScope">Semantic scope</dt>
            <dd id="micromachine-scope">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineConsumedAxes">Consumed axes by manager</dt>
            <dd id="micromachine-consumed-axes">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineTargetPriority">Target priority</dt>
            <dd id="micromachine-target-priority">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineAttackGate">Attack gate</dt>
            <dd id="micromachine-attack-gate">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineTacticalEvidence">Tactical effect evidence</dt>
            <dd id="micromachine-tactical-evidence">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineCommandExecution">Command execution</dt>
            <dd id="micromachine-command-execution">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineRefusalReason">Refusal / clarification</dt>
            <dd id="micromachine-refusal">-</dd>
          </div>
          <div class="wide-card">
            <dt data-i18n="microMachineTacticalLogs">Recent tactical logs</dt>
            <dd><ul id="micromachine-log-snippets"></ul></dd>
          </div>
        </dl>
        <details class="micro-json-panel">
          <summary data-i18n="microMachineRawEvidence">Raw modulation / telemetry evidence</summary>
          <pre id="micromachine-raw-evidence">{}</pre>
        </details>
      </section>
    </details>
  </aside>
</main>
</div>
<script>
"use strict";
var POLL_INTERVAL_MS = __POLL_MS__;
var token = new URLSearchParams(window.location.search).get("token") || "";
var authQuery = token ? "?token=" + encodeURIComponent(token) : "";
var authJoin = token ? "&token=" + encodeURIComponent(token) : "";
var lastSeq = 0;
var lastEventSeq = 0;
var commandEventBlackboardDir = "";
var commandEventSource = null;
var commandEventReconnectTimer = null;
var commandEventHealthy = false;
var commandEventAwaitingInitialSnapshot = false;
var commandEventPollWonInitialHydration = false;
var commandEventFailedSources = {};
var fallbackPollingIntervals = [];
var logBox = document.getElementById("log");
var currentLang = "ko";
var llmConfigured = false;
var llmSetupAttemptSeq = 0;
var activeLlmSetupAttemptSeq = 0;
var MAX_CHAT_EVENTS = 36;
var COMPACT_AFTER_EVENTS = 28;
var COMPACT_KEEP_EVENTS = 24;
var MAX_MESSAGE_PREVIEW_CHARS = 280;
var MICROMACHINE_CHAT_TIMEOUT_MS = 35000;
var MICROMACHINE_ASYNC_PENDING_TIMEOUT_MS = 120000;
var MICROMACHINE_STATUS_POLL_TIMEOUT_MS = 12000;
var OPERATION_PENDING_RECORD_TIMEOUT_MS = 120000;
var OPERATION_RECORD_MAXIMUM = 24;
var TACTICAL_RADIO_MAX_QUEUE = 8;
var TACTICAL_RADIO_MAX_CAPTION_HISTORY = 20;
var TACTICAL_RADIO_MAX_SPEECH_CHARS = 180;
var TACTICAL_RADIO_MAX_PLAN_OPERATIONS = 3;
var TACTICAL_RADIO_MAX_PLAN_IDENTITIES = 256;
var TACTICAL_RADIO_MAX_OPERATION_HIGH_WATER = 256;
var VOICE_FINALIZATION_GRACE_MS = 350;
var TACTICAL_RADIO_PRIORITY_INTERVAL_MS = {
  0: 0,
  1: 1200,
  2: 3500,
  3: 0
};
var TACTICAL_RADIO_DEDUPE_TTL_MS = {
  0: 10000,
  1: 20000,
  2: 30000,
  3: 15000
};
var TACTICAL_RADIO_REPLAY_MAX_AGE_MS = {
  0: 8000,
  1: 15000,
  2: 12000,
  3: 15000
};
var trimmedChatEvents = 0;
var recentEvents = [];
var archivedChatEvents = [];
var pendingMicroMachineAsyncUpdates = {};
var deferredPendingMicroMachineTransfers = {};
var knownPendingMicroMachineUpdateKeys = {};
var consumedMicroMachineResultIdsByScope = {};
var microMachinePollRequestSeq = 0;
var microMachinePollAppliedSeq = 0;
var microMachinePollBlackboardDir = null;
var microMachineBlackboardContextGeneration = 0;
var microMachineCommandAnnouncementSeq = 0;
var microMachineSubmissionSeq = 0;
var microMachineClientUpdateSeq = 0;
var operationRecordSeq = 0;
var microMachinePollInFlight = false;
var microMachinePollQueued = false;
var microMachinePollActiveRequestSeq = 0;
var microMachinePollAbortController = null;
var microMachinePollTimeoutId = null;
var compactedContext = {
  total: 0,
  successful: 0,
  failed: 0,
  readOnly: 0,
  commands: [],
  successfulThemes: {},
  failedThemes: {},
  failureReasons: {},
  lastNarration: ""
};
var pendingCommandSeq = 0;
var pendingNodes = {};
var pendingAggregateId = "pending-aggregate";
var pendingAggregateNode = null;
var latestMicroMachinePlanText = "";
var operationRecords = {};
var operationRecordOrder = [];
var operationConsoleScopeId = "";
var operationConsoleSessionEpoch = "";
var operationConsoleRetiredSessionEpochs = [];
var selectedOperationKey = "";
var activeCommandConsoleRecord = {
  pendingId: "",
  scopeId: "",
  sessionEpoch: "",
  updateId: "",
  operationId: "",
  operationGeneration: 0,
  text: "",
  state: "idle",
  data: null,
  startedAt: 0,
  stageRank: 0,
  telemetryFrame: -1,
  observationTimedOut: false,
  submissionDelayed: false,
  announcementOrdinal: 0
};
var latestState = null;
var briefingAdviceToggleEnabled = false;
var recognition = null;
var isRecording = false;
var voiceSessionSeq = 0;
var voiceRecognitionRequestSeq = 0;
var pendingVoiceRecognitionRequest = null;
var activeVoiceSession = null;
var voiceSessionsByPendingId = {};
var tacticalRadio = {
  muted: false,
  supported: Boolean(
    window.speechSynthesis &&
    typeof window.SpeechSynthesisUtterance === "function"
  ),
  speaking: false,
  current: null,
  queue: [],
  captions: [],
  dedupe: {},
  planAnnouncements: {},
  planAnnouncementOrder: [],
  frameHighWater: {},
  timelineHighWater: {},
  operationHighWaterOrder: [],
  scopeId: "",
  sessionEpoch: "",
  speechToken: 0,
  timerId: null,
  lastSpokenAt: { 0: 0, 1: 0, 2: 0 }
};
var liveGuiUrl = "";
var COMMAND_MODE_MICROMACHINE = "__COMMAND_MODE_MICROMACHINE__";
var COMMAND_MODE_LEGACY_COMMANDER = "__COMMAND_MODE_LEGACY_COMMANDER__";
var activeCommandMode = COMMAND_MODE_MICROMACHINE;
var LLM_MODELS = {
  myproxy: [
    { value: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
    { value: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
    { value: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
    { value: "gpt-5.5", label: "GPT-5.5" }
  ],
  openai: [
    { value: "gpt-5.5", label: "GPT-5.5" },
    { value: "gpt-4.1-mini", label: "GPT-4.1 Mini" },
    { value: "gpt-5.5-chat-latest", label: "GPT-5.5 Chat Latest" },
    { value: "gpt-5.4", label: "GPT-5.4" },
    { value: "gpt-5.4-mini", label: "GPT-5.4 Mini" },
    { value: "gpt-5.4-nano", label: "GPT-5.4 Nano" },
    { value: "gpt-5.1", label: "GPT-5.1" },
    { value: "gpt-5.1-mini", label: "GPT-5.1 Mini" },
    { value: "gpt-4.1", label: "GPT-4.1" },
    { value: "gpt-4.1-nano", label: "GPT-4.1 Nano" },
    { value: "gpt-4o", label: "GPT-4o" },
    { value: "gpt-4o-mini", label: "GPT-4o Mini" }
  ],
  anthropic: [
    { value: "claude-fable-4-5-20251001", label: "Claude Fable 4.5" },
    { value: "claude-mythos-4-5-20251001", label: "Claude Mythos 4.5" },
    { value: "claude-opus-4-8-20251201", label: "Claude Opus 4.8" },
    { value: "claude-sonnet-4-6-20251120", label: "Claude Sonnet 4.6" },
    { value: "claude-opus-4-5-20251101", label: "Claude Opus 4.5" },
    { value: "claude-sonnet-4-5-20250929", label: "Claude Sonnet 4.5" },
    { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
    { value: "claude-3-7-sonnet-latest", label: "Claude 3.7 Sonnet" }
  ],
  gemini: [
    { value: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
    { value: "gemini-3.1-pro", label: "Gemini 3.1 Pro" },
    { value: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash-Lite" },
    { value: "gemini-3-flash", label: "Gemini 3 Flash" },
    { value: "gemini-3-pro-preview", label: "Gemini 3 Pro Preview" },
    { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
    { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
    { value: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash-Lite" }
  ],
  grok: [
    { value: "grok-4.3", label: "Grok 4.3" },
    { value: "grok-4.3-fast", label: "Grok 4.3 Fast" },
    { value: "grok-build-0.1", label: "Grok Build 0.1" },
    { value: "grok-4.1-fast", label: "Grok 4.1 Fast" },
    { value: "grok-2-vision-1212", label: "Grok 2 Vision" }
  ]
};

var MAX_OPTIONAL_STRATEGIC_EVIDENCE_CHARS = 520;
var MAX_OPTIONAL_STRATEGIC_EVIDENCE_LINES = 4;
var MAX_STRATEGIC_EVIDENCE_LINE_CHARS = 220;

var I18N = {
  ko: {
    eyebrow: "Live RTS Command Center",
    heroHint: "대화하듯 명령하고, 우측 대시보드에서 전장 상태를 확인하세요.",
    connectionChecking: "SC2 연결 확인 중",
    connectionWaiting: "SC2 상태 대기 중",
    connectionReady: "SC2 연결됨",
    chatTitle: "커맨더 채팅",
    chatSubtitle: "명령, 질문, 상태 확인을 한 창에서 처리합니다.",
    runtimeModeTitle: "명령 라우팅 모드",
    runtimeModeMicroSummary: "MicroMachine DSL blackboard가 기본입니다.",
    runtimeModeLegacySummary: "Legacy python-sc2 commander compatibility mode입니다.",
    microModeLabel: "MicroMachine policy cockpit",
    microModeDescription: "채팅/음성은 LLM forced-tool DSL만 사용하며, 구조화 응답 검증에 성공한 명령만 MicroMachine blackboard에 publish됩니다.",
    legacyModeLabel: "Legacy python-sc2 commander",
    legacyModeDescription: "이전 데모 호환 모드입니다. MicroMachine이 아니며, LLM 키가 있어야 /api/command로 전송됩니다.",
    legacyModeWarning: "Legacy mode는 MicroMachine이 아닙니다. SC2 실행/명령이 python-sc2 demo 경로로 가므로 MicroMachine QA와 혼동하지 마세요.",
    runtimeIdleMicro: "MicroMachine 런타임 대기 중입니다. 선택 모드 실행을 누르면 SC2/MicroMachine smoke session을 시작합니다.",
    runtimeIdleLegacy: "Legacy python-sc2 런타임 대기 중입니다. 키 설정 후 선택 모드 실행을 누르면 legacy demo를 시작합니다.",
    runtimeStartButton: "선택 모드 실행",
    runtimeOpenButton: "Live GUI 열기",
    runtimeRefreshButton: "런타임 상태 확인",
    microMachineEnemyDifficulty: "수동 live-hold 적 난이도 (1..10)",
    runtimeStarting: "선택한 런타임 시작 중",
    runtimeRunning: "선택한 런타임 실행 중",
    runtimeConnected: "MicroMachine telemetry 연결됨",
    runtimePassed: "MicroMachine smoke 통과",
    runtimeDetachedTelemetry: "MicroMachine telemetry 파일은 있지만 현재 런타임 프로세스에 붙어 있지 않음",
    runtimeReady: "Legacy live GUI 준비됨",
    runtimeBlocked: "런타임 시작 보류",
    runtimeFailed: "런타임 시작 실패",
    quickStatus: "작전상황",
    quickScout: "1마린 정찰",
    quickScv: "주력 공격",
    quickPosition: "전군 후퇴",
    send: "전송",
    dashboardTitle: "전장 통제",
    battlefieldLinkWaiting: "MicroMachine 대기",
    battlefieldLinkConnected: "전장 링크 연결",
    battlefieldCommandState: "현재 명령",
    battlefieldFrame: "전장 프레임",
    battlefieldForce: "통제 병력",
    battlefieldPosture: "현재 자세",
    battlefieldControlWaiting: "명령을 입력하면 MicroMachine의 실제 배정·실행·효과 확인 상태를 추적합니다.",
    commandConsoleKicker: "Active field order",
    commandConsoleIdleTitle: "명령을 입력하면 실제 실행 단계가 여기에 표시됩니다.",
    commandConsoleIdleState: "명령 대기",
    commandConsoleWaiting: "대기 중",
    commandConsoleIntent: "작전 해석",
    commandConsoleUnits: "배정된 전력",
    commandConsoleAction: "실제 명령",
    commandConsoleTarget: "목표",
    commandConsoleVerification: "화면에서 확인해야 할 결과",
    commandConsoleRefresh: "실행 상태 새로고침",
    commandConsoleRevise: "현재 명령 수정",
    commandConsoleRetreat: "긴급 전군 후퇴",
    commandConsoleTechnical: "기술 상세 보기",
    commandStageInterpret: "명령 해석",
    commandStageAssign: "유닛 배정",
    commandStageExecute: "SC2 실행",
    commandStageVerify: "효과 확인",
    minerals: "미네랄",
    vespene: "가스",
    supply: "보급",
    workers: "일꾼",
    army: "병력",
    structures: "건물",
    noState: "게임 상태를 아직 읽을 수 없습니다.",
    microMachineStateDashboardDisabled: "MicroMachine 모드에서는 레거시 전장 대시보드를 폴링하지 않습니다. 실제 소비 증거는 MicroMachine DSL 개입 대시보드를 보세요.",
    microMachineStateConnection: "MicroMachine cockpit · legacy /api/state 비활성",
    microMachineStateBriefing: "MicroMachine 모드는 dry-run 전장 자원값을 표시하지 않습니다. blackboard/telemetry evidence가 기준입니다.",
    noStructures: "없음",
    incompleteObservation: "관측이 불완전합니다.",
    briefingTitle: "전략 브리핑",
    briefingWaiting: "상태 데이터를 기다리는 중입니다.",
    briefingCurrentStrategy: "현재 전략",
    briefingEvidence: "판단 근거",
    briefingProgress: "진행 상황",
    briefingRisk: "리스크",
    briefingMemory: "압축 메모리",
    briefingAdvice: "추천 보기",
    strategyOpening: "아직 명령 기록이 부족합니다. 현재는 전장 상태 파악 단계입니다.",
    strategyEconomy: "경제와 생산 기반을 안정화하는 전략을 펼치고 있습니다.",
    strategyProduction: "테란 생산 인프라를 확보하는 전략을 펼치고 있습니다.",
    strategyScout: "정보 우위를 확보하기 위해 정찰 중심 운영을 펼치고 있습니다.",
    strategyDefense: "본진 방어와 생존을 우선하는 전략을 펼치고 있습니다.",
    progressRecent: "최근 명령",
    compactedNone: "아직 압축된 이전 맥락은 없습니다.",
    compactedSummary: "이전 대화/명령 {total}건 압축됨. 성공/정보 {successful}건, 차단/확인필요 {failed}건.",
    riskNoArmy: "방어 병력이 없어 초반 공격에 취약합니다.",
    riskNoScout: "적 정보가 부족합니다.",
    riskSupply: "보급 여유가 낮습니다.",
    riskStable: "즉시 위험 신호는 크지 않습니다.",
    briefingEconomy: "경제",
    briefingSupply: "보급",
    briefingForces: "전력",
    briefingEnemy: "적 관측",
    briefingEnemyNone: "발견된 적 없음",
    briefingSuggestionSupply: "보급 여유가 낮습니다. 보급고를 준비하세요.",
    briefingSuggestionScout: "적 정보가 없습니다. 정찰 명령을 고려하세요.",
    briefingSuggestionArmy: "병력이 없습니다. 병영 이후 마린 생산을 준비하세요.",
    briefingSuggestionStable: "즉시 위험 신호는 없습니다. 경제와 생산을 유지하세요.",
    chatTrimmed: "이전 대화 일부 생략",
    chatArchiveOpen: "전체 보기",
    messageExpand: "전체 내용 보기",
    assistantThinking: "응답 하는중",
    assistantWaiting: "LLM 응답을 기다리는 중",
    assistantPendingCount: "대기 중인 응답 {count}개",
    voiceListening: "녹음중",
    voiceFinalizing: "음성 확정 중",
    voiceTranscriptUnavailable: "음성 transcript를 사용할 수 없습니다.",
    voiceInputLabel: "음성 입력",
    voiceStopLabel: "녹음 중지",
    voiceUnsupported: "이 브라우저는 음성 인식을 지원하지 않습니다.",
    voiceNoResult: "음성이 인식되지 않았습니다.",
    tacticalRadioTitle: "전술 무전",
    tacticalRadioCaptionsLabel: "전술 무전 자막",
    tacticalRadioReady: "음성 준비 · 자막은 항상 표시",
    tacticalRadioSpeaking: "전술 무전 재생 중 · 자막 활성",
    tacticalRadioMuted: "음소거됨 · 자막은 계속 활성",
    tacticalRadioUnavailable: "이 브라우저는 음성 출력을 지원하지 않음 · 자막 활성",
    tacticalRadioMute: "음소거",
    tacticalRadioUnmute: "음성 켜기",
    tacticalPlanConfirmed: "계획 확인",
    tacticalOperationIdentity: "작전",
    tacticalForceAssigned: "병력 배정",
    tacticalForcePartiallyAssigned: "부분 편성",
    tacticalMoving: "이동 시작",
    tacticalEngaged: "교전 시작",
    tacticalTargetReached: "목표 도달",
    tacticalCompleted: "작전 완료",
    tacticalBlocked: "작전 차단",
    tacticalRouteUnavailable: "경로 불가",
    tacticalEmergencyRetreat: "긴급 후퇴 시작",
    tacticalBaseAttack: "본진 공격 감지",
    tacticalCriticalAbilityFailure: "중요 능력 사용 실패",
    tacticalForceLoss: "핵심 병력 손실",
    tacticalSubmittedCaption: "SC2 명령 제출 확인",
    workerUnit: "기",
    idleLabel: "유휴",
    llmTitle: "LLM 설정",
    llmHint: "API 키는 이 로컬 프로세스 메모리에만 보관됩니다.",
    llmProviderLabel: "모델사 선택",
    llmModelLabel: "모델 선택",
    llmCheckingLabel: "상태 확인",
    llmSettingLabel: "설정 중",
    llmSuccessLabel: "설정 완료",
    llmFailedLabel: "설정 실패",
    llmRequiredLabel: "설정 필요",
    llmChecking: "LLM 키 상태를 확인 중입니다.",
    llmCheckingFailed: "LLM 키 상태 확인 실패",
    llmSaving: "LLM 키 설정 중...",
    liveStarting: "선택한 런타임 시작 중...",
    liveReady: "선택한 런타임 준비됨",
    liveFailed: "런타임 시작 실패",
    liveIdle: "선택한 런타임 대기 중입니다.",
    legacyLiveDisabled: "선택한 런타임이 아직 시작되지 않았습니다.",
    liveOpenButton: "Live GUI 열기",
    liveRefreshButton: "런타임 상태 확인",
    microMachineTitle: "MicroMachine runtime / DSL evidence",
    microMachineHint: "기본 입력은 왼쪽 커맨더 채팅/음성입니다. 이 패널은 그 입력이 publish될 blackboard, semantic scope, telemetry 소비 증거를 확인하는 runtime/debug control입니다. SC2 화면/키보드 자동화나 raw unit 명령은 쓰지 않습니다.",
    microMachineBlackboardLabel: "Blackboard directory",
    microMachineCommandLabel: "고급 직접 publish 테스트 텍스트",
    microMachineArmyGroup: "Semantic army group",
    microMachineLocationIntent: "Location intent",
    microMachineUnitClasses: "Unit classes",
    microMachineSafetyMargin: "Safety margin",
    microMachineDuration: "Scope duration seconds",
    microMachineTtl: "TTL seconds",
    microMachineSend: "고급 직접 publish 전송",
    microMachineSending: "MicroMachine DSL publish 전송 중...",
    microMachinePublished: "게시됨",
    microMachineConsumed: "소비 확인",
    microMachinePending: "텔레메트리 대기",
    microMachineDashboardTitle: "DSL 개입 대시보드",
    microMachineLatestUpdate: "최신 update",
    microMachineActiveIds: "MicroMachine active id",
    microMachineFrame: "Telemetry frame",
    microMachineDomains: "Bias domain",
    microMachineGoal: "컴파일된 DSL goal",
    microMachineStrategyMode: "전략 모드 / 플레이 스타일",
    microMachineManagers: "Manager 증거",
    microMachinePosture: "전술 posture",
    microMachineScope: "Semantic scope",
    microMachineConsumedAxes: "Manager별 consumed axes",
    microMachineTargetPriority: "Target priority",
    microMachineAttackGate: "공격 게이트",
    microMachineTacticalEvidence: "전술 효과 증거",
    microMachineCommandExecution: "명령 실행 상태",
    microMachineRefusalReason: "거부 / 추가 확인",
    microMachineTacticalLogs: "최근 MicroMachine 전술 로그",
    microMachineRawEvidence: "Raw modulation / telemetry 증거",
    microMachineRefused: "거부됨",
    microMachineClarification: "추가 확인 필요",
    microMachineFailed: "게시 실패",
    llmReady: "LLM 키 설정됨",
    llmMissing: "LLM 필수: Legacy commander 명령은 API 키를 먼저 설정해야 보낼 수 있습니다.",
    llmOptionalMicro: "MicroMachine mode: production 채팅/음성 publish에는 LLM 키가 필요합니다. Keyword DSL은 명시 smoke/test 모드에서만 허용됩니다.",
    llmEnterKey: "API 키를 입력하세요.",
    llmSaveFailed: "LLM 키 설정 요청에 실패했습니다.",
    userLabel: "사용자",
    commanderLabel: "커맨더",
    commandPlaceholderMicro: "MicroMachine 의도를 입력하세요. 예: enemy natural 압박 / 탱크는 수비적으로 / worker line harass",
    commandPlaceholderLegacy: "Legacy python-sc2 명령. 예: 보급고 지어 / 정찰보내",
    commandPlaceholderReady: "대화하듯 입력하세요. 예: 보급고 지어 / 정찰보내",
    commandPlaceholderLocked: "LLM 키 설정 후 명령 입력이 활성화됩니다.",
    commandRejected: "LLM 키가 설정되지 않아 명령을 보내지 않았습니다.",
    microMachineChatPublished: "MicroMachine DSL modulation을 blackboard에 publish했습니다.",
    microMachineChatQueued: "MicroMachine telemetry 소비 대기 중입니다.",
    microMachineChatRefused: "MicroMachine DSL 요청이 거부되거나 추가 확인이 필요합니다.",
    microMachineChatFailed: "MicroMachine DSL publish 실패",
    saveLlm: "로컬 키 설정",
    startupGuide: "🚀 시작 메뉴얼\\n1. 기본 모드는 MicroMachine policy cockpit입니다. 채팅/음성 입력은 LLM forced-tool DSL로 blackboard에 publish됩니다.\\n2. LLM이 tool-call/JSON 계약을 충족하지 못하면 명령은 publish되지 않고 실패 상태가 표시됩니다.\\n3. 우측 MicroMachine 패널에서 blackboard directory와 semantic scope를 확인하거나 조정하세요.\\n4. Legacy python-sc2 commander는 호환 모드로 직접 선택한 경우에만 /api/command를 사용합니다.\\n🎙️ 음성 버튼을 켜면 말한 내용이 현재 선택된 모드로 전송됩니다."
  },
  en: {
    eyebrow: "Live RTS Command Center",
    heroHint: "Command conversationally and monitor the battlefield dashboard.",
    connectionChecking: "Checking SC2 link",
    connectionWaiting: "Waiting for SC2 state",
    connectionReady: "SC2 connected",
    chatTitle: "Commander Chat",
    chatSubtitle: "Orders, questions, and status reports in one cockpit.",
    runtimeModeTitle: "Command routing mode",
    runtimeModeMicroSummary: "MicroMachine DSL blackboard is the default.",
    runtimeModeLegacySummary: "Legacy python-sc2 commander compatibility mode.",
    microModeLabel: "MicroMachine policy cockpit",
    microModeDescription: "Chat/voice uses LLM forced-tool DSL only and publishes only structurally validated commands to the MicroMachine blackboard.",
    legacyModeLabel: "Legacy python-sc2 commander",
    legacyModeDescription: "Compatibility mode for the older demo path. It is not MicroMachine and requires an LLM key before posting to /api/command.",
    legacyModeWarning: "Legacy mode is not MicroMachine. SC2 launch/commands go through the python-sc2 demo path, so do not use it as MicroMachine QA evidence.",
    runtimeIdleMicro: "MicroMachine runtime is idle. Click Launch selected runtime to start the SC2/MicroMachine smoke session.",
    runtimeIdleLegacy: "Legacy python-sc2 runtime is idle. Configure a key, then click Launch selected runtime to start the legacy demo.",
    runtimeStartButton: "Launch selected runtime",
    runtimeOpenButton: "Open Live GUI",
    runtimeRefreshButton: "Check runtime status",
    microMachineEnemyDifficulty: "Manual live-hold enemy difficulty (1..10)",
    runtimeStarting: "Starting selected runtime",
    runtimeRunning: "Selected runtime is running",
    runtimeConnected: "MicroMachine telemetry connected",
    runtimePassed: "MicroMachine smoke passed",
    runtimeDetachedTelemetry: "MicroMachine telemetry file exists but is not attached to a running runtime",
    runtimeReady: "Legacy live GUI ready",
    runtimeBlocked: "Runtime start blocked",
    runtimeFailed: "Runtime start failed",
    quickStatus: "Battle report",
    quickScout: "1-Marine scout",
    quickScv: "Main attack",
    quickPosition: "Full retreat",
    send: "Send",
    dashboardTitle: "Battlefield Control",
    battlefieldLinkWaiting: "Waiting for MicroMachine",
    battlefieldLinkConnected: "Battlefield link online",
    battlefieldCommandState: "Current order",
    battlefieldFrame: "Battlefield frame",
    battlefieldForce: "Controlled force",
    battlefieldPosture: "Current posture",
    battlefieldControlWaiting: "Issue an order to track actual MicroMachine assignment, SC2 action, and observed effect.",
    commandConsoleKicker: "Active field order",
    commandConsoleIdleTitle: "Issue an order to see each real execution stage here.",
    commandConsoleIdleState: "Waiting for order",
    commandConsoleWaiting: "Waiting",
    commandConsoleIntent: "Interpreted operation",
    commandConsoleUnits: "Assigned force",
    commandConsoleAction: "Actual command",
    commandConsoleTarget: "Target",
    commandConsoleVerification: "Result to verify on screen",
    commandConsoleRefresh: "Refresh execution",
    commandConsoleRevise: "Revise current order",
    commandConsoleRetreat: "Emergency retreat",
    commandConsoleTechnical: "Show technical details",
    commandStageInterpret: "Interpret",
    commandStageAssign: "Assign units",
    commandStageExecute: "Execute in SC2",
    commandStageVerify: "Verify effect",
    minerals: "Minerals",
    vespene: "Vespene",
    supply: "Supply",
    workers: "Workers",
    army: "Army",
    structures: "Structures",
    noState: "Game state is not available yet.",
    microMachineStateDashboardDisabled: "MicroMachine mode does not poll the legacy battlefield dashboard. Use the MicroMachine DSL intervention dashboard for actual consumption evidence.",
    microMachineStateConnection: "MicroMachine cockpit · legacy /api/state disabled",
    microMachineStateBriefing: "MicroMachine mode does not display dry-run battlefield resources. Blackboard/telemetry evidence is authoritative.",
    noStructures: "None",
    incompleteObservation: "Observation is incomplete.",
    briefingTitle: "Strategy Briefing",
    briefingWaiting: "Waiting for state data.",
    briefingCurrentStrategy: "Current Strategy",
    briefingEvidence: "Evidence",
    briefingProgress: "Progress",
    briefingRisk: "Risk",
    briefingMemory: "Compacted Memory",
    briefingAdvice: "Show Advice",
    strategyOpening: "Not enough command history yet. Current mode is battlefield assessment.",
    strategyEconomy: "You are stabilizing economy and production foundations.",
    strategyProduction: "You are building Terran production infrastructure.",
    strategyScout: "You are playing for information advantage through scouting.",
    strategyDefense: "You are prioritizing main-base defense and survival.",
    progressRecent: "Recent commands",
    compactedNone: "No older context has been compacted yet.",
    compactedSummary: "{total} older command/chat events compacted. Successful/info {successful}, blocked/needs-clarification {failed}.",
    riskNoArmy: "No army is available, making early pressure dangerous.",
    riskNoScout: "Enemy information is limited.",
    riskSupply: "Supply buffer is low.",
    riskStable: "No major immediate risk signal.",
    briefingEconomy: "Economy",
    briefingSupply: "Supply",
    briefingForces: "Forces",
    briefingEnemy: "Enemy intel",
    briefingEnemyNone: "No enemy spotted",
    briefingSuggestionSupply: "Supply is tight. Prepare another depot.",
    briefingSuggestionScout: "Enemy intel is empty. Consider scouting.",
    briefingSuggestionArmy: "You have no army. Prepare Marine production after Barracks.",
    briefingSuggestionStable: "No immediate risk signal. Keep economy and production running.",
    chatTrimmed: "Older chat omitted",
    chatArchiveOpen: "View full archive",
    messageExpand: "Show full message",
    assistantThinking: "Thinking",
    assistantWaiting: "Waiting for LLM response",
    assistantPendingCount: "{count} response(s) pending",
    voiceListening: "Recording",
    voiceFinalizing: "Finalizing speech",
    voiceTranscriptUnavailable: "Voice transcript unavailable.",
    voiceInputLabel: "Voice input",
    voiceStopLabel: "Stop recording",
    voiceUnsupported: "This browser does not support speech recognition.",
    voiceNoResult: "No speech was recognized.",
    tacticalRadioTitle: "Tactical Radio",
    tacticalRadioCaptionsLabel: "Tactical radio captions",
    tacticalRadioReady: "Audio ready · captions always active",
    tacticalRadioSpeaking: "Tactical radio speaking · captions active",
    tacticalRadioMuted: "Muted · captions remain active",
    tacticalRadioUnavailable: "Audio unavailable · captions remain active",
    tacticalRadioMute: "Mute",
    tacticalRadioUnmute: "Unmute",
    tacticalPlanConfirmed: "Plan confirmed",
    tacticalOperationIdentity: "Operation",
    tacticalForceAssigned: "Force assigned",
    tacticalForcePartiallyAssigned: "Force partially assigned",
    tacticalMoving: "Movement started",
    tacticalEngaged: "Engagement started",
    tacticalTargetReached: "Target reached",
    tacticalCompleted: "Operation completed",
    tacticalBlocked: "Operation blocked",
    tacticalRouteUnavailable: "Route unavailable",
    tacticalEmergencyRetreat: "Emergency retreat started",
    tacticalBaseAttack: "Base under attack",
    tacticalCriticalAbilityFailure: "Critical ability failed",
    tacticalForceLoss: "Critical force loss",
    tacticalSubmittedCaption: "SC2 command submission confirmed",
    workerUnit: "",
    idleLabel: "idle",
    llmTitle: "LLM Settings",
    llmHint: "The API key is stored only in this local process memory.",
    llmProviderLabel: "Provider",
    llmModelLabel: "Model",
    llmCheckingLabel: "Checking",
    llmSettingLabel: "Setting",
    llmSuccessLabel: "Success",
    llmFailedLabel: "Failed",
    llmRequiredLabel: "Required",
    llmChecking: "Checking LLM key status.",
    llmCheckingFailed: "Failed to check LLM key status",
    llmSaving: "Configuring LLM key...",
    liveStarting: "Starting selected runtime...",
    liveReady: "Selected runtime ready",
    liveFailed: "Runtime start failed",
    liveIdle: "Selected runtime is idle.",
    legacyLiveDisabled: "Selected runtime has not started yet.",
    liveOpenButton: "Open Live GUI",
    liveRefreshButton: "Check Status",
    microMachineTitle: "MicroMachine runtime / DSL evidence",
    microMachineHint: "Primary input is the Commander Chat/voice box on the left. This panel controls the blackboard, semantic scope, and telemetry evidence used by that route. It does not automate the SC2 screen/keyboard or send raw unit commands.",
    microMachineBlackboardLabel: "Blackboard directory",
    microMachineCommandLabel: "Advanced direct publish test text",
    microMachineArmyGroup: "Semantic army group",
    microMachineLocationIntent: "Location intent",
    microMachineUnitClasses: "Unit classes",
    microMachineSafetyMargin: "Safety margin",
    microMachineDuration: "Scope duration seconds",
    microMachineTtl: "TTL seconds",
    microMachineSend: "Send advanced direct publish",
    microMachineSending: "Sending MicroMachine DSL publish...",
    microMachinePublished: "Published",
    microMachineConsumed: "Consumed",
    microMachinePending: "Waiting for telemetry",
    microMachineDashboardTitle: "DSL intervention dashboard",
    microMachineLatestUpdate: "Latest update",
    microMachineActiveIds: "Active ids in MicroMachine",
    microMachineFrame: "Telemetry frame",
    microMachineDomains: "Bias domains",
    microMachineGoal: "Compiled DSL goal",
    microMachineStrategyMode: "Strategy mode / play style",
    microMachineManagers: "Manager evidence",
    microMachinePosture: "Tactical posture",
    microMachineScope: "Semantic scope",
    microMachineConsumedAxes: "Consumed axes by manager",
    microMachineTargetPriority: "Target priority",
    microMachineAttackGate: "Attack gate",
    microMachineTacticalEvidence: "Tactical effect evidence",
    microMachineCommandExecution: "Command execution",
    microMachineRefusalReason: "Refusal / clarification",
    microMachineTacticalLogs: "Recent MicroMachine tactical logs",
    microMachineRawEvidence: "Raw modulation / telemetry evidence",
    microMachineRefused: "Refused",
    microMachineClarification: "Clarification needed",
    microMachineFailed: "Publish failed",
    llmReady: "LLM key configured",
    llmMissing: "LLM required: legacy commander commands need an API key first.",
    llmOptionalMicro: "MicroMachine mode: production chat/voice publishing requires an LLM key. Keyword DSL is explicit smoke/test-only.",
    llmEnterKey: "Enter an API key.",
    llmSaveFailed: "Failed to configure the LLM key.",
    userLabel: "User",
    commanderLabel: "Commander",
    commandPlaceholderMicro: "Enter MicroMachine intent. Example: pressure enemy natural / defensive tanks / worker-line harass",
    commandPlaceholderLegacy: "Legacy python-sc2 command. Example: build a supply depot / send scout",
    commandPlaceholderReady: "Type naturally. Example: build a supply depot / send scout",
    commandPlaceholderLocked: "Command input unlocks after LLM key setup.",
    commandRejected: "Command not sent because the LLM key is not configured.",
    microMachineChatPublished: "Published MicroMachine DSL modulation to the blackboard.",
    microMachineChatQueued: "Waiting for MicroMachine telemetry consumption.",
    microMachineChatRefused: "MicroMachine DSL request was refused or needs clarification.",
    microMachineChatFailed: "MicroMachine DSL publish failed",
    saveLlm: "Save Local Key",
    startupGuide: "🚀 Startup guide\\n1. The default mode is the MicroMachine policy cockpit. Chat/voice uses LLM forced-tool DSL and publishes to the blackboard.\\n2. If the LLM misses the tool-call/JSON contract, the command is not published and the failure is shown.\\n3. Use the MicroMachine panel to confirm or adjust the blackboard directory and semantic scope.\\n4. Legacy python-sc2 commander uses /api/command only when explicitly selected.\\n🎙️ Voice sends recognized speech through the currently selected mode."
  },
  zh: {
    eyebrow: "实时 RTS 指挥中心",
    heroHint: "像聊天一样下达命令，并在右侧查看战场仪表盘。",
    connectionChecking: "正在检查 SC2 连接",
    connectionWaiting: "等待 SC2 状态",
    connectionReady: "SC2 已连接",
    chatTitle: "指挥官聊天",
    chatSubtitle: "命令、问题和状态报告集中在一个驾驶舱。",
    runtimeModeTitle: "命令路由模式",
    runtimeModeMicroSummary: "默认使用 MicroMachine DSL blackboard。",
    runtimeModeLegacySummary: "Legacy python-sc2 commander 兼容模式。",
    microModeLabel: "MicroMachine policy cockpit",
    microModeDescription: "聊天/语音仅使用 LLM forced-tool DSL，只有通过结构验证的命令才会发布到 MicroMachine blackboard。",
    legacyModeLabel: "Legacy python-sc2 commander",
    legacyModeDescription: "旧 demo 路径的兼容模式。它不是 MicroMachine，并且需要 LLM key 才会发送到 /api/command。",
    legacyModeWarning: "Legacy mode 不是 MicroMachine。SC2 启动/命令会走 python-sc2 demo 路径，不要把它当作 MicroMachine QA 证据。",
    runtimeIdleMicro: "MicroMachine runtime 正在等待。点击启动所选 runtime 会启动 SC2/MicroMachine smoke session。",
    runtimeIdleLegacy: "Legacy python-sc2 runtime 正在等待。先设置 key，再点击启动所选 runtime。",
    runtimeStartButton: "启动所选 runtime",
    runtimeOpenButton: "打开 Live GUI",
    runtimeRefreshButton: "检查 runtime 状态",
    microMachineEnemyDifficulty: "手动 live-hold 敌方难度 (1..10)",
    runtimeStarting: "正在启动所选 runtime",
    runtimeRunning: "所选 runtime 正在运行",
    runtimeConnected: "MicroMachine telemetry 已连接",
    runtimePassed: "MicroMachine smoke 已通过",
    runtimeDetachedTelemetry: "存在 MicroMachine telemetry 文件，但未连接到正在运行的 runtime",
    runtimeReady: "Legacy live GUI 已就绪",
    runtimeBlocked: "runtime 启动被阻止",
    runtimeFailed: "runtime 启动失败",
    quickStatus: "作战状态",
    quickScout: "1名陆战队员侦察",
    quickScv: "主力进攻",
    quickPosition: "全军撤退",
    send: "发送",
    dashboardTitle: "战场控制",
    battlefieldLinkWaiting: "等待 MicroMachine",
    battlefieldLinkConnected: "战场链路已连接",
    battlefieldCommandState: "当前命令",
    battlefieldFrame: "战场帧",
    battlefieldForce: "受控部队",
    battlefieldPosture: "当前姿态",
    battlefieldControlWaiting: "输入命令后，将追踪 MicroMachine 的实际分配、SC2 动作与效果确认。",
    commandConsoleKicker: "Active field order",
    commandConsoleIdleTitle: "输入命令后，这里会显示每个实际执行阶段。",
    commandConsoleIdleState: "等待命令",
    commandConsoleWaiting: "等待中",
    commandConsoleIntent: "作战解析",
    commandConsoleUnits: "已分配部队",
    commandConsoleAction: "实际命令",
    commandConsoleTarget: "目标",
    commandConsoleVerification: "需要在画面确认的结果",
    commandConsoleRefresh: "刷新执行状态",
    commandConsoleRevise: "修改当前命令",
    commandConsoleRetreat: "紧急全军撤退",
    commandConsoleTechnical: "查看技术详情",
    commandStageInterpret: "命令解析",
    commandStageAssign: "单位分配",
    commandStageExecute: "SC2 执行",
    commandStageVerify: "效果确认",
    minerals: "晶体矿",
    vespene: "瓦斯",
    supply: "补给",
    workers: "工人",
    army: "部队",
    structures: "建筑",
    noState: "暂时无法读取游戏状态。",
    microMachineStateDashboardDisabled: "MicroMachine 模式不会轮询旧战场仪表盘。请以 MicroMachine DSL intervention dashboard 的消费证据为准。",
    microMachineStateConnection: "MicroMachine cockpit · legacy /api/state 已禁用",
    microMachineStateBriefing: "MicroMachine 模式不会显示 dry-run 战场资源值。blackboard/telemetry evidence 才是依据。",
    noStructures: "无",
    incompleteObservation: "侦测信息不完整。",
    briefingTitle: "战略简报",
    briefingWaiting: "正在等待状态数据。",
    briefingCurrentStrategy: "当前战略",
    briefingEvidence: "判断依据",
    briefingProgress: "进度",
    briefingRisk: "风险",
    briefingMemory: "压缩记忆",
    briefingAdvice: "查看建议",
    strategyOpening: "命令记录还不足。目前处于战场评估阶段。",
    strategyEconomy: "你正在稳定经济和生产基础。",
    strategyProduction: "你正在建立 Terran 生产体系。",
    strategyScout: "你正在通过侦察获取情报优势。",
    strategyDefense: "你正在优先保护主基地并确保生存。",
    progressRecent: "最近命令",
    compactedNone: "还没有压缩的旧上下文。",
    compactedSummary: "已压缩 {total} 条较早对话/命令。成功/信息 {successful} 条，阻塞/需确认 {failed} 条。",
    riskNoArmy: "当前没有部队，容易受到早期压制。",
    riskNoScout: "敌方情报不足。",
    riskSupply: "补给余量偏低。",
    riskStable: "暂无明显即时风险。",
    briefingEconomy: "经济",
    briefingSupply: "补给",
    briefingForces: "战力",
    briefingEnemy: "敌情",
    briefingEnemyNone: "未发现敌人",
    briefingSuggestionSupply: "补给余量偏低。请准备补给站。",
    briefingSuggestionScout: "缺少敌方情报。建议派出侦察。",
    briefingSuggestionArmy: "当前没有部队。建造兵营后准备生产陆战队员。",
    briefingSuggestionStable: "暂无明显危险信号。继续维持经济和生产。",
    chatTrimmed: "已省略较早对话",
    chatArchiveOpen: "查看完整记录",
    messageExpand: "查看完整内容",
    assistantThinking: "正在回答",
    assistantWaiting: "正在等待 LLM 响应",
    assistantPendingCount: "等待中的响应 {count} 条",
    voiceListening: "录音中",
    voiceFinalizing: "Finalizing speech",
    voiceTranscriptUnavailable: "Voice transcript unavailable.",
    voiceInputLabel: "语音输入",
    voiceStopLabel: "停止录音",
    voiceUnsupported: "此浏览器不支持语音识别。",
    voiceNoResult: "未识别到语音。",
    tacticalRadioTitle: "Tactical Radio",
    tacticalRadioCaptionsLabel: "Tactical radio captions",
    tacticalRadioReady: "Audio ready · captions always active",
    tacticalRadioSpeaking: "Tactical radio speaking · captions active",
    tacticalRadioMuted: "Muted · captions remain active",
    tacticalRadioUnavailable: "Audio unavailable · captions remain active",
    tacticalRadioMute: "Mute",
    tacticalRadioUnmute: "Unmute",
    tacticalPlanConfirmed: "Plan confirmed",
    tacticalOperationIdentity: "Operation",
    tacticalForceAssigned: "Force assigned",
    tacticalForcePartiallyAssigned: "Force partially assigned",
    tacticalMoving: "Movement started",
    tacticalEngaged: "Engagement started",
    tacticalTargetReached: "Target reached",
    tacticalCompleted: "Operation completed",
    tacticalBlocked: "Operation blocked",
    tacticalRouteUnavailable: "Route unavailable",
    tacticalEmergencyRetreat: "Emergency retreat started",
    tacticalBaseAttack: "Base under attack",
    tacticalCriticalAbilityFailure: "Critical ability failed",
    tacticalForceLoss: "Critical force loss",
    tacticalSubmittedCaption: "SC2 command submission confirmed",
    workerUnit: "",
    idleLabel: "空闲",
    llmTitle: "LLM 设置",
    llmHint: "API key 只保存在本地进程内存中。",
    llmProviderLabel: "模型供应商",
    llmModelLabel: "模型",
    llmCheckingLabel: "检查中",
    llmSettingLabel: "设置中",
    llmSuccessLabel: "设置成功",
    llmFailedLabel: "设置失败",
    llmRequiredLabel: "需要设置",
    llmChecking: "正在检查 LLM key 状态。",
    llmCheckingFailed: "LLM key 状态检查失败",
    llmSaving: "正在设置 LLM key...",
    liveStarting: "正在启动所选 runtime...",
    liveReady: "所选 runtime 已就绪",
    liveFailed: "runtime 启动失败",
    liveIdle: "所选 runtime 正在等待。",
    legacyLiveDisabled: "所选 runtime 尚未启动。",
    liveOpenButton: "打开 Live GUI",
    liveRefreshButton: "检查状态",
    microMachineTitle: "MicroMachine runtime / DSL evidence",
    microMachineHint: "默认输入是左侧 Commander Chat/语音框。此面板用于控制该路径使用的 blackboard、semantic scope 与 telemetry 证据。不会自动操作 SC2 画面/键盘，也不会发送 raw unit 命令。",
    microMachineBlackboardLabel: "Blackboard directory",
    microMachineCommandLabel: "高级直接 publish 测试文本",
    microMachineArmyGroup: "Semantic army group",
    microMachineLocationIntent: "Location intent",
    microMachineUnitClasses: "Unit classes",
    microMachineSafetyMargin: "Safety margin",
    microMachineDuration: "Scope duration seconds",
    microMachineTtl: "TTL seconds",
    microMachineSend: "发送高级直接 publish",
    microMachineSending: "正在发送 MicroMachine DSL publish...",
    microMachinePublished: "已发布",
    microMachineConsumed: "已消费",
    microMachinePending: "等待 telemetry",
    microMachineDashboardTitle: "DSL intervention dashboard",
    microMachineLatestUpdate: "最新 update",
    microMachineActiveIds: "MicroMachine active id",
    microMachineFrame: "Telemetry frame",
    microMachineDomains: "Bias domain",
    microMachineGoal: "已编译 DSL goal",
    microMachineStrategyMode: "Strategy mode / play style",
    microMachineManagers: "Manager evidence",
    microMachinePosture: "Tactical posture",
    microMachineScope: "Semantic scope",
    microMachineConsumedAxes: "Consumed axes by manager",
    microMachineTargetPriority: "Target priority",
    microMachineAttackGate: "Attack gate",
    microMachineTacticalEvidence: "Tactical effect evidence",
    microMachineCommandExecution: "Command execution",
    microMachineRefusalReason: "Refusal / clarification",
    microMachineTacticalLogs: "Recent MicroMachine tactical logs",
    microMachineRawEvidence: "Raw modulation / telemetry evidence",
    microMachineRefused: "已拒绝",
    microMachineClarification: "需要进一步确认",
    microMachineFailed: "发布失败",
    llmReady: "LLM key 已设置",
    llmMissing: "Legacy commander 命令必须先设置 LLM API key。",
    llmOptionalMicro: "MicroMachine mode：production 聊天/语音发布需要 LLM key。Keyword DSL 仅限显式 smoke/test。",
    llmEnterKey: "请输入 API key。",
    llmSaveFailed: "LLM key 设置请求失败。",
    userLabel: "用户",
    commanderLabel: "指挥官",
    commandPlaceholderMicro: "输入 MicroMachine 意图。例如：pressure enemy natural / defensive tanks / worker-line harass",
    commandPlaceholderLegacy: "Legacy python-sc2 命令。例如：建造补给站 / 派出侦察",
    commandPlaceholderReady: "自然输入命令。例如：建造补给站 / 派出侦察",
    commandPlaceholderLocked: "设置 LLM key 后才能输入命令。",
    commandRejected: "LLM key 未设置，命令未发送。",
    microMachineChatPublished: "已把 MicroMachine DSL modulation 发布到 blackboard。",
    microMachineChatQueued: "正在等待 MicroMachine telemetry 消费。",
    microMachineChatRefused: "MicroMachine DSL 请求被拒绝或需要进一步确认。",
    microMachineChatFailed: "MicroMachine DSL 发布失败",
    saveLlm: "保存本地 Key",
    startupGuide: "🚀 启动指南\\n1. 默认模式是 MicroMachine policy cockpit。聊天/语音使用 LLM forced-tool DSL 并发布到 blackboard。\\n2. 如果 LLM 未满足 tool-call/JSON 契约，命令不会发布，并会显示失败状态。\\n3. 在 MicroMachine 面板确认或调整 blackboard directory 与 semantic scope。\\n4. Legacy python-sc2 commander 只有显式选择时才使用 /api/command。\\n🎙️ 语音会通过当前选择的模式发送。"
  }
};

function t(key) {
  return (I18N[currentLang] && I18N[currentLang][key]) || I18N.ko[key] || key;
}

function isMicroMachineCommandMode() {
  return activeCommandMode === COMMAND_MODE_MICROMACHINE;
}

function selectedCommandMode() {
  var selected = document.querySelector("input[name='command-mode']:checked");
  return selected && selected.value === COMMAND_MODE_LEGACY_COMMANDER
    ? COMMAND_MODE_LEGACY_COMMANDER
    : COMMAND_MODE_MICROMACHINE;
}

function setCommandMode(mode) {
  activeCommandMode = mode === COMMAND_MODE_LEGACY_COMMANDER
    ? COMMAND_MODE_LEGACY_COMMANDER
    : COMMAND_MODE_MICROMACHINE;
  Array.prototype.forEach.call(document.querySelectorAll("input[name='command-mode']"), function (input) {
    input.checked = input.value === activeCommandMode;
  });
  var summary = document.getElementById("runtime-mode-summary");
  if (summary) {
    var key = isMicroMachineCommandMode() ? "runtimeModeMicroSummary" : "runtimeModeLegacySummary";
    summary.setAttribute("data-i18n", key);
    summary.textContent = t(key);
  }
  var warning = document.getElementById("legacy-mode-warning");
  if (warning) {
    warning.style.display = isMicroMachineCommandMode() ? "none" : "block";
  }
  var difficultyControl = document.getElementById("micromachine-enemy-difficulty-control");
  if (difficultyControl) {
    difficultyControl.style.display = isMicroMachineCommandMode() ? "flex" : "none";
  }
  if (!llmConfigured) {
    setLlmStatus(
      "missing",
      "llmRequiredLabel",
      isMicroMachineCommandMode() ? t("llmOptionalMicro") : t("llmMissing")
    );
  }
  if (isMicroMachineCommandMode()) {
    renderMicroMachineStatePlaceholder();
  } else {
    pollState();
  }
  setCommandEnabled(llmConfigured);
}

function setCommandEnabled(legacyEnabled) {
  var input = document.getElementById("command-input");
  var button = document.getElementById("send-button");
  var voiceButton = document.getElementById("voice-button");
  var enabled = isMicroMachineCommandMode() || !!legacyEnabled;
  input.disabled = !enabled;
  button.disabled = !enabled;
  voiceButton.disabled = !enabled;
  if (isMicroMachineCommandMode()) {
    input.placeholder = t("commandPlaceholderMicro");
  } else {
    input.placeholder = enabled ? t("commandPlaceholderLegacy") : t("commandPlaceholderLocked");
  }
}

function applyLanguage(lang) {
  currentLang = I18N[lang] ? lang : "ko";
  document.documentElement.lang = currentLang;
  Array.prototype.forEach.call(document.querySelectorAll("[data-i18n]"), function (node) {
    node.textContent = t(node.getAttribute("data-i18n"));
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-i18n-aria-label]"), function (node) {
    node.setAttribute(
      "aria-label",
      t(node.getAttribute("data-i18n-aria-label"))
    );
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-i18n-title]"), function (node) {
    node.setAttribute(
      "title",
      t(node.getAttribute("data-i18n-title"))
    );
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-lang-button]"), function (button) {
    button.classList.toggle("active", button.getAttribute("data-lang-button") === currentLang);
  });
  setCommandMode(activeCommandMode);
  renderStartupGuide();
  refreshExpandableLabels();
  refreshPendingLabels();
  updateAssistantPendingState();
  renderChatTrimNote();
  renderTacticalRadioState();
  renderTacticalRadioCaptions();
  setVoiceButtonRecordingState(isRecording);
  if (
    activeVoiceSession &&
    (
      activeVoiceSession.state === "listening" ||
      activeVoiceSession.state === "finalizing"
    )
  ) {
    renderVoiceSession(activeVoiceSession);
  }
  if (latestState) { renderStrategyBriefing(latestState); }
  if (activeCommandConsoleRecord.data) {
    renderActiveCommandConsole(activeCommandConsoleRecord.data, true);
  }
}

function appendCompactText(parent, text, className) {
  var normalized = text === undefined || text === null ? "" : String(text);
  if (normalized.length <= MAX_MESSAGE_PREVIEW_CHARS) {
    var body = document.createElement("span");
    body.className = className + " message-text";
    body.textContent = normalized;
    parent.appendChild(body);
    return;
  }
  var preview = document.createElement("span");
  preview.className = className + " message-preview";
  preview.textContent = normalized.slice(0, MAX_MESSAGE_PREVIEW_CHARS).replace(/\\s+$/g, "") + "…";
  parent.appendChild(preview);
  var details = document.createElement("details");
  details.className = "message-expander";
  var summary = document.createElement("summary");
  summary.setAttribute("data-message-length", String(normalized.length));
  summary.textContent = expandedMessageLabel(normalized.length);
  details.appendChild(summary);
  var full = document.createElement("span");
  full.className = className + " message-full";
  full.textContent = normalized;
  details.appendChild(full);
  parent.appendChild(details);
}

function readableCommanderNarration(text) {
  var normalized = text === undefined || text === null ? "" : String(text);
  normalized = normalized.replace(/^\\[(executed|partially_executed|blocked|clarification|read_only)\\]\\s*/i, "");
  if (normalized.indexOf("no_safe_placement") >= 0) {
    return "건설 위치를 찾지 못했습니다.\\n보이는 지형 안에서 지을 수 있는 칸을 찾지 못했어요.\\n다시 말해 주세요: 본진에 보급고 지어 / 본진 앞에 보급고 지어 / 본진 입구에 보급고 지어";
  }
  if (normalized.indexOf("invalid_refinery_target") >= 0) {
    if (normalized.indexOf("no_free_geyser") >= 0) {
      return "사용 가능한 가스 간헐천을 찾지 못했습니다.\\n이미 가까운 가스에 정제소가 있거나, 아직 다른 간헐천을 관측하지 못한 상태입니다.\\n다시 말해 주세요: 본진 가스 확인해 / 앞마당 정찰해 / 앞마당 가스에 정제소 지어";
    }
    return "정제소는 가스 간헐천 위에만 지을 수 있습니다.\\n위치를 더 구체적으로 말해 주세요: 본진 가스 / 앞마당 가스";
  }
  return normalized
    .replace(/명령을 실행하지 못했습니다\\. 이유:\\s*/g, "")
    .replace(/실행하지 않았습니다\\. 이유:\\s*/g, "")
    .replace(/\\. 대안:\\s*/g, ".\\n다음 행동: ");
}

function expandedMessageLabel(length) {
  return t("messageExpand") + " · " + length + " chars";
}

function refreshExpandableLabels() {
  Array.prototype.forEach.call(document.querySelectorAll(".message-expander > summary"), function (summary) {
    var length = Number(summary.getAttribute("data-message-length") || 0);
    if (length > 0) { summary.textContent = expandedMessageLabel(length); }
  });
}

function archiveTrimmedEntry(entry) {
  var item = { command_text: "", narration: "", status: "" };
  var userMessage = entry.querySelector(".message-user");
  var botMessage = entry.querySelector(".message-bot");
  if (userMessage) {
    item.command_text = userMessage.getAttribute("data-full-text") || userMessage.textContent || "";
  }
  if (botMessage) {
    item.narration = botMessage.getAttribute("data-full-text") || botMessage.textContent || "";
    item.status = botMessage.getAttribute("data-status") || "";
  }
  if (item.command_text || item.narration) {
    archivedChatEvents.push(item);
  }
}

function renderChatTrimNote() {
  var existingNote = document.getElementById("chat-trim-note");
  if (trimmedChatEvents < 1) {
    if (existingNote) { existingNote.remove(); }
    return;
  }
  if (!existingNote) {
    existingNote = document.createElement("details");
    existingNote.id = "chat-trim-note";
    existingNote.className = "chat-trim-note";
    var summary = document.createElement("summary");
    existingNote.appendChild(summary);
    existingNote.addEventListener("toggle", function () {
      if (existingNote.open) { renderArchivedChatDetails(existingNote); }
    });
    logBox.insertBefore(existingNote, logBox.firstElementChild);
  }
  var noteSummary = existingNote.querySelector("summary");
  if (noteSummary) {
    noteSummary.textContent = t("chatTrimmed") + " · " + trimmedChatEvents + " · " + t("chatArchiveOpen");
  }
  if (existingNote.open) { renderArchivedChatDetails(existingNote); }
}

function renderArchivedChatDetails(note) {
  var oldBody = note.querySelector(".archived-chat");
  if (oldBody) { oldBody.remove(); }
  var body = document.createElement("div");
  body.className = "archived-chat";
  archivedChatEvents.forEach(function (ev, index) {
    var item = document.createElement("div");
    item.className = "archived-chat-item";
    var meta = document.createElement("span");
    meta.className = "archived-chat-meta";
    meta.textContent = "#" + (index + 1) + (ev.status ? " · " + ev.status : "");
    item.appendChild(meta);
    if (ev.command_text) {
      appendCompactText(item, t("userLabel") + ": " + ev.command_text, "archived-chat-text");
    }
    if (ev.narration) {
      appendCompactText(item, t("commanderLabel") + ": " + ev.narration, "archived-chat-text");
    }
    body.appendChild(item);
  });
  note.appendChild(body);
}

function oldestTrimCandidate() {
  var entries = logBox.querySelectorAll(".log-entry");
  for (var i = 0; i < entries.length; i += 1) {
    var voiceSession = voiceSessionForNode(entries[i]);
    if (
      entries[i] !== pendingAggregateNode &&
      !(
        voiceSession &&
        voiceSession.submitted !== true &&
        (
          voiceSession.state === "listening" ||
          voiceSession.state === "finalizing"
        )
      )
    ) {
      return entries[i];
    }
  }
  return null;
}

function retireVoiceSessionForTrim(entry) {
  var session = voiceSessionForNode(entry);
  if (!session) { return false; }
  clearVoiceFinalizationTimer(session);
  var retiredPendingId = String(session.pendingId || "");
  if (retiredPendingId) {
    removePendingById(retiredPendingId, true);
  }
  if (
    session.pendingId &&
    voiceSessionsByPendingId[session.pendingId] === session
  ) {
    delete voiceSessionsByPendingId[session.pendingId];
  }
  if (activeVoiceSession === session) {
    activeVoiceSession = null;
  }
  session.state = session.pendingId ? "retired" : session.state;
  session.node = null;
  return Boolean(retiredPendingId);
}

function trimChatLog() {
  var retiredPendingVoice = false;
  while (logBox.querySelectorAll(".log-entry").length > MAX_CHAT_EVENTS) {
    var oldestEntry = oldestTrimCandidate();
    if (!oldestEntry) { break; }
    archiveTrimmedEntry(oldestEntry);
    retiredPendingVoice = retireVoiceSessionForTrim(oldestEntry) ||
      retiredPendingVoice;
    logBox.removeChild(oldestEntry);
    trimmedChatEvents += 1;
  }
  if (retiredPendingVoice) {
    renderPendingAggregate("", true);
    updateAssistantPendingState();
    if (logBox.querySelectorAll(".log-entry").length > MAX_CHAT_EVENTS) {
      trimChatLog();
      return;
    }
  }
  renderChatTrimNote();
}

function renderStartupGuide() {
  var existing = document.getElementById("startup-guide-entry");
  if (!existing) {
    existing = document.createElement("div");
    existing.id = "startup-guide-entry";
    existing.className = "log-entry";
    var botMessage = document.createElement("div");
    botMessage.className = "message message-bot";
    var botMeta = document.createElement("span");
    botMeta.className = "message-meta";
    botMeta.textContent = t("commanderLabel");
    botMessage.appendChild(botMeta);
    var narration = document.createElement("span");
    narration.className = "narration startup-guide-text";
    botMessage.appendChild(narration);
    existing.appendChild(botMessage);
    logBox.insertBefore(existing, logBox.firstChild);
  }
  var meta = existing.querySelector(".message-meta");
  var botMessage = existing.querySelector(".message-bot");
  if (meta) { meta.textContent = t("commanderLabel"); }
  if (botMessage) {
    while (botMessage.childNodes.length > 1) {
      botMessage.removeChild(botMessage.lastChild);
    }
    botMessage.setAttribute("data-full-text", t("startupGuide"));
    appendCompactText(botMessage, t("startupGuide"), "narration startup-guide-text");
  }
}

function appendLog(ev) {
  var matchedVoiceSession = pendingVoiceSessionForHistoryEvent(ev);
  if (ev && typeof ev.seq === "number") {
    recentEvents.push(ev);
    compactRecentEventsIfNeeded();
    removePendingForHistoryEvent(ev);
  }
  if (matchedVoiceSession) {
    renderVoiceSessionTerminal(
      matchedVoiceSession,
      String(ev.status || "clarification"),
      readableCommanderNarration(ev.narration || "")
    );
    if (latestState) { renderStrategyBriefing(latestState); }
    return;
  }
  var entry = document.createElement("div");
  entry.className = "log-entry";
  if (ev.command_text) {
    var userMessage = document.createElement("div");
    userMessage.className = "message message-user";
    userMessage.setAttribute("data-full-text", String(ev.command_text));
    var userMeta = document.createElement("span");
    userMeta.className = "message-meta";
    userMeta.textContent = t("userLabel");
    userMessage.appendChild(userMeta);
    appendCompactText(userMessage, ev.command_text, "command-text");
    entry.appendChild(userMessage);
  }
  var botMessage = document.createElement("div");
  botMessage.className = "message message-bot";
  var readableNarration = readableCommanderNarration(ev.narration || "");
  botMessage.setAttribute("data-full-text", readableNarration);
  botMessage.setAttribute("data-status", String(ev.status || "clarification"));
  var botMeta = document.createElement("span");
  botMeta.className = "message-meta";
  botMeta.textContent = t("commanderLabel");
  botMessage.appendChild(botMeta);
  var status = document.createElement("span");
  status.className = "status status-" + (ev.status || "clarification");
  status.setAttribute("aria-hidden", "true");
  status.textContent = "";
  botMessage.appendChild(status);
  var narration = document.createElement("span");
  narration.className = "narration message-text";
  if (readableNarration.length <= MAX_MESSAGE_PREVIEW_CHARS) {
    narration.textContent = readableNarration;
    botMessage.appendChild(narration);
  } else {
    appendCompactText(botMessage, readableNarration, "narration");
  }
  entry.appendChild(botMessage);
  logBox.appendChild(entry);
  trimChatLog();
  logBox.scrollTop = logBox.scrollHeight;
  if (latestState) { renderStrategyBriefing(latestState); }
}

function compactRecentEventsIfNeeded() {
  if (recentEvents.length <= COMPACT_AFTER_EVENTS) { return; }
  var compactCount = recentEvents.length - COMPACT_KEEP_EVENTS;
  var toCompact = recentEvents.slice(0, compactCount);
  recentEvents = recentEvents.slice(compactCount);
  toCompact.forEach(function (ev) {
    compactedContext.total += 1;
    if (isSuccessfulRecordStatus(ev.status)) {
      compactedContext.successful += 1;
      addThemeCount(compactedContext.successfulThemes, classifyCommandTheme(ev.command_text || ""));
    }
    if (isFailureRecordStatus(ev.status)) {
      compactedContext.failed += 1;
      addThemeCount(compactedContext.failedThemes, classifyCommandTheme(ev.command_text || ""));
      addThemeCount(compactedContext.failureReasons, classifyFailureReasonTheme(ev.narration || ev.command_text || ""));
    }
    if (ev.status === "read_only") {
      compactedContext.readOnly += 1;
    }
    if (ev.command_text) {
      compactedContext.commands.push(String(ev.command_text));
      if (compactedContext.commands.length > 12) {
        compactedContext.commands = compactedContext.commands.slice(-12);
      }
    }
    if (ev.narration) {
      compactedContext.lastNarration = String(ev.narration).slice(0, 220);
    }
  });
}

function appendPendingCommand(text, voiceSession) {
  pendingCommandSeq += 1;
  var pendingId = "pending-" + pendingCommandSeq;
  if (!pendingNodes[text]) { pendingNodes[text] = []; }
  pendingNodes[text].push(pendingId);
  if (voiceSession) {
    voiceSession.pendingId = pendingId;
    voiceSession.state = "pending";
    voiceSessionsByPendingId[pendingId] = voiceSession;
  }
  if (voiceSession) {
    renderVoiceSessionPending(voiceSession, text);
  } else {
    renderPendingAggregate(text);
  }
  updateAssistantPendingState();
  logBox.scrollTop = logBox.scrollHeight;
  return pendingId;
}

function pendingCommandTexts() {
  var texts = [];
  Object.keys(pendingNodes).forEach(function (key) {
    var ids = pendingNodes[key] || [];
    ids.forEach(function (pendingId) {
      if (!voiceSessionForPendingId(pendingId)) {
        texts.push(key);
      }
    });
  });
  return texts;
}

function renderPendingAggregate(latestText, skipTrim) {
  var texts = pendingCommandTexts();
  var entry = pendingAggregateNode ||
    document.getElementById(pendingAggregateId);
  if (!texts.length) {
    if (entry) { entry.remove(); }
    pendingAggregateNode = null;
    return;
  }
  var displayText = latestText || texts[texts.length - 1] || "";
  if (!entry) {
    entry = document.createElement("div");
    entry.className = "log-entry pending-entry";
    entry.id = pendingAggregateId;
    logBox.appendChild(entry);
  }
  pendingAggregateNode = entry;
  entry.className = "log-entry pending-entry";
  entry.id = pendingAggregateId;
  entry.textContent = "";

  var userMessage = document.createElement("div");
  userMessage.className = "message message-user";
  userMessage.setAttribute("data-full-text", displayText);
  var userMeta = document.createElement("span");
  userMeta.className = "message-meta";
  userMeta.textContent = t("userLabel");
  userMessage.appendChild(userMeta);
  appendCompactText(userMessage, displayText, "command-text");
  if (texts.length > 1) {
    var aggregateMeta = document.createElement("span");
    aggregateMeta.className = "message-meta";
    aggregateMeta.textContent = " · " + assistantPendingLabel(texts.length);
    userMessage.appendChild(aggregateMeta);
  }
  entry.appendChild(userMessage);

  var botMessage = document.createElement("div");
  botMessage.className = "message message-bot message-pending";
  botMessage.setAttribute("data-full-text", t("assistantThinking"));
  botMessage.setAttribute("data-status", "pending");
  botMessage.setAttribute("aria-label", t("assistantWaiting"));
  var botMeta = document.createElement("span");
  botMeta.className = "message-meta";
  botMeta.textContent = t("commanderLabel");
  botMessage.appendChild(botMeta);
  var narration = document.createElement("span");
  narration.className = "narration";
  narration.textContent = t("assistantThinking");
  botMessage.appendChild(narration);
  var typingIndicator = document.createElement("span");
  typingIndicator.className = "typing-indicator";
  typingIndicator.setAttribute("aria-hidden", "true");
  for (var i = 0; i < 3; i += 1) {
    typingIndicator.appendChild(document.createElement("span"));
  }
  botMessage.appendChild(typingIndicator);
  entry.appendChild(botMessage);
  if (skipTrim !== true) { trimChatLog(); }
}

function clearPendingMicroMachinePlan() {
  var pendingVoiceSessions = Object.keys(
    voiceSessionsByPendingId
  ).map(function(pendingId) {
    return voiceSessionsByPendingId[pendingId];
  }).filter(Boolean);
  if (
    activeVoiceSession &&
    (
      activeVoiceSession.state === "listening" ||
      activeVoiceSession.state === "finalizing" ||
      activeVoiceSession.state === "pending"
    ) &&
    pendingVoiceSessions.indexOf(activeVoiceSession) < 0
  ) {
    pendingVoiceSessions.push(activeVoiceSession);
  }
  pendingVoiceSessions.forEach(function(session) {
    if (session.pendingId) {
      removePendingById(session.pendingId);
    }
    invalidateVoiceSession(
      session,
      commandUiText(
        "MicroMachine 전장 링크가 전환되어 이전 음성 명령 추적을 종료했습니다.",
        "The MicroMachine battlefield link changed, so the previous voice order is no longer tracked.",
        "MicroMachine battlefield link changed; the previous voice order is no longer tracked."
      )
    );
  });
  if (isRecording) {
    isRecording = false;
    setVoiceButtonRecordingState(false);
    if (recognition) {
      if (typeof recognition.abort === "function") {
        recognition.abort();
      } else if (typeof recognition.stop === "function") {
        recognition.stop();
      }
    }
  }
  Object.keys(pendingNodes).forEach(function (key) {
    delete pendingNodes[key];
  });
  voiceSessionsByPendingId = {};
  renderPendingAggregate();
  updateAssistantPendingState();
}

function appendMicroMachinePendingPlan(text, voiceSession) {
  latestMicroMachinePlanText = text;
  var pendingId = appendPendingCommand(text, voiceSession);
  beginActiveCommandConsole(text, pendingId);
  return pendingId;
}

function appendVoiceRecordingBubble() {
  voiceSessionSeq += 1;
  var entry = document.createElement("div");
  entry.className = "log-entry voice-session-entry";
  entry.id = "voice-session-" + voiceSessionSeq;
  entry.setAttribute("data-voice-session-id", String(voiceSessionSeq));
  var session = {
    sessionId: voiceSessionSeq,
    state: "listening",
    segments: [],
    finalText: "",
    interimText: "",
    submitted: false,
    pendingId: "",
    error: false,
    invalidated: false,
    contextGeneration: microMachineBlackboardContextGeneration,
    finalizationTimerId: null,
    node: entry
  };
  activeVoiceSession = session;
  renderVoiceSession(session);
  logBox.appendChild(entry);
  trimChatLog();
  logBox.scrollTop = logBox.scrollHeight;
  return session;
}

function renderVoiceSession(session) {
  if (!session || !session.node) { return; }
  var entry = session.node;
  entry.textContent = "";
  var userMessage = document.createElement("div");
  userMessage.className = "message message-user";
  userMessage.setAttribute(
    "data-full-text",
    session.finalText || session.interimText || ""
  );
  var meta = document.createElement("span");
  meta.className = "message-meta";
  meta.textContent = t("userLabel");
  userMessage.appendChild(meta);
  var stateLabel = document.createElement("span");
  stateLabel.className = "voice-session-state";
  stateLabel.textContent = session.state === "finalizing"
    ? t("voiceFinalizing")
    : t("voiceListening");
  userMessage.appendChild(stateLabel);
  if (session.state === "listening") {
    var wave = document.createElement("span");
    wave.className = "voice-wave";
    wave.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 5; i += 1) {
      wave.appendChild(document.createElement("span"));
    }
    userMessage.appendChild(wave);
  }
  var transcriptText = String(
    session.finalText ||
    session.interimText ||
    ""
  ).trim();
  if (transcriptText) {
    var transcript = document.createElement("span");
    transcript.className = "voice-transcript" +
      (session.finalText ? "" : " voice-transcript-interim");
    transcript.textContent = transcriptText;
    userMessage.appendChild(transcript);
  }
  entry.appendChild(userMessage);
}

function renderVoiceSessionPending(session, text) {
  if (!session || !session.node) { return; }
  session.state = "pending";
  var entry = session.node;
  entry.className = "log-entry pending-entry voice-session-entry";
  entry.textContent = "";

  var userMessage = document.createElement("div");
  userMessage.className = "message message-user";
  userMessage.setAttribute("data-full-text", String(text || ""));
  var userMeta = document.createElement("span");
  userMeta.className = "message-meta";
  userMeta.textContent = t("userLabel");
  userMessage.appendChild(userMeta);
  appendCompactText(userMessage, text, "command-text");
  var voiceState = document.createElement("span");
  voiceState.className = "voice-session-state";
  voiceState.textContent = t("voiceFinalizing");
  userMessage.appendChild(voiceState);
  entry.appendChild(userMessage);

  var botMessage = document.createElement("div");
  botMessage.className = "message message-bot message-pending";
  botMessage.setAttribute("data-full-text", t("assistantThinking"));
  botMessage.setAttribute("data-status", "pending");
  botMessage.setAttribute("aria-label", t("assistantWaiting"));
  var botMeta = document.createElement("span");
  botMeta.className = "message-meta";
  botMeta.textContent = t("commanderLabel");
  botMessage.appendChild(botMeta);
  var narration = document.createElement("span");
  narration.className = "narration";
  narration.textContent = t("assistantThinking");
  botMessage.appendChild(narration);
  var typingIndicator = document.createElement("span");
  typingIndicator.className = "typing-indicator";
  typingIndicator.setAttribute("aria-hidden", "true");
  for (var index = 0; index < 3; index += 1) {
    typingIndicator.appendChild(document.createElement("span"));
  }
  botMessage.appendChild(typingIndicator);
  entry.appendChild(botMessage);
  trimChatLog();
}

function clearVoiceFinalizationTimer(session) {
  if (
    session &&
    session.finalizationTimerId !== null &&
    window.clearTimeout
  ) {
    window.clearTimeout(session.finalizationTimerId);
  }
  if (session) { session.finalizationTimerId = null; }
}

function voiceSessionContextIsCurrent(session) {
  return Boolean(
    session &&
    session.invalidated !== true &&
    session.contextGeneration === microMachineBlackboardContextGeneration
  );
}

function setVoiceButtonRecordingState(recording) {
  var voiceButton = document.getElementById("voice-button");
  if (!voiceButton) { return; }
  var active = recording === true;
  voiceButton.classList.toggle("recording", active);
  voiceButton.setAttribute("aria-pressed", active ? "true" : "false");
  voiceButton.setAttribute(
    "aria-label",
    t(active ? "voiceStopLabel" : "voiceInputLabel")
  );
  voiceButton.setAttribute(
    "title",
    t(active ? "voiceStopLabel" : "voiceInputLabel")
  );
}

function removeVoiceRecordingBubble() {
  if (activeVoiceSession && activeVoiceSession.node) {
    clearVoiceFinalizationTimer(activeVoiceSession);
    activeVoiceSession.node.remove();
  }
  activeVoiceSession = null;
}

function voiceSessionForNode(node) {
  if (!node) { return null; }
  if (activeVoiceSession && activeVoiceSession.node === node) {
    return activeVoiceSession;
  }
  var match = null;
  Object.keys(voiceSessionsByPendingId).some(function(pendingId) {
    var session = voiceSessionsByPendingId[pendingId];
    if (session && session.node === node) {
      match = session;
      return true;
    }
    return false;
  });
  return match;
}

function voiceSessionForPendingId(pendingId) {
  return pendingId ? voiceSessionsByPendingId[pendingId] || null : null;
}

function pendingVoiceSessionForCommand(text) {
  var pendingIds = pendingNodes[text] || [];
  for (var index = 0; index < pendingIds.length; index += 1) {
    var session = voiceSessionForPendingId(pendingIds[index]);
    if (session) { return session; }
  }
  return null;
}

function historyEventRequestId(ev) {
  var detail = ev && ev.detail || {};
  return String(
    ev && ev.request_id ||
    detail.web_request_id ||
    ""
  ).trim();
}

function uniquePendingIdForCommand(text) {
  var pendingIds = pendingNodes[text] || [];
  return pendingIds.length === 1 ? pendingIds[0] : "";
}

function pendingVoiceSessionForHistoryEvent(ev) {
  if (!ev || typeof ev !== "object") { return null; }
  var requestId = historyEventRequestId(ev);
  if (requestId) {
    return voiceSessionForPendingId(requestId);
  }
  var pendingId = uniquePendingIdForCommand(
    String(ev.command_text || "")
  );
  return pendingId ? voiceSessionForPendingId(pendingId) : null;
}

function pendingVoiceSessionForPendingTexts() {
  var match = null;
  Object.keys(pendingNodes).some(function(text) {
    match = pendingVoiceSessionForCommand(text);
    return Boolean(match);
  });
  return match;
}

function releaseVoicePendingSession(session) {
  if (!session) { return; }
  if (session.pendingId) {
    delete voiceSessionsByPendingId[session.pendingId];
  }
}

function renderVoiceSessionTerminal(session, status, narration) {
  if (!session || !session.node) { return; }
  clearVoiceFinalizationTimer(session);
  session.state = status === "blocked" ? "failed" : "completed";
  releaseVoicePendingSession(session);
  var entry = session.node;
  entry.className = "log-entry voice-session-entry";
  entry.textContent = "";
  var userMessage = document.createElement("div");
  userMessage.className = "message message-user";
  userMessage.setAttribute("data-full-text", session.finalText || "");
  var userMeta = document.createElement("span");
  userMeta.className = "message-meta";
  userMeta.textContent = t("userLabel");
  userMessage.appendChild(userMeta);
  appendCompactText(
    userMessage,
    session.finalText || session.interimText || t("voiceTranscriptUnavailable"),
    "command-text"
  );
  entry.appendChild(userMessage);
  var botMessage = document.createElement("div");
  botMessage.className = "message message-bot";
  botMessage.setAttribute("data-full-text", narration);
  botMessage.setAttribute("data-status", status || "clarification");
  var botMeta = document.createElement("span");
  botMeta.className = "message-meta";
  botMeta.textContent = t("commanderLabel");
  botMessage.appendChild(botMeta);
  appendCompactText(botMessage, narration, "narration");
  entry.appendChild(botMessage);
  trimChatLog();
  logBox.scrollTop = logBox.scrollHeight;
}

function failVoiceSession(session, message) {
  if (!session || (session.submitted && session.pendingId)) { return; }
  clearVoiceFinalizationTimer(session);
  session.error = true;
  session.state = "failed";
  renderVoiceSessionTerminal(
    session,
    "blocked",
    message || t("voiceTranscriptUnavailable")
  );
}

function invalidateVoiceSession(session, message) {
  if (!session || session.invalidated) { return; }
  clearVoiceFinalizationTimer(session);
  session.invalidated = true;
  session.error = true;
  if (session.pendingId) {
    removePendingById(session.pendingId);
  }
  renderVoiceSessionTerminal(
    session,
    "blocked",
    message || t("voiceTranscriptUnavailable")
  );
  if (activeVoiceSession === session) {
    activeVoiceSession = null;
  }
}

function tacticalRadioNow() {
  return Date.now();
}

function tacticalRadioUiState() {
  if (!tacticalRadio.supported) { return "unavailable"; }
  if (tacticalRadio.muted) { return "muted"; }
  if (tacticalRadio.speaking) { return "speaking"; }
  return "ready";
}

function renderTacticalRadioState() {
  var statusNode = document.getElementById("tactical-radio-status");
  var muteButton = document.getElementById("tactical-radio-mute");
  var state = tacticalRadioUiState();
  if (statusNode) {
    statusNode.className = "tactical-radio-status is-" + state;
    statusNode.textContent = t(
      state === "speaking"
        ? "tacticalRadioSpeaking"
        : (
          state === "muted"
            ? "tacticalRadioMuted"
            : (
              state === "unavailable"
                ? "tacticalRadioUnavailable"
                : "tacticalRadioReady"
            )
        )
    );
  }
  if (muteButton) {
    muteButton.setAttribute("aria-pressed", tacticalRadio.muted ? "true" : "false");
    muteButton.textContent = t(
      tacticalRadio.muted ? "tacticalRadioUnmute" : "tacticalRadioMute"
    );
  }
}

function renderTacticalRadioCaptions() {
  var list = document.getElementById("tactical-radio-captions");
  if (!list) { return; }
  list.textContent = "";
  tacticalRadio.captions.forEach(function(item) {
    var row = document.createElement("li");
    row.className = "tactical-radio-caption";
    var priority = document.createElement("span");
    priority.className = "tactical-radio-priority";
    priority.textContent = "P" + String(item.priority);
    var text = document.createElement("span");
    text.className = "tactical-radio-caption-text";
    text.textContent = item.caption;
    row.appendChild(priority);
    row.appendChild(text);
    list.appendChild(row);
  });
  list.scrollTop = list.scrollHeight;
}

function appendTacticalRadioCaption(callout) {
  tacticalRadio.captions.push({
    priority: callout.priority,
    caption: callout.caption,
    createdAt: callout.createdAt
  });
  tacticalRadio.captions = tacticalRadio.captions.slice(
    -TACTICAL_RADIO_MAX_CAPTION_HISTORY
  );
  renderTacticalRadioCaptions();
}

function clearTacticalRadioTimer() {
  if (tacticalRadio.timerId !== null && window.clearTimeout) {
    window.clearTimeout(tacticalRadio.timerId);
  }
  tacticalRadio.timerId = null;
}

function interruptTacticalRadioSpeech() {
  clearTacticalRadioTimer();
  tacticalRadio.speechToken += 1;
  tacticalRadio.speaking = false;
  tacticalRadio.current = null;
  if (
    tacticalRadio.supported &&
    window.speechSynthesis &&
    typeof window.speechSynthesis.cancel === "function"
  ) {
    window.speechSynthesis.cancel();
  }
  renderTacticalRadioState();
}

function cancelTacticalRadioSpeechAndQueue() {
  tacticalRadio.queue = [];
  interruptTacticalRadioSpeech();
}

function resetTacticalRadio(scopeId, sessionEpoch) {
  cancelTacticalRadioSpeechAndQueue();
  tacticalRadio.scopeId = String(scopeId || "");
  tacticalRadio.sessionEpoch = String(sessionEpoch || "");
  tacticalRadio.dedupe = {};
  tacticalRadio.planAnnouncements = {};
  tacticalRadio.planAnnouncementOrder = [];
  tacticalRadio.frameHighWater = {};
  tacticalRadio.timelineHighWater = {};
  tacticalRadio.operationHighWaterOrder = [];
  tacticalRadio.captions = [];
  tacticalRadio.lastSpokenAt = { 0: 0, 1: 0, 2: 0 };
  renderTacticalRadioCaptions();
  renderTacticalRadioState();
}

function ensureTacticalRadioScope(scopeId, sessionEpoch) {
  var normalized = String(scopeId || "");
  var normalizedEpoch = String(sessionEpoch || "");
  if (!normalized) { return true; }
  if (!tacticalRadio.scopeId) {
    tacticalRadio.scopeId = normalized;
    tacticalRadio.sessionEpoch = normalizedEpoch;
    return true;
  }
  if (
    tacticalRadio.scopeId !== normalized ||
    (
      normalizedEpoch &&
      tacticalRadio.sessionEpoch &&
      tacticalRadio.sessionEpoch !== normalizedEpoch
    )
  ) {
    resetTacticalRadio(normalized, normalizedEpoch);
  } else if (!tacticalRadio.sessionEpoch && normalizedEpoch) {
    tacticalRadio.sessionEpoch = normalizedEpoch;
  }
  return true;
}

function rememberBoundedTacticalRadioValue(
  registry,
  order,
  key,
  value,
  maximum
) {
  var normalizedKey = String(key || "");
  if (!normalizedKey) { return; }
  var existingIndex = order.indexOf(normalizedKey);
  if (existingIndex >= 0) { order.splice(existingIndex, 1); }
  order.push(normalizedKey);
  registry[normalizedKey] = value;
  while (order.length > maximum) {
    delete registry[order.shift()];
  }
}

function tacticalRadioOperationKey(scopeId, sessionEpoch, operationId, generation) {
  return [
    String(scopeId || ""),
    String(sessionEpoch || ""),
    String(operationId || ""),
    String(generation || 0)
  ].join("|");
}

function tacticalRadioPlanAnnouncementKey(
  scopeId,
  sessionEpoch,
  updateId,
  operationId,
  generation
) {
  return [
    String(scopeId || ""),
    String(sessionEpoch || ""),
    String(updateId || ""),
    String(operationId || ""),
    String(generation || 0)
  ].join("|");
}

function rememberTacticalRadioHighWater(key, frame, timelineSeq) {
  rememberBoundedTacticalRadioValue(
    tacticalRadio.frameHighWater,
    tacticalRadio.operationHighWaterOrder,
    key,
    Math.max(Number(tacticalRadio.frameHighWater[key] || -1), frame),
    TACTICAL_RADIO_MAX_OPERATION_HIGH_WATER
  );
  tacticalRadio.timelineHighWater[key] = Math.max(
    Number(tacticalRadio.timelineHighWater[key] || 0),
    timelineSeq
  );
  Object.keys(tacticalRadio.timelineHighWater).forEach(function(candidate) {
    if (tacticalRadio.operationHighWaterOrder.indexOf(candidate) < 0) {
      delete tacticalRadio.timelineHighWater[candidate];
    }
  });
}

function tacticalRadioDedupeExpired(now) {
  Object.keys(tacticalRadio.dedupe).forEach(function(key) {
    if (Number(tacticalRadio.dedupe[key] || 0) <= now) {
      delete tacticalRadio.dedupe[key];
    }
  });
}

function tacticalRadioSpeechText(text) {
  var normalized = String(text || "").replace(/\\s+/g, " ").trim();
  if (normalized.length <= TACTICAL_RADIO_MAX_SPEECH_CHARS) {
    return normalized;
  }
  return normalized.slice(0, TACTICAL_RADIO_MAX_SPEECH_CHARS - 1).trim() + "…";
}

function tacticalRadioQueueSort(left, right) {
  if (left.priority !== right.priority) {
    return left.priority - right.priority;
  }
  return left.createdAt - right.createdAt;
}

function compactTacticalRadioQueue(callout) {
  if (
    callout.priority !== 2 ||
    !callout.operationKey ||
    callout.progressionRank < 0
  ) {
    return;
  }
  tacticalRadio.queue = tacticalRadio.queue.filter(function(item) {
    return !(
      item.priority === 2 &&
      item.operationKey === callout.operationKey &&
      item.progressionRank >= 0 &&
      item.progressionRank <= callout.progressionRank
    );
  });
}

function speakNextTacticalRadioCallout() {
  clearTacticalRadioTimer();
  if (
    tacticalRadio.muted ||
    !tacticalRadio.supported ||
    tacticalRadio.speaking ||
    !tacticalRadio.queue.length
  ) {
    renderTacticalRadioState();
    return;
  }
  tacticalRadio.queue.sort(tacticalRadioQueueSort);
  var callout = tacticalRadio.queue.shift();
  var now = tacticalRadioNow();
  var lastSpokenAt = Number(
    tacticalRadio.lastSpokenAt[callout.priority] || 0
  );
  var interval = Number(
    TACTICAL_RADIO_PRIORITY_INTERVAL_MS[callout.priority] || 0
  );
  var delay = Math.max(0, interval - Math.max(0, now - lastSpokenAt));
  if (delay > 0 && window.setTimeout) {
    tacticalRadio.queue.unshift(callout);
    tacticalRadio.timerId = window.setTimeout(
      speakNextTacticalRadioCallout,
      delay
    );
    return;
  }
  var utterance = new window.SpeechSynthesisUtterance(
    tacticalRadioSpeechText(callout.speech)
  );
  utterance.lang = currentLang === "en"
    ? "en-US"
    : (currentLang === "zh" ? "zh-CN" : "ko-KR");
  var speechToken = tacticalRadio.speechToken + 1;
  tacticalRadio.speechToken = speechToken;
  tacticalRadio.current = callout;
  tacticalRadio.speaking = true;
  tacticalRadio.lastSpokenAt[callout.priority] = now;
  function finishSpeech() {
    if (tacticalRadio.speechToken !== speechToken) { return; }
    tacticalRadio.speaking = false;
    tacticalRadio.current = null;
    renderTacticalRadioState();
    speakNextTacticalRadioCallout();
  }
  utterance.onend = finishSpeech;
  utterance.onerror = finishSpeech;
  renderTacticalRadioState();
  window.speechSynthesis.speak(utterance);
}

function queueTacticalRadioCallout(callout) {
  if (!callout || !callout.caption) { return false; }
  var now = tacticalRadioNow();
  callout.priority = Math.max(0, Math.min(3, Number(callout.priority || 0)));
  callout.createdAt = Number(callout.createdAt || now);
  callout.progressionRank = Number.isFinite(callout.progressionRank)
    ? callout.progressionRank
    : -1;
  var maximumAge = Number(
    TACTICAL_RADIO_REPLAY_MAX_AGE_MS[callout.priority] || 0
  );
  if (
    callout.fromReplay === true &&
    maximumAge > 0 &&
    now - callout.createdAt > maximumAge
  ) {
    return false;
  }
  tacticalRadioDedupeExpired(now);
  var dedupeKey = String(
    callout.dedupeKey ||
    [callout.priority, callout.caption].join("|")
  );
  if (Number(tacticalRadio.dedupe[dedupeKey] || 0) > now) {
    return false;
  }
  tacticalRadio.dedupe[dedupeKey] = now +
    Number(TACTICAL_RADIO_DEDUPE_TTL_MS[callout.priority] || 0);
  appendTacticalRadioCaption(callout);
  if (
    callout.priority === 3 ||
    tacticalRadio.muted ||
    !tacticalRadio.supported ||
    !callout.speech
  ) {
    renderTacticalRadioState();
    return true;
  }
  compactTacticalRadioQueue(callout);
  if (callout.priority === 0) {
    tacticalRadio.queue = tacticalRadio.queue.filter(function(item) {
      return item.priority < 2;
    });
    if (
      tacticalRadio.current &&
      tacticalRadio.current.priority >= 1
    ) {
      interruptTacticalRadioSpeech();
    }
  } else if (callout.priority === 1) {
    if (
      tacticalRadio.current &&
      tacticalRadio.current.priority === 2
    ) {
      interruptTacticalRadioSpeech();
    }
  }
  tacticalRadio.queue.push(callout);
  tacticalRadio.queue.sort(tacticalRadioQueueSort);
  if (tacticalRadio.queue.length > TACTICAL_RADIO_MAX_QUEUE) {
    tacticalRadio.queue = tacticalRadio.queue.slice(
      0,
      TACTICAL_RADIO_MAX_QUEUE
    );
  }
  speakNextTacticalRadioCallout();
  return true;
}

function tacticalRadioSetMuted(muted) {
  tacticalRadio.muted = Boolean(muted);
  if (tacticalRadio.muted) {
    cancelTacticalRadioSpeechAndQueue();
  }
  renderTacticalRadioState();
}

function structuredOperationVector(operation, parentData) {
  var update = operation && operation.update || {};
  var vector = update.vector && typeof update.vector === "object"
    ? update.vector
    : {};
  if (Object.keys(vector).length) { return vector; }
  var compileResult = operation && operation.compile_result ||
    parentData && parentData.compile_result || {};
  var rootVector = compileResult.vector &&
    typeof compileResult.vector === "object"
    ? compileResult.vector
    : {};
  var operationId = String(
    operation && operation.operation_id ||
    operation && operation.operationId ||
    ""
  );
  var rawOperations = rootVector.operations;
  if (Array.isArray(rawOperations)) {
    for (var index = 0; index < rawOperations.length; index += 1) {
      var candidate = rawOperations[index];
      if (
        candidate &&
        typeof candidate === "object" &&
        String(candidate.operation_id || "") === operationId
      ) {
        return candidate.vector && typeof candidate.vector === "object"
          ? Object.assign({}, candidate, candidate.vector)
          : candidate;
      }
    }
  } else if (
    rawOperations &&
    typeof rawOperations === "object" &&
    rawOperations[operationId] &&
    typeof rawOperations[operationId] === "object"
  ) {
    return rawOperations[operationId];
  }
  return rootVector;
}

function structuredOperationUpdateId(operation) {
  if (!operation || typeof operation !== "object") { return ""; }
  var update = operation.update || {};
  var compileResult = operation.compile_result || {};
  var battlefieldOperation = operation.battlefield_operation || {};
  var identity = battlefieldOperation.identity || {};
  return String(
    operation.update_id ||
    update.update_id ||
    compileResult.update_id ||
    identity.update_id ||
    ""
  );
}

function structuredOperationsForReadback(data) {
  if (!data || typeof data !== "object") { return []; }
  var compileResult = data.compile_result || {};
  var registryOperations = Array.isArray(data.operations);
  var operations = registryOperations ? data.operations.slice() : [];
  var rootVector = compileResult.vector || {};
  var rootUpdateId = String(
    data.update_id ||
    compileResult.update_id ||
    ""
  );
  if (registryOperations) {
    operations = rootUpdateId
      ? operations.filter(function(operation) {
          return structuredOperationUpdateId(operation) === rootUpdateId;
        })
      : [];
  }
  if (!operations.length) {
    var rawOperations = rootVector.operations;
    if (Array.isArray(rawOperations)) {
      operations = rawOperations.slice();
    } else if (rawOperations && typeof rawOperations === "object") {
      operations = Object.keys(rawOperations).map(function(operationId) {
        return Object.assign(
          { operation_id: operationId },
          rawOperations[operationId]
        );
      });
    } else if (rootVector.operation_id || data.operation_id) {
      operations = [{
        operation_id: rootVector.operation_id || data.operation_id,
        operation_generation: (
          rootVector.generation ||
          data.operation_generation ||
          1
        ),
        update: { vector: rootVector }
      }];
    }
  }
  return operations.map(function(operation) {
    var vector = structuredOperationVector(operation, data);
    var tacticalTask = vector.tactical_task || {};
    var route = vector.route_intent || {};
    var targetIntent = vector.target_intent || {};
    var lifetime = vector.lifetime || {};
    var operationId = String(
      operation.operation_id ||
      vector.operation_id ||
      tacticalTask.task_id ||
      ""
    );
    var generation = Number(
      operation.operation_generation ||
      operation.generation ||
      vector.generation ||
      data.operation_generation ||
      1
    );
    if (!operationId || generation <= 0) { return null; }
    var requirements = Array.isArray(vector.composition_requirements)
      ? vector.composition_requirements
      : [];
    if (!requirements.length) {
      var unitClasses = Array.isArray(tacticalTask.unit_classes)
        ? tacticalTask.unit_classes
        : [];
      requirements = unitClasses.map(function(unitType) {
        return {
          unit_type: unitType,
          count: Number(
            tacticalTask.min_units ||
            tacticalTask.max_units ||
            1
          )
        };
      });
    }
    return {
      operationId: operationId,
      generation: generation,
      requirements: requirements,
      task: String(
        tacticalTask.task_type ||
        operation.mission ||
        vector.goal ||
        "operation"
      ),
      target: String(
        targetIntent.target_type ||
        route.target_intent ||
        route.location_intent ||
        route.target ||
        tacticalTask.location_intent ||
        (vector.scope || {}).location_intent ||
        "-"
      ),
      route: String(route.route_type || route.type || "direct"),
      lifetime: structuredOperationLifetimeReadback(
        lifetime,
        vector,
        data
      )
    };
  }).filter(Boolean);
}

function structuredOperationLifetimeReadback(lifetime, vector, data) {
  var normalizedLifetime = (
    lifetime && typeof lifetime === "object"
      ? lifetime
      : {}
  );
  var parts = [];
  var mode = String(
    normalizedLifetime.mode ||
    (
      vector.ttl_seconds || data.ttl_seconds
        ? "ttl=" + String(vector.ttl_seconds || data.ttl_seconds) + "s"
        : "until_completed"
    )
  );
  parts.push(mode);
  if (
    Array.isArray(normalizedLifetime.completion_conditions) &&
    normalizedLifetime.completion_conditions.length
  ) {
    parts.push(
      "conditions=" +
      normalizedLifetime.completion_conditions.join(", ")
    );
  }
  if (normalizedLifetime.completion_state) {
    parts.push("state=" + String(normalizedLifetime.completion_state));
  }
  var reason = String(
    normalizedLifetime.reason ||
    normalizedLifetime.completion_reason ||
    ""
  );
  if (reason) {
    parts.push("reason=" + reason);
  }
  return parts.join(" · ");
}

function tacticalRequirementSummary(requirements) {
  if (!Array.isArray(requirements) || !requirements.length) {
    return commandUiText("편성 자동", "adaptive force", "adaptive force");
  }
  return requirements.map(function(requirement) {
    var unitType = String(
      requirement && (
        requirement.unit_type ||
        requirement.unit_class ||
        requirement.family
      ) || "unit"
    ).replace(/^TERRAN_/, "");
    var count = Number(
      requirement && (
        requirement.count ||
        requirement.min_count ||
        requirement.min_units
      ) || 1
    );
    return unitType + " ×" + count;
  }).join(", ");
}

function tacticalPlanSessionEpoch(data, operations, scopeId, updateId) {
  var explicitEpoch = operationPayloadSessionEpoch(data, operations);
  if (explicitEpoch) { return explicitEpoch; }
  var normalizedScope = String(scopeId || "");
  var normalizedUpdate = String(updateId || "");
  if (
    normalizedScope &&
    activeCommandConsoleRecord.scopeId === normalizedScope &&
    activeCommandConsoleRecord.updateId === normalizedUpdate &&
    activeCommandConsoleRecord.sessionEpoch
  ) {
    return String(activeCommandConsoleRecord.sessionEpoch);
  }
  return "";
}

function announceAcceptedTacticalPlan(data, source) {
  if (!data || typeof data !== "object") { return false; }
  if (data.accepted === false || data.ok === false) { return false; }
  var compileResult = data.compile_result || {};
  var status = String(data.status || compileResult.status || "").toLowerCase();
  if (
    status &&
    ["published", "compiled", "accepted", "pending"].indexOf(status) < 0
  ) {
    return false;
  }
  var operations = structuredOperationsForReadback(data);
  if (!operations.length) { return false; }
  var scopeId = String(
    data.blackboard_scope_id ||
    compileResult.blackboard_scope_id ||
    ""
  );
  var updateId = String(
    data.update_id ||
    compileResult.update_id ||
    ""
  );
  if (!updateId) { return false; }
  var sessionEpoch = tacticalPlanSessionEpoch(
    data,
    data.operations || [],
    scopeId,
    updateId
  );
  if (scopeId && !sessionEpoch) { return false; }
  ensureTacticalRadioScope(scopeId, sessionEpoch);
  var announced = false;
  var extraOperationCount = Math.max(
    0,
    operations.length - TACTICAL_RADIO_MAX_PLAN_OPERATIONS
  );
  operations.forEach(function(operation, index) {
    var operationKey = tacticalRadioOperationKey(
      scopeId,
      sessionEpoch,
      operation.operationId,
      operation.generation
    );
    var announcementKey = tacticalRadioPlanAnnouncementKey(
      scopeId,
      sessionEpoch,
      updateId,
      operation.operationId,
      operation.generation
    );
    if (tacticalRadio.planAnnouncements[announcementKey]) { return; }
    var detail = t("tacticalOperationIdentity") + " " +
      operation.operationId + "#" + operation.generation + " · " +
      tacticalRequirementSummary(operation.requirements) + " · " +
      operation.task + " · " + operation.target + " · " +
      operation.route + " · " + operation.lifetime;
    var speech = "";
    if (index < TACTICAL_RADIO_MAX_PLAN_OPERATIONS) {
      speech = t("tacticalPlanConfirmed") + ". " + detail;
      if (
        extraOperationCount > 0 &&
        index === TACTICAL_RADIO_MAX_PLAN_OPERATIONS - 1
      ) {
        speech += ". " + commandUiText(
          "추가 " + extraOperationCount + "개 작전",
          extraOperationCount + " more operations",
          extraOperationCount + " more operations"
        );
      }
    }
    var queued = queueTacticalRadioCallout({
      priority: 2,
      caption: t("tacticalPlanConfirmed") + " · " + detail,
      speech: speech,
      dedupeKey: announcementKey + "|plan",
      operationKey: operationKey,
      progressionRank: 0,
      createdAt: tacticalRadioNow(),
      source: source || "submission"
    });
    if (queued) {
      rememberBoundedTacticalRadioValue(
        tacticalRadio.planAnnouncements,
        tacticalRadio.planAnnouncementOrder,
        announcementKey,
        true,
        TACTICAL_RADIO_MAX_PLAN_IDENTITIES
      );
      announced = true;
    }
  });
  return announced;
}

function seedAcceptedTacticalPlanAnnouncements(data) {
  if (!data || typeof data !== "object") { return; }
  if (data.accepted === false || data.ok === false) { return; }
  var compileResult = data.compile_result || {};
  var status = String(data.status || compileResult.status || "").toLowerCase();
  if (
    status &&
    ["published", "compiled", "accepted", "pending"].indexOf(status) < 0
  ) {
    return;
  }
  var operations = structuredOperationsForReadback(data);
  if (!operations.length) { return; }
  var scopeId = String(
    data.blackboard_scope_id ||
    compileResult.blackboard_scope_id ||
    ""
  );
  var updateId = String(
    data.update_id ||
    compileResult.update_id ||
    ""
  );
  if (!updateId) { return; }
  var sessionEpoch = tacticalPlanSessionEpoch(
    data,
    data.operations || [],
    scopeId,
    updateId
  );
  ensureTacticalRadioScope(scopeId, sessionEpoch);
  operations.forEach(function(operation) {
    var announcementKey = tacticalRadioPlanAnnouncementKey(
      scopeId,
      sessionEpoch,
      updateId,
      operation.operationId,
      operation.generation
    );
    rememberBoundedTacticalRadioValue(
      tacticalRadio.planAnnouncements,
      tacticalRadio.planAnnouncementOrder,
      announcementKey,
      true,
      TACTICAL_RADIO_MAX_PLAN_IDENTITIES
    );
  });
}

function normalizedTacticalReason(payload) {
  var technical = payload && payload.technical || {};
  return String(
    payload && payload.summary ||
    payload && payload.blocker ||
    technical.blocker ||
    technical.reason ||
    ""
  ).trim().toLowerCase().replace(/\\s+/g, " ");
}

function operationEventMatchesRecordUpdate(envelope, payload, record) {
  var envelopeUpdateId = String(
    envelope && envelope.update_id || ""
  );
  var payloadUpdateId = String(
    payload && payload.update_id || ""
  );
  var recordRequestUpdateId = String(
    record && record.updateId || ""
  );
  var recordExecutionOwnerUpdateId = String(
    record && record.data &&
      record.data.operation_console_execution_owner_update_id ||
    recordRequestUpdateId ||
    ""
  );
  if (
    !recordRequestUpdateId ||
    !recordExecutionOwnerUpdateId ||
    (!envelopeUpdateId && !payloadUpdateId) ||
    (
      envelopeUpdateId &&
      payloadUpdateId &&
      envelopeUpdateId !== payloadUpdateId
    )
  ) {
    return false;
  }
  var eventUpdateId = String(payloadUpdateId || envelopeUpdateId);
  return (
    eventUpdateId === recordRequestUpdateId ||
    eventUpdateId === recordExecutionOwnerUpdateId
  );
}

function tacticalLifecycleCallout(envelope, payload, scopeId, record) {
  var kind = String(payload && payload.kind || "").toLowerCase();
  var operationId = String(payload && payload.operation_id || "");
  var generation = Number(payload && payload.generation || 0);
  var requestedGeneration = Number(
    payload && payload.requested_generation || generation
  );
  var recordRequestedGeneration = Number(
    record && (
      record.requestedOperationGeneration ||
      record.operationGeneration
    ) ||
    0
  );
  if (
    !operationId ||
    generation <= 0 ||
    requestedGeneration < generation ||
    !record ||
    Number(record.operationGeneration || 0) !== generation ||
    requestedGeneration !== recordRequestedGeneration ||
    !operationEventMatchesRecordUpdate(envelope, payload, record)
  ) {
    return null;
  }
  var frame = Number(payload.game_frame);
  var sessionEpoch = String(
    payload && payload.session_epoch ||
    record && record.sessionEpoch ||
    operationConsoleSessionEpoch ||
    ""
  );
  var operationKey = tacticalRadioOperationKey(
    scopeId,
    sessionEpoch,
    operationId,
    generation
  );
  var timelineSeq = Number(payload.timeline_seq || 0);
  var timelineHighWater = Number(
    tacticalRadio.timelineHighWater[operationKey] || 0
  );
  var projectionIdentityValid = !(
    payload.technical &&
    payload.technical.projection_identity_valid === false
  );
  if (
    Number.isFinite(timelineSeq) &&
    timelineSeq > 0 &&
    timelineSeq <= timelineHighWater
  ) {
    return null;
  }
  var frameHighWater = Number(
    tacticalRadio.frameHighWater[operationKey] || -1
  );
  if (
    projectionIdentityValid &&
    Number.isFinite(frame) &&
    frame >= 0 &&
    frameHighWater >= 0 &&
    frame < frameHighWater
  ) {
    return null;
  }
  if (
    projectionIdentityValid &&
    Number.isFinite(frame) &&
    frame >= 0
  ) {
    rememberTacticalRadioHighWater(
      operationKey,
      Math.max(frameHighWater, frame),
      timelineHighWater
    );
  }
  var reason = normalizedTacticalReason(payload);
  var priority = 3;
  var label = "";
  var progressionRank = -1;
  if (kind === "assigned") {
    priority = 2;
    label = t("tacticalForceAssigned");
    progressionRank = 1;
  } else if (kind === "partially_assigned") {
    priority = 3;
    label = t("tacticalForcePartiallyAssigned");
  } else if (kind === "movement_observed" || kind === "moving") {
    priority = 2;
    label = t("tacticalMoving");
    progressionRank = 2;
  } else if (kind === "engagement_observed" || kind === "engaged") {
    priority = 2;
    label = t("tacticalEngaged");
    progressionRank = 3;
  } else if (kind === "target_reached" || kind === "reached") {
    priority = 2;
    label = t("tacticalTargetReached");
    progressionRank = 4;
  } else if (kind === "completed") {
    priority = 2;
    label = t("tacticalCompleted");
    progressionRank = 5;
  } else if (kind === "blocked" || kind === "waiting") {
    priority = 1;
    label = /route|path|경로/.test(reason)
      ? t("tacticalRouteUnavailable")
      : t("tacticalBlocked");
  } else if (kind === "emergency_retreat") {
    priority = 0;
    label = t("tacticalEmergencyRetreat");
  } else if (kind === "base_under_attack") {
    priority = 0;
    label = t("tacticalBaseAttack");
  } else if (kind === "critical_ability_failure") {
    priority = 0;
    label = t("tacticalCriticalAbilityFailure");
  } else if (kind === "force_loss") {
    priority = 1;
    label = t("tacticalForceLoss");
  } else if (kind === "submitted") {
    priority = 3;
    label = t("tacticalSubmittedCaption");
  } else {
    return null;
  }
  if (Number.isFinite(timelineSeq) && timelineSeq > 0) {
    rememberTacticalRadioHighWater(
      operationKey,
      Number(tacticalRadio.frameHighWater[operationKey] || -1),
      Math.max(timelineHighWater, timelineSeq)
    );
  }
  var identity = operationId + "#" + generation;
  var detail = reason && reason !== kind ? " · " + reason : "";
  return {
    priority: priority,
    caption: label + " · " + identity + detail,
    speech: priority < 3 ? label + ". " + identity + detail : "",
    dedupeKey: [
      scopeId,
      String(payload.update_id || envelope.update_id || ""),
      operationId,
      generation,
      requestedGeneration,
      kind,
      reason
    ].join("|"),
    operationKey: operationKey,
    progressionRank: progressionRank,
    createdAt: Number(envelope.created_at_unix_ms || tacticalRadioNow()),
    fromReplay: true
  };
}

function announceOperationLifecycleEvent(envelope, payload, scopeId, record) {
  ensureTacticalRadioScope(
    scopeId,
    payload && payload.session_epoch ||
      record && record.sessionEpoch ||
      operationConsoleSessionEpoch ||
      ""
  );
  var callout = tacticalLifecycleCallout(
    envelope,
    payload,
    scopeId,
    record
  );
  return callout ? queueTacticalRadioCallout(callout) : false;
}

function hydrateTacticalRadioState(data) {
  if (!data || typeof data !== "object") { return; }
  seedAcceptedTacticalPlanAnnouncements(data);
  if (Array.isArray(data.modulation_results)) {
    data.modulation_results.forEach(function(result) {
      seedAcceptedTacticalPlanAnnouncements(
        Object.assign(
          {
            blackboard_scope_id: microMachineScopeId(data)
          },
          result || {}
        )
      );
    });
  }
  var operations = commandOperationPayloads(data);
  var scopeId = microMachineScopeId(data);
  if (!scopeId && operations.length) {
    scopeId = operationPayloadScopeId(operations[0], data);
  }
  var sessionEpoch = operationPayloadSessionEpoch(data, operations);
  ensureTacticalRadioScope(scopeId, sessionEpoch);
  operations.forEach(function(operation) {
    var operationId = operationPayloadOperationId(operation);
    var generation = Number(operation.operation_generation || 0);
    if (!operationId || generation <= 0) { return; }
    var key = tacticalRadioOperationKey(
      scopeId,
      sessionEpoch,
      operationId,
      generation
    );
    var operationData = commandOperationData(operation, data);
    var projection = operationData &&
      operationData.battlefield_operation;
    var projectionIdentityValid = Boolean(
      !projection ||
      typeof projection !== "object" ||
      !Object.keys(projection).length ||
      operationCanonicalProjectionMatches(operationData)
    );
    var frame = projectionIdentityValid
      ? commandConsoleTelemetryFrame(operationData)
      : -1;
    var timeline = Array.isArray(operation.semantic_timeline)
      ? operation.semantic_timeline
      : [];
    var timelineSeq = 0;
    timeline.forEach(function(event) {
      if (
        !event.technical ||
        event.technical.projection_identity_valid !== false
      ) {
        frame = Math.max(frame, Number(event.game_frame || -1));
      }
      timelineSeq = Math.max(
        timelineSeq,
        Number(event.timeline_seq || 0)
      );
    });
    rememberTacticalRadioHighWater(key, frame, timelineSeq);
  });
}

function removePendingForCommand(text) {
  var pendingIds = pendingNodes[text];
  if (!pendingIds || !pendingIds.length) { return false; }
  pendingIds.shift();
  if (!pendingIds.length) { delete pendingNodes[text]; }
  renderPendingAggregate();
  updateAssistantPendingState();
  return true;
}

function removePendingForHistoryEvent(ev) {
  if (!ev || typeof ev !== "object") { return false; }
  var requestId = historyEventRequestId(ev);
  if (requestId) {
    return removePendingById(requestId);
  }
  var pendingId = uniquePendingIdForCommand(
    String(ev.command_text || "")
  );
  return pendingId ? removePendingById(pendingId) : false;
}

function removePendingById(pendingId, skipRender) {
  if (!pendingId) { return false; }
  var removed = false;
  Object.keys(pendingNodes).some(function(text) {
    var pendingIds = pendingNodes[text] || [];
    var index = pendingIds.indexOf(pendingId);
    if (index < 0) { return false; }
    pendingIds.splice(index, 1);
    if (!pendingIds.length) { delete pendingNodes[text]; }
    removed = true;
    return true;
  });
  if (removed && skipRender !== true) {
    renderPendingAggregate();
    updateAssistantPendingState();
  }
  return removed;
}

function removeOldestPendingCommand() {
  var keys = Object.keys(pendingNodes);
  if (!keys.length) { return false; }
  return removePendingForCommand(keys[0]);
}

function assistantPendingLabel(count) {
  if (count <= 1) { return t("assistantWaiting"); }
  return t("assistantPendingCount").replace("{count}", String(count));
}

function pendingCommandCount() {
  return Object.keys(pendingNodes).reduce(function (total, key) {
    return total + pendingNodes[key].length;
  }, 0);
}

function updateAssistantPendingState() {
  var statusNode = document.getElementById("assistant-pending-status");
  var count = pendingCommandCount();
  if (statusNode) {
    statusNode.textContent = count > 0 ? assistantPendingLabel(count) : "";
  }
  logBox.setAttribute("aria-busy", count > 0 ? "true" : "false");
}

function refreshPendingLabels() {
  Array.prototype.forEach.call(logBox.querySelectorAll(".message-pending"), function (message) {
    message.setAttribute("data-full-text", t("assistantThinking"));
    message.setAttribute("aria-label", t("assistantWaiting"));
    var narration = message.querySelector(".narration");
    if (narration) { narration.textContent = t("assistantThinking"); }
  });
}

function pollHistory() {
  if (isMicroMachineCommandMode()) { return; }
  fetch("/api/history?after=" + lastSeq + authJoin)
    .then(function (response) { return response.json(); })
    .then(function (data) {
      (data.events || []).forEach(appendLog);
      if (typeof data.latest === "number" && data.latest > lastSeq) {
        lastSeq = data.latest;
      }
    })
    .catch(function () { /* 서버가 잠시 응답하지 않아도 폴링은 계속됩니다. */ });
}

function currentEventBlackboardDirectory() {
  var input = document.getElementById("micromachine-blackboard-dir");
  return input ? String(input.value || "").trim() : "";
}

function resetEventCursorForBlackboard(directory) {
  var normalized = String(directory || "").trim();
  if (commandEventBlackboardDir === normalized) { return false; }
  commandEventBlackboardDir = normalized;
  lastEventSeq = 0;
  commandEventPollWonInitialHydration = false;
  return true;
}

function eventSourceUrl() {
  var params = [];
  var directory = currentEventBlackboardDirectory();
  resetEventCursorForBlackboard(directory);
  if (token) { params.push("token=" + encodeURIComponent(token)); }
  if (lastEventSeq > 0) {
    params.push("after=" + encodeURIComponent(String(lastEventSeq)));
  }
  if (directory) {
    params.push("blackboard_dir=" + encodeURIComponent(directory));
  }
  return "/api/events" + (params.length ? "?" + params.join("&") : "");
}

function startPollingFallback() {
  if (fallbackPollingIntervals.length) { return; }
  pollHistory();
  pollState();
  pollMicroMachineStatus();
  fallbackPollingIntervals = [
    window.setInterval(pollHistory, POLL_INTERVAL_MS),
    window.setInterval(pollState, POLL_INTERVAL_MS),
    window.setInterval(pollMicroMachineStatus, POLL_INTERVAL_MS)
  ];
}

function stopPollingFallback() {
  fallbackPollingIntervals.forEach(function(intervalId) {
    window.clearInterval(intervalId);
  });
  fallbackPollingIntervals = [];
  microMachinePollQueued = false;
  if (
    microMachinePollAbortController &&
    typeof microMachinePollAbortController.abort === "function"
  ) {
    microMachinePollAbortController.abort();
  }
}

function refreshEventPollingFallback() {
  if (Object.keys(commandEventFailedSources).length > 0) {
    startPollingFallback();
    return;
  }
  if (commandEventHealthy) {
    stopPollingFallback();
  }
}

function markEventSourceFailed(source) {
  commandEventFailedSources[String(source || "event source")] = true;
  refreshEventPollingFallback();
}

function markEventSourceRecovered(source) {
  delete commandEventFailedSources[String(source || "event source")];
  refreshEventPollingFallback();
}

function serverEventRegressesOperation(envelope) {
  var operationId = String(
    envelope && (envelope.operation_id || envelope.update_id) || ""
  );
  var generation = Number(envelope && envelope.generation || 0);
  var frame = Number(envelope && envelope.game_frame);
  if (!operationId) { return false; }
  var regresses = false;
  Object.keys(operationRecords).some(function(key) {
    var record = operationRecords[key];
    if (
      !record ||
      (
        record.operationId !== operationId &&
        record.updateId !== operationId
      )
    ) {
      return false;
    }
    if (
      generation > 0 &&
      record.operationGeneration > 0 &&
      generation < record.operationGeneration
    ) {
      regresses = true;
      return true;
    }
    if (
      Number.isFinite(frame) &&
      frame >= 0 &&
      record.telemetryFrame >= 0 &&
      frame < record.telemetryFrame
    ) {
      regresses = true;
      return true;
    }
    return false;
  });
  return regresses;
}

function applyHistoryEventPayload(payload) {
  if (!payload || typeof payload !== "object") { return; }
  var historySeq = Number(payload.seq || 0);
  if (historySeq > 0 && historySeq <= lastSeq) { return; }
  appendLog(payload);
  if (historySeq > lastSeq) { lastSeq = historySeq; }
}

function applyEventSnapshot(payload) {
  if (!payload || typeof payload !== "object") { return; }
  commandEventFailedSources = {};
  var history = payload.history || {};
  if (history.error) {
    commandEventFailedSources.history = true;
  }
  (history.events || []).forEach(applyHistoryEventPayload);
  if (typeof history.latest === "number" && history.latest > lastSeq) {
    lastSeq = history.latest;
  }
  if (!isMicroMachineCommandMode() && payload.state) {
    renderState(payload.state);
  }
  if (payload.state && payload.state.error) {
    commandEventFailedSources.state = true;
  }
  if (payload.micromachine_status) {
    hydrateTacticalRadioState(payload.micromachine_status);
    safeRenderMicroMachineStatus(
      payload.micromachine_status,
      { suppressPlanAnnouncements: true }
    );
    if (payload.micromachine_status.status === "source_error") {
      commandEventFailedSources.micromachine_status = true;
    }
  }
  commandEventAwaitingInitialSnapshot = false;
}

function serverEventMatchesCurrentBlackboard(envelope, payload) {
  if (!payload || typeof payload !== "object") { return true; }
  var currentDirectory = currentEventBlackboardDirectory();
  var eventDirectory = String(payload.blackboard_dir || "").trim();
  var expectedScope = String(envelope && envelope.blackboard_scope_id || "");
  var payloadScope = String(payload.blackboard_scope_id || "");
  if (expectedScope && payloadScope && expectedScope !== payloadScope) {
    return false;
  }
  return !currentDirectory || !eventDirectory || currentDirectory === eventDirectory;
}

function operationRecordMatchesServerEvent(operationId, updateId) {
  return Object.keys(operationRecords).some(function(key) {
    var record = operationRecords[key];
    return Boolean(
      record &&
      (
        record.pendingId === operationId ||
        record.operationId === operationId ||
        (Boolean(updateId) && record.updateId === updateId)
      )
    );
  });
}

function applyCommandReceivedEvent(envelope, payload) {
  if (!serverEventMatchesCurrentBlackboard(envelope, payload)) { return; }
  var text = String(payload.command_text || "");
  var operationId = String(
    envelope.operation_id || envelope.update_id || ""
  );
  var updateId = String(envelope.update_id || "");
  if (!text || !operationId) { return; }
  if (!operationRecordMatchesServerEvent(operationId, updateId)) {
    if (activeCommandConsoleRecord.state === "idle") {
      beginActiveCommandConsole(text, operationId);
    } else {
      beginOperationRecord(text, operationId);
    }
  }
  bindOperationRecordUpdate(
    text,
    operationId,
    String(envelope.blackboard_scope_id || ""),
    updateId
  );
  if (
    activeCommandConsoleRecord.pendingId === operationId ||
    (Boolean(updateId) && activeCommandConsoleRecord.updateId === updateId)
  ) {
    var activeBound = bindActiveCommandConsoleUpdate(
      text,
      operationId,
      String(envelope.blackboard_scope_id || ""),
      updateId
    );
    if (activeBound) {
      renderActiveCommandConsole({
        status: "received",
        command_text: text,
        update_id: updateId,
        blackboard_scope_id: String(envelope.blackboard_scope_id || ""),
        consumption_status: "received"
      }, true);
    }
  }
}

function applyEventSourceError(envelope, payload) {
  if (!serverEventMatchesCurrentBlackboard(envelope, payload)) { return; }
  var source = String(payload.source || "event source");
  var error = String(payload.error || "source unavailable");
  var node = document.getElementById("micromachine-status");
  if (node) {
    node.textContent = commandUiText(
      "실시간 상태 소스 오류: " + source + " · " + error,
      "Real-time state source error: " + source + " · " + error,
      "实时状态源错误：" + source + " · " + error
    );
  }
  markEventSourceFailed(source);
}

function applyOperationSemanticEvent(envelope, payload) {
  if (!serverEventMatchesCurrentBlackboard(envelope, payload)) { return; }
  var eventEpoch = String(payload.session_epoch || "");
  if (
    operationConsoleSessionEpoch &&
    eventEpoch &&
    operationConsoleSessionEpoch !== eventEpoch
  ) {
    return;
  }
  var scopeId = String(
    payload.blackboard_scope_id ||
    envelope.blackboard_scope_id ||
    operationConsoleScopeId ||
    ""
  );
  var operationId = String(
    payload.operation_id || envelope.operation_id || ""
  );
  var generation = Number(
    payload.generation || envelope.generation || 0
  );
  if (!operationId || generation <= 0) { return; }
  var record = operationRecords[
    operationRecordKey(scopeId, operationId)
  ];
  if (!record || Number(record.operationGeneration || 0) !== generation) {
    return;
  }
  if (!operationEventMatchesRecordUpdate(envelope, payload, record)) {
    return;
  }
  record.data = Object.assign({}, record.data || {}, {
    semantic_timeline: mergeOperationSemanticTimeline(
      record.data && record.data.semantic_timeline,
      [payload]
    )
  });
  renderOperationRecords();
  announceOperationLifecycleEvent(envelope, payload, scopeId, record);
}

function applyServerEvent(event) {
  var envelope;
  try {
    envelope = JSON.parse(event.data || "{}");
  } catch (error) {
    return;
  }
  var eventType = String(envelope.event_type || event.type || "message");
  var eventSeq = Number(envelope.event_seq || event.lastEventId || 0);
  var payload = envelope.payload || {};
  if (eventType === "snapshot") {
    lastEventSeq = Number.isFinite(eventSeq) && eventSeq >= 0 ? eventSeq : 0;
    if (commandEventPollWonInitialHydration) {
      commandEventPollWonInitialHydration = false;
      commandEventFailedSources = {};
    } else {
      applyEventSnapshot(payload);
    }
    if (commandEventSource) {
      commandEventHealthy = true;
      refreshEventPollingFallback();
    }
    return;
  }
  if (
    eventSeq > 0 &&
    eventSeq <= lastEventSeq
  ) {
    return;
  }
  if (serverEventRegressesOperation(envelope)) { return; }
  if (eventSeq > lastEventSeq) { lastEventSeq = eventSeq; }
  if (eventType === "source_error") {
    applyEventSourceError(envelope, payload);
    return;
  }
  if (eventType === "source_recovered") {
    markEventSourceRecovered(payload.source);
    return;
  }
  if (eventType === "command_received") {
    applyCommandReceivedEvent(envelope, payload);
    return;
  }
  if (eventType === "history") {
    markEventSourceRecovered("history");
    applyHistoryEventPayload(payload);
    return;
  }
  if (eventType === "operation_event") {
    applyOperationSemanticEvent(envelope, payload);
    return;
  }
  if (eventType === "state") {
    markEventSourceRecovered("state");
    if (!isMicroMachineCommandMode()) { renderState(payload); }
    return;
  }
  if (
    eventType === "micromachine_status" ||
    eventType === "micromachine_submission"
  ) {
    if (serverEventMatchesCurrentBlackboard(envelope, payload)) {
      if (eventType === "micromachine_status") {
        markEventSourceRecovered("micromachine_status");
      } else {
        announceAcceptedTacticalPlan(payload, "event");
      }
      safeRenderMicroMachineStatus(payload);
    }
  }
}

function scheduleEventReconnect() {
  if (commandEventReconnectTimer !== null) { return; }
  commandEventReconnectTimer = window.setTimeout(function() {
    commandEventReconnectTimer = null;
    connectEventChannel();
  }, Math.min(POLL_INTERVAL_MS, 1000));
}

function connectEventChannel() {
  if (typeof window.EventSource !== "function") {
    commandEventAwaitingInitialSnapshot = false;
    startPollingFallback();
    return;
  }
  if (commandEventSource) {
    commandEventSource.close();
    commandEventSource = null;
  }
  commandEventPollWonInitialHydration = false;
  commandEventAwaitingInitialSnapshot = true;
  startPollingFallback();
  var source = new window.EventSource(eventSourceUrl());
  commandEventSource = source;
  source.onopen = function() {
    if (commandEventSource !== source) { return; }
    commandEventHealthy = true;
    refreshEventPollingFallback();
  };
  [
    "snapshot",
    "history",
    "state",
    "micromachine_status",
    "micromachine_submission",
    "command_received",
    "operation_event",
    "source_error",
    "source_recovered"
  ].forEach(function(eventType) {
    source.addEventListener(eventType, function(event) {
      if (commandEventSource !== source) { return; }
      applyServerEvent(event);
    });
  });
  source.onerror = function() {
    if (commandEventSource !== source) { return; }
    commandEventHealthy = false;
    source.close();
    commandEventSource = null;
    startPollingFallback();
    scheduleEventReconnect();
  };
}

function reconnectEventChannel() {
  if (commandEventReconnectTimer !== null) {
    window.clearTimeout(commandEventReconnectTimer);
    commandEventReconnectTimer = null;
  }
  connectEventChannel();
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function setLlmStatus(state, labelKey, message) {
  var normalized = state || "checking";
  var statusNode = document.getElementById("llm-status");
  var labelNode = document.getElementById("llm-status-label");
  var messageNode = document.getElementById("llm-status-message");
  if (!statusNode || !labelNode || !messageNode) { return; }
  statusNode.className = "llm-status llm-status-" + normalized;
  statusNode.setAttribute("data-llm-state", normalized);
  labelNode.setAttribute("data-i18n", labelKey);
  labelNode.textContent = t(labelKey);
  messageNode.textContent = message;
}

function renderMicroMachineStatePlaceholder() {
  latestState = null;
  setText("state-minerals", "-");
  setText("state-vespene", "-");
  setText("state-supply", "-");
  setText("state-workers", "-");
  setText("state-army", "-");
  setText("state-structures", "-");
  setText("state-availability", t("microMachineStateDashboardDisabled"));
  setText("connection-status", t("microMachineStateConnection"));
  setText("strategy-briefing", t("microMachineStateBriefing"));
  if (!activeCommandConsoleRecord.data) {
    setMicroMachineText("battlefield-command-state", t("commandConsoleIdleState"));
    setMicroMachineText("battlefield-frame", "-");
    setMicroMachineText("battlefield-force", "-");
    setMicroMachineText("battlefield-posture", "-");
    setMicroMachineText("battlefield-unassigned", "-");
    setMicroMachineText("battlefield-readiness", "-");
    setMicroMachineText("battlefield-transfer", "-");
    setMicroMachineText("battlefield-integrity", "-");
    setMicroMachineText("battlefield-production-waits", "-");
    setMicroMachineText("battlefield-control-summary", t("battlefieldControlWaiting"));
  }
}

function renderState(data) {
  if (isMicroMachineCommandMode()) {
    renderMicroMachineStatePlaceholder();
    return;
  }
  if (!data || data.available === false) {
    latestState = null;
    setText("state-availability", t("noState"));
    setText("connection-status", t("connectionWaiting"));
    setText("strategy-briefing", t("briefingWaiting"));
    return;
  }
  latestState = data;
  setText("state-minerals", String(data.minerals));
  setText("state-vespene", String(data.vespene));
  setText("state-supply", data.supply_used + " / " + data.supply_cap);
  var workers = (data.own_units && data.own_units.SCV) || 0;
  setText("state-workers", workers + t("workerUnit") + " (" + t("idleLabel") + " " + (data.idle_worker_count || 0) + t("workerUnit") + ")");
  setText("state-army", (data.army_count || 0) + t("workerUnit"));
  var structures = data.own_structures || {};
  var parts = Object.keys(structures).map(function (name) {
    return name + " " + structures[name];
  });
  setText("state-structures", parts.length ? parts.join(", ") : t("noStructures"));
  setText(
    "state-availability",
    data.observation_complete === false ? t("incompleteObservation") : ""
  );
  setText("connection-status", t("connectionReady") + " · " + Math.floor(data.game_time_seconds || 0) + "s");
  renderStrategyBriefing(data);
}

function sumValues(source) {
  if (!source) { return 0; }
  return Object.keys(source).reduce(function (total, key) {
    var value = Number(source[key] || 0);
    return total + (Number.isFinite(value) ? value : 0);
  }, 0);
}

function renderStrategyBriefing(data) {
  var workers = (data.own_units && data.own_units.SCV) || 0;
  var enemyUnits = sumValues(data.visible_enemy_units);
  var enemyStructures = sumValues(data.visible_enemy_structures);
  var structures = data.own_structures || {};
  var recentTexts = recentEvents.slice(-5).map(function (ev) {
    return ev.command_text || "";
  }).filter(Boolean);
  var compactedTexts = compactedContext.commands.slice(-5);
  var strategyTexts = compactedTexts.concat(recentTexts);
  var successful = recentEvents.filter(function (ev) {
    return isSuccessfulRecordStatus(ev.status);
  }).length + compactedContext.successful;
  var failed = recentEvents.filter(function (ev) {
    return isFailureRecordStatus(ev.status);
  }).length + compactedContext.failed;
  var suggestions = [];
  if ((data.supply_left || 0) <= 2) { suggestions.push(t("briefingSuggestionSupply")); }
  if (enemyUnits + enemyStructures === 0) { suggestions.push(t("briefingSuggestionScout")); }
  if ((data.army_count || 0) === 0) { suggestions.push(t("briefingSuggestionArmy")); }
  if (!suggestions.length) { suggestions.push(t("briefingSuggestionStable")); }
  var risks = [];
  if ((data.army_count || 0) === 0) { risks.push(t("riskNoArmy")); }
  if (enemyUnits + enemyStructures === 0) { risks.push(t("riskNoScout")); }
  if ((data.supply_left || 0) <= 2) { risks.push(t("riskSupply")); }
  if (!risks.length) { risks.push(t("riskStable")); }
  var strategy = inferStrategy(strategyTexts, structures);
  var enemyLine = enemyUnits + enemyStructures > 0
    ? enemyUnits + " / " + enemyStructures
    : t("briefingEnemyNone");
  var evidenceSummary = buildKoreanEvidenceSummary(
    data,
    workers,
    enemyUnits,
    enemyStructures,
    buildKoreanCommandHistoryEvidence(strategyTexts, successful, failed),
    buildKoreanOutcomeRecordSummary(recentEvents, compactedContext),
    buildKoreanStandingOrderEvidence(data.standing_orders),
    buildKoreanCompactedMemoryEvidence(data.compacted_memory),
    buildKoreanLlmSummaryEvidence(data.llm_summary)
  );
  var briefing = document.getElementById("strategy-briefing");
  briefing.innerHTML = "";
  briefing.appendChild(briefingBlock(t("briefingCurrentStrategy"), strategy));
  briefing.appendChild(briefingBlock(t("briefingEvidence"), evidenceSummary));
  briefing.appendChild(briefingBlock(
    t("briefingProgress"),
    t("briefingEconomy") + ": " + data.minerals + "M / " + data.vespene + "G, " + workers + t("workerUnit") + "\\n" +
    t("briefingSupply") + ": " + data.supply_used + "/" + data.supply_cap + " (" + (data.supply_left || 0) + ")\\n" +
    t("briefingForces") + ": " + (data.army_count || 0) + t("workerUnit") + "\\n" +
    t("briefingEnemy") + ": " + enemyLine + "\\n" +
    t("progressRecent") + ": " + (recentTexts.length ? recentTexts.join(" / ") : "-") + "\\n" +
    "OK/Needs attention: " + successful + " / " + failed
  ));
  briefing.appendChild(briefingBlock(t("briefingMemory"), compactedContextSummary()));
  briefing.appendChild(briefingBlock(t("briefingRisk"), risks.join("\\n")));
  var details = document.createElement("details");
  var adviceRequested = hasRecentExplicitAdviceRequest(recentEvents);
  details.open = briefingAdviceToggleEnabled || adviceRequested;
  if (typeof details.setAttribute === "function") {
    details.setAttribute("data-advice-state", "suppressed");
    details.setAttribute("data-advice-requested", adviceRequested ? "true" : "false");
    details.setAttribute("data-advice-toggle-enabled", briefingAdviceToggleEnabled ? "true" : "false");
  }
  var summary = document.createElement("summary");
  summary.textContent = t("briefingAdvice");
  details.appendChild(summary);
  if (typeof details.addEventListener === "function") {
    details.addEventListener("toggle", function () {
      briefingAdviceToggleEnabled = !!details.open;
      if (typeof details.setAttribute === "function") {
        details.setAttribute("data-advice-toggle-enabled", briefingAdviceToggleEnabled ? "true" : "false");
      }
      setBriefingAdviceVisible(details, suggestions, !!details.open);
    });
  }
  setBriefingAdviceVisible(details, suggestions, !!details.open);
  briefing.appendChild(details);
}

function hasRecentExplicitAdviceRequest(events) {
  var candidates = (events || []).slice(-8);
  for (var i = candidates.length - 1; i >= 0; i -= 1) {
    var ev = candidates[i] || {};
    if (isExplicitAdviceRequestEvent(ev)) { return true; }
  }
  return false;
}

function isExplicitAdviceRequestEvent(ev) {
  if (!ev || ev.status !== "read_only") { return false; }
  var text = String(ev.command_text || "").toLowerCase().replace(/\\s+/g, "");
  if (!text) { return false; }
  var explicitMarkers = [
    "추천", "조언", "다음할일", "지금할일", "지금할거", "지금할것",
    "뭐해야", "뭘해야", "뭐하면", "뭘하면", "뭐하지", "뭐할까", "뭘할까",
    "whatshould", "nextaction", "nexttodo", "recommend", "advice", "advise"
  ];
  for (var i = 0; i < explicitMarkers.length; i += 1) {
    if (text.indexOf(explicitMarkers[i]) >= 0) { return true; }
  }
  return false;
}

function setBriefingAdviceVisible(details, suggestions, visible) {
  if (!details) { return; }
  if (visible) {
    if (!details._briefingAdviceNode) {
      var advice = createBriefingAdviceBlock(suggestions);
      details._briefingAdviceNode = advice;
      details.appendChild(advice);
    }
    if (typeof details.setAttribute === "function") {
      details.setAttribute("data-advice-state", "visible");
      details.setAttribute("aria-expanded", "true");
    }
    return;
  }
  var existingAdvice = details._briefingAdviceNode;
  if (existingAdvice) {
    if (existingAdvice.parentNode && typeof existingAdvice.parentNode.removeChild === "function") {
      existingAdvice.parentNode.removeChild(existingAdvice);
    } else if (details.children) {
      details.children = Array.prototype.filter.call(details.children, function (child) {
        return child !== existingAdvice;
      });
    }
    details._briefingAdviceNode = null;
  }
  if (typeof details.setAttribute === "function") {
    details.setAttribute("data-advice-state", "suppressed");
    details.setAttribute("aria-expanded", "false");
  }
}

function createBriefingAdviceBlock(suggestions) {
  var advice = document.createElement("div");
  advice.className = "briefing-block";
  advice.textContent = suggestions.join("\\n");
  return advice;
}

function buildKoreanEvidenceSummary(
  data,
  workers,
  enemyUnits,
  enemyStructures,
  historyEvidence,
  outcomeEvidence,
  standingOrderEvidence,
  compactedMemoryEvidence,
  llmSummaryEvidence
) {
  var supplyLeft = Number(data.supply_left || 0);
  var armyCount = Number(data.army_count || 0);
  var enemyText = enemyUnits + enemyStructures > 0
    ? "적 " + enemyUnits + "기/건물 " + enemyStructures + "개 관측"
    : "적 관측 없음";
  var observationText = data.observation_complete === false
    ? "관측 불완전"
    : "관측 정상";
  var baseEvidence = (
    "현재 관측 요약: 미네랄 " + data.minerals +
    ", 가스 " + data.vespene +
    ", 보급 " + data.supply_used + "/" + data.supply_cap +
    "(여유 " + supplyLeft + "), SCV " + workers +
    "기, 병력 " + armyCount + "기, " + enemyText +
    ", " + observationText + ".\\n" + historyEvidence +
    "\\n" + outcomeEvidence +
    "\\n" + standingOrderEvidence
  );
  var optionalEvidence = buildDistinctStrategicEvidenceLines(
    baseEvidence,
    [compactedMemoryEvidence, llmSummaryEvidence]
  ).join("\\n");
  return baseEvidence + (optionalEvidence ? "\\n" + optionalEvidence : "");
}

function buildDistinctStrategicEvidenceLines(baseEvidence, candidateLines) {
  var context = String(baseEvidence || "");
  var accepted = [];
  var acceptedChars = 0;
  candidateLines.forEach(function (line) {
    splitStrategicEvidenceChunks(line).forEach(function (chunk) {
      if (accepted.length >= MAX_OPTIONAL_STRATEGIC_EVIDENCE_LINES) { return; }
      var text = String(chunk || "").trim();
      if (isRedactionOnlyStrategicEvidence(text) && accepted.length) {
        var previous = accepted[accepted.length - 1];
        var replacement = limitStrategicEvidenceText(
          previous + " " + text,
          Math.min(
            MAX_STRATEGIC_EVIDENCE_LINE_CHARS,
            MAX_OPTIONAL_STRATEGIC_EVIDENCE_CHARS - acceptedChars + previous.length + 1
          )
        );
        acceptedChars += replacement.length - previous.length;
        accepted[accepted.length - 1] = replacement;
        return;
      }
      if (!text || !hasDistinctStrategicContext(text, context)) { return; }
      var remaining = MAX_OPTIONAL_STRATEGIC_EVIDENCE_CHARS - acceptedChars;
      if (remaining < 32) { return; }
      var boundedText = limitStrategicEvidenceText(
        text,
        Math.min(MAX_STRATEGIC_EVIDENCE_LINE_CHARS, remaining)
      );
      accepted.push(boundedText);
      acceptedChars += boundedText.length + 1;
      context += "\\n" + text;
    });
  });
  return accepted;
}

function isRedactionOnlyStrategicEvidence(text) {
  return String(text || "").indexOf("[redacted]") >= 0 &&
    strategicContextTokens(text).length < 2;
}

function splitStrategicEvidenceChunks(line) {
  var normalized = redactSensitiveBriefingText(line)
    .replace(/([.!?。！？])/g, "$1\\n");
  return normalized.split(/\\n+|\\s+\\/\\s+/).map(function (chunk) {
    return chunk.trim();
  }).filter(Boolean);
}

function limitStrategicEvidenceText(text, maxChars) {
  var normalized = redactSensitiveBriefingText(text);
  var limit = Math.max(24, Number(maxChars || 0));
  if (normalized.length <= limit) { return normalized; }
  return normalized.slice(0, Math.max(0, limit - 8)).trim() + "...(축약)";
}

function hasDistinctStrategicContext(candidate, context) {
  var candidateTokens = strategicContextTokens(candidate);
  if (candidateTokens.length < 2) { return false; }
  var contextTokenSet = {};
  strategicContextTokens(context).forEach(function (token) {
    contextTokenSet[token] = true;
  });
  var unseen = [];
  candidateTokens.forEach(function (token) {
    if (!contextTokenSet[token] && unseen.indexOf(token) < 0) {
      unseen.push(token);
    }
  });
  if (!unseen.length) { return false; }
  return unseen.length >= 2 || unseen.length / candidateTokens.length >= 0.25;
}

function strategicContextTokens(text) {
  var stopTokens = {
    "압축": true,
    "메모리": true,
    "입력": true,
    "llm": true,
    "요약": true,
    "현재": true,
    "관측": true,
    "최근": true,
    "흐름": true,
    "성과": true,
    "차단": true,
    "상비": true,
    "명령": true,
    "정상": true,
    "입니다": true,
    "그리고": true,
    "또는": true,
    "redacted": true,
    "the": true,
    "and": true
  };
  var matches = redactSensitiveBriefingText(text)
    .toLowerCase()
    .match(/[가-힣a-z0-9]+/g) || [];
  var tokens = [];
  matches.forEach(function (token) {
    if (token.length < 2 || stopTokens[token]) { return; }
    if (tokens.indexOf(token) < 0) {
      tokens.push(token);
    }
  });
  return tokens;
}

function redactSensitiveBriefingText(text) {
  return String(text || "")
    .replace(/\\bsk-[A-Za-z0-9_\\-.]{8,}\\b/g, "[redacted]")
    .replace(/\\bxai-[A-Za-z0-9_\\-.]{8,}\\b/g, "[redacted]")
    .replace(/\\bAIza[A-Za-z0-9_\\-.]{8,}\\b/g, "[redacted]")
    .replace(/\\s+/g, " ")
    .trim();
}

function isUnsafeBriefingKey(key) {
  var compact = String(key || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return (
    compact.indexOf("prompt") >= 0 ||
    compact.indexOf("apikey") >= 0 ||
    compact === "key" ||
    compact.indexOf("secret") >= 0
  );
}

function normalizeBriefingSummaryInput(value) {
  if (value === null || value === undefined || value === false) { return ""; }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return redactSensitiveBriefingText(value);
  }
  if (Array.isArray(value)) {
    return value.map(normalizeBriefingSummaryInput).filter(Boolean).join(" / ");
  }
  if (typeof value === "object") {
    var preferredKeys = [
      "korean_summary", "summary", "text", "content", "briefing",
      "evidence", "llm_summary", "memory_summary"
    ];
    for (var i = 0; i < preferredKeys.length; i += 1) {
      if (Object.prototype.hasOwnProperty.call(value, preferredKeys[i])) {
        var preferred = normalizeBriefingSummaryInput(value[preferredKeys[i]]);
        if (preferred) { return preferred; }
      }
    }
    return Object.keys(value).filter(function (key) {
      return !isUnsafeBriefingKey(key);
    }).map(function (key) {
      return normalizeBriefingSummaryInput(value[key]);
    }).filter(Boolean).join(" / ");
  }
  return "";
}

function buildKoreanCompactedMemoryEvidence(memoryInput) {
  if (!memoryInput) { return ""; }
  if (typeof memoryInput === "object" && !Array.isArray(memoryInput)) {
    var total = Number(memoryInput.total || memoryInput.count || 0);
    var successful = Number(memoryInput.successful || memoryInput.success || 0);
    var failed = Number(memoryInput.failed || memoryInput.blocked || 0);
    var commands = Array.isArray(memoryInput.commands) ? memoryInput.commands : [];
    if (total > 0 || successful > 0 || failed > 0 || commands.length) {
      var themeCounts = {};
      commands.forEach(function (command) {
        addThemeCount(themeCounts, classifyCommandTheme(command));
      });
      var themeText = commands.length
        ? ", 최근 흐름은 " + rankedThemeText(themeCounts, "일반 지시") + " 중심"
        : "";
      return "압축 메모리 입력: 누적 " + total + "건, 성공/정보 " +
        successful + "건, 차단/확인 필요 " + failed + "건" + themeText + "입니다.";
    }
  }
  var normalized = normalizeBriefingSummaryInput(memoryInput);
  return normalized ? "압축 메모리 입력: " + normalized : "";
}

function buildKoreanLlmSummaryEvidence(summaryInput) {
  var normalized = normalizeBriefingSummaryInput(summaryInput);
  return normalized ? "LLM 요약 입력: " + normalized : "";
}

function buildKoreanStandingOrderEvidence(standingOrders) {
  var fallbackLabels = {
    keep_worker_production: "지속 SCV 생산",
    prevent_supply_block: "보급 차단 방지"
  };
  var activeKinds = Array.isArray(standingOrders && standingOrders.active_kinds)
    ? standingOrders.active_kinds
    : [];
  var labels = (standingOrders && standingOrders.korean_labels) || {};
  var activeLabels = activeKinds.map(function (kind) {
    var key = String(kind || "").trim();
    return labels[key] || fallbackLabels[key] || "";
  }).filter(Boolean);
  if (!activeLabels.length) {
    return "상비 명령 요약: 활성 상비 명령이 없어 현재 관측과 최근 명령 기록을 우선합니다.";
  }
  var priorities = [];
  if (activeKinds.indexOf("keep_worker_production") >= 0) {
    priorities.push("경제 생산 유지");
  }
  if (activeKinds.indexOf("prevent_supply_block") >= 0) {
    priorities.push("보급 차단 예방");
  }
  if (!priorities.length) {
    priorities.push("등록된 상비 정책 유지");
  }
  return "상비 명령 요약: " + activeLabels.join("/") +
    " 정책이 활성이라 " + priorities.join("와 ") +
    " 항목을 계속 우선합니다.";
}

function buildKoreanCommandHistoryEvidence(historyTexts, successful, failed) {
  var texts = (historyTexts || []).map(function (text) {
    return String(text || "").trim();
  }).filter(Boolean);
  var totalOutcomes = Math.max(0, Number(successful || 0) + Number(failed || 0));
  if (!texts.length && totalOutcomes < 1) {
    return "최근 명령 흐름: 기록된 명령이 없어 현재 관측만 근거로 판단합니다.";
  }
  var themeCounts = {};
  texts.forEach(function (text) {
    var theme = classifyCommandTheme(text);
    themeCounts[theme] = (themeCounts[theme] || 0) + 1;
  });
  var themePriority = ["생산", "건설", "정찰", "상황 확인", "전술 조작", "일반 지시"];
  var rankedThemes = themePriority.filter(function (theme) {
    return themeCounts[theme] > 0;
  }).sort(function (left, right) {
    return themeCounts[right] - themeCounts[left] ||
      themePriority.indexOf(left) - themePriority.indexOf(right);
  });
  var focusText = rankedThemes.length
    ? rankedThemes.slice(0, 2).join("/") + " 중심"
    : "일반 지시 중심";
  var outcomeText = totalOutcomes > 0
    ? "성공/정보 " + Number(successful || 0) + "건, 확인 필요 " + Number(failed || 0) + "건"
    : "아직 실행 결과 집계 전";
  return "최근 명령 흐름: 최근 " + texts.length + "건은 " + focusText +
    "이며, " + outcomeText + "입니다.";
}

function isSuccessfulRecordStatus(status) {
  return ["executed", "partially_executed", "read_only"].indexOf(status) >= 0;
}

function isFailureRecordStatus(status) {
  return ["blocked", "clarification"].indexOf(status) >= 0;
}

function buildKoreanOutcomeRecordSummary(events, compacted) {
  var successful = Number((compacted && compacted.successful) || 0);
  var failed = Number((compacted && compacted.failed) || 0);
  var readOnly = Number((compacted && compacted.readOnly) || 0);
  var successfulThemes = cloneCountMap(compacted && compacted.successfulThemes);
  var failedThemes = cloneCountMap(compacted && compacted.failedThemes);
  var failureReasons = cloneCountMap(compacted && compacted.failureReasons);
  (events || []).forEach(function (ev) {
    var status = ev.status || "";
    var theme = classifyCommandTheme(ev.command_text || "");
    if (isSuccessfulRecordStatus(status)) {
      successful += 1;
      addThemeCount(successfulThemes, theme);
      if (status === "read_only") { readOnly += 1; }
    }
    if (isFailureRecordStatus(status)) {
      failed += 1;
      addThemeCount(failedThemes, theme);
      addThemeCount(failureReasons, classifyFailureReasonTheme(ev.narration || ev.command_text || ""));
    }
  });
  var total = successful + failed;
  if (total < 1) {
    return "성과/차단 요약: 아직 성공 또는 차단 기록이 없어 현재 관측과 최근 명령 흐름을 우선합니다.";
  }
  var balance = successful >= failed ? "성공 흐름이 우세합니다" : "차단/확인 필요 흐름이 더 많습니다";
  var successFocus = rankedThemeText(successfulThemes, "성공 기록 없음");
  var failedFocus = rankedThemeText(failedThemes, "차단 기록 없음");
  var reasonFocus = rankedThemeText(failureReasons, "차단 사유 없음");
  var readOnlyText = readOnly > 0 ? ", 그중 정보 확인 " + readOnly + "건" : "";
  return (
    "성과/차단 요약: 성공/정보 " + successful + "건" + readOnlyText +
    ", 차단/확인 필요 " + failed + "건으로 " + balance + ". " +
    "성공은 " + successFocus + " 중심이고, 차단은 " + failedFocus +
    " 중심이며, 주요 차단 사유는 " + reasonFocus + "입니다."
  );
}

function cloneCountMap(source) {
  var result = {};
  if (!source) { return result; }
  Object.keys(source).forEach(function (key) {
    var value = Number(source[key] || 0);
    if (value > 0) { result[key] = value; }
  });
  return result;
}

function addThemeCount(bucket, theme, amount) {
  var key = String(theme || "").trim() || "일반 지시";
  var increment = Number(amount || 1);
  bucket[key] = (Number(bucket[key] || 0) + (Number.isFinite(increment) ? increment : 1));
}

function rankedThemeText(themeCounts, fallback) {
  var keys = Object.keys(themeCounts || {}).filter(function (key) {
    return Number(themeCounts[key] || 0) > 0;
  });
  if (!keys.length) { return fallback; }
  var priority = [
    "생산", "건설", "정찰", "상황 확인", "전술 조작", "일반 지시",
    "자원/조건 확인", "보급 확인", "위치/대상 확인", "시야/정찰 확인",
    "추가 확인", "LLM 설정 확인", "실행 조건 확인"
  ];
  keys.sort(function (left, right) {
    var countDiff = Number(themeCounts[right] || 0) - Number(themeCounts[left] || 0);
    if (countDiff) { return countDiff; }
    var leftPriority = priority.indexOf(left);
    var rightPriority = priority.indexOf(right);
    if (leftPriority < 0) { leftPriority = priority.length; }
    if (rightPriority < 0) { rightPriority = priority.length; }
    return leftPriority - rightPriority || left.localeCompare(right);
  });
  return keys.slice(0, 2).join("/");
}

function classifyCommandTheme(text) {
  var compact = String(text || "").toLowerCase().replace(/\\s+/g, "");
  if (!compact) { return "일반 지시"; }
  if (compact.indexOf("정찰") >= 0 || compact.indexOf("scout") >= 0) {
    return "정찰";
  }
  if (
    compact.indexOf("상태") >= 0 || compact.indexOf("요약") >= 0 ||
    compact.indexOf("알려") >= 0 || compact.indexOf("뭐해야") >= 0 ||
    compact.indexOf("왜안") >= 0 || compact.indexOf("전략") >= 0
  ) {
    return "상황 확인";
  }
  if (
    compact.indexOf("공격") >= 0 || compact.indexOf("이동") >= 0 ||
    compact.indexOf("카메라") >= 0 || compact.indexOf("화면") >= 0 ||
    compact.indexOf("attack") >= 0 || compact.indexOf("move") >= 0
  ) {
    return "전술 조작";
  }
  if (
    compact.indexOf("지어") >= 0 || compact.indexOf("건설") >= 0 ||
    compact.indexOf("보급고") >= 0 || compact.indexOf("배럭") >= 0 ||
    compact.indexOf("병영") >= 0 || compact.indexOf("supply") >= 0 ||
    compact.indexOf("depot") >= 0 || compact.indexOf("barracks") >= 0
  ) {
    return "건설";
  }
  if (
    compact.indexOf("생산") >= 0 || compact.indexOf("찍") >= 0 ||
    compact.indexOf("scv") >= 0 || compact.indexOf("일꾼") >= 0 ||
    compact.indexOf("마린") >= 0 || compact.indexOf("marine") >= 0 ||
    compact.indexOf("train") >= 0
  ) {
    return "생산";
  }
  return "일반 지시";
}

function classifyFailureReasonTheme(text) {
  var compact = String(text || "").toLowerCase().replace(/\\s+/g, "");
  if (!compact) { return "실행 조건 확인"; }
  if (
    compact.indexOf("llm") >= 0 || compact.indexOf("api") >= 0 ||
    compact.indexOf("key") >= 0 || compact.indexOf("model") >= 0 ||
    compact.indexOf("provider") >= 0
  ) {
    return "LLM 설정 확인";
  }
  if (compact.indexOf("보급") >= 0 || compact.indexOf("supply") >= 0) {
    return "보급 확인";
  }
  if (
    compact.indexOf("미네랄") >= 0 || compact.indexOf("가스") >= 0 ||
    compact.indexOf("자원") >= 0 || compact.indexOf("비용") >= 0 ||
    compact.indexOf("부족") >= 0 || compact.indexOf("mineral") >= 0 ||
    compact.indexOf("vespene") >= 0 || compact.indexOf("gas") >= 0
  ) {
    return "자원/조건 확인";
  }
  if (
    compact.indexOf("위치") >= 0 || compact.indexOf("타일") >= 0 ||
    compact.indexOf("대상") >= 0 || compact.indexOf("어디") >= 0 ||
    compact.indexOf("본진") >= 0 || compact.indexOf("앞마당") >= 0 ||
    compact.indexOf("placement") >= 0 || compact.indexOf("target") >= 0
  ) {
    return "위치/대상 확인";
  }
  if (
    compact.indexOf("정찰") >= 0 || compact.indexOf("시야") >= 0 ||
    compact.indexOf("보이지") >= 0 || compact.indexOf("발견") >= 0 ||
    compact.indexOf("scout") >= 0 || compact.indexOf("vision") >= 0 ||
    compact.indexOf("unscouted") >= 0
  ) {
    return "시야/정찰 확인";
  }
  if (
    compact.indexOf("확인") >= 0 || compact.indexOf("모호") >= 0 ||
    compact.indexOf("어느") >= 0 || compact.indexOf("무엇") >= 0
  ) {
    return "추가 확인";
  }
  return "실행 조건 확인";
}

function compactedContextSummary() {
  if (compactedContext.total < 1) {
    return t("compactedNone");
  }
  var summary = t("compactedSummary")
    .replace("{total}", String(compactedContext.total))
    .replace("{successful}", String(compactedContext.successful))
    .replace("{failed}", String(compactedContext.failed));
  if (compactedContext.commands.length) {
    summary += "\\n" + t("progressRecent") + ": " + compactedContext.commands.slice(-5).join(" / ");
  }
  if (compactedContext.lastNarration) {
    summary += "\\n" + compactedContext.lastNarration;
  }
  return summary;
}

function inferStrategy(recentTexts, structures) {
  var text = recentTexts.join(" ").toLowerCase();
  if (!recentTexts.length) { return t("strategyOpening"); }
  if (text.indexOf("정찰") >= 0 || text.indexOf("scout") >= 0) {
    return t("strategyScout");
  }
  if (text.indexOf("방어") >= 0 || text.indexOf("입구") >= 0 || text.indexOf("벙커") >= 0) {
    return t("strategyDefense");
  }
  if (text.indexOf("병영") >= 0 || text.indexOf("배럭") >= 0 || text.indexOf("마린") >= 0 || structures.BARRACKS) {
    return t("strategyProduction");
  }
  if (text.indexOf("scv") >= 0 || text.indexOf("자원") >= 0 || text.indexOf("미네랄") >= 0 || text.indexOf("보급") >= 0) {
    return t("strategyEconomy");
  }
  return t("strategyOpening");
}

function briefingBlock(label, text) {
  var block = document.createElement("div");
  block.className = "briefing-block";
  var labelNode = document.createElement("span");
  labelNode.className = "briefing-label";
  labelNode.textContent = label;
  var body = document.createElement("span");
  body.textContent = text;
  block.appendChild(labelNode);
  block.appendChild(body);
  return block;
}

function pollState() {
  if (isMicroMachineCommandMode()) {
    renderMicroMachineStatePlaceholder();
    return Promise.resolve(null);
  }
  return fetch("/api/state" + authQuery)
    .then(function (response) { return response.json(); })
    .then(function (data) {
      if (isMicroMachineCommandMode()) {
        renderMicroMachineStatePlaceholder();
        return null;
      }
      return renderState(data);
    })
    .catch(function () { /* 다음 폴링에서 다시 시도합니다. */ });
}

function renderLlmSettings(data) {
  if (!data) { return; }
  setSelectedLlmProvider(data.provider || "openai");
  renderModelSelect(data.provider || "openai", data.model || "");
  llmConfigured = !!data.configured;
  setCommandEnabled(llmConfigured);
  if (data.configured) {
    var effort = data.reasoning_effort
      ? " / effort=" + data.reasoning_effort
      : "";
    setLlmStatus(
      "success",
      "llmSuccessLabel",
      t("llmReady") + " (" + data.provider + " / " + data.model + effort + ")"
    );
    return;
  }
  setLlmStatus(
    "missing",
    "llmRequiredLabel",
    isMicroMachineCommandMode() ? t("llmOptionalMicro") : t("llmMissing")
  );
}

function pollLlmSettings() {
  fetch("/api/llm" + authQuery)
    .then(parseJsonResponse)
    .then(function (data) {
      if (activeLlmSetupAttemptSeq) { return; }
      renderLlmSettings(data);
    })
    .catch(function (error) {
      if (activeLlmSetupAttemptSeq) { return; }
      setLlmStatus("failed", "llmFailedLabel", t("llmCheckingFailed") + ": " + error.message);
    });
}

function parseJsonResponse(response) {
  return response.text().then(function (text) {
    var data = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (error) {
        throw new Error("invalid JSON response: " + text.slice(0, 160));
      }
    }
    if (!response.ok) {
      throw new Error(data.error || ("HTTP " + response.status));
    }
    return data;
  });
}

function selectedLlmChoice() {
  var selectedProvider = document.querySelector("input[name='llm-provider-choice']:checked");
  var modelSelect = document.getElementById("llm-model-select");
  if (!selectedProvider) {
    throw new Error("LLM provider is not selected.");
  }
  if (!modelSelect || !modelSelect.value) {
    throw new Error("LLM model is not selected.");
  }
  return {
    provider: selectedProvider.value || "openai",
    model: modelSelect.value
  };
}

function setSelectedLlmProvider(provider) {
  var matched = false;
  Array.prototype.forEach.call(document.querySelectorAll("input[name='llm-provider-choice']"), function (input) {
    var isMatch = input.value === provider;
    input.checked = isMatch;
    matched = matched || isMatch;
  });
  if (!matched) {
    var fallback = document.querySelector("input[name='llm-provider-choice'][value='openai']");
    if (fallback) { fallback.checked = true; }
  }
}

function selectedProviderValue() {
  var selectedProvider = document.querySelector("input[name='llm-provider-choice']:checked");
  return selectedProvider ? selectedProvider.value : "openai";
}

function handleProviderChoiceChange(provider) {
  setSelectedLlmProvider(provider || "openai");
  renderModelSelect(selectedProviderValue(), "");
}

function renderModelSelect(provider, selectedModel) {
  var modelSelect = document.getElementById("llm-model-select");
  var models = LLM_MODELS[provider] || LLM_MODELS.openai;
  if (!modelSelect || !models.length) { return; }
  modelSelect.innerHTML = "";
  models.forEach(function (model) {
    var option = document.createElement("option");
    option.value = model.value;
    option.textContent = model.label;
    modelSelect.appendChild(option);
  });
  var wanted = selectedModel || models[0].value;
  modelSelect.value = models.some(function (model) { return model.value === wanted; }) ? wanted : models[0].value;
}

function runtimeStatusQuery() {
  var mode = selectedCommandMode();
  var query = "?mode=" + encodeURIComponent(mode);
  if (mode === COMMAND_MODE_MICROMACHINE) {
    query += "&blackboard_dir=" + encodeURIComponent(optionalMicroMachineField("micromachine-blackboard-dir"));
  }
  return query + authJoin;
}

function runtimeStartPayload() {
  var mode = selectedCommandMode();
  var payload = { mode: mode };
  if (mode === COMMAND_MODE_MICROMACHINE) {
    payload.blackboard_dir = optionalMicroMachineField("micromachine-blackboard-dir");
    payload.enemy_difficulty = requireMicroMachineEnemyDifficulty();
  }
  return payload;
}

function requireMicroMachineEnemyDifficulty() {
  var rawValue = optionalMicroMachineField("micromachine-enemy-difficulty");
  if (!rawValue) { return 7; }
  var value = Number(rawValue);
  if (!Number.isInteger(value) || value < 1 || value > 10) {
    throw new Error("enemy difficulty must be an integer from 1 to 10.");
  }
  return value;
}

function handleLiveStart(status, options) {
  handleRuntimeStatus(status, options || {});
}

function handleRuntimeStatus(status, options) {
  var mode = (status && status.mode) || selectedCommandMode();
  if (!status || !status.enabled) {
    setLiveStatusText(t(mode === COMMAND_MODE_MICROMACHINE ? "runtimeIdleMicro" : "runtimeIdleLegacy"));
    return;
  }
  if ((status.status === "ready" || status.status === "passed") && status.url) {
    setLiveStatusLink(t("runtimeReady"), status.url);
    if (options && options.autoOpen) { window.location.assign(status.url); }
    return;
  }
  if (mode === COMMAND_MODE_MICROMACHINE && status.telemetry_stale_or_detached) {
    setLiveStatusText(t("runtimeDetachedTelemetry") + formatRuntimeDetails(status));
    return;
  }
  if (status.status === "ready" && mode === COMMAND_MODE_LEGACY_COMMANDER) {
    setLiveStatusText(t("runtimeReady") + formatLivePid(status));
    return;
  }
  if (status.status === "passed") {
    setLiveStatusText(t("runtimePassed") + formatRuntimeDetails(status));
    return;
  }
  if (status.status === "connected") {
    setLiveStatusText(t("runtimeConnected") + formatRuntimeDetails(status));
    if (status.pid && (!options || options.poll !== false)) { pollLiveStatus(0); }
    return;
  }
  if (status.status === "blocked") {
    setLiveStatusText(t("runtimeBlocked") + ": " + (status.error || status.last_line || "blocked"));
    return;
  }
  if (status.status === "failed") {
    setLiveStatusText(t("runtimeFailed") + ": " + (status.error || status.last_line || "unknown error"));
    return;
  }
  if (status.status === "idle") {
    setLiveStatusText(t(mode === COMMAND_MODE_MICROMACHINE ? "runtimeIdleMicro" : "runtimeIdleLegacy"));
    return;
  }
  var label = status.status === "running" ? t("runtimeRunning") : t("runtimeStarting");
  setLiveStatusText(label + " (" + (status.status || "starting") + formatRuntimeDetails(status) + ")");
  if (!options || options.poll !== false) { pollLiveStatus(0); }
}

function pollLiveStatus(attempt) {
  if (attempt > 90) {
    setLiveStatusText(t("runtimeFailed") + ": timeout waiting for selected runtime");
    return;
  }
  window.setTimeout(function () {
    fetch("/api/runtime/status" + runtimeStatusQuery())
      .then(parseJsonResponse)
      .then(function (status) {
        handleRuntimeStatus(status, { poll: false });
        if (
          ["starting", "running"].indexOf(status.status) !== -1 ||
          (status.status === "connected" && status.pid)
        ) {
          pollLiveStatus(attempt + 1);
          return;
        }
      })
      .catch(function (error) {
        setLiveStatusText(t("runtimeFailed") + ": " + error.message);
      });
  }, 1000);
}

function refreshLiveConnectionFlow() {
  fetch("/api/runtime/status" + runtimeStatusQuery())
    .then(parseJsonResponse)
    .then(function (status) { handleRuntimeStatus(status, { poll: false }); })
    .catch(function (error) {
      setLiveStatusText(t("runtimeFailed") + ": " + error.message);
    });
}

function startSelectedRuntime() {
  var payload;
  try {
    payload = runtimeStartPayload();
  } catch (error) {
    setLiveStatusText(t("runtimeFailed") + ": " + error.message);
    return;
  }
  setLiveStatusText(t("runtimeStarting") + " (" + payload.mode + ")");
  fetch("/api/runtime/start" + authQuery, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(parseJsonResponse)
    .then(function (status) { handleRuntimeStatus(status); })
    .catch(function (error) {
      setLiveStatusText(t("runtimeFailed") + ": " + error.message);
    });
}

function setLiveStatusLink(label, url) {
  liveGuiUrl = url || "";
  var statusNode = document.getElementById("live-status");
  statusNode.textContent = label + ": ";
  var link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = url;
  statusNode.appendChild(link);
  setLiveButtonEnabled(true);
}

function setLiveStatusText(text) {
  document.getElementById("live-status").textContent = text;
  setLiveButtonEnabled(!!liveGuiUrl);
}

function setLiveButtonEnabled(enabled) {
  document.getElementById("live-open-button").disabled = !enabled;
}

function formatLivePid(status) {
  return status && status.pid ? ", pid " + status.pid : "";
}

function formatRuntimeDetails(status) {
  if (!status) { return ""; }
  var parts = [];
  if (status.pid) { parts.push("pid " + status.pid); }
  if (status.telemetry_frame || status.telemetry_frame === 0) {
    parts.push("frame " + status.telemetry_frame);
  }
  if (status.blackboard_dir) { parts.push("blackboard " + status.blackboard_dir); }
  if (status.enemy_difficulty) {
    parts.push("enemy difficulty " + status.enemy_difficulty);
  }
  return parts.length ? " (" + parts.join(", ") + ")" : formatLivePid(status);
}

function setMicroMachineText(id, value) {
  var node = document.getElementById(id);
  if (!node) { return; }
  if (Array.isArray(value)) {
    node.textContent = value.length ? value.join(", ") : "-";
    return;
  }
  if (value === null || value === undefined || value === "") {
    node.textContent = "-";
    return;
  }
  node.textContent = String(value);
}

function commandUiText(ko, en, zh) {
  if (currentLang === "en") { return en; }
  if (currentLang === "zh") { return zh; }
  return ko;
}

function microMachineExecutionStage(execution, name) {
  var stages = execution && Array.isArray(execution.stages)
    ? execution.stages
    : [];
  for (var index = 0; index < stages.length; index += 1) {
    if (stages[index] && stages[index].name === name) {
      return stages[index];
    }
  }
  return null;
}

function microMachineExecutionEffectObserved(execution) {
  var stage = microMachineExecutionStage(execution, "effect_observed");
  return Boolean(stage && stage.ok === true);
}

function microMachineExecutionActionIssued(execution) {
  var actionStage = microMachineExecutionStage(execution, "action_issued");
  return Boolean(actionStage && actionStage.ok === true);
}

function commandConsoleDataUpdateIds(data) {
  var compileResult = (data && data.compile_result) || {};
  var update = (data && data.update) || {};
  var latestRequest = (data && data.latest_request) || {};
  var intervention = (data && data.intervention) || {};
  var execution = intervention.command_execution || {};
  var values = [
    data && data.update_id,
    update.update_id,
    execution.command_id,
    intervention.latest_update_id,
    latestRequest.update_id,
    compileResult.update_id
  ];
  var result = [];
  values.forEach(function(value) {
    var normalized = String(value || "");
    if (normalized && result.indexOf(normalized) === -1) {
      result.push(normalized);
    }
  });
  return result;
}

function commandConsolePreferredUpdateId(data) {
  var compileResult = (data && data.compile_result) || {};
  var latestRequest = (data && data.latest_request) || {};
  var compileUpdateId = String(compileResult.update_id || "");
  var latestRequestUpdateId = String(latestRequest.update_id || "");
  var latestRequestTerminal = Boolean(
    compileResult.refusal_reason ||
    compileResult.clarification_prompt ||
    compileResult.status === "refused" ||
    compileResult.status === "clarification_required" ||
    data && data.status === "publish_failed" ||
    data && data.status === "superseded"
  );
  if (
    compileUpdateId &&
    latestRequestUpdateId === compileUpdateId &&
    latestRequest.is_active_update === false &&
    latestRequestTerminal
  ) {
    return compileUpdateId;
  }
  var intervention = (data && data.intervention) || {};
  var execution = intervention.command_execution || {};
  return String(
    execution.command_id ||
    (data && data.update && data.update.update_id) ||
    intervention.latest_update_id ||
    compileUpdateId ||
    latestRequestUpdateId ||
    data && data.update_id ||
    ""
  );
}

function handoffActiveCommandConsole(data, scopeId, updateId) {
  var compileResult = (data && data.compile_result) || {};
  var latestRequest = (data && data.latest_request) || {};
  microMachineCommandAnnouncementSeq += 1;
  activeCommandConsoleRecord = {
    pendingId: "",
    scopeId: scopeId,
    sessionEpoch: operationPayloadSessionEpoch(
      data,
      commandOperationPayloads(data)
    ),
    updateId: updateId,
    operationId: String(data && data.operation_id || ""),
    operationGeneration: Number(
      data && data.operation_generation || 0
    ),
    text: String(
      latestRequest.command_text ||
      compileResult.command_text ||
      data.command_text ||
      commandConsoleGoal(data, "")
    ),
    state: "interpreting",
    data: null,
    startedAt: Date.now(),
    stageRank: 0,
    telemetryFrame: -1,
    observationTimedOut: false,
    submissionDelayed: false,
    announcementOrdinal: microMachineCommandAnnouncementSeq
  };
}

function beginActiveCommandConsole(text, pendingId) {
  microMachineCommandAnnouncementSeq += 1;
  activeCommandConsoleRecord = {
    pendingId: pendingId || "",
    scopeId: "",
    sessionEpoch: "",
    updateId: "",
    operationId: "",
    operationGeneration: 0,
    text: String(text || ""),
    state: "received",
    data: {
      status: "received",
      command_text: String(text || ""),
      consumption_status: "received"
    },
    startedAt: Date.now(),
    stageRank: 0,
    telemetryFrame: -1,
    observationTimedOut: false,
    submissionDelayed: false,
    announcementOrdinal: microMachineCommandAnnouncementSeq
  };
  beginOperationRecord(text, pendingId);
  renderActiveCommandConsole(activeCommandConsoleRecord.data, true);
}

function resetActiveCommandConsoleState(sessionEpoch) {
  activeCommandConsoleRecord = {
    pendingId: "",
    scopeId: "",
    sessionEpoch: String(sessionEpoch || ""),
    updateId: "",
    operationId: "",
    operationGeneration: 0,
    text: "",
    state: "idle",
    data: null,
    startedAt: 0,
    stageRank: 0,
    telemetryFrame: -1,
    observationTimedOut: false,
    submissionDelayed: false,
    announcementOrdinal: 0
  };
  var consoleNode = document.getElementById("active-command-console");
  if (consoleNode) {
    consoleNode.className = "active-command-console command-console-idle";
  }
  setMicroMachineText("command-console-title", t("commandConsoleIdleTitle"));
  setMicroMachineText("command-console-state", t("commandConsoleIdleState"));
  [
    "command-console-intent",
    "command-console-units",
    "command-console-action",
    "command-console-target",
    "command-console-verification"
  ].forEach(function(id) {
    setMicroMachineText(id, t("commandConsoleWaiting"));
  });
  ["interpret", "assign", "execute", "verify"].forEach(function(stageName) {
    var stageNode = document.getElementById("command-stage-" + stageName);
    if (!stageNode) { return; }
    stageNode.className = "command-stage";
    stageNode.setAttribute("role", "listitem");
    stageNode.setAttribute("aria-current", "false");
    stageNode.setAttribute(
      "aria-label",
      stageNode.textContent + ": " + commandUiText("대기", "waiting", "等待")
    );
  });
  setMicroMachineText("command-console-technical", "{}");
  var announcement = document.getElementById("command-console-announcement");
  if (announcement) {
    announcement.textContent = "";
  }
  setMicroMachineText("battlefield-command-state", t("commandConsoleIdleState"));
  setMicroMachineText("battlefield-frame", "-");
  setMicroMachineText("battlefield-force", "-");
  setMicroMachineText("battlefield-posture", "-");
  setMicroMachineText("battlefield-unassigned", "-");
  setMicroMachineText("battlefield-readiness", "-");
  setMicroMachineText("battlefield-transfer", "-");
  setMicroMachineText("battlefield-integrity", "-");
  setMicroMachineText("battlefield-production-waits", "-");
  setMicroMachineText("battlefield-control-summary", t("battlefieldControlWaiting"));
  var badge = document.getElementById("battlefield-link-badge");
  if (badge) {
    badge.className = "battlefield-link-badge";
    badge.textContent = t("battlefieldLinkWaiting");
  }
}

function resetActiveCommandConsole() {
  resetActiveCommandConsoleState("");
  resetOperationConsoleRegistry();
}

function bindActiveCommandConsoleUpdate(text, pendingId, scopeId, updateId) {
  var normalizedText = String(text || "");
  if (
    activeCommandConsoleRecord.pendingId &&
    pendingId &&
    activeCommandConsoleRecord.pendingId !== pendingId
  ) {
    return false;
  }
  if (
    activeCommandConsoleRecord.text &&
    normalizedText &&
    activeCommandConsoleRecord.text !== normalizedText &&
    activeCommandConsoleRecord.pendingId
  ) {
    return false;
  }
  activeCommandConsoleRecord.text = normalizedText || activeCommandConsoleRecord.text;
  activeCommandConsoleRecord.pendingId = pendingId || activeCommandConsoleRecord.pendingId;
  activeCommandConsoleRecord.scopeId = String(scopeId || activeCommandConsoleRecord.scopeId || "");
  activeCommandConsoleRecord.updateId = String(updateId || activeCommandConsoleRecord.updateId || "");
  bindOperationRecordUpdate(
    activeCommandConsoleRecord.text,
    pendingId,
    activeCommandConsoleRecord.scopeId,
    activeCommandConsoleRecord.updateId
  );
  return true;
}

function shouldRenderActiveCommandConsoleData(data) {
  if (!data || typeof data !== "object") { return false; }
  var scopeId = microMachineScopeId(data);
  if (
    activeCommandConsoleRecord.scopeId &&
    scopeId &&
    activeCommandConsoleRecord.scopeId !== scopeId
  ) {
    return false;
  }
  var updateIds = commandConsoleDataUpdateIds(data);
  if (activeCommandConsoleRecord.updateId) {
    var currentUpdateIncluded = (
      updateIds.indexOf(activeCommandConsoleRecord.updateId) !== -1
    );
    var preferredUpdateId = commandConsolePreferredUpdateId(data);
    var currentModel = commandConsoleStageModel(
      activeCommandConsoleRecord.data || {}
    );
    if (
      currentUpdateIncluded &&
      (
        !preferredUpdateId ||
        preferredUpdateId === activeCommandConsoleRecord.updateId ||
        !currentModel.terminal
      )
    ) {
      return true;
    }
    var handoffUpdateId = (
      preferredUpdateId &&
      preferredUpdateId !== activeCommandConsoleRecord.updateId
    )
      ? preferredUpdateId
      : (!currentUpdateIncluded && updateIds.length ? updateIds[0] : "");
    if (!currentModel.terminal || !handoffUpdateId) {
      return currentUpdateIncluded;
    }
    var candidateFrame = commandConsoleTelemetryFrame(data);
    if (
      candidateFrame >= 0 &&
      activeCommandConsoleRecord.telemetryFrame >= 0 &&
      candidateFrame < activeCommandConsoleRecord.telemetryFrame
    ) {
      return currentUpdateIncluded;
    }
    handoffActiveCommandConsole(data, scopeId, handoffUpdateId);
    return true;
  }
  if (activeCommandConsoleRecord.pendingId) {
    return false;
  }
  if (!activeCommandConsoleRecord.text) {
    return Boolean(updateIds.length || data.command_text);
  }
  var latestRequest = (data && data.latest_request) || {};
  var commandText = String(
    data.command_text ||
    latestRequest.command_text ||
    ""
  );
  return Boolean(commandText && commandText === activeCommandConsoleRecord.text);
}

function commandConsoleDataForUpdate(data, updateId) {
  if (!data || typeof data !== "object" || !updateId) { return data; }
  var result = Object.assign({}, data);
  var compileResult = data.compile_result || {};
  var update = data.update || {};
  var latestRequest = data.latest_request || {};
  var intervention = Object.assign({}, data.intervention || {});
  var execution = intervention.command_execution || {};
  var executionOwnerUpdateId = String(
    data.operation_console_execution_owner_update_id || ""
  );
  var selectedOperationUpdateId = operationPayloadUpdateId(data);
  var operationGeneration = Number(data.operation_generation || 0);
  var executionGeneration = Number(execution.operation_generation || 0);
  var linkedOperationExecution = Boolean(
    executionOwnerUpdateId &&
    selectedOperationUpdateId === updateId &&
    String(execution.command_id || "") === executionOwnerUpdateId &&
    data.operation_id &&
    String(execution.operation_id || "") === String(data.operation_id) &&
    operationGeneration > 0 &&
    executionGeneration === operationGeneration
  );
  if (compileResult.update_id && String(compileResult.update_id) !== updateId) {
    result.compile_result = {};
  }
  if (update.update_id && String(update.update_id) !== updateId) {
    result.update = {};
  }
  if (latestRequest.update_id && String(latestRequest.update_id) !== updateId) {
    result.latest_request = null;
  }
  if (
    execution.command_id &&
    String(execution.command_id) !== updateId &&
    !linkedOperationExecution
  ) {
    intervention.command_execution = {};
  }
  if (
    intervention.latest_update_id &&
    String(intervention.latest_update_id) !== updateId &&
    !(execution.command_id && String(execution.command_id) === updateId) &&
    !linkedOperationExecution
  ) {
    intervention = { command_execution: intervention.command_execution || {} };
  }
  result.intervention = intervention;
  return result;
}

function commandConsoleTelemetryFrame(data) {
  var intervention = (data && data.intervention) || {};
  var dashboard = (data && data.dashboard) || {};
  var telemetry = dashboard.telemetry || {};
  var value = intervention.telemetry_frame;
  if (value === null || value === undefined || value === "") {
    value = telemetry.frame;
  }
  var frame = Number(value);
  return Number.isFinite(frame) ? frame : -1;
}

function commandConsoleStageRank(model) {
  if (
    model.canonicalCompletionVerified ||
    model.effectObserved ||
    model.blocked ||
    model.superseded ||
    model.cancelled
  ) { return 4; }
  if (model.actionIssued) { return 3; }
  if (model.assignmentReady) { return 2; }
  if (model.interpreted) { return 1; }
  return 0;
}

function shouldAdvanceActiveCommandConsole(model, telemetryFrame) {
  var currentData = activeCommandConsoleRecord.data || {};
  var currentModel = commandConsoleStageModel(currentData);
  var candidateRank = commandConsoleStageRank(model);
  if (currentModel.effectObserved && !model.effectObserved) {
    return false;
  }
  if (
    currentModel.canonicalCompletionVerified &&
    !model.canonicalCompletionVerified
  ) {
    return false;
  }
  if (currentModel.cancelled && !model.cancelled) {
    return false;
  }
  if (currentModel.superseded && !model.superseded) {
    return false;
  }
  if (currentModel.blocked && !model.blocked) {
    return false;
  }
  if (
    telemetryFrame >= 0 &&
    activeCommandConsoleRecord.telemetryFrame >= 0 &&
    telemetryFrame < activeCommandConsoleRecord.telemetryFrame
  ) {
    return false;
  }
  if (candidateRank < activeCommandConsoleRecord.stageRank) {
    return false;
  }
  return true;
}

function commandConsoleStageState(stageName, model) {
  var order = ["interpret", "assign", "execute", "verify"];
  var stageIndex = order.indexOf(stageName);
  var currentIndex = order.indexOf(model.currentStage);
  if (model.blocked && stageIndex === currentIndex) { return "stage-blocked"; }
  if (model.effectObserved && model.done[stageName]) { return "stage-verified"; }
  if (model.done[stageName]) { return "stage-done"; }
  if (!model.terminal && stageIndex === currentIndex) { return "stage-current"; }
  return "";
}

function commandConsoleStageModel(data) {
  var compileResult = (data && data.compile_result) || {};
  var intervention = (data && data.intervention) || {};
  var execution = intervention.command_execution || {};
  var projection = data && data.battlefield_operation || {};
  var projectionIdentity = projection.identity || {};
  var projectionOwnership = projection.operation_ownership || {};
  var projectionLaunch = projection.operation_launch_policy || {};
  var projectionCompletion = projection.operation_completion || {};
  var canonicalProjectionMatches = operationCanonicalProjectionMatches(data);
  var canonicalAssignmentObserved = Boolean(
    canonicalProjectionMatches &&
    (
      Number(projectionOwnership.owner_count || 0) > 0 ||
      Number(projectionLaunch.launch_count || 0) > 0 ||
      ["assigned", "submitted", "moving", "engaged", "completed"].indexOf(
        String(projectionIdentity.stage || "").toLowerCase()
      ) >= 0
    )
  );
  var canonicalActionObserved = Boolean(
    canonicalProjectionMatches &&
    (
      projectionCompletion.movement_observed === true ||
      projectionCompletion.engagement_observed === true ||
      projectionCompletion.target_reached === true ||
      ["submitted", "moving", "engaged", "completed"].indexOf(
        String(projectionIdentity.stage || "").toLowerCase()
      ) >= 0
    )
  );
  var parsed = microMachineExecutionStage(execution, "parsed");
  var reduced = microMachineExecutionStage(execution, "reduced");
  var consumed = microMachineExecutionStage(execution, "consumed_by_manager");
  var assigned = microMachineExecutionStage(execution, "queued_or_assigned");
  var observedEffect = microMachineExecutionEffectObserved(execution);
  var executionState = String(execution.state || "").toLowerCase();
  var received = Boolean(data && data.status === "received");
  var blockerReason = String(execution.blocker_reason || "").toLowerCase();
  var canonicalState = operationCanonicalTerminalState(data);
  var cancelled = Boolean(
    executionState === "cancelled" ||
    executionState === "canceled" ||
    blockerReason === "cancelled_by_policy" ||
    canonicalState === "cancelled" ||
    canonicalState === "canceled"
  );
  var cancellationCleanupVerified = Boolean(
    cancelled &&
    commandConsoleTerminalCleanupVerified({ execution: execution })
  );
  var superseded = Boolean(
    !cancelled &&
    (
      data && data.status === "superseded" ||
      executionState === "superseded" ||
      executionState === "replaced" ||
      canonicalState === "superseded"
    )
  );
  var refused = Boolean(
    compileResult.refusal_reason ||
    compileResult.clarification_prompt ||
    data && data.accepted === false ||
    data && data.status === "publish_failed"
  );
  var canonicalCompletion = (
    ["completed", "succeeded", "success"].indexOf(canonicalState) >= 0
  );
  var failed = Boolean(
    !cancelled &&
    !superseded &&
    (
      refused ||
      execution.failed === true ||
      execution.expired === true ||
      canonicalState === "failed" ||
      canonicalState === "expired" ||
      executionState === "failed" ||
      executionState === "expired" ||
      executionState === "blocked" ||
      executionState === "rejected" ||
      (
        (execution.completed === true || executionState === "completed") &&
        !observedEffect &&
        !canonicalCompletion
      )
    )
  );
  var canonicalCompletionVerified = Boolean(
    canonicalCompletion &&
    !cancelled &&
    !superseded &&
    !failed
  );
  var effectObserved = Boolean(
    observedEffect && !cancelled && !superseded && !failed
  );
  var actionIssued = Boolean(
    effectObserved ||
    microMachineExecutionActionIssued(execution)
  );
  var assignmentReady = Boolean(
    actionIssued ||
    (
      consumed && consumed.ok === true &&
      assigned && assigned.ok === true
    )
  );
  var interpreted = Boolean(
    assignmentReady ||
    (parsed && parsed.ok === true && reduced && reduced.ok === true) ||
    compileResult.status === "compiled" ||
    data && data.status === "published"
  );
  var currentStage = "interpret";
  if (interpreted) { currentStage = "assign"; }
  if (assignmentReady) { currentStage = "execute"; }
  if (actionIssued) { currentStage = "verify"; }
  if (effectObserved) { currentStage = "verify"; }
  if (failed && execution.state) {
    if (execution.state === "parsed" || execution.state === "reduced" || execution.state === "published") {
      currentStage = "interpret";
    } else if (
      execution.state === "consumed_by_manager" ||
      execution.state === "queued_or_assigned"
    ) {
      currentStage = "assign";
    } else if (
      execution.state === "order_issued" ||
      execution.state === "action_issued"
    ) {
      currentStage = "execute";
    } else {
      currentStage = "verify";
    }
  }
  if (failed && canonicalState) {
    if (canonicalActionObserved) {
      currentStage = "verify";
    } else if (canonicalAssignmentObserved) {
      currentStage = "execute";
    }
  }
  return {
    execution: execution,
    received: received,
    interpreted: interpreted,
    assignmentReady: assignmentReady,
    actionIssued: actionIssued,
    effectObserved: effectObserved,
    observationDelayed: Boolean(data && data.command_console_observation_delayed),
    submissionDelayed: Boolean(data && data.command_console_submission_delayed),
    refused: refused,
    cancelled: cancelled,
    cancellationCleanupVerified: cancellationCleanupVerified,
    superseded: superseded,
    canonicalState: canonicalState,
    canonicalCompletion: canonicalCompletion,
    canonicalCompletionVerified: canonicalCompletionVerified,
    blocked: failed,
    terminal: (
      canonicalCompletionVerified ||
      cancellationCleanupVerified ||
      failed ||
      superseded ||
      effectObserved
    ),
    currentStage: currentStage,
    done: {
      interpret: interpreted,
      assign: assignmentReady || canonicalAssignmentObserved,
      execute: actionIssued || canonicalActionObserved,
      verify: effectObserved || canonicalCompletionVerified
    }
  };
}

function commandConsoleEvidenceValue(execution, stageNames, keys) {
  for (var stageIndex = 0; stageIndex < stageNames.length; stageIndex += 1) {
    var stage = microMachineExecutionStage(execution, stageNames[stageIndex]);
    var evidence = stage && stage.evidence && typeof stage.evidence === "object"
      ? stage.evidence
      : {};
    for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
      var value = evidence[keys[keyIndex]];
      if (value !== null && value !== undefined && value !== "" && value !== 0) {
        return value;
      }
    }
  }
  return "";
}

function commandConsoleAssignedForce(model) {
  var execution = model.execution;
  var typeValue = commandConsoleEvidenceValue(
    execution,
    ["action_issued", "queued_or_assigned", "order_issued"],
    [
      "scout_last_commanded_unit_type",
      "commanded_unit_type",
      "actor_unit_type",
      "unit_type",
      "item"
    ]
  );
  var countValue = commandConsoleEvidenceValue(
    execution,
    ["queued_or_assigned", "action_issued", "order_issued"],
    [
      "assigned_unit_count",
      "assigned_count",
      "scout_scope_assigned_unit_count",
      "scout_marine_assigned_count",
      "main_attack_assigned_unit_count",
      "planned_count"
    ]
  );
  var tagValue = commandConsoleEvidenceValue(
    execution,
    ["action_issued", "order_issued", "queued_or_assigned"],
    [
      "scout_last_commanded_unit_tag",
      "commanded_unit_tag",
      "actor_unit_tag",
      "unit_tag"
    ]
  );
  var parts = [];
  if (typeValue) { parts.push(String(typeValue)); }
  if (countValue) { parts.push(String(countValue) + commandUiText("기", " unit(s)", " 个")); }
  if (tagValue) { parts.push("#" + String(tagValue)); }
  if (parts.length) { return parts.join(" · "); }
  var assignedStage = microMachineExecutionStage(execution, "queued_or_assigned");
  if (assignedStage && assignedStage.ok === true) {
    return commandUiText(
      (assignedStage.manager || "MicroMachine") + "에서 편성 또는 생산 큐를 확정했습니다.",
      (assignedStage.manager || "MicroMachine") + " confirmed assignment or production queue.",
      (assignedStage.manager || "MicroMachine") + " 已确认编队或生产队列。"
    );
  }
  return commandUiText("조건에 맞는 유닛 또는 생산 대기", "Waiting for eligible units or production", "等待符合条件的单位或生产");
}

function commandConsoleActualAction(model) {
  var execution = model.execution;
  var action = commandConsoleEvidenceValue(
    execution,
    ["action_issued"],
    [
      "last_actual_command",
      "scout_last_issued_action",
      "last_issued_action",
      "cast_submitted_action",
      "last_actual_production_command",
      "last_building_command",
      "action"
    ]
  );
  if (action) { return String(action); }
  if (model.actionIssued) {
    return commandUiText("SC2 명령 전송 경로 도달", "SC2 command submission path reached", "已到达 SC2 命令提交路径");
  }
  return commandUiText("구체적인 이동·공격·생산 명령 대기", "Waiting for a concrete move, attack, or production command", "等待具体的移动、攻击或生产命令");
}

function commandConsoleTarget(data, model) {
  var intervention = (data && data.intervention) || {};
  var scope = intervention.tactical_scope || {};
  var requested = scope.requested || {};
  var gate = intervention.attack_gate || {};
  var execution = model.execution;
  var location = requested.location_intent || commandConsoleEvidenceValue(
    execution,
    ["order_issued", "action_issued", "queued_or_assigned"],
    ["location_intent", "target", "target_name", "target_location"]
  );
  var x = gate.order_x;
  var y = gate.order_y;
  if ((x === null || x === undefined) || (y === null || y === undefined)) {
    x = commandConsoleEvidenceValue(
      execution,
      ["action_issued", "order_issued"],
      ["target_x", "order_x", "x"]
    );
    y = commandConsoleEvidenceValue(
      execution,
      ["action_issued", "order_issued"],
      ["target_y", "order_y", "y"]
    );
  }
  var parts = [];
  if (location) { parts.push(String(location)); }
  if (
    x !== null && x !== undefined && x !== "" &&
    y !== null && y !== undefined && y !== ""
  ) {
    parts.push("(" + x + ", " + y + ")");
  }
  return parts.length
    ? parts.join(" · ")
    : commandUiText("목표 좌표 또는 전술 위치 계산 대기", "Waiting for target coordinates or tactical location", "等待目标坐标或战术位置");
}

function commandConsoleTerminalCleanupVerified(model) {
  var execution = (model && model.execution) || {};
  var cleanup = execution.terminal_cleanup;
  if (!cleanup || typeof cleanup !== "object") { return false; }
  var action = String(cleanup.action || "").toLowerCase();
  var frame = Number(cleanup.frame || 0);
  var operationId = String(execution.operation_id || "");
  var cleanupOperationId = String(cleanup.operation_id || "");
  var generation = Number(execution.operation_generation || 0);
  var cleanupGeneration = Number(cleanup.generation || 0);
  return (
    (
      action.startsWith("release_stop|") ||
      action.startsWith("release_no_owned_units|")
    ) &&
    frame > 0 &&
    operationId !== "" &&
    cleanupOperationId === operationId &&
    generation > 0 &&
    cleanupGeneration === generation
  );
}

function commandConsoleTerminalCleanupStoppedOwnedUnits(model) {
  var execution = (model && model.execution) || {};
  var cleanup = execution.terminal_cleanup;
  if (!cleanup || typeof cleanup !== "object") { return false; }
  return String(cleanup.action || "").toLowerCase().startsWith("release_stop|");
}

function commandConsoleVerification(data, model) {
  var intervention = (data && data.intervention) || {};
  var compileResult = (data && data.compile_result) || {};
  var tacticalEvidence = intervention.tactical_evidence || {};
  var effectStage = microMachineExecutionStage(model.execution, "effect_observed");
  var effectEvidence = effectStage && effectStage.evidence && typeof effectStage.evidence === "object"
    ? effectStage.evidence
    : {};
  var observedEffects = Array.isArray(tacticalEvidence.observed_effects)
    ? tacticalEvidence.observed_effects
    : [];
  var confirmation = effectEvidence.confirmation_effect || effectEvidence.observed_effect || "";
  var maxDistance = (
    effectEvidence.max_home_distance ||
    effectEvidence.scout_max_home_distance ||
    effectEvidence.scout_marine_max_home_distance ||
    effectEvidence.main_attack_max_home_distance ||
    ""
  );
  var parts = [];
  if (observedEffects.length) { parts.push(observedEffects.join(", ")); }
  if (confirmation) { parts.push(String(confirmation)); }
  if (maxDistance) {
    parts.push(commandUiText(
      "본진 최대 이탈 거리 " + maxDistance,
      "max distance from home " + maxDistance,
      "离主基地最大距离 " + maxDistance
    ));
  }
  if (model.cancelled) {
    var cancellationReason = (
      model.execution.blocker_reason ||
      commandUiText(
        "사용자 또는 정책 명령으로 작전을 종료했습니다.",
        "The operation was terminated by a user or policy order.",
        "作战已由用户或策略命令终止。"
      )
    );
    if (!commandConsoleTerminalCleanupVerified(model)) {
      return commandUiText(
        "작전 취소 요청 수락: terminal 상태를 확인했습니다. 유닛 정지·해제 증거를 기다립니다. ",
        "Cancellation accepted: the terminal state is confirmed. Waiting for unit stop-and-release evidence. ",
        "已接受取消请求：已确认终止状态，正在等待单位停止并释放的证据。"
      ) + cancellationReason;
    }
    return (
      commandConsoleTerminalCleanupStoppedOwnedUnits(model)
        ? commandUiText(
            "작전 취소: 소유 유닛의 기존 명령을 중지하고 작전에서 해제했습니다. ",
            "Operation cancelled: owned units were stopped and released from the operation. ",
            "作战已取消：所属单位已停止并从作战中释放。"
          )
        : commandUiText(
            "작전 취소: 소유 유닛이 없어 중지 명령 없이 작전 해제를 확인했습니다. ",
            "Operation cancelled: no owned units remained, so release was verified without a stop command. ",
            "作战已取消：没有剩余所属单位，因此无需停止命令即可确认释放。"
          )
    ) + cancellationReason;
  }
  if (model.superseded) {
    return commandUiText(
      "작전 교체: 새 명령이 이 작전을 대체했습니다.",
      "Order superseded: a newer order replaced this operation.",
      "作战已替换：新命令替代了此作战。"
    );
  }
  if (model.blocked) {
    var projection = data && data.battlefield_operation || {};
    var completion = projection.operation_completion || {};
    var blockerManager = model.execution.blocker_manager || (
      model.canonicalState ? "BattlefieldProjection" : "TacticalEvidence"
    );
    var blockerReason = (
      model.execution.blocker_reason ||
      completion.reason ||
      compileResult.refusal_reason ||
      compileResult.clarification_prompt ||
      intervention.refusal_reason ||
      ((model.execution.completed === true || model.execution.state === "completed")
        ? commandUiText(
          "effect_observed 증거가 없습니다.",
          "effect_observed evidence is missing.",
          "缺少 effect_observed 证据。"
        )
        : commandUiText(
          "실제 효과 확인 단계에서 중단되었습니다.",
          "Execution stopped during effect verification.",
          "执行在效果确认阶段停止。"
        ))
    );
    return commandUiText(
      "실행 중단: ",
      "Execution stopped: ",
      "执行停止："
    ) + blockerManager + " · " + blockerReason;
  }
  if (model.effectObserved) {
    return commandUiText("실제 게임 상태 확인 완료: ", "Observed in live game state: ", "已在实际游戏状态确认：") +
      (parts.length ? parts.join(" · ") : commandUiText("요청 효과 관측", "requested effect observed", "已观察到请求效果"));
  }
  if (model.canonicalCompletionVerified) {
    return commandUiText(
      "MicroMachine 권위 완료 조건 확인: ",
      "MicroMachine canonical completion confirmed: ",
      "MicroMachine 权威完成条件已确认："
    ) + operationCompletionSummary(data);
  }
  if (model.submissionDelayed) {
    return commandUiText(
      "웹 게이트웨이 응답이 지연되고 있습니다. 실패로 확정하지 않고 같은 명령 응답을 계속 기다립니다.",
      "The web gateway response is delayed. The order is still tracked and has not been marked failed.",
      "网页网关响应延迟。该命令仍在跟踪中，尚未判定为失败。"
    );
  }
  if (model.observationDelayed) {
    return commandUiText(
      "SC2 실행 결과 관측이 지연되고 있습니다. 명령 identity는 유지하며 늦게 도착한 실제 효과를 계속 반영합니다.",
      "SC2 effect observation is delayed. The command identity is preserved so a late real effect can still reconcile.",
      "SC2 效果观察延迟。命令标识会保留，稍后到达的真实效果仍会继续同步。"
    );
  }
  if (model.received && !model.interpreted) {
    return commandUiText(
      "웹 커맨더가 명령을 수신했습니다. LLM 구조화 해석을 시작합니다.",
      "The web commander received the order. Structured LLM interpretation is starting.",
      "网页指挥官已收到命令，正在开始 LLM 结构化解析。"
    );
  }
  if (model.actionIssued) {
    return commandUiText(
      "SC2 명령은 전송됐습니다. 실제 이동·공격·능력 변화를 확인 중입니다.",
      "The SC2 command was submitted. Waiting for movement, attack, or ability change.",
      "SC2 命令已提交，正在确认移动、攻击或技能变化。"
    );
  }
  if (model.assignmentReady) {
    return commandUiText(
      "유닛 또는 생산 작업이 배정됐습니다. 실제 SC2 명령 전송을 기다립니다.",
      "Units or production work are assigned. Waiting for the SC2 command.",
      "单位或生产任务已分配，正在等待 SC2 命令。"
    );
  }
  if (model.interpreted) {
    return commandUiText(
      "명령 해석은 끝났습니다. MicroMachine의 유닛 배정을 기다립니다.",
      "Interpretation is complete. Waiting for MicroMachine assignment.",
      "命令解析完成，正在等待 MicroMachine 分配单位。"
    );
  }
  return commandUiText("명령 구조를 해석하고 있습니다.", "Interpreting the command structure.", "正在解析命令结构。");
}

function commandConsoleStateLabel(model) {
  if (model.cancelled) {
    if (!commandConsoleTerminalCleanupVerified(model)) {
      return commandUiText(
        "취소 정리 확인 중",
        "Cancellation cleanup pending",
        "正在确认取消清理"
      );
    }
    return commandUiText("작전 취소", "Operation cancelled", "作战已取消");
  }
  if (model.superseded) {
    return commandUiText("작전 교체", "Order superseded", "作战已替换");
  }
  if (model.blocked) {
    return commandUiText("실행 실패", "Execution blocked", "执行失败");
  }
  if (model.effectObserved) {
    return commandUiText("실행 확인", "Execution verified", "执行已确认");
  }
  if (model.canonicalCompletionVerified) {
    return commandUiText("실행 확인", "Execution verified", "执行已确认");
  }
  if (model.submissionDelayed) {
    return commandUiText("게이트웨이 응답 지연", "Gateway response delayed", "网关响应延迟");
  }
  if (model.observationDelayed) {
    return commandUiText("효과 확인 지연", "Effect check delayed", "效果确认延迟");
  }
  if (model.actionIssued) {
    return commandUiText("전장에서 실행 중", "Executing in battle", "战场执行中");
  }
  if (model.assignmentReady) {
    return commandUiText("유닛 편성 완료", "Force assigned", "部队已分配");
  }
  if (model.interpreted) {
    return commandUiText("MicroMachine 배정 중", "MicroMachine assigning", "MicroMachine 正在分配");
  }
  if (model.received) {
    return commandUiText("명령 수신", "Order received", "已收到命令");
  }
  return commandUiText("명령 해석 중", "Interpreting order", "正在解析命令");
}

function commandConsoleClassName(model) {
  if (model.cancelled) {
    return commandConsoleTerminalCleanupVerified(model)
      ? "active-command-console command-console-superseded"
      : "active-command-console command-console-waiting";
  }
  if (model.superseded) { return "active-command-console command-console-superseded"; }
  if (model.blocked) { return "active-command-console command-console-blocked"; }
  if (model.effectObserved) { return "active-command-console command-console-verified"; }
  if (model.canonicalCompletionVerified) {
    return "active-command-console command-console-verified";
  }
  if (model.submissionDelayed) { return "active-command-console command-console-interpreting"; }
  if (model.observationDelayed) { return "active-command-console command-console-executing"; }
  if (model.actionIssued) { return "active-command-console command-console-executing"; }
  if (model.assignmentReady) { return "active-command-console command-console-assigning"; }
  return "active-command-console command-console-interpreting";
}

function commandConsoleGoal(data, fallbackText) {
  var compileResult = (data && data.compile_result) || {};
  var vector = compileResult.vector || {};
  var intervention = (data && data.intervention) || {};
  var execution = intervention.command_execution || {};
  var activePlan = execution.active_plan || {};
  return String(
    intervention.goal ||
    activePlan.goal ||
    vector.goal ||
    data && data.command_text ||
    fallbackText ||
    ""
  );
}

function operationRecordKey(scopeId, operationId) {
  return String(scopeId || "") + "\u0000" + String(operationId || "");
}

function operationPayloadScopeId(operation, data) {
  var explicitScope = String(
    operation && operation.blackboard_scope_id ||
    data && data.blackboard_scope_id ||
    ""
  );
  if (explicitScope) { return explicitScope; }
  var operationKey = String(operation && operation.operation_key || "");
  var separatorIndex = operationKey.indexOf("\u0000");
  return separatorIndex >= 0 ? operationKey.slice(0, separatorIndex) : microMachineScopeId(data || {});
}

function operationPayloadUpdateId(operation) {
  return String(
    operationPayloadRequestUpdateId(operation) ||
    operationPayloadExecutionOwnerUpdateId(operation) ||
    ""
  );
}

function operationPayloadRequestUpdateId(operation) {
  var update = (operation && operation.update) || {};
  var compileResult = (operation && operation.compile_result) || {};
  return String(
    operation && operation.update_id ||
    update.update_id ||
    compileResult.update_id ||
    ""
  );
}

function operationPayloadExecutionOwnerUpdateId(operation) {
  var intervention = (operation && operation.intervention) || {};
  var execution = intervention.command_execution || {};
  return String(
    operation && operation.operation_console_execution_owner_update_id ||
    execution.command_id ||
    ""
  );
}

function operationPayloadEditAction(operation) {
  var edit = operation && operation.operation_edit || {};
  var action = String(edit.action || "").trim();
  var allowed = [
    "create",
    "update",
    "resize",
    "reinforce",
    "retarget",
    "transfer_in",
    "transfer_out",
    "cancel",
    "restart"
  ];
  return allowed.indexOf(action) >= 0 ? action : "";
}

function operationPayloadOperationId(operation) {
  var update = (operation && operation.update) || {};
  var vector = update.vector || {};
  var intervention = (operation && operation.intervention) || {};
  var execution = intervention.command_execution || {};
  return String(
    operation && operation.operation_id ||
    execution.operation_id ||
    intervention.operation_id ||
    update.operation_id ||
    vector.operation_id ||
    operationPayloadUpdateId(operation) ||
    ""
  );
}

function commandOperationPayloads(data) {
  if (!data || typeof data !== "object") { return []; }
  if (Array.isArray(data.operations)) {
    return data.operations.filter(function(operation) {
      return operation && typeof operation === "object";
    });
  }
  if (data.operation_registry_authoritative === true) {
    return [];
  }
  var payloads = [];
  var updateIds = commandConsoleDataUpdateIds(data);
  if (updateIds.length || data.command_text) {
    var updateId = commandConsolePreferredUpdateId(data) || updateIds[0] || "";
    payloads.push({
      operation_id: updateId,
      update_id: updateId,
      command_text: data.command_text || "",
      transport_status: data.status || "",
      consumption_status: data.consumption_status || "",
      compile_result: data.compile_result || {},
      update: data.update || {},
      intervention: data.intervention || {},
      command_queue: data.command_queue || {},
      telemetry_frame: commandConsoleTelemetryFrame(data),
      disposition: ""
    });
  }
  if (!payloads.length && Array.isArray(data.modulation_results)) {
    data.modulation_results.forEach(function(result) {
      commandOperationPayloads(result).forEach(function(operation) {
        payloads.push(operation);
      });
    });
  }
  return payloads;
}

function operationExecutionMatchesPayload(
  operation,
  execution,
  operationId,
  updateId,
  operationGeneration
) {
  if (!execution || typeof execution !== "object") { return false; }
  var executionOwnerUpdateId = String(
    operation && operation.operation_console_execution_owner_update_id ||
    updateId ||
    ""
  );
  var executionGeneration = Number(
    execution.operation_generation || execution.generation || 0
  );
  return Boolean(
    operationId &&
    updateId &&
    Number.isFinite(operationGeneration) &&
    operationGeneration > 0 &&
    executionOwnerUpdateId &&
    String(execution.command_id || "") === executionOwnerUpdateId &&
    String(execution.operation_id || "") === operationId &&
    Number.isFinite(executionGeneration) &&
    executionGeneration === operationGeneration
  );
}

function commandOperationData(operation, parentData) {
  var operationId = operationPayloadOperationId(operation);
  var updateId = operationPayloadUpdateId(operation);
  var scopeId = operationPayloadScopeId(operation, parentData);
  var operationGeneration = Number(operation.operation_generation || 0);
  var intervention = Object.assign({}, operation.intervention || {});
  var execution = intervention.command_execution || {};
  if (
    operationGeneration > 0 &&
    Object.keys(execution).length &&
    !operationExecutionMatchesPayload(
      operation,
      execution,
      operationId,
      updateId,
      operationGeneration
    )
  ) {
    intervention.command_execution = {};
  }
  if (
    (intervention.telemetry_frame === null ||
      intervention.telemetry_frame === undefined ||
      intervention.telemetry_frame === "") &&
    operation.telemetry_frame !== null &&
    operation.telemetry_frame !== undefined
  ) {
    intervention.telemetry_frame = operation.telemetry_frame;
  }
  var disposition = String(operation.disposition || "");
  var status = String(
    operation.transport_status ||
    operation.status ||
    parentData && parentData.status ||
    ""
  );
  if (disposition === "superseded") { status = "superseded"; }
  if (
    disposition === "blocked" &&
    !(intervention.command_execution && intervention.command_execution.failed)
  ) {
    status = "publish_failed";
  }
  return {
    status: status,
    command_text: String(operation.command_text || ""),
    consumption_status: String(operation.consumption_status || ""),
    compile_result: operation.compile_result || {},
    latest_request: operation.latest_request || null,
    update: operation.update || {},
    intervention: intervention,
    command_queue: operation.command_queue || {},
    dashboard: parentData && parentData.dashboard || {},
    blackboard_scope_id: scopeId,
    update_id: updateId,
    operation_id: operationId,
    operation_key: operationRecordKey(scopeId, operationId),
    operation_generation: operationGeneration,
    requested_operation_generation: Number(
      operation.requested_operation_generation ||
      operation.operation_generation ||
      0
    ),
    operation_console_execution_owner_update_id: String(
      operation.operation_console_execution_owner_update_id ||
      updateId ||
      ""
    ),
    operation_disposition: disposition,
    operation_mission: String(operation.mission || "operation"),
    operation_edit: operation.operation_edit || {},
    operation_convergence: operation.operation_convergence || {},
    battlefield_operation: operation.battlefield_operation || null,
    battlefield_overview: parentData && parentData.battlefield_overview || null,
    semantic_timeline: Array.isArray(operation.semantic_timeline)
      ? operation.semantic_timeline
      : [],
    squad_order: String(operation.squad_order || ""),
    family_evidence: Array.isArray(operation.family_evidence)
      ? operation.family_evidence
      : [],
    telemetry_current: operation.telemetry_current === true
  };
}

function commandConsoleDataWithoutForeignOperation(
  data,
  updateId,
  operationId,
  operationGeneration
) {
  var result = Object.assign({}, data || {});
  result.battlefield_operation = null;
  var intervention = Object.assign({}, result.intervention || {});
  var execution = intervention.command_execution || {};
  var executionGeneration = Number(
    execution.operation_generation || execution.generation || 0
  );
  var executionOwnerUpdateId = String(
    result.operation_console_execution_owner_update_id ||
    updateId ||
    ""
  );
  var executionIsOperationScoped = Boolean(
    execution.operation_id || executionGeneration > 0
  );
  var executionMatches = Boolean(
    updateId &&
    operationId &&
    operationGeneration > 0 &&
    operationPayloadUpdateId(result) === String(updateId) &&
    String(execution.command_id || "") === executionOwnerUpdateId &&
    String(execution.operation_id || "") === String(operationId) &&
    executionGeneration === Number(operationGeneration)
  );
  if (executionIsOperationScoped && !executionMatches) {
    intervention.command_execution = {};
    result.intervention = intervention;
  }
  return result;
}

function commandConsoleDataForCanonicalOperation(
  data,
  updateId,
  operationId,
  operationGeneration
) {
  if (!data || typeof data !== "object" || !updateId) { return data; }
  var normalizedUpdateId = String(updateId);
  var normalizedOperationId = String(operationId || "");
  var normalizedGeneration = Number(operationGeneration || 0);
  var candidates = [];
  var seenCandidates = {};
  var hasCanonicalPayload = Boolean(data.battlefield_operation);
  function addCandidate(operation, candidate) {
    if (!candidate || !candidate.battlefield_operation) { return; }
    var payloadUpdateId = operationPayloadUpdateId(operation);
    var candidateOperationId = String(candidate.operation_id || "");
    var candidateGeneration = Number(candidate.operation_generation || 0);
    if (
      payloadUpdateId !== normalizedUpdateId ||
      !candidateOperationId ||
      candidateGeneration <= 0
    ) {
      return;
    }
    if (
      normalizedOperationId &&
      candidateOperationId !== normalizedOperationId
    ) {
      return;
    }
    if (
      normalizedGeneration > 0 &&
      candidateGeneration !== normalizedGeneration
    ) {
      return;
    }
    var key = [
      payloadUpdateId,
      candidateOperationId,
      candidateGeneration
    ].join("\u0000");
    if (seenCandidates[key]) { return; }
    seenCandidates[key] = true;
    candidates.push({
      data: candidate,
      operationId: candidateOperationId,
      generation: candidateGeneration,
      frame: commandConsoleTelemetryFrame(candidate)
    });
  }
  if (data.operation_id && data.battlefield_operation) {
    addCandidate(data, data);
  }
  commandOperationPayloads(data).forEach(function(operation) {
    if (operation.battlefield_operation) {
      hasCanonicalPayload = true;
    }
    var candidate = commandOperationData(operation, data);
    addCandidate(operation, candidate);
  });
  if (!candidates.length && !hasCanonicalPayload) {
    return data;
  }
  if (
    !candidates.length ||
    (!normalizedOperationId && candidates.length !== 1)
  ) {
    return commandConsoleDataWithoutForeignOperation(
      data,
      normalizedUpdateId,
      normalizedOperationId,
      normalizedGeneration
    );
  }
  candidates.sort(function(left, right) {
    if (left.generation !== right.generation) {
      return right.generation - left.generation;
    }
    return right.frame - left.frame;
  });
  var operationData = candidates[0].data;
  var result = Object.assign({}, data, operationData);
  [
    "compile_result",
    "update",
    "command_queue"
  ].forEach(function(field) {
    if (
      operationData[field] &&
      typeof operationData[field] === "object" &&
      !Object.keys(operationData[field]).length
    ) {
      result[field] = data[field] || operationData[field];
    }
  });
  if (!operationData.latest_request && data.latest_request) {
    result.latest_request = data.latest_request;
  }
  if (!operationData.command_text && data.command_text) {
    result.command_text = data.command_text;
  }
  return result;
}

function operationPayloadSessionEpoch(data, operations) {
  var overview = data && data.battlefield_overview;
  var overviewIdentity = overview && typeof overview === "object"
    ? overview.identity || {}
    : {};
  var overviewEpoch = String(overviewIdentity.session_epoch || "");
  if (overviewEpoch) { return overviewEpoch; }
  var projectionIdentity = data &&
    typeof data.battlefield_projection_identity === "object"
    ? data.battlefield_projection_identity || {}
    : {};
  var projectionEpoch = String(
    projectionIdentity.session_epoch || ""
  );
  if (projectionEpoch) { return projectionEpoch; }
  var payloads = Array.isArray(operations) ? operations : [];
  for (var index = 0; index < payloads.length; index += 1) {
    var projection = payloads[index] && payloads[index].battlefield_operation;
    var identity = projection && typeof projection === "object"
      ? projection.identity || {}
      : {};
    var epoch = String(identity.session_epoch || "");
    if (epoch) { return epoch; }
  }
  return "";
}

function operationCanonicalProjectionMatches(data) {
  var projection = data && data.battlefield_operation;
  if (!projection || typeof projection !== "object") { return false; }
  var identity = projection.identity || {};
  var operationId = String(data.operation_id || "");
  var projectionUpdateId = String(
    data.operation_console_execution_owner_update_id ||
    operationPayloadUpdateId(data || {}) ||
    ""
  );
  var generation = Number(data.operation_generation || 0);
  var projectionGeneration = Number(projection.generation || 0);
  return Boolean(
    operationId &&
    projectionUpdateId &&
    generation > 0 &&
    String(identity.update_id || "") === projectionUpdateId &&
    String(projection.operation_id || "") === operationId &&
    projectionGeneration === generation &&
    String(identity.operation_id || "") === operationId &&
    Number(identity.generation || 0) === generation
  );
}

function normalizeOperationTerminalState(value) {
  var state = String(value || "").toLowerCase();
  if (state === "success" || state === "succeeded") {
    return "completed";
  }
  if (state === "canceled") {
    return "cancelled";
  }
  return state;
}

function operationCanonicalTerminalState(data) {
  var projection = data && data.battlefield_operation;
  if (!operationCanonicalProjectionMatches(data)) { return ""; }
  var completion = projection.operation_completion || {};
  var lifetime = projection.operation_lifetime || {};
  var state = normalizeOperationTerminalState(completion.state);
  var lifetimeState = normalizeOperationTerminalState(
    lifetime.completion_state
  );
  var generation = Number(data.operation_generation || 0);
  var recognizedStates = [
    "completed",
    "failed",
    "cancelled",
    "expired",
    "superseded"
  ];
  if (
    Number(completion.generation || 0) === generation &&
    completion.terminal === true &&
    lifetime.completed === true &&
    recognizedStates.indexOf(state) >= 0 &&
    (!lifetimeState || lifetimeState === state)
  ) {
    return state;
  }
  return "";
}

function operationCanonicalCompletion(data) {
  return (
    ["completed"].indexOf(
      operationCanonicalTerminalState(data)
    ) >= 0
  );
}

function operationRecordDisposition(model, data) {
  var reported = String(
    data && data.operation_disposition || ""
  ).toLowerCase();
  if (model.cancelled) {
    return commandConsoleTerminalCleanupVerified(model)
      ? "superseded"
      : "active";
  }
  if (model.superseded) { return "superseded"; }
  if (model.blocked) {
    return (
      reported === "expired" || model.canonicalState === "expired"
    ) ? "expired" : "blocked";
  }
  if (model.canonicalCompletionVerified) {
    return "completed";
  }
  if (reported === "completed") { return "active"; }
  return reported || "active";
}

function operationRecordTerminal(model, data) {
  var disposition = operationRecordDisposition(model, data);
  return Boolean(
    disposition === "completed" ||
    disposition === "blocked" ||
    disposition === "expired" ||
    disposition === "superseded"
  );
}

function rememberRetiredOperationSessionEpoch(sessionEpoch) {
  var normalized = String(sessionEpoch || "");
  if (!normalized) { return; }
  operationConsoleRetiredSessionEpochs =
    operationConsoleRetiredSessionEpochs.filter(function(epoch) {
      return epoch !== normalized;
    });
  operationConsoleRetiredSessionEpochs.push(normalized);
  operationConsoleRetiredSessionEpochs =
    operationConsoleRetiredSessionEpochs.slice(-8);
}

function operationSessionEpochIsStale(currentEpoch, incomingEpoch) {
  var current = String(currentEpoch || "");
  var incoming = String(incomingEpoch || "");
  if (!current || !incoming || current === incoming) { return false; }
  if (operationConsoleRetiredSessionEpochs.indexOf(incoming) >= 0) {
    return true;
  }
  var currentNumber = Number(current);
  var incomingNumber = Number(incoming);
  return Boolean(
    Number.isFinite(currentNumber) &&
    Number.isFinite(incomingNumber) &&
    incomingNumber < currentNumber
  );
}

function resetOperationConsoleRegistry(preserveEpochHistory) {
  operationRecords = {};
  operationRecordOrder = [];
  operationConsoleScopeId = "";
  operationConsoleSessionEpoch = "";
  if (preserveEpochHistory !== true) {
    operationConsoleRetiredSessionEpochs = [];
  }
  selectedOperationKey = "";
  var lanes = ensureOperationLaneContainers();
  Object.keys(lanes).forEach(function(name) {
    lanes[name].textContent = "";
    setMicroMachineText("operation-lane-" + name + "-count", "0");
  });
  renderOperationTimeline(null);
  setMicroMachineText(
    "operation-summary",
    commandUiText("활성 작전 0개", "0 active operations", "0 个活跃作战")
  );
}

function beginOperationRecord(text, pendingId) {
  operationRecordSeq += 1;
  var clientId = String(pendingId || ("client-operation-" + operationRecordSeq));
  var key = operationRecordKey("pending", clientId);
  operationRecords[key] = {
    key: key,
    pendingId: clientId,
    scopeId: "",
    updateId: "",
    operationId: clientId,
    text: String(text || ""),
    data: {
      status: "received",
      command_text: String(text || ""),
      consumption_status: "received",
      operation_id: clientId
    },
    stageRank: 0,
    telemetryFrame: -1,
    operationGeneration: 0,
    requestedOperationGeneration: 0,
    terminal: false,
    disposition: "pending",
    createdAt: Date.now(),
    domId: "operation-card-" + operationRecordSeq,
    node: null
  };
  operationRecordOrder.push(key);
  renderOperationRecords();
  return operationRecords[key];
}

function rekeyOperationRecord(record, newKey) {
  if (!record || !newKey || record.key === newKey) { return record; }
  var oldKey = record.key;
  delete operationRecords[oldKey];
  record.key = newKey;
  operationRecords[newKey] = record;
  if (selectedOperationKey === oldKey) {
    selectedOperationKey = newKey;
  }
  var index = operationRecordOrder.indexOf(oldKey);
  if (index >= 0) {
    operationRecordOrder[index] = newKey;
  }
  return record;
}

function bindOperationRecordUpdate(text, pendingId, scopeId, updateId) {
  var record = null;
  Object.keys(operationRecords).some(function(key) {
    var candidate = operationRecords[key];
    if (
      candidate &&
      candidate.pendingId &&
      pendingId &&
      candidate.pendingId === pendingId
    ) {
      record = candidate;
      return true;
    }
    return false;
  });
  if (!record) { return false; }
  record.text = String(text || record.text || "");
  record.scopeId = String(scopeId || record.scopeId || "");
  record.updateId = String(updateId || record.updateId || "");
  if (record.updateId) {
    record.operationId = record.updateId;
    rekeyOperationRecord(
      record,
      operationRecordKey(record.scopeId, record.operationId)
    );
  }
  renderOperationRecords();
  return true;
}

function operationRecordForCandidate(key, updateId) {
  if (operationRecords[key]) { return operationRecords[key]; }
  var match = null;
  Object.keys(operationRecords).some(function(recordKey) {
    var candidate = operationRecords[recordKey];
    if (
      candidate &&
      updateId &&
      candidate.updateId === updateId &&
      (
        candidate.pendingId ||
        !candidate.operationId ||
        candidate.operationId === updateId
      )
    ) {
      match = candidate;
      return true;
    }
    return false;
  });
  return match;
}

function operationFamilyEvidenceKey(item) {
  return [
    String(item && item.family || ""),
    String(item && item.role || ""),
    String(item && item.action || "")
  ].join("|");
}

function operationFamilyEvidenceRank(item) {
  var stageRank = {
    waiting: 0,
    represented: 1,
    assigned: 2,
    attempted: 3,
    executed: 4,
    effect: 5,
    blocked: 6
  };
  return [
    Number(item && item.attempt_generation || 0),
    Number(item && item.effect_frame || 0),
    Number(item && item.submitted_frame || 0),
    Number(item && item.attempted_frame || 0),
    Number(item && item.effect_count || 0),
    Number(item && item.submitted_count || 0),
    Number(item && item.attempted_count || 0),
    Number(stageRank[String(item && item.stage || "waiting")] || 0)
  ];
}

function operationFamilyEvidenceRankCompare(left, right) {
  var leftRank = operationFamilyEvidenceRank(left);
  var rightRank = operationFamilyEvidenceRank(right);
  for (var index = 0; index < leftRank.length; index += 1) {
    if (leftRank[index] !== rightRank[index]) {
      return leftRank[index] - rightRank[index];
    }
  }
  return 0;
}

function mergeOperationFamilyEvidence(previous, incoming) {
  var merged = {};
  var order = [];
  function absorb(item) {
    if (!item || typeof item !== "object") { return; }
    var key = operationFamilyEvidenceKey(item);
    if (!Object.prototype.hasOwnProperty.call(merged, key)) {
      order.push(key);
      merged[key] = item;
      return;
    }
    if (operationFamilyEvidenceRankCompare(item, merged[key]) >= 0) {
      merged[key] = item;
    }
  }
  (Array.isArray(previous) ? previous : []).forEach(absorb);
  (Array.isArray(incoming) ? incoming : []).forEach(absorb);
  return order.map(function(key) { return merged[key]; });
}

function reconcileOperationRecord(operation, parentData) {
  var data = commandOperationData(operation, parentData);
  var operationId = String(data.operation_id || "");
  var updateId = operationPayloadUpdateId(operation);
  var requestUpdateId = operationPayloadRequestUpdateId(operation);
  var executionOwnerUpdateId =
    operationPayloadExecutionOwnerUpdateId(operation);
  var scopeId = String(data.blackboard_scope_id || "");
  if (!operationId) { return null; }
  var key = String(
    data.operation_key ||
    operationRecordKey(scopeId, operationId)
  );
  var record = operationRecordForCandidate(key, updateId);
  var incomingProjection = data && data.battlefield_operation;
  var projectionIdentityMismatch = Boolean(
    incomingProjection &&
    typeof incomingProjection === "object" &&
    Object.keys(incomingProjection).length &&
    !operationCanonicalProjectionMatches(data)
  );
  if (projectionIdentityMismatch) {
    var acceptedProjectionData = record && record.data || {};
    data = Object.assign({}, data, {
      battlefield_operation:
        acceptedProjectionData.battlefield_operation || {},
      telemetry_frame: record
        ? record.telemetryFrame
        : -1
    });
  }
  var model = commandConsoleStageModel(data);
  var telemetryFrame = commandConsoleTelemetryFrame(data);
  var stageRank = commandConsoleStageRank(model);
  var operationGeneration = Number(
    model.execution.operation_generation ||
    data.operation_generation ||
    0
  );
  if (!Number.isFinite(operationGeneration) || operationGeneration < 0) {
    operationGeneration = 0;
  }
  var requestedOperationGeneration = Number(
    data.requested_operation_generation ||
    operationGeneration ||
    0
  );
  if (
    !Number.isFinite(requestedOperationGeneration) ||
    requestedOperationGeneration < 0
  ) {
    requestedOperationGeneration = 0;
  }
  var editPayload = operationEditPayload(data);
  var editAction = operationPayloadEditAction(data);
  var hasEditPayload = Boolean(editAction);
  var rejectedEditPayload = Boolean(
    String(editPayload.resolution || "").toLowerCase() === "blocked" ||
    Boolean(editPayload.blocker)
  );
  var latestRequestedOperationGeneration = record
    ? Number(
      record.requestedOperationGeneration ||
      record.operationGeneration ||
      0
    )
    : 0;
  if (
    !Number.isFinite(latestRequestedOperationGeneration) ||
    latestRequestedOperationGeneration < 0
  ) {
    latestRequestedOperationGeneration = 0;
  }
  var acceptedRequestUpdateId = String(record && record.updateId || "");
  var acceptedExecutionOwnerUpdateId = String(
    record && record.data &&
      record.data.operation_console_execution_owner_update_id ||
    acceptedRequestUpdateId ||
    ""
  );
  var sameGenerationUpdateIdentityAccepted = Boolean(
    acceptedRequestUpdateId &&
    acceptedExecutionOwnerUpdateId &&
    requestUpdateId === acceptedRequestUpdateId &&
    executionOwnerUpdateId === acceptedExecutionOwnerUpdateId
  );
  var sameGenerationUpdateIdentityRequired = Boolean(
    record &&
    record.operationGeneration > 0 &&
    operationGeneration === record.operationGeneration
  );
  var sameGenerationExecutionOwnerUpdate = Boolean(
    sameGenerationUpdateIdentityRequired &&
    requestedOperationGeneration < latestRequestedOperationGeneration &&
    acceptedRequestUpdateId &&
    acceptedExecutionOwnerUpdateId &&
    (
      requestUpdateId === acceptedRequestUpdateId ||
      requestUpdateId === acceptedExecutionOwnerUpdateId
    ) &&
    executionOwnerUpdateId === acceptedExecutionOwnerUpdateId
  );
  var newerOperationRequest = Boolean(
    requestedOperationGeneration > latestRequestedOperationGeneration &&
    requestUpdateId &&
    requestUpdateId !== acceptedRequestUpdateId &&
    requestUpdateId !== acceptedExecutionOwnerUpdateId &&
    executionOwnerUpdateId === acceptedExecutionOwnerUpdateId &&
    hasEditPayload
  );
  var foreignExecution = (data.intervention || {}).command_execution || {};
  var foreignExecutionGeneration = Number(
    foreignExecution.operation_generation ||
    foreignExecution.generation ||
    0
  );
  var cancellationIdentityMatches = Boolean(
    ["cancelled", "canceled"].indexOf(
      String(foreignExecution.state || "").toLowerCase()
    ) >= 0 &&
    String(foreignExecution.blocker_reason || "").toLowerCase() ===
      "cancelled_by_policy" &&
    executionOwnerUpdateId &&
    executionOwnerUpdateId === acceptedExecutionOwnerUpdateId &&
    String(foreignExecution.operation_id || "") === operationId &&
    foreignExecutionGeneration === operationGeneration
  );
  var sameGenerationCancellationTransition = Boolean(
    cancellationIdentityMatches &&
    requestUpdateId === acceptedRequestUpdateId
  );
  var newerCancellationRequest = Boolean(
    cancellationIdentityMatches &&
    newerOperationRequest &&
    editAction === "cancel"
  );
  var cancellationState = (
    ["cancelled", "canceled"].indexOf(
      String(foreignExecution.state || "").toLowerCase()
    ) >= 0
  );
  if (
    sameGenerationUpdateIdentityRequired &&
    !sameGenerationUpdateIdentityAccepted &&
    !sameGenerationExecutionOwnerUpdate &&
    (
      cancellationState
        ? !sameGenerationCancellationTransition &&
          !newerCancellationRequest
        : !newerOperationRequest
    )
  ) {
    return record;
  }
  var staleRequestedGeneration = Boolean(
    record &&
    requestedOperationGeneration > 0 &&
    requestedOperationGeneration < latestRequestedOperationGeneration
  );
  if (
    staleRequestedGeneration &&
    operationGeneration <= record.operationGeneration &&
    !sameGenerationExecutionOwnerUpdate
  ) {
    return record;
  }
  if (
    staleRequestedGeneration &&
    (
      operationGeneration > record.operationGeneration ||
      sameGenerationExecutionOwnerUpdate
    )
  ) {
    var latestData = record.data || {};
    var executionOwnerUpdateId = String(
      data.operation_console_execution_owner_update_id ||
      (data.intervention || {}).command_execution &&
        (data.intervention || {}).command_execution.command_id ||
      data.update_id ||
      ""
    );
    data = Object.assign({}, data, {
      update_id: record.updateId || data.update_id || "",
      command_text: record.text || latestData.command_text || "",
      compile_result: latestData.compile_result || {},
      latest_request: latestData.latest_request || null,
      update: latestData.update || {},
      command_queue: latestData.command_queue || {},
      requested_operation_generation: latestRequestedOperationGeneration,
      operation_edit: operationEditPayload(latestData),
      operation_console_execution_owner_update_id: executionOwnerUpdateId
    });
    updateId = record.updateId || updateId;
  }
  var rejectedEditRefresh = Boolean(
    record &&
    record.terminal &&
    operationGeneration === record.operationGeneration &&
    requestedOperationGeneration > latestRequestedOperationGeneration &&
    rejectedEditPayload
  );
  if (
    record &&
    record.operationGeneration > 0 &&
    operationGeneration <= 0
  ) {
    return record;
  }
  if (
    record &&
    record.operationGeneration > 0 &&
    operationGeneration > 0 &&
    operationGeneration < record.operationGeneration
  ) {
    return record;
  }
  if (
    record &&
    operationGeneration > record.operationGeneration
  ) {
    record.stageRank = 0;
    record.telemetryFrame = -1;
    record.terminal = false;
    record.disposition = "pending";
  }
  if (record && record.terminal && !rejectedEditRefresh) {
    return record;
  }
  if (
    record &&
    telemetryFrame >= 0 &&
    record.telemetryFrame >= 0 &&
    telemetryFrame < record.telemetryFrame
  ) {
    return record;
  }
  if (record && stageRank < record.stageRank && !rejectedEditRefresh) {
    return record;
  }
  if (!record) {
    operationRecordSeq += 1;
    record = {
      key: key,
      pendingId: "",
      scopeId: scopeId,
      updateId: updateId,
      operationId: operationId,
      text: "",
      data: null,
      stageRank: 0,
      telemetryFrame: -1,
      operationGeneration: operationGeneration,
      requestedOperationGeneration: requestedOperationGeneration,
      terminal: false,
      disposition: "pending",
      createdAt: Date.now(),
      domId: "operation-card-" + operationRecordSeq,
      node: null
    };
    operationRecords[key] = record;
    operationRecordOrder.push(key);
  } else {
    rekeyOperationRecord(record, key);
  }
  record.pendingId = "";
  record.scopeId = scopeId || record.scopeId;
  record.updateId = updateId || record.updateId;
  record.operationId = operationId;
  record.text = String(
    data.command_text ||
    commandConsoleGoal(data, record.text) ||
    record.text ||
    operationId
  );
  if (
    record.data &&
    operationGeneration === record.operationGeneration
  ) {
    data.family_evidence = mergeOperationFamilyEvidence(
      record.data.family_evidence,
      data.family_evidence
    );
    data.semantic_timeline = mergeOperationSemanticTimeline(
      record.data.semantic_timeline,
      data.semantic_timeline
    );
  }
  record.data = data;
  record.stageRank = Math.max(record.stageRank, stageRank);
  record.telemetryFrame = Math.max(record.telemetryFrame, telemetryFrame);
  record.operationGeneration = operationGeneration || record.operationGeneration;
  record.requestedOperationGeneration = Math.max(
    record.requestedOperationGeneration || 0,
    requestedOperationGeneration
  );
  var operationTerminal = operationRecordTerminal(model, data);
  record.terminal = rejectedEditRefresh
    ? Boolean(record.terminal || operationTerminal)
    : operationTerminal;
  record.disposition = operationRecordDisposition(model, data);
  return record;
}

function operationAppendDetail(container, label, value, extraClass) {
  var detail = document.createElement("div");
  detail.className = "operation-card-detail" + (extraClass ? " " + extraClass : "");
  var labelNode = document.createElement("span");
  labelNode.textContent = label;
  var valueNode = document.createElement("strong");
  valueNode.textContent = value;
  detail.appendChild(labelNode);
  detail.appendChild(valueNode);
  container.appendChild(detail);
}

function operationProjection(data) {
  var projection = data && data.battlefield_operation;
  return projection && typeof projection === "object" ? projection : {};
}

function operationRouteSummary(data) {
  var route = operationProjection(data).operation_route || {};
  var requested = String(route.requested_route_type || "-");
  var applied = String(route.applied_route_type || "-");
  var target = String(
    route.resolved_target_label ||
    route.location_intent ||
    route.target_type ||
    "-"
  );
  return requested + " → " + applied + " · " + target;
}

function operationLifetimeSummary(data) {
  var lifetime = operationProjection(data).operation_lifetime || {};
  var conditions = Array.isArray(lifetime.completion_conditions)
    ? lifetime.completion_conditions.join(", ")
    : "-";
  var parts = [
    String(lifetime.mode || "-"),
    commandUiText("종료 ", "deadline ", "截止 ") +
      String(lifetime.deadline_frame || "-"),
    conditions
  ];
  if (lifetime.standing === true) {
    parts.push(commandUiText("지속 작전", "standing", "持续作战"));
  }
  return parts.join(" · ");
}

function operationForceSummary(data) {
  var projection = operationProjection(data);
  var ownership = projection.operation_ownership || {};
  var launch = projection.operation_launch_policy || {};
  var convergence = data && data.operation_convergence || {};
  var requested = Number(
    convergence.target_count ||
    launch.max_units ||
    launch.min_units ||
    0
  );
  var represented = Number(convergence.represented_count || 0);
  var owners = Number(ownership.owner_count || 0);
  return commandUiText("요구 ", "requested ", "请求 ") + requested +
    commandUiText(" · 반영 ", " · represented ", " · 已反映 ") + represented +
    commandUiText(" · 실제 소유 ", " · exact owners ", " · 实际所有者 ") + owners;
}

function operationLaunchSummary(data) {
  var launch = operationProjection(data).operation_launch_policy || {};
  var decision = String(launch.decision || "unavailable");
  var launchCount = Number(launch.launch_count || 0);
  var missing = Number(launch.missing_count || 0);
  var safety = launch.partial_launch_allowed === true
    ? (
      launch.partial_launch_safe === true
        ? commandUiText("부분 출동 안전", "partial launch safe", "部分出动安全")
        : commandUiText("부분 출동 불가", "partial launch unsafe", "部分出动不安全")
    )
    : commandUiText("정원 출동", "full-force policy", "满编策略");
  return decision + " · " + launchCount +
    commandUiText(" 출동 · 부족 ", " launched · missing ", " 出动 · 缺少 ") +
    missing + " · " + safety;
}

function operationCompletionSummary(data) {
  var completion = operationProjection(data).operation_completion || {};
  var observed = [];
  if (completion.movement_observed === true) {
    observed.push(commandUiText("이동", "movement", "移动"));
  }
  if (completion.engagement_observed === true) {
    observed.push(commandUiText("교전", "engagement", "交战"));
  }
  if (completion.target_reached === true) {
    observed.push(commandUiText("목표 도달", "target reached", "到达目标"));
  }
  if (completion.terminal === true) {
    observed.push(commandUiText("권위 종료", "authoritative terminal", "权威终止"));
  }
  return observed.length
    ? observed.join(" · ") + (completion.reason ? " · " + completion.reason : "")
    : commandUiText("관측 증거 대기", "awaiting observed evidence", "等待观测证据");
}

function operationBlockerSummary(data) {
  var projection = operationProjection(data);
  var launch = projection.operation_launch_policy || {};
  var lifetime = projection.operation_lifetime || {};
  var convergence = data && data.operation_convergence || {};
  var intervention = data && data.intervention || {};
  var execution = intervention.command_execution || {};
  var blocker = String(
    launch.blocker ||
    convergence.blocker ||
    execution.blocker_reason ||
    ""
  );
  var choices = Array.isArray(launch.recommended_choices)
    ? launch.recommended_choices
    : [];
  var resolution = choices.length
    ? choices.join(", ")
    : (
      Array.isArray(lifetime.completion_conditions)
        ? lifetime.completion_conditions.join(", ")
        : ""
    );
  return (blocker || commandUiText("차단 없음", "no blocker", "无阻塞")) +
    " · " +
    (resolution || commandUiText("런타임 재평가", "runtime re-evaluation", "运行时重新评估"));
}

function mergeOperationSemanticTimeline(previous, incoming) {
  var merged = {};
  (Array.isArray(previous) ? previous : []).concat(
    Array.isArray(incoming) ? incoming : []
  ).forEach(function(event) {
    if (!event || typeof event !== "object") { return; }
    var sequence = Number(event.timeline_seq || 0);
    var key = sequence > 0
      ? String(sequence)
      : [
        event.operation_id,
        event.generation,
        event.kind,
        event.game_frame,
        event.summary
      ].join("|");
    merged[key] = event;
  });
  return Object.keys(merged).map(function(key) {
    return merged[key];
  }).sort(function(left, right) {
    return Number(left.timeline_seq || 0) - Number(right.timeline_seq || 0);
  }).slice(-32);
}

function operationEditPayload(data) {
  var edit = data && data.operation_edit;
  return edit && typeof edit === "object" ? edit : {};
}

function operationCompositionLabel(value) {
  if (!Array.isArray(value) || !value.length) {
    return commandUiText("없음", "none", "无");
  }
  return value.map(function(requirement) {
    var unitType = String(requirement && requirement.unit_type || "")
      .replace(/^TERRAN_/, "");
    var count = Number(requirement && requirement.count || 0);
    return unitType + " ×" + count;
  }).join(" · ");
}

function operationEditSummary(data) {
  var edit = operationEditPayload(data);
  var action = String(edit.action || "");
  if (!action) { return ""; }
  var counterpart = String(edit.counterpart_operation_id || "");
  return counterpart ? action + " · " + counterpart : action;
}

function operationEditForceChange(data) {
  var edit = operationEditPayload(data);
  if (!edit.action) { return ""; }
  return operationCompositionLabel(edit.before_composition) +
    " → " + operationCompositionLabel(edit.after_composition);
}

function operationEditResolution(data) {
  var edit = operationEditPayload(data);
  if (!edit.action) { return ""; }
  var parts = [];
  if (edit.resolution) { parts.push(String(edit.resolution)); }
  if (edit.blocker) { parts.push(String(edit.blocker)); }
  if (Number(edit.transferred_in_count || 0) > 0) {
    parts.push("+" + Number(edit.transferred_in_count) + " transferred");
  }
  if (Number(edit.transferred_out_count || 0) > 0) {
    parts.push("-" + Number(edit.transferred_out_count) + " transferred");
  }
  return parts.join(" · ") || commandUiText(
    "런타임 적용 대기",
    "awaiting runtime application",
    "等待运行时应用"
  );
}

function operationConvergenceSummary(data) {
  var convergence = data && data.operation_convergence;
  if (!convergence || typeof convergence !== "object") { return ""; }
  var requirements = Array.isArray(convergence.requirements)
    ? convergence.requirements
    : [];
  var requirementParts = requirements.map(function(requirement) {
    var unitType = String(requirement && requirement.unit_type || "")
      .replace(/^TERRAN_/, "");
    var represented = Number(
      requirement && requirement.represented_count || 0
    );
    var target = Number(requirement && requirement.target_count || 0);
    var missing = Number(requirement && requirement.missing_count || 0);
    var completed = Number(
      requirement && requirement.completed_count || 0
    );
    var inProgress = Number(
      requirement && requirement.in_progress_count || 0
    );
    var queued = Number(requirement && requirement.queued_count || 0);
    var blocker = String(
      requirement && requirement.production_blocker || ""
    );
    var missingPrerequisites = Array.isArray(
      requirement && requirement.missing_prerequisites
    )
      ? requirement.missing_prerequisites.map(function(item) {
        return String(item || "").replace(/^TERRAN_/, "");
      }).filter(Boolean)
      : [];
    var text = unitType + " " + represented + "/" + target;
    var pipelineParts = [];
    if (completed > 0) {
      pipelineParts.push(
        commandUiText("완료 ", "ready ", "已完成 ") + completed
      );
    }
    if (inProgress > 0) {
      pipelineParts.push(
        commandUiText("생산 중 ", "training ", "生产中 ") + inProgress
      );
    }
    if (queued > 0) {
      pipelineParts.push(
        commandUiText("큐 ", "queued ", "队列 ") + queued
      );
    }
    if (pipelineParts.length) {
      text += " · " + pipelineParts.join(" / ");
    }
    if (missing > 0) {
      text += commandUiText(" · 부족 ", " · missing ", " · 缺少 ") + missing;
    }
    if (missing > 0 && missingPrerequisites.length) {
      text += commandUiText(" · 필요 ", " · needs ", " · 需要 ") +
        missingPrerequisites.join(" → ");
    }
    var blockerLabels = {
      supply_blocked: commandUiText(
        "보급 막힘",
        "supply blocked",
        "补给受阻"
      ),
      gas_pending: commandUiText("가스 부족", "gas pending", "气体不足"),
      minerals_pending: commandUiText(
        "광물 부족",
        "minerals pending",
        "矿物不足"
      ),
      missing_producer: commandUiText(
        "생산 건물 대기",
        "producer missing",
        "缺少生产建筑"
      ),
      producer_busy: commandUiText(
        "생산 건물 사용 중",
        "producer busy",
        "生产建筑忙碌"
      ),
      missing_addon: commandUiText(
        "애드온 대기",
        "addon missing",
        "缺少附属建筑"
      ),
      missing_tech: commandUiText(
        "기술 건물 대기",
        "tech missing",
        "缺少科技建筑"
      ),
      production_queued: commandUiText(
        "생산 예약됨",
        "production queued",
        "已排入生产"
      ),
      training: commandUiText("생산 중", "training", "生产中"),
      composition_assignment_pending: commandUiText(
        "편성 배정 대기",
        "awaiting squad assignment",
        "等待编队分配"
      )
    };
    if (blocker && blocker !== "ready") {
      text += commandUiText(" · 상태 ", " · status ", " · 状态 ") +
        (blockerLabels[blocker] || blocker);
    }
    return text;
  }).filter(Boolean);
  if (requirementParts.length) {
    return requirementParts.join(" | ");
  }
  var targetCount = Number(convergence.target_count || 0);
  var representedCount = Number(convergence.represented_count || 0);
  if (targetCount > 0) {
    return representedCount + "/" + targetCount;
  }
  return "";
}

function operationFamilyEvidenceSummary(data) {
  var evidence = data && Array.isArray(data.family_evidence)
    ? data.family_evidence
    : [];
  if (!evidence.length) { return ""; }
  var stageLabels = {
    waiting: commandUiText("대기", "waiting", "等待"),
    represented: commandUiText("생산 반영", "represented", "已纳入生产"),
    assigned: commandUiText("배정", "assigned", "已分配"),
    attempted: commandUiText("명령 시도", "attempted", "已尝试命令"),
    executed: commandUiText("SC2 제출", "SC2 submitted", "已提交 SC2"),
    effect: commandUiText("효과 관측", "effect observed", "已观察效果"),
    blocked: commandUiText("차단", "blocked", "受阻")
  };
  return evidence.map(function(item) {
    var name = String(
      item && (item.display_name || item.family || item.unit_type) || ""
    ).replace(/^TERRAN_/, "");
    var role = String(item && item.role || "");
    var assigned = Number(item && item.assigned || 0);
    var represented = Number(item && item.represented || 0);
    var stage = String(item && item.stage || "waiting");
    var action = String(item && item.action || "");
    var effectKind = String(item && item.effect_kind || "");
    var effectCount = Number(item && item.effect_count || 0);
    var attemptedCount = Number(item && item.attempted_count || 0);
    var submittedCount = Number(item && item.submitted_count || 0);
    var blocker = String(item && item.blocker || "");
    var text = name;
    if (role) { text += "/" + role; }
    text += " " + assigned + "/" + represented;
    text += " · " + (stageLabels[stage] || stage);
    if (action) { text += " · " + action; }
    if (attemptedCount > 0) { text += " · try " + attemptedCount; }
    if (submittedCount > 0) { text += " · SC2 " + submittedCount; }
    if (effectKind) {
      text += " · " + effectKind;
      if (effectCount > 0) { text += " " + effectCount; }
    }
    if (blocker) { text += " · " + blocker; }
    return text;
  }).join(" | ");
}

function prefillOperationEdit(record, action) {
  var input = document.getElementById("command-input");
  if (!input) { return; }
  if (action === "reinforce") {
    input.value = currentLang === "en"
      ? "Reinforce operation " + record.operationId + " with "
      : (currentLang === "zh"
        ? "为作战 " + record.operationId + " 增援 "
        : "작전 " + record.operationId + "에 병력을 증원해: ");
  } else if (action === "retarget") {
    input.value = currentLang === "en"
      ? "Retarget operation " + record.operationId + " to "
      : (currentLang === "zh"
        ? "把作战 " + record.operationId + " 的目标改为 "
        : "작전 " + record.operationId + "의 목표를 변경해: ");
  } else {
    input.value = record.text || "";
  }
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

function operationActionButton(record, label, action, handler) {
  var button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.setAttribute("data-operation-action", action);
  button.setAttribute("data-operation-key", record.key);
  button.setAttribute(
    "aria-label",
    label + ": " + (record.text || record.operationId)
  );
  if (action === "cancel") {
    button.className = "operation-cancel-button";
    button.setAttribute(
      "aria-disabled",
      record.terminal ? "true" : "false"
    );
  }
  button.addEventListener("click", function(event) {
    if (button.getAttribute("aria-disabled") === "true") {
      if (event && typeof event.preventDefault === "function") {
        event.preventDefault();
      }
      return;
    }
    handler(event);
  });
  return button;
}

function operationResolutionDefinition(action) {
  var definitions = {
    launch_partial: {
      label: function() {
        return commandUiText("가용 병력 부분 출동", "Launch partial force", "部分兵力出动");
      },
      command: function(operationId) {
        return currentLang === "en"
          ? "Launch the canonically safe partial force for operation " + operationId
          : (currentLang === "zh"
            ? "让作战 " + operationId + " 按权威安全判定派出部分兵力"
            : "작전 " + operationId + "의 권위 안전 판정을 통과한 가용 병력을 부분 출동해");
      }
    },
    wait_for_full_force: {
      label: function() {
        return commandUiText("정원까지 대기", "Wait for full force", "等待满编");
      },
      command: function(operationId) {
        return currentLang === "en"
          ? "Keep operation " + operationId + " waiting until its full required force is ready"
          : (currentLang === "zh"
            ? "让作战 " + operationId + " 等待到所需兵力满编"
            : "작전 " + operationId + "을 요구 병력 정원 충족까지 대기시켜");
      }
    },
    transfer_available_units: {
      label: function() {
        return commandUiText("안전 이관 병력 전부", "Transfer safe available units", "转移全部安全可用兵力");
      },
      command: function(operationId) {
        return currentLang === "en"
          ? "Transfer every canonically safe available unit from operation " + operationId
          : (currentLang === "zh"
            ? "从作战 " + operationId + " 转移全部经权威安全判定的可用兵力"
            : "작전 " + operationId + "에서 권위 안전 판정을 통과한 가용 병력을 모두 이관해");
      }
    },
    transfer_two_units: {
      label: function() {
        return commandUiText("안전 이관 2기", "Transfer two safe units", "安全转移 2 个单位");
      },
      command: function(operationId) {
        return currentLang === "en"
          ? "Transfer exactly two canonically safe units from operation " + operationId
          : (currentLang === "zh"
            ? "从作战 " + operationId + " 精确转移 2 个经权威安全判定的单位"
            : "작전 " + operationId + "에서 권위 안전 판정을 통과한 병력 정확히 2기를 이관해");
      }
    }
  };
  return definitions[action] || null;
}

function operationSafeCommandIdentifier(operationId) {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(
    String(operationId || "")
  );
}

function operationResolutionChoice(
  action,
  safe,
  reason
) {
  var definition = operationResolutionDefinition(action);
  if (!definition) { return null; }
  return {
    action: action,
    label: definition.label(),
    safe: safe === true,
    reason: String(reason || "")
  };
}

function operationResolutionChoices(data) {
  var projection = operationProjection(data);
  var launch = projection.operation_launch_policy || {};
  var overview = data && data.battlefield_overview || {};
  var choices = [];
  var seen = {};
  var canonicalIdentity = operationCanonicalProjectionMatches(data);
  var operationIdSafe = operationSafeCommandIdentifier(
    data && data.operation_id
  );
  var launchSafety = launch.safety_evidence || {};
  function appendChoice(choice) {
    if (!choice || seen[choice.action]) { return; }
    seen[choice.action] = true;
    choices.push(choice);
  }
  (Array.isArray(launch.recommended_choices)
    ? launch.recommended_choices
    : []
  ).forEach(function(choice) {
    var action = String(choice || "");
    if (action === "launch_partial") {
      var partialSafe = Boolean(
        canonicalIdentity &&
        operationIdSafe &&
        launch.strict_scope === true &&
        launch.partial_launch_allowed === true &&
        launch.partial_launch_safe === true &&
        Number(launch.launch_count || 0) > 0 &&
        launchSafety.protected_defense_minimum_respected === true &&
        launchSafety.source_operation_minimum_respected === true &&
        !String(launch.blocker || "")
      );
      appendChoice(operationResolutionChoice(
        action,
        partialSafe,
        partialSafe
          ? ""
          : String(
            launch.blocker ||
            "canonical_partial_launch_safety_incomplete"
          )
      ));
      return;
    }
    if (action === "wait_for_full_force") {
      var decision = String(launch.decision || "").toLowerCase();
      var waitSafe = Boolean(
        canonicalIdentity &&
        operationIdSafe &&
        launch.strict_scope === true &&
        Number(launch.min_units || 0) > 0 &&
        Number(launch.missing_count || 0) > 0 &&
        ["wait", "waiting", "blocked"].indexOf(decision) >= 0
      );
      appendChoice(operationResolutionChoice(
        action,
        waitSafe,
        waitSafe ? "" : "canonical_full_force_wait_fields_incomplete"
      ));
    }
  });
  var transfer = overview.transfer_availability || {};
  var entries = Array.isArray(transfer.entries) ? transfer.entries : [];
  entries.forEach(function(entry) {
    if (
      !entry ||
      String(entry.source_owner_id || "") !== String(data.operation_id || "")
    ) {
      return;
    }
    (Array.isArray(entry.recommended_resolution_choices)
      ? entry.recommended_resolution_choices
      : []
    ).forEach(function(choice) {
      var action = String(choice || "");
      if (
        action !== "transfer_available_units" &&
        action !== "transfer_two_units"
      ) {
        return;
      }
      var safety = entry.safety_evidence || {};
      var inputs = entry.atomic_revalidation_inputs || {};
      var requiredCount = action === "transfer_two_units" ? 2 : 1;
      var transferSafe = Boolean(
        canonicalIdentity &&
        operationIdSafe &&
        String(overview.authority || "") === "micromachine_cpp" &&
        transfer.atomic_revalidation_required === true &&
        entry.transfer_safe === true &&
        Number(entry.transferable_count || 0) >= requiredCount &&
        !String(entry.atomic_runtime_blocker || "") &&
        safety.protected_minimum_respected === true &&
        safety.atomic_revalidation_required === true &&
        inputs.atomic_revalidation_ready === true &&
        inputs.source_active === true &&
        inputs.ownership_integrity === true
      );
      appendChoice(operationResolutionChoice(
        action,
        transferSafe,
        transferSafe
          ? ""
          : String(
            entry.atomic_runtime_blocker ||
            "canonical_transfer_safety_incomplete"
          )
      ));
    });
  });
  return choices;
}

function operationResolutionCommand(action, record) {
  var definition = operationResolutionDefinition(action);
  if (
    !definition ||
    !record ||
    !operationSafeCommandIdentifier(record.operationId)
  ) {
    return "";
  }
  return definition.command(record.operationId);
}

function operationNodeContains(root, candidate) {
  var current = candidate;
  while (current) {
    if (current === root) { return true; }
    current = current.parentNode;
  }
  return false;
}

function operationFocusedControlKey(root) {
  var active = document.activeElement;
  if (!active || !operationNodeContains(root, active)) { return ""; }
  return String(
    active.getAttribute("data-operation-action") ||
    active.getAttribute("data-operation-resolution") ||
    ""
  );
}

function restoreOperationFocusedControl(root, key) {
  if (!key) { return; }
  var controls = root.querySelectorAll("button");
  var fallback = null;
  for (var index = 0; index < controls.length; index += 1) {
    var candidate = controls[index];
    if (
      !fallback &&
      candidate.getAttribute("data-operation-action") === "view"
    ) {
      fallback = candidate;
    }
    if (
      candidate.getAttribute("data-operation-action") === key ||
      candidate.getAttribute("data-operation-resolution") === key
    ) {
      candidate.focus({ preventScroll: true });
      return;
    }
  }
  if (fallback) {
    fallback.focus({ preventScroll: true });
  }
}

function renderOperationResolutionActions(record, data) {
  var choices = operationResolutionChoices(data);
  if (!choices.length) { return null; }
  var container = document.createElement("div");
  container.className = "operation-resolution-actions";
  var label = document.createElement("span");
  label.textContent = commandUiText(
    "상황별 해결 선택",
    "Contextual resolution",
    "情境解决选项"
  );
  container.appendChild(label);
  var controls = document.createElement("div");
  choices.forEach(function(choice) {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = choice.label;
    button.setAttribute(
      "aria-disabled",
      choice.safe === true ? "false" : "true"
    );
    button.setAttribute("data-operation-resolution", choice.action);
    button.setAttribute(
      "aria-label",
      choice.label + ": " + (record.text || record.operationId)
    );
    if (choice.reason) {
      button.title = choice.reason;
      var reason = document.createElement("span");
      reason.id = record.domId + "-resolution-" + choice.action + "-reason";
      reason.className = "operation-resolution-reason";
      reason.textContent = choice.reason;
      button.setAttribute("aria-describedby", reason.id);
      controls.appendChild(reason);
    }
    button.addEventListener("click", function(event) {
      if (button.getAttribute("aria-disabled") === "true") {
        if (event && typeof event.preventDefault === "function") {
          event.preventDefault();
        }
        return;
      }
      var command = operationResolutionCommand(choice.action, record);
      if (command) { submitCommanderControlOrder(command); }
    });
    controls.appendChild(button);
  });
  container.appendChild(controls);
  return container;
}

function renderOperationTimeline(record) {
  var list = document.getElementById("operation-timeline");
  var selection = document.getElementById("operation-timeline-selection");
  if (!list || !selection) { return; }
  var events = record && record.data &&
    Array.isArray(record.data.semantic_timeline)
    ? record.data.semantic_timeline
    : [];
  var generation = Number(record && record.operationGeneration || 0);
  var timelineFingerprint = JSON.stringify({
    key: record && record.key || "",
    generation: generation,
    events: events
  });
  if (
    list.getAttribute("data-operation-timeline-fingerprint") ===
    timelineFingerprint
  ) {
    return;
  }
  var openEventSequences = {};
  var focusedEventSequence = "";
  var focusedTimelineNode = document.activeElement;
  list.querySelectorAll(".operation-timeline-item").forEach(function(item) {
    var sequence = String(
      item.getAttribute("data-operation-event-seq") || ""
    );
    var disclosure = item.querySelector("details");
    if (sequence && disclosure && disclosure.open) {
      openEventSequences[sequence] = true;
    }
    if (
      sequence &&
      focusedTimelineNode &&
      operationNodeContains(item, focusedTimelineNode)
    ) {
      focusedEventSequence = sequence;
    }
  });
  list.textContent = "";
  list.setAttribute(
    "data-operation-timeline-fingerprint",
    timelineFingerprint
  );
  if (!record || !record.data) {
    selection.textContent = commandUiText(
      "작전을 선택하세요",
      "Select an operation",
      "请选择作战"
    );
    return;
  }
  selection.textContent = record.operationId + "#" + generation;
  if (!events.length) {
    var empty = document.createElement("li");
    empty.className = "operation-empty";
    empty.textContent = commandUiText(
      "아직 의미 사건이 없습니다.",
      "No semantic events yet.",
      "尚无语义事件。"
    );
    list.appendChild(empty);
    return;
  }
  events.forEach(function(event) {
    var item = document.createElement("li");
    item.className = "operation-timeline-item";
    item.setAttribute("data-operation-event-kind", String(event.kind || ""));
    item.setAttribute("data-operation-event-seq", String(event.timeline_seq || 0));
    var kind = document.createElement("span");
    kind.className = "operation-timeline-kind";
    kind.textContent = String(event.kind || "event");
    var summary = document.createElement("strong");
    summary.className = "operation-timeline-summary";
    summary.textContent = String(event.summary || event.kind || "");
    var frame = document.createElement("span");
    frame.className = "operation-timeline-frame";
    frame.textContent = Number(event.game_frame || -1) >= 0
      ? "f" + Number(event.game_frame)
      : commandUiText("프레임 대기", "frame pending", "等待帧");
    item.appendChild(kind);
    item.appendChild(summary);
    item.appendChild(frame);
    var details = document.createElement("details");
    if (openEventSequences[String(event.timeline_seq || 0)]) {
      details.open = true;
    }
    var detailsSummary = document.createElement("summary");
    detailsSummary.textContent = commandUiText(
      "기술 증거",
      "Technical evidence",
      "技术证据"
    );
    var technical = document.createElement("pre");
    technical.textContent = JSON.stringify(event.technical || {}, null, 2);
    details.appendChild(detailsSummary);
    details.appendChild(technical);
    item.appendChild(details);
    list.appendChild(item);
    if (
      focusedEventSequence === String(event.timeline_seq || 0)
    ) {
      detailsSummary.focus({ preventScroll: true });
    }
  });
}

function focusOperationRecord(record) {
  if (!record || !record.data) { return; }
  selectedOperationKey = record.key;
  microMachineCommandAnnouncementSeq += 1;
  activeCommandConsoleRecord = {
    pendingId: "",
    scopeId: record.scopeId,
    sessionEpoch: operationPayloadSessionEpoch(
      record.data,
      [record.data]
    ),
    updateId: record.updateId,
    operationId: record.operationId,
    operationGeneration: record.operationGeneration,
    text: record.text,
    state: "interpreting",
    data: record.data,
    startedAt: record.createdAt,
    stageRank: record.stageRank,
    telemetryFrame: record.telemetryFrame,
    observationTimedOut: false,
    submissionDelayed: false,
    announcementOrdinal: microMachineCommandAnnouncementSeq
  };
  renderActiveCommandConsole(record.data, true);
  renderOperationTimeline(record);
}

function renderOperationCard(record) {
  var data = record.data || {
    status: "queued",
    command_text: record.text,
    consumption_status: "pending_compile",
    operation_id: record.operationId
  };
  var model = commandConsoleStageModel(data);
  var canonicalCompletionVerified = Boolean(
    model.canonicalCompletionVerified
  );
  var card = record.node || document.createElement("article");
  record.node = card;
  var cardFingerprint = JSON.stringify({
    key: record.key,
    generation: record.operationGeneration,
    requestedGeneration: record.requestedOperationGeneration,
    frame: record.telemetryFrame,
    terminal: record.terminal,
    disposition: record.disposition,
    data: data
  });
  if (
    card.getAttribute("data-operation-card-fingerprint") ===
    cardFingerprint
  ) {
    return card;
  }
  var focusedControlKey = operationFocusedControlKey(card);
  card.textContent = "";
  card.id = record.domId;
  card.className = commandConsoleClassName(model).replace(
    "active-command-console",
    "operation-card"
  );
  card.setAttribute("role", "listitem");
  card.setAttribute("data-operation-key", record.key);
  card.setAttribute("data-operation-id", record.operationId);
  card.setAttribute("data-operation-card-fingerprint", cardFingerprint);
  card.setAttribute("aria-labelledby", record.domId + "-title");

  var header = document.createElement("div");
  header.className = "operation-card-header";
  var titleGroup = document.createElement("div");
  var kicker = document.createElement("span");
  kicker.className = "operation-card-kicker";
  kicker.textContent = String(data.operation_mission || "operation") +
    " · " + record.operationId + "#" + Number(record.operationGeneration || 0);
  var title = document.createElement("h3");
  title.id = record.domId + "-title";
  title.className = "operation-card-title";
  title.textContent = record.text || commandConsoleGoal(data, record.operationId);
  titleGroup.appendChild(kicker);
  titleGroup.appendChild(title);
  var state = document.createElement("span");
  state.id = record.domId + "-status";
  state.className = "operation-card-state";
  state.setAttribute("role", "status");
  state.setAttribute("aria-live", "polite");
  state.setAttribute("aria-atomic", "true");
  state.textContent = canonicalCompletionVerified
    ? commandUiText("실행 확인", "Execution verified", "执行已确认")
    : (
      model.effectObserved
        ? commandUiText("효과 관측", "Effect observed", "已观测效果")
        : commandConsoleStateLabel(model)
    );
  header.appendChild(titleGroup);
  header.appendChild(state);
  card.appendChild(header);

  var stageLine = document.createElement("div");
  stageLine.className = "operation-stage-line";
  stageLine.setAttribute("role", "list");
  [
    ["interpret", commandUiText("해석", "Interpret", "解析")],
    ["assign", commandUiText("배정", "Assign", "分配")],
    ["execute", commandUiText("제출", "Submit", "提交")],
    ["verify", commandUiText("관측", "Observe", "观察")]
  ].forEach(function(stageDefinition) {
    var stageName = stageDefinition[0];
    var stage = document.createElement("span");
    var stageState = canonicalCompletionVerified
      ? "stage-done"
      : commandConsoleStageState(stageName, model);
    stage.className = "operation-stage " + stageState;
    stage.setAttribute("role", "listitem");
    stage.setAttribute(
      "aria-current",
      stageState === "stage-current" ? "step" : "false"
    );
    stage.textContent = stageDefinition[1];
    stageLine.appendChild(stage);
  });
  card.appendChild(stageLine);

  var details = document.createElement("div");
  details.className = "operation-card-details";
  operationAppendDetail(
    details,
    commandUiText("배정 전력", "Assigned force", "已分配兵力"),
    commandConsoleAssignedForce(model)
  );
  operationAppendDetail(
    details,
    commandUiText("작전/태스크", "Mission/task", "作战/任务"),
    String(data.operation_mission || "operation")
  );
  if (data.squad_order) {
    operationAppendDetail(
      details,
      commandUiText("Squad 오더", "Squad order", "编队命令"),
      String(data.squad_order)
    );
  }
  operationAppendDetail(
    details,
    commandUiText("경로/의미 목표", "Route/semantic target", "路线/语义目标"),
    operationRouteSummary(data)
  );
  operationAppendDetail(
    details,
    commandUiText("수명/종료 조건", "Lifetime/completion", "生命周期/完成条件"),
    operationLifetimeSummary(data),
    "operation-card-verification"
  );
  operationAppendDetail(
    details,
    commandUiText("요구/반영/실소유", "Requested/represented/owned", "请求/反映/所有"),
    operationForceSummary(data),
    "operation-card-verification"
  );
  operationAppendDetail(
    details,
    commandUiText("출동 결정", "Launch decision", "出动决定"),
    operationLaunchSummary(data),
    "operation-card-verification"
  );
  if (operationConvergenceSummary(data)) {
    operationAppendDetail(
      details,
      commandUiText("병력 수렴", "Force convergence", "兵力收敛"),
      operationConvergenceSummary(data),
      "operation-card-verification"
    );
  }
  if (operationFamilyEvidenceSummary(data)) {
    operationAppendDetail(
      details,
      commandUiText("유닛 실행", "Unit execution", "单位执行"),
      operationFamilyEvidenceSummary(data),
      "operation-card-verification"
    );
  }
  if (operationEditSummary(data)) {
    operationAppendDetail(
      details,
      commandUiText("작전 변경", "Operation edit", "作战变更"),
      operationEditSummary(data)
    );
    operationAppendDetail(
      details,
      commandUiText("병력 변화", "Force change", "兵力变化"),
      operationEditForceChange(data)
    );
    operationAppendDetail(
      details,
      commandUiText("충돌 해결", "Conflict resolution", "冲突处理"),
      operationEditResolution(data),
      "operation-card-verification"
    );
  }
  operationAppendDetail(
    details,
    commandUiText("실제 SC2 명령", "Actual SC2 command", "实际 SC2 命令"),
    commandConsoleActualAction(model)
  );
  operationAppendDetail(
    details,
    commandUiText("목표", "Target", "目标"),
    commandConsoleTarget(data, model)
  );
  operationAppendDetail(
    details,
    commandUiText("실행 증거", "Execution evidence", "执行证据"),
    commandConsoleVerification(data, model),
    "operation-card-verification"
  );
  operationAppendDetail(
    details,
    commandUiText("권위 관측", "Canonical observation", "权威观测"),
    operationCompletionSummary(data),
    "operation-card-verification"
  );
  operationAppendDetail(
    details,
    commandUiText("첫 차단/해결 조건", "First blocker/resolution", "首个阻塞/解决条件"),
    operationBlockerSummary(data),
    "operation-card-verification"
  );
  card.appendChild(details);

  var actions = document.createElement("div");
  actions.className = "operation-card-actions";
  actions.appendChild(
    operationActionButton(
      record,
      commandUiText("대표 보기", "View", "查看"),
      "view",
      function() { focusOperationRecord(record); }
    )
  );
  actions.appendChild(
    operationActionButton(
      record,
      commandUiText("수정", "Revise", "修改"),
      "revise",
      function() { prefillOperationEdit(record, "revise"); }
    )
  );
  actions.appendChild(
    operationActionButton(
      record,
      commandUiText("증원", "Reinforce", "增援"),
      "reinforce",
      function() { prefillOperationEdit(record, "reinforce"); }
    )
  );
  actions.appendChild(
    operationActionButton(
      record,
      commandUiText("목표 변경", "Retarget", "变更目标"),
      "retarget",
      function() { prefillOperationEdit(record, "retarget"); }
    )
  );
  actions.appendChild(
    operationActionButton(
      record,
      commandUiText("작전 취소", "Cancel operation", "取消作战"),
      "cancel",
      function() {
        if (record.terminal) { return; }
        submitCommanderControlOrder(
          currentLang === "en"
            ? "Cancel operation " + record.operationId
            : (currentLang === "zh"
              ? "取消作战 " + record.operationId
              : "작전 " + record.operationId + " 취소해")
        );
      }
    )
  );
  card.appendChild(actions);
  var resolutionActions = renderOperationResolutionActions(record, data);
  if (resolutionActions) {
    card.appendChild(resolutionActions);
  }
  restoreOperationFocusedControl(card, focusedControlKey);
  return card;
}

function operationLaneDefinitions() {
  return [
    ["planning", commandUiText("해석/편성", "Planning", "解析/编组")],
    ["executing", commandUiText("실행 중", "Executing", "执行中")],
    ["completed", commandUiText("관측 완료", "Completed", "观测完成")],
    ["waiting", commandUiText("대기/차단", "Waiting/blocked", "等待/阻塞")]
  ];
}

function ensureOperationLaneContainers() {
  var root = document.getElementById("operation-list");
  var lanes = {};
  if (!root) { return lanes; }
  operationLaneDefinitions().forEach(function(definition) {
    var name = definition[0];
    var laneList = document.getElementById("operation-lane-" + name);
    if (!laneList) {
      var lane = document.createElement("section");
      lane.className = "operation-lane";
      lane.setAttribute("data-operation-lane", name);
      var header = document.createElement("div");
      header.className = "operation-lane-header";
      var title = document.createElement("h3");
      title.className = "operation-lane-title";
      title.textContent = definition[1];
      var count = document.createElement("span");
      count.id = "operation-lane-" + name + "-count";
      count.className = "operation-lane-count";
      count.textContent = "0";
      header.appendChild(title);
      header.appendChild(count);
      laneList = document.createElement("div");
      laneList.id = "operation-lane-" + name;
      laneList.className = "operation-lane-list";
      laneList.setAttribute("role", "list");
      lane.appendChild(header);
      lane.appendChild(laneList);
      root.appendChild(lane);
    }
    lanes[name] = laneList;
  });
  return lanes;
}

function operationRecordLane(record) {
  var data = record && record.data || {};
  var model = commandConsoleStageModel(data);
  var projection = operationProjection(data);
  var launch = projection.operation_launch_policy || {};
  var convergence = data.operation_convergence || {};
  var intervention = data.intervention || {};
  var execution = intervention.command_execution || {};
  var executionState = String(execution.state || "").toLowerCase();
  var disposition = String(record && record.disposition || "").toLowerCase();
  if (
    disposition === "completed" &&
    model.canonicalCompletionVerified
  ) {
    return "completed";
  }
  if (
    model.cancelled && !model.cancellationCleanupVerified
  ) {
    return "waiting";
  }
  if (
    disposition === "blocked" ||
    disposition === "expired" ||
    disposition === "superseded" ||
    record && record.terminal ||
    ["wait", "waiting", "blocked"].indexOf(
      String(launch.decision || "").toLowerCase()
    ) >= 0 ||
    Boolean(launch.blocker || convergence.blocker) ||
    executionState.indexOf("waiting") === 0
  ) {
    return "waiting";
  }
  if (model.actionIssued) {
    return "executing";
  }
  return "planning";
}

function pruneOperationRecords() {
  while (operationRecordOrder.length > OPERATION_RECORD_MAXIMUM) {
    var key = operationRecordOrder.find(function(candidateKey) {
      return operationRecords[candidateKey] &&
        operationRecords[candidateKey].terminal;
    }) || operationRecordOrder.find(function(candidateKey) {
      var candidate = operationRecords[candidateKey];
      return candidate && !(
        candidate.pendingId &&
        !candidate.updateId &&
        Date.now() - Number(candidate.createdAt || 0) <
          OPERATION_PENDING_RECORD_TIMEOUT_MS
      );
    }) || operationRecordOrder[0];
    removeOperationRecord(key);
  }
}

function removeOperationRecord(key) {
  var record = operationRecords[key];
  if (record && record.node && record.node.parentNode) {
    record.node.parentNode.removeChild(record.node);
  }
  delete operationRecords[key];
  var index = operationRecordOrder.indexOf(key);
  if (index >= 0) { operationRecordOrder.splice(index, 1); }
  if (selectedOperationKey === key) { selectedOperationKey = ""; }
}

function reconcileAuthoritativeOperationMembership(authoritativeKeys) {
  var now = Date.now();
  operationRecordOrder.slice().forEach(function(key) {
    var record = operationRecords[key];
    if (!record || authoritativeKeys[key]) { return; }
    var preserveUnacknowledgedPending = Boolean(
      record.pendingId &&
      !record.updateId &&
      Number(record.operationGeneration || 0) <= 0 &&
      now - Number(record.createdAt || 0) <
        OPERATION_PENDING_RECORD_TIMEOUT_MS
    );
    if (!preserveUnacknowledgedPending) {
      removeOperationRecord(key);
    }
  });
}

function renderOperationRecords() {
  var list = document.getElementById("operation-list");
  if (!list) { return; }
  pruneOperationRecords();
  var lanes = ensureOperationLaneContainers();
  var oldEmpty = document.getElementById("operation-list-empty");
  if (oldEmpty && oldEmpty.parentNode) {
    oldEmpty.parentNode.removeChild(oldEmpty);
  }
  var visibleRecords = operationRecordOrder.map(function(key) {
    return operationRecords[key];
  }).filter(function(record) {
    return Boolean(record);
  });
  if (!visibleRecords.length) {
    var empty = document.createElement("p");
    empty.id = "operation-list-empty";
    empty.className = "operation-empty";
    empty.textContent = commandUiText(
      "아직 추적 중인 작전이 없습니다.",
      "No operations are being tracked yet.",
      "尚无正在跟踪的作战。"
    );
    list.appendChild(empty);
  } else {
    visibleRecords.forEach(function(record) {
      var laneName = operationRecordLane(record);
      var lane = lanes[laneName] || lanes.planning;
      var card = renderOperationCard(record);
      var focusedControlKey = operationFocusedControlKey(card);
      if (card.parentNode && card.parentNode !== lane) {
        card.parentNode.removeChild(card);
      }
      if (card.parentNode !== lane) {
        lane.appendChild(card);
      }
      restoreOperationFocusedControl(card, focusedControlKey);
    });
  }
  list.querySelectorAll(".operation-card").forEach(function(card) {
    var key = String(card.getAttribute("data-operation-key") || "");
    if (!operationRecords[key] && card.parentNode) {
      card.parentNode.removeChild(card);
    }
  });
  Object.keys(lanes).forEach(function(name) {
    setMicroMachineText(
      "operation-lane-" + name + "-count",
      String(lanes[name].children.length)
    );
  });
  if (
    !selectedOperationKey ||
    !operationRecords[selectedOperationKey]
  ) {
    selectedOperationKey = visibleRecords.length
      ? visibleRecords[0].key
      : "";
  }
  renderOperationTimeline(
    selectedOperationKey ? operationRecords[selectedOperationKey] : null
  );
  var activeCount = visibleRecords.filter(function(record) {
    return !record.terminal;
  }).length;
  var blockedCount = visibleRecords.filter(function(record) {
    return record.disposition === "blocked" ||
      record.disposition === "expired" ||
      record.disposition === "superseded";
  }).length;
  setMicroMachineText(
    "operation-summary",
    commandUiText(
      "활성 " + activeCount + " · 종료/차단 " + blockedCount,
      activeCount + " active · " + blockedCount + " terminal/blocked",
      "活跃 " + activeCount + " · 结束/受阻 " + blockedCount
    )
  );
}

function boundedAuthoritativeOperationPayloads(operations) {
  var preservedPendingCount = operationRecordOrder.filter(function(key) {
    var record = operationRecords[key];
    return Boolean(
      record &&
      record.pendingId &&
      !record.updateId &&
      Number(record.operationGeneration || 0) <= 0 &&
      Date.now() - Number(record.createdAt || 0) <
        OPERATION_PENDING_RECORD_TIMEOUT_MS
    );
  }).length;
  var limit = Math.max(
    0,
    OPERATION_RECORD_MAXIMUM - preservedPendingCount
  );
  if (limit <= 0) { return []; }
  if (operations.length <= limit) { return operations; }
  return operations.slice().sort(function(left, right) {
    var leftFrame = commandConsoleTelemetryFrame(
      commandOperationData(left, {})
    );
    var rightFrame = commandConsoleTelemetryFrame(
      commandOperationData(right, {})
    );
    if (leftFrame !== rightFrame) { return leftFrame - rightFrame; }
    var leftId = String(left && left.operation_id || "");
    var rightId = String(right && right.operation_id || "");
    return leftId < rightId ? -1 : (leftId > rightId ? 1 : 0);
  }).slice(-limit);
}

function renderOperationConsole(data) {
  if (!data || typeof data !== "object") { return false; }
  var operations = commandOperationPayloads(data);
  var scopeId = microMachineScopeId(data);
  if (!scopeId && operations.length) {
    scopeId = operationPayloadScopeId(operations[0], data);
  }
  var sessionEpoch = operationPayloadSessionEpoch(data, operations);
  var epochAuthoritative =
    data.operation_registry_authoritative !== false;
  if (sessionEpoch && !epochAuthoritative) {
    return false;
  }
  if (
    operationConsoleScopeId &&
    scopeId &&
    operationConsoleScopeId !== scopeId
  ) {
    return false;
  }
  if (
    operationConsoleSessionEpoch &&
    sessionEpoch &&
    operationConsoleSessionEpoch !== sessionEpoch
  ) {
    if (
      operationSessionEpochIsStale(
        operationConsoleSessionEpoch,
        sessionEpoch
      )
    ) {
      return false;
    }
    rememberRetiredOperationSessionEpoch(
      operationConsoleSessionEpoch
    );
    resetActiveCommandConsoleState(sessionEpoch);
    resetOperationConsoleRegistry(true);
  }
  if (!operationConsoleScopeId && scopeId) {
    operationConsoleScopeId = scopeId;
  }
  if (!operationConsoleSessionEpoch && sessionEpoch) {
    operationConsoleSessionEpoch = sessionEpoch;
  }
  if (data.operation_registry_authoritative === true) {
    operations = boundedAuthoritativeOperationPayloads(operations);
  }
  var authoritativeKeys = {};
  operations.forEach(function(operation) {
    var record = reconcileOperationRecord(operation, data);
    if (record) { authoritativeKeys[record.key] = true; }
  });
  if (data.operation_registry_authoritative === true) {
    reconcileAuthoritativeOperationMembership(authoritativeKeys);
  }
  renderOperationRecords();
  return Boolean(operations.length);
}

function renderOperationFailure(text, error, pendingId) {
  var record = null;
  Object.keys(operationRecords).some(function(key) {
    var candidate = operationRecords[key];
    if (candidate && candidate.pendingId === pendingId) {
      record = candidate;
      return true;
    }
    return false;
  });
  var operationId = record ? record.operationId : String(pendingId || "");
  reconcileOperationRecord(
    {
      operation_id: operationId,
      update_id: record ? record.updateId : "",
      command_text: String(text || record && record.text || ""),
      transport_status: "publish_failed",
      compile_result: {
        status: "refused",
        refusal_reason: error && error.message ? error.message : String(error || "")
      },
      intervention: {
        command_execution: {
          command_id: record ? record.updateId : "",
          operation_id: operationId,
          state: "failed",
          failed: true,
          completed: false,
          expired: false,
          blocker_manager: "CommandGateway",
          blocker_reason: error && error.message ? error.message : String(error || ""),
          stages: []
        }
      },
      disposition: "blocked"
    },
    { blackboard_scope_id: record ? record.scopeId : "" }
  );
  renderOperationRecords();
}

function renderActiveCommandConsole(data, force) {
  var consoleNode = document.getElementById("active-command-console");
  if (!consoleNode || !data || typeof data !== "object") { return; }
  var operationPayloads = commandOperationPayloads(data);
  var sessionEpoch = operationPayloadSessionEpoch(
    data,
    operationPayloads
  );
  var epochAuthoritative =
    data.operation_registry_authoritative !== false;
  if (sessionEpoch && !epochAuthoritative) {
    return;
  }
  if (
    activeCommandConsoleRecord.sessionEpoch &&
    sessionEpoch &&
    activeCommandConsoleRecord.sessionEpoch !== sessionEpoch
  ) {
    if (
      operationSessionEpochIsStale(
        activeCommandConsoleRecord.sessionEpoch,
        sessionEpoch
      )
    ) {
      return;
    }
    resetActiveCommandConsoleState(sessionEpoch);
  } else if (!activeCommandConsoleRecord.sessionEpoch && sessionEpoch) {
    activeCommandConsoleRecord.sessionEpoch = sessionEpoch;
  }
  if (
    !activeCommandConsoleRecord.updateId &&
    !activeCommandConsoleRecord.pendingId &&
    !activeCommandConsoleRecord.text &&
    operationPayloads.length
  ) {
    var candidateOperation = operationPayloads[0];
    var candidateData = commandOperationData(candidateOperation, data);
    handoffActiveCommandConsole(
      candidateData,
      operationPayloadScopeId(candidateOperation, data),
      operationPayloadUpdateId(candidateOperation)
    );
    data = candidateData;
    force = true;
  }
  if (!force && !shouldRenderActiveCommandConsoleData(data)) { return; }
  var updateIds = commandConsoleDataUpdateIds(data);
  if (updateIds.length && !activeCommandConsoleRecord.updateId) {
    activeCommandConsoleRecord.updateId = updateIds[0];
  }
  if (!activeCommandConsoleRecord.scopeId) {
    activeCommandConsoleRecord.scopeId = microMachineScopeId(data);
  }
  var scopedData = commandConsoleDataForUpdate(
    data,
    activeCommandConsoleRecord.updateId
  );
  scopedData = commandConsoleDataForCanonicalOperation(
    scopedData,
    activeCommandConsoleRecord.updateId,
    activeCommandConsoleRecord.operationId,
    activeCommandConsoleRecord.operationGeneration
  );
  if (
    !activeCommandConsoleRecord.operationId &&
    scopedData.operation_id &&
    Number(scopedData.operation_generation || 0) > 0
  ) {
    activeCommandConsoleRecord.operationId = String(
      scopedData.operation_id
    );
    activeCommandConsoleRecord.operationGeneration = Number(
      scopedData.operation_generation
    );
  }
  if (activeCommandConsoleRecord.observationTimedOut) {
    scopedData = Object.assign({}, scopedData, {
      command_console_observation_delayed: true
    });
  }
  if (activeCommandConsoleRecord.submissionDelayed) {
    scopedData = Object.assign({}, scopedData, {
      command_console_submission_delayed: true
    });
  }
  var model = commandConsoleStageModel(scopedData);
  var telemetryFrame = commandConsoleTelemetryFrame(scopedData);
  if (!force && !shouldAdvanceActiveCommandConsole(model, telemetryFrame)) {
    return;
  }
  if (
    model.canonicalCompletionVerified ||
    model.effectObserved ||
    model.blocked ||
    model.superseded ||
    model.cancelled
  ) {
    activeCommandConsoleRecord.observationTimedOut = false;
    activeCommandConsoleRecord.submissionDelayed = false;
    if (scopedData.command_console_observation_delayed) {
      scopedData = Object.assign({}, scopedData);
      delete scopedData.command_console_observation_delayed;
      model = commandConsoleStageModel(scopedData);
    }
  }
  activeCommandConsoleRecord.data = scopedData;
  activeCommandConsoleRecord.stageRank = Math.max(
    activeCommandConsoleRecord.stageRank,
    commandConsoleStageRank(model)
  );
  activeCommandConsoleRecord.telemetryFrame = Math.max(
    activeCommandConsoleRecord.telemetryFrame,
    telemetryFrame
  );
  activeCommandConsoleRecord.state = commandConsoleStateLabel(model);
  consoleNode.className = commandConsoleClassName(model);
  var title = activeCommandConsoleRecord.text || String(scopedData.command_text || "") ||
    commandConsoleGoal(scopedData, activeCommandConsoleRecord.text);
  setMicroMachineText("command-console-title", title || t("commandConsoleIdleTitle"));
  setMicroMachineText("command-console-state", activeCommandConsoleRecord.state);
  setMicroMachineText(
    "command-console-intent",
    commandConsoleGoal(scopedData, activeCommandConsoleRecord.text) ||
      commandUiText("명령 해석 중", "Interpreting order", "正在解析命令")
  );
  setMicroMachineText("command-console-units", commandConsoleAssignedForce(model));
  setMicroMachineText("command-console-action", commandConsoleActualAction(model));
  setMicroMachineText("command-console-target", commandConsoleTarget(scopedData, model));
  var verificationText = commandConsoleVerification(scopedData, model);
  setMicroMachineText("command-console-verification", verificationText);
  ["interpret", "assign", "execute", "verify"].forEach(function(stageName) {
    var stageNode = document.getElementById("command-stage-" + stageName);
    if (stageNode) {
      var stageState = commandConsoleStageState(stageName, model);
      var stageLabel = stageState === "stage-done" || stageState === "stage-verified"
        ? commandUiText("완료", "done", "已完成")
        : (stageState === "stage-current"
          ? commandUiText("진행 중", "in progress", "进行中")
          : (stageState === "stage-blocked"
            ? commandUiText("차단", "blocked", "受阻")
            : commandUiText("대기", "waiting", "等待")));
      stageNode.className = "command-stage " + stageState;
      stageNode.setAttribute("role", "listitem");
      stageNode.setAttribute("aria-current", stageState === "stage-current" ? "step" : "false");
      stageNode.setAttribute("aria-label", stageNode.textContent + ": " + stageLabel);
    }
  });
  var technical = document.getElementById("command-console-technical");
  if (technical) {
    technical.textContent = JSON.stringify({
      update_id: activeCommandConsoleRecord.updateId,
      state: model.execution.state || data.status || "",
      blocker_manager: model.execution.blocker_manager || "",
      blocker_reason: model.execution.blocker_reason || "",
      stages: model.execution.stages || [],
      command_queue: microMachineCommandQueue(scopedData),
      observation_delayed: model.observationDelayed,
      submission_delayed: model.submissionDelayed
    }, null, 2);
  }
  var announcement = document.getElementById("command-console-announcement");
  if (announcement) {
    var announcementText = commandUiText("명령 ", "Order ", "命令 ") +
      String(activeCommandConsoleRecord.announcementOrdinal || 0) +
      ": " + (title || commandConsoleGoal(scopedData, "")) +
      ". " + activeCommandConsoleRecord.state + ". " + verificationText;
    if (announcement.textContent !== announcementText) {
      announcement.textContent = announcementText;
    }
  }
  renderBattlefieldControlOverview(scopedData, model);
}

function renderActiveCommandFailure(text, error, pendingId) {
  if (
    activeCommandConsoleRecord.pendingId &&
    pendingId &&
    activeCommandConsoleRecord.pendingId !== pendingId
  ) {
    return;
  }
  activeCommandConsoleRecord.text = String(text || activeCommandConsoleRecord.text || "");
  var message = error && error.message ? error.message : String(error || "");
  renderOperationFailure(activeCommandConsoleRecord.text, error, pendingId);
  renderActiveCommandConsole({
    command_text: activeCommandConsoleRecord.text,
    accepted: false,
    status: "publish_failed",
    compile_result: { refusal_reason: message },
    intervention: {
      command_execution: {
        command_id: activeCommandConsoleRecord.updateId,
        state: "failed",
        failed: true,
        completed: false,
        expired: false,
        blocker_manager: "CommandGateway",
        blocker_reason: message,
        stages: []
      }
    }
  }, true);
}

function renderBattlefieldControlOverview(data, model) {
  var overview = data && data.battlefield_overview;
  overview = overview && typeof overview === "object" ? overview : null;
  var identity = overview && overview.identity || {};
  var frame = overview ? identity.game_frame : null;
  var badge = document.getElementById("battlefield-link-badge");
  if (badge) {
    var linked = Boolean(
      overview &&
      String(overview.authority || "") &&
      frame !== null &&
      frame !== undefined &&
      frame !== ""
    );
    badge.className = "battlefield-link-badge" + (linked ? " control-linked" : "");
    badge.textContent = linked ? t("battlefieldLinkConnected") : t("battlefieldLinkWaiting");
  }
  if (!overview) {
    [
      "battlefield-command-state",
      "battlefield-frame",
      "battlefield-force",
      "battlefield-posture",
      "battlefield-unassigned",
      "battlefield-readiness",
      "battlefield-transfer",
      "battlefield-integrity",
      "battlefield-production-waits"
    ].forEach(function(id) { setMicroMachineText(id, "-"); });
    setMicroMachineText(
      "battlefield-control-summary",
      commandUiText(
        "권위 battlefield_overview를 기다리는 중입니다.",
        "Waiting for the authoritative battlefield_overview.",
        "正在等待权威 battlefield_overview。"
      )
    );
    return;
  }
  var explicit = Number(overview.explicit_operation_owned_count || 0);
  var autonomous = Number(overview.autonomous_owned_count || 0);
  var unassigned = Number(overview.unassigned_count || 0);
  var duplicate = Number(overview.duplicate_owner_count || 0);
  setMicroMachineText(
    "battlefield-command-state",
    String(overview.authority || "-") + " · schema " +
      String(overview.schema_version || "-")
  );
  setMicroMachineText("battlefield-frame", frame);
  setMicroMachineText(
    "battlefield-force",
    String(Number(overview.eligible_combat_count || 0))
  );
  setMicroMachineText(
    "battlefield-posture",
    commandUiText("명시 ", "explicit ", "显式 ") + explicit +
      commandUiText(" · 자율 ", " · autonomous ", " · 自主 ") + autonomous
  );
  setMicroMachineText("battlefield-unassigned", String(unassigned));

  var bases = Array.isArray(overview.bases) ? overview.bases : [];
  var readinessParts = bases.map(function(base) {
    var readiness = base && base.base_readiness || {};
    var protectedMinimum = Array.isArray(readiness.protected_minimum)
      ? readiness.protected_minimum.map(function(item) {
        return String(item.family || item.role || "unit") + " " +
          Number(item.count || 0);
      }).join(", ")
      : "";
    return String(base.semantic_anchor || base.base_id || "base") + ": " +
      String(readiness.readiness_state || "unknown") +
      (readiness.reason ? " · " + readiness.reason : "") +
      (protectedMinimum
        ? commandUiText(" · 보호 ", " · protected ", " · 保护 ") + protectedMinimum
        : "");
  });
  setMicroMachineText(
    "battlefield-readiness",
    readinessParts.length ? readinessParts.join(" | ") : "-"
  );

  var transfer = overview.transfer_availability || {};
  var transferEntries = Array.isArray(transfer.entries) ? transfer.entries : [];
  var transferParts = transferEntries.map(function(entry) {
    return String(entry.source_owner_id || "owner") + ": " +
      Number(entry.transferable_count || 0) + "/" +
      Number(entry.source_owner_count || 0) +
      (entry.transfer_safe === true
        ? commandUiText(" 안전", " safe", " 安全")
        : " · " + String(entry.atomic_runtime_blocker || "unsafe"));
  });
  setMicroMachineText(
    "battlefield-transfer",
    transferParts.length ? transferParts.join(" | ") : "-"
  );
  setMicroMachineText(
    "battlefield-integrity",
    duplicate === 0
      ? commandUiText("정상 · 중복 0", "valid · duplicates 0", "有效 · 重复 0")
      : commandUiText("경고 · 중복 ", "alert · duplicates ", "警告 · 重复 ") + duplicate
  );

  var operations = Array.isArray(overview.operation_ownership)
    ? overview.operation_ownership
    : [];
  var waits = operations.map(function(operation) {
    var launch = operation && operation.operation_launch_policy || {};
    var decision = String(launch.decision || "").toLowerCase();
    var blocker = String(launch.blocker || "");
    if (
      ["wait", "waiting", "blocked"].indexOf(decision) < 0 &&
      !blocker
    ) {
      return "";
    }
    return String(operation.operation_id || "operation") + ": " +
      (blocker || decision) + " · " +
      Number(launch.launch_count || 0) + "/" +
      Number(launch.min_units || 0);
  }).filter(Boolean);
  setMicroMachineText(
    "battlefield-production-waits",
    waits.length
      ? waits.join(" | ")
      : commandUiText("대기 없음", "no canonical waits", "无权威等待")
  );
  setMicroMachineText(
    "battlefield-control-summary",
    commandUiText("전투 가능 ", "Eligible ", "可战斗 ") +
      Number(overview.eligible_combat_count || 0) +
      commandUiText(" · 명시 소유 ", " · explicit ", " · 显式所有 ") + explicit +
      commandUiText(" · 자율 소유 ", " · autonomous ", " · 自主所有 ") + autonomous +
      commandUiText(" · 미배정 ", " · unassigned ", " · 未分配 ") + unassigned
  );
}

function summarizeMicroMachineManagers(managers) {
  if (!managers || typeof managers !== "object") { return "-"; }
  var parts = [];
  Object.keys(managers).forEach(function (manager) {
    var payload = managers[manager] || {};
    if (manager === "WorkerManager" && payload.repeat_order_guard_active === true) {
      parts.push(
        manager + ": repeat blocked " + (payload.repeat_order_suppressed_count || 0) +
        ", self-position " + (payload.self_position_command_block_count || 0) +
        " (" + (payload.root_cause_status || "none") + ")"
      );
    } else if (manager === "ProductionManager" && payload.last_doctrine_action) {
      parts.push(
        manager + ": " + (payload.strategy_doctrine || payload.last_doctrine || "unknown") +
        " action=" + payload.last_doctrine_action +
        " item=" + (payload.last_doctrine_queue_item || "none") +
        " evidence=" + (payload.last_doctrine_evidence || "missing") +
        " actual=" + (payload.last_actual_production_command || "none") +
        " count=" + (payload.actual_production_command_issued_count || 0)
      );
    } else if (payload.policy_active === true) {
      parts.push(manager + ": policy_active");
    } else if (payload.active === true) {
      parts.push(manager + ": active");
    }
  });
  return parts.length ? parts.join(" | ") : "-";
}

function formatMicroMachineScope(scope) {
  if (!scope || typeof scope !== "object") { return "-"; }
  var requested = scope.requested || {};
  var telemetry = scope.telemetry || {};
  var parts = [];
  Object.keys(requested).forEach(function (key) {
    var value = requested[key];
    if (Array.isArray(value)) { value = value.join(", "); }
    parts.push("requested." + key + "=" + value);
  });
  Object.keys(telemetry).forEach(function (key) {
    parts.push("telemetry." + key + "=" + telemetry[key]);
  });
  return parts.length ? parts.join(" | ") : "-";
}

function formatMicroMachineLifetime(lifetime) {
  if (!lifetime || typeof lifetime !== "object") { return "-"; }
  var parts = [];
  if (lifetime.mode) { parts.push("mode=" + lifetime.mode); }
  if (lifetime.completion_state) {
    parts.push("state=" + lifetime.completion_state);
  }
  if (Array.isArray(lifetime.completion_conditions) && lifetime.completion_conditions.length) {
    parts.push("conditions=" + lifetime.completion_conditions.join(", "));
  }
  if (lifetime.reason) { parts.push("reason=" + lifetime.reason); }
  var telemetry = lifetime.telemetry || {};
  Object.keys(telemetry).forEach(function (key) {
    parts.push("telemetry." + key + "=" + telemetry[key]);
  });
  return parts.length ? parts.join(" | ") : "-";
}

function formatMicroMachineAxesByManager(axesByManager) {
  if (!axesByManager || typeof axesByManager !== "object") { return "-"; }
  var parts = [];
  Object.keys(axesByManager).forEach(function (manager) {
    var axes = axesByManager[manager];
    if (Array.isArray(axes) && axes.length) {
      parts.push(manager + ": " + axes.join(", "));
    }
  });
  return parts.length ? parts.join(" | ") : "-";
}

function formatMicroMachineTargetPriority(priority) {
  if (!priority || typeof priority !== "object") { return "-"; }
  var parts = [];
  if (priority.selected_target_class) {
    parts.push("selected=" + priority.selected_target_class);
  }
  ["requested_biases", "telemetry_biases"].forEach(function (key) {
    var payload = priority[key];
    if (!payload || typeof payload !== "object") { return; }
    var items = Object.keys(payload).map(function (name) {
      return name + "=" + payload[name];
    });
    if (items.length) { parts.push(key + ": " + items.join(", ")); }
  });
  return parts.length ? parts.join(" | ") : "-";
}

function formatMicroMachineAttackGate(gate) {
  if (!gate || typeof gate !== "object") { return "-"; }
  var parts = [];
  if (gate.status) { parts.push("status=" + gate.status); }
  if (gate.reason) { parts.push("reason=" + gate.reason); }
  if (gate.unit_count !== null && gate.unit_count !== undefined) {
    var unitText = "units=" + gate.unit_count;
    if (gate.min_units !== null && gate.min_units !== undefined) {
      unitText += "/" + gate.min_units;
    }
    parts.push(unitText);
  }
  if (gate.scope_threshold_met !== null && gate.scope_threshold_met !== undefined) {
    parts.push("threshold_met=" + gate.scope_threshold_met);
  }
  if (gate.simulation_won !== null && gate.simulation_won !== undefined) {
    parts.push("simulation_won=" + gate.simulation_won);
  }
  if (gate.order_x !== null && gate.order_x !== undefined && gate.order_y !== null && gate.order_y !== undefined) {
    parts.push("order=(" + gate.order_x + ", " + gate.order_y + ")");
  }
  return parts.length ? parts.join(" | ") : "-";
}

function formatMicroMachineTacticalEvidence(evidence) {
  if (!evidence || typeof evidence !== "object") { return "-"; }
  var parts = [];
  if (evidence.status) { parts.push("status=" + evidence.status); }
  if (Array.isArray(evidence.observed_effects) && evidence.observed_effects.length) {
    parts.push("observed=" + evidence.observed_effects.join(", "));
  }
  if (Array.isArray(evidence.expected_effects) && evidence.expected_effects.length) {
    parts.push("expected=" + evidence.expected_effects.join(", "));
  }
  if (Array.isArray(evidence.missing_effects) && evidence.missing_effects.length) {
    parts.push("missing=" + evidence.missing_effects.join(", "));
  }
  if (Array.isArray(evidence.unsupported_effects) && evidence.unsupported_effects.length) {
    parts.push("unsupported=" + evidence.unsupported_effects.join(", "));
  }
  if (Array.isArray(evidence.refusal_reasons) && evidence.refusal_reasons.length) {
    parts.push("refused=" + evidence.refusal_reasons[0]);
  }
  return parts.length ? parts.join(" | ") : "-";
}

function formatMicroMachineCommandExecution(execution) {
  if (!execution || typeof execution !== "object") { return "-"; }
  var parts = [];
  if (execution.state) { parts.push("state=" + execution.state); }
  if (execution.command_id) { parts.push("id=" + execution.command_id); }
  if (execution.completed !== undefined) { parts.push("completed=" + execution.completed); }
  if (execution.failed) { parts.push("failed=true"); }
  if (execution.expired) { parts.push("expired=true"); }
  if (execution.blocker_manager) {
    parts.push("blocker=" + execution.blocker_manager + ": " + (execution.blocker_reason || ""));
  }
  if (Array.isArray(execution.stages) && execution.stages.length) {
    var missing = execution.stages
      .filter(function (stage) { return stage && stage.ok === false; })
      .map(function (stage) { return stage.name + "@" + (stage.manager || "unknown"); });
    if (missing.length) { parts.push("missing=" + missing.join(", ")); }
  }
  if (Array.isArray(execution.scenarios) && execution.scenarios.length) {
    var passed = execution.scenarios
      .filter(function (scenario) { return scenario && scenario.ok === true; })
      .map(function (scenario) { return scenario.name; });
    var missingScenarios = execution.scenarios
      .filter(function (scenario) { return scenario && scenario.ok === false; })
      .map(function (scenario) { return scenario.name; });
    if (passed.length) { parts.push("passed=" + passed.join(", ")); }
    if (missingScenarios.length) { parts.push("scenario_missing=" + missingScenarios.join(", ")); }
  }
  return parts.length ? parts.join(" | ") : "-";
}

function renderMicroMachineLogSnippets(snippets) {
  var list = document.getElementById("micromachine-log-snippets");
  if (!list) { return; }
  list.textContent = "";
  if (!Array.isArray(snippets) || !snippets.length) {
    var empty = document.createElement("li");
    empty.textContent = "-";
    list.appendChild(empty);
    return;
  }
  snippets.forEach(function (snippet) {
    var item = document.createElement("li");
    var source = snippet && snippet.source ? "[" + snippet.source + "] " : "";
    item.textContent = source + ((snippet && snippet.line) || "");
    list.appendChild(item);
  });
}

function updateMicroMachineBadge(data, updateId) {
  var badge = document.getElementById("micromachine-applied-badge");
  if (!badge) { return; }
  var normalizedUpdateId = String(
    updateId ||
    commandConsolePreferredUpdateId(data || {}) ||
    microMachineUpdateId(data || {}) ||
    ""
  );
  var scopedData = commandConsoleDataForUpdate(
    data || {},
    normalizedUpdateId
  );
  scopedData = commandConsoleDataForCanonicalOperation(
    scopedData,
    normalizedUpdateId,
    activeCommandConsoleRecord.operationId,
    activeCommandConsoleRecord.operationGeneration
  );
  var intervention = scopedData.intervention || {};
  var status = scopedData.consumption_status || scopedData.status || "";
  var model = commandConsoleStageModel(scopedData);
  badge.className = "micro-badge micro-badge-pending";
  if (model.cancelled) {
    if (!commandConsoleTerminalCleanupVerified(model)) {
      badge.textContent = commandUiText(
        "취소 정리 확인 중",
        "Cancellation cleanup pending",
        "正在确认取消清理"
      );
      return;
    }
    badge.className = "micro-badge micro-badge-cancelled";
    badge.textContent = commandUiText("작전 취소", "Operation cancelled", "作战已取消");
    return;
  }
  if (model.superseded) {
    badge.className = "micro-badge micro-badge-cancelled";
    badge.textContent = commandUiText("작전 교체", "Order superseded", "作战已替换");
    return;
  }
  if (model.blocked) {
    badge.className = "micro-badge micro-badge-blocked";
    badge.textContent = commandUiText("실행 실패", "Execution blocked", "执行失败");
    return;
  }
  if (model.effectObserved) {
    badge.className = "micro-badge micro-badge-applied";
    badge.textContent = commandUiText("실행 확인", "Effect verified", "效果已确认");
    return;
  }
  if (model.canonicalCompletionVerified) {
    badge.className = "micro-badge micro-badge-applied";
    badge.textContent = commandUiText(
      "작전 완료 확인",
      "Operation completion verified",
      "作战完成已确认"
    );
    return;
  }
  if (model.actionIssued) {
    badge.className = "micro-badge micro-badge-active";
    badge.textContent = commandUiText("전장에서 실행 중", "Executing in SC2", "正在 SC2 执行");
    return;
  }
  if (intervention && intervention.applied) {
    badge.className = "micro-badge micro-badge-active";
    badge.textContent = commandUiText("배정·실행 대기", "Waiting for assignment/action", "等待分配与执行");
    return;
  }
  badge.textContent = status === "consumed"
    ? commandUiText("MicroMachine이 명령을 읽음", "MicroMachine read the order", "MicroMachine 已读取命令")
    : commandUiText("실행 상태 대기", "Waiting for execution state", "等待执行状态");
}

function renderMicroMachineIntervention(data) {
  var intervention = (data && data.intervention) || {};
  setMicroMachineText("micromachine-latest-update", intervention.latest_update_id);
  setMicroMachineText("micromachine-active-ids", intervention.active_modulation_ids);
  setMicroMachineText("micromachine-frame", intervention.telemetry_frame);
  setMicroMachineText("micromachine-domains", intervention.manager_bias_domains);
  var goalParts = [];
  if (intervention.goal) { goalParts.push(intervention.goal); }
  if (intervention.override_level) { goalParts.push("override=" + intervention.override_level); }
  if (intervention.confidence !== null && intervention.confidence !== undefined) {
    goalParts.push("confidence=" + intervention.confidence);
  }
  setMicroMachineText("micromachine-goal", goalParts.join(" | "));
  setMicroMachineText("micromachine-strategy-mode", intervention.strategy_mode);
  setMicroMachineText("micromachine-managers", summarizeMicroMachineManagers(intervention.manager_snapshot));
  setMicroMachineText("micromachine-posture", intervention.tactical_posture);
  var scopeText = formatMicroMachineScope(intervention.tactical_scope);
  var lifetimeText = formatMicroMachineLifetime(intervention.lifetime);
  setMicroMachineText(
    "micromachine-scope",
    scopeText + " | lifetime " + lifetimeText
  );
  setMicroMachineText("micromachine-consumed-axes", formatMicroMachineAxesByManager(intervention.consumed_axes_by_manager));
  setMicroMachineText("micromachine-target-priority", formatMicroMachineTargetPriority(intervention.target_priority));
  setMicroMachineText("micromachine-attack-gate", formatMicroMachineAttackGate(intervention.attack_gate));
  setMicroMachineText("micromachine-tactical-evidence", formatMicroMachineTacticalEvidence(intervention.tactical_evidence));
  setMicroMachineText("micromachine-command-execution", formatMicroMachineCommandExecution(intervention.command_execution));
  setMicroMachineText("micromachine-refusal", intervention.refusal_reason);
  renderMicroMachineLogSnippets(intervention.log_snippets);
  updateMicroMachineBadge(
    data || {},
    activeCommandConsoleRecord.updateId ||
      commandConsolePreferredUpdateId(data || {})
  );
  var raw = document.getElementById("micromachine-raw-evidence");
  if (raw) {
    raw.textContent = JSON.stringify({
      intervention: intervention,
      update: data && data.update,
      telemetry: data && data.dashboard && data.dashboard.telemetry,
      command_execution: intervention.command_execution,
      tactical_logs: intervention.log_snippets
    }, null, 2);
  }
}

function microMachineStatusIsStaleForActiveCommand(data) {
  if (!activeCommandConsoleRecord.updateId) { return false; }
  var updateIds = commandConsoleDataUpdateIds(data || {});
  if (updateIds.indexOf(activeCommandConsoleRecord.updateId) === -1) {
    return false;
  }
  var preferredUpdateId = commandConsolePreferredUpdateId(data || {});
  if (
    preferredUpdateId &&
    preferredUpdateId !== activeCommandConsoleRecord.updateId
  ) {
    return false;
  }
  var telemetryFrame = commandConsoleTelemetryFrame(data || {});
  return Boolean(
    telemetryFrame >= 0 &&
    activeCommandConsoleRecord.telemetryFrame >= 0 &&
    telemetryFrame < activeCommandConsoleRecord.telemetryFrame
  );
}

function renderMicroMachineStatus(data, options) {
  options = options || {};
  var node = document.getElementById("micromachine-status");
  if (!node) { return; }
  if (!data || data.enabled === false) {
    node.textContent = (data && data.error) || "MicroMachine modulation disabled.";
    renderOperationConsole(data || {});
    renderBattlefieldControlOverview(data || {});
    renderActiveCommandConsole(data || {});
    renderMicroMachineIntervention(data || {});
    return;
  }
  var statusOperationPayloads = commandOperationPayloads(data);
  var statusSessionEpoch = operationPayloadSessionEpoch(
    data,
    statusOperationPayloads
  );
  var currentSessionEpoch = (
    operationConsoleSessionEpoch ||
    activeCommandConsoleRecord.sessionEpoch
  );
  if (
    data.operation_registry_authoritative === false &&
    statusSessionEpoch &&
    statusSessionEpoch !== currentSessionEpoch
  ) {
    return;
  }
  var modulationResults = Array.isArray(data.modulation_results)
    ? data.modulation_results
    : [];
  modulationResults.forEach(function(result) {
    if (!options.suppressPlanAnnouncements) {
      announceAcceptedTacticalPlan(
        Object.assign(
          {
            blackboard_scope_id: microMachineScopeId(data)
          },
          result || {}
        ),
        "status"
      );
    }
    maybeAppendMicroMachineAsyncCompletion(result);
  });
  if (!options.suppressPlanAnnouncements) {
    announceAcceptedTacticalPlan(data, "status");
  }
  renderOperationConsole(data);
  renderBattlefieldControlOverview(data);
  if (microMachineStatusIsStaleForActiveCommand(data)) {
    return;
  }
  var dashboard = data.dashboard || {};
  var active = Array.isArray(dashboard.active_updates) ? dashboard.active_updates : [];
  var latest = active.length ? active[0] : null;
  var parts = [];
  if (data.status) { parts.push(String(data.status)); }
  if (data.consumption_status) { parts.push(String(data.consumption_status)); }
  if (latest && latest.update_id) { parts.push("update " + latest.update_id); }
  if (latest && Array.isArray(latest.manager_bias_domains)) {
    parts.push("domains " + latest.manager_bias_domains.join(", "));
  }
  if (data.latest_request && data.latest_request.update_id) {
    var latestRequest = data.latest_request;
    var requestBits = ["latest_request " + latestRequest.update_id];
    if (latestRequest.status) { requestBits.push(String(latestRequest.status)); }
    if (latestRequest.consumption_status) {
      requestBits.push(String(latestRequest.consumption_status));
    }
    parts.push(requestBits.join(" "));
  }
  if (dashboard.telemetry && typeof dashboard.telemetry.frame === "number") {
    parts.push("frame " + dashboard.telemetry.frame);
  }
  if (data.compile_result && data.compile_result.refusal_reason) {
    parts.push(t("microMachineRefused") + ": " + data.compile_result.refusal_reason);
  }
  if (data.compile_result && data.compile_result.clarification_prompt) {
    parts.push(t("microMachineClarification") + ": " + data.compile_result.clarification_prompt);
  }
  if (dashboard.last_failure) { parts.push("failure " + dashboard.last_failure); }
  var statusText = parts.length ? parts.join(" | ") : t("microMachinePending");
  if (node.textContent !== statusText) {
    node.textContent = statusText;
  }
  if (!(data && data.command_console_skip_render)) {
    renderActiveCommandConsole(data);
  }
  renderMicroMachineIntervention(data);
  maybeAppendMicroMachineAsyncCompletion(data);
}

function safeRenderMicroMachineStatus(data, options) {
  try {
    renderMicroMachineStatus(data, options);
  } catch (error) {
    var node = document.getElementById("micromachine-status");
    if (node) {
      node.textContent = t("microMachineFailed") + ": dashboard render failed: " + error.message;
    }
    if (typeof console !== "undefined" && console.warn) {
      console.warn("MicroMachine dashboard render failed", error);
    }
  }
}

function synchronizeMicroMachineBlackboardDirectory(directory) {
  var normalized = String(directory || "").trim();
  if (microMachinePollBlackboardDir === null) {
    microMachinePollBlackboardDir = normalized;
    return false;
  }
  if (microMachinePollBlackboardDir === normalized) {
    return false;
  }
  microMachinePollBlackboardDir = normalized;
  resetEventCursorForBlackboard(normalized);
  microMachineBlackboardContextGeneration += 1;
  microMachinePollQueued = false;
  if (
    microMachinePollAbortController &&
    typeof microMachinePollAbortController.abort === "function"
  ) {
    microMachinePollAbortController.abort();
  }
  if (microMachinePollTimeoutId !== null && window.clearTimeout) {
    window.clearTimeout(microMachinePollTimeoutId);
  }
  microMachinePollAbortController = null;
  microMachinePollTimeoutId = null;
  microMachinePollInFlight = false;
  microMachinePollActiveRequestSeq = 0;
  pendingMicroMachineAsyncUpdates = {};
  deferredPendingMicroMachineTransfers = {};
  knownPendingMicroMachineUpdateKeys = {};
  consumedMicroMachineResultIdsByScope = {};
  latestMicroMachinePlanText = "";
  clearPendingMicroMachinePlan();
  resetActiveCommandConsole();
  resetTacticalRadio("");
  renderMicroMachineIntervention({});
  var statusNode = document.getElementById("micromachine-status");
  if (statusNode) {
    statusNode.textContent = commandUiText(
      "새 MicroMachine blackboard 상태를 기다리는 중입니다.",
      "Waiting for the new MicroMachine blackboard state.",
      "正在等待新的 MicroMachine blackboard 状态。"
    );
  }
  return true;
}

function finishMicroMachinePoll(requestSeq) {
  if (microMachinePollActiveRequestSeq !== requestSeq) { return; }
  if (microMachinePollTimeoutId !== null && window.clearTimeout) {
    window.clearTimeout(microMachinePollTimeoutId);
  }
  microMachinePollInFlight = false;
  microMachinePollActiveRequestSeq = 0;
  microMachinePollAbortController = null;
  microMachinePollTimeoutId = null;
  if (microMachinePollQueued) {
    microMachinePollQueued = false;
    pollMicroMachineStatus();
  }
}

function pollMicroMachineStatus() {
  var input = document.getElementById("micromachine-blackboard-dir");
  var suffix = authQuery;
  var directory = input ? input.value.trim() : "";
  synchronizeMicroMachineBlackboardDirectory(directory);
  if (microMachinePollInFlight) {
    microMachinePollQueued = true;
    return;
  }
  if (directory) {
    suffix += (suffix ? "&" : "?") + "blackboard_dir=" + encodeURIComponent(directory);
  }
  microMachinePollRequestSeq += 1;
  var requestSeq = microMachinePollRequestSeq;
  var contextGeneration = microMachineBlackboardContextGeneration;
  var hydrateOnly = commandEventAwaitingInitialSnapshot;
  microMachinePollInFlight = true;
  microMachinePollActiveRequestSeq = requestSeq;
  microMachinePollAbortController = typeof AbortController !== "undefined"
    ? new AbortController()
    : null;
  var fetchOptions = microMachinePollAbortController
    ? { signal: microMachinePollAbortController.signal }
    : undefined;
  if (window.setTimeout) {
    microMachinePollTimeoutId = window.setTimeout(function() {
      if (
        contextGeneration !== microMachineBlackboardContextGeneration ||
        microMachinePollActiveRequestSeq !== requestSeq
      ) {
        return;
      }
      if (
        microMachinePollAbortController &&
        typeof microMachinePollAbortController.abort === "function"
      ) {
        microMachinePollAbortController.abort();
      }
      expirePendingMicroMachineAsync();
      var node = document.getElementById("micromachine-status");
      if (node) {
        node.textContent = commandUiText(
          "MicroMachine 상태 확인이 지연되어 새 요청으로 재시도합니다.",
          "MicroMachine status timed out; retrying with a fresh request.",
          "MicroMachine 状态请求超时，正在使用新请求重试。"
        );
      }
      microMachinePollQueued = true;
      finishMicroMachinePoll(requestSeq);
    }, MICROMACHINE_STATUS_POLL_TIMEOUT_MS);
  }
  fetch("/api/micromachine/status" + suffix, fetchOptions)
    .then(parseJsonResponse)
    .then(function(data) {
      if (
        contextGeneration === microMachineBlackboardContextGeneration &&
        microMachinePollActiveRequestSeq === requestSeq &&
        requestSeq >= microMachinePollAppliedSeq
      ) {
        if (
          hydrateOnly &&
          !commandEventAwaitingInitialSnapshot
        ) {
          finishMicroMachinePoll(requestSeq);
          return;
        }
        microMachinePollAppliedSeq = requestSeq;
        if (hydrateOnly) {
          commandEventAwaitingInitialSnapshot = false;
          commandEventPollWonInitialHydration = true;
          hydrateTacticalRadioState(data);
        }
        safeRenderMicroMachineStatus(
          data,
          hydrateOnly ? { suppressPlanAnnouncements: true } : undefined
        );
      }
      finishMicroMachinePoll(requestSeq);
    })
    .catch(function (error) {
      if (
        contextGeneration === microMachineBlackboardContextGeneration &&
        microMachinePollActiveRequestSeq === requestSeq &&
        requestSeq >= microMachinePollAppliedSeq &&
        error && error.name !== "AbortError"
      ) {
        microMachinePollAppliedSeq = requestSeq;
        expirePendingMicroMachineAsync();
        var node = document.getElementById("micromachine-status");
        if (node) { node.textContent = t("microMachineFailed") + ": " + error.message; }
      }
      finishMicroMachinePoll(requestSeq);
    });
}

function optionalMicroMachineField(id) {
  var node = document.getElementById(id);
  return node ? node.value.trim() : "";
}

function optionalMicroMachineNumber(id) {
  var value = optionalMicroMachineField(id);
  if (!value) { return null; }
  var parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function buildMicroMachineSemanticScopePayload() {
  var scope = {};
  var armyGroup = optionalMicroMachineField("micromachine-army-group");
  var locationIntent = optionalMicroMachineField("micromachine-location-intent");
  var unitClassText = optionalMicroMachineField("micromachine-unit-classes");
  var durationSeconds = optionalMicroMachineNumber("micromachine-duration-seconds");
  var safetyMargin = optionalMicroMachineNumber("micromachine-safety-margin");
  if (armyGroup) { scope.army_group = armyGroup; }
  if (locationIntent) { scope.location_intent = locationIntent; }
  if (unitClassText) {
    unitClassText = unitClassText
      .replace(/siege tank/ig, "siege_tank")
      .replace(/widow mine/ig, "widow_mine");
    scope.unit_classes = unitClassText.split(/[\\s,]+/).map(function (item) {
      return item.trim().toLowerCase().replace(/-/g, "_");
    }).filter(Boolean);
  }
  if (durationSeconds !== null) { scope.duration_seconds = Math.floor(durationSeconds); }
  if (safetyMargin !== null) { scope.require_safety_margin = safetyMargin; }
  return scope;
}

function detectMicroMachineResponseLanguage(text) {
  var normalized = text || "";
  if (/[가-힣]/.test(normalized)) { return "ko"; }
  if (/[\u4e00-\u9fff]/.test(normalized)) { return "zh"; }
  if (/[A-Za-z]/.test(normalized)) { return "en"; }
  return currentLang || "ko";
}

function looksLikeMicroMachineTacticalCommand(text) {
  var normalized = (text || "").toLowerCase();
  if (!normalized) { return false; }
  return /공격|러쉬|러시|압박|정찰|수색|적진|본진|기지|attack|rush|pressure|scout|recon|enemy base|enemy main|main base|进攻|侦察/.test(normalized);
}

function buildMicroMachineModulationPayload(text) {
  var blackboardInput = document.getElementById("micromachine-blackboard-dir");
  var payload = {
    text: text,
    blackboard_dir: blackboardInput ? blackboardInput.value.trim() : "",
    ui_language: currentLang || "ko",
    response_language: detectMicroMachineResponseLanguage(text),
    async_publish: true
  };
  var semanticScope = buildMicroMachineSemanticScopePayload();
  if (Object.keys(semanticScope).length) {
    payload.semantic_scope = semanticScope;
  }
  var ttlSeconds = optionalMicroMachineNumber("micromachine-ttl-seconds");
  if (ttlSeconds !== null) {
    payload.ttl_seconds = Math.floor(ttlSeconds);
  }
  return payload;
}

function newMicroMachineClientUpdateId() {
  microMachineClientUpdateSeq += 1;
  return "voi-web-" + Date.now() + "-" + microMachineClientUpdateSeq;
}

function looksLikeMicroMachineEmergencyCommand(text) {
  // Display-only classifier retained for local affordances; it never retires
  // pending work. Server command_queue edges are the sole authority for that.
  var normalized = " " + String(text || "").toLowerCase()
    .replace(/\\s+/g, " ")
    .trim() + " ";
  if (
    /(?:취소|중지|중단|후퇴|퇴각|철수).{0,12}(?:하지 마|말고|금지|없이)|(?:후퇴|퇴각|철수).{0,12}(?:아니|안 하)|\\b(?:no retreat|do not stop|never retreat|retreat is not an option)\\b|(?:不要|禁止).{0,4}(?:撤退|取消|停止)/.test(normalized)
  ) {
    return false;
  }
  return (
    /^(?:(?:긴급|즉시|당장|지금|전원|모두)\\s*)*(?:후퇴|퇴각|철수)(?:\\s*(?:해|하라|하세요|해라|해줘|해\\s*주세요|진행해|시작해))?[.!]?$/.test(normalized.trim()) ||
    /^(?:please\\s+)?(?:emergency\\s+)?(?:retreat|fall\\s+back)(?:\\s+(?:now|immediately))?[.!]?$/.test(normalized.trim()) ||
    /^(?:(?:立即|马上|紧急)\\s*)?撤退(?:吧|！|。)?$/.test(normalized.trim()) ||
    /(?:공격|러시|러쉬|압박|작전|진격)(?:을|를|은|는)?\\s*(?:취소|중지|중단|멈춰|그만)|(?:cancel|abort|stop)\\s+(?:the\\s+)?(?:attack|attacking|rush|pressure|operation|advance)|(?:attack|rush|pressure|operation|advance)\\s+(?:cancel|abort|stop)|(?:取消|停止)\\s*(?:进攻|攻击|行动)|(?:进攻|攻击|行动)\\s*(?:取消|停止)/.test(normalized)
  );
}

function microMachineScopeId(data) {
  var compileResult = data && data.compile_result;
  var scopeId = data && data.blackboard_scope_id;
  if (!scopeId && compileResult) { scopeId = compileResult.blackboard_scope_id; }
  return typeof scopeId === "string" ? scopeId : "";
}

function microMachineResultId(data) {
  var compileResult = data && data.compile_result;
  var resultId = data && data.result_id;
  if (!resultId && compileResult) { resultId = compileResult.result_id; }
  return typeof resultId === "string" ? resultId : "";
}

function microMachineUpdateId(data) {
  var compileResult = (data && data.compile_result) || {};
  var update = (data && data.update) || {};
  var intervention = (data && data.intervention) || {};
  var execution = intervention.command_execution || {};
  return String(
    update.update_id ||
    compileResult.update_id ||
    execution.command_id ||
    ""
  );
}

function microMachinePendingKey(scopeId, updateId) {
  return scopeId + "\u0000" + updateId;
}

function pendingMicroMachineRecord(scopeId, updateId) {
  if (!scopeId || !updateId) { return null; }
  return pendingMicroMachineAsyncUpdates[
    microMachinePendingKey(scopeId, updateId)
  ] || null;
}

function rememberPendingMicroMachineAsync(text, data, pendingId, bindConsole) {
  var scopeId = microMachineScopeId(data);
  var updateId = data && data.update_id;
  if (
    !data ||
    !data.async_publish ||
    typeof scopeId !== "string" ||
    !scopeId ||
    typeof updateId !== "string" ||
    !updateId
  ) {
    return null;
  }
  var record = {
    scopeId: scopeId,
    updateId: updateId,
    text: text,
    pendingId: pendingId || "",
    createdAt: Date.now(),
    observationTimedOut: false,
    supersededUpdateIds: [],
    preservedUpdateIds: [],
    preservedCommandTexts: []
  };
  var pendingKey = microMachinePendingKey(scopeId, updateId);
  pendingMicroMachineAsyncUpdates[pendingKey] = record;
  knownPendingMicroMachineUpdateKeys[pendingKey] = true;
  applyDeferredPendingMicroMachineTransfers(scopeId, updateId);
  if (
    bindConsole !== false &&
    bindActiveCommandConsoleUpdate(text, pendingId, scopeId, updateId)
  ) {
    activeCommandConsoleRecord.submissionDelayed = false;
    renderActiveCommandConsole(Object.assign({}, data, {
      command_text: text,
      intervention: data.intervention || {
        command_execution: {
          command_id: updateId,
          state: "parsed",
          completed: false,
          failed: false,
          expired: false,
          stages: []
        }
      }
    }), true);
  }
  return record;
}

function appendUniqueMicroMachineValue(values, value) {
  if (value && values.indexOf(value) === -1) {
    values.push(value);
  }
}

function movePendingMicroMachinePredecessor(
  scopeId,
  predecessorUpdateId,
  replacementUpdateId,
  relation
) {
  if (
    !scopeId ||
    !predecessorUpdateId ||
    predecessorUpdateId === replacementUpdateId
  ) {
    return false;
  }
  var predecessor = pendingMicroMachineRecord(scopeId, predecessorUpdateId);
  if (!predecessor) { return false; }
  var replacement = pendingMicroMachineRecord(scopeId, replacementUpdateId);
  if (!replacement) {
    var deferredKey = microMachinePendingKey(scopeId, replacementUpdateId);
    if (knownPendingMicroMachineUpdateKeys[deferredKey]) {
      delete pendingMicroMachineAsyncUpdates[
        microMachinePendingKey(scopeId, predecessorUpdateId)
      ];
      removePendingById(predecessor.pendingId);
      return true;
    }
    var deferred = deferredPendingMicroMachineTransfers[deferredKey] || [];
    var duplicate = deferred.some(function(item) {
      return (
        item.predecessorUpdateId === predecessorUpdateId &&
        item.relation === relation
      );
    });
    if (!duplicate) {
      deferred.push({
        predecessorUpdateId: predecessorUpdateId,
        relation: relation
      });
    }
    deferredPendingMicroMachineTransfers[deferredKey] = deferred;
    predecessor.deferredReplacementUpdateId = replacementUpdateId;
    return true;
  }
  delete pendingMicroMachineAsyncUpdates[
    microMachinePendingKey(scopeId, predecessorUpdateId)
  ];
  removePendingById(predecessor.pendingId);
  var targetIds = relation === "parent"
    ? replacement.preservedUpdateIds
    : replacement.supersededUpdateIds;
  appendUniqueMicroMachineValue(targetIds, predecessorUpdateId);
  var inheritedIds = relation === "parent"
    ? predecessor.preservedUpdateIds
    : predecessor.supersededUpdateIds;
  (Array.isArray(inheritedIds) ? inheritedIds : []).forEach(function(updateId) {
    appendUniqueMicroMachineValue(targetIds, updateId);
  });
  if (relation === "parent") {
    appendUniqueMicroMachineValue(
      replacement.preservedCommandTexts,
      predecessor.text
    );
    (Array.isArray(predecessor.preservedCommandTexts)
      ? predecessor.preservedCommandTexts
      : []
    ).forEach(function(commandText) {
      appendUniqueMicroMachineValue(
        replacement.preservedCommandTexts,
        commandText
      );
    });
  }
  return true;
}

function applyDeferredPendingMicroMachineTransfers(scopeId, replacementUpdateId) {
  var deferredKey = microMachinePendingKey(scopeId, replacementUpdateId);
  var deferred = deferredPendingMicroMachineTransfers[deferredKey];
  if (!Array.isArray(deferred) || !deferred.length) { return false; }
  delete deferredPendingMicroMachineTransfers[deferredKey];
  var moved = false;
  deferred.forEach(function(item) {
    if (
      movePendingMicroMachinePredecessor(
        scopeId,
        item.predecessorUpdateId,
        replacementUpdateId,
        item.relation
      )
    ) {
      moved = true;
    }
  });
  return moved;
}

function microMachineCommandQueue(data) {
  var intervention = (data && data.intervention) || {};
  var compileResult = (data && data.compile_result) || {};
  var candidates = [
    data && data.command_queue,
    intervention.command_queue,
    compileResult.command_queue
  ];
  for (var index = 0; index < candidates.length; index += 1) {
    var candidate = candidates[index];
    if (
      candidate &&
      typeof candidate === "object" &&
      !Array.isArray(candidate) &&
      Object.keys(candidate).length
    ) {
      return candidate;
    }
  }
  return {};
}

function exactMicroMachinePredecessorEdges(data, scopeId, currentUpdateId) {
  var commandQueue = microMachineCommandQueue(data);
  var changed = false;
  var parentIds = Array.isArray(commandQueue.parent_command_ids)
    ? commandQueue.parent_command_ids
    : [];
  var supersededIds = Array.isArray(commandQueue.superseded_update_ids)
    ? commandQueue.superseded_update_ids
    : [];
  var normalizedSupersededIds = supersededIds.map(function(updateId) {
    return String(updateId || "");
  });
  parentIds.forEach(function(parentUpdateId) {
    var normalizedParentUpdateId = String(parentUpdateId || "");
    if (normalizedSupersededIds.indexOf(normalizedParentUpdateId) !== -1) {
      return;
    }
    if (
      movePendingMicroMachinePredecessor(
        scopeId,
        normalizedParentUpdateId,
        currentUpdateId,
        "parent"
      )
    ) {
      changed = true;
    }
  });
  normalizedSupersededIds.forEach(function(supersededUpdateId) {
    if (
      movePendingMicroMachinePredecessor(
        scopeId,
        supersededUpdateId,
        currentUpdateId,
        "superseded"
      )
    ) {
      changed = true;
    }
  });
  var supersededByUpdateId = String(
    commandQueue.superseded_by_update_id || ""
  );
  if (
    supersededByUpdateId &&
    movePendingMicroMachinePredecessor(
      scopeId,
      currentUpdateId,
      supersededByUpdateId,
      "superseded"
    )
  ) {
    changed = true;
  }
  return changed;
}

function microMachineAsyncTimeoutError() {
  return new Error(
    "MicroMachine 실행 효과가 " +
    Math.round(MICROMACHINE_ASYNC_PENDING_TIMEOUT_MS / 1000) +
    "초 안에 관측되지 않았습니다. 명령 추적은 유지하며 늦게 도착한 실제 효과를 계속 반영합니다."
  );
}

function expirePendingMicroMachineAsync(nowMs) {
  var currentTime = typeof nowMs === "number" ? nowMs : Date.now();
  Object.keys(pendingMicroMachineAsyncUpdates || {}).forEach(function(key) {
    var pending = pendingMicroMachineAsyncUpdates[key];
    var createdAt = pending && Number(pending.createdAt);
    if (
      !createdAt ||
      pending.observationTimedOut ||
      currentTime - createdAt < MICROMACHINE_ASYNC_PENDING_TIMEOUT_MS
    ) {
      return;
    }
    pending.observationTimedOut = true;
    removePendingById(pending.pendingId);
    if (
      activeCommandConsoleRecord.updateId === pending.updateId &&
      (
        !activeCommandConsoleRecord.scopeId ||
        activeCommandConsoleRecord.scopeId === pending.scopeId
      )
    ) {
      activeCommandConsoleRecord.observationTimedOut = true;
      renderActiveCommandConsole(
        Object.assign({}, activeCommandConsoleRecord.data || {}, {
          command_console_observation_delayed: true
        }),
        true
      );
    }
  });
}

function maybeAppendMicroMachineAsyncCompletion(data) {
  if (!data || !pendingMicroMachineAsyncUpdates) { return; }
  var scopeId = microMachineScopeId(data);
  var resultId = microMachineResultId(data);
  if (!scopeId || !resultId) {
    expirePendingMicroMachineAsync();
    return;
  }
  var consumedResultIds = consumedMicroMachineResultIdsByScope[scopeId] || {};
  var resultAlreadyConsumed = Boolean(consumedResultIds[resultId]);
  var compileResult = data.compile_result || {};
  var update = data.update || {};
  var intervention = data.intervention || {};
  var execution = intervention.command_execution || {};
  var compileUpdateId = String(compileResult.update_id || "");
  var activeUpdateId = String(update.update_id || "");
  var executionUpdateId = String(execution.command_id || activeUpdateId);
  var currentUpdateId = microMachineUpdateId(data);
  if (!currentUpdateId) {
    expirePendingMicroMachineAsync();
    return;
  }
  var isTerminalRefusal = Boolean(
    compileResult.refusal_reason ||
    compileResult.clarification_prompt ||
    compileResult.status === "refused" ||
    compileResult.status === "clarification_required" ||
    data.status === "publish_failed" ||
    data.status === "superseded"
  );
  var commandQueue = microMachineCommandQueue(data);
  exactMicroMachinePredecessorEdges(
    data,
    scopeId,
    currentUpdateId
  );
  var terminalExecutionStates = {
    blocked: true,
    canceled: true,
    cancelled: true,
    completed: true,
    effect_observed: true,
    failed: true,
    expired: true,
    rejected: true,
    superseded: true
  };
  var candidateUpdateIds = [];
  if (activeUpdateId) { candidateUpdateIds.push(activeUpdateId); }
  if (compileUpdateId && compileUpdateId !== activeUpdateId) {
    candidateUpdateIds.push(compileUpdateId);
  }
  if (
    executionUpdateId &&
    executionUpdateId !== activeUpdateId &&
    executionUpdateId !== compileUpdateId
  ) {
    candidateUpdateIds.push(executionUpdateId);
  }
  commandOperationPayloads(data).forEach(function(operation) {
    var operationUpdateId = operationPayloadUpdateId(operation);
    if (
      operationUpdateId &&
      candidateUpdateIds.indexOf(operationUpdateId) === -1
    ) {
      candidateUpdateIds.push(operationUpdateId);
    }
  });
  var terminalHandled = false;
  var resultIdentityHandled = false;
  candidateUpdateIds.forEach(function(updateId) {
    var pending = pendingMicroMachineRecord(scopeId, updateId);
    if (!updateId || !pending) { return; }
    if (pending.deferredReplacementUpdateId) { return; }
    var terminalForUpdate = updateId === compileUpdateId && isTerminalRefusal;
    var executionState = updateId === executionUpdateId
      ? String(execution.state || "").toLowerCase()
      : "";
    var operationsForUpdate = (
      Array.isArray(data.operations) ? data.operations : []
    ).filter(function(operation) {
      return (
        operation &&
        typeof operation === "object" &&
        operationPayloadUpdateId(operation) === updateId
      );
    });
    var operationCandidates = operationsForUpdate
      .filter(function(operation) {
        return Boolean(operation.battlefield_operation);
      })
      .map(function(operation) {
        return commandOperationData(operation, data);
      });
    var allCanonicalOperationsTerminal = Boolean(
      operationsForUpdate.length &&
      operationCandidates.length === operationsForUpdate.length &&
      operationCandidates.every(function(candidate) {
        return commandConsoleStageModel(candidate).terminal;
      })
    );
    var legacyOperation = (
      !operationCandidates.length &&
      operationsForUpdate.length === 1
    ) ? operationsForUpdate[0] : null;
    var legacyOperationId = legacyOperation
      ? operationPayloadOperationId(legacyOperation)
      : "";
    var legacyOperationGeneration = legacyOperation
      ? Number(legacyOperation.operation_generation || 0)
      : 0;
    var legacyOperationExecutionMatches = Boolean(
      legacyOperation &&
      operationExecutionMatchesPayload(
        legacyOperation,
        execution,
        legacyOperationId,
        updateId,
        legacyOperationGeneration
      )
    );
    var executionGeneration = Number(
      execution.operation_generation || execution.generation || 0
    );
    var unscopedLegacyExecution = Boolean(
      !operationsForUpdate.length &&
      String(execution.command_id || "") === updateId &&
      !execution.operation_id &&
      executionGeneration <= 0
    );
    var terminalExecutionState = Boolean(
      terminalExecutionStates[executionState] &&
      (
        executionState !== "effect_observed" ||
        microMachineExecutionEffectObserved(execution)
      )
    );
    var terminalExecution = Boolean(
      terminalExecutionState &&
      (
        legacyOperationExecutionMatches ||
        unscopedLegacyExecution
      )
    );
    if (
      !terminalForUpdate &&
      !terminalExecution &&
      !allCanonicalOperationsTerminal
    ) {
      return;
    }
    if (updateId === compileUpdateId && resultAlreadyConsumed) { return; }
    delete pendingMicroMachineAsyncUpdates[
      microMachinePendingKey(scopeId, updateId)
    ];
    var narrationData = data;
    if (terminalExecution && !terminalForUpdate && compileUpdateId && compileUpdateId !== updateId) {
      narrationData = Object.assign({}, data, {
        compile_result: {},
        latest_request: null
      });
    }
    if (pending.supersededUpdateIds && pending.supersededUpdateIds.length) {
      narrationData = Object.assign({}, narrationData, {
        command_queue: Object.assign({}, commandQueue, {
          superseded_previous: true,
          superseded_update_ids: pending.supersededUpdateIds.slice()
        })
      });
    }
    if (pending.preservedUpdateIds && pending.preservedUpdateIds.length) {
      narrationData = Object.assign({}, narrationData, {
        command_queue: Object.assign(
          {},
          narrationData.command_queue || commandQueue,
          {
            preserved_update_ids: pending.preservedUpdateIds.slice(),
            preserved_command_texts: pending.preservedCommandTexts.slice()
          }
        )
      });
    }
    narrationData = commandConsoleDataForUpdate(narrationData, updateId);
    if (operationCandidates.length === 1) {
      narrationData = Object.assign(
        {},
        narrationData,
        operationCandidates[0]
      );
    } else if (operationCandidates.length > 1) {
      narrationData = Object.assign({}, narrationData, {
        command_console_operation_aggregate:
          canonicalOperationAggregateOutcome(operationCandidates)
      });
    }
    narrationData = commandConsoleDataForCanonicalOperation(
      narrationData,
      updateId,
      narrationData.operation_id,
      narrationData.operation_generation
    );
    var outcomeStatus = microMachineChatOutcomeStatus(
      narrationData,
      terminalForUpdate ? "clarification" : ""
    );
    terminalHandled = true;
    if (updateId === compileUpdateId) {
      resultIdentityHandled = true;
    }
    safelyAppendMicroMachineChatResult(
      pending.text,
      Object.assign({}, narrationData, {
        chat_outcome_status: outcomeStatus,
        command_console_skip_render: true
      }),
      pending.pendingId
    );
  });
  // A predecessor edge is non-terminal for the replacement update. The
  // server intentionally keeps one immutable result_id per update while its
  // execution advances, so only terminal chat delivery may consume that ID.
  if (terminalHandled && resultIdentityHandled) {
    consumedResultIds[resultId] = true;
    consumedMicroMachineResultIdsByScope[scopeId] = consumedResultIds;
  }
  expirePendingMicroMachineAsync();
}

function canonicalOperationAggregateOutcome(operationCandidates) {
  var candidates = Array.isArray(operationCandidates)
    ? operationCandidates
    : [];
  var aggregate = {
    total: candidates.length,
    executed: 0,
    blocked: 0,
    cancelled: 0,
    status: "partially_executed"
  };
  candidates.forEach(function(candidate) {
    var model = commandConsoleStageModel(candidate || {});
    if (model.effectObserved || model.canonicalCompletionVerified) {
      aggregate.executed += 1;
      return;
    }
    if (model.cancelled || model.superseded) {
      aggregate.cancelled += 1;
      return;
    }
    if (model.blocked) {
      aggregate.blocked += 1;
    }
  });
  if (aggregate.total > 0 && aggregate.executed === aggregate.total) {
    aggregate.status = "executed";
  } else if (aggregate.total > 0 && aggregate.blocked === aggregate.total) {
    aggregate.status = "blocked";
  } else if (aggregate.total > 0 && aggregate.cancelled === aggregate.total) {
    aggregate.status = "clarification";
  }
  return aggregate;
}

function microMachineAssistantMessage(compileResult, vector) {
  var message = compileResult && compileResult.assistant_message;
  if (typeof message === "string" && message.trim()) {
    return message.trim();
  }
  message = vector && vector.assistant_message;
  if (typeof message === "string" && message.trim()) {
    return message.trim();
  }
  return "";
}

function microMachineChatOutcomeStatus(data, requestedStatus) {
  var model = commandConsoleStageModel(data || {});
  var compileResult = (data && data.compile_result) || {};
  var aggregate = data && data.command_console_operation_aggregate;
  var accepted = data && data.accepted !== false && data.ok !== false;
  if (
    aggregate &&
    Number(aggregate.total || 0) > 1 &&
    ["executed", "partially_executed", "blocked", "clarification"].indexOf(
      String(aggregate.status || "")
    ) >= 0
  ) {
    return String(aggregate.status);
  }
  if (model.cancelled || model.superseded) {
    return "clarification";
  }
  if (
    compileResult.clarification_prompt ||
    compileResult.status === "clarification_required"
  ) {
    return "clarification";
  }
  if (model.blocked) {
    return (
      compileResult.refusal_reason ||
      compileResult.status === "refused" ||
      !accepted
    ) ? "clarification" : "blocked";
  }
  if (model.effectObserved || model.canonicalCompletionVerified) {
    return "executed";
  }
  if (!accepted || requestedStatus === "clarification") {
    return "clarification";
  }
  if (requestedStatus === "blocked") {
    return "blocked";
  }
  return "partially_executed";
}

function microMachineChatNarration(data) {
  var narrationUpdateId = commandConsolePreferredUpdateId(data || {}) ||
    microMachineUpdateId(data || {});
  var scopedData = commandConsoleDataForUpdate(
    data || {},
    narrationUpdateId
  );
  scopedData = commandConsoleDataForCanonicalOperation(
    scopedData,
    narrationUpdateId,
    scopedData.operation_id,
    scopedData.operation_generation
  );
  var intervention = scopedData.intervention || {};
  var compileResult = scopedData.compile_result || {};
  var vector = compileResult.vector || {};
  var assistantMessage = microMachineAssistantMessage(compileResult, vector);
  var execution = intervention.command_execution || {};
  var model = commandConsoleStageModel(scopedData);
  var aggregate = scopedData.command_console_operation_aggregate || {};
  var parts = [];
  if (assistantMessage) { parts.push(assistantMessage); }
  if (scopedData.status === "queued") {
    parts.push(commandUiText(
      "명령을 해석하고 있습니다. 같은 작전 카드에서 다음 단계가 계속 갱신됩니다.",
      "Interpreting the order. The same operation card will keep updating.",
      "正在解析命令，同一张作战卡会持续更新。"
    ));
  } else if (compileResult.refusal_reason || compileResult.clarification_prompt || scopedData.accepted === false) {
    parts.push(commandUiText(
      "명령을 실행하지 못했습니다.",
      "The order could not be executed.",
      "无法执行该命令。"
    ));
    if (compileResult.refusal_reason) { parts.push(compileResult.refusal_reason); }
    if (compileResult.clarification_prompt) { parts.push(compileResult.clarification_prompt); }
  } else if (Number(aggregate.total || 0) > 1) {
    if (aggregate.status === "executed") {
      parts.push(commandUiText(
        "병렬 작전 완료 확인: " + aggregate.total + "개 작전의 실행 결과를 확인했습니다.",
        "Parallel operations verified: confirmed execution outcomes for " + aggregate.total + " operations.",
        "并行作战已确认：已确认 " + aggregate.total + " 个作战的执行结果。"
      ));
    } else if (aggregate.status === "blocked") {
      parts.push(commandUiText(
        "병렬 작전 차단: " + aggregate.total + "개 작전이 실행 조건을 충족하지 못했습니다.",
        "Parallel operations blocked: all " + aggregate.total + " operations failed their execution conditions.",
        "并行作战受阻：" + aggregate.total + " 个作战均未满足执行条件。"
      ));
    } else if (aggregate.status === "clarification") {
      parts.push(commandUiText(
        "병렬 작전 종료: " + aggregate.total + "개 작전이 취소되거나 새 명령으로 교체됐습니다.",
        "Parallel operations ended: all " + aggregate.total + " operations were cancelled or superseded.",
        "并行作战已结束：" + aggregate.total + " 个作战均已取消或替换。"
      ));
    } else {
      parts.push(commandUiText(
        "병렬 작전 종료: 전체 " + aggregate.total + "개 중 실행 확인 " +
          aggregate.executed + "개, 차단 " + aggregate.blocked + "개, 취소·교체 " +
          aggregate.cancelled + "개입니다.",
        "Parallel operations ended: " + aggregate.executed + " verified, " +
          aggregate.blocked + " blocked, and " + aggregate.cancelled +
          " cancelled or superseded out of " + aggregate.total + ".",
        "并行作战已结束：共 " + aggregate.total + " 个，已确认 " +
          aggregate.executed + " 个，受阻 " + aggregate.blocked +
          " 个，取消或替换 " + aggregate.cancelled + " 个。"
      ));
    }
  } else if (model.cancelled) {
    parts.push(
      commandConsoleTerminalCleanupVerified(model)
        ? (
            commandConsoleTerminalCleanupStoppedOwnedUnits(model)
              ? commandUiText(
                  "작전 취소: 소유 유닛의 기존 명령을 중지하고 작전에서 해제했습니다.",
                  "Operation cancelled: owned units were stopped and released from the operation.",
                  "作战已取消：所属单位已停止并从作战中释放。"
                )
              : commandUiText(
                  "작전 취소: 소유 유닛이 없어 중지 명령 없이 작전 해제를 확인했습니다.",
                  "Operation cancelled: no owned units remained, so release was verified without a stop command.",
                  "作战已取消：没有剩余所属单位，因此无需停止命令即可确认释放。"
                )
          )
        : commandUiText(
            "작전 취소 요청을 수락했고 terminal 상태를 확인했습니다. 유닛 정지·해제 증거를 기다립니다.",
            "The cancellation request was accepted and the terminal state is confirmed. Waiting for unit stop-and-release evidence.",
            "已接受取消请求并确认终止状态，正在等待单位停止并释放的证据。"
          )
    );
  } else if (model.superseded) {
    parts.push(commandUiText(
      "작전 교체: 새 명령이 기존 작전을 대체했습니다.",
      "Order superseded: a newer order replaced the operation.",
      "作战已替换：新命令替代了原作战。"
    ));
  } else if (model.blocked) {
    parts.push(commandUiText(
      "실행 실패: 실제 효과 확인까지 도달하지 못했습니다.",
      "Execution blocked: the order did not reach observed effect confirmation.",
      "执行失败：命令未达到实际效果确认。"
    ));
  } else if (model.effectObserved) {
    parts.push(commandUiText(
      "실행 확인: 실제 게임 상태에서 명령 효과를 확인했습니다.",
      "Execution verified: the requested effect was observed in the game state.",
      "执行已确认：已在游戏状态中观察到请求效果。"
    ));
  } else if (model.canonicalCompletionVerified) {
    parts.push(commandUiText(
      "실행 확인: MicroMachine 권위 완료 조건으로 작전 완료를 확인했습니다.",
      "Execution verified: MicroMachine confirmed the operation's canonical completion conditions.",
      "执行已确认：MicroMachine 已确认作战的权威完成条件。"
    ));
  } else if (model.actionIssued) {
    parts.push(commandUiText(
      "실행 중: SC2 명령은 전송됐고 실제 효과를 확인하고 있습니다.",
      "Executing: the SC2 command was submitted and effect verification is pending.",
      "执行中：SC2 命令已提交，正在确认实际效果。"
    ));
  } else if (model.assignmentReady) {
    parts.push(commandUiText(
      "유닛 또는 생산 작업을 배정했습니다. 실제 SC2 명령 전송을 기다립니다.",
      "Units or production work are assigned. Waiting for SC2 command submission.",
      "单位或生产任务已分配，正在等待提交 SC2 命令。"
    ));
  } else if (model.interpreted) {
    parts.push(commandUiText(
      "명령 해석이 끝났습니다. MicroMachine이 유닛을 편성하고 있습니다.",
      "Interpretation is complete. MicroMachine is assigning units.",
      "命令解析完成，MicroMachine 正在分配单位。"
    ));
  } else {
    parts.push(commandUiText(
      "명령을 MicroMachine 실행 경로로 전달하고 있습니다.",
      "Sending the order into the MicroMachine execution path.",
      "正在把命令送入 MicroMachine 执行路径。"
    ));
  }
  var goal = commandConsoleGoal(scopedData);
  if (goal) {
    parts.push(commandUiText("작전: ", "Operation: ", "作战：") + goal);
  }
  var commandQueue = scopedData.command_queue || intervention.command_queue || compileResult.command_queue || {};
  var supersededIds = Array.isArray(commandQueue.superseded_update_ids)
    ? commandQueue.superseded_update_ids
    : [];
  var preservedIds = Array.isArray(commandQueue.preserved_update_ids)
    ? commandQueue.preserved_update_ids
    : [];
  if (supersededIds.length || commandQueue.superseded_previous) {
    parts.push(commandUiText(
      "작전 변경: 이전 명령 " + Math.max(1, supersededIds.length) + "건을 새 명령으로 교체했습니다.",
      "Order change: replaced " + Math.max(1, supersededIds.length) + " previous order(s).",
      "作战变更：已用新命令替换 " + Math.max(1, supersededIds.length) + " 条旧命令。"
    ));
  }
  if (preservedIds.length || commandQueue.standing_order_preserved) {
    parts.push(commandUiText(
      "지속 명령 유지: 기존 전략 지시 " + Math.max(1, preservedIds.length) + "건은 계속 적용됩니다.",
      "Standing orders preserved: " + Math.max(1, preservedIds.length) + " strategic directive(s) remain active.",
      "持续命令保留：" + Math.max(1, preservedIds.length) + " 条战略指令继续生效。"
    ));
  }
  if (commandQueue.merged_command_count) {
    parts.push(commandUiText(
      "명령 통합: " + commandQueue.merged_command_count + "개의 지시를 하나의 작전으로 묶었습니다.",
      "Order merge: combined " + commandQueue.merged_command_count + " directives into one operation.",
      "命令合并：已把 " + commandQueue.merged_command_count + " 条指令合并为一个作战。"
    ));
  }
  if (model.blocked) {
    var completion = (
      scopedData.battlefield_operation &&
      scopedData.battlefield_operation.operation_completion
    ) || {};
    var blocker = (
      execution.blocker_reason ||
      completion.reason ||
      compileResult.refusal_reason ||
      intervention.refusal_reason ||
      ""
    );
    var blockerManager = execution.blocker_manager || "";
    if (blocker) {
      parts.push(
        commandUiText("차단 원인: ", "Blocker: ", "阻塞原因：") +
        (blockerManager ? blockerManager + " · " : "") +
        blocker
      );
    }
  }
  return parts.join("\\n");
}

function removeMicroMachineChatPending(text, pendingId) {
  return pendingId
    ? removePendingById(pendingId)
    : removePendingForCommand(text);
}

function appendMicroMachineChatResult(text, data, pendingId) {
  var voiceSession = voiceSessionForPendingId(pendingId);
  var resultUpdateId = commandConsolePreferredUpdateId(data || {}) ||
    microMachineUpdateId(data || {});
  var resultData = commandConsoleDataForUpdate(
    data || {},
    resultUpdateId
  );
  resultData = commandConsoleDataForCanonicalOperation(
    resultData,
    resultUpdateId,
    resultData.operation_id,
    resultData.operation_generation
  );
  if (!resultData.command_console_skip_render) {
    renderActiveCommandConsole(resultData);
  }
  var removed = removeMicroMachineChatPending(text, pendingId);
  if (removed && text === latestMicroMachinePlanText) { latestMicroMachinePlanText = ""; }
  var outcomeStatus = microMachineChatOutcomeStatus(
    resultData,
    resultData.chat_outcome_status
  );
  var narration = microMachineChatNarration(resultData);
  if (voiceSession) {
    renderVoiceSessionTerminal(voiceSession, outcomeStatus, narration);
  } else {
    appendLog({
      command_text: text,
      status: outcomeStatus,
      narration: narration
    });
  }
  if (!removed) {
    updateAssistantPendingState();
  }
}

function appendMicroMachineChatFailure(text, error, pendingId) {
  var voiceSession = voiceSessionForPendingId(pendingId);
  renderActiveCommandFailure(text, error, pendingId);
  var removed = removeMicroMachineChatPending(text, pendingId);
  if (removed && text === latestMicroMachinePlanText) { latestMicroMachinePlanText = ""; }
  var narration = t("microMachineChatFailed") + ": " + error.message;
  if (voiceSession) {
    renderVoiceSessionTerminal(voiceSession, "blocked", narration);
  } else {
    appendLog({
      command_text: text,
      status: "blocked",
      narration: narration
    });
  }
  if (!removed) {
    updateAssistantPendingState();
  }
}

function safelyAppendMicroMachineChatResult(text, data, pendingId) {
  try {
    appendMicroMachineChatResult(text, data, pendingId);
  } catch (error) {
    removeMicroMachineChatPending(text, pendingId);
    updateAssistantPendingState();
    var node = document.getElementById("micromachine-status");
    if (node) {
      node.textContent = t("microMachineFailed") + ": chat render failed: " + error.message;
    }
    if (typeof console !== "undefined" && console.warn) {
      console.warn("MicroMachine chat render failed", error);
    }
  }
}

function safelyAppendMicroMachineChatFailure(text, error, pendingId) {
  try {
    appendMicroMachineChatFailure(text, error, pendingId);
  } catch (renderError) {
    removeMicroMachineChatPending(text, pendingId);
    updateAssistantPendingState();
    var node = document.getElementById("micromachine-status");
    if (node) {
      node.textContent = t("microMachineFailed") + ": chat failure render failed: " + renderError.message;
    }
    if (typeof console !== "undefined" && console.warn) {
      console.warn("MicroMachine failure chat render failed", renderError);
    }
  }
}

function markMicroMachineSubmitDelayed(text, pendingId) {
  var removed = removeMicroMachineChatPending(text, pendingId);
  if (removed && text === latestMicroMachinePlanText) {
    latestMicroMachinePlanText = "";
  }
  if (
    activeCommandConsoleRecord.pendingId &&
    pendingId &&
    activeCommandConsoleRecord.pendingId !== pendingId
  ) {
    return;
  }
  activeCommandConsoleRecord.submissionDelayed = true;
  Object.keys(operationRecords).forEach(function(key) {
    var record = operationRecords[key];
    if (!record || record.pendingId !== pendingId) { return; }
    record.data = Object.assign({}, record.data || {}, {
      command_text: text,
      command_console_submission_delayed: true
    });
  });
  renderOperationRecords();
  renderActiveCommandConsole(Object.assign(
    {},
    activeCommandConsoleRecord.data || {},
    {
      command_text: text,
      command_console_submission_delayed: true
    }
  ), true);
  var statusNode = document.getElementById("micromachine-status");
  if (statusNode) {
    statusNode.textContent = commandUiText(
      "MicroMachine 게이트웨이 응답이 지연되고 있습니다. 실패로 확정하지 않고 계속 추적합니다.",
      "The MicroMachine gateway response is delayed. Tracking continues without marking failure.",
      "MicroMachine 网关响应延迟。系统会继续跟踪，不会判定失败。"
    );
  }
}

function microMachineSubmitContextIsCurrent(contextGeneration, blackboardDirectory) {
  var input = document.getElementById("micromachine-blackboard-dir");
  var currentDirectory = input
    ? String(input.value || "").trim()
    : String(microMachinePollBlackboardDir || "").trim();
  return (
    contextGeneration === microMachineBlackboardContextGeneration &&
    blackboardDirectory === currentDirectory
  );
}

function discardStaleMicroMachineSubmit(text, pendingId) {
  var removed = removeMicroMachineChatPending(text, pendingId);
  if (removed && text === latestMicroMachinePlanText) {
    latestMicroMachinePlanText = "";
  }
  updateAssistantPendingState();
}

function submitMicroMachineModulation(payload, options) {
  options = options || {};
  var statusNode = document.getElementById("micromachine-status");
  if (statusNode) { statusNode.textContent = t("microMachineSending"); }
  var contextGeneration = microMachineBlackboardContextGeneration;
  var blackboardDirectory = String(payload.blackboard_dir || "").trim();
  var operationId = String(options.operationId || options.pendingId || "");
  if (!payload.update_id) {
    payload.update_id = newMicroMachineClientUpdateId();
  }
  if (!operationId) { operationId = String(payload.update_id || ""); }
  payload.operation_id = operationId;
  payload.operation_generation = microMachineBlackboardContextGeneration + 1;
  bindOperationRecordUpdate(
    payload.text || "",
    operationId,
    "",
    String(payload.update_id || "")
  );
  if (
    activeCommandConsoleRecord.pendingId === operationId ||
    !activeCommandConsoleRecord.updateId
  ) {
    bindActiveCommandConsoleUpdate(
      payload.text || "",
      operationId,
      "",
      String(payload.update_id || "")
    );
  }
  var timeoutId = null;
  if (
    (options.appendChat || operationId) &&
    options.timeoutMs !== 0 &&
    window.setTimeout
  ) {
    timeoutId = window.setTimeout(function () {
      if (!microMachineSubmitContextIsCurrent(contextGeneration, blackboardDirectory)) {
        discardStaleMicroMachineSubmit(payload.text || "", options.pendingId);
        return;
      }
      markMicroMachineSubmitDelayed(
        payload.text || "",
        operationId || options.pendingId
      );
    }, options.timeoutMs || MICROMACHINE_CHAT_TIMEOUT_MS);
  }
  function clearSubmitTimeout() {
    if (timeoutId !== null && window.clearTimeout) {
      window.clearTimeout(timeoutId);
    }
    timeoutId = null;
  }
  return fetch("/api/micromachine/modulate" + authQuery, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(parseJsonResponse)
    .then(function (data) {
      clearSubmitTimeout();
      if (!microMachineSubmitContextIsCurrent(contextGeneration, blackboardDirectory)) {
        discardStaleMicroMachineSubmit(payload.text || "", options.pendingId);
        return data;
      }
      var responseOwnsActiveConsole = Boolean(
        !operationId ||
        activeCommandConsoleRecord.pendingId === operationId
      );
      var responseUpdateId = String(
        data && data.update_id ||
        microMachineUpdateId(data || {}) ||
        ""
      );
      if (responseUpdateId) {
        bindOperationRecordUpdate(
          payload.text || "",
          operationId,
          microMachineScopeId(data || {}),
          responseUpdateId
        );
        if (responseOwnsActiveConsole) {
          bindActiveCommandConsoleUpdate(
            payload.text || "",
            operationId,
            microMachineScopeId(data || {}),
            responseUpdateId
          );
        }
      }
      if (data && data.async_publish) {
        rememberPendingMicroMachineAsync(
          payload.text || "",
          data,
          options.pendingId,
          responseOwnsActiveConsole
        );
      }
      announceAcceptedTacticalPlan(data, "direct");
      if (options.appendChat && !(data && data.async_publish)) {
        safelyAppendMicroMachineChatResult(
          payload.text || "",
          Object.assign({}, data, {
            command_console_skip_render: !responseOwnsActiveConsole
          }),
          options.pendingId
        );
      }
      if (responseOwnsActiveConsole) {
        safeRenderMicroMachineStatus(data);
      } else {
        renderOperationConsole(data);
      }
      if (options.clearInput && data.ok && responseOwnsActiveConsole) {
        options.clearInput.value = "";
      }
      return data;
    })
    .catch(function (error) {
      clearSubmitTimeout();
      if (!microMachineSubmitContextIsCurrent(contextGeneration, blackboardDirectory)) {
        discardStaleMicroMachineSubmit(payload.text || "", options.pendingId);
        throw error;
      }
      var failureOwnsActiveConsole = Boolean(
        !operationId ||
        activeCommandConsoleRecord.pendingId === operationId
      );
      renderOperationFailure(
        payload.text || "",
        error,
        operationId || options.pendingId
      );
      if (statusNode && failureOwnsActiveConsole) {
        statusNode.textContent = t("microMachineFailed") + ": " + error.message;
      }
      if (options.appendChat) {
        safelyAppendMicroMachineChatFailure(
          payload.text || "",
          error,
          options.pendingId
        );
      } else if (failureOwnsActiveConsole) {
        renderActiveCommandFailure(
          payload.text || "",
          error,
          operationId
        );
      }
      throw error;
    });
}

var microMachineForm = document.getElementById("micromachine-form");
if (microMachineForm) {
  microMachineForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var commandInput = document.getElementById("micromachine-command-input");
    var text = commandInput.value.trim();
    if (!text) { return; }
    synchronizeMicroMachineBlackboardDirectory(
      optionalMicroMachineField("micromachine-blackboard-dir")
    );
    microMachineSubmissionSeq += 1;
    var directOperationId = "direct-submit-" + microMachineSubmissionSeq;
    beginActiveCommandConsole(text, directOperationId);
    submitMicroMachineModulation(
      buildMicroMachineModulationPayload(text),
      {
        clearInput: commandInput,
        operationId: directOperationId
      }
    ).catch(function () {});
  });
}

function submitCommanderText(text, options) {
  options = options || {};
  var input = options.input || document.getElementById("command-input");
  var normalizedText = String(text || "").trim();
  var voiceSession = options.voiceSession || null;
  if (!normalizedText) { return ""; }
  if (voiceSession) {
    voiceSession.finalText = normalizedText;
    voiceSession.interimText = "";
  }
  setCommandMode(selectedCommandMode());
  if (isMicroMachineCommandMode()) {
    synchronizeMicroMachineBlackboardDirectory(
      optionalMicroMachineField("micromachine-blackboard-dir")
    );
    if (voiceSession && !voiceSessionContextIsCurrent(voiceSession)) {
      return "";
    }
    var pendingId = appendMicroMachinePendingPlan(
      normalizedText,
      voiceSession
    );
    var microPayload = buildMicroMachineModulationPayload(normalizedText);
    submitMicroMachineModulation(
      microPayload,
      {
        appendChat: true,
        pendingId: pendingId,
        timeoutMs: MICROMACHINE_CHAT_TIMEOUT_MS
      }
    ).catch(function () {});
    if (input) {
      input.value = "";
      if (!options.preserveFocus && typeof input.focus === "function") {
        input.focus();
      }
    }
    return pendingId;
  }
  if (!llmConfigured) {
    setLlmStatus("missing", "llmRequiredLabel", t("commandRejected"));
    if (voiceSession) {
      failVoiceSession(voiceSession, t("commandRejected"));
    }
    return "";
  }
  var legacyPendingId = appendPendingCommand(normalizedText, voiceSession);
  fetch("/api/command" + authQuery, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: normalizedText,
      request_id: legacyPendingId,
      operation_id: legacyPendingId
    })
  }).then(function (response) {
    return response.text().then(function (text) {
      var data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (error) {
        data = {};
      }
      if (!response.ok) {
        throw new Error(
          String(data.error || ("HTTP " + String(response.status || 0)))
        );
      }
      return data;
    });
  }).then(function () {
    pollHistory();
  }).catch(function (error) {
    var failedVoiceSession = voiceSessionForPendingId(legacyPendingId);
    var failureMessage = error && error.message
      ? error.message
      : t("voiceTranscriptUnavailable");
    removePendingById(legacyPendingId);
    if (failedVoiceSession) {
      renderVoiceSessionTerminal(
        failedVoiceSession,
        "blocked",
        failureMessage
      );
    } else {
      appendLog({
        request_id: legacyPendingId,
        command_text: normalizedText,
        status: "blocked",
        narration: failureMessage
      });
    }
  });
  if (input) {
    input.value = "";
    if (!options.preserveFocus && typeof input.focus === "function") {
      input.focus();
    }
  }
  return legacyPendingId;
}

function setupVoiceInput() {
  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var voiceButton = document.getElementById("voice-button");
  setVoiceButtonRecordingState(false);
  if (!SpeechRecognition) {
    voiceButton.addEventListener("click", function () {
      setLlmStatus("failed", "llmFailedLabel", t("voiceUnsupported"));
    });
    return;
  }
  function createVoiceRecognition() {
    var instance = new SpeechRecognition();
    instance.lang = currentLang === "en"
      ? "en-US"
      : (currentLang === "zh" ? "zh-CN" : "ko-KR");
    instance.interimResults = true;
    instance.continuous = false;
    instance.onstart = function () {
      handleVoiceRecognitionStart(instance);
    };
    return instance;
  }
  function retireVoiceRecognition(instance) {
    if (recognition === instance) {
      recognition = createVoiceRecognition();
    }
  }
  function abortDetachedVoiceRecognition(instance) {
    instance.onend = function () {};
    instance.onerror = function () {};
    instance.onresult = function () {};
    if (typeof instance.abort === "function") {
      instance.abort();
    } else if (typeof instance.stop === "function") {
      instance.stop();
    }
  }
  function handleVoiceRecognitionStart(instance) {
    var startRequest = pendingVoiceRecognitionRequest;
    if (
      recognition !== instance ||
      !startRequest ||
      startRequest.recognitionInstance !== instance
    ) {
      abortDetachedVoiceRecognition(instance);
      return;
    }
    pendingVoiceRecognitionRequest = null;
    if (
      startRequest.requestId !== voiceRecognitionRequestSeq ||
      startRequest.contextGeneration !==
        microMachineBlackboardContextGeneration ||
      startRequest.blackboardDirectory !==
        currentEventBlackboardDirectory()
    ) {
      isRecording = false;
      setVoiceButtonRecordingState(false);
      retireVoiceRecognition(instance);
      abortDetachedVoiceRecognition(instance);
      return;
    }
    if (
      activeVoiceSession &&
      !activeVoiceSession.submitted &&
      !activeVoiceSession.error &&
      activeVoiceSession.state === "finalizing" &&
      voiceSessionContextIsCurrent(activeVoiceSession)
    ) {
      if (typeof instance.stop === "function") {
        instance.stop();
      }
      return;
    }
    isRecording = true;
    setVoiceButtonRecordingState(true);
    var session = appendVoiceRecordingBubble();
    session.recognitionInstance = instance;
    function voiceRecognitionOwnsSession() {
      return Boolean(
        session &&
        session.recognitionInstance === instance &&
        activeVoiceSession === session &&
        !session.invalidated
      );
    }
    instance.onend = function () {
      if (!voiceRecognitionOwnsSession()) { return; }
      isRecording = false;
      setVoiceButtonRecordingState(false);
      retireVoiceRecognition(instance);
      if (
        !session ||
        session.error ||
        session.submitted ||
        !voiceSessionContextIsCurrent(session)
      ) {
        return;
      }
      clearVoiceFinalizationTimer(session);
      session.state = "finalizing";
      renderVoiceSession(session);
      session.finalizationTimerId = window.setTimeout(function () {
        session.finalizationTimerId = null;
        if (
          session.error ||
          session.submitted ||
          !voiceSessionContextIsCurrent(session)
        ) {
          return;
        }
        var fallbackText = String(
          session.finalText || session.interimText || ""
        ).trim();
        if (!fallbackText) {
          failVoiceSession(session, t("voiceNoResult"));
          return;
        }
        submitVoiceSession(session, fallbackText);
      }, VOICE_FINALIZATION_GRACE_MS);
    };
    instance.onerror = function () {
      if (!voiceRecognitionOwnsSession()) { return; }
      isRecording = false;
      setVoiceButtonRecordingState(false);
      retireVoiceRecognition(instance);
      clearVoiceFinalizationTimer(session);
      if (!voiceSessionContextIsCurrent(session)) { return; }
      setLlmStatus("failed", "llmFailedLabel", t("voiceNoResult"));
      failVoiceSession(session, t("voiceTranscriptUnavailable"));
    };
    instance.onresult = function (event) {
      if (
        !voiceRecognitionOwnsSession() ||
        !session ||
        session.error ||
        session.submitted ||
        !voiceSessionContextIsCurrent(session)
      ) {
        return;
      }
      for (var i = 0; i < event.results.length; i += 1) {
        session.segments[i] = {
          text: String(event.results[i][0].transcript || ""),
          final: event.results[i].isFinal === true
        };
      }
      var finalParts = [];
      var interimParts = [];
      session.segments.forEach(function(segment) {
        if (!segment || !segment.text) { return; }
        if (segment.final) {
          finalParts.push(segment.text);
        } else {
          interimParts.push(segment.text);
        }
      });
      session.finalText = finalParts.join(" ").trim();
      session.interimText = interimParts.join(" ").trim();
      var visibleText = String(
        session.finalText || session.interimText || ""
      ).trim();
      document.getElementById("command-input").value = visibleText;
      renderVoiceSession(session);
      var finalResult = event.results[event.results.length - 1];
      if (finalResult && finalResult.isFinal && session.finalText) {
        clearVoiceFinalizationTimer(session);
        submitVoiceSession(session, session.finalText);
      }
    };
  }
  recognition = createVoiceRecognition();
  voiceButton.addEventListener("click", function () {
    if (isRecording) {
      recognition.stop();
      return;
    }
    if (pendingVoiceRecognitionRequest) {
      return;
    }
    if (
      activeVoiceSession &&
      !activeVoiceSession.submitted &&
      !activeVoiceSession.error &&
      activeVoiceSession.state === "finalizing" &&
      voiceSessionContextIsCurrent(activeVoiceSession)
    ) {
      return;
    }
    var recognitionInstance = recognition;
    recognitionInstance.lang = currentLang === "en"
      ? "en-US"
      : (currentLang === "zh" ? "zh-CN" : "ko-KR");
    voiceRecognitionRequestSeq += 1;
    pendingVoiceRecognitionRequest = {
      requestId: voiceRecognitionRequestSeq,
      contextGeneration: microMachineBlackboardContextGeneration,
      blackboardDirectory: currentEventBlackboardDirectory(),
      recognitionInstance: recognitionInstance
    };
    var requestedRecognition = pendingVoiceRecognitionRequest;
    recognitionInstance.onerror = function (error) {
      if (pendingVoiceRecognitionRequest !== requestedRecognition) {
        return;
      }
      pendingVoiceRecognitionRequest = null;
      isRecording = false;
      setVoiceButtonRecordingState(false);
      retireVoiceRecognition(recognitionInstance);
      setLlmStatus(
        "failed",
        "llmFailedLabel",
        error && (error.message || error.error)
          ? String(error.message || error.error)
          : t("voiceNoResult")
      );
    };
    recognitionInstance.onend = function () {
      if (pendingVoiceRecognitionRequest !== requestedRecognition) {
        return;
      }
      pendingVoiceRecognitionRequest = null;
      isRecording = false;
      setVoiceButtonRecordingState(false);
      retireVoiceRecognition(recognitionInstance);
    };
    try {
      recognitionInstance.start();
    } catch (error) {
      if (pendingVoiceRecognitionRequest === requestedRecognition) {
        pendingVoiceRecognitionRequest = null;
      }
      isRecording = false;
      setVoiceButtonRecordingState(false);
      retireVoiceRecognition(recognitionInstance);
      setLlmStatus(
        "failed",
        "llmFailedLabel",
        error && error.message ? error.message : t("voiceNoResult")
      );
    }
  });
}

function submitVoiceSession(session, text) {
  if (!session || session.submitted || session.error) {
    return session ? session.pendingId : "";
  }
  if (!voiceSessionContextIsCurrent(session)) {
    invalidateVoiceSession(
      session,
      commandUiText(
        "MicroMachine 전장 링크가 전환되어 이 음성 명령을 제출하지 않았습니다.",
        "The MicroMachine battlefield link changed, so this voice order was not submitted.",
        "MicroMachine battlefield link changed; this voice order was not submitted."
      )
    );
    return "";
  }
  var normalizedText = String(text || "").trim();
  if (!normalizedText) {
    failVoiceSession(session, t("voiceNoResult"));
    return "";
  }
  clearVoiceFinalizationTimer(session);
  session.submitted = true;
  session.state = "finalizing";
  session.finalText = normalizedText;
  session.interimText = "";
  renderVoiceSession(session);
  return submitCommanderText(normalizedText, {
    input: document.getElementById("command-input"),
    voiceSession: session,
    preserveFocus: true
  });
}

document.getElementById("command-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var input = document.getElementById("command-input");
  var text = input.value.trim();
  if (!text) { return; }
  submitCommanderText(text, { input: input });
});

Array.prototype.forEach.call(document.querySelectorAll("[data-command]"), function (button) {
  button.addEventListener("click", function () {
    var input = document.getElementById("command-input");
    input.value = button.getAttribute("data-command") || "";
    input.focus();
  });
});

function submitCommanderControlOrder(text) {
  submitCommanderText(text);
}

var commandRefreshButton = document.getElementById("command-refresh-button");
if (commandRefreshButton) {
  commandRefreshButton.addEventListener("click", function () {
    pollMicroMachineStatus();
  });
}

var commandReviseButton = document.getElementById("command-revise-button");
if (commandReviseButton) {
  commandReviseButton.addEventListener("click", function () {
    var input = document.getElementById("command-input");
    if (!input) { return; }
    input.value = activeCommandConsoleRecord.text || "";
    input.focus();
  });
}

var commandRetreatButton = document.getElementById("command-retreat-button");
if (commandRetreatButton) {
  commandRetreatButton.addEventListener("click", function () {
    submitCommanderControlOrder(
      currentLang === "en"
        ? "Emergency: retreat all combat units now"
        : (currentLang === "zh" ? "紧急全军立即撤退" : "긴급 전군 즉시 후퇴해")
    );
  });
}

var microMachineBlackboardInput = document.getElementById("micromachine-blackboard-dir");
if (microMachineBlackboardInput) {
  microMachineBlackboardInput.addEventListener("change", function () {
    synchronizeMicroMachineBlackboardDirectory(microMachineBlackboardInput.value);
    reconnectEventChannel();
  });
}

Array.prototype.forEach.call(document.querySelectorAll("input[name='command-mode']"), function (input) {
  input.addEventListener("change", function () {
    setCommandMode(input.value);
    refreshLiveConnectionFlow();
  });
});

document.getElementById("llm-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var keyInput = document.getElementById("llm-api-key");
  var choice;
  try {
    choice = selectedLlmChoice();
  } catch (error) {
    setLlmStatus("failed", "llmFailedLabel", error.message);
    return;
  }
  var payload = {
    provider: choice.provider,
    model: choice.model,
    api_key: keyInput.value.trim()
  };
  if (!payload.api_key) {
    setLlmStatus("failed", "llmFailedLabel", t("llmEnterKey"));
    return;
  }
  llmSetupAttemptSeq += 1;
  var setupAttemptSeq = llmSetupAttemptSeq;
  activeLlmSetupAttemptSeq = setupAttemptSeq;
  setLlmStatus("setting", "llmSettingLabel", t("llmSaving"));
  fetch("/api/llm" + authQuery, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(parseJsonResponse)
    .then(function (data) {
      if (setupAttemptSeq !== activeLlmSetupAttemptSeq) { return; }
      activeLlmSetupAttemptSeq = 0;
      keyInput.value = "";
      renderLlmSettings(data);
      if (data.configured) {
        var effort = data.reasoning_effort
          ? " / effort=" + data.reasoning_effort
          : "";
        setLlmStatus(
          "success",
          "llmSuccessLabel",
          t("llmReady") + " (" + data.provider + " / " + data.model + effort + ")"
        );
        if (data.live_start) {
          handleLiveStart(data.live_start);
        } else {
          refreshLiveConnectionFlow();
        }
      }
    })
    .catch(function (error) {
      if (setupAttemptSeq !== activeLlmSetupAttemptSeq) { return; }
      activeLlmSetupAttemptSeq = 0;
      setLlmStatus("failed", "llmFailedLabel", t("llmSaveFailed") + ": " + error.message);
    });
});

Array.prototype.forEach.call(document.querySelectorAll("[data-lang-button]"), function (button) {
  button.addEventListener("click", function () {
    applyLanguage(button.getAttribute("data-lang-button") || "ko");
    pollState();
    pollLlmSettings();
  });
});

var providerOptions = document.getElementById("llm-provider-options");
providerOptions.addEventListener("click", function (event) {
  var target = event.target;
  var input = target && target.closest ? target.closest("input[name='llm-provider-choice']") : null;
  if (!input && target && target.closest) {
    var label = target.closest(".provider-option");
    input = label ? label.querySelector("input[name='llm-provider-choice']") : null;
  }
  if (input) { handleProviderChoiceChange(input.value); }
});
Array.prototype.forEach.call(document.querySelectorAll("input[name='llm-provider-choice']"), function (input) {
  input.addEventListener("change", function () { handleProviderChoiceChange(input.value); });
});

document.getElementById("live-open-button").addEventListener("click", function () {
  if (liveGuiUrl) { window.open(liveGuiUrl, "_blank", "noopener"); }
});

document.getElementById("runtime-start-button").addEventListener("click", function () {
  setCommandMode(selectedCommandMode());
  startSelectedRuntime();
});

document.getElementById("runtime-refresh-button").addEventListener("click", function () {
  refreshLiveConnectionFlow();
});

var tacticalRadioMuteButton = document.getElementById("tactical-radio-mute");
if (tacticalRadioMuteButton) {
  tacticalRadioMuteButton.addEventListener("click", function () {
    tacticalRadioSetMuted(!tacticalRadio.muted);
  });
}

applyLanguage("ko");
setLlmStatus("checking", "llmCheckingLabel", t("llmChecking"));
renderModelSelect(selectedProviderValue(), "");
setupVoiceInput();
renderTacticalRadioState();
pollLlmSettings();
refreshLiveConnectionFlow();
connectEventChannel();
</script>
</body>
</html>
"""
"""Embedded single-page Korean UI template (no external CDN)."""


def render_web_gui_page(micromachine_blackboard_dir: str = "") -> str:
    """Render the embedded single-page Korean web GUI HTML."""

    blackboard_dir = html.escape(
        _clean_blackboard_dir(
            micromachine_blackboard_dir,
            _default_micromachine_blackboard_dir(),
        ),
        quote=True,
    )
    return (
        _WEB_GUI_PAGE_TEMPLATE
        .replace("__TITLE__", WEB_GUI_PAGE_TITLE)
        .replace("__POLL_MS__", str(WEB_GUI_POLL_INTERVAL_MS))
        .replace("__COMMAND_MODE_MICROMACHINE__", COMMAND_MODE_MICROMACHINE)
        .replace("__COMMAND_MODE_LEGACY_COMMANDER__", COMMAND_MODE_LEGACY_COMMANDER)
        .replace("__COLOR_EXECUTED__", WEB_GUI_STATUS_COLORS["executed"])
        .replace("__COLOR_PARTIAL__", WEB_GUI_STATUS_COLORS["partially_executed"])
        .replace("__COLOR_BLOCKED__", WEB_GUI_STATUS_COLORS["blocked"])
        .replace("__COLOR_CLARIFICATION__", WEB_GUI_STATUS_COLORS["clarification"])
        .replace("__COLOR_READ_ONLY__", WEB_GUI_STATUS_COLORS["read_only"])
        .replace("__MICROMACHINE_BLACKBOARD_DIR__", blackboard_dir)
    )


class _BridgedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the web GUI bridge for its handlers."""

    daemon_threads = True
    _OPERATION_EVENT_SCOPE_RETENTION = 8
    _OPERATION_EVENT_SCOPE_HISTORY_RETENTION = 128

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        bridge: WebGuiBridgeInterface,
        auth_token: str = "",
        event_journal: _WebEventJournal | None = None,
    ) -> None:
        self.bridge = bridge
        self.auth_token = auth_token
        self.event_journal = event_journal or _WebEventJournal()
        self._event_source_lock = threading.RLock()
        self._operation_status_locks_guard = threading.Lock()
        self._operation_status_locks: WeakValueDictionary[
            str,
            threading.Lock,
        ] = WeakValueDictionary()
        self._observed_history_seq = 0
        self._observed_payload_hashes: dict[str, str] = {}
        self._observed_payload_identities: dict[
            str,
            tuple[str, int, int],
        ] = {}
        self._observed_payload_snapshots: dict[str, dict[str, object]] = {}
        self._observed_operation_event_seq: dict[str, int] = {}
        self._observed_operation_scope_order: deque[str] = deque()
        self._observed_operation_event_high_water: dict[str, int] = {}
        self._observed_operation_event_history_order: deque[str] = deque()
        self._pending_operation_event_seqs: dict[str, set[int]] = {}
        self._failed_event_sources: set[str] = set()
        self.shutdown_event = threading.Event()
        super().__init__(server_address, handler_class)

    def operation_status_lock(
        self,
        blackboard_dir: str,
    ) -> threading.Lock:
        """Return one weakly retained source coordinator lock per blackboard."""

        key = os.path.realpath(os.path.abspath(blackboard_dir))
        with self._operation_status_locks_guard:
            return self._operation_status_locks.setdefault(
                key,
                threading.Lock(),
            )

    def publish_event(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        update_id: str = "",
        operation_id: str = "",
        generation: int = 0,
        game_frame: int = -1,
        blackboard_dir: str = "",
        blackboard_scope_id: str = "",
    ) -> dict[str, object]:
        with self._event_source_lock:
            scope_id = _web_event_blackboard_scope_id(
                payload,
                blackboard_dir=blackboard_dir,
                blackboard_scope_id=blackboard_scope_id,
            )
            return self.event_journal.publish(
                event_type,
                payload,
                update_id=update_id,
                operation_id=operation_id,
                generation=generation,
                game_frame=game_frame,
                blackboard_scope_id=scope_id,
            )

    def publish_changed_snapshot(
        self,
        cache_key: str,
        event_type: str,
        payload: Mapping[str, object],
        *,
        blackboard_dir: str = "",
        blackboard_scope_id: str = "",
    ) -> bool:
        with self._event_source_lock:
            safe_payload = _redact_json_ready(payload)
            serialized = json.dumps(
                safe_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            if self._observed_payload_hashes.get(cache_key) == digest:
                return False
            identity = _web_snapshot_order_identity(payload)
            previous_identity = self._observed_payload_identities.get(
                cache_key
            )
            if (
                identity is not None
                and previous_identity is not None
                and _web_snapshot_identity_regresses(
                    previous_identity,
                    identity,
                )
            ):
                return False
            self._observed_payload_hashes[cache_key] = digest
            if identity is not None:
                self._observed_payload_identities[cache_key] = identity
            if isinstance(safe_payload, Mapping):
                self._observed_payload_snapshots[cache_key] = deepcopy(
                    dict(safe_payload)
                )
            update_id, operation_id, generation, game_frame = (
                _web_event_identity(payload)
            )
            self.publish_event(
                event_type,
                payload,
                update_id=update_id,
                operation_id=operation_id,
                generation=generation,
                game_frame=game_frame,
                blackboard_dir=blackboard_dir,
                blackboard_scope_id=blackboard_scope_id,
            )
            return True

    def admit_operation_event_scope(self, scope_id: str) -> bool:
        """Admit one replay cursor without allowing unbounded scope growth."""

        normalized_scope = str(scope_id or "")
        if not normalized_scope:
            return False
        if normalized_scope in self._observed_operation_event_high_water:
            try:
                self._observed_operation_event_history_order.remove(
                    normalized_scope
                )
            except ValueError:
                pass
            self._observed_operation_event_history_order.append(
                normalized_scope
            )
            return True
        while (
            len(self._observed_operation_event_high_water)
            >= self._OPERATION_EVENT_SCOPE_HISTORY_RETENTION
        ):
            evicted = self._observed_operation_event_history_order.popleft()
            if evicted in self._observed_operation_event_seq:
                self._observed_operation_event_history_order.append(evicted)
                continue
            self._observed_operation_event_high_water.pop(evicted, None)
            self._pending_operation_event_seqs.pop(evicted, None)
        self._observed_operation_event_high_water[normalized_scope] = 0
        self._observed_operation_event_history_order.append(normalized_scope)
        return True

    def remember_operation_event_high_water(
        self,
        scope_id: str,
        timeline_seq: int,
    ) -> bool:
        """Retain a bounded replay cursor or fail closed for a novel scope."""

        normalized_scope = str(scope_id or "")
        normalized_seq = max(0, int(timeline_seq))
        if not self.admit_operation_event_scope(normalized_scope):
            return False
        self._observed_operation_event_high_water[normalized_scope] = max(
            self._observed_operation_event_high_water.get(
                normalized_scope,
                0,
            ),
            normalized_seq,
        )
        return True

    def operation_event_source_cursor(
        self,
        scope_id: str,
    ) -> tuple[bool, int]:
        """Return whether one scope has a materialized source cursor."""

        normalized_scope = str(scope_id or "")
        if not normalized_scope:
            return False, 0
        with self._event_source_lock:
            if normalized_scope in self._observed_operation_event_seq:
                return (
                    True,
                    int(
                        self._observed_operation_event_seq[
                            normalized_scope
                        ]
                    ),
                )
            if normalized_scope in self._observed_operation_event_high_water:
                return (
                    True,
                    int(
                        self._observed_operation_event_high_water[
                            normalized_scope
                        ]
                    ),
                )
            return False, 0

    def authoritative_snapshot_payload(
        self,
        cache_key: str,
        payload: Mapping[str, object],
    ) -> tuple[bool, dict[str, object]]:
        """Accept one source snapshot or return the last accepted payload."""

        safe_payload = _redact_json_ready(payload)
        if not isinstance(safe_payload, Mapping):
            safe_payload = {"value": safe_payload}
        normalized_payload = dict(safe_payload)
        serialized = json.dumps(
            normalized_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        incoming = _web_snapshot_order_identity(payload)
        with self._event_source_lock:
            previous = self._observed_payload_identities.get(cache_key)
            if (
                incoming is not None
                and previous is not None
                and _web_snapshot_identity_regresses(previous, incoming)
            ):
                accepted = self._observed_payload_snapshots.get(cache_key)
                if accepted is not None:
                    return False, deepcopy(accepted)
                return (
                    False,
                    {
                        "enabled": False,
                        "status": "stale_source_rejected",
                    },
                )
            self._observed_payload_hashes[cache_key] = digest
            if incoming is not None:
                self._observed_payload_identities[cache_key] = incoming
            self._observed_payload_snapshots[cache_key] = deepcopy(
                normalized_payload
            )
            return True, deepcopy(normalized_payload)

    def remember_pending_operation_event(
        self,
        scope_id: str,
        event_seq: int,
    ) -> None:
        """Retain lifecycle events until one SSE write succeeds."""

        normalized_scope = str(scope_id or "")
        normalized_seq = max(0, int(event_seq))
        if not normalized_scope or normalized_seq <= 0:
            return
        with self._event_source_lock:
            self._pending_operation_event_seqs.setdefault(
                normalized_scope,
                set(),
            ).add(normalized_seq)

    def operation_event_snapshot_cursor(
        self,
        scope_id: str,
        *,
        latest_seq: int,
        oldest_seq: int,
    ) -> int:
        """Keep undelivered lifecycle events newer than the snapshot cursor."""

        normalized_scope = str(scope_id or "")
        normalized_latest = max(0, int(latest_seq))
        normalized_oldest = max(1, int(oldest_seq))
        with self._event_source_lock:
            pending = self._pending_operation_event_seqs.get(
                normalized_scope
            )
            if not pending:
                return normalized_latest
            pending.intersection_update(
                {
                    seq
                    for seq in pending
                    if seq >= normalized_oldest
                }
            )
            if not pending:
                self._pending_operation_event_seqs.pop(
                    normalized_scope,
                    None,
                )
                return normalized_latest
            return min(normalized_latest, min(pending) - 1)

    def mark_operation_event_delivered(
        self,
        scope_id: str,
        event_seq: int,
    ) -> None:
        """A successful SSE event write satisfies the pending replay."""

        normalized_scope = str(scope_id or "")
        normalized_seq = max(0, int(event_seq))
        with self._event_source_lock:
            pending = self._pending_operation_event_seqs.get(
                normalized_scope
            )
            if not pending:
                return
            pending.discard(normalized_seq)
            if not pending:
                self._pending_operation_event_seqs.pop(
                    normalized_scope,
                    None,
                )

    def touch_operation_event_scope(self, scope_id: str) -> None:
        """Bound per-blackboard event cursors and their related caches."""

        with self._event_source_lock:
            try:
                self._observed_operation_scope_order.remove(scope_id)
            except ValueError:
                pass
            self._observed_operation_scope_order.append(scope_id)
            while (
                len(self._observed_operation_scope_order)
                > self._OPERATION_EVENT_SCOPE_RETENTION
            ):
                evicted = self._observed_operation_scope_order.popleft()
                evicted_seq = self._observed_operation_event_seq.pop(
                    evicted,
                    0,
                )
                self.remember_operation_event_high_water(
                    evicted,
                    evicted_seq,
                )
                self._observed_payload_hashes.pop(
                    f"micromachine:{evicted}",
                    None,
                )
                self._observed_payload_identities.pop(
                    f"micromachine:{evicted}",
                    None,
                )
                self._observed_payload_snapshots.pop(
                    f"micromachine:{evicted}",
                    None,
                )
                source_key = f"micromachine_status:{evicted}"
                self._observed_payload_hashes.pop(
                    f"source:{source_key}",
                    None,
                )
                self._observed_payload_identities.pop(
                    f"source:{source_key}",
                    None,
                )
                self._observed_payload_snapshots.pop(
                    f"source:{source_key}",
                    None,
                )
                self._failed_event_sources.discard(source_key)

    def begin_shutdown(self) -> None:
        """Signal active streams and wake their journal waits."""

        self.shutdown_event.set()
        self.event_journal.wake_waiters()

    def publish_source_error(
        self,
        source_key: str,
        source: str,
        payload: Mapping[str, object],
        *,
        blackboard_dir: str = "",
    ) -> None:
        """Publish one deduplicated source failure until recovery is observed."""

        with self._event_source_lock:
            self._failed_event_sources.add(source_key)
            self.publish_changed_snapshot(
                f"source:{source_key}",
                "source_error",
                {"source": source, **dict(payload)},
                blackboard_dir=blackboard_dir,
            )

    def publish_source_recovered(
        self,
        source_key: str,
        source: str,
        *,
        blackboard_dir: str = "",
    ) -> None:
        """Publish recovery once and allow the same later failure to reappear."""

        with self._event_source_lock:
            if source_key not in self._failed_event_sources:
                return
            self._failed_event_sources.remove(source_key)
            self._observed_payload_hashes.pop(f"source:{source_key}", None)
            self.publish_event(
                "source_recovered",
                {
                    "source": source,
                    "blackboard_dir": blackboard_dir,
                },
                blackboard_dir=blackboard_dir,
            )


class _WebGuiRequestHandler(BaseHTTPRequestHandler):
    """Quiet request handler for the local commander web GUI."""

    server_version = "voiStarcraft2WebGui/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def _bridge(self) -> WebGuiBridgeInterface:
        return self.server.bridge  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence per-request stderr logging (the GUI is a local cockpit)."""

        return None

    def do_GET(self) -> None:  # noqa: N802 - http.server contract.
        if not self._authorized():
            self._send_unauthorized()
            return
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            blackboard_dir = ""
            default_blackboard_dir = getattr(
                self._bridge,
                "micromachine_blackboard_dir",
                None,
            )
            if callable(default_blackboard_dir):
                blackboard_dir = str(default_blackboard_dir())
            self._send_html(
                HTTPStatus.OK,
                render_web_gui_page(blackboard_dir),
            )
            return
        if path == "/api/state":
            self._handle_state()
            return
        if path == "/api/history":
            self._handle_history()
            return
        if path == "/api/events":
            self._handle_events()
            return
        if path == "/api/llm":
            self._handle_llm_status()
            return
        if path == "/api/live/status":
            self._handle_live_status()
            return
        if path == "/api/runtime/status":
            self._handle_runtime_status()
            return
        if path == "/api/micromachine/status":
            self._handle_micromachine_status()
            return
        self._send_not_found()

    def do_POST(self) -> None:  # noqa: N802 - http.server contract.
        if not self._authorized():
            self._read_request_body()
            self._send_unauthorized()
            return
        path = urlsplit(self.path).path
        if path == "/api/command":
            self._handle_command()
            return
        if path == "/api/llm":
            self._handle_llm_configure()
            return
        if path == "/api/runtime/start":
            self._handle_runtime_start()
            return
        if path == "/api/micromachine/modulate":
            self._handle_micromachine_modulate()
            return
        # Drain any request body so a keep-alive connection stays usable.
        self._read_request_body()
        self._send_not_found()

    def _handle_state(self) -> None:
        try:
            snapshot = self._bridge.state_snapshot()
        except Exception as error:  # noqa: BLE001 - surfaced honestly as 500.
            self._send_internal_error(error)
            return
        if snapshot is None:
            self._send_json(HTTPStatus.OK, {"available": False})
            return
        payload: dict[str, object] = {"available": True}
        payload.update(dict(snapshot))
        self._send_json(HTTPStatus.OK, payload)

    def _handle_history(self) -> None:
        params = parse_qs(urlsplit(self.path).query)
        after_raw = (params.get("after", ["0"])[0] or "0").strip() or "0"
        try:
            after = int(after_raw)
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        f"after 파라미터는 정수여야 합니다 (받은 값: {after_raw!r}). "
                        "마지막으로 받은 latest 값을 그대로 전달해 주세요."
                    )
                },
            )
            return
        try:
            # latest first, events second: a concurrently recorded event then
            # shows up in events with seq > latest and the max() below keeps
            # the reported latest honest, so pollers never skip an event.
            latest = int(self._bridge.latest_seq())
            events = [dict(event) for event in self._bridge.history_since(after)]
        except Exception as error:  # noqa: BLE001 - surfaced honestly as 500.
            self._send_internal_error(error)
            return
        for event in events:
            seq_value = event.get("seq")
            if isinstance(seq_value, int) and seq_value > latest:
                latest = seq_value
        self._send_json(HTTPStatus.OK, {"events": events, "latest": latest})

    def _handle_events(self) -> None:
        params = parse_qs(urlsplit(self.path).query)
        after_raw = (
            self.headers.get("Last-Event-ID", "")
            or params.get("after", ["0"])[0]
            or "0"
        ).strip()
        try:
            after = max(0, int(after_raw))
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Last-Event-ID 또는 after 파라미터는 정수여야 합니다."},
            )
            return
        blackboard_dir = self._resolved_micromachine_blackboard_dir(
            params.get("blackboard_dir", [""])[0] or ""
        )
        blackboard_scope_id = _micromachine_blackboard_scope_id(
            blackboard_dir
        )
        once = (params.get("once", [""])[0] or "").lower() in {
            "1",
            "true",
            "yes",
        }
        journal = self.server.event_journal  # type: ignore[attr-defined]
        self.send_response(int(HTTPStatus.OK))
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        cursor = after
        try:
            replay_available, replay_events = journal.replay_batch(after)
            if after == 0 or not replay_available:
                while True:
                    cursor = self._write_authoritative_sse_snapshot(
                        journal,
                        blackboard_dir,
                        blackboard_scope_id,
                    )
                    replay_available, replay_events = journal.replay_batch(
                        cursor
                    )
                    if replay_available:
                        cursor = self._write_visible_sse_events(
                            replay_events,
                            cursor=cursor,
                            blackboard_scope_id=blackboard_scope_id,
                        )
                        break
            else:
                cursor = self._write_visible_sse_events(
                    replay_events,
                    cursor=cursor,
                    blackboard_scope_id=blackboard_scope_id,
                )
            self._write_sse_heartbeat()
            if once:
                return
            server = self.server  # type: ignore[assignment]
            while not server.shutdown_event.is_set():  # type: ignore[attr-defined]
                self._refresh_event_sources(blackboard_dir)
                replay_available, events = journal.wait_for_replay_batch(
                    cursor,
                    WEB_GUI_SSE_REFRESH_SECONDS,
                )
                if not replay_available:
                    while not server.shutdown_event.is_set():  # type: ignore[attr-defined]
                        cursor = self._write_authoritative_sse_snapshot(
                            journal,
                            blackboard_dir,
                            blackboard_scope_id,
                        )
                        replay_available, events = journal.replay_batch(
                            cursor
                        )
                        if replay_available:
                            cursor = self._write_visible_sse_events(
                                events,
                                cursor=cursor,
                                blackboard_scope_id=blackboard_scope_id,
                            )
                            break
                    continue
                if events:
                    cursor = self._write_visible_sse_events(
                        events,
                        cursor=cursor,
                        blackboard_scope_id=blackboard_scope_id,
                    )
                    continue
                if (
                    time.monotonic()
                    - getattr(self, "_last_sse_write_monotonic", 0.0)
                    >= WEB_GUI_SSE_HEARTBEAT_SECONDS
                ):
                    self._write_sse_heartbeat()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return

    def _write_authoritative_sse_snapshot(
        self,
        journal: _WebEventJournal,
        blackboard_dir: str,
        blackboard_scope_id: str,
    ) -> int:
        server = self.server  # type: ignore[assignment]
        # Direct journal publications after this cut remain replayable. A cold
        # operation source is materialized once without journal publication;
        # the second read and every warm read commit only timeline_seq values
        # newer than that scope-local source cursor.
        snapshot_cut = journal.latest_seq
        source_lock = server.operation_status_lock(  # type: ignore[attr-defined]
            blackboard_dir
        )
        with source_lock:
            status_read_succeeded = True
            try:
                micromachine_status = self._micromachine_status_payload(
                    blackboard_dir
                )
            except Exception as error:  # noqa: BLE001 - snapshot remains usable.
                status_read_succeeded = False
                micromachine_status = {
                    "enabled": False,
                    "status": "source_error",
                    "error": _redact_sensitive_text(
                        error,
                        normalize_whitespace=True,
                    ),
                }
            status_scope_id = str(
                micromachine_status.get("blackboard_scope_id")
                or blackboard_scope_id
            )
            source_materialized, _ = (
                server.operation_event_source_cursor(  # type: ignore[attr-defined]
                    status_scope_id
                )
            )
            if status_read_succeeded and not source_materialized:
                # First materialization is historical hydration. It advances
                # the source cursor but emits no lifecycle event.
                self._publish_new_operation_events(
                    micromachine_status,
                    blackboard_dir=blackboard_dir,
                    publish=True,
                )
                try:
                    candidate_status = self._micromachine_status_payload(
                        blackboard_dir
                    )
                except Exception:  # noqa: BLE001 - retain the materialized cut.
                    candidate_status = micromachine_status
                candidate_scope_id = str(
                    candidate_status.get("blackboard_scope_id")
                    or blackboard_scope_id
                )
                first_identity = _web_snapshot_order_identity(
                    micromachine_status
                )
                candidate_identity = _web_snapshot_order_identity(
                    candidate_status
                )
                if (
                    candidate_scope_id == status_scope_id
                    and not (
                        first_identity is not None
                        and candidate_identity is not None
                        and _web_snapshot_identity_regresses(
                            first_identity,
                            candidate_identity,
                        )
                    )
                ):
                    micromachine_status = candidate_status
            status_scope_id = str(
                micromachine_status.get("blackboard_scope_id")
                or blackboard_scope_id
            )
            if status_read_succeeded:
                with server._event_source_lock:  # type: ignore[attr-defined]
                    status_accepted, accepted_status = (
                        server.authoritative_snapshot_payload(  # type: ignore[attr-defined]
                            f"micromachine:{status_scope_id}",
                            micromachine_status,
                        )
                    )
                    if status_accepted:
                        self._publish_new_operation_events(
                            micromachine_status,
                            blackboard_dir=blackboard_dir,
                            publish=True,
                        )
                    micromachine_status = accepted_status
            snapshot_cursor = server.operation_event_snapshot_cursor(  # type: ignore[attr-defined]
                status_scope_id,
                latest_seq=snapshot_cut,
                oldest_seq=journal.oldest_seq,
            )
            snapshot_payload = self._authoritative_event_snapshot(
                blackboard_dir,
                micromachine_status=micromachine_status,
            )
            snapshot_event = {
                "event_seq": snapshot_cursor,
                "event_type": "snapshot",
                "created_at_unix_ms": int(time.time() * 1000),
                "update_id": "",
                "operation_id": "",
                "generation": 0,
                "game_frame": -1,
                "blackboard_scope_id": blackboard_scope_id,
                "payload": snapshot_payload,
            }
            self._write_sse_event(snapshot_event)
        return snapshot_cursor

    def _write_visible_sse_events(
        self,
        events: Sequence[Mapping[str, object]],
        *,
        cursor: int,
        blackboard_scope_id: str,
    ) -> int:
        """Advance over all journal events but emit only this subscriber's scope."""

        next_cursor = cursor
        for event in events:
            next_cursor = max(next_cursor, int(event.get("event_seq", 0)))
            event_scope_id = str(
                event.get("blackboard_scope_id", "") or ""
            )
            if event_scope_id and event_scope_id != blackboard_scope_id:
                continue
            self._write_sse_event(event)
            if str(event.get("event_type", "") or "") == "operation_event":
                server = self.server  # type: ignore[assignment]
                server.mark_operation_event_delivered(  # type: ignore[attr-defined]
                    event_scope_id or blackboard_scope_id,
                    int(event.get("event_seq", 0)),
                )
        return next_cursor

    def _resolved_micromachine_blackboard_dir(
        self,
        blackboard_dir: str,
    ) -> str:
        default_dir = _default_micromachine_blackboard_dir()
        default_fn = getattr(
            self._bridge,
            "micromachine_blackboard_dir",
            None,
        )
        if callable(default_fn):
            default_dir = str(default_fn())
        return _clean_blackboard_dir(blackboard_dir, default_dir)

    def _authoritative_event_snapshot(
        self,
        blackboard_dir: str,
        *,
        micromachine_status: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            snapshot = self._state_payload()
        except Exception as error:  # noqa: BLE001 - snapshot remains usable.
            snapshot = {
                "available": False,
                "error": _redact_sensitive_text(
                    error,
                    normalize_whitespace=True,
                ),
            }
        try:
            latest = int(self._bridge.latest_seq())
            history = [dict(event) for event in self._bridge.history_since(0)]
            for event in history:
                seq_value = event.get("seq")
                if isinstance(seq_value, int):
                    latest = max(latest, seq_value)
            history_payload: dict[str, object] = {
                "events": history,
                "latest": latest,
            }
        except Exception as error:  # noqa: BLE001 - snapshot remains usable.
            history_payload = {
                "events": [],
                "latest": 0,
                "error": _redact_sensitive_text(
                    error,
                    normalize_whitespace=True,
                ),
            }
        if micromachine_status is None:
            try:
                micromachine_status = self._micromachine_status_payload(
                    blackboard_dir
                )
            except Exception as error:  # noqa: BLE001 - snapshot remains usable.
                micromachine_status = {
                    "enabled": False,
                    "status": "source_error",
                    "error": _redact_sensitive_text(
                        error,
                        normalize_whitespace=True,
                    ),
                }
        return {
            "snapshot_reason": "initial_or_replay_unavailable",
            "state": snapshot,
            "history": history_payload,
            "micromachine_status": dict(micromachine_status),
        }

    def _refresh_event_sources(self, blackboard_dir: str) -> None:
        server = self.server  # type: ignore[assignment]
        try:
            with server._event_source_lock:  # type: ignore[attr-defined]
                observed_at_read = int(server._observed_history_seq)  # type: ignore[attr-defined]
            latest = int(self._bridge.latest_seq())
            history = [
                dict(event)
                for event in self._bridge.history_since(observed_at_read)
            ]
            with server._event_source_lock:  # type: ignore[attr-defined]
                observed = int(server._observed_history_seq)  # type: ignore[attr-defined]
                for event in history:
                    payload = dict(event)
                    seq_value = payload.get("seq")
                    if not isinstance(seq_value, int) or seq_value <= observed:
                        continue
                    server.publish_event("history", payload)  # type: ignore[attr-defined]
                    observed = seq_value
                server._observed_history_seq = max(observed, latest)  # type: ignore[attr-defined]
            server.publish_source_recovered(  # type: ignore[attr-defined]
                "history",
                "history",
            )
        except Exception as error:  # noqa: BLE001 - stream remains available.
            server.publish_source_error(  # type: ignore[attr-defined]
                "history",
                "history",
                {
                    "error": _redact_sensitive_text(
                        error,
                        normalize_whitespace=True,
                    ),
                },
            )
        try:
            state_payload = self._state_payload()
            server.publish_changed_snapshot(  # type: ignore[attr-defined]
                "state",
                "state",
                state_payload,
            )
            server.publish_source_recovered(  # type: ignore[attr-defined]
                "state",
                "state",
            )
        except Exception as error:  # noqa: BLE001 - stream remains available.
            server.publish_source_error(  # type: ignore[attr-defined]
                "state",
                "state",
                {
                    "error": _redact_sensitive_text(
                        error,
                        normalize_whitespace=True,
                    ),
                },
            )
        try:
            source_lock = server.operation_status_lock(  # type: ignore[attr-defined]
                blackboard_dir
            )
            with source_lock:
                status = self._micromachine_status_payload(blackboard_dir)
                scope = str(
                    status.get("blackboard_scope_id")
                    or status.get("blackboard_dir")
                    or blackboard_dir
                )
                with server._event_source_lock:  # type: ignore[attr-defined]
                    status_published = server.publish_changed_snapshot(  # type: ignore[attr-defined]
                        f"micromachine:{scope}",
                        "micromachine_status",
                        status,
                        blackboard_dir=blackboard_dir,
                    )
                    if status_published:
                        self._publish_new_operation_events(
                            status,
                            blackboard_dir=blackboard_dir,
                            publish=True,
                        )
                server.publish_source_recovered(  # type: ignore[attr-defined]
                    f"micromachine_status:{scope}",
                    "micromachine_status",
                    blackboard_dir=blackboard_dir,
                )
        except Exception as error:  # noqa: BLE001 - stream remains available.
            scope_id = _micromachine_blackboard_scope_id(blackboard_dir)
            server.publish_source_error(  # type: ignore[attr-defined]
                f"micromachine_status:{scope_id}",
                "micromachine_status",
                {
                    "blackboard_dir": blackboard_dir,
                    "blackboard_scope_id": scope_id,
                    "error": _redact_sensitive_text(
                        error,
                        normalize_whitespace=True,
                    ),
                },
                blackboard_dir=blackboard_dir,
            )

    def _publish_new_operation_events(
        self,
        status: Mapping[str, object],
        *,
        blackboard_dir: str,
        publish: bool,
    ) -> None:
        if not publish:
            return
        server = self.server  # type: ignore[assignment]
        with server._event_source_lock:  # type: ignore[attr-defined]
            scope_id = str(
                status.get("blackboard_scope_id")
                or _micromachine_blackboard_scope_id(blackboard_dir)
            )
            first_scope_observation = bool(
                scope_id
                and scope_id
                not in server._observed_operation_event_seq  # type: ignore[attr-defined]
                and scope_id
                not in server._observed_operation_event_high_water  # type: ignore[attr-defined]
            )
            if not server.admit_operation_event_scope(scope_id):  # type: ignore[attr-defined]
                return
            server.touch_operation_event_scope(scope_id)  # type: ignore[attr-defined]
            raw_events = status.get("operation_events")
            events = (
                [
                    dict(item)
                    for item in raw_events
                    if isinstance(item, Mapping)
                ]
                if isinstance(raw_events, Sequence)
                and not isinstance(raw_events, (str, bytes, bytearray))
                else []
            )
            if first_scope_observation:
                observed = max(
                    (
                        _web_event_int(
                            event.get("timeline_seq"),
                            0,
                        )
                        for event in events
                    ),
                    default=0,
                )
                server._observed_operation_event_seq[scope_id] = observed  # type: ignore[attr-defined]
                server.remember_operation_event_high_water(  # type: ignore[attr-defined]
                    scope_id,
                    observed,
                )
                return
            else:
                observed = int(
                    max(
                        server._observed_operation_event_seq.get(  # type: ignore[attr-defined]
                            scope_id,
                            0,
                        ),
                        server._observed_operation_event_high_water.get(  # type: ignore[attr-defined]
                            scope_id,
                            0,
                        ),
                    )
                )
            latest = observed
            for event in sorted(
                events,
                key=lambda item: _web_event_int(
                    item.get("timeline_seq"),
                    0,
                ),
            ):
                timeline_seq = _web_event_int(
                    event.get("timeline_seq"),
                    0,
                )
                if timeline_seq <= observed:
                    continue
                latest = max(latest, timeline_seq)
                published_event = server.publish_event(  # type: ignore[attr-defined]
                    "operation_event",
                    event,
                    update_id=str(event.get("update_id", "") or ""),
                    operation_id=str(
                        event.get("operation_id", "") or ""
                    ),
                    generation=max(
                        0,
                        _web_event_int(event.get("generation"), 0),
                    ),
                    game_frame=_web_event_int(
                        event.get("game_frame"),
                        -1,
                    ),
                    blackboard_dir=blackboard_dir,
                    blackboard_scope_id=scope_id,
                )
                server.remember_pending_operation_event(  # type: ignore[attr-defined]
                    scope_id,
                    int(published_event.get("event_seq", 0)),
                )
            server._observed_operation_event_seq[scope_id] = latest  # type: ignore[attr-defined]
            server.remember_operation_event_high_water(  # type: ignore[attr-defined]
                scope_id,
                latest,
            )

    def _state_payload(self) -> dict[str, object]:
        snapshot = self._bridge.state_snapshot()
        if snapshot is None:
            return {"available": False}
        payload: dict[str, object] = {"available": True}
        payload.update(dict(snapshot))
        return payload

    def _micromachine_status_payload(
        self,
        blackboard_dir: str,
    ) -> dict[str, object]:
        status_fn = getattr(self._bridge, "micromachine_status", None)
        if not callable(status_fn):
            return {
                "enabled": False,
                "error": "MicroMachine modulation bridge is disabled.",
            }
        runtime_snapshot = None
        validated_telemetry_document = None
        launcher = getattr(self.server, "micromachine_launcher", None)  # type: ignore[attr-defined]
        validated_snapshot_fn = getattr(
            launcher,
            "validated_snapshot",
            None,
        )
        if callable(validated_snapshot_fn):
            validated_snapshot = validated_snapshot_fn(
                blackboard_dir=blackboard_dir
            )
            if isinstance(
                validated_snapshot,
                _MicroMachineValidatedRuntimeSnapshot,
            ):
                runtime_snapshot = dict(validated_snapshot.metadata)
                if isinstance(
                    validated_snapshot.telemetry_document,
                    Mapping,
                ):
                    validated_telemetry_document = deepcopy(
                        dict(validated_snapshot.telemetry_document)
                    )
        elif launcher is not None and callable(getattr(launcher, "snapshot", None)):
            runtime_snapshot = dict(
                launcher.snapshot(blackboard_dir=blackboard_dir)
            )
            if runtime_snapshot.get("runtime_attached") is True:
                runtime_snapshot["telemetry_current_for_process"] = False
                runtime_snapshot["telemetry_stale_or_detached"] = (
                    runtime_snapshot.get("telemetry_present") is True
                )
        runtime_claims_current_telemetry = bool(
            isinstance(runtime_snapshot, Mapping)
            and runtime_snapshot.get("runtime_attached") is True
            and runtime_snapshot.get("telemetry_current_for_process") is True
        )
        runtime_instance_id = ""
        if (
            runtime_claims_current_telemetry
            and isinstance(validated_telemetry_document, Mapping)
        ):
            runtime_instance_id = str(
                runtime_snapshot.get("runtime_instance_id", "") or ""
            ).strip()
        runtime_status_fn = getattr(
            self._bridge,
            "micromachine_status_for_runtime",
            None,
        )
        if (
            runtime_claims_current_telemetry
            and runtime_instance_id
            and callable(runtime_status_fn)
        ):
            payload = dict(
                runtime_status_fn(
                    blackboard_dir=blackboard_dir,
                    runtime_instance_id=runtime_instance_id,
                    telemetry_document=validated_telemetry_document,
                )
            )
        elif runtime_claims_current_telemetry:
            runtime_snapshot = dict(runtime_snapshot)
            runtime_snapshot["telemetry_current_for_process"] = False
            runtime_snapshot["telemetry_stale_or_detached"] = True
            payload = {
                "enabled": True,
                "blackboard_dir": blackboard_dir,
                "status": "source_error",
                "error": (
                    "Attached MicroMachine runtime status requires a bridge "
                    "that consumes the launcher-validated telemetry snapshot."
                ),
            }
        else:
            detached_status_fn = getattr(
                self._bridge,
                "micromachine_status_detached",
                None,
            )
            status_builder = (
                detached_status_fn
                if isinstance(runtime_snapshot, Mapping)
                and runtime_snapshot.get("telemetry_present") is True
                and callable(detached_status_fn)
                else status_fn
            )
            payload = dict(
                status_builder(blackboard_dir=blackboard_dir)
            )
        return _micromachine_status_with_runtime_gate(
            payload,
            runtime_snapshot=runtime_snapshot,
            blackboard_dir=str(
                payload.get("blackboard_dir", blackboard_dir) or ""
            ),
        )

    def _write_sse_event(self, event: Mapping[str, object]) -> None:
        safe_event = _redact_json_ready(event)
        body = json.dumps(
            safe_event,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        event_seq = int(event.get("event_seq", 0))
        event_type = str(event.get("event_type", "message") or "message")
        self.wfile.write(f"id: {event_seq}\n".encode("utf-8"))
        self.wfile.write(f"event: {event_type}\n".encode("utf-8"))
        self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
        self.wfile.flush()
        self._last_sse_write_monotonic = time.monotonic()

    def _write_sse_heartbeat(self) -> None:
        self.wfile.write(b": heartbeat\n\n")
        self.wfile.flush()
        self._last_sse_write_monotonic = time.monotonic()

    def _handle_command(self) -> None:
        body = self._read_request_body()
        if body is None:
            self._send_command_rejection(
                "요청 본문을 읽을 수 없습니다. "
                'Content-Length 헤더와 JSON 본문 {"text": "명령"} 형식으로 다시 보내 주세요.'
            )
            return
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_command_rejection(
                "본문이 올바른 JSON이 아닙니다. "
                '{"text": "명령"} 형식의 UTF-8 JSON으로 다시 보내 주세요.'
            )
            return
        if not isinstance(document, dict):
            self._send_command_rejection(
                'JSON 본문은 객체여야 합니다. {"text": "명령"} 형식으로 다시 보내 주세요.'
            )
            return
        text = document.get("text")
        if not isinstance(text, str) or not text.strip():
            self._send_command_rejection(
                "text 필드는 비어 있지 않은 문자열이어야 합니다. "
                "예: 마린 6기 입구로 보내고 SCV 계속 찍어"
            )
            return
        try:
            web_request_id = _normalize_web_request_id(
                document.get("request_id")
            )
        except (TypeError, ValueError) as error:
            self._send_command_rejection(
                f"request_id가 올바르지 않습니다: {error}"
            )
            return
        try:
            llm_snapshot = dict(self._bridge.llm_settings_snapshot())
        except Exception as error:  # noqa: BLE001 - surfaced honestly as 500.
            self._send_internal_error(error)
            return
        if not bool(llm_snapshot.get("configured")):
            self._send_json(
                HTTPStatus.CONFLICT,
                {"accepted": False, "error": LLM_REQUIRED_COMMAND_ERROR},
            )
            return
        try:
            submit_correlated = getattr(
                self._bridge,
                "submit_correlated_command",
                None,
            )
            if web_request_id and callable(submit_correlated):
                submit_correlated(text.strip(), web_request_id)
            else:
                self._bridge.submit_command(text.strip())
        except RuntimeError:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "accepted": False,
                    "error": (
                        "명령 처리 루프가 실행 중이 아닙니다. "
                        "서버를 재시작한 뒤 다시 시도해 주세요."
                    ),
                },
            )
            return
        except Exception as error:  # noqa: BLE001 - surfaced honestly as 500.
            self._send_internal_error(error)
            return
        legacy_operation_id = str(
            document.get("operation_id", "")
            or web_request_id
            or f"legacy-{uuid.uuid4().hex}"
        )
        self.server.publish_event(  # type: ignore[attr-defined]
            "command_received",
            {
                "command_text": text.strip(),
                "status": "received",
                "mode": COMMAND_MODE_LEGACY_COMMANDER,
                "request_id": web_request_id,
            },
            operation_id=legacy_operation_id,
            generation=max(0, _web_event_int(document.get("generation"), 0)),
        )
        self._send_json(HTTPStatus.ACCEPTED, {"accepted": True})

    def _handle_llm_status(self) -> None:
        try:
            self._send_json(HTTPStatus.OK, dict(self._bridge.llm_settings_snapshot()))
        except Exception as error:  # noqa: BLE001 - surfaced honestly.
            self._send_internal_error(error)

    def _handle_llm_configure(self) -> None:
        body = self._read_request_body()
        if body is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"configured": False, "error": "LLM 설정 JSON 본문을 읽을 수 없습니다."},
            )
            return
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"configured": False, "error": "LLM 설정 본문이 올바른 JSON이 아닙니다."},
            )
            return
        if not isinstance(document, Mapping):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"configured": False, "error": "LLM 설정 본문은 JSON 객체여야 합니다."},
            )
            return
        provider = str(document.get("provider", "") or "")
        api_key = str(document.get("api_key", "") or "")
        model = str(document.get("model", "") or "")
        try:
            snapshot = self._bridge.configure_llm(provider, api_key, model)
        except Exception as error:  # noqa: BLE001 - user-facing config failure.
            status, payload = _build_llm_setup_failure_response(
                error,
                provider=provider,
                model=model,
                api_key=api_key,
            )
            self._send_json(status, payload)
            return
        response = dict(snapshot)
        launcher = getattr(self.server, "live_launcher", None)  # type: ignore[attr-defined]
        if launcher is not None:
            launcher.configure(provider, api_key, model)
        if bool(getattr(self.server, "auto_launch_live", False)):  # type: ignore[attr-defined]
            if launcher is not None:
                response["live_start"] = _public_runtime_launcher_payload(
                    launcher.start()
                )
        self._send_json(HTTPStatus.OK, response)

    def _handle_live_status(self) -> None:
        launcher = getattr(self.server, "live_launcher", None)  # type: ignore[attr-defined]
        if launcher is None:
            self._send_json(
                HTTPStatus.OK,
                {"enabled": False, "status": "disabled", "url": "", "error": ""},
            )
            return
        self._send_json(
            HTTPStatus.OK,
            _public_runtime_launcher_payload(launcher.snapshot()),
        )

    def _handle_runtime_status(self) -> None:
        params = parse_qs(urlsplit(self.path).query)
        mode = _normalize_runtime_mode(params.get("mode", [""])[0] or "")
        if mode == COMMAND_MODE_LEGACY_COMMANDER:
            launcher = getattr(self.server, "live_launcher", None)  # type: ignore[attr-defined]
            if launcher is None:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "enabled": False,
                        "mode": mode,
                        "status": "disabled",
                        "url": "",
                        "error": "",
                    },
                )
                return
            payload = dict(launcher.snapshot())
            payload["mode"] = mode
            self._send_json(
                HTTPStatus.OK,
                _public_runtime_launcher_payload(payload),
            )
            return
        launcher = getattr(self.server, "micromachine_launcher", None)  # type: ignore[attr-defined]
        if launcher is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "enabled": False,
                    "mode": mode,
                    "status": "disabled",
                    "error": "MicroMachine launcher is disabled.",
                },
            )
            return
        blackboard_dir = params.get("blackboard_dir", [""])[0] or ""
        try:
            self._send_json(
                HTTPStatus.OK,
                _public_runtime_launcher_payload(
                    launcher.snapshot(blackboard_dir=blackboard_dir)
                ),
            )
        except Exception as error:  # noqa: BLE001 - surfaced honestly.
            self._send_internal_error(error)

    def _handle_runtime_start(self) -> None:
        body = self._read_request_body()
        if body is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "runtime start JSON 본문을 읽을 수 없습니다."},
            )
            return
        try:
            document = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "runtime start 본문이 올바른 JSON이 아닙니다."},
            )
            return
        if not isinstance(document, Mapping):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "runtime start 본문은 JSON 객체여야 합니다."},
            )
            return
        mode = _normalize_runtime_mode(str(document.get("mode", "") or ""))
        if mode == COMMAND_MODE_LEGACY_COMMANDER:
            launcher = getattr(self.server, "live_launcher", None)  # type: ignore[attr-defined]
            if launcher is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "accepted": False,
                        "enabled": False,
                        "mode": mode,
                        "status": "disabled",
                        "error": "Legacy launcher is disabled.",
                    },
                )
                return
            payload = dict(launcher.start())
            payload["accepted"] = payload.get("status") != "blocked"
            payload["mode"] = mode
            status = (
                HTTPStatus.CONFLICT
                if payload.get("status") == "blocked"
                else HTTPStatus.ACCEPTED
            )
            self._send_json(
                status,
                _public_runtime_launcher_payload(payload),
            )
            return
        launcher = getattr(self.server, "micromachine_launcher", None)  # type: ignore[attr-defined]
        if launcher is None:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "accepted": False,
                    "enabled": False,
                    "mode": mode,
                    "status": "disabled",
                    "error": "MicroMachine launcher is disabled.",
                },
            )
            return
        try:
            enemy_difficulty = _require_micromachine_enemy_difficulty(
                document.get("enemy_difficulty")
            )
        except (TypeError, ValueError) as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": str(error)},
            )
            return
        try:
            payload = dict(
                launcher.start(
                    blackboard_dir=str(document.get("blackboard_dir", "") or ""),
                    enemy_difficulty=enemy_difficulty,
                )
            )
        except Exception as error:  # noqa: BLE001 - surfaced honestly.
            self._send_internal_error(error)
            return
        payload["accepted"] = payload.get("status") not in {
            "blocked",
            "failed",
            "disabled",
        }
        status = (
            HTTPStatus.CONFLICT
            if payload.get("status") == "blocked"
            else HTTPStatus.ACCEPTED
        )
        self._send_json(
            status,
            _public_runtime_launcher_payload(payload),
        )

    def _handle_micromachine_status(self) -> None:
        params = parse_qs(urlsplit(self.path).query)
        blackboard_dir = params.get("blackboard_dir", [""])[0] or ""
        try:
            self._send_json(
                HTTPStatus.OK,
                self._micromachine_status_payload(blackboard_dir),
            )
        except Exception as error:  # noqa: BLE001 - surfaced honestly.
            self._send_internal_error(error)

    def _handle_micromachine_modulate(self) -> None:
        submit_fn = getattr(self._bridge, "submit_micromachine_modulation", None)
        if not callable(submit_fn):
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"accepted": False, "error": "MicroMachine modulation bridge is disabled."},
            )
            return
        body = self._read_request_body()
        if body is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "MicroMachine 요청 JSON 본문을 읽을 수 없습니다."},
            )
            return
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "MicroMachine 요청 본문이 올바른 JSON이 아닙니다."},
            )
            return
        if not isinstance(document, Mapping):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "MicroMachine 요청 본문은 JSON 객체여야 합니다."},
            )
            return
        try:
            semantic_scope, ttl_seconds = _extract_micromachine_semantic_scope(document)
        except ValueError as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": str(error)},
            )
            return
        text = document.get("text")
        if not isinstance(text, str) or not text.strip():
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "text 필드는 비어 있지 않은 문자열이어야 합니다."},
            )
            return
        cleaned_text = text.strip()
        request_blackboard_dir = self._resolved_micromachine_blackboard_dir(
            str(document.get("blackboard_dir", "") or "")
        )
        commander_context = _extract_micromachine_language_context(
            document,
            cleaned_text,
        )
        provider_output = document.get("provider_output")
        if provider_output is not None and not isinstance(provider_output, Mapping):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "provider_output 필드는 JSON 객체여야 합니다."},
            )
            return
        allow_smoke_keyword_provider = document.get("allow_smoke_keyword_provider", False)
        if type(allow_smoke_keyword_provider) is not bool:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "accepted": False,
                    "error": "allow_smoke_keyword_provider 필드는 boolean이어야 합니다.",
                },
            )
            return
        async_publish = document.get("async_publish", False)
        if type(async_publish) is not bool:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "async_publish 필드는 boolean이어야 합니다."},
            )
            return
        current_frame = document.get("current_frame")
        if current_frame is not None and (
            type(current_frame) is bool or not isinstance(current_frame, int)
        ):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": "current_frame 필드는 정수여야 합니다."},
            )
            return
        try:
            update_id = (
                require_micromachine_update_id("update_id", document["update_id"])
                if isinstance(document.get("update_id"), str)
                else _new_micromachine_update_id()
            )
            operation_id = str(document.get("operation_id", "") or update_id)
            operation_generation = max(
                0,
                _web_event_int(document.get("operation_generation"), 0),
            )
            self.server.publish_event(  # type: ignore[attr-defined]
                "command_received",
                {
                    "command_text": cleaned_text,
                    "status": "received",
                    "mode": COMMAND_MODE_MICROMACHINE,
                    "blackboard_dir": request_blackboard_dir,
                },
                update_id=update_id,
                operation_id=operation_id,
                generation=operation_generation,
                game_frame=(
                    current_frame if isinstance(current_frame, int) else -1
                ),
                blackboard_dir=request_blackboard_dir,
            )
            if async_publish:
                async_submit_fn = getattr(
                    self._bridge,
                    "submit_micromachine_modulation_background",
                    None,
                )
                if not callable(async_submit_fn):
                    self._send_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "accepted": False,
                            "error": "MicroMachine async modulation bridge is disabled.",
                        },
                    )
                    return
                payload = dict(
                    async_submit_fn(
                        cleaned_text,
                        blackboard_dir=request_blackboard_dir,
                        provider_output=provider_output,
                        allow_smoke_keyword_provider=allow_smoke_keyword_provider,
                        semantic_scope=semantic_scope,
                        commander_context=commander_context,
                        ttl_seconds=ttl_seconds,
                        current_frame=current_frame,
                        update_id=update_id,
                    )
                )
                self.server.publish_event(  # type: ignore[attr-defined]
                    "micromachine_submission",
                    payload,
                    update_id=update_id,
                    operation_id=operation_id,
                    generation=operation_generation,
                    game_frame=(
                        current_frame if isinstance(current_frame, int) else -1
                    ),
                    blackboard_dir=request_blackboard_dir,
                )
                self._send_json(HTTPStatus.ACCEPTED, payload)
                return
            payload = dict(
                submit_fn(
                    cleaned_text,
                    blackboard_dir=request_blackboard_dir,
                    provider_output=provider_output,
                    allow_smoke_keyword_provider=allow_smoke_keyword_provider,
                    semantic_scope=semantic_scope,
                    commander_context=commander_context,
                    ttl_seconds=ttl_seconds,
                    current_frame=current_frame,
                    update_id=update_id,
                )
            )
        except ValueError as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": str(error)},
            )
            return
        except MissingLLMDependencyError:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"accepted": False, "error": LLM_REQUIRED_COMMAND_ERROR},
            )
            return
        except concurrent.futures.TimeoutError:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "accepted": False,
                    "error": "MicroMachine modulation request timed out.",
                },
            )
            return
        except _MicroMachineRequestSupersededError as error:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "accepted": False,
                    "status": "superseded",
                    "error": str(error),
                },
            )
            return
        except Exception as error:  # noqa: BLE001 - surfaced honestly as 500.
            self._send_internal_error(error)
            return
        status = (
            HTTPStatus.OK
            if not bool(payload.get("ok"))
            else HTTPStatus.ACCEPTED
        )
        payload["accepted"] = bool(payload.get("ok"))
        event_update_id, event_operation_id, event_generation, event_frame = (
            _web_event_identity(payload)
        )
        self.server.publish_event(  # type: ignore[attr-defined]
            "micromachine_submission",
            payload,
            update_id=event_update_id or update_id,
            operation_id=event_operation_id or operation_id,
            generation=event_generation or operation_generation,
            game_frame=(
                event_frame
                if event_frame >= 0
                else (current_frame if isinstance(current_frame, int) else -1)
            ),
            blackboard_dir=request_blackboard_dir,
        )
        self._send_json(status, payload)

    def _read_request_body(self) -> bytes | None:
        """Read the request body; ``None`` marks malformed/oversized input."""

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None
        try:
            length = int(raw_length)
        except ValueError:
            self.close_connection = True
            return None
        if length < 0 or length > MAX_COMMAND_BODY_BYTES:
            self.close_connection = True
            return None
        if length == 0:
            return b""
        try:
            return self.rfile.read(length)
        except OSError:
            self.close_connection = True
            return None

    def _send_command_rejection(self, reason: str) -> None:
        self._send_json(HTTPStatus.BAD_REQUEST, {"accepted": False, "error": reason})

    def _send_not_found(self) -> None:
        self._send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": (
                    f"지원하지 않는 경로입니다: {urlsplit(self.path).path}. "
                    "사용 가능한 경로: GET /, GET /api/state, "
                    "GET /api/history?after=N, GET /api/events, "
                    "GET/POST /api/llm, "
                    "POST /api/command, GET /api/runtime/status, "
                    "POST /api/runtime/start, GET /api/micromachine/status, "
                    "POST /api/micromachine/modulate."
                )
            },
        )

    def _send_internal_error(self, error: Exception) -> None:
        self._send_json(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {
                "error": (
                    "서버 내부 오류가 발생했습니다: "
                    f"{_redact_sensitive_text(error, normalize_whitespace=True)}. "
                    "잠시 후 다시 시도해 주세요."
                )
            },
        )

    def _authorized(self) -> bool:
        expected = getattr(self.server, "auth_token", "")  # type: ignore[attr-defined]
        if not expected:
            return True
        supplied = self.headers.get(WEB_GUI_TOKEN_HEADER, "")
        if supplied == expected:
            return True
        params = parse_qs(urlsplit(self.path).query)
        return (params.get(WEB_GUI_TOKEN_QUERY_PARAM, [""])[0] or "") == expected

    def _send_unauthorized(self) -> None:
        self._send_json(
            HTTPStatus.FORBIDDEN,
            {
                "error": (
                    "웹 GUI 인증 토큰이 필요합니다. 실행 시 출력된 ?token=... URL로 "
                    "접속하거나 X-voiStarcraft2-Token 헤더를 전달해 주세요."
                )
            },
        )

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
        safe_payload = _redact_json_ready(payload)
        body = json.dumps(safe_payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_body(status, "application/json; charset=utf-8", body)

    def _send_html(self, status: HTTPStatus, page: str) -> None:
        self._send_body(status, "text/html; charset=utf-8", page.encode("utf-8"))

    def _send_body(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class WebGuiServer:
    """Threaded HTTP server for the commander web GUI.

    The default bind host is ``127.0.0.1``. To use a phone/tablet as a
    companion controller while StarCraft II owns desktop focus, pass a
    non-localhost host such as ``0.0.0.0`` together with a non-empty auth
    token. Pass ``port=0`` to bind an ephemeral port (tests); :attr:`port`
    reports the actually bound port once started.
    """

    def __init__(
        self,
        bridge: WebGuiBridgeInterface,
        port: int = DEFAULT_WEB_GUI_PORT,
        host: str = WEB_GUI_HOST,
        auth_token: str = "",
        auto_launch_live: bool = False,
    ) -> None:
        if not isinstance(bridge, WebGuiBridgeInterface):
            raise TypeError(
                "Web GUI server bridge must implement submit_command(), "
                "state_snapshot(), history_since(), and latest_seq()."
            )
        if type(port) is not int:
            raise TypeError("Web GUI server port must be an int.")
        if not 0 <= port <= 65535:
            raise ValueError("Web GUI server port must be between 0 and 65535.")
        if type(host) is not str or not host.strip():
            raise TypeError("Web GUI server host must be a non-empty string.")
        cleaned_host = host.strip()
        if type(auth_token) is not str:
            raise TypeError("Web GUI server auth_token must be a string.")
        cleaned_token = auth_token.strip()
        if not _is_localhost_bind(cleaned_host) and not cleaned_token:
            raise ValueError(
                "Non-localhost web GUI binding requires an auth token."
            )
        self._bridge = bridge
        self._requested_port = port
        self._host = cleaned_host
        self._auth_token = cleaned_token
        self._auto_launch_live = bool(auto_launch_live)
        self._event_journal = _WebEventJournal()
        self._live_launcher = _LiveLaunchManager()
        self._micromachine_launcher = _MicroMachineLaunchManager()
        self._lifecycle_lock = threading.Lock()
        self._http: _BridgedThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        """Return the configured bind host."""

        return self._host

    @property
    def port(self) -> int:
        """Return the bound port once started, else the requested port."""

        http = self._http
        if http is not None:
            return int(http.server_address[1])
        return self._requested_port

    @property
    def url(self) -> str:
        """Return the browsable URL for the configured bind host."""

        suffix = (
            f"/?{WEB_GUI_TOKEN_QUERY_PARAM}={self._auth_token}"
            if self._auth_token
            else ""
        )
        return f"http://{self.host}:{self.port}{suffix}"

    @property
    def is_running(self) -> bool:
        """Return whether the serve_forever thread is alive."""

        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Bind the configured host and serve in a daemon thread; idempotent."""

        with self._lifecycle_lock:
            if self._http is not None:
                return
            self._http = _BridgedThreadingHTTPServer(
                (self._host, self._requested_port),
                _WebGuiRequestHandler,
                self._bridge,
                self._auth_token,
                self._event_journal,
            )
            self._http.auto_launch_live = self._auto_launch_live  # type: ignore[attr-defined]
            self._http.live_launcher = self._live_launcher  # type: ignore[attr-defined]
            self._http.micromachine_launcher = self._micromachine_launcher  # type: ignore[attr-defined]
            self._thread = threading.Thread(
                target=self._http.serve_forever,
                kwargs={"poll_interval": 0.1},
                name=_SERVER_THREAD_NAME,
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Shut down the server, close the socket, and join the thread."""

        with self._lifecycle_lock:
            http = self._http
            thread = self._thread
            self._http = None
            self._thread = None
        if http is not None:
            http.begin_shutdown()
            http.shutdown()
            http.server_close()
        if thread is not None:
            thread.join(timeout=timeout)


def _is_localhost_bind(host: str) -> bool:
    """Return whether ``host`` is loopback-only for no-token GUI binding."""

    return host in {"127.0.0.1", "localhost", "::1"}


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the web GUI argument parser."""

    parser = argparse.ArgumentParser(
        prog="python -m starcraft_commander.web_gui",
        description=(
            "voiStarcraft2 커맨더 로컬 웹 GUI. "
            "--dry-run은 내장 가짜 BotAI로 전체 파이프라인을 실행합니다. "
            "MicroMachine 조작은 blackboard live session/soak 경로를 사용하고, "
            "python-sc2 demo는 legacy compatibility mode입니다."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run against the built-in scripted DemoFakeBotAI (no StarCraft II needed)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_WEB_GUI_PORT,
        help=f"local web GUI port (default: {DEFAULT_WEB_GUI_PORT}; 0 for ephemeral)",
    )
    parser.add_argument(
        "--host",
        default=WEB_GUI_HOST,
        help=(
            "web GUI bind host (default: 127.0.0.1). Use 0.0.0.0 for "
            "phone/tablet companion control, together with --token."
        ),
    )
    parser.add_argument(
        "--token",
        default="",
        help="auth token required when exposing the web GUI beyond localhost",
    )
    parser.add_argument(
        "--auto-launch-legacy-live",
        action="store_true",
        help=(
            "after LLM setup, auto-start the legacy python-sc2 demo live GUI. "
            "Disabled by default so it is not confused with MicroMachine."
        ),
    )
    return parser


def _wait_for_interrupt() -> None:
    """Block the main thread until KeyboardInterrupt (Ctrl+C)."""

    while True:
        time.sleep(0.5)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entrypoint for ``python -m starcraft_commander.web_gui``."""

    args = build_argument_parser().parse_args(argv)
    if not args.dry_run:
        print(
            "웹 GUI 단독 실행은 지금은 --dry-run 모드만 지원합니다 "
            "(실제 게임 연결 로직이 아직 이 진입점에 없기 때문입니다)."
        )
        print(
            "대안: 가짜 봇으로 체험하려면 "
            "'python -m starcraft_commander.web_gui --dry-run', "
            "MicroMachine은 integrations/micromachine scripts와 "
            "blackboard live session을 사용하세요. "
            "이전 python-sc2 demo는 legacy commander mode로만 사용하세요."
        )
        return 2

    # Lazy import: reuse the demo's dry-run wiring (scripted DemoFakeBotAI +
    # adapter + executor + session) instead of duplicating it here.
    from starcraft_commander.demo_sc2 import MVP_DEMO_COMMAND, build_dry_run_session
    from starcraft_commander.llm_interpreter import (
        MYPROXY_API_KEY_ENV_VAR,
        HybridCommandInterpreter,
        LocalLLMControl,
    )

    default_provider = (
        "myproxy"
        if any(
            os.environ.get(name, "").strip()
            for name in (MYPROXY_API_KEY_ENV_VAR, "CODEX_MYPROXY_API_KEY")
        )
        else "openai"
    )
    llm_control = LocalLLMControl(provider=default_provider)
    interpreter = HybridCommandInterpreter(llm_interpreter=llm_control)
    session, _bot = build_dry_run_session(interpreter=interpreter)
    bridge = SessionLoopBridge(session=session, llm_control=llm_control)
    server = WebGuiServer(
        bridge=bridge,
        port=args.port,
        host=args.host,
        auth_token=args.token,
        auto_launch_live=args.auto_launch_legacy_live,
    )
    bridge.start()
    try:
        try:
            server.start()
        except OSError as error:
            print(
                f"포트 {args.port}에 바인딩하지 못했습니다 (이유: {error}). "
                "다른 --port 값을 지정하거나 --port 0으로 임시 포트를 사용해 주세요."
            )
            return 1
        print(f"voiStarcraft2 커맨더 웹 GUI 시작: {server.url}")
        print(
            f"브라우저에서 위 주소를 열고 한국어 명령을 입력하세요. "
            f"예: {MVP_DEMO_COMMAND} (종료: Ctrl+C)"
        )
        _wait_for_interrupt()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        bridge.stop()
    print("웹 GUI를 종료합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
