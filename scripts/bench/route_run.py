"""Run the route-sketch interpreter over route_corpus.jsonl for several VLMs and score it (route_scoring.py).

The system under test is RouteBackend's ``starcraft_commander.route_sketch.interpret_sketch(req, world, *, vlm)`` with an
injected VlmReader per model (the SHIPPED ``route_vlm.ProxyVlmReader`` over the proxy, image part = the rendered minimap incl.
strokes; only its urlopen is wrapped to read the proxy-reported token usage) and the
deterministic no-VLM fallback (``vlm=None``).  Latency = wall time of one interpret call (the rendered PNG is built before
the clock starts).  ``tokens`` = proxy-reported total tokens of that call's VLM request(s).  The whole request path (prompt,
schema, response_format, 20 s timeout) is the product's, so an unparseable / rejected / timed-out answer is an ``ask``.

Usage (repo root):
  source ~/.zshenv
  python3 scripts/bench/route_run.py run --models gemini-3-flash claude-haiku-5-5 --reps 2 --concurrency 6
  python3 scripts/bench/route_run.py run --models none            # deterministic fallback only
  python3 scripts/bench/route_run.py report                       # table + selection from docs/bench-results/route_*.json
  python3 scripts/bench/route_run.py probe                        # which proxy models accept an image part

Outputs: docs/bench-results/route_<variant>.json (meta incl. uptime/loadavg, aggregate, per-row scores, predictions).
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import route_corpus as RC  # noqa: E402
import route_scoring as RS  # noqa: E402
import route_world as RW  # noqa: E402

RESULTS = HERE.parents[1] / "docs" / "bench-results"
MODELS = ["gemini-3-flash", "gemini-3.8-flash-high", "claude-haiku-5-5", "claude-sonnet-4-6", "gpt-6-sol"]
IMG_SIZE = 512


# --------------------------------------------------------------------------- adapters (corpus row -> shared contract)


def build_request_body(row: dict, *, render_images: bool = True) -> dict:
    """The POST /api/route/sketch body of the shared contract (same shape the HUD sends)."""

    req = row["request"]
    img = None
    if render_images:
        img = base64.b64encode(RW.render_minimap(RW.World.from_dict(row["world"]), req["strokes"], size=IMG_SIZE)).decode()
    return {"update_id": req["update_id"], "strokes": req["strokes"], "image_png_b64": img, "transcript": req.get("transcript", ""),
            "minimap_rect": dict(req["minimap_rect"])}


def build_world_dict(row: dict) -> dict:
    """SketchWorld fields of the shared contract as a plain dict (the backend adapter turns it into its own class)."""

    w = RW.World.from_dict(row["world"])
    return {"playable": tuple(w.playable), "map_size": tuple(w.map_size), "pathable": w.pathable,
            "own_units": [tuple(u) for u in w.own_units], "enemies": [tuple(e) for e in w.enemies],
            "places": [tuple(p) for p in w.places]}


def _get(o, name, default=None):
    return o.get(name, default) if isinstance(o, dict) else getattr(o, name, default)


def flatten_decision(dec) -> dict:
    """RouteDecision (dict / dataclass) -> the flat prediction the scorer reads (world units only)."""

    route = _get(dec, "route")
    squad = _get(dec, "squad")
    wps = [[float(_get(w, "x")), float(_get(w, "y"))] for w in (_get(route, "waypoints", []) or [])] if route else []
    fin = _get(route, "final") if route else None
    out = {"kind": _get(dec, "kind"), "waypoints": wps,
           "final": {"action": _get(fin, "action"), "x": float(_get(fin, "x")), "y": float(_get(fin, "y"))} if fin else None,
           "squad": {"unit_count": int(_get(squad, "unit_count", 0) or 0), "label_ko": str(_get(squad, "label_ko", "") or "")} if squad else None}
    if _get(dec, "question_ko"):
        out["question_ko"] = _get(dec, "question_ko")
    return out


# --------------------------------------------------------------------------- runner


def uptime_line() -> str:
    try:
        return subprocess.run(["uptime"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return "n/a"


_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def _scrub(text: str) -> str:
    text = _KEY_RE.sub("***", text)
    for name in ("MYPROXY_API_KEY", "VOI_REALTIME_API_KEY", "OPENAI_API_KEY"):
        k = os.environ.get(name)
        if k:
            text = text.replace(k, "***")
    return text


def _one(row: dict, interpret, vlm, render_images: bool) -> dict:
    body = build_request_body(row, render_images=render_images)
    world = build_world_dict(row)
    if vlm is not None and hasattr(vlm, "reset"):
        vlm.reset()
    t0 = time.perf_counter()
    try:
        dec = interpret(body, world, vlm)
        pred = flatten_decision(dec)
    except Exception as e:  # noqa: BLE001  an interpreter crash = an unanswered row, never an execution
        return {"kind": None, "error": _scrub(f"{type(e).__name__}: {e}")[:300], "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "tokens": None}
    pred["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    pred["tokens"] = getattr(vlm, "last_tokens", None) if vlm is not None else None
    return pred


def run_variant(name: str, rows: list[dict], interpret, make_vlm, *, reps: int = 2, concurrency: int = 6, render_images: bool = True) -> dict:
    """``interpret(body, world, vlm) -> RouteDecision``; ``make_vlm() -> VlmReader | None`` (one per rep, shared by workers)."""

    t_start = time.time()
    load_before = os.getloadavg()
    up_before = uptime_line()
    all_reps = []
    for rep in range(reps):
        vlm = make_vlm()
        preds: dict[str, dict] = {}
        with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            futs = {ex.submit(_one, r, interpret, vlm, render_images): r["id"] for r in rows}
            for f in cf.as_completed(futs):
                preds[futs[f]] = f.result()
        scores = RS.score_all(rows, preds)
        all_reps.append({"rep": rep, "predictions": preds, "scores": scores, "aggregate": RS.aggregate(scores)})
    pooled = RS.aggregate([s for r in all_reps for s in r["scores"]])
    return {"meta": {"variant": name, "reps": reps, "concurrency": concurrency, "n_rows": len(rows), "render_images": render_images,
                     "uptime": up_before, "uptime_after": uptime_line(), "loadavg_1_5_15": [round(x, 2) for x in load_before],
                     "wall_s": round(time.time() - t_start, 1), "corpus_sha256_16": _corpus_hash(), "code": code_commit()},
            "aggregate": pooled, "reps": all_reps}


def code_commit() -> dict:
    """Commit that last touched the system under test (route_sketch.py / route_vlm.py) + the tree HEAD + dirty flag."""

    def git(*a):
        try:
            return subprocess.run(["git", *a], capture_output=True, text=True, timeout=10, cwd=HERE.parents[1]).stdout.strip()
        except Exception:  # noqa: BLE001
            return "n/a"

    return {"route_code": git("log", "-1", "--format=%h", "--", "starcraft_commander/route_sketch.py", "starcraft_commander/route_vlm.py"),
            "head": git("rev-parse", "--short", "HEAD"), "dirty_sut": bool(git("status", "--porcelain", "--", "starcraft_commander"))}


def _corpus_hash() -> str:
    import hashlib

    return hashlib.sha256(RC.CORPUS.read_bytes()).hexdigest()[:16]


# --------------------------------------------------------------------------- the real system under test


class UsageRecordingVlm:
    """Wraps the product's ProxyVlmReader: same request, plus thread-local token usage read from the response body."""

    def __init__(self, model: str):
        import urllib.request

        from starcraft_commander import realtime_intent, route_vlm

        cfg = realtime_intent.load_upstream_config(os.environ)
        if not cfg.api_key:
            raise SystemExit("no API key: source ~/.zshenv")
        self.model = model
        self._tl = threading.local()
        self._urlopen = urllib.request.urlopen
        self._reader = route_vlm.ProxyVlmReader(base_url=cfg.base_url, api_key=cfg.api_key, model=model, opener=self._open)

    def _open(self, request, **kw):
        import io
        import json as _json

        resp = self._urlopen(request, **kw)
        raw = resp.read()
        resp.close()
        try:
            u = _json.loads(raw).get("usage") or {}
            self._tl.tokens = u.get("total_tokens") or ((u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)) or None
        except ValueError:
            self._tl.tokens = None
        return io.BytesIO(raw)

    @property
    def last_tokens(self):
        return getattr(self._tl, "tokens", None)

    def reset(self):
        self._tl.tokens = None

    def read(self, image_png, prompt, schema):
        return self._reader.read(image_png, prompt, schema)


