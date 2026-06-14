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
import threading
import time
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

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

WEB_GUI_PAGE_TITLE: Final[str] = "VoiStarCraft 커맨더"
"""Korean single-page UI title."""

LLM_REQUIRED_COMMAND_ERROR: Final[str] = (
    "LLM 키가 설정되지 않아 명령을 실행하지 않았습니다. "
    "이 프로젝트는 LLM 기반 해석을 필수로 사용합니다. "
    "우측 LLM 설정에서 OpenAI 또는 Anthropic API 키를 먼저 설정하세요."
)
"""User-facing refusal when a command arrives before local LLM configuration."""

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
            return dict(to_dict())
        if isinstance(state, Mapping):
            return dict(state)
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
    return document


def _as_event_mapping(entry: object) -> dict[str, object]:
    """Normalize one duck-typed history entry into a JSON-ready mapping."""

    if isinstance(entry, Mapping):
        return dict(entry)
    to_dict = getattr(entry, "to_dict", None)
    if callable(to_dict):
        try:
            rendered = to_dict()
        except Exception:
            rendered = None
        if isinstance(rendered, Mapping):
            return dict(rendered)
    document: dict[str, object] = {}
    for attribute in ("seq", "command_text", "status", "narration"):
        value = getattr(entry, attribute, None)
        if value is not None:
            document[attribute] = value
    return document


def _internal_error_outcome(text: str, error: Exception) -> object:
    """Build one honest blocked outcome for a session-level failure."""

    # Lazy import: the bridge itself duck-types sessions, so importing the
    # module never needs the live pipeline (and its ToyCraft interpreter).
    from starcraft_commander.live_pipeline import SC2CommandOutcome

    return SC2CommandOutcome(
        command_text=str(text),
        status="blocked",
        narration=(
            f"내부 오류로 명령을 실행하지 못했습니다 (이유: {error}). "
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
    background:
      radial-gradient(circle at 14% 12%, rgba(77, 238, 234, 0.28), transparent 30%),
      radial-gradient(circle at 82% 4%, rgba(181, 140, 255, 0.25), transparent 26%),
      radial-gradient(circle at 78% 84%, rgba(255, 209, 102, 0.16), transparent 30%),
      linear-gradient(135deg, #030816 0%, #0a1230 42%, #1a0f2e 100%);
  }
  body::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; opacity: 0.28;
    background-image:
      radial-gradient(circle, rgba(239, 246, 255, 0.75) 1px, transparent 1.5px),
      linear-gradient(rgba(128, 167, 255, 0.08) 1px, transparent 1px),
      linear-gradient(90deg, rgba(128, 167, 255, 0.08) 1px, transparent 1px);
    background-size: 120px 120px, 38px 38px, 38px 38px;
  }
  body::after {
    content: ""; position: fixed; inset: auto -12% -35% 46%; width: 70vw; height: 70vw;
    pointer-events: none; border-radius: 999px;
    background: radial-gradient(circle, rgba(77, 238, 234, 0.18), transparent 58%);
    filter: blur(6px);
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
  main { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(330px, 0.75fr); gap: 18px; align-items: stretch; }
  #command-panel {
    min-width: 0; display: flex; flex-direction: column; overflow: hidden;
    min-height: min(740px, calc(100vh - 150px)); border: 1px solid var(--line);
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
  .quick-commands { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .quick-commands button {
    border: 1px solid rgba(77, 238, 234, 0.3); background: rgba(255, 255, 255, 0.08); color: var(--ink);
    border-radius: 999px; padding: 8px 10px; font-weight: 800; cursor: pointer;
  }
  #state-panel {
    min-width: 0; background: var(--panel); border: 1px solid var(--line);
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
  #strategy-briefing {
    margin: 0; color: var(--ink); line-height: 1.55; font-size: 0.92rem; white-space: pre-wrap;
  }
  .chat-trim-note {
    margin: 0 auto 14px; width: fit-content; max-width: 90%; padding: 7px 11px;
    color: var(--muted); border: 1px solid var(--line); border-radius: 999px;
    background: rgba(255, 255, 255, 0.08); font-size: 0.78rem; font-weight: 800;
  }
  #llm-panel label { display: block; margin: 8px 0 4px; font-size: 0.78rem; font-weight: 900; color: var(--muted); }
  #llm-panel select, #llm-panel input {
    width: 100%; padding: 10px 11px; border: 1px solid rgba(96, 112, 128, 0.28);
    border-radius: 12px; background: rgba(255, 255, 255, 0.92); color: #071225;
  }
  #llm-panel button {
    width: 100%; margin-top: 10px; padding: 11px 12px; border: none; border-radius: 14px;
    background: linear-gradient(135deg, var(--accent), var(--violet)); color: #061126; font-weight: 900; cursor: pointer;
  }
  #llm-status { margin: 8px 0 0; font-size: 0.78rem; color: var(--muted); }
  #log {
    flex: 1; min-height: 360px; overflow-y: auto; padding: 20px;
    background:
      linear-gradient(180deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.02)),
      radial-gradient(circle at 20% 20%, rgba(77, 238, 234, 0.11), transparent 32%);
  }
  .log-entry { display: grid; gap: 8px; margin: 0 0 16px; }
  .message {
    max-width: min(74ch, 86%); padding: 12px 14px; border-radius: 18px;
    box-shadow: 0 10px 24px rgba(17, 24, 39, 0.08); white-space: pre-wrap;
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
  .status { display: inline-block; font-weight: 900; margin-right: 7px; white-space: nowrap; }
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
    .hero { display: block; }
    .connection-pill { display: inline-block; margin-top: 12px; }
    main { grid-template-columns: 1fr; }
    #command-panel { min-height: 68vh; }
    .dashboard-grid { grid-template-columns: 1fr 1fr; }
  }
  @media (max-width: 620px) {
    .dashboard-grid { grid-template-columns: 1fr; }
    #command-form { flex-direction: column; }
    .message { max-width: 94%; }
  }
