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
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; background: #f4f6f8; color: #20262c;
    font-family: "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
  }
  h1 { margin: 0 0 4px; font-size: 1.5rem; }
  p.hint { margin: 0 0 12px; color: #5b6570; font-size: 0.9rem; }
  main { display: flex; gap: 16px; align-items: stretch; }
  #command-panel { flex: 3; display: flex; flex-direction: column; min-width: 0; }
  #state-panel {
    flex: 1; min-width: 220px; background: #ffffff; border: 1px solid #d6dde4;
    border-radius: 8px; padding: 12px 16px;
  }
  #state-panel h2 { margin: 0 0 8px; font-size: 1.05rem; }
  #state-panel dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }
  #state-panel dt { font-weight: 600; color: #44505c; }
  #state-panel dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
  #state-availability { margin: 10px 0 0; font-size: 0.8rem; color: #8a939c; }
  #log {
    flex: 1; min-height: 320px; max-height: 60vh; overflow-y: auto;
    background: #ffffff; border: 1px solid #d6dde4; border-radius: 8px;
    padding: 12px; margin-bottom: 12px;
  }
  .log-entry { padding: 6px 4px; border-bottom: 1px solid #eef1f4; }
  .log-entry .command-text { color: #8a939c; font-size: 0.8rem; margin-bottom: 2px; }
  .log-entry .status { font-weight: 700; margin-right: 6px; white-space: nowrap; }
  .status-executed { color: __COLOR_EXECUTED__; }
  .status-partially_executed { color: __COLOR_PARTIAL__; }
  .status-blocked { color: __COLOR_BLOCKED__; }
  .status-clarification { color: __COLOR_CLARIFICATION__; }
  .status-read_only { color: __COLOR_READ_ONLY__; }
  #command-form { display: flex; gap: 8px; }
  #command-input {
    flex: 1; font-size: 1.15rem; padding: 12px 14px;
    border: 2px solid #9fb4c7; border-radius: 8px;
  }
  #command-input:focus { outline: none; border-color: #1565c0; }
  #send-button {
    font-size: 1.1rem; font-weight: 700; padding: 12px 24px; border: none;
    border-radius: 8px; background: #1565c0; color: #ffffff; cursor: pointer;
  }
  #send-button:hover { background: #0d4f9e; }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="hint">한국어 명령을 입력하고 Enter 또는 전송 버튼을 누르세요.
예: 마린 6기 입구로 보내고 SCV 계속 찍어</p>
<main>
  <section id="command-panel">
    <div id="log" aria-live="polite"></div>
    <form id="command-form">
      <input id="command-input" type="text" autocomplete="off" autofocus
             placeholder="명령을 입력하세요 (예: SCV 계속 찍어)">
      <button type="submit" id="send-button">전송</button>
    </form>
  </section>
  <aside id="state-panel">
    <h2>전장 상태</h2>
    <dl>
      <dt>미네랄</dt><dd id="state-minerals">-</dd>
      <dt>가스</dt><dd id="state-vespene">-</dd>
      <dt>보급</dt><dd id="state-supply">-</dd>
      <dt>일꾼</dt><dd id="state-workers">-</dd>
      <dt>병력</dt><dd id="state-army">-</dd>
      <dt>건물</dt><dd id="state-structures">-</dd>
    </dl>
    <p id="state-availability"></p>
  </aside>
</main>
<script>
"use strict";
var POLL_INTERVAL_MS = __POLL_MS__;
var token = new URLSearchParams(window.location.search).get("token") || "";
var authQuery = token ? "?token=" + encodeURIComponent(token) : "";
var authJoin = token ? "&token=" + encodeURIComponent(token) : "";
var lastSeq = 0;
var logBox = document.getElementById("log");

function appendLog(ev) {
  var entry = document.createElement("div");
  entry.className = "log-entry";
  if (ev.command_text) {
    var command = document.createElement("div");
    command.className = "command-text";
    command.textContent = "명령: " + ev.command_text;
    entry.appendChild(command);
  }
  var status = document.createElement("span");
  status.className = "status status-" + (ev.status || "clarification");
  status.textContent = "[" + (ev.status || "?") + "]";
  entry.appendChild(status);
  var narration = document.createElement("span");
  narration.className = "narration";
  narration.textContent = ev.narration || "";
  entry.appendChild(narration);
  logBox.appendChild(entry);
  logBox.scrollTop = logBox.scrollHeight;
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
    setText("state-availability", "게임 상태를 아직 읽을 수 없습니다.");
    return;
  }
  setText("state-minerals", String(data.minerals));
  setText("state-vespene", String(data.vespene));
  setText("state-supply", data.supply_used + " / " + data.supply_cap);
  var workers = (data.own_units && data.own_units.SCV) || 0;
  setText("state-workers", workers + "기 (유휴 " + (data.idle_worker_count || 0) + "기)");
  setText("state-army", (data.army_count || 0) + "기");
  var structures = data.own_structures || {};
  var parts = Object.keys(structures).map(function (name) {
    return name + " " + structures[name];
  });
  setText("state-structures", parts.length ? parts.join(", ") : "없음");
  setText(
    "state-availability",
    data.observation_complete === false ? "관측이 불완전합니다." : ""
  );
}

function pollState() {
  fetch("/api/state" + authQuery)
    .then(function (response) { return response.json(); })
    .then(renderState)
    .catch(function () { /* 다음 폴링에서 다시 시도합니다. */ });
}

document.getElementById("command-form").addEventListener("submit", function (event) {
  event.preventDefault();
  var input = document.getElementById("command-input");
  var text = input.value.trim();
  if (!text) { return; }
  fetch("/api/command" + authQuery, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: text })
  }).then(function () { pollHistory(); }).catch(function () {});
  input.value = "";
  input.focus();
});

setInterval(pollHistory, POLL_INTERVAL_MS);
setInterval(pollState, POLL_INTERVAL_MS);
pollHistory();
pollState();
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
                    "GET /api/history?after=N, POST /api/command."
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