def real_interpret():
    """Bind to RouteBackend's interpret_sketch (merged from feat/r6-route).  Adapter lives here so the bench never edits it."""

    from starcraft_commander import route_sketch as RSK

    def interpret(body: dict, world: dict, vlm):
        req = RSK.SketchRequest.from_payload(body) if hasattr(RSK.SketchRequest, "from_payload") else RSK.SketchRequest(**body)
        sw = RSK.SketchWorld(**world)
        return RSK.interpret_sketch(req, sw, vlm=vlm)

    return interpret


def save(variant: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"route_{variant['meta']['variant']}.json"
    path.write_text(_scrub(json.dumps(variant, ensure_ascii=False, indent=1, sort_keys=True)) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- report


def fmt(v, nd=3):
    return "n/a" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def table(variants: dict[str, dict]) -> str:
    cols = ["n", "kind_acc", "kind_acc_lenient", "unsafe", "unanswered", "wp_mean", "wp_p95", "order_ok", "dest_ok", "action_ok", "squad_ok",
            "latency_p50_ms", "latency_p95_ms", "tokens_mean"]
    lines = ["| variant | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for name, v in variants.items():
        a = v["aggregate"]["all"]
        lines.append(f"| {name} | " + " | ".join(fmt(a[c], 2 if c.startswith(("wp", "lat", "tok")) else 3) for c in cols) + " |")
    return "\n".join(lines)


def category_table(name: str, v: dict) -> str:
    cols = ["n", "kind_acc", "kind_acc_lenient", "unsafe", "unanswered", "wp_mean", "wp_p95", "order_ok", "action_ok", "squad_ok", "latency_p50_ms"]
    lines = [f"| {name}: category | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for cat, a in v["aggregate"].items():
        lines.append(f"| {cat} | " + " | ".join(fmt(a[c], 2 if c.startswith(("wp", "lat")) else 3) for c in cols) + " |")
    return "\n".join(lines)


def load_results() -> dict[str, dict]:
    out = {}
    for p in sorted(RESULTS.glob("route_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if "meta" in d and "aggregate" in d:
            out[d["meta"]["variant"]] = d
    return out


# --------------------------------------------------------------------------- cli


def probe(models: list[str]) -> int:
    row = RC.load_rows()[0]
    png = RW.render_minimap(RW.World.from_dict(row["world"]), row["request"]["strokes"], size=384)
    for m in models:
        vlm = UsageRecordingVlm(m)
        t0 = time.perf_counter()
        try:
            out = vlm.read(png, 'Reply JSON {"stroke_color": <color name of the drawn line>}.', {"type": "object"})
            print(f"{m}: image OK ({(time.perf_counter() - t0) * 1000:.0f} ms, {vlm.last_tokens} tokens) -> {out}")
        except Exception as e:  # noqa: BLE001
            print(f"{m}: REJECTED/failed: {_scrub(str(e))}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "report", "probe"])
    ap.add_argument("--models", nargs="*", default=MODELS + ["none"], help="proxy model ids and/or 'none' (deterministic fallback)")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="first N rows only (smoke)")
    ap.add_argument("--no-images", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        return probe([m for m in a.models if m != "none"])
    if a.cmd == "report":
        res = load_results()
        print(table(res))
        for k, v in res.items():
            print()
            print(category_table(k, v))
        print()
        print("n per category:", {c: a["n"] for c, a in next(iter(res.values()))["aggregate"].items()})
        print("uptime per run:", {k: v["meta"]["uptime"].split("load averages:")[-1].strip() for k, v in res.items()})
        chosen, trace = RS.choose({k: v["aggregate"]["all"] for k, v in res.items()})
        print("\n".join(trace))
        print("chosen:", chosen)
        return 0
    rows = RC.load_rows()
    if a.limit:
        rows = rows[:: max(1, len(rows) // a.limit)][: a.limit]
    interpret = real_interpret()
    for m in a.models:
        mk = (lambda: None) if m == "none" else (lambda m=m: UsageRecordingVlm(m))
        run = run_variant(m, rows, interpret, mk, reps=a.reps, concurrency=a.concurrency, render_images=not a.no_images)
        print(f"== {m}: {fmt(run['aggregate']['all']['kind_acc'])} kind_acc, unsafe {run['aggregate']['all']['unsafe']}, "
              f"unanswered {run['aggregate']['all']['unanswered']}, p50 {run['aggregate']['all']['latency_p50_ms']} ms; {run['meta']['uptime']}")
        print("wrote", save(run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