</style>
</head>
<body>
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
    <section id="briefing-panel">
      <h2 data-i18n="briefingTitle">전략 브리핑</h2>
      <div id="strategy-briefing" data-i18n="briefingWaiting">상태 데이터를 기다리는 중입니다.</div>
    </section>
    <section id="llm-panel">
      <h2 data-i18n="llmTitle">LLM 설정</h2>
      <p class="hint" data-i18n="llmHint">API 키는 이 로컬 프로세스 메모리에만 보관됩니다.</p>
      <form id="llm-form">
        <label for="llm-provider">Provider</label>
        <select id="llm-provider">
          <option value="openai">OpenAI / GPT</option>
          <option value="anthropic">Anthropic / Claude</option>
        </select>
        <label for="llm-model">Model</label>
        <input id="llm-model" type="text" autocomplete="off" placeholder="기본 모델 사용">
        <label for="llm-api-key">API Key</label>
        <input id="llm-api-key" type="password" autocomplete="off" placeholder="sk-...">
        <button type="submit" data-i18n="saveLlm">로컬 키 설정</button>
      </form>
      <p id="llm-status" data-i18n="llmChecking">LLM 키 상태를 확인 중입니다.</p>
    </section>
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
var MAX_CHAT_EVENTS = 80;
var trimmedChatEvents = 0;
var recentEvents = [];
var pendingCommandSeq = 0;
var pendingNodes = {};
var latestState = null;
var recognition = null;
var isRecording = false;

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
    briefingProgress: "진행 상황",
    briefingRisk: "리스크",
    briefingAdvice: "추천 보기",
    strategyOpening: "아직 명령 기록이 부족합니다. 현재는 전장 상태 파악 단계입니다.",
    strategyEconomy: "경제와 생산 기반을 안정화하는 전략을 펼치고 있습니다.",
    strategyProduction: "테란 생산 인프라를 확보하는 전략을 펼치고 있습니다.",
    strategyScout: "정보 우위를 확보하기 위해 정찰 중심 운영을 펼치고 있습니다.",
    strategyDefense: "본진 방어와 생존을 우선하는 전략을 펼치고 있습니다.",
    progressRecent: "최근 명령",
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
    assistantThinking: "응답 하는중",
    voiceListening: "녹음중",
    voiceUnsupported: "이 브라우저는 음성 인식을 지원하지 않습니다.",
    voiceNoResult: "음성이 인식되지 않았습니다.",
    workerUnit: "기",
    idleLabel: "유휴",
    llmTitle: "LLM 설정",
    llmHint: "API 키는 이 로컬 프로세스 메모리에만 보관됩니다.",
    llmChecking: "LLM 키 상태를 확인 중입니다.",
    llmReady: "LLM 키 설정됨",
    llmMissing: "LLM 필수: API 키를 먼저 설정해야 명령을 보낼 수 있습니다.",
    llmEnterKey: "API 키를 입력하세요.",
    llmSaveFailed: "LLM 키 설정 요청에 실패했습니다.",
    userLabel: "사용자",
    commanderLabel: "커맨더",
    commandPlaceholderReady: "대화하듯 입력하세요. 예: 보급고 지어 / 정찰보내",
    commandPlaceholderLocked: "LLM 키 설정 후 명령 입력이 활성화됩니다.",
    commandRejected: "LLM 키가 설정되지 않아 명령을 보내지 않았습니다.",
    saveLlm: "로컬 키 설정"
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
    briefingProgress: "Progress",
    briefingRisk: "Risk",
    briefingAdvice: "Show Advice",
    strategyOpening: "Not enough command history yet. Current mode is battlefield assessment.",
    strategyEconomy: "You are stabilizing economy and production foundations.",
    strategyProduction: "You are building Terran production infrastructure.",
    strategyScout: "You are playing for information advantage through scouting.",
    strategyDefense: "You are prioritizing main-base defense and survival.",
    progressRecent: "Recent commands",
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
    assistantThinking: "Thinking",
    voiceListening: "Recording",
    voiceUnsupported: "This browser does not support speech recognition.",
    voiceNoResult: "No speech was recognized.",
    workerUnit: "",
    idleLabel: "idle",
    llmTitle: "LLM Settings",
    llmHint: "The API key is stored only in this local process memory.",
    llmChecking: "Checking LLM key status.",
    llmReady: "LLM key configured",
    llmMissing: "LLM required: configure an API key before sending commands.",
    llmEnterKey: "Enter an API key.",
    llmSaveFailed: "Failed to configure the LLM key.",
    userLabel: "User",
    commanderLabel: "Commander",
    commandPlaceholderReady: "Type naturally. Example: build a supply depot / send scout",
    commandPlaceholderLocked: "Command input unlocks after LLM key setup.",
    commandRejected: "Command not sent because the LLM key is not configured.",
    saveLlm: "Save Local Key"
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
    briefingProgress: "进度",
    briefingRisk: "风险",
    briefingAdvice: "查看建议",
    strategyOpening: "命令记录还不足。目前处于战场评估阶段。",
    strategyEconomy: "你正在稳定经济和生产基础。",
    strategyProduction: "你正在建立 Terran 生产体系。",
    strategyScout: "你正在通过侦察获取情报优势。",
    strategyDefense: "你正在优先保护主基地并确保生存。",
    progressRecent: "最近命令",
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
    assistantThinking: "正在回答",
    voiceListening: "录音中",
    voiceUnsupported: "此浏览器不支持语音识别。",
    voiceNoResult: "未识别到语音。",
    workerUnit: "",
    idleLabel: "空闲",
    llmTitle: "LLM 设置",
    llmHint: "API key 只保存在本地进程内存中。",
    llmChecking: "正在检查 LLM key 状态。",
    llmReady: "LLM key 已设置",
    llmMissing: "必须先设置 LLM API key 才能发送命令。",
    llmEnterKey: "请输入 API key。",
    llmSaveFailed: "LLM key 设置请求失败。",
    userLabel: "用户",
    commanderLabel: "指挥官",
    commandPlaceholderReady: "自然输入命令。例如：建造补给站 / 派出侦察",
    commandPlaceholderLocked: "设置 LLM key 后才能输入命令。",
    commandRejected: "LLM key 未设置，命令未发送。",
    saveLlm: "保存本地 Key"
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
  if (latestState) { renderStrategyBriefing(latestState); }
}

