"""Stdlib-only local web GUI for the StarCraft II Korean commander.

``python -m starcraft_commander.web_gui --dry-run`` serves a single-page
Korean interface (title: "VoiStarCraft 커맨더") on hard-coded localhost where
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
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

from starcraft_commander.runtime_deps import MissingLLMDependencyError
from starcraft_commander.state_resolver import (
    DEFAULT_SC2_STATE_RESOLVER,
    SC2StateResolverInterface,
)


WEB_GUI_HOST: Final[str] = "127.0.0.1"
"""Default localhost binding for the web GUI."""

WEB_GUI_TOKEN_QUERY_PARAM: Final[str] = "token"
"""Query parameter accepted as the web GUI auth token."""

WEB_GUI_TOKEN_HEADER: Final[str] = "X-VoiStarCraft-Token"
"""HTTP header accepted as the web GUI auth token."""

DEFAULT_WEB_GUI_PORT: Final[int] = 8350
"""Default web GUI port; ``0`` requests an ephemeral port (used by tests)."""

DEFAULT_SC2_INSTALL_PATH: Final[str] = (
    "/Users/jinminseong/Desktop/StarCraft2/StarCraft II"
)
"""Default local StarCraft II install path used by auto live launch."""

DEFAULT_LIVE_MAP: Final[str] = "AcropolisLE"
"""Default map for auto-launched live smoke sessions."""

DEFAULT_LIVE_DIFFICULTY: Final[str] = "easy"
"""Default computer difficulty for auto-launched live smoke sessions."""

_LOCAL_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://127\.0\.0\.1:\d+(?:/[^\s]*)?"
)


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


WEB_GUI_PAGE_TITLE: Final[str] = "VoiStarCraft 커맨더"
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

_BRIDGE_THREAD_NAME: Final[str] = "voistarcraft-web-gui-session-loop"
"""Daemon thread name for the bridge's asyncio loop (asserted clean in tests)."""

_SERVER_THREAD_NAME: Final[str] = "voistarcraft-web-gui-http-server"
"""Daemon thread name for the HTTP server's serve_forever loop."""

