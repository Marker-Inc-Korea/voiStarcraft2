"""Real-ASR probe for emergency / negated / micro words on the product realtime session.

Session = shipped_schema.realtime_session_body('gpt-realtime', 'both', vad_silence_ms=350) (server_vad 350 ms,
gpt-4o-mini-transcribe), minted via /realtime/client_secrets (429 backoff, falls back to the direct key +
session.update).  After session.updated we re-send the same session with turn_detection.create_response=false so no
model response is generated -- only the ASR events are measured.

Audio: gpt-4o-mini-tts, variants v0..v2 from audio.VARIANTS, cached at audio_cache/words/<slug>.<v>.pcm.
Streaming discipline = pipelines._realtime_once: pcm16 24 kHz in 100 ms chunks (unpaced), t0 = instant the last speech
sample was sent, then trailing silence in 100 ms chunks at real-time pace.

Usage:  python scripts/bench/asr_words_probe.py [--reps 2] [--conc 2] [--tts-only] [--report-only] [--limit N]
Outputs: docs/bench-results/asr_words_probe.{json,md}; raw per-call records are appended to
scripts/bench/audio_cache/words/calls.jsonl (resumable).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict

import httpx
import websockets

import audio as audio_mod
import shipped_schema as S
from common import AUDIO_DIR, BASE_URL, RESULTS_DIR, UA, api_key, ssl_context, ws_url
from starcraft_commander import realtime_intent as RI
from starcraft_commander import web_gui as WG

MODEL = "gpt-realtime"
VARIANT = "both"
VAD_MS = 350
CHUNK_BYTES = 4800
PACE_S = 0.1
TAIL_CAP_S = 8.0
AFTER_COMPLETED_S = 1.5
WORDS_DIR = AUDIO_DIR / "words"
CALLS_JSONL = WORDS_DIR / "calls.jsonl"
OUT_JSON = RESULTS_DIR / "asr_words_probe.json"
OUT_MD = RESULTS_DIR / "asr_words_probe.md"

POSITIVE = ["후퇴", "후퇴해", "퇴각", "철수", "전원 철수", "긴급 후퇴", "retreat", "Retreat now", "fall back",
            "홀드", "홀드해", "제자리", "제자리 홀드", "멈춰 서", "hold", "hold position",
            "멈춰", "멈춰!", "정지", "그만", "스톱", "stop", "Stop!"]
NEGATED = ["후퇴하지 마", "후퇴하지 말고 공격해", "멈추지 마", "멈추지 말고 계속 가", "정지하지 마", "그만하지 마",
           "홀드하지 마", "don't stop", "don't retreat", "don't hold", "스팀팩 멈춰", "시즈모드 멈춰", "공격 멈춰",
           "일꾼 후퇴", "일꾼 멈춰"]
MICRO = ["스팀팩", "마린 스팀팩", "불곰 스팀팩", "스팀팩 써", "시즈모드", "탱크 시즈모드", "시즈 풀어", "언시즈"]
PHRASES = ([("positive", t) for t in POSITIVE] + [("negated", t) for t in NEGATED] + [("micro", t) for t in MICRO])


def slug(text: str) -> str:
    s = text.replace("!", "_excl").replace("'", "").replace(" ", "_")
    return re.sub(r"[^\w가-힣]", "_", s)


def wpath(text: str, variant: str):
    return WORDS_DIR / f"{slug(text)}.{variant}.pcm"


def uptime() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------------------------------------- TTS
async def synth_all(conc: int = 2) -> dict:
    WORDS_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(conc)
    failed: list[str] = []
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": f"Bearer {api_key()}", "User-Agent": UA},
                                 verify=ssl_context()) as client:
        async def one(text, v):
            path = wpath(text, v)
            if path.exists() and path.stat().st_size > 1000:
                return
            cfg = audio_mod.VARIANTS[v]
            try:
                async with sem:
                    pcm = await audio_mod._tts(client, text, cfg["voice"], cfg["speed"])
                if cfg["noise_snr_db"]:
                    pcm = audio_mod.add_noise(pcm, cfg["noise_snr_db"], "words:" + text)
                path.write_bytes(pcm)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{text!r}/{v}: {exc}")

        await asyncio.gather(*(one(t, v) for _, t in PHRASES for v in audio_mod.VARIANTS))
    return {"failed": failed}


# --------------------------------------------------------------------------------------------- realtime session
_MINT_DOWN_UNTIL = 0.0


async def mint(client: httpx.AsyncClient) -> tuple[str | None, dict]:
    global _MINT_DOWN_UNTIL
    body = S.realtime_session_body(MODEL, VARIANT, vad_silence_ms=VAD_MS)
    n429 = 0
    t = time.perf_counter()
    if time.time() < _MINT_DOWN_UNTIL:
        return None, {"mint_429": 0, "mint_circuit_open": True}
    for attempt in range(4):
        r = await client.post("/realtime/client_secrets", json=body)
        if r.status_code == 200:
            return r.json()["value"], {"mint_429": n429, "mint_ms": round((time.perf_counter() - t) * 1000, 1)}
        if r.status_code == 429:
            n429 += 1
            await asyncio.sleep(2.0 ** attempt + random.random())
            continue
        raise RuntimeError(f"client_secrets {r.status_code} {r.text[:200]}")
    _MINT_DOWN_UNTIL = time.time() + 45
    return None, {"mint_429": n429, "mint_fallback_direct_key": True}


def _rule(text: str) -> bool:
    return bool(WG._micromachine_request_is_emergency(text, None))


async def one_call(client, text: str, variant: str, rep: int) -> dict:
    pcm = wpath(text, variant).read_bytes()
    token, mint_info = await mint(client)
    auth = "mint" if token else "direct_key"
    rec: dict = {"text": text, "variant": variant, "rep": rep, "auth": auth, **mint_info}
    t_conn = time.perf_counter()
    async with websockets.connect(ws_url(MODEL), additional_headers={"Authorization": f"Bearer {token or api_key()}",
                                                                     "User-Agent": UA},
                                  ssl=ssl_context(), max_size=None, open_timeout=25, ping_interval=None) as ws:
        rec["connect_ms"] = round((time.perf_counter() - t_conn) * 1000, 1)
        st = {"events": [], "error": None, "t_speech_started": None, "t_speech_stopped": None, "t_committed": None,
              "t_completed": None, "items": {}, "order": [], "updated": 0}
        updated = asyncio.Event()

        async def reader():
            async for raw in ws:
                ev = json.loads(raw)
                ty = ev.get("type", "")
                now = time.perf_counter()
                if ty == "session.updated":
                    st["updated"] += 1
                    updated.set()
                elif ty == "error":
                    st["error"] = json.dumps(ev.get("error", ev))[:300]
                    updated.set()
                elif ty == "input_audio_buffer.speech_started":
                    st["t_speech_started"] = st["t_speech_started"] or now
                elif ty == "input_audio_buffer.speech_stopped":
                    st["t_speech_stopped"] = st["t_speech_stopped"] or now
                elif ty == "input_audio_buffer.committed":
                    st["t_committed"] = st["t_committed"] or now
                elif ty == "conversation.item.input_audio_transcription.delta":
                    it = st["items"].setdefault(ev.get("item_id"), {"deltas": [], "final": None, "t_final": None})
                    if ev.get("item_id") not in st["order"]:
                        st["order"].append(ev.get("item_id"))
                    it["deltas"].append((now, ev.get("delta") or ""))
                elif ty == "conversation.item.input_audio_transcription.completed":
                    it = st["items"].setdefault(ev.get("item_id"), {"deltas": [], "final": None, "t_final": None})
                    if ev.get("item_id") not in st["order"]:
                        st["order"].append(ev.get("item_id"))
                    it["final"] = ev.get("transcript") or ""
                    it["t_final"] = now
                    st["t_completed"] = st["t_completed"] or now
                elif ty == "conversation.item.input_audio_transcription.failed":
                    st["error"] = "transcription.failed " + json.dumps(ev.get("error", ev))[:200]

        rtask = asyncio.create_task(reader())
        try:
            # session: same body the product mints; response creation switched off (ASR-only measurement)
            sess = S.realtime_session_body(MODEL, VARIANT, vad_silence_ms=VAD_MS)["session"]
            sess.pop("model", None)
            td = sess["audio"]["input"]["turn_detection"]
            td["create_response"] = False
            await ws.send(json.dumps({"type": "session.update", "session": sess}))
            try:
                await asyncio.wait_for(updated.wait(), 20)
            except asyncio.TimeoutError:
                rec["session_update_timeout"] = True
            if st["error"]:
                raise RuntimeError(f"session.update error {st['error']}")
            for i in range(0, len(pcm), CHUNK_BYTES):
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(pcm[i:i + CHUNK_BYTES]).decode()}))
            t0 = time.perf_counter()
            silence = base64.b64encode(b"\x00" * CHUNK_BYTES).decode()
            for _ in range(int(TAIL_CAP_S / PACE_S)):
                if rtask.done() or st["t_completed"] is not None or st["error"]:
                    break
                await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": silence}))
                await asyncio.sleep(PACE_S)
            if st["t_completed"] is not None:
                await asyncio.sleep(AFTER_COMPLETED_S)
        finally:
            rtask.cancel()
    if st["error"]:
        rec["error"] = st["error"]

    def ms(t):
        return None if t is None else round((t - t0) * 1000, 1)

    rec["t_speech_started_ms"] = ms(st["t_speech_started"])
    rec["t_speech_stopped_ms"] = ms(st["t_speech_stopped"])
    rec["t_committed_ms"] = ms(st["t_committed"])
    rec["t_completed_ms"] = ms(st["t_completed"])
    rec["audio_ms"] = round(len(pcm) / 2 / 24000 * 1000)
    rec["n_items"] = len(st["order"])
    finals = [st["items"][i]["final"] for i in st["order"] if st["items"][i]["final"] is not None]
    rec["transcript"] = " ".join(f.strip() for f in finals) if finals else None
    rec["transcripts_per_item"] = finals
    all_deltas = [(t, d, i) for i in st["order"] for t, d in st["items"][i]["deltas"]]
    all_deltas.sort(key=lambda x: x[0])
    rec["n_deltas"] = len(all_deltas)
    rec["t_first_delta_ms"] = ms(all_deltas[0][0]) if all_deltas else None
    rec["t_last_delta_ms"] = ms(all_deltas[-1][0]) if all_deltas else None
    rec["delta_text"] = "".join(d for _, d, _ in all_deltas)
    rec["deltas"] = [{"t_ms": ms(t), "d": d} for t, d, _ in all_deltas]
    # per item prefix analysis
    prefix_ok = True
    rule_partial_not_final = False
    partial_rule_first_ms = None
    for i in st["order"]:
        it = st["items"][i]
        final = it["final"]
        if final is None:
            continue
        cum = ""
        final_rule = _rule(final)
        for t, d in it["deltas"]:
            cum += d
            if not final.startswith(cum):
                prefix_ok = False
            if _rule(cum) and not final_rule:
                rule_partial_not_final = True
                partial_rule_first_ms = partial_rule_first_ms if partial_rule_first_ms is not None else ms(t)
    rec["delta_cumulative_prefix_of_final"] = prefix_ok if all_deltas else None
    rec["delta_equals_final"] = (rec["delta_text"] == "".join(finals)) if all_deltas and finals else None
    rec["partial_rule_match_but_final_not"] = rule_partial_not_final if all_deltas else None
    rec["partial_rule_first_ms"] = partial_rule_first_ms
    rec["final_emergency_rule"] = _rule(rec["transcript"]) if rec["transcript"] else None
    rec["final_negates_emergency"] = (bool(RI.transcript_negates_emergency(rec["transcript"]))
                                      if rec["transcript"] else None)
    return rec


async def run_calls(todo: list[tuple[str, str, int]], conc: int) -> None:
    WORDS_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(conc)
    done = 0
    lock = asyncio.Lock()
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": f"Bearer {api_key()}", "User-Agent": UA},
                                 verify=ssl_context(), timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        async def one(text, v, rep):
            nonlocal done
            rec = None
            for attempt in range(3):
                try:
                    async with sem:
                        rec = await one_call(client, text, v, rep)
                    if not rec.get("error"):
                        break
                except Exception as exc:  # noqa: BLE001
                    rec = {"text": text, "variant": v, "rep": rep, "error": repr(exc)[:300]}
                await asyncio.sleep(2.0 * (attempt + 1))
            rec["attempts"] = attempt + 1
            async with lock:
                with CALLS_JSONL.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done += 1
                if done % 10 == 0:
                    print(f"  calls {done}/{len(todo)}  {uptime()[-45:]}", flush=True)

        await asyncio.gather(*(one(*x) for x in todo))


# ------------------------------------------------------------------------------------------------------ report
def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def dist(xs):
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "p50": pct(xs, .5), "p95": pct(xs, .95), "min": min(xs) if xs else None,
            "max": max(xs) if xs else None, "mean": round(statistics.mean(xs), 1) if xs else None}


def load_calls() -> list[dict]:
    if not CALLS_JSONL.exists():
        return []
    latest: dict = {}
    for line in CALLS_JSONL.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            latest[(r["text"], r["variant"], r["rep"])] = r  # last wins
    return list(latest.values())


def build_report(calls: list[dict], meta: dict) -> None:
    cat = {t: c for c, t in PHRASES}
    ok = [c for c in calls if not c.get("error") and c.get("transcript") is not None]
    sum_ = {}
    sum_["n_calls_total"] = len(calls)
    sum_["n_completed"] = len(ok)
    sum_["n_error_or_no_transcript"] = len(calls) - len(ok)
    sum_["auth_counts"] = dict(Counter(c.get("auth") for c in calls))
    sum_["mint_429_total"] = sum(c.get("mint_429", 0) or 0 for c in calls)
    sum_["t_speech_stopped_ms"] = dist([c.get("t_speech_stopped_ms") for c in ok])
    sum_["t_first_delta_ms"] = dist([c.get("t_first_delta_ms") for c in ok])
    sum_["t_last_delta_ms"] = dist([c.get("t_last_delta_ms") for c in ok])
    sum_["t_completed_ms"] = dist([c.get("t_completed_ms") for c in ok])
    sum_["completed_minus_speech_stopped_ms"] = dist(
        [c["t_completed_ms"] - c["t_speech_stopped_ms"] for c in ok
         if c.get("t_completed_ms") is not None and c.get("t_speech_stopped_ms") is not None])
    with_d = [c for c in ok if c["n_deltas"] > 0]
    sum_["frac_calls_with_deltas"] = round(len(with_d) / len(ok), 3) if ok else None
    before = [c for c in with_d if c["t_first_delta_ms"] < c["t_completed_ms"]]
    sum_["frac_deltas_before_completed"] = round(len(before) / len(ok), 3) if ok else None
    sum_["first_delta_lead_over_completed_ms"] = dist([c["t_completed_ms"] - c["t_first_delta_ms"] for c in with_d])
    sum_["last_delta_lead_over_completed_ms"] = dist([c["t_completed_ms"] - c["t_last_delta_ms"] for c in with_d])
    sum_["first_delta_minus_speech_stopped_ms"] = dist(
        [c["t_first_delta_ms"] - c["t_speech_stopped_ms"] for c in with_d if c.get("t_speech_stopped_ms") is not None])
    sum_["n_deltas"] = dist([c["n_deltas"] for c in with_d])
    sum_["n_delta_cum_not_prefix_of_final"] = sum(1 for c in with_d if c["delta_cumulative_prefix_of_final"] is False)
    sum_["n_delta_text_not_equal_final"] = sum(1 for c in with_d if c["delta_equals_final"] is False)
    mean_chg = [c for c in with_d if c["partial_rule_match_but_final_not"]]
    sum_["n_partial_matches_emergency_rule_but_final_not"] = len(mean_chg)
    sum_["partial_meaning_change_examples"] = [
        {"text": c["text"], "variant": c["variant"], "final": c["transcript"], "delta_text": c["delta_text"],
         "deltas": [d["d"] for d in c["deltas"]], "partial_rule_first_ms": c["partial_rule_first_ms"],
         "t_completed_ms": c["t_completed_ms"]} for c in mean_chg[:20]]
    sum_["n_multi_item"] = sum(1 for c in ok if c["n_items"] > 1)
    by_cat = {}
    for k in ("positive", "negated", "micro"):
        sub = [c for c in ok if cat[c["text"]] == k]
        by_cat[k] = {"n": len(sub),
                     "t_speech_stopped_ms": dist([c.get("t_speech_stopped_ms") for c in sub]),
                     "t_first_delta_ms": dist([c.get("t_first_delta_ms") for c in sub]),
                     "t_completed_ms": dist([c.get("t_completed_ms") for c in sub]),
                     "final_emergency_rule_true": sum(1 for c in sub if c["final_emergency_rule"]),
                     "partial_rule_but_final_not": sum(1 for c in sub if c["partial_rule_match_but_final_not"])}
    sum_["by_category"] = by_cat

    per_utt = {}
    for k, t in PHRASES:
        sub = [c for c in calls if c["text"] == t]
        trs = Counter((c.get("transcript") if c.get("transcript") is not None else "<none>") for c in sub)
        per_utt[t] = {"category": k, "n": len(sub), "transcripts": dict(trs.most_common()),
                      "final_emergency_rule_true": sum(1 for c in sub if c.get("final_emergency_rule")),
                      "n_errors": sum(1 for c in sub if c.get("error")),
                      "partial_rule_but_final_not": sum(1 for c in sub if c.get("partial_rule_match_but_final_not")),
                      "t_completed_ms_p50": pct([c.get("t_completed_ms") for c in sub], .5)}
    out = {"meta": meta, "summary": sum_, "per_utterance": per_utt, "calls": calls}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def fmt(d):
        return "n/a" if not d or d["p50"] is None else f"{d['p50']:.0f} / {d['p95']:.0f}"

    L = ["# ASR words probe (real gpt-realtime session, server_vad 350 ms, gpt-4o-mini-transcribe)", "",
         f"- session: `realtime_session_body('{MODEL}','{VARIANT}',vad_silence_ms={VAD_MS})`, then session.update with "
         "`turn_detection.create_response=false` (no model response; ASR events only)",
         f"- audio: gpt-4o-mini-tts v0 alloy 1.0x / v1 onyx 1.15x / v2 nova 1.0x + 18 dB noise; "
         f"{meta.get('reps')} reps per (utterance, variant)",
         "- times in ms relative to the instant the last speech sample was sent (t0, pipelines.py convention); "
         "may be negative because the audio is sent unpaced before t0",
         f"- uptime before: `{meta.get('uptime_before')}`", f"- uptime after: `{meta.get('uptime_after')}`", "",
         "## Summary", "",
         f"calls: {sum_['n_calls_total']}, completed with transcript: {sum_['n_completed']}, "
         f"errors/no transcript: {sum_['n_error_or_no_transcript']}; auth: {sum_['auth_counts']}; "
         f"mint 429s: {sum_['mint_429_total']}; multi-item calls: {sum_['n_multi_item']}", "",
         "| metric (ms) | n | p50 | p95 | min | max |", "|---|---|---|---|---|---|"]
    for key in ("t_speech_stopped_ms", "t_first_delta_ms", "t_last_delta_ms", "t_completed_ms",
                "completed_minus_speech_stopped_ms", "first_delta_minus_speech_stopped_ms",
                "first_delta_lead_over_completed_ms", "last_delta_lead_over_completed_ms"):
        d = sum_[key]
        L.append(f"| {key} | {d['n']} | {d['p50']} | {d['p95']} | {d['min']} | {d['max']} |")
    L += ["", f"- fraction of completed calls with any delta: {sum_['frac_calls_with_deltas']}",
          f"- fraction where first delta arrives BEFORE completed: {sum_['frac_deltas_before_completed']} "
          f"(lead p50 {sum_['first_delta_lead_over_completed_ms']['p50']} ms, p95 "
          f"{sum_['first_delta_lead_over_completed_ms']['p95']} ms; last-delta lead p50 "
          f"{sum_['last_delta_lead_over_completed_ms']['p50']} ms)",
          f"- deltas per call: p50 {sum_['n_deltas']['p50']}, p95 {sum_['n_deltas']['p95']}",
          f"- calls where a cumulative delta text is NOT a prefix of the final: {sum_['n_delta_cum_not_prefix_of_final']}",
          f"- calls where concat(deltas) != final: {sum_['n_delta_text_not_equal_final']}",
          f"- calls where some delta-prefix matches the emergency rule (`_micromachine_request_is_emergency`) but the "
          f"final does NOT (meaning change): {sum_['n_partial_matches_emergency_rule_but_final_not']}", ""]
    if sum_["partial_meaning_change_examples"]:
        L += ["| utterance | var | deltas | final | partial-rule first ms | completed ms |", "|---|---|---|---|---|---|"]
        for e in sum_["partial_meaning_change_examples"]:
            L.append(f"| {e['text']} | {e['variant']} | {' · '.join(e['deltas'])} | {e['final']} | "
                     f"{e['partial_rule_first_ms']} | {e['t_completed_ms']} |")
        L.append("")
    L += ["### By category (p50 / p95 ms)", "", "| category | n | speech_stopped | first_delta | completed | final matches "
          "emergency rule | partial-only rule match |", "|---|---|---|---|---|---|---|"]
    for k, v in by_cat.items():
        L.append(f"| {k} | {v['n']} | {fmt(v['t_speech_stopped_ms'])} | {fmt(v['t_first_delta_ms'])} | "
                 f"{fmt(v['t_completed_ms'])} | {v['final_emergency_rule_true']} | {v['partial_rule_but_final_not']} |")
    L += ["", "## Per utterance: distinct ASR transcripts (count)", "",
          "| cat | utterance | n | transcripts (count) | final→emergency rule | partial-only rule | completed p50 |",
          "|---|---|---|---|---|---|---|"]
    for k, t in PHRASES:
        u = per_utt[t]
        trs = "; ".join(f"`{tr}` ×{n}" for tr, n in u["transcripts"].items())
        L.append(f"| {k} | {t} | {u['n']} | {trs} | {u['final_emergency_rule_true']}/{u['n']} | "
                 f"{u['partial_rule_but_final_not']} | {u['t_completed_ms_p50']} |")
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--conc", type=int, default=2)
    ap.add_argument("--tts-only", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="debug: only first N phrases")
    args = ap.parse_args()
    conc = min(args.conc, 2)
    phrases = [t for _, t in PHRASES][: args.limit or None]
    meta = {"model": MODEL, "variant": VARIANT, "vad_silence_ms": VAD_MS, "reps": args.reps, "conc": conc,
            "uptime_before": uptime(), "tts_failed": []}
    if not args.report_only:
        r = await synth_all(conc)
        meta["tts_failed"] = r["failed"]
        print("TTS done; failed:", r["failed"], flush=True)
        if not args.tts_only:
            have = {(c["text"], c["variant"], c["rep"]) for c in load_calls() if not c.get("error") and c.get("transcript") is not None}
            todo = [(t, v, rep) for t in phrases for v in audio_mod.VARIANTS for rep in range(args.reps)
                    if wpath(t, v).exists() and (t, v, rep) not in have]
            random.Random(7).shuffle(todo)
            todo.sort(key=lambda x: x[2])  # rep-major: every (utterance, variant) gets one draw before any gets two
            print(f"{len(todo)} calls to run", flush=True)
            await run_calls(todo, conc)
    meta["uptime_after"] = uptime()
    if args.report_only and OUT_JSON.exists():
        old = json.loads(OUT_JSON.read_text(encoding="utf-8"))["meta"]
        meta.update({k: old[k] for k in ("uptime_before", "tts_failed") if k in old})
        meta["uptime_after"] = old.get("uptime_after", meta["uptime_after"])
    if not args.tts_only:
        build_report(load_calls(), meta)
        print("wrote", OUT_JSON, OUT_MD)


if __name__ == "__main__":
    asyncio.run(main())