function trimChatLog() {
  while (logBox.querySelectorAll(".log-entry").length > MAX_CHAT_EVENTS) {
    var oldestEntry = logBox.querySelector(".log-entry");
    if (!oldestEntry) { break; }
    logBox.removeChild(oldestEntry);
    trimmedChatEvents += 1;
  }
  var existingNote = document.getElementById("chat-trim-note");
  if (trimmedChatEvents < 1) {
    if (existingNote) { existingNote.remove(); }
    return;
  }
  if (!existingNote) {
    existingNote = document.createElement("div");
    existingNote.id = "chat-trim-note";
    existingNote.className = "chat-trim-note";
    logBox.insertBefore(existingNote, logBox.firstElementChild);
  }
  existingNote.textContent = t("chatTrimmed") + " · " + trimmedChatEvents;
}

function appendLog(ev) {
  if (ev && typeof ev.seq === "number") {
    recentEvents.push(ev);
    if (recentEvents.length > MAX_CHAT_EVENTS) {
      recentEvents = recentEvents.slice(recentEvents.length - MAX_CHAT_EVENTS);
    }
    removePendingForCommand(ev.command_text || "");
  }
  var entry = document.createElement("div");
  entry.className = "log-entry";
  if (ev.command_text) {
    var userMessage = document.createElement("div");
    userMessage.className = "message message-user";
    var userMeta = document.createElement("span");
    userMeta.className = "message-meta";
    userMeta.textContent = t("userLabel");
    userMessage.appendChild(userMeta);
    userMessage.appendChild(document.createTextNode(ev.command_text));
    entry.appendChild(userMessage);
  }
  var botMessage = document.createElement("div");
  botMessage.className = "message message-bot";
  var botMeta = document.createElement("span");
  botMeta.className = "message-meta";
  botMeta.textContent = t("commanderLabel");
  botMessage.appendChild(botMeta);
  var status = document.createElement("span");
  status.className = "status status-" + (ev.status || "clarification");
  status.textContent = "[" + (ev.status || "?") + "]";
  botMessage.appendChild(status);
  var narration = document.createElement("span");
  narration.className = "narration";
  narration.textContent = ev.narration || "";
  botMessage.appendChild(narration);
  entry.appendChild(botMessage);
  logBox.appendChild(entry);
  trimChatLog();
  logBox.scrollTop = logBox.scrollHeight;
  if (latestState) { renderStrategyBriefing(latestState); }
}