_STOP_SENTINEL: Final[object] = object()
"""Internal queue sentinel asking the bridge worker loop to exit."""


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
    """Start one local live SC2 process and expose safe startup metadata."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._status = "idle"
        self._url = ""
        self._error = ""
        self._last_line = ""
        self._redactions: tuple[str, ...] = ()

    def start(self, provider: str, api_key: str, model: str) -> dict[str, object]:
        """Start the live demo process once, passing the key only via env."""

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._snapshot_unlocked()
            self._status = "starting"
            self._url = ""
            self._error = ""
            self._last_line = ""
            self._redactions = (api_key.strip(),) if api_key.strip() else ()
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
                name="voistarcraft-live-launch-reader",
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

    def configure_llm(self, provider: str, api_key: str, model: str = "") -> Mapping[str, object]:
        control = self._llm_control
        configure = getattr(control, "configure", None)
        if not callable(configure):
            raise RuntimeError("이 세션은 웹 LLM 키 설정을 지원하지 않습니다.")
        return dict(configure(provider, api_key, model))

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
            await self._process_one(str(item))

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
    --muted: #9fb3d9;
    --panel: rgba(7, 13, 34, 0.78);
    --panel-strong: rgba(14, 23, 54, 0.92);
    --line: rgba(136, 169, 255, 0.2);
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
    margin: 0; min-height: 100vh; padding: 22px; color: var(--ink);
    font-family: "Avenir Next", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
    background: #02030b; overflow-x: hidden;
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
    .metric-card, .collapsible-panel, .message, #log, #command-form {
      forced-color-adjust: auto; background: Canvas; color: CanvasText;
      border-color: CanvasText; box-shadow: none; backdrop-filter: none;
    }
    #send-button, #voice-button, #llm-panel button {
      background: ButtonFace; color: ButtonText; border: 1px solid ButtonText;
    }
  }
  .app-shell { position: relative; z-index: 1; max-width: 1480px; margin: 0 auto; }
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
    margin-bottom: 18px;
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
    display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(330px, 0.75fr);
    gap: 18px; align-items: stretch; min-height: 0;
  }
  #command-panel {
    min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden;
    height: clamp(420px, calc(100vh - 150px), 780px); max-height: calc(100vh - 150px);
    border: 1px solid var(--line);
    border-radius: 28px; background: var(--panel); box-shadow: var(--shadow);
    backdrop-filter: blur(18px);
  }
  .chat-header {
    display: flex; justify-content: space-between; gap: 12px; align-items: center;
    padding: 18px 20px; border-bottom: 1px solid var(--line);
    background: linear-gradient(90deg, rgba(77, 238, 234, 0.15), rgba(181, 140, 255, 0.13));
  }
  .chat-title { margin: 0; font-size: 1rem; font-weight: 900; }
  .chat-subtitle { margin: 3px 0 0; color: var(--muted); font-size: 0.82rem; }
  .assistant-pending-status {
    min-height: 1.2em; margin: 5px 0 0; color: var(--accent);
    font-size: 0.78rem; font-weight: 900; letter-spacing: 0.01em;
  }
  .assistant-pending-status:empty { visibility: hidden; }
  .quick-commands { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .quick-commands button {
    border: 1px solid rgba(77, 238, 234, 0.3); background: rgba(255, 255, 255, 0.08); color: var(--ink);
    border-radius: 999px; padding: 8px 10px; font-weight: 800; cursor: pointer;
  }
  #state-panel {
    min-width: 0; min-height: 0; max-height: calc(100vh - 150px); overflow-y: auto;
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 28px; padding: 18px; box-shadow: var(--shadow); backdrop-filter: blur(18px);
  }
  #state-panel h2, #llm-panel h2, #briefing-panel h2 { margin: 0 0 10px; font-size: 1rem; letter-spacing: -0.02em; }
  .dashboard-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .metric-card {
    min-height: 86px; padding: 13px; border-radius: 20px; background: var(--panel-strong);
    border: 1px solid var(--line); position: relative; overflow: hidden;
  }
  .metric-card::after {
    content: ""; position: absolute; right: -20px; top: -26px; width: 70px; height: 70px;
    border-radius: 50%; background: rgba(15, 118, 110, 0.12);
  }
  .metric-card dt { margin: 0 0 8px; color: var(--muted); font-weight: 800; font-size: 0.76rem; }
  .metric-card dd { margin: 0; font-size: 1.28rem; font-weight: 900; font-variant-numeric: tabular-nums; }
  .wide-card { grid-column: 1 / -1; }
  #state-availability { margin: 12px 0 0; font-size: 0.82rem; color: var(--muted); }
  #briefing-panel, #llm-panel {
    margin-top: 16px; padding: 16px; border: 1px solid var(--line); border-radius: 22px;
    background: rgba(255, 255, 255, 0.07);
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
    border-radius: 12px; background: rgba(255, 255, 255, 0.92); color: #071225;
  }
  .provider-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 8px 0 10px; }
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
  #live-status {
    margin: 10px 0 0; padding: 10px 11px; border: 1px solid var(--line); border-radius: 14px;
    background: rgba(255, 255, 255, 0.08); color: var(--ink); font-size: 0.8rem; line-height: 1.45;
  }
  #live-status a { color: var(--accent); font-weight: 900; }
  .live-actions { display: flex; gap: 8px; margin-top: 8px; }
  .live-actions button {
    flex: 1; margin-top: 0 !important; padding: 9px 10px !important;
    background: rgba(255, 255, 255, 0.9) !important; color: #071225 !important;
  }
  #log {
    flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 20px;
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
    display: flex; gap: 10px; padding: 16px; border-top: 1px solid var(--line);
    background: rgba(7, 13, 34, 0.72);
  }
  #command-input {
    flex: 1; font-size: 1.02rem; padding: 14px 16px;
    border: 1px solid rgba(136, 169, 255, 0.28); border-radius: 18px; background: rgba(255, 255, 255, 0.92); color: #071225;
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
  @media (max-width: 980px) {
    body { padding: 12px; }
    .space-background::after { inset: 20% -20% -18% 24%; width: 105vw; height: 105vw; opacity: 0.48; }
    .star-depth { inset: -14vmax; }
    .star-depth-near { opacity: 0.42; }
    .hero { display: block; }
    .connection-pill { display: inline-block; margin-top: 12px; }
    main { grid-template-columns: 1fr; }
    #command-panel { height: 68vh; min-height: 0; max-height: 68vh; }
    #state-panel { max-height: none; }
    .dashboard-grid { grid-template-columns: 1fr 1fr; }
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
    .dashboard-grid { grid-template-columns: 1fr; }
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
          <option value="gpt-4.1-mini">GPT-4.1 Mini</option>
          <option value="gpt-5.5">GPT-5.5</option>
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
      <div id="live-status" data-i18n="liveIdle">StarCraft II 자동 연결 대기 중입니다.</div>
      <div class="live-actions">
        <button id="live-open-button" type="button" data-i18n="liveOpenButton" disabled>Live GUI 열기</button>
        <button id="live-refresh-button" type="button" data-i18n="liveRefreshButton">연결 상태 확인</button>
      </div>
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
var trimmedChatEvents = 0;
var recentEvents = [];
var archivedChatEvents = [];
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
var latestState = null;
var briefingAdviceToggleEnabled = false;
var recognition = null;
var isRecording = false;
var liveGuiUrl = "";
var LLM_MODELS = {
  openai: [
    { value: "gpt-4.1-mini", label: "GPT-4.1 Mini" },
    { value: "gpt-5.5", label: "GPT-5.5" },
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
    liveStarting: "StarCraft II 연결 시작 중...",
    liveReady: "StarCraft II 연결 준비됨",
    liveFailed: "StarCraft II 자동 연결 실패",
    liveIdle: "StarCraft II 자동 연결 대기 중입니다.",
    liveOpenButton: "Live GUI 열기",
    liveRefreshButton: "연결 상태 확인",
    llmReady: "LLM 키 설정됨",
    llmMissing: "LLM 필수: API 키를 먼저 설정해야 명령을 보낼 수 있습니다.",
    llmEnterKey: "API 키를 입력하세요.",
    llmSaveFailed: "LLM 키 설정 요청에 실패했습니다.",
    userLabel: "사용자",
    commanderLabel: "커맨더",
    commandPlaceholderReady: "대화하듯 입력하세요. 예: 보급고 지어 / 정찰보내",
    commandPlaceholderLocked: "LLM 키 설정 후 명령 입력이 활성화됩니다.",
    commandRejected: "LLM 키가 설정되지 않아 명령을 보내지 않았습니다.",
    saveLlm: "로컬 키 설정",
    startupGuide: "🚀 시작 메뉴얼\\n1. 오른쪽 LLM 설정에서 모델사를 고르고 모델을 선택하세요.\\n2. API 키를 붙여넣고 로컬 키 설정을 누르세요. 키는 이 로컬 프로세스 메모리에만 저장됩니다.\\n3. 설정 성공 후 StarCraft II 자동 연결이 시작되고, 준비되면 이 탭이 실제 Live GUI로 전환됩니다.\\n4. 먼저 상태 알려줘 / 일꾼 계속 찍어 / 보급고 지어 처럼 입력해 보세요.\\n🎙️ 음성 버튼을 켜면 말한 내용이 채팅 입력으로 들어갑니다."
  },
  en: {
    eyebrow: "Live RTS Command Center",
    heroHint: "Command conversationally and monitor the battlefield dashboard.",
    connectionChecking: "Checking SC2 link",
    connectionWaiting: "Waiting for SC2 state",
    connectionReady: "SC2 connected",
    chatTitle: "Commander Chat",
    chatSubtitle: "Orders, questions, and status reports in one cockpit.",
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
    liveStarting: "Starting StarCraft II connection...",
    liveReady: "StarCraft II connection ready",
    liveFailed: "Failed to auto-connect StarCraft II",
    liveIdle: "Waiting to auto-connect StarCraft II.",
    liveOpenButton: "Open Live GUI",
    liveRefreshButton: "Check Status",
    llmReady: "LLM key configured",
    llmMissing: "LLM required: configure an API key before sending commands.",
    llmEnterKey: "Enter an API key.",
    llmSaveFailed: "Failed to configure the LLM key.",
    userLabel: "User",
    commanderLabel: "Commander",
    commandPlaceholderReady: "Type naturally. Example: build a supply depot / send scout",
    commandPlaceholderLocked: "Command input unlocks after LLM key setup.",
    commandRejected: "Command not sent because the LLM key is not configured.",
    saveLlm: "Save Local Key",
    startupGuide: "🚀 Startup guide\\n1. Choose a provider and model in LLM Settings.\\n2. Paste your API key and press Save Local Key. The key stays only in this local process memory.\\n3. After success, StarCraft II auto-connect starts and this tab switches to the real Live GUI when ready.\\n4. Try: status, keep training SCVs, build a supply depot.\\n🎙️ Turn on voice to place recognized speech into the chat input."
  },
  zh: {
    eyebrow: "实时 RTS 指挥中心",
    heroHint: "像聊天一样下达命令，并在右侧查看战场仪表盘。",
    connectionChecking: "正在检查 SC2 连接",
    connectionWaiting: "等待 SC2 状态",
    connectionReady: "SC2 已连接",
    chatTitle: "指挥官聊天",
    chatSubtitle: "命令、问题和状态报告集中在一个驾驶舱。",
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
    liveStarting: "正在启动 StarCraft II 连接...",
    liveReady: "StarCraft II 连接已就绪",
    liveFailed: "StarCraft II 自动连接失败",
    liveIdle: "正在等待自动连接 StarCraft II。",
    liveOpenButton: "打开 Live GUI",
    liveRefreshButton: "检查状态",
    llmReady: "LLM key 已设置",
    llmMissing: "必须先设置 LLM API key 才能发送命令。",
    llmEnterKey: "请输入 API key。",
    llmSaveFailed: "LLM key 设置请求失败。",
    userLabel: "用户",
    commanderLabel: "指挥官",
    commandPlaceholderReady: "自然输入命令。例如：建造补给站 / 派出侦察",
    commandPlaceholderLocked: "设置 LLM key 后才能输入命令。",
    commandRejected: "LLM key 未设置，命令未发送。",
    saveLlm: "保存本地 Key",
    startupGuide: "🚀 启动指南\\n1. 在 LLM 设置中选择供应商和模型。\\n2. 粘贴 API key，然后点击保存本地 Key。Key 只保存在本地进程内存中。\\n3. 设置成功后会开始自动连接 StarCraft II，准备好后此标签页会切换到真实 Live GUI。\\n4. 可以先输入：查看状态 / 持续生产 SCV / 建造补给站。\\n🎙️ 开启语音后，识别到的话会进入聊天输入框。"
  }
};

function t(key) {
  return (I18N[currentLang] && I18N[currentLang][key]) || I18N.ko[key] || key;
}

function setCommandEnabled(enabled) {
  var input = document.getElementById("command-input");
  var button = document.getElementById("send-button");
  var voiceButton = document.getElementById("voice-button");
  input.disabled = !enabled;
  button.disabled = !enabled;
  voiceButton.disabled = !enabled;
  input.placeholder = enabled ? t("commandPlaceholderReady") : t("commandPlaceholderLocked");
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
  setCommandEnabled(llmConfigured);
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
  normalized = normalized.replace(/^\[(executed|partially_executed|blocked|clarification|read_only)\]\s*/i, "");
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
    .replace(/명령을 실행하지 못했습니다\. 이유:\s*/g, "")
    .replace(/실행하지 않았습니다\. 이유:\s*/g, "")
    .replace(/\. 대안:\s*/g, ".\\n다음 행동: ");
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

function renderState(data) {
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
  fetch("/api/state" + authQuery)
    .then(function (response) { return response.json(); })
    .then(renderState)
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
  setLlmStatus("missing", "llmRequiredLabel", t("llmMissing"));
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

function handleLiveStart(status) {
  if (!status || !status.enabled) { return; }
  if (status.status === "ready" && status.url) {
    setLiveStatusLink(t("liveReady"), status.url);
    window.location.assign(status.url);
    return;
  }
  if (status.status === "failed") {
    setLiveStatusText(t("liveFailed") + ": " + (status.error || status.last_line || "unknown error"));
    return;
  }
  setLiveStatusText(t("liveStarting") + " (" + (status.status || "starting") + formatLivePid(status) + ")");
  pollLiveStatus(0);
}

function pollLiveStatus(attempt) {
  if (attempt > 90) {
    setLiveStatusText(t("liveFailed") + ": timeout waiting for live GUI URL");
    return;
  }
  window.setTimeout(function () {
    fetch("/api/live/status" + authQuery)
      .then(parseJsonResponse)
      .then(function (status) {
        if (status.status === "ready" && status.url) {
          setLiveStatusLink(t("liveReady"), status.url);
          window.location.assign(status.url);
          return;
        }
        if (status.status === "failed") {
          setLiveStatusText(t("liveFailed") + ": " + (status.error || status.last_line || "unknown error"));
          return;
        }
        setLiveStatusText(t("liveStarting") + " (" + status.status + formatLivePid(status) + ")");
        pollLiveStatus(attempt + 1);
      })
      .catch(function (error) {
        setLiveStatusText(t("liveFailed") + ": " + error.message);
      });
  }, 1000);
}

function refreshLiveConnectionFlow() {
  fetch("/api/live/status" + authQuery)
    .then(parseJsonResponse)
    .then(handleLiveStart)
    .catch(function (error) {
      setLiveStatusText(t("liveFailed") + ": " + error.message);
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

document.getElementById("command-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var input = document.getElementById("command-input");
  var text = input.value.trim();
  if (!text) { return; }
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

document.getElementById("live-refresh-button").addEventListener("click", function () {
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
applyLanguage("ko");
setLlmStatus("checking", "llmCheckingLabel", t("llmChecking"));
renderModelSelect(selectedProviderValue(), "");
setupVoiceInput();
pollHistory();
pollState();
pollLlmSettings();
</script>
</body>
</html>
"""
"""Embedded single-page Korean UI template (no external CDN)."""


