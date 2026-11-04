"""Benchmark pipelines. Common interface:

    result = await pipeline.run(row, rep)  ->  dict(
        tool_args, t_first_token_ms, t_tool_call_ms, total_ms, tokens{...}, error, attempts, transcript, ...)

Latency definition used everywhere: ``t_tool_call_ms`` = wall time from END OF SPEECH to the
complete tool-call arguments being available to the client.
  * A manual:  end of speech = moment the commit is sent (an ideal external endpointer, zero VAD delay).
  * A vad:     end of speech = last speech sample sent; trailing silence is streamed at real-time pace,
               so the server's silence_duration_ms endpointing delay IS included.
  * B text:    request start (transcript already available; ASR time NOT included - see report notes).
  * C fastpath: function call time.
Session secret mint and WebSocket connect happen before the timer starts (a real app holds a warm socket).
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from dataclasses import dataclass, field

import httpx
import websockets

import audio as audio_mod
import fastpath
from common import BASE_URL, SHIPPED_MODE, S, UA, api_key, ssl_context, ws_url

AUDIO_VARIANT_BY_REP = ["v0", "v1", "v2"]
CHUNK_BYTES = 4800  # 100 ms of 24 kHz pcm16
SILENCE_PACE_S = 0.1


class NonRetriable(Exception):
    pass


@dataclass(frozen=True)
class Config:
    name: str
    kind: str  # rt | tx | fp
    model: str = ""
    variant: str = "full"  # schema/instruction variant
    endpoint: str = "manual"  # rt: manual | vad500 | vad300
    reasoning_effort: str = ""  # tx
    asr: bool = False  # rt: also request input transcription (records transcript; may add server load)
    transcript_source: str = "gold"  # tx: gold | asr (use recorded realtime transcripts)
    extra: dict = field(default_factory=dict)

    @property
    def needs_audio(self) -> bool:
        return self.kind == "rt"


def rt_config(model, endpoint="manual", variant="full", asr=False) -> Config:
    name = f"rt:{model}:{endpoint}:{variant}" + (":asr" if asr and not SHIPPED_MODE else "")
    return Config(name, "rt", model, variant, endpoint, asr=asr)


def tx_config(model, variant="compact", effort="", src="gold") -> Config:
    name = f"tx:{model}:{variant}" + (f":{effort}" if effort else "") + (":asrtxt" if src == "asr" else "")
    return Config(name, "tx", model, variant, reasoning_effort=effort, transcript_source=src)


def fp_config(which, src="gold") -> Config:
    return Config(f"fp:{which}" + (":asrtxt" if src == "asr" else ""), "fp", model=which, transcript_source=src)


# ---------------------------------------------------------------------------
# HTTP/WS clients (shared, lazily created inside the running loop)
# ---------------------------------------------------------------------------
_client: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key()}", "User-Agent": UA},
            verify=ssl_context(),
            timeout=httpx.Timeout(90.0, connect=20.0),
            limits=httpx.Limits(max_connections=32),
        )
    return _client


async def close_http():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def with_retries(fn, attempts=3):
    last = None
    for i in range(attempts):
        try:
            return await fn(), i + 1
        except NonRetriable as exc:
            raise
        except Exception as exc:  # noqa: BLE001 - transport/API failures are all retried identically
            last = exc
            text = repr(exc)
            if "RESOURCE_EXHAUSTED" in text or "model_not_found" in text or "Individual quota" in text:
                raise NonRetriable(text) from exc
            await asyncio.sleep(1.0 * (2**i) + random.random() * 0.5)
    raise last  # type: ignore[misc]


def _ms(a: float, b: float) -> float:
    return round((b - a) * 1000.0, 1)


# ---------------------------------------------------------------------------
# A: Realtime audio -> tool call
# ---------------------------------------------------------------------------

def _turn_detection(endpoint: str):
    if endpoint == "manual":
        return None
    sil = {"vad500": 500, "vad300": 300, "vad350": 350}[endpoint]
    return {"type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300,
            "silence_duration_ms": sil, "create_response": True, "interrupt_response": False}


def realtime_session(cfg: Config) -> dict:
    tool = S.build_tool(cfg.variant)
    audio_in = {"format": {"type": "audio/pcm", "rate": 24000}, "turn_detection": _turn_detection(cfg.endpoint)}
    if cfg.asr:
        audio_in["transcription"] = {"model": "gpt-4o-mini-transcribe"}
    return {
        "type": "realtime",
        "model": cfg.model,
        "instructions": S.build_instructions(cfg.variant),
        "tools": [tool],
        "tool_choice": "required",
        "output_modalities": ["text"],
        "audio": {"input": audio_in},
    }


_MINT_BLOCKED_UNTIL = 0.0
MINT_STATS = {"calls": 0, "http_429": 0, "retries_exhausted": 0, "mint_ms": []}


_SHIPPED_MINT_DOWN_UNTIL = 0.0


async def _mint_shipped(cfg: Config) -> tuple[str | None, dict]:
    """Ephemeral secret via POST /realtime/client_secrets with the SHIPPED session body (realtime_intent._session_body),
    exponential backoff on HTTP 429 (3 attempts, ~1+2+4 s). If the proxy keeps answering 429
    (`realtime_client_secret_capacity_exhausted`) the call falls back to the long-lived key + an identical
    `session.update` (auth='direct_key'); the 429 count and the fallback are recorded per call. After a failed ladder the
    mint is not retried for 60 s (circuit breaker) so a capacity outage does not stall the whole run.
    Mint time is NOT part of the measured latency (the app mints before the user speaks)."""
    global _SHIPPED_MINT_DOWN_UNTIL
    vad = None if cfg.endpoint == "manual" else int(cfg.endpoint.replace("vad", ""))
    body = S.realtime_session_body(cfg.model, cfg.variant, vad_silence_ms=vad, transcription=True)
    n429 = 0
    t_start = time.perf_counter()
    if time.time() < _SHIPPED_MINT_DOWN_UNTIL:
        return None, {"mint_429": 0, "mint_ms": None, "mint_skipped_circuit_open": True}
    for attempt in range(3):
        MINT_STATS["calls"] += 1
        r = await http().post("/realtime/client_secrets", json=body)
        if r.status_code == 200:
            ms = _ms(t_start, time.perf_counter())
            MINT_STATS["mint_ms"].append(ms)
            return r.json()["value"], {"mint_429": n429, "mint_ms": ms, "mint_attempts": attempt + 1}
        if r.status_code == 429:
            n429 += 1
            MINT_STATS["http_429"] += 1
            await asyncio.sleep(2.0**attempt + random.random())
            continue
        raise RuntimeError(f"client_secrets {r.status_code} {r.text[:200]}")
    MINT_STATS["retries_exhausted"] += 1
    _SHIPPED_MINT_DOWN_UNTIL = time.time() + 60
    return None, {"mint_429": n429, "mint_ms": None, "mint_fallback_direct_key": True}


async def _mint(cfg: Config) -> str | None:
    """Legacy (private-schema) path: mint with fallback to the long-lived key when the proxy has no secret capacity."""
    global _MINT_BLOCKED_UNTIL
    if time.time() < _MINT_BLOCKED_UNTIL:
        return None
    r = await http().post("/realtime/client_secrets", json={"session": realtime_session(cfg)})
    if r.status_code == 429 and "capacity_exhausted" in r.text:
        _MINT_BLOCKED_UNTIL = time.time() + 120
        return None
    if r.status_code != 200:
        raise RuntimeError(f"client_secrets {r.status_code} {r.text[:200]}")
    return r.json()["value"]


async def _realtime_once(cfg: Config, pcm: bytes) -> dict:
    mint_info: dict = {}
    if SHIPPED_MODE:
        token, mint_info = await _mint_shipped(cfg)
    else:
        token = await _mint(cfg)
    auth = "mint" if token else "direct_key"
    async with websockets.connect(
        ws_url(cfg.model),
        additional_headers={"Authorization": f"Bearer {token or api_key()}", "User-Agent": UA},
        ssl=ssl_context(),
        max_size=None,
        open_timeout=25,
        ping_interval=None,
    ) as ws:
        st: dict = {"t_first": None, "t_done": None, "args": None, "usage": None, "t_speech_stopped": None,
                    "transcript": None, "t_transcript": None, "error": None, "resp_status": None}
        t0_box: dict = {}
        st["session_updated"] = asyncio.Event()

        async def reader():
            async for raw in ws:
                ev = json.loads(raw)
                ty = ev.get("type", "")
                now = time.perf_counter()
                if ty == "session.updated":
                    st["session_updated"].set()
                if ty == "error":
                    st["error"] = json.dumps(ev.get("error", ev))[:300]
                    st["session_updated"].set()
                    return
                if ty == "response.function_call_arguments.delta" and st["t_first"] is None:
                    st["t_first"] = now
                elif ty == "response.function_call_arguments.done":
                    st["t_done"] = now
                    st["args"] = ev.get("arguments")
                elif ty == "input_audio_buffer.speech_stopped":
                    st["t_speech_stopped"] = now
                elif ty == "conversation.item.input_audio_transcription.completed":
                    st["t_transcript"] = now
                    st["transcript"] = ev.get("transcript")
                elif ty == "response.done":
                    r = ev.get("response", {})
                    st["usage"] = r.get("usage")
                    st["resp_status"] = r.get("status")
                    if st["args"] is None:
                        # tool call may be present only in the final output
                        for item in r.get("output", []):
                            if item.get("type") == "function_call":
                                st["args"] = item.get("arguments")
                                st["t_done"] = st["t_done"] or now
                    if cfg.asr and st["args"] is not None:
                        for _ in range(100 if SHIPPED_MODE else 30):  # let the (async) input transcription land for recording
                            if st["t_transcript"] is not None:
                                break
                            await asyncio.sleep(0.1)
                    return

        rtask = asyncio.create_task(reader())
        try:
            if auth == "direct_key":
                sess = realtime_session(cfg)
                sess.pop("model", None)
                await ws.send(json.dumps({"type": "session.update", "session": sess}))
                await asyncio.wait_for(st["session_updated"].wait(), 20)
                if st["error"]:
                    raise RuntimeError(f"session.update error {st['error']}")
            for i in range(0, len(pcm), CHUNK_BYTES):
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(pcm[i:i + CHUNK_BYTES]).decode()}))
            t0 = time.perf_counter()
            t0_box["t0"] = t0
            if cfg.endpoint == "manual":
                await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                await ws.send(json.dumps({"type": "response.create"}))
            else:
                silence = base64.b64encode(b"\x00" * CHUNK_BYTES).decode()
                for _ in range(int(2.5 / SILENCE_PACE_S)):
                    if rtask.done() or st["t_done"] is not None:
                        break
                    await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": silence}))
                    await asyncio.sleep(SILENCE_PACE_S)
            await asyncio.wait_for(rtask, timeout=60)
        finally:
            if not rtask.done():
                rtask.cancel()
        if st["error"]:
            raise RuntimeError(f"realtime error {st['error']}")
        if st["args"] is None:
            return {"tool_args": None, "error": f"no tool call (status={st['resp_status']})", "t0": t0_box["t0"], "auth": auth, **mint_info, **st}
        return {"tool_args": st["args"], "t0": t0_box["t0"], "auth": auth, **mint_info, **st}


def _rt_tokens(usage) -> dict:
    usage = usage or {}
    det = usage.get("input_token_details") or {}
    return {"text_in": det.get("text_tokens", 0), "audio_in": det.get("audio_tokens", 0),
            "out": usage.get("output_tokens", 0), "total": usage.get("total_tokens", 0)}


async def run_realtime(cfg: Config, row: dict, rep: int) -> dict:
    variant = AUDIO_VARIANT_BY_REP[rep % len(AUDIO_VARIANT_BY_REP)]
    pcm = audio_mod.load_audio(row["id"], variant)
    out = {"audio_variant": variant}
    t_start = time.perf_counter()
    try:
        res, attempts = await with_retries(lambda: _realtime_once(cfg, pcm))
    except Exception as exc:  # noqa: BLE001
        return {**out, "tool_args": None, "error": repr(exc)[:300], "attempts": 3, "total_ms": _ms(t_start, time.perf_counter()),
                "t_tool_call_ms": None, "t_first_token_ms": None, "tokens": {}}
    t0 = res["t0"]
    try:
        args = json.loads(res["tool_args"]) if res["tool_args"] else None
        err = res.get("error")
    except json.JSONDecodeError:
        args, err = None, "tool args not JSON"
    return {**out, "auth": res.get("auth"), "mint_429": res.get("mint_429"), "mint_ms": res.get("mint_ms"), "tool_args": args, "error": err, "attempts": attempts,
            "t_first_token_ms": _ms(t0, res["t_first"]) if res["t_first"] else None,
            "t_tool_call_ms": _ms(t0, res["t_done"]) if res["t_done"] else None,
            "t_speech_stopped_ms": _ms(t0, res["t_speech_stopped"]) if res["t_speech_stopped"] else None,
            "t_transcript_ms": _ms(t0, res["t_transcript"]) if res["t_transcript"] else None,
            "total_ms": _ms(t0, res["t_done"]) if res["t_done"] else None,
            "transcript": res["transcript"], "tokens": _rt_tokens(res["usage"])}


# ---------------------------------------------------------------------------
# B: text LLM -> tool call (chat completions, streaming)
# ---------------------------------------------------------------------------
_TOOL_CHOICE_MODE: dict[str, str] = {}  # model -> "required" | "auto" (learned on 400)


async def _chat_once(cfg: Config, text: str) -> dict:
    tool = S.to_chat_tool(S.build_tool(cfg.variant))
    mode = _TOOL_CHOICE_MODE.get(cfg.model, "required")
    sys_prompt = S.build_instructions(cfg.variant)
    if mode == "auto":
        sys_prompt += "\nYou MUST respond by calling submit_command; never answer in prose."
    body = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": text}],
        "tools": [tool],
        "tool_choice": "required" if mode == "required" else "auto",
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 1500,
    }
    if cfg.reasoning_effort:
        body["reasoning_effort"] = cfg.reasoning_effort
    t0 = time.perf_counter()
    t_first = None
    pieces: dict[int, str] = {}
    usage = None
    async with http().stream("POST", "/chat/completions", json=body) as r:
        if r.status_code != 200:
            raw = (await r.aread()).decode("utf-8", "replace")[:300]
            if r.status_code == 400 and "tool_choice" in raw and mode == "required":
                _TOOL_CHOICE_MODE[cfg.model] = "auto"
                raise RuntimeError("retry-with-auto-tool-choice")
            raise RuntimeError(f"chat {r.status_code} {raw}")
        async for line in r.aiter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            d = json.loads(line[5:])
            if d.get("usage"):
                usage = d["usage"]
            for ch in d.get("choices", []):
                for tc in ch.get("delta", {}).get("tool_calls") or []:
                    if t_first is None:
                        t_first = time.perf_counter()
                    idx = tc.get("index", 0)
                    pieces[idx] = pieces.get(idx, "") + (tc.get("function", {}).get("arguments") or "")
    t_done = time.perf_counter()
    args = pieces.get(0) or (next(iter(pieces.values())) if pieces else None)
    return {"args": args, "t0": t0, "t_first": t_first, "t_done": t_done, "usage": usage}


async def run_text(cfg: Config, row: dict, rep: int, asr_transcripts: dict | None = None) -> dict:
    text = row["text"]
    if cfg.transcript_source == "asr":
        text = (asr_transcripts or {}).get((row["id"], rep))
        if not text:
            return {"tool_args": None, "error": "no ASR transcript available for this (row, rep)", "attempts": 0, "tokens": {},
                    "t_tool_call_ms": None, "t_first_token_ms": None, "total_ms": None, "input_text": ""}
    t_start = time.perf_counter()
    try:
        res, attempts = await with_retries(lambda: _chat_once(cfg, text), attempts=4)
    except Exception as exc:  # noqa: BLE001
        return {"tool_args": None, "error": repr(exc)[:300], "attempts": 4, "total_ms": _ms(t_start, time.perf_counter()),
                "t_tool_call_ms": None, "t_first_token_ms": None, "tokens": {}, "input_text": text}
    u = res["usage"] or {}
    tokens = {"text_in": u.get("prompt_tokens", 0), "audio_in": 0, "out": u.get("completion_tokens", 0),
              "total": u.get("total_tokens", 0)}
    if res["args"] is None:
        return {"tool_args": None, "error": "no tool call", "attempts": attempts, "tokens": tokens,
                "t_tool_call_ms": None, "t_first_token_ms": None, "total_ms": _ms(res["t0"], res["t_done"]), "input_text": text}
    try:
        args, err = json.loads(res["args"]), None
    except json.JSONDecodeError:
        args, err = None, "tool args not JSON"
    return {"tool_args": args, "error": err, "attempts": attempts, "tokens": tokens, "input_text": text,
            "t_first_token_ms": _ms(res["t0"], res["t_first"]) if res["t_first"] else None,
            "t_tool_call_ms": _ms(res["t0"], res["t_done"]), "total_ms": _ms(res["t0"], res["t_done"])}


# ---------------------------------------------------------------------------
# C: deterministic fast path
# ---------------------------------------------------------------------------

async def run_fastpath(cfg: Config, row: dict, rep: int, asr_transcripts: dict | None = None) -> dict:
    """cfg.model: 'prod_regex' | 'ext_rules' | 'shipped_fast' | 'shipped_regex'; transcript_source gold|asr."""
    fastpath._prod()  # warm the web_gui import outside the timed region
    text = row["text"]
    if cfg.transcript_source == "asr":
        text = (asr_transcripts or {}).get((row["id"], rep))
        if not text:
            return {"tool_args": None, "abstained": True, "error": "no ASR transcript available", "attempts": 0, "tokens": {},
                    "t_tool_call_ms": None, "t_first_token_ms": None, "total_ms": None, "input_text": ""}
    t0 = time.perf_counter()
    args = fastpath.run_named(cfg.model, text)
    t1 = time.perf_counter()
    return {"tool_args": args, "abstained": args is None, "error": None, "attempts": 1, "t_first_token_ms": None,
            "t_tool_call_ms": _ms(t0, t1), "total_ms": _ms(t0, t1), "tokens": {}, "input_text": text}


async def run(cfg: Config, row: dict, rep: int, asr_transcripts: dict | None = None) -> dict:
    if cfg.kind == "rt":
        return await run_realtime(cfg, row, rep)
    if cfg.kind == "tx":
        return await run_text(cfg, row, rep, asr_transcripts)
    if cfg.kind == "fp":
        return await run_fastpath(cfg, row, rep, asr_transcripts)
    raise ValueError(cfg.kind)


# ---------------------------------------------------------------------------
# Config registry
# ---------------------------------------------------------------------------
RT_MODELS = ["gpt-realtime", "gpt-realtime-mini", "gpt-realtime-1.5", "gpt-realtime-2", "gpt-realtime-2025-08-28"]
TX_MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "claude-haiku-5-5", "claude-sonnet-5-5-high",
             "gemini-3.5-flash-lite"]


def registry() -> dict[str, Config]:
    cfgs: list[Config] = []
    if SHIPPED_MODE:
        for m in ("gpt-realtime", "gpt-realtime-mini"):
            for ep in ("vad350", "manual"):
                for v in S.VARIANTS:
                    cfgs.append(rt_config(m, ep, v, asr=True))
        for m in ("gemini-3.5-flash-lite", "gpt-5.6-sol"):
            for v in S.VARIANTS:
                cfgs.append(tx_config(m, v))
                cfgs.append(tx_config(m, v, "", "asr"))
        for w in ("shipped_regex", "shipped_fast", "ext_rules"):
            cfgs.append(fp_config(w))
            cfgs.append(fp_config(w, "asr"))
        return {c.name: c for c in cfgs}
    for m in RT_MODELS:
        for ep in ("manual", "vad500", "vad300"):
            for v in ("full", "compact", "fewshot"):
                cfgs.append(rt_config(m, ep, v))
    for m in TX_MODELS:
        for v in ("full", "compact", "fewshot"):
            cfgs.append(tx_config(m, v))
    for m in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        for v in ("compact", "fewshot"):
            cfgs.append(tx_config(m, v, "low"))
    cfgs.append(rt_config("gpt-realtime-mini", "manual", "compact", asr=True))
    cfgs.append(rt_config("gpt-realtime", "manual", "compact", asr=True))
    cfgs += [fp_config("prod_regex"), fp_config("ext_rules")]
    return {c.name: c for c in cfgs}


def get_config(name: str) -> Config:
    reg = registry()
    if name in reg:
        return reg[name]
    parts = name.split(":")
    if parts[0] == "fp":  # fp:<which>[:asrtxt]
        return fp_config(parts[1], "asr" if parts[-1] == "asrtxt" else "gold")
    if parts[0] == "tx" and parts[-1] == "asrtxt":  # tx:<model>:<variant>[:effort]:asrtxt
        effort = parts[3] if len(parts) == 5 else ""
        return tx_config(parts[1], parts[2], effort, "asr")
    if parts[0] == "tx":
        return tx_config(parts[1], parts[2], parts[3] if len(parts) > 3 else "", "gold")
    if parts[0] == "rt":  # rt:<model>:<endpoint>:<variant>[:asr]
        return rt_config(parts[1], parts[2], parts[3], asr=SHIPPED_MODE or parts[-1] == "asr")
    raise KeyError(f"unknown config {name!r}; see `run.py list`")