function appendPendingCommand(text) {
  pendingCommandSeq += 1;
  var pendingId = "pending-" + pendingCommandSeq;
  var entry = document.createElement("div");
  entry.className = "log-entry pending-entry";
  entry.id = pendingId;

  var userMessage = document.createElement("div");
  userMessage.className = "message message-user";
  var userMeta = document.createElement("span");
  userMeta.className = "message-meta";
  userMeta.textContent = t("userLabel");
  userMessage.appendChild(userMeta);
  userMessage.appendChild(document.createTextNode(text));
  entry.appendChild(userMessage);

  var botMessage = document.createElement("div");
  botMessage.className = "message message-bot message-pending";
  var botMeta = document.createElement("span");
  botMeta.className = "message-meta";
  botMeta.textContent = t("commanderLabel");
  botMessage.appendChild(botMeta);
  var narration = document.createElement("span");
  narration.className = "narration";
  narration.textContent = t("assistantThinking");
  botMessage.appendChild(narration);
  entry.appendChild(botMessage);

  pendingNodes[text] = pendingId;
  logBox.appendChild(entry);
  trimChatLog();
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
  var pendingId = pendingNodes[text];
  if (!pendingId) { return; }
  var node = document.getElementById(pendingId);
  if (node) { node.remove(); }
  delete pendingNodes[text];
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
  var successful = recentEvents.filter(function (ev) {
    return ["executed", "partially_executed", "read_only"].indexOf(ev.status) >= 0;
  }).length;
  var failed = recentEvents.filter(function (ev) {
    return ["blocked", "clarification"].indexOf(ev.status) >= 0;
  }).length;
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
  var strategy = inferStrategy(recentTexts, structures);
  var enemyLine = enemyUnits + enemyStructures > 0
    ? enemyUnits + " / " + enemyStructures
    : t("briefingEnemyNone");
  var briefing = document.getElementById("strategy-briefing");
  briefing.innerHTML = "";
  briefing.appendChild(briefingBlock(t("briefingCurrentStrategy"), strategy));
  briefing.appendChild(briefingBlock(
    t("briefingProgress"),
    t("briefingEconomy") + ": " + data.minerals + "M / " + data.vespene + "G, " + workers + t("workerUnit") + "\n" +
    t("briefingSupply") + ": " + data.supply_used + "/" + data.supply_cap + " (" + (data.supply_left || 0) + ")\n" +
    t("briefingForces") + ": " + (data.army_count || 0) + t("workerUnit") + "\n" +
    t("briefingEnemy") + ": " + enemyLine + "\n" +
    t("progressRecent") + ": " + (recentTexts.length ? recentTexts.join(" / ") : "-") + "\n" +
    "OK/Needs attention: " + successful + " / " + failed
  ));
  briefing.appendChild(briefingBlock(t("briefingRisk"), risks.join("\n")));
  var details = document.createElement("details");
  var summary = document.createElement("summary");
  summary.textContent = t("briefingAdvice");
  details.appendChild(summary);
  var advice = document.createElement("div");
  advice.className = "briefing-block";
  advice.textContent = suggestions.join("\n");
  details.appendChild(advice);
  briefing.appendChild(details);
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
  if (data.provider) {
    document.getElementById("llm-provider").value = data.provider;
  }
  document.getElementById("llm-model").value = data.model || "";
  llmConfigured = !!data.configured;
  setCommandEnabled(llmConfigured);
  setText(
    "llm-status",
    data.configured
      ? t("llmReady") + " (" + data.provider + " / " + data.model + ")"
      : t("llmMissing")
  );
}

