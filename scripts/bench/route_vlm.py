"""Chat-completions (OpenAI-compatible, image part) VlmReader over the proxy, for the R6 benchmark.

Implements the shared contract ``VlmReader.read(image_png: bytes | None, prompt: str, schema: dict) -> dict``.  Thread safe
per call; ``last_latency_ms`` / ``last_tokens`` are thread-local so a concurrent runner can attribute them per row.
The API key is read from the environment only and is never logged or put into exception text.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time

import httpx

BASE_URL = os.environ.get("VOI_REALTIME_BASE_URL", "https://proxy.nomadamas.org/v1").rstrip("/")
UA = "curl/8.7.1"


class VlmError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """First top-level JSON object in a model reply (tolerates ```json fences and prose around it)."""

    s = text or ""
    dec = json.JSONDecoder()
    i = s.find("{")
    while i != -1:
        try:
            obj, _end = dec.raw_decode(s[i:])
        except ValueError:
            i = s.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            return obj
        i = s.find("{", i + 1)
    raise ValueError("no JSON object in the model reply")


def _api_key() -> str:
    for name in ("VOI_REALTIME_API_KEY", "MYPROXY_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    raise SystemExit("no API key: set MYPROXY_API_KEY (source ~/.zshenv)")


def make_client(timeout: float = 90.0) -> httpx.Client:
    import ssl

    import certifi

    return httpx.Client(base_url=BASE_URL, headers={"User-Agent": UA}, verify=ssl.create_default_context(cafile=certifi.where()),
                        timeout=httpx.Timeout(timeout, connect=20.0), limits=httpx.Limits(max_connections=16))


class ProxyVlm:
    def __init__(self, model: str, *, client: httpx.Client | None = None, api_key: str | None = None, retries: int = 2,
                 backoff_s: float = 1.5, max_tokens: int = 1500):
        self.model = model
        self._client = client or make_client()
        self._key = api_key
        self.retries = retries
        self.backoff_s = backoff_s
        self.max_tokens = max_tokens
        self._tl = threading.local()

    @property
    def last_latency_ms(self) -> float | None:
        return getattr(self._tl, "latency_ms", None)

    @property
    def last_tokens(self) -> int | None:
        return getattr(self._tl, "tokens", None)

    def reset(self) -> None:
        self._tl.latency_ms = None
        self._tl.tokens = None

    def read(self, image_png: bytes | None, prompt: str, schema: dict) -> dict:
        text = prompt
        if schema:
            text += "\n\nReply with ONE JSON object only (no prose, no markdown) that matches this JSON schema:\n" + json.dumps(schema, ensure_ascii=False)
        if image_png:
            content: list | str = [{"type": "text", "text": text},
                                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_png).decode()}}]
        else:
            content = [{"type": "text", "text": text}]
        body = {"model": self.model, "messages": [{"role": "user", "content": content}], "max_tokens": self.max_tokens, "temperature": 0}
        key = self._key or _api_key()
        headers = {"Authorization": f"Bearer {key}"}
        last = "no attempt"
        t0 = time.perf_counter()
        for attempt in range(self.retries + 1):
            try:
                r = self._client.post("/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as e:
                last = f"transport {type(e).__name__}"
            else:
                if r.status_code == 200:
                    try:
                        data = r.json()
                        msg = data["choices"][0]["message"]["content"]
                        out = extract_json(msg if isinstance(msg, str) else json.dumps(msg))
                    except (ValueError, KeyError, IndexError, TypeError) as e:
                        last = f"unparseable reply ({type(e).__name__})"
                    else:
                        self._tl.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                        usage = data.get("usage") or {}
                        self._tl.tokens = usage.get("total_tokens") or ((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)) or None
                        return out
                else:
                    last = f"http {r.status_code}: {(r.text or '')[:160]}"
                    if r.status_code < 500 and r.status_code != 429:
                        break
            if attempt < self.retries:
                time.sleep(self.backoff_s * (attempt + 1))
        self._tl.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self._tl.tokens = None
        raise VlmError(last.replace(key, "***"))