def render_web_gui_page() -> str:
    """Render the embedded single-page Korean web GUI HTML."""

    return (
        _WEB_GUI_PAGE_TEMPLATE
        .replace("__TITLE__", WEB_GUI_PAGE_TITLE)
        .replace("__POLL_MS__", str(WEB_GUI_POLL_INTERVAL_MS))
        .replace("__COLOR_EXECUTED__", WEB_GUI_STATUS_COLORS["executed"])
        .replace("__COLOR_PARTIAL__", WEB_GUI_STATUS_COLORS["partially_executed"])
        .replace("__COLOR_BLOCKED__", WEB_GUI_STATUS_COLORS["blocked"])
        .replace("__COLOR_CLARIFICATION__", WEB_GUI_STATUS_COLORS["clarification"])
        .replace("__COLOR_READ_ONLY__", WEB_GUI_STATUS_COLORS["read_only"])
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

    server_version = "VoiStarCraftWebGui/1.0"
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
            self._send_html(HTTPStatus.OK, render_web_gui_page())
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
        if bool(getattr(self.server, "auto_launch_live", False)):  # type: ignore[attr-defined]
            launcher = getattr(self.server, "live_launcher", None)  # type: ignore[attr-defined]
            if launcher is not None:
                response["live_start"] = launcher.start(provider, api_key, model)
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
                    "POST /api/command."
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
                    "접속하거나 X-VoiStarCraft-Token 헤더를 전달해 주세요."
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
        self._live_launcher = _LiveLaunchManager() if self._auto_launch_live else None
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
            "VoiStarCraft 커맨더 로컬 웹 GUI. "
            "--dry-run은 내장 가짜 BotAI로 전체 파이프라인을 실행합니다. "
            "실제 게임 연결은 python -m starcraft_commander.demo_sc2 --gui를 사용하세요."
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
            "실제 StarCraft II에 연결하려면 "
            "'python -m starcraft_commander.demo_sc2 --gui'를 사용하세요."
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
        auto_launch_live=True,
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
        print(f"VoiStarCraft 커맨더 웹 GUI 시작: {server.url}")
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
