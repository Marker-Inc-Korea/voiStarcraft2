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
polls read-only JSON endpoints; polling never touches the interpreter.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

from starcraft_commander.micromachine_bridge import require_micromachine_update_id
from starcraft_commander.micromachine_command_execution import (
    classify_micromachine_command_execution,
)
from starcraft_commander.micromachine_tactical_evidence import (
    classify_micromachine_tactical_evidence,
    normalize_tactical_effect_tags,
)
from starcraft_commander.policy_modulation import (
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

DEFAULT_SC2_INSTALL_PATH: Final[str] = (
    "/Users/jinminseong/Desktop/StarCraft2/StarCraft II"
)
"""Default local StarCraft II install path used by auto live launch."""

DEFAULT_LIVE_MAP: Final[str] = "AcropolisLE"
"""Default map for opt-in legacy python-sc2 auto-launch sessions."""

DEFAULT_LIVE_DIFFICULTY: Final[str] = "easy"
"""Default difficulty for opt-in legacy python-sc2 auto-launch sessions."""

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


def _default_micromachine_blackboard_dir() -> str:
    return os.environ.get("VOI_MICROMACHINE_BLACKBOARD_DIR", "").strip() or (
        "/private/tmp/voi-mm-live"
    )


def _micromachine_compile_result_path(blackboard_dir: str) -> str:
    return os.path.join(blackboard_dir, "latest_modulation_compile_result.json")


def _new_micromachine_update_id() -> str:
    return f"voi-mm-{uuid.uuid4().hex}"


def _write_micromachine_compile_result(
    blackboard_dir: str,
    payload: Mapping[str, object],
) -> None:
    os.makedirs(blackboard_dir, exist_ok=True)
    path = _micromachine_compile_result_path(blackboard_dir)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".latest_modulation_compile_result.",
        suffix=".tmp",
        dir=blackboard_dir,
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


def _micromachine_status_payload(
    dashboard: Mapping[str, object],
    *,
    telemetry: object | None = None,
    blackboard_dir: str = "",
    compile_result: object | None = None,
) -> dict[str, object]:
    """Promote latest blackboard state into the same top-level UI contract."""

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
    return {
        "status": "published" if latest is not None else "idle",
        "dashboard": dict(dashboard),
        "update": dict(latest) if latest is not None else None,
        "intervention": intervention,
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
    }


def _micromachine_status_with_runtime_gate(
    payload: Mapping[str, object],
    *,
    runtime_snapshot: Mapping[str, object] | None,
    blackboard_dir: str,
) -> dict[str, object]:
    """Attach runtime metadata and fail closed when telemetry is detached."""

    result = dict(payload)
    if not isinstance(runtime_snapshot, Mapping):
        return result

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
            result[key] = runtime_snapshot[key]
    result["runtime_status"] = runtime_status

    telemetry_is_current = runtime_snapshot.get("telemetry_current_for_process") is True
    runtime_attached = runtime_snapshot.get("runtime_attached") is True
    if runtime_attached and telemetry_is_current:
        return result

    dashboard = result.get("dashboard", {})
    if not isinstance(dashboard, Mapping):
        dashboard = {}
    rebuilt = _micromachine_status_payload(
        dashboard,
        telemetry=None,
        blackboard_dir=blackboard_dir,
        compile_result=result.get("compile_result"),
    )
    result.update(rebuilt)
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
    return result


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
    tactical_evidence = classify_micromachine_tactical_evidence(
        latest_telemetry=evidence_telemetry,
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
        target_frame=(telemetry_frame or 0) if evidence_can_be_current else 0,
    ).to_dict()
    tactical_evidence_payload = tactical_evidence.to_dict()
    dashboard_managers = evidence_telemetry.get("managers", {})
    if not isinstance(dashboard_managers, Mapping):
        dashboard_managers = {}
    return {
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
"""Browser polling interval for ``/api/state`` and ``/api/history``."""

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

_BRIDGE_THREAD_NAME: Final[str] = "voiStarcraft2-web-gui-session-loop"
"""Daemon thread name for the bridge's asyncio loop (asserted clean in tests)."""

_SERVER_THREAD_NAME: Final[str] = "voiStarcraft2-web-gui-http-server"
"""Daemon thread name for the HTTP server's serve_forever loop."""

_STOP_SENTINEL: Final[object] = object()
"""Internal queue sentinel asking the bridge worker loop to exit."""

_MICROMACHINE_REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0
"""Maximum HTTP wait for one queued MicroMachine modulation submission."""

_MICROMACHINE_COMPILE_RESULT_FRESH_SECONDS: Final[float] = 300.0
"""How long a failed/clarifying compile result remains current in the dashboard."""

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


@dataclass(frozen=True)
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

    def __init__(self, llm_control: object | None) -> None:
        self.llm_control = llm_control

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
        output = propose(request)
        if not isinstance(output, Mapping):
            return _llm_policy_modulation_unavailable_output(
                "LLM provider가 JSON 객체가 아닌 응답을 반환했습니다."
            )
        return {**dict(output), "source": "llm"}


class _WebGuiTacticalFallbackPolicyModulationProvider:
    """Use deterministic UI DSL for live cockpit commands when LLM tooling fails."""

    source = PolicyModulationSource.UI

    def __init__(self, primary_provider: object) -> None:
        self.primary_provider = primary_provider

    def propose_policy_modulation(self, request: object) -> Mapping[str, object]:
        method = getattr(self.primary_provider, "propose_policy_modulation", None)
        if not callable(method):
            return self._fallback(request)
        output = method(request)
        if not isinstance(output, Mapping):
            return self._fallback(request)
        if not _should_use_web_gui_tactical_fallback(request, output):
            return output
        return self._fallback(request, primary_output=output)

    def _fallback(
        self,
        request: object,
        *,
        primary_output: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        from starcraft_commander.micromachine_live_session import (
            KeywordPolicyModulationProvider,
            _force_provider_output_source,
        )

        output = KeywordPolicyModulationProvider().propose_policy_modulation(request)
        if not isinstance(output, Mapping):
            return output
        forced = dict(_force_provider_output_source(output, PolicyModulationSource.UI))
        tags = list(_string_list(forced.get("tags", ())))
        if "web_gui_tactical_fallback" not in tags:
            tags.append("web_gui_tactical_fallback")
        reason = (
            str(primary_output.get("refusal_reason", "") or "").lower()
            if isinstance(primary_output, Mapping)
            else ""
        )
        if any(marker in reason for marker in ("llm 설정", "llm 키", "provider unavailable")):
            tags.append("web_gui_llm_unavailable_fallback")
        forced["tags"] = tags
        return forced


def _should_use_web_gui_tactical_fallback(
    request: object,
    output: Mapping[str, object],
) -> bool:
    """Return whether the local cockpit should rescue one LLM tooling failure."""

    text = str(getattr(request, "command_text", "") or "")
    if not _looks_like_micromachine_tactical_command(text):
        return False
    status = str(output.get("status", "") or "").strip().lower()
    reason = str(output.get("refusal_reason", "") or "").strip().lower()
    if status != "refused" and not reason:
        return False
    return not reason or any(
        marker in reason for marker in _WEB_GUI_TACTICAL_FALLBACK_REASON_MARKERS
    )


_WEB_GUI_TACTICAL_FALLBACK_REASON_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "configured llm provider",
        "failed with",
        "json 객체가 아닌",
        "llm provider",
        "llm policy modulation failed",
        "llm provider 확인",
        "llm 설정",
        "llm 키",
        "missing modulation object",
        "missing substantive",
        "no forced-tool",
        "provider unavailable",
        "required bounded tactical_task",
        "structured json",
        "tool call",
        "사용 가능하지",
        "확인하지 못",
    }
)
"""LLM/tooling failure text that the local web cockpit may rescue."""


_WEB_GUI_TACTICAL_COMMAND_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "attack",
        "defend",
        "enemy",
        "harass",
        "hold",
        "marine",
        "pressure",
        "rush",
        "scout",
        "공격",
        "러시",
        "러쉬",
        "마린",
        "버텨",
        "수비",
        "압박",
        "적 발견",
        "적발견",
        "정찰",
        "탐색",
        "탱크",
        "해병",
    }
)
"""Tactical live-QA utterance markers safe for bounded UI fallback."""