function pollLlmSettings() {
  fetch("/api/llm" + authQuery)
    .then(function (response) { return response.json(); })
    .then(renderLlmSettings)
    .catch(function () {});
}

document.getElementById("command-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var input = document.getElementById("command-input");
  var text = input.value.trim();
  if (!text) { return; }
  if (!llmConfigured) {
    setText("llm-status", t("commandRejected"));
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
  var payload = {
    provider: document.getElementById("llm-provider").value,
    model: document.getElementById("llm-model").value.trim(),
    api_key: keyInput.value.trim()
  };
  if (!payload.api_key) {
    setText("llm-status", t("llmEnterKey"));
    return;
  }
  fetch("/api/llm" + authQuery, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(function (response) { return response.json(); })
    .then(function (data) {
      keyInput.value = "";
      if (data.error) {
        setText("llm-status", data.error);
        return;
      }
      renderLlmSettings(data);
    })
    .catch(function () { setText("llm-status", t("llmSaveFailed")); });
});

Array.prototype.forEach.call(document.querySelectorAll("[data-lang-button]"), function (button) {
  button.addEventListener("click", function () {
    applyLanguage(button.getAttribute("data-lang-button") || "ko");
    pollState();
    pollLlmSettings();
  });
});

function setupVoiceInput() {
  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var voiceButton = document.getElementById("voice-button");
  if (!SpeechRecognition) {
    voiceButton.addEventListener("click", function () {
      setText("llm-status", t("voiceUnsupported"));
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
    setText("llm-status", t("voiceNoResult"));
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
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"configured": False, "error": str(error)},
            )
            return
        self._send_json(HTTPStatus.OK, dict(snapshot))

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
                    f"서버 내부 오류가 발생했습니다: {error}. "
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
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_body(status, "application/json; charset=utf-8", body)

    def _send_html(self, status: HTTPStatus, page: str) -> None:
        self._send_body(status, "text/html; charset=utf-8", page.encode("utf-8"))

    def _send_body(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
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

    session, _bot = build_dry_run_session()
    bridge = SessionLoopBridge(session=session)
    server = WebGuiServer(
        bridge=bridge,
        port=args.port,
        host=args.host,
        auth_token=args.token,
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
