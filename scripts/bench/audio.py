"""Synthesize Korean/English speech for each corpus row with proxy TTS (gpt-4o-mini-tts).

Variants (one audio file per row per variant, 24 kHz mono pcm16 little-endian):
  v0  voice=alloy speed=1.00 clean
  v1  voice=onyx  speed=1.15 clean (fast speaker)
  v2  voice=nova  speed=1.00 + white Gaussian noise at SNR 18 dB (seeded per row)

audio_cache/ is gitignored; audio_manifest.json (sha256 per file) is committed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random

import httpx

from common import AUDIO_DIR, BASE_URL, MANIFEST, UA, api_key, load_corpus, ssl_context

VARIANTS = {
    "v0": {"voice": "alloy", "speed": 1.0, "noise_snr_db": None},
    "v1": {"voice": "onyx", "speed": 1.15, "noise_snr_db": None},
    "v2": {"voice": "nova", "speed": 1.0, "noise_snr_db": 18.0},
}
SAMPLE_RATE = 24000
TTS_MODEL = "gpt-4o-mini-tts"


def audio_path(row_id: str, variant: str):
    return AUDIO_DIR / f"{row_id}.{variant}.pcm"


def add_noise(pcm: bytes, snr_db: float, seed: str) -> bytes:
    import numpy as np

    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    if x.size == 0:
        return pcm
    power = float(np.mean(x**2)) or 1.0
    sigma = (power / (10 ** (snr_db / 10))) ** 0.5
    rng = np.random.default_rng(int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16))
    y = np.clip(x + rng.normal(0, sigma, x.shape), -32768, 32767).astype("<i2")
    return y.tobytes()


def load_audio(row_id: str, variant: str) -> bytes:
    return audio_path(row_id, variant).read_bytes()


async def _tts(client: httpx.AsyncClient, text: str, voice: str, speed: float) -> bytes:
    body = {"model": TTS_MODEL, "voice": voice, "input": text, "response_format": "pcm"}
    if speed != 1.0:
        body["speed"] = speed
    last = None
    for attempt in range(5):
        try:
            r = await client.post("/audio/speech", json=body, timeout=90)
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
            last = f"{r.status_code} {r.text[:120]}"
        except httpx.HTTPError as exc:
            last = repr(exc)
        await asyncio.sleep(1.5 * (attempt + 1) + random.random())
    raise RuntimeError(f"TTS failed for {text!r}: {last}")


async def synthesize(concurrency: int = 6, variants=None) -> dict:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_corpus()
    variants = variants or list(VARIANTS)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    sem = asyncio.Semaphore(concurrency)
    done = 0
    todo = [(r, v) for r in rows for v in variants]
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {api_key()}", "User-Agent": UA},
        verify=ssl_context(),
    ) as client:

        async def one(row, variant):
            nonlocal done
            path = audio_path(row["id"], variant)
            cfg = VARIANTS[variant]
            text_sha = hashlib.sha256(row["text"].encode()).hexdigest()[:12]
            entry = manifest.get(row["id"], {}).get(variant)
            if path.exists() and entry and entry["text_sha"] == text_sha and entry["voice"] == cfg["voice"]:
                return
            async with sem:
                pcm = await _tts(client, row["text"], cfg["voice"], cfg["speed"])
            if cfg["noise_snr_db"]:
                pcm = add_noise(pcm, cfg["noise_snr_db"], row["id"])
            path.write_bytes(pcm)
            manifest.setdefault(row["id"], {})[variant] = {
                "sha256": hashlib.sha256(pcm).hexdigest(),
                "bytes": len(pcm),
                "duration_s": round(len(pcm) / 2 / SAMPLE_RATE, 3),
                "text_sha": text_sha,
                **cfg,
                "tts_model": TTS_MODEL,
            }
            done += 1
            if done % 40 == 0:
                print(f"  synthesized {done}/{len(todo)}", flush=True)

        await asyncio.gather(*(one(r, v) for r, v in todo))
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=0, sort_keys=True) + "\n")
    return manifest