def _looks_like_micromachine_tactical_command(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    return bool(normalized) and any(
        marker in normalized for marker in _WEB_GUI_TACTICAL_COMMAND_MARKERS
    )


def _llm_policy_modulation_unavailable_output(reason: str) -> Mapping[str, object]:
    return {
        "source": "llm",
        "status": "refused",
        "refusal_reason": reason,
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


class _MicroMachineLaunchManager:
    """Start the patched MicroMachine runtime script and expose cockpit status."""

    def __init__(self, script_path: str = "", cwd: str = "") -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._status = "idle"
        self._error = ""
        self._last_line = ""
        self._blackboard_dir = _default_micromachine_blackboard_dir()
        self._launch_wall_time = 0.0
        self._cwd = cwd.strip() or _REPO_ROOT
        candidate_script = script_path.strip()
        if candidate_script and not os.path.isabs(candidate_script):
            candidate_script = os.path.join(self._cwd, candidate_script)
        self._script_path = candidate_script or os.path.join(
            _REPO_ROOT,
            _MICROMACHINE_SMOKE_SCRIPT_RELATIVE_PATH,
        )

    def start(self, blackboard_dir: str = "") -> dict[str, object]:
        """Launch MicroMachine smoke/live runtime for the selected blackboard."""

        root = _clean_blackboard_dir(blackboard_dir, self._blackboard_dir)
        with self._lock:
            self._refresh_unlocked()
            if self._process is not None and self._process.poll() is None:
                if os.path.realpath(root) != os.path.realpath(self._blackboard_dir):
                    payload = self._snapshot_unlocked()
                    payload["status"] = "blocked"
                    payload["accepted"] = False
                    payload["requested_blackboard_dir"] = root
                    payload["error"] = (
                        "MicroMachine runtime is already running with a different "
                        f"blackboard_dir: {self._blackboard_dir}"
                    )
                    return payload
                return self._snapshot_unlocked()
            self._blackboard_dir = root
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
            max_attempts = env.get(_MICROMACHINE_UI_SMOKE_MAX_ATTEMPTS_ENV, "1")
            env.setdefault("SMOKE_MAX_ATTEMPTS", max_attempts)
            argv = [
                "bash",
                self._script_path,
                "--live-hold",
                "--blackboard-dir",
                root,
                "--max-attempts",
                max_attempts,
            ]
            try:
                self._launch_wall_time = time.time()
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
                self._launch_wall_time = 0.0
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
            self._refresh_unlocked()
            return self._snapshot_unlocked()

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
                elif self._latest_telemetry_frame_unlocked() is not None:
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

    def _refresh_unlocked(self) -> None:
        process = self._process
        telemetry_frame = self._latest_telemetry_frame_unlocked()
        if process is not None and process.poll() is None:
            if telemetry_frame is not None and self._status in {"starting", "running"}:
                self._status = "connected"
            return
        if process is not None and process.poll() is not None:
            if process.returncode == 0:
                self._status = "passed"
                self._error = ""
            elif self._status not in {"failed", "passed"}:
                self._status = "failed"
                self._error = self._last_line or f"process exited {process.returncode}"
            return

    def _snapshot_unlocked(self) -> dict[str, object]:
        process = self._process
        telemetry_frame = self._latest_telemetry_frame_unlocked()
        runtime_attached = process is not None and process.poll() is None
        telemetry_current_for_process = bool(runtime_attached and telemetry_frame is not None)
        return {
            "enabled": True,
            "mode": COMMAND_MODE_MICROMACHINE,
            "status": self._status,
            "pid": process.pid if runtime_attached else None,
            "runtime_attached": runtime_attached,
            "blackboard_dir": self._blackboard_dir,
            "script_path": self._script_path,
            "last_line": self._last_line,
            "error": self._error,
            "telemetry_present": telemetry_frame is not None,
            "telemetry_current_for_process": telemetry_current_for_process,
            "telemetry_stale_or_detached": (
                telemetry_frame is not None and not telemetry_current_for_process
            ),
            "telemetry_frame": telemetry_frame,
        }

    def _latest_telemetry_frame_unlocked(self) -> int | None:
        path = os.path.join(self._blackboard_dir, "latest_telemetry.json")
        root_real = os.path.realpath(self._blackboard_dir)
        path_real = os.path.realpath(path)
        if not path_real.startswith(root_real + os.sep) or not os.path.isfile(path_real):
            return None
        try:
            with open(path_real, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(document, Mapping):
            return None
        if document.get("protocol_version") != "voi-mm-bridge/v1":
            return None
        process = self._process
        if process is not None and process.poll() is None and self._launch_wall_time:
            try:
                if os.path.getmtime(path_real) + 1.0 < self._launch_wall_time:
                    return None
            except OSError:
                return None
        frame = document.get("frame")
        return frame if type(frame) is int else None


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
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: "asyncio.Queue[object]" | None = None
        self._ready = threading.Event()

    @property
    def is_running(self) -> bool:
        """Return whether the worker loop thread is alive and accepting work."""

        thread = self._thread
        return thread is not None and thread.is_alive() and self._loop is not None

    def start(self) -> None:
        """Start the daemon loop thread; idempotent while already running."""

        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name=_BRIDGE_THREAD_NAME,
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=10.0):
            raise RuntimeError("Session loop bridge event loop failed to start in 10s.")

    def stop(self, timeout: float = 10.0) -> None:
        """Drain pending commands, stop the loop, and join the thread."""

        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            loop = self._loop
            queue = self._queue
            if thread.is_alive() and loop is not None and queue is not None:
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, _STOP_SENTINEL)
                except RuntimeError:
                    # The loop already closed on its own; just join below.
                    pass
            thread.join(timeout=timeout)
            self._thread = None

    def submit_command(self, text: str) -> None:
        """Enqueue one utterance for sequential processing (non-blocking)."""

        if not isinstance(text, str):
            raise TypeError("Web GUI command text must be a string.")
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Web GUI command text must be non-empty.")
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None or not self.is_running:
            raise RuntimeError("Session loop bridge is not running; call start() first.")
        loop.call_soon_threadsafe(queue.put_nowait, cleaned)

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
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None or not self.is_running:
            raise RuntimeError("Session loop bridge is not running; call start() first.")
        future: concurrent.futures.Future[Mapping[str, object]] = (
            concurrent.futures.Future()
        )
        request = _MicroMachineModulationRequest(
            text=cleaned,
            blackboard_dir=blackboard_dir,
            provider_output=provider_output,
            allow_smoke_keyword_provider=allow_smoke_keyword_provider,
            semantic_scope=semantic_scope,
            commander_context=dict(commander_context or {}),
            ttl_seconds=ttl_seconds,
            current_frame=current_frame,
            update_id=update_id,
            future=future,
        )
        loop.call_soon_threadsafe(queue.put_nowait, request)
        return future.result(timeout=_MICROMACHINE_REQUEST_TIMEOUT_SECONDS)

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
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None or not self.is_running:
            raise RuntimeError("Session loop bridge is not running; call start() first.")
        root = _clean_blackboard_dir(blackboard_dir, self._micromachine_blackboard_dir)
        resolved_update_id = update_id or _new_micromachine_update_id()
        future: concurrent.futures.Future[Mapping[str, object]] = (
            concurrent.futures.Future()
        )

        def observe_background_result(done: concurrent.futures.Future[Mapping[str, object]]) -> None:
            try:
                done.result()
            except Exception as error:  # noqa: BLE001 - persist async failures for UI polling.
                _write_micromachine_compile_result(
                    root,
                    _redact_json_ready(
                        {
                            "command_text": cleaned,
                            "status": "publish_failed",
                            "current_frame": current_frame,
                            "compile_result": {
                                "status": "refused",
                                "source": "llm",
                                "refusal_reason": (
                                    "MicroMachine async publish failed with "
                                    f"{type(error).__name__}: {error}"
                                ),
                                "update_id": resolved_update_id,
                            },
                            "update_id": resolved_update_id,
                            "duration_ms": 0,
                            "written_at_unix": time.time(),
                        }
                    ),
                )

        future.add_done_callback(observe_background_result)
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
        )
        loop.call_soon_threadsafe(queue.put_nowait, request)
        return {
            "accepted": True,
            "ok": True,
            "queued": True,
            "async_publish": True,
            "status": "queued",
            "command_text": cleaned,
            "update_id": resolved_update_id,
            "blackboard_dir": root,
            "consumption_status": "pending_compile",
            "message": (
                "MicroMachine publish를 백그라운드에서 시작했습니다. "
                "LLM 우선 DSL 컴파일 또는 웹 전술 fallback publish는 status polling으로 갱신됩니다."
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
        elif allow_smoke_keyword_provider:
            provider = KeywordPolicyModulationProvider()
        else:
            provider = _WebGuiTacticalFallbackPolicyModulationProvider(
                _LocalLLMPolicyModulationProvider(self._llm_control)
            )
        if semantic_scope or ttl_seconds is not None:
            provider = _SemanticScopePolicyModulationProvider(
                provider,
                semantic_scope=semantic_scope,
                ttl_seconds=ttl_seconds,
            )
        started_at = time.monotonic()
        result = MicroMachineLiveTextSession(
            MicroMachineFilesystemBlackboard(root),
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
        compile_result_for_document = payload.get("compile_result")
        if isinstance(compile_result_for_document, Mapping) and compile_update_id:
            compile_result_for_document = dict(compile_result_for_document)
            compile_result_for_document.setdefault("update_id", compile_update_id)
            payload["compile_result"] = compile_result_for_document
        compile_document: dict[str, object] = {
            "command_text": text,
            "status": str(payload.get("status", "") or ""),
            "current_frame": payload.get("current_frame"),
            "compile_result": compile_result_for_document,
            "update_id": compile_update_id,
            "command_queue": payload.get("command_queue"),
            "duration_ms": duration_ms,
            "written_at_unix": time.time(),
        }
        _write_micromachine_compile_result(
            root,
            _redact_json_ready(compile_document),
        )
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
        return payload

    def micromachine_status(self, *, blackboard_dir: str = "") -> Mapping[str, object]:
        from starcraft_commander.micromachine_runtime import (
            MicroMachineFilesystemBlackboard,
        )
        from starcraft_commander.policy_observability import (
            PolicyModulationBridgeStatus,
        )

        root = _clean_blackboard_dir(blackboard_dir, self._micromachine_blackboard_dir)
        backend = MicroMachineFilesystemBlackboard(root)
        telemetry = backend.read_latest_telemetry()
        frame = telemetry.frame if telemetry is not None else 0
        snapshot = backend.dashboard_snapshot(
            current_frame=frame,
            bridge_status=PolicyModulationBridgeStatus.CONNECTED,
        )
        compile_document = _read_micromachine_compile_result(root)
        compile_result = _latest_compile_result_payload(compile_document)
        return {
            "enabled": True,
            "blackboard_dir": root,
            **_micromachine_status_payload(
                snapshot.to_dict(),
                telemetry=telemetry,
                blackboard_dir=root,
                compile_result=compile_result,
            ),
        }

    def _run_loop(self) -> None:
        """Daemon thread body: run a private asyncio loop draining commands."""

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._queue = asyncio.Queue()
        self._ready.set()
        try:
            loop.run_until_complete(self._drain_commands())
        finally:
            self._loop = None
            self._queue = None
            asyncio.set_event_loop(None)
            loop.close()

    async def _drain_commands(self) -> None:
        """Process queued texts strictly in submission order until stopped."""

        queue = self._queue
        assert queue is not None  # Set by _run_loop before _ready fires.
        while True:
            item = await queue.get()
            if item is _STOP_SENTINEL:
                return
            if isinstance(item, _MicroMachineModulationRequest):
                self._process_one_micromachine_request(item)
                continue
            await self._process_one(str(item))

    def _process_one_micromachine_request(
        self,
        request: _MicroMachineModulationRequest,
    ) -> None:
        """Publish one MicroMachine update on the single bridge worker queue."""

        if request.future.cancelled():
            return
        try:
            payload = self._publish_micromachine_modulation(
                request.text,
                blackboard_dir=request.blackboard_dir,
                provider_output=request.provider_output,
                allow_smoke_keyword_provider=request.allow_smoke_keyword_provider,
                semantic_scope=request.semantic_scope,
                commander_context=request.commander_context,
                ttl_seconds=request.ttl_seconds,
                current_frame=request.current_frame,
                update_id=request.update_id,
            )
        except Exception as error:  # noqa: BLE001 - returned to HTTP handler.
            request.future.set_exception(error)
            return
        request.future.set_result(payload)

    async def _process_one(self, text: str) -> None:
        """Run one utterance through the session; never drop it silently."""

        try:
            outcomes = await self._session.process_text(text)
        except Exception as error:  # noqa: BLE001 - recorded honestly, never dropped.
            self._history.record(_internal_error_outcome(text, error))
            return
        for outcome in outcomes:
            self._history.record(outcome)


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
    .star-depth { animation: none; transform: none; will-change: auto; }
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
    .runtime-mode-panel, .mode-option {
      forced-color-adjust: auto; background: Canvas; color: CanvasText;
      border-color: CanvasText; box-shadow: none; backdrop-filter: none;
    }
    #send-button, #voice-button, #llm-panel button, .runtime-actions button {
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
  .runtime-actions button {
    flex: 1 1 160px; margin-top: 0 !important; padding: 10px 12px !important;
    background: rgba(255, 255, 255, 0.9) !important; color: #071225 !important;
  }
  #state-panel {
    min-width: 0; min-height: 0; max-height: calc(100vh - 160px); overflow-y: auto;
    display: flex; flex-direction: column; gap: 16px; scrollbar-gutter: stable;
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 28px; padding: 20px; box-shadow: var(--shadow); backdrop-filter: blur(18px);
  }
  #state-panel > * { min-width: 0; }
  #state-panel h2, #llm-panel h2, #briefing-panel h2 { margin: 0; font-size: 1rem; letter-spacing: -0.02em; }
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
  .message-meta { display: block; margin-bottom: 5px; color: rgba(255, 255, 255, 0.72); font-size: 0.74rem; font-weight: 800; }
  .message-bot .message-meta { color: var(--muted); }
  .status { display: none; font-weight: 900; margin-right: 7px; white-space: nowrap; }
  .status-executed { color: __COLOR_EXECUTED__; }
  .status-partially_executed { color: __COLOR_PARTIAL__; }
  .status-blocked { color: __COLOR_BLOCKED__; }
  .status-clarification { color: __COLOR_CLARIFICATION__; }
  .status-read_only { color: __COLOR_READ_ONLY__; }
  #command-form {
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
    #command-panel { height: 68vh; min-height: 0; max-height: 68vh; }
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
    .chat-header { display: block; }
    .quick-commands { margin-top: 12px; }
    .dashboard-grid, .mode-options, .micro-scope-grid, .micro-intervention-grid, .provider-options { grid-template-columns: 1fr; }
    #command-form { flex-direction: column; }
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
        <p id="assistant-pending-status" class="assistant-pending-status" aria-live="polite"></p>
      </div>
      <div class="quick-commands">
        <button type="button" data-command="상태확인" data-i18n="quickStatus">상태확인</button>
        <button type="button" data-command="정찰보내" data-i18n="quickScout">정찰보내</button>
        <button type="button" data-command="SCV 여러개 뽑아" data-i18n="quickScv">SCV 생산</button>
        <button type="button" data-command="건물 위치 지정 가능?" data-i18n="quickPosition">위치 질문</button>
      </div>
    </div>
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
            <span class="mode-description" data-i18n="microModeDescription">채팅/음성은 LLM forced-tool DSL을 우선 사용하고, 전술 tool-call 실패 시 웹 전술 fallback DSL로 MicroMachine blackboard에 publish됩니다.</span>
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
      <div class="runtime-actions">
        <button id="runtime-start-button" type="button" data-i18n="runtimeStartButton">선택 모드 실행</button>
        <button id="live-open-button" type="button" data-i18n="runtimeOpenButton" disabled>Live GUI 열기</button>
        <button id="runtime-refresh-button" type="button" data-i18n="runtimeRefreshButton">런타임 상태 확인</button>
      </div>
    </section>
    <div id="log" aria-live="polite" role="log"></div>
    <form id="command-form">
      <input id="command-input" type="text" autocomplete="off" autofocus
             placeholder="대화하듯 입력하세요. 예: 보급고 지어 / 음성지원도 되나?">
      <button type="button" id="voice-button" title="Voice input" aria-label="Voice input">◉</button>
      <button type="submit" id="send-button" data-i18n="send">전송</button>
    </form>
  </section>
  <aside id="state-panel">
    <h2 data-i18n="dashboardTitle">전장 대시보드</h2>
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
      <div id="micromachine-status" aria-live="polite">왼쪽 커맨더 채팅 또는 고급 직접 publish 입력을 기다리는 중입니다.</div>
      <section id="micromachine-intervention-dashboard" aria-live="polite">
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
var trimmedChatEvents = 0;
var recentEvents = [];
var archivedChatEvents = [];
var pendingMicroMachineAsyncUpdates = {};
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
var latestMicroMachinePlanText = "";
var latestState = null;
var briefingAdviceToggleEnabled = false;
var recognition = null;
var isRecording = false;
var liveGuiUrl = "";
var COMMAND_MODE_MICROMACHINE = "__COMMAND_MODE_MICROMACHINE__";
var COMMAND_MODE_LEGACY_COMMANDER = "__COMMAND_MODE_LEGACY_COMMANDER__";
var activeCommandMode = COMMAND_MODE_MICROMACHINE;
var LLM_MODELS = {
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
    microModeDescription: "채팅/음성은 LLM forced-tool DSL을 우선 사용하고, 전술 tool-call 실패 시 웹 전술 fallback DSL로 MicroMachine blackboard에 publish됩니다.",
    legacyModeLabel: "Legacy python-sc2 commander",
    legacyModeDescription: "이전 데모 호환 모드입니다. MicroMachine이 아니며, LLM 키가 있어야 /api/command로 전송됩니다.",
    legacyModeWarning: "Legacy mode는 MicroMachine이 아닙니다. SC2 실행/명령이 python-sc2 demo 경로로 가므로 MicroMachine QA와 혼동하지 마세요.",
    runtimeIdleMicro: "MicroMachine 런타임 대기 중입니다. 선택 모드 실행을 누르면 SC2/MicroMachine smoke session을 시작합니다.",
    runtimeIdleLegacy: "Legacy python-sc2 런타임 대기 중입니다. 키 설정 후 선택 모드 실행을 누르면 legacy demo를 시작합니다.",
    runtimeStartButton: "선택 모드 실행",
    runtimeOpenButton: "Live GUI 열기",
    runtimeRefreshButton: "런타임 상태 확인",
    runtimeStarting: "선택한 런타임 시작 중",
    runtimeRunning: "선택한 런타임 실행 중",
    runtimeConnected: "MicroMachine telemetry 연결됨",
    runtimePassed: "MicroMachine smoke 통과",
    runtimeDetachedTelemetry: "MicroMachine telemetry 파일은 있지만 현재 런타임 프로세스에 붙어 있지 않음",
    runtimeReady: "Legacy live GUI 준비됨",
    runtimeBlocked: "런타임 시작 보류",
    runtimeFailed: "런타임 시작 실패",
    quickStatus: "상태확인",
    quickScout: "정찰보내",
    quickScv: "SCV 생산",
    quickPosition: "위치 질문",
    send: "전송",
    dashboardTitle: "전장 대시보드",
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
    voiceUnsupported: "이 브라우저는 음성 인식을 지원하지 않습니다.",
    voiceNoResult: "음성이 인식되지 않았습니다.",
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
    startupGuide: "🚀 시작 메뉴얼\\n1. 기본 모드는 MicroMachine policy cockpit입니다. 채팅/음성 입력은 LLM forced-tool DSL을 우선 사용해 blackboard에 publish됩니다.\\n2. LLM이 tool-call/JSON을 반환하지 못해도 정찰/공격/수비 같은 전술 명령은 웹 전술 fallback DSL로 publish됩니다.\\n3. 우측 MicroMachine 패널에서 blackboard directory와 semantic scope를 확인하거나 조정하세요.\\n4. Legacy python-sc2 commander는 호환 모드로 직접 선택한 경우에만 /api/command를 사용합니다.\\n🎙️ 음성 버튼을 켜면 말한 내용이 현재 선택된 모드로 전송됩니다."
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
    microModeDescription: "Chat/voice prefers LLM forced-tool DSL, then publishes a bounded web tactical fallback DSL if tactical tool-calling fails.",
    legacyModeLabel: "Legacy python-sc2 commander",
    legacyModeDescription: "Compatibility mode for the older demo path. It is not MicroMachine and requires an LLM key before posting to /api/command.",
    legacyModeWarning: "Legacy mode is not MicroMachine. SC2 launch/commands go through the python-sc2 demo path, so do not use it as MicroMachine QA evidence.",
    runtimeIdleMicro: "MicroMachine runtime is idle. Click Launch selected runtime to start the SC2/MicroMachine smoke session.",
    runtimeIdleLegacy: "Legacy python-sc2 runtime is idle. Configure a key, then click Launch selected runtime to start the legacy demo.",
    runtimeStartButton: "Launch selected runtime",
    runtimeOpenButton: "Open Live GUI",
    runtimeRefreshButton: "Check runtime status",
    runtimeStarting: "Starting selected runtime",
    runtimeRunning: "Selected runtime is running",
    runtimeConnected: "MicroMachine telemetry connected",
    runtimePassed: "MicroMachine smoke passed",
    runtimeDetachedTelemetry: "MicroMachine telemetry file exists but is not attached to a running runtime",
    runtimeReady: "Legacy live GUI ready",
    runtimeBlocked: "Runtime start blocked",
    runtimeFailed: "Runtime start failed",
    quickStatus: "Status",
    quickScout: "Scout",
    quickScv: "Train SCV",
    quickPosition: "Placement Help",
    send: "Send",
    dashboardTitle: "Battlefield Dashboard",
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
    voiceUnsupported: "This browser does not support speech recognition.",
    voiceNoResult: "No speech was recognized.",
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
    startupGuide: "🚀 Startup guide\\n1. The default mode is the MicroMachine policy cockpit. Chat/voice prefers LLM forced-tool DSL and publishes to the blackboard.\\n2. If the LLM misses tool-call/JSON output, tactical scout/attack/defend commands are published through the bounded web fallback DSL.\\n3. Use the MicroMachine panel to confirm or adjust the blackboard directory and semantic scope.\\n4. Legacy python-sc2 commander uses /api/command only when explicitly selected.\\n🎙️ Voice sends recognized speech through the currently selected mode."
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
    microModeDescription: "聊天/语音优先使用 LLM forced-tool DSL；战术 tool-call 失败时会通过有界网页 fallback DSL 发布到 MicroMachine blackboard。",
    legacyModeLabel: "Legacy python-sc2 commander",
    legacyModeDescription: "旧 demo 路径的兼容模式。它不是 MicroMachine，并且需要 LLM key 才会发送到 /api/command。",
    legacyModeWarning: "Legacy mode 不是 MicroMachine。SC2 启动/命令会走 python-sc2 demo 路径，不要把它当作 MicroMachine QA 证据。",
    runtimeIdleMicro: "MicroMachine runtime 正在等待。点击启动所选 runtime 会启动 SC2/MicroMachine smoke session。",
    runtimeIdleLegacy: "Legacy python-sc2 runtime 正在等待。先设置 key，再点击启动所选 runtime。",
    runtimeStartButton: "启动所选 runtime",
    runtimeOpenButton: "打开 Live GUI",
    runtimeRefreshButton: "检查 runtime 状态",
    runtimeStarting: "正在启动所选 runtime",
    runtimeRunning: "所选 runtime 正在运行",
    runtimeConnected: "MicroMachine telemetry 已连接",
    runtimePassed: "MicroMachine smoke 已通过",
    runtimeDetachedTelemetry: "存在 MicroMachine telemetry 文件，但未连接到正在运行的 runtime",
    runtimeReady: "Legacy live GUI 已就绪",
    runtimeBlocked: "runtime 启动被阻止",
    runtimeFailed: "runtime 启动失败",
    quickStatus: "状态",
    quickScout: "侦察",
    quickScv: "生产 SCV",
    quickPosition: "位置帮助",
    send: "发送",
    dashboardTitle: "战场仪表盘",
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
    voiceUnsupported: "此浏览器不支持语音识别。",
    voiceNoResult: "未识别到语音。",
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
    startupGuide: "🚀 启动指南\\n1. 默认模式是 MicroMachine policy cockpit。聊天/语音优先使用 LLM forced-tool DSL 并发布到 blackboard。\\n2. 如果 LLM 未返回 tool-call/JSON，侦察/攻击/防守等战术命令会通过有界网页 fallback DSL 发布。\\n3. 在 MicroMachine 面板确认或调整 blackboard directory 与 semantic scope。\\n4. Legacy python-sc2 commander 只有显式选择时才使用 /api/command。\\n🎙️ 语音会通过当前选择的模式发送。"
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
  Array.prototype.forEach.call(document.querySelectorAll("[data-lang-button]"), function (button) {
    button.classList.toggle("active", button.getAttribute("data-lang-button") === currentLang);
  });
  setCommandMode(activeCommandMode);
  renderStartupGuide();
  refreshExpandableLabels();
  refreshPendingLabels();
  updateAssistantPendingState();
  renderChatTrimNote();
  if (latestState) { renderStrategyBriefing(latestState); }
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
    if (entries[i].id !== "voice-recording-entry") {
      return entries[i];
    }
  }
  return null;
}

function trimChatLog() {
  while (logBox.querySelectorAll(".log-entry").length > MAX_CHAT_EVENTS) {
    var oldestEntry = oldestTrimCandidate();
    if (!oldestEntry) { break; }
    archiveTrimmedEntry(oldestEntry);
    logBox.removeChild(oldestEntry);
    trimmedChatEvents += 1;
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
  if (ev && typeof ev.seq === "number") {
    recentEvents.push(ev);
    compactRecentEventsIfNeeded();
    if (!removePendingForCommand(ev.command_text || "")) {
      removeOldestPendingCommand();
    }
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

function appendPendingCommand(text) {
  pendingCommandSeq += 1;
  var pendingId = "pending-" + pendingCommandSeq;
  var entry = document.createElement("div");
  entry.className = "log-entry pending-entry";
  entry.id = pendingId;

  var userMessage = document.createElement("div");
  userMessage.className = "message message-user";
  userMessage.setAttribute("data-full-text", text);
  var userMeta = document.createElement("span");
  userMeta.className = "message-meta";
  userMeta.textContent = t("userLabel");
  userMessage.appendChild(userMeta);
  appendCompactText(userMessage, text, "command-text");
  entry.appendChild(userMessage);

  var botMessage = document.createElement("div");
  botMessage.className = "message message-bot message-pending";
  botMessage.setAttribute("data-full-text", t("assistantThinking"));
  botMessage.setAttribute("data-status", "pending");
  botMessage.setAttribute("role", "status");
  botMessage.setAttribute("aria-live", "polite");
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

  if (!pendingNodes[text]) { pendingNodes[text] = []; }
  pendingNodes[text].push(pendingId);
  logBox.appendChild(entry);
  trimChatLog();
  updateAssistantPendingState();
  logBox.scrollTop = logBox.scrollHeight;
}

function clearPendingMicroMachinePlan() {
  Object.keys(pendingNodes).forEach(function (key) {
    var ids = pendingNodes[key] || [];
    ids.forEach(function (pendingId) {
      var node = document.getElementById(pendingId);
      if (node) { node.remove(); }
    });
    delete pendingNodes[key];
  });
  updateAssistantPendingState();
}

function appendMicroMachinePendingPlan(text) {
  clearPendingMicroMachinePlan();
  latestMicroMachinePlanText = text;
  appendPendingCommand(text);
}

function appendVoiceRecordingBubble() {
  removeVoiceRecordingBubble();
  var entry = document.createElement("div");
  entry.className = "log-entry";
  entry.id = "voice-recording-entry";
  var userMessage = document.createElement("div");
  userMessage.className = "message message-user";
  var meta = document.createElement("span");
  meta.className = "message-meta";
  meta.textContent = t("userLabel");
  userMessage.appendChild(meta);
  userMessage.appendChild(document.createTextNode(t("voiceListening")));
  var wave = document.createElement("span");
  wave.className = "voice-wave";
  for (var i = 0; i < 5; i += 1) {
    wave.appendChild(document.createElement("span"));
  }
  userMessage.appendChild(wave);
  entry.appendChild(userMessage);
  logBox.appendChild(entry);
  trimChatLog();
  logBox.scrollTop = logBox.scrollHeight;
}

function removeVoiceRecordingBubble() {
  var existing = document.getElementById("voice-recording-entry");
  if (existing) { existing.remove(); }
}

function removePendingForCommand(text) {
  var pendingIds = pendingNodes[text];
  if (!pendingIds || !pendingIds.length) { return false; }
  var pendingId = pendingIds.shift();
  var node = document.getElementById(pendingId);
  if (node) { node.remove(); }
  if (!pendingIds.length) { delete pendingNodes[text]; }
  updateAssistantPendingState();
  return true;
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
    setLlmStatus(
      "success",
      "llmSuccessLabel",
      t("llmReady") + " (" + data.provider + " / " + data.model + ")"
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
  }
  return payload;
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
  var payload = runtimeStartPayload();
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

function updateMicroMachineBadge(intervention, status) {
  var badge = document.getElementById("micromachine-applied-badge");
  if (!badge) { return; }
  badge.className = "micro-badge micro-badge-pending";
  if (intervention && intervention.applied) {
    badge.className = "micro-badge micro-badge-applied";
    badge.textContent = t("microMachineConsumed");
    return;
  }
  if (intervention && intervention.policy_active) {
    badge.className = "micro-badge micro-badge-active";
    badge.textContent = "policy_active";
    return;
  }
  badge.textContent = status || t("microMachinePending");
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
  updateMicroMachineBadge(intervention, data && data.consumption_status);
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

function renderMicroMachineStatus(data) {
  var node = document.getElementById("micromachine-status");
  if (!node) { return; }
  if (!data || data.enabled === false) {
    node.textContent = (data && data.error) || "MicroMachine modulation disabled.";
    renderMicroMachineIntervention(data || {});
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
  node.textContent = parts.length ? parts.join(" | ") : t("microMachinePending");
  renderMicroMachineIntervention(data);
  maybeAppendMicroMachineAsyncCompletion(data);
}

function safeRenderMicroMachineStatus(data) {
  try {
    renderMicroMachineStatus(data);
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

function pollMicroMachineStatus() {
  var input = document.getElementById("micromachine-blackboard-dir");
  var suffix = authQuery;
  var directory = input ? input.value.trim() : "";
  if (directory) {
    suffix += (suffix ? "&" : "?") + "blackboard_dir=" + encodeURIComponent(directory);
  }
  fetch("/api/micromachine/status" + suffix)
    .then(parseJsonResponse)
    .then(renderMicroMachineStatus)
    .catch(function (error) {
      var node = document.getElementById("micromachine-status");
      if (node) { node.textContent = t("microMachineFailed") + ": " + error.message; }
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
  var tacticalCommand = looksLikeMicroMachineTacticalCommand(text);
  var payload = {
    text: text,
    blackboard_dir: blackboardInput ? blackboardInput.value.trim() : "",
    ui_language: currentLang || "ko",
    response_language: detectMicroMachineResponseLanguage(text),
    async_publish: !tacticalCommand
  };
  if (tacticalCommand) {
    payload.allow_smoke_keyword_provider = true;
  }
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

function rememberPendingMicroMachineAsync(text, data) {
  if (!data || !data.async_publish || !data.update_id) { return; }
  pendingMicroMachineAsyncUpdates[data.update_id] = {
    text: text,
    createdAt: Date.now()
  };
}

function maybeAppendMicroMachineAsyncCompletion(data) {
  if (!data || !pendingMicroMachineAsyncUpdates) { return; }
  var compileResult = data.compile_result || {};
  var update = data.update || {};
  var compileUpdateId = compileResult.update_id || "";
  var activeUpdateId = update.update_id || "";
  var isTerminalRefusal = Boolean(
    compileResult.refusal_reason ||
    compileResult.clarification_prompt ||
    compileResult.status === "refused" ||
    compileResult.status === "clarification_required"
  );
  var candidateUpdateIds = [];
  if (activeUpdateId) { candidateUpdateIds.push(activeUpdateId); }
  if (compileUpdateId && compileUpdateId !== activeUpdateId) {
    candidateUpdateIds.push(compileUpdateId);
  }
  candidateUpdateIds.forEach(function(updateId) {
    if (!updateId || !pendingMicroMachineAsyncUpdates[updateId]) { return; }
    var requestStatus = data.latest_request && data.latest_request.update_id === updateId
      ? data.latest_request.consumption_status
      : (updateId === activeUpdateId ? data.consumption_status : "");
    var terminalForUpdate = updateId === compileUpdateId && isTerminalRefusal;
    var isConsumed = requestStatus === "consumed";
    if (!terminalForUpdate && !isConsumed) { return; }
    var pending = pendingMicroMachineAsyncUpdates[updateId];
    delete pendingMicroMachineAsyncUpdates[updateId];
    removePendingForCommand(pending.text || "");
    var narrationData = data;
    if (isConsumed && !terminalForUpdate && compileUpdateId && compileUpdateId !== updateId) {
      narrationData = Object.assign({}, data, {
        compile_result: {},
        latest_request: null
      });
    }
    appendLog({
      command_text: pending.text,
      status: terminalForUpdate ? "clarification" : "partially_executed",
      narration: microMachineChatNarration(narrationData)
    });
  });
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

function microMachineChatNarration(data) {
  var intervention = (data && data.intervention) || {};
  var compileResult = (data && data.compile_result) || {};
  var vector = compileResult.vector || {};
  var assistantMessage = microMachineAssistantMessage(compileResult, vector);
  if (assistantMessage) {
    return assistantMessage;
  }
  var parts = [];
  if (data && data.status === "queued") {
    parts.push("LLM이 MicroMachine DSL을 해석 중입니다.");
    if (data.message) { parts.push(data.message); }
  } else if (compileResult.refusal_reason || compileResult.clarification_prompt || data && data.accepted === false) {
    parts.push("MicroMachine blackboard에 publish하지 않았습니다.");
    if (compileResult.refusal_reason) { parts.push(compileResult.refusal_reason); }
    if (compileResult.clarification_prompt) { parts.push(compileResult.clarification_prompt); }
  } else {
    parts.push(t("microMachineChatPublished"));
    parts.push("해석: " + (intervention.goal || vector.goal || (data && data.command_text) || "전술 의도"));
    parts.push("적용 증거: MicroMachine manager bias로 publish됨");
    if (data && data.provider_source) {
      parts.push("provider_source=" + data.provider_source);
    } else if (compileResult.source) {
      parts.push("provider_source=" + compileResult.source);
    }
    if (data && data.consumption_status && data.consumption_status !== "consumed") {
      parts.push(t("microMachineChatQueued") + " (" + data.consumption_status + ")");
    }
    if (data && data.consumption_status === "consumed") {
      parts.push("MicroMachine telemetry가 이 update 소비를 확인했습니다.");
    }
  }
  if (intervention.latest_update_id) { parts.push("update_id=" + intervention.latest_update_id); }
  var commandQueue = (data && data.command_queue) || intervention.command_queue || compileResult.command_queue || {};
  if (commandQueue.category || commandQueue.action) {
    var queueBits = ["command_queue"];
    if (commandQueue.category) { queueBits.push("category=" + commandQueue.category); }
    if (commandQueue.action) { queueBits.push("action=" + commandQueue.action); }
    if (commandQueue.merged_command_count) {
      queueBits.push("merged=" + commandQueue.merged_command_count);
    }
    if (commandQueue.superseded_previous) { queueBits.push("superseded_previous=true"); }
    if (commandQueue.standing_order_preserved) { queueBits.push("standing_order_preserved=true"); }
    parts.push(queueBits.join(" | "));
  }
  if (intervention.tactical_posture) { parts.push("posture=" + intervention.tactical_posture); }
  var lifetimeText = formatMicroMachineLifetime(intervention.lifetime);
  if (lifetimeText !== "-") { parts.push("lifetime=" + lifetimeText); }
  if (Array.isArray(intervention.manager_bias_domains) && intervention.manager_bias_domains.length) {
    parts.push("domains=" + intervention.manager_bias_domains.join(", "));
  }
  var gateText = formatMicroMachineAttackGate(intervention.attack_gate);
  if (gateText !== "-") {
    parts.push("attack_gate=" + gateText);
  }
  if (intervention.refusal_reason && parts.indexOf(intervention.refusal_reason) < 0) {
    parts.push(intervention.refusal_reason);
  }
  return parts.join("\\n");
}

function appendMicroMachineChatResult(text, data) {
  var removed = removePendingForCommand(text);
  if (!removed && latestMicroMachinePlanText && text !== latestMicroMachinePlanText && pendingCommandCount() > 0) {
    updateAssistantPendingState();
    return;
  }
  if (removed && text === latestMicroMachinePlanText) { latestMicroMachinePlanText = ""; }
  var accepted = data && data.accepted !== false && data.ok !== false;
  appendLog({
    command_text: text,
    status: accepted ? "partially_executed" : "clarification",
    narration: microMachineChatNarration(data || {})
  });
  if (!removed) {
    updateAssistantPendingState();
  }
}

function appendMicroMachineChatFailure(text, error) {
  var removed = removePendingForCommand(text);
  if (!removed && latestMicroMachinePlanText && text !== latestMicroMachinePlanText && pendingCommandCount() > 0) {
    updateAssistantPendingState();
    return;
  }
  if (removed && text === latestMicroMachinePlanText) { latestMicroMachinePlanText = ""; }
  appendLog({
    command_text: text,
    status: "blocked",
    narration: t("microMachineChatFailed") + ": " + error.message
  });
  if (!removed) {
    updateAssistantPendingState();
  }
}

function safelyAppendMicroMachineChatResult(text, data) {
  try {
    appendMicroMachineChatResult(text, data);
  } catch (error) {
    removePendingForCommand(text);
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

function safelyAppendMicroMachineChatFailure(text, error) {
  try {
    appendMicroMachineChatFailure(text, error);
  } catch (renderError) {
    removePendingForCommand(text);
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

function microMachineTimeoutError() {
  return new Error(
    "MicroMachine publish 응답이 " + Math.round(MICROMACHINE_CHAT_TIMEOUT_MS / 1000) +
    "초 안에 돌아오지 않았습니다. pending을 해제했습니다. 런타임/브라우저 탭을 새로고침하고 다시 시도해 주세요."
  );
}

function submitMicroMachineModulation(payload, options) {
  options = options || {};
  var statusNode = document.getElementById("micromachine-status");
  if (statusNode) { statusNode.textContent = t("microMachineSending"); }
  var timedOut = false;
  var timeoutId = null;
  if (options.appendChat && options.timeoutMs !== 0 && window.setTimeout) {
    timeoutId = window.setTimeout(function () {
      timedOut = true;
      safelyAppendMicroMachineChatFailure(payload.text || "", microMachineTimeoutError());
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
      if (data && data.async_publish) { rememberPendingMicroMachineAsync(payload.text || "", data); }
      if (options.appendChat && !timedOut && !(data && data.async_publish)) {
        safelyAppendMicroMachineChatResult(payload.text || "", data);
      }
      safeRenderMicroMachineStatus(data);
      if (options.clearInput && data.ok) { options.clearInput.value = ""; }
      return data;
    })
    .catch(function (error) {
      clearSubmitTimeout();
      if (statusNode) {
        statusNode.textContent = t("microMachineFailed") + ": " + error.message;
      }
      if (options.appendChat && !timedOut) { safelyAppendMicroMachineChatFailure(payload.text || "", error); }
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
    submitMicroMachineModulation(
      buildMicroMachineModulationPayload(text),
      { clearInput: commandInput }
    ).catch(function () {});
  });
}

document.getElementById("command-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var input = document.getElementById("command-input");
  var text = input.value.trim();
  if (!text) { return; }
  setCommandMode(selectedCommandMode());
  if (isMicroMachineCommandMode()) {
    appendMicroMachinePendingPlan(text);
    var microPayload = buildMicroMachineModulationPayload(text);
    submitMicroMachineModulation(
      microPayload,
      { appendChat: true, timeoutMs: MICROMACHINE_CHAT_TIMEOUT_MS }
    ).catch(function () {});
    input.value = "";
    input.focus();
    return;
  }
  if (!llmConfigured) {
    setLlmStatus("missing", "llmRequiredLabel", t("commandRejected"));
    return;
  }
  appendPendingCommand(text);
  fetch("/api/command" + authQuery, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: text })
  }).then(function () { pollHistory(); }).catch(function () { removePendingForCommand(text); });
  input.value = "";
  input.focus();
});

Array.prototype.forEach.call(document.querySelectorAll("[data-command]"), function (button) {
  button.addEventListener("click", function () {
    var input = document.getElementById("command-input");
    input.value = button.getAttribute("data-command") || "";
    input.focus();
  });
});

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
        setLlmStatus(
          "success",
          "llmSuccessLabel",
          t("llmReady") + " (" + data.provider + " / " + data.model + ")"
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

function setupVoiceInput() {
  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var voiceButton = document.getElementById("voice-button");
  if (!SpeechRecognition) {
    voiceButton.addEventListener("click", function () {
      setLlmStatus("failed", "llmFailedLabel", t("voiceUnsupported"));
    });
    return;
  }
  recognition = new SpeechRecognition();
  recognition.lang = currentLang === "en" ? "en-US" : (currentLang === "zh" ? "zh-CN" : "ko-KR");
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.onstart = function () {
    isRecording = true;
    voiceButton.classList.add("recording");
    appendVoiceRecordingBubble();
  };
  recognition.onend = function () {
    isRecording = false;
    voiceButton.classList.remove("recording");
    removeVoiceRecordingBubble();
  };
  recognition.onerror = function () {
    setLlmStatus("failed", "llmFailedLabel", t("voiceNoResult"));
  };
  recognition.onresult = function (event) {
    var transcript = "";
    for (var i = event.resultIndex; i < event.results.length; i += 1) {
      transcript += event.results[i][0].transcript;
    }
    document.getElementById("command-input").value = transcript.trim();
    if (event.results[event.results.length - 1].isFinal) {
      removeVoiceRecordingBubble();
      document.getElementById("command-form").dispatchEvent(new Event("submit", { cancelable: true }));
    }
  };
  voiceButton.addEventListener("click", function () {
    if (isRecording) {
      recognition.stop();
      return;
    }
    recognition.lang = currentLang === "en" ? "en-US" : (currentLang === "zh" ? "zh-CN" : "ko-KR");
    recognition.start();
  });
}

setInterval(pollHistory, POLL_INTERVAL_MS);
setInterval(pollState, POLL_INTERVAL_MS);
setInterval(pollMicroMachineStatus, POLL_INTERVAL_MS);
applyLanguage("ko");
setLlmStatus("checking", "llmCheckingLabel", t("llmChecking"));
renderModelSelect(selectedProviderValue(), "");
setupVoiceInput();
pollHistory();
pollState();
pollLlmSettings();
refreshLiveConnectionFlow();
pollMicroMachineStatus();
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

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        bridge: WebGuiBridgeInterface,
        auth_token: str = "",
    ) -> None:
        self.bridge = bridge
        self.auth_token = auth_token
        super().__init__(server_address, handler_class)


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
                response["live_start"] = launcher.start()
        self._send_json(HTTPStatus.OK, response)

    def _handle_live_status(self) -> None:
        launcher = getattr(self.server, "live_launcher", None)  # type: ignore[attr-defined]
        if launcher is None:
            self._send_json(
                HTTPStatus.OK,
                {"enabled": False, "status": "disabled", "url": "", "error": ""},
            )
            return
        self._send_json(HTTPStatus.OK, launcher.snapshot())

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
            self._send_json(HTTPStatus.OK, payload)
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
                launcher.snapshot(blackboard_dir=blackboard_dir),
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
            self._send_json(status, payload)
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
            payload = dict(
                launcher.start(
                    blackboard_dir=str(document.get("blackboard_dir", "") or ""),
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
        self._send_json(status, payload)

    def _handle_micromachine_status(self) -> None:
        status_fn = getattr(self._bridge, "micromachine_status", None)
        if not callable(status_fn):
            self._send_json(
                HTTPStatus.OK,
                {"enabled": False, "error": "MicroMachine modulation bridge is disabled."},
            )
            return
        params = parse_qs(urlsplit(self.path).query)
        blackboard_dir = params.get("blackboard_dir", [""])[0] or ""
        try:
            payload = dict(status_fn(blackboard_dir=blackboard_dir))
            runtime_snapshot = None
            launcher = getattr(self.server, "micromachine_launcher", None)  # type: ignore[attr-defined]
            if launcher is not None and callable(getattr(launcher, "snapshot", None)):
                runtime_snapshot = dict(launcher.snapshot(blackboard_dir=blackboard_dir))
            self._send_json(
                HTTPStatus.OK,
                _micromachine_status_with_runtime_gate(
                    payload,
                    runtime_snapshot=runtime_snapshot,
                    blackboard_dir=str(payload.get("blackboard_dir", blackboard_dir) or ""),
                ),
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
                else None
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
                        blackboard_dir=str(document.get("blackboard_dir", "") or ""),
                        provider_output=provider_output,
                        allow_smoke_keyword_provider=allow_smoke_keyword_provider,
                        semantic_scope=semantic_scope,
                        commander_context=commander_context,
                        ttl_seconds=ttl_seconds,
                        current_frame=current_frame,
                        update_id=update_id,
                    )
                )
                self._send_json(HTTPStatus.ACCEPTED, payload)
                return
            payload = dict(
                submit_fn(
                    cleaned_text,
                    blackboard_dir=str(document.get("blackboard_dir", "") or ""),
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
        except Exception as error:  # noqa: BLE001 - surfaced honestly as 500.
            self._send_internal_error(error)
            return
        update = payload.get("update")
        vector = update.get("vector") if isinstance(update, Mapping) else None
        tags = _string_list(vector.get("tags", ())) if isinstance(vector, Mapping) else ()
        llm_unavailable_fallback = "web_gui_llm_unavailable_fallback" in tags
        status = (
            HTTPStatus.OK
            if llm_unavailable_fallback or not bool(payload.get("ok"))
            else HTTPStatus.ACCEPTED
        )
        payload["accepted"] = bool(payload.get("ok"))
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
                    "GET /api/history?after=N, GET/POST /api/llm, "
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
        HybridCommandInterpreter,
        LocalLLMControl,
    )

    llm_control = LocalLLMControl()
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
