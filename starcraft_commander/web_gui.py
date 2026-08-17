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
import json
import os
import re
import stat
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
from pathlib import Path
from typing import BinaryIO, Callable, Final, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit
from weakref import WeakValueDictionary

from starcraft_commander.companion_ui import render_companion_page
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
from starcraft_commander.contextual_transfer import (
    CONTEXTUAL_TRANSFER_REQUEST_FIELDS,
    ContextualTransferRejectedError,
    ContextualTransferRequest,
    prepare_contextual_transfer,
)
from starcraft_commander.micromachine_tactical_evidence import (
    classify_micromachine_tactical_evidence,
    normalize_tactical_effect_tags,
)
from starcraft_commander.micromachine_terran_capabilities import (
    TERRAN_UNIT_FAMILY_BY_NAME,
    canonical_terran_unit_family,
    operation_family_evidence,
    terran_unit_form_prerequisites,
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
from starcraft_commander.runtime_data import source_repository_root
from starcraft_commander.sc2_launch_contract import (
    DEFAULT_SC2_API_PORT,
    REQUIRED_SC2_BASE,
)
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


def resolve_required_sc2_executable(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the live SC2 binary without loading cockpit bootstrap eagerly."""

    from starcraft_commander.local_cockpit import (
        resolve_required_sc2_executable as resolve,
    )

    return resolve(environment)


def read_sc2_launch_receipt(
    path: Path,
    nonce: str,
    *,
    now_unix: float | None = None,
    require_live_process: bool = True,
) -> dict[str, object]:
    """Validate visible-launch proof without loading cockpit bootstrap eagerly."""

    from starcraft_commander.local_cockpit import (
        read_sc2_launch_receipt as read_receipt,
    )

    return read_receipt(
        path,
        nonce,
        now_unix=now_unix,
        require_live_process=require_live_process,
    )


_REPO_ROOT: Final[str] = str(Path(__file__).resolve().parents[1])
"""Module installation root used by explicit repo-local tooling."""


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
            item.setdefault(
                "battlefield_session_epoch",
                str(
                    document.get("battlefield_session_epoch", "") or ""
                ).strip(),
            )
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
            "battlefield_session_epoch": str(
                document.get("battlefield_session_epoch", "") or ""
            ).strip(),
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


def _micromachine_payload_battlefield_session_epoch(
    payload: Mapping[str, object] | None,
) -> str:
    if not isinstance(payload, Mapping):
        return ""
    return str(
        payload.get("battlefield_session_epoch", "") or ""
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


_TERRAN_CAPABILITY_PREREQUISITES: Final[frozenset[str]] = frozenset(
    prerequisite
    for family in TERRAN_UNIT_FAMILY_BY_NAME.values()
    for prerequisite in family.prerequisites
) | frozenset(
    prerequisite
    for unit_type in (
        unit_type
        for family in TERRAN_UNIT_FAMILY_BY_NAME.values()
        for unit_type in family.unit_types
    )
    for prerequisite in terran_unit_form_prerequisites(unit_type)
)


def _canonical_terran_prerequisite_token(value: object) -> str:
    token = re.sub(
        r"[^A-Z0-9]+",
        "_",
        str(value or "").strip().upper(),
    ).strip("_")
    if not token:
        return ""
    candidates = [token]
    stem = token.removeprefix("TERRAN_")
    for suffix in ("TECHLAB", "REACTOR"):
        if stem.endswith(suffix) and not stem.endswith(f"_{suffix}"):
            candidates.append(f"{stem[:-len(suffix)]}_{suffix}")
    return next(
        (
            candidate
            for candidate in candidates
            if candidate in _TERRAN_CAPABILITY_PREREQUISITES
        ),
        token,
    )


def _micromachine_operation_requirement_payload(
    requirement: Mapping[str, object],
) -> dict[str, object]:
    unit_type = str(requirement.get("unit_type", "") or "")
    family = canonical_terran_unit_family(unit_type)
    capability = TERRAN_UNIT_FAMILY_BY_NAME.get(family)
    allowed_prerequisites = (
        frozenset(terran_unit_form_prerequisites(unit_type))
        if capability is not None
        else frozenset()
    )
    reported_prerequisites = list(
        dict.fromkeys(
            token
            for value in _string_list(
                requirement.get("prerequisites", ())
            )
            if (token := _canonical_terran_prerequisite_token(value))
        )
    )
    reported_missing = list(
        dict.fromkeys(
            token
            for value in _string_list(
                requirement.get("missing_prerequisites", ())
            )
            if (token := _canonical_terran_prerequisite_token(value))
        )
    )
    blockers: list[str] = []
    if capability is None:
        blockers.append("unit_family_evidence_missing")
    elif not reported_prerequisites:
        blockers.append("prerequisite_chain_evidence_missing")
    has_unrelated_prerequisite = any(
        prerequisite not in allowed_prerequisites
        for prerequisite in (*reported_prerequisites, *reported_missing)
    )
    if has_unrelated_prerequisite:
        blockers.append("unrelated_prerequisite_evidence")
    elif (
        capability is not None
        and reported_prerequisites
        and not allowed_prerequisites.issubset(reported_prerequisites)
    ):
        blockers.append("prerequisite_chain_incomplete")
    if any(
        prerequisite not in reported_prerequisites
        for prerequisite in reported_missing
        if prerequisite in allowed_prerequisites
    ):
        blockers.append("missing_prerequisite_chain_mismatch")
    count_fields = (
        "target_count",
        "assigned_count",
        "represented_count",
        "completed_count",
        "in_progress_count",
        "queued_count",
        "missing_count",
    )
    counts: dict[str, int | None] = {}
    for field in count_fields:
        value = _int_or_none(requirement.get(field))
        counts[field] = value if value is not None and value >= 0 else None
    if any(value is None for value in counts.values()):
        blockers.append("requirement_count_evidence_missing")
    prerequisites = [
        prerequisite
        for prerequisite in reported_prerequisites
        if prerequisite in allowed_prerequisites
    ]
    missing_prerequisites = [
        prerequisite
        for prerequisite in reported_missing
        if prerequisite in allowed_prerequisites
    ]
    return {
        "unit_type": unit_type,
        "canonical_family": family,
        "role": str(requirement.get("role", "") or ""),
        **counts,
        "production_blocker": str(
            requirement.get("production_blocker", "") or ""
        ),
        "prerequisites": prerequisites,
        "missing_prerequisites": missing_prerequisites,
        "prerequisite_integrity_status": (
            "blocked" if blockers else "valid"
        ),
        "prerequisite_integrity_blockers": list(
            dict.fromkeys(blockers)
        ),
    }


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
    battlefield_session_epoch: str = "",
) -> dict[str, object]:
    update_id = str(operation_update.get("update_id", "") or "").strip()
    operation_vector = _mapping_child(operation_update, "vector")
    persisted_operation_generation = (
        operation_vector.get("generation")
        if type(operation_vector.get("generation")) is int
        and int(operation_vector.get("generation")) > 0
        else 0
    )
    operation_generation = persisted_operation_generation or 1
    explicit_operation_identity = bool(
        str(operation_vector.get("operation_id", "") or "").strip()
        and persisted_operation_generation > 0
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
        _micromachine_operation_requirement_payload(requirement)
        for requirement in (
            requirement_progress
            if isinstance(requirement_progress, Sequence)
            and not isinstance(requirement_progress, (str, bytes))
            else ()
        )
        if isinstance(requirement, Mapping)
    ]
    prerequisite_integrity_blockers = list(
        dict.fromkeys(
            blocker
            for requirement in normalized_requirement_progress
            for blocker in _string_list(
                requirement.get("prerequisite_integrity_blockers", ())
            )
        )
    )
    operation_count_fields = {
        "target_count": "requirement_target_count",
        "represented_count": "requirement_represented_count",
        "missing_count": "requirement_missing_count",
    }
    operation_counts: dict[str, int | None] = {}
    for public_name, telemetry_name in operation_count_fields.items():
        value = _int_or_none(
            operation_telemetry.get(telemetry_name)
            if isinstance(operation_telemetry, Mapping)
            else None
        )
        operation_counts[public_name] = (
            value if value is not None and value >= 0 else None
        )
    if any(value is None for value in operation_counts.values()):
        prerequisite_integrity_blockers.append(
            "operation_count_evidence_missing"
        )
        prerequisite_integrity_blockers = list(
            dict.fromkeys(prerequisite_integrity_blockers)
        )
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
        **operation_counts,
        "requirements": normalized_requirement_progress,
        "prerequisite_integrity_status": (
            "blocked"
            if prerequisite_integrity_blockers
            else (
                "valid"
                if normalized_requirement_progress
                else "missing_evidence"
            )
        ),
        "prerequisite_integrity_blockers": (
            prerequisite_integrity_blockers
        ),
    }
    family_evidence = _public_operation_family_evidence(
        aggregate_family_evidence
    )
    payload = {
        "operation_key": operation_key,
        "blackboard_scope_id": scope_id,
        "battlefield_session_epoch": str(
            battlefield_session_epoch or ""
        ).strip(),
        "battlefield_projection_identity_complete": bool(
            explicit_operation_identity
            and str(battlefield_session_epoch or "").strip()
        ),
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
) -> dict[tuple[str, str, str, int, str], dict[str, object]]:
    if not isinstance(battlefield_overview, Mapping):
        return {}
    operations = battlefield_overview.get("operation_ownership")
    if not isinstance(operations, Sequence) or isinstance(
        operations,
        (str, bytes, bytearray),
    ):
        return {}
    index: dict[tuple[str, str, str, int, str], dict[str, object]] = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        identity = _mapping_child(operation, "identity")
        update_id = str(identity.get("update_id", "") or "").strip()
        scope = str(identity.get("scope", "") or "").strip()
        session_epoch = str(
            identity.get("session_epoch", "") or ""
        ).strip()
        operation_id = str(operation.get("operation_id", "") or "").strip()
        generation = operation.get("generation")
        if (
            not update_id
            or scope != f"operation:{operation_id}"
            or not session_epoch
            or not operation_id
            or type(generation) is not int
            or int(generation) <= 0
        ):
            continue
        index[
            (
                scope,
                session_epoch,
                operation_id,
                int(generation),
                update_id,
            )
        ] = dict(operation)
    return index


def _attach_battlefield_operation_projections(
    operations: Sequence[Mapping[str, object]],
    battlefield_overview: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    index = _battlefield_operation_index(battlefield_overview)
    overview_identity = _mapping_child(
        battlefield_overview or {},
        "identity",
    )
    session_epoch = str(
        overview_identity.get("session_epoch", "") or ""
    ).strip()
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
        identity_complete = (
            item.get("battlefield_projection_identity_complete") is True
        )
        scope = f"operation:{operation_id}" if operation_id else ""
        operation_session_epoch = str(
            item.get("battlefield_session_epoch", "") or ""
        ).strip()
        join_key = (
            scope,
            operation_session_epoch,
            operation_id,
            int(generation),
            update_id,
        ) if (
            update_id
            and scope
            and identity_complete
            and session_epoch
            and operation_session_epoch == session_epoch
            and type(generation) is int
            and int(generation) > 0
        ) else None
        projection = (
            index.get(join_key)
            if join_key is not None
            else None
        )
        item["battlefield_operation"] = (
            dict(projection) if projection is not None else None
        )
        item["battlefield_projection_join"] = {
            "status": "matched" if projection is not None else "missing",
            "reason": (
                ""
                if projection is not None
                else (
                    "authoritative_overview_unavailable"
                    if not isinstance(battlefield_overview, Mapping)
                    else (
                        "operation_session_epoch_missing"
                        if not operation_session_epoch
                        else (
                            "operation_canonical_identity_incomplete"
                            if not identity_complete
                            else (
                                "operation_session_epoch_mismatch"
                                if operation_session_epoch != session_epoch
                                else "exact_projection_identity_not_found"
                            )
                        )
                    )
                )
            ),
            "update_id": update_id,
            "scope": scope,
            "session_epoch": operation_session_epoch,
            "operation_id": operation_id,
            "generation": (
                int(generation)
                if type(generation) is int and int(generation) > 0
                else 0
            ),
        }
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
            "composition": (
                {
                    "family": _PUBLIC_BATTLEFIELD_SCALAR,
                    "role": _PUBLIC_BATTLEFIELD_SCALAR,
                    "count": _PUBLIC_BATTLEFIELD_SCALAR,
                    "ground_capable_count": _PUBLIC_BATTLEFIELD_SCALAR,
                    "air_capable_count": _PUBLIC_BATTLEFIELD_SCALAR,
                },
            ),
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
            operation_session_epoch = (
                _micromachine_payload_battlefield_session_epoch(result_item)
            )
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
                    battlefield_session_epoch=operation_session_epoch,
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
            operation_session_epoch = (
                _micromachine_payload_battlefield_session_epoch(result_item)
            )
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
                    battlefield_session_epoch=operation_session_epoch,
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
    battlefield_overview = (
        dict(battlefield_projection.battlefield_overview)
        if battlefield_projection.ok
        and battlefield_projection.battlefield_overview is not None
        else None
    )
    operations = _micromachine_operations_payload(
        dashboard,
        telemetry=telemetry,
        telemetry_archive=telemetry_archive,
        blackboard_dir=blackboard_dir,
        compile_result=compile_result,
        result_stream=result_stream,
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
        "battlefield_projection_fingerprint": "",
        "battlefield_projection_integrity": dict(
            battlefield_projection.integrity
        ),
    }
    public_payload = _public_micromachine_runtime_payload(payload)
    if not isinstance(public_payload, Mapping):
        return {}
    result = dict(public_payload)
    public_overview = result.get("battlefield_overview")
    result["battlefield_projection_fingerprint"] = (
        battlefield_overview_fingerprint(public_overview)
        if isinstance(public_overview, Mapping)
        else ""
    )
    return result


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
    command_queue = (
        dict(compile_result.get("command_queue"))
        if isinstance(compile_result.get("command_queue"), Mapping)
        else {}
    )
    command_text = str(compile_result.get("command_text", "") or "")
    if not command_text:
        command_text = str(command_queue.get("command_text", "") or "")
    return {
        "update_id": result_update_id,
        "status": result_status,
        "source": str(compile_result.get("source", "") or ""),
        "command_text": command_text,
        "consumption_status": request_consumption_status,
        "active_update_id": active_update_id,
        "is_active_update": bool(result_update_id and result_update_id == active_update_id),
        "refusal_reason": str(compile_result.get("refusal_reason", "") or ""),
        "clarification_prompt": str(
            compile_result.get("clarification_prompt", "") or ""
        ),
        "duration_ms": compile_result.get("duration_ms"),
        "command_queue": command_queue,
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


LLM_REQUIRED_COMMAND_ERROR: Final[str] = (
    "LLM 키가 설정되지 않아 명령을 실행하지 않았습니다. "
    "이 프로젝트는 LLM 기반 해석을 필수로 사용합니다. "
    "설치 앱의 로컬 설정 또는 지원되는 환경 변수에 LLM 키를 먼저 설정하세요."
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

WEB_GUI_EVENT_RETENTION: Final[int] = 512
"""Maximum append-only web lifecycle events retained for SSE replay."""

WEB_GUI_SSE_HEARTBEAT_SECONDS: Final[float] = 10.0
"""Maximum quiet period before an SSE heartbeat comment is emitted."""

WEB_GUI_SSE_REFRESH_SECONDS: Final[float] = 0.25
"""Server-side source refresh cadence while at least one SSE client is open."""

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
_CONTEXTUAL_TRANSFER_REPLAY_LIMIT: Final[int] = 256
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

_MICROMACHINE_SMOKE_SCRIPT_MAX_BYTES: Final[int] = 1024 * 1024
"""Maximum source launcher size admitted into the immutable launch snapshot."""

_MICROMACHINE_VALIDATED_SCRIPT_DIR_ENV: Final[str] = (
    "VOI_MICROMACHINE_VALIDATED_SCRIPT_DIR"
)
"""Internal directory binding used only with a validated stdin launcher."""

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


class _ContextualTransferIdentityMismatchError(ValueError):
    """Raised when one replay identity is reused for a different choice."""


class _ContextualTransferReplayCapacityError(RuntimeError):
    """Raised when every bounded replay slot is still in flight."""


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
    publish_frame_resolver: Callable[[], int | None] | None
    update_id: str | None
    future: concurrent.futures.Future[Mapping[str, object]]
    cancel_event: threading.Event
    deadline_monotonic: float | None = None
    emergency: bool = False
    emergency_epoch: int = 0
    accepted_at_unix_ns: int = 0
    acceptance_ordinal: int = 0
    publish_committed: bool = False
    contextual_transfer: ContextualTransferRequest | None = None
    contextual_status_resolver: (
        Callable[[], Mapping[str, object]] | None
    ) = None


@dataclass(frozen=True)
class _ContextualTransferReplay:
    """One in-flight or completed replay-safe contextual transfer."""

    payload_fingerprint: str
    future: concurrent.futures.Future[Mapping[str, object]]


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
    _SCOPE_EPOCH_HISTORY_RETENTION = 128
    _RETIRED_OPAQUE_EPOCH_RETENTION = 16
    _PER_SCOPE_OPERATION_RETENTION = 64
    _GLOBAL_OPERATION_RETENTION = (
        _SCOPE_RETENTION * _PER_SCOPE_OPERATION_RETENTION
    )
    _GLOBAL_OPERATION_HISTORY_RETENTION = (
        _SCOPE_EPOCH_HISTORY_RETENTION
        * _PER_SCOPE_OPERATION_RETENTION
    )
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
        self._scope_event_high_water: dict[str, int] = {}
        self._scope_epochs: dict[str, str] = {}
        self._retired_scope_epochs: dict[str, deque[str]] = {}
        self._opaque_epoch_history_saturated: set[str] = set()
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

    def _admit_scope(self, scope_id: str, session_epoch: str = "") -> bool:
        if scope_id not in self._scope_epoch_history:
            if (
                len(self._scope_epoch_history)
                >= self._SCOPE_EPOCH_HISTORY_RETENTION
            ):
                return False
            self._scope_epoch_history[scope_id] = str(session_epoch or "")
            self._scope_epoch_history_order.append(scope_id)
            return True
        if session_epoch:
            self._scope_epoch_history[scope_id] = str(session_epoch)
        return True

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
    ) -> bool:
        return self._admit_scope(scope_id, session_epoch)

    @staticmethod
    def _numeric_epoch(session_epoch: str) -> int | None:
        try:
            return int(session_epoch)
        except (TypeError, ValueError):
            return None

    def _reset_scope_epoch(self, scope_id: str, session_epoch: str) -> None:
        previous_epoch = (
            self._scope_epochs.get(scope_id, "")
            or self._scope_epoch_history.get(scope_id, "")
        )
        if previous_epoch and previous_epoch != session_epoch:
            if self._numeric_epoch(previous_epoch) is None:
                retired = self._retired_scope_epochs.setdefault(
                    scope_id,
                    deque(),
                )
                if previous_epoch not in retired:
                    retired.append(previous_epoch)
                if (
                    len(retired)
                    >= self._RETIRED_OPAQUE_EPOCH_RETENTION
                ):
                    self._opaque_epoch_history_saturated.add(scope_id)
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
        incoming_numeric = self._numeric_epoch(incoming_epoch)
        current_numeric = self._numeric_epoch(current_epoch)
        if incoming_numeric is not None and current_numeric is not None:
            return incoming_numeric < current_numeric
        if (incoming_numeric is None) != (current_numeric is None):
            return True
        return scope_id in self._opaque_epoch_history_saturated

    @staticmethod
    def _scope_capacity_rejected_result(
        result: dict[str, object],
    ) -> dict[str, object]:
        result["enabled"] = False
        result["status"] = "scope_capacity_rejected"
        result["error"] = (
            "MicroMachine operation scope capacity is exhausted."
        )
        result["operation_registry_authoritative"] = False
        result["operation_timeline_status"] = "scope_capacity_rejected"
        result["operations"] = []
        result["operation_summary"] = _micromachine_operation_summary([])
        result["battlefield_overview"] = None
        result["operation_events"] = []
        result["operation_event_latest_seq"] = 0
        return result

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
            else int(self._scope_event_high_water.get(scope_id, 0))
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

    def _snapshot_fits_family_history(
        self,
        operations: Sequence[Mapping[str, object]],
        *,
        scope_id: str,
        session_epoch: str,
    ) -> bool:
        """Refuse new identities instead of deleting replay tombstones."""

        new_families = {
            (scope_id, session_epoch, operation_id)
            for operation in operations
            if (
                (operation_id := str(
                    operation.get("operation_id", "") or ""
                ).strip())
                and max(
                    0,
                    _int_or_none(
                        operation.get("operation_generation")
                    )
                    or 0,
                )
                > 0
                and (
                    scope_id,
                    session_epoch,
                    operation_id,
                )
                not in self._generation_high_water
            )
        }
        return (
            len(self._family_order) + len(new_families)
            <= self._GLOBAL_OPERATION_HISTORY_RETENTION
        )

    @staticmethod
    def _family_capacity_rejected_result(
        result: dict[str, object],
    ) -> dict[str, object]:
        result["enabled"] = False
        result["status"] = "operation_history_capacity_rejected"
        result["error"] = (
            "MicroMachine operation identity history capacity is exhausted."
        )
        result["operation_registry_authoritative"] = False
        result["operation_timeline_status"] = (
            "operation_history_capacity_rejected"
        )
        result["operations"] = []
        result["operation_summary"] = _micromachine_operation_summary([])
        result["battlefield_overview"] = None
        result["operation_events"] = []
        result["operation_event_latest_seq"] = 0
        return result

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
                result["operation_event_latest_seq"] = int(
                    self._scope_event_high_water.get(scope_id, 0)
                )
                return result
            if not self._admit_scope(scope_id):
                return self._scope_capacity_rejected_result(result)
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
            if not self._snapshot_fits_family_history(
                operations,
                scope_id=scope_id,
                session_epoch=session_epoch,
            ):
                return self._family_capacity_rejected_result(result)
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
                    self._scope_event_high_water[scope_id] = self._seq
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
                else int(self._scope_event_high_water.get(scope_id, 0))
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
        self._visible_launch_proof: dict[str, object] = {}
        self._sc2_launch_receipt_path = Path(
            os.environ.get(
                "VOI_SC2_LAUNCH_RECEIPT",
                str(
                    Path.home()
                    / "Library"
                    / "Application Support"
                    / "voiStarcraft2"
                    / "sc2-launch-receipt.json"
                ),
            )
        ).expanduser()
        candidate_script = script_path.strip()
        self._requires_source_provenance = not candidate_script
        default_root = (
            _REPO_ROOT
            if self._requires_source_provenance
            and os.path.exists(os.path.join(_REPO_ROOT, ".git"))
            else ""
        )
        self._cwd = cwd.strip() or default_root or os.getcwd()
        if candidate_script and not os.path.isabs(candidate_script):
            candidate_script = os.path.join(self._cwd, candidate_script)
        self._script_path = candidate_script or (
            os.path.join(
                default_root,
                _MICROMACHINE_SMOKE_SCRIPT_RELATIVE_PATH,
            )
            if default_root
            else ""
        )
        self._launch_available = bool(self._script_path)

    def _validated_source_launcher_unlocked(
        self,
    ) -> tuple[BinaryIO, str] | None:
        if not self._requires_source_provenance:
            return None

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._script_path, flags)
            with os.fdopen(descriptor, "rb") as source:
                admitted_stat = os.fstat(source.fileno())
                if not stat.S_ISREG(admitted_stat.st_mode):
                    self._launch_available = False
                    return None
                admitted_payload = source.read(
                    _MICROMACHINE_SMOKE_SCRIPT_MAX_BYTES + 1
                )
                if (
                    len(admitted_payload)
                    > _MICROMACHINE_SMOKE_SCRIPT_MAX_BYTES
                ):
                    self._launch_available = False
                    return None

                repository_root = source_repository_root()
                validated_path = (
                    os.path.join(
                        os.fspath(repository_root),
                        _MICROMACHINE_SMOKE_SCRIPT_RELATIVE_PATH,
                    )
                    if repository_root is not None
                    else ""
                )
                if not (
                    validated_path
                    and os.path.realpath(self._script_path)
                    == os.path.realpath(validated_path)
                ):
                    self._launch_available = False
                    return None

                current_path_stat = os.lstat(self._script_path)
                current_descriptor_stat = os.fstat(source.fileno())
                source.seek(0)
                current_payload = source.read(
                    _MICROMACHINE_SMOKE_SCRIPT_MAX_BYTES + 1
                )
        except OSError:
            self._launch_available = False
            return None

        admitted_identity = (
            admitted_stat.st_dev,
            admitted_stat.st_ino,
            stat.S_IMODE(admitted_stat.st_mode),
            admitted_stat.st_size,
        )
        current_path_identity = (
            current_path_stat.st_dev,
            current_path_stat.st_ino,
            stat.S_IMODE(current_path_stat.st_mode),
            current_path_stat.st_size,
        )
        current_descriptor_identity = (
            current_descriptor_stat.st_dev,
            current_descriptor_stat.st_ino,
            stat.S_IMODE(current_descriptor_stat.st_mode),
            current_descriptor_stat.st_size,
        )
        if (
            admitted_identity != current_path_identity
            or admitted_identity != current_descriptor_identity
            or admitted_payload != current_payload
        ):
            self._launch_available = False
            return None

        snapshot = tempfile.TemporaryFile(mode="w+b")
        try:
            snapshot.write(admitted_payload)
            snapshot.flush()
            snapshot.seek(0)
        except OSError:
            snapshot.close()
            self._launch_available = False
            return None
        self._launch_available = True
        return snapshot, os.path.dirname(validated_path)

    def _spawn_process_unlocked(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        launcher_input: BinaryIO | None,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            argv,
            cwd=self._cwd,
            env=env,
            stdin=launcher_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def start(
        self,
        blackboard_dir: str = "",
        enemy_difficulty: int = DEFAULT_MICROMACHINE_LIVE_ENEMY_DIFFICULTY,
        sc2_launch_nonce: str = "",
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
            validated_launcher: BinaryIO | None = None
            validated_script_dir = ""
            if self._requires_source_provenance:
                validated = self._validated_source_launcher_unlocked()
                if validated is not None:
                    validated_launcher, validated_script_dir = validated
            else:
                self._launch_available = bool(self._script_path)
            if not self._launch_available:
                self._status = "blocked"
                self._error = (
                    "MicroMachine launch is available only from a source "
                    "checkout with current Git provenance."
                )
                return self._snapshot_unlocked()
            if (
                validated_launcher is None
                and not os.path.isfile(self._script_path)
            ):
                self._status = "failed"
                self._error = (
                    "MicroMachine launcher script not found: "
                    f"{self._script_path}"
                )
                return self._snapshot_unlocked()
            try:
                visible_launch_proof = read_sc2_launch_receipt(
                    self._sc2_launch_receipt_path,
                    sc2_launch_nonce,
                )
                sc2_executable = resolve_required_sc2_executable()
            except ImportError as error:
                self._status = "failed"
                self._error = f"Live cockpit dependency unavailable: {error}"
                self._visible_launch_proof = {}
                if validated_launcher is not None:
                    validated_launcher.close()
                return self._snapshot_unlocked()
            except RuntimeError as error:
                self._status = "blocked"
                self._error = str(error)
                self._visible_launch_proof = {}
                if validated_launcher is not None:
                    validated_launcher.close()
                return self._snapshot_unlocked()
            allowed_environment_keys = (
                "HOME",
                "USER",
                "LOGNAME",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "LC_CTYPE",
                "__CF_USER_TEXT_ENCODING",
            )
            env = {
                key: os.environ[key]
                for key in allowed_environment_keys
                if os.environ.get(key)
            }
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
            env["BLACKBOARD_DIR"] = root
            env["SC2_ROOT"] = DEFAULT_SC2_INSTALL_PATH
            env["SC2_EXECUTABLE"] = str(sc2_executable)
            env["SC2_REQUIRED_BASE"] = str(REQUIRED_SC2_BASE)
            env["VOI_SC2_CONNECT_PORT"] = str(DEFAULT_SC2_API_PORT)
            env["SC2_CLEAN_PORTS_BEFORE_LAUNCH"] = "0"
            env["SMOKE_KEEP_RUNNING_AFTER_PASS"] = "1"
            env["SMOKE_ENEMY_DIFFICULTY"] = str(difficulty)
            max_attempts = os.environ.get(
                _MICROMACHINE_UI_SMOKE_MAX_ATTEMPTS_ENV,
                "1",
            )
            env["SMOKE_MAX_ATTEMPTS"] = max_attempts
            self._runtime_instance_id = uuid.uuid4().hex
            env["VOI_MICROMACHINE_RUNTIME_INSTANCE_ID"] = (
                self._runtime_instance_id
            )
            launch_arguments = [
                "--live-hold",
                "--fresh-live-session",
                "--blackboard-dir",
                root,
                "--enemy-difficulty",
                str(difficulty),
                "--max-attempts",
                max_attempts,
            ]
            if validated_launcher is not None:
                env[_MICROMACHINE_VALIDATED_SCRIPT_DIR_ENV] = (
                    validated_script_dir
                )
                argv = ["/bin/bash", "-s", "--", *launch_arguments]
            else:
                argv = ["bash", self._script_path, *launch_arguments]
            try:
                telemetry_path = os.path.realpath(
                    os.path.join(root, "latest_telemetry.json")
                )
                baseline = _read_micromachine_telemetry_file(telemetry_path)
                self._launch_telemetry_baseline = (
                    baseline[1] if baseline is not None else None
                )
                self._launch_started_at_ns = time.time_ns()
                self._process = self._spawn_process_unlocked(
                    argv,
                    env=env,
                    launcher_input=validated_launcher,
                )
                self._visible_launch_proof = {
                    (
                        "sc2_pid"
                        if key == "pid"
                        else key
                    ): visible_launch_proof.get(key)
                    for key in (
                        "accepted",
                        "bootstrap_accepted",
                        "pid",
                        "port",
                        "base",
                        "process_created",
                        "api_ready",
                        "window_created",
                        "window_onscreen",
                        "frontmost",
                        "screen_locked",
                        "render_verified",
                        "window_id",
                        "window_width",
                        "window_height",
                    )
                }
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
                self._visible_launch_proof = {}
                return self._snapshot_unlocked()
            finally:
                if validated_launcher is not None:
                    validated_launcher.close()
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
            "enabled": self._launch_available,
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
            **self._visible_launch_proof,
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
        self._micromachine_request_lock = threading.RLock()
        self._micromachine_requests: dict[
            str,
            _MicroMachineModulationRequest,
        ] = {}
        self._contextual_transfer_replay_lock = threading.Lock()
        self._contextual_transfer_replays: dict[
            tuple[str, str],
            _ContextualTransferReplay,
        ] = {}
        self._contextual_transfer_replay_order: deque[
            tuple[str, str]
        ] = deque()
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
        publish_frame_resolver: Callable[[], int | None] | None = None,
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
            publish_frame_resolver=publish_frame_resolver,
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

    def submit_micromachine_contextual_transfer(
        self,
        contextual_request: ContextualTransferRequest | Mapping[str, object],
        *,
        blackboard_dir: str = "",
        status_resolver: Callable[[], Mapping[str, object]],
    ) -> Mapping[str, object]:
        """Queue one typed transfer and replay the same identity safely."""

        if not callable(status_resolver):
            raise TypeError("contextual transfer status_resolver must be callable.")
        request_contract = (
            contextual_request
            if isinstance(contextual_request, ContextualTransferRequest)
            else ContextualTransferRequest.from_mapping(contextual_request)
        )
        root = _clean_blackboard_dir(
            blackboard_dir,
            self._micromachine_blackboard_dir,
        )
        replay_key = (os.path.realpath(root), request_contract.request_id)
        payload_fingerprint = request_contract.replay_fingerprint()
        request: _MicroMachineModulationRequest | None = None
        with self._contextual_transfer_replay_lock:
            replay = self._contextual_transfer_replays.get(replay_key)
            if replay is not None:
                if replay.payload_fingerprint != payload_fingerprint:
                    raise _ContextualTransferIdentityMismatchError(
                        "request_identity_mismatch"
                    )
                future = replay.future
            else:
                self._prune_contextual_transfer_replays(
                    target_size=_CONTEXTUAL_TRANSFER_REPLAY_LIMIT - 1
                )
                if (
                    len(self._contextual_transfer_replays)
                    >= _CONTEXTUAL_TRANSFER_REPLAY_LIMIT
                ):
                    raise _ContextualTransferReplayCapacityError(
                        "contextual_transfer_replay_capacity"
                    )
                future = concurrent.futures.Future()
                replay = _ContextualTransferReplay(
                    payload_fingerprint=payload_fingerprint,
                    future=future,
                )
                self._contextual_transfer_replays[replay_key] = replay
                self._contextual_transfer_replay_order.append(replay_key)
                request = _MicroMachineModulationRequest(
                    text=(
                        "Canonical contextual transfer "
                        f"{request_contract.source_operation_id} -> "
                        f"{request_contract.destination_operation_id}"
                    ),
                    blackboard_dir=root,
                    provider_output=None,
                    allow_smoke_keyword_provider=False,
                    semantic_scope=None,
                    commander_context={},
                    ttl_seconds=None,
                    current_frame=None,
                    publish_frame_resolver=None,
                    update_id=request_contract.request_id,
                    future=future,
                    cancel_event=threading.Event(),
                    deadline_monotonic=(
                        time.monotonic()
                        + _MICROMACHINE_SYNC_PUBLISH_DEADLINE_SECONDS
                    ),
                    contextual_transfer=request_contract,
                    contextual_status_resolver=status_resolver,
                )
        if request is not None:
            try:
                self._accept_micromachine_request(request)
            except Exception:
                with self._contextual_transfer_replay_lock:
                    self._forget_contextual_transfer_replay(
                        replay_key,
                        replay,
                    )
                raise
        try:
            return future.result(timeout=_MICROMACHINE_REQUEST_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            if request is not None:
                with self._micromachine_request_lock:
                    publish_committed = request.publish_committed
                    if not publish_committed:
                        request.cancel_event.set()
                        if not future.done():
                            future.set_exception(
                                concurrent.futures.TimeoutError(
                                    "contextual transfer request timed out"
                                )
                            )
                        if (
                            self._micromachine_requests.get(
                                request.update_id or ""
                            )
                            is request
                        ):
                            del self._micromachine_requests[
                                request.update_id or ""
                            ]
                        with self._contextual_transfer_replay_lock:
                            self._forget_contextual_transfer_replay(
                                replay_key,
                                replay,
                            )
                if publish_committed:
                    return future.result()
            raise

    def _forget_contextual_transfer_replay(
        self,
        replay_key: tuple[str, str],
        replay: _ContextualTransferReplay,
    ) -> None:
        if self._contextual_transfer_replays.get(replay_key) is replay:
            del self._contextual_transfer_replays[replay_key]
        self._contextual_transfer_replay_order = deque(
            key
            for key in self._contextual_transfer_replay_order
            if key != replay_key
        )

    def _prune_contextual_transfer_replays(
        self,
        *,
        target_size: int = _CONTEXTUAL_TRANSFER_REPLAY_LIMIT,
    ) -> None:
        retained: deque[tuple[str, str]] = deque()
        seen: set[tuple[str, str]] = set()
        while self._contextual_transfer_replay_order:
            replay_key = self._contextual_transfer_replay_order.popleft()
            if replay_key in seen:
                continue
            seen.add(replay_key)
            replay = self._contextual_transfer_replays.get(replay_key)
            if replay is None:
                continue
            if (
                len(self._contextual_transfer_replays) > target_size
                and replay.future.done()
            ):
                del self._contextual_transfer_replays[replay_key]
                continue
            retained.append(replay_key)
        self._contextual_transfer_replay_order = retained

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
        publish_frame_resolver: Callable[[], int | None] | None = None,
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
            publish_frame_resolver=publish_frame_resolver,
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
        publish_frame_resolver: Callable[[], int | None] | None = None,
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
            publish_frame_resolver=publish_frame_resolver,
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
        telemetry_document = _telemetry_to_mapping(telemetry)
        battlefield_identity = _mapping_child(
            _mapping_child(
                telemetry_document,
                "battlefield_overview",
            ),
            "identity",
        )
        battlefield_session_epoch = str(
            battlefield_identity.get("session_epoch", "") or ""
        ).strip()
        payload["battlefield_session_epoch"] = battlefield_session_epoch
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
                "battlefield_session_epoch",
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
            "battlefield_session_epoch": battlefield_session_epoch,
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
            if request.contextual_transfer is not None:
                payload = self._publish_micromachine_contextual_transfer(
                    request,
                    blackboard_dir=root,
                )
            else:
                payload = self._publish_micromachine_modulation(
                    request.text,
                    blackboard_dir=root,
                    provider_output=request.provider_output,
                    allow_smoke_keyword_provider=request.allow_smoke_keyword_provider,
                    semantic_scope=request.semantic_scope,
                    commander_context=commander_context,
                    ttl_seconds=request.ttl_seconds,
                    current_frame=request.current_frame,
                    publish_frame_resolver=request.publish_frame_resolver,
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

    def _publish_micromachine_contextual_transfer(
        self,
        request: _MicroMachineModulationRequest,
        *,
        blackboard_dir: str,
    ) -> Mapping[str, object]:
        """Revalidate and publish one typed transfer under the writer lock."""

        from starcraft_commander.micromachine_runtime import (
            MicroMachineFilesystemBlackboard,
        )

        contract = request.contextual_transfer
        status_resolver = request.contextual_status_resolver
        if contract is None or not callable(status_resolver):
            raise ValueError("contextual transfer request is incomplete.")
        with self._micromachine_request_lock:
            if request.cancel_event.is_set():
                raise _MicroMachinePublishCancelledError(
                    "Contextual transfer was cancelled before revalidation."
                )
            status = status_resolver()
            if not isinstance(status, Mapping):
                raise ContextualTransferRejectedError(
                    "authoritative_status_unavailable",
                    "The launcher-validated status resolver returned no mapping.",
                )
            backend = MicroMachineFilesystemBlackboard(blackboard_dir)
            current_update = backend.read_latest_update(
                current_frame=contract.projection_frame,
            )
            if current_update is None:
                raise ContextualTransferRejectedError(
                    "current_vector_unavailable",
                    "The latest MicroMachine vector is unavailable.",
                )
            preparation = prepare_contextual_transfer(
                contract,
                status=status,
                current_vector=current_update.vector.to_dict(),
            )
            request.text = preparation.command_text
            request.provider_output = preparation.provider_output
            request.current_frame = preparation.current_frame
            request.publish_frame_resolver = None
            payload = dict(
                self._publish_micromachine_modulation(
                    preparation.command_text,
                    blackboard_dir=blackboard_dir,
                    provider_output=preparation.provider_output,
                    commander_context={},
                    current_frame=preparation.current_frame,
                    update_id=contract.request_id,
                    request=request,
                )
            )
            payload["contextual_transfer"] = {
                "schema_version": contract.schema_version,
                "choice_id": contract.choice_id,
                "request_id": contract.request_id,
                "action": contract.action,
                "source_operation_id": contract.source_operation_id,
                "destination_operation_id": contract.destination_operation_id,
                "requested_count": contract.requested_count,
                "stage": (
                    "published" if payload.get("ok") is True else "blocked"
                ),
            }
            return payload

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


class _BridgedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the web GUI bridge for its handlers."""

    daemon_threads = True
    _OPERATION_EVENT_SCOPE_RETENTION = 8
    _OPERATION_EVENT_SCOPE_HISTORY_RETENTION = 128
    _PENDING_OPERATION_EVENT_RETENTION = 192

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
        self._materialized_operation_event_scopes: set[str] = set()
        self._pending_operation_events: dict[
            str,
            dict[int, dict[str, object]],
        ] = {}
        self._failed_event_sources: set[str] = set()
        self.shutdown_event = threading.Event()
        super().__init__(server_address, handler_class)

    def handle_error(
        self,
        request: object,
        client_address: tuple[str, int],
    ) -> None:
        """Suppress expected client disconnects without hiding server faults."""

        error = sys.exc_info()[1]
        if isinstance(
            error,
            (BrokenPipeError, ConnectionResetError, TimeoutError),
        ):
            return
        super().handle_error(request, client_address)

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

    def _admit_operation_event_scope_locked(self, scope_id: str) -> bool:
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
        if (
            len(self._observed_operation_event_high_water)
            >= self._OPERATION_EVENT_SCOPE_HISTORY_RETENTION
        ):
            return False
        self._observed_operation_event_high_water[normalized_scope] = 0
        self._observed_operation_event_history_order.append(normalized_scope)
        return True

    def admit_operation_event_scope(self, scope_id: str) -> bool:
        """Atomically reserve one bounded replay scope."""

        with self._event_source_lock:
            return self._admit_operation_event_scope_locked(scope_id)

    def has_admitted_operation_event_scope(self, scope_id: str) -> bool:
        """Return whether one scope already owns bounded replay history."""

        normalized_scope = str(scope_id or "")
        if not normalized_scope:
            return False
        with self._event_source_lock:
            return (
                normalized_scope
                in self._observed_operation_event_high_water
            )

    def remember_operation_event_high_water(
        self,
        scope_id: str,
        timeline_seq: int,
    ) -> bool:
        """Retain a bounded replay cursor or fail closed for a novel scope."""

        normalized_scope = str(scope_id or "")
        normalized_seq = max(0, int(timeline_seq))
        with self._event_source_lock:
            if not self._admit_operation_event_scope_locked(
                normalized_scope
            ):
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
            if (
                normalized_scope
                not in self._materialized_operation_event_scopes
            ):
                return False, 0
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
        event: Mapping[str, object],
    ) -> None:
        """Retain lifecycle events until one SSE write succeeds."""

        normalized_scope = str(scope_id or "")
        payload = event.get("payload")
        timeline_seq = (
            _web_event_int(payload.get("timeline_seq"), 0)
            if isinstance(payload, Mapping)
            else 0
        )
        pending_key = timeline_seq or max(
            0,
            int(event.get("event_seq", 0)),
        )
        if not normalized_scope or pending_key <= 0:
            return
        with self._event_source_lock:
            pending = self._pending_operation_events.setdefault(
                normalized_scope,
                {},
            )
            pending[pending_key] = deepcopy(dict(event))
            while len(pending) > self._PENDING_OPERATION_EVENT_RETENTION:
                pending.pop(min(pending), None)

    def prepare_authoritative_operation_replay(
        self,
        scope_id: str,
        *,
        snapshot_cursor: int,
    ) -> tuple[int, tuple[dict[str, object], ...]]:
        """Prepare a subscriber-local replay independent of journal retention."""

        normalized_scope = str(scope_id or "")
        normalized_cursor = max(0, int(snapshot_cursor))
        with self._event_source_lock:
            replay_available, retained = self.event_journal.replay_batch(
                normalized_cursor
            )
            effective_cursor = (
                normalized_cursor
                if replay_available
                else self.event_journal.latest_seq
            )
            pending = self._pending_operation_events.get(
                normalized_scope,
                {},
            )
            retained_events = [
                dict(event)
                for event in (retained if replay_available else ())
            ]
            retained_pending_keys = {
                _web_event_int(event["payload"].get("timeline_seq"), 0)
                for event in retained_events
                if (
                    str(event.get("event_type", "") or "")
                    == "operation_event"
                    and str(
                        event.get("blackboard_scope_id", "") or ""
                    )
                    == normalized_scope
                    and isinstance(event.get("payload"), Mapping)
                )
            }
            historical_pending = []
            for pending_key, event in sorted(pending.items()):
                if pending_key in retained_pending_keys:
                    continue
                replay_event = deepcopy(dict(event))
                replay_event["event_seq"] = effective_cursor
                replay_event["subscriber_local_replay"] = True
                historical_pending.append(replay_event)
            return (
                effective_cursor,
                tuple((*historical_pending, *retained_events)),
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
        if path in ("/", "/index.html", "/companion"):
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
                render_companion_page(blackboard_dir),
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
        if path == "/api/micromachine/contextual-transfer":
            self._handle_micromachine_contextual_transfer()
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
        self.send_header("Connection", "close" if once else "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        cursor = after
        snapshot_admitted = True
        try:
            if after == 0:
                while True:
                    cursor = self._write_authoritative_sse_snapshot(
                        journal,
                        blackboard_dir,
                        blackboard_scope_id,
                    )
                    snapshot_admitted = bool(
                        getattr(
                            self,
                            "_last_authoritative_snapshot_admitted",
                            False,
                        )
                    )
                    if not snapshot_admitted:
                        break
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
                server = self.server  # type: ignore[assignment]
                cursor, replay_events = (
                    server.prepare_authoritative_operation_replay(  # type: ignore[attr-defined]
                        blackboard_scope_id,
                        snapshot_cursor=after,
                    )
                )
                replay_requires_snapshot = any(
                    str(event.get("event_type", "") or "")
                    == "operation_event"
                    and str(
                        event.get("blackboard_scope_id", "") or ""
                    )
                    == blackboard_scope_id
                    for event in replay_events
                )
                replay_cursor_changed = cursor != after
                reconnect_authority_current = False
                if not replay_cursor_changed:
                    try:
                        reconnect_status = self._micromachine_status_payload(
                            blackboard_dir,
                            read_only=True,
                        )
                        reconnect_scope = str(
                            reconnect_status.get("blackboard_scope_id")
                            or blackboard_scope_id
                        )
                        reconnect_status_name = str(
                            reconnect_status.get("status", "") or ""
                        )
                        reconnect_authority_current = bool(
                            reconnect_scope == blackboard_scope_id
                            and reconnect_status.get(
                                "operation_registry_authoritative"
                            )
                            is True
                            and reconnect_status_name
                            not in {
                                "operation_history_capacity_rejected",
                                "scope_capacity_rejected",
                                "scope_identity_mismatch",
                                "source_error",
                            }
                        )
                    except Exception:  # noqa: BLE001 - snapshot reports failure.
                        reconnect_authority_current = False
                scope_was_authoritative = (
                    server.has_admitted_operation_event_scope(  # type: ignore[attr-defined]
                        blackboard_scope_id
                    )
                )
                if (
                    replay_cursor_changed
                    or replay_requires_snapshot
                    or (
                        scope_was_authoritative
                        and not reconnect_authority_current
                    )
                ):
                    cursor = self._write_authoritative_sse_snapshot(
                        journal,
                        blackboard_dir,
                        blackboard_scope_id,
                    )
                    snapshot_admitted = bool(
                        getattr(
                            self,
                            "_last_authoritative_snapshot_admitted",
                            False,
                        )
                    )
                else:
                    cursor = self._write_visible_sse_events(
                        replay_events,
                        cursor=cursor,
                        blackboard_scope_id=blackboard_scope_id,
                    )
            self._write_sse_heartbeat()
            if once:
                self.wfile.flush()
                return
            server = self.server  # type: ignore[assignment]
            while not server.shutdown_event.is_set():  # type: ignore[attr-defined]
                if not snapshot_admitted:
                    if server.shutdown_event.wait(  # type: ignore[attr-defined]
                        WEB_GUI_SSE_REFRESH_SECONDS
                    ):
                        return
                    cursor = self._write_authoritative_sse_snapshot(
                        journal,
                        blackboard_dir,
                        blackboard_scope_id,
                    )
                    snapshot_admitted = bool(
                        getattr(
                            self,
                            "_last_authoritative_snapshot_admitted",
                            False,
                        )
                    )
                    if snapshot_admitted:
                        replay_available, events = journal.replay_batch(
                            cursor
                        )
                        if replay_available:
                            cursor = self._write_visible_sse_events(
                                events,
                                cursor=cursor,
                                blackboard_scope_id=blackboard_scope_id,
                            )
                    continue
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
                        snapshot_admitted = bool(
                            getattr(
                                self,
                                "_last_authoritative_snapshot_admitted",
                                False,
                            )
                        )
                        if not snapshot_admitted:
                            break
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
            requested_scope_id = str(blackboard_scope_id or "")
            status_read_succeeded = True
            try:
                micromachine_status = self._micromachine_status_payload(
                    blackboard_dir,
                    read_only=True,
                )
            except Exception as error:  # noqa: BLE001 - snapshot remains usable.
                status_read_succeeded = False
                micromachine_status = {
                    "enabled": False,
                    "status": "source_error",
                    "blackboard_scope_id": requested_scope_id,
                    "error": _redact_sensitive_text(
                        error,
                        normalize_whitespace=True,
                    ),
                }
            status_scope_id = str(
                micromachine_status.get("blackboard_scope_id")
                or requested_scope_id
            )
            if (
                status_read_succeeded
                and status_scope_id != requested_scope_id
            ):
                status_read_succeeded = False
                micromachine_status = {
                    "enabled": False,
                    "status": "scope_identity_mismatch",
                    "blackboard_scope_id": requested_scope_id,
                    "reported_blackboard_scope_id": status_scope_id,
                    "error": (
                        "MicroMachine status scope does not match the "
                        "requested blackboard scope."
                    ),
                }
                status_scope_id = requested_scope_id
            if (
                status_read_succeeded
                and str(
                    micromachine_status.get("status", "") or ""
                )
                in {
                    "operation_history_capacity_rejected",
                    "scope_capacity_rejected",
                    "scope_identity_mismatch",
                    "source_error",
                }
            ):
                status_read_succeeded = False
            status_is_authoritative = bool(
                micromachine_status.get(
                    "operation_registry_authoritative"
                )
                is True
            )
            if (
                status_read_succeeded
                and status_is_authoritative
                and not server.admit_operation_event_scope(  # type: ignore[attr-defined]
                    requested_scope_id
                )
            ):
                status_read_succeeded = False
                micromachine_status = {
                    "enabled": False,
                    "status": "scope_capacity_rejected",
                    "blackboard_scope_id": requested_scope_id,
                    "error": (
                        "MicroMachine operation scope capacity is exhausted."
                    ),
                }
                status_scope_id = requested_scope_id
            source_materialized, _ = (
                server.operation_event_source_cursor(  # type: ignore[attr-defined]
                    status_scope_id
                )
            )
            if (
                status_read_succeeded
                and status_is_authoritative
                and not source_materialized
            ):
                # First materialization is historical hydration. It advances
                # the source cursor but emits no lifecycle event.
                self._publish_new_operation_events(
                    micromachine_status,
                    blackboard_dir=blackboard_dir,
                    publish=True,
                )
                try:
                    candidate_status = self._micromachine_status_payload(
                        blackboard_dir,
                        read_only=True,
                    )
                except Exception:  # noqa: BLE001 - retain the materialized cut.
                    candidate_status = micromachine_status
                candidate_scope_id = str(
                    candidate_status.get("blackboard_scope_id")
                    or blackboard_scope_id
                )
                candidate_status_name = str(
                    candidate_status.get("status", "") or ""
                )
                first_identity = _web_snapshot_order_identity(
                    micromachine_status
                )
                candidate_identity = _web_snapshot_order_identity(
                    candidate_status
                )
                if (
                    candidate_scope_id == status_scope_id
                    and candidate_status.get(
                        "operation_registry_authoritative"
                    )
                    is True
                    and candidate_status_name
                    not in {
                        "operation_history_capacity_rejected",
                        "scope_capacity_rejected",
                        "scope_identity_mismatch",
                        "source_error",
                    }
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
            status_accepted = False
            if status_read_succeeded and status_is_authoritative:
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
            snapshot_payload = self._authoritative_event_snapshot(
                blackboard_dir,
                micromachine_status=micromachine_status,
            )
            authoritative_status_admitted = bool(
                status_read_succeeded and status_accepted
            )
            if authoritative_status_admitted:
                snapshot_cursor, prepared_replay = (
                    server.prepare_authoritative_operation_replay(  # type: ignore[attr-defined]
                        status_scope_id,
                        snapshot_cursor=snapshot_cut,
                    )
                )
            else:
                snapshot_cursor = snapshot_cut
                prepared_replay = ()
            self._last_authoritative_snapshot_admitted = (
                authoritative_status_admitted
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
        return self._write_visible_sse_events(
            prepared_replay,
            cursor=snapshot_cursor,
            blackboard_scope_id=blackboard_scope_id,
        )

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

    def _validated_micromachine_command_frame(
        self,
        blackboard_dir: str,
    ) -> int | None:
        """Return a launcher-validated frame without consulting stale files."""

        launcher = getattr(self.server, "micromachine_launcher", None)  # type: ignore[attr-defined]
        validated_snapshot_fn = getattr(launcher, "validated_snapshot", None)
        if not callable(validated_snapshot_fn):
            return None
        validated_snapshot = validated_snapshot_fn(
            blackboard_dir=blackboard_dir
        )
        if not isinstance(
            validated_snapshot,
            _MicroMachineValidatedRuntimeSnapshot,
        ):
            return None
        runtime_snapshot = validated_snapshot.metadata
        telemetry_document = validated_snapshot.telemetry_document
        runtime_blackboard_dir = str(
            runtime_snapshot.get("blackboard_dir", "") or ""
        )
        if (
            runtime_snapshot.get("runtime_attached") is not True
            or runtime_snapshot.get("telemetry_current_for_process") is not True
            or runtime_snapshot.get("telemetry_stale_or_detached") is True
            or not runtime_blackboard_dir
            or _micromachine_blackboard_scope_id(runtime_blackboard_dir)
            != _micromachine_blackboard_scope_id(blackboard_dir)
            or not isinstance(telemetry_document, Mapping)
        ):
            return None
        frame = telemetry_document.get("frame")
        if type(frame) is not int or frame < 0:
            return None
        return frame

    def _resolved_micromachine_command_frame(
        self,
        blackboard_dir: str,
        requested_frame: int | None,
    ) -> int:
        """Bind frame-less commands to current telemetry, never stale files."""

        if requested_frame is not None:
            return requested_frame
        validated_frame = self._validated_micromachine_command_frame(
            blackboard_dir
        )
        return validated_frame if validated_frame is not None else 0

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
                    blackboard_dir,
                    read_only=True,
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
                requested_scope = _micromachine_blackboard_scope_id(
                    blackboard_dir
                )
                status = self._micromachine_status_payload(
                    blackboard_dir,
                    read_only=True,
                )
                scope = str(
                    status.get("blackboard_scope_id")
                    or requested_scope
                )
                if scope != requested_scope:
                    raise RuntimeError(
                        "MicroMachine status scope does not match the "
                        "requested blackboard scope."
                    )
                status_name = str(status.get("status", "") or "")
                status_is_authoritative = bool(
                    status.get("operation_registry_authoritative")
                    is True
                    and status_name
                    not in {
                        "operation_history_capacity_rejected",
                        "scope_capacity_rejected",
                        "scope_identity_mismatch",
                        "source_error",
                    }
                )
                if not status_is_authoritative:
                    if not server.has_admitted_operation_event_scope(  # type: ignore[attr-defined]
                        requested_scope
                    ):
                        return
                    with server._event_source_lock:  # type: ignore[attr-defined]
                        server.publish_changed_snapshot(  # type: ignore[attr-defined]
                            f"micromachine:{scope}",
                            "micromachine_status",
                            status,
                            blackboard_dir=blackboard_dir,
                        )
                    if status_name == "source_error":
                        server.publish_source_error(  # type: ignore[attr-defined]
                            f"micromachine_status:{scope}",
                            "micromachine_status",
                            {
                                "blackboard_dir": blackboard_dir,
                                "blackboard_scope_id": scope,
                                "error": str(
                                    status.get("error", "")
                                    or "MicroMachine status source failed."
                                ),
                            },
                            blackboard_dir=blackboard_dir,
                        )
                    else:
                        server.publish_source_recovered(  # type: ignore[attr-defined]
                            f"micromachine_status:{scope}",
                            "micromachine_status",
                            blackboard_dir=blackboard_dir,
                        )
                    return
                if not server.admit_operation_event_scope(  # type: ignore[attr-defined]
                    requested_scope
                ):
                    return
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
            if not server.has_admitted_operation_event_scope(  # type: ignore[attr-defined]
                scope_id
            ):
                return
            error_text = _redact_sensitive_text(
                error,
                normalize_whitespace=True,
            )
            server.publish_changed_snapshot(  # type: ignore[attr-defined]
                f"micromachine:{scope_id}",
                "micromachine_status",
                {
                    "enabled": False,
                    "status": "source_error",
                    "blackboard_dir": blackboard_dir,
                    "blackboard_scope_id": scope_id,
                    "operation_registry_authoritative": False,
                    "error": error_text,
                },
                blackboard_dir=blackboard_dir,
            )
            server.publish_source_error(  # type: ignore[attr-defined]
                f"micromachine_status:{scope_id}",
                "micromachine_status",
                {
                    "blackboard_dir": blackboard_dir,
                    "blackboard_scope_id": scope_id,
                    "error": error_text,
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
                not in server._materialized_operation_event_scopes  # type: ignore[attr-defined]
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
                server._materialized_operation_event_scopes.add(  # type: ignore[attr-defined]
                    scope_id
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
                    published_event,
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
        *,
        read_only: bool = False,
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
        runtime_blackboard_dir = (
            str(runtime_snapshot.get("blackboard_dir", "") or "")
            if isinstance(runtime_snapshot, Mapping)
            else ""
        )
        runtime_scope_matches_request = bool(
            runtime_blackboard_dir
            and _micromachine_blackboard_scope_id(runtime_blackboard_dir)
            == _micromachine_blackboard_scope_id(blackboard_dir)
        )
        if (
            runtime_claims_current_telemetry
            and not runtime_scope_matches_request
        ):
            # A live launcher owns exactly one blackboard. Never project its
            # validated telemetry into a request-controlled foreign scope.
            runtime_snapshot = dict(runtime_snapshot)
            runtime_snapshot["telemetry_current_for_process"] = False
            runtime_snapshot["telemetry_stale_or_detached"] = bool(
                runtime_snapshot.get("telemetry_present") is True
            )
            validated_telemetry_document = None
            runtime_claims_current_telemetry = False
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
            use_detached = bool(
                read_only
                or (
                    isinstance(runtime_snapshot, Mapping)
                    and runtime_snapshot.get("telemetry_present") is True
                )
            )
            if use_detached and not callable(detached_status_fn):
                payload = {
                    "enabled": True,
                    "blackboard_dir": blackboard_dir,
                    "status": "source_error",
                    "error": (
                        "Read-only MicroMachine status requires a bridge "
                        "that supports detached status projection."
                    ),
                }
            else:
                status_builder = (
                    detached_status_fn if use_detached else status_fn
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
                    sc2_launch_nonce=str(
                        document.get("sc2_launch_nonce", "") or ""
                    ),
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
        blackboard_dir = self._resolved_micromachine_blackboard_dir(
            params.get("blackboard_dir", [""])[0] or ""
        )
        requested_scope_id = _micromachine_blackboard_scope_id(
            blackboard_dir
        )
        try:
            payload = self._micromachine_status_payload(
                blackboard_dir,
                read_only=True,
            )
            reported_scope_id = str(
                payload.get("blackboard_scope_id")
                or requested_scope_id
            )
            if reported_scope_id != requested_scope_id:
                payload = {
                    "enabled": False,
                    "status": "scope_identity_mismatch",
                    "blackboard_dir": blackboard_dir,
                    "blackboard_scope_id": requested_scope_id,
                    "reported_blackboard_scope_id": reported_scope_id,
                    "error": (
                        "MicroMachine status scope does not match the "
                        "requested blackboard scope."
                    ),
                }
            elif (
                str(payload.get("status", "") or "")
                == "scope_capacity_rejected"
            ):
                payload = dict(payload)
                payload["enabled"] = False
                payload["status"] = "scope_capacity_rejected"
                payload.setdefault(
                    "error",
                    (
                        "MicroMachine operation scope capacity is "
                        "exhausted."
                    ),
                )
            self._send_json(HTTPStatus.OK, payload)
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
        requested_current_frame = current_frame
        current_frame = self._resolved_micromachine_command_frame(
            request_blackboard_dir,
            requested_current_frame,
        )
        publish_frame_resolver = (
            None
            if requested_current_frame is not None
            else lambda: self._validated_micromachine_command_frame(
                request_blackboard_dir
            )
        )
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
                        publish_frame_resolver=publish_frame_resolver,
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
                    publish_frame_resolver=publish_frame_resolver,
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

    def _handle_micromachine_contextual_transfer(self) -> None:
        submit_fn = getattr(
            self._bridge,
            "submit_micromachine_contextual_transfer",
            None,
        )
        if not callable(submit_fn):
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "accepted": False,
                    "error": (
                        "MicroMachine contextual transfer bridge is disabled."
                    ),
                },
            )
            return
        body = self._read_request_body()
        if body is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "accepted": False,
                    "error": (
                        "contextual transfer JSON request body is required."
                    ),
                },
            )
            return
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "accepted": False,
                    "error": "contextual transfer body must be valid UTF-8 JSON.",
                },
            )
            return
        if not isinstance(document, Mapping):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "accepted": False,
                    "error": "contextual transfer body must be a JSON object.",
                },
            )
            return
        allowed_fields = set(CONTEXTUAL_TRANSFER_REQUEST_FIELDS) | {
            "blackboard_dir"
        }
        unknown_fields = sorted(set(document) - allowed_fields)
        if unknown_fields:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "accepted": False,
                    "error": (
                        "contextual transfer request contains unsupported "
                        f"fields: {', '.join(unknown_fields)}"
                    ),
                },
            )
            return
        request_blackboard_dir = self._resolved_micromachine_blackboard_dir(
            str(document.get("blackboard_dir", "") or "")
        )
        try:
            contract = ContextualTransferRequest.from_mapping(
                {
                    field_name: document[field_name]
                    for field_name in CONTEXTUAL_TRANSFER_REQUEST_FIELDS
                    if field_name in document
                }
            )
            source_lock = self.server.operation_status_lock(  # type: ignore[attr-defined]
                request_blackboard_dir
            )
            with source_lock:
                payload = dict(
                    submit_fn(
                        contract,
                        blackboard_dir=request_blackboard_dir,
                        status_resolver=lambda: self._micromachine_status_payload(
                            request_blackboard_dir,
                            read_only=False,
                        ),
                    )
                )
        except _ContextualTransferIdentityMismatchError:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "accepted": False,
                    "status": "rejected",
                    "request_id": str(document.get("request_id", "") or ""),
                    "choice_id": str(document.get("choice_id", "") or ""),
                    "blocker": {
                        "code": "request_identity_mismatch",
                        "message": (
                            "request_id was already bound to a different "
                            "contextual transfer payload."
                        ),
                    },
                    "consumption_status": "not_published",
                },
            )
            return
        except _ContextualTransferReplayCapacityError:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "accepted": False,
                    "status": "busy",
                    "request_id": str(document.get("request_id", "") or ""),
                    "choice_id": str(document.get("choice_id", "") or ""),
                    "blocker": {
                        "code": "contextual_transfer_replay_capacity",
                        "message": (
                            "All contextual transfer replay slots are still "
                            "in flight; retry after one request completes."
                        ),
                    },
                    "consumption_status": "not_published",
                },
            )
            return
        except ContextualTransferRejectedError as error:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "accepted": False,
                    "status": "rejected",
                    "request_id": str(document.get("request_id", "") or ""),
                    "choice_id": str(document.get("choice_id", "") or ""),
                    "blocker": error.to_dict(),
                    "consumption_status": "not_published",
                },
            )
            return
        except ValueError as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"accepted": False, "error": str(error)},
            )
            return
        except concurrent.futures.TimeoutError:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "accepted": False,
                    "error": "contextual transfer request timed out.",
                },
            )
            return
        except Exception as error:  # noqa: BLE001 - surfaced honestly.
            self._send_internal_error(error)
            return
        payload["accepted"] = bool(payload.get("ok"))
        event_update_id, event_operation_id, event_generation, event_frame = (
            _web_event_identity(payload)
        )
        self.server.publish_event(  # type: ignore[attr-defined]
            "micromachine_submission",
            payload,
            update_id=event_update_id or contract.request_id,
            operation_id=(
                event_operation_id or contract.source_operation_id
            ),
            generation=event_generation or contract.source_generation,
            game_frame=(
                event_frame
                if event_frame >= 0
                else contract.projection_frame
            ),
            blackboard_dir=request_blackboard_dir,
        )
        self._send_json(
            HTTPStatus.ACCEPTED if payload["accepted"] else HTTPStatus.OK,
            payload,
        )

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
                    "POST /api/micromachine/modulate, "
                    "POST /api/micromachine/contextual-transfer."
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
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return


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

    def configure_micromachine_runtime(
        self,
        *,
        script_path: str = "",
        cwd: str = "",
    ) -> None:
        """Set an explicit packaged runtime launcher before the server starts."""

        with self._lifecycle_lock:
            if self._http is not None:
                raise RuntimeError(
                    "MicroMachine runtime paths cannot change after server start."
                )
            self._micromachine_launcher = _MicroMachineLaunchManager(
                script_path=script_path,
                cwd=cwd,
            )

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
    parser.add_argument("--micromachine-script", default="", help=argparse.SUPPRESS)
    parser.add_argument("--micromachine-cwd", default="", help=argparse.SUPPRESS)
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
    server.configure_micromachine_runtime(
        script_path=args.micromachine_script,
        cwd=args.micromachine_cwd,
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
