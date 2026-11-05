"""Cold-start vs warm-session probe (product session: gpt-realtime, server_vad 350 ms, 'both' prompt, tool_choice required).

(a) N cold starts, sequential: POST /realtime/client_secrets (ms, HTTP status), websocket open (ms), session.update -> session.updated (ms).
    When the mint 429s (realtime_client_secret_capacity_exhausted) the call is recorded as such and the socket authenticates with
    the long-lived key + session.update (the same session body).
(b) first-utterance vs warm-session: the same N corpus rows (mix of tiers, v0 audio) streamed once each on N FRESH sessions
    (cold socket, first utterance) and sequentially on ONE already-warm session; after every response the warm session deletes the
    conversation items like electron/voice.js pruneConversation (conversation.item.delete for each response.output item and for the
    user item). Cold and warm tasks of a rep run CONCURRENTLY (equal machine load); 2 reps. Latency = end of speech (last speech
    sample sent; trailing silence streamed at real-time pace) -> function_call_arguments.done.

  BENCH_SCHEMA_MODULE=shipped_schema python warm_probe.py [--n 12] [--reps 2] --out ../../docs/bench-results/warm_probe_raw.json
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import random
import statistics
import subprocess
import sys
import time

import websockets

import audio as audio_mod
import pipelines as P
from common import S, UA, api_key, load_corpus, ssl_context, ws_url
from scoring import percentile

MODEL = "gpt-realtime"
ENDPOINT = "vad350"
VARIANT = "both"
CHUNK = P.CHUNK_BYTES
TIERS = ("micro", "emergency", "operation", "macro", "adversarial")


def session_body() -> dict:
    return S.realtime_session_body(MODEL, VARIANT, endpoint=ENDPOINT, transcription=True)


def ms(a, b):
    return round((b - a) * 1000.0, 1)


class Sess:
    """One realtime websocket with a persistent event reader."""

    def __init__(self):
        self.ws = None
        self.updated = asyncio.Event()
        self.ev_q: asyncio.Queue = asyncio.Queue()
        self.t_open_ms = None
        self.t_update_ms = None
        self.mint = {}
        self.auth = None

    async def open(self, mint: bool):
        token = None
        if mint:
            t = time.perf_counter()
            r = await P.http().post("/realtime/client_secrets", json=session_body())
            self.mint = {"mint_ms": ms(t, time.perf_counter()), "mint_status": r.status_code}
            if r.status_code == 200:
                token = r.json()["value"]
            else:
                self.mint["mint_body"] = r.text[:120]
        self.auth = "mint" if token else "direct_key"
        t = time.perf_counter()
        self.ws = await websockets.connect(ws_url(MODEL), additional_headers={"Authorization": f"Bearer {token or api_key()}", "User-Agent": UA},
                                           ssl=ssl_context(), max_size=None, open_timeout=25, ping_interval=None)
        self.t_open_ms = ms(t, time.perf_counter())
        self.reader_task = asyncio.create_task(self._reader())
        if not token:
            sess = session_body()["session"]
            sess.pop("model", None)
            t = time.perf_counter()
            await self.ws.send(json.dumps({"type": "session.update", "session": sess}))
            await asyncio.wait_for(self.updated.wait(), 25)
            self.t_update_ms = ms(t, self.t_updated)
        else:  # minted: session fixed at mint, still time a (no-op identical) session.update round trip for comparability
            sess = session_body()["session"]
            sess.pop("model", None)
            self.updated.clear()
            t = time.perf_counter()
            await self.ws.send(json.dumps({"type": "session.update", "session": sess}))
            await asyncio.wait_for(self.updated.wait(), 25)
            self.t_update_ms = ms(t, self.t_updated)
        return self

    async def _reader(self):
        try:
            async for raw in self.ws:
                ev = json.loads(raw)
                now = time.perf_counter()
                if ev.get("type") == "session.updated":
                    self.t_updated = now
                    self.updated.set()
                await self.ev_q.put((now, ev))
        except Exception as exc:  # noqa: BLE001
            await self.ev_q.put((time.perf_counter(), {"type": "_closed", "error": repr(exc)}))

    async def utterance(self, pcm: bytes, prune: bool) -> dict:
        while not self.ev_q.empty():
            self.ev_q.get_nowait()
        for i in range(0, len(pcm), CHUNK):
            await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[i:i + CHUNK]).decode()}))
        t0 = time.perf_counter()
        silence = base64.b64encode(b"\x00" * CHUNK).decode()
        st = {"t_done": None, "args": None, "t_transcript": None, "transcript": None, "user_item": None, "out_items": [], "status": None,
              "error": None, "usage": None}
        resp_done = False
        deadline = t0 + 40
        next_silence = t0
        n_sil = 0
        while time.perf_counter() < deadline:
            if not resp_done and time.perf_counter() >= next_silence and n_sil < 100:
                await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": silence}))
                n_sil += 1
                next_silence += P.SILENCE_PACE_S
            try:
                now, ev = await asyncio.wait_for(self.ev_q.get(), timeout=0.05)
            except asyncio.TimeoutError:
                if resp_done and (st["t_transcript"] is not None or time.perf_counter() > t_resp_done + 6):
                    break
                continue
            ty = ev.get("type", "")
            if ty == "error":
                st["error"] = json.dumps(ev.get("error", ev))[:200]
                break
            if ty == "_closed":
                st["error"] = ev["error"]
                break
            if ty == "conversation.item.added" or ty == "conversation.item.created":
                it = ev.get("item", {})
                if it.get("role") == "user" and st["user_item"] is None:
                    st["user_item"] = it.get("id")
            elif ty == "response.function_call_arguments.done":
                st["t_done"] = now
                st["args"] = ev.get("arguments")
            elif ty == "conversation.item.input_audio_transcription.completed":
                st["t_transcript"] = now
                st["transcript"] = ev.get("transcript")
                if resp_done:
                    break
            elif ty == "response.done":
                r = ev.get("response", {})
                st["status"] = r.get("status")
                st["usage"] = r.get("usage")
                st["out_items"] = [i.get("id") for i in r.get("output", []) if i.get("id")]
                for item in r.get("output", []):
                    if item.get("type") == "function_call" and st["args"] is None:
                        st["args"] = item.get("arguments")
                        st["t_done"] = st["t_done"] or now
                resp_done = True
                t_resp_done = now
                if st["t_transcript"] is not None:
                    break
        if prune:  # electron/voice.js pruneConversation
            for iid in [*st["out_items"], st["user_item"]]:
                if iid:
                    await self.ws.send(json.dumps({"type": "conversation.item.delete", "item_id": iid}))
        return {"t_tool_call_ms": ms(t0, st["t_done"]) if st["t_done"] else None,
                "t_transcript_ms": ms(t0, st["t_transcript"]) if st["t_transcript"] else None,
                "transcript": st["transcript"], "ok": st["args"] is not None, "error": st["error"],
                "tokens_total": (st["usage"] or {}).get("total_tokens"), "tokens_in": (st["usage"] or {}).get("input_tokens")}

    async def close(self):
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def pick_rows(n: int) -> list[dict]:
    rows = load_corpus()
    rng = random.Random(7)
    by = {t: [r for r in rows if r["tier"] == t] for t in TIERS}
    for v in by.values():
        rng.shuffle(v)
    out = []
    i = 0
    while len(out) < n:
        t = TIERS[i % len(TIERS)]
        out.append(by[t][i // len(TIERS)])
        i += 1
    return out


async def cold_starts(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        s = Sess()
        try:
            await s.open(mint=True)
            out.append({**s.mint, "auth": s.auth, "ws_open_ms": s.t_open_ms, "session_update_ms": s.t_update_ms})
        except Exception as exc:  # noqa: BLE001
            out.append({**s.mint, "auth": s.auth, "error": repr(exc)[:160]})
        finally:
            if s.ws:
                await s.close()
    return out


async def cold_first(rows, rep) -> list[dict]:
    out = []
    for r in rows:
        s = Sess()
        try:
            await s.open(mint=False)
            res = await s.utterance(audio_mod.load_audio(r["id"], "v0"), prune=False)
            out.append({"id": r["id"], "tier": r["tier"], "rep": rep, "kind": "cold_first", "ws_open_ms": s.t_open_ms,
                        "session_update_ms": s.t_update_ms, **res})
        except Exception as exc:  # noqa: BLE001
            out.append({"id": r["id"], "tier": r["tier"], "rep": rep, "kind": "cold_first", "ok": False, "error": repr(exc)[:160]})
        finally:
            if s.ws:
                await s.close()
    return out


async def warm_seq(rows, rep) -> list[dict]:
    out = []
    s = Sess()
    await s.open(mint=False)
    try:
        for i, r in enumerate(rows):
            res = await s.utterance(audio_mod.load_audio(r["id"], "v0"), prune=True)
            out.append({"id": r["id"], "tier": r["tier"], "rep": rep, "kind": "warm", "nth": i + 1, **res})
            await asyncio.sleep(0.3)  # let the deletes land
    finally:
        await s.close()
    return out


def stats(vals):
    v = [x for x in vals if x is not None]
    return {"n": len(v), "p50": percentile(v, .5), "p95": percentile(v, .95), "mean": round(statistics.fmean(v), 1) if v else None}


def uptime():
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


async def amain(a):
    rows = pick_rows(a.n)
    res = {"uptime_before": uptime(), "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": [r["id"] for r in rows]}
    res["cold_starts"] = await cold_starts(a.n)
    cs = res["cold_starts"]
    res["cold_start_summary"] = {k: stats([c.get(k) for c in cs]) for k in ("mint_ms", "ws_open_ms", "session_update_ms")}
    res["cold_start_summary"]["mint_http_status"] = {str(k): sum(1 for c in cs if c.get("mint_status") == k) for k in {c.get("mint_status") for c in cs}}
    utts = []
    for rep in range(a.reps):
        cold, warm = await asyncio.gather(cold_first(rows, rep), warm_seq(rows, rep))
        utts += cold + warm
    res["utterances"] = utts
    summ = {}
    for kind in ("cold_first", "warm"):
        sel = [u for u in utts if u["kind"] == kind and u.get("ok")]
        summ[kind] = {"n": len(sel), "n_total": sum(1 for u in utts if u["kind"] == kind), "t_tool_call_ms": stats([u["t_tool_call_ms"] for u in sel]),
                      "t_transcript_ms": stats([u["t_transcript_ms"] for u in sel])}
    # paired by (id, rep): warm - cold_first
    cf = {(u["id"], u["rep"]): u for u in utts if u["kind"] == "cold_first" and u.get("ok")}
    pd = [u["t_tool_call_ms"] - cf[(u["id"], u["rep"])]["t_tool_call_ms"] for u in utts
          if u["kind"] == "warm" and u.get("ok") and (u["id"], u["rep"]) in cf]
    summ["paired_warm_minus_cold_tool_ms"] = {"n": len(pd), "median": percentile(pd, .5), "mean": round(statistics.fmean(pd), 1) if pd else None}
    summ["warm_first_vs_rest_tool_ms"] = {"first": stats([u["t_tool_call_ms"] for u in utts if u["kind"] == "warm" and u.get("ok") and u["nth"] == 1]),
                                           "rest": stats([u["t_tool_call_ms"] for u in utts if u["kind"] == "warm" and u.get("ok") and u["nth"] > 1])}
    res["summary"] = summ
    res["uptime_after"] = uptime()
    res["end"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(a.out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({"cold_start_summary": res["cold_start_summary"], "summary": summ, "uptime_before": res["uptime_before"], "uptime_after": res["uptime_after"]}, indent=1))
    await P.close_http()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--out", default="warm_probe_raw.json")
    asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
