"""Shared helpers: schema module loader (swappable), paths, env/auth/ssl."""

from __future__ import annotations

import importlib
import os
import ssl
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for p in (str(HERE), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

S = importlib.import_module(os.environ.get("BENCH_SCHEMA_MODULE", "schema"))

CORPUS = HERE / "corpus.jsonl"
AUDIO_DIR = HERE / "audio_cache"
RUNS_DIR = HERE / "runs"
MANIFEST = HERE / "audio_manifest.json"
RESULTS_DIR = REPO_ROOT / "docs" / "bench-results"

BASE_URL = os.environ.get("VOI_REALTIME_BASE_URL", "https://proxy.nomadamas.org/v1").rstrip("/")
UA = "curl/8.7.1"


def api_key() -> str:
    for name in ("VOI_REALTIME_API_KEY", "MYPROXY_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    raise SystemExit("no API key: set MYPROXY_API_KEY (source ~/.zshenv)")


def ssl_context() -> ssl.SSLContext:
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def ws_url(model: str) -> str:
    return BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + f"/realtime?model={model}"


def load_corpus(path: Path = CORPUS) -> list[dict]:
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
